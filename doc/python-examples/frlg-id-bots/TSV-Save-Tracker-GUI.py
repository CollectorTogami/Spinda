#!/usr/bin/env python3
r"""LAN-visible Flask dashboard for the 8192 FR/LG TSV save files.

This GUI is intentionally read-only and solo-compute. It binds to ``0.0.0.0``
by default so another device on the same LAN can open ``http://<pc-ip>:8765/``.
It still only scans the local TSV save folder, reads the local SID ledger when
present, and never starts workers, queues, or multi-PC coordination. Flask may
handle multiple browser/API requests at once so a held browser connection cannot
block status checks.

Default save folder:

    <repo-root>\TSVs

Expected save names:

    TSV-0000-sid-00003.sav
    TSV-8191-sid-65535.sav

For TID 0, the TSV is ``SID >> 3``. The tracker uses that rule to flag
misnamed or mismatched save files.
"""

from __future__ import annotations

import argparse
import math
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import frlg_id_bot_common as common  # noqa: E402


DEFAULT_SAVE_DIR = Path(__file__).resolve().parents[3] / "TSVs"
DEFAULT_LEDGER_NAME = "_sid_shiny_value_ledger_tid_0x0000.json"
DEFAULT_STATUS_LOG_NAME = "_sid_live_status.log"
DEFAULT_BIND_HOST = "0.0.0.0"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_PAGE_SIZE = 256
SAVE_NAME_RE = re.compile(r"^TSV-(?P<tsv>\d{4})-sid-(?P<sid>\d{5})\.sav$", re.IGNORECASE)
RECENT_SAVE_LIMIT = 12
RATE_WINDOW_SIZE = 50
LIVE_LOG_ACTIVE_SECONDS = 30.0
LIVE_LOG_TAIL_BYTES = 64 * 1024


def discover_lan_ipv4_addresses() -> tuple[str, ...]:
    """Return likely LAN IPv4 addresses for browser URLs.

    Binding to ``0.0.0.0`` makes Flask listen on every interface. This helper is
    display-only: it gives the operator concrete ``http://ip:port`` URLs without
    changing what the server binds to.
    """

    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            addresses.add(str(probe.getsockname()[0]))
    except OSError:
        pass

    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(info[4][0]))
    except OSError:
        pass

    return tuple(
        sorted(
            address
            for address in addresses
            if not address.startswith("127.") and not address.startswith("169.254.")
        )
    )


def tracker_urls(bind_host: str, port: int) -> tuple[str, ...]:
    """Return URLs worth printing for the selected bind address."""

    clean_host = str(bind_host).strip() or DEFAULT_BIND_HOST
    if clean_host in {"0.0.0.0", "::"}:
        urls = [f"http://{LOOPBACK_HOST}:{int(port)}/"]
        urls.extend(f"http://{address}:{int(port)}/" for address in discover_lan_ipv4_addresses())
        if len(urls) == 1:
            urls.append(f"http://<this-pc-ip>:{int(port)}/")
        return tuple(dict.fromkeys(urls))
    return (f"http://{clean_host}:{int(port)}/",)


