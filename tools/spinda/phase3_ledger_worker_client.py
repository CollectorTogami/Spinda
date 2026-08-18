#!/usr/bin/env python3
"""Claim Phase 3 lanes from a coordinator, then run the native worker pool.

This script is the bridge between the new Flask ledger API and an assisted
workstation. It claims a bounded batch, launches the existing worker pool for
that batch, heartbeats while workers run, and reports each lane finished/failed.
"""

from __future__ import annotations

import argparse
import json
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Iterable, Sequence
from urllib import error as urlerror
from urllib import request as urlrequest


ROOT = Path(__file__).absolute().parents[2]
DEFAULT_COORDINATOR_URL = "http://127.0.0.1:235"
DEFAULT_WORKER_POOL_SCRIPT = ROOT / "tools" / "spinda" / "native_phase3_worker_pool.py"
DEFAULT_OUTPUT_DIR = ROOT / "Phase3SpindaBlocks"
DEFAULT_STATUS_NAME = "_phase3_ledger_worker_client_status.json"
DEFAULT_BATCH_SIZE = 24
DEFAULT_LEASE_SECONDS = 6 * 3600
DEFAULT_HEARTBEAT_SECONDS = 60.0
DEFAULT_SLEEP_ERROR_SECONDS = 60.0
MIN_FINAL_ZIP_BYTES = 1024
ACTIVE_STATUSES = {"claimed", "running_batch", "report_error", "reporting_existing"}


def post_json(base_url: str, path: str, payload: dict[str, Any], *, timeout: float = 15.0) -> dict[str, Any]:
    """POST JSON to coordinator and return decoded JSON."""

    url = base_url.rstrip("/") + path
    request = urlrequest.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlrequest.urlopen(request, timeout=timeout) as response:
            data = json.loads(response.read().decode("utf-8"))
    except (OSError, urlerror.URLError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"coordinator request failed: {url}: {exc}") from exc
    if not data.get("ok", True):
        raise RuntimeError(f"coordinator rejected {path}: {data.get('error') or data}")
    return data


def lane_ranges(lanes: Sequence[str]) -> list[str]:
    """Compact claimed lane ids for low-cost command-center heartbeats."""

    values: list[int] = []
    for lane in lanes:
        try:
            values.append(int(str(lane), 16))
        except ValueError:
            continue
    if not values:
        return []
    values = sorted(set(values))
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
        start = previous = value
    ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
    return ranges


def valid_local_zip(output_dir: Path, lane: str) -> bool:
    """Return true for a final ZIP large enough to be a completed lane."""

    zip_path = output_dir / f"{lane}.spinda80.zip"
    try:
        return zip_path.is_file() and zip_path.stat().st_size >= MIN_FINAL_ZIP_BYTES
    except OSError:
        return False


