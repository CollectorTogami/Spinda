#!/usr/bin/env python3
"""Read-only independent watcher for Phase 3 Spinda production.

The watcher is deliberately outside the command center and worker pool. It does
not kill, restart, delete, decompress, or validate ZIP contents. It compares
three cheap views of production health:

- output folder filenames and mtimes
- worker-pool status JSON
- host process list, plus optional command-center API status

It writes its own small status JSON and event JSONL so a stalled browser,
command center, or worker pool can still leave a visible warning trail.
"""

from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from typing import Any, Iterable
from urllib.error import URLError
from urllib.request import Request, urlopen


# Use absolute(), not resolve(), so Windows junctions/symlinks do not push
# default output paths into a vendor/source mirror instead of this workspace.
ROOT = Path(__file__).absolute().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "Phase3SpindaBlocks"
DEFAULT_POOL_STATUS = DEFAULT_OUTPUT_DIR / "_native_phase3_worker_pool_status.json"
DEFAULT_POOL_CONTROL = DEFAULT_OUTPUT_DIR / "_native_phase3_worker_pool_control.json"
DEFAULT_WATCHER_STATUS = DEFAULT_OUTPUT_DIR / "_phase3_independent_watcher_status.json"
DEFAULT_WATCHER_EVENTS = DEFAULT_OUTPUT_DIR / "_phase3_independent_watcher_events.jsonl"
DEFAULT_COMMAND_CENTER_URL = "http://127.0.0.1:235/api/status"
DEFAULT_TARGET_LANES = 0xFFFE
SPINDAS_PER_LANE = 0x10000
ZIP_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.spinda80\.zip$")
TMP_RE = re.compile(r"^0x[0-9A-Fa-f]{4}\.spinda80\.zip\..*\.tmp$")
DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS = 300.0
DEFAULT_COMMAND_CENTER_CHECK_INTERVAL_SECONDS = 300.0


def now_local() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S %Z", time.localtime())


def read_json(path: Path) -> dict[str, Any] | None:
    """Read JSON status/control files, tolerating PowerShell UTF-8 BOMs."""

    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "path": str(path)}


def _pool_control_path(pool_status_path: Path) -> Path:
    """Return the worker-pool control path beside the status JSON."""

    return pool_status_path.with_name("_native_phase3_worker_pool_control.json")