@dataclass(frozen=True)
class ParsedSaveFile:
    """One save filename parsed from the TSV folder."""

    path: Path
    tsv: int | None
    sid: int | None
    valid: bool
    reason: str | None = None

    @property
    def mapped_tsv(self) -> int | None:
        return None if self.sid is None else self.sid >> 3

    def to_json(self) -> dict[str, Any]:
        """Return API-safe JSON."""

        return {
            "path": str(self.path),
            "name": self.path.name,
            "tsv": self.tsv,
            "sid": self.sid,
            "mapped_tsv": self.mapped_tsv,
            "valid": self.valid,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class TrackedTsvRow:
    """Dashboard row for one expected TSV."""

    tsv: int
    status: str
    sid: int | None
    save_name: str | None
    save_path: str | None
    save_exists: bool
    duplicate_count: int
    ledger_done: bool
    ledger_sid: int | None
    ledger_save_path: str | None
    save_sha1: str | None
    wait_frames: int | None
    rng_advance: int | None
    note: str | None
    error: str | None

    @property
    def tsv_text(self) -> str:
        return f"{self.tsv:04d}"

    @property
    def sid_text(self) -> str:
        return "" if self.sid is None else f"{self.sid:05d}"

    def to_json(self) -> dict[str, Any]:
        """Return API-safe JSON."""

        return {
            "tsv": self.tsv,
            "tsv_text": self.tsv_text,
            "status": self.status,
            "sid": self.sid,
            "sid_text": self.sid_text,
            "save_name": self.save_name,
            "save_path": self.save_path,
            "save_exists": self.save_exists,
            "duplicate_count": self.duplicate_count,
            "ledger_done": self.ledger_done,
            "ledger_sid": self.ledger_sid,
            "ledger_save_path": self.ledger_save_path,
            "save_sha1": self.save_sha1,
            "wait_frames": self.wait_frames,
            "rng_advance": self.rng_advance,
            "note": self.note,
            "error": self.error,
        }


@dataclass(frozen=True)
class TrackerSnapshot:
    """Full read-only view of the local TSV save bank."""

    save_dir: Path
    ledger_path: Path
    ledger_exists: bool
    rows: tuple[TrackedTsvRow, ...]
    invalid_files: tuple[ParsedSaveFile, ...]
    mismatched_files: tuple[ParsedSaveFile, ...]
    load_errors: tuple[str, ...]

    @property
    def summary(self) -> dict[str, Any]:
        completed = sum(1 for row in self.rows if row.save_exists)
        duplicates = sum(1 for row in self.rows if row.duplicate_count > 1)
        ledger_missing = sum(1 for row in self.rows if row.status == "ledger-missing")
        row_errors = sum(1 for row in self.rows if row.status == "error")
        total = len(self.rows)
        percent = (completed / total * 100.0) if total else 0.0
        return {
            "total": total,
            "completed": completed,
            "missing": total - completed,
            "percent": round(percent, 2),
            "duplicates": duplicates,
            "ledger_missing": ledger_missing,
            "row_errors": row_errors,
            "invalid_files": len(self.invalid_files),
            "mismatched_files": len(self.mismatched_files),
            "load_errors": len(self.load_errors),
            "save_dir": str(self.save_dir),
            "ledger_path": str(self.ledger_path),
            "ledger_exists": self.ledger_exists,
        }

    def to_json(self) -> dict[str, Any]:
        """Return compact API-safe JSON."""

        progress = build_progress_panel(self)
        return {
            "summary": self.summary,
            "progress": progress,
            "recent_saves": progress["recent_saves"],
            "live": read_live_status(self.save_dir),
            "invalid_files": [entry.to_json() for entry in self.invalid_files],
            "mismatched_files": [entry.to_json() for entry in self.mismatched_files],
            "load_errors": list(self.load_errors),
        }


def expected_save_name(tsv: int, sid: int) -> str:
    """Return the decimal filename used by the SID bot."""

    return f"TSV-{int(tsv):04d}-sid-{int(sid):05d}.sav"


def parse_save_filename(path: str | Path) -> ParsedSaveFile:
    """Parse and validate one ``TSV-xxxx-sid-yyyyy.sav`` filename."""

    save_path = Path(path)
    match = SAVE_NAME_RE.match(save_path.name)
    if not match:
        return ParsedSaveFile(save_path, None, None, False, "name")

    tsv = int(match.group("tsv"))
    sid = int(match.group("sid"))
    if not 0 <= tsv < common.SHINY_VALUE_COUNT:
        return ParsedSaveFile(save_path, tsv, sid, False, "tsv-range")
    if not 0 <= sid <= common.UINT16_MASK:
        return ParsedSaveFile(save_path, tsv, sid, False, "sid-range")
    if (sid >> 3) != tsv:
        return ParsedSaveFile(save_path, tsv, sid, False, "tid0-tsv-sid-mismatch")
    return ParsedSaveFile(save_path, tsv, sid, True)


def resolve_ledger_path(save_dir: Path, ledger_path: Path | None) -> Path:
    """Return explicit ledger path or default ledger inside the save folder."""

    if ledger_path is not None:
        return Path(ledger_path)
    return Path(save_dir) / DEFAULT_LEDGER_NAME


def resolve_save_path(save_dir: Path, raw_path: str | None) -> Path | None:
    """Resolve a ledger save path without assuming it is absolute."""

    if not raw_path:
        return None
    save_path = Path(raw_path)
    if save_path.is_absolute():
        return save_path
    return Path(save_dir) / save_path


def scan_save_dir(save_dir: Path) -> tuple[dict[int, list[ParsedSaveFile]], list[ParsedSaveFile], list[ParsedSaveFile]]:
    """Scan local ``.sav`` files and group valid TID-0 saves by TSV."""

    valid_by_tsv: dict[int, list[ParsedSaveFile]] = {}
    invalid_files: list[ParsedSaveFile] = []
    mismatched_files: list[ParsedSaveFile] = []
    if not save_dir.exists():
        return valid_by_tsv, invalid_files, mismatched_files

    for path in sorted(save_dir.glob("*.sav")):
        parsed = parse_save_filename(path)
        if parsed.valid and parsed.tsv is not None:
            valid_by_tsv.setdefault(parsed.tsv, []).append(parsed)
        elif parsed.reason == "tid0-tsv-sid-mismatch":
            mismatched_files.append(parsed)
        else:
            invalid_files.append(parsed)

    return valid_by_tsv, invalid_files, mismatched_files


def load_ledger_rows(ledger_path: Path) -> tuple[dict[int, common.SidLedgerEntry], list[str]]:
    """Load ledger rows by TSV, returning errors instead of crashing the GUI."""

    if not ledger_path.exists():
        return {}, []
    try:
        ledger = common.read_json(ledger_path)
        if ledger.get("format") != common.SID_LEDGER_FORMAT:
            return {}, [f"unsupported ledger format: {ledger.get('format')!r}"]
        rows = common.ledger_entries_by_shiny_value(ledger)
    except Exception as exc:  # noqa: BLE001 - UI should show bad ledger state.
        return {}, [f"could not read ledger: {exc}"]
    return rows, []


def choose_row_status(
    *,
    files: list[ParsedSaveFile],
    ledger_entry: common.SidLedgerEntry | None,
    ledger_save_path: Path | None,
) -> tuple[str, bool, int | None, str | None, str | None]:
    """Choose dashboard status and primary save display for one TSV."""

    if files:
        first = files[0]
        status = "duplicate" if len(files) > 1 else "done"
        return status, True, first.sid, first.path.name, str(first.path)

    if ledger_entry and ledger_entry.done:
        if ledger_save_path is not None and ledger_save_path.is_file():
            return "done", True, ledger_entry.sid, ledger_save_path.name, str(ledger_save_path)
        return (
            "ledger-missing",
            False,
            ledger_entry.sid,
            ledger_save_path.name if ledger_save_path is not None else None,
            str(ledger_save_path) if ledger_save_path is not None else None,
        )

    if ledger_entry and ledger_entry.error:
        return "error", False, ledger_entry.sid, None, None

    return "missing", False, ledger_entry.sid if ledger_entry else None, None, None


def build_snapshot(save_dir: Path = DEFAULT_SAVE_DIR, ledger_path: Path | None = None) -> TrackerSnapshot:
    """Build a read-only snapshot of all 8192 expected TSV saves."""

    save_dir = Path(save_dir)
    resolved_ledger = resolve_ledger_path(save_dir, ledger_path)
    valid_by_tsv, invalid_files, mismatched_files = scan_save_dir(save_dir)
    ledger_rows, load_errors = load_ledger_rows(resolved_ledger)

    rows: list[TrackedTsvRow] = []
    for tsv in range(common.SHINY_VALUE_COUNT):
        files = valid_by_tsv.get(tsv, [])
        ledger_entry = ledger_rows.get(tsv)
        ledger_save_path = resolve_save_path(save_dir, ledger_entry.save_path) if ledger_entry else None
        status, save_exists, sid, save_name, save_path = choose_row_status(
            files=files,
            ledger_entry=ledger_entry,
            ledger_save_path=ledger_save_path,
        )
        rows.append(
            TrackedTsvRow(
                tsv=tsv,
                status=status,
                sid=sid,
                save_name=save_name,
                save_path=save_path,
                save_exists=save_exists,
                duplicate_count=len(files),
                ledger_done=bool(ledger_entry.done) if ledger_entry else False,
                ledger_sid=ledger_entry.sid if ledger_entry else None,
                ledger_save_path=str(ledger_save_path) if ledger_save_path is not None else None,
                save_sha1=ledger_entry.save_sha1 if ledger_entry else None,
                wait_frames=ledger_entry.wait_frames if ledger_entry else None,
                rng_advance=ledger_entry.rng_advance if ledger_entry else None,
                note=ledger_entry.note if ledger_entry else None,
                error=ledger_entry.error if ledger_entry else None,
            )
        )

    return TrackerSnapshot(
        save_dir=save_dir,
        ledger_path=resolved_ledger,
        ledger_exists=resolved_ledger.exists(),
        rows=tuple(rows),
        invalid_files=tuple(invalid_files),
        mismatched_files=tuple(mismatched_files),
        load_errors=tuple(load_errors),
    )


def filter_rows(rows: tuple[TrackedTsvRow, ...], *, status: str, query: str) -> list[TrackedTsvRow]:
    """Filter rows for the HTML table and JSON row API."""

    filtered = list(rows)
    if status != "all":
        filtered = [row for row in filtered if row.status == status]

    query = query.strip().lower()
    if not query:
        return filtered

    numeric_query: int | None = None
    try:
        numeric_query = common.parse_int(query)
    except ValueError:
        numeric_query = None

    def matches(row: TrackedTsvRow) -> bool:
        if numeric_query is not None and numeric_query in {row.tsv, row.sid, row.ledger_sid}:
            return True
        text = " ".join(
            item
            for item in (
                row.tsv_text,
                row.sid_text,
                row.status,
                row.save_name or "",
                row.save_path or "",
                row.ledger_save_path or "",
                row.error or "",
                row.note or "",
            )
            if item
        ).lower()
        return query in text

    return [row for row in filtered if matches(row)]


def duration_text(seconds: float | None) -> str:
    """Format a duration for the live browser panel."""

    if seconds is None or not math.isfinite(float(seconds)) or seconds < 0:
        return "unknown"
    total = int(round(float(seconds)))
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    if minutes:
        return f"{minutes}m {secs:02d}s"
    return f"{secs}s"


def local_time_text(epoch_seconds: float | None) -> str:
    """Return a short local timestamp for the live panel."""

    if epoch_seconds is None:
        return "unknown"
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(float(epoch_seconds)))