def write_client_status(
    args: argparse.Namespace,
    *,
    status: str,
    lanes: Sequence[str] = (),
    error: str | None = None,
    extra: dict[str, Any] | None = None,
) -> None:
    """Write the subordinate's live claim/run status for local UI and upstream heartbeat.

    The coordinator is still the source of assignment truth. This file only
    mirrors what the helper is currently doing so a command center with no
    worker sidecar JSONs can still see claimed/running lane ranges.
    """

    active_lanes = list(lanes) if status in ACTIVE_STATUSES else []
    payload: dict[str, Any] = {
        "schema_version": 1,
        "status": status,
        "device_id": args.device_id,
        "worker_id": args.worker_id,
        "coordinator_url": args.coordinator_url,
        "claimed_lanes": list(lanes),
        "active_lane_ranges": lane_ranges(active_lanes),
        "workers": args.workers,
        "bundle_size": args.bundle_size,
        "batch_size": args.batch_size,
        "updated_at_unix": time.time(),
        "updated_at_local": time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime()),
    }
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    args.status_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.status_out.with_suffix(args.status_out.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(args.status_out)


def claim_lanes(args: argparse.Namespace) -> list[str]:
    """Claim one batch of lanes from coordinator."""

    payload = {
        "device_id": args.device_id,
        "worker_id": args.worker_id,
        "count": args.batch_size,
        "lanes": args.lanes,
        "lease_seconds": args.lease_seconds,
    }
    data = post_json(args.coordinator_url, "/api/ledger/claim", payload)
    return [str(lane) for lane in data.get("claimed_lanes", [])]


def heartbeat(args: argparse.Namespace, lanes: Sequence[str]) -> None:
    """Best-effort heartbeat for claimed lanes."""

    payload = {
        "device_id": args.device_id,
        "worker_id": args.worker_id,
        "lanes": list(lanes),
        "lease_seconds": args.lease_seconds,
    }
    post_json(args.coordinator_url, "/api/ledger/heartbeat", payload, timeout=5.0)


def report_one_result(args: argparse.Namespace, lane: str) -> bool:
    """Report one lane if its final ZIP exists and is large enough.

    Returns true for a `/finish` report and false for a retryable `/fail`
    report. Both cases are explicit coordinator updates; exceptions mean no
    authoritative report was accepted.
    """

    zip_path = Path(args.output_dir) / f"{lane}.spinda80.zip"
    try:
        zip_size = zip_path.stat().st_size
    except OSError:
        zip_size = 0
    if zip_path.is_file() and zip_size >= MIN_FINAL_ZIP_BYTES:
        post_json(
            args.coordinator_url,
            "/api/ledger/finish",
            {
                "device_id": args.device_id,
                "worker_id": args.worker_id,
                "lane": lane,
                "zip_path": str(zip_path),
                "zip_size": zip_size,
                "pk3_count": 65536,
            },
        )
        return True
    post_json(
        args.coordinator_url,
        "/api/ledger/fail",
        {
            "device_id": args.device_id,
            "worker_id": args.worker_id,
            "lane": lane,
            "reason": f"worker pool exited without valid final ZIP: {zip_path} ({zip_size} bytes)",
            "retryable": True,
        },
    )
    return False


def report_available_results(
    args: argparse.Namespace,
    lanes: Sequence[str],
    reported_lanes: set[str],
) -> list[str]:
    """Best-effort report for completed ZIPs while a larger batch still runs."""

    newly_reported: list[str] = []
    for lane in lanes:
        if lane in reported_lanes or not valid_local_zip(Path(args.output_dir), lane):
            continue
        if report_one_result(args, lane):
            reported_lanes.add(lane)
            newly_reported.append(lane)
    return newly_reported


def report_results(args: argparse.Namespace, lanes: Sequence[str]) -> None:
    """Report finished/failed lanes by final ZIP filename."""

    for lane in lanes:
        report_one_result(args, lane)


def worker_pool_command(args: argparse.Namespace, lanes: Sequence[str], passthrough: Sequence[str]) -> list[str]:
    """Build existing worker-pool command for one claimed batch.

    Cross-platform helper nodes keep all OS-specific paths in `passthrough`.
    The client itself only owns the lane claim, worker count, bundle count, and
    output folder used to decide whether a claimed lane produced its ZIP.
    """

    return [
        str(args.python_exe),
        str(args.worker_pool_script),
        "--lanes",
        ",".join(lanes),
        "--workers",
        str(args.workers),
        "--bundle-size",
        str(args.bundle_size),
        "--output-dir",
        str(args.output_dir),
        "--skip-existing-by-name",
        "--overwrite",
        *passthrough,
    ]


def run_claimed_batch(
    args: argparse.Namespace,
    lanes: Sequence[str],
    passthrough: Sequence[str],
    *,
    all_claimed_lanes: Sequence[str] | None = None,
    reported_lanes: set[str] | None = None,
) -> int:
    """Run one claimed batch, heartbeat, and report completed ZIPs as they appear."""

    command = worker_pool_command(args, lanes, passthrough)
    write_client_status(args, status="running_batch", lanes=lanes, extra={"command": command})
    process = subprocess.Popen(command, cwd=str(ROOT))
    next_heartbeat = 0.0
    next_result_report = 0.0
    all_claimed_lanes = list(all_claimed_lanes or lanes)
    reported_lanes = reported_lanes if reported_lanes is not None else set()
    while process.poll() is None:
        now = time.monotonic()
        if now >= next_heartbeat:
            try:
                heartbeat(args, lanes)
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr, flush=True)
            next_heartbeat = now + args.heartbeat_seconds
        if now >= next_result_report:
            try:
                reported = report_available_results(args, all_claimed_lanes, reported_lanes)
                if reported:
                    write_client_status(args, status="running_batch", lanes=lanes, extra={"command": command, "reported_lanes": sorted(reported_lanes)})
            except RuntimeError as exc:
                print(str(exc), file=sys.stderr, flush=True)
            next_result_report = now + args.heartbeat_seconds
        time.sleep(min(2.0, max(0.5, args.heartbeat_seconds / 10.0)))
    return int(process.returncode or 0)