def _control_requests_idle(control: dict[str, Any] | None) -> bool:
    """Return true when the operator intentionally stopped workers.

    The watcher normally treats missing pool status and zero workers as serious
    during production. After a command-center killswitch or manual reset,
    however, the control file is the authoritative sign that idle is expected.
    """

    if not isinstance(control, dict) or control.get("error"):
        return False
    desired = control.get("desired_workers")
    try:
        desired_workers = int(desired)
    except (TypeError, ValueError):
        return False
    return desired_workers <= 0 and bool(control.get("shutdown"))


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def add_sample(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    bucket = samples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(value)


def scan_phase3_outputs(
    output_dir: Path,
    *,
    target_lanes: int = DEFAULT_TARGET_LANES,
    sample_limit: int = 12,
    tiny_zip_bytes: int = 1024,
    stale_tmp_seconds: float = 1800.0,
    now: float | None = None,
) -> dict[str, Any]:
    """Scan final Phase 3 outputs by filename and size only."""

    now = time.time() if now is None else now
    seen = bytearray(0x10000)
    complete_lanes = 0
    zip_files = 0
    zero_size_zips = 0
    tiny_zips = 0
    tmp_files = 0
    stale_tmp_files = 0
    bad_names = 0
    duplicate_lanes = 0
    last_good_lane: int | None = None
    newest_zip_mtime: float | None = None
    newest_zip_name: str | None = None
    samples: dict[str, list[str]] = {}

    try:
        with os.scandir(output_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                name = entry.name
                zip_match = ZIP_RE.match(name)
                try:
                    stat = entry.stat()
                except OSError:
                    stat = None

                if zip_match:
                    zip_files += 1
                    lane = int(zip_match.group(1), 16)
                    size = stat.st_size if stat else 0
                    mtime = stat.st_mtime if stat else None
                    if size <= 0:
                        zero_size_zips += 1
                        add_sample(samples, "zero_size_zips", name, sample_limit)
                    elif size < tiny_zip_bytes:
                        tiny_zips += 1
                        add_sample(samples, "tiny_zips", f"{name} ({size} bytes)", sample_limit)
                    elif seen[lane]:
                        duplicate_lanes += 1
                        add_sample(samples, "duplicate_lanes", name, sample_limit)
                    else:
                        seen[lane] = 1
                        complete_lanes += 1
                        last_good_lane = lane if last_good_lane is None else max(last_good_lane, lane)
                        if mtime is not None and (newest_zip_mtime is None or mtime > newest_zip_mtime):
                            newest_zip_mtime = mtime
                            newest_zip_name = name
                    continue

                if name.endswith(".spinda80.zip") or ".spinda80.zip." in name:
                    if TMP_RE.match(name):
                        tmp_files += 1
                        age = (now - stat.st_mtime) if stat else math.inf
                        if age >= stale_tmp_seconds:
                            stale_tmp_files += 1
                            add_sample(samples, "stale_tmp_files", name, sample_limit)
                        else:
                            add_sample(samples, "tmp_files", name, sample_limit)
                    else:
                        bad_names += 1
                        add_sample(samples, "bad_names", name, sample_limit)
    except FileNotFoundError:
        add_sample(samples, "folder_errors", f"missing folder: {output_dir}", sample_limit)
    except OSError as exc:
        add_sample(samples, "folder_errors", str(exc), sample_limit)

    newest_age = (now - newest_zip_mtime) if newest_zip_mtime is not None else None
    bad_artifacts = zero_size_zips + tiny_zips + tmp_files + bad_names + duplicate_lanes
    return {
        "folder": str(output_dir),
        "target_lanes": target_lanes,
        "complete_lanes": complete_lanes,
        "completed_spindas": complete_lanes * SPINDAS_PER_LANE,
        "zip_files": zip_files,
        "missing_lanes": max(0, target_lanes - complete_lanes),
        "zero_size_zips": zero_size_zips,
        "tiny_zips": tiny_zips,
        "tmp_files": tmp_files,
        "stale_tmp_files": stale_tmp_files,
        "bad_names": bad_names,
        "duplicate_lanes": duplicate_lanes,
        "bad_artifacts": bad_artifacts,
        "last_good_lane": f"0x{last_good_lane:04X}" if last_good_lane is not None else None,
        "newest_zip_name": newest_zip_name,
        "newest_zip_mtime_unix": newest_zip_mtime,
        "newest_zip_age_seconds": newest_age,
        "samples": samples,
    }


def fetch_command_center_status(url: str | None, *, timeout_seconds: float = 2.0) -> dict[str, Any]:
    if not url:
        return {"enabled": False, "reachable": False}
    try:
        request = Request(url, headers={"User-Agent": "phase3-independent-watcher/1"})
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310 - local operator URL by default.
            body = response.read(2 * 1024 * 1024)
        payload = json.loads(body.decode("utf-8"))
        return {"enabled": True, "reachable": True, "url": url, "payload": payload}
    except (OSError, URLError, json.JSONDecodeError, TimeoutError) as exc:
        return {"enabled": True, "reachable": False, "url": url, "error": str(exc)}


def _process_rows_from_psutil() -> list[dict[str, Any]] | None:
    try:
        import psutil  # type: ignore[import-not-found]
    except Exception:
        return None

    rows: list[dict[str, Any]] = []
    for process in psutil.process_iter(["pid", "name"]):
        try:
            info = process.info
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
        name = str(info.get("name") or "")
        lower_name = name.lower()
        if lower_name != "mgba-spinda-phase3.exe" and not lower_name.startswith("python"):
            continue
        cmdline = ""
        if lower_name.startswith("python"):
            try:
                cmdline = " ".join(str(part) for part in process.cmdline())
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
            if (
                "native_phase3_worker_pool.py" not in cmdline
                and "phase3_command_center_web.py" not in cmdline
                and "phase3_ledger_worker_client.py" not in cmdline
            ):
                continue
        rows.append({"pid": int(info.get("pid") or 0), "name": name, "command_line": cmdline})
    return rows


def _process_rows_from_powershell() -> list[dict[str, Any]]:
    command = (
        "$rows = Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'mgba-spinda-phase3.exe' -or "
        "(($_.Name -like 'python*') -and "
        "($_.CommandLine -like '*native_phase3_worker_pool.py*' -or "
        "$_.CommandLine -like '*phase3_command_center_web.py*' -or "
        "$_.CommandLine -like '*phase3_ledger_worker_client.py*')) } | "
        "Select-Object ProcessId,Name,CommandLine; "
        "$rows | ConvertTo-Json -Compress"
    )
    completed = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", command],
        check=False,
        capture_output=True,
        text=True,
        timeout=8,
    )
    if completed.returncode != 0 or not completed.stdout.strip():
        return []
    parsed = json.loads(completed.stdout)
    if isinstance(parsed, dict):
        parsed = [parsed]
    rows: list[dict[str, Any]] = []
    for item in parsed:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "pid": int(item.get("ProcessId") or 0),
                "name": str(item.get("Name") or ""),
                "command_line": str(item.get("CommandLine") or ""),
            }
        )
    return rows


