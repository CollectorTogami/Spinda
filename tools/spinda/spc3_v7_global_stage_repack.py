#!/usr/bin/env python3
"""Pack and verify SPC3 v7 with global stage streams.

This experimental v7 transform keeps the verified v6 value-residual strategy
but moves stage-1/stage-2 state out of each lane stream. Lane streams contain
only the template substream. Stage-1 rule residual bits, stage-2 explicit bits,
and shifted runtime classes are stored once in a global stage section.

The verifier reconstructs every lane from the v7 file and compares it against
the source v2 SPC3.
"""

from __future__ import annotations

import argparse
import json
import struct
import sys
import tempfile
import time
import zlib
from collections import OrderedDict, defaultdict
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
import spc3_v6_upper_repack as v6  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_OUTPUT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3"
)
DEFAULT_REPORT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.verify.json"
)

SPC3_VERSION_TWO_STAGE_V7 = 7
STREAM_KIND_TWO_STAGE_RUNTIME_GLOBAL_STAGE_LEVEL3 = 9

V7_SUBSTREAM_COUNT = 1
V7_GLOBAL_MAGIC = b"SPC3V7G1"
V7_GLOBAL_VERSION = 1
V7_GLOBAL_HEADER_STRUCT = struct.Struct("<8s8I")

STAGE_LAYOUT_SPLIT_BITMAPS = "split-bitmaps"
STAGE_LAYOUT_IDS = {STAGE_LAYOUT_SPLIT_BITMAPS: 1}
STAGE_LAYOUT_NAMES = {value: key for key, value in STAGE_LAYOUT_IDS.items()}

V7_STAGE_MAGIC = b"SPC3V7S1"
V7_STAGE_VERSION = 1
V7_STAGE_HEADER_STRUCT = struct.Struct("<8s11I")
V7_STAGE_ENTRY_STRUCT = struct.Struct("<IIQQQI")
V7_STAGE_KIND_STAGE1_RESIDUAL = 1
V7_STAGE_KIND_EXPLICIT_FULL = 2
V7_STAGE_ENTRY_COUNT = v6.V6_BAND_COUNT * 2
V7_STAGE_BAND_RAW_BYTES_PER_LANE = base.BITMAP_BYTES // v6.V6_BAND_COUNT

SHIFT_RECORD_STRUCT = struct.Struct("<HHB")


class StageBandWriter:
    """Temporary stage bitmap buckets keyed by upper PID high byte."""

    def __init__(self, root: Path, prefix: str, max_open: int = 768) -> None:
        self.root = root
        self.prefix = prefix
        self.max_open = max(1, max_open)
        self.lane_count = 0
        self._handles: dict[tuple[int, int], BinaryIO] = {}
        self._open_order: OrderedDict[tuple[int, int], None] = OrderedDict()

    def path(self, kind: int, band: int) -> Path:
        label = "stage1" if kind == V7_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
        return self.root / f"{self.prefix}_{label}_band_{band:03d}.bin"

    def _handle(self, kind: int, band: int) -> BinaryIO:
        key = (kind, band)
        handle = self._handles.get(key)
        if handle is None:
            handle = self.path(kind, band).open("ab")
            self._handles[key] = handle
        self._open_order.pop(key, None)
        self._open_order[key] = None
        while len(self._open_order) > self.max_open:
            old_key, _marker = self._open_order.popitem(last=False)
            old_handle = self._handles.pop(old_key)
            old_handle.close()
        return handle

    def write_lane(self, stage1_residual: bytes, explicit_full: bytes) -> None:
        if len(stage1_residual) != base.BITMAP_BYTES:
            raise RuntimeError(f"stage1 residual bitmap has {len(stage1_residual):,} bytes")
        if len(explicit_full) != base.BITMAP_BYTES:
            raise RuntimeError(f"explicit full bitmap has {len(explicit_full):,} bytes")
        chunk = V7_STAGE_BAND_RAW_BYTES_PER_LANE
        for band in range(v6.V6_BAND_COUNT):
            start = band * chunk
            stop = start + chunk
            self._handle(V7_STAGE_KIND_STAGE1_RESIDUAL, band).write(stage1_residual[start:stop])
            self._handle(V7_STAGE_KIND_EXPLICIT_FULL, band).write(explicit_full[start:stop])
        self.lane_count += 1

    def close_all(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._open_order.clear()


class StageBandReader:
    """Sequential reader for materialized v7 stage bands."""

    def __init__(self, root: Path, prefix: str, lane_count: int) -> None:
        self.root = root
        self.prefix = prefix
        self.lane_count = lane_count
        self.rows_read = 0
        self.stage1_handles = [
            self._path(V7_STAGE_KIND_STAGE1_RESIDUAL, band).open("rb") for band in range(v6.V6_BAND_COUNT)
        ]
        self.explicit_handles = [
            self._path(V7_STAGE_KIND_EXPLICIT_FULL, band).open("rb") for band in range(v6.V6_BAND_COUNT)
        ]

    def _path(self, kind: int, band: int) -> Path:
        label = "stage1" if kind == V7_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
        return self.root / f"{self.prefix}_{label}_band_{band:03d}.bin"

    def read_lane(self) -> tuple[bytes, bytes]:
        if self.rows_read >= self.lane_count:
            raise RuntimeError("read past end of v7 stage bands")
        stage1_parts: list[bytes] = []
        explicit_parts: list[bytes] = []
        chunk = V7_STAGE_BAND_RAW_BYTES_PER_LANE
        for handle in self.stage1_handles:
            raw = handle.read(chunk)
            if len(raw) != chunk:
                raise RuntimeError("stage1 band ended early")
            stage1_parts.append(raw)
        for handle in self.explicit_handles:
            raw = handle.read(chunk)
            if len(raw) != chunk:
                raise RuntimeError("explicit band ended early")
            explicit_parts.append(raw)
        self.rows_read += 1
        return b"".join(stage1_parts), b"".join(explicit_parts)

    def ensure_consumed(self) -> None:
        if self.rows_read != self.lane_count:
            raise RuntimeError(f"stage reader consumed {self.rows_read:,}/{self.lane_count:,} rows")
        for handle in [*self.stage1_handles, *self.explicit_handles]:
            if handle.read(1):
                raise RuntimeError("stage band has unread trailing bytes")

    def close(self) -> None:
        for handle in [*self.stage1_handles, *self.explicit_handles]:
            handle.close()


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
    parser.add_argument("--value-layout", choices=tuple(v6.VALUE_LAYOUT_IDS), default=v6.VALUE_LAYOUT_UPPER_MASK_GROUP)
    parser.add_argument("--stage-layout", choices=tuple(STAGE_LAYOUT_IDS), default=STAGE_LAYOUT_SPLIT_BITMAPS)
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


def full_bitmap_from_uppers(uppers: np.ndarray) -> bytes:
    bitmap = np.zeros(base.BITMAP_BYTES, dtype=np.uint8)
    if len(uppers):
        indices = (uppers >> np.uint32(3)).astype(np.intp, copy=False)
        bits = (np.uint8(1) << (uppers & np.uint32(7)).astype(np.uint8, copy=False)).astype(np.uint8, copy=False)
        np.bitwise_or.at(bitmap, indices, bits)
    return bitmap.tobytes()


def bitmap_uppers(bitmap: bytes) -> np.ndarray:
    if len(bitmap) != base.BITMAP_BYTES:
        raise RuntimeError(f"bitmap has {len(bitmap):,} bytes")
    bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8), bitorder="little")
    return np.flatnonzero(bits).astype(np.uint32)


