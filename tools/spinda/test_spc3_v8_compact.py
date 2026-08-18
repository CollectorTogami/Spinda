#!/usr/bin/env python3
"""Focused regression tests for SPC3 v8 compact streams."""

from __future__ import annotations

import tempfile
from pathlib import Path

import numpy as np

import spc3_rule_bitmap_repack as base
import spc3_two_stage_runtime_repack as two_stage
import spc3_v8_compact_repack as v8


def assert_runtime_error_contains(func, text: str) -> None:
    try:
        func()
    except RuntimeError as exc:
        assert text in str(exc), str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def iv32(values: tuple[int, int, int, int, int, int]) -> np.uint32:
    out = 0
    for value, shift in zip(values, two_stage.STAT_SHIFTS, strict=True):
        out |= (value & 31) << shift
    return np.uint32(out)


def test_stage_transforms_roundtrip() -> None:
    lane_count = 10
    row_bytes = v8.V8_STAGE_BAND_RAW_BYTES_PER_LANE
    matrix = np.zeros((lane_count, row_bytes), dtype=np.uint8)
    matrix[0, 0] = 0b00000001
    matrix[3, 7] = 0b01000000
    matrix[:, 12] = 0b11110000
    matrix[9, 31] = 0b10000000
    raw = matrix.tobytes()

    for transform in (
        v8.V8_STAGE_TRANSFORM_RAW,
        v8.V8_STAGE_TRANSFORM_TRANSPOSE,
        v8.V8_STAGE_TRANSFORM_INDEX_LIST,
    ):
        encoded = v8.encode_stage_transform(raw, lane_count, transform)
        decoded = v8.decode_stage_transform(encoded, lane_count, transform)
        assert decoded == raw


def test_residual_selector_roundtrip() -> None:
    uppers = np.array([0x0001, 0x0102, 0x01F0, 0xFFFF], dtype=np.uint32)
    actual = np.array(
        [
            iv32((1, 2, 3, 4, 5, 6)),
            iv32((7, 8, 9, 10, 11, 12)),
            iv32((13, 14, 15, 16, 17, 18)),
            iv32((19, 20, 21, 22, 23, 24)),
        ],
        dtype=np.uint32,
    )
    runtime = np.array(
        [
            iv32((1, 2, 3, 4, 5, 6)),
            iv32((0, 0, 0, 0, 0, 0)),
            iv32((13, 14, 15, 16, 17, 18)),
            iv32((31, 31, 31, 31, 31, 31)),
        ],
        dtype=np.uint32,
    )
    old = np.array(
        [
            iv32((31, 31, 31, 31, 31, 31)),
            iv32((7, 8, 9, 0, 0, 0)),
            iv32((0, 0, 0, 0, 0, 0)),
            iv32((19, 20, 21, 0, 0, 0)),
        ],
        dtype=np.uint32,
    )

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        pack = v8.ResidualChoiceBuckets(root, "pack")
        pack.write(uppers, actual, runtime, old)
        pack.close_all()

        section, totals = v8.build_residual_section(
            pack,
            layout=v8.VALUE_LAYOUT_SELECTED_MASK_GROUP,
            zstd_level=3,
        )
        assert totals["residual_old_predictor_selected"] == 2

        context = v8.ResidualChoiceBuckets(root, "context")
        context.write_context_only(uppers, runtime, old)
        context.close_all()
        _entries, decoded = v8.materialize_decoded_residuals(
            residual_stream=section,
            layout=v8.VALUE_LAYOUT_SELECTED_MASK_GROUP,
            context_buckets=context,
            output_root=root,
        )
        rebuilt = []
        for band in range(v8.v6.V6_BAND_COUNT):
            if int(decoded.counts[band]):
                rebuilt.extend(np.fromfile(decoded.actual_path(band), dtype="<u4").tolist())
        assert rebuilt == actual.tolist()