def host_phase3_processes() -> dict[str, Any]:
    """Return relevant host process rows without controlling them."""

    source = "psutil"
    rows = _process_rows_from_psutil()
    if rows is None:
        source = "powershell"
        try:
            rows = _process_rows_from_powershell()
        except Exception as exc:  # noqa: BLE001 - watcher should report process query failures.
            return {"source": source, "error": str(exc), "rows": [], "pids": []}

    phase3_workers: list[dict[str, Any]] = []
    worker_pools: list[dict[str, Any]] = []
    command_centers: list[dict[str, Any]] = []
    ledger_clients: list[dict[str, Any]] = []
    for row in rows:
        name = str(row.get("name") or "").lower()
        command_line = str(row.get("command_line") or "").lower()
        is_python = name.startswith("python")
        if name == "mgba-spinda-phase3.exe":
            phase3_workers.append(row)
        if is_python and "native_phase3_worker_pool.py" in command_line:
            worker_pools.append(row)
        if is_python and "phase3_ledger_worker_client.py" in command_line:
            ledger_clients.append(row)
        if is_python and "phase3_command_center_web.py" in command_line:
            command_centers.append(row)

    return {
        "source": source,
        "rows": phase3_workers + worker_pools + ledger_clients + command_centers,
        "phase3_workers": phase3_workers,
        "worker_pools": worker_pools,
        "ledger_clients": ledger_clients,
        "command_centers": command_centers,
        "pids": sorted({int(row["pid"]) for row in phase3_workers if int(row.get("pid") or 0) > 0}),
        "worker_pool_pids": sorted({int(row["pid"]) for row in worker_pools if int(row.get("pid") or 0) > 0}),
        "ledger_client_pids": sorted({int(row["pid"]) for row in ledger_clients if int(row.get("pid") or 0) > 0}),
        "command_center_pids": sorted({int(row["pid"]) for row in command_centers if int(row.get("pid") or 0) > 0}),
    }


def disk_snapshot(path: Path) -> dict[str, Any]:
    try:
        usage = shutil.disk_usage(path)
    except OSError as exc:
        return {"path": str(path), "error": str(exc)}
    return {
        "path": str(path),
        "total_bytes": int(usage.total),
        "used_bytes": int(usage.used),
        "free_bytes": int(usage.free),
        "used_percent": round((usage.used / usage.total) * 100.0, 3) if usage.total else None,
    }


