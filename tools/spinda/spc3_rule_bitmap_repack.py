#!/usr/bin/env python3
"""Repack typed level-3 SPC3 with a global rule-predicted exception bitmap.

This is an experimental SPC3 v3 transform for the Phase 3 Spinda corpus. It
keeps the existing predictor, per-lane template records, and per-lane XOR value
streams, but replaces each actual exception bitmap with:

    residual_bitmap = actual_exception_bitmap XOR rule_bitmap[lane]

The rule table used here is the measured `lane low byte + lane mod 24 + upper`
majority classifier. It reduces bitmap entropy while preserving exact decode
when paired with the unchanged XOR values.

The current public SPC3 v2 tools do not understand this v3 stream kind. This
script can both pack and verify the new container against a source v2 SPC3.
"""

from __future__ import annotations

import argparse
import json
import struct
import time
import zlib
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import BinaryIO

import numpy as np
import zstandard as zstd


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_OUTPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.rule-lm24-lowbyte.spc3"
DEFAULT_REPORT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.rule-lm24-lowbyte.verify.json"

SPC3_HEADER_SIZE = 80
SPC3_TABLE_ENTRY_SIZE = 96
TYPED_SUBSTREAM_ENTRY_SIZE = 32
TYPED_SUBSTREAM_COUNT = 3
EXPECTED_RECORDS = 0x10000
RECORD_SIZE = 80
BITMAP_BYTES = EXPECTED_RECORDS // 8

SPC3_MAGIC = b"SPC3"
SPC3_VERSION_V2 = 2
SPC3_VERSION_RULE_V3 = 3
SPC3_LEVEL = 3
SPC3_FLAG_PREDICTOR_EMBEDDED = 0x00000001
SPC3_FLAG_RULE_BITMAP = 0x00000002

STREAM_KIND_TYPED_LEVEL3 = 4
STREAM_KIND_RULE_BITMAP_LEVEL3 = 5

SUBSTREAM_TEMPLATE = 1
SUBSTREAM_BITMAP = 2
SUBSTREAM_VALUES = 3

CODEC_LEGACY_AUTO = 0
CODEC_NONE = 1
CODEC_ZLIB = 2
CODEC_ZSTD = 3

RULE_MAGIC = b"SPC3RUL1"
RULE_VERSION = 1
RULE_ID_LANE_MOD24_LOWBYTE_UPPER = 1
RULE_NAME = "lane_mod24_lowbyte_plus_upper_majority"
RULE_GROUP_COUNT = 768
RULE_HEADER_STRUCT = struct.Struct("<8s6I")

HEADER_STRUCT = struct.Struct("<4s7I6Q")
TABLE_ENTRY_STRUCT = struct.Struct("<4I10Q")
SUBSTREAM_ENTRY_STRUCT = struct.Struct("<IIQQQ")


@dataclass(frozen=True)
class Header:
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


@dataclass
class RuleInfo:
    rule_name: str
    group_count: int
    raw_size: int
    compressed_size: int
    raw_crc32: int
    predicted_miss_cells: int
    residual_error_cells: int
    residual_error_rate_pct: float
    group_size_min: int
    group_size_max: int


def pack_codec_flags(codec_id: int, level: int = 0, settings: int = 0) -> int:
    return (codec_id & 0xFF) | ((level & 0xFF) << 8) | ((settings & 0xFF) << 16)


def codec_id_from_flags(flags: int) -> int:
    codec_id = flags & 0xFF
    return CODEC_ZLIB if codec_id == CODEC_LEGACY_AUTO else codec_id


def codec_name(flags: int) -> str:
    codec_id = codec_id_from_flags(flags)
    if codec_id == CODEC_NONE:
        return "none"
    if codec_id == CODEC_ZLIB:
        return "zlib"
    if codec_id == CODEC_ZSTD:
        return "zstd"
    return f"unknown_{codec_id}"


def read_exact_at(handle: BinaryIO, offset: int, size: int) -> bytes:
    handle.seek(offset)
    data = handle.read(size)
    if len(data) != size:
        raise RuntimeError(f"short read at {offset:,}: wanted {size:,}, got {len(data):,}")
    return data


