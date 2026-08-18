#!/usr/bin/env python3
"""Materialize an SPC3 v7 global-stage file as typed level-3 SPC3 v2.

The v7 Python packer/verifier proves the compressed model directly, but the
native unpacker currently understands the older typed level-3 stream shape.
This bridge decodes v7 stage/residual streams back into ordinary per-lane
template/bitmap/XOR streams so the native tool can physically extract lane ZIPs.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

import numpy as np


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import spc3_iv_offset_classifier as clf  # noqa: E402
import spc3_rule_bitmap_repack as base  # noqa: E402
import spc3_two_stage_runtime_repack as two_stage  # noqa: E402
import spc3_v6_upper_repack as v6  # noqa: E402
import spc3_v7_global_stage_repack as v7  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3"
)
DEFAULT_OUTPUT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.materialized-v2.spc3"
)
DEFAULT_REPORT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.materialized-v2.report.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--predictor-json", type=Path, default=clf.DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--sample-lanes", type=int, default=None)
    parser.add_argument("--lane-from", type=parse_lane_arg, default=None)
    parser.add_argument("--lane-to", type=parse_lane_arg, default=None)
    parser.add_argument("--progress-every", type=int, default=4096)
    parser.add_argument("--scratch-dir", type=Path, default=None)
    parser.add_argument("--keep-scratch", action="store_true")
    return parser.parse_args()


def parse_lane_arg(value: str) -> int:
    try:
        lane = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid lane value: {value!r}") from exc
    if lane < 0 or lane > 0xFFFF:
        raise argparse.ArgumentTypeError(f"lane out of range 0..0xFFFF: {value!r}")
    return lane


def selected_entries(
    entries: list[base.LaneEntry],
    sample_lanes: int | None,
    lane_from: int | None,
    lane_to: int | None,
) -> list[base.LaneEntry]:
    if sample_lanes is not None and (lane_from is not None or lane_to is not None):
        raise ValueError("--sample-lanes cannot be combined with --lane-from/--lane-to")
    if sample_lanes is None:
        if lane_from is None and lane_to is None:
            return entries
        start = 0 if lane_from is None else lane_from
        end = 0xFFFF if lane_to is None else lane_to
        if end < start:
            raise ValueError("--lane-to must be greater than or equal to --lane-from")
        return [entry for entry in entries if start <= entry.lane <= end]
    if sample_lanes < 0:
        raise ValueError("--sample-lanes must be non-negative")
    return entries[:sample_lanes]


def make_none_substream(kind: int, offset: int, raw_size: int) -> base.SubstreamEntry:
    return base.SubstreamEntry(
        kind=kind,
        flags=base.pack_codec_flags(base.CODEC_NONE),
        offset=offset,
        stream_size=raw_size,
        raw_size=raw_size,
    )


def build_typed_v2_lane_stream(template: bytes, bitmap: bytes, values: bytes) -> bytes:
    if len(template) != base.RECORD_SIZE:
        raise RuntimeError(f"decoded template has {len(template):,} bytes")
    if len(bitmap) != base.BITMAP_BYTES:
        raise RuntimeError(f"decoded bitmap has {len(bitmap):,} bytes")
    if len(values) % 4:
        raise RuntimeError("XOR value stream has partial u32")

    table_size = base.TYPED_SUBSTREAM_COUNT * base.TYPED_SUBSTREAM_ENTRY_SIZE
    template_sub = make_none_substream(base.SUBSTREAM_TEMPLATE, table_size, len(template))
    bitmap_sub = make_none_substream(base.SUBSTREAM_BITMAP, template_sub.offset + template_sub.stream_size, len(bitmap))
    values_sub = make_none_substream(base.SUBSTREAM_VALUES, bitmap_sub.offset + bitmap_sub.stream_size, len(values))
    return b"".join(
        [
            base.pack_substream_entry(template_sub),
            base.pack_substream_entry(bitmap_sub),
            base.pack_substream_entry(values_sub),
            template,
            bitmap,
            values,
        ]
    )


def load_v7_context(input_path: Path, temp_root: Path, predictor_json: Path) -> dict[str, object]:
    with input_path.open("rb") as handle:
        header = base.parse_header(handle)
        if header.version != v7.SPC3_VERSION_TWO_STAGE_V7:
            raise RuntimeError(f"input is not SPC3 v7: version={header.version}")
        predictor, predictor_source = clf.load_predictor(handle, header, predictor_json)
        (
            predictor_stream,
            model_meta,
            rule_raw,
            rule_meta,
            stage_stream,
            stage_layout,
            residual_stream,
            value_layout,
        ) = v7.parse_v7_global_streams(handle, header)

    candidate_table, rebuilt_model = v7.rebuild_model_from_meta(model_meta)
    rule_rows = np.frombuffer(rule_raw, dtype=np.uint8).reshape(base.RULE_GROUP_COUNT, base.BITMAP_BYTES)
    shift_by_lane, stage_lane_count = v7.materialize_stage_section(stage_stream, temp_root)
    if stage_lane_count != header.lane_count:
        raise RuntimeError("v7 stage lane count differs from SPC3 header")
    return {
        "header": header,
        "predictor": predictor,
        "predictor_source": predictor_source,
        "predictor_stream": predictor_stream,
        "model_meta": model_meta,
        "rebuilt_model": rebuilt_model,
        "rule_meta": rule_meta,
        "rule_rows": rule_rows,
        "shift_by_lane": shift_by_lane,
        "residual_stream": residual_stream,
        "value_layout": value_layout,
        "stage_layout": stage_layout,
    }


def build_residual_actual_buckets(
    *,
    input_path: Path,
    entries: list[base.LaneEntry],
    context: dict[str, object],
    temp_root: Path,
    progress_every: int,
) -> tuple[v6.BandBuckets, list[dict[str, int]]]:
    baseline_buckets = v6.BandBuckets(temp_root, "materialize_baseline")
    stage_reader = v7.StageBandReader(temp_root, "decoded_stage", int(context["header"].lane_count))
    try:
        with input_path.open("rb") as handle:
            _header = base.parse_header(handle)
            for index, entry in enumerate(entries, 1):
                stage1_residual, explicit_full = stage_reader.read_lane()
                _bitmap, uppers, explicit_indices, _shift_indices, _shift_classes, _stats = v7.stage_from_raw(
                    lane=entry.lane,
                    stage1_residual=stage1_residual,
                    explicit_full=explicit_full,
                    shift_by_lane=context["shift_by_lane"],  # type: ignore[arg-type]
                    rule_rows=context["rule_rows"],  # type: ignore[arg-type]
                    candidate_table=context["candidate_table"],  # type: ignore[arg-type]
                )
                explicit_uppers = uppers[explicit_indices]
                baseline = (
                    context["candidate_table"][0, explicit_uppers]  # type: ignore[index]
                    if len(explicit_uppers)
                    else np.empty(0, dtype=np.uint32)
                )
                baseline_buckets.write_baseline_only(explicit_uppers, baseline)
                if progress_every and (index % progress_every == 0 or index == len(entries)):
                    print(f"v7 materialize baseline: {index}/{len(entries)} lanes", flush=True)
        if len(entries) == int(context["header"].lane_count):
            stage_reader.ensure_consumed()
    finally:
        stage_reader.close()
        baseline_buckets.close_all()

    residual_entries, actual_buckets = v6.materialize_decoded_residuals(
        residual_stream=context["residual_stream"],  # type: ignore[arg-type]
        layout=str(context["value_layout"]),
        baseline_buckets=baseline_buckets,
        output_root=temp_root,
    )
    return actual_buckets, residual_entries


def materialize(
    *,
    input_path: Path,
    output_path: Path,
    report_path: Path,
    predictor_json: Path,
    sample_lanes: int | None,
    lane_from: int | None,
    lane_to: int | None,
    progress_every: int,
    scratch_dir: Path | None,
    keep_scratch: bool,
) -> dict[str, object]:
    started = time.perf_counter()
    scratch_context: tempfile.TemporaryDirectory[str] | None = None
    if scratch_dir is None:
        scratch_context = tempfile.TemporaryDirectory(prefix="spc3-v7-materialize-v2-")
        temp_root = Path(scratch_context.name)
    else:
        temp_root = scratch_dir
        temp_root.mkdir(parents=True, exist_ok=True)

    temp_path, out = base.open_temp_output(output_path)
    totals = {
        "lane_stream_bytes": 0,
        "template_bytes": 0,
        "bitmap_bytes": 0,
        "value_bytes": 0,
        "old_miss_cells": 0,
        "stage2_explicit_cells": 0,
        "payload_crc_mismatches": 0,
    }
    try:
        context = load_v7_context(input_path, temp_root, predictor_json)
        context["candidate_table"] = v7.rebuild_model_from_meta(context["model_meta"])[0]  # type: ignore[arg-type]

        with input_path.open("rb") as handle:
            input_header = base.parse_header(handle)
            all_entries = base.parse_lane_entries(handle, input_header)
            entries = selected_entries(all_entries, sample_lanes, lane_from, lane_to)
        selected_lanes = {entry.lane for entry in entries}

        actual_buckets, residual_entries = build_residual_actual_buckets(
            input_path=input_path,
            entries=all_entries,
            context=context,
            temp_root=temp_root,
            progress_every=progress_every,
        )
        actual_handles = {
            band: actual_buckets.actual_path(band).open("rb")
            for band in range(v6.V6_BAND_COUNT)
            if int(actual_buckets.counts[band])
        }

        predictor_stream = context["predictor_stream"]  # type: ignore[assignment]
        table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream)  # type: ignore[arg-type]
        table_size = len(entries) * base.SPC3_TABLE_ENTRY_SIZE
        data_offset = table_offset + table_size
        out.write(b"\x00" * base.SPC3_HEADER_SIZE)
        out.write(predictor_stream)  # type: ignore[arg-type]
        out.write(b"\x00" * table_size)

        new_entries: list[base.LaneEntry] = []
        stage_reader = v7.StageBandReader(temp_root, "decoded_stage", int(context["header"].lane_count))
        try:
            with input_path.open("rb") as handle:
                _header = base.parse_header(handle)
                selected_index = 0
                for source_index, entry in enumerate(all_entries, 1):
                    stage1_residual, explicit_full = stage_reader.read_lane()
                    bitmap, uppers, explicit_indices, shift_indices, shift_classes, _stats = v7.stage_from_raw(
                        lane=entry.lane,
                        stage1_residual=stage1_residual,
                        explicit_full=explicit_full,
                        shift_by_lane=context["shift_by_lane"],  # type: ignore[arg-type]
                        rule_rows=context["rule_rows"],  # type: ignore[arg-type]
                        candidate_table=context["candidate_table"],  # type: ignore[arg-type]
                    )
                    explicit_actual = None
                    if len(explicit_indices):
                        explicit_uppers = uppers[explicit_indices]
                        explicit_actual = v6.read_explicit_actuals_from_bands(actual_handles, explicit_uppers)
                    if entry.lane not in selected_lanes:
                        continue
                    template_substreams = v7.parse_v7_substreams(handle, entry)
                    template = two_stage.decode_substream(
                        handle,
                        entry,
                        template_substreams[base.SUBSTREAM_TEMPLATE],
                        f"lane 0x{entry.lane:04X} template",
                    )

                    actual = context["candidate_table"][0, uppers].astype(np.uint32, copy=True)  # type: ignore[index]
                    if len(shift_indices):
                        actual[shift_indices] = context["candidate_table"][shift_classes, uppers[shift_indices]]  # type: ignore[index]
                    if len(explicit_indices):
                        if explicit_actual is None:
                            raise RuntimeError("missing explicit actuals for selected lane")
                        actual[explicit_indices] = explicit_actual
                    xor_values = np.bitwise_xor(
                        actual,
                        context["predictor"][uppers].astype(np.uint32, copy=False),  # type: ignore[index]
                    ).astype("<u4", copy=False)
                    values = xor_values.tobytes()
                    lane_stream = build_typed_v2_lane_stream(template, bitmap, values)
                    stream_offset = out.tell()
                    out.write(lane_stream)

                    new_entries.append(
                        base.LaneEntry(
                            lane=entry.lane,
                            level=base.SPC3_LEVEL,
                            stream_kind=base.STREAM_KIND_TYPED_LEVEL3,
                            flags=0,
                            source_zip_size=entry.source_zip_size,
                            source_zip_crc32=entry.source_zip_crc32,
                            source_zip_fnv64=entry.source_zip_fnv64,
                            original_payload_crc32=entry.original_payload_crc32,
                            rebuilt_payload_crc32=entry.rebuilt_payload_crc32,
                            stream_offset=stream_offset,
                            stream_size=len(lane_stream),
                            uncompressed_model_size=len(template) + len(bitmap) + len(values),
                            predictor_matches=entry.predictor_matches,
                            predictor_exceptions=entry.predictor_exceptions,
                        )
                    )
                    totals["lane_stream_bytes"] += len(lane_stream)
                    totals["template_bytes"] += len(template)
                    totals["bitmap_bytes"] += len(bitmap)
                    totals["value_bytes"] += len(values)
                    totals["old_miss_cells"] += int(len(uppers))
                    totals["stage2_explicit_cells"] += int(len(explicit_indices))
                    selected_index += 1
                    if progress_every and (selected_index % progress_every == 0 or selected_index == len(entries)):
                        print(
                            f"v7 materialize write: {selected_index}/{len(entries)} lanes "
                            f"(source {source_index}/{len(all_entries)})",
                            flush=True,
                        )
            stage_reader.ensure_consumed()
        finally:
            stage_reader.close()
            for handle in actual_handles.values():
                handle.close()

        data_size = out.tell() - data_offset
        output_header = base.Header(
            version=base.SPC3_VERSION_V2,
            level=base.SPC3_LEVEL,
            lane_count=len(new_entries),
            expected_records=base.EXPECTED_RECORDS,
            record_size=base.RECORD_SIZE,
            flags=base.SPC3_FLAG_PREDICTOR_EMBEDDED,
            header_size=base.SPC3_HEADER_SIZE,
            predictor_offset=base.SPC3_HEADER_SIZE,
            predictor_size=len(predictor_stream),  # type: ignore[arg-type]
            table_offset=table_offset,
            table_entry_size=base.SPC3_TABLE_ENTRY_SIZE,
            data_offset=data_offset,
            data_size=data_size,
        )
        out.seek(0)
        out.write(base.pack_header(output_header))
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
    finally:
        if scratch_context is not None and not keep_scratch:
            scratch_context.cleanup()

    elapsed = time.perf_counter() - started
    report = {
        "schema": "spc3_v7_materialized_typed_v2.v1",
        "input": str(input_path),
        "output": str(output_path),
        "sample_lanes": sample_lanes,
        "lane_from": lane_from,
        "lane_to": lane_to,
        "elapsed_seconds": elapsed,
        "output_size_bytes": output_path.stat().st_size,
        "totals": totals,
        "input_model": context["model_meta"],
        "rule": context["rule_meta"],
        "stage_layout": context["stage_layout"],
        "value_layout": context["value_layout"],
        "predictor_source": context["predictor_source"],
        "residual_entries": residual_entries,
        "output_header": asdict(output_header),
        "scratch_dir": str(temp_root) if keep_scratch else None,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"materialized {output_path} ({output_path.stat().st_size:,} bytes) in {elapsed:.1f}s", flush=True)
    return report


def main() -> int:
    args = parse_args()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    materialize(
        input_path=args.input,
        output_path=args.output,
        report_path=args.report,
        predictor_json=args.predictor_json,
        sample_lanes=args.sample_lanes,
        lane_from=args.lane_from,
        lane_to=args.lane_to,
        progress_every=args.progress_every,
        scratch_dir=args.scratch_dir,
        keep_scratch=args.keep_scratch,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
