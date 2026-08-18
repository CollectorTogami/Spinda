#!/usr/bin/env python3
"""Pack and verify SPC3 v6 with global upper-byte residual streams.

This experimental v6 transform keeps the v5 two-stage runtime predictor, stage-1
rule bitmap, stage-2 explicit bitmap, and shift records. The difference is that
cells still explicit after stage 2 no longer store residual IV32 data inside each
lane stream. Instead, residual values are stored once in a global residual
section before the lane table, grouped by upper PID high byte.

The verifier reconstructs every lane from the v6 file and compares it against
the source v2 SPC3.
"""

from __future__ import annotations

import argparse
import json
import math
import shutil
import struct
import sys
import tempfile
import time
import zlib
from collections import OrderedDict
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np
import zstandard as zstd


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import spc3_iv_offset_classifier as clf  # noqa: E402
import spc3_rule_bitmap_repack as base  # noqa: E402
import spc3_two_stage_runtime_repack as two_stage  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_OUTPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-v6.spc3"
DEFAULT_REPORT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-v6.verify.json"

SPC3_VERSION_TWO_STAGE_V6 = 6
STREAM_KIND_TWO_STAGE_RUNTIME_UPPER_LEVEL3 = 8

V6_SUBSTREAM_COUNT = 4
V6_GLOBAL_MAGIC = b"SPC3V6G1"
V6_GLOBAL_VERSION = 1
V6_GLOBAL_HEADER_STRUCT = struct.Struct("<8s5I")
V6_RESIDUAL_MAGIC = b"SPC3V6R1"
V6_RESIDUAL_VERSION = 1
V6_RESIDUAL_HEADER_STRUCT = struct.Struct("<8s5I")
V6_BAND_ENTRY_STRUCT = struct.Struct("<IIQQQII")
V6_BAND_COUNT = 256

VALUE_LAYOUT_UPPER_STATDELTA = "upper-statdelta"
VALUE_LAYOUT_UPPER_RECORD_MASK = "upper-record-mask"
VALUE_LAYOUT_UPPER_MASK_GROUP = "upper-mask-group"
VALUE_LAYOUT_IDS = {
    VALUE_LAYOUT_UPPER_STATDELTA: 1,
    VALUE_LAYOUT_UPPER_RECORD_MASK: 2,
    VALUE_LAYOUT_UPPER_MASK_GROUP: 3,
}
VALUE_LAYOUT_NAMES = {value: key for key, value in VALUE_LAYOUT_IDS.items()}

STAT_COUNT = 6
STAT_BIT_WEIGHTS = (1 << np.arange(STAT_COUNT, dtype=np.uint8)).astype(np.uint8)