def parse_header_from_bytes(raw: bytes) -> Header:
    fields = HEADER_STRUCT.unpack(raw)
    if fields[0] != SPC3_MAGIC:
        raise RuntimeError(f"not an SPC3 file: magic={fields[0]!r}")
    header = Header(*fields[1:])
    if header.header_size != SPC3_HEADER_SIZE:
        raise RuntimeError(f"unsupported header size: {header.header_size}")
    if header.expected_records != EXPECTED_RECORDS or header.record_size != RECORD_SIZE:
        raise RuntimeError("unsupported record geometry")
    if header.table_entry_size != SPC3_TABLE_ENTRY_SIZE:
        raise RuntimeError(f"unsupported table entry size: {header.table_entry_size}")
    if header.level != SPC3_LEVEL:
        raise RuntimeError(f"unsupported level: {header.level}")
    return header


def parse_header(handle: BinaryIO) -> Header:
    return parse_header_from_bytes(read_exact_at(handle, 0, SPC3_HEADER_SIZE))


def pack_header(header: Header) -> bytes:
    return HEADER_STRUCT.pack(
        SPC3_MAGIC,
        header.version,
        header.level,
        header.lane_count,
        header.expected_records,
        header.record_size,
        header.flags,
        header.header_size,
        header.predictor_offset,
        header.predictor_size,
        header.table_offset,
        header.table_entry_size,
        header.data_offset,
        header.data_size,
    )


def parse_lane_entries(handle: BinaryIO, header: Header) -> list[LaneEntry]:
    raw = read_exact_at(handle, header.table_offset, header.lane_count * header.table_entry_size)
    entries: list[LaneEntry] = []
    expected_offset = header.data_offset
    for index in range(header.lane_count):
        pos = index * header.table_entry_size
        entry = LaneEntry(*TABLE_ENTRY_STRUCT.unpack_from(raw, pos))
        if entry.lane > 0xFFFF or entry.level != header.level:
            raise RuntimeError(f"bad table entry at index {index}")
        if entry.stream_offset != expected_offset:
            raise RuntimeError(f"stream layout gap at lane 0x{entry.lane:04X}")
        expected_offset += entry.stream_size
        entries.append(entry)
    if expected_offset != header.data_offset + header.data_size:
        raise RuntimeError("data section has trailing or missing stream bytes")
    return entries


def pack_lane_entry(entry: LaneEntry) -> bytes:
    return TABLE_ENTRY_STRUCT.pack(
        entry.lane,
        entry.level,
        entry.stream_kind,
        entry.flags,
        entry.source_zip_size,
        entry.source_zip_crc32,
        entry.source_zip_fnv64,
        entry.original_payload_crc32,
        entry.rebuilt_payload_crc32,
        entry.stream_offset,
        entry.stream_size,
        entry.uncompressed_model_size,
        entry.predictor_matches,
        entry.predictor_exceptions,
    )


def parse_substreams(handle: BinaryIO, entry: LaneEntry, *, expected_stream_kinds: set[int]) -> dict[int, SubstreamEntry]:
    if entry.stream_kind not in expected_stream_kinds:
        raise RuntimeError(f"lane 0x{entry.lane:04X} has unsupported stream kind {entry.stream_kind}")
    raw = read_exact_at(handle, entry.stream_offset, TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE)
    substreams: dict[int, SubstreamEntry] = {}
    expected_offset = TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE
    for index in range(TYPED_SUBSTREAM_COUNT):
        sub = SubstreamEntry(*SUBSTREAM_ENTRY_STRUCT.unpack_from(raw, index * TYPED_SUBSTREAM_ENTRY_SIZE))
        if sub.kind in substreams:
            raise RuntimeError(f"duplicate substream kind {sub.kind} on lane 0x{entry.lane:04X}")
        if sub.kind not in {SUBSTREAM_TEMPLATE, SUBSTREAM_BITMAP, SUBSTREAM_VALUES}:
            raise RuntimeError(f"bad substream kind {sub.kind} on lane 0x{entry.lane:04X}")
        if sub.offset != expected_offset:
            raise RuntimeError(f"substream layout gap on lane 0x{entry.lane:04X}")
        if sub.offset + sub.stream_size > entry.stream_size:
            raise RuntimeError(f"substream extends past lane stream on lane 0x{entry.lane:04X}")
        substreams[sub.kind] = sub
        expected_offset += sub.stream_size
    if expected_offset != entry.stream_size:
        raise RuntimeError(f"lane 0x{entry.lane:04X} has trailing stream bytes")
    if set(substreams) != {SUBSTREAM_TEMPLATE, SUBSTREAM_BITMAP, SUBSTREAM_VALUES}:
        raise RuntimeError(f"lane 0x{entry.lane:04X} missing typed substreams")
    return substreams