def test_template_section_roundtrip() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "templates.bin"
        raw = bytes(range(80)) + bytes(reversed(range(80)))
        path.write_bytes(raw)
        section, totals = v8.build_template_section(path, lane_count=2, zstd_level=3)
        decoded = v8.decode_template_section(section, lane_count=2)
        assert decoded == raw
        assert totals["template_raw_bytes"] == 160


def test_parse_v8_global_rejects_truncated_model() -> None:
    model_header = two_stage.MODEL_HEADER_STRUCT.pack(
        two_stage.MODEL_MAGIC,
        two_stage.MODEL_VERSION,
        999,
        0,
    )
    global_stream = model_header + b"\x00" * v8.V8_GLOBAL_HEADER_STRUCT.size
    header = base.Header(
        version=v8.SPC3_VERSION_TWO_STAGE_V8,
        level=base.SPC3_LEVEL,
        lane_count=0,
        expected_records=base.EXPECTED_RECORDS,
        record_size=base.RECORD_SIZE,
        flags=base.SPC3_FLAG_PREDICTOR_EMBEDDED | base.SPC3_FLAG_RULE_BITMAP | two_stage.SPC3_FLAG_TWO_STAGE_RUNTIME,
        header_size=base.SPC3_HEADER_SIZE,
        predictor_offset=base.SPC3_HEADER_SIZE,
        predictor_size=0,
        table_offset=base.SPC3_HEADER_SIZE + len(global_stream),
        table_entry_size=base.SPC3_TABLE_ENTRY_SIZE,
        data_offset=base.SPC3_HEADER_SIZE + len(global_stream),
        data_size=0,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "truncated.spc3"
        path.write_bytes(base.pack_header(header) + global_stream)
        with path.open("rb") as handle:
            assert_runtime_error_contains(
                lambda: v8.parse_v8_global_streams(handle, header),
                "v8 model stream extends past global stream",
            )


def test_parse_v8_global_rejects_oversized_sections() -> None:
    model_stream = two_stage.make_model_stream({"kind": "test"})
    global_header = v8.V8_GLOBAL_HEADER_STRUCT.pack(
        v8.V8_GLOBAL_MAGIC,
        v8.V8_GLOBAL_VERSION,
        v8.VALUE_LAYOUT_IDS[v8.VALUE_LAYOUT_SELECTED_MASK_GROUP],
        v8.STAGE_LAYOUT_IDS[v8.STAGE_LAYOUT_ADAPTIVE_BITMAPS],
        1,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    global_stream = model_stream + global_header
    header = base.Header(
        version=v8.SPC3_VERSION_TWO_STAGE_V8,
        level=base.SPC3_LEVEL,
        lane_count=0,
        expected_records=base.EXPECTED_RECORDS,
        record_size=base.RECORD_SIZE,
        flags=base.SPC3_FLAG_PREDICTOR_EMBEDDED | base.SPC3_FLAG_RULE_BITMAP | two_stage.SPC3_FLAG_TWO_STAGE_RUNTIME,
        header_size=base.SPC3_HEADER_SIZE,
        predictor_offset=base.SPC3_HEADER_SIZE,
        predictor_size=0,
        table_offset=base.SPC3_HEADER_SIZE + len(global_stream),
        table_entry_size=base.SPC3_TABLE_ENTRY_SIZE,
        data_offset=base.SPC3_HEADER_SIZE + len(global_stream),
        data_size=0,
    )
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "oversized.spc3"
        path.write_bytes(base.pack_header(header) + global_stream)
        with path.open("rb") as handle:
            assert_runtime_error_contains(
                lambda: v8.parse_v8_global_streams(handle, header),
                "v8 global section sizes exceed global stream",
            )


def main() -> int:
    tests = [
        test_stage_transforms_roundtrip,
        test_residual_selector_roundtrip,
        test_template_section_roundtrip,
        test_parse_v8_global_rejects_truncated_model,
        test_parse_v8_global_rejects_oversized_sections,
    ]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