def latest_valid_save_entries(save_dir: Path) -> list[dict[str, Any]]:
    """Return one latest valid save entry per TSV, sorted by mtime."""

    latest_by_tsv: dict[int, dict[str, Any]] = {}
    if not save_dir.exists():
        return []
    for path in save_dir.glob("TSV-*.sav"):
        parsed = parse_save_filename(path)
        if not parsed.valid or parsed.tsv is None:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        entry = {
            "name": path.name,
            "path": str(path),
            "tsv": parsed.tsv,
            "sid": parsed.sid,
            "mtime": float(stat.st_mtime),
            "mtime_local": local_time_text(stat.st_mtime),
            "size": int(stat.st_size),
        }
        old = latest_by_tsv.get(parsed.tsv)
        if old is None or entry["mtime"] > old["mtime"]:
            latest_by_tsv[parsed.tsv] = entry
    return sorted(latest_by_tsv.values(), key=lambda item: float(item["mtime"]))


def rate_from_entries(entries: list[dict[str, Any]]) -> float | None:
    """Estimate save rate from the newest bounded window of valid saves."""

    if len(entries) < 2:
        return None
    window = entries[-min(len(entries), RATE_WINDOW_SIZE) :]
    elapsed = float(window[-1]["mtime"]) - float(window[0]["mtime"])
    if elapsed <= 0:
        return None
    return (len(window) - 1) / elapsed * 3600.0


