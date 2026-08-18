#!/usr/bin/env python3
"""Repack SPC3 level-3 data with a two-stage IV32 predictor.

This experimental v5 transform keeps the existing embedded IV32 predictor as
stage 1. Stage-1 misses are represented by the existing lane/mod24/upper rule
residual bitmap. Those old misses are then tried against a runtime RS/FRLG egg
IV generator. Only cells still unmatched by the runtime model receive explicit
residual values. The default residual encoding stores only changed 5-bit IV stat
fields relative to runtime-normal.

The public native SPC3 tool does not decode this v5 stream kind. This script can
pack and verify the transform against a source v2 SPC3.
"""

from __future__ import annotations

import argparse
import json
import math
import struct
import sys
import time
import zlib
from dataclasses import asdict
from pathlib import Path
from typing import BinaryIO, Sequence

import numpy as np
import zstandard as zstd


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import spc3_iv_offset_classifier as clf  # noqa: E402
import spc3_rule_bitmap_repack as base  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_OUTPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3"
DEFAULT_REPORT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.verify.json"

SPC3_VERSION_TWO_STAGE_V4 = 4
SPC3_VERSION_TWO_STAGE_V5 = 5
SPC3_FLAG_TWO_STAGE_RUNTIME = 0x00000004
STREAM_KIND_TWO_STAGE_RUNTIME_LEVEL3 = 6
STREAM_KIND_TWO_STAGE_RUNTIME_STATDELTA_LEVEL3 = 7

TWO_STAGE_SUBSTREAM_COUNT = 5
SUBSTREAM_STAGE1_BITMAP = base.SUBSTREAM_BITMAP
SUBSTREAM_STAGE2_EXPLICIT_BITMAP = 4
SUBSTREAM_STAGE2_SHIFT_RECORDS = 5

MODEL_MAGIC = b"SPC3S2P1"
MODEL_VERSION = 1
MODEL_HEADER_STRUCT = struct.Struct("<8s3I")