def bitmap_bits_at(bitmap: bytes, uppers: np.ndarray) -> np.ndarray:
    raw = np.frombuffer(bitmap, dtype=np.uint8)
    byte_indices = (uppers >> np.uint32(3)).astype(np.intp, copy=False)
    shifts = (uppers & np.uint32(7)).astype(np.uint8, copy=False)
    return (((raw[byte_indices] >> shifts) & np.uint8(1)) != 0)


def xor_bitmaps(left: bytes, right: np.ndarray) -> bytes:
    return np.bitwise_xor(np.frombuffer(left, dtype=np.uint8), right).tobytes()


def pack_shift_records(shift_records: list[tuple[int, int, int]]) -> bytes:
    raw = bytearray(len(shift_records) * SHIFT_RECORD_STRUCT.size)
    cursor = 0
    for lane, upper, class_id in shift_records:
        SHIFT_RECORD_STRUCT.pack_into(raw, cursor, lane, upper, class_id)
        cursor += SHIFT_RECORD_STRUCT.size
    return bytes(raw)


def unpack_shift_records(raw: bytes) -> dict[int, tuple[np.ndarray, np.ndarray]]:
    if len(raw) % SHIFT_RECORD_STRUCT.size:
        raise RuntimeError(f"v7 shift stream has trailing partial record: {len(raw):,} bytes")
    by_lane: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for offset in range(0, len(raw), SHIFT_RECORD_STRUCT.size):
        lane, upper, class_id = SHIFT_RECORD_STRUCT.unpack_from(raw, offset)
        if class_id <= 0:
            raise RuntimeError(f"v7 shift record has invalid class id {class_id}")
        by_lane[lane].append((upper, class_id))
    result: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lane, pairs in by_lane.items():
        pairs.sort()
        uppers = np.asarray([upper for upper, _class_id in pairs], dtype=np.uint32)
        classes = np.asarray([class_id for _upper, class_id in pairs], dtype=np.uint8)
        result[lane] = (uppers, classes)
    return result


def parse_v7_substreams(handle: BinaryIO, entry: base.LaneEntry) -> dict[int, base.SubstreamEntry]:
    return two_stage.parse_substreams_count(
        handle,
        entry,
        substream_count=V7_SUBSTREAM_COUNT,
        expected_stream_kinds={STREAM_KIND_TWO_STAGE_RUNTIME_GLOBAL_STAGE_LEVEL3},
        required_kinds={base.SUBSTREAM_TEMPLATE},
    )