def pack_substream_entry(sub: SubstreamEntry) -> bytes:
    return SUBSTREAM_ENTRY_STRUCT.pack(sub.kind, sub.flags, sub.offset, sub.stream_size, sub.raw_size)


def decode_payload(raw: bytes, sub: SubstreamEntry, *, label: str) -> bytes:
    codec_id = codec_id_from_flags(sub.flags)
    if sub.raw_size == 0:
        if raw:
            raise RuntimeError(f"{label}: zero raw size but stream has bytes")
        return b""
    if codec_id == CODEC_NONE:
        decoded = raw
    elif codec_id == CODEC_ZLIB:
        decoded = zlib.decompress(raw)
    elif codec_id == CODEC_ZSTD:
        decoded = zstd.ZstdDecompressor().decompress(raw, max_output_size=sub.raw_size)
    else:
        raise RuntimeError(f"{label}: unsupported codec id {codec_id}")
    if len(decoded) != sub.raw_size:
        raise RuntimeError(f"{label}: decoded {len(decoded):,}, expected {sub.raw_size:,}")
    return decoded


def read_substream_raw(handle: BinaryIO, entry: LaneEntry, sub: SubstreamEntry) -> bytes:
    return read_exact_at(handle, entry.stream_offset + sub.offset, sub.stream_size)


def read_decoded_bitmap(handle: BinaryIO, entry: LaneEntry, substreams: dict[int, SubstreamEntry]) -> bytes:
    sub = substreams[SUBSTREAM_BITMAP]
    if sub.raw_size != BITMAP_BYTES:
        raise RuntimeError(f"lane 0x{entry.lane:04X} bitmap raw size is {sub.raw_size}")
    raw = read_substream_raw(handle, entry, sub)
    return decode_payload(raw, sub, label=f"lane 0x{entry.lane:04X} bitmap")


def build_group_index() -> tuple[list[tuple[int, int]], dict[tuple[int, int], int]]:
    keys = sorted({(lane & 0xFF, lane % 24) for lane in range(0x10000)})
    if len(keys) != RULE_GROUP_COUNT:
        raise RuntimeError(f"expected {RULE_GROUP_COUNT} reachable groups, saw {len(keys)}")
    return keys, {key: index for index, key in enumerate(keys)}


GROUP_KEYS, GROUP_INDEX = build_group_index()


def lane_group(lane: int) -> int:
    return GROUP_INDEX[(lane & 0xFF, lane % 24)]