def read_log_tail_lines(path: Path, *, max_bytes: int = LIVE_LOG_TAIL_BYTES) -> list[str]:
    """Read only the end of a live status log.

    The browser polls status every few seconds during long SID runs. Reading a
    multi-hour log from byte zero each time can make the dashboard sluggish, so
    keep only a bounded tail. The first returned line may be partial; callers use
    the last line and newest save marker, which remain accurate.
    """

    limit = max(1, int(max_bytes))
    with Path(path).open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read(limit).decode("utf-8", errors="replace").splitlines()


def read_live_status(save_dir: Path) -> dict[str, Any]:
    """Read the bounded SID bot live log tail without failing the dashboard."""

    path = Path(save_dir) / DEFAULT_STATUS_LOG_NAME
    if not path.exists():
        return {
            "exists": False,
            "active": False,
            "path": str(path),
            "age_seconds": None,
            "last_line": "",
        }
    try:
        stat = path.stat()
        lines = read_log_tail_lines(path)
    except OSError as exc:
        return {
            "exists": True,
            "active": False,
            "path": str(path),
            "age_seconds": None,
            "last_line": f"could not read status log: {exc}",
        }
    age = max(0.0, time.time() - float(stat.st_mtime))
    last_line = lines[-1] if lines else ""
    last_saved = next((line for line in reversed(lines) if " saved " in line), "")
    return {
        "exists": True,
        "active": age <= LIVE_LOG_ACTIVE_SECONDS,
        "path": str(path),
        "age_seconds": round(age, 1),
        "last_line": last_line,
        "last_saved_line": last_saved,
        "tail_bytes": LIVE_LOG_TAIL_BYTES,
        "updated_at_local": local_time_text(stat.st_mtime),
    }


def build_progress_panel(snapshot: TrackerSnapshot) -> dict[str, Any]:
    """Build live progress data for the browser's real-time panel."""

    summary = snapshot.summary
    now = time.time()
    entries = latest_valid_save_entries(snapshot.save_dir)
    recent = list(reversed(entries[-RECENT_SAVE_LIMIT:]))
    rate = rate_from_entries(entries)
    eta_seconds = None
    if rate and rate > 0:
        eta_seconds = float(summary["missing"]) / rate * 3600.0
    finish_epoch = now + eta_seconds if eta_seconds is not None else None
    last = entries[-1] if entries else None
    last_age = None if last is None else max(0.0, now - float(last["mtime"]))
    return {
        "updated_at_epoch": now,
        "updated_at_local": local_time_text(now),
        "completed": summary["completed"],
        "missing": summary["missing"],
        "total": summary["total"],
        "percent": summary["percent"],
        "rate_per_hour": None if rate is None else round(rate, 1),
        "eta_seconds": None if eta_seconds is None else round(eta_seconds, 1),
        "eta_text": duration_text(eta_seconds),
        "finish_time_local": local_time_text(finish_epoch),
        "last_save_name": "" if last is None else str(last["name"]),
        "last_save_age_seconds": None if last_age is None else round(last_age, 1),
        "last_save_age_text": duration_text(last_age),
        "recent_saves": recent,
        "rate_basis": f"last {min(len(entries), RATE_WINDOW_SIZE)} saves",
    }


