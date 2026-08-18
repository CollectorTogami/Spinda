#!/usr/bin/env python3
"""Umbrella CLI for SPC3 full-corpus compression formats v2 through v8."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import spc3_iv_offset_classifier as clf  # noqa: E402
import spc3_rule_bitmap_repack as v3  # noqa: E402
import spc3_two_stage_runtime_repack as v45  # noqa: E402
import spc3_v6_upper_repack as v6  # noqa: E402
import spc3_v7_global_stage_repack as v7  # noqa: E402
import spc3_v8_compact_repack as v8  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
ARTIFACTS = ROOT / "Helper-PC-Artifacts"
DEFAULT_OUTPUT_DIR = ARTIFACTS / "spc3-umbrella-outputs"
DEFAULT_SOURCE_V2 = ARTIFACTS / "helper_full_corpus_65536.spc3"
DEFAULT_ROOT = ROOT / "Phase3SpindaBlocks"
DEFAULT_NATIVE_EXE = SCRIPT_DIR / "spc3_prototype" / "spc3_prototype.exe"
TARGETS = ("v2", "v3", "v4", "v5", "v6", "v7", "v8")


@dataclass(frozen=True)
class TargetSpec:
    target: str
    label: str
    default_output: Path
    default_report: Path


SPECS = {
    "v2": TargetSpec(
        "v2",
        "native typed level-3 v2",
        DEFAULT_SOURCE_V2,
        ARTIFACTS / "helper_full_corpus_65536.umbrella-v2.verify.json",
    ),
    "v3": TargetSpec(
        "v3",
        "rule bitmap v3",
        ARTIFACTS / "helper_full_corpus_65536.rule-lm24-lowbyte.spc3",
        ARTIFACTS / "helper_full_corpus_65536.rule-lm24-lowbyte.umbrella-verify.json",
    ),
    "v4": TargetSpec(
        "v4",
        "two-stage runtime XOR residual v4",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg.spc3",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg.umbrella-verify.json",
    ),
    "v5": TargetSpec(
        "v5",
        "two-stage runtime stat-delta residual v5",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.umbrella-verify.json",
    ),
    "v6": TargetSpec(
        "v6",
        "two-stage runtime upper-mask-group residual v6",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.umbrella-verify.json",
    ),
    "v7": TargetSpec(
        "v7",
        "two-stage runtime global-stage v7",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.umbrella-verify.json",
    ),
    "v8": TargetSpec(
        "v8",
        "two-stage runtime compact global-stage v8",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3",
        ARTIFACTS / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.umbrella-verify.json",
    ),
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=(*TARGETS, "all"), required=True)
    parser.add_argument("--mode", choices=("pack", "verify", "pack-verify", "audit"), default="pack-verify")
    parser.add_argument("--input", type=Path, default=DEFAULT_SOURCE_V2, help="source v2 SPC3 for v3-v8")
    parser.add_argument("--output", type=Path, help="single-target output SPC3 path")
    parser.add_argument("--report", type=Path, help="single-target report or all-target summary report")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="default directory for pack outputs and all-target reports")
    parser.add_argument("--native-exe", type=Path, default=DEFAULT_NATIVE_EXE)
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT, help="Phase3SpindaBlocks root for native v2 pack/verify")
    parser.add_argument("--predictor-json", type=Path, default=clf.DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--limit-zips", default="all")
    parser.add_argument("--level", type=int, default=3)
    parser.add_argument("--codec-profile", choices=("auto", "compat", "fast", "small"), default="fast")
    parser.add_argument("--typed-level3", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--source-compare", action="store_true", help="v2 verify: compare against source ZIP root")
    parser.add_argument("--start-rng", default="0x2B0C94C1")
    parser.add_argument("--runtime-max-steps", type=int, default=4_000_000)
    parser.add_argument("--base-model", choices=tuple(clf.BASE_MODEL_POSITIONS), default="rsfrlg")
    parser.add_argument("--max-extra", type=int, default=2)
    parser.add_argument("--zstd-level", type=int, default=9)
    parser.add_argument(
        "--value-layout",
        choices=tuple(v6.VALUE_LAYOUT_IDS),
        default=v6.VALUE_LAYOUT_UPPER_MASK_GROUP,
        help="v6/v7 residual value layout. v8 always uses selected-mask-group.",
    )
    parser.add_argument(
        "--stage-layout",
        choices=tuple(v7.STAGE_LAYOUT_IDS),
        default=v7.STAGE_LAYOUT_SPLIT_BITMAPS,
        help="v7 stage layout.",
    )
    parser.add_argument(
        "--sample-lanes",
        type=int,
        default=None,
        help="Smoke-test first N lanes for v4-v8 pack modes. v3 and v2 do not support this.",
    )
    parser.add_argument("--progress-every", type=int, default=4096)
    parser.add_argument("--scratch-dir", type=Path, default=None)
    parser.add_argument("--keep-scratch", action="store_true")
    return parser.parse_args()


def default_report_for(target: str, mode: str, output_dir: Path) -> Path:
    if mode == "audit":
        mode = "verify"
    spec = SPECS[target]
    if output_dir == ARTIFACTS:
        return spec.default_report if mode == "verify" else spec.default_report.with_name(
            spec.default_report.name.replace(".umbrella-verify.json", f".umbrella-{mode}.json")
        )
    return output_dir / f"spc3_{target}_{mode}.json"


def default_output_for(target: str, output_dir: Path, pack_mode: bool) -> Path:
    spec = SPECS[target]
    if not pack_mode:
        return spec.default_output
    return output_dir / spec.default_output.name


def pack_report_path(report_path: Path) -> Path:
    return report_path.with_name(report_path.stem + ".pack.json")


def load_report(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"_missing": str(path)}
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_report(report: dict[str, Any], output_path: Path) -> dict[str, Any]:
    status = report.get("status")
    mismatch_count = report.get("mismatch_count")
    if status is None and report.get("ok") is not None:
        status = "ok" if report.get("ok") else "failed"
    if mismatch_count is None and report.get("internal_crc_mismatches") is not None:
        mismatch_count = int(report.get("internal_crc_mismatches") or 0) + int(report.get("source_compare_mismatches") or 0)
    size = report.get("new_size_bytes") or report.get("spc3_size_bytes")
    if size is None and output_path.exists():
        size = output_path.stat().st_size
    elapsed_seconds = report.get("elapsed_seconds")
    if elapsed_seconds is None and report.get("total_ms") is not None:
        elapsed_seconds = float(report["total_ms"]) / 1000.0
    return {
        "schema": report.get("schema"),
        "status": status,
        "mismatch_count": mismatch_count,
        "size_bytes": size,
        "elapsed_seconds": elapsed_seconds,
    }


def run_native_v2(
    *,
    args: argparse.Namespace,
    mode: str,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    native = args.native_exe
    if not native.is_file():
        raise FileNotFoundError(f"native SPC3 executable not found: {native}")
    if mode == "audit":
        mode = "verify"
    if args.sample_lanes is not None and mode in {"pack", "pack-verify"}:
        raise ValueError("v2 pack uses --limit-zips; it does not support --sample-lanes")

    def invoke(command: list[str]) -> None:
        print(">", subprocess.list2cmdline(command), flush=True)
        subprocess.run(command, cwd=ROOT, check=True)

    if mode in {"pack", "pack-verify"}:
        command = [
            str(native),
            "--mode",
            "pack",
            "--root",
            str(args.root),
            "--predictor",
            str(args.predictor_json),
            "--report",
            str(pack_report_path(report_path) if mode == "pack-verify" else report_path),
            "--output",
            str(output_path),
            "--level",
            str(args.level),
            "--limit-zips",
            str(args.limit_zips),
        ]
        if args.typed_level3:
            command.append("--typed-level3")
        if args.codec_profile != "auto":
            command += ["--codec-profile", args.codec_profile]
        invoke(command)
    if mode in {"verify", "pack-verify"}:
        verify_input = output_path if mode == "pack-verify" else args.input
        command = [
            str(native),
            "--mode",
            "verify",
            "--input",
            str(verify_input),
            "--predictor",
            str(args.predictor_json),
            "--report",
            str(report_path),
        ]
        if args.source_compare:
            command += ["--root", str(args.root)]
        else:
            command.append("--no-source-compare")
        invoke(command)
    return load_report(report_path)


def run_target(
    *,
    target: str,
    args: argparse.Namespace,
    mode: str,
    output_path: Path,
    report_path: Path,
) -> dict[str, Any]:
    if mode == "audit":
        mode = "verify"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    source = args.input
    start_rng = v45.parse_int(args.start_rng)

    if target == "v2":
        return run_native_v2(args=args, mode=mode, output_path=output_path, report_path=report_path)
    if target == "v3":
        if args.sample_lanes is not None and mode in {"pack", "pack-verify"}:
            raise ValueError("v3 pack does not support --sample-lanes")
        if mode in {"pack", "pack-verify"}:
            v3.repack(
                source,
                output_path,
                pack_report_path(report_path) if mode == "pack-verify" else report_path,
                zstd_level=args.zstd_level,
                progress_every=args.progress_every,
            )
        if mode in {"verify", "pack-verify"}:
            return v3.verify(output_path, source, report_path, progress_every=args.progress_every)
        return load_report(report_path)
    if target in {"v4", "v5"}:
        residual = v45.RESIDUAL_ENCODING_XOR if target == "v4" else v45.RESIDUAL_ENCODING_STAT_DELTA
        if mode in {"pack", "pack-verify"}:
            v45.repack(
                source,
                output_path,
                pack_report_path(report_path) if mode == "pack-verify" else report_path,
                predictor_json=args.predictor_json,
                start_rng=start_rng,
                runtime_max_steps=args.runtime_max_steps,
                base_model=args.base_model,
                max_extra=args.max_extra,
                zstd_level=args.zstd_level,
                residual_encoding=residual,
                sample_lanes=args.sample_lanes,
                progress_every=args.progress_every,
            )
        if mode in {"verify", "pack-verify"}:
            return v45.verify(output_path, source, report_path, predictor_json=args.predictor_json, progress_every=args.progress_every)
        return load_report(report_path)
    if target == "v6":
        if mode in {"pack", "pack-verify"}:
            v6.repack(
                source,
                output_path,
                pack_report_path(report_path) if mode == "pack-verify" else report_path,
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
        if mode in {"verify", "pack-verify"}:
            return v6.verify(output_path, source, report_path, predictor_json=args.predictor_json, progress_every=args.progress_every)
        return load_report(report_path)
    if target == "v7":
        if mode in {"pack", "pack-verify"}:
            v7.repack(
                source,
                output_path,
                pack_report_path(report_path) if mode == "pack-verify" else report_path,
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
        if mode in {"verify", "pack-verify"}:
            return v7.verify(output_path, source, report_path, predictor_json=args.predictor_json, progress_every=args.progress_every)
        return load_report(report_path)
    if target == "v8":
        if mode in {"pack", "pack-verify"}:
            v8.repack(
                source,
                output_path,
                pack_report_path(report_path) if mode == "pack-verify" else report_path,
                predictor_json=args.predictor_json,
                start_rng=start_rng,
                runtime_max_steps=args.runtime_max_steps,
                base_model=args.base_model,
                max_extra=args.max_extra,
                zstd_level=args.zstd_level,
                value_layout=v8.VALUE_LAYOUT_SELECTED_MASK_GROUP,
                stage_layout=v8.STAGE_LAYOUT_ADAPTIVE_BITMAPS,
                sample_lanes=args.sample_lanes,
                progress_every=args.progress_every,
                scratch_dir=args.scratch_dir,
                keep_scratch=args.keep_scratch,
            )
        if mode in {"verify", "pack-verify"}:
            return v8.verify(output_path, source, report_path, predictor_json=args.predictor_json, progress_every=args.progress_every)
        return load_report(report_path)
    raise ValueError(f"unsupported target: {target}")


def write_summary(path: Path, summary: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def main() -> int:
    args = parse_args()
    if not 1 <= args.zstd_level <= 22:
        raise SystemExit("--zstd-level must be in 1..22")
    if args.max_extra < 0:
        raise SystemExit("--max-extra must be non-negative")
    if args.sample_lanes is not None and args.sample_lanes < 0:
        raise SystemExit("--sample-lanes must be non-negative")
    mode = "verify" if args.mode == "audit" else args.mode
    targets = list(TARGETS) if args.target == "all" else [args.target]
    started = time.perf_counter()

    if len(targets) > 1:
        summary_report = args.report or args.output_dir / f"spc3_umbrella_all_{args.mode}.json"
    else:
        summary_report = args.report or default_report_for(targets[0], args.mode, args.output_dir)

    results: list[dict[str, Any]] = []
    for target in targets:
        pack_mode = mode in {"pack", "pack-verify"}
        if target == "v2" and mode == "verify":
            output_path = args.input
        elif args.output and len(targets) == 1:
            output_path = args.output
        else:
            output_path = default_output_for(target, args.output_dir, pack_mode)
        report_path = (
            summary_report
            if len(targets) == 1
            else summary_report.with_name(f"{summary_report.stem}.{target}.json")
        )
        print(f"== {target}: {SPECS[target].label} ==", flush=True)
        report = run_target(target=target, args=args, mode=args.mode, output_path=output_path, report_path=report_path)
        result = {
            "target": target,
            "label": SPECS[target].label,
            "output": str(output_path),
            "report": str(report_path),
            **summarize_report(report, output_path),
        }
        results.append(result)
        status = result.get("status") or "unknown"
        mismatches = result.get("mismatch_count")
        print(f"{target} status={status} mismatches={mismatches} size={result.get('size_bytes')}", flush=True)

    failed = [
        result
        for result in results
        if result.get("status") not in (None, "ok")
        or result.get("mismatch_count") not in (None, 0)
        or result.get("schema") in (None, "")
    ]
    summary = {
        "schema": "spc3_umbrella_compress.v1",
        "mode": args.mode,
        "target": args.target,
        "input": str(args.input),
        "elapsed_seconds": time.perf_counter() - started,
        "results": results,
        "failed_count": len(failed),
        "failed": failed,
    }
    if len(targets) > 1:
        write_summary(summary_report, summary)
        print(f"REPORT {summary_report}", flush=True)
    print(
        f"SUMMARY target={args.target} mode={args.mode} failed={len(failed)} "
        f"elapsed={summary['elapsed_seconds']:.1f}s",
        flush=True,
    )
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
