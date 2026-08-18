#!/usr/bin/env python3
"""Classify SPC3 IV32 values against post-R0 RNG insertion models.

This script tests the concrete question: if the upper PID half draw R0 is kept
fixed, can the observed final Gen 3 IV/egg/ability word be reproduced by
inserting extra RNG advances at specific later points in the egg path?

The SPC3 level-3 corpus stores normal predictor hits implicitly and predictor
misses as per-lane XOR values. The classifier streams those exception values,
generates candidate IV32 tables for a chosen R0 state source, and counts how
often corpus values match each simulated post-R0 offset class.
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
from typing import BinaryIO, Iterable, Sequence

import numpy as np
import zstandard as zstd


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
DEFAULT_SPC3 = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_PREDICTOR_JSON = ROOT / "Phase3SpindaBlocks" / "_phase3_pid_second_half_iv_reference.json"
DEFAULT_SECONDHALF_CSV = ROOT / "inputs" / "secondhalf.csv"
DEFAULT_OUTPUT = ROOT / "_tmp" / "spc3_exception_probe" / "iv_post_r0_offset_classifier.json"

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
UINT32_MASK = 0xFFFFFFFF
EGG_IV32_BIT = 0x40000000

CODEC_LEGACY_AUTO = 0
CODEC_NONE = 1
CODEC_ZLIB = 2
CODEC_ZSTD = 3

# PokeFinder's internal IV stat order is HP, Atk, Def, SpA, SpD, Spe.
# Project/report order is HP, Atk, Def, Spe, SpA, SpD.
PARENT_A_PF = np.array([31, 31, 31, 15, 31, 31], dtype=np.uint8)
PARENT_B_PF = np.array([20, 13, 31, 6, 16, 29], dtype=np.uint8)
FRLG_INHERIT_ORDER_PF = [0, 1, 2, 5, 3, 4]

BASE_MODEL_POSITIONS = {
    # Draw indexes are 1-based after R0. The documented project model is:
    # R1/R2 base IVs, R3-R5 inherited stat picks, R6-R8 parent picks.
    "doc": (1, 2, 3, 4, 5, 6, 7, 8),
    # PokeFinder Gen 3 FRLG breeding variants, represented as post-R0 draw
    # positions. These are useful cross-checks while the exact project route
    # calibration is being pinned down.
    "rsfrlg": (2, 3, 5, 6, 7, 8, 9, 10),
    "rsfrlg_split": (1, 3, 5, 6, 7, 8, 9, 10),
    "rsfrlg_alternate": (2, 3, 6, 7, 8, 9, 10, 11),
    "rsfrlg_mixed": (1, 2, 5, 6, 7, 8, 9, 10),
}

INSERTION_POINTS = [
    ("before_iv1", 0),
    ("between_iv1_iv2", 1),
    ("before_stat1", 2),
    ("between_stat1_stat2", 3),
    ("between_stat2_stat3", 4),
    ("before_parent1", 5),
    ("between_parent1_parent2", 6),
    ("between_parent2_parent3", 7),
]


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


@dataclass(frozen=True)
class CandidateClass:
    name: str
    positions: tuple[int, int, int, int, int, int, int, int]
    insertion_point: str | None
    extra_advances: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--spc3", type=Path, default=DEFAULT_SPC3)
    parser.add_argument("--predictor-json", type=Path, default=DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--secondhalf-csv", type=Path, default=DEFAULT_SECONDHALF_CSV)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--state-source",
        choices=("runtime", "csv"),
        default="runtime",
        help="Use runtime first-hit R0 states from --start-rng, or CSV t-0 rng_seed states.",
    )
    parser.add_argument(
        "--start-rng",
        default="0x2B0C94C1",
        help="Phase 3 observed start gRngValue used for --state-source runtime.",
    )
    parser.add_argument("--runtime-max-steps", type=int, default=4_000_000)
    parser.add_argument(
        "--base-model",
        choices=tuple(BASE_MODEL_POSITIONS),
        default="doc",
        help="Post-R0 baseline draw layout to shift.",
    )
    parser.add_argument(
        "--max-extra",
        type=int,
        default=2,
        help="Generate +1..+N extra-advance classes for every insertion point.",
    )
    parser.add_argument(
        "--lanes",
        default="all",
        help="Lane selector: all, or comma-separated lane ids/ranges like 0x0000,0x1234-0x1238.",
    )
    parser.add_argument(
        "--sample-lanes",
        type=int,
        default=None,
        help="Process only the first N selected lane table entries.",
    )
    parser.add_argument("--progress-every", type=int, default=4096)
    parser.add_argument(
        "--sample-unmatched",
        type=int,
        default=20,
        help="Maximum unmatched explicit examples to include in the report.",
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
    if unpacked[0] != b"SPC3":
        raise RuntimeError(f"not an SPC3 file: magic={unpacked[0]!r}")
    header = Spc3Header(*unpacked[1:])
    if header.header_size != SPC3_HEADER_SIZE:
        raise RuntimeError(f"unsupported SPC3 header size: {header.header_size}")
    if header.expected_records != EXPECTED_RECORDS:
        raise RuntimeError(f"unsupported records-per-lane: {header.expected_records}")
    if header.table_entry_size != SPC3_TABLE_ENTRY_SIZE:
        raise RuntimeError(f"unsupported table entry size: {header.table_entry_size}")
    return header


def parse_lane_entries(handle: BinaryIO, header: Spc3Header) -> list[LaneEntry]:
    table_raw = read_exact_at(handle, header.table_offset, header.lane_count * header.table_entry_size)
    entry_struct = struct.Struct("<4I10Q")
    entries: list[LaneEntry] = []
    for index in range(header.lane_count):
        entry = LaneEntry(*entry_struct.unpack_from(table_raw, index * header.table_entry_size))
        if entry.level != header.level:
            raise RuntimeError(f"lane table level mismatch at index {index}: {entry.level} != {header.level}")
        if entry.level != 3 or entry.stream_kind != TYPED_LEVEL3_STREAM_KIND:
            raise RuntimeError(
                "this script expects SPC3 v2 typed level-3 streams; "
                f"lane 0x{entry.lane:04X} is level={entry.level} kind={entry.stream_kind}"
            )
        entries.append(entry)
    return entries


def parse_substreams(handle: BinaryIO, entry: LaneEntry) -> dict[int, SubstreamEntry]:
    raw = read_exact_at(handle, entry.stream_offset, TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE)
    sub_struct = struct.Struct("<IIQQQ")
    substreams: dict[int, SubstreamEntry] = {}
    expected_offset = TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE
    for index in range(TYPED_SUBSTREAM_COUNT):
        sub = SubstreamEntry(*sub_struct.unpack_from(raw, index * TYPED_SUBSTREAM_ENTRY_SIZE))
        if sub.kind in substreams:
            raise RuntimeError(f"duplicate substream kind {sub.kind} on lane 0x{entry.lane:04X}")
        if sub.offset != expected_offset:
            raise RuntimeError(f"substream layout gap on lane 0x{entry.lane:04X}")
        if sub.offset + sub.stream_size > entry.stream_size:
            raise RuntimeError(f"substream extends past lane stream on lane 0x{entry.lane:04X}")
        substreams[sub.kind] = sub
        expected_offset += sub.stream_size
    required = {SUBSTREAM_TEMPLATE, SUBSTREAM_BITMAP, SUBSTREAM_VALUES}
    if set(substreams) != required:
        raise RuntimeError(f"missing typed substreams on lane 0x{entry.lane:04X}: {sorted(substreams)}")
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
        raise RuntimeError(f"unsupported codec id {codec_id} on lane 0x{lane:04X}")
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
    bitmap_raw = read_exact_at(handle, entry.stream_offset + bitmap_sub.offset, bitmap_sub.stream_size)
    values_raw = read_exact_at(handle, entry.stream_offset + values_sub.offset, values_sub.stream_size)
    bitmap = decode_substream(bitmap_raw, bitmap_sub, lane=entry.lane)
    values_bytes = decode_substream(values_raw, values_sub, lane=entry.lane)
    if len(bitmap) != EXPECTED_RECORDS // 8:
        raise RuntimeError(f"bad bitmap length on lane 0x{entry.lane:04X}: {len(bitmap):,}")
    if len(values_bytes) % 4:
        raise RuntimeError(f"partial u32 value stream on lane 0x{entry.lane:04X}")
    return bitmap, np.frombuffer(values_bytes, dtype="<u4")


def load_embedded_predictor(handle: BinaryIO, header: Spc3Header) -> np.ndarray | None:
    if header.predictor_size == 0:
        return None
    stream = read_exact_at(handle, header.predictor_offset, header.predictor_size)
    if header.predictor_size == EXPECTED_RECORDS * 4:
        raw = stream
    else:
        raw = zlib.decompress(stream)
    if len(raw) != EXPECTED_RECORDS * 4:
        raise RuntimeError(f"embedded predictor decoded to {len(raw):,} bytes")
    return np.frombuffer(raw, dtype="<u4").astype(np.uint32, copy=True)


def load_predictor_json(path: Path) -> np.ndarray:
    payload = json.loads(path.read_text(encoding="utf-8"))
    raw_values = payload["iv32_by_pid_second_half_hex"]
    if len(raw_values) != EXPECTED_RECORDS:
        raise RuntimeError(f"predictor JSON has {len(raw_values):,} entries")
    return np.array([int(value, 16) for value in raw_values], dtype=np.uint32)


def load_predictor(handle: BinaryIO, header: Spc3Header, predictor_json: Path) -> tuple[np.ndarray, str]:
    embedded = load_embedded_predictor(handle, header)
    if embedded is not None:
        return embedded, "embedded_spc3_predictor"
    return load_predictor_json(predictor_json), str(predictor_json)


def parse_int(raw: str) -> int:
    return int(raw.strip(), 0)


def lcrng_next(state: int | np.ndarray) -> int | np.ndarray:
    return (state * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT) & UINT32_MASK


def load_csv_r0_states(path: Path) -> np.ndarray:
    states = np.zeros(EXPECTED_RECORDS, dtype=np.uint32)
    seen = np.zeros(EXPECTED_RECORDS, dtype=np.bool_)
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required = {"target_half_16bit", "t_minus", "rng_seed"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise RuntimeError(f"secondhalf CSV missing required columns: {sorted(missing)}")
        for row in reader:
            if row["t_minus"].lower() != "t-0":
                continue
            upper = parse_int(row["target_half_16bit"])
            if seen[upper]:
                raise RuntimeError(f"duplicate t-0 row for upper half 0x{upper:04X}")
            states[upper] = parse_int(row["rng_seed"]) & UINT32_MASK
            seen[upper] = True
    if int(seen.sum()) != EXPECTED_RECORDS:
        raise RuntimeError(f"secondhalf CSV has {int(seen.sum()):,} unique t-0 rows")
    return states


def build_runtime_r0_states(start_rng: int, max_steps: int) -> tuple[np.ndarray, int]:
    states = np.zeros(EXPECTED_RECORDS, dtype=np.uint32)
    seen = np.zeros(EXPECTED_RECORDS, dtype=np.bool_)
    state = start_rng & UINT32_MASK
    count = 0
    final_step = 0
    for step in range(1, max_steps + 1):
        state = int(lcrng_next(state))
        upper = (state >> 16) & 0xFFFF
        if not seen[upper]:
            seen[upper] = True
            states[upper] = state
            count += 1
            final_step = step
            if count == EXPECTED_RECORDS:
                return states, final_step
    raise RuntimeError(f"runtime schedule covered {count:,} upper halves in {max_steps:,} steps")


def build_rng_words(r0_states: np.ndarray, max_draw: int) -> np.ndarray:
    words = np.empty((max_draw + 1, EXPECTED_RECORDS), dtype=np.uint16)
    words[0].fill(0)
    states = r0_states.astype(np.uint64, copy=True)
    for draw in range(1, max_draw + 1):
        states = (states * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT) & UINT32_MASK
        words[draw] = ((states >> 16) & 0xFFFF).astype(np.uint16)
    return words


def shifted_positions(
    base_positions: Sequence[int],
    insertion_index: int,
    extra_advances: int,
) -> tuple[int, int, int, int, int, int, int, int]:
    return tuple(
        int(position + (extra_advances if index >= insertion_index else 0))
        for index, position in enumerate(base_positions)
    )


def build_candidate_classes(base_model: str, max_extra: int) -> list[CandidateClass]:
    if max_extra < 0:
        raise ValueError("--max-extra must be non-negative")
    base = tuple(BASE_MODEL_POSITIONS[base_model])
    classes = [CandidateClass("normal", base, None, 0)]
    for extra in range(1, max_extra + 1):
        for point_name, insertion_index in INSERTION_POINTS:
            classes.append(
                CandidateClass(
                    name=f"{point_name}+{extra}",
                    positions=shifted_positions(base, insertion_index, extra),
                    insertion_point=point_name,
                    extra_advances=extra,
                )
            )
    return classes


def pack_pf_ivs_to_raw_iv32(ivs_pf: np.ndarray) -> np.ndarray:
    hp = ivs_pf[0].astype(np.uint32)
    atk = ivs_pf[1].astype(np.uint32)
    defense = ivs_pf[2].astype(np.uint32)
    spa = ivs_pf[3].astype(np.uint32)
    spd = ivs_pf[4].astype(np.uint32)
    spe = ivs_pf[5].astype(np.uint32)
    return (
        EGG_IV32_BIT
        | hp
        | (atk << 5)
        | (defense << 10)
        | (spe << 15)
        | (spa << 20)
        | (spd << 25)
    ).astype(np.uint32)


def apply_frlg_inheritance(
    ivs_pf: np.ndarray,
    inh: list[np.ndarray],
    par: list[np.ndarray],
) -> np.ndarray:
    available = np.tile(np.arange(6, dtype=np.uint8).reshape(6, 1), (1, EXPECTED_RECORDS))
    uppers = np.arange(EXPECTED_RECORDS)
    for pick_index, size in enumerate((5, 4, None)):
        stat_values = available[inh[pick_index], uppers]
        real_stats = np.take(np.array(FRLG_INHERIT_ORDER_PF, dtype=np.uint8), stat_values)
        parent_values = np.where(
            par[pick_index].astype(np.bool_),
            PARENT_B_PF[real_stats],
            PARENT_A_PF[real_stats],
        )
        ivs_pf[real_stats, uppers] = parent_values
        if size is not None:
            # FRLG's bug removes by stat value, not by the randomly chosen
            # slot index. This mirrors PokeFinder's RS/FRLG inheritance model.
            for stat_value in range(6):
                affected = stat_values == stat_value
                if not bool(affected.any()):
                    continue
                for i in range(stat_value, size):
                    available[i, affected] = available[i + 1, affected]
    return ivs_pf


def generate_candidate_table(words: np.ndarray, classes: Sequence[CandidateClass]) -> np.ndarray:
    table = np.empty((len(classes), EXPECTED_RECORDS), dtype=np.uint32)
    for class_index, candidate in enumerate(classes):
        iv1_pos, iv2_pos, stat1_pos, stat2_pos, stat3_pos, par1_pos, par2_pos, par3_pos = candidate.positions
        iv1 = words[iv1_pos].astype(np.uint16, copy=False)
        iv2 = words[iv2_pos].astype(np.uint16, copy=False)

        ivs_pf = np.empty((6, EXPECTED_RECORDS), dtype=np.uint8)
        ivs_pf[0] = (iv1 & 31).astype(np.uint8)
        ivs_pf[1] = ((iv1 >> 5) & 31).astype(np.uint8)
        ivs_pf[2] = ((iv1 >> 10) & 31).astype(np.uint8)
        ivs_pf[3] = ((iv2 >> 5) & 31).astype(np.uint8)
        ivs_pf[4] = ((iv2 >> 10) & 31).astype(np.uint8)
        ivs_pf[5] = (iv2 & 31).astype(np.uint8)

        inh = [
            (words[stat1_pos] % 6).astype(np.uint8),
            (words[stat2_pos] % 5).astype(np.uint8),
            (words[stat3_pos] % 4).astype(np.uint8),
        ]
        par = [
            (words[par1_pos] % 2).astype(np.uint8),
            (words[par2_pos] % 2).astype(np.uint8),
            (words[par3_pos] % 2).astype(np.uint8),
        ]
        table[class_index] = pack_pf_ivs_to_raw_iv32(apply_frlg_inheritance(ivs_pf, inh, par))
    return table


def classify_values(uppers: np.ndarray, actual: np.ndarray, candidate_table: np.ndarray) -> np.ndarray:
    class_ids = np.full(len(uppers), -1, dtype=np.int16)
    remaining = np.ones(len(uppers), dtype=np.bool_)
    for class_index in range(candidate_table.shape[0]):
        remaining_idx = np.flatnonzero(remaining)
        if len(remaining_idx) == 0:
            break
        remaining_uppers = uppers[remaining_idx]
        matched = actual[remaining_idx] == candidate_table[class_index, remaining_uppers]
        if bool(matched.any()):
            hit_idx = remaining_idx[matched]
            class_ids[hit_idx] = class_index
            remaining[hit_idx] = False
    return class_ids


def count_classes(class_ids: np.ndarray, class_count: int) -> tuple[np.ndarray, int]:
    matched = class_ids >= 0
    counts = np.bincount(class_ids[matched], minlength=class_count).astype(np.uint64)
    return counts, int((~matched).sum())


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
            raise ValueError("--sample-lanes must be non-negative")
        selected = selected[:sample_lanes]
    return selected


def percent(part: int, total: int) -> float:
    return (100.0 * part / total) if total else 0.0


def class_counts_to_json(counts: np.ndarray, classes: Sequence[CandidateClass], total: int) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for index, count in enumerate(counts):
        rows.append(
            {
                "class_id": index,
                "name": classes[index].name,
                "count": int(count),
                "pct": percent(int(count), total),
                "positions": list(classes[index].positions),
            }
        )
    return rows


def run_classification(
    spc3_path: Path,
    entries: Sequence[LaneEntry],
    predictor: np.ndarray,
    candidate_table: np.ndarray,
    classes: Sequence[CandidateClass],
    progress_every: int,
    sample_unmatched: int,
) -> dict[str, object]:
    started = time.perf_counter()
    all_uppers = np.arange(EXPECTED_RECORDS, dtype=np.uint32)
    predictor_class_ids = classify_values(all_uppers, predictor, candidate_table)
    predictor_counts, predictor_unmatched = count_classes(predictor_class_ids, len(classes))

    explicit_counts = np.zeros(len(classes), dtype=np.uint64)
    explicit_unmatched = 0
    all_counts = np.zeros(len(classes), dtype=np.int64)
    all_unmatched = 0
    lane_rows: list[dict[str, object]] = []
    unmatched_samples: list[dict[str, object]] = []

    total_cells = len(entries) * EXPECTED_RECORDS
    explicit_total = 0
    predictor_hit_total = 0
    value_count_mismatch_lane_count = 0

    with spc3_path.open("rb") as handle:
        for index, entry in enumerate(entries, 1):
            lane_all_counts = predictor_counts.astype(np.int64, copy=True)
            lane_all_unmatched = predictor_unmatched
            lane_explicit_counts = np.zeros(len(classes), dtype=np.uint64)
            lane_explicit_unmatched = 0

            explicit_total += int(entry.predictor_exceptions)
            predictor_hit_total += int(entry.predictor_matches)

            if entry.predictor_exceptions:
                bitmap, xor_values = read_bitmap_and_values(handle, entry)
                bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8), bitorder="little")
                uppers = np.flatnonzero(bits).astype(np.uint32)
                if len(uppers) != len(xor_values) or len(uppers) != entry.predictor_exceptions:
                    value_count_mismatch_lane_count += 1
                actual = predictor[uppers] ^ xor_values.astype(np.uint32, copy=False)
                explicit_class_ids = classify_values(uppers, actual, candidate_table)
                lane_explicit_counts, lane_explicit_unmatched = count_classes(explicit_class_ids, len(classes))

                old_counts, old_unmatched = count_classes(predictor_class_ids[uppers], len(classes))
                lane_all_counts -= old_counts.astype(np.int64)
                lane_all_unmatched -= old_unmatched
                lane_all_counts += lane_explicit_counts.astype(np.int64)
                lane_all_unmatched += lane_explicit_unmatched

                if len(unmatched_samples) < sample_unmatched and lane_explicit_unmatched:
                    unmatched_idx = np.flatnonzero(explicit_class_ids < 0)
                    for local_index in unmatched_idx[: sample_unmatched - len(unmatched_samples)]:
                        upper = int(uppers[local_index])
                        unmatched_samples.append(
                            {
                                "lane": f"0x{entry.lane:04X}",
                                "upper": f"0x{upper:04X}",
                                "actual_iv32": f"0x{int(actual[local_index]):08X}",
                                "predictor_iv32": f"0x{int(predictor[upper]):08X}",
                            }
                        )

            explicit_counts += lane_explicit_counts
            explicit_unmatched += lane_explicit_unmatched
            all_counts += lane_all_counts
            all_unmatched += lane_all_unmatched
            lane_rows.append(
                {
                    "lane": f"0x{entry.lane:04X}",
                    "predictor_exceptions": int(entry.predictor_exceptions),
                    "explicit_matched": int(lane_explicit_counts.sum()),
                    "explicit_unmatched": int(lane_explicit_unmatched),
                    "explicit_match_pct": percent(int(lane_explicit_counts.sum()), int(entry.predictor_exceptions)),
                    "all_unmatched": int(lane_all_unmatched),
                }
            )

            if progress_every and (index % progress_every == 0 or index == len(entries)):
                elapsed = time.perf_counter() - started
                print(
                    f"classified {index:,}/{len(entries):,} lanes "
                    f"explicit={explicit_total:,} elapsed={elapsed:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )

    explicit_matched = int(explicit_counts.sum())
    all_matched = int(all_counts.sum())
    predictor_matched = int(predictor_counts.sum())
    return {
        "elapsed_seconds": time.perf_counter() - started,
        "lane_count_processed": len(entries),
        "total_cells_processed": total_cells,
        "predictor_hit_cells": predictor_hit_total,
        "explicit_cases_total": explicit_total,
        "value_count_mismatch_lane_count": value_count_mismatch_lane_count,
        "predictor_table_sanity": {
            "matched": predictor_matched,
            "unmatched": int(predictor_unmatched),
            "match_pct": percent(predictor_matched, EXPECTED_RECORDS),
            "class_counts": class_counts_to_json(predictor_counts, classes, EXPECTED_RECORDS),
        },
        "explicit_cases": {
            "matched": explicit_matched,
            "unmatched": int(explicit_unmatched),
            "match_pct": percent(explicit_matched, explicit_total),
            "class_counts": class_counts_to_json(explicit_counts, classes, explicit_total),
        },
        "all_cells": {
            "matched": all_matched,
            "unmatched": int(all_unmatched),
            "match_pct": percent(all_matched, total_cells),
            "class_counts": class_counts_to_json(all_counts.astype(np.uint64), classes, total_cells),
        },
        "unmatched_explicit_samples": unmatched_samples,
        "lane_samples": lane_rows[:128],
    }


def main() -> int:
    args = parse_args()
    if args.max_extra < 0:
        raise SystemExit("--max-extra must be non-negative")

    start = time.perf_counter()
    lane_filter = parse_lane_selector(args.lanes)
    with args.spc3.open("rb") as handle:
        header = parse_header(handle)
        entries = parse_lane_entries(handle, header)
        predictor, predictor_source = load_predictor(handle, header, args.predictor_json)

    selected = selected_entries(entries, lane_filter, args.sample_lanes)
    classes = build_candidate_classes(args.base_model, args.max_extra)
    max_draw = max(max(candidate.positions) for candidate in classes)

    if args.state_source == "runtime":
        start_rng = parse_int(args.start_rng)
        r0_states, schedule_cover_step = build_runtime_r0_states(start_rng, args.runtime_max_steps)
        state_source_meta = {
            "kind": "runtime",
            "start_rng": f"0x{start_rng:08X}",
            "runtime_max_steps": args.runtime_max_steps,
            "covered_all_uppers_at_step": schedule_cover_step,
        }
    else:
        r0_states = load_csv_r0_states(args.secondhalf_csv)
        state_source_meta = {
            "kind": "csv",
            "secondhalf_csv": str(args.secondhalf_csv),
        }

    words = build_rng_words(r0_states, max_draw)
    candidate_table = generate_candidate_table(words, classes)
    results = run_classification(
        args.spc3,
        selected,
        predictor,
        candidate_table,
        classes,
        args.progress_every,
        args.sample_unmatched,
    )

    report = {
        "schema": "spc3_iv_post_r0_offset_classifier.v1",
        "created_unix": time.time(),
        "inputs": {
            "spc3": str(args.spc3),
            "predictor_source": predictor_source,
            "predictor_json_fallback": str(args.predictor_json),
        },
        "spc3_header": asdict(header),
        "selection": {
            "lanes": args.lanes,
            "sample_lanes": args.sample_lanes,
            "selected_lane_count": len(selected),
        },
        "model": {
            "state_source": state_source_meta,
            "base_model": args.base_model,
            "base_positions": list(BASE_MODEL_POSITIONS[args.base_model]),
            "max_extra": args.max_extra,
            "class_priority_note": "first matching class wins; normal is tested before shifted classes",
            "parent_ivs_project_order": {
                "A_male": {"hp": 31, "atk": 31, "def": 31, "spe": 31, "spa": 15, "spd": 31},
                "B_female": {"hp": 20, "atk": 13, "def": 31, "spe": 29, "spa": 6, "spd": 16},
            },
            "egg_bit": "forced to 1 in generated IV32; ability bit remains 0",
            "classes": [
                {
                    "class_id": index,
                    "name": candidate.name,
                    "positions": list(candidate.positions),
                    "insertion_point": candidate.insertion_point,
                    "extra_advances": candidate.extra_advances,
                }
                for index, candidate in enumerate(classes)
            ],
        },
        "results": results,
        "total_elapsed_seconds": time.perf_counter() - start,
    }

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")

    summary = {
        "output": str(args.output),
        "state_source": args.state_source,
        "base_model": args.base_model,
        "max_extra": args.max_extra,
        "lanes": len(selected),
        "predictor_table_match_pct": results["predictor_table_sanity"]["match_pct"],
        "explicit_match_pct": results["explicit_cases"]["match_pct"],
        "explicit_matched": results["explicit_cases"]["matched"],
        "explicit_unmatched": results["explicit_cases"]["unmatched"],
        "all_cells_match_pct": results["all_cells"]["match_pct"],
    }
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
