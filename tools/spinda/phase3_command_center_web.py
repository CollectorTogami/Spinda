#!/usr/bin/env python3
"""Flask command center for Phase 3 Spinda production.

This dashboard is designed for headless CLI worker runs. It reads final ZIP
filenames for cheap status, exposes worker-pool controls, and avoids opening
ZIP contents. Deep validation remains in the separate ZIP and PKHeX auditors.
"""

from __future__ import annotations

import argparse
import ctypes
import json
import math
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any, Iterable
from urllib import error as urlerror
from urllib.parse import urlsplit
from urllib import request as urlrequest

try:
    from flask import Flask, Response, jsonify, render_template_string, request
except ImportError as exc:  # pragma: no cover - operator environment check.
    raise SystemExit(
        "Flask is required for the Phase 3 command center. Install it with:\n"
        "python -m pip install Flask"
    ) from exc


def _env_bool(name: str, default: bool = False) -> bool:
    """Read simple operator booleans from environment."""

    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    """Read integer environment option with safe fallback."""

    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _normalize_role(value: str | None) -> str:
    """Return command-center network role."""

    role = (value or "coordinator").strip().lower()
    if role not in {"coordinator", "subordinate"}:
        raise ValueError("role must be coordinator or subordinate")
    return role


def _normalize_scheme(value: str | None) -> str:
    """Return supported URL scheme for coordinator traffic."""

    scheme = (value or "http").strip().lower().removesuffix("://").rstrip(":")
    if scheme not in {"http", "https"}:
        raise ValueError("scheme must be http or https")
    return scheme


def _split_urlish_host(value: str, *, default_scheme: str) -> tuple[str, str | None, int | None]:
    """Parse host boxes that may include scheme, slash, or port.

    Operators sometimes paste `https://host/` into the host field. Stripping the
    slash matters: `host/:443` sends the port as a path and breaks heartbeat.
    """

    raw = value.strip()
    if not raw:
        return "", None, None
    if "://" in raw:
        parsed = urlsplit(raw)
        return (parsed.hostname or "").strip(), parsed.scheme or default_scheme, parsed.port
    host = raw.split("/", 1)[0].strip()
    if host.count(":") == 1:
        name, port_text = host.rsplit(":", 1)
        if port_text.isdigit():
            return name.strip(), None, int(port_text)
    return host, None, None


# Early role decision. CLI flags can override this later, but the module has a
# clear default as soon as it loads.
ROOT = Path(__file__).absolute().parents[2]
COMMAND_CENTER_ROLE = _normalize_role(os.environ.get("SPINDA_PHASE3_COMMAND_CENTER_ROLE", "coordinator"))
POOL_STATUS_NAME = "_native_phase3_worker_pool_status.json"
POOL_CONTROL_NAME = "_native_phase3_worker_pool_control.json"
WATCHER_STATUS_NAME = "_phase3_independent_watcher_status.json"
COORDINATION_SETTINGS_NAME = "_phase3_command_center_network.json"
LEDGER_NAME = "_phase3_lane_ledger.json"
LEDGER_CLIENT_STATUS_NAME = "_phase3_ledger_worker_client_status.json"


DEFAULT_OUTPUT_DIR = Path(os.environ.get("SPINDA_PHASE3_OUTPUT_DIR", str(ROOT / "Phase3SpindaBlocks")))
DEFAULT_POOL_STATUS_PATH = DEFAULT_OUTPUT_DIR / POOL_STATUS_NAME
DEFAULT_POOL_CONTROL_PATH = DEFAULT_OUTPUT_DIR / POOL_CONTROL_NAME
DEFAULT_WATCHER_STATUS_PATH = DEFAULT_OUTPUT_DIR / WATCHER_STATUS_NAME
DEFAULT_COORDINATION_SETTINGS_PATH = DEFAULT_OUTPUT_DIR / COORDINATION_SETTINGS_NAME
DEFAULT_LEDGER_PATH = DEFAULT_OUTPUT_DIR / LEDGER_NAME
DEFAULT_WORKER_POOL_SCRIPT = ROOT / "tools" / "spinda" / "native_phase3_worker_pool.py"
DEFAULT_LEDGER_WORKER_CLIENT_SCRIPT = ROOT / "tools" / "spinda" / "phase3_ledger_worker_client.py"
DEFAULT_LEDGER_CLIENT_STATUS_PATH = DEFAULT_OUTPUT_DIR / LEDGER_CLIENT_STATUS_NAME
DEFAULT_PYTHON = Path(sys.executable)
DEFAULT_LANES = "0x0001-0xFFFE"
DEFAULT_CACHE_DIR = DEFAULT_OUTPUT_DIR / "_cache"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 235
DEFAULT_COORDINATION_ONLINE = _env_bool("SPINDA_PHASE3_COORDINATION_ONLINE", False)
DEFAULT_COORDINATION_PRIMARY_SCHEME = _normalize_scheme(os.environ.get("SPINDA_PHASE3_PRIMARY_SCHEME", "http"))
DEFAULT_COORDINATION_PRIMARY_HOST = os.environ.get("SPINDA_PHASE3_PRIMARY_HOST", "127.0.0.1")
DEFAULT_COORDINATION_PRIMARY_PORT = _env_int("SPINDA_PHASE3_PRIMARY_PORT", DEFAULT_PORT)
DEFAULT_COORDINATION_ADVERTISE_SCHEME = _normalize_scheme(os.environ.get("SPINDA_PHASE3_ADVERTISE_SCHEME", "http"))
DEFAULT_COORDINATION_ADVERTISE_HOST = os.environ.get("SPINDA_PHASE3_ADVERTISE_HOST", "")
DEFAULT_COORDINATION_ADVERTISE_PORT = _env_int("SPINDA_PHASE3_ADVERTISE_PORT", 0)
DEFAULT_COORDINATION_HEARTBEAT_SECONDS = 60.0
DEFAULT_TARGET_LANES = 0xFFFE
SPINDAS_PER_LANE = 0x10000
DEFAULT_CONTROL_MAX_WORKERS = 64
DEFAULT_BUNDLE_SIZE = 2
DEFAULT_ZIP_METHOD = "deflate"
DEFAULT_STATUS_WRITE_SECONDS = 10.0
DEFAULT_SAMPLE_LIMIT = 16
DEFAULT_SAMPLE_INTERVAL_SECONDS = 5.0
DEFAULT_ZIP_SCAN_INTERVAL_SECONDS = 60.0
DEFAULT_HOST_RESOURCE_INTERVAL_SECONDS = 15.0
DEFAULT_LEDGER_LEASE_SECONDS = 6 * 3600
DEFAULT_LEDGER_MAX_CLAIM_COUNT = 512
DEFAULT_LEDGER_CLIENT_STALE_SECONDS = 300.0
MIN_FINAL_ZIP_BYTES = 1024
STALE_POOL_SECONDS = 120.0
SLOW_WORKER_MULTIPLIER = 2.25
ZIP_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.spinda80\.zip$")
TMP_RE = re.compile(r"^0x[0-9A-Fa-f]{4}\.spinda80\.zip\..*\.tmp$")
_CPU_SAMPLE_LOCK = Lock()
_CPU_SAMPLE: tuple[int, int] | None = None


def lane_ids_to_range_strings(lane_ids: Iterable[int]) -> list[str]:
    """Return compact `0x0001-0x0003` ranges for 16-bit lane IDs."""

    ranges: list[str] = []
    sorted_lanes = sorted({int(lane) for lane in lane_ids if 0 <= int(lane) <= 0xFFFF})
    if not sorted_lanes:
        return ranges
    start = previous = sorted_lanes[0]
    for lane in sorted_lanes[1:]:
        if lane == previous + 1:
            previous = lane
            continue
        ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
        start = previous = lane
    ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
    return ranges


@dataclass(frozen=True)
class Phase3ZipAudit:
    """Fast filename-only audit of Phase 3 output folder."""

    folder: str
    target_lanes: int
    complete_lanes: int
    zip_files: int
    missing_lanes: int
    bad_names: int
    zero_size_zips: int
    tiny_zips: int
    tmp_files: int
    duplicate_lanes: int
    bad_zip_artifacts: int
    last_good_lane: str | None
    complete_lane_ranges: list[str]
    samples: dict[str, list[str]]

    @property
    def progress_percent(self) -> float:
        """Return total completion percent."""

        if self.target_lanes <= 0:
            return 0.0
        return round((self.complete_lanes / self.target_lanes) * 100.0, 6)


@dataclass
class RateSnapshot:
    """Rate estimate from command-center boot to current sample."""

    lanes_per_hour: float | None
    elapsed_seconds: float
    eta_seconds: float | None
    finish_time_local: str | None
    completed_since_boot: int
    first_complete_lanes: int


