#!/usr/bin/env python3
"""Count SPC3 explicit IV32 cases not covered by early offset classes.

The current typed level-3 SPC3 stores normal predictor hits implicitly and stores
predictor misses as a bitmap plus XOR values. This script streams those typed
substreams directly, reconstructs each explicit IV32 value, and asks whether the
value could have been represented by an early schedule-offset class instead of
an explicit XOR.

Offset class meaning used here:
    case 0 = the normal predictor table entry for the PID upper half.
    case N = predictor table entry for the Nth later RNG upper-half target from
             the same secondhalf.csv t-0 state.

This is a compression-model probe. It does not prove the gameplay cause of the
alternate stream.
"""

from __future__ import annotations

import argparse
import csv
import json
import struct
import sys
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np
import zstandard as zstd


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SPC3 = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_PREDICTOR_JSON = ROOT / "Phase3SpindaBlocks" / "_phase3_pid_second_half_iv_reference.json"
DEFAULT_SECONDHALF_CSV = ROOT / "inputs" / "secondhalf.csv"
DEFAULT_OUTPUT = ROOT / "_tmp" / "spc3_exception_probe" / "first15_explicit_fallback_count.json"

EXPECTED_RECORDS = 0x10000
SPC3_HEADER_SIZE = 80
SPC3_TABLE_ENTRY_SIZE = 96
TYPED_LEVEL3_STREAM_KIND = 4
TYPED_SUBSTREAM_ENTRY_SIZE = 32
TYPED_SUBSTREAM_COUNT = 3
SUBSTREAM_TEMPLATE = 1
SUBSTREAM_BITMAP = 2
SUBSTREAM_VALUES = 3

GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073

CODEC_LEGACY_AUTO = 0
CODEC_NONE = 1
CODEC_ZLIB = 2
CODEC_ZSTD = 3


@dataclass(frozen=True)
class Spc3Header:
    version: int
    level: int
    lane_count: int
    expected_records: int
    record_size: int
    flags: int
    header_size: int
    predictor_offset: int
    predictor_size: int
    table_offset: int
    table_entry_size: int
    data_offset: int
    data_size: int


@dataclass(frozen=True)
class LaneEntry:
    lane: int
    level: int
    stream_kind: int
    flags: int
    source_zip_size: int
    source_zip_crc32: int
    source_zip_fnv64: int
    original_payload_crc32: int
    rebuilt_payload_crc32: int
    stream_offset: int
    stream_size: int
    uncompressed_model_size: int
    predictor_matches: int
    predictor_exceptions: int


@dataclass(frozen=True)
class SubstreamEntry:
    kind: int
    flags: int
    offset: int
    stream_size: int
    raw_size: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Stream a typed SPC3 and count explicit IV32 cases not represented "
            "by early schedule-offset predictor classes."
        )
    )
    parser.add_argument("--spc3", type=Path, default=DEFAULT_SPC3)
    parser.add_argument("--predictor-json", type=Path, default=DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--secondhalf-csv", type=Path, default=DEFAULT_SECONDHALF_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--max-offset",
        type=int,
        default=14,
        help="Highest +N schedule-offset class to test. Default: 14 for cases +0..+14.",
    )
    parser.add_argument(
        "--sample-lanes",
        type=int,
        default=None,
        help="Process only the first N lane table entries for a quick smoke run.",
    )
    parser.add_argument(
        "--lanes",
        default="all",
        help="Lane selector: all, or comma-separated lane ids/ranges like 0x0000,0x1234-0x1238.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=4096,
        help="Print progress to stderr every N processed lanes. Use 0 to disable.",
    )
    return parser.parse_args()