def make_v7_global_stream(
    model_stream: bytes,
    rule_stream: bytes,
    stage_stream: bytes,
    residual_stream: bytes,
    *,
    value_layout: str,
    stage_layout: str,
) -> bytes:
    header = V7_GLOBAL_HEADER_STRUCT.pack(
        V7_GLOBAL_MAGIC,
        V7_GLOBAL_VERSION,
        v6.VALUE_LAYOUT_IDS[value_layout],
        STAGE_LAYOUT_IDS[stage_layout],
        len(rule_stream),
        len(stage_stream),
        len(residual_stream),
        zlib.crc32(stage_stream) & 0xFFFFFFFF,
        zlib.crc32(residual_stream) & 0xFFFFFFFF,
    )
    return model_stream + header + rule_stream + stage_stream + residual_stream


def parse_v7_global_streams(
    handle: BinaryIO,
    header: base.Header,
) -> tuple[bytes, dict[str, object], bytes, dict[str, int | str], bytes, str, bytes, str]:
    predictor_stream = base.read_exact_at(handle, header.predictor_offset, header.predictor_size)
    global_offset = header.predictor_offset + header.predictor_size
    global_size = header.table_offset - global_offset
    global_stream = base.read_exact_at(handle, global_offset, global_size)
    if len(global_stream) < two_stage.MODEL_HEADER_STRUCT.size + V7_GLOBAL_HEADER_STRUCT.size:
        raise RuntimeError("v7 global stream too short")
    _magic, _version, model_payload_size, _crc = two_stage.MODEL_HEADER_STRUCT.unpack_from(global_stream)
    model_size = two_stage.MODEL_HEADER_STRUCT.size + model_payload_size
    if model_size + V7_GLOBAL_HEADER_STRUCT.size > len(global_stream):
        raise RuntimeError("v7 model stream extends past global stream")
    model_meta = two_stage.parse_model_stream(global_stream[:model_size])
    cursor = model_size
    (
        magic,
        version,
        value_layout_id,
        stage_layout_id,
        rule_size,
        stage_size,
        residual_size,
        stage_crc32,
        residual_crc32,
    ) = V7_GLOBAL_HEADER_STRUCT.unpack_from(global_stream, cursor)
    cursor += V7_GLOBAL_HEADER_STRUCT.size
    if magic != V7_GLOBAL_MAGIC or version != V7_GLOBAL_VERSION:
        raise RuntimeError(f"bad v7 global header: {magic!r}/{version}")
    value_layout = v6.VALUE_LAYOUT_NAMES.get(value_layout_id)
    if value_layout is None:
        raise RuntimeError(f"unsupported v7 value layout id: {value_layout_id}")
    stage_layout = STAGE_LAYOUT_NAMES.get(stage_layout_id)
    if stage_layout is None:
        raise RuntimeError(f"unsupported v7 stage layout id: {stage_layout_id}")
    if cursor + rule_size + stage_size + residual_size > len(global_stream):
        raise RuntimeError("v7 global section sizes exceed global stream")
    rule_stream = global_stream[cursor : cursor + rule_size]
    cursor += rule_size
    stage_stream = global_stream[cursor : cursor + stage_size]
    cursor += stage_size
    residual_stream = global_stream[cursor : cursor + residual_size]
    cursor += residual_size
    if cursor != len(global_stream):
        raise RuntimeError("v7 global stream has trailing bytes")
    if (zlib.crc32(stage_stream) & 0xFFFFFFFF) != stage_crc32:
        raise RuntimeError("v7 stage stream CRC mismatch")
    if (zlib.crc32(residual_stream) & 0xFFFFFFFF) != residual_crc32:
        raise RuntimeError("v7 residual stream CRC mismatch")
    rule_raw, rule_meta = base.parse_rule_stream(rule_stream)
    return predictor_stream, model_meta, rule_raw, rule_meta, stage_stream, stage_layout, residual_stream, value_layout