class BandBuckets:
    """Temporary actual/baseline u32 buckets keyed by upper PID high byte."""

    def __init__(self, root: Path, prefix: str, max_open: int = 768) -> None:
        self.root = root
        self.prefix = prefix
        self.max_open = max(1, max_open)
        self.counts = np.zeros(V6_BAND_COUNT, dtype=np.uint64)
        self._handles: dict[tuple[str, int], BinaryIO] = {}
        self._open_order: OrderedDict[tuple[str, int], None] = OrderedDict()

    def actual_path(self, band: int) -> Path:
        return self.root / f"{self.prefix}_band_{band:03d}_actual.u32"

    def baseline_path(self, band: int) -> Path:
        return self.root / f"{self.prefix}_band_{band:03d}_baseline.u32"

    def _path_for_kind(self, kind: str, band: int) -> Path:
        if kind == "actual":
            return self.actual_path(band)
        if kind == "baseline":
            return self.baseline_path(band)
        raise ValueError(f"unsupported bucket kind: {kind}")

    def _handle(self, kind: str, band: int) -> BinaryIO:
        key = (kind, band)
        handle = self._handles.get(key)
        if handle is None:
            handle = self._path_for_kind(kind, band).open("ab")
            self._handles[key] = handle
        self._open_order.pop(key, None)
        self._open_order[key] = None
        while len(self._open_order) > self.max_open:
            old_key, _marker = self._open_order.popitem(last=False)
            old_handle = self._handles.pop(old_key)
            old_handle.close()
        return handle

    def close_all(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._open_order.clear()

    def write(self, uppers: np.ndarray, actual: np.ndarray, baseline: np.ndarray) -> None:
        if len(actual) == 0:
            return
        bands = (uppers >> np.uint32(8)).astype(np.uint8, copy=False)
        for band_value in np.unique(bands):
            band = int(band_value)
            mask = bands == band_value
            actual_part = np.asarray(actual[mask], dtype="<u4")
            baseline_part = np.asarray(baseline[mask], dtype="<u4")
            self._handle("actual", band).write(actual_part.tobytes())
            self._handle("baseline", band).write(baseline_part.tobytes())
            self.counts[band] += len(actual_part)

    def write_baseline_only(self, uppers: np.ndarray, baseline: np.ndarray) -> None:
        if len(baseline) == 0:
            return
        bands = (uppers >> np.uint32(8)).astype(np.uint8, copy=False)
        for band_value in np.unique(bands):
            band = int(band_value)
            mask = bands == band_value
            baseline_part = np.asarray(baseline[mask], dtype="<u4")
            self._handle("baseline", band).write(baseline_part.tobytes())
            self.counts[band] += len(baseline_part)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pack", "verify", "pack-verify"), default="pack-verify")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--predictor-json", type=Path, default=clf.DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--start-rng", default="0x2B0C94C1")
    parser.add_argument("--runtime-max-steps", type=int, default=4_000_000)
    parser.add_argument("--base-model", choices=tuple(clf.BASE_MODEL_POSITIONS), default="rsfrlg")
    parser.add_argument("--max-extra", type=int, default=2)
    parser.add_argument("--zstd-level", type=int, default=9)
    parser.add_argument("--value-layout", choices=tuple(VALUE_LAYOUT_IDS), default=VALUE_LAYOUT_UPPER_STATDELTA)
    parser.add_argument("--sample-lanes", type=int, default=None)
    parser.add_argument("--progress-every", type=int, default=4096)
    parser.add_argument("--scratch-dir", type=Path, default=None)
    parser.add_argument("--keep-scratch", action="store_true")
    return parser.parse_args()


def selected_entries(entries: Iterable[base.LaneEntry], sample_lanes: int | None) -> list[base.LaneEntry]:
    entries_list = list(entries)
    if sample_lanes is None:
        return entries_list
    if sample_lanes < 0:
        raise ValueError("--sample-lanes must be non-negative")
    return entries_list[:sample_lanes]


def best_codec(raw: bytes, compressor: zstd.ZstdCompressor, zstd_level: int) -> tuple[int, bytes]:
    return two_stage.best_codec(raw, compressor, zstd_level)


def decode_payload(raw: bytes, flags: int, raw_size: int, label: str) -> bytes:
    sub = base.SubstreamEntry(0, flags, 0, len(raw), raw_size)
    return base.decode_payload(raw, sub, label=label)


def changed_mask_values(actual_fields: np.ndarray, baseline_fields: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    changed = actual_fields != baseline_fields
    masks = (changed.T.astype(np.uint8) * STAT_BIT_WEIGHTS).sum(axis=1).astype(np.uint8)
    return changed, masks


def values_for_changed_mask(actual_fields: np.ndarray, changed: np.ndarray) -> np.ndarray:
    if actual_fields.shape[1] == 0:
        return np.empty(0, dtype=np.uint8)
    return actual_fields.T[changed.T].astype(np.uint8, copy=False)


def stat_delta_stats(actual: np.ndarray, baseline: np.ndarray) -> dict[str, int]:
    actual_u32 = np.asarray(actual, dtype=np.uint32)
    baseline_u32 = np.asarray(baseline, dtype=np.uint32)
    if len(actual_u32) != len(baseline_u32):
        raise RuntimeError("stat-delta actual/baseline length mismatch")
    if len(actual_u32) == 0:
        return {
            "stat_delta_changed_values": 0,
            "stat_delta_high_bit_mismatches": 0,
            **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
            **{f"stat_delta_stat_{stat}_changed_values": 0 for stat in range(STAT_COUNT)},
        }
    high_diff = int((((actual_u32 ^ baseline_u32) & np.uint32(~two_stage.STAT_MASK & 0xFFFFFFFF)) != 0).sum())
    if high_diff:
        raise RuntimeError(f"stat-delta cannot encode {high_diff:,} high-bit IV32 differences")
    actual_fields = two_stage.iv32_stat_fields(actual_u32)
    baseline_fields = two_stage.iv32_stat_fields(baseline_u32)
    changed = actual_fields != baseline_fields
    changed_per_record = changed.sum(axis=0)
    return {
        "stat_delta_changed_values": int(changed.sum()),
        "stat_delta_high_bit_mismatches": high_diff,
        **{f"stat_delta_records_changed_{count}": int((changed_per_record == count).sum()) for count in range(7)},
        **{f"stat_delta_stat_{stat}_changed_values": int(changed[stat].sum()) for stat in range(STAT_COUNT)},
    }


def pack_record_mask_values(actual: np.ndarray, baseline: np.ndarray) -> bytes:
    actual_fields = two_stage.iv32_stat_fields(actual)
    baseline_fields = two_stage.iv32_stat_fields(baseline)
    changed, masks = changed_mask_values(actual_fields, baseline_fields)
    values = values_for_changed_mask(actual_fields, changed)
    return masks.tobytes() + two_stage.pack_5bit_values(values)


def unpack_record_mask_values(raw: bytes, baseline: np.ndarray) -> np.ndarray:
    count = len(baseline)
    if count == 0:
        if raw:
            raise RuntimeError("record-mask stream has bytes for zero records")
        return np.asarray(baseline, dtype=np.uint32)
    if len(raw) < count:
        raise RuntimeError(f"record-mask stream too short: {len(raw):,} < {count:,}")
    masks = np.frombuffer(raw[:count], dtype=np.uint8).copy()
    changed = ((masks[None, :] & STAT_BIT_WEIGHTS[:, None]) != 0)
    value_count = int(changed.sum())
    values_raw = raw[count:]
    values = two_stage.unpack_5bit_values(values_raw, value_count)
    fields = two_stage.iv32_stat_fields(baseline)
    fields.T[changed.T] = values
    return two_stage.replace_iv32_stat_fields(baseline, fields)


def pack_mask_group_values(actual: np.ndarray, baseline: np.ndarray) -> bytes:
    actual_fields = two_stage.iv32_stat_fields(actual)
    baseline_fields = two_stage.iv32_stat_fields(baseline)
    changed, masks = changed_mask_values(actual_fields, baseline_fields)
    parts = [masks.tobytes()]
    for mask_value in range(1 << STAT_COUNT):
        stat_indices = [stat for stat in range(STAT_COUNT) if mask_value & (1 << stat)]
        if not stat_indices:
            grouped = np.empty(0, dtype=np.uint8)
        else:
            record_indices = np.flatnonzero(masks == mask_value)
            grouped = actual_fields[np.ix_(stat_indices, record_indices)].T.reshape(-1).astype(np.uint8, copy=False)
        parts.append(two_stage.pack_5bit_values(grouped))
    return b"".join(parts)


def unpack_mask_group_values(raw: bytes, baseline: np.ndarray) -> np.ndarray:
    count = len(baseline)
    if count == 0:
        if raw:
            raise RuntimeError("mask-group stream has bytes for zero records")
        return np.asarray(baseline, dtype=np.uint32)
    if len(raw) < count:
        raise RuntimeError(f"mask-group stream too short: {len(raw):,} < {count:,}")
    masks = np.frombuffer(raw[:count], dtype=np.uint8).copy()
    fields = two_stage.iv32_stat_fields(baseline)
    cursor = count
    for mask_value in range(1 << STAT_COUNT):
        subset = masks == mask_value
        record_count = int(subset.sum())
        value_count = record_count * int(mask_value.bit_count())
        packed_size = (value_count * 5 + 7) // 8
        values = two_stage.unpack_5bit_values(raw[cursor : cursor + packed_size], value_count)
        cursor += packed_size
        if value_count == 0:
            continue
        subset_indices = np.flatnonzero(subset)
        stat_indices = [stat for stat in range(STAT_COUNT) if mask_value & (1 << stat)]
        reshaped = values.reshape(record_count, len(stat_indices))
        for column, stat_index in enumerate(stat_indices):
            fields[stat_index, subset_indices] = reshaped[:, column]
    if cursor != len(raw):
        raise RuntimeError(f"mask-group stream has trailing bytes: {len(raw) - cursor:,}")
    return two_stage.replace_iv32_stat_fields(baseline, fields)


def pack_values_for_layout(layout: str, actual: np.ndarray, baseline: np.ndarray) -> tuple[bytes, dict[str, int]]:
    if layout == VALUE_LAYOUT_UPPER_STATDELTA:
        return two_stage.pack_stat_delta_values(actual, baseline)
    stats = stat_delta_stats(actual, baseline)
    if layout == VALUE_LAYOUT_UPPER_RECORD_MASK:
        return pack_record_mask_values(actual, baseline), stats
    if layout == VALUE_LAYOUT_UPPER_MASK_GROUP:
        return pack_mask_group_values(actual, baseline), stats
    raise RuntimeError(f"unsupported value layout: {layout}")


def unpack_values_for_layout(layout: str, raw: bytes, baseline: np.ndarray) -> np.ndarray:
    if layout == VALUE_LAYOUT_UPPER_STATDELTA:
        return two_stage.unpack_stat_delta_values(raw, baseline)
    if layout == VALUE_LAYOUT_UPPER_RECORD_MASK:
        return unpack_record_mask_values(raw, baseline)
    if layout == VALUE_LAYOUT_UPPER_MASK_GROUP:
        return unpack_mask_group_values(raw, baseline)
    raise RuntimeError(f"unsupported value layout: {layout}")


def parse_v6_substreams(handle: BinaryIO, entry: base.LaneEntry) -> dict[int, base.SubstreamEntry]:
    return two_stage.parse_substreams_count(
        handle,
        entry,
        substream_count=V6_SUBSTREAM_COUNT,
        expected_stream_kinds={STREAM_KIND_TWO_STAGE_RUNTIME_UPPER_LEVEL3},
        required_kinds={
            base.SUBSTREAM_TEMPLATE,
            two_stage.SUBSTREAM_STAGE1_BITMAP,
            two_stage.SUBSTREAM_STAGE2_EXPLICIT_BITMAP,
            two_stage.SUBSTREAM_STAGE2_SHIFT_RECORDS,
        },
    )


def make_v6_global_stream(model_stream: bytes, rule_stream: bytes, residual_stream: bytes, layout: str) -> bytes:
    global_header = V6_GLOBAL_HEADER_STRUCT.pack(
        V6_GLOBAL_MAGIC,
        V6_GLOBAL_VERSION,
        VALUE_LAYOUT_IDS[layout],
        len(rule_stream),
        len(residual_stream),
        zlib.crc32(residual_stream) & 0xFFFFFFFF,
    )
    return model_stream + global_header + rule_stream + residual_stream


def parse_v6_global_streams(
    handle: BinaryIO,
    header: base.Header,
) -> tuple[bytes, dict[str, object], bytes, dict[str, int | str], bytes, str]:
    predictor_stream = base.read_exact_at(handle, header.predictor_offset, header.predictor_size)
    global_offset = header.predictor_offset + header.predictor_size
    global_size = header.table_offset - global_offset
    global_stream = base.read_exact_at(handle, global_offset, global_size)
    if len(global_stream) < two_stage.MODEL_HEADER_STRUCT.size + V6_GLOBAL_HEADER_STRUCT.size:
        raise RuntimeError("v6 global stream too short")
    _, _, model_payload_size, _ = two_stage.MODEL_HEADER_STRUCT.unpack_from(global_stream)
    model_size = two_stage.MODEL_HEADER_STRUCT.size + model_payload_size
    if model_size + V6_GLOBAL_HEADER_STRUCT.size > len(global_stream):
        raise RuntimeError("v6 model stream extends past global stream")
    model_meta = two_stage.parse_model_stream(global_stream[:model_size])
    cursor = model_size
    magic, version, layout_id, rule_size, residual_size, residual_crc32 = V6_GLOBAL_HEADER_STRUCT.unpack_from(
        global_stream, cursor
    )
    cursor += V6_GLOBAL_HEADER_STRUCT.size
    if magic != V6_GLOBAL_MAGIC or version != V6_GLOBAL_VERSION:
        raise RuntimeError(f"bad v6 global header: {magic!r}/{version}")
    layout = VALUE_LAYOUT_NAMES.get(layout_id)
    if layout is None:
        raise RuntimeError(f"unsupported v6 value layout id: {layout_id}")
    if cursor + rule_size + residual_size > len(global_stream):
        raise RuntimeError("v6 global section sizes exceed global stream")
    rule_stream = global_stream[cursor : cursor + rule_size]
    cursor += rule_size
    residual_stream = global_stream[cursor : cursor + residual_size]
    cursor += residual_size
    if cursor != len(global_stream):
        raise RuntimeError("v6 global stream has trailing bytes")
    if (zlib.crc32(residual_stream) & 0xFFFFFFFF) != residual_crc32:
        raise RuntimeError("v6 residual stream CRC mismatch")
    rule_raw, rule_meta = base.parse_rule_stream(rule_stream)
    return predictor_stream, model_meta, rule_raw, rule_meta, residual_stream, layout


def build_residual_section(
    buckets: BandBuckets,
    *,
    layout: str,
    zstd_level: int,
) -> tuple[bytes, dict[str, int | list[dict[str, int]]]]:
    compressor = zstd.ZstdCompressor(level=zstd_level)
    entries: list[base.SubstreamEntry] = []
    band_meta: list[dict[str, int]] = []
    payloads: list[bytes] = []
    totals: dict[str, int] = {
        "global_value_stream_bytes": 0,
        "global_value_raw_bytes": 0,
        "global_value_records": 0,
        "stat_delta_changed_values": 0,
        "stat_delta_high_bit_mismatches": 0,
        **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
        **{f"stat_delta_stat_{stat}_changed_values": 0 for stat in range(STAT_COUNT)},
    }
    offset = 0
    for band in range(V6_BAND_COUNT):
        count = int(buckets.counts[band])
        if count:
            actual = np.fromfile(buckets.actual_path(band), dtype="<u4")
            baseline = np.fromfile(buckets.baseline_path(band), dtype="<u4")
            if len(actual) != count or len(baseline) != count:
                raise RuntimeError(f"band {band} bucket count mismatch")
            raw, stats = pack_values_for_layout(layout, actual, baseline)
        else:
            raw = b""
            stats = {
                "stat_delta_changed_values": 0,
                "stat_delta_high_bit_mismatches": 0,
                **{f"stat_delta_records_changed_{value}": 0 for value in range(7)},
                **{f"stat_delta_stat_{stat}_changed_values": 0 for stat in range(STAT_COUNT)},
            }
        flags, stream = best_codec(raw, compressor, zstd_level)
        entries.append(base.SubstreamEntry(band, flags, offset, len(stream), len(raw)))
        payloads.append(stream)
        crc32 = zlib.crc32(raw) & 0xFFFFFFFF
        band_meta.append(
            {
                "band": band,
                "records": count,
                "raw_bytes": len(raw),
                "stream_bytes": len(stream),
                "raw_crc32": crc32,
            }
        )
        offset += len(stream)
        totals["global_value_stream_bytes"] += len(stream)
        totals["global_value_raw_bytes"] += len(raw)
        totals["global_value_records"] += count
        for key, value in stats.items():
            totals[key] = totals.get(key, 0) + int(value)

    header = V6_RESIDUAL_HEADER_STRUCT.pack(
        V6_RESIDUAL_MAGIC,
        V6_RESIDUAL_VERSION,
        VALUE_LAYOUT_IDS[layout],
        V6_BAND_COUNT,
        V6_BAND_ENTRY_STRUCT.size,
        offset,
    )
    table = b"".join(
        V6_BAND_ENTRY_STRUCT.pack(
            entry.kind,
            entry.flags,
            entry.offset,
            entry.stream_size,
            entry.raw_size,
            int(buckets.counts[entry.kind]),
            band_meta[entry.kind]["raw_crc32"],
        )
        for entry in entries
    )
    section = header + table + b"".join(payloads)
    totals["global_residual_section_bytes"] = len(section)
    totals["global_residual_table_bytes"] = len(header) + len(table)
    totals["bands"] = band_meta
    return section, totals


def parse_residual_section(raw: bytes) -> tuple[list[dict[str, int]], bytes, str]:
    if len(raw) < V6_RESIDUAL_HEADER_STRUCT.size:
        raise RuntimeError("v6 residual section too short")
    magic, version, layout_id, band_count, entry_size, data_size = V6_RESIDUAL_HEADER_STRUCT.unpack_from(raw)
    if magic != V6_RESIDUAL_MAGIC or version != V6_RESIDUAL_VERSION:
        raise RuntimeError(f"bad v6 residual header: {magic!r}/{version}")
    if band_count != V6_BAND_COUNT or entry_size != V6_BAND_ENTRY_STRUCT.size:
        raise RuntimeError("v6 residual geometry mismatch")
    layout = VALUE_LAYOUT_NAMES.get(layout_id)
    if layout is None:
        raise RuntimeError(f"unsupported v6 residual layout id: {layout_id}")
    table_offset = V6_RESIDUAL_HEADER_STRUCT.size
    data_offset = table_offset + band_count * entry_size
    if len(raw) != data_offset + data_size:
        raise RuntimeError("v6 residual section size mismatch")
    entries: list[dict[str, int]] = []
    expected_offset = 0
    for index in range(band_count):
        band, flags, offset, stream_size, raw_size, record_count, raw_crc32 = V6_BAND_ENTRY_STRUCT.unpack_from(
            raw, table_offset + index * entry_size
        )
        if band != index or offset != expected_offset or offset + stream_size > data_size:
            raise RuntimeError(f"bad v6 residual band entry {index}")
        entries.append(
            {
                "band": band,
                "flags": flags,
                "offset": offset,
                "stream_size": stream_size,
                "raw_size": raw_size,
                "record_count": record_count,
                "raw_crc32": raw_crc32,
            }
        )
        expected_offset += stream_size
    if expected_offset != data_size:
        raise RuntimeError("v6 residual payload has trailing bytes")
    return entries, raw[data_offset:], layout


def source_lane_parts(
    src: BinaryIO,
    old_entry: base.LaneEntry,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
    rule_rows: np.ndarray,
    compressor: zstd.ZstdCompressor,
    zstd_level: int,
) -> tuple[bytes, dict[str, int], np.ndarray, np.ndarray, np.ndarray]:
    old_subs = two_stage.parse_source_substreams(src, old_entry)
    template_sub = old_subs[base.SUBSTREAM_TEMPLATE]
    bitmap_sub = old_subs[base.SUBSTREAM_BITMAP]
    template_stream = two_stage.read_substream_raw(src, old_entry, template_sub)
    actual_bitmap, uppers, actual = two_stage.source_actual_for_exceptions(src, old_entry, predictor)

    rule_bitmap = rule_rows[base.lane_group(old_entry.lane)]
    stage1_residual_bitmap = np.bitwise_xor(np.frombuffer(actual_bitmap, dtype=np.uint8), rule_bitmap).tobytes()
    stage1_flags, stage1_stream = best_codec(stage1_residual_bitmap, compressor, zstd_level)

    if len(uppers):
        class_ids = clf.classify_values(uppers, actual, candidate_table)
        explicit_mask = class_ids < 0
        explicit_indices = np.flatnonzero(explicit_mask)
        shift_indices = np.flatnonzero(class_ids > 0).astype(np.uint16)
        shift_classes = class_ids[shift_indices].astype(np.uint8)
    else:
        class_ids = np.empty(0, dtype=np.int16)
        explicit_mask = np.empty(0, dtype=np.bool_)
        explicit_indices = np.empty(0, dtype=np.int64)
        shift_indices = np.empty(0, dtype=np.uint16)
        shift_classes = np.empty(0, dtype=np.uint8)

    stage2_bitmap_raw = np.packbits(explicit_mask.astype(np.uint8, copy=False), bitorder="little").tobytes()
    if len(stage2_bitmap_raw) != math.ceil(len(uppers) / 8):
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} stage2 bitmap size mismatch")
    stage2_bitmap_flags, stage2_bitmap_stream = best_codec(stage2_bitmap_raw, compressor, zstd_level)

    shift_raw = two_stage.pack_shift_records(shift_indices, shift_classes)
    shift_flags, shift_stream = best_codec(shift_raw, compressor, zstd_level)

    sub_table_size = V6_SUBSTREAM_COUNT * base.TYPED_SUBSTREAM_ENTRY_SIZE
    sub_template = base.SubstreamEntry(
        base.SUBSTREAM_TEMPLATE,
        template_sub.flags,
        sub_table_size,
        len(template_stream),
        template_sub.raw_size,
    )
    sub_stage1 = base.SubstreamEntry(
        two_stage.SUBSTREAM_STAGE1_BITMAP,
        stage1_flags,
        sub_template.offset + sub_template.stream_size,
        len(stage1_stream),
        base.BITMAP_BYTES,
    )
    sub_stage2 = base.SubstreamEntry(
        two_stage.SUBSTREAM_STAGE2_EXPLICIT_BITMAP,
        stage2_bitmap_flags,
        sub_stage1.offset + sub_stage1.stream_size,
        len(stage2_bitmap_stream),
        len(stage2_bitmap_raw),
    )
    sub_shift = base.SubstreamEntry(
        two_stage.SUBSTREAM_STAGE2_SHIFT_RECORDS,
        shift_flags,
        sub_stage2.offset + sub_stage2.stream_size,
        len(shift_stream),
        len(shift_raw),
    )
    lane_stream = b"".join(
        [
            base.pack_substream_entry(sub_template),
            base.pack_substream_entry(sub_stage1),
            base.pack_substream_entry(sub_stage2),
            base.pack_substream_entry(sub_shift),
            template_stream,
            stage1_stream,
            stage2_bitmap_stream,
            shift_stream,
        ]
    )
    if bitmap_sub.raw_size != base.BITMAP_BYTES:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} source bitmap raw size changed")

    explicit_uppers = uppers[explicit_indices]
    explicit_actual = actual[explicit_indices]
    explicit_baseline = candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
    stats = {
        "old_exceptions": int(len(uppers)),
        "stage2_normal": int((class_ids == 0).sum()) if len(class_ids) else 0,
        "stage2_shift": int((class_ids > 0).sum()) if len(class_ids) else 0,
        "stage2_explicit": int(len(explicit_indices)),
        "template_stream_bytes": len(template_stream),
        "stage1_bitmap_stream_bytes": len(stage1_stream),
        "stage1_bitmap_raw_bytes": base.BITMAP_BYTES,
        "stage1_residual_bits": int(np.unpackbits(np.frombuffer(stage1_residual_bitmap, dtype=np.uint8), bitorder="little").sum()),
        "stage2_bitmap_stream_bytes": len(stage2_bitmap_stream),
        "stage2_bitmap_raw_bytes": len(stage2_bitmap_raw),
        "shift_stream_bytes": len(shift_stream),
        "shift_raw_bytes": len(shift_raw),
        "lane_stream_bytes": len(lane_stream),
    }
    return lane_stream, stats, explicit_uppers, explicit_actual, explicit_baseline