def read_exact_at(handle: BinaryIO, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise RuntimeError(f"short read at offset {offset:,}: wanted {size:,}, got {len(data):,}")
    return data


def parse_header(handle: BinaryIO) -> Spc3Header:
    raw = read_exact_at(handle, 0, SPC3_HEADER_SIZE)
    unpacked = struct.unpack("<4s7I6Q", raw)
    magic = unpacked[0]
    if magic != b"SPC3":
        raise RuntimeError(f"not an SPC3 file: magic={magic!r}")
    header = Spc3Header(*unpacked[1:])
    if header.header_size != SPC3_HEADER_SIZE:
        raise RuntimeError(f"unsupported SPC3 header size: {header.header_size}")
    if header.expected_records != EXPECTED_RECORDS:
        raise RuntimeError(f"unsupported records-per-lane: {header.expected_records}")
    if header.table_entry_size != SPC3_TABLE_ENTRY_SIZE:
        raise RuntimeError(f"unsupported table entry size: {header.table_entry_size}")
    return header


def parse_lane_entries(handle: BinaryIO, header: Spc3Header) -> list[LaneEntry]:
    table_raw = read_exact_at(
        handle,
        header.table_offset,
        header.lane_count * header.table_entry_size,
    )
    entries: list[LaneEntry] = []
    entry_struct = struct.Struct("<4I10Q")
    for index in range(header.lane_count):
        offset = index * header.table_entry_size
        fields = entry_struct.unpack_from(table_raw, offset)
        entry = LaneEntry(*fields)
        if entry.level != header.level:
            raise RuntimeError(f"lane table level mismatch at index {index}: {entry.level} != {header.level}")
        if entry.level != 3 or entry.stream_kind != TYPED_LEVEL3_STREAM_KIND:
            raise RuntimeError(
                "this script expects typed level-3 streams; "
                f"lane 0x{entry.lane:04X} is level={entry.level} kind={entry.stream_kind}"
            )
        entries.append(entry)
    return entries


def parse_substreams(handle: BinaryIO, entry: LaneEntry) -> dict[int, SubstreamEntry]:
    raw = read_exact_at(
        handle,
        entry.stream_offset,
        TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE,
    )
    sub_struct = struct.Struct("<IIQQQ")
    substreams: dict[int, SubstreamEntry] = {}
    expected_offset = TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE
    for index in range(TYPED_SUBSTREAM_COUNT):
        sub = SubstreamEntry(*sub_struct.unpack_from(raw, index * TYPED_SUBSTREAM_ENTRY_SIZE))
        if sub.kind in substreams:
            raise RuntimeError(f"duplicate typed substream kind {sub.kind} on lane 0x{entry.lane:04X}")
        if sub.offset != expected_offset:
            raise RuntimeError(
                f"substream layout gap on lane 0x{entry.lane:04X}: "
                f"expected {expected_offset}, saw {sub.offset}"
            )
        if sub.offset + sub.stream_size > entry.stream_size:
            raise RuntimeError(f"substream extends past lane stream on lane 0x{entry.lane:04X}")
        substreams[sub.kind] = sub
        expected_offset += sub.stream_size

    required = {SUBSTREAM_TEMPLATE, SUBSTREAM_BITMAP, SUBSTREAM_VALUES}
    if set(substreams) != required:
        raise RuntimeError(f"missing typed substream on lane 0x{entry.lane:04X}: {sorted(substreams)}")
    return substreams


def codec_id_from_flags(flags: int) -> int:
    codec_id = flags & 0xFF
    return CODEC_ZLIB if codec_id == CODEC_LEGACY_AUTO else codec_id


def decode_substream(raw: bytes, sub: SubstreamEntry, *, lane: int) -> bytes:
    codec_id = codec_id_from_flags(sub.flags)
    if sub.raw_size == 0:
        if raw:
            raise RuntimeError(f"zero-raw substream has bytes on lane 0x{lane:04X}")
        return b""
    if codec_id == CODEC_NONE:
        decoded = raw
    elif codec_id == CODEC_ZLIB:
        decoded = zlib.decompress(raw)
    elif codec_id == CODEC_ZSTD:
        decoded = zstd.ZstdDecompressor().decompress(raw, max_output_size=sub.raw_size)
    else:
        raise RuntimeError(
            f"unsupported substream codec id {codec_id} on lane 0x{lane:04X}; "
            "this script supports none, zlib, and zstd"
        )
    if len(decoded) != sub.raw_size:
        raise RuntimeError(
            f"decoded substream size mismatch on lane 0x{lane:04X}: "
            f"wanted {sub.raw_size:,}, got {len(decoded):,}"
        )
    return decoded


def read_bitmap_and_values(handle: BinaryIO, entry: LaneEntry) -> tuple[bytes, np.ndarray]:
    substreams = parse_substreams(handle, entry)
    bitmap_sub = substreams[SUBSTREAM_BITMAP]
    values_sub = substreams[SUBSTREAM_VALUES]

    bitmap_raw = read_exact_at(
        handle,
        entry.stream_offset + bitmap_sub.offset,
        bitmap_sub.stream_size,
    )
    values_raw = read_exact_at(
        handle,
        entry.stream_offset + values_sub.offset,
        values_sub.stream_size,
    )
    bitmap = decode_substream(bitmap_raw, bitmap_sub, lane=entry.lane)
    values_bytes = decode_substream(values_raw, values_sub, lane=entry.lane)
    if len(bitmap) != EXPECTED_RECORDS // 8:
        raise RuntimeError(f"bad bitmap length on lane 0x{entry.lane:04X}: {len(bitmap):,}")
    if len(values_bytes) % 4:
        raise RuntimeError(f"partial u32 value stream on lane 0x{entry.lane:04X}")
    values = np.frombuffer(values_bytes, dtype="<u4")
    return bitmap, values


def load_embedded_predictor(handle: BinaryIO, header: Spc3Header) -> np.ndarray | None:
    if header.predictor_size == 0:
        return None
    expected_size = EXPECTED_RECORDS * 4
    stream = read_exact_at(handle, header.predictor_offset, header.predictor_size)
    if header.predictor_size == expected_size:
        raw = stream
    else:
        # SPC3 currently embeds the predictor as zlib-9 raw IV32 bytes. The
        # header stores only the compressed stream size, not a separate codec id.
        raw = zlib.decompress(stream)
    if len(raw) != expected_size:
        raise RuntimeError(
            f"embedded predictor decoded to {len(raw):,} bytes; wanted {expected_size:,}"
        )
    return np.frombuffer(raw, dtype="<u4").astype(np.uint32, copy=True)


def load_predictor_json(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_values = payload["iv32_by_pid_second_half_hex"]
    if len(raw_values) != EXPECTED_RECORDS:
        raise RuntimeError(f"predictor JSON has {len(raw_values):,} entries")
    values = np.array([int(value, 16) for value in raw_values], dtype=np.uint32)
    return values


def load_predictor(handle: BinaryIO, header: Spc3Header, predictor_json: Path) -> tuple[np.ndarray, str]:
    embedded = load_embedded_predictor(handle, header)
    if embedded is not None:
        return embedded, "embedded_spc3_predictor"
    return load_predictor_json(predictor_json), str(predictor_json)


def parse_int(raw: str) -> int:
    return int(raw.strip(), 0)


def load_secondhalf_t0_states(path: Path) -> np.ndarray:
    states = np.zeros(EXPECTED_RECORDS, dtype=np.uint32)
    seen = np.zeros(EXPECTED_RECORDS, dtype=np.bool_)
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"target_half_16bit", "t_minus", "rng_seed"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"secondhalf CSV missing required columns: {sorted(missing)}")
        for row in reader:
            if row["t_minus"] != "t-0":
                continue
            upper = parse_int(row["target_half_16bit"])
            if not 0 <= upper < EXPECTED_RECORDS:
                raise RuntimeError(f"target half outside u16 range: {row['target_half_16bit']!r}")
            if seen[upper]:
                raise RuntimeError(f"duplicate t-0 row for upper half 0x{upper:04X}")
            states[upper] = parse_int(row["rng_seed"]) & 0xFFFFFFFF
            seen[upper] = True
    if int(seen.sum()) != EXPECTED_RECORDS:
        raise RuntimeError(f"secondhalf CSV has {int(seen.sum()):,} unique t-0 rows")
    return states


def build_offset_candidates(
    predictor: np.ndarray,
    t0_states: np.ndarray,
    max_offset: int,
) -> list[np.ndarray]:
    if max_offset < 1:
        raise ValueError("max_offset must be at least 1")
    candidates: list[np.ndarray] = [predictor]
    states = t0_states.astype(np.uint64, copy=True)
    for _offset in range(1, max_offset + 1):
        states = (states * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT) & 0xFFFFFFFF
        future_upper = ((states >> 16) & 0xFFFF).astype(np.uint16)
        candidates.append(predictor[future_upper])
    return candidates


def parse_lane_selector(raw: str) -> set[int] | None:
    if raw.lower() == "all":
        return None
    lanes: set[int] = set()
    for item in raw.split(","):
        item = item.strip()
        if not item:
            continue
        if "-" in item:
            start_raw, end_raw = item.split("-", 1)
            start = parse_int(start_raw)
            end = parse_int(end_raw)
            if end < start:
                raise ValueError(f"bad lane range: {item!r}")
            lanes.update(range(start, end + 1))
        else:
            lanes.add(parse_int(item))
    for lane in lanes:
        if not 0 <= lane <= 0xFFFF:
            raise ValueError(f"lane outside u16 range: 0x{lane:X}")
    return lanes


def selected_entries(
    entries: Iterable[LaneEntry],
    lane_filter: set[int] | None,
    sample_lanes: int | None,
) -> list[LaneEntry]:
    selected = [entry for entry in entries if lane_filter is None or entry.lane in lane_filter]
    if sample_lanes is not None:
        if sample_lanes < 0:
            raise ValueError("sample_lanes must be non-negative")
        selected = selected[:sample_lanes]
    return selected


def percent(part: int, total: int) -> float:
    return (100.0 * part / total) if total else 0.0


def count_lanes(
    spc3_path: Path,
    entries: list[LaneEntry],
    predictor: np.ndarray,
    candidates: list[np.ndarray],
    progress_every: int,
) -> dict[str, object]:
    max_offset = len(candidates) - 1
    offset_match_counts = np.zeros(max_offset + 1, dtype=np.uint64)
    explicit_total = 0
    normal_predictor_hits = 0
    processed_cells = 0
    lanes_with_exceptions = 0
    mismatch_count = 0
    value_count_mismatches: list[dict[str, object]] = []
    start = time.perf_counter()

    with spc3_path.open("rb") as handle:
        for index, entry in enumerate(entries, 1):
            normal_predictor_hits += entry.predictor_matches
            explicit_total += entry.predictor_exceptions
            processed_cells += EXPECTED_RECORDS
            if entry.predictor_exceptions:
                lanes_with_exceptions += 1
                bitmap, xor_values = read_bitmap_and_values(handle, entry)
                bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8), bitorder="little")
                uppers = np.flatnonzero(bits).astype(np.uint32)
                if len(uppers) != len(xor_values) or len(uppers) != entry.predictor_exceptions:
                    value_count_mismatches.append(
                        {
                            "lane": f"0x{entry.lane:04X}",
                            "bitmap_count": int(len(uppers)),
                            "value_count": int(len(xor_values)),
                            "table_predictor_exceptions": int(entry.predictor_exceptions),
                        }
                    )
                    mismatch_count += 1
                actual_iv32 = predictor[uppers] ^ xor_values.astype(np.uint32, copy=False)
                unmatched = np.ones(len(uppers), dtype=np.bool_)
                for offset in range(0, max_offset + 1):
                    remaining = np.flatnonzero(unmatched)
                    if len(remaining) == 0:
                        break
                    remaining_uppers = uppers[remaining]
                    matched = actual_iv32[remaining] == candidates[offset][remaining_uppers]
                    match_count = int(matched.sum())
                    if match_count:
                        offset_match_counts[offset] += match_count
                        unmatched[remaining[matched]] = False

            if progress_every and index % progress_every == 0:
                elapsed = time.perf_counter() - start
                print(
                    f"processed {index:,}/{len(entries):,} lanes "
                    f"explicit={explicit_total:,} elapsed={elapsed:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )

    offset_matches = {f"+{offset}": int(offset_match_counts[offset]) for offset in range(1, max_offset + 1)}
    matched_0_to_14 = int(offset_match_counts[0:15].sum()) if max_offset >= 14 else int(offset_match_counts.sum())
    matched_1_to_14 = int(offset_match_counts[1:15].sum()) if max_offset >= 14 else int(offset_match_counts[1:].sum())
    matched_1_to_15 = int(offset_match_counts[1:16].sum()) if max_offset >= 15 else None
    explicit_not_case_ids_0_to_14 = explicit_total - matched_0_to_14
    explicit_not_offsets_1_to_15 = explicit_total - matched_1_to_15 if matched_1_to_15 is not None else None

    return {
        "lane_count_processed": len(entries),
        "lanes_with_explicit_cases": lanes_with_exceptions,
        "total_cells_processed": processed_cells,
        "normal_predictor_hits": int(normal_predictor_hits),
        "explicit_cases_total": int(explicit_total),
        "explicit_case_rate_pct_of_processed_cells": percent(int(explicit_total), processed_cells),
        "explicit_plus0_matches": int(offset_match_counts[0]),
        "offset_class_matches": {"+0": int(offset_match_counts[0]), **offset_matches},
        "matched_by_case_ids_0_to_14": matched_0_to_14,
        "matched_by_offsets_1_to_14": matched_1_to_14,
        "matched_by_offsets_1_to_15": matched_1_to_15,
        "explicit_not_represented_by_case_ids_0_to_14": int(explicit_not_case_ids_0_to_14),
        "explicit_not_represented_by_offsets_1_to_15": (
            int(explicit_not_offsets_1_to_15) if explicit_not_offsets_1_to_15 is not None else None
        ),
        "explicit_not_represented_by_case_ids_0_to_14_pct_of_explicit": percent(
            int(explicit_not_case_ids_0_to_14), int(explicit_total)
        ),
        "explicit_not_represented_by_offsets_1_to_15_pct_of_explicit": (
            percent(int(explicit_not_offsets_1_to_15), int(explicit_total))
            if explicit_not_offsets_1_to_15 is not None
            else None
        ),
        "value_count_mismatch_lane_count": mismatch_count,
        "value_count_mismatch_samples": value_count_mismatches[:20],
        "elapsed_seconds": time.perf_counter() - start,
    }


def main() -> int:
    args = parse_args()
    if args.max_offset < 14:
        raise ValueError("--max-offset must be at least 14 for the +0..+14 first-15 report fields")
    lane_filter = parse_lane_selector(args.lanes)

    start = time.perf_counter()
    with args.spc3.open("rb") as handle:
        header = parse_header(handle)
        entries = parse_lane_entries(handle, header)
        predictor, predictor_source = load_predictor(handle, header, args.predictor_json)

    t0_states = load_secondhalf_t0_states(args.secondhalf_csv)
    candidates = build_offset_candidates(predictor, t0_states, args.max_offset)
    selected = selected_entries(entries, lane_filter, args.sample_lanes)

    counts = count_lanes(
        args.spc3,
        selected,
        predictor,
        candidates,
        args.progress_every,
    )
    report = {
        "schema": "spc3_first15_fallback_counter.v1",
        "created_unix": time.time(),
        "inputs": {
            "spc3": str(args.spc3),
            "predictor_source": predictor_source,
            "predictor_json_fallback": str(args.predictor_json),
            "secondhalf_csv": str(args.secondhalf_csv),
        },
        "spc3_header": asdict(header),
        "selection": {
            "lanes": args.lanes,
            "sample_lanes": args.sample_lanes,
            "max_offset": args.max_offset,
        },
        "case_model": {
            "case_0": "normal predictor table value for this PID upper half",
            "case_N": (
                "predictor table value for the Nth later RNG upper-half target "
                "from the same secondhalf.csv t-0 state"
            ),
            "first_15_case_ids_0_to_14": "normal predictor plus offsets +1..+14",
            "offsets_1_to_15": "optional legacy comparison when --max-offset is at least 15",
            "note": (
                "These are compression-model offset classes; this count does not "
                "prove the gameplay cause of the alternate stream."
            ),
        },
        "counts": counts,
        "total_elapsed_seconds": time.perf_counter() - start,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report["counts"], indent=2, sort_keys=True))
    print(f"wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