RESIDUAL_BASE_RUNTIME_NORMAL = "runtime-normal"
RESIDUAL_ENCODING_XOR = "xor"
RESIDUAL_ENCODING_STAT_DELTA = "stat-delta"
STAT_SHIFTS = (0, 5, 10, 15, 20, 25)
STAT_MASK = 0x3FFFFFFF


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("pack", "verify", "pack-verify"), default="pack-verify")
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT, help="source/current SPC3 v2 file")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT, help="new experimental SPC3 v5 output")
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT, help="JSON report path")
    parser.add_argument("--predictor-json", type=Path, default=clf.DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--start-rng", default="0x2B0C94C1")
    parser.add_argument("--runtime-max-steps", type=int, default=4_000_000)
    parser.add_argument("--base-model", choices=tuple(clf.BASE_MODEL_POSITIONS), default="rsfrlg")
    parser.add_argument("--max-extra", type=int, default=2)
    parser.add_argument("--zstd-level", type=int, default=9)
    parser.add_argument(
        "--residual-encoding",
        choices=(RESIDUAL_ENCODING_STAT_DELTA, RESIDUAL_ENCODING_XOR),
        default=RESIDUAL_ENCODING_STAT_DELTA,
        help="How to store cells still unmatched after the runtime predictor.",
    )
    parser.add_argument(
        "--sample-lanes",
        type=int,
        default=None,
        help="Process only the first N source lanes. Intended for fast smoke tests.",
    )
    parser.add_argument("--progress-every", type=int, default=4096)
    return parser.parse_args()


def parse_int(raw: str) -> int:
    return int(raw.strip(), 0)


def codec_flags(codec_id: int, level: int = 0) -> int:
    return base.pack_codec_flags(codec_id, level)


def best_codec(raw: bytes, compressor: zstd.ZstdCompressor, zstd_level: int) -> tuple[int, bytes]:
    if not raw:
        return codec_flags(base.CODEC_ZSTD, zstd_level), b""
    compressed = compressor.compress(raw)
    if len(compressed) < len(raw):
        return codec_flags(base.CODEC_ZSTD, zstd_level), compressed
    return codec_flags(base.CODEC_NONE), raw


def pack_5bit_values(values: np.ndarray) -> bytes:
    vals = np.asarray(values, dtype=np.uint8) & 31
    count = len(vals)
    if count == 0:
        return b""
    out = np.zeros((count * 5 + 7) // 8, dtype=np.uint8)
    full_groups = count // 8
    if full_groups:
        groups = vals[: full_groups * 8].reshape(-1, 8).astype(np.uint16)
        out_groups = out[: full_groups * 5].reshape(-1, 5)
        out_groups[:, 0] = (groups[:, 0] | ((groups[:, 1] & 0x07) << 5)).astype(np.uint8)
        out_groups[:, 1] = ((groups[:, 1] >> 3) | (groups[:, 2] << 2) | ((groups[:, 3] & 0x01) << 7)).astype(
            np.uint8
        )
        out_groups[:, 2] = ((groups[:, 3] >> 1) | ((groups[:, 4] & 0x0F) << 4)).astype(np.uint8)
        out_groups[:, 3] = ((groups[:, 4] >> 4) | (groups[:, 5] << 1) | ((groups[:, 6] & 0x03) << 6)).astype(
            np.uint8
        )
        out_groups[:, 4] = ((groups[:, 6] >> 2) | (groups[:, 7] << 3)).astype(np.uint8)
    bit = full_groups * 40
    for value in vals[full_groups * 8 :].tolist():
        byte = bit >> 3
        offset = bit & 7
        out[byte] |= (value << offset) & 0xFF
        if offset > 3:
            out[byte + 1] |= value >> (8 - offset)
        bit += 5
    return out.tobytes()


def unpack_5bit_values(raw: bytes, count: int) -> np.ndarray:
    if count < 0:
        raise ValueError("count must be non-negative")
    expected_size = (count * 5 + 7) // 8
    if len(raw) != expected_size:
        raise RuntimeError(f"5-bit stream size mismatch: {len(raw):,} != {expected_size:,}")
    values = np.zeros(count, dtype=np.uint8)
    if count == 0:
        return values
    data = np.frombuffer(raw, dtype=np.uint8)
    full_groups = count // 8
    if full_groups:
        groups = data[: full_groups * 5].reshape(-1, 5).astype(np.uint16)
        out_groups = values[: full_groups * 8].reshape(-1, 8)
        out_groups[:, 0] = (groups[:, 0] & 31).astype(np.uint8)
        out_groups[:, 1] = (((groups[:, 0] >> 5) | ((groups[:, 1] & 0x03) << 3)) & 31).astype(np.uint8)
        out_groups[:, 2] = ((groups[:, 1] >> 2) & 31).astype(np.uint8)
        out_groups[:, 3] = (((groups[:, 1] >> 7) | ((groups[:, 2] & 0x0F) << 1)) & 31).astype(np.uint8)
        out_groups[:, 4] = (((groups[:, 2] >> 4) | ((groups[:, 3] & 0x01) << 4)) & 31).astype(np.uint8)
        out_groups[:, 5] = ((groups[:, 3] >> 1) & 31).astype(np.uint8)
        out_groups[:, 6] = (((groups[:, 3] >> 6) | ((groups[:, 4] & 0x07) << 2)) & 31).astype(np.uint8)
        out_groups[:, 7] = ((groups[:, 4] >> 3) & 31).astype(np.uint8)
    bit = full_groups * 40
    for index in range(full_groups * 8, count):
        byte = bit >> 3
        offset = bit & 7
        value = int(data[byte] >> offset)
        if offset > 3 and byte + 1 < len(data):
            value |= int(data[byte + 1]) << (8 - offset)
        values[index] = value & 31
        bit += 5
    return values


def iv32_stat_fields(values: np.ndarray) -> np.ndarray:
    source = np.asarray(values, dtype=np.uint32)
    return np.stack([((source >> shift) & 31) for shift in STAT_SHIFTS]).astype(np.uint8)


def replace_iv32_stat_fields(base_values: np.ndarray, fields: np.ndarray) -> np.ndarray:
    rebuilt = np.asarray(base_values, dtype=np.uint32) & np.uint32(~STAT_MASK & 0xFFFFFFFF)
    for stat_index, shift in enumerate(STAT_SHIFTS):
        rebuilt |= fields[stat_index].astype(np.uint32) << shift
    return rebuilt.astype(np.uint32)


def pack_stat_delta_values(actual: np.ndarray, baseline: np.ndarray) -> tuple[bytes, dict[str, int]]:
    actual_u32 = np.asarray(actual, dtype=np.uint32)
    baseline_u32 = np.asarray(baseline, dtype=np.uint32)
    if len(actual_u32) != len(baseline_u32):
        raise RuntimeError("stat-delta actual/baseline length mismatch")
    if len(actual_u32) == 0:
        return b"", {
            "stat_delta_changed_values": 0,
            "stat_delta_high_bit_mismatches": 0,
            **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
        }
    high_diff = int((((actual_u32 ^ baseline_u32) & np.uint32(~STAT_MASK & 0xFFFFFFFF)) != 0).sum())
    if high_diff:
        raise RuntimeError(f"stat-delta cannot encode {high_diff:,} high-bit IV32 differences")
    actual_fields = iv32_stat_fields(actual_u32)
    baseline_fields = iv32_stat_fields(baseline_u32)
    changed = actual_fields != baseline_fields
    mask_size = (len(actual_u32) + 7) // 8
    parts: list[bytes] = []
    change_counts_by_stat: list[int] = []
    for stat_index in range(6):
        stat_mask = np.packbits(changed[stat_index].astype(np.uint8), bitorder="little").tobytes()
        if len(stat_mask) != mask_size:
            raise RuntimeError("stat-delta mask size mismatch")
        parts.append(stat_mask)
    for stat_index in range(6):
        values = actual_fields[stat_index][changed[stat_index]]
        change_counts_by_stat.append(int(len(values)))
        parts.append(pack_5bit_values(values))

    changed_per_record = changed.sum(axis=0)
    stats = {
        "stat_delta_changed_values": int(changed.sum()),
        "stat_delta_high_bit_mismatches": high_diff,
    }
    for count in range(7):
        stats[f"stat_delta_records_changed_{count}"] = int((changed_per_record == count).sum())
    for stat_index, count in enumerate(change_counts_by_stat):
        stats[f"stat_delta_stat_{stat_index}_changed_values"] = count
    return b"".join(parts), stats


def unpack_stat_delta_values(raw: bytes, baseline: np.ndarray) -> np.ndarray:
    baseline_u32 = np.asarray(baseline, dtype=np.uint32)
    count = len(baseline_u32)
    if count == 0:
        if raw:
            raise RuntimeError(f"stat-delta has {len(raw):,} bytes for zero records")
        return baseline_u32.astype(np.uint32, copy=True)

    mask_size = (count + 7) // 8
    masks_size = mask_size * 6
    if len(raw) < masks_size:
        raise RuntimeError(f"stat-delta too short for masks: {len(raw):,} < {masks_size:,}")

    changed = np.empty((6, count), dtype=np.bool_)
    cursor = 0
    for stat_index in range(6):
        mask_raw = raw[cursor : cursor + mask_size]
        cursor += mask_size
        changed[stat_index] = np.unpackbits(np.frombuffer(mask_raw, dtype=np.uint8), bitorder="little")[:count].astype(
            np.bool_,
            copy=False,
        )

    fields = iv32_stat_fields(baseline_u32)
    for stat_index in range(6):
        change_count = int(changed[stat_index].sum())
        packed_size = (change_count * 5 + 7) // 8
        values = unpack_5bit_values(raw[cursor : cursor + packed_size], change_count)
        fields[stat_index][changed[stat_index]] = values
        cursor += packed_size
    if cursor != len(raw):
        raise RuntimeError(f"stat-delta has trailing bytes: {len(raw) - cursor:,}")
    return replace_iv32_stat_fields(baseline_u32, fields)


def read_substream_raw(handle: BinaryIO, entry: base.LaneEntry, sub: base.SubstreamEntry) -> bytes:
    return base.read_exact_at(handle, entry.stream_offset + sub.offset, sub.stream_size)


def decode_substream(handle: BinaryIO, entry: base.LaneEntry, sub: base.SubstreamEntry, label: str) -> bytes:
    return base.decode_payload(read_substream_raw(handle, entry, sub), sub, label=label)


def parse_substreams_count(
    handle: BinaryIO,
    entry: base.LaneEntry,
    *,
    substream_count: int,
    expected_stream_kinds: set[int],
    required_kinds: set[int],
) -> dict[int, base.SubstreamEntry]:
    if entry.stream_kind not in expected_stream_kinds:
        raise RuntimeError(f"lane 0x{entry.lane:04X} has unsupported stream kind {entry.stream_kind}")
    raw = base.read_exact_at(handle, entry.stream_offset, substream_count * base.TYPED_SUBSTREAM_ENTRY_SIZE)
    substreams: dict[int, base.SubstreamEntry] = {}
    expected_offset = substream_count * base.TYPED_SUBSTREAM_ENTRY_SIZE
    for index in range(substream_count):
        sub = base.SubstreamEntry(*base.SUBSTREAM_ENTRY_STRUCT.unpack_from(raw, index * base.TYPED_SUBSTREAM_ENTRY_SIZE))
        if sub.kind in substreams:
            raise RuntimeError(f"duplicate substream kind {sub.kind} on lane 0x{entry.lane:04X}")
        if sub.offset != expected_offset:
            raise RuntimeError(f"substream layout gap on lane 0x{entry.lane:04X}")
        if sub.offset + sub.stream_size > entry.stream_size:
            raise RuntimeError(f"substream extends past lane stream on lane 0x{entry.lane:04X}")
        substreams[sub.kind] = sub
        expected_offset += sub.stream_size
    if expected_offset != entry.stream_size:
        raise RuntimeError(f"lane 0x{entry.lane:04X} has trailing stream bytes")
    if set(substreams) != required_kinds:
        raise RuntimeError(f"lane 0x{entry.lane:04X} substream kind mismatch: {sorted(substreams)}")
    return substreams


def parse_source_substreams(handle: BinaryIO, entry: base.LaneEntry) -> dict[int, base.SubstreamEntry]:
    return base.parse_substreams(handle, entry, expected_stream_kinds={base.STREAM_KIND_TYPED_LEVEL3})


def parse_two_stage_substreams(handle: BinaryIO, entry: base.LaneEntry) -> dict[int, base.SubstreamEntry]:
    return parse_substreams_count(
        handle,
        entry,
        substream_count=TWO_STAGE_SUBSTREAM_COUNT,
        expected_stream_kinds={STREAM_KIND_TWO_STAGE_RUNTIME_LEVEL3, STREAM_KIND_TWO_STAGE_RUNTIME_STATDELTA_LEVEL3},
        required_kinds={
            base.SUBSTREAM_TEMPLATE,
            SUBSTREAM_STAGE1_BITMAP,
            base.SUBSTREAM_VALUES,
            SUBSTREAM_STAGE2_EXPLICIT_BITMAP,
            SUBSTREAM_STAGE2_SHIFT_RECORDS,
        },
    )


def selected_entries(entries: Sequence[base.LaneEntry], sample_lanes: int | None) -> list[base.LaneEntry]:
    if sample_lanes is None:
        return list(entries)
    if sample_lanes < 0:
        raise ValueError("--sample-lanes must be non-negative")
    return list(entries[:sample_lanes])


def build_candidate_model(
    *,
    start_rng: int,
    runtime_max_steps: int,
    base_model: str,
    max_extra: int,
    residual_encoding: str,
) -> tuple[np.ndarray, list[clf.CandidateClass], dict[str, object]]:
    classes = clf.build_candidate_classes(base_model, max_extra)
    max_draw = max(max(candidate.positions) for candidate in classes)
    r0_states, cover_step = clf.build_runtime_r0_states(start_rng, runtime_max_steps)
    words = clf.build_rng_words(r0_states, max_draw)
    candidate_table = clf.generate_candidate_table(words, classes)
    meta = {
        "kind": "runtime-rsfrlg-two-stage",
        "start_rng": f"0x{start_rng:08X}",
        "runtime_max_steps": runtime_max_steps,
        "covered_all_uppers_at_step": cover_step,
        "base_model": base_model,
        "base_positions": list(clf.BASE_MODEL_POSITIONS[base_model]),
        "max_extra": max_extra,
        "residual_base": RESIDUAL_BASE_RUNTIME_NORMAL,
        "residual_encoding": residual_encoding,
        "class_priority_note": "first matching class wins; class 0 is normal runtime RS/FRLG",
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
    }
    return candidate_table, classes, meta


def make_model_stream(meta: dict[str, object]) -> bytes:
    payload = json.dumps(meta, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return MODEL_HEADER_STRUCT.pack(
        MODEL_MAGIC,
        MODEL_VERSION,
        len(payload),
        zlib.crc32(payload) & 0xFFFFFFFF,
    ) + payload


def parse_model_stream(raw: bytes) -> dict[str, object]:
    if len(raw) < MODEL_HEADER_STRUCT.size:
        raise RuntimeError("two-stage model stream too short")
    magic, version, payload_size, payload_crc32 = MODEL_HEADER_STRUCT.unpack_from(raw)
    if magic != MODEL_MAGIC:
        raise RuntimeError(f"bad two-stage model magic: {magic!r}")
    if version != MODEL_VERSION:
        raise RuntimeError(f"unsupported two-stage model version: {version}")
    payload = raw[MODEL_HEADER_STRUCT.size :]
    if len(payload) != payload_size:
        raise RuntimeError(f"two-stage model payload size mismatch: {len(payload):,} != {payload_size:,}")
    actual_crc32 = zlib.crc32(payload) & 0xFFFFFFFF
    if actual_crc32 != payload_crc32:
        raise RuntimeError(f"two-stage model CRC mismatch: {actual_crc32:08X} != {payload_crc32:08X}")
    return json.loads(payload.decode("utf-8"))


def parse_global_streams(handle: BinaryIO, header: base.Header) -> tuple[bytes, dict[str, object], bytes, dict[str, int | str]]:
    predictor_stream = base.read_exact_at(handle, header.predictor_offset, header.predictor_size)
    global_offset = header.predictor_offset + header.predictor_size
    global_size = header.table_offset - global_offset
    if global_size <= 0:
        raise RuntimeError("missing v4 global streams")
    global_stream = base.read_exact_at(handle, global_offset, global_size)
    if len(global_stream) < MODEL_HEADER_STRUCT.size:
        raise RuntimeError("two-stage global stream too short for model header")
    _, _, model_payload_size, _ = MODEL_HEADER_STRUCT.unpack_from(global_stream)
    model_size = MODEL_HEADER_STRUCT.size + model_payload_size
    if model_size > len(global_stream):
        raise RuntimeError("two-stage model stream extends past global stream")
    model_meta = parse_model_stream(global_stream[:model_size])
    rule_raw, rule_meta = base.parse_rule_stream(global_stream[model_size:])
    return predictor_stream, model_meta, rule_raw, rule_meta


def pack_shift_records(indices: np.ndarray, class_ids: np.ndarray) -> bytes:
    if len(indices) == 0:
        return b""
    ordinals = indices.astype("<u2", copy=False).view(np.uint8).reshape(-1, 2)
    classes = class_ids.astype(np.uint8, copy=False)
    raw = np.empty(len(indices) * 3, dtype=np.uint8)
    raw[0::3] = ordinals[:, 0]
    raw[1::3] = ordinals[:, 1]
    raw[2::3] = classes
    return raw.tobytes()


def unpack_shift_records(raw: bytes) -> tuple[np.ndarray, np.ndarray]:
    if len(raw) % 3:
        raise RuntimeError(f"shift record stream has partial record: {len(raw):,} bytes")
    if not raw:
        return np.empty(0, dtype=np.uint16), np.empty(0, dtype=np.uint8)
    data = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3)
    indices = (data[:, 0].astype(np.uint16) | (data[:, 1].astype(np.uint16) << 8)).astype(np.uint16)
    classes = data[:, 2].astype(np.uint8)
    return indices, classes


def source_actual_for_exceptions(
    handle: BinaryIO,
    entry: base.LaneEntry,
    predictor: np.ndarray,
) -> tuple[bytes, np.ndarray, np.ndarray]:
    substreams = parse_source_substreams(handle, entry)
    bitmap = base.read_decoded_bitmap(handle, entry, substreams)
    values_sub = substreams[base.SUBSTREAM_VALUES]
    values_raw = decode_substream(handle, entry, values_sub, f"lane 0x{entry.lane:04X} source values")
    if len(values_raw) % 4:
        raise RuntimeError(f"lane 0x{entry.lane:04X} source values are not u32-aligned")
    xor_values = np.frombuffer(values_raw, dtype="<u4")
    bits = np.unpackbits(np.frombuffer(bitmap, dtype=np.uint8), bitorder="little")
    uppers = np.flatnonzero(bits).astype(np.uint32)
    if len(uppers) != entry.predictor_exceptions or len(xor_values) != len(uppers):
        raise RuntimeError(
            f"lane 0x{entry.lane:04X} exception count mismatch: "
            f"bits={len(uppers):,} values={len(xor_values):,} table={entry.predictor_exceptions:,}"
        )
    actual = predictor[uppers] ^ xor_values.astype(np.uint32, copy=False)
    return bitmap, uppers, actual


def build_two_stage_lane_stream(
    *,
    src: BinaryIO,
    old_entry: base.LaneEntry,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
    rule_rows: np.ndarray,
    compressor: zstd.ZstdCompressor,
    zstd_level: int,
    residual_encoding: str,
) -> tuple[bytes, dict[str, int]]:
    old_subs = parse_source_substreams(src, old_entry)
    template_sub = old_subs[base.SUBSTREAM_TEMPLATE]
    bitmap_sub = old_subs[base.SUBSTREAM_BITMAP]

    template_stream = read_substream_raw(src, old_entry, template_sub)
    actual_bitmap, uppers, actual = source_actual_for_exceptions(src, old_entry, predictor)

    rule_bitmap = rule_rows[base.lane_group(old_entry.lane)]
    stage1_residual_bitmap = np.bitwise_xor(
        np.frombuffer(actual_bitmap, dtype=np.uint8),
        rule_bitmap,
    ).tobytes()
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

    shift_raw = pack_shift_records(shift_indices, shift_classes)
    shift_flags, shift_stream = best_codec(shift_raw, compressor, zstd_level)

    if len(explicit_indices):
        explicit_uppers = uppers[explicit_indices]
        baseline_values = candidate_table[0, explicit_uppers]
        if residual_encoding == RESIDUAL_ENCODING_XOR:
            residual_values = actual[explicit_indices] ^ baseline_values
            value_raw = residual_values.astype("<u4", copy=False).tobytes()
            value_stats = {
                "stat_delta_changed_values": 0,
                "stat_delta_high_bit_mismatches": 0,
                **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
            }
        elif residual_encoding == RESIDUAL_ENCODING_STAT_DELTA:
            value_raw, value_stats = pack_stat_delta_values(actual[explicit_indices], baseline_values)
        else:
            raise RuntimeError(f"unsupported residual encoding: {residual_encoding}")
    else:
        value_raw = b""
        value_stats = {
            "stat_delta_changed_values": 0,
            "stat_delta_high_bit_mismatches": 0,
            **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
        }
    values_flags, values_stream = best_codec(value_raw, compressor, zstd_level)

    sub_table_size = TWO_STAGE_SUBSTREAM_COUNT * base.TYPED_SUBSTREAM_ENTRY_SIZE
    sub_template = base.SubstreamEntry(
        base.SUBSTREAM_TEMPLATE,
        template_sub.flags,
        sub_table_size,
        len(template_stream),
        template_sub.raw_size,
    )
    sub_stage1 = base.SubstreamEntry(
        SUBSTREAM_STAGE1_BITMAP,
        stage1_flags,
        sub_template.offset + sub_template.stream_size,
        len(stage1_stream),
        base.BITMAP_BYTES,
    )
    sub_stage2 = base.SubstreamEntry(
        SUBSTREAM_STAGE2_EXPLICIT_BITMAP,
        stage2_bitmap_flags,
        sub_stage1.offset + sub_stage1.stream_size,
        len(stage2_bitmap_stream),
        len(stage2_bitmap_raw),
    )
    sub_shift = base.SubstreamEntry(
        SUBSTREAM_STAGE2_SHIFT_RECORDS,
        shift_flags,
        sub_stage2.offset + sub_stage2.stream_size,
        len(shift_stream),
        len(shift_raw),
    )
    sub_values = base.SubstreamEntry(
        base.SUBSTREAM_VALUES,
        values_flags,
        sub_shift.offset + sub_shift.stream_size,
        len(values_stream),
        len(value_raw),
    )

    lane_stream = b"".join(
        [
            base.pack_substream_entry(sub_template),
            base.pack_substream_entry(sub_stage1),
            base.pack_substream_entry(sub_stage2),
            base.pack_substream_entry(sub_shift),
            base.pack_substream_entry(sub_values),
            template_stream,
            stage1_stream,
            stage2_bitmap_stream,
            shift_stream,
            values_stream,
        ]
    )

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
        "value_stream_bytes": len(values_stream),
        "value_raw_bytes": len(value_raw),
        "lane_stream_bytes": len(lane_stream),
        **value_stats,
    }
    if bitmap_sub.raw_size != base.BITMAP_BYTES:
        raise RuntimeError(f"lane 0x{old_entry.lane:04X} source bitmap raw size changed")
    return lane_stream, stats


def add_totals(totals: dict[str, int], stats: dict[str, int]) -> None:
    for key, value in stats.items():
        totals[key] = totals.get(key, 0) + int(value)


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
    residual_encoding: str,
    sample_lanes: int | None,
    progress_every: int,
) -> dict[str, object]:
    started = time.perf_counter()
    rule_raw, rule_info = base.compute_rule_table(input_path, progress_every)
    rule_stream = base.make_rule_stream(rule_raw, zstd_level, rule_info)
    rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
    candidate_table, classes, model_meta = build_candidate_model(
        start_rng=start_rng,
        runtime_max_steps=runtime_max_steps,
        base_model=base_model,
        max_extra=max_extra,
        residual_encoding=residual_encoding,
    )
    model_stream = make_model_stream(model_meta)
    compressor = zstd.ZstdCompressor(level=zstd_level)

    temp_path, out = base.open_temp_output(output_path)
    new_entries: list[base.LaneEntry] = []
    totals: dict[str, int] = {}
    lane_samples: list[dict[str, object]] = []

    try:
        with input_path.open("rb") as src:
            old_header = base.parse_header(src)
            if old_header.version != base.SPC3_VERSION_V2 or not (old_header.flags & base.SPC3_FLAG_PREDICTOR_EMBEDDED):
                raise RuntimeError("input must be SPC3 v2 typed level-3 with an embedded predictor")
            old_entries = selected_entries(base.parse_lane_entries(src, old_header), sample_lanes)
            predictor_stream = base.read_exact_at(src, old_header.predictor_offset, old_header.predictor_size)
            predictor, predictor_source = clf.load_predictor(src, old_header, predictor_json)

            global_stream = model_stream + rule_stream
            table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream) + len(global_stream)
            table_size = len(old_entries) * base.SPC3_TABLE_ENTRY_SIZE
            data_offset = table_offset + table_size

            out.write(b"\x00" * base.SPC3_HEADER_SIZE)
            out.write(predictor_stream)
            out.write(global_stream)
            out.write(b"\x00" * table_size)

            for index, old_entry in enumerate(old_entries, 1):
                lane_stream, stats = build_two_stage_lane_stream(
                    src=src,
                    old_entry=old_entry,
                    predictor=predictor,
                    candidate_table=candidate_table,
                    rule_rows=rule_rows,
                    compressor=compressor,
                    zstd_level=zstd_level,
                    residual_encoding=residual_encoding,
                )
                stream_offset = out.tell()
                out.write(lane_stream)

                new_entry = base.LaneEntry(
                    lane=old_entry.lane,
                    level=base.SPC3_LEVEL,
                    stream_kind=(
                        STREAM_KIND_TWO_STAGE_RUNTIME_STATDELTA_LEVEL3
                        if residual_encoding == RESIDUAL_ENCODING_STAT_DELTA
                        else STREAM_KIND_TWO_STAGE_RUNTIME_LEVEL3
                    ),
                    flags=0,
                    source_zip_size=old_entry.source_zip_size,
                    source_zip_crc32=old_entry.source_zip_crc32,
                    source_zip_fnv64=old_entry.source_zip_fnv64,
                    original_payload_crc32=old_entry.original_payload_crc32,
                    rebuilt_payload_crc32=old_entry.rebuilt_payload_crc32,
                    stream_offset=stream_offset,
                    stream_size=len(lane_stream),
                    uncompressed_model_size=(
                        base.RECORD_SIZE
                        + base.BITMAP_BYTES
                        + stats["stage2_bitmap_raw_bytes"]
                        + stats["shift_raw_bytes"]
                        + stats["value_raw_bytes"]
                    ),
                    predictor_matches=old_entry.predictor_matches,
                    predictor_exceptions=old_entry.predictor_exceptions,
                )
                new_entries.append(new_entry)
                add_totals(totals, stats)
                if len(lane_samples) < 32:
                    lane_samples.append({"lane": f"0x{old_entry.lane:04X}", **stats})

                if progress_every and (index % progress_every == 0 or index == len(old_entries)):
                    print(f"pack pass: {index}/{len(old_entries)} lanes", flush=True)

            data_size = out.tell() - data_offset
            new_header = base.Header(
                version=(
                    SPC3_VERSION_TWO_STAGE_V5
                    if residual_encoding == RESIDUAL_ENCODING_STAT_DELTA
                    else SPC3_VERSION_TWO_STAGE_V4
                ),
                level=base.SPC3_LEVEL,
                lane_count=len(new_entries),
                expected_records=base.EXPECTED_RECORDS,
                record_size=base.RECORD_SIZE,
                flags=(
                    base.SPC3_FLAG_PREDICTOR_EMBEDDED
                    | base.SPC3_FLAG_RULE_BITMAP
                    | SPC3_FLAG_TWO_STAGE_RUNTIME
                ),
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
            for entry in new_entries:
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

    elapsed = time.perf_counter() - started
    output_size = output_path.stat().st_size
    source_size = input_path.stat().st_size
    explicit_total = totals.get("old_exceptions", 0)
    report = {
        "schema": "spc3_two_stage_runtime_repack.v1",
        "mode": "pack",
        "input": str(input_path),
        "output": str(output_path),
        "elapsed_seconds": elapsed,
        "zstd_level": zstd_level,
        "residual_encoding": residual_encoding,
        "predictor_source": predictor_source,
        "model": model_meta,
        "rule": asdict(rule_info),
        "class_count": len(classes),
        "totals": totals,
        "stage2": {
            "old_predictor_exceptions": explicit_total,
            "runtime_matched": totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0),
            "runtime_normal": totals.get("stage2_normal", 0),
            "runtime_shift": totals.get("stage2_shift", 0),
            "still_explicit": totals.get("stage2_explicit", 0),
            "runtime_match_pct_of_old_exceptions": (
                100.0
                * (totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0))
                / explicit_total
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
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"packed {output_path} ({output_size:,} bytes) in {elapsed:.1f}s")
    return report


def reconstruct_two_stage_actual(
    *,
    handle: BinaryIO,
    entry: base.LaneEntry,
    uppers: np.ndarray,
    candidate_table: np.ndarray,
    residual_encoding: str,
) -> tuple[np.ndarray, dict[str, int]]:
    substreams = parse_two_stage_substreams(handle, entry)
    stage2_sub = substreams[SUBSTREAM_STAGE2_EXPLICIT_BITMAP]
    shift_sub = substreams[SUBSTREAM_STAGE2_SHIFT_RECORDS]
    values_sub = substreams[base.SUBSTREAM_VALUES]

    stage2_bitmap = decode_substream(handle, entry, stage2_sub, f"lane 0x{entry.lane:04X} stage2 bitmap")
    expected_stage2_bitmap_size = math.ceil(len(uppers) / 8)
    if len(stage2_bitmap) != expected_stage2_bitmap_size:
        raise RuntimeError(
            f"lane 0x{entry.lane:04X} stage2 bitmap raw size mismatch: "
            f"{len(stage2_bitmap):,} != {expected_stage2_bitmap_size:,}"
        )
    if len(uppers):
        explicit_mask = np.unpackbits(np.frombuffer(stage2_bitmap, dtype=np.uint8), bitorder="little")[: len(uppers)].astype(
            np.bool_,
            copy=False,
        )
    else:
        explicit_mask = np.empty(0, dtype=np.bool_)

    shift_raw = decode_substream(handle, entry, shift_sub, f"lane 0x{entry.lane:04X} shift records")
    shift_indices, shift_classes = unpack_shift_records(shift_raw)
    if len(shift_indices):
        if int(shift_indices.max()) >= len(uppers):
            raise RuntimeError(f"lane 0x{entry.lane:04X} shift ordinal outside old-miss range")
        if int(shift_classes.min()) <= 0 or int(shift_classes.max()) >= candidate_table.shape[0]:
            raise RuntimeError(f"lane 0x{entry.lane:04X} shift class outside candidate range")
        if len(np.unique(shift_indices)) != len(shift_indices):
            raise RuntimeError(f"lane 0x{entry.lane:04X} has duplicate shift ordinals")
        if bool(explicit_mask[shift_indices].any()):
            raise RuntimeError(f"lane 0x{entry.lane:04X} marks the same old miss as shift and explicit")

    value_raw = decode_substream(handle, entry, values_sub, f"lane 0x{entry.lane:04X} residual values")
    explicit_indices = np.flatnonzero(explicit_mask)

    reconstructed = candidate_table[0, uppers].astype(np.uint32, copy=True)
    if len(shift_indices):
        reconstructed[shift_indices] = candidate_table[shift_classes, uppers[shift_indices]]
    if len(explicit_indices):
        baseline_values = candidate_table[0, uppers[explicit_indices]]
        if residual_encoding == RESIDUAL_ENCODING_XOR:
            if len(value_raw) % 4:
                raise RuntimeError(f"lane 0x{entry.lane:04X} residual values are not u32-aligned")
            residual_values = np.frombuffer(value_raw, dtype="<u4")
            if len(residual_values) != len(explicit_indices):
                raise RuntimeError(
                    f"lane 0x{entry.lane:04X} residual value count mismatch: "
                    f"{len(residual_values):,} != {len(explicit_indices):,}"
                )
            reconstructed[explicit_indices] = baseline_values ^ residual_values
            value_stats = {
                "stat_delta_changed_values": 0,
                "stat_delta_high_bit_mismatches": 0,
                **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
            }
        elif residual_encoding == RESIDUAL_ENCODING_STAT_DELTA:
            decoded_values = unpack_stat_delta_values(value_raw, baseline_values)
            reconstructed[explicit_indices] = decoded_values
            _, value_stats = pack_stat_delta_values(decoded_values, baseline_values)
        else:
            raise RuntimeError(f"unsupported residual encoding: {residual_encoding}")
    else:
        if value_raw:
            raise RuntimeError(f"lane 0x{entry.lane:04X} has residual bytes but no explicit records")
        value_stats = {
            "stat_delta_changed_values": 0,
            "stat_delta_high_bit_mismatches": 0,
            **{f"stat_delta_records_changed_{count}": 0 for count in range(7)},
        }

    stats = {
        "stage2_normal": int(len(uppers) - len(shift_indices) - len(explicit_indices)),
        "stage2_shift": int(len(shift_indices)),
        "stage2_explicit": int(len(explicit_indices)),
        "stage2_bitmap_stream_bytes": int(stage2_sub.stream_size),
        "stage2_bitmap_raw_bytes": int(stage2_sub.raw_size),
        "shift_stream_bytes": int(shift_sub.stream_size),
        "shift_raw_bytes": int(shift_sub.raw_size),
        "value_stream_bytes": int(values_sub.stream_size),
        "value_raw_bytes": int(values_sub.raw_size),
        **value_stats,
    }
    return reconstructed, stats


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

    with original_path.open("rb") as old_handle, new_path.open("rb") as new_handle:
        old_header = base.parse_header(old_handle)
        new_header = base.parse_header(new_handle)
        if new_header.version not in {SPC3_VERSION_TWO_STAGE_V4, SPC3_VERSION_TWO_STAGE_V5}:
            raise RuntimeError(f"not a two-stage v4/v5 SPC3: version={new_header.version}")
        if not (new_header.flags & SPC3_FLAG_TWO_STAGE_RUNTIME):
            raise RuntimeError(f"missing two-stage flag: 0x{new_header.flags:08X}")

        old_entries = base.parse_lane_entries(old_handle, old_header)
        new_entries = base.parse_lane_entries(new_handle, new_header)
        old_by_lane = {entry.lane: entry for entry in old_entries}

        old_predictor_stream = base.read_exact_at(old_handle, old_header.predictor_offset, old_header.predictor_size)
        new_predictor_stream, model_meta, rule_raw, rule_meta = parse_global_streams(new_handle, new_header)
        if old_predictor_stream != new_predictor_stream:
            raise RuntimeError("embedded predictor stream differs from source SPC3")
        residual_encoding = str(model_meta.get("residual_encoding", RESIDUAL_ENCODING_XOR))
        if residual_encoding not in {RESIDUAL_ENCODING_XOR, RESIDUAL_ENCODING_STAT_DELTA}:
            raise RuntimeError(f"unsupported residual encoding in model: {residual_encoding}")
        if "residual_encoding" not in model_meta:
            model_meta = dict(model_meta)
            model_meta["residual_encoding"] = residual_encoding
        if new_header.version == SPC3_VERSION_TWO_STAGE_V5 and residual_encoding != RESIDUAL_ENCODING_STAT_DELTA:
            raise RuntimeError("v5 two-stage SPC3 must use stat-delta residual encoding")

        predictor, predictor_source = clf.load_predictor(old_handle, old_header, predictor_json)
        start_rng = parse_int(str(model_meta["start_rng"]))
        candidate_table, classes, rebuilt_model_meta = build_candidate_model(
            start_rng=start_rng,
            runtime_max_steps=int(model_meta["runtime_max_steps"]),
            base_model=str(model_meta["base_model"]),
            max_extra=int(model_meta["max_extra"]),
            residual_encoding=residual_encoding,
        )
        if rebuilt_model_meta != model_meta:
            raise RuntimeError("rebuilt two-stage model metadata differs from stored model")
        rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)

        for index, new_entry in enumerate(new_entries, 1):
            old_entry = old_by_lane.get(new_entry.lane)
            if old_entry is None:
                raise RuntimeError(f"new lane 0x{new_entry.lane:04X} not present in original")
            if new_entry.original_payload_crc32 != old_entry.original_payload_crc32:
                raise RuntimeError(f"lane 0x{new_entry.lane:04X} CRC metadata changed")
            if new_entry.predictor_exceptions != old_entry.predictor_exceptions:
                raise RuntimeError(f"lane 0x{new_entry.lane:04X} exception metadata changed")

            old_subs = parse_source_substreams(old_handle, old_entry)
            new_subs = parse_two_stage_substreams(new_handle, new_entry)

            old_template = read_substream_raw(old_handle, old_entry, old_subs[base.SUBSTREAM_TEMPLATE])
            new_template = read_substream_raw(new_handle, new_entry, new_subs[base.SUBSTREAM_TEMPLATE])
            if old_template != new_template:
                mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "template"})

            old_bitmap, uppers, old_actual = source_actual_for_exceptions(old_handle, old_entry, predictor)
            stage1_residual = decode_substream(
                new_handle,
                new_entry,
                new_subs[SUBSTREAM_STAGE1_BITMAP],
                f"lane 0x{new_entry.lane:04X} stage1 residual bitmap",
            )
            reconstructed_bitmap = np.bitwise_xor(
                np.frombuffer(stage1_residual, dtype=np.uint8),
                rule_rows[base.lane_group(new_entry.lane)],
            ).tobytes()
            if reconstructed_bitmap != old_bitmap:
                mismatches.append({"lane": f"0x{new_entry.lane:04X}", "kind": "stage1_bitmap"})

            new_actual, stage2_stats = reconstruct_two_stage_actual(
                handle=new_handle,
                entry=new_entry,
                uppers=uppers,
                candidate_table=candidate_table,
                residual_encoding=residual_encoding,
            )
            if len(new_actual) != len(old_actual) or bool(np.any(new_actual != old_actual)):
                mismatch_index = int(np.flatnonzero(new_actual != old_actual)[0]) if len(new_actual) == len(old_actual) else -1
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
                "template_stream_bytes": int(new_subs[base.SUBSTREAM_TEMPLATE].stream_size),
                "stage1_bitmap_stream_bytes": int(new_subs[SUBSTREAM_STAGE1_BITMAP].stream_size),
                "stage1_bitmap_raw_bytes": int(new_subs[SUBSTREAM_STAGE1_BITMAP].raw_size),
                "stage1_residual_bits": int(
                    np.unpackbits(np.frombuffer(stage1_residual, dtype=np.uint8), bitorder="little").sum()
                ),
                "lane_stream_bytes": int(new_entry.stream_size),
                **stage2_stats,
            }
            add_totals(totals, stats)

            if progress_every and (index % progress_every == 0 or index == len(new_entries)):
                print(f"verify pass: {index}/{len(new_entries)} lanes", flush=True)

    elapsed = time.perf_counter() - started
    explicit_total = totals.get("old_exceptions", 0)
    output_size = new_path.stat().st_size
    source_size = original_path.stat().st_size
    report = {
        "schema": "spc3_two_stage_runtime_verify.v1",
        "mode": "verify",
        "new_spc3": str(new_path),
        "original_spc3": str(original_path),
        "elapsed_seconds": elapsed,
        "status": "ok" if not mismatches else "failed",
        "mismatch_count": len(mismatches),
        "mismatch_samples": mismatches[:20],
        "predictor_source": predictor_source,
        "residual_encoding": residual_encoding,
        "model": model_meta,
        "rebuilt_model": rebuilt_model_meta,
        "rule": rule_meta,
        "totals": totals,
        "stage2": {
            "old_predictor_exceptions": explicit_total,
            "runtime_matched": totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0),
            "runtime_normal": totals.get("stage2_normal", 0),
            "runtime_shift": totals.get("stage2_shift", 0),
            "still_explicit": totals.get("stage2_explicit", 0),
            "runtime_match_pct_of_old_exceptions": (
                100.0
                * (totals.get("stage2_normal", 0) + totals.get("stage2_shift", 0))
                / explicit_total
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
        raise RuntimeError(f"verification failed with {len(mismatches)} mismatches")
    print(f"verify ok: {new_path} ({output_size:,} bytes) in {elapsed:.1f}s")
    return report


def main() -> int:
    args = parse_args()
    if not 1 <= args.zstd_level <= 22:
        raise SystemExit("--zstd-level must be in 1..22")
    if args.max_extra < 0:
        raise SystemExit("--max-extra must be non-negative")
    start_rng = parse_int(args.start_rng)
    args.report.parent.mkdir(parents=True, exist_ok=True)

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
            residual_encoding=args.residual_encoding,
            sample_lanes=args.sample_lanes,
            progress_every=args.progress_every,
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