def add_totals(totals: dict[str, int], stats: dict[str, int]) -> None:
    for key, value in stats.items():
        totals[key] = totals.get(key, 0) + int(value)


def scan_v6_stage(
    handle: BinaryIO,
    entry: base.LaneEntry,
    rule_rows: np.ndarray,
) -> tuple[bytes, np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    substreams = parse_v6_substreams(handle, entry)
    stage1_residual = two_stage.decode_substream(
        handle,
        entry,
        substreams[two_stage.SUBSTREAM_STAGE1_BITMAP],
        f"lane 0x{entry.lane:04X} stage1 residual bitmap",
    )
    reconstructed_bitmap = np.bitwise_xor(
        np.frombuffer(stage1_residual, dtype=np.uint8),
        rule_rows[base.lane_group(entry.lane)],
    ).tobytes()
    bits = np.unpackbits(np.frombuffer(reconstructed_bitmap, dtype=np.uint8), bitorder="little")
    uppers = np.flatnonzero(bits).astype(np.uint32)

    stage2_bitmap = two_stage.decode_substream(
        handle,
        entry,
        substreams[two_stage.SUBSTREAM_STAGE2_EXPLICIT_BITMAP],
        f"lane 0x{entry.lane:04X} stage2 bitmap",
    )
    expected_stage2_size = math.ceil(len(uppers) / 8)
    if len(stage2_bitmap) != expected_stage2_size:
        raise RuntimeError(f"lane 0x{entry.lane:04X} stage2 raw size mismatch")
    explicit_mask = np.unpackbits(np.frombuffer(stage2_bitmap, dtype=np.uint8), bitorder="little")[: len(uppers)].astype(
        np.bool_,
        copy=False,
    )
    explicit_indices = np.flatnonzero(explicit_mask)

    shift_raw = two_stage.decode_substream(
        handle,
        entry,
        substreams[two_stage.SUBSTREAM_STAGE2_SHIFT_RECORDS],
        f"lane 0x{entry.lane:04X} shift records",
    )
    shift_indices, shift_classes = two_stage.unpack_shift_records(shift_raw)
    if len(shift_indices):
        if int(shift_indices.max()) >= len(uppers):
            raise RuntimeError(f"lane 0x{entry.lane:04X} shift ordinal outside old-miss range")
        if len(np.unique(shift_indices)) != len(shift_indices):
            raise RuntimeError(f"lane 0x{entry.lane:04X} has duplicate shift ordinals")
        if bool(explicit_mask[shift_indices].any()):
            raise RuntimeError(f"lane 0x{entry.lane:04X} marks same old miss as shift and explicit")

    stats = {
        "stage2_normal": int(len(uppers) - len(shift_indices) - len(explicit_indices)),
        "stage2_shift": int(len(shift_indices)),
        "stage2_explicit": int(len(explicit_indices)),
        "stage2_bitmap_stream_bytes": int(substreams[two_stage.SUBSTREAM_STAGE2_EXPLICIT_BITMAP].stream_size),
        "stage2_bitmap_raw_bytes": int(substreams[two_stage.SUBSTREAM_STAGE2_EXPLICIT_BITMAP].raw_size),
        "shift_stream_bytes": int(substreams[two_stage.SUBSTREAM_STAGE2_SHIFT_RECORDS].stream_size),
        "shift_raw_bytes": int(substreams[two_stage.SUBSTREAM_STAGE2_SHIFT_RECORDS].raw_size),
        "stage1_bitmap_stream_bytes": int(substreams[two_stage.SUBSTREAM_STAGE1_BITMAP].stream_size),
        "stage1_bitmap_raw_bytes": int(substreams[two_stage.SUBSTREAM_STAGE1_BITMAP].raw_size),
        "stage1_residual_bits": int(np.unpackbits(np.frombuffer(stage1_residual, dtype=np.uint8), bitorder="little").sum()),
        "template_stream_bytes": int(substreams[base.SUBSTREAM_TEMPLATE].stream_size),
    }
    return reconstructed_bitmap, uppers, explicit_indices, shift_indices, shift_classes, stats


def write_baseline_buckets(
    *,
    new_path: Path,
    predictor_json: Path,
    baseline_root: Path,
) -> tuple[BandBuckets, dict[str, object], bytes, np.ndarray, str]:
    buckets = BandBuckets(baseline_root, "verify")
    with new_path.open("rb") as handle:
        header = base.parse_header(handle)
        predictor_stream, model_meta, rule_raw, _rule_meta, residual_stream, layout = parse_v6_global_streams(handle, header)
        candidate_table, _classes, rebuilt_model = two_stage.build_candidate_model(
            start_rng=two_stage.parse_int(str(model_meta["start_rng"])),
            runtime_max_steps=int(model_meta["runtime_max_steps"]),
            base_model=str(model_meta["base_model"]),
            max_extra=int(model_meta["max_extra"]),
            residual_encoding=str(model_meta.get("residual_encoding", VALUE_LAYOUT_UPPER_STATDELTA)),
        )
        if rebuilt_model != model_meta:
            raise RuntimeError("rebuilt v6 model metadata differs from stored model")
        rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
        for entry in base.parse_lane_entries(handle, header):
            _bitmap, uppers, explicit_indices, _shift_indices, _shift_classes, _stats = scan_v6_stage(
                handle,
                entry,
                rule_rows,
            )
            explicit_uppers = uppers[explicit_indices]
            baseline = candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
            buckets.write_baseline_only(explicit_uppers, baseline)
    buckets.close_all()
    return buckets, model_meta, residual_stream, candidate_table, layout


def materialize_decoded_residuals(
    *,
    residual_stream: bytes,
    layout: str,
    baseline_buckets: BandBuckets,
    output_root: Path,
) -> tuple[list[dict[str, int]], BandBuckets]:
    entries, payload, section_layout = parse_residual_section(residual_stream)
    if section_layout != layout:
        raise RuntimeError(f"residual layout mismatch: {section_layout} != {layout}")
    actual_buckets = BandBuckets(output_root, "decoded")
    for entry in entries:
        band = entry["band"]
        count = entry["record_count"]
        if count != int(baseline_buckets.counts[band]):
            raise RuntimeError(f"band {band} record count mismatch")
        baseline = (
            np.fromfile(baseline_buckets.baseline_path(band), dtype="<u4")
            if count
            else np.empty(0, dtype=np.uint32)
        )
        raw_payload = payload[entry["offset"] : entry["offset"] + entry["stream_size"]]
        raw = decode_payload(raw_payload, entry["flags"], entry["raw_size"], f"v6 residual band {band}")
        if (zlib.crc32(raw) & 0xFFFFFFFF) != entry["raw_crc32"]:
            raise RuntimeError(f"band {band} residual raw CRC mismatch")
        actual = unpack_values_for_layout(layout, raw, baseline)
        if len(actual) != count:
            raise RuntimeError(f"band {band} decoded count mismatch")
        if count:
            with actual_buckets.actual_path(band).open("wb") as handle:
                handle.write(np.asarray(actual, dtype="<u4").tobytes())
            actual_buckets.counts[band] = count
    return entries, actual_buckets


def read_explicit_actuals_from_bands(
    handles: dict[int, BinaryIO],
    explicit_uppers: np.ndarray,
) -> np.ndarray:
    actual = np.empty(len(explicit_uppers), dtype=np.uint32)
    if len(explicit_uppers) == 0:
        return actual
    bands = (explicit_uppers >> np.uint32(8)).astype(np.uint8, copy=False)
    for band_value in np.unique(bands):
        band = int(band_value)
        mask = bands == band_value
        count = int(mask.sum())
        raw = handles[band].read(count * 4)
        if len(raw) != count * 4:
            raise RuntimeError(f"decoded residual band {band} ended early")
        actual[mask] = np.frombuffer(raw, dtype="<u4")
    return actual


def repack(
    input_path: Path,
    output_path: Path,
    report_path: Path,
    *,
    predictor_json: Path,
    start_rng: int,
    runtime_max_steps: int,
    base_model: str,
    max_extra: int,
    zstd_level: int,
    value_layout: str,
    sample_lanes: int | None,
    progress_every: int,
    scratch_dir: Path | None,
    keep_scratch: bool,
) -> dict[str, object]:
    started = time.perf_counter()
    rule_raw, rule_info = base.compute_rule_table(input_path, progress_every)
    rule_stream = base.make_rule_stream(rule_raw, zstd_level, rule_info)
    rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
    candidate_table, classes, model_meta = two_stage.build_candidate_model(
        start_rng=start_rng,
        runtime_max_steps=runtime_max_steps,
        base_model=base_model,
        max_extra=max_extra,
        residual_encoding=value_layout,
    )
    model_stream = two_stage.make_model_stream(model_meta)
    compressor = zstd.ZstdCompressor(level=zstd_level)

    scratch_context: tempfile.TemporaryDirectory[str] | None = None
    if scratch_dir is None:
        scratch_context = tempfile.TemporaryDirectory(prefix="spc3-v6-upper-")
        scratch_root = Path(scratch_context.name)
    else:
        scratch_root = scratch_dir
        scratch_root.mkdir(parents=True, exist_ok=True)

    temp_path, out = base.open_temp_output(output_path)
    lane_data_path = scratch_root / "lane_data.bin"
    buckets = BandBuckets(scratch_root, "pack")
    new_entries: list[base.LaneEntry] = []
    totals: dict[str, int] = {}
    lane_samples: list[dict[str, object]] = []

    try:
        with input_path.open("rb") as src, lane_data_path.open("wb") as lane_data:
            old_header = base.parse_header(src)
            if old_header.version != base.SPC3_VERSION_V2 or not (old_header.flags & base.SPC3_FLAG_PREDICTOR_EMBEDDED):
                raise RuntimeError("input must be SPC3 v2 typed level-3 with an embedded predictor")
            old_entries = selected_entries(base.parse_lane_entries(src, old_header), sample_lanes)
            predictor_stream = base.read_exact_at(src, old_header.predictor_offset, old_header.predictor_size)
            predictor, predictor_source = clf.load_predictor(src, old_header, predictor_json)

            for index, old_entry in enumerate(old_entries, 1):
                lane_stream, stats, explicit_uppers, explicit_actual, explicit_baseline = source_lane_parts(
                    src,
                    old_entry,
                    predictor,
                    candidate_table,
                    rule_rows,
                    compressor,
                    zstd_level,
                )
                stream_offset_in_lane_data = lane_data.tell()
                lane_data.write(lane_stream)
                buckets.write(explicit_uppers, explicit_actual, explicit_baseline)
                new_entries.append(
                    base.LaneEntry(
                        lane=old_entry.lane,
                        level=base.SPC3_LEVEL,
                        stream_kind=STREAM_KIND_TWO_STAGE_RUNTIME_UPPER_LEVEL3,
                        flags=0,
                        source_zip_size=old_entry.source_zip_size,
                        source_zip_crc32=old_entry.source_zip_crc32,
                        source_zip_fnv64=old_entry.source_zip_fnv64,
                        original_payload_crc32=old_entry.original_payload_crc32,
                        rebuilt_payload_crc32=old_entry.rebuilt_payload_crc32,
                        stream_offset=stream_offset_in_lane_data,
                        stream_size=len(lane_stream),
                        uncompressed_model_size=(
                            base.RECORD_SIZE
                            + base.BITMAP_BYTES
                            + stats["stage2_bitmap_raw_bytes"]
                            + stats["shift_raw_bytes"]
                        ),
                        predictor_matches=old_entry.predictor_matches,
                        predictor_exceptions=old_entry.predictor_exceptions,
                    )
                )
                add_totals(totals, stats)
                if len(lane_samples) < 32:
                    lane_samples.append({"lane": f"0x{old_entry.lane:04X}", **stats})
                if progress_every and (index % progress_every == 0 or index == len(old_entries)):
                    print(f"v6 pack scan: {index}/{len(old_entries)} lanes", flush=True)

        buckets.close_all()
        residual_section, residual_totals = build_residual_section(buckets, layout=value_layout, zstd_level=zstd_level)
        global_stream = make_v6_global_stream(model_stream, rule_stream, residual_section, value_layout)
        table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream) + len(global_stream)
        table_size = len(new_entries) * base.SPC3_TABLE_ENTRY_SIZE
        data_offset = table_offset + table_size
        data_size = lane_data_path.stat().st_size

        out.write(b"\x00" * base.SPC3_HEADER_SIZE)
        out.write(predictor_stream)
        out.write(global_stream)
        out.write(b"\x00" * table_size)
        with lane_data_path.open("rb") as lane_data:
            shutil.copyfileobj(lane_data, out, length=1024 * 1024)

        adjusted_entries: list[base.LaneEntry] = []
        cursor = data_offset
        for entry in new_entries:
            adjusted = base.LaneEntry(
                lane=entry.lane,
                level=entry.level,
                stream_kind=entry.stream_kind,
                flags=entry.flags,
                source_zip_size=entry.source_zip_size,
                source_zip_crc32=entry.source_zip_crc32,
                source_zip_fnv64=entry.source_zip_fnv64,
                original_payload_crc32=entry.original_payload_crc32,
                rebuilt_payload_crc32=entry.rebuilt_payload_crc32,
                stream_offset=cursor,
                stream_size=entry.stream_size,
                uncompressed_model_size=entry.uncompressed_model_size,
                predictor_matches=entry.predictor_matches,
                predictor_exceptions=entry.predictor_exceptions,
            )
            adjusted_entries.append(adjusted)
            cursor += entry.stream_size
        if cursor != data_offset + data_size:
            raise RuntimeError("v6 lane data size mismatch")

        new_header = base.Header(
            version=SPC3_VERSION_TWO_STAGE_V6,
            level=base.SPC3_LEVEL,
            lane_count=len(adjusted_entries),
            expected_records=base.EXPECTED_RECORDS,
            record_size=base.RECORD_SIZE,
            flags=base.SPC3_FLAG_PREDICTOR_EMBEDDED | base.SPC3_FLAG_RULE_BITMAP | two_stage.SPC3_FLAG_TWO_STAGE_RUNTIME,
            header_size=base.SPC3_HEADER_SIZE,
            predictor_offset=base.SPC3_HEADER_SIZE,
            predictor_size=len(predictor_stream),
            table_offset=table_offset,
            table_entry_size=base.SPC3_TABLE_ENTRY_SIZE,
            data_offset=data_offset,
            data_size=data_size,
        )
        out.seek(0)
        out.write(base.pack_header(new_header))
        out.seek(table_offset)
        for entry in adjusted_entries:
            out.write(base.pack_lane_entry(entry))
        out.close()
        base.atomic_replace(temp_path, output_path)
    except Exception:
        out.close()
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise
    finally:
        buckets.close_all()
        if scratch_context is not None and not keep_scratch:
            scratch_context.cleanup()

    add_totals(totals, {key: value for key, value in residual_totals.items() if isinstance(value, int)})
    elapsed = time.perf_counter() - started
    output_size = output_path.stat().st_size
    source_size = input_path.stat().st_size
    explicit_total = totals.get("old_exceptions", 0)
    report = {
        "schema": "spc3_v6_upper_repack.v1",
        "mode": "pack",
        "input": str(input_path),
        "output": str(output_path),
        "elapsed_seconds": elapsed,
        "zstd_level": zstd_level,
        "value_layout": value_layout,
        "predictor_source": predictor_source,
        "model": model_meta,
        "rule": asdict(rule_info),
        "class_count": len(classes),
        "totals": totals,
        "residual_bands": residual_totals["bands"],
        "stage2": {
            "old_predictor_exceptions": explicit_total,
            "runtime_matched": totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0),
            "runtime_normal": totals.get("stage2_normal", 0),
            "runtime_shift": totals.get("stage2_shift", 0),
            "still_explicit": totals.get("stage2_explicit", 0),
            "runtime_match_pct_of_old_exceptions": (
                100.0 * (totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0)) / explicit_total
                if explicit_total
                else 0.0
            ),
        },
        "lane_samples": lane_samples,
        "spc3_header": asdict(new_header),
        "size_bytes": output_size,
        "size_gb_decimal": output_size / 1_000_000_000,
        "size_gib": output_size / (1024**3),
        "source_size_bytes": source_size,
        "savings_bytes": source_size - output_size,
        "savings_pct": 100.0 * (source_size - output_size) / source_size if source_size else 0.0,
        "scratch_dir": str(scratch_root) if keep_scratch else None,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"packed {output_path} ({output_size:,} bytes) in {elapsed:.1f}s")
    return report