class WatcherRuntimeCache:
    """Throttle process/API checks when watcher interval is set very low.

    Folder scan and pool-status reads are cheap enough to perform every sample.
    Host process enumeration and command-center HTTP checks are context checks,
    so they can be reused for a few minutes without reducing worker safety.
    """

    def __init__(
        self,
        *,
        process_check_interval_seconds: float = DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS,
        command_center_check_interval_seconds: float = DEFAULT_COMMAND_CENTER_CHECK_INTERVAL_SECONDS,
    ) -> None:
        self.process_check_interval_seconds = max(1.0, process_check_interval_seconds)
        self.command_center_check_interval_seconds = max(1.0, command_center_check_interval_seconds)
        self._process_at = 0.0
        self._process_snapshot: dict[str, Any] | None = None
        self._command_center_at = 0.0
        self._command_center_snapshot: dict[str, Any] | None = None
        self._command_center_key: tuple[str | None, bool] | None = None

    def process_snapshot(self) -> dict[str, Any]:
        now = time.monotonic()
        if (
            self._process_snapshot is None
            or now - self._process_at >= self.process_check_interval_seconds
        ):
            self._process_snapshot = host_phase3_processes()
            self._process_at = now
        return self._process_snapshot

    def command_center_snapshot(self, url: str | None, disabled: bool) -> dict[str, Any]:
        key = (url, disabled)
        if disabled or not url:
            self._command_center_key = key
            self._command_center_snapshot = {"enabled": False, "reachable": False}
            self._command_center_at = time.monotonic()
            return self._command_center_snapshot
        now = time.monotonic()
        if (
            self._command_center_snapshot is None
            or self._command_center_key != key
            or now - self._command_center_at >= self.command_center_check_interval_seconds
        ):
            self._command_center_snapshot = fetch_command_center_status(url)
            self._command_center_key = key
            self._command_center_at = now
        return self._command_center_snapshot


def add_check(checks: list[dict[str, Any]], severity: str, code: str, message: str, **extra: Any) -> None:
    checks.append({"severity": severity, "code": code, "message": message, **extra})


def max_severity(checks: list[dict[str, Any]]) -> str:
    rank = {"ok": 0, "info": 0, "warning": 1, "critical": 2}
    worst = "ok"
    for check in checks:
        severity = str(check.get("severity") or "ok")
        if rank.get(severity, 0) > rank.get(worst, 0):
            worst = severity
    return worst


def _status_age(pool_status: dict[str, Any] | None, now: float) -> float | None:
    if not pool_status or "time_unix" not in pool_status:
        return None
    try:
        return max(0.0, now - float(pool_status["time_unix"]))
    except (TypeError, ValueError):
        return None


def _reported_worker_pids(pool_status: dict[str, Any] | None) -> list[int]:
    pids: list[int] = []
    for worker in (pool_status or {}).get("running") or []:
        try:
            pid = int(worker.get("pid") or 0)
        except (AttributeError, TypeError, ValueError):
            continue
        if pid > 0:
            pids.append(pid)
    return sorted(set(pids))