def build_stage_section(
    stage_bands: StageBandWriter,
    shift_records: list[tuple[int, int, int]],
    *,
    stage_layout: str,
    zstd_level: int,
) -> tuple[bytes, dict[str, object]]:
    if stage_layout != STAGE_LAYOUT_SPLIT_BITMAPS:
        raise RuntimeError(f"unsupported v7 stage layout: {stage_layout}")
    compressor = zstd.ZstdCompressor(level=zstd_level)
    entries: list[tuple[int, int, int, int, int, int, int]] = []
    payloads: list[bytes] = []
    band_meta: list[dict[str, int | str]] = []
    offset = 0
    expected_raw_size = stage_bands.lane_count * V7_STAGE_BAND_RAW_BYTES_PER_LANE
    for kind in (V7_STAGE_KIND_STAGE1_RESIDUAL, V7_STAGE_KIND_EXPLICIT_FULL):
        for band in range(v6.V6_BAND_COUNT):
            path = stage_bands.path(kind, band)
            raw = path.read_bytes() if path.exists() else b""
            if len(raw) != expected_raw_size:
                raise RuntimeError(
                    f"v7 stage band kind={kind} band={band} has {len(raw):,} bytes; "
                    f"expected {expected_raw_size:,}"
                )
            flags, stream = v6.best_codec(raw, compressor, zstd_level)
            crc32 = zlib.crc32(raw) & 0xFFFFFFFF
            entries.append((kind, band, flags, offset, len(stream), len(raw), crc32))
            payloads.append(stream)
            band_meta.append(
                {
                    "kind": "stage1_residual" if kind == V7_STAGE_KIND_STAGE1_RESIDUAL else "explicit_full",
                    "band": band,
                    "flags": flags,
                    "raw_bytes": len(raw),
                    "stream_bytes": len(stream),
                    "raw_crc32": crc32,
                }
            )
            offset += len(stream)

    shift_raw = pack_shift_records(shift_records)
    shift_flags, shift_stream = v6.best_codec(shift_raw, compressor, zstd_level)
    header = V7_STAGE_HEADER_STRUCT.pack(
        V7_STAGE_MAGIC,
        V7_STAGE_VERSION,
        STAGE_LAYOUT_IDS[stage_layout],
        stage_bands.lane_count,
        v6.V6_BAND_COUNT,
        len(entries),
        V7_STAGE_ENTRY_STRUCT.size,
        offset,
        shift_flags,
        len(shift_stream),
        len(shift_raw),
        zlib.crc32(shift_raw) & 0xFFFFFFFF,
    )
    table = b"".join(
        V7_STAGE_ENTRY_STRUCT.pack(
            kind * v6.V6_BAND_COUNT + band,
            flags,
            entry_offset,
            stream_size,
            raw_size,
            crc32,
        )
        for kind, band, flags, entry_offset, stream_size, raw_size, crc32 in entries
    )
    section = header + table + b"".join(payloads) + shift_stream
    totals = {
        "stage_section_bytes": len(section),
        "stage_table_bytes": len(header) + len(table),
        "stage_band_stream_bytes": offset,
        "stage_band_raw_bytes": expected_raw_size * len(entries),
        "stage1_global_stream_bytes": sum(int(meta["stream_bytes"]) for meta in band_meta[: v6.V6_BAND_COUNT]),
        "explicit_global_stream_bytes": sum(int(meta["stream_bytes"]) for meta in band_meta[v6.V6_BAND_COUNT :]),
        "shift_global_stream_bytes": len(shift_stream),
        "shift_global_raw_bytes": len(shift_raw),
        "shift_global_records": len(shift_records),
        "stage_bands": band_meta,
    }
    return section, totals


def parse_stage_section(raw: bytes) -> tuple[dict[tuple[int, int], dict[str, int]], bytes, bytes, str, int]:
    if len(raw) < V7_STAGE_HEADER_STRUCT.size:
        raise RuntimeError("v7 stage section too short")
    (
        magic,
        version,
        layout_id,
        lane_count,
        band_count,
        entry_count,
        entry_size,
        band_payload_size,
        shift_flags,
        shift_size,
        shift_raw_size,
        shift_crc32,
    ) = V7_STAGE_HEADER_STRUCT.unpack_from(raw)
    if magic != V7_STAGE_MAGIC or version != V7_STAGE_VERSION:
        raise RuntimeError(f"bad v7 stage header: {magic!r}/{version}")
    layout = STAGE_LAYOUT_NAMES.get(layout_id)
    if layout is None:
        raise RuntimeError(f"unsupported v7 stage layout id: {layout_id}")
    if band_count != v6.V6_BAND_COUNT or entry_count != V7_STAGE_ENTRY_COUNT:
        raise RuntimeError("v7 stage section geometry mismatch")
    if entry_size != V7_STAGE_ENTRY_STRUCT.size:
        raise RuntimeError("v7 stage entry size mismatch")
    table_offset = V7_STAGE_HEADER_STRUCT.size
    payload_offset = table_offset + entry_count * entry_size
    shift_offset = payload_offset + band_payload_size
    if len(raw) != shift_offset + shift_size:
        raise RuntimeError("v7 stage section size mismatch")
    entries: dict[tuple[int, int], dict[str, int]] = {}
    expected_offset = 0
    for index in range(entry_count):
        packed_kind, flags, offset, stream_size, raw_size, raw_crc32 = V7_STAGE_ENTRY_STRUCT.unpack_from(
            raw, table_offset + index * entry_size
        )
        kind = packed_kind // v6.V6_BAND_COUNT
        band = packed_kind % v6.V6_BAND_COUNT
        if kind not in {V7_STAGE_KIND_STAGE1_RESIDUAL, V7_STAGE_KIND_EXPLICIT_FULL}:
            raise RuntimeError(f"bad v7 stage kind {kind}")
        if offset != expected_offset or offset + stream_size > band_payload_size:
            raise RuntimeError(f"bad v7 stage band entry {index}")
        entries[(kind, band)] = {
            "flags": flags,
            "offset": offset,
            "stream_size": stream_size,
            "raw_size": raw_size,
            "raw_crc32": raw_crc32,
        }
        expected_offset += stream_size
    if expected_offset != band_payload_size:
        raise RuntimeError("v7 stage band payload has trailing bytes")
    shift_payload = raw[shift_offset : shift_offset + shift_size]
    shift_raw = v6.decode_payload(shift_payload, shift_flags, shift_raw_size, "v7 shift records")
    if (zlib.crc32(shift_raw) & 0xFFFFFFFF) != shift_crc32:
        raise RuntimeError("v7 shift raw CRC mismatch")
    return entries, raw[payload_offset:shift_offset], shift_raw, layout, lane_count


