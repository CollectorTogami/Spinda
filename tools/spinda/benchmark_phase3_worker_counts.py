#!/usr/bin/env python3
"""Benchmark Phase 3 worker counts on the same lane range.

The script launches the existing worker pool once per requested worker count,
records wall time, completed lanes, generated records, and rough lanes/hour.
Use small `--limit` runs first; full-lane benchmarks can be expensive.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).absolute().parents[2]
DEFAULT_POOL = ROOT / "tools" / "spinda" / "native_phase3_worker_pool.py"
DEFAULT_PYTHON = ROOT / ".venv-mgba" / "bin" / "python.exe"
DEFAULT_OUTPUT_ROOT = ROOT / "Phase3SpindaBlocks" / "_bench_worker_counts"


def parse_worker_counts(raw: str) -> list[int]:
    counts: list[int] = []
    for part in raw.split(","):
        value = int(part.strip(), 0)
        if value < 1:
            raise ValueError("worker counts must be positive")
        if value not in counts:
            counts.append(value)
    return counts


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def validate_output_dirs(output_root: Path, worker_counts: Sequence[int], *, reuse_output: bool) -> None:
    """Reject stale benchmark output that would turn reruns into skip tests.

    The worker pool intentionally supports filename-only resume. That is good
    for production, but bad for timing: old lane ZIPs would be skipped and make
    a worker-count benchmark look faster than it is.
    """
    if reuse_output:
        return
    for workers in worker_counts:
        output_dir = output_root / f"workers_{workers}"
        if not output_dir.exists():
            continue
        if not output_dir.is_dir():
            raise ValueError(f"benchmark output path is not a directory: {output_dir}")
        try:
            next(output_dir.iterdir())
        except StopIteration:
            continue
        raise ValueError(f"benchmark output dir is not empty: {output_dir}; pass --reuse-output to resume/skip existing lanes")


def summarize_lane_status_files(output_dir: Path) -> tuple[int, int]:
    """Return exact completed lanes and generated records from lane statuses.

    Pool status intentionally preview-limits the ``done`` list for full runs.
    Benchmark summaries need exact counts, so read the compact per-lane status
    files after the worker pool exits instead of trusting that preview list.
    """
    complete_lanes = 0
    generated_records = 0
    for status_path in output_dir.glob("_0x*.phase3_status.json"):
        payload = load_json(status_path)
        if payload.get("status") != "complete":
            continue
        complete_lanes += 1
        try:
            generated_records += int(payload.get("generated_records", 0))
        except (TypeError, ValueError):
            continue
    return complete_lanes, generated_records


def run_one(args: argparse.Namespace, workers: int) -> dict[str, Any]:
    output_dir = Path(args.output_root) / f"workers_{workers}"
    command = [
        str(args.python),
        str(args.pool_script),
        "--runner",
        "cli",
        "--lanes",
        args.lanes,
        "--workers",
        str(workers),
        "--output-dir",
        str(output_dir),
        "--cache-dir",
        str(args.cache_dir),
        "--poll-seconds",
        str(args.poll_seconds),
        "--overwrite",
        "--skip-existing-by-name",
        "--zip-method",
        args.zip_method,
    ]
    if args.limit is not None:
        command.extend(["--limit", str(args.limit)])
    if args.no_learn_pickup_delay:
        command.append("--no-learn-pickup-delay")
    else:
        command.extend(["--learn-pickup-delay-samples", str(args.learn_pickup_delay_samples)])

    started = time.monotonic()
    if args.dry_run:
        command.append("--dry-run")
    completed = subprocess.run(command, cwd=str(ROOT), text=True, capture_output=True)
    elapsed = time.monotonic() - started
    status = load_json(output_dir / "_native_phase3_worker_pool_status.json")
    counts = status.get("counts", {})
    done = status.get("done", [])
    complete_lanes, generated_records = summarize_lane_status_files(output_dir)
    status_source = "lane_status_files"
    if complete_lanes == 0 and generated_records == 0:
        status_source = "pool_status_preview"
        complete_lanes = sum(int(item.get("complete_lanes", 0)) for item in done if isinstance(item, dict))
        generated_records = sum(
            int(lane_status.get("generated_records", 0))
            for item in done
            if isinstance(item, dict)
            for lane_status in item.get("lane_statuses", [])
            if isinstance(lane_status, dict)
        )
    return {
        "workers": workers,
        "exit_code": completed.returncode,
        "elapsed_seconds": round(elapsed, 3),
        "complete_lanes": complete_lanes,
        "generated_records": generated_records,
        "lanes_per_hour": round((complete_lanes / elapsed) * 3600.0, 3) if elapsed > 0 else 0,
        "counts": counts,
        "status_source": status_source,
        "output_dir": str(output_dir),
        "command": command,
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Benchmark Phase 3 worker-count throughput.")
    parser.add_argument("--worker-counts", default="2,4,6", help="Comma list such as 2,4,6.")
    parser.add_argument("--lanes", required=True, help="Lane range passed to native_phase3_worker_pool.py.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--cache-dir", type=Path, default=ROOT / "Phase3SpindaBlocks" / "_cache")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--pool-script", type=Path, default=DEFAULT_POOL)
    parser.add_argument("--poll-seconds", type=float, default=1.0)
    parser.add_argument("--zip-method", choices=("deflate", "store"), default="deflate")
    parser.add_argument("--learn-pickup-delay-samples", type=int, default=32)
    parser.add_argument("--no-learn-pickup-delay", action="store_true")
    parser.add_argument("--reuse-output", action="store_true", help="Allow existing workers_N dirs; useful for resume, not clean timing.")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        worker_counts = parse_worker_counts(args.worker_counts)
    except ValueError as error:
        parser.error(str(error))
    if args.learn_pickup_delay_samples < 1:
        parser.error("--learn-pickup-delay-samples must be positive")
    try:
        validate_output_dirs(args.output_root, worker_counts, reuse_output=args.reuse_output)
    except ValueError as error:
        parser.error(str(error))

    results = [run_one(args, workers) for workers in worker_counts]
    args.output_root.mkdir(parents=True, exist_ok=True)
    summary_path = args.output_root / "_worker_count_benchmark_summary.json"
    summary_path.write_text(json.dumps({"results": results}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"summary_path": str(summary_path), "results": results}, indent=2, sort_keys=True))
    return 0 if all(result["exit_code"] == 0 for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