@dataclass(frozen=True)
class CoordinationConfig:
    """Multi-device command-center network settings.

    `coordinator` owns lane assignment. In online mode every worker launcher,
    including workers on the coordinator PC, should claim lanes through that
    ledger instead of picking from local filenames.
    """

    role: str = COMMAND_CENTER_ROLE
    online: bool = DEFAULT_COORDINATION_ONLINE
    primary_scheme: str = DEFAULT_COORDINATION_PRIMARY_SCHEME
    primary_host: str = DEFAULT_COORDINATION_PRIMARY_HOST
    primary_port: int = DEFAULT_COORDINATION_PRIMARY_PORT
    advertise_scheme: str = DEFAULT_COORDINATION_ADVERTISE_SCHEME
    advertise_host: str = DEFAULT_COORDINATION_ADVERTISE_HOST
    advertise_port: int = DEFAULT_COORDINATION_ADVERTISE_PORT
    heartbeat_seconds: float = DEFAULT_COORDINATION_HEARTBEAT_SECONDS
    device_id: str = socket.gethostname()

    @property
    def primary_url(self) -> str:
        return f"{self.primary_scheme}://{self.primary_host}:{self.primary_port}"

    @property
    def advertise_url(self) -> str | None:
        if not self.advertise_host:
            return None
        return f"{self.advertise_scheme}://{self.advertise_host}:{self.advertise_port}"

    def snapshot(self, registered_devices: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """Return JSON-safe network role/status."""

        return {
            "role": self.role,
            "online": self.online,
            "device_id": self.device_id,
            "primary_scheme": self.primary_scheme,
            "primary_host": self.primary_host,
            "primary_port": self.primary_port,
            "primary_url": self.primary_url,
            "advertise_scheme": self.advertise_scheme,
            "advertise_host": self.advertise_host or None,
            "advertise_port": self.advertise_port,
            "advertise_url": self.advertise_url,
            "heartbeat_seconds": self.heartbeat_seconds,
            "registered_devices": registered_devices or [],
            "registered_device_count": len(registered_devices or []),
            "note": (
                "coordinator accepts subordinate heartbeats"
                if self.role == "coordinator"
                else "subordinate launches local workers only after coordinator ledger claims"
            ),
        }


class SubordinateRegistry:
    """In-memory list of subordinate command centers seen by coordinator."""

    def __init__(self) -> None:
        self._lock = Lock()
        self._devices: dict[str, dict[str, Any]] = {}

    def update(self, payload: dict[str, Any], *, remote_addr: str | None) -> dict[str, Any]:
        """Insert/update one subordinate heartbeat."""

        device_id = str(payload.get("device_id") or payload.get("hostname") or remote_addr or "unknown")
        now = time.time()
        record = {
            "device_id": device_id,
            "remote_addr": remote_addr,
            "advertise_url": payload.get("advertise_url"),
            "role": payload.get("role", "subordinate"),
            "workers": payload.get("workers") if isinstance(payload.get("workers"), dict) else {},
            "progress": payload.get("progress") if isinstance(payload.get("progress"), dict) else {},
            "ledger": payload.get("ledger") if isinstance(payload.get("ledger"), dict) else {},
            "ledger_client": payload.get("ledger_client") if isinstance(payload.get("ledger_client"), dict) else {},
            "health": payload.get("health") if isinstance(payload.get("health"), dict) else {},
            "last_seen_unix": now,
            "last_seen_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        with self._lock:
            self._devices[device_id] = record
        return record

    def snapshot(self) -> list[dict[str, Any]]:
        """Return devices ordered by most recent heartbeat."""

        now = time.time()
        with self._lock:
            records = [dict(record) for record in self._devices.values()]
        for record in records:
            record["age_seconds"] = max(0.0, now - float(record.get("last_seen_unix") or now))
        return sorted(records, key=lambda item: item.get("last_seen_unix") or 0, reverse=True)


class SubordinateHeartbeatClient:
    """Tiny background heartbeat from subordinate to coordinator."""

    def __init__(
        self,
        *,
        config: CoordinationConfig,
        payload_cache: "CommandCenterPayloadCache",
        controller: "Phase3WorkerController",
        lane_ledger: "Phase3LaneLedger | None" = None,
        ledger_client_status_path: Path | None = None,
    ) -> None:
        self._config = config
        self.payload_cache = payload_cache
        self.controller = controller
        self.lane_ledger = lane_ledger
        self.ledger_client_status_path = ledger_client_status_path or DEFAULT_LEDGER_CLIENT_STATUS_PATH
        self._stop = Event()
        self._lock = Lock()
        self._thread: Thread | None = None
        self.last_result: dict[str, Any] | None = None

    @property
    def config(self) -> CoordinationConfig:
        with self._lock:
            return self._config

    def update_config(self, config: CoordinationConfig) -> None:
        """Replace heartbeat target/settings and start thread if needed."""

        with self._lock:
            self._config = config
        self.start()

    def start(self) -> None:
        """Start subordinate heartbeat thread when online mode requests it."""

        config = self.config
        if not config.online or config.role != "subordinate" or self._thread is not None:
            return
        self._thread = Thread(target=self._run, name="phase3-subordinate-heartbeat", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        while not self._stop.is_set():
            config = self.config
            if config.online and config.role == "subordinate":
                self.last_result = self.send_once(config)
                wait_seconds = max(5.0, config.heartbeat_seconds)
            else:
                wait_seconds = 5.0
            self._stop.wait(wait_seconds)

    def send_once(self, config: CoordinationConfig | None = None) -> dict[str, Any]:
        """Send one heartbeat. Errors are returned, not raised."""

        config = config or self.config
        try:
            payload = self.payload_cache.get()
            ledger_client = ledger_client_status_payload(self.ledger_client_status_path)
            ledger_client_max_age = max(config.heartbeat_seconds * 4, DEFAULT_LEDGER_CLIENT_STALE_SECONDS)
            pool_active_lanes = _active_worker_lane_ranges(payload)
            ledger_active_lanes = ledger_client_active_lane_ranges(
                ledger_client,
                max_age_seconds=ledger_client_max_age,
            )
            pool_running_workers = _nonnegative_int(payload.get("workers", {}).get("running_workers"))
            ledger_running_workers = ledger_client_running_workers(
                ledger_client,
                max_age_seconds=ledger_client_max_age,
            )
            running_lanes = sorted(set(pool_active_lanes + ledger_active_lanes))
            body = {
                "device_id": config.device_id,
                "role": config.role,
                "advertise_url": config.advertise_url,
                "progress": payload.get("progress", {}),
                "health": {
                    "complete_lane_ranges": payload.get("health", {}).get("complete_lane_ranges", []),
                },
                "ledger": self.lane_ledger.summary() if self.lane_ledger is not None else {},
                "workers": {
                    "running_workers": max(pool_running_workers, ledger_running_workers),
                    "pool_running_workers": pool_running_workers,
                    "ledger_client_running_workers": ledger_running_workers,
                    "pending_lanes": payload.get("workers", {}).get("pending_lanes"),
                    "failed_jobs_since_pool_boot": payload.get("workers", {}).get("failed_jobs_since_pool_boot"),
                    "active_lane_ranges": [str(item) for item in running_lanes if item],
                    "pool_active_lane_ranges": [str(item) for item in pool_active_lanes if item],
                    "ledger_client_active_lane_ranges": [str(item) for item in ledger_active_lanes if item],
                },
                "ledger_client": ledger_client,
                "controller": self.controller.state(),
                "sent_at_unix": time.time(),
            }
            request = urlrequest.Request(
                f"{config.primary_url}/api/coordination/heartbeat",
                data=json.dumps(body).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urlrequest.urlopen(request, timeout=2.0) as response:
                return {"ok": 200 <= response.status < 300, "status": response.status}
        except (OSError, urlerror.URLError, TimeoutError) as exc:
            return {"ok": False, "error": str(exc)}


def _int_from_mapping(payload: dict[str, Any], key: str, default: int) -> int:
    try:
        return int(payload.get(key, default))
    except (TypeError, ValueError):
        return default


def _bool_from_mapping(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def coordination_config_from_mapping(
    payload: dict[str, Any],
    *,
    base: CoordinationConfig,
) -> CoordinationConfig:
    """Build validated coordination config from partial JSON/form input."""

    role = _normalize_role(str(payload.get("role", base.role)))
    primary_scheme = _normalize_scheme(str(payload.get("primary_scheme", base.primary_scheme)))
    advertise_scheme = _normalize_scheme(str(payload.get("advertise_scheme", base.advertise_scheme)))
    primary_host, embedded_primary_scheme, embedded_primary_port = _split_urlish_host(
        str(payload.get("primary_host", base.primary_host)),
        default_scheme=primary_scheme,
    )
    advertise_host, embedded_advertise_scheme, embedded_advertise_port = _split_urlish_host(
        str(payload.get("advertise_host", base.advertise_host)),
        default_scheme=advertise_scheme,
    )
    if embedded_primary_scheme and "primary_scheme" not in payload:
        primary_scheme = _normalize_scheme(embedded_primary_scheme)
    if embedded_advertise_scheme and "advertise_scheme" not in payload:
        advertise_scheme = _normalize_scheme(embedded_advertise_scheme)
    primary_port = _int_from_mapping(
        payload,
        "primary_port",
        embedded_primary_port or base.primary_port,
    )
    advertise_port = _int_from_mapping(
        payload,
        "advertise_port",
        embedded_advertise_port or base.advertise_port,
    )
    heartbeat_seconds = float(payload.get("heartbeat_seconds", base.heartbeat_seconds))
    if primary_port < 1 or primary_port > 65535:
        raise ValueError("primary_port must be between 1 and 65535")
    if advertise_host and (advertise_port < 1 or advertise_port > 65535):
        raise ValueError("advertise_port must be between 1 and 65535")
    if heartbeat_seconds < 5:
        raise ValueError("heartbeat_seconds must be at least 5")
    return CoordinationConfig(
        role=role,
        online=_bool_from_mapping(payload, "online", base.online),
        primary_scheme=primary_scheme,
        primary_host=primary_host or base.primary_host,
        primary_port=primary_port,
        advertise_scheme=advertise_scheme,
        advertise_host=advertise_host,
        advertise_port=advertise_port,
        heartbeat_seconds=heartbeat_seconds,
        device_id=str(payload.get("device_id", base.device_id)).strip() or base.device_id,
    )


def load_coordination_config(path: Path, base: CoordinationConfig) -> CoordinationConfig:
    """Load persisted UI network settings if present."""

    data = _read_json_file(path)
    if not isinstance(data, dict) or data.get("error"):
        return base
    try:
        return coordination_config_from_mapping(data, base=base)
    except ValueError:
        return base


def write_coordination_config(path: Path, config: CoordinationConfig) -> None:
    """Persist network settings changed from Flask UI."""

    write_json_atomic(
        path,
        {
            "role": config.role,
            "online": config.online,
            "primary_scheme": config.primary_scheme,
            "primary_host": config.primary_host,
            "primary_port": config.primary_port,
            "advertise_scheme": config.advertise_scheme,
            "advertise_host": config.advertise_host,
            "advertise_port": config.advertise_port,
            "heartbeat_seconds": config.heartbeat_seconds,
            "device_id": config.device_id,
            "updated_at_unix": time.time(),
            "updated_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        },
    )


class Phase3RateTracker:
    """Small in-memory rate tracker for command-center boot-session progress."""

    def __init__(self, target_lanes: int = DEFAULT_TARGET_LANES) -> None:
        self.target_lanes = target_lanes
        self._lock = Lock()
        self._first_time: float | None = None
        self._first_complete_lanes: int | None = None
        self._last_complete_lanes: int | None = None

    def update(self, complete_lanes: int, now: float | None = None) -> RateSnapshot:
        """Record one total-completion sample and return rate/ETA."""

        now = time.time() if now is None else now
        with self._lock:
            if (
                self._first_time is None
                or self._first_complete_lanes is None
                or self._last_complete_lanes is None
                or complete_lanes < self._last_complete_lanes
            ):
                self._first_time = now
                self._first_complete_lanes = complete_lanes
            self._last_complete_lanes = complete_lanes
            first_time = self._first_time
            first_complete_lanes = self._first_complete_lanes

        elapsed = max(0.0, now - first_time)
        completed_since_boot = max(0, complete_lanes - first_complete_lanes)
        lanes_per_hour = (
            completed_since_boot / elapsed * 3600.0
            if elapsed > 0 and completed_since_boot > 0
            else None
        )
        remaining = max(0, self.target_lanes - complete_lanes)
        eta = remaining / (lanes_per_hour / 3600.0) if lanes_per_hour else None
        finish = None
        if eta is not None and math.isfinite(eta):
            finish = (datetime.now().astimezone() + timedelta(seconds=eta)).strftime(
                "%Y-%m-%d %H:%M:%S %Z"
            )
        return RateSnapshot(lanes_per_hour, elapsed, eta, finish, completed_since_boot, first_complete_lanes)


class CommandCenterPayloadCache:
    """Throttle status reads and expensive folder scans independently.

    Worker-pool JSON is small and useful to refresh often. Full output-folder
    scans get more expensive as the project approaches 65,536 ZIP files, so the
    ZIP audit has its own slower TTL and is shared by all browser/SSE clients.
    """

    def __init__(
        self,
        *,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        pool_status_path: Path | None = None,
        watcher_status_path: Path | None = None,
        target_lanes: int = DEFAULT_TARGET_LANES,
        sample_limit: int = DEFAULT_SAMPLE_LIMIT,
        sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
        zip_scan_interval_seconds: float = DEFAULT_ZIP_SCAN_INTERVAL_SECONDS,
        host_resource_interval_seconds: float = DEFAULT_HOST_RESOURCE_INTERVAL_SECONDS,
    ) -> None:
        self.output_dir = output_dir
        self.pool_status_path = pool_status_path or (output_dir / POOL_STATUS_NAME)
        self.watcher_status_path = watcher_status_path or (output_dir / WATCHER_STATUS_NAME)
        self.target_lanes = target_lanes
        self.sample_limit = sample_limit
        self.sample_interval_seconds = max(0.5, sample_interval_seconds)
        self.zip_scan_interval_seconds = max(self.sample_interval_seconds, zip_scan_interval_seconds)
        self.host_resource_interval_seconds = max(self.sample_interval_seconds, host_resource_interval_seconds)
        self.boot_time_unix = time.time()
        self.boot_time_local = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        self._tracker = Phase3RateTracker(target_lanes)
        self._pool_file_cache = JsonFileCache(self.pool_status_path)
        self._watcher_file_cache = JsonFileCache(self.watcher_status_path)
        self._lock = Lock()
        self._payload: dict[str, Any] | None = None
        self._sampled_at_monotonic = 0.0
        self._zip_audit: Phase3ZipAudit | None = None
        self._zip_sampled_at_monotonic = 0.0
        self._host_snapshot: dict[str, Any] | None = None
        self._host_sampled_at_monotonic = 0.0
        self._max_running_workers_seen = 0

    def get(self, *, force: bool = False, force_zip: bool = False) -> dict[str, Any]:
        """Return cached command-center payload.

        A normal forced refresh means "read small worker status now." It does
        not force a folder scan unless `force_zip` is set or the ZIP scan TTL
        expires. That keeps button/SSE traffic from walking a 65k-file output
        directory every time the browser asks for fresh worker timers.
        """

        now = time.monotonic()
        with self._lock:
            cache_age = now - self._sampled_at_monotonic
            if not force and self._payload is not None and cache_age < self.sample_interval_seconds:
                payload = dict(self._payload)
                payload["cache_age_seconds"] = cache_age
                payload["zip_scan_age_seconds"] = (
                    now - self._zip_sampled_at_monotonic
                    if self._zip_audit is not None
                    else None
                )
                payload["host_sample_age_seconds"] = (
                    now - self._host_sampled_at_monotonic
                    if self._host_snapshot is not None
                    else None
                )
                return payload

            zip_age = now - self._zip_sampled_at_monotonic
            if (
                force_zip
                or self._zip_audit is None
                or zip_age >= self.zip_scan_interval_seconds
            ):
                self._zip_audit = audit_phase3_zips(
                    self.output_dir,
                    target_lanes=self.target_lanes,
                    sample_limit=self.sample_limit,
                )
                self._zip_sampled_at_monotonic = time.monotonic()
                zip_age = 0.0

            host_age = now - self._host_sampled_at_monotonic
            if self._host_snapshot is None or host_age >= self.host_resource_interval_seconds:
                # Host CPU/RAM/disk is for operator context only. Cache it so
                # browser refreshes do not steal cycles from CLI workers.
                self._host_snapshot = host_resource_snapshot(self.output_dir)
                self._host_sampled_at_monotonic = time.monotonic()
                host_age = 0.0

            payload = build_command_center_payload(
                output_dir=self.output_dir,
                pool_status_path=self.pool_status_path,
                watcher_status_path=self.watcher_status_path,
                target_lanes=self.target_lanes,
                sample_limit=self.sample_limit,
                tracker=self._tracker,
                zip_audit=self._zip_audit,
                pool_status=self._pool_file_cache.read(),
                watcher_status=self._watcher_file_cache.read(),
                host_snapshot=self._host_snapshot,
                boot_time_unix=self.boot_time_unix,
                boot_time_local=self.boot_time_local,
                max_running_workers_seen=self._max_running_workers_seen,
            )
            self._max_running_workers_seen = max(
                self._max_running_workers_seen,
                int(payload["workers"]["running_workers"]),
            )
            payload["workers"]["max_running_workers_seen"] = self._max_running_workers_seen
            self._sampled_at_monotonic = time.monotonic()
            payload["cache_age_seconds"] = 0.0
            payload["sample_interval_seconds"] = self.sample_interval_seconds
            payload["zip_scan_age_seconds"] = zip_age
            payload["zip_scan_interval_seconds"] = self.zip_scan_interval_seconds
            payload["host_sample_age_seconds"] = host_age
            payload["host_resource_interval_seconds"] = self.host_resource_interval_seconds
            self._payload = payload
            return dict(payload)


def _duration_text(seconds: float | None) -> str:
    """Human duration string."""

    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    total = int(seconds + 0.5)
    days, rem = divmod(total, 86_400)
    hours, rem = divmod(rem, 3600)
    minutes, secs = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h {minutes}m"
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _read_json_file(path: Path) -> dict[str, Any] | None:
    """Read optional JSON without treating absence as fatal.

    `utf-8-sig` accepts the UTF-8 BOM that Windows PowerShell 5 can add when an
    operator or cleanup script rewrites control/status JSON.
    """

    try:
        if not path.is_file():
            return None
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"error": str(exc), "path": str(path)}


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write JSON through a temp file so the worker pool never reads half JSON."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


class JsonFileCache:
    """Small mtime/size cache for status JSON files read on every panel tick."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._signature: tuple[int, int] | None = None
        self._payload: dict[str, Any] | None = None

    def read(self) -> dict[str, Any] | None:
        """Return parsed JSON, reparsing only after file metadata changes."""

        try:
            stat = self.path.stat()
        except FileNotFoundError:
            self._signature = None
            self._payload = None
            return None
        except OSError as exc:
            return {"error": str(exc), "path": str(self.path)}

        signature = (int(stat.st_mtime_ns), int(stat.st_size))
        if signature == self._signature:
            return self._payload
        try:
            self._payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
            self._signature = signature
            return self._payload
        except (OSError, json.JSONDecodeError) as exc:
            self._signature = signature
            self._payload = {"error": str(exc), "path": str(self.path)}
            return self._payload


def _add_sample(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    bucket = samples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(value)


def audit_phase3_zips(
    output_dir: Path,
    *,
    target_lanes: int = DEFAULT_TARGET_LANES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> Phase3ZipAudit:
    """Count final lane ZIPs by filename and size only.

    This deliberately does not open ZIP central directories. Deep content checks
    belong to `phase3_zip_validator.py` and the PKHeX.Core validator.
    """

    # All valid lane IDs are four hex digits. A bytearray is cheaper than a
    # Python set for the fixed 0x0000-0xFFFF domain and still detects duplicates.
    seen_lanes = bytearray(0x10000)
    complete_count = 0
    zip_files = 0
    bad_names = 0
    zero_size_zips = 0
    tmp_files = 0
    duplicate_lanes = 0
    tiny_zips = 0
    last_good_lane: int | None = None
    complete_lane_ids: list[int] = []
    samples: dict[str, list[str]] = {}

    try:
        with os.scandir(output_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                name = entry.name
                zip_match = ZIP_RE.match(name)
                if zip_match:
                    zip_files += 1
                    lane = int(zip_match.group(1), 16)
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    if size <= 0:
                        zero_size_zips += 1
                        _add_sample(samples, "zero_size_zips", name, sample_limit)
                    elif size < MIN_FINAL_ZIP_BYTES:
                        tiny_zips += 1
                        _add_sample(samples, "tiny_zips", f"{name} ({size} bytes)", sample_limit)
                    elif seen_lanes[lane]:
                        duplicate_lanes += 1
                        _add_sample(samples, "duplicate_lanes", name, sample_limit)
                    else:
                        seen_lanes[lane] = 1
                        complete_lane_ids.append(lane)
                        complete_count += 1
                        last_good_lane = lane if last_good_lane is None else max(last_good_lane, lane)
                    continue
                if name.endswith(".spinda80.zip") or ".spinda80.zip." in name:
                    if TMP_RE.match(name):
                        tmp_files += 1
                        _add_sample(samples, "tmp_files", name, sample_limit)
                    else:
                        bad_names += 1
                        _add_sample(samples, "bad_names", name, sample_limit)
    except FileNotFoundError:
        _add_sample(samples, "folder_errors", f"missing folder: {output_dir}", sample_limit)
    except OSError as exc:
        _add_sample(samples, "folder_errors", str(exc), sample_limit)

    return Phase3ZipAudit(
        folder=str(output_dir),
        target_lanes=target_lanes,
        complete_lanes=complete_count,
        zip_files=zip_files,
        missing_lanes=max(0, target_lanes - complete_count),
        bad_names=bad_names,
        zero_size_zips=zero_size_zips,
        tiny_zips=tiny_zips,
        tmp_files=tmp_files,
        duplicate_lanes=duplicate_lanes,
        bad_zip_artifacts=bad_names + zero_size_zips + tiny_zips + tmp_files + duplicate_lanes,
        last_good_lane=f"0x{last_good_lane:04X}" if last_good_lane is not None else None,
        complete_lane_ranges=lane_ids_to_range_strings(complete_lane_ids),
        samples=samples,
    )


def _parse_int(raw: str) -> int:
    return int(raw.strip(), 0)


def parse_lane_token(token: str) -> list[int]:
    """Parse one lane token/range into integer lane IDs."""

    token = token.strip()
    if not token:
        return []
    for separator in ("..", "-"):
        if separator in token:
            start_raw, end_raw = token.split(separator, 1)
            start = _parse_int(start_raw)
            end = _parse_int(end_raw)
            if end < start:
                raise ValueError(f"lane range ends before it starts: {token}")
            return list(range(start, end + 1))
    return [_parse_int(token)]


def parse_lanes_text(value: str) -> list[int]:
    """Parse comma/space separated lane IDs and ranges."""

    lanes: list[int] = []
    seen: set[int] = set()
    for part in re.split(r"[\s,]+", value.strip()):
        if not part:
            continue
        for lane in parse_lane_token(part):
            if not 0 <= lane <= 0xFFFF:
                raise ValueError(f"lane must fit in 16 bits: {part}")
            if lane not in seen:
                seen.add(lane)
                lanes.append(lane)
    return lanes


def lane_ranges_to_id_set(ranges: Iterable[Any], *, target_lanes: int = DEFAULT_TARGET_LANES) -> set[int]:
    """Expand compact range strings, ignoring malformed remote heartbeat data."""

    lanes: set[int] = set()
    for value in ranges or []:
        try:
            for lane in parse_lanes_text(str(value)):
                if 0 <= lane < target_lanes:
                    lanes.add(lane)
        except (TypeError, ValueError):
            continue
    return lanes


def ledger_client_status_payload(path: Path, *, now: float | None = None) -> dict[str, Any]:
    """Read the local ledger-worker client mirror JSON.

    In online multi-PC mode, lane ownership belongs to the coordinator ledger.
    The ledger client writes this small mirror while it claims and runs batches
    so local UI and upstream heartbeats can show active coordinator-owned lanes
    even when the coordinator has no per-lane worker sidecar files.
    """

    now = time.time() if now is None else now
    payload = _read_json_file(path)
    if not isinstance(payload, dict):
        return {"exists": False, "path": str(path), "active_lane_ranges": []}
    ranges = payload.get("active_lane_ranges") if isinstance(payload.get("active_lane_ranges"), list) else []
    updated_at = payload.get("updated_at_unix")
    age_seconds = None
    if isinstance(updated_at, (int, float)):
        age_seconds = max(0.0, now - float(updated_at))
    return {
        **payload,
        "exists": path.is_file(),
        "path": str(path),
        "age_seconds": age_seconds,
        "active_lane_ranges": [str(item) for item in ranges],
    }


def ledger_client_is_fresh(
    status: dict[str, Any],
    *,
    max_age_seconds: float = DEFAULT_LEDGER_CLIENT_STALE_SECONDS,
) -> bool:
    """Return true only while the ledger-client sidecar is fresh enough to trust."""

    age = status.get("age_seconds")
    return isinstance(age, (int, float)) and 0.0 <= float(age) <= max_age_seconds


def ledger_client_active_lane_ranges(
    status: dict[str, Any],
    *,
    max_age_seconds: float = DEFAULT_LEDGER_CLIENT_STALE_SECONDS,
) -> list[str]:
    """Return live ledger-client lane ranges, ignoring stale sidecar files."""

    if not ledger_client_is_fresh(status, max_age_seconds=max_age_seconds):
        return []
    return [str(item) for item in status.get("active_lane_ranges") or [] if item]


def ledger_client_running_workers(
    status: dict[str, Any],
    *,
    max_age_seconds: float = DEFAULT_LEDGER_CLIENT_STALE_SECONDS,
) -> int:
    """Return worker count represented by a fresh active ledger-client status."""

    if str(status.get("status") or "") not in {"claimed", "running_batch", "report_error", "reporting_existing"}:
        return 0
    if not ledger_client_is_fresh(status, max_age_seconds=max_age_seconds):
        return 0
    return _nonnegative_int(status.get("workers"))


def sanitize_remote_workers_for_stale_sidecar(
    body: dict[str, Any],
    *,
    target_lanes: int,
    max_age_seconds: float,
) -> dict[str, Any]:
    """Drop stale ledger-client ranges from old helper heartbeats.

    Updated helpers already omit stale ledger-client sidecars before sending a
    heartbeat. This coordinator-side guard protects against older helper code:
    if the sidecar is stale, ranges and worker count that can only be explained
    by that sidecar are not allowed to refresh leases on the coordinator.
    """

    workers = dict(body.get("workers") if isinstance(body.get("workers"), dict) else {})
    ledger_client = body.get("ledger_client") if isinstance(body.get("ledger_client"), dict) else {}
    if not ledger_client or ledger_client_is_fresh(ledger_client, max_age_seconds=max_age_seconds):
        return workers

    stale_ranges = ledger_client.get("active_lane_ranges") if isinstance(ledger_client.get("active_lane_ranges"), list) else []
    stale_lanes = lane_ranges_to_id_set(stale_ranges, target_lanes=target_lanes)
    worker_ranges = workers.get("active_lane_ranges") if isinstance(workers.get("active_lane_ranges"), list) else []
    worker_lanes = lane_ranges_to_id_set(worker_ranges, target_lanes=target_lanes)
    pool_ranges = workers.get("pool_active_lane_ranges") if isinstance(workers.get("pool_active_lane_ranges"), list) else []
    pool_lanes = lane_ranges_to_id_set(pool_ranges, target_lanes=target_lanes)
    health = body.get("health") if isinstance(body.get("health"), dict) else {}
    ledger = body.get("ledger") if isinstance(body.get("ledger"), dict) else {}
    done_lanes = lane_ranges_to_id_set(health.get("complete_lane_ranges") or [], target_lanes=target_lanes)
    done_lanes.update(lane_ranges_to_id_set(ledger.get("done_ranges") or [], target_lanes=target_lanes))
    inferred_legacy_pool_lanes: set[int] = set()
    # Older helpers did not split pool ranges from ledger-client ranges. When
    # their stale sidecar range overlaps a bundle whose first lane is already
    # reported complete, the remaining lanes are still real active pool work.
    if not pool_lanes and worker_lanes and (worker_lanes & done_lanes):
        inferred_legacy_pool_lanes = worker_lanes - done_lanes
    if stale_lanes:
        workers["active_lane_ranges"] = lane_ids_to_range_strings(
            pool_lanes or inferred_legacy_pool_lanes or (worker_lanes - stale_lanes)
        )
    stale_workers = _nonnegative_int(ledger_client.get("workers"))
    running_workers = _nonnegative_int(workers.get("running_workers"))
    pool_workers = _nonnegative_int(workers.get("pool_running_workers"))
    if pool_workers:
        workers["running_workers"] = pool_workers
    elif inferred_legacy_pool_lanes:
        workers["running_workers"] = running_workers
    elif stale_workers and running_workers <= stale_workers:
        workers["running_workers"] = 0
    elif stale_workers:
        workers["running_workers"] = max(0, running_workers - stale_workers)
    workers["stale_ledger_client_ignored"] = True
    workers["stale_ledger_client_age_seconds"] = ledger_client.get("age_seconds")
    if inferred_legacy_pool_lanes:
        workers["legacy_pool_ranges_inferred_from_done"] = True
    return workers


def _active_worker_lane_ranges(payload: dict[str, Any]) -> list[str]:
    """Return bundle lanes currently reserved by a worker-pool status payload."""

    lane_ids: set[int] = set()
    workers = payload.get("workers") if isinstance(payload.get("workers"), dict) else {}
    for worker in workers.get("running") or []:
        if not isinstance(worker, dict):
            continue
        for key in ("bundle_lane_id", "lane_id"):
            raw = worker.get(key)
            if not raw:
                continue
            try:
                lane_ids.update(parse_lanes_text(str(raw)))
            except ValueError:
                continue
    return lane_ids_to_range_strings(lane_ids)


def _lane_record_status(record: dict[str, Any] | None) -> str:
    return str((record or {}).get("status") or "pending")


def _lane_zip_path(output_dir: Path, lane_id: int) -> Path:
    return output_dir / f"0x{lane_id:04X}.spinda80.zip"


def _valid_named_lane_zip(output_dir: Path, lane_id: int) -> bool:
    path = _lane_zip_path(output_dir, lane_id)
    try:
        return path.is_file() and path.stat().st_size >= MIN_FINAL_ZIP_BYTES
    except OSError:
        return False


class Phase3LaneLedger:
    """Persistent lane ownership ledger for multi-device Phase 3 runs.

    Pending lanes are implicit. The JSON file stores lanes that are claimed,
    running, done, failed, released, or quarantined. Claims have leases, so a
    crashed subordinate does not permanently own a lane.
    """

    def __init__(
        self,
        *,
        path: Path,
        output_dir: Path,
        target_lanes: int = DEFAULT_TARGET_LANES,
        default_lease_seconds: int = DEFAULT_LEDGER_LEASE_SECONDS,
    ) -> None:
        self.path = path
        self.output_dir = output_dir
        self.target_lanes = target_lanes
        self.default_lease_seconds = default_lease_seconds
        self._lock = Lock()
        self._signature: tuple[int, int] | None = None
        self._payload: dict[str, Any] | None = None
        self._summary_signature: tuple[int, int] | None = None
        self._summary_payload: dict[str, Any] | None = None

    def _empty_payload(self) -> dict[str, Any]:
        now = time.time()
        return {
            "version": 1,
            "created_at_unix": now,
            "created_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "updated_at_unix": now,
            "updated_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
            "target_lanes": self.target_lanes,
            "records": {},
        }

    def _read_unlocked(self) -> dict[str, Any]:
        try:
            stat = self.path.stat()
            signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except FileNotFoundError:
            self._signature = None
            self._payload = self._empty_payload()
            return self._payload
        if signature == self._signature and self._payload is not None:
            return self._payload
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            payload = self._empty_payload()
        if not isinstance(payload.get("records"), dict):
            payload["records"] = {}
        self._signature = signature
        self._payload = payload
        return payload

    def _write_unlocked(self, payload: dict[str, Any]) -> None:
        now = time.time()
        payload["updated_at_unix"] = now
        payload["updated_at_local"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
        payload["target_lanes"] = self.target_lanes
        write_json_atomic(self.path, payload)
        try:
            stat = self.path.stat()
            self._signature = (int(stat.st_mtime_ns), int(stat.st_size))
        except OSError:
            self._signature = None
        self._payload = payload
        self._summary_signature = None
        self._summary_payload = None

    def _mark_done_from_zip_unlocked(self, payload: dict[str, Any], lane_id: int) -> None:
        lane_hex = f"0x{lane_id:04X}"
        path = _lane_zip_path(self.output_dir, lane_id)
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        # Folder reconciliation is intentionally filename/size-only. A tiny
        # file is more likely an interrupted write than a completed 65,536-PK3
        # lane, so never promote it into the persistent ledger as done.
        if size is None or size < MIN_FINAL_ZIP_BYTES:
            return
        record = dict(payload["records"].get(lane_hex) or {})
        if record.get("status") == "done":
            return
        record.update(
            {
                "lane": lane_hex,
                "status": "done",
                "device_id": record.get("device_id") or "folder-reconcile",
                "worker_id": record.get("worker_id"),
                "finished_at_unix": time.time(),
                "finished_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                "zip_path": str(path),
                "zip_size": size,
                "source": "zip-folder",
            }
        )
        payload["records"][lane_hex] = record

    def _claimable_unlocked(self, payload: dict[str, Any], lane_id: int, now: float) -> bool:
        lane_hex = f"0x{lane_id:04X}"
        if _valid_named_lane_zip(self.output_dir, lane_id):
            self._mark_done_from_zip_unlocked(payload, lane_id)
            return False
        status = _lane_record_status(payload["records"].get(lane_hex))
        if status in {"done", "verified", "quarantined"}:
            return False
        if status in {"claimed", "running"}:
            lease_until = float(payload["records"][lane_hex].get("lease_until_unix") or 0.0)
            if lease_until > now:
                return False
        return True

    def claim(
        self,
        *,
        device_id: str,
        worker_id: str | None = None,
        count: int = 1,
        lanes: str = "0x0000-0xFFFF",
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Claim next available lanes from coordinator."""

        if not device_id:
            raise ValueError("device_id is required")
        count = max(1, min(DEFAULT_LEDGER_MAX_CLAIM_COUNT, int(count)))
        lease_seconds = int(lease_seconds or self.default_lease_seconds)
        if lease_seconds < 60:
            raise ValueError("lease_seconds must be at least 60")
        lane_ids = parse_lanes_text(lanes)
        now = time.time()
        claim_id = uuid.uuid4().hex
        claimed: list[str] = []
        with self._lock:
            payload = self._read_unlocked()
            for lane_id in lane_ids:
                if len(claimed) >= count:
                    break
                if not self._claimable_unlocked(payload, lane_id, now):
                    continue
                lane_hex = f"0x{lane_id:04X}"
                prior = payload["records"].get(lane_hex) or {}
                payload["records"][lane_hex] = {
                    "lane": lane_hex,
                    "status": "claimed",
                    "claim_id": claim_id,
                    "device_id": device_id,
                    "worker_id": worker_id,
                    "claimed_at_unix": now,
                    "claimed_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "heartbeat_at_unix": now,
                    "lease_until_unix": now + lease_seconds,
                    "lease_seconds": lease_seconds,
                    "attempts": int(prior.get("attempts") or 0) + 1,
                }
                claimed.append(lane_hex)
            self._write_unlocked(payload)
        return {"claim_id": claim_id, "device_id": device_id, "worker_id": worker_id, "claimed_lanes": claimed, "count": len(claimed)}

    def heartbeat(
        self,
        *,
        device_id: str,
        lanes: Iterable[str],
        lease_seconds: int | None = None,
    ) -> dict[str, Any]:
        """Refresh leases for lanes owned by one device."""

        if not device_id:
            raise ValueError("device_id is required")
        lease_seconds = int(lease_seconds or self.default_lease_seconds)
        now = time.time()
        updated: list[str] = []
        ignored: list[str] = []
        with self._lock:
            payload = self._read_unlocked()
            for lane_hex in lanes:
                record = payload["records"].get(str(lane_hex).upper().replace("X", "x"))
                if not record or record.get("device_id") != device_id:
                    ignored.append(str(lane_hex))
                    continue
                if record.get("status") not in {"claimed", "running"}:
                    ignored.append(str(lane_hex))
                    continue
                record["status"] = "running"
                record["heartbeat_at_unix"] = now
                record["lease_until_unix"] = now + lease_seconds
                updated.append(record["lane"])
            self._write_unlocked(payload)
        return {"updated_lanes": updated, "ignored_lanes": ignored}

    def finish(self, *, device_id: str, lane: str, metadata: dict[str, Any] | None = None) -> dict[str, Any]:
        """Mark one claimed lane done after result ZIP exists/returns."""

        if not device_id:
            raise ValueError("device_id is required")
        lane_id = parse_lanes_text(lane)[0]
        lane_hex = f"0x{lane_id:04X}"
        metadata = metadata or {}
        now = time.time()
        with self._lock:
            payload = self._read_unlocked()
            record = dict(payload["records"].get(lane_hex) or {"lane": lane_hex})
            if record.get("device_id") not in {None, device_id, "folder-reconcile"}:
                raise ValueError(f"lane {lane_hex} owned by {record.get('device_id')}")
            zip_path = metadata.get("zip_path") or str(_lane_zip_path(self.output_dir, lane_id))
            zip_size = metadata.get("zip_size")
            if zip_size is None and Path(zip_path).is_file():
                try:
                    zip_size = Path(zip_path).stat().st_size
                except OSError:
                    zip_size = None
            try:
                zip_size_int = int(zip_size) if zip_size is not None else None
            except (TypeError, ValueError):
                raise ValueError(f"lane {lane_hex} has invalid zip_size={zip_size!r}") from None
            if zip_size_int is None:
                raise ValueError(f"lane {lane_hex} cannot be marked done without ZIP size proof")
            if zip_size_int is not None and zip_size_int < MIN_FINAL_ZIP_BYTES:
                raise ValueError(f"lane {lane_hex} ZIP is too small to mark done: {zip_size_int} bytes")
            pk3_count = metadata.get("pk3_count", record.get("pk3_count"))
            try:
                pk3_count_int = int(pk3_count)
            except (TypeError, ValueError):
                raise ValueError(f"lane {lane_hex} pk3_count must be {SPINDAS_PER_LANE}, got {pk3_count!r}") from None
            if pk3_count_int != SPINDAS_PER_LANE:
                raise ValueError(f"lane {lane_hex} pk3_count must be {SPINDAS_PER_LANE}, got {pk3_count_int}")
            record.update(
                {
                    "lane": lane_hex,
                    "status": "done",
                    "device_id": device_id,
                    "worker_id": metadata.get("worker_id", record.get("worker_id")),
                    "finished_at_unix": now,
                    "finished_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "zip_path": zip_path,
                    "zip_size": zip_size_int,
                    "zip_sha256": metadata.get("zip_sha256", record.get("zip_sha256")),
                    "pk3_count": pk3_count_int,
                }
            )
            payload["records"][lane_hex] = record
            self._write_unlocked(payload)
        return {"lane": lane_hex, "status": "done"}

    def fail(self, *, device_id: str, lane: str, reason: str = "", retryable: bool = True) -> dict[str, Any]:
        """Mark a lane failed or quarantined."""

        if not device_id:
            raise ValueError("device_id is required")
        lane_id = parse_lanes_text(lane)[0]
        lane_hex = f"0x{lane_id:04X}"
        with self._lock:
            payload = self._read_unlocked()
            record = dict(payload["records"].get(lane_hex) or {"lane": lane_hex})
            if record.get("device_id") not in {None, device_id}:
                raise ValueError(f"lane {lane_hex} owned by {record.get('device_id')}")
            record.update(
                {
                    "lane": lane_hex,
                    "status": "failed" if retryable else "quarantined",
                    "device_id": device_id,
                    "failed_at_unix": time.time(),
                    "failed_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                    "failure_reason": reason,
                    "retryable": retryable,
                }
            )
            payload["records"][lane_hex] = record
            self._write_unlocked(payload)
        return {"lane": lane_hex, "status": record["status"]}

    def release(self, *, device_id: str, lanes: Iterable[str]) -> dict[str, Any]:
        """Release unstarted claims back to pending pool."""

        if not device_id:
            raise ValueError("device_id is required")
        released: list[str] = []
        ignored: list[str] = []
        with self._lock:
            payload = self._read_unlocked()
            for lane in lanes:
                lane_id = parse_lanes_text(str(lane))[0]
                lane_hex = f"0x{lane_id:04X}"
                record = payload["records"].get(lane_hex)
                if not record or record.get("device_id") != device_id or record.get("status") not in {"claimed", "running"}:
                    ignored.append(lane_hex)
                    continue
                record["status"] = "released"
                record["released_at_unix"] = time.time()
                released.append(lane_hex)
            self._write_unlocked(payload)
        return {"released_lanes": released, "ignored_lanes": ignored}

    def release_inactive_claims(
        self,
        *,
        device_id: str,
        keep_lanes: Iterable[int] = (),
        worker_id: str | None = None,
        only_sources: set[str] | None = None,
        reason: str = "active-claim-reconcile",
    ) -> dict[str, Any]:
        """Release active rows for one device that are no longer reported live.

        Multi-PC mode has two live signals: direct ledger claim/heartbeat calls
        and command-center coordination heartbeats. If a helper restarts, keeps
        an old sidecar, or disappears, stale `running` rows can otherwise block
        reassignment until the long production lease expires. This method keeps
        the coordinator ledger aligned with the helper's current reported lane
        set without touching completed/quarantined proof rows.
        """

        if not device_id:
            return {"released_lanes": [], "marked_done_lanes": [], "ignored_lanes": []}
        keep = {int(lane) for lane in keep_lanes if 0 <= int(lane) <= 0xFFFF}
        released: list[str] = []
        marked_done: list[str] = []
        ignored: list[str] = []
        with self._lock:
            payload = self._read_unlocked()
            for lane_hex, record in list(payload.get("records", {}).items()):
                if record.get("device_id") != device_id:
                    continue
                if worker_id is not None and record.get("worker_id") != worker_id:
                    ignored.append(str(lane_hex))
                    continue
                if only_sources is not None and record.get("source") not in only_sources:
                    ignored.append(str(lane_hex))
                    continue
                status = _lane_record_status(record)
                if status not in {"claimed", "running"}:
                    continue
                try:
                    lane_id = parse_lanes_text(str(lane_hex))[0]
                except (IndexError, ValueError):
                    ignored.append(str(lane_hex))
                    continue
                if lane_id in keep:
                    continue
                if _valid_named_lane_zip(self.output_dir, lane_id):
                    before = _lane_record_status(payload["records"].get(f"0x{lane_id:04X}"))
                    self._mark_done_from_zip_unlocked(payload, lane_id)
                    after = _lane_record_status(payload["records"].get(f"0x{lane_id:04X}"))
                    if before != after:
                        marked_done.append(f"0x{lane_id:04X}")
                    continue
                record["status"] = "released"
                record["released_at_unix"] = time.time()
                record["released_at_local"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                record["release_reason"] = reason
                released.append(f"0x{lane_id:04X}")
            if released or marked_done:
                self._write_unlocked(payload)
        return {"released_lanes": released, "marked_done_lanes": marked_done, "ignored_lanes": ignored}

    def reconcile_completed_zips(self) -> dict[str, Any]:
        """Mark existing final ZIP filenames as done in ledger."""

        reconciled = 0
        with self._lock:
            payload = self._read_unlocked()
            try:
                with os.scandir(self.output_dir) as entries:
                    for entry in entries:
                        if not entry.is_file():
                            continue
                        match = ZIP_RE.match(entry.name)
                        if not match:
                            continue
                        lane_id = int(match.group(1), 16)
                        try:
                            size = entry.stat().st_size
                        except OSError:
                            continue
                        if size < MIN_FINAL_ZIP_BYTES:
                            continue
                        before = _lane_record_status(payload["records"].get(f"0x{lane_id:04X}"))
                        self._mark_done_from_zip_unlocked(payload, lane_id)
                        after = _lane_record_status(payload["records"].get(f"0x{lane_id:04X}"))
                        if before != after:
                            reconciled += 1
            except FileNotFoundError:
                pass
            self._write_unlocked(payload)
        return {"reconciled_lanes": reconciled}

    def import_remote_status(
        self,
        *,
        device_id: str,
        ledger: dict[str, Any],
        workers: dict[str, Any],
        health: dict[str, Any] | None = None,
        lease_seconds: int | None = None,
        sync_active_claims: bool = False,
    ) -> dict[str, Any]:
        """Merge subordinate heartbeat lane ranges into coordinator ledger.

        This keeps assignment state aligned when a helper reports completed or
        active lanes before the final ZIPs are manually consolidated. Existing
        local `done`/`verified` rows win; remote status never overwrites proof
        already present on the coordinator.
        """

        if not device_id:
            return {"remote_done_imported": 0, "remote_active_imported": 0, "remote_inactive_released": [], "conflicts": []}
        health = health if isinstance(health, dict) else {}
        lease_seconds = int(lease_seconds or self.default_lease_seconds)
        done_lanes = lane_ranges_to_id_set(ledger.get("done_ranges") or [], target_lanes=self.target_lanes)
        done_lanes.update(lane_ranges_to_id_set(health.get("complete_lane_ranges") or [], target_lanes=self.target_lanes))
        active_lanes = lane_ranges_to_id_set(ledger.get("active_claim_ranges") or [], target_lanes=self.target_lanes)
        active_lanes.update(lane_ranges_to_id_set(workers.get("active_lane_ranges") or [], target_lanes=self.target_lanes))
        now = time.time()
        done_imported = 0
        active_imported = 0
        conflicts: list[str] = []
        with self._lock:
            payload = self._read_unlocked()
            for lane_id in sorted(done_lanes):
                lane_hex = f"0x{lane_id:04X}"
                if _valid_named_lane_zip(self.output_dir, lane_id):
                    self._mark_done_from_zip_unlocked(payload, lane_id)
                    continue
                existing = dict(payload["records"].get(lane_hex) or {})
                status = _lane_record_status(existing)
                if status in {"done", "verified", "quarantined"}:
                    continue
                owner = existing.get("device_id")
                lease_until = float(existing.get("lease_until_unix") or 0.0)
                if status in {"claimed", "running"} and owner not in {None, device_id} and lease_until > now:
                    conflicts.append(lane_hex)
                    continue
                existing.update(
                    {
                        "lane": lane_hex,
                        "status": "done",
                        "device_id": device_id,
                        "worker_id": existing.get("worker_id") or "remote-heartbeat",
                        "finished_at_unix": existing.get("finished_at_unix") or now,
                        "finished_at_local": existing.get("finished_at_local")
                        or datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
                        "source": "remote-ledger",
                    }
                )
                payload["records"][lane_hex] = existing
                done_imported += 1
            released_inactive: list[str] = []
            if sync_active_claims:
                keep_lanes = done_lanes | active_lanes
                for lane_hex, record in list(payload.get("records", {}).items()):
                    if record.get("device_id") != device_id:
                        continue
                    status = _lane_record_status(record)
                    if status not in {"claimed", "running"}:
                        continue
                    try:
                        lane_id = parse_lanes_text(str(lane_hex))[0]
                    except (IndexError, ValueError):
                        continue
                    if lane_id in keep_lanes:
                        continue
                    if _valid_named_lane_zip(self.output_dir, lane_id):
                        self._mark_done_from_zip_unlocked(payload, lane_id)
                        continue
                    record["status"] = "released"
                    record["released_at_unix"] = now
                    record["released_at_local"] = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
                    record["release_reason"] = "remote-heartbeat-active-set-sync"
                    released_inactive.append(f"0x{lane_id:04X}")
            for lane_id in sorted(active_lanes - done_lanes):
                lane_hex = f"0x{lane_id:04X}"
                existing = dict(payload["records"].get(lane_hex) or {})
                status = _lane_record_status(existing)
                if status in {"done", "verified", "quarantined"}:
                    continue
                owner = existing.get("device_id")
                lease_until = float(existing.get("lease_until_unix") or 0.0)
                if status in {"claimed", "running"} and owner not in {None, device_id} and lease_until > now:
                    conflicts.append(lane_hex)
                    continue
                new_lease_until = max(lease_until, now + lease_seconds)
                existing.update(
                    {
                        "lane": lane_hex,
                        "status": "running",
                        "device_id": device_id,
                        "worker_id": existing.get("worker_id") or "remote-heartbeat",
                        "heartbeat_at_unix": now,
                        "lease_until_unix": new_lease_until,
                        "lease_seconds": lease_seconds,
                        "source": "remote-active-heartbeat",
                    }
                )
                payload["records"][lane_hex] = existing
                active_imported += 1
            if done_imported or active_imported or released_inactive:
                self._write_unlocked(payload)
        return {
            "remote_done_imported": done_imported,
            "remote_active_imported": active_imported,
            "remote_inactive_released": released_inactive,
            "conflicts": conflicts,
        }

    def summary(self, *, sample_limit: int = DEFAULT_SAMPLE_LIMIT) -> dict[str, Any]:
        """Return cached ledger counts and active claim samples."""

        with self._lock:
            payload = self._read_unlocked()
            signature = self._signature
            if signature == self._summary_signature and self._summary_payload is not None:
                return dict(self._summary_payload)
            records = payload.get("records") or {}
            counts: dict[str, int] = {
                "pending": self.target_lanes,
                "claimed": 0,
                "running": 0,
                "done": 0,
                "failed": 0,
                "released": 0,
                "quarantined": 0,
                "verified": 0,
                "expired_claims": 0,
            }
            now = time.time()
            active: list[dict[str, Any]] = []
            recent_done: list[dict[str, Any]] = []
            done_lane_ids: list[int] = []
            active_lane_ids: list[int] = []
            for lane_hex, record in records.items():
                status = _lane_record_status(record)
                try:
                    lane_id = parse_lanes_text(str(lane_hex))[0]
                except (IndexError, ValueError):
                    lane_id = None
                lease_until = float(record.get("lease_until_unix") or 0.0)
                is_active_claim = status in {"claimed", "running"} and lease_until > now
                if status in {"claimed", "running"}:
                    if is_active_claim:
                        counts[status] += 1
                    else:
                        counts["expired_claims"] += 1
                else:
                    counts.setdefault(status, 0)
                    counts[status] += 1
                if status in {"done", "verified", "quarantined"} or (
                    status in {"claimed", "running"} and is_active_claim
                ):
                    counts["pending"] -= 1
                if status in {"claimed", "running"} and is_active_claim:
                    if lane_id is not None:
                        active_lane_ids.append(lane_id)
                    if len(active) < sample_limit:
                        active.append(
                            {
                                "lane": lane_hex,
                                "status": status,
                                "device_id": record.get("device_id"),
                                "worker_id": record.get("worker_id"),
                                "lease_until_unix": record.get("lease_until_unix"),
                            }
                        )
                if status == "done":
                    if lane_id is not None:
                        done_lane_ids.append(lane_id)
                if status == "done" and len(recent_done) < sample_limit:
                    recent_done.append(
                        {
                            "lane": lane_hex,
                            "device_id": record.get("device_id"),
                            "zip_size": record.get("zip_size"),
                            "finished_at_unix": record.get("finished_at_unix"),
                        }
                    )
            counts["pending"] = max(0, counts["pending"])
            summary = {
                "exists": self.path.is_file(),
                "path": str(self.path),
                "target_lanes": self.target_lanes,
                "counts": counts,
                "done_ranges": lane_ids_to_range_strings(done_lane_ids),
                "active_claim_ranges": lane_ids_to_range_strings(active_lane_ids),
                "active_claims": active,
                "recent_done": sorted(recent_done, key=lambda item: item.get("finished_at_unix") or 0, reverse=True)[:sample_limit],
                "updated_at_unix": payload.get("updated_at_unix"),
                "updated_at_local": payload.get("updated_at_local"),
            }
            self._summary_signature = signature
            self._summary_payload = summary
            return dict(summary)


def _status_age_seconds(pool_status: dict[str, Any] | None, now: float) -> float | None:
    if not pool_status or "time_unix" not in pool_status:
        return None
    try:
        return max(0.0, now - float(pool_status["time_unix"]))
    except (TypeError, ValueError):
        return None


def _pool_control_requests_idle(pool_control_path: Path) -> bool:
    """Return true when control JSON says a worker shutdown was intentional."""

    control = _read_json_file(pool_control_path)
    if not isinstance(control, dict) or control.get("error"):
        return False
    try:
        desired_workers = int(control.get("desired_workers"))
    except (TypeError, ValueError):
        return False
    return desired_workers <= 0 and bool(control.get("shutdown"))


def _number_or_none(value: Any) -> float | None:
    """Return finite float for JSON numbers, otherwise None."""

    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _lane_duration_seconds(status: dict[str, Any]) -> float | None:
    """Best known wall-time estimate for one lane status sidecar.

    Older CLI hot-run status files intentionally skip full `elapsed_seconds`.
    They still keep timing buckets, which are accurate enough for dashboard
    last-lane/current-lane timers during bundled two-lane workers.
    """

    for key in ("outer_elapsed_seconds", "elapsed_seconds"):
        number = _number_or_none(status.get(key))
        if number is not None:
            return round(number, 3)

    timing = status.get("timing")
    if not isinstance(timing, dict):
        return None
    total = 0.0
    for key, value in timing.items():
        if not str(key).endswith("_seconds"):
            continue
        number = _number_or_none(value)
        if number is not None:
            total += number
    return round(total, 3) if total > 0.0 else None


def _read_lane_status(entry: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Merge worker-pool lane preview with the lane's sidecar status JSON."""

    lane_id = str(entry.get("lane_id") or "")
    raw_status_path = str(entry.get("status_path") or "")
    status_path = Path(raw_status_path) if raw_status_path else None
    if status_path is None and lane_id:
        status_path = output_dir / f"_{lane_id}.phase3_status.json"

    merged = dict(entry)
    status = _read_json_file(status_path) if status_path else None
    if isinstance(status, dict):
        merged.update(status)
    if status_path:
        merged["status_path"] = str(status_path)
        try:
            merged["status_age_seconds"] = round(max(0.0, time.time() - status_path.stat().st_mtime), 3)
        except OSError:
            merged["status_age_seconds"] = None
    if not merged.get("lane_id") and lane_id:
        merged["lane_id"] = lane_id
    merged["duration_seconds"] = _lane_duration_seconds(merged)
    return merged


def _active_bundle_lane(worker: dict[str, Any], output_dir: Path) -> dict[str, Any]:
    """Pick active lane and latest completed lane inside one bundled worker."""

    raw_statuses = worker.get("lane_statuses")
    if isinstance(raw_statuses, list) and raw_statuses:
        lane_statuses = [
            _read_lane_status(entry, output_dir)
            for entry in raw_statuses
            if isinstance(entry, dict)
        ]
    else:
        lane_statuses = [
            _read_lane_status(
                {
                    "lane_id": worker.get("lane_id"),
                    "status": worker.get("status"),
                    "generated_records": worker.get("generated_records", 0),
                    "selected_targets": worker.get("selected_targets"),
                    "elapsed_seconds": worker.get("elapsed_seconds"),
                    "status_path": worker.get("status_path"),
                    "output_zip": worker.get("output_zip"),
                    "zip_exists": worker.get("zip_exists"),
                },
                output_dir,
            )
        ]

    active_index = 0
    for index, lane in enumerate(lane_statuses):
        if lane.get("status") not in ("complete", "failed"):
            active_index = index
            break
    else:
        active_index = max(0, len(lane_statuses) - 1)

    active = lane_statuses[active_index] if lane_statuses else {}
    outer_elapsed = _number_or_none(worker.get("current_outer_elapsed_seconds"))
    previous_elapsed = 0.0
    previous_complete = True
    for lane in lane_statuses[:active_index]:
        duration = _number_or_none(lane.get("duration_seconds"))
        if duration is None:
            previous_complete = False
            break
        previous_elapsed += duration

    active_elapsed = _number_or_none(active.get("duration_seconds"))
    if active_elapsed is None and outer_elapsed is not None and previous_complete:
        active_elapsed = max(0.0, outer_elapsed - previous_elapsed)

    last_lane: dict[str, Any] | None = None
    for lane in lane_statuses[:active_index]:
        if lane.get("status") in ("complete", "failed"):
            last_lane = lane

    return {
        "bundle_lane_id": worker.get("lane_id"),
        "bundle_elapsed_seconds": outer_elapsed,
        "active": active,
        "active_elapsed_seconds": round(active_elapsed, 3) if active_elapsed is not None else None,
        "last_lane": last_lane,
        "lane_statuses": lane_statuses,
    }


def _summarize_running_workers(
    running: list[dict[str, Any]],
    *,
    output_dir: Path,
    sample_limit: int,
) -> list[dict[str, Any]]:
    """Return compact worker rows, reading only active status sidecars."""

    rows: list[dict[str, Any]] = []
    for worker in running[:sample_limit]:
        bundle = _active_bundle_lane(worker, output_dir)
        active = bundle["active"] or {}
        last_lane = bundle["last_lane"] or {}
        rows.append(
            {
                "slot_id": worker.get("slot_id"),
                "worker_name": worker.get("worker_name"),
                "pid": worker.get("pid"),
                "lane_id": active.get("lane_id", worker.get("lane_id")),
                "bundle_lane_id": bundle["bundle_lane_id"],
                "runner": worker.get("runner"),
                "status": active.get("status", worker.get("status", "pending")),
                "generated_records": active.get("generated_records", 0),
                "selected_targets": active.get("selected_targets", worker.get("selected_targets")),
                "elapsed_seconds": active.get("duration_seconds"),
                "active_lane_elapsed_seconds": bundle["active_elapsed_seconds"],
                "current_outer_elapsed_seconds": worker.get("current_outer_elapsed_seconds"),
                "bundle_elapsed_seconds": bundle["bundle_elapsed_seconds"],
                "output_zip": worker.get("output_zip"),
                "complete_lanes": worker.get("complete_lanes"),
                "last_completed_lane": last_lane.get("lane_id"),
                "last_completed_status": last_lane.get("status"),
                "last_completed_elapsed_seconds": last_lane.get("duration_seconds"),
                "status_age_seconds": active.get("status_age_seconds"),
                "status_path": active.get("status_path"),
            }
        )
    return rows


def _stall_reason(slot: dict[str, Any]) -> str | None:
    """Return a dashboard warning if one worker appears stuck."""

    status = slot.get("current_status")
    if status in (None, "", "complete", "failed"):
        return None

    current = _number_or_none(slot.get("current_elapsed_seconds"))
    last = _number_or_none(slot.get("last_iteration_seconds"))
    if current is not None and last is not None and last > 0:
        slow_threshold = max(last * SLOW_WORKER_MULTIPLIER, last + 1800.0)
        if current > slow_threshold:
            return (
                f"current lane {_duration_text(current)} exceeds prior lane "
                f"{_duration_text(last)} by slowdown threshold"
            )
    elif current is not None and current > 7200.0:
        return f"current lane has run {_duration_text(current)} without a prior timing baseline"
    return None


def _worker_stall_warnings(worker_slots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Build compact stall-warning rows for the command center."""

    warnings: list[dict[str, Any]] = []
    for slot in worker_slots:
        reason = _stall_reason(slot)
        if not reason:
            continue
        warnings.append(
            {
                "slot_id": slot.get("slot_id"),
                "pid": slot.get("pid"),
                "lane": slot.get("current_lane"),
                "reason": reason,
            }
        )
    return warnings


def _worker_slot_rows(
    running: list[dict[str, Any]],
    done: list[dict[str, Any]],
    *,
    sample_limit: int,
) -> list[dict[str, Any]]:
    """Pair current and previous lane timing by worker slot."""

    by_slot: dict[int, dict[str, Any]] = {}
    for item in done:
        slot = item.get("slot_id")
        if slot is None:
            continue
        try:
            slot_id = int(slot)
        except (TypeError, ValueError):
            continue
        by_slot[slot_id] = item

    slot_ids: set[int] = set(by_slot)
    for item in running:
        slot = item.get("slot_id")
        if slot is None:
            continue
        try:
            slot_ids.add(int(slot))
        except (TypeError, ValueError):
            continue

    rows: list[dict[str, Any]] = []
    running_by_slot: dict[int, dict[str, Any]] = {}
    for item in running:
        slot = item.get("slot_id")
        if slot is None:
            continue
        try:
            running_by_slot[int(slot)] = item
        except (TypeError, ValueError):
            continue
    for slot_id in sorted(slot_ids)[:sample_limit]:
        current = running_by_slot.get(slot_id)
        last = by_slot.get(slot_id)
        rows.append(
            {
                "slot_id": slot_id,
                "pid": (current or {}).get("pid"),
                "current_lane": (current or {}).get("lane_id"),
                "current_status": (current or {}).get("status"),
                "current_records": (current or {}).get("generated_records"),
                "current_elapsed_seconds": (current or {}).get("active_lane_elapsed_seconds")
                or (current or {}).get("elapsed_seconds")
                or (current or {}).get("current_outer_elapsed_seconds")
                or (current or {}).get("bundle_elapsed_seconds"),
                "current_status_age_seconds": (current or {}).get("status_age_seconds"),
                "bundle_lane": (current or {}).get("bundle_lane_id"),
                "bundle_elapsed_seconds": (current or {}).get("bundle_elapsed_seconds"),
                "last_lane": (current or {}).get("last_completed_lane")
                or (last or {}).get("lane_id"),
                "last_status": (current or {}).get("last_completed_status")
                or (last or {}).get("status"),
                "last_iteration_seconds": (current or {}).get("last_completed_elapsed_seconds")
                or (last or {}).get("outer_elapsed_seconds")
                or (last or {}).get("elapsed_seconds"),
                "last_complete_lanes": (last or {}).get("complete_lanes")
                or _lane_count_from_text((current or {}).get("last_completed_lane")),
            }
        )
    legacy_index = 1
    for current in running:
        if current.get("slot_id") is not None or len(rows) >= sample_limit:
            continue
        rows.append(
            {
                "slot_id": f"legacy-{legacy_index}",
                "pid": current.get("pid"),
                "current_lane": current.get("lane_id"),
                "current_status": current.get("status"),
                "current_records": current.get("generated_records"),
                "current_elapsed_seconds": current.get("current_outer_elapsed_seconds")
                or current.get("elapsed_seconds"),
                "current_status_age_seconds": current.get("status_age_seconds"),
                "last_lane": None,
                "last_status": None,
                "last_iteration_seconds": None,
                "last_complete_lanes": 0,
            }
        )
        legacy_index += 1
    return rows


class Phase3WorkerController:
    """Control-plane bridge from Flask to the worker-pool process."""

    def __init__(
        self,
        *,
        python_exe: Path = DEFAULT_PYTHON,
        worker_pool_script: Path = DEFAULT_WORKER_POOL_SCRIPT,
        ledger_worker_client_script: Path = DEFAULT_LEDGER_WORKER_CLIENT_SCRIPT,
        output_dir: Path = DEFAULT_OUTPUT_DIR,
        cache_dir: Path | None = None,
        pool_control_path: Path | None = None,
        ledger_client_status_path: Path | None = None,
        lanes: str = DEFAULT_LANES,
        control_max_workers: int = DEFAULT_CONTROL_MAX_WORKERS,
        bundle_size: int = DEFAULT_BUNDLE_SIZE,
        zip_method: str = DEFAULT_ZIP_METHOD,
        status_write_seconds: float = DEFAULT_STATUS_WRITE_SECONDS,
    ) -> None:
        self.python_exe = python_exe
        self.worker_pool_script = worker_pool_script
        self.ledger_worker_client_script = ledger_worker_client_script
        self.output_dir = output_dir
        self.cache_dir = cache_dir or (output_dir / "_cache")
        self.pool_control_path = pool_control_path or (output_dir / POOL_CONTROL_NAME)
        self.ledger_client_status_path = ledger_client_status_path or (output_dir / LEDGER_CLIENT_STATUS_NAME)
        self.lanes = lanes
        self.control_max_workers = control_max_workers
        self.bundle_size = max(1, bundle_size)
        self.zip_method = zip_method
        self.status_write_seconds = max(0.0, status_write_seconds)
        self._lock = Lock()
        self._managed_process: subprocess.Popen[Any] | None = None
        self._managed_kind = "none"

    def state(self) -> dict[str, Any]:
        """Return command-center managed process state."""

        process = self._managed_process
        return {
            "managed_pool_pid": process.pid if process else None,
            "managed_pool_returncode": process.poll() if process else None,
            "control_file": str(self.pool_control_path),
            "default_lanes": self.lanes,
            "control_max_workers": self.control_max_workers,
            "bundle_size": self.bundle_size,
            "zip_method": self.zip_method,
            "status_write_seconds": self.status_write_seconds,
            "managed_process_kind": self._managed_kind,
            "ledger_client_status_path": str(self.ledger_client_status_path),
        }

    def write_desired_workers(self, workers: int, *, shutdown: bool = False, lanes: str | None = None) -> dict[str, Any]:
        """Write worker-pool control file."""

        if workers < 0 or workers > self.control_max_workers:
            raise ValueError(f"workers must be between 0 and {self.control_max_workers}")
        if lanes:
            self.lanes = lanes
        payload = {
            "desired_workers": workers,
            "shutdown": shutdown,
            "source": "phase3_command_center_web",
            "lanes": self.lanes,
            "updated_at_unix": time.time(),
            "updated_at_local": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        }
        write_json_atomic(self.pool_control_path, payload)
        return payload

    def ensure_pool(
        self,
        workers: int,
        *,
        lanes: str | None = None,
        claim_url: str | None = None,
        device_id: str | None = None,
        heartbeat_seconds: float = DEFAULT_COORDINATION_HEARTBEAT_SECONDS,
    ) -> dict[str, Any]:
        """Launch a managed worker launcher if none is still running.

        Offline mode starts the native pool directly. Online mode starts the
        ledger client, which asks the coordinator for each batch before it
        launches the native pool. That keeps the coordinator as the only lane
        allocator across multiple PCs.
        """

        with self._lock:
            if lanes:
                self.lanes = lanes
            self.write_desired_workers(workers, shutdown=False, lanes=self.lanes)
            process = self._managed_process
            if process is not None and process.poll() is None:
                return {"launched": False, **self.state()}

            common_pool_args = [
                "--runner",
                "cli",
                "--zip-method",
                self.zip_method,
                "--cache-dir",
                str(self.cache_dir),
                "--control-file",
                str(self.pool_control_path),
                "--control-max-workers",
                str(self.control_max_workers),
                "--poll-seconds",
                "2",
                "--status-write-seconds",
                str(self.status_write_seconds),
            ]
            if claim_url:
                batch_size = max(1, int(workers)) * self.bundle_size
                command = [
                    str(self.python_exe),
                    str(self.ledger_worker_client_script),
                    "--coordinator-url",
                    claim_url,
                    "--device-id",
                    device_id or socket.gethostname(),
                    "--worker-id",
                    "command-center-worker-pool",
                    "--lanes",
                    self.lanes,
                    "--batch-size",
                    str(batch_size),
                    "--workers",
                    str(max(1, workers)),
                    "--bundle-size",
                    str(self.bundle_size),
                    "--heartbeat-seconds",
                    str(max(5.0, heartbeat_seconds)),
                    "--python-exe",
                    str(self.python_exe),
                    "--worker-pool-script",
                    str(self.worker_pool_script),
                    "--output-dir",
                    str(self.output_dir),
                    "--status-out",
                    str(self.ledger_client_status_path),
                    "--",
                    *common_pool_args,
                ]
                self._managed_kind = "ledger-client"
            else:
                command = [
                    str(self.python_exe),
                    str(self.worker_pool_script),
                    "--lanes",
                    self.lanes,
                    "--workers",
                    str(max(1, workers)),
                    "--bundle-size",
                    str(self.bundle_size),
                    "--skip-existing-by-name",
                    "--overwrite",
                    "--output-dir",
                    str(self.output_dir),
                    *common_pool_args,
                ]
                self._managed_kind = "direct-pool"
            creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            self._managed_process = subprocess.Popen(
                command,
                cwd=str(ROOT),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=creationflags,
            )
            return {"launched": True, "command": command, **self.state()}

    def stop(self, *, force_pids: Iterable[int] = ()) -> dict[str, Any]:
        """Request shutdown and optionally force-kill known worker PIDs."""

        with self._lock:
            self.write_desired_workers(0, shutdown=True)
            killed: list[int] = []
            for pid in force_pids:
                if pid <= 0:
                    continue
                try:
                    os.kill(pid, 9)
                    killed.append(pid)
                except OSError:
                    pass
            process = self._managed_process
            if process is not None and process.poll() is None:
                process.terminate()
            self._managed_kind = "stopped"
            return {"shutdown_requested": True, "force_killed_pids": killed, **self.state()}


def _legacy_completed_since_pool_boot(pool_status: dict[str, Any] | None) -> tuple[int, bool]:
    """Return pool-session completed lanes and whether value is legacy-estimated."""

    if not pool_status:
        return 0, False
    counts = pool_status.get("counts") or {}
    if "completed_lanes" in counts:
        return int(counts.get("completed_lanes") or 0), False
    done = pool_status.get("done") or []
    done_omitted = int(pool_status.get("done_omitted") or 0)
    complete = 0
    for item in done:
        if item.get("status") == "complete":
            complete += int(item.get("complete_lanes") or len(item.get("lane_ids", ())) or 1)
    if done_omitted:
        complete += done_omitted
        return complete, True
    return complete, False


def _eta_confidence_band(rate: RateSnapshot) -> str:
    """Label ETA confidence from current command-center boot sample size."""

    if rate.lanes_per_hour is None:
        return "warming up"
    if rate.completed_since_boot < 5:
        return "wide"
    if rate.completed_since_boot < 24:
        return "medium"
    return "narrow"


def _lane_count_from_text(value: Any) -> int:
    """Best-effort count for a lane or lane bundle label."""

    text = str(value or "")
    if not text:
        return 0
    if ".." in text:
        start_raw, end_raw = text.split("..", 1)
        try:
            start = int(start_raw, 16)
            end = int(end_raw, 16)
        except ValueError:
            return 1
        return max(1, end - start + 1)
    if "," in text:
        return max(1, len([part for part in text.split(",") if part.strip()]))
    return 1


def _recent_lane_seconds(
    worker_slots: list[dict[str, Any]],
    done_raw: list[dict[str, Any]],
) -> tuple[float | None, str]:
    """Return recent seconds-per-lane from completed jobs or slot history."""

    samples: list[float] = []
    for item in done_raw[-64:]:
        elapsed = _number_or_none(item.get("outer_elapsed_seconds")) or _number_or_none(item.get("elapsed_seconds"))
        lane_count = int(item.get("complete_lanes") or 0)
        if lane_count <= 0:
            lane_count = _lane_count_from_text(item.get("lane_id"))
        if elapsed is not None and elapsed > 0.0 and lane_count > 0:
            samples.append(elapsed / lane_count)
    if samples:
        return sum(samples) / len(samples), "recent completed worker jobs"

    for slot in worker_slots:
        elapsed = _number_or_none(slot.get("last_iteration_seconds"))
        lane_count = int(slot.get("last_complete_lanes") or 0)
        if lane_count <= 0:
            lane_count = _lane_count_from_text(slot.get("last_lane"))
        if elapsed is not None and elapsed > 0.0 and lane_count > 0:
            samples.append(elapsed / lane_count)
    if samples:
        return sum(samples) / len(samples), "worker-slot last timings"

    return None, "warming up"


def projected_finish_snapshot(
    *,
    complete_lanes: int,
    target_lanes: int,
    running_workers: int,
    worker_slots: list[dict[str, Any]],
    done_raw: list[dict[str, Any]],
    now: float,
) -> dict[str, Any]:
    """Estimate finish from recent completed lane time and active workers.

    Command-center boot ETA is useful only after the current panel process has
    observed new lane completions. This projection survives panel restarts by
    using recent worker-pool job durations already written to pool status JSON.
    """

    lane_seconds, basis = _recent_lane_seconds(worker_slots, done_raw)
    active_workers = max(0, int(running_workers or 0))
    lanes_remaining = max(0, target_lanes - complete_lanes)
    if lane_seconds is None or lane_seconds <= 0.0 or active_workers <= 0:
        return {
            "lanes_per_hour": None,
            "eta_seconds": None,
            "eta_text": "unknown",
            "finish_time_local": None,
            "basis": basis,
            "lane_seconds": lane_seconds,
            "active_workers": active_workers,
        }

    lanes_per_hour = active_workers * 3600.0 / lane_seconds
    eta_seconds = lanes_remaining / (lanes_per_hour / 3600.0) if lanes_per_hour else None
    finish = None
    if eta_seconds is not None and math.isfinite(eta_seconds):
        finish = (datetime.fromtimestamp(now).astimezone() + timedelta(seconds=eta_seconds)).strftime(
            "%Y-%m-%d %H:%M:%S %Z"
        )
    return {
        "lanes_per_hour": round(lanes_per_hour, 3),
        "eta_seconds": eta_seconds,
        "eta_text": _duration_text(eta_seconds),
        "finish_time_local": finish,
        "basis": basis,
        "lane_seconds": round(lane_seconds, 3),
        "active_workers": active_workers,
    }


def validation_policy_payload(complete_lanes: int, target_lanes: int, *, output_dir: Path = DEFAULT_OUTPUT_DIR) -> dict[str, Any]:
    """Return current production validation policy for dashboard/API."""

    pkhex_ready = complete_lanes >= target_lanes
    python_exe = ROOT / ".venv-mgba" / "bin" / "python.exe"
    validator = ROOT / "tools" / "spinda" / "phase3_zip_validator.py"
    pkhex_tool = ROOT / "tools" / "spinda" / "phase3_pkhex_validator"
    return {
        "raw_zip_validator": {
            "status": "available",
            "active_run_command": f"{python_exe} {validator} --root {output_dir} --manifest-only --allow-incomplete",
            "note": "Manifest checks are safe during production; deep ZIP checks read entries in RAM and should run on batches.",
        },
        "pkhex_validator": {
            "status": "ready" if pkhex_ready else "deferred",
            "ready": pkhex_ready,
            "reason": (
                "All lanes complete; final semantic audit can run."
                if pkhex_ready
                else "PKHeX.Core semantic validation is final-audit work, not hot production work."
            ),
            "tool": str(pkhex_tool),
        },
    }


def _windows_memory_snapshot() -> dict[str, Any]:
    """Return RAM status through Win32 when psutil is not installed."""

    if os.name != "nt":
        return {"available_bytes": None, "total_bytes": None, "used_percent": None}

    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = MEMORYSTATUSEX()
    status.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
        return {"available_bytes": None, "total_bytes": None, "used_percent": None}
    return {
        "available_bytes": int(status.ullAvailPhys),
        "total_bytes": int(status.ullTotalPhys),
        "used_percent": float(status.dwMemoryLoad),
    }


def _windows_cpu_percent() -> float | None:
    """Return CPU percent from Win32 system-time deltas.

    The first call seeds the sample and returns None. Later calls compare CPU
    idle time against total kernel+user time without starting a subprocess.
    """

    if os.name != "nt":
        return None

    class FILETIME(ctypes.Structure):
        _fields_ = [
            ("dwLowDateTime", ctypes.c_ulong),
            ("dwHighDateTime", ctypes.c_ulong),
        ]

    idle = FILETIME()
    kernel = FILETIME()
    user = FILETIME()
    if not ctypes.windll.kernel32.GetSystemTimes(  # type: ignore[attr-defined]
        ctypes.byref(idle),
        ctypes.byref(kernel),
        ctypes.byref(user),
    ):
        return None

    def to_int(value: FILETIME) -> int:
        return int(value.dwLowDateTime) | (int(value.dwHighDateTime) << 32)

    idle_ticks = to_int(idle)
    total_ticks = to_int(kernel) + to_int(user)
    global _CPU_SAMPLE
    with _CPU_SAMPLE_LOCK:
        previous = _CPU_SAMPLE
        _CPU_SAMPLE = (idle_ticks, total_ticks)
    if previous is None:
        return None
    previous_idle, previous_total = previous
    total_delta = total_ticks - previous_total
    idle_delta = idle_ticks - previous_idle
    if total_delta <= 0:
        return None
    used = 100.0 * (1.0 - max(0.0, min(1.0, idle_delta / total_delta)))
    return round(used, 1)


def host_resource_snapshot(output_dir: Path) -> dict[str, Any]:
    """Return low-overhead host disk/RAM/CPU data for operator display."""

    disk_path = output_dir if output_dir.exists() else output_dir.parent
    try:
        disk = shutil.disk_usage(disk_path)
        disk_payload = {
            "path": str(disk_path),
            "total_bytes": int(disk.total),
            "used_bytes": int(disk.used),
            "free_bytes": int(disk.free),
            "used_percent": round((disk.used / disk.total) * 100.0, 3) if disk.total else None,
        }
    except OSError as exc:
        disk_payload = {"path": str(disk_path), "error": str(exc)}

    cpu_percent: float | None = None
    try:
        import psutil  # type: ignore[import-not-found]

        cpu_percent = float(psutil.cpu_percent(interval=None))
        mem = psutil.virtual_memory()
        memory_payload = {
            "available_bytes": int(mem.available),
            "total_bytes": int(mem.total),
            "used_percent": float(mem.percent),
        }
    except Exception:  # noqa: BLE001 - psutil is optional in this workspace.
        cpu_percent = _windows_cpu_percent()
        memory_payload = _windows_memory_snapshot()

    return {
        "cpu_percent": cpu_percent,
        "memory": memory_payload,
        "disk": disk_payload,
    }


def build_command_center_payload(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pool_status_path: Path | None = None,
    watcher_status_path: Path | None = None,
    target_lanes: int = DEFAULT_TARGET_LANES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    tracker: Phase3RateTracker | None = None,
    zip_audit: Phase3ZipAudit | None = None,
    pool_status: dict[str, Any] | None = None,
    watcher_status: dict[str, Any] | None = None,
    host_snapshot: dict[str, Any] | None = None,
    boot_time_unix: float | None = None,
    boot_time_local: str | None = None,
    max_running_workers_seen: int = 0,
) -> dict[str, Any]:
    """Build one read-only command-center payload."""

    now = time.time()
    pool_status_path = pool_status_path or (output_dir / POOL_STATUS_NAME)
    watcher_status_path = watcher_status_path or (output_dir / WATCHER_STATUS_NAME)
    audit = zip_audit or audit_phase3_zips(output_dir, target_lanes=target_lanes, sample_limit=sample_limit)
    if pool_status is None and pool_status_path.is_file():
        pool_status = _read_json_file(pool_status_path)
    if watcher_status is None and watcher_status_path.is_file():
        watcher_status = _read_json_file(watcher_status_path)
    tracker = tracker or Phase3RateTracker(target_lanes)
    rate = tracker.update(audit.complete_lanes, now=now)
    pool_counts = (pool_status or {}).get("counts") or {}
    running_raw = (pool_status or {}).get("running") or []
    done_raw = (pool_status or {}).get("done") or []
    idle_requested = _pool_control_requests_idle(pool_status_path.with_name("_native_phase3_worker_pool_control.json"))
    if idle_requested:
        # A killswitch/stop leaves old pool JSON on disk. The control file is
        # stronger evidence that local production is intentionally idle, so do
        # not keep showing stale worker slots as alive.
        running_raw = []
        pool_counts = {**pool_counts, "running": 0}
    running = _summarize_running_workers(
        running_raw,
        output_dir=output_dir,
        sample_limit=sample_limit,
    )
    worker_slots = _worker_slot_rows(running, done_raw, sample_limit=sample_limit)
    stall_warnings = _worker_stall_warnings(worker_slots)
    completed_since_pool_boot, completed_pool_estimated = _legacy_completed_since_pool_boot(pool_status)
    status_age = _status_age_seconds(pool_status, now)
    failed_since_pool_boot = int(pool_counts.get("failed_jobs") or 0)
    generated_since_pool_boot = int(pool_counts.get("generated_records") or 0)
    projected = projected_finish_snapshot(
        complete_lanes=audit.complete_lanes,
        target_lanes=audit.target_lanes,
        running_workers=len(running_raw),
        worker_slots=worker_slots,
        done_raw=done_raw,
        now=now,
    )

    payload = {
        "generated_at_unix": now,
        "generated_at": datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z"),
        "boot": {
            "command_center_boot_unix": boot_time_unix,
            "command_center_boot_local": boot_time_local,
            "uptime_seconds": max(0.0, now - boot_time_unix) if boot_time_unix else rate.elapsed_seconds,
            "uptime_text": _duration_text(max(0.0, now - boot_time_unix) if boot_time_unix else rate.elapsed_seconds),
        },
        "progress": {
            "complete_lanes": audit.complete_lanes,
            "target_lanes": audit.target_lanes,
            "completed_spindas": audit.complete_lanes * SPINDAS_PER_LANE,
            "target_spindas": audit.target_lanes * SPINDAS_PER_LANE,
            "missing_lanes": audit.missing_lanes,
            "percent": audit.progress_percent,
            "completed_since_command_center_boot": rate.completed_since_boot,
            "first_complete_lanes": rate.first_complete_lanes,
            "completed_since_pool_boot": completed_since_pool_boot,
            "completed_since_pool_boot_estimated": completed_pool_estimated,
            "lanes_per_hour": rate.lanes_per_hour,
            "eta_seconds": rate.eta_seconds,
            "eta_text": _duration_text(rate.eta_seconds),
            "eta_confidence": _eta_confidence_band(rate),
            "finish_time_local": rate.finish_time_local,
            "projected_lanes_per_hour": projected["lanes_per_hour"],
            "projected_eta_seconds": projected["eta_seconds"],
            "projected_eta_text": projected["eta_text"],
            "projected_finish_time_local": projected["finish_time_local"],
            "projected_basis": projected["basis"],
            "projected_lane_seconds": projected["lane_seconds"],
            "projected_active_workers": projected["active_workers"],
        },
        "workers": {
            "running_workers": len(running_raw),
            "running_workers_previewed": len(running),
            "max_running_workers_seen": max(max_running_workers_seen, len(running_raw)),
            "pending_lanes": int(pool_counts.get("pending") or 0),
            "finished_jobs_since_pool_boot": int(pool_counts.get("done") or 0),
            "failed_jobs_since_pool_boot": failed_since_pool_boot,
            "generated_records_since_pool_boot": generated_since_pool_boot,
            "skipped_existing_complete": int(pool_counts.get("skipped_existing_complete") or 0),
            "pool_status_age_seconds": status_age,
            "pool_status_stale": (not idle_requested) and (status_age is None or status_age > STALE_POOL_SECONDS),
            "pool_idle_requested": idle_requested,
            "pool_status_path": str(pool_status_path),
            "stall_warning_count": len(stall_warnings),
            "stall_warnings": stall_warnings,
            "running": running,
            "worker_slots": worker_slots,
            "recent_done": done_raw[-sample_limit:] if pool_status else [],
            "done_omitted": int((pool_status or {}).get("done_omitted") or 0),
            "pending_omitted": int((pool_status or {}).get("pending_omitted") or 0),
        },
        "health": {
            **asdict(audit),
            "pool_status_exists": pool_status_path.is_file(),
            "pool_status_error": (pool_status or {}).get("error") if isinstance(pool_status, dict) else None,
        },
        "host": host_snapshot or host_resource_snapshot(output_dir),
        "validation_policy": validation_policy_payload(audit.complete_lanes, audit.target_lanes, output_dir=output_dir),
        "watcher": watcher_status_payload(watcher_status_path, watcher_status, now),
    }
    return payload


def watcher_status_payload(
    watcher_status_path: Path,
    watcher_status: dict[str, Any] | None,
    now: float,
) -> dict[str, Any]:
    """Return compact independent-watcher status for the command center."""

    if watcher_status is None:
        return {
            "exists": False,
            "status_path": str(watcher_status_path),
            "status": "missing",
            "age_seconds": None,
            "summary": {},
            "checks": [],
        }
    generated_at = watcher_status.get("generated_at_unix")
    age_seconds = None
    if isinstance(generated_at, (int, float)):
        age_seconds = max(0.0, now - float(generated_at))
    checks = watcher_status.get("checks") if isinstance(watcher_status.get("checks"), list) else []
    return {
        "exists": True,
        "status_path": str(watcher_status_path),
        "status": watcher_status.get("status", "unknown"),
        "age_seconds": age_seconds,
        "summary": watcher_status.get("summary") if isinstance(watcher_status.get("summary"), dict) else {},
        "checks": checks[:8],
    }


def _nonnegative_int(value: Any) -> int:
    """Coerce loose heartbeat JSON counts without letting bad data crash UI."""

    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def trusted_remote_completion(
    registered_devices: list[dict[str, Any]],
    *,
    target_lanes: int,
    local_done_lanes: Iterable[int] = (),
) -> dict[str, Any]:
    """Union subordinate ledger ranges for coordinator grand total.

    Remote machines may keep completed ZIPs locally until manual consolidation.
    Counts alone double-count overlapping machines, so only compact lane ranges
    contribute to totals. Legacy counts remain diagnostics.
    """

    devices: list[dict[str, Any]] = []
    all_done = set(local_done_lanes)
    remote_unique_lanes: set[int] = set()
    legacy_fallback_lanes = 0
    for device in registered_devices:
        ledger = device.get("ledger") if isinstance(device.get("ledger"), dict) else {}
        counts = ledger.get("counts") if isinstance(ledger.get("counts"), dict) else {}
        progress = device.get("progress") if isinstance(device.get("progress"), dict) else {}
        health = device.get("health") if isinstance(device.get("health"), dict) else {}
        done_ranges = ledger.get("done_ranges") if isinstance(ledger.get("done_ranges"), list) else []
        health_done_ranges = (
            health.get("complete_lane_ranges")
            if isinstance(health.get("complete_lane_ranges"), list)
            else []
        )
        combined_ranges = list(done_ranges) + list(health_done_ranges)
        if combined_ranges:
            source = "ledger_ranges"
            if health_done_ranges and not done_ranges:
                source = "health_ranges"
            elif health_done_ranges:
                source = "ledger_and_health_ranges"
            device_lanes = lane_ranges_to_id_set(combined_ranges, target_lanes=target_lanes)
            duplicate_lanes = len(device_lanes & all_done)
            unique_lanes = device_lanes - all_done
            all_done.update(device_lanes)
            remote_unique_lanes.update(unique_lanes)
            lanes = len(device_lanes)
        else:
            source = "legacy_count"
            lanes = _nonnegative_int(progress.get("complete_lanes"))
            if "done" in counts:
                source = "legacy_ledger_count"
                lanes = _nonnegative_int(counts.get("done"))
            lanes = min(max(0, target_lanes), lanes)
            legacy_fallback_lanes += lanes
            duplicate_lanes = None
            unique_lanes = set()
        lanes = min(max(0, target_lanes), lanes)
        devices.append(
            {
                "device_id": device.get("device_id") or "unknown",
                "trusted_lanes": lanes,
                "unique_lanes": len(unique_lanes),
                "duplicate_lanes": duplicate_lanes,
                "source": source,
                "age_seconds": device.get("age_seconds"),
            }
        )
    grand_total = min(max(0, target_lanes), len(all_done))
    remote_unique_total = min(max(0, target_lanes), len(remote_unique_lanes))
    return {
        "grand_complete_lanes": grand_total,
        "trusted_remote_lanes": remote_unique_total,
        "trusted_remote_spindas": remote_unique_total * SPINDAS_PER_LANE,
        "trusted_remote_device_count": len(devices),
        "trusted_remote_devices": devices,
        "legacy_fallback_lanes": legacy_fallback_lanes,
        "trust_policy": "subordinate lane ranges are unioned with local lanes; legacy count fallback is diagnostic only because it cannot dedupe",
    }


INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spinda Phase 3 Command Center</title>
  <style>
    :root {
      --bg: #101417;
      --panel: #171f23;
      --panel-2: #20292e;
      --ink: #edf4ef;
      --muted: #a8b6b0;
      --line: rgba(237, 244, 239, 0.14);
      --good: #66d09a;
      --warn: #f2b55f;
      --bad: #f07474;
      --accent: #4aa3ad;
      --detail-bg: rgba(255, 255, 255, 0.045);
      --pre-bg: rgba(0, 0, 0, 0.24);
      --bar-bg: rgba(255, 255, 255, 0.1);
      --control-bg: #11181c;
      --control-ink: #edf4ef;
      --button-bg: #26343a;
      --button-ink: #edf4ef;
    }
    body.theme-light {
      --bg: #111417;
      --panel: #f3f0e7;
      --panel-2: #fffdf6;
      --ink: #1f2422;
      --muted: #68716d;
      --line: rgba(31, 36, 34, 0.16);
      --good: #237a50;
      --warn: #a8621b;
      --bad: #a43131;
      --accent: #2f7f8f;
      --detail-bg: rgba(255, 253, 246, 0.62);
      --pre-bg: rgba(31, 36, 34, 0.08);
      --bar-bg: rgba(31, 36, 34, 0.1);
      --control-bg: #fffdf6;
      --control-ink: #1f2422;
      --button-bg: #e5dfd1;
      --button-ink: #1f2422;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      min-height: 100vh;
      color: var(--ink);
      font-family: "Segoe UI", Arial, sans-serif;
      background: var(--bg);
      padding: 18px;
    }
    main { width: min(1320px, 100%); margin: 0 auto; }
    header {
      color: var(--ink);
      display: flex;
      justify-content: space-between;
      gap: 16px;
      align-items: end;
      margin-bottom: 14px;
    }
    h1 { margin: 0; font-size: 2.2rem; line-height: 1; letter-spacing: 0; }
    .subtitle { color: var(--muted); margin: 6px 0 0; }
    .pill {
      border: 1px solid var(--line);
      border-radius: 999px;
      padding: 8px 12px;
      white-space: nowrap;
      font-variant-numeric: tabular-nums;
    }
    .grid { display: grid; grid-template-columns: 1.25fr 0.75fr; gap: 12px; }
    .metrics { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 10px; }
    .panel, .metric {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 16px;
    }
    .metric { background: var(--panel-2); }
    input, select, button {
      background: var(--control-bg);
      color: var(--control-ink);
      border: 1px solid var(--line);
      border-radius: 6px;
    }
    button {
      background: var(--button-bg);
      color: var(--button-ink);
      cursor: pointer;
    }
    .theme-toggle {
      padding: 8px 12px;
      font-weight: 700;
      white-space: nowrap;
    }
    .label {
      color: var(--muted);
      font-size: 0.78rem;
      text-transform: uppercase;
      letter-spacing: 0.08em;
    }
    .hero-number {
      font-size: clamp(2.7rem, 7vw, 5.8rem);
      line-height: 0.95;
      margin: 6px 0 12px;
      font-variant-numeric: tabular-nums;
      display: flex;
      gap: 0.16em;
      align-items: baseline;
      flex-wrap: wrap;
    }
    .hero-total { font-size: clamp(1.6rem, 3vw, 2.6rem); color: var(--muted); letter-spacing: 0; }
    .value { font-size: 1.35rem; margin-top: 4px; font-variant-numeric: tabular-nums; }
    .bar { height: 18px; background: var(--bar-bg); border-radius: 999px; overflow: hidden; }
    .fill { height: 100%; width: 0%; background: linear-gradient(90deg, var(--accent), #48a06e); }
    table { width: 100%; border-collapse: collapse; margin-top: 8px; }
    td, th {
      border-bottom: 1px solid var(--line);
      padding: 7px 0;
      text-align: left;
      font-variant-numeric: tabular-nums;
      vertical-align: top;
    }
    td:last-child { text-align: right; font-weight: 700; }
    th { color: var(--muted); font-weight: 600; font-size: 0.82rem; }
    .worker-table td:last-child, .worker-table th:last-child { text-align: left; }
    .good { color: var(--good); }
    .warn { color: var(--warn); }
    .bad { color: var(--bad); }
    .spinda-counter {
      margin-top: 10px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
    }
    .sub-number {
      font-size: clamp(1.4rem, 4vw, 2.2rem);
      font-weight: 800;
      font-variant-numeric: tabular-nums;
      line-height: 1.05;
    }
    .sub-total { color: var(--muted); font-size: 1rem; font-weight: 700; }
    button.killswitch {
      background: var(--bad);
      border: 1px solid rgba(164, 49, 49, 0.7);
      color: #fff;
      font-weight: 800;
    }
    .details-grid {
      display: grid;
      grid-template-columns: 1fr 1.35fr;
      gap: 12px;
      margin-top: 8px;
    }
    .detail-box {
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px;
      background: var(--detail-bg);
      min-height: 120px;
    }
    .detail-title {
      color: var(--muted);
      font-size: 0.82rem;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0;
      margin-bottom: 8px;
    }
    .sample-group { margin: 0 0 10px; }
    .sample-group h3 {
      margin: 0 0 4px;
      font-size: 0.95rem;
      line-height: 1.2;
    }
    .sample-group ul {
      margin: 0;
      padding-left: 18px;
      color: var(--muted);
      font-size: 0.9rem;
    }
    .recent-table { margin-top: 0; }
    .recent-table td:last-child, .recent-table th:last-child { text-align: left; }
    .warning-list {
      margin: 8px 0 0;
      padding-left: 18px;
      color: var(--bad);
      font-size: 0.92rem;
    }
    pre {
      white-space: pre-wrap;
      word-break: break-word;
      background: var(--pre-bg);
      border-radius: 8px;
      padding: 10px;
      max-height: 260px;
      overflow: auto;
      margin: 8px 0 0;
    }
    @media (max-width: 960px) {
      body { padding: 12px; }
      header, .grid { display: block; }
      .pill { margin-top: 10px; display: inline-block; }
      .panel { margin-bottom: 12px; }
      .metrics { grid-template-columns: repeat(2, 1fr); }
      .details-grid { display: block; }
      .detail-box { margin-top: 10px; }
    }
    @media (max-width: 540px) {
      .metrics { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
<main>
  <header>
    <div>
      <h1>Spinda Phase 3 Command Center</h1>
      <p class="subtitle">Headless worker dashboard and control plane. ZIP names only for hot status; deep validation stays separate.</p>
    </div>
    <div style="display:flex;gap:10px;align-items:center;justify-content:flex-end;flex-wrap:wrap;">
      <button id="theme-toggle" class="theme-toggle" type="button" aria-pressed="true">Dark mode</button>
      <div class="pill" id="connection">connecting...</div>
    </div>
  </header>

  <section class="grid">
    <div class="panel">
      <div class="label">Total completed lanes</div>
      <div class="hero-number"><span id="complete">0</span><span class="hero-total">/ 65534</span></div>
      <div class="spinda-counter">
        <div class="label">Exact Spindas generated</div>
        <div class="sub-number"><span id="completed-spindas">0</span><span class="sub-total"> / <span id="target-spindas">4,294,836,224</span></span></div>
        <div class="label">done lanes x 65,534</div>
      </div>
      <div class="bar"><div class="fill" id="fill"></div></div>
      <div class="metrics">
        <div class="metric"><div class="label">Percent</div><div class="value" id="percent">0%</div></div>
        <div class="metric"><div class="label">Since panel boot</div><div class="value" id="since-panel">0</div></div>
        <div class="metric"><div class="label">Since pool boot</div><div class="value" id="since-pool">0</div></div>
        <div class="metric"><div class="label">ETA</div><div class="value" id="eta">unknown</div></div>
        <div class="metric"><div class="label">Local ZIP lanes</div><div class="value" id="local-complete">0</div></div>
        <div class="metric"><div class="label">Trusted ledger/remote lanes</div><div class="value" id="trusted-remote">0</div></div>
        <div class="metric"><div class="label">Grand counter source</div><div class="value" id="grand-source" style="font-size:0.95rem;">local only</div></div>
      </div>
    </div>
    <div class="panel">
      <div class="label">Worker summary</div>
      <table>
        <tr><td>Combined running workers</td><td id="running-workers">0</td></tr>
        <tr><td>Local running workers</td><td id="local-running-workers">0</td></tr>
        <tr><td>Remote running workers</td><td id="remote-running-workers">0</td></tr>
        <tr><td>Max seen</td><td id="max-workers">0</td></tr>
        <tr><td>Pending lanes</td><td id="pending">0</td></tr>
        <tr><td>Finished jobs</td><td id="finished-jobs">0</td></tr>
        <tr><td>Failed jobs</td><td id="failed-jobs">0</td></tr>
        <tr><td>Stall warnings</td><td id="stall-warnings">0</td></tr>
        <tr><td>Pool status age</td><td id="pool-age">unknown</td></tr>
      </table>
    </div>
  </section>

  <section class="panel" style="margin-top: 12px;">
    <div class="label">Independent watcher</div>
    <div class="metrics">
      <div class="metric"><div class="label">Watcher status</div><div class="value" id="watcher-status">missing</div></div>
      <div class="metric"><div class="label">Watcher age</div><div class="value" id="watcher-age">unknown</div></div>
      <div class="metric"><div class="label">Watcher checks</div><div class="value" id="watcher-checks">0</div></div>
      <div class="metric"><div class="label">Watcher workers</div><div class="value" id="watcher-workers">0 / 0</div></div>
    </div>
    <div id="watcher-list" class="detail-box" style="margin-top:10px;">Watcher not running.</div>
  </section>

  <section class="panel" style="margin-top: 12px;">
    <div class="label">Multi-device coordination</div>
    <div class="metrics">
      <div class="metric"><div class="label">Role</div><div class="value" id="coord-role">coordinator</div></div>
      <div class="metric"><div class="label">Online mode</div><div class="value" id="coord-online">offline</div></div>
      <div class="metric"><div class="label">Advertise URL</div><div class="value" id="coord-advertise" style="font-size:0.95rem;">none</div></div>
      <div class="metric"><div class="label">Primary URL</div><div class="value" id="coord-primary" style="font-size:0.95rem;">none</div></div>
    </div>
    <div class="metrics" style="margin-top:10px;">
      <label class="metric"><div class="label">Role setting</div><select id="coord-role-input" style="width:100%;font-size:1.05rem;margin-top:6px;"><option value="coordinator">coordinator</option><option value="subordinate">subordinate</option></select></label>
      <label class="metric"><div class="label">Online setting</div><input id="coord-online-input" type="checkbox" style="margin-top:12px;transform:scale(1.35);"></label>
      <label class="metric"><div class="label">Primary HTTP/HTTPS</div><select id="coord-primary-scheme-input" style="width:100%;font-size:1.05rem;margin-top:6px;"><option value="http">http</option><option value="https">https</option></select></label>
      <label class="metric"><div class="label">Primary IP</div><input id="coord-primary-host-input" value="127.0.0.1" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <label class="metric"><div class="label">Primary port</div><input id="coord-primary-port-input" type="number" min="1" max="65535" value="235" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <label class="metric"><div class="label">Advertise HTTP/HTTPS</div><select id="coord-advertise-scheme-input" style="width:100%;font-size:1.05rem;margin-top:6px;"><option value="http">http</option><option value="https">https</option></select></label>
      <label class="metric"><div class="label">Advertise IP</div><input id="coord-advertise-host-input" value="" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <label class="metric"><div class="label">Advertise port</div><input id="coord-advertise-port-input" type="number" min="1" max="65535" value="235" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <label class="metric"><div class="label">Heartbeat seconds</div><input id="coord-heartbeat-input" type="number" min="5" value="60" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <div class="metric"><div class="label">Settings file</div><div class="value" id="coord-settings-path" style="font-size:0.82rem;">unknown</div></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">
      <button id="save-coordination" type="button" style="padding:10px 14px;">Save network settings</button>
      <span id="coord-save-status" class="label" style="align-self:center;">unchanged</span>
    </div>
    <div class="detail-box" style="margin-top:10px;">
      <div class="detail-title">Registered subordinate panels</div>
      <div class="label" style="margin-bottom:8px;">Grand total trusts lane ranges from local ZIPs, coordinator ledger, and subordinate heartbeats. Bare legacy counts are diagnostic only.</div>
      <div id="coord-devices">No subordinate panels reported.</div>
    </div>
  </section>

  <section class="panel" style="margin-top: 12px;">
    <div class="label">Lane ledger / claims</div>
    <div class="metrics">
      <div class="metric"><div class="label">Ledger done</div><div class="value" id="ledger-done">0</div></div>
      <div class="metric"><div class="label">Claimed / running</div><div class="value" id="ledger-active">0</div></div>
      <div class="metric"><div class="label">Failed / quarantine</div><div class="value" id="ledger-failed">0</div></div>
      <div class="metric"><div class="label">Expired claims</div><div class="value" id="ledger-expired">0</div></div>
      <div class="metric"><div class="label">Ledger pending</div><div class="value" id="ledger-pending">65,536</div></div>
      <div class="metric"><div class="label">Ledger file</div><div class="value" id="ledger-path" style="font-size:0.82rem;">unknown</div></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">
      <button id="ledger-reconcile" type="button" style="padding:10px 14px;">Reconcile finished ZIPs into ledger</button>
      <span id="ledger-status" class="label" style="align-self:center;">ready</span>
    </div>
    <div class="details-grid">
      <div class="detail-box">
        <div class="detail-title">Active claims</div>
        <div id="ledger-active-list">No active claims.</div>
      </div>
      <div class="detail-box">
        <div class="detail-title">Recent ledger done</div>
        <div id="ledger-recent-list">No ledger completions.</div>
      </div>
    </div>
  </section>

  <section class="panel" style="margin-top: 12px;">
    <div class="label">Host controls</div>
    <div class="metrics">
      <label class="metric"><div class="label">Desired workers</div><input id="desired-workers" type="number" min="0" max="64" value="6" style="width:100%;font-size:1.25rem;margin-top:6px;"></label>
      <label class="metric"><div class="label">Lane range</div><input id="lane-range" value="0x0000-0xFFFF" style="width:100%;font-size:1.05rem;margin-top:6px;"></label>
      <div class="metric"><div class="label">Pool PID</div><div class="value" id="pool-pid">none</div></div>
      <div class="metric"><div class="label">Control file</div><div class="value" id="control-state" style="font-size:0.95rem;">ready</div></div>
    </div>
    <div style="display:flex;gap:10px;flex-wrap:wrap;margin-top:10px;">
      <button id="apply-workers" type="button" style="padding:10px 14px;">Apply / launch workers</button>
      <button id="stop-workers" type="button" style="padding:10px 14px;">Stop workers</button>
      <button id="force-stop-workers" type="button" style="padding:10px 14px;">Force kill running workers</button>
      <button id="killswitch-workers" class="killswitch" type="button" style="padding:10px 14px;">Killswitch: stop all workers</button>
    </div>
  </section>

  <section class="grid" style="margin-top: 12px;">
    <div class="panel">
      <div class="label">Worker slots</div>
      <table class="worker-table">
        <thead><tr><th>Slot</th><th>PID</th><th>Current lane</th><th>Current timer</th><th>Last lane</th><th>Last timer</th></tr></thead>
        <tbody id="workers-body"><tr><td colspan="6">No worker slots reported.</td></tr></tbody>
      </table>
    </div>
    <div class="panel">
      <div class="label">Rates</div>
      <table>
        <tr><td>Lanes/hour</td><td id="rate">warming up</td></tr>
        <tr><td>ETA confidence</td><td id="eta-confidence">warming up</td></tr>
        <tr><td>Finish time</td><td id="finish-time">unknown</td></tr>
        <tr><td>Projected lanes/hour</td><td id="projected-rate">warming up</td></tr>
        <tr><td>Projected finish</td><td id="projected-finish">unknown</td></tr>
        <tr><td>Projection basis</td><td id="projected-basis">warming up</td></tr>
        <tr><td>Missing lanes</td><td id="missing">0</td></tr>
        <tr><td>Skipped at pool boot</td><td id="skipped">0</td></tr>
        <tr><td>Generated records this pool</td><td id="records">0</td></tr>
        <tr><td>Command center uptime</td><td id="uptime">0s</td></tr>
      </table>
    </div>
  </section>

  <section class="grid" style="margin-top: 12px;">
    <div class="panel">
      <div class="label">Output health</div>
      <table>
        <tr><td>Final ZIPs</td><td id="zip-files">0</td></tr>
        <tr><td>Last good lane</td><td id="last-good-lane">none</td></tr>
        <tr><td>Bad ZIP artifacts</td><td id="bad-artifacts">0</td></tr>
        <tr><td>Zero-size ZIPs</td><td id="zero-size">0</td></tr>
        <tr><td>Temp ZIPs</td><td id="tmp-files">0</td></tr>
        <tr><td>Bad ZIP names</td><td id="bad-names">0</td></tr>
        <tr><td>Duplicate lanes</td><td id="dupes">0</td></tr>
        <tr><td>ZIP scan age</td><td id="zip-scan-age">unknown</td></tr>
        <tr><td>Disk free</td><td id="disk-free">unknown</td></tr>
        <tr><td>RAM used</td><td id="ram-used">unknown</td></tr>
        <tr><td>CPU used</td><td id="cpu-used">unknown</td></tr>
      </table>
    </div>
    <div class="panel">
      <div class="label">Samples / recent done</div>
      <div class="details-grid">
        <div class="detail-box">
          <div class="detail-title">Health samples</div>
          <div id="samples-list">No health samples.</div>
          <div class="detail-title" style="margin-top:10px;">Worker warnings</div>
          <div id="warning-list">No worker warnings.</div>
          <div class="detail-title" style="margin-top:10px;">Validation policy</div>
          <div id="validation-policy">PKHeX final audit deferred.</div>
        </div>
        <div class="detail-box">
          <div class="detail-title">Recent completed jobs</div>
          <table class="recent-table">
            <thead><tr><th>Lane(s)</th><th>Status</th><th>Records</th><th>Timer</th></tr></thead>
            <tbody id="recent-done-body"><tr><td colspan="4">No recent completed jobs.</td></tr></tbody>
          </table>
          <div class="label" id="done-omitted-note" style="margin-top:8px;"></div>
        </div>
      </div>
    </div>
  </section>
</main>
<script>
  const nf = new Intl.NumberFormat();
  const setText = (id, value) => { document.getElementById(id).textContent = value; };
  const fmt = value => nf.format(Number(value || 0));
  const pct = value => `${Number(value || 0).toFixed(3)}%`;
  const rateText = value => value ? `${Number(value).toFixed(2)}` : "warming up";
  const secondsText = value => value == null ? "unknown" : `${Number(value).toFixed(1)}s`;
  let coordDirty = false;
  function applyTheme(theme) {
    const useLight = theme === "light";
    document.body.classList.toggle("theme-light", useLight);
    localStorage.setItem("phase3-command-center-theme", useLight ? "light" : "dark");
    const button = document.getElementById("theme-toggle");
    button.textContent = useLight ? "Light mode" : "Dark mode";
    button.setAttribute("aria-pressed", useLight ? "false" : "true");
  }
  applyTheme(localStorage.getItem("phase3-command-center-theme") === "light" ? "light" : "dark");
  function bytesText(value) {
    if (value == null) return "unknown";
    const units = ["B", "KiB", "MiB", "GiB", "TiB"];
    let amount = Number(value);
    let unit = 0;
    while (amount >= 1024 && unit < units.length - 1) {
      amount /= 1024;
      unit += 1;
    }
    return `${amount.toFixed(unit === 0 ? 0 : 1)} ${units[unit]}`;
  }
  function cls(id, count) {
    const node = document.getElementById(id);
    node.classList.remove("good", "warn", "bad");
    node.classList.add(Number(count || 0) ? "warn" : "good");
  }
  async function postJson(url, payload) {
    const response = await fetch(url, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload)});
    const data = await response.json();
    if (!response.ok || !data.ok) throw new Error(data.error || "control failed");
    return data;
  }
  function setInputValue(id, value) {
    const node = document.getElementById(id);
    if (node) node.value = value == null ? "" : value;
  }
  function markCoordDirty() {
    coordDirty = true;
    setText("coord-save-status", "unsaved");
  }
  function renderWorkers(slots) {
    const body = document.getElementById("workers-body");
    body.replaceChildren();
    if (!slots || slots.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 6;
      cell.textContent = "No worker slots reported.";
      row.appendChild(cell);
      body.appendChild(row);
      return;
    }
    for (const slot of slots) {
      const row = document.createElement("tr");
      const currentElapsed = slot.current_elapsed_seconds == null ? "none" : `${Number(slot.current_elapsed_seconds).toFixed(1)}s`;
      const lastElapsed = slot.last_iteration_seconds == null ? "none" : `${Number(slot.last_iteration_seconds).toFixed(1)}s`;
      for (const value of [slot.slot_id || "", slot.pid || "", slot.current_lane || "idle", currentElapsed, slot.last_lane || "none", lastElapsed]) {
        const cell = document.createElement("td");
        cell.textContent = value;
        row.appendChild(cell);
      }
      body.appendChild(row);
    }
  }
  function renderSamples(samples) {
    const panel = document.getElementById("samples-list");
    panel.replaceChildren();
    const entries = Object.entries(samples || {}).filter(([, values]) => Array.isArray(values) && values.length);
    if (!entries.length) {
      panel.textContent = "No health samples.";
      return;
    }
    for (const [name, values] of entries) {
      const group = document.createElement("div");
      group.className = "sample-group";
      const title = document.createElement("h3");
      title.textContent = name.replaceAll("_", " ");
      const list = document.createElement("ul");
      for (const value of values.slice(0, 8)) {
        const item = document.createElement("li");
        item.textContent = String(value);
        list.appendChild(item);
      }
      group.appendChild(title);
      group.appendChild(list);
      panel.appendChild(group);
    }
  }
  function renderRecentDone(recentDone, omitted) {
    const body = document.getElementById("recent-done-body");
    body.replaceChildren();
    if (!recentDone || recentDone.length === 0) {
      const row = document.createElement("tr");
      const cell = document.createElement("td");
      cell.colSpan = 4;
      cell.textContent = "No recent completed jobs.";
      row.appendChild(cell);
      body.appendChild(row);
    } else {
      for (const item of recentDone.slice().reverse()) {
        const row = document.createElement("tr");
        const lanes = Array.isArray(item.lane_ids) ? item.lane_ids.join(", ") : (item.lane_id || "unknown");
        const timer = item.outer_elapsed_seconds == null ? "unknown" : `${Number(item.outer_elapsed_seconds).toFixed(1)}s`;
        for (const value of [lanes, item.status || "unknown", fmt(item.generated_records), timer]) {
          const cell = document.createElement("td");
          cell.textContent = value;
          row.appendChild(cell);
        }
        body.appendChild(row);
      }
    }
    const note = document.getElementById("done-omitted-note");
    note.textContent = Number(omitted || 0) ? `${fmt(omitted)} older completed jobs hidden.` : "";
  }
  function renderWarnings(warnings) {
    const panel = document.getElementById("warning-list");
    panel.replaceChildren();
    if (!warnings || warnings.length === 0) {
      panel.textContent = "No worker warnings.";
      return;
    }
    const list = document.createElement("ul");
    list.className = "warning-list";
    for (const warning of warnings) {
      const item = document.createElement("li");
      item.textContent = `slot ${warning.slot_id || "?"} lane ${warning.lane || "unknown"}: ${warning.reason || "warning"}`;
      list.appendChild(item);
    }
    panel.appendChild(list);
  }
  function renderValidationPolicy(policy) {
    const panel = document.getElementById("validation-policy");
    panel.replaceChildren();
    const raw = (policy || {}).raw_zip_validator || {};
    const pkhex = (policy || {}).pkhex_validator || {};
    const rows = [
      `Raw ZIP: ${raw.status || "unknown"}`,
      `PKHeX: ${pkhex.status || "unknown"} - ${pkhex.reason || "no policy loaded"}`
    ];
    const list = document.createElement("ul");
    list.className = "sample-group";
    for (const value of rows) {
      const item = document.createElement("li");
      item.textContent = value;
      list.appendChild(item);
    }
    panel.appendChild(list);
  }
  function renderWatcher(watcher) {
    const status = (watcher || {}).status || "missing";
    const summary = (watcher || {}).summary || {};
    const checks = Array.isArray((watcher || {}).checks) ? watcher.checks : [];
    setText("watcher-status", status);
    setText("watcher-age", secondsText((watcher || {}).age_seconds));
    setText("watcher-checks", fmt(summary.check_count || checks.length || 0));
    setText("watcher-workers", `${fmt(summary.running_workers_reported)} / ${fmt(summary.phase3_worker_processes)}`);
    cls("watcher-checks", summary.warning_count || summary.critical_count || checks.length);
    const statusNode = document.getElementById("watcher-status");
    statusNode.classList.remove("good", "warn", "bad");
    statusNode.classList.add(status === "ok" ? "good" : status === "critical" ? "bad" : "warn");
    const panel = document.getElementById("watcher-list");
    panel.replaceChildren();
    if (!watcher || !watcher.exists) {
      panel.textContent = "Watcher status file missing. Run tools\\spinda\\phase3_independent_watcher.py.";
      return;
    }
    if (!checks.length) {
      panel.textContent = "No watcher warnings.";
      return;
    }
    const list = document.createElement("ul");
    list.className = "warning-list";
    for (const check of checks) {
      const item = document.createElement("li");
      item.textContent = `${(check.severity || "warning").toUpperCase()} ${check.code || "check"}: ${check.message || ""}`;
      list.appendChild(item);
    }
    panel.appendChild(list);
  }
  function renderCoordination(coordination) {
    const coord = coordination || {};
    setText("coord-role", coord.role || "coordinator");
    setText("coord-online", coord.online ? "online" : "offline");
    setText("coord-advertise", coord.advertise_url || "not advertised");
    setText("coord-primary", coord.primary_url || "none");
    setText("coord-settings-path", coord.settings_path || "unknown");
    if (!coordDirty) {
      setInputValue("coord-role-input", coord.role || "coordinator");
      document.getElementById("coord-online-input").checked = Boolean(coord.online);
      setInputValue("coord-primary-scheme-input", coord.primary_scheme || "http");
      setInputValue("coord-primary-host-input", coord.primary_host || "127.0.0.1");
      setInputValue("coord-primary-port-input", coord.primary_port || 235);
      setInputValue("coord-advertise-scheme-input", coord.advertise_scheme || "http");
      setInputValue("coord-advertise-host-input", coord.advertise_host || "");
      setInputValue("coord-advertise-port-input", coord.advertise_port || 235);
      setInputValue("coord-heartbeat-input", coord.heartbeat_seconds || 60);
      setText("coord-save-status", "saved");
    }
    const devices = Array.isArray(coord.registered_devices) ? coord.registered_devices : [];
    const panel = document.getElementById("coord-devices");
    panel.replaceChildren();
    if (!devices.length) {
      panel.textContent = "No subordinate panels reported.";
      return;
    }
    const list = document.createElement("ul");
    list.className = "sample-group";
    for (const device of devices.slice(0, 12)) {
      const item = document.createElement("li");
      const progress = device.progress || {};
      const workers = device.workers || {};
      const ledger = device.ledger || {};
      const counts = ledger.counts || {};
      const trusted = counts.done == null ? progress.complete_lanes : counts.done;
      const source = counts.done == null ? "progress" : "ledger";
      item.textContent = `${device.device_id || "unknown"} @ ${device.advertise_url || device.remote_addr || "unknown"}: ${fmt(trusted)} reported lanes (${source}), ${fmt(workers.running_workers)} workers, age ${secondsText(device.age_seconds)}`;
      list.appendChild(item);
    }
    panel.appendChild(list);
  }
  function renderLedger(ledger) {
    const counts = (ledger || {}).counts || {};
    const activeCount = Number(counts.claimed || 0) + Number(counts.running || 0);
    const failedCount = Number(counts.failed || 0) + Number(counts.quarantined || 0);
    setText("ledger-done", fmt(counts.done || 0));
    setText("ledger-active", fmt(activeCount));
    setText("ledger-failed", fmt(failedCount));
    setText("ledger-expired", fmt(counts.expired_claims || 0));
    setText("ledger-pending", fmt(counts.pending || 0));
    setText("ledger-path", (ledger || {}).path || "unknown");
    cls("ledger-expired", counts.expired_claims || 0);
    cls("ledger-failed", failedCount);

    const activePanel = document.getElementById("ledger-active-list");
    activePanel.replaceChildren();
    const active = Array.isArray((ledger || {}).active_claims) ? ledger.active_claims : [];
    if (!active.length) {
      activePanel.textContent = "No active claims.";
    } else {
      const list = document.createElement("ul");
      list.className = "sample-group";
      for (const claim of active.slice(0, 12)) {
        const item = document.createElement("li");
        item.textContent = `${claim.lane || "unknown"} ${claim.status || "claimed"} ${claim.device_id || "unknown"} ${claim.worker_id || ""}`;
        list.appendChild(item);
      }
      activePanel.appendChild(list);
    }

    const donePanel = document.getElementById("ledger-recent-list");
    donePanel.replaceChildren();
    const done = Array.isArray((ledger || {}).recent_done) ? ledger.recent_done : [];
    if (!done.length) {
      donePanel.textContent = "No ledger completions.";
    } else {
      const list = document.createElement("ul");
      list.className = "sample-group";
      for (const itemData of done.slice(0, 12)) {
        const item = document.createElement("li");
        item.textContent = `${itemData.lane || "unknown"} ${itemData.device_id || "unknown"} ${bytesText(itemData.zip_size)}`;
        list.appendChild(item);
      }
      donePanel.appendChild(list);
    }
  }
  function render(data) {
    const progress = data.progress || {};
    const workers = data.workers || {};
    const health = data.health || {};
    const host = data.host || {};
    const disk = host.disk || {};
    const memory = host.memory || {};
    setText("complete", fmt(progress.complete_lanes));
    setText("completed-spindas", fmt(progress.completed_spindas));
    setText("target-spindas", fmt(progress.target_spindas || 4294967296));
    setText("local-complete", fmt(progress.local_complete_lanes ?? progress.complete_lanes));
    setText("trusted-remote", fmt(progress.trusted_remote_lanes));
    setText("grand-source", progress.trusted_remote_lanes ? `local + trusted remote` : "local only");
    setText("percent", pct(progress.percent));
    setText("since-panel", fmt(progress.completed_since_command_center_boot));
    setText("since-pool", fmt(progress.completed_since_pool_boot));
    setText("eta", progress.eta_text || "unknown");
    setText("running-workers", fmt(workers.combined_running_workers ?? workers.running_workers));
    setText("local-running-workers", fmt(workers.local_running_workers ?? workers.running_workers));
    setText("remote-running-workers", fmt(workers.remote_running_workers));
    setText("max-workers", fmt(workers.max_running_workers_seen));
    setText("pending", fmt(workers.pending_lanes));
    setText("finished-jobs", fmt(workers.finished_jobs_since_pool_boot));
    setText("failed-jobs", fmt(workers.failed_jobs_since_pool_boot));
    setText("stall-warnings", fmt(workers.stall_warning_count));
    setText("pool-age", secondsText(workers.pool_status_age_seconds));
    setText("pool-pid", ((data.control || {}).managed_pool_pid) || "external/none");
    setText("control-state", workers.pool_status_stale ? "pool stale" : "pool live");
    setText("rate", rateText(progress.lanes_per_hour));
    setText("eta-confidence", progress.eta_confidence || "warming up");
    setText("finish-time", progress.finish_time_local || "unknown");
    setText("projected-rate", rateText(progress.projected_lanes_per_hour));
    setText("projected-finish", progress.projected_finish_time_local || "unknown");
    setText("projected-basis", progress.projected_basis || "warming up");
    setText("missing", fmt(progress.missing_lanes));
    setText("skipped", fmt(workers.skipped_existing_complete));
    setText("records", fmt(workers.generated_records_since_pool_boot));
    setText("uptime", (data.boot || {}).uptime_text || "unknown");
    setText("zip-files", fmt(health.zip_files));
    setText("last-good-lane", health.last_good_lane || "none");
    setText("bad-artifacts", fmt(health.bad_zip_artifacts));
    setText("zero-size", fmt(health.zero_size_zips));
    setText("tmp-files", fmt(health.tmp_files));
    setText("bad-names", fmt(health.bad_names));
    setText("dupes", fmt(health.duplicate_lanes));
    setText("zip-scan-age", secondsText(data.zip_scan_age_seconds));
    setText("disk-free", bytesText(disk.free_bytes));
    setText("ram-used", memory.used_percent == null ? "unknown" : `${Number(memory.used_percent).toFixed(1)}%`);
    setText("cpu-used", host.cpu_percent == null ? "unknown" : `${Number(host.cpu_percent).toFixed(1)}%`);
    cls("failed-jobs", workers.failed_jobs_since_pool_boot);
    cls("stall-warnings", workers.stall_warning_count);
    cls("bad-artifacts", health.bad_zip_artifacts);
    cls("zero-size", health.zero_size_zips);
    cls("tmp-files", health.tmp_files);
    cls("bad-names", health.bad_names);
    cls("dupes", health.duplicate_lanes);
    document.getElementById("fill").style.width = `${Math.min(100, Number(progress.percent || 0))}%`;
    renderWorkers(workers.worker_slots || []);
    renderSamples(health.samples || {});
    renderWarnings(workers.stall_warnings || []);
    renderValidationPolicy(data.validation_policy || {});
    renderWatcher(data.watcher || {});
    renderCoordination(data.coordination || {});
    renderLedger(data.ledger || {});
    renderRecentDone(workers.recent_done || [], workers.done_omitted || 0);
  }
  async function initialLoad() {
    const response = await fetch("/api/status");
    render(await response.json());
  }
  async function saveCoordinationSettings() {
    const payload = {
      role: document.getElementById("coord-role-input").value,
      online: document.getElementById("coord-online-input").checked,
      primary_scheme: document.getElementById("coord-primary-scheme-input").value,
      primary_host: document.getElementById("coord-primary-host-input").value,
      primary_port: Number(document.getElementById("coord-primary-port-input").value),
      advertise_scheme: document.getElementById("coord-advertise-scheme-input").value,
      advertise_host: document.getElementById("coord-advertise-host-input").value,
      advertise_port: Number(document.getElementById("coord-advertise-port-input").value),
      heartbeat_seconds: Number(document.getElementById("coord-heartbeat-input").value)
    };
    try {
      const data = await postJson("/api/coordination/settings", payload);
      coordDirty = false;
      setText("coord-save-status", "saved");
      renderCoordination(data.coordination);
      await initialLoad();
    } catch (error) {
      setText("coord-save-status", error.message);
    }
  }
  async function reconcileLedger() {
    try {
      setText("ledger-status", "reconciling...");
      const data = await postJson("/api/ledger/reconcile", {});
      setText("ledger-status", `reconciled ${fmt(data.reconciled_lanes)} lanes`);
      renderLedger(data.summary);
      await initialLoad();
    } catch (error) {
      setText("ledger-status", error.message);
    }
  }
  document.getElementById("apply-workers").addEventListener("click", async () => {
    setText("connection", "applying...");
    await postJson("/api/control/workers", {workers: Number(document.getElementById("desired-workers").value), lanes: document.getElementById("lane-range").value, launch_if_needed: true});
    await initialLoad();
  });
  document.getElementById("stop-workers").addEventListener("click", async () => {
    setText("connection", "stopping...");
    await postJson("/api/control/stop", {force: false});
    await initialLoad();
  });
  document.getElementById("force-stop-workers").addEventListener("click", async () => {
    setText("connection", "killing...");
    await postJson("/api/control/stop", {force: true});
    await initialLoad();
  });
  document.getElementById("killswitch-workers").addEventListener("click", async () => {
    setText("connection", "killswitch...");
    await postJson("/api/control/killswitch", {confirm: true});
    await initialLoad();
  });
  document.getElementById("save-coordination").addEventListener("click", saveCoordinationSettings);
  document.getElementById("ledger-reconcile").addEventListener("click", reconcileLedger);
  document.getElementById("theme-toggle").addEventListener("click", () => {
    applyTheme(document.body.classList.contains("theme-light") ? "dark" : "light");
  });
  for (const id of ["coord-role-input", "coord-online-input", "coord-primary-scheme-input", "coord-primary-host-input", "coord-primary-port-input", "coord-advertise-scheme-input", "coord-advertise-host-input", "coord-advertise-port-input", "coord-heartbeat-input"]) {
    document.getElementById(id).addEventListener("change", markCoordDirty);
    document.getElementById(id).addEventListener("input", markCoordDirty);
  }
  initialLoad().catch(() => setText("connection", "initial load failed"));
  const source = new EventSource("/events?interval=3");
  source.addEventListener("open", () => setText("connection", "live"));
  source.addEventListener("progress", event => render(JSON.parse(event.data)));
  source.addEventListener("error", () => setText("connection", "reconnecting..."));
</script>
</body>
</html>"""


def _sse_message(event: str, payload: dict[str, Any]) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, sort_keys=True, separators=(',', ':'))}\n\n"


def _detect_lan_ip() -> str:
    """Return best non-loopback IPv4 address for another device on LAN."""

    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            address = probe.getsockname()[0]
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    try:
        for address in socket.gethostbyname_ex(socket.gethostname())[2]:
            if address and not address.startswith("127."):
                return address
    except OSError:
        pass
    return "127.0.0.1"


def _display_host(bind_host: str) -> str:
    if bind_host in {"", "0.0.0.0", "::", "127.0.0.1", "localhost"}:
        return _detect_lan_ip()
    return bind_host


def _host_phase3_worker_pids() -> list[int]:
    """Return host worker PIDs for emergency command-center killswitch use."""

    if os.name != "nt":
        return []
    command = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.Name -eq 'mgba-spinda-phase3.exe' -or "
        "$_.CommandLine -like '*native_phase3_worker_pool.py*' -or "
        "$_.CommandLine -like '*phase3_ledger_worker_client.py*' } | "
        "Select-Object -ExpandProperty ProcessId"
    )
    try:
        completed = subprocess.run(
            ["powershell", "-NoProfile", "-Command", command],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    pids: list[int] = []
    current_pid = os.getpid()
    for line in completed.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid > 0 and pid != current_pid:
            pids.append(pid)
    return sorted(set(pids))


def create_app(
    *,
    output_dir: Path = DEFAULT_OUTPUT_DIR,
    pool_status_path: Path | None = None,
    watcher_status_path: Path | None = None,
    target_lanes: int = DEFAULT_TARGET_LANES,
    sample_interval_seconds: float = DEFAULT_SAMPLE_INTERVAL_SECONDS,
    zip_scan_interval_seconds: float = DEFAULT_ZIP_SCAN_INTERVAL_SECONDS,
    host_resource_interval_seconds: float = DEFAULT_HOST_RESOURCE_INTERVAL_SECONDS,
    display_url: str | None = None,
    controller: Phase3WorkerController | None = None,
    coordination: CoordinationConfig | None = None,
    coordination_settings_path: Path | None = None,
    ledger_client_status_path: Path | None = None,
    start_heartbeat: bool = True,
    ledger_path: Path | None = None,
) -> Flask:
    """Create Flask app with injectable paths for tests."""

    app = Flask(__name__)
    pool_status_path = pool_status_path or (output_dir / POOL_STATUS_NAME)
    watcher_status_path = watcher_status_path or (output_dir / WATCHER_STATUS_NAME)
    ledger_client_status_path = ledger_client_status_path or (output_dir / LEDGER_CLIENT_STATUS_NAME)
    controller = controller or Phase3WorkerController(output_dir=output_dir)
    coordination = coordination or CoordinationConfig()
    coordination_settings_path = coordination_settings_path or (output_dir / COORDINATION_SETTINGS_NAME)
    coordination_holder = {"config": coordination}
    subordinate_registry = SubordinateRegistry()
    lane_ledger = Phase3LaneLedger(
        path=ledger_path or (output_dir / "_phase3_lane_ledger.json"),
        output_dir=output_dir,
        target_lanes=target_lanes,
    )
    payload_cache = CommandCenterPayloadCache(
        output_dir=output_dir,
        pool_status_path=pool_status_path,
        watcher_status_path=watcher_status_path,
        target_lanes=target_lanes,
        sample_interval_seconds=sample_interval_seconds,
        zip_scan_interval_seconds=zip_scan_interval_seconds,
        host_resource_interval_seconds=host_resource_interval_seconds,
    )
    heartbeat_client = SubordinateHeartbeatClient(
        config=coordination,
        payload_cache=payload_cache,
        controller=controller,
        lane_ledger=lane_ledger,
        ledger_client_status_path=ledger_client_status_path,
    )
    if start_heartbeat:
        heartbeat_client.start()

    def live_payload(*, force: bool = False) -> dict[str, Any]:
        payload = payload_cache.get(force=force)
        payload["server"] = {"display_url": display_url}
        payload["control"] = controller.state()
        ledger_client = ledger_client_status_payload(ledger_client_status_path)
        payload["ledger_client"] = ledger_client
        coord = coordination_holder["config"]
        registered_devices = subordinate_registry.snapshot()
        snapshot = coord.snapshot(registered_devices)
        snapshot["settings_path"] = str(coordination_settings_path)
        snapshot["heartbeat_last_result"] = heartbeat_client.last_result
        payload["coordination"] = snapshot
        ledger_client_max_age = max(coord.heartbeat_seconds * 4, DEFAULT_LEDGER_CLIENT_STALE_SECONDS)
        if coord.role == "coordinator":
            local_worker_active_lanes = lane_ranges_to_id_set(
                _active_worker_lane_ranges(payload),
                target_lanes=target_lanes,
            )
            stale_device_ids = [
                str(device.get("device_id") or "")
                for device in registered_devices
                if str(device.get("device_id") or "")
                and _nonnegative_int(device.get("age_seconds")) > ledger_client_max_age
            ]
            for device_id in stale_device_ids:
                lane_ledger.release_inactive_claims(
                    device_id=device_id,
                    only_sources={"remote-active-heartbeat"},
                    reason="stale-subordinate-heartbeat",
                )
            if ledger_client.get("exists"):
                ledger_client_device_id = str(ledger_client.get("device_id") or coord.device_id)
                ledger_client_worker_id = str(ledger_client.get("worker_id") or "command-center-worker-pool")
                pool_running = _nonnegative_int(payload.get("workers", {}).get("running_workers"))
                if ledger_client_is_fresh(ledger_client, max_age_seconds=ledger_client_max_age):
                    keep_lanes = local_worker_active_lanes | lane_ranges_to_id_set(
                        ledger_client.get("active_lane_ranges") or [],
                        target_lanes=target_lanes,
                    )
                    lane_ledger.release_inactive_claims(
                        device_id=ledger_client_device_id,
                        worker_id=ledger_client_worker_id,
                        keep_lanes=keep_lanes,
                        reason="local-ledger-client-active-set-sync",
                    )
                elif local_worker_active_lanes:
                    lane_ledger.release_inactive_claims(
                        device_id=ledger_client_device_id,
                        worker_id=ledger_client_worker_id,
                        keep_lanes=local_worker_active_lanes,
                        reason="local-worker-active-set-sync-stale-ledger-client",
                    )
                elif pool_running == 0:
                    lane_ledger.release_inactive_claims(
                        device_id=ledger_client_device_id,
                        worker_id=ledger_client_worker_id,
                        reason="stale-local-ledger-client-no-workers",
                    )
        ledger_summary = lane_ledger.summary()
        payload["ledger"] = ledger_summary
        local_done_lanes = lane_ranges_to_id_set(
            payload.get("health", {}).get("complete_lane_ranges") or [],
            target_lanes=target_lanes,
        )
        ledger_done_lanes = lane_ranges_to_id_set(
            ledger_summary.get("done_ranges") or [],
            target_lanes=target_lanes,
        )
        known_done_lanes = local_done_lanes | ledger_done_lanes
        remote = trusted_remote_completion(
            registered_devices if coord.role == "coordinator" else [],
            target_lanes=target_lanes,
            local_done_lanes=known_done_lanes,
        )
        progress = payload.setdefault("progress", {})
        local_complete = _nonnegative_int(progress.get("complete_lanes"))
        target = _nonnegative_int(progress.get("target_lanes")) or target_lanes
        grand_complete = remote["grand_complete_lanes"] if coord.role == "coordinator" else local_complete
        remote_worker_count = sum(
            _nonnegative_int((device.get("workers") or {}).get("running_workers"))
            for device in (registered_devices if coord.role == "coordinator" else [])
            if _nonnegative_int(device.get("age_seconds")) <= max(coord.heartbeat_seconds * 4, 300.0)
        )
        workers = payload.setdefault("workers", {})
        local_running_workers = max(
            _nonnegative_int(workers.get("running_workers")),
            ledger_client_running_workers(ledger_client, max_age_seconds=ledger_client_max_age),
        )
        workers["local_running_workers"] = local_running_workers
        workers["remote_running_workers"] = remote_worker_count
        workers["combined_running_workers"] = local_running_workers + remote_worker_count
        workers["active_lane_ranges"] = sorted(
            set(
                _active_worker_lane_ranges(payload)
                + ledger_client_active_lane_ranges(ledger_client, max_age_seconds=ledger_client_max_age)
            )
        )
        workers["ledger_client_status"] = ledger_client.get("status")
        workers["ledger_client_age_seconds"] = ledger_client.get("age_seconds")
        progress.update(
            {
                "local_complete_lanes": local_complete,
                "local_completed_spindas": local_complete * SPINDAS_PER_LANE,
                "ledger_done_lanes": len(ledger_done_lanes),
                "trusted_remote_lanes": max(0, grand_complete - len(local_done_lanes)),
                "trusted_remote_spindas": max(0, grand_complete - len(local_done_lanes)) * SPINDAS_PER_LANE,
                "trusted_remote_device_count": remote["trusted_remote_device_count"],
                "trusted_remote_devices": remote["trusted_remote_devices"],
                "legacy_remote_count_without_ranges": remote["legacy_fallback_lanes"],
                "grand_total_trust_policy": remote["trust_policy"],
                "complete_lanes": grand_complete,
                "completed_spindas": grand_complete * SPINDAS_PER_LANE,
                "missing_lanes": max(0, target - grand_complete),
                "percent": round((grand_complete / target) * 100.0, 6) if target else 0.0,
            }
        )
        return payload

    @app.get("/")
    def index() -> str:
        return render_template_string(INDEX_HTML)

    @app.get("/api/status")
    def api_status():
        return jsonify(live_payload())

    @app.get("/api/workers")
    def api_workers():
        payload = payload_cache.get()
        return jsonify(payload["workers"])

    @app.get("/api/coordination/state")
    def api_coordination_state():
        coord = coordination_holder["config"]
        snapshot = coord.snapshot(subordinate_registry.snapshot())
        snapshot["settings_path"] = str(coordination_settings_path)
        snapshot["heartbeat_last_result"] = heartbeat_client.last_result
        return jsonify(snapshot)

    @app.post("/api/coordination/settings")
    def api_coordination_settings():
        body = request.get_json(silent=True) or request.form
        try:
            new_config = coordination_config_from_mapping(dict(body), base=coordination_holder["config"])
            coordination_holder["config"] = new_config
            if start_heartbeat:
                heartbeat_client.update_config(new_config)
            write_coordination_config(coordination_settings_path, new_config)
            payload_cache.get(force=True)
            snapshot = new_config.snapshot(subordinate_registry.snapshot())
            snapshot["settings_path"] = str(coordination_settings_path)
            snapshot["heartbeat_last_result"] = heartbeat_client.last_result
            return jsonify({"ok": True, "coordination": snapshot})
        except (TypeError, ValueError, OSError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/coordination/register")
    @app.post("/api/coordination/heartbeat")
    def api_coordination_heartbeat():
        coord = coordination_holder["config"]
        if coord.role != "coordinator" or not coord.online:
            return jsonify({"ok": False, "error": "coordination endpoint disabled"}), 409
        raw_body = request.get_json(silent=True) or {}
        body = dict(raw_body) if isinstance(raw_body, dict) else {}
        sanitized_workers = sanitize_remote_workers_for_stale_sidecar(
            body,
            target_lanes=target_lanes,
            max_age_seconds=max(coord.heartbeat_seconds * 4, DEFAULT_LEDGER_CLIENT_STALE_SECONDS),
        )
        if sanitized_workers is not (body.get("workers") if isinstance(body.get("workers"), dict) else None):
            body["workers"] = sanitized_workers
        record = subordinate_registry.update(body, remote_addr=request.remote_addr)
        import_result = lane_ledger.import_remote_status(
            device_id=str(body.get("device_id") or record.get("device_id") or request.remote_addr or ""),
            ledger=body.get("ledger") if isinstance(body.get("ledger"), dict) else {},
            workers=sanitized_workers,
            health=body.get("health") if isinstance(body.get("health"), dict) else {},
            lease_seconds=int(coord.heartbeat_seconds * 4),
            sync_active_claims=bool(
                isinstance(body.get("ledger_client"), dict)
                and (
                    body.get("ledger_client", {}).get("exists")
                    or body.get("ledger_client", {}).get("status")
                    or body.get("ledger_client", {}).get("active_lane_ranges")
                    or sanitized_workers.get("stale_ledger_client_ignored")
                )
            ),
        )
        return jsonify(
            {
                "ok": True,
                "device": record,
                "ledger_import": import_result,
                "coordination": coord.snapshot(subordinate_registry.snapshot()),
            }
        )

    @app.get("/api/ledger/status")
    def api_ledger_status():
        return jsonify(lane_ledger.summary())

    @app.post("/api/ledger/reconcile")
    def api_ledger_reconcile():
        result = lane_ledger.reconcile_completed_zips()
        result["summary"] = lane_ledger.summary()
        return jsonify({"ok": True, **result})

    @app.post("/api/ledger/claim")
    def api_ledger_claim():
        if coordination_holder["config"].role != "coordinator":
            return jsonify({"ok": False, "error": "ledger claims require coordinator role"}), 409
        body = request.get_json(silent=True) or request.form
        try:
            result = lane_ledger.claim(
                device_id=str(body.get("device_id") or ""),
                worker_id=str(body.get("worker_id") or "") or None,
                count=int(body.get("count") or 1),
                lanes=str(body.get("lanes") or "0x0000-0xFFFF"),
                lease_seconds=int(body.get("lease_seconds") or DEFAULT_LEDGER_LEASE_SECONDS),
            )
            result["summary"] = lane_ledger.summary()
            return jsonify({"ok": True, **result})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/ledger/heartbeat")
    def api_ledger_heartbeat():
        body = request.get_json(silent=True) or request.form
        lanes = body.get("lanes") or []
        if isinstance(lanes, str):
            lanes = parse_lanes_text(lanes)
            lanes = [f"0x{lane:04X}" for lane in lanes]
        try:
            result = lane_ledger.heartbeat(
                device_id=str(body.get("device_id") or ""),
                lanes=lanes,
                lease_seconds=int(body.get("lease_seconds") or DEFAULT_LEDGER_LEASE_SECONDS),
            )
            result["summary"] = lane_ledger.summary()
            return jsonify({"ok": True, **result})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/ledger/finish")
    def api_ledger_finish():
        body = request.get_json(silent=True) or request.form
        try:
            result = lane_ledger.finish(
                device_id=str(body.get("device_id") or ""),
                lane=str(body.get("lane") or ""),
                metadata=dict(body),
            )
            result["summary"] = lane_ledger.summary()
            return jsonify({"ok": True, **result})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/ledger/fail")
    def api_ledger_fail():
        body = request.get_json(silent=True) or request.form
        try:
            result = lane_ledger.fail(
                device_id=str(body.get("device_id") or ""),
                lane=str(body.get("lane") or ""),
                reason=str(body.get("reason") or ""),
                retryable=_bool_from_mapping(dict(body), "retryable", True),
            )
            result["summary"] = lane_ledger.summary()
            return jsonify({"ok": True, **result})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/ledger/release")
    def api_ledger_release():
        body = request.get_json(silent=True) or request.form
        lanes = body.get("lanes") or []
        if isinstance(lanes, str):
            lanes = [f"0x{lane:04X}" for lane in parse_lanes_text(lanes)]
        try:
            result = lane_ledger.release(
                device_id=str(body.get("device_id") or ""),
                lanes=lanes,
            )
            result["summary"] = lane_ledger.summary()
            return jsonify({"ok": True, **result})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/control/workers")
    def api_control_workers():
        body = request.get_json(silent=True) or request.form
        try:
            workers = int(body.get("workers", 0))
            lanes = str(body.get("lanes") or controller.lanes)
            launch_if_needed = str(body.get("launch_if_needed", "1")).lower() not in {"0", "false", "no"}
            if launch_if_needed and workers > 0:
                coord = coordination_holder["config"]
                claim_url = None
                if coord.online:
                    # Coordinator-owned local workers should claim from this
                    # process even if the operator edited the "primary" field
                    # for subordinate machines. Subordinates still use the
                    # configured primary coordinator URL.
                    claim_url = display_url.rstrip("/") if coord.role == "coordinator" else coord.primary_url
                result = controller.ensure_pool(
                    workers,
                    lanes=lanes,
                    claim_url=claim_url,
                    device_id=coord.device_id,
                    heartbeat_seconds=coord.heartbeat_seconds,
                )
            else:
                result = controller.write_desired_workers(workers, shutdown=False, lanes=lanes)
            payload_cache.get(force=True)
            return jsonify({"ok": True, "control": result, "controller": controller.state()})
        except (TypeError, ValueError) as exc:
            return jsonify({"ok": False, "error": str(exc)}), 400

    @app.post("/api/control/stop")
    def api_control_stop():
        body = request.get_json(silent=True) or request.form
        force = str(body.get("force", "0")).lower() in {"1", "true", "yes"}
        pids: list[int] = []
        if force:
            payload = payload_cache.get(force=True)
            for worker in payload.get("workers", {}).get("running", []):
                try:
                    pids.append(int(worker.get("pid") or 0))
                except (TypeError, ValueError):
                    pass
        result = controller.stop(force_pids=pids if force else ())
        payload_cache.get(force=True)
        return jsonify({"ok": True, "control": result, "controller": controller.state()})

    @app.post("/api/control/killswitch")
    def api_control_killswitch():
        """Emergency stop: request pool shutdown and kill known worker PIDs."""

        pids: set[int] = set(_host_phase3_worker_pids())
        payload = payload_cache.get(force=True)
        for worker in payload.get("workers", {}).get("running", []):
            try:
                pid = int(worker.get("pid") or 0)
            except (TypeError, ValueError):
                continue
            if pid > 0:
                pids.add(pid)
        result = controller.stop(force_pids=sorted(pids))
        payload_cache.get(force=True)
        return jsonify(
            {
                "ok": True,
                "killswitch": True,
                "pid_candidates": sorted(pids),
                "control": result,
                "controller": controller.state(),
            }
        )

    @app.get("/api/control/state")
    def api_control_state():
        return jsonify(controller.state())

    @app.get("/events")
    def events() -> Response:
        interval = request.args.get("interval", "3")
        try:
            interval_seconds = min(60.0, max(0.5, float(interval)))
        except ValueError:
            interval_seconds = 3.0

        def stream():
            yield "retry: 2500\n\n"
            while True:
                yield _sse_message("progress", live_payload())
                time.sleep(interval_seconds)

        return Response(stream(), mimetype="text/event-stream")

    return app


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-center CLI options."""

    parser = argparse.ArgumentParser(
        description="Run read-only Flask command center for Spinda Phase 3 workers.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--folder", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--pool-status", type=Path)
    parser.add_argument("--pool-control", type=Path)
    parser.add_argument("--watcher-status", type=Path)
    parser.add_argument("--ledger-client-status", type=Path)
    parser.add_argument("--coordination-settings", type=Path)
    parser.add_argument("--ledger", type=Path, help="Persistent lane claim ledger JSON.")
    parser.add_argument("--worker-pool-script", type=Path, default=DEFAULT_WORKER_POOL_SCRIPT)
    parser.add_argument("--ledger-worker-client-script", type=Path, default=DEFAULT_LEDGER_WORKER_CLIENT_SCRIPT)
    parser.add_argument("--python-exe", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--cache-dir", type=Path)
    parser.add_argument("--lanes", default=DEFAULT_LANES)
    parser.add_argument("--control-max-workers", type=int, default=DEFAULT_CONTROL_MAX_WORKERS)
    parser.add_argument("--bundle-size", type=int, default=DEFAULT_BUNDLE_SIZE)
    parser.add_argument("--zip-method", choices=("deflate", "store"), default=DEFAULT_ZIP_METHOD)
    parser.add_argument("--status-write-seconds", type=float, default=DEFAULT_STATUS_WRITE_SECONDS)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--display-host")
    parser.add_argument("--target-lanes", type=int, default=DEFAULT_TARGET_LANES)
    parser.add_argument(
        "--sample-interval",
        type=float,
        default=DEFAULT_SAMPLE_INTERVAL_SECONDS,
        help="Minimum seconds between cheap worker-status refreshes shared by JSON and SSE clients.",
    )
    parser.add_argument(
        "--zip-scan-interval",
        type=float,
        default=DEFAULT_ZIP_SCAN_INTERVAL_SECONDS,
        help="Minimum seconds between final ZIP folder scans. Larger values reduce overhead once many lanes exist.",
    )
    parser.add_argument(
        "--host-resource-interval",
        type=float,
        default=DEFAULT_HOST_RESOURCE_INTERVAL_SECONDS,
        help="Minimum seconds between CPU/RAM/disk samples. These values are operator context, not worker control.",
    )
    parser.add_argument(
        "--role",
        choices=("coordinator", "subordinate"),
        default=COMMAND_CENTER_ROLE,
        help="Multi-device role. Coordinator receives subordinate heartbeats; subordinate reports local progress upstream.",
    )
    online_group = parser.add_mutually_exclusive_group()
    online_group.add_argument(
        "--online",
        dest="online",
        action="store_true",
        help="Enable multi-device coordination traffic.",
    )
    online_group.add_argument(
        "--offline",
        dest="online",
        action="store_false",
        help="Disable multi-device coordination traffic.",
    )
    parser.set_defaults(online=DEFAULT_COORDINATION_ONLINE)
    parser.add_argument("--primary-scheme", choices=("http", "https"), default=DEFAULT_COORDINATION_PRIMARY_SCHEME)
    parser.add_argument("--primary-host", default=DEFAULT_COORDINATION_PRIMARY_HOST)
    parser.add_argument("--primary-port", type=int, default=DEFAULT_COORDINATION_PRIMARY_PORT)
    parser.add_argument("--advertise-scheme", choices=("http", "https"), default=DEFAULT_COORDINATION_ADVERTISE_SCHEME)
    parser.add_argument(
        "--advertise-host",
        default=DEFAULT_COORDINATION_ADVERTISE_HOST,
        help="Exact IP address other devices should use for this command center.",
    )
    parser.add_argument("--advertise-port", type=int, default=DEFAULT_COORDINATION_ADVERTISE_PORT)
    parser.add_argument(
        "--heartbeat-seconds",
        type=float,
        default=DEFAULT_COORDINATION_HEARTBEAT_SECONDS,
        help="Subordinate heartbeat interval when online mode is enabled.",
    )
    parser.add_argument("--debug", action="store_true")
    args = parser.parse_args(argv)
    args.pool_status = args.pool_status or (args.folder / POOL_STATUS_NAME)
    args.pool_control = args.pool_control or (args.folder / POOL_CONTROL_NAME)
    args.watcher_status = args.watcher_status or (args.folder / WATCHER_STATUS_NAME)
    args.ledger_client_status = args.ledger_client_status or (args.folder / LEDGER_CLIENT_STATUS_NAME)
    args.cache_dir = args.cache_dir or (args.folder / "_cache")
    return args


def main(argv: Iterable[str] | None = None) -> int:
    """Run Flask server for operator use."""

    args = parse_args(argv)
    if args.bundle_size < 1:
        raise SystemExit("--bundle-size must be positive")
    if args.status_write_seconds < 0:
        raise SystemExit("--status-write-seconds must be non-negative")
    if args.primary_port < 1 or args.primary_port > 65535:
        raise SystemExit("--primary-port must be between 1 and 65535")
    if args.advertise_port and (args.advertise_port < 1 or args.advertise_port > 65535):
        raise SystemExit("--advertise-port must be between 1 and 65535")
    if args.heartbeat_seconds < 5:
        raise SystemExit("--heartbeat-seconds must be at least 5")
    display_host = args.display_host or _display_host(args.host)
    display_url = f"http://{display_host}:{args.port}/"
    advertise_host = args.advertise_host or display_host
    advertise_port = args.advertise_port or args.port
    coordination_settings = args.coordination_settings or (args.folder / COORDINATION_SETTINGS_NAME)
    base_coordination = CoordinationConfig(
        role=_normalize_role(args.role),
        online=bool(args.online),
        primary_scheme=_normalize_scheme(args.primary_scheme),
        primary_host=args.primary_host,
        primary_port=args.primary_port,
        advertise_scheme=_normalize_scheme(args.advertise_scheme),
        advertise_host=advertise_host,
        advertise_port=advertise_port,
        heartbeat_seconds=args.heartbeat_seconds,
    )
    coordination = load_coordination_config(coordination_settings, base_coordination)
    app = create_app(
        output_dir=args.folder,
        pool_status_path=args.pool_status,
        watcher_status_path=args.watcher_status,
        target_lanes=args.target_lanes,
        sample_interval_seconds=args.sample_interval,
        zip_scan_interval_seconds=args.zip_scan_interval,
        host_resource_interval_seconds=args.host_resource_interval,
        display_url=display_url,
        controller=Phase3WorkerController(
            python_exe=args.python_exe,
            worker_pool_script=args.worker_pool_script,
            ledger_worker_client_script=args.ledger_worker_client_script,
            output_dir=args.folder,
            cache_dir=args.cache_dir,
            pool_control_path=args.pool_control,
            ledger_client_status_path=args.ledger_client_status,
            lanes=args.lanes,
            control_max_workers=args.control_max_workers,
            bundle_size=args.bundle_size,
            zip_method=args.zip_method,
            status_write_seconds=args.status_write_seconds,
        ),
        coordination=coordination,
        coordination_settings_path=coordination_settings,
        ledger_client_status_path=args.ledger_client_status,
        ledger_path=args.ledger or (args.folder / LEDGER_NAME),
    )
    print(f"Spinda Phase 3 command center: {display_url}", flush=True)
    print(
        "Coordination: "
        f"role={coordination.role} online={coordination.online} "
        f"primary={coordination.primary_url} "
        f"advertise={coordination.advertise_url}",
        flush=True,
    )
    print(f"Binding Flask server on {args.host}:{args.port}", flush=True)
    app.run(host=args.host, port=args.port, debug=args.debug, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