def materialize_stage_section(stage_stream: bytes, output_root: Path) -> tuple[dict[int, tuple[np.ndarray, np.ndarray]], int]:
    entries, payload, shift_raw, layout, lane_count = parse_stage_section(stage_stream)
    if layout != STAGE_LAYOUT_SPLIT_BITMAPS:
        raise RuntimeError(f"unsupported v7 stage layout: {layout}")
    prefix = "decoded_stage"
    for kind in (V7_STAGE_KIND_STAGE1_RESIDUAL, V7_STAGE_KIND_EXPLICIT_FULL):
        for band in range(v6.V6_BAND_COUNT):
            entry = entries[(kind, band)]
            raw_payload = payload[entry["offset"] : entry["offset"] + entry["stream_size"]]
            raw = v6.decode_payload(raw_payload, entry["flags"], entry["raw_size"], f"v7 stage kind={kind} band={band}")
            if (zlib.crc32(raw) & 0xFFFFFFFF) != entry["raw_crc32"]:
                raise RuntimeError(f"v7 stage kind={kind} band={band} raw CRC mismatch")
            label = "stage1" if kind == V7_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
            (output_root / f"{prefix}_{label}_band_{band:03d}.bin").write_bytes(raw)
    return unpack_shift_records(shift_raw), lane_count


def read_new_template(handle: BinaryIO, entry: base.LaneEntry) -> bytes:
    substreams = parse_v7_substreams(handle, entry)
    return two_stage.read_substream_raw(handle, entry, substreams[base.SUBSTREAM_TEMPLATE])


def source_lane_parts(
    src: BinaryIO,
    old_entry: base.LaneEntry,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
    rule_rows: np.ndarray,
) -> tuple[
    bytes,
    dict[str, int],
    bytes,
    bytes,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
    np.ndarray,
]:
    old_subs = two_stage.parse_source_substreams(src, old_entry)
    template_sub = old_subs[base.SUBSTREAM_TEMPLATE]
    bitmap_sub = old_subs[base.SUBSTREAM_BITMAP]
    template_stream = two_stage.read_substream_raw(src, old_entry, template_sub)
    actual_bitmap, uppers, actual = two_stage.source_actual_for_exceptions(src, old_entry, predictor)

    rule_bitmap = rule_rows[base.lane_group(old_entry.lane)]
    stage1_residual_bitmap = xor_bitmaps(actual_bitmap, rule_bitmap)

    if len(uppers):
        class_ids = clf.classify_values(uppers, actual, candidate_table)
        explicit_mask = class_ids < 0
        explicit_indices = np.flatnonzero(explicit_mask)
        shift_indices = np.flatnonzero(class_ids > 0).astype(np.intp)
        shift_uppers = uppers[shift_indices]
        shift_classes = class_ids[shift_indices].astype(np.uint8)
    else:
        class_ids = np.empty(0, dtype=np.int16)
        explicit_indices = np.empty(0, dtype=np.int64)
        shift_uppers = np.empty(0, dtype=np.uint32)
        shift_classes = np.empty(0, dtype=np.uint8)

    explicit_uppers = uppers[explicit_indices]
    explicit_full_bitmap = full_bitmap_from_uppers(explicit_uppers)

    sub_table_size = V7_SUBSTREAM_COUNT * base.TYPED_SUBSTREAM_ENTRY_SIZE
    sub_template = base.SubstreamEntry(
        base.SUBSTREAM_TEMPLATE,
        template_sub.flags,
        sub_table_size,
        len(template_stream),
        template_sub.raw_size,
    )
    lane_stream = base.pack_substream_entry(sub_template) + template_stream
    if bitmap_sub.raw_size != base.BITMAP_BYTES:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} source bitmap raw size changed")

    explicit_actual = actual[explicit_indices]
    explicit_baseline = candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
    stats = {
        "old_exceptions": int(len(uppers)),
        "stage2_normal": int((class_ids == 0).sum()) if len(class_ids) else 0,
        "stage2_shift": int((class_ids > 0).sum()) if len(class_ids) else 0,
        "stage2_explicit": int(len(explicit_indices)),
        "template_stream_bytes": len(template_stream),
        "lane_stream_bytes": len(lane_stream),
        "stage1_residual_bits": int(np.unpackbits(np.frombuffer(stage1_residual_bitmap, dtype=np.uint8), bitorder="little").sum()),
    }
    return (
        lane_stream,
        stats,
        stage1_residual_bitmap,
        explicit_full_bitmap,
        shift_uppers,
        shift_classes,
        explicit_uppers,
        explicit_actual,
        explicit_baseline,
    )


def add_totals(totals: dict[str, int], stats: dict[str, int]) -> None:
    for key, value in stats.items():
        totals[key] = totals.get(key, 0) + int(value)


