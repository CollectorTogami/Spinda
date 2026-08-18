#!/usr/bin/env python3
"""Pack and verify SPC3 v8 with compact global streams.

This experimental v8 transform builds on v7 and targets the remaining
container overhead: adaptive global stage-band transforms, a global template
block instead of per-lane template streams, and residual values encoded against
the better of runtime RS/FRLG and the old predictor on each explicit cell.
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
DEFAULT_OUTPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3"
DEFAULT_REPORT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json"
)

SPC3_VERSION_TWO_STAGE_V8 = 8
STREAM_KIND_TWO_STAGE_RUNTIME_COMPACT_LEVEL3 = 10

V8_GLOBAL_MAGIC = b"SPC3V8G1"
V8_GLOBAL_VERSION = 1
V8_GLOBAL_HEADER_STRUCT = struct.Struct("<8s10I")

STAGE_LAYOUT_ADAPTIVE_BITMAPS = "adaptive-bitmaps"
STAGE_LAYOUT_IDS = {STAGE_LAYOUT_ADAPTIVE_BITMAPS: 1}
STAGE_LAYOUT_NAMES = {value: key for key, value in STAGE_LAYOUT_IDS.items()}

VALUE_LAYOUT_SELECTED_MASK_GROUP = "selected-mask-group"
VALUE_LAYOUT_IDS = {VALUE_LAYOUT_SELECTED_MASK_GROUP: 1}
VALUE_LAYOUT_NAMES = {value: key for key, value in VALUE_LAYOUT_IDS.items()}

V8_STAGE_MAGIC = b"SPC3V8S1"
V8_STAGE_VERSION = 1
V8_STAGE_HEADER_STRUCT = struct.Struct("<8s11I")
V8_STAGE_ENTRY_STRUCT = struct.Struct("<IIIQQQI")
V8_STAGE_KIND_STAGE1_RESIDUAL = 1
V8_STAGE_KIND_EXPLICIT_FULL = 2
V8_STAGE_ENTRY_COUNT = v6.V6_BAND_COUNT * 2
V8_STAGE_BAND_RAW_BYTES_PER_LANE = base.BITMAP_BYTES // v6.V6_BAND_COUNT
V8_STAGE_TRANSFORM_RAW = 0
V8_STAGE_TRANSFORM_TRANSPOSE = 1
V8_STAGE_TRANSFORM_INDEX_LIST = 2
V8_STAGE_TRANSFORM_NAMES = {
    V8_STAGE_TRANSFORM_RAW: "raw",
    V8_STAGE_TRANSFORM_TRANSPOSE: "byte-transpose",
    V8_STAGE_TRANSFORM_INDEX_LIST: "bit-index-list",
}

V8_RESIDUAL_MAGIC = b"SPC3V8R1"
V8_RESIDUAL_VERSION = 1
V8_RESIDUAL_HEADER_STRUCT = struct.Struct("<8s5I")
V8_RESIDUAL_BAND_ENTRY_STRUCT = struct.Struct("<IIIQQQII")
V8_RESIDUAL_MODE_RUNTIME_MASK_GROUP = 0
V8_RESIDUAL_MODE_SELECTED_MASK_GROUP = 1
V8_RESIDUAL_MODE_NAMES = {
    V8_RESIDUAL_MODE_RUNTIME_MASK_GROUP: "runtime-mask-group",
    V8_RESIDUAL_MODE_SELECTED_MASK_GROUP: "selected-mask-group",
}

V8_TEMPLATE_MAGIC = b"SPC3V8T1"
V8_TEMPLATE_VERSION = 1
V8_TEMPLATE_HEADER_STRUCT = struct.Struct("<8s7I")

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
        label = "stage1" if kind == V8_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
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
        chunk = V8_STAGE_BAND_RAW_BYTES_PER_LANE
        for band in range(v6.V6_BAND_COUNT):
            start = band * chunk
            stop = start + chunk
            self._handle(V8_STAGE_KIND_STAGE1_RESIDUAL, band).write(stage1_residual[start:stop])
            self._handle(V8_STAGE_KIND_EXPLICIT_FULL, band).write(explicit_full[start:stop])
        self.lane_count += 1

    def close_all(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._open_order.clear()


class StageBandReader:
    """Sequential reader for materialized v8 stage bands."""

    def __init__(self, root: Path, prefix: str, lane_count: int) -> None:
        self.root = root
        self.prefix = prefix
        self.lane_count = lane_count
        self.rows_read = 0
        self.stage1_handles = [
            self._path(V8_STAGE_KIND_STAGE1_RESIDUAL, band).open("rb") for band in range(v6.V6_BAND_COUNT)
        ]
        self.explicit_handles = [
            self._path(V8_STAGE_KIND_EXPLICIT_FULL, band).open("rb") for band in range(v6.V6_BAND_COUNT)
        ]

    def _path(self, kind: int, band: int) -> Path:
        label = "stage1" if kind == V8_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
        return self.root / f"{self.prefix}_{label}_band_{band:03d}.bin"

    def read_lane(self) -> tuple[bytes, bytes]:
        if self.rows_read >= self.lane_count:
            raise RuntimeError("read past end of v8 stage bands")
        stage1_parts: list[bytes] = []
        explicit_parts: list[bytes] = []
        chunk = V8_STAGE_BAND_RAW_BYTES_PER_LANE
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
    parser.add_argument("--value-layout", choices=tuple(VALUE_LAYOUT_IDS), default=VALUE_LAYOUT_SELECTED_MASK_GROUP)
    parser.add_argument("--stage-layout", choices=tuple(STAGE_LAYOUT_IDS), default=STAGE_LAYOUT_ADAPTIVE_BITMAPS)
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
        raise RuntimeError(f"v8 shift stream has trailing partial record: {len(raw):,} bytes")
    by_lane: dict[int, list[tuple[int, int]]] = defaultdict(list)
    for offset in range(0, len(raw), SHIFT_RECORD_STRUCT.size):
        lane, upper, class_id = SHIFT_RECORD_STRUCT.unpack_from(raw, offset)
        if class_id <= 0:
            raise RuntimeError(f"v8 shift record has invalid class id {class_id}")
        by_lane[lane].append((upper, class_id))
    result: dict[int, tuple[np.ndarray, np.ndarray]] = {}
    for lane, pairs in by_lane.items():
        pairs.sort()
        uppers = np.asarray([upper for upper, _class_id in pairs], dtype=np.uint32)
        classes = np.asarray([class_id for _upper, class_id in pairs], dtype=np.uint8)
        result[lane] = (uppers, classes)
    return result


def make_v8_global_stream(
    model_stream: bytes,
    rule_stream: bytes,
    stage_stream: bytes,
    residual_stream: bytes,
    template_stream: bytes,
    *,
    value_layout: str,
    stage_layout: str,
) -> bytes:
    header = V8_GLOBAL_HEADER_STRUCT.pack(
        V8_GLOBAL_MAGIC,
        V8_GLOBAL_VERSION,
        VALUE_LAYOUT_IDS[value_layout],
        STAGE_LAYOUT_IDS[stage_layout],
        len(rule_stream),
        len(stage_stream),
        len(residual_stream),
        len(template_stream),
        zlib.crc32(stage_stream) & 0xFFFFFFFF,
        zlib.crc32(residual_stream) & 0xFFFFFFFF,
        zlib.crc32(template_stream) & 0xFFFFFFFF,
    )
    return model_stream + header + rule_stream + stage_stream + residual_stream + template_stream


def parse_v8_global_streams(
    handle: BinaryIO,
    header: base.Header,
) -> tuple[bytes, dict[str, object], bytes, dict[str, int | str], bytes, str, bytes, str, bytes]:
    predictor_stream = base.read_exact_at(handle, header.predictor_offset, header.predictor_size)
    global_offset = header.predictor_offset + header.predictor_size
    global_size = header.table_offset - global_offset
    global_stream = base.read_exact_at(handle, global_offset, global_size)
    if len(global_stream) < two_stage.MODEL_HEADER_STRUCT.size + V8_GLOBAL_HEADER_STRUCT.size:
        raise RuntimeError("v8 global stream too short")
    _magic, _version, model_payload_size, _crc = two_stage.MODEL_HEADER_STRUCT.unpack_from(global_stream)
    model_size = two_stage.MODEL_HEADER_STRUCT.size + model_payload_size
    if model_size + V8_GLOBAL_HEADER_STRUCT.size > len(global_stream):
        raise RuntimeError("v8 model stream extends past global stream")
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
        template_size,
        stage_crc32,
        residual_crc32,
        template_crc32,
    ) = V8_GLOBAL_HEADER_STRUCT.unpack_from(global_stream, cursor)
    cursor += V8_GLOBAL_HEADER_STRUCT.size
    if magic != V8_GLOBAL_MAGIC or version != V8_GLOBAL_VERSION:
        raise RuntimeError(f"bad v8 global header: {magic!r}/{version}")
    value_layout = VALUE_LAYOUT_NAMES.get(value_layout_id)
    if value_layout is None:
        raise RuntimeError(f"unsupported v8 value layout id: {value_layout_id}")
    stage_layout = STAGE_LAYOUT_NAMES.get(stage_layout_id)
    if stage_layout is None:
        raise RuntimeError(f"unsupported v8 stage layout id: {stage_layout_id}")
    if cursor + rule_size + stage_size + residual_size + template_size > len(global_stream):
        raise RuntimeError("v8 global section sizes exceed global stream")
    rule_stream = global_stream[cursor : cursor + rule_size]
    cursor += rule_size
    stage_stream = global_stream[cursor : cursor + stage_size]
    cursor += stage_size
    residual_stream = global_stream[cursor : cursor + residual_size]
    cursor += residual_size
    template_stream = global_stream[cursor : cursor + template_size]
    cursor += template_size
    if cursor != len(global_stream):
        raise RuntimeError("v8 global stream has trailing bytes")
    if (zlib.crc32(stage_stream) & 0xFFFFFFFF) != stage_crc32:
        raise RuntimeError("v8 stage stream CRC mismatch")
    if (zlib.crc32(residual_stream) & 0xFFFFFFFF) != residual_crc32:
        raise RuntimeError("v8 residual stream CRC mismatch")
    if (zlib.crc32(template_stream) & 0xFFFFFFFF) != template_crc32:
        raise RuntimeError("v8 template stream CRC mismatch")
    rule_raw, rule_meta = base.parse_rule_stream(rule_stream)
    return (
        predictor_stream,
        model_meta,
        rule_raw,
        rule_meta,
        stage_stream,
        stage_layout,
        residual_stream,
        value_layout,
        template_stream,
    )


def transpose_stage_band(raw: bytes, lane_count: int) -> bytes:
    if lane_count == 0:
        return b""
    return np.frombuffer(raw, dtype=np.uint8).reshape(lane_count, V8_STAGE_BAND_RAW_BYTES_PER_LANE).T.copy().tobytes()


def untranspose_stage_band(raw: bytes, lane_count: int) -> bytes:
    if lane_count == 0:
        if raw:
            raise RuntimeError("empty transposed stage band has bytes")
        return b""
    return np.frombuffer(raw, dtype=np.uint8).reshape(V8_STAGE_BAND_RAW_BYTES_PER_LANE, lane_count).T.copy().tobytes()


def stage_index_list_size(raw: bytes, lane_count: int) -> int:
    if lane_count == 0:
        return 0
    matrix = np.frombuffer(raw, dtype=np.uint8).reshape(lane_count, V8_STAGE_BAND_RAW_BYTES_PER_LANE)
    bits = np.unpackbits(matrix, axis=1, bitorder="little")
    counts = bits.sum(axis=0, dtype=np.uint64)
    chosen = np.minimum(counts, np.uint64(lane_count) - counts)
    return int(256 * 5 + int(chosen.sum()) * 2)


def encode_stage_index_list(raw: bytes, lane_count: int) -> bytes:
    if lane_count == 0:
        return b""
    matrix = np.frombuffer(raw, dtype=np.uint8).reshape(lane_count, V8_STAGE_BAND_RAW_BYTES_PER_LANE)
    bits = np.unpackbits(matrix, axis=1, bitorder="little")
    out = bytearray()
    for column_index in range(bits.shape[1]):
        column = bits[:, column_index].astype(np.bool_, copy=False)
        ones = int(column.sum())
        zeros = lane_count - ones
        if ones <= zeros:
            indices = np.flatnonzero(column).astype("<u2", copy=False)
            out.append(0)
        else:
            indices = np.flatnonzero(~column).astype("<u2", copy=False)
            out.append(1)
        out.extend(struct.pack("<I", len(indices)))
        out.extend(indices.tobytes())
    return bytes(out)


def decode_stage_index_list(raw: bytes, lane_count: int) -> bytes:
    if lane_count == 0:
        if raw:
            raise RuntimeError("empty stage index-list has bytes")
        return b""
    out = np.zeros((lane_count, V8_STAGE_BAND_RAW_BYTES_PER_LANE), dtype=np.uint8)
    cursor = 0
    for column_index in range(256):
        if cursor + 5 > len(raw):
            raise RuntimeError("stage index-list ended inside column header")
        mode = raw[cursor]
        count = struct.unpack_from("<I", raw, cursor + 1)[0]
        cursor += 5
        if count > lane_count:
            raise RuntimeError(f"stage index-list column {column_index} count exceeds lane count")
        byte_count = count * 2
        if cursor + byte_count > len(raw):
            raise RuntimeError("stage index-list ended inside lane indices")
        indices = np.frombuffer(raw[cursor : cursor + byte_count], dtype="<u2").astype(np.intp, copy=False)
        cursor += byte_count
        byte_index = column_index >> 3
        bit = np.uint8(1 << (column_index & 7))
        if mode == 0:
            out[indices, byte_index] |= bit
        elif mode == 1:
            out[:, byte_index] |= bit
            out[indices, byte_index] &= np.uint8(~int(bit) & 0xFF)
        else:
            raise RuntimeError(f"bad stage index-list mode {mode}")
    if cursor != len(raw):
        raise RuntimeError("stage index-list has trailing bytes")
    return out.tobytes()


def encode_stage_transform(raw: bytes, lane_count: int, transform: int) -> bytes:
    if transform == V8_STAGE_TRANSFORM_RAW:
        return raw
    if transform == V8_STAGE_TRANSFORM_TRANSPOSE:
        return transpose_stage_band(raw, lane_count)
    if transform == V8_STAGE_TRANSFORM_INDEX_LIST:
        return encode_stage_index_list(raw, lane_count)
    raise RuntimeError(f"unsupported v8 stage transform: {transform}")


def decode_stage_transform(raw: bytes, lane_count: int, transform: int) -> bytes:
    if transform == V8_STAGE_TRANSFORM_RAW:
        return raw
    if transform == V8_STAGE_TRANSFORM_TRANSPOSE:
        return untranspose_stage_band(raw, lane_count)
    if transform == V8_STAGE_TRANSFORM_INDEX_LIST:
        return decode_stage_index_list(raw, lane_count)
    raise RuntimeError(f"unsupported v8 stage transform: {transform}")


def choose_stage_payload(
    raw: bytes,
    *,
    lane_count: int,
    compressor: zstd.ZstdCompressor,
    zstd_level: int,
) -> tuple[int, int, bytes, bytes]:
    best_transform = V8_STAGE_TRANSFORM_RAW
    best_encoded = raw
    best_flags, best_stream = v6.best_codec(raw, compressor, zstd_level)

    transposed = transpose_stage_band(raw, lane_count)
    transpose_flags, transpose_stream = v6.best_codec(transposed, compressor, zstd_level)
    if len(transpose_stream) < len(best_stream):
        best_transform = V8_STAGE_TRANSFORM_TRANSPOSE
        best_encoded = transposed
        best_flags = transpose_flags
        best_stream = transpose_stream

    index_size = stage_index_list_size(raw, lane_count)
    if index_size and index_size < len(raw):
        indexed = encode_stage_index_list(raw, lane_count)
        index_flags, index_stream = v6.best_codec(indexed, compressor, zstd_level)
        if len(index_stream) < len(best_stream):
            best_transform = V8_STAGE_TRANSFORM_INDEX_LIST
            best_encoded = indexed
            best_flags = index_flags
            best_stream = index_stream

    return best_transform, best_flags, best_stream, best_encoded


def build_stage_section(
    stage_bands: StageBandWriter,
    shift_records: list[tuple[int, int, int]],
    *,
    stage_layout: str,
    zstd_level: int,
) -> tuple[bytes, dict[str, object]]:
    if stage_layout != STAGE_LAYOUT_ADAPTIVE_BITMAPS:
        raise RuntimeError(f"unsupported v8 stage layout: {stage_layout}")
    compressor = zstd.ZstdCompressor(level=zstd_level)
    entries: list[tuple[int, int, int, int, int, int, int, int]] = []
    payloads: list[bytes] = []
    band_meta: list[dict[str, int | str]] = []
    transform_counts = {name: 0 for name in V8_STAGE_TRANSFORM_NAMES.values()}
    offset = 0
    expected_raw_size = stage_bands.lane_count * V8_STAGE_BAND_RAW_BYTES_PER_LANE
    for kind in (V8_STAGE_KIND_STAGE1_RESIDUAL, V8_STAGE_KIND_EXPLICIT_FULL):
        for band in range(v6.V6_BAND_COUNT):
            path = stage_bands.path(kind, band)
            raw = path.read_bytes() if path.exists() else b""
            if len(raw) != expected_raw_size:
                raise RuntimeError(
                    f"v8 stage band kind={kind} band={band} has {len(raw):,} bytes; "
                    f"expected {expected_raw_size:,}"
                )
            transform, flags, stream, encoded = choose_stage_payload(
                raw,
                lane_count=stage_bands.lane_count,
                compressor=compressor,
                zstd_level=zstd_level,
            )
            crc32 = zlib.crc32(raw) & 0xFFFFFFFF
            entries.append((kind, band, flags, transform, offset, len(stream), len(encoded), crc32))
            payloads.append(stream)
            transform_name = V8_STAGE_TRANSFORM_NAMES[transform]
            transform_counts[transform_name] += 1
            band_meta.append(
                {
                    "kind": "stage1_residual" if kind == V8_STAGE_KIND_STAGE1_RESIDUAL else "explicit_full",
                    "band": band,
                    "flags": flags,
                    "transform": transform_name,
                    "raw_bytes": len(raw),
                    "encoded_raw_bytes": len(encoded),
                    "stream_bytes": len(stream),
                    "raw_crc32": crc32,
                }
            )
            offset += len(stream)

    shift_raw = pack_shift_records(shift_records)
    shift_flags, shift_stream = v6.best_codec(shift_raw, compressor, zstd_level)
    header = V8_STAGE_HEADER_STRUCT.pack(
        V8_STAGE_MAGIC,
        V8_STAGE_VERSION,
        STAGE_LAYOUT_IDS[stage_layout],
        stage_bands.lane_count,
        v6.V6_BAND_COUNT,
        len(entries),
        V8_STAGE_ENTRY_STRUCT.size,
        offset,
        shift_flags,
        len(shift_stream),
        len(shift_raw),
        zlib.crc32(shift_raw) & 0xFFFFFFFF,
    )
    table = b"".join(
        V8_STAGE_ENTRY_STRUCT.pack(
            kind * v6.V6_BAND_COUNT + band,
            flags,
            transform,
            entry_offset,
            stream_size,
            encoded_raw_size,
            crc32,
        )
        for kind, band, flags, transform, entry_offset, stream_size, encoded_raw_size, crc32 in entries
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
        "stage_transform_counts": transform_counts,
        "stage_bands": band_meta,
    }
    return section, totals


def parse_stage_section(raw: bytes) -> tuple[dict[tuple[int, int], dict[str, int]], bytes, bytes, str, int]:
    if len(raw) < V8_STAGE_HEADER_STRUCT.size:
        raise RuntimeError("v8 stage section too short")
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
    ) = V8_STAGE_HEADER_STRUCT.unpack_from(raw)
    if magic != V8_STAGE_MAGIC or version != V8_STAGE_VERSION:
        raise RuntimeError(f"bad v8 stage header: {magic!r}/{version}")
    layout = STAGE_LAYOUT_NAMES.get(layout_id)
    if layout is None:
        raise RuntimeError(f"unsupported v8 stage layout id: {layout_id}")
    if band_count != v6.V6_BAND_COUNT or entry_count != V8_STAGE_ENTRY_COUNT:
        raise RuntimeError("v8 stage section geometry mismatch")
    if entry_size != V8_STAGE_ENTRY_STRUCT.size:
        raise RuntimeError("v8 stage entry size mismatch")
    table_offset = V8_STAGE_HEADER_STRUCT.size
    payload_offset = table_offset + entry_count * entry_size
    shift_offset = payload_offset + band_payload_size
    if len(raw) != shift_offset + shift_size:
        raise RuntimeError("v8 stage section size mismatch")
    entries: dict[tuple[int, int], dict[str, int]] = {}
    expected_offset = 0
    for index in range(entry_count):
        packed_kind, flags, transform, offset, stream_size, raw_size, raw_crc32 = V8_STAGE_ENTRY_STRUCT.unpack_from(
            raw, table_offset + index * entry_size
        )
        kind = packed_kind // v6.V6_BAND_COUNT
        band = packed_kind % v6.V6_BAND_COUNT
        if kind not in {V8_STAGE_KIND_STAGE1_RESIDUAL, V8_STAGE_KIND_EXPLICIT_FULL}:
            raise RuntimeError(f"bad v8 stage kind {kind}")
        if transform not in V8_STAGE_TRANSFORM_NAMES:
            raise RuntimeError(f"bad v8 stage transform {transform}")
        if offset != expected_offset or offset + stream_size > band_payload_size:
            raise RuntimeError(f"bad v8 stage band entry {index}")
        entries[(kind, band)] = {
            "flags": flags,
            "transform": transform,
            "offset": offset,
            "stream_size": stream_size,
            "raw_size": raw_size,
            "raw_crc32": raw_crc32,
        }
        expected_offset += stream_size
    if expected_offset != band_payload_size:
        raise RuntimeError("v8 stage band payload has trailing bytes")
    shift_payload = raw[shift_offset : shift_offset + shift_size]
    shift_raw = v6.decode_payload(shift_payload, shift_flags, shift_raw_size, "v8 shift records")
    if (zlib.crc32(shift_raw) & 0xFFFFFFFF) != shift_crc32:
        raise RuntimeError("v8 shift raw CRC mismatch")
    return entries, raw[payload_offset:shift_offset], shift_raw, layout, lane_count


def materialize_stage_section(stage_stream: bytes, output_root: Path) -> tuple[dict[int, tuple[np.ndarray, np.ndarray]], int]:
    entries, payload, shift_raw, layout, lane_count = parse_stage_section(stage_stream)
    if layout != STAGE_LAYOUT_ADAPTIVE_BITMAPS:
        raise RuntimeError(f"unsupported v8 stage layout: {layout}")
    prefix = "decoded_stage"
    expected_raw_size = lane_count * V8_STAGE_BAND_RAW_BYTES_PER_LANE
    for kind in (V8_STAGE_KIND_STAGE1_RESIDUAL, V8_STAGE_KIND_EXPLICIT_FULL):
        for band in range(v6.V6_BAND_COUNT):
            entry = entries[(kind, band)]
            raw_payload = payload[entry["offset"] : entry["offset"] + entry["stream_size"]]
            encoded = v6.decode_payload(
                raw_payload,
                entry["flags"],
                entry["raw_size"],
                f"v8 stage kind={kind} band={band}",
            )
            raw = decode_stage_transform(encoded, lane_count, entry["transform"])
            if len(raw) != expected_raw_size:
                raise RuntimeError(f"v8 stage kind={kind} band={band} decoded raw size mismatch")
            if (zlib.crc32(raw) & 0xFFFFFFFF) != entry["raw_crc32"]:
                raise RuntimeError(f"v8 stage kind={kind} band={band} raw CRC mismatch")
            label = "stage1" if kind == V8_STAGE_KIND_STAGE1_RESIDUAL else "explicit"
            (output_root / f"{prefix}_{label}_band_{band:03d}.bin").write_bytes(raw)
    return unpack_shift_records(shift_raw), lane_count


def source_lane_parts(
    src: BinaryIO,
    old_entry: base.LaneEntry,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
    rule_rows: np.ndarray,
) -> tuple[bytes, dict[str, int], bytes, bytes, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    old_subs = two_stage.parse_source_substreams(src, old_entry)
    template_sub = old_subs[base.SUBSTREAM_TEMPLATE]
    bitmap_sub = old_subs[base.SUBSTREAM_BITMAP]
    template_stream = two_stage.read_substream_raw(src, old_entry, template_sub)
    if template_sub.raw_size != base.RECORD_SIZE:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} template raw size is not 80 bytes")
    if len(template_stream) != base.RECORD_SIZE:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} template stream has {len(template_stream):,} bytes")
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

    if bitmap_sub.raw_size != base.BITMAP_BYTES:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} source bitmap raw size changed")

    explicit_actual = actual[explicit_indices]
    explicit_baseline = candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
    explicit_old_baseline = predictor[explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
    stats = {
        "old_exceptions": int(len(uppers)),
        "stage2_normal": int((class_ids == 0).sum()) if len(class_ids) else 0,
        "stage2_shift": int((class_ids > 0).sum()) if len(class_ids) else 0,
        "stage2_explicit": int(len(explicit_indices)),
        "template_stream_bytes": len(template_stream),
        "lane_stream_bytes": 0,
        "stage1_residual_bits": int(np.unpackbits(np.frombuffer(stage1_residual_bitmap, dtype=np.uint8), bitorder="little").sum()),
    }
    return (
        template_stream,
        stats,
        stage1_residual_bitmap,
        explicit_full_bitmap,
        shift_uppers,
        shift_classes,
        explicit_uppers,
        explicit_actual,
        explicit_baseline,
        explicit_old_baseline,
    )


def add_totals(totals: dict[str, int], stats: dict[str, int]) -> None:
    for key, value in stats.items():
        totals[key] = totals.get(key, 0) + int(value)


class ResidualChoiceBuckets:
    """Temporary residual buckets with runtime and old-predictor baselines."""

    def __init__(self, root: Path, prefix: str, max_open: int = 768) -> None:
        self.root = root
        self.prefix = prefix
        self.max_open = max(1, max_open)
        self.counts = np.zeros(v6.V6_BAND_COUNT, dtype=np.uint64)
        self._handles: dict[tuple[str, int], BinaryIO] = {}
        self._open_order: OrderedDict[tuple[str, int], None] = OrderedDict()

    def path(self, kind: str, band: int) -> Path:
        return self.root / f"{self.prefix}_band_{band:03d}_{kind}.u32"

    def actual_path(self, band: int) -> Path:
        return self.path("actual", band)

    def runtime_path(self, band: int) -> Path:
        return self.path("runtime", band)

    def old_path(self, band: int) -> Path:
        return self.path("old", band)

    def _handle(self, kind: str, band: int) -> BinaryIO:
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

    def close_all(self) -> None:
        for handle in self._handles.values():
            handle.close()
        self._handles.clear()
        self._open_order.clear()

    def write(
        self,
        uppers: np.ndarray,
        actual: np.ndarray,
        runtime_baseline: np.ndarray,
        old_baseline: np.ndarray,
    ) -> None:
        self._write_parts(uppers, actual, runtime_baseline, old_baseline)

    def write_context_only(self, uppers: np.ndarray, runtime_baseline: np.ndarray, old_baseline: np.ndarray) -> None:
        empty_actual = np.empty(len(uppers), dtype=np.uint32)
        self._write_parts(uppers, empty_actual, runtime_baseline, old_baseline, include_actual=False)

    def _write_parts(
        self,
        uppers: np.ndarray,
        actual: np.ndarray,
        runtime_baseline: np.ndarray,
        old_baseline: np.ndarray,
        *,
        include_actual: bool = True,
    ) -> None:
        if len(uppers) == 0:
            return
        if len(runtime_baseline) != len(uppers) or len(old_baseline) != len(uppers):
            raise RuntimeError("residual bucket baseline length mismatch")
        if include_actual and len(actual) != len(uppers):
            raise RuntimeError("residual bucket actual length mismatch")
        bands = (uppers >> np.uint32(8)).astype(np.uint8, copy=False)
        for band_value in np.unique(bands):
            band = int(band_value)
            mask = bands == band_value
            if include_actual:
                self._handle("actual", band).write(np.asarray(actual[mask], dtype="<u4").tobytes())
            self._handle("runtime", band).write(np.asarray(runtime_baseline[mask], dtype="<u4").tobytes())
            self._handle("old", band).write(np.asarray(old_baseline[mask], dtype="<u4").tobytes())
            self.counts[band] += int(mask.sum())


def pack_selector_bits(selector: np.ndarray) -> bytes:
    return np.packbits(np.asarray(selector, dtype=np.uint8), bitorder="little").tobytes()


def unpack_selector_bits(raw: bytes, count: int) -> np.ndarray:
    expected = (count + 7) // 8
    if len(raw) != expected:
        raise RuntimeError(f"selector bitmap size mismatch: {len(raw):,} != {expected:,}")
    if count == 0:
        return np.empty(0, dtype=np.bool_)
    return np.unpackbits(np.frombuffer(raw, dtype=np.uint8), bitorder="little")[:count].astype(np.bool_, copy=False)


def select_residual_baseline(
    actual: np.ndarray,
    runtime_baseline: np.ndarray,
    old_baseline: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    count = len(actual)
    if len(runtime_baseline) != count or len(old_baseline) != count:
        raise RuntimeError("residual selector length mismatch")
    if count == 0:
        return np.empty(0, dtype=np.uint32), np.empty(0, dtype=np.bool_), {
            "residual_old_predictor_selected": 0,
            "residual_runtime_selected": 0,
        }
    actual_fields = two_stage.iv32_stat_fields(actual)
    runtime_fields = two_stage.iv32_stat_fields(runtime_baseline)
    old_fields = two_stage.iv32_stat_fields(old_baseline)
    runtime_changed = (actual_fields != runtime_fields).sum(axis=0)
    old_changed = (actual_fields != old_fields).sum(axis=0)
    use_old = old_changed < runtime_changed
    selected = np.asarray(runtime_baseline, dtype=np.uint32).copy()
    selected[use_old] = old_baseline[use_old]
    stats = {
        "residual_old_predictor_selected": int(use_old.sum()),
        "residual_runtime_selected": int(count - int(use_old.sum())),
        "residual_changed_values_runtime_only": int(runtime_changed.sum()),
        "residual_changed_values_old_only": int(old_changed.sum()),
        "residual_changed_values_selected": int(np.minimum(runtime_changed, old_changed).sum()),
    }
    return selected, use_old, stats


def build_residual_section(
    buckets: ResidualChoiceBuckets,
    *,
    layout: str,
    zstd_level: int,
) -> tuple[bytes, dict[str, int | list[dict[str, int]]]]:
    if layout != VALUE_LAYOUT_SELECTED_MASK_GROUP:
        raise RuntimeError(f"unsupported v8 residual layout: {layout}")
    compressor = zstd.ZstdCompressor(level=zstd_level)
    entries: list[base.SubstreamEntry] = []
    band_meta: list[dict[str, int]] = []
    payloads: list[bytes] = []
    totals: dict[str, int] = {
        "global_value_stream_bytes": 0,
        "global_value_raw_bytes": 0,
        "global_value_records": 0,
        "residual_runtime_mode_records": 0,
        "residual_selected_mode_records": 0,
        "residual_runtime_forced_stream_bytes": 0,
        "residual_selected_forced_stream_bytes": 0,
        "residual_selector_raw_bytes": 0,
        "residual_old_predictor_selected": 0,
        "residual_runtime_selected": 0,
        "residual_changed_values_runtime_only": 0,
        "residual_changed_values_old_only": 0,
        "residual_changed_values_selected": 0,
        "stat_delta_changed_values": 0,
        "stat_delta_high_bit_mismatches": 0,
        **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
        **{f"stat_delta_stat_{stat}_changed_values": 0 for stat in range(v6.STAT_COUNT)},
    }
    offset = 0
    for band in range(v6.V6_BAND_COUNT):
        count = int(buckets.counts[band])
        if count:
            actual = np.fromfile(buckets.actual_path(band), dtype="<u4")
            runtime_baseline = np.fromfile(buckets.runtime_path(band), dtype="<u4")
            old_baseline = np.fromfile(buckets.old_path(band), dtype="<u4")
            if len(actual) != count or len(runtime_baseline) != count or len(old_baseline) != count:
                raise RuntimeError(f"band {band} residual bucket count mismatch")

            runtime_raw = v6.pack_mask_group_values(actual, runtime_baseline)
            runtime_stats = v6.stat_delta_stats(actual, runtime_baseline)
            runtime_flags, runtime_stream = v6.best_codec(runtime_raw, compressor, zstd_level)

            selected, use_old, selector_stats = select_residual_baseline(actual, runtime_baseline, old_baseline)
            selector_raw = pack_selector_bits(use_old)
            selected_value_raw = v6.pack_mask_group_values(actual, selected)
            selected_stats = v6.stat_delta_stats(actual, selected)
            selected_raw = selector_raw + selected_value_raw
            selected_flags, selected_stream = v6.best_codec(selected_raw, compressor, zstd_level)

            totals["residual_runtime_forced_stream_bytes"] += len(runtime_stream)
            totals["residual_selected_forced_stream_bytes"] += len(selected_stream)
            if len(selected_stream) < len(runtime_stream):
                mode = V8_RESIDUAL_MODE_SELECTED_MASK_GROUP
                raw = selected_raw
                flags = selected_flags
                stream = selected_stream
                chosen_selector_raw_size = len(selector_raw)
                chosen_selector_stats = selector_stats
                value_stats = selected_stats
                totals["residual_selected_mode_records"] += count
            else:
                mode = V8_RESIDUAL_MODE_RUNTIME_MASK_GROUP
                raw = runtime_raw
                flags = runtime_flags
                stream = runtime_stream
                chosen_selector_raw_size = 0
                chosen_selector_stats = {
                    "residual_old_predictor_selected": 0,
                    "residual_runtime_selected": count,
                    "residual_changed_values_runtime_only": int(runtime_stats["stat_delta_changed_values"]),
                    "residual_changed_values_old_only": 0,
                    "residual_changed_values_selected": int(runtime_stats["stat_delta_changed_values"]),
                }
                value_stats = runtime_stats
                totals["residual_runtime_mode_records"] += count
        else:
            mode = V8_RESIDUAL_MODE_RUNTIME_MASK_GROUP
            raw = b""
            flags, stream = v6.best_codec(raw, compressor, zstd_level)
            chosen_selector_raw_size = 0
            chosen_selector_stats = {
                "residual_old_predictor_selected": 0,
                "residual_runtime_selected": 0,
                "residual_changed_values_runtime_only": 0,
                "residual_changed_values_old_only": 0,
                "residual_changed_values_selected": 0,
            }
            value_stats = {
                "stat_delta_changed_values": 0,
                "stat_delta_high_bit_mismatches": 0,
                **{f"stat_delta_records_changed_{value}": 0 for value in range(7)},
                **{f"stat_delta_stat_{stat}_changed_values": 0 for stat in range(v6.STAT_COUNT)},
            }
        entries.append(base.SubstreamEntry(band, flags, offset, len(stream), len(raw)))
        payloads.append(stream)
        crc32 = zlib.crc32(raw) & 0xFFFFFFFF
        band_meta.append(
            {
                "band": band,
                "records": count,
                "mode": mode,
                "mode_name": V8_RESIDUAL_MODE_NAMES[mode],
                "raw_bytes": len(raw),
                "stream_bytes": len(stream),
                "selector_raw_bytes": chosen_selector_raw_size,
                "raw_crc32": crc32,
            }
        )
        offset += len(stream)
        totals["global_value_stream_bytes"] += len(stream)
        totals["global_value_raw_bytes"] += len(raw)
        totals["global_value_records"] += count
        totals["residual_selector_raw_bytes"] += chosen_selector_raw_size
        for stats in (chosen_selector_stats, value_stats):
            for key, value in stats.items():
                totals[key] = totals.get(key, 0) + int(value)

    header = V8_RESIDUAL_HEADER_STRUCT.pack(
        V8_RESIDUAL_MAGIC,
        V8_RESIDUAL_VERSION,
        VALUE_LAYOUT_IDS[layout],
        v6.V6_BAND_COUNT,
        V8_RESIDUAL_BAND_ENTRY_STRUCT.size,
        offset,
    )
    table = b"".join(
        V8_RESIDUAL_BAND_ENTRY_STRUCT.pack(
            entry.kind,
            entry.flags,
            band_meta[entry.kind]["mode"],
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
    if len(raw) < V8_RESIDUAL_HEADER_STRUCT.size:
        raise RuntimeError("v8 residual section too short")
    magic, version, layout_id, band_count, entry_size, data_size = V8_RESIDUAL_HEADER_STRUCT.unpack_from(raw)
    if magic != V8_RESIDUAL_MAGIC or version != V8_RESIDUAL_VERSION:
        raise RuntimeError(f"bad v8 residual header: {magic!r}/{version}")
    if band_count != v6.V6_BAND_COUNT or entry_size != V8_RESIDUAL_BAND_ENTRY_STRUCT.size:
        raise RuntimeError("v8 residual geometry mismatch")
    layout = VALUE_LAYOUT_NAMES.get(layout_id)
    if layout is None:
        raise RuntimeError(f"unsupported v8 residual layout id: {layout_id}")
    table_offset = V8_RESIDUAL_HEADER_STRUCT.size
    data_offset = table_offset + band_count * entry_size
    if len(raw) != data_offset + data_size:
        raise RuntimeError("v8 residual section size mismatch")
    entries: list[dict[str, int]] = []
    expected_offset = 0
    for index in range(band_count):
        band, flags, mode, offset, stream_size, raw_size, record_count, raw_crc32 = V8_RESIDUAL_BAND_ENTRY_STRUCT.unpack_from(
            raw,
            table_offset + index * entry_size,
        )
        if band != index or offset != expected_offset or offset + stream_size > data_size:
            raise RuntimeError(f"bad v8 residual band entry {index}")
        if mode not in V8_RESIDUAL_MODE_NAMES:
            raise RuntimeError(f"bad v8 residual mode {mode}")
        entries.append(
            {
                "band": band,
                "flags": flags,
                "mode": mode,
                "offset": offset,
                "stream_size": stream_size,
                "raw_size": raw_size,
                "record_count": record_count,
                "raw_crc32": raw_crc32,
            }
        )
        expected_offset += stream_size
    if expected_offset != data_size:
        raise RuntimeError("v8 residual payload has trailing bytes")
    return entries, raw[data_offset:], layout


def materialize_decoded_residuals(
    *,
    residual_stream: bytes,
    layout: str,
    context_buckets: ResidualChoiceBuckets,
    output_root: Path,
) -> tuple[list[dict[str, int]], v6.BandBuckets]:
    if layout != VALUE_LAYOUT_SELECTED_MASK_GROUP:
        raise RuntimeError(f"unsupported v8 residual layout: {layout}")
    entries, payload, section_layout = parse_residual_section(residual_stream)
    if section_layout != layout:
        raise RuntimeError(f"residual layout mismatch: {section_layout} != {layout}")
    actual_buckets = v6.BandBuckets(output_root, "decoded")
    for entry in entries:
        band = entry["band"]
        count = entry["record_count"]
        if count != int(context_buckets.counts[band]):
            raise RuntimeError(f"band {band} record count mismatch")
        runtime_baseline = (
            np.fromfile(context_buckets.runtime_path(band), dtype="<u4") if count else np.empty(0, dtype=np.uint32)
        )
        old_baseline = (
            np.fromfile(context_buckets.old_path(band), dtype="<u4") if count else np.empty(0, dtype=np.uint32)
        )
        if len(runtime_baseline) != count or len(old_baseline) != count:
            raise RuntimeError(f"band {band} residual context count mismatch")
        raw_payload = payload[entry["offset"] : entry["offset"] + entry["stream_size"]]
        raw = v6.decode_payload(raw_payload, entry["flags"], entry["raw_size"], f"v8 residual band {band}")
        if (zlib.crc32(raw) & 0xFFFFFFFF) != entry["raw_crc32"]:
            raise RuntimeError(f"band {band} residual raw CRC mismatch")
        if entry["mode"] == V8_RESIDUAL_MODE_RUNTIME_MASK_GROUP:
            actual = v6.unpack_mask_group_values(raw, runtime_baseline)
        elif entry["mode"] == V8_RESIDUAL_MODE_SELECTED_MASK_GROUP:
            selector_size = (count + 7) // 8
            selector = unpack_selector_bits(raw[:selector_size], count)
            selected = runtime_baseline.copy()
            selected[selector] = old_baseline[selector]
            actual = v6.unpack_mask_group_values(raw[selector_size:], selected)
        else:
            raise RuntimeError(f"unsupported v8 residual mode {entry['mode']}")
        if len(actual) != count:
            raise RuntimeError(f"band {band} decoded count mismatch")
        if count:
            with actual_buckets.actual_path(band).open("wb") as handle:
                handle.write(np.asarray(actual, dtype="<u4").tobytes())
            actual_buckets.counts[band] = count
    return entries, actual_buckets


def build_template_section(template_path: Path, lane_count: int, *, zstd_level: int) -> tuple[bytes, dict[str, int]]:
    raw = template_path.read_bytes()
    expected_size = lane_count * base.RECORD_SIZE
    if len(raw) != expected_size:
        raise RuntimeError(f"template block has {len(raw):,} bytes; expected {expected_size:,}")
    compressor = zstd.ZstdCompressor(level=zstd_level)
    flags, stream = v6.best_codec(raw, compressor, zstd_level)
    header = V8_TEMPLATE_HEADER_STRUCT.pack(
        V8_TEMPLATE_MAGIC,
        V8_TEMPLATE_VERSION,
        lane_count,
        base.RECORD_SIZE,
        flags,
        len(stream),
        len(raw),
        zlib.crc32(raw) & 0xFFFFFFFF,
    )
    section = header + stream
    return section, {
        "template_section_bytes": len(section),
        "global_template_stream_bytes": len(stream),
        "template_raw_bytes": len(raw),
    }


def decode_template_section(raw: bytes, lane_count: int) -> bytes:
    if len(raw) < V8_TEMPLATE_HEADER_STRUCT.size:
        raise RuntimeError("v8 template section too short")
    magic, version, stored_lanes, record_size, flags, stream_size, raw_size, raw_crc32 = V8_TEMPLATE_HEADER_STRUCT.unpack_from(raw)
    if magic != V8_TEMPLATE_MAGIC or version != V8_TEMPLATE_VERSION:
        raise RuntimeError(f"bad v8 template header: {magic!r}/{version}")
    if stored_lanes != lane_count or record_size != base.RECORD_SIZE or raw_size != lane_count * base.RECORD_SIZE:
        raise RuntimeError("v8 template geometry mismatch")
    if len(raw) != V8_TEMPLATE_HEADER_STRUCT.size + stream_size:
        raise RuntimeError("v8 template section size mismatch")
    payload = raw[V8_TEMPLATE_HEADER_STRUCT.size :]
    decoded = v6.decode_payload(payload, flags, raw_size, "v8 template block")
    if (zlib.crc32(decoded) & 0xFFFFFFFF) != raw_crc32:
        raise RuntimeError("v8 template block CRC mismatch")
    return decoded


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
        residual_encoding=str(model_meta.get("residual_encoding", VALUE_LAYOUT_SELECTED_MASK_GROUP)),
    )
    if rebuilt_model != model_meta:
        raise RuntimeError("rebuilt v8 model metadata differs from stored model")
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
    if value_layout != VALUE_LAYOUT_SELECTED_MASK_GROUP:
        raise RuntimeError(f"unsupported v8 value layout: {value_layout}")
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
        scratch_context = tempfile.TemporaryDirectory(prefix="spc3-v8-compact-")
        scratch_root = Path(scratch_context.name)
    else:
        scratch_root = scratch_dir
        scratch_root.mkdir(parents=True, exist_ok=True)

    temp_path, out = base.open_temp_output(output_path)
    template_path = scratch_root / "templates.bin"
    stage_bands = StageBandWriter(scratch_root, "pack_stage")
    residual_buckets = ResidualChoiceBuckets(scratch_root, "pack_residual")
    shift_records: list[tuple[int, int, int]] = []
    new_entries: list[base.LaneEntry] = []
    totals: dict[str, int] = {}
    lane_samples: list[dict[str, object]] = []

    try:
        with input_path.open("rb") as src, template_path.open("wb") as template_out:
            old_header = base.parse_header(src)
            if old_header.version != base.SPC3_VERSION_V2 or not (old_header.flags & base.SPC3_FLAG_PREDICTOR_EMBEDDED):
                raise RuntimeError("input must be SPC3 v2 typed level-3 with an embedded predictor")
            old_entries = selected_entries(base.parse_lane_entries(src, old_header), sample_lanes)
            predictor_stream = base.read_exact_at(src, old_header.predictor_offset, old_header.predictor_size)
            predictor, predictor_source = clf.load_predictor(src, old_header, predictor_json)

            for index, old_entry in enumerate(old_entries, 1):
                (
                    template_stream,
                    stats,
                    stage1_residual,
                    explicit_full,
                    shift_uppers,
                    shift_classes,
                    explicit_uppers,
                    explicit_actual,
                    explicit_runtime_baseline,
                    explicit_old_baseline,
                ) = source_lane_parts(src, old_entry, predictor, candidate_table, rule_rows)
                template_out.write(template_stream)
                stage_bands.write_lane(stage1_residual, explicit_full)
                for upper, class_id in zip(shift_uppers, shift_classes, strict=True):
                    shift_records.append((old_entry.lane, int(upper), int(class_id)))
                residual_buckets.write(
                    explicit_uppers,
                    explicit_actual,
                    explicit_runtime_baseline,
                    explicit_old_baseline,
                )
                new_entries.append(
                    base.LaneEntry(
                        lane=old_entry.lane,
                        level=base.SPC3_LEVEL,
                        stream_kind=STREAM_KIND_TWO_STAGE_RUNTIME_COMPACT_LEVEL3,
                        flags=0,
                        source_zip_size=old_entry.source_zip_size,
                        source_zip_crc32=old_entry.source_zip_crc32,
                        source_zip_fnv64=old_entry.source_zip_fnv64,
                        original_payload_crc32=old_entry.original_payload_crc32,
                        rebuilt_payload_crc32=old_entry.rebuilt_payload_crc32,
                        stream_offset=0,
                        stream_size=0,
                        uncompressed_model_size=base.RECORD_SIZE,
                        predictor_matches=old_entry.predictor_matches,
                        predictor_exceptions=old_entry.predictor_exceptions,
                    )
                )
                add_totals(totals, stats)
                if len(lane_samples) < 32:
                    lane_samples.append({"lane": f"0x{old_entry.lane:04X}", **stats})
                if progress_every and (index % progress_every == 0 or index == len(old_entries)):
                    print(f"v8 pack scan: {index}/{len(old_entries)} lanes", flush=True)

        stage_bands.close_all()
        residual_buckets.close_all()
        stage_section, stage_totals = build_stage_section(
            stage_bands,
            shift_records,
            stage_layout=stage_layout,
            zstd_level=zstd_level,
        )
        residual_section, residual_totals = build_residual_section(
            residual_buckets,
            layout=value_layout,
            zstd_level=zstd_level,
        )
        template_section, template_totals = build_template_section(
            template_path,
            len(new_entries),
            zstd_level=zstd_level,
        )
        global_stream = make_v8_global_stream(
            model_stream,
            rule_stream,
            stage_section,
            residual_section,
            template_section,
            value_layout=value_layout,
            stage_layout=stage_layout,
        )
        table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream) + len(global_stream)
        table_size = len(new_entries) * base.SPC3_TABLE_ENTRY_SIZE
        data_offset = table_offset + table_size
        data_size = 0

        out.write(b"\x00" * base.SPC3_HEADER_SIZE)
        out.write(predictor_stream)
        out.write(global_stream)
        out.write(b"\x00" * table_size)

        adjusted_entries: list[base.LaneEntry] = []
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
                stream_offset=data_offset,
                stream_size=0,
                uncompressed_model_size=entry.uncompressed_model_size,
                predictor_matches=entry.predictor_matches,
                predictor_exceptions=entry.predictor_exceptions,
            )
            adjusted_entries.append(adjusted)

        new_header = base.Header(
            version=SPC3_VERSION_TWO_STAGE_V8,
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
    add_totals(totals, {key: value for key, value in template_totals.items() if isinstance(value, int)})
    elapsed = time.perf_counter() - started
    output_size = output_path.stat().st_size
    source_size = input_path.stat().st_size
    explicit_total = totals.get("old_exceptions", 0)
    report = {
        "schema": "spc3_v8_compact_repack.v1",
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
        "stage_transform_counts": stage_totals["stage_transform_counts"],
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

    with tempfile.TemporaryDirectory(prefix="spc3-v8-verify-") as temp_name:
        temp_root = Path(temp_name)
        with new_path.open("rb") as new_handle:
            new_header = base.parse_header(new_handle)
            if new_header.version != SPC3_VERSION_TWO_STAGE_V8:
                raise RuntimeError(f"not a v8 SPC3: version={new_header.version}")
            (
                new_predictor_stream,
                model_meta,
                rule_raw,
                rule_meta,
                stage_stream,
                stage_layout,
                residual_stream,
                value_layout,
                template_stream,
            ) = parse_v8_global_streams(new_handle, new_header)
            predictor, predictor_source = clf.load_predictor(new_handle, new_header, predictor_json)
        candidate_table, _rebuilt_model = rebuild_model_from_meta(model_meta)
        rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
        shift_by_lane, stage_lane_count = materialize_stage_section(stage_stream, temp_root)
        if stage_lane_count != new_header.lane_count:
            raise RuntimeError("v8 stage lane count differs from SPC3 header")
        template_block = decode_template_section(template_stream, new_header.lane_count)

        context_buckets = ResidualChoiceBuckets(temp_root, "verify_residual")
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
                    runtime_baseline = (
                        candidate_table[0, explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
                    )
                    old_baseline = predictor[explicit_uppers] if len(explicit_uppers) else np.empty(0, dtype=np.uint32)
                    context_buckets.write_context_only(explicit_uppers, runtime_baseline, old_baseline)
            stage_reader.ensure_consumed()
        finally:
            stage_reader.close()
            context_buckets.close_all()

        residual_entries, actual_buckets = materialize_decoded_residuals(
            residual_stream=residual_stream,
            layout=value_layout,
            context_buckets=context_buckets,
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

                for index, new_entry in enumerate(new_entries, 1):
                    if new_entry.stream_kind != STREAM_KIND_TWO_STAGE_RUNTIME_COMPACT_LEVEL3:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} has bad v8 stream kind: {new_entry.stream_kind}")
                    if new_entry.stream_size != 0:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} has unexpected v8 lane stream bytes")
                    old_entry = old_by_lane.get(new_entry.lane)
                    if old_entry is None:
                        raise RuntimeError(f"new lane 0x{new_entry.lane:04X} not present in original")
                    if new_entry.original_payload_crc32 != old_entry.original_payload_crc32:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} CRC metadata changed")
                    if new_entry.predictor_exceptions != old_entry.predictor_exceptions:
                        raise RuntimeError(f"lane 0x{new_entry.lane:04X} exception metadata changed")

                    old_subs = two_stage.parse_source_substreams(old_handle, old_entry)
                    old_template = two_stage.read_substream_raw(old_handle, old_entry, old_subs[base.SUBSTREAM_TEMPLATE])
                    template_start = (index - 1) * base.RECORD_SIZE
                    new_template = template_block[template_start : template_start + base.RECORD_SIZE]
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
                        print(f"v8 verify pass: {index}/{len(new_entries)} lanes", flush=True)

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
        "template_section_bytes": len(template_stream),
        "template_raw_bytes": len(template_block),
    }
    report = {
        "schema": "spc3_v8_compact_verify.v1",
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
        raise RuntimeError(f"v8 verification failed with {len(mismatches)} mismatches")
    print(f"v8 verify ok: {new_path} ({output_size:,} bytes) in {elapsed:.1f}s")
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