def verify(
    new_path: Path,
    original_path: Path,
    report_path: Path,
    *,
    predictor_json: Path,
    progress_every: int,
) -> dict[str, object]:
    started = time.perf_counter()
    mismatches: list[dict[str, object]] = []
    totals: dict[str, int] = {}

    with tempfile.TemporaryDirectory(prefix="spc3-v6-verify-") as temp_name:
        temp_root = Path(temp_name)
        baseline_buckets, model_meta, residual_stream, candidate_table, layout = write_baseline_buckets(
            new_path=new_path,
            predictor_json=predictor_json,
            baseline_root=temp_root,
        )
        residual_entries, actual_buckets = materialize_decoded_residuals(
            residual_stream=residual_stream,
            layout=layout,
            baseline_buckets=baseline_buckets,
            output_root=temp_root,
        )
        actual_handles = {
            band: actual_buckets.actual_path(band).open("rb")
            for band in range(V6_BAND_COUNT)
            if int(actual_buckets.counts[band])
        }
        try:
            with original_path.open("rb") as old_handle, new_path.open("rb") as new_handle:
                old_header = base.parse_header(old_handle)
                new_header = base.parse_header(new_handle)
                if new_header.version != SPC3_VERSION_TWO_STAGE_V6:
                    raise RuntimeError(f"not a v6 SPC3: version={new_header.version}")
                if not (new_header.flags & two_stage.SPC3_FLAG_TWO_STAGE_RUNTIME):
                    raise RuntimeError(f"missing two-stage flag: 0x{new_header.flags:08X}")
                old_entries = base.parse_lane_entries(old_handle, old_header)
                new_entries = base.parse_lane_entries(new_handle, new_header)
                old_by_lane = {entry.lane: entry for entry in old_entries}

                old_predictor_stream = base.read_exact_at(old_handle, old_header.predictor_offset, old_header.predictor_size)
                new_predictor_stream, stored_model, rule_raw, rule_meta, _residual_stream, stored_layout = parse_v6_global_streams(
                    new_handle, new_header
                )
                if old_predictor_stream != new_predictor_stream:
                    raise RuntimeError("embedded predictor stream differs from source SPC3")
                if stored_model != model_meta or stored_layout != layout:
                    raise RuntimeError("v6 global metadata changed between passes")
                predictor, predictor_source = clf.load_predictor(old_handle, old_header, predictor_json)
                rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)

                for index, new_entry in enumerate(new_entries, 1):
                    old_entry = old_by_lane.get(new_entry.lane)
                    if old_entry is None:
                        raise RuntimeError(f"new lane 0x{new_entry.lane:04X} not present in original")
                    if new_entry.original_payload_crc32 != old_entry.original_payload_crc32:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} CRC metadata changed")
                    if new_entry.predictor_exceptions != old_entry.predictor_exceptions:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} exception metadata changed")

                    old_subs = two_stage.parse_source_substreams(old_handle, old_entry)
                    new_subs = parse_v6_substreams(new_handle, new_entry)
                    old_template = two_stage.read_substream_raw(old_handle, old_entry, old_subs[base.SUBSTREAM_TEMPLATE])
                    new_template = two_stage.read_substream_raw(new_handle, new_entry, new_subs[base.SUBSTREAM_TEMPLATE])
                    if old_template != new_template:
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "template"})

                    old_bitmap, old_uppers, old_actual = two_stage.source_actual_for_exceptions(
                        old_handle, old_entry, predictor
                    )
                    reconstructed_bitmap, uppers, explicit_indices, shift_indices, shift_classes, stage_stats = scan_v6_stage(
                        new_handle, new_entry, rule_rows
                    )
                    if reconstructed_bitmap != old_bitmap:
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "stage1_bitmap"})
                    if len(uppers) != len(old_uppers) or bool(np.any(uppers != old_uppers)):
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "old_miss_uppers"})

                    reconstructed = candidate_table[0, uppers].astype(np.uint32, copy=True)
                    if len(shift_indices):
                        if int(shift_classes.min()) <= 0 or int(shift_classes.max()) >= candidate_table.shape[0]:
                            raise RuntimeError(f"lane 0x{new_entry.lane:04X} shift class outside candidate range")
                        reconstructed[shift_indices] = candidate_table[shift_classes, uppers[shift_indices]]
                    if len(explicit_indices):
                        explicit_uppers = uppers[explicit_indices]
                        explicit_actual = read_explicit_actuals_from_bands(actual_handles, explicit_uppers)
                        reconstructed[explicit_indices] = explicit_actual

                    if len(reconstructed) != len(old_actual) or bool(np.any(reconstructed != old_actual)):
                        mismatch_index = (
                            int(np.flatnonzero(reconstructed != old_actual)[0])
                            if len(reconstructed) == len(old_actual)
                            else -1
                        )
                        mismatches.append(
                            {
                                "lane": f"0x{new_entry.lane:04X}",
                                "kind": "actual_iv32",
                                "old_miss_ordinal": mismatch_index,
                                "upper": f"0x{int(uppers[mismatch_index]):04X}" if mismatch_index >= 0 else None,
                            }
                        )

                    stats = {
                        "old_exceptions": int(len(uppers)),
                        "lane_stream_bytes": int(new_entry.stream_size),
                        **stage_stats,
                    }
                    add_totals(totals, stats)
                    if progress_every and (index % progress_every == 0 or index == len(new_entries)):
                        print(f"v6 verify pass: {index}/{len(new_entries)} lanes", flush=True)

            for band, handle in actual_handles.items():
                remaining = handle.read(1)
                if remaining:
                    raise RuntimeError(f"decoded residual band {band} has unread bytes")
        finally:
            for handle in actual_handles.values():
                handle.close()

    elapsed = time.perf_counter() - started
    output_size = new_path.stat().st_size
    source_size = original_path.stat().st_size
    explicit_total = totals.get("old_exceptions", 0)
    residual_totals = {
        "global_value_stream_bytes": sum(entry["stream_size"] for entry in residual_entries),
        "global_value_raw_bytes": sum(entry["raw_size"] for entry in residual_entries),
        "global_value_records": sum(entry["record_count"] for entry in residual_entries),
        "global_residual_section_bytes": len(residual_stream),
    }
    report = {
        "schema": "spc3_v6_upper_verify.v1",
        "mode": "verify",
        "new_spc3": str(new_path),
        "original_spc3": str(original_path),
        "elapsed_seconds": elapsed,
        "status": "ok" if not mismatches else "failed",
        "mismatch_count": len(mismatches),
        "mismatch_samples": mismatches[:20],
        "predictor_source": predictor_source,
        "value_layout": layout,
        "model": model_meta,
        "rule": rule_meta,
        "totals": {**totals, **residual_totals},
        "stage2": {
            "old_predictor_exceptions": explicit_total,
            "runtime_matched": totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0),
            "runtime_normal": totals.get("stage2_normal", 0),
            "runtime_shift": totals.get("stage2_shift", 0),
            "still_explicit": totals.get("stage2_explicit", 0),
            "runtime_match_pct_of_old_exceptions": (
                100.0 * (totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0)) / explicit_total
                if explicit_total
                else 0.0
            ),
        },
        "new_size_bytes": output_size,
        "new_size_gb_decimal": output_size / 1_000_000_000,
        "new_size_gib": output_size / (1024**3),
        "original_size_bytes": source_size,
        "savings_bytes": source_size - output_size,
        "savings_pct": 100.0 * (source_size - output_size) / source_size if source_size else 0.0,
        "spc3_header": asdict(new_header),
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    if mismatches:
        raise RuntimeError(f"v6 verification failed with {len(mismatches)} mismatches")
    print(f"v6 verify ok: {new_path} ({output_size:,} bytes) in {elapsed:.1f}s")
    return report


def main() -> int:
    args = parse_args()
    if not 1 <= args.zstd_level <= 22:
        raise SystemExit("--zstd-level must be in 1..22")
    if args.max_extra < 0:
        raise SystemExit("--max-extra must be non-negative")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    start_rng = two_stage.parse_int(args.start_rng)

    if args.mode in {"pack", "pack-verify"}:
        pack_report = args.report
        if args.mode == "pack-verify":
            pack_report = args.report.with_name(args.report.stem + ".pack.json")
        repack(
            args.input,
            args.output,
            pack_report,
            predictor_json=args.predictor_json,
            start_rng=start_rng,
            runtime_max_steps=args.runtime_max_steps,
            base_model=args.base_model,
            max_extra=args.max_extra,
            zstd_level=args.zstd_level,
            value_layout=args.value_layout,
            sample_lanes=args.sample_lanes,
            progress_every=args.progress_every,
            scratch_dir=args.scratch_dir,
            keep_scratch=args.keep_scratch,
        )
    if args.mode in {"verify", "pack-verify"}:
        verify(
            args.output,
            args.input,
            args.report,
            predictor_json=args.predictor_json,
            progress_every=args.progress_every,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