def compute_rule_table(input_path: Path, progress_every: int) -> tuple[bytes, RuleInfo]:
    start = time.perf_counter()
    group_sizes = np.zeros(RULE_GROUP_COUNT, dtype=np.uint16)
    counts = np.zeros((RULE_GROUP_COUNT, EXPECTED_RECORDS), dtype=np.uint16)

    with input_path.open("rb") as handle:
        header = parse_header(handle)
        if header.version != SPC3_VERSION_V2 or not (header.flags & SPC3_FLAG_PREDICTOR_EMBEDDED):
            raise RuntimeError("input must be an SPC3 v2 typed level-3 file with embedded predictor")
        entries = parse_lane_entries(handle, header)
        for index, entry in enumerate(entries, 1):
            substreams = parse_substreams(handle, entry, expected_stream_kinds={STREAM_KIND_TYPED_LEVEL3})
            bitmap = read_decoded_bitmap(handle, entry, substreams)
            group = lane_group(entry.lane)
            group_sizes[group] += 1
            bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8), bitorder="little")
            counts[group] += bits.astype(np.uint16, copy=False)
            if progress_every and (index % progress_every == 0 or index == len(entries)):
                print(f"rule pass: {index}/{len(entries)} lanes")

    rule_bits = counts * 2 > group_sizes[:, None]
    predicted_miss_cells = int(rule_bits.sum())
    residual_errors = int(np.where(rule_bits, group_sizes[:, None] - counts, counts).sum())
    packed = np.packbits(rule_bits.astype(np.uint8, copy=False), axis=1, bitorder="little")
    rule_raw = packed.tobytes()
    if len(rule_raw) != RULE_GROUP_COUNT * BITMAP_BYTES:
        raise RuntimeError(f"bad rule raw size {len(rule_raw):,}")

    info = RuleInfo(
        rule_name=RULE_NAME,
        group_count=RULE_GROUP_COUNT,
        raw_size=len(rule_raw),
        compressed_size=0,
        raw_crc32=zlib.crc32(rule_raw) & 0xFFFFFFFF,
        predicted_miss_cells=predicted_miss_cells,
        residual_error_cells=residual_errors,
        residual_error_rate_pct=residual_errors / (0x10000 * 0x10000) * 100.0,
        group_size_min=int(group_sizes.min()),
        group_size_max=int(group_sizes.max()),
    )
    print(
        "rule built: "
        f"predicted_miss_cells={info.predicted_miss_cells:,} "
        f"residual_errors={info.residual_error_cells:,} "
        f"residual_rate={info.residual_error_rate_pct:.6f}% "
        f"elapsed={time.perf_counter() - start:.1f}s"
    )
    return rule_raw, info


def make_rule_stream(rule_raw: bytes, zstd_level: int, info: RuleInfo) -> bytes:
    compressed = zstd.ZstdCompressor(level=zstd_level).compress(rule_raw)
    info.compressed_size = len(compressed)
    header = RULE_HEADER_STRUCT.pack(
        RULE_MAGIC,
        RULE_VERSION,
        RULE_ID_LANE_MOD24_LOWBYTE_UPPER,
        RULE_GROUP_COUNT,
        EXPECTED_RECORDS,
        len(rule_raw),
        zlib.crc32(rule_raw) & 0xFFFFFFFF,
    )
    return header + compressed


def parse_rule_stream(raw: bytes) -> tuple[bytes, dict[str, int | str]]:
    if len(raw) < RULE_HEADER_STRUCT.size:
        raise RuntimeError("rule stream is too short")
    magic, version, rule_id, group_count, records, raw_size, raw_crc32 = RULE_HEADER_STRUCT.unpack_from(raw)
    if magic != RULE_MAGIC:
        raise RuntimeError(f"bad rule magic: {magic!r}")
    if version != RULE_VERSION or rule_id != RULE_ID_LANE_MOD24_LOWBYTE_UPPER:
        raise RuntimeError(f"unsupported rule version/id: {version}/{rule_id}")
    if group_count != RULE_GROUP_COUNT or records != EXPECTED_RECORDS or raw_size != RULE_GROUP_COUNT * BITMAP_BYTES:
        raise RuntimeError("rule stream geometry mismatch")
    compressed = raw[RULE_HEADER_STRUCT.size :]
    rule_raw = zstd.ZstdDecompressor().decompress(compressed, max_output_size=raw_size)
    if len(rule_raw) != raw_size:
        raise RuntimeError("rule stream decoded to wrong size")
    actual_crc = zlib.crc32(rule_raw) & 0xFFFFFFFF
    if actual_crc != raw_crc32:
        raise RuntimeError(f"rule CRC mismatch: {actual_crc:08X} != {raw_crc32:08X}")
    return rule_raw, {
        "rule_name": RULE_NAME,
        "rule_version": version,
        "rule_id": rule_id,
        "group_count": group_count,
        "records": records,
        "raw_size": raw_size,
        "compressed_size": len(compressed),
        "raw_crc32": f"0x{raw_crc32:08X}",
    }