def report_results_until_success(args: argparse.Namespace, lanes: Sequence[str], *, batch_code: int) -> None:
    """Keep reporting a completed batch before claiming more work.

    If the coordinator is temporarily unreachable after a lane finishes, moving
    on would leave finished lanes marked running until their leases expire.
    Retrying here keeps the coordinator ledger closer to reality and prevents
    duplicate assignment.
    """

    while True:
        try:
            report_results(args, lanes)
            write_client_status(args, status="reported_batch", lanes=lanes, extra={"batch_returncode": batch_code})
            return
        except RuntimeError as exc:
            write_client_status(args, status="report_error", lanes=lanes, error=str(exc), extra={"batch_returncode": batch_code})
            print(str(exc), file=sys.stderr, flush=True)
            if args.once:
                raise
            time.sleep(args.sleep_error_seconds)


def parse_args(argv: Iterable[str] | None = None) -> tuple[argparse.Namespace, list[str]]:
    """Parse ledger client args. Extra args after `--` pass to worker pool."""

    parser = argparse.ArgumentParser(
        description="Claim Phase 3 lanes from Flask coordinator and run native worker pool batches.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--coordinator-url", default=DEFAULT_COORDINATOR_URL)
    parser.add_argument("--device-id", default=socket.gethostname())
    parser.add_argument("--worker-id", default="ledger-worker")
    parser.add_argument("--lanes", default="0x0000-0xFFFF")
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    parser.add_argument("--workers", type=int, default=6)
    parser.add_argument("--bundle-size", type=int, default=2)
    parser.add_argument("--lease-seconds", type=int, default=DEFAULT_LEASE_SECONDS)
    parser.add_argument("--heartbeat-seconds", type=float, default=DEFAULT_HEARTBEAT_SECONDS)
    parser.add_argument("--python-exe", type=Path, default=Path(sys.executable))
    parser.add_argument("--worker-pool-script", type=Path, default=DEFAULT_WORKER_POOL_SCRIPT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--status-out", type=Path, help="Live status JSON written by this ledger client.")
    parser.add_argument("--once", action="store_true", help="Process one claimed batch, then exit.")
    parser.add_argument("--sleep-empty-seconds", type=float, default=300.0)
    parser.add_argument("--sleep-error-seconds", type=float, default=DEFAULT_SLEEP_ERROR_SECONDS)
    args, passthrough = parser.parse_known_args(argv)
    if args.batch_size < 1:
        parser.error("--batch-size must be positive")
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.bundle_size < 1:
        parser.error("--bundle-size must be positive")
    if args.lease_seconds < 60:
        parser.error("--lease-seconds must be at least 60")
    if args.heartbeat_seconds < 5:
        parser.error("--heartbeat-seconds must be at least 5")
    if args.sleep_error_seconds < 5:
        parser.error("--sleep-error-seconds must be at least 5")
    if args.status_out is None:
        args.status_out = Path(args.output_dir) / DEFAULT_STATUS_NAME
    if passthrough and passthrough[0] == "--":
        passthrough = passthrough[1:]
    return args, passthrough


def main(argv: Iterable[str] | None = None) -> int:
    """Run claimed batches until no lane is available or `--once` completes."""

    args, passthrough = parse_args(argv)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    exit_code = 0
    write_client_status(args, status="boot")
    while True:
        try:
            lanes = claim_lanes(args)
        except RuntimeError as exc:
            write_client_status(args, status="coordinator_error", error=str(exc))
            print(str(exc), file=sys.stderr, flush=True)
            if args.once:
                return 2
            time.sleep(args.sleep_error_seconds)
            continue
        if not lanes:
            write_client_status(args, status="idle_no_claim")
            print("No claimable lanes returned by coordinator.", flush=True)
            if args.once:
                return exit_code
            time.sleep(args.sleep_empty_seconds)
            continue
        write_client_status(args, status="claimed", lanes=lanes)
        print(f"Claimed {len(lanes)} lanes: {', '.join(lanes[:8])}{' ...' if len(lanes) > 8 else ''}", flush=True)
        run_lanes = [lane for lane in lanes if not valid_local_zip(Path(args.output_dir), lane)]
        reported_lanes: set[str] = set()
        try:
            report_available_results(args, lanes, reported_lanes)
        except RuntimeError as exc:
            print(str(exc), file=sys.stderr, flush=True)
        batch_code = 0
        if run_lanes:
            batch_code = run_claimed_batch(
                args,
                run_lanes,
                passthrough,
                all_claimed_lanes=lanes,
                reported_lanes=reported_lanes,
            )
        else:
            write_client_status(args, status="reporting_existing", lanes=lanes)
        try:
            report_results_until_success(args, lanes, batch_code=batch_code)
        except RuntimeError as exc:
            if args.once:
                return batch_code or 2
        exit_code = exit_code or batch_code
        if args.once:
            return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