def _running_lane_warnings(
    pool_status: dict[str, Any] | None,
    *,
    worker_stall_warning_seconds: float,
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    for worker in (pool_status or {}).get("running") or []:
        try:
            outer = float(worker.get("current_outer_elapsed_seconds") or 0.0)
        except (TypeError, ValueError, AttributeError):
            outer = 0.0
        if outer < worker_stall_warning_seconds:
            continue
        warnings.append(
            {
                "severity": "warning",
                "code": "worker_lane_slow",
                "message": (
                    f"worker {worker.get('slot_id', '?')} lane "
                    f"{worker.get('lane_id', 'unknown')} has run {outer:.1f}s"
                ),
                "slot_id": worker.get("slot_id"),
                "pid": worker.get("pid"),
                "lane_id": worker.get("lane_id"),
                "elapsed_seconds": outer,
            }
        )
    return warnings


def _oldest_running_lane_elapsed(pool_status: dict[str, Any] | None) -> float | None:
    """Return the longest active lane timer reported by the worker pool.

    ZIP age alone can be misleading right after workers are relaunched: the
    newest completed ZIP may be old even though the current lanes have only
    been running for a few minutes. Using the active lane timer keeps the
    watcher from turning a healthy restart into a false stall alert.
    """

    elapsed_values: list[float] = []
    for worker in (pool_status or {}).get("running") or []:
        try:
            elapsed = float(worker.get("current_outer_elapsed_seconds") or worker.get("elapsed_seconds") or 0.0)
        except (AttributeError, TypeError, ValueError):
            continue
        if elapsed > 0.0:
            elapsed_values.append(elapsed)
    return max(elapsed_values) if elapsed_values else None


def build_watcher_payload(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pool_status_path: Path = DEFAULT_POOL_STATUS,
    command_center_url: str | None = DEFAULT_COMMAND_CENTER_URL,
    target_lanes: int = DEFAULT_TARGET_LANES,
    expected_running: bool = True,
    now: float | None = None,
    process_snapshot: dict[str, Any] | None = None,
    command_center_snapshot: dict[str, Any] | None = None,
    no_zip_warning_seconds: float = 3600.0,
    no_zip_critical_seconds: float = 7200.0,
    pool_stale_warning_seconds: float = 120.0,
    pool_stale_critical_seconds: float = 600.0,
    worker_stall_warning_seconds: float = 7200.0,
    disk_warning_bytes: int = 100 * 1024**3,
    disk_critical_bytes: int = 50 * 1024**3,
    tiny_zip_bytes: int = 1024,
    stale_tmp_seconds: float = 1800.0,
    sample_limit: int = 12,
) -> dict[str, Any]:
    now = time.time() if now is None else now
    folder = scan_phase3_outputs(
        output_dir,
        target_lanes=target_lanes,
        sample_limit=sample_limit,
        tiny_zip_bytes=tiny_zip_bytes,
        stale_tmp_seconds=stale_tmp_seconds,
        now=now,
    )
    pool_status = read_json(pool_status_path)
    pool_control_path = _pool_control_path(pool_status_path)
    pool_control = read_json(pool_control_path)
    idle_requested = _control_requests_idle(pool_control)
    should_expect_running = expected_running and not idle_requested
    command_center = (
        command_center_snapshot
        if command_center_snapshot is not None
        else fetch_command_center_status(command_center_url)
    )
    processes = process_snapshot if process_snapshot is not None else host_phase3_processes()
    disk = disk_snapshot(output_dir if output_dir.exists() else output_dir.parent)
    checks: list[dict[str, Any]] = []

    if "folder_errors" in folder.get("samples", {}):
        add_check(checks, "critical", "output_folder_error", "Phase 3 output folder missing or unreadable")

    status_age = _status_age(pool_status, now)
    if pool_status is None:
        if not idle_requested:
            add_check(checks, "critical" if expected_running else "warning", "pool_status_missing", "worker-pool status JSON missing")
    elif pool_status.get("error"):
        add_check(checks, "critical", "pool_status_unreadable", str(pool_status.get("error")))
    elif idle_requested:
        pass
    elif status_age is None:
        add_check(checks, "warning", "pool_status_no_timestamp", "worker-pool status has no time_unix")
    elif status_age >= pool_stale_critical_seconds:
        add_check(checks, "critical", "pool_status_stale", f"worker-pool status age {status_age:.1f}s", age_seconds=status_age)
    elif status_age >= pool_stale_warning_seconds:
        add_check(checks, "warning", "pool_status_stale", f"worker-pool status age {status_age:.1f}s", age_seconds=status_age)

    if command_center.get("enabled") and not command_center.get("reachable"):
        add_check(checks, "warning", "command_center_unreachable", str(command_center.get("error") or "command center API unreachable"))
    if command_center.get("reachable"):
        cc_payload = command_center.get("payload") or {}
        cc_progress = cc_payload.get("progress") or {}
        cc_complete = cc_progress.get("local_complete_lanes", cc_progress.get("complete_lanes"))
        if isinstance(cc_complete, int) and cc_complete != folder["complete_lanes"]:
            add_check(
                checks,
                "warning",
                "command_center_folder_mismatch",
                f"command center complete_lanes={cc_complete}, folder scan={folder['complete_lanes']}",
            )

    reported_pids = _reported_worker_pids(pool_status)
    os_worker_pids = set(int(pid) for pid in processes.get("pids", []))
    missing_pids = [pid for pid in reported_pids if pid not in os_worker_pids]
    unreported_pids = sorted(pid for pid in os_worker_pids if pid not in set(reported_pids))
    if idle_requested:
        reported_pids = []
        missing_pids = []
    if missing_pids:
        add_check(checks, "critical", "reported_worker_pid_missing", "pool reports worker PID not found in OS process list", pids=missing_pids)
    if unreported_pids and should_expect_running:
        add_check(checks, "warning", "unreported_worker_process", "OS has Phase 3 worker processes not listed in pool status", pids=unreported_pids)

    running_count = 0 if idle_requested else int(((pool_status or {}).get("counts") or {}).get("running") or len(reported_pids))
    if should_expect_running and running_count <= 0 and not os_worker_pids:
        add_check(checks, "critical", "no_running_workers", "no running Phase 3 workers found")

    newest_age = folder.get("newest_zip_age_seconds")
    oldest_running_elapsed = _oldest_running_lane_elapsed(pool_status)
    zip_stall_check_ready = oldest_running_elapsed is None or oldest_running_elapsed >= no_zip_warning_seconds
    if should_expect_running:
        if newest_age is None and zip_stall_check_ready:
            add_check(checks, "warning", "no_completed_zip_yet", "no completed Phase 3 ZIP found")
        elif zip_stall_check_ready and newest_age >= no_zip_critical_seconds:
            add_check(checks, "critical", "zip_output_stalled", f"newest completed ZIP age {newest_age:.1f}s", age_seconds=newest_age)
        elif zip_stall_check_ready and newest_age >= no_zip_warning_seconds:
            add_check(checks, "warning", "zip_output_stalled", f"newest completed ZIP age {newest_age:.1f}s", age_seconds=newest_age)

    if folder["zero_size_zips"]:
        add_check(checks, "critical", "zero_size_zip", "zero-size final ZIP files present", count=folder["zero_size_zips"])
    if folder["tiny_zips"]:
        add_check(checks, "warning", "tiny_zip", "suspiciously small final ZIP files present", count=folder["tiny_zips"])
    if folder["bad_names"]:
        add_check(checks, "warning", "bad_zip_name", "bad Phase 3 ZIP-like names present", count=folder["bad_names"])
    if folder["duplicate_lanes"]:
        add_check(checks, "warning", "duplicate_lane_name", "duplicate lane names detected by case-insensitive scan", count=folder["duplicate_lanes"])
    if folder["stale_tmp_files"]:
        add_check(checks, "warning", "stale_tmp_file", "old temp ZIP files present", count=folder["stale_tmp_files"])

    free_bytes = disk.get("free_bytes")
    if isinstance(free_bytes, int):
        if free_bytes <= disk_critical_bytes:
            add_check(checks, "critical", "disk_free_low", f"disk free below critical threshold: {free_bytes} bytes", free_bytes=free_bytes)
        elif free_bytes <= disk_warning_bytes:
            add_check(checks, "warning", "disk_free_low", f"disk free below warning threshold: {free_bytes} bytes", free_bytes=free_bytes)

    checks.extend(_running_lane_warnings(pool_status, worker_stall_warning_seconds=worker_stall_warning_seconds))

    severity = max_severity(checks)
    return {
        "schema_version": 1,
        "generated_at_unix": now,
        "generated_at": now_local(),
        "status": severity,
        "summary": {
            "status": severity,
            "complete_lanes": folder["complete_lanes"],
            "target_lanes": folder["target_lanes"],
            "completed_spindas": folder["completed_spindas"],
            "running_workers_reported": running_count,
            "phase3_worker_processes": len(os_worker_pids),
            "pool_status_age_seconds": status_age,
            "oldest_running_lane_elapsed_seconds": oldest_running_elapsed,
            "command_center_reachable": bool(command_center.get("reachable")),
            "newest_zip_age_seconds": newest_age,
            "disk_free_bytes": free_bytes,
            "check_count": len(checks),
            "warning_count": sum(1 for check in checks if check.get("severity") == "warning"),
            "critical_count": sum(1 for check in checks if check.get("severity") == "critical"),
        },
        "checks": checks,
        "folder": folder,
        "pool": {
            "status_path": str(pool_status_path),
            "exists": pool_status_path.is_file(),
            "age_seconds": status_age,
            "counts": (pool_status or {}).get("counts") if isinstance(pool_status, dict) else None,
            "reported_worker_pids": reported_pids,
            "running_preview": (pool_status or {}).get("running", [])[:sample_limit] if isinstance(pool_status, dict) else [],
        },
        "control": {
            "path": str(pool_control_path),
            "exists": pool_control_path.is_file(),
            "idle_requested": idle_requested,
            "desired_workers": (pool_control or {}).get("desired_workers") if isinstance(pool_control, dict) else None,
            "shutdown": (pool_control or {}).get("shutdown") if isinstance(pool_control, dict) else None,
            "source": (pool_control or {}).get("source") if isinstance(pool_control, dict) else None,
        },
        "command_center": {
            "enabled": command_center.get("enabled", False),
            "reachable": command_center.get("reachable", False),
            "url": command_center.get("url"),
            "error": command_center.get("error"),
        },
        "processes": processes,
        "disk": disk,
        "thresholds": {
            "no_zip_warning_seconds": no_zip_warning_seconds,
            "no_zip_critical_seconds": no_zip_critical_seconds,
            "pool_stale_warning_seconds": pool_stale_warning_seconds,
            "pool_stale_critical_seconds": pool_stale_critical_seconds,
            "worker_stall_warning_seconds": worker_stall_warning_seconds,
            "disk_warning_bytes": disk_warning_bytes,
            "disk_critical_bytes": disk_critical_bytes,
            "tiny_zip_bytes": tiny_zip_bytes,
            "stale_tmp_seconds": stale_tmp_seconds,
        },
    }


def status_signature(payload: dict[str, Any]) -> str:
    codes = sorted(str(check.get("code")) for check in payload.get("checks", []))
    return json.dumps([payload.get("status"), codes], separators=(",", ":"))


def append_event(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    event = {
        "time_unix": payload["generated_at_unix"],
        "time_local": payload["generated_at"],
        "status": payload["status"],
        "summary": payload["summary"],
        "checks": payload["checks"],
    }
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(event, sort_keys=True, separators=(",", ":")) + "\n")


def run_once(
    args: argparse.Namespace,
    previous_signature: str | None = None,
    runtime_cache: WatcherRuntimeCache | None = None,
) -> tuple[dict[str, Any], str]:
    process_snapshot = runtime_cache.process_snapshot() if runtime_cache is not None else None
    command_center_snapshot = (
        runtime_cache.command_center_snapshot(args.command_center_url, args.no_command_center_api)
        if runtime_cache is not None
        else None
    )
    payload = build_watcher_payload(
        output_dir=args.folder,
        pool_status_path=args.pool_status,
        command_center_url=None if args.no_command_center_api else args.command_center_url,
        process_snapshot=process_snapshot,
        command_center_snapshot=command_center_snapshot,
        target_lanes=args.target_lanes,
        expected_running=not args.allow_idle,
        no_zip_warning_seconds=args.no_zip_warning_seconds,
        no_zip_critical_seconds=args.no_zip_critical_seconds,
        pool_stale_warning_seconds=args.pool_stale_warning_seconds,
        pool_stale_critical_seconds=args.pool_stale_critical_seconds,
        worker_stall_warning_seconds=args.worker_stall_warning_seconds,
        disk_warning_bytes=args.disk_warning_gib * 1024**3,
        disk_critical_bytes=args.disk_critical_gib * 1024**3,
        tiny_zip_bytes=args.tiny_zip_bytes,
        stale_tmp_seconds=args.stale_tmp_seconds,
        sample_limit=args.sample_limit,
    )
    signature = status_signature(payload)
    write_json_atomic(args.status_out, payload)
    if args.log_every_sample or signature != previous_signature or payload["status"] != "ok":
        append_event(args.events_out, payload)
    if args.print_summary:
        summary = payload["summary"]
        print(
            f"{payload['status'].upper()} lanes={summary['complete_lanes']}/{summary['target_lanes']} "
            f"workers={summary['running_workers_reported']} os={summary['phase3_worker_processes']} "
            f"checks={summary['check_count']} status={args.status_out}"
        )
        for check in payload["checks"][: args.sample_limit]:
            print(f"  {check['severity'].upper()} {check['code']}: {check['message']}")
    return payload, signature


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a read-only independent watcher for Phase 3 Spinda production.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--folder", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pool-status", type=Path, default=DEFAULT_POOL_STATUS)
    parser.add_argument("--status-out", type=Path, default=DEFAULT_WATCHER_STATUS)
    parser.add_argument("--events-out", type=Path, default=DEFAULT_WATCHER_EVENTS)
    parser.add_argument("--command-center-url", default=DEFAULT_COMMAND_CENTER_URL)
    parser.add_argument("--no-command-center-api", action="store_true")
    parser.add_argument("--target-lanes", type=int, default=DEFAULT_TARGET_LANES)
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--process-check-interval-seconds", type=float, default=DEFAULT_PROCESS_CHECK_INTERVAL_SECONDS)
    parser.add_argument(
        "--command-center-check-interval-seconds",
        type=float,
        default=DEFAULT_COMMAND_CENTER_CHECK_INTERVAL_SECONDS,
    )
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--allow-idle", action="store_true", help="do not warn when no workers are running")
    parser.add_argument("--log-every-sample", action="store_true")
    parser.add_argument("--print-summary", action="store_true")
    parser.add_argument("--sample-limit", type=int, default=12)
    parser.add_argument("--no-zip-warning-seconds", type=float, default=3600.0)
    parser.add_argument("--no-zip-critical-seconds", type=float, default=7200.0)
    parser.add_argument("--pool-stale-warning-seconds", type=float, default=120.0)
    parser.add_argument("--pool-stale-critical-seconds", type=float, default=600.0)
    parser.add_argument("--worker-stall-warning-seconds", type=float, default=7200.0)
    parser.add_argument("--disk-warning-gib", type=int, default=100)
    parser.add_argument("--disk-critical-gib", type=int, default=50)
    parser.add_argument("--tiny-zip-bytes", type=int, default=1024)
    parser.add_argument("--stale-tmp-seconds", type=float, default=1800.0)
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    if args.interval_seconds <= 0:
        raise SystemExit("--interval-seconds must be positive")
    if args.sample_limit < 1:
        raise SystemExit("--sample-limit must be positive")
    if args.process_check_interval_seconds <= 0:
        raise SystemExit("--process-check-interval-seconds must be positive")
    if args.command_center_check_interval_seconds <= 0:
        raise SystemExit("--command-center-check-interval-seconds must be positive")

    previous = status_signature(read_json(args.status_out) or {}) if args.status_out.is_file() else None
    runtime_cache = WatcherRuntimeCache(
        process_check_interval_seconds=args.process_check_interval_seconds,
        command_center_check_interval_seconds=args.command_center_check_interval_seconds,
    )
    while True:
        payload, previous = run_once(args, previous, runtime_cache=runtime_cache)
        if args.once:
            return 2 if payload["status"] == "critical" else 1 if payload["status"] == "warning" else 0
        time.sleep(args.interval_seconds)


if __name__ == "__main__":
    raise SystemExit(main())