def open_temp_output(output: Path) -> tuple[Path, BinaryIO]:
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(output.name + ".tmp")
    if temp.exists():
        temp.unlink()
    return temp, temp.open("wb")


def atomic_replace(temp: Path, output: Path) -> None:
    if output.exists():
        output.unlink()
    temp.replace(output)


def repack(input_path: Path, output_path: Path, report_path: Path, *, zstd_level: int, progress_every: int) -> dict:
    started = time.perf_counter()
    rule_raw, rule_info = compute_rule_table(input_path, progress_every)
    rule_stream = make_rule_stream(rule_raw, zstd_level, rule_info)
    rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(RULE_GROUP_COUNT, BITMAP_BYTES)
    compressor = zstd.ZstdCompressor(level=zstd_level)

    temp_path, out = open_temp_output(output_path)
    new_entries: list[LaneEntry] = []
    totals = {
        "actual_exceptions": 0,
        "residual_exceptions": 0,
        "template_stream_size": 0,
        "residual_bitmap_stream_size": 0,
        "value_stream_size": 0,
        "residual_bitmap_raw_size": 0,
        "value_raw_size": 0,
    }

    try:
        with input_path.open("rb") as src:
            old_header = parse_header(src)
            old_entries = parse_lane_entries(src, old_header)
            predictor_stream = read_exact_at(src, old_header.predictor_offset, old_header.predictor_size)

            table_offset = SPC3_HEADER_SIZE + len(predictor_stream) + len(rule_stream)
            table_size = len(old_entries) * SPC3_TABLE_ENTRY_SIZE
            data_offset = table_offset + table_size

            out.write(b"\x00" * SPC3_HEADER_SIZE)
            out.write(predictor_stream)
            out.write(rule_stream)
            out.write(b"\x00" * table_size)

            for index, old_entry in enumerate(old_entries, 1):
                old_subs = parse_substreams(src, old_entry, expected_stream_kinds={STREAM_KIND_TYPED_LEVEL3})
                template_sub = old_subs[SUBSTREAM_TEMPLATE]
                bitmap_sub = old_subs[SUBSTREAM_BITMAP]
                values_sub = old_subs[SUBSTREAM_VALUES]

                template_stream = read_substream_raw(src, old_entry, template_sub)
                actual_bitmap = read_decoded_bitmap(src, old_entry, old_subs)
                values_stream = read_substream_raw(src, old_entry, values_sub)

                rule_bitmap = rule_rows[lane_group(old_entry.lane)]
                residual_bitmap = np.bitwise_xor(
                    np.frombuffer(actual_bitmap, dtype=np.uint8),
                    rule_bitmap,
                ).tobytes()
                residual_stream = compressor.compress(residual_bitmap)

                sub_table_size = TYPED_SUBSTREAM_COUNT * TYPED_SUBSTREAM_ENTRY_SIZE
                sub_template = SubstreamEntry(
                    SUBSTREAM_TEMPLATE,
                    template_sub.flags,
                    sub_table_size,
                    len(template_stream),
                    template_sub.raw_size,
                )
                sub_bitmap = SubstreamEntry(
                    SUBSTREAM_BITMAP,
                    pack_codec_flags(CODEC_ZSTD, zstd_level),
                    sub_template.offset + sub_template.stream_size,
                    len(residual_stream),
                    BITMAP_BYTES,
                )
                sub_values = SubstreamEntry(
                    SUBSTREAM_VALUES,
                    values_sub.flags,
                    sub_bitmap.offset + sub_bitmap.stream_size,
                    len(values_stream),
                    values_sub.raw_size,
                )

                stream_offset = out.tell()
                lane_stream_size = sub_values.offset + sub_values.stream_size
                out.write(pack_substream_entry(sub_template))
                out.write(pack_substream_entry(sub_bitmap))
                out.write(pack_substream_entry(sub_values))
                out.write(template_stream)
                out.write(residual_stream)
                out.write(values_stream)

                residual_pop = int(np.unpackbits(np.frombuffer(residual_bitmap, dtype=np.uint8), bitorder="little").sum())
                actual_pop = old_entry.predictor_exceptions
                if values_sub.raw_size != actual_pop * 4:
                    raise RuntimeError(f"lane 0x{old_entry.lane:04X} values raw size does not match exception count")

                new_entry = LaneEntry(
                    lane=old_entry.lane,
                    level=SPC3_LEVEL,
                    stream_kind=STREAM_KIND_RULE_BITMAP_LEVEL3,
                    flags=0,
                    source_zip_size=old_entry.source_zip_size,
                    source_zip_crc32=old_entry.source_zip_crc32,
                    source_zip_fnv64=old_entry.source_zip_fnv64,
                    original_payload_crc32=old_entry.original_payload_crc32,
                    rebuilt_payload_crc32=old_entry.rebuilt_payload_crc32,
                    stream_offset=stream_offset,
                    stream_size=lane_stream_size,
                    uncompressed_model_size=template_sub.raw_size + BITMAP_BYTES + values_sub.raw_size,
                    predictor_matches=old_entry.predictor_matches,
                    predictor_exceptions=old_entry.predictor_exceptions,
                )
                new_entries.append(new_entry)

                totals["actual_exceptions"] += actual_pop
                totals["residual_exceptions"] += residual_pop
                totals["template_stream_size"] += len(template_stream)
                totals["residual_bitmap_stream_size"] += len(residual_stream)
                totals["value_stream_size"] += len(values_stream)
                totals["residual_bitmap_raw_size"] += BITMAP_BYTES
                totals["value_raw_size"] += values_sub.raw_size

                if progress_every and (index % progress_every == 0 or index == len(old_entries)):
                    print(f"pack pass: {index}/{len(old_entries)} lanes")

            data_size = out.tell() - data_offset
            new_header = Header(
                version=SPC3_VERSION_RULE_V3,
                level=SPC3_LEVEL,
                lane_count=len(new_entries),
                expected_records=EXPECTED_RECORDS,
                record_size=RECORD_SIZE,
                flags=SPC3_FLAG_PREDICTOR_EMBEDDED | SPC3_FLAG_RULE_BITMAP,
                header_size=SPC3_HEADER_SIZE,
                predictor_offset=SPC3_HEADER_SIZE,
                predictor_size=len(predictor_stream),
                table_offset=table_offset,
                table_entry_size=SPC3_TABLE_ENTRY_SIZE,
                data_offset=data_offset,
                data_size=data_size,
            )

            out.seek(0)
            out.write(pack_header(new_header))
            out.seek(table_offset)
            for entry in new_entries:
                out.write(pack_lane_entry(entry))
        out.close()
        atomic_replace(temp_path, output_path)
    except Exception:
        out.close()
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise

    elapsed = time.perf_counter() - started
    output_size = output_path.stat().st_size

    report = {
        "schema": "spc3_rule_bitmap_repack.v1",
        "mode": "pack",
        "input": str(input_path),
        "output": str(output_path),
        "elapsed_seconds": elapsed,
        "zstd_level": zstd_level,
        "rule": asdict(rule_info),
        "totals": totals,
        "spc3_header": asdict(new_header),
        "size_bytes": output_size,
        "size_gb_decimal": output_size / 1_000_000_000,
        "size_gib": output_size / (1024 ** 3),
        "source_size_bytes": input_path.stat().st_size,
        "savings_bytes": input_path.stat().st_size - output_size,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"packed {output_path} ({output_size:,} bytes) in {elapsed:.1f}s")
    return report