HTML_TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>TSV Save Tracker</title>
  <style>
    :root {
      color-scheme: light;
      --bg: #f7f8fa;
      --panel: #ffffff;
      --text: #1f2328;
      --muted: #667085;
      --line: #d7dde5;
      --done: #1f7a3a;
      --missing: #a23a2a;
      --warn: #9a6400;
      --info: #2459a6;
      --error: #8a1f34;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.4 "Segoe UI", system-ui, -apple-system, sans-serif;
    }
    header {
      background: #ffffff;
      border-bottom: 1px solid var(--line);
      padding: 20px 24px 16px;
    }
    main { padding: 18px 24px 28px; }
    h1 { margin: 0 0 12px; font-size: 24px; font-weight: 650; letter-spacing: 0; }
    .live-grid {
      display: grid;
      grid-template-columns: minmax(220px, 1.5fr) repeat(3, minmax(140px, 1fr));
      gap: 10px;
      margin-bottom: 12px;
    }
    .hero-number {
      font-size: 34px;
      line-height: 1;
      font-weight: 750;
      letter-spacing: 0;
    }
    .hero-total { color: var(--muted); font-size: 16px; font-weight: 600; margin-left: 4px; }
    .meta {
      display: grid;
      grid-template-columns: repeat(4, minmax(130px, 1fr));
      gap: 10px;
      margin-bottom: 14px;
    }
    .metric {
      border: 1px solid var(--line);
      background: var(--panel);
      border-radius: 8px;
      padding: 10px 12px;
      min-width: 0;
    }
    .metric strong { display: block; font-size: 22px; line-height: 1.1; }
    .metric span { color: var(--muted); font-size: 12px; }
    .live-pill {
      display: inline-flex;
      align-items: center;
      justify-content: center;
      min-width: 92px;
      border-radius: 999px;
      padding: 3px 10px;
      font-size: 12px;
      font-weight: 700;
      color: #fff;
      background: var(--warn);
    }
    .live-pill.active { background: var(--done); }
    .live-pill.idle { background: var(--warn); color: #1f2328; }
    .recent-panel {
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
      margin-bottom: 12px;
    }
    .recent-list {
      margin: 0;
      padding-left: 18px;
      max-height: 150px;
      overflow: auto;
    }
    .recent-list li { margin: 2px 0; overflow-wrap: anywhere; }
    .paths {
      display: grid;
      grid-template-columns: 1fr;
      gap: 4px;
      color: var(--muted);
      overflow-wrap: anywhere;
    }
    .progress {
      width: 100%;
      height: 12px;
      background: #e7ebf0;
      border-radius: 999px;
      overflow: hidden;
      border: 1px solid var(--line);
    }
    .progress div { height: 100%; background: var(--done); width: {{ snapshot.summary.percent }}%; }
    .toolbar {
      display: flex;
      flex-wrap: wrap;
      align-items: center;
      gap: 8px;
      margin-bottom: 12px;
    }
    .tabs { display: flex; flex-wrap: wrap; gap: 6px; }
    .tab, button {
      appearance: none;
      border: 1px solid var(--line);
      background: #fff;
      color: var(--text);
      border-radius: 7px;
      padding: 7px 10px;
      text-decoration: none;
      font: inherit;
      cursor: pointer;
    }
    .tab.active { border-color: var(--info); color: var(--info); background: #eef5ff; }
    form { margin-left: auto; display: flex; gap: 6px; min-width: min(430px, 100%); }
    input {
      border: 1px solid var(--line);
      border-radius: 7px;
      padding: 8px 10px;
      font: inherit;
      flex: 1;
      min-width: 0;
    }
    table {
      width: 100%;
      border-collapse: collapse;
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      overflow: hidden;
    }
    th, td {
      border-bottom: 1px solid var(--line);
      padding: 8px 10px;
      text-align: left;
      vertical-align: top;
      overflow-wrap: anywhere;
    }
    th {
      background: #eef1f5;
      font-weight: 650;
      color: #374151;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    tr:last-child td { border-bottom: 0; }
    .status {
      display: inline-block;
      min-width: 92px;
      text-align: center;
      border-radius: 999px;
      padding: 2px 8px;
      font-size: 12px;
      font-weight: 650;
    }
    .status-done { color: #fff; background: var(--done); }
    .status-missing { color: #fff; background: var(--missing); }
    .status-duplicate, .status-ledger-missing { color: #1f2328; background: #f4c95d; }
    .status-error { color: #fff; background: var(--error); }
    .pager {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 10px;
      margin: 12px 0;
      color: var(--muted);
    }
    .pager a { color: var(--info); text-decoration: none; }
    .issues {
      margin-top: 14px;
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 12px;
    }
    .issue-box {
      background: #fff;
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 10px 12px;
      min-width: 0;
    }
    .issue-box h2 { margin: 0 0 8px; font-size: 15px; }
    .issue-box ul { margin: 0; padding-left: 18px; max-height: 210px; overflow: auto; }
    code { font-family: Consolas, "Liberation Mono", monospace; }
    @media (max-width: 900px) {
      .live-grid { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
      .meta { grid-template-columns: repeat(2, minmax(130px, 1fr)); }
      .recent-panel { grid-template-columns: 1fr; }
      form { margin-left: 0; width: 100%; }
      .issues { grid-template-columns: 1fr; }
      th:nth-child(5), td:nth-child(5), th:nth-child(6), td:nth-child(6) { display: none; }
    }
  </style>
</head>
<body>
  <header>
    <h1>TSV Save Tracker</h1>
    <div class="live-grid">
      <div class="metric">
        <span>Completed TSVs</span>
        <div class="hero-number"><span id="complete-count">{{ progress.completed }}</span><span class="hero-total">/ <span id="total-count">{{ progress.total }}</span></span></div>
      </div>
      <div class="metric"><strong id="rate-hour">{{ progress.rate_per_hour or "warming" }}</strong><span> saves/hour</span></div>
      <div class="metric"><strong id="eta-text">{{ progress.eta_text }}</strong><span> ETA</span></div>
      <div class="metric"><strong id="finish-time">{{ progress.finish_time_local }}</strong><span> finish time</span></div>
    </div>
    <div class="meta">
      <div class="metric"><strong id="missing-count">{{ snapshot.summary.missing }}</strong><span>missing</span></div>
      <div class="metric"><strong id="percent-count">{{ snapshot.summary.percent }}%</strong><span>progress</span></div>
      <div class="metric"><strong id="duplicates-count">{{ snapshot.summary.duplicates }}</strong><span>duplicates</span></div>
      <div class="metric"><strong id="live-state"><span class="live-pill {{ 'active' if live.active else 'idle' }}">{{ 'active' if live.active else 'idle' }}</span></strong><span>live log</span></div>
    </div>
    <div class="progress" aria-label="progress"><div id="progress-fill"></div></div>
    <div class="paths">
      <div><code>{{ snapshot.save_dir }}</code></div>
      <div><code>{{ snapshot.ledger_path }}</code></div>
    </div>
  </header>
  <main>
    <section class="recent-panel">
      <div class="issue-box">
        <h2>Live Run</h2>
        <div>Updated <code id="updated-at">{{ progress.updated_at_local }}</code></div>
        <div>Last save <code id="last-save">{{ progress.last_save_name or "none" }}</code></div>
        <div>Last save age <code id="last-save-age">{{ progress.last_save_age_text }}</code></div>
        <div>Log age <code id="log-age">{{ live.age_seconds if live.age_seconds is not none else "unknown" }}</code></div>
        <div id="last-log-line">{{ live.last_line }}</div>
      </div>
      <div class="issue-box">
        <h2>Recent Saves</h2>
        <ul class="recent-list" id="recent-saves">
          {% for entry in progress.recent_saves %}
          <li><code>{{ entry.name }}</code> {{ entry.mtime_local }}</li>
          {% endfor %}
        </ul>
      </div>
    </section>
    <div class="toolbar">
      <nav class="tabs">
        {% for name in ["all", "done", "missing", "ledger-missing", "duplicate", "error"] %}
        <a class="tab {{ 'active' if status == name else '' }}" href="{{ url_for('index', status=name, q=query) }}">{{ name }}</a>
        {% endfor %}
      </nav>
      <form method="get" action="{{ url_for('index') }}">
        <input type="hidden" name="status" value="{{ status }}">
        <input name="q" value="{{ query }}" placeholder="TSV, SID, file, note">
        <button type="submit">Search</button>
      </form>
    </div>

    <div class="pager">
      <div id="pager-text">{{ filtered_count }} rows, page {{ page }} / {{ pages }}</div>
      <div>
        {% if page > 1 %}<a href="{{ url_for('index', status=status, q=query, page=page - 1, page_size=page_size) }}">Prev</a>{% endif %}
        {% if page < pages %}<a href="{{ url_for('index', status=status, q=query, page=page + 1, page_size=page_size) }}">Next</a>{% endif %}
      </div>
    </div>

    <table>
      <thead>
        <tr>
          <th>TSV</th>
          <th>Status</th>
          <th>SID</th>
          <th>Save</th>
          <th>Wait</th>
          <th>SHA1</th>
        </tr>
      </thead>
      <tbody id="rows-body">
      {% for row in rows %}
        <tr>
          <td><code>{{ row.tsv_text }}</code></td>
          <td><span class="status status-{{ row.status }}">{{ row.status }}</span></td>
          <td><code>{{ row.sid_text }}</code></td>
          <td>
            {% if row.save_name %}<code>{{ row.save_name }}</code>{% endif %}
            {% if row.error %}<div>{{ row.error }}</div>{% endif %}
            {% if row.note %}<div>{{ row.note }}</div>{% endif %}
          </td>
          <td>{{ row.wait_frames if row.wait_frames is not none else "" }}</td>
          <td><code>{{ row.save_sha1 or "" }}</code></td>
        </tr>
      {% endfor %}
      </tbody>
    </table>

    {% if snapshot.load_errors or snapshot.invalid_files or snapshot.mismatched_files %}
    <section class="issues">
      <div class="issue-box">
        <h2>Load Issues</h2>
        <ul>
          {% for error in snapshot.load_errors %}<li>{{ error }}</li>{% endfor %}
          {% for entry in snapshot.invalid_files[:50] %}<li><code>{{ entry.path.name }}</code> {{ entry.reason }}</li>{% endfor %}
        </ul>
      </div>
      <div class="issue-box">
        <h2>SID/TSV Mismatch</h2>
        <ul>
          {% for entry in snapshot.mismatched_files[:50] %}
          <li><code>{{ entry.path.name }}</code> SID {{ entry.sid }} maps to TSV {{ entry.mapped_tsv }}</li>
          {% endfor %}
        </ul>
      </div>
    </section>
    {% endif %}
  </main>
<script>
  const pageState = {
    status: {{ status|tojson }},
    query: {{ query|tojson }},
    page: {{ page }},
    pageSize: {{ page_size }},
  };
  const numberFormat = new Intl.NumberFormat();
  const setText = (id, value) => {
    const node = document.getElementById(id);
    if (node) node.textContent = value == null || value === "" ? "unknown" : String(value);
  };
  const fmt = value => numberFormat.format(Number(value || 0));
  const pct = value => `${Number(value || 0).toFixed(2)}%`;
  const rate = value => value == null ? "warming" : Number(value).toFixed(1);
  const seconds = value => value == null ? "unknown" : `${Number(value).toFixed(1)}s`;
  const escapeText = value => String(value == null ? "" : value);

  function renderLiveState(live) {
    const active = Boolean(live && live.active);
    const pill = document.createElement("span");
    pill.className = `live-pill ${active ? "active" : "idle"}`;
    pill.textContent = active ? "active" : "idle";
    const parent = document.getElementById("live-state");
    parent.replaceChildren(pill);
    setText("log-age", live && live.age_seconds != null ? seconds(live.age_seconds) : "unknown");
    setText("last-log-line", live ? live.last_line : "");
  }

  function renderRecentSaves(entries) {
    const list = document.getElementById("recent-saves");
    list.replaceChildren();
    if (!entries || entries.length === 0) {
      const item = document.createElement("li");
      item.textContent = "No saves yet.";
      list.appendChild(item);
      return;
    }
    for (const entry of entries) {
      const item = document.createElement("li");
      const code = document.createElement("code");
      code.textContent = entry.name;
      item.appendChild(code);
      item.appendChild(document.createTextNode(` ${entry.mtime_local}`));
      list.appendChild(item);
    }
  }

  function renderStatus(payload) {
    const summary = payload.summary || {};
    const progress = payload.progress || {};
    setText("complete-count", fmt(summary.completed));
    setText("total-count", fmt(summary.total));
    setText("missing-count", fmt(summary.missing));
    setText("percent-count", pct(summary.percent));
    setText("duplicates-count", fmt(summary.duplicates));
    setText("rate-hour", rate(progress.rate_per_hour));
    setText("eta-text", progress.eta_text || "unknown");
    setText("finish-time", progress.finish_time_local || "unknown");
    setText("updated-at", progress.updated_at_local || "unknown");
    setText("last-save", progress.last_save_name || "none");
    setText("last-save-age", progress.last_save_age_text || "unknown");
    const fill = document.getElementById("progress-fill");
    fill.style.width = `${Math.max(0, Math.min(100, Number(summary.percent || 0)))}%`;
    renderLiveState(payload.live || {});
    renderRecentSaves(payload.recent_saves || progress.recent_saves || []);
  }

  function renderRows(payload) {
    const body = document.getElementById("rows-body");
    body.replaceChildren();
    setText("pager-text", `${fmt(payload.filtered_count)} rows, page ${fmt(payload.page)} / ${fmt(payload.pages)}`);
    for (const row of payload.rows || []) {
      const tr = document.createElement("tr");
      const tsv = document.createElement("td");
      const tsvCode = document.createElement("code");
      tsvCode.textContent = row.tsv_text;
      tsv.appendChild(tsvCode);

      const status = document.createElement("td");
      const badge = document.createElement("span");
      badge.className = `status status-${row.status}`;
      badge.textContent = row.status;
      status.appendChild(badge);

      const sid = document.createElement("td");
      const sidCode = document.createElement("code");
      sidCode.textContent = row.sid_text || "";
      sid.appendChild(sidCode);

      const save = document.createElement("td");
      if (row.save_name) {
        const saveCode = document.createElement("code");
        saveCode.textContent = row.save_name;
        save.appendChild(saveCode);
      }
      for (const text of [row.error, row.note]) {
        if (!text) continue;
        const div = document.createElement("div");
        div.textContent = text;
        save.appendChild(div);
      }

      const wait = document.createElement("td");
      wait.textContent = row.wait_frames == null ? "" : String(row.wait_frames);

      const sha = document.createElement("td");
      const shaCode = document.createElement("code");
      shaCode.textContent = row.save_sha1 || "";
      sha.appendChild(shaCode);

      for (const cell of [tsv, status, sid, save, wait, sha]) tr.appendChild(cell);
      body.appendChild(tr);
    }
  }

  async function refreshStatus() {
    try {
      const response = await fetch("/api/status", {cache: "no-store"});
      renderStatus(await response.json());
    } catch (error) {
      setText("last-log-line", `status refresh failed: ${escapeText(error.message)}`);
    }
  }

  async function refreshRows() {
    try {
      const params = new URLSearchParams({
        status: pageState.status,
        q: pageState.query,
        page: String(pageState.page),
        page_size: String(pageState.pageSize),
      });
      const response = await fetch(`/api/rows?${params.toString()}`, {cache: "no-store"});
      renderRows(await response.json());
    } catch (error) {
      setText("last-log-line", `row refresh failed: ${escapeText(error.message)}`);
    }
  }

  refreshStatus();
  refreshRows();
  setInterval(refreshStatus, 2000);
  setInterval(refreshRows, 5000);
</script>
</body>
</html>
"""


def positive_int(value: str, *, default: int, low: int, high: int) -> int:
    """Parse bounded positive integer query args."""

    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return min(high, max(low, parsed))


def create_app(save_dir: Path = DEFAULT_SAVE_DIR, ledger_path: Path | None = None):
    """Create the local Flask app."""

    try:
        from flask import Flask, jsonify, render_template_string, request, url_for
    except ImportError as exc:  # pragma: no cover - depends on local install.
        raise RuntimeError("Flask is required. Install flask in the Python environment.") from exc

    app = Flask(__name__)
    app.config["SAVE_DIR"] = Path(save_dir)
    app.config["LEDGER_PATH"] = Path(ledger_path) if ledger_path is not None else None

    @app.route("/")
    def index():
        snapshot = build_snapshot(app.config["SAVE_DIR"], app.config["LEDGER_PATH"])
        progress = build_progress_panel(snapshot)
        live = read_live_status(snapshot.save_dir)
        status = request.args.get("status", "all")
        if status not in {"all", "done", "missing", "ledger-missing", "duplicate", "error"}:
            status = "all"
        query = request.args.get("q", "").strip()
        page_size = positive_int(
            request.args.get("page_size", str(DEFAULT_PAGE_SIZE)),
            default=DEFAULT_PAGE_SIZE,
            low=32,
            high=1024,
        )
        filtered = filter_rows(snapshot.rows, status=status, query=query)
        pages = max(1, math.ceil(len(filtered) / page_size))
        page = positive_int(request.args.get("page", "1"), default=1, low=1, high=pages)
        start = (page - 1) * page_size
        visible_rows = filtered[start : start + page_size]
        return render_template_string(
            HTML_TEMPLATE,
            snapshot=snapshot,
            progress=progress,
            live=live,
            rows=visible_rows,
            status=status,
            query=query,
            page=page,
            pages=pages,
            page_size=page_size,
            filtered_count=len(filtered),
            url_for=url_for,
        )

    @app.route("/api/status")
    def api_status():
        snapshot = build_snapshot(app.config["SAVE_DIR"], app.config["LEDGER_PATH"])
        return jsonify(snapshot.to_json())

    @app.route("/api/rows")
    def api_rows():
        snapshot = build_snapshot(app.config["SAVE_DIR"], app.config["LEDGER_PATH"])
        status = request.args.get("status", "all")
        query = request.args.get("q", "").strip()
        if status not in {"all", "done", "missing", "ledger-missing", "duplicate", "error"}:
            status = "all"
        page_size = positive_int(
            request.args.get("page_size", str(DEFAULT_PAGE_SIZE)),
            default=DEFAULT_PAGE_SIZE,
            low=32,
            high=1024,
        )
        filtered = filter_rows(snapshot.rows, status=status, query=query)
        pages = max(1, math.ceil(len(filtered) / page_size))
        page = positive_int(request.args.get("page", "1"), default=1, low=1, high=pages)
        start = (page - 1) * page_size
        return jsonify(
            {
                "summary": snapshot.summary,
                "page": page,
                "pages": pages,
                "page_size": page_size,
                "filtered_count": len(filtered),
                "rows": [row.to_json() for row in filtered[start : start + page_size]],
            }
        )

    @app.route("/api/tsv/<int:tsv>")
    def api_tsv(tsv: int):
        if not 0 <= int(tsv) < common.SHINY_VALUE_COUNT:
            return jsonify({"error": "TSV out of range"}), 404
        snapshot = build_snapshot(app.config["SAVE_DIR"], app.config["LEDGER_PATH"])
        return jsonify(snapshot.rows[int(tsv)].to_json())

    @app.route("/healthz")
    def healthz():
        return jsonify({"ok": True, "mode": "solo-lan-readonly"})

    return app


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--save-dir", type=Path, default=DEFAULT_SAVE_DIR)
    parser.add_argument(
        "--ledger",
        type=Path,
        default=None,
        help="Ledger path. Defaults to _sid_shiny_value_ledger_tid_0x0000.json inside --save-dir.",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--host",
        default=DEFAULT_BIND_HOST,
        help="Bind address. Default 0.0.0.0 allows same-LAN browser access.",
    )
    parser.add_argument("--open-browser", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the read-only Flask tracker."""

    args = build_parser().parse_args(argv)
    app = create_app(save_dir=args.save_dir, ledger_path=args.ledger)
    urls = tracker_urls(str(args.host), int(args.port))
    print(f"TSV tracker listening on host={args.host} port={int(args.port)}")
    for url in urls:
        print(f"open={url}")
    print(f"save_dir={Path(args.save_dir)}")
    if args.open_browser:
        import threading
        import webbrowser

        threading.Timer(0.75, lambda: webbrowser.open(urls[0])).start()
    # Threading is only for browser/API responsiveness. The tracker remains
    # read-only and does not create workers or remote coordination.
    app.run(host=str(args.host), port=int(args.port), debug=False, use_reloader=False, threaded=True)
    return 0


if __name__ == "__main__":
    main()