def stage_from_raw(
    *,
    lane: int,
    stage1_residual: bytes,
    explicit_full: bytes,
    shift_by_lane: dict[int, tuple[np.ndarray, np.ndarray]],
    rule_rows: np.ndarray,
    candidate_table: np.ndarray,
) -> tuple[bytes, np.ndarray, np.ndarray, np.ndarray, np.ndarray, dict[str, int]]:
    reconstructed_bitmap = xor_bitmaps(stage1_residual, rule_rows[base.lane_group(lane)])
    uppers = bitmap_uppers(reconstructed_bitmap)
    explicit_uppers_all = bitmap_uppers(explicit_full)
    if len(explicit_uppers_all):
        positions = np.searchsorted(uppers, explicit_uppers_all)
        if bool((positions >= len(uppers)).any()) or bool(np.any(uppers[positions] != explicit_uppers_all)):
            raise RuntimeError(f"lane 0x{lane:04X} explicit bitmap contains non-miss cells")
    explicit_mask = bitmap_bits_at(explicit_full, uppers) if len(uppers) else np.empty(0, dtype=np.bool_)
    explicit_indices = np.flatnonzero(explicit_mask)

    shift_uppers, shift_classes = shift_by_lane.get(
        lane,
        (np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.uint8)),
    )
    if len(shift_uppers):
        if int(shift_classes.min()) <= 0 or int(shift_classes.max()) >= candidate_table.shape[0]:
            raise RuntimeError(f"lane 0x{lane:04X} shift class outside candidate range")
        shift_indices = np.searchsorted(uppers, shift_uppers)
        if bool((shift_indices >= len(uppers)).any()) or bool(np.any(uppers[shift_indices] != shift_uppers)):
            raise RuntimeError(f"lane 0x{lane:04X} shift records contain non-miss cells")
        if len(np.unique(shift_indices)) != len(shift_indices):
            raise RuntimeError(f"lane 0x{lane:04X} has duplicate shift upper values")
        if bool(explicit_mask[shift_indices].any()):
            raise RuntimeError(f"lane 0x{lane:04X} marks same cell as shift and explicit")
    else:
        shift_indices = np.empty(0, dtype=np.int64)

    stats = {
        "old_exceptions": int(len(uppers)),
        "stage2_normal": int(len(uppers) - len(shift_indices) - len(explicit_indices)),
        "stage2_shift": int(len(shift_indices)),
        "stage2_explicit": int(len(explicit_indices)),
        "stage1_residual_bits": int(np.unpackbits(np.frombuffer(stage1_residual, dtype=np.uint8), bitorder="little").sum()),
    }
    return reconstructed_bitmap, uppers, explicit_indices, shift_indices, shift_classes, stats


