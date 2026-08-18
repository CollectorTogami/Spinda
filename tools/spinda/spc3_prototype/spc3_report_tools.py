#!/usr/bin/env python3
"""Small report helpers for SPC3 JSON artifacts.

This script is intentionally dependency-free. It reads existing JSON reports;
it does not decode SPC3 lane streams or touch source ZIPs.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Any


REPORT_FACT_FIELDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("schema", ("schema",)),
    ("mode", ("mode",)),
    ("ok", ("ok",)),
    ("version", ("version",)),
    ("level", ("level",)),
    ("lane count", ("lane_count",)),
    ("typed level 3", ("typed_level3",)),
    ("codec", ("codec",)),
    ("codec profile", ("codec_profile",)),
    ("input", ("input",)),
    ("output", ("output",)),
    ("unpack dir", ("unpack_dir",)),
    ("consolidate root", ("consolidate_root",)),
    ("copy mode", ("copy_mode",)),
    ("input spc3 count", ("input_spc3_count",)),
    ("spc3 size bytes", ("spc3_size_bytes",)),
    ("source zip bytes", ("source_zip_bytes",)),
    ("raw payload bytes", ("raw_payload_bytes",)),
    ("hotloop backend", ("config", "hotloop_backend")),
    ("predictor loaded", ("config", "predictor_loaded")),
    ("limit zips", ("config", "limit_zips")),
    ("zips found", ("config", "zips_found_for_run")),
    ("audit failed", ("totals", "audit_failed")),
    ("records processed", ("totals", "records_processed")),
    ("lane error count", ("totals", "lane_error_count")),
    ("duplicate entries", ("totals", "duplicate_entries")),
    ("missing entries", ("totals", "missing_entries")),
    ("checksum failures", ("totals", "checksum_failures")),
    ("content pid mismatches", ("totals", "content_pid_mismatches")),
    ("template mismatches", ("totals", "template_mismatches")),
    ("predictor exceptions", ("totals", "predictor_exceptions")),
    ("predictor roundtrip mismatches", ("totals", "predictor_roundtrip_mismatches")),
    ("rebuild mismatches", ("totals", "rebuild_mismatches")),
    ("audit zip size bytes", ("totals", "zip_size_bytes")),
    ("audit raw payload bytes", ("totals", "raw_payload_bytes")),
    ("inflate ms", ("timings_ms", "inflate_ms")),
    ("decrypt model ms", ("timings_ms", "decrypt_model_ms")),
    ("rebuild ms", ("timings_ms", "rebuild_ms")),
    ("entropy probe ms", ("timings_ms", "entropy_probe_ms")),
    ("audit total ms", ("timings_ms", "total_ms")),
    ("roundtrip mismatches", ("roundtrip_mismatches",)),
    ("internal crc mismatches", ("internal_crc_mismatches",)),
    ("source compare enabled", ("source_compare_enabled",)),
    ("source compare mismatches", ("source_compare_mismatches",)),
    ("crc mismatches", ("crc_mismatches",)),
    ("build ms", ("build_ms",)),
    ("total ms", ("total_ms",)),
    ("gpu status", ("gpu_rebuild", "status")),
    ("gpu device", ("gpu_rebuild", "device_name")),
    ("gpu requested", ("gpu_rebuild", "requested")),
    ("gpu used", ("gpu_rebuild", "used")),
    ("gpu fallback reason", ("gpu_rebuild", "fallback_reason")),
    ("gpu download mode", ("gpu_rebuild", "download_mode")),
    ("gpu runtime cache hit", ("gpu_rebuild", "runtime_cache_hit")),
    ("gpu runtime failure cached", ("gpu_rebuild", "runtime_failure_cached")),
    ("gpu runtime initializations", ("gpu_rebuild", "runtime_initializations")),
    ("gpu output bytes", ("gpu_rebuild", "output_bytes")),
    ("gpu value count", ("gpu_rebuild", "value_count")),
    ("gpu mismatched lanes", ("gpu_rebuild", "mismatched_lanes")),
    ("gpu mismatched bytes", ("gpu_rebuild", "mismatched_bytes")),
    ("gpu compile ms", ("gpu_rebuild", "compile_ms")),
    ("gpu upload ms", ("gpu_rebuild", "upload_ms")),
    ("gpu kernel ms", ("gpu_rebuild", "kernel_ms")),
    ("gpu download ms", ("gpu_rebuild", "download_ms")),
    ("gpu host crc ms", ("gpu_rebuild", "host_crc_ms")),
    ("gpu total ms", ("gpu_rebuild", "total_ms")),
    ("cpu profile used", ("cpu_decode_profile", "used")),
    ("cpu crc backend", ("cpu_decode_profile", "crc_backend")),
    ("cpu profile lanes", ("cpu_decode_profile", "lane_count")),
    ("cpu profile typed lanes", ("cpu_decode_profile", "typed_lanes")),
    ("cpu profile legacy lanes", ("cpu_decode_profile", "legacy_lanes")),
    ("cpu crc bytes", ("cpu_decode_profile", "crc_bytes")),
    ("cpu stream decode ms", ("cpu_decode_profile", "stream_decode_ms")),
    ("cpu iv expand ms", ("cpu_decode_profile", "iv_expand_ms")),
    ("cpu rebuild encrypt ms", ("cpu_decode_profile", "rebuild_encrypt_ms")),
    ("cpu crc ms", ("cpu_decode_profile", "crc_ms")),
    ("cpu profile total ms", ("cpu_decode_profile", "total_ms")),
    ("asm policy", ("asm_recommendation", "policy")),
    ("asm largest slice", ("asm_recommendation", "largest_slice")),
    ("asm largest slice ms", ("asm_recommendation", "largest_slice_ms")),
    ("asm decision", ("asm_recommendation", "decision")),
    ("asm next action", ("asm_recommendation", "next_action")),
)

EMPTY_VALUE_FACTS = {"gpu fallback reason"}


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def text_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return str(value)


def markdown_cell(value: Any) -> str:
    text = text_value(value)
    text = text.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    return text.replace("|", "\\|")


def nested_lookup(report: dict[str, Any], *keys: str) -> tuple[bool, Any]:
    current: Any = report
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return False, None
        current = current[key]
    return True, current


def int_value(value: Any, default: int = 0) -> int:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def float_value(value: Any, default: float = 0.0) -> float:
    if value in (None, "") or isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def optional_float(value: Any) -> float | None:
    if value in (None, "") or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(",", "")
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def format_int(value: Any) -> str:
    if value in (None, ""):
        return "0"
    if isinstance(value, int) and not isinstance(value, bool):
        return f"{value:,}"
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)


def format_float(value: Any) -> str:
    return f"{float_value(value):.3f}"


def format_optional_float(value: Any) -> str:
    if value in ("", None):
        return ""
    return format_float(value)


def bench_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for sample in report.get("samples", []):
        lane_count = sample.get("lane_count", 0)
        zip_bytes = sample.get("current_zip_bytes", 0)
        exception_stats = sample.get("exception_stats", {})
        for level in sample.get("spc3_levels", []):
            rows.append(
                {
                    "lane_count": lane_count,
                    "kind": "spc3_level",
                    "codec": "default",
                    "codec_level": "",
                    "level": level.get("level"),
                    "zip_bytes": zip_bytes,
                    "size_bytes": level.get("size_bytes", 0),
                    "unpack_ms": level.get("unpack_ms", 0),
                    "decode_mib_s": level.get("decode_mib_s", 0),
                    "decode_crc_mismatches": level.get("decode_crc_mismatches", 0),
                    "predictor_exceptions": exception_stats.get("predictor_exceptions", ""),
                    "bitmap_density": exception_stats.get("bitmap_density", ""),
                    "rans_fse_table_init_risk": exception_stats.get("rans_fse_table_init_risk", ""),
                }
            )
        for row in sample.get("native_codec_matrix", []):
            rows.append(
                {
                    "lane_count": lane_count,
                    "kind": "native_codec",
                    "codec": row.get("codec"),
                    "codec_level": row.get("codec_level"),
                    "level": row.get("spc3_level"),
                    "zip_bytes": zip_bytes,
                    "size_bytes": row.get("size_bytes", 0),
                    "unpack_ms": row.get("unpack_ms", 0),
                    "decode_mib_s": row.get("decode_mib_s", 0),
                    "decode_crc_mismatches": row.get("decode_crc_mismatches", 0),
                    "predictor_exceptions": exception_stats.get("predictor_exceptions", ""),
                    "bitmap_density": exception_stats.get("bitmap_density", ""),
                    "rans_fse_table_init_risk": exception_stats.get("rans_fse_table_init_risk", ""),
                }
            )
        for row in sample.get("typed_level3_matrix", []):
            rows.append(
                {
                    "lane_count": lane_count,
                    "kind": "typed_level3",
                    "codec": row.get("policy"),
                    "codec_level": "",
                    "level": 3,
                    "zip_bytes": zip_bytes,
                    "size_bytes": row.get("size_bytes", 0),
                    "unpack_ms": row.get("unpack_ms", 0),
                    "decode_mib_s": row.get("decode_mib_s", 0),
                    "decode_crc_mismatches": row.get("decode_crc_mismatches", 0),
                    "predictor_exceptions": exception_stats.get("predictor_exceptions", ""),
                    "bitmap_density": exception_stats.get("bitmap_density", ""),
                    "rans_fse_table_init_risk": exception_stats.get("rans_fse_table_init_risk", ""),
                }
            )
        gpu = sample.get("gpu_offload", {})
        if gpu and gpu.get("status") not in (None, "not_run", "not_requested"):
            kernel_ms = float_value(gpu.get("kernel_ms", 0))
            output_bytes = int_value(gpu.get("output_bytes", 0))
            decode_mib_s = 0.0
            if kernel_ms > 0.0:
                decode_mib_s = (output_bytes / (1024 * 1024)) / (kernel_ms / 1000)
            rows.append(
                {
                    "lane_count": lane_count,
                    "kind": "gpu_offload",
                    "codec": gpu.get("backend", "cuda_driver_nvrtc"),
                    "codec_level": "",
                    "level": 3,
                    "zip_bytes": zip_bytes,
                    "size_bytes": output_bytes,
                    "unpack_ms": kernel_ms,
                    "decode_mib_s": decode_mib_s,
                    "decode_crc_mismatches": int_value(gpu.get("mismatched_lanes", 0)),
                    "predictor_exceptions": exception_stats.get("predictor_exceptions", ""),
                    "bitmap_density": exception_stats.get("bitmap_density", ""),
                    "rans_fse_table_init_risk": gpu.get("status", ""),
                }
            )
    return rows


def inspect_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    report_level = report.get("level", "")
    for lane in report.get("lanes", []):
        rows.append(
            {
                "lane": lane.get("lane"),
                "level": lane.get("level", report_level),
                "codec": lane.get("codec", ""),
                "codec_level": lane.get("codec_level", ""),
                "stream_size": lane.get("stream_size", 0),
                "uncompressed_model_size": lane.get("uncompressed_model_size", 0),
                "stream_to_source_zip_ratio": lane.get("stream_to_source_zip_ratio", 0),
                "stream_to_uncompressed_model_ratio": lane.get("stream_to_uncompressed_model_ratio", 0),
                "predictor_exceptions": lane.get("predictor_exceptions", 0),
            }
        )
    return rows


def audit_lane_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for lane in report.get("lanes", []):
        if not isinstance(lane, dict) or "entry_count" not in lane:
            continue
        timings = lane.get("timings_ms", {})
        if not isinstance(timings, dict):
            timings = {}
        errors = lane.get("errors", [])
        first_error = errors[0] if isinstance(errors, list) and errors else ""
        rows.append(
            {
                "lane": lane.get("lane"),
                "audit_failed": text_value(lane.get("audit_failed", "")),
                "entry_count": lane.get("entry_count", 0),
                "zip_size_bytes": lane.get("zip_size_bytes", 0),
                "predictor_exceptions": lane.get("predictor_exceptions", 0),
                "checksum_failures": lane.get("checksum_failures", 0),
                "template_mismatches": lane.get("template_mismatches", 0),
                "rebuild_mismatches": lane.get("rebuild_mismatches", 0),
                "total_ms": timings.get("total_ms", 0),
                "first_error": first_error,
            }
        )
    return rows


def lane_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = audit_lane_rows(report)
    if rows:
        return rows
    return inspect_rows(report)


def report_fact_rows(report: dict[str, Any]) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    for label, path in REPORT_FACT_FIELDS:
        exists, value = nested_lookup(report, *path)
        if not exists or isinstance(value, (dict, list)):
            continue
        if value in (None, "") and label not in EMPTY_VALUE_FACTS:
            continue
        rows.append({"field": label, "value": text_value(value)})

    lanes = report.get("lanes")
    if isinstance(lanes, list):
        rows.append({"field": "lane table rows", "value": str(len(lanes))})

    outputs = report.get("outputs")
    if isinstance(outputs, list):
        rows.append({"field": "outputs", "value": str(len(outputs))})

    return rows


def write_output_text(text: str, output: Path | None) -> None:
    if output:
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
    else:
        encoded = text.encode("utf-8")
        if hasattr(sys.stdout, "buffer"):
            sys.stdout.buffer.write(encoded)
        else:
            sys.stdout.write(text)


def write_csv(rows: list[dict[str, Any]], output: Path | None) -> None:
    if not rows:
        text = ""
    else:
        import io

        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=list(rows[0].keys()), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
        text = stream.getvalue()
    write_output_text(text, output)


def write_markdown_summary(report: dict[str, Any], output: Path | None) -> None:
    schema = report.get("schema", "unknown")
    rows = bench_rows(report) if "bench" in schema or "oracle" in schema else lane_rows(report)
    lines = [f"# SPC3 Report Summary", "", f"- schema: `{schema}`"]
    if report.get("streaming"):
        lines.append("- streaming: `true`")
    if "samples" in report:
        lines.extend(["", "| Lanes | Kind | Codec | Level | Size | Unpack ms | MiB/s | Mismatches | Risk |"])
        lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |")
        for row in rows:
            codec = (
                row["codec"]
                if row["codec"] == "default" or row["codec_level"] in ("", None)
                else f"{row['codec']}-{row['codec_level']}"
            )
            lines.append(
                f"| {markdown_cell(row['lane_count'])} | {markdown_cell(row['kind'])} | {markdown_cell(codec)} | "
                f"{markdown_cell(row['level'])} | {markdown_cell(format_int(row['size_bytes']))} | "
                f"{markdown_cell(format_float(row['unpack_ms']))} | {markdown_cell(format_float(row['decode_mib_s']))} | "
                f"{markdown_cell(format_int(row['decode_crc_mismatches']))} | "
                f"{markdown_cell(row.get('rans_fse_table_init_risk', ''))} |"
            )
    else:
        facts = report_fact_rows(report)
        if facts:
            lines.extend(["", "## Fields", "", "| Field | Value |"])
            lines.append("| --- | --- |")
            for row in facts:
                lines.append(f"| {markdown_cell(row['field'])} | {markdown_cell(row['value'])} |")

        if rows and "audit_failed" in rows[0]:
            lines.extend(
                [
                    "",
                    "## Lanes",
                    "",
                    "| Lane | Failed | Entries | ZIP size | Predictor exceptions | Checksum failures | Template mismatches | Rebuild mismatches | Total ms | First error |",
                ]
            )
            lines.append("| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |")
            for row in rows:
                lines.append(
                    f"| {markdown_cell(row['lane'])} | {markdown_cell(row['audit_failed'])} | "
                    f"{markdown_cell(format_int(row['entry_count']))} | "
                    f"{markdown_cell(format_int(row['zip_size_bytes']))} | "
                    f"{markdown_cell(format_int(row['predictor_exceptions']))} | "
                    f"{markdown_cell(format_int(row['checksum_failures']))} | "
                    f"{markdown_cell(format_int(row['template_mismatches']))} | "
                    f"{markdown_cell(format_int(row['rebuild_mismatches']))} | "
                    f"{markdown_cell(format_float(row['total_ms']))} | "
                    f"{markdown_cell(row['first_error'])} |"
                )
        elif rows:
            lines.extend(["", "## Lanes", "", "| Lane | Codec | Level | Stream size | Model size | Predictor exceptions |"])
            lines.append("| --- | --- | ---: | ---: | ---: | ---: |")
            for row in rows:
                codec = row["codec"] if row["codec_level"] in ("", None) else f"{row['codec']}-{row['codec_level']}"
                lines.append(
                    f"| {markdown_cell(row['lane'])} | {markdown_cell(codec)} | {markdown_cell(row['level'])} | "
                    f"{markdown_cell(format_int(row['stream_size']))} | "
                    f"{markdown_cell(format_int(row['uncompressed_model_size']))} | "
                    f"{markdown_cell(format_int(row['predictor_exceptions']))} |"
                )
    text = "\n".join(lines) + "\n"
    write_output_text(text, output)


def release_step_rows(reports: dict[str, tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label, (path, report) in reports.items():
        if label == "gpu_cache":
            continue
        gpu = report.get("gpu_rebuild", {})
        cpu = report.get("cpu_decode_profile", {})
        fallback = gpu.get("fallback_reason", "") if isinstance(gpu, dict) else ""
        if not fallback and isinstance(gpu, dict) and gpu.get("requested") and not gpu.get("used"):
            fallback = gpu.get("status", "")
        mismatches = [
            report.get("roundtrip_mismatches", ""),
            report.get("internal_crc_mismatches", ""),
            report.get("source_compare_mismatches", ""),
            report.get("crc_mismatches", ""),
        ]
        if isinstance(gpu, dict):
            mismatches.extend([gpu.get("mismatched_lanes", ""), gpu.get("mismatched_bytes", "")])
        mismatch_text = "/".join(text_value(value) for value in mismatches if value not in ("", None))
        report_ms = report.get("total_ms", "")
        if report_ms in ("", None) and label == "pack":
            report_ms = report.get("build_ms", "")
        path_ms = ""
        if isinstance(cpu, dict) and cpu.get("used"):
            path_ms = cpu.get("total_ms", "")
        elif isinstance(gpu, dict) and gpu.get("used"):
            path_ms = gpu.get("total_ms", "")
        rows.append(
            {
                "step": label.replace("_", " "),
                "ok": report.get("ok", ""),
                "schema": report.get("schema", ""),
                "gpu_status": gpu.get("status", "") if isinstance(gpu, dict) else "",
                "gpu_used": gpu.get("used", "") if isinstance(gpu, dict) else "",
                "fallback": fallback,
                "mismatches": mismatch_text,
                "report_ms": report_ms,
                "path_ms": path_ms,
                "report": str(path),
            }
        )
    return rows


def release_cpu_profile_rows(reports: dict[str, tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in ("cpu_verify", "cpu_unpack"):
        if label not in reports:
            continue
        _path, report = reports[label]
        cpu = report.get("cpu_decode_profile", {})
        if not isinstance(cpu, dict) or not cpu.get("used"):
            continue
        rows.append(
            {
                "path": label.replace("_", " "),
                "backend": cpu.get("crc_backend", ""),
                "crc_bytes": cpu.get("crc_bytes", ""),
                "stream_ms": cpu.get("stream_decode_ms", ""),
                "iv_ms": cpu.get("iv_expand_ms", ""),
                "rebuild_ms": cpu.get("rebuild_encrypt_ms", ""),
                "crc_ms": cpu.get("crc_ms", ""),
                "total_ms": cpu.get("total_ms", ""),
            }
        )
    return rows


def release_gpu_rows(reports: dict[str, tuple[Path, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for label in ("gpu_verify", "gpu_unpack"):
        if label not in reports:
            continue
        _path, report = reports[label]
        gpu = report.get("gpu_rebuild", {})
        if not isinstance(gpu, dict):
            continue
        rows.append(
            {
                "path": label.replace("_", " "),
                "device": gpu.get("device_name", ""),
                "status": gpu.get("status", ""),
                "used": gpu.get("used", ""),
                "fallback": gpu.get("fallback_reason", ""),
                "download_mode": gpu.get("download_mode", ""),
                "cache_hit": gpu.get("runtime_cache_hit", ""),
                "failure_cached": gpu.get("runtime_failure_cached", ""),
                "initializations": gpu.get("runtime_initializations", ""),
                "value_count": gpu.get("value_count", ""),
                "compile_ms": gpu.get("compile_ms", ""),
                "upload_ms": gpu.get("upload_ms", ""),
                "kernel_ms": gpu.get("kernel_ms", ""),
                "download_ms": gpu.get("download_ms", ""),
                "host_crc_ms": gpu.get("host_crc_ms", ""),
                "total_ms": gpu.get("total_ms", ""),
                "mismatches": f"{gpu.get('mismatched_lanes', '')}/{gpu.get('mismatched_bytes', '')}",
            }
        )
    return rows


def release_cache_rows(report: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not report:
        return []
    rows: list[dict[str, Any]] = []
    for sample in report.get("samples", []):
        gpu = sample.get("gpu_offload", {})
        if not isinstance(gpu, dict):
            continue
        rows.append(
            {
                "lanes": sample.get("lane_count", gpu.get("lane_count", "")),
                "device": gpu.get("device_name", ""),
                "status": gpu.get("status", ""),
                "used": gpu.get("used", ""),
                "download_mode": gpu.get("download_mode", ""),
                "cache_hit": gpu.get("runtime_cache_hit", ""),
                "initializations": gpu.get("runtime_initializations", ""),
                "compile_ms": gpu.get("compile_ms", ""),
                "upload_ms": gpu.get("upload_ms", ""),
                "kernel_ms": gpu.get("kernel_ms", ""),
                "download_ms": gpu.get("download_ms", ""),
                "total_ms": gpu.get("total_ms", ""),
                "mismatches": f"{gpu.get('mismatched_lanes', '')}/{gpu.get('mismatched_bytes', '')}",
            }
        )
    return rows


def write_release_summary(reports: dict[str, tuple[Path, dict[str, Any]]], output: Path | None) -> None:
    pack = reports.get("pack", (None, {}))[1]
    lines = ["# SPC3 v0.2 Typed Level-3 Release Gate", ""]
    lines.extend(
        [
            "| Field | Value |",
            "| --- | --- |",
            f"| codec profile | {markdown_cell(pack.get('codec_profile', ''))} |",
            f"| codec | {markdown_cell(pack.get('codec', ''))} |",
            f"| level | {markdown_cell(pack.get('level', ''))} |",
            f"| typed v0.2 | {markdown_cell(pack.get('typed_level3', ''))} |",
            f"| lanes | {markdown_cell(pack.get('lane_count', ''))} |",
            f"| source ZIP bytes | {markdown_cell(format_int(pack.get('source_zip_bytes', 0)))} |",
            f"| raw payload bytes | {markdown_cell(format_int(pack.get('raw_payload_bytes', 0)))} |",
            f"| SPC3 size bytes | {markdown_cell(format_int(pack.get('spc3_size_bytes', 0)))} |",
            f"| pack build ms | {markdown_cell(format_float(pack.get('build_ms', 0)))} |",
        ]
    )

    lines.extend(["", "## Gate Results", "", "| Step | OK | Schema | GPU status | GPU used | Fallback | Mismatches | Report ms | Decode/GPU ms | Report |"])
    lines.append("| --- | --- | --- | --- | --- | --- | --- | ---: | ---: | --- |")
    for row in release_step_rows(reports):
        lines.append(
            f"| {markdown_cell(row['step'])} | {markdown_cell(row['ok'])} | {markdown_cell(row['schema'])} | "
            f"{markdown_cell(row['gpu_status'])} | {markdown_cell(row['gpu_used'])} | "
            f"{markdown_cell(row['fallback'] or 'none')} | {markdown_cell(row['mismatches'])} | "
            f"{markdown_cell(format_optional_float(row['report_ms']))} | "
            f"{markdown_cell(format_optional_float(row['path_ms']))} | {markdown_cell(row['report'])} |"
        )

    cpu_rows = release_cpu_profile_rows(reports)
    if cpu_rows:
        lines.extend(
            [
                "",
                "## CPU Typed Decode Profile",
                "",
                "| Path | CRC backend | CRC bytes | Stream ms | IV ms | Rebuild/encrypt ms | CRC ms | Total ms |",
                "| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: |",
            ]
        )
        for row in cpu_rows:
            lines.append(
                f"| {markdown_cell(row['path'])} | {markdown_cell(row['backend'])} | "
                f"{markdown_cell(format_int(row['crc_bytes']))} | {markdown_cell(format_float(row['stream_ms']))} | "
                f"{markdown_cell(format_float(row['iv_ms']))} | {markdown_cell(format_float(row['rebuild_ms']))} | "
                f"{markdown_cell(format_float(row['crc_ms']))} | {markdown_cell(format_float(row['total_ms']))} |"
            )

    gpu_rows = release_gpu_rows(reports)
    if gpu_rows:
        lines.extend(
            [
                "",
                "## GPU Rebuild",
                "",
                "| Path | Device | Status | Used | Fallback | Download | Cache hit | Failure cached | Init count | XOR values | Compile ms | Upload ms | Kernel ms | Download ms | Host CRC ms | Total ms | Mismatches |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in gpu_rows:
            lines.append(
                f"| {markdown_cell(row['path'])} | {markdown_cell(row['device'])} | "
                f"{markdown_cell(row['status'])} | {markdown_cell(row['used'])} | "
                f"{markdown_cell(row['fallback'] or 'none')} | {markdown_cell(row['download_mode'])} | "
                f"{markdown_cell(row['cache_hit'])} | {markdown_cell(row['failure_cached'])} | "
                f"{markdown_cell(row['initializations'])} | {markdown_cell(format_int(row['value_count']))} | "
                f"{markdown_cell(format_float(row['compile_ms']))} | "
                f"{markdown_cell(format_float(row['upload_ms']))} | {markdown_cell(format_float(row['kernel_ms']))} | "
                f"{markdown_cell(format_float(row['download_ms']))} | {markdown_cell(format_float(row['host_crc_ms']))} | "
                f"{markdown_cell(format_float(row['total_ms']))} | {markdown_cell(row['mismatches'])} |"
            )

    cache_report = reports.get("gpu_cache", (None, None))[1]
    cache_rows = release_cache_rows(cache_report)
    if cache_rows:
        lines.extend(
            [
                "",
                "## Long-Running GPU Cache Smoke",
                "",
                "| Lanes | Status | Used | Download | Cache hit | Init count | Compile ms | Upload ms | Kernel ms | Download ms | Total ms | Mismatches |",
                "| ---: | --- | --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | --- |",
            ]
        )
        for row in cache_rows:
            lines.append(
                f"| {markdown_cell(row['lanes'])} | {markdown_cell(row['status'])} | {markdown_cell(row['used'])} | "
                f"{markdown_cell(row['download_mode'])} | {markdown_cell(row['cache_hit'])} | "
                f"{markdown_cell(row['initializations'])} | {markdown_cell(format_float(row['compile_ms']))} | "
                f"{markdown_cell(format_float(row['upload_ms']))} | {markdown_cell(format_float(row['kernel_ms']))} | "
                f"{markdown_cell(format_float(row['download_ms']))} | {markdown_cell(format_float(row['total_ms']))} | "
                f"{markdown_cell(row['mismatches'])} |"
            )

    if cpu_rows:
        lines.extend(["", "## CPU/ASM Decision", ""])
        for row in cpu_rows:
            crc_ms = float_value(row["crc_ms"])
            rebuild_ms = float_value(row["rebuild_ms"])
            relation = "above" if crc_ms > rebuild_ms else "not above"
            lines.append(
                f"- {row['path']}: CRC is {relation} rebuild/encrypt "
                f"({crc_ms:.3f} ms vs {rebuild_ms:.3f} ms)."
            )
        lines.append("- Keep `zlib_crc32` as the measured CRC backend for now.")
        lines.append("- Keep targeted PK3 shuffle ASM active; do not add broader PK3 rebuild/encrypt ASM until CRC reduction, batching, or backend replacement is decided.")

    text = "\n".join(lines) + "\n"
    write_output_text(text, output)


def compare_bench_reports(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    left_rows = {
        (row["lane_count"], row["kind"], row["codec"], row["codec_level"], row["level"]): row
        for row in bench_rows(left)
    }
    right_rows = {
        (row["lane_count"], row["kind"], row["codec"], row["codec_level"], row["level"]): row
        for row in bench_rows(right)
    }
    rows: list[dict[str, Any]] = []
    for key in sorted(set(left_rows) & set(right_rows), key=lambda item: tuple(str(part) for part in item)):
        a = left_rows[key]
        b = right_rows[key]
        left_size = int_value(a["size_bytes"])
        right_size = int_value(b["size_bytes"])
        left_unpack = float_value(a["unpack_ms"])
        right_unpack = float_value(b["unpack_ms"])
        rows.append(
            {
                "lane_count": key[0],
                "kind": key[1],
                "codec": key[2],
                "codec_level": key[3],
                "level": key[4],
                "left_size_bytes": left_size,
                "right_size_bytes": right_size,
                "size_delta": right_size - left_size,
                "left_unpack_ms": left_unpack,
                "right_unpack_ms": right_unpack,
                "unpack_ms_delta": right_unpack - left_unpack,
            }
        )
    return rows


def compare_fact_reports(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, str]]:
    left_facts = {row["field"]: row["value"] for row in report_fact_rows(left)}
    right_facts = {row["field"]: row["value"] for row in report_fact_rows(right)}
    ordered_fields = [label for label, _ in REPORT_FACT_FIELDS] + ["lane table rows", "outputs"]
    fields = [field for field in ordered_fields if field in left_facts or field in right_facts]
    fields.extend(sorted((set(left_facts) | set(right_facts)) - set(fields)))

    rows: list[dict[str, str]] = []
    for field in fields:
        left_value = left_facts.get(field, "")
        right_value = right_facts.get(field, "")
        left_num = optional_float(left_value)
        right_num = optional_float(right_value)
        delta = "" if left_num is None or right_num is None else f"{right_num - left_num:.3f}"
        rows.append({"field": field, "left": left_value, "right": right_value, "delta": delta})
    return rows


def compare_reports(left: dict[str, Any], right: dict[str, Any]) -> list[dict[str, Any]]:
    if bench_rows(left) and bench_rows(right):
        return compare_bench_reports(left, right)
    return compare_fact_reports(left, right)


def summary_csv_rows(report: dict[str, Any], table: str) -> list[dict[str, Any]]:
    if table == "fields":
        return report_fact_rows(report)
    if table == "lanes":
        return lane_rows(report)

    rows = bench_rows(report)
    if rows:
        return rows
    rows = report_fact_rows(report)
    if rows:
        return rows
    return inspect_rows(report)


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize or compare SPC3 JSON reports.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    summary = subparsers.add_parser("summary")
    summary.add_argument("report", type=Path)
    summary.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    summary.add_argument(
        "--table",
        choices=("auto", "fields", "lanes"),
        default="auto",
        help="CSV table for non-benchmark summaries; markdown includes both fields and lanes when available.",
    )
    summary.add_argument("--output", type=Path)

    compare = subparsers.add_parser("compare")
    compare.add_argument("left", type=Path)
    compare.add_argument("right", type=Path)
    compare.add_argument("--format", choices=("markdown", "csv"), default="markdown")
    compare.add_argument("--output", type=Path)

    release = subparsers.add_parser("release-summary")
    release.add_argument("--pack", type=Path, required=True)
    release.add_argument("--cpu-verify", type=Path, required=True)
    release.add_argument("--gpu-verify", type=Path, required=True)
    release.add_argument("--cpu-unpack", type=Path, required=True)
    release.add_argument("--gpu-unpack", type=Path, required=True)
    release.add_argument("--gpu-cache", type=Path)
    release.add_argument("--output", type=Path)

    args = parser.parse_args()
    if args.command == "summary":
        report = load_json(args.report)
        if args.format == "csv":
            write_csv(summary_csv_rows(report, args.table), args.output)
        else:
            write_markdown_summary(report, args.output)
        return 0

    if args.command == "release-summary":
        reports = {
            "pack": (args.pack, load_json(args.pack)),
            "cpu_verify": (args.cpu_verify, load_json(args.cpu_verify)),
            "gpu_verify": (args.gpu_verify, load_json(args.gpu_verify)),
            "cpu_unpack": (args.cpu_unpack, load_json(args.cpu_unpack)),
            "gpu_unpack": (args.gpu_unpack, load_json(args.gpu_unpack)),
        }
        if args.gpu_cache:
            reports["gpu_cache"] = (args.gpu_cache, load_json(args.gpu_cache))
        write_release_summary(reports, args.output)
        return 0

    left = load_json(args.left)
    right = load_json(args.right)
    rows = compare_reports(left, right)
    if args.format == "csv":
        write_csv(rows, args.output)
    else:
        lines = ["# SPC3 Report Compare", ""]
        if rows and "field" in rows[0]:
            lines.append("| Field | Left | Right | Delta |")
            lines.append("| --- | --- | --- | ---: |")
            for row in rows:
                lines.append(
                    f"| {markdown_cell(row['field'])} | {markdown_cell(row['left'])} | "
                    f"{markdown_cell(row['right'])} | {markdown_cell(row['delta'])} |"
                )
        else:
            lines.append("| Lanes | Kind | Codec | Level | Left size | Right size | Size delta | Left unpack | Right unpack | Delta |")
            lines.append("| ---: | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
            for row in rows:
                codec = (
                    row["codec"]
                    if row["codec"] == "default" or row["codec_level"] in ("", None)
                    else f"{row['codec']}-{row['codec_level']}"
                )
                lines.append(
                    f"| {markdown_cell(row['lane_count'])} | {markdown_cell(row['kind'])} | "
                    f"{markdown_cell(codec)} | {markdown_cell(row['level'])} | "
                    f"{markdown_cell(format_int(row['left_size_bytes']))} | "
                    f"{markdown_cell(format_int(row['right_size_bytes']))} | "
                    f"{markdown_cell(format_int(row['size_delta']))} | "
                    f"{markdown_cell(format_float(row['left_unpack_ms']))} | "
                    f"{markdown_cell(format_float(row['right_unpack_ms']))} | "
                    f"{markdown_cell(format_float(row['unpack_ms_delta']))} |"
                )
        text = "\n".join(lines) + "\n"
        write_output_text(text, args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