def parse_rule_header_from_file(handle: BinaryIO, header: Header) -> tuple[bytes, dict]:
    if header.version != SPC3_VERSION_RULE_V3:
        raise RuntimeError(f"not an SPC3 rule v3 file: version={header.version}")
    if (header.flags & (SPC3_FLAG_PREDICTOR_EMBEDDED | SPC3_FLAG_RULE_BITMAP)) != (
        SPC3_FLAG_PREDICTOR_EMBEDDED | SPC3_FLAG_RULE_BITMAP
    ):
        raise RuntimeError(f"missing required rule flags: 0x{header.flags:08X}")
    rule_offset = header.predictor_offset + header.predictor_size
    rule_size = header.table_offset - rule_offset
    rule_stream = read_exact_at(handle, rule_offset, rule_size)
    return parse_rule_stream(rule_stream)


def verify(new_path: Path, original_path: Path, report_path: Path, *, progress_every: int) -> dict:
    started = time.perf_counter()
    mismatches: list[dict] = []
    totals = {
        "lanes_verified": 0,
        "actual_exceptions": 0,
        "residual_exceptions": 0,
        "template_stream_bytes": 0,
        "residual_bitmap_stream_bytes": 0,
        "value_stream_bytes": 0,
        "residual_bitmap_raw_bytes": 0,
        "value_raw_bytes": 0,
    }

    with original_path.open("rb") as old_handle, new_path.open("rb") as new_handle:
        old_header = parse_header(old_handle)
        new_header = parse_header(new_handle)
        old_entries = parse_lane_entries(old_handle, old_header)
        new_entries = parse_lane_entries(new_handle, new_header)
        if len(old_entries) != len(new_entries):
            raise RuntimeError("lane count mismatch")
        old_by_lane = {entry.lane: entry for entry in old_entries}
        rule_raw, rule_meta = parse_rule_header_from_file(new_handle, new_header)
        rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(RULE_GROUP_COUNT, BITMAP_BYTES)

        old_predictor = read_exact_at(old_handle, old_header.predictor_offset, old_header.predictor_size)
        new_predictor = read_exact_at(new_handle, new_header.predictor_offset, new_header.predictor_size)
        if old_predictor != new_predictor:
            raise RuntimeError("embedded predictor stream differs from source SPC3")

        for index, new_entry in enumerate(new_entries, 1):
            old_entry = old_by_lane.get(new_entry.lane)
            if old_entry is None:
                raise RuntimeError(f"new lane 0x{new_entry.lane:04X} not present in original")
            if new_entry.stream_kind != STREAM_KIND_RULE_BITMAP_LEVEL3:
                raise RuntimeError(f"lane 0x{new_entry.lane:04X} is not rule stream kind")
            if new_entry.original_payload_crc32 != old_entry.original_payload_crc32:
                raise RuntimeError(f"lane 0x{new_entry.lane:04X} CRC metadata changed")
            if new_entry.predictor_exceptions != old_entry.predictor_exceptions:
                raise RuntimeError(f"lane 0x{new_entry.lane:04X} exception metadata changed")

            old_subs = parse_substreams(old_handle, old_entry, expected_stream_kinds={STREAM_KIND_TYPED_LEVEL3})
            new_subs = parse_substreams(new_handle, new_entry, expected_stream_kinds={STREAM_KIND_RULE_BITMAP_LEVEL3})

            old_template = read_substream_raw(old_handle, old_entry, old_subs[SUBSTREAM_TEMPLATE])
            new_template = read_substream_raw(new_handle, new_entry, new_subs[SUBSTREAM_TEMPLATE])
            if old_template != new_template or old_subs[SUBSTREAM_TEMPLATE].raw_size != new_subs[SUBSTREAM_TEMPLATE].raw_size:
                mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "template"})

            old_values = read_substream_raw(old_handle, old_entry, old_subs[SUBSTREAM_VALUES])
            new_values = read_substream_raw(new_handle, new_entry, new_subs[SUBSTREAM_VALUES])
            if (
                old_values != new_values
                or old_subs[SUBSTREAM_VALUES].raw_size != new_subs[SUBSTREAM_VALUES].raw_size
                or old_subs[SUBSTREAM_VALUES].flags != new_subs[SUBSTREAM_VALUES].flags
            ):
                mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "values"})

            old_bitmap = read_decoded_bitmap(old_handle, old_entry, old_subs)
            residual_bitmap = read_decoded_bitmap(new_handle, new_entry, new_subs)
            reconstructed_bitmap = np.bitwise_xor(
                np.frombuffer(residual_bitmap, dtype=np.uint8),
                rule_rows[lane_group(new_entry.lane)],
            ).tobytes()
            if reconstructed_bitmap != old_bitmap:
                mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "bitmap"})
            actual_pop = int(np.unpackbits(np.frombuffer(reconstructed_bitmap, dtype=np.uint8), bitorder="little").sum())
            residual_pop = int(np.unpackbits(np.frombuffer(residual_bitmap, dtype=np.uint8), bitorder="little").sum())
            if actual_pop != new_entry.predictor_exceptions:
                mismatches.append(
                    {
                        "lane": f"0x{new_entry.lane:04X}",
                        "kind": "exception_count",
                        "actual_pop": actual_pop,
                        "metadata": new_entry.predictor_exceptions,
                    }
                )
            if new_subs[SUBSTREAM_VALUES].raw_size != actual_pop * 4:
                mismatches.append(
                    {
                        "lane": f"0x{new_entry.lane:04X}",
                        "kind": "value_count",
                        "value_raw_size": new_subs[SUBSTREAM_VALUES].raw_size,
                        "actual_pop": actual_pop,
                    }
                )

            totals["lanes_verified"] += 1
            totals["actual_exceptions"] += actual_pop
            totals["residual_exceptions"] += residual_pop
            totals["template_stream_bytes"] += new_subs[SUBSTREAM_TEMPLATE].stream_size
            totals["residual_bitmap_stream_bytes"] += new_subs[SUBSTREAM_BITMAP].stream_size
            totals["value_stream_bytes"] += new_subs[SUBSTREAM_VALUES].stream_size
            totals["residual_bitmap_raw_bytes"] += new_subs[SUBSTREAM_BITMAP].raw_size
            totals["value_raw_bytes"] += new_subs[SUBSTREAM_VALUES].raw_size

            if progress_every and (index % progress_every == 0 or index == len(new_entries)):
                print(f"verify pass: {index}/{len(new_entries)} lanes")

    elapsed = time.perf_counter() - started
    with new_path.open("rb") as final_header_handle:
        final_header = parse_header(final_header_handle)

    report = {
        "schema": "spc3_rule_bitmap_verify.v1",
        "mode": "verify",
        "new_spc3": str(new_path),
        "original_spc3": str(original_path),
        "elapsed_seconds": elapsed,
        "status": "ok" if not mismatches else "failed",
        "mismatch_count": len(mismatches),
        "mismatch_samples": mismatches[:20],
        "rule": rule_meta,
        "totals": totals,
        "new_size_bytes": new_path.stat().st_size,
        "new_size_gb_decimal": new_path.stat().st_size / 1_000_000_000,
        "new_size_gib": new_path.stat().st_size / (1024 ** 3),
        "original_size_bytes": original_path.stat().st_size,
        "savings_bytes": original_path.stat().st_size - new_path.stat().st_size,
        "savings_mb_decimal": (original_path.stat().st_size - new_path.stat().st_size) / 1_000_000,
        "spc3_header": asdict(final_header),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if mismatches:
        raise RuntimeError(f"verification failed with {len(mismatches)} mismatches")
    print(f"verify ok: {new_path} ({new_path.stat().st_size:,} bytes) in {elapsed:.1f}s")
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pack", "verify", "pack-verify"), default="pack-verify")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source/current SPC3 v2 file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="new SPC3 v3 output path")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="JSON report path")
    parser.add_argument("--zstd-level", type=int, default=9)
    parser.add_argument("--progress-every", type=int, default=4096)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if args.zstd_level < 1 or args.zstd_level > 22:
        raise SystemExit("--zstd-level must be in 1..22")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    if args.mode in {"pack", "pack-verify"}:
        pack_report = args.report
        if args.mode == "pack-verify":
            pack_report = args.report.with_name(args.report.stem + ".pack.json")
        repack(args.input, args.output, pack_report, zstd_level=args.zstd_level, progress_every=args.progress_every)
    if args.mode in {"verify", "pack-verify"}:
        verify(args.output, args.input, args.report, progress_every=args.progress_every)


if __name__ == "__main__":
    main()