def rebuild_model_from_meta(model_meta: dict[str, object]) -> tuple[np.ndarray, dict[str, object]]:
    candidate_table, _classes, rebuilt_model = two_stage.build_candidate_model(
        start_rng=two_stage.parse_int(str(model_meta["start_rng"])),
        runtime_max_steps=int(model_meta["runtime_max_steps"]),
        base_model=str(model_meta["base_model"]),
        max_extra=int(model_meta["max_extra"]),
        residual_encoding=str(model_meta.get("residual_encoding", v6.VALUE_LAYOUT_UPPER_MASK_GROUP)),
    )
    if rebuilt_model != model_meta:
        raise RuntimeError("rebuilt v7 model metadata differs from stored model")
    return candidate_table, rebuilt_model


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
    stage_layout: str,
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

    scratch_context: tempfile.TemporaryDirectory[str] | None = None
    if scratch_dir is None:
        scratch_context = tempfile.TemporaryDirectory(prefix="spc3-v7-global-stage-")
        scratch_root = Path(scratch_context.name)
    else:
        scratch_root = scratch_dir
        scratch_root.mkdir(parents=True, exist_ok=True)

    temp_path, out = base.open_temp_output(output_path)
    lane_data_path = scratch_root / "lane_data.bin"
    stage_bands = StageBandWriter(scratch_root, "pack_stage")
    residual_buckets = v6.BandBuckets(scratch_root, "pack_residual")
    shift_records: list[tuple[int, int, int]] = []
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
                (
                    lane_stream,
                    stats,
                    stage1_residual,
                    explicit_full,
                    shift_uppers,
                    shift_classes,
                    explicit_uppers,
                    explicit_actual,
                    explicit_baseline,
                ) = source_lane_parts(src, old_entry, predictor, candidate_table, rule_rows)
                stream_offset_in_lane_data = lane_data.tell()
                lane_data.write(lane_stream)
                stage_bands.write_lane(stage1_residual, explicit_full)
                for upper, class_id in zip(shift_uppers, shift_classes, strict=True):
                    shift_records.append((old_entry.lane, int(upper), int(class_id)))
                residual_buckets.write(explicit_uppers, explicit_actual, explicit_baseline)
                new_entries.append(
                    base.LaneEntry(
                        lane=old_entry.lane,
                        level=base.SPC3_LEVEL,
                        stream_kind=STREAM_KIND_TWO_STAGE_RUNTIME_GLOBAL_STAGE_LEVEL3,
                        flags=0,
                        source_zip_size=old_entry.source_zip_size,
                        source_zip_crc32=old_entry.source_zip_crc32,
                        source_zip_fnv64=old_entry.source_zip_fnv64,
                        original_payload_crc32=old_entry.original_payload_crc32,
                        rebuilt_payload_crc32=old_entry.rebuilt_payload_crc32,
                        stream_offset=stream_offset_in_lane_data,
                        stream_size=len(lane_stream),
                        uncompressed_model_size=base.RECORD_SIZE,
                        predictor_matches=old_entry.predictor_matches,
                        predictor_exceptions=old_entry.predictor_exceptions,
                    )
                )
                add_totals(totals, stats)
                if len(lane_samples) < 32:
                    lane_samples.append({"lane": f"0x{old_entry.lane:04X}", **stats})
                if progress_every and (index % progress_every == 0 or index == len(old_entries)):
                    print(f"v7 pack scan: {index}/{len(old_entries)} lanes", flush=True)

        stage_bands.close_all()
        residual_buckets.close_all()
        stage_section, stage_totals = build_stage_section(
            stage_bands,
            shift_records,
            stage_layout=stage_layout,
            zstd_level=zstd_level,
        )
        residual_section, residual_totals = v6.build_residual_section(
            residual_buckets,
            layout=value_layout,
            zstd_level=zstd_level,
        )
        global_stream = make_v7_global_stream(
            model_stream,
            rule_stream,
            stage_section,
            residual_section,
            value_layout=value_layout,
            stage_layout=stage_layout,
        )
        table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream) + len(global_stream)
        table_size = len(new_entries) * base.SPC3_TABLE_ENTRY_SIZE
        data_offset = table_offset + table_size
        data_size = lane_data_path.stat().st_size

        out.write(b"\x00" * base.SPC3_HEADER_SIZE)
        out.write(predictor_stream)
        out.write(global_stream)
        out.write(b"\x00" * table_size)
        with lane_data_path.open("rb") as lane_data:
            while True:
                chunk = lane_data.read(1024 * 1024)
                if not chunk:
                    break
                out.write(chunk)

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
            raise RuntimeError("v7 lane data size mismatch")

        new_header = base.Header(
            version=SPC3_VERSION_TWO_STAGE_V7,
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
        stage_bands.close_all()
        residual_buckets.close_all()
        if scratch_context is not None and not keep_scratch:
            scratch_context.cleanup()

    add_totals(totals, {key: value for key, value in stage_totals.items() if isinstance(value, int)})
    add_totals(totals, {key: value for key, value in residual_totals.items() if isinstance(value, int)})
    elapsed = time.perf_counter() - started
    output_size = output_path.stat().st_size
    source_size = input_path.stat().st_size
    explicit_total = totals.get("old_exceptions", 0)
    report = {
        "schema": "spc3_v7_global_stage_repack.v1",
        "mode": "pack",
        "input": str(input_path),
        "output": str(output_path),
        "elapsed_seconds": elapsed,
        "zstd_level": zstd_level,
        "value_layout": value_layout,
        "stage_layout": stage_layout,
        "sample_lanes": sample_lanes,
        "predictor_source": predictor_source,
        "model": model_meta,
        "rule": asdict(rule_info),
        "class_count": len(classes),
        "totals": totals,
        "stage_bands": stage_totals["stage_bands"],
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

    with tempfile.TemporaryDirectory(prefix="spc3-v7-verify-") as temp_name:
        temp_root = Path(temp_name)
        with new_path.open("rb") as new_handle:
            new_header = base.parse_header(new_handle)
            if new_header.version != SPC3_VERSION_TWO_STAGE_V7:
                raise RuntimeError(f"not a v7 SPC3: version={new_header.version}")
            (
                new_predictor_stream,
                model_meta,
                rule_raw,
                rule_meta,
                stage_stream,
                stage_layout,
                residual_stream,
                value_layout,
            ) = parse_v7_global_streams(new_handle, new_header)
        candidate_table, _rebuilt_model = rebuild_model_from_meta(model_meta)
        rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
        shift_by_lane, stage_lane_count = materialize_stage_section(stage_stream, temp_root)
        if stage_lane_count != new_header.lane_count:
            raise RuntimeError("v7 stage lane count differs from SPC3 header")

        baseline_buckets = v6.BandBuckets(temp_root, "verify_residual")
        stage_reader = StageBandReader(temp_root, "decoded_stage", new_header.lane_count)
        try:
            with new_path.open("rb") as new_handle:
                header = base.parse_header(new_handle)
                for entry in base.parse_lane_entries(new_handle, header):
                    stage1_residual, explicit_full = stage_reader.read_lane()
                    _bitmap, uppers, explicit_indices, _shift_indices, _shift_classes, _stats = stage_from_raw(
                        lane=entry.lane,
                        stage1_residual=stage1_residual,
                        explicit_full=explicit_full,
                        shift_by_lane=shift_by_lane,
                        rule_rows=rule_rows,
                        candidate_table=candidate_table,
                    )
                    explicit_uppers = uppers[explicit_indices]
                    baseline = (
                        candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
                    )
                    baseline_buckets.write_baseline_only(explicit_uppers, baseline)
            stage_reader.ensure_consumed()
        finally:
            stage_reader.close()
            baseline_buckets.close_all()

        residual_entries, actual_buckets = v6.materialize_decoded_residuals(
            residual_stream=residual_stream,
            layout=value_layout,
            baseline_buckets=baseline_buckets,
            output_root=temp_root,
        )
        actual_handles = {
            band: actual_buckets.actual_path(band).open("rb")
            for band in range(v6.V6_BAND_COUNT)
            if int(actual_buckets.counts[band])
        }
        stage_reader = StageBandReader(temp_root, "decoded_stage", new_header.lane_count)
        try:
            with original_path.open("rb") as old_handle, new_path.open("rb") as new_handle:
                old_header = base.parse_header(old_handle)
                new_header = base.parse_header(new_handle)
                if not (new_header.flags & two_stage.SPC3_FLAG_TWO_STAGE_RUNTIME):
                    raise RuntimeError(f"missing two-stage flag: 0x{new_header.flags:08X}")
                old_entries = base.parse_lane_entries(old_handle, old_header)
                new_entries = base.parse_lane_entries(new_handle, new_header)
                old_by_lane = {entry.lane: entry for entry in old_entries}

                old_predictor_stream = base.read_exact_at(old_handle, old_header.predictor_offset, old_header.predictor_size)
                if old_predictor_stream != new_predictor_stream:
                    raise RuntimeError("embedded predictor stream differs from source SPC3")
                predictor, predictor_source = clf.load_predictor(old_handle, old_header, predictor_json)

                for index, new_entry in enumerate(new_entries, 1):
                    old_entry = old_by_lane.get(new_entry.lane)
                    if old_entry is None:
                        raise RuntimeError(f"new lane 0x{new_entry.lane:04X} not present in original")
                    if new_entry.original_payload_crc32 != old_entry.original_payload_crc32:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} CRC metadata changed")
                    if new_entry.predictor_exceptions != old_entry.predictor_exceptions:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} exception metadata changed")

                    old_subs = two_stage.parse_source_substreams(old_handle, old_entry)
                    old_template = two_stage.read_substream_raw(old_handle, old_entry, old_subs[base.SUBSTREAM_TEMPLATE])
                    new_template = read_new_template(new_handle, new_entry)
                    if old_template != new_template:
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "template"})

                    old_bitmap, old_uppers, old_actual = two_stage.source_actual_for_exceptions(
                        old_handle,
                        old_entry,
                        predictor,
                    )
                    stage1_residual, explicit_full = stage_reader.read_lane()
                    reconstructed_bitmap, uppers, explicit_indices, shift_indices, shift_classes, stage_stats = stage_from_raw(
                        lane=new_entry.lane,
                        stage1_residual=stage1_residual,
                        explicit_full=explicit_full,
                        shift_by_lane=shift_by_lane,
                        rule_rows=rule_rows,
                        candidate_table=candidate_table,
                    )
                    if reconstructed_bitmap != old_bitmap:
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "stage1_bitmap"})
                    if len(uppers) != len(old_uppers) or bool(np.any(uppers != old_uppers)):
                        mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "old_miss_uppers"})

                    reconstructed = candidate_table[0, uppers].astype(np.uint32, copy=True)
                    if len(shift_indices):
                        reconstructed[shift_indices] = candidate_table[shift_classes, uppers[shift_indices]]
                    if len(explicit_indices):
                        explicit_uppers = uppers[explicit_indices]
                        explicit_actual = v6.read_explicit_actuals_from_bands(actual_handles, explicit_uppers)
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
                        "template_stream_bytes": int(len(new_template)),
                        **stage_stats,
                    }
                    add_totals(totals, stats)
                    if progress_every and (index % progress_every == 0 or index == len(new_entries)):
                        print(f"v7 verify pass: {index}/{len(new_entries)} lanes", flush=True)

            stage_reader.ensure_consumed()
            for band, handle in actual_handles.items():
                remaining = handle.read(1)
                if remaining:
                    raise RuntimeError(f"decoded residual band {band} has unread bytes")
        finally:
            stage_reader.close()
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
        "schema": "spc3_v7_global_stage_verify.v1",
        "mode": "verify",
        "new_spc3": str(new_path),
        "original_spc3": str(original_path),
        "elapsed_seconds": elapsed,
        "status": "ok" if not mismatches else "failed",
        "mismatch_count": len(mismatches),
        "mismatch_samples": mismatches[:20],
        "predictor_source": predictor_source,
        "value_layout": value_layout,
        "stage_layout": stage_layout,
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
        raise RuntimeError(f"v7 verification failed with {len(mismatches)} mismatches")
    print(f"v7 verify ok: {new_path} ({output_size:,} bytes) in {elapsed:.1f}s")
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
            stage_layout=args.stage_layout,
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
