#!/usr/bin/env python3
"""Merge Phase 3 lane ledger JSON from one output folder into another.

When no folders are passed on the command line, the script opens native folder
pickers for a source helper folder and a destination folder. It then merges
`_phase3_lane_ledger.json` records into the destination ledger, writes a backup
of the old destination ledger, and writes a JSON merge report.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


LEDGER_NAME = "_phase3_lane_ledger.json"
MIN_FINAL_ZIP_BYTES = 1024
SPINDAS_PER_LANE = 65536
ACTIVE_STATUSES = {"claimed", "running"}
STATUS_RANK = {
    "verified": 70,
    "done": 60,
    "quarantined": 50,
    "failed": 30,
    "released": 20,
    "claimed": 10,
    "running": 10,
}
TIMESTAMP_FIELDS = (
    "verified_at_unix",
    "finished_at_unix",
    "failed_at_unix",
    "released_at_unix",
    "heartbeat_at_unix",
    "claimed_at_unix",
    "updated_at_unix",
)


@dataclass
class MergeStats:
    added: list[int] = field(default_factory=list)
    replaced: list[int] = field(default_factory=list)
    kept_existing: list[int] = field(default_factory=list)
    skipped_active: list[int] = field(default_factory=list)
    skipped_invalid: list[str] = field(default_factory=list)
    conflicts: list[int] = field(default_factory=list)


def now_local() -> str:
    """Return local timestamp text matching the command-center ledger style."""

    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")


def read_json_object(path: Path) -> dict[str, Any]:
    """Read one JSON object, accepting a UTF-8 BOM from Windows tools."""

    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
    except FileNotFoundError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"could not read JSON ledger {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"ledger top-level JSON is not an object: {path}")
    return payload


def write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    """Write a JSON object through a temp file, then replace the target."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def parse_lane(value: Any) -> int:
    """Parse a lane id from `0x1234`, `1234`, or integer form."""

    if isinstance(value, int):
        lane = value
    else:
        text = str(value).strip()
        if text.lower().startswith("0x"):
            lane = int(text, 16)
        elif text and all(ch in "0123456789abcdefABCDEF" for ch in text):
            lane = int(text, 16)
        else:
            lane = int(text, 0)
    if not 0 <= lane <= 0xFFFF:
        raise ValueError(f"lane out of range: {value!r}")
    return lane


def lane_hex(lane: int) -> str:
    """Format a lane id as command-center ledger key text."""

    return f"0x{lane:04X}"


def compact_lane_ranges(lanes: Iterable[int]) -> list[str]:
    """Compact lane ids into `0x0001-0x0003` style ranges."""

    values = sorted(set(lanes))
    if not values:
        return []
    ranges: list[str] = []
    start = previous = values[0]
    for value in values[1:]:
        if value == previous + 1:
            previous = value
            continue
        ranges.append(lane_hex(start) if start == previous else f"{lane_hex(start)}-{lane_hex(previous)}")
        start = previous = value
    ranges.append(lane_hex(start) if start == previous else f"{lane_hex(start)}-{lane_hex(previous)}")
    return ranges


def empty_ledger(target_lanes: int = 0x10000) -> dict[str, Any]:
    """Create a command-center-compatible empty lane ledger."""

    now = time.time()
    return {
        "version": 1,
        "created_at_unix": now,
        "created_at_local": now_local(),
        "updated_at_unix": now,
        "updated_at_local": now_local(),
        "target_lanes": target_lanes,
        "records": {},
    }


def load_destination_ledger(path: Path) -> dict[str, Any]:
    """Load destination ledger or create a new ledger object."""

    if not path.exists():
        return empty_ledger()
    payload = read_json_object(path)
    if not isinstance(payload.get("records"), dict):
        payload["records"] = {}
    return payload


def normalized_records(payload: dict[str, Any]) -> tuple[dict[int, dict[str, Any]], list[str]]:
    """Return records keyed by integer lane id plus invalid-row messages."""

    raw_records = payload.get("records")
    if not isinstance(raw_records, dict):
        return {}, ["ledger records field is missing or not an object"]
    records: dict[int, dict[str, Any]] = {}
    errors: list[str] = []
    for key, raw_record in raw_records.items():
        if not isinstance(raw_record, dict):
            errors.append(f"{key}: record is not an object")
            continue
        try:
            lane = parse_lane(raw_record.get("lane", key))
        except (TypeError, ValueError) as exc:
            errors.append(f"{key}: {exc}")
            continue
        record = dict(raw_record)
        record["lane"] = lane_hex(lane)
        records[lane] = record
    return records, errors


def record_status(record: dict[str, Any]) -> str:
    """Return normalized status text."""

    return str(record.get("status") or "").strip().lower()


def record_timestamp(record: dict[str, Any]) -> float:
    """Return the best freshness timestamp on a ledger record."""

    for field_name in TIMESTAMP_FIELDS:
        value = record.get(field_name)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def has_zip_proof(record: dict[str, Any]) -> bool:
    """Return true when a record has enough ZIP proof to be useful offline."""

    try:
        zip_size = int(record.get("zip_size"))
    except (TypeError, ValueError):
        return False
    return zip_size >= MIN_FINAL_ZIP_BYTES


def record_score(record: dict[str, Any]) -> tuple[int, int, float, int]:
    """Build a deterministic score for choosing one record over another."""

    status = record_status(record)
    return (
        STATUS_RANK.get(status, 0),
        1 if has_zip_proof(record) else 0,
        record_timestamp(record),
        len(record),
    )


def ledger_target_lanes(payload: dict[str, Any]) -> int:
    """Return a safe target-lane count from a ledger object."""

    try:
        return max(0, int(payload.get("target_lanes") or 0))
    except (TypeError, ValueError):
        return 0


def refresh_zip_metadata(record: dict[str, Any], lane: int, source_folder: Path, destination_folder: Path) -> dict[str, Any]:
    """Prefer ZIP proof from destination, then source, without copying files."""

    result = dict(record)
    for folder in (destination_folder, source_folder):
        zip_path = folder / f"{lane_hex(lane)}.spinda80.zip"
        try:
            size = zip_path.stat().st_size
        except OSError:
            continue
        if size < MIN_FINAL_ZIP_BYTES:
            continue
        result["zip_path"] = str(zip_path)
        result["zip_size"] = size
        result.setdefault("pk3_count", SPINDAS_PER_LANE)
        return result
    return result


def merge_records(
    source_records: dict[int, dict[str, Any]],
    destination_records: dict[int, dict[str, Any]],
    *,
    source_folder: Path,
    destination_folder: Path,
    include_active: bool,
) -> tuple[dict[int, dict[str, Any]], MergeStats]:
    """Merge source ledger records into destination records."""

    merged = dict(destination_records)
    stats = MergeStats()
    for lane, source_record in sorted(source_records.items()):
        status = record_status(source_record)
        if not status:
            stats.skipped_invalid.append(f"{lane_hex(lane)}: missing status")
            continue
        if status in ACTIVE_STATUSES and not include_active:
            stats.skipped_active.append(lane)
            continue
        candidate = refresh_zip_metadata(source_record, lane, source_folder, destination_folder)
        current = merged.get(lane)
        if current is None:
            merged[lane] = candidate
            stats.added.append(lane)
            continue
        current_score = record_score(current)
        candidate_score = record_score(candidate)
        if candidate_score > current_score:
            if record_status(current) not in {record_status(candidate), ""}:
                stats.conflicts.append(lane)
            merged[lane] = candidate
            stats.replaced.append(lane)
        else:
            if record_status(current) != record_status(candidate):
                stats.conflicts.append(lane)
            stats.kept_existing.append(lane)
    return merged, stats


def ledger_from_records(base: dict[str, Any], records: dict[int, dict[str, Any]], source_path: Path) -> dict[str, Any]:
    """Build final destination ledger payload from merged records."""

    now = time.time()
    payload = dict(base)
    payload["version"] = int(payload.get("version") or 1)
    payload["updated_at_unix"] = now
    payload["updated_at_local"] = now_local()
    payload["target_lanes"] = max(int(payload.get("target_lanes") or 0x10000), max(records.keys(), default=-1) + 1)
    payload["records"] = {lane_hex(lane): records[lane] for lane in sorted(records)}
    history = payload.get("merge_history")
    if not isinstance(history, list):
        history = []
    history.append(
        {
            "merged_at_unix": now,
            "merged_at_local": now_local(),
            "source_ledger": str(source_path),
        }
    )
    payload["merge_history"] = history[-20:]
    return payload


def stats_payload(stats: MergeStats, *, source_count: int, destination_before: int, destination_after: int) -> dict[str, Any]:
    """Return compact report counters and ranges."""

    return {
        "source_records": source_count,
        "destination_records_before": destination_before,
        "destination_records_after": destination_after,
        "added": len(stats.added),
        "replaced": len(stats.replaced),
        "kept_existing": len(stats.kept_existing),
        "skipped_active": len(stats.skipped_active),
        "skipped_invalid": len(stats.skipped_invalid),
        "conflicts": len(stats.conflicts),
        "added_ranges": compact_lane_ranges(stats.added),
        "replaced_ranges": compact_lane_ranges(stats.replaced),
        "skipped_active_ranges": compact_lane_ranges(stats.skipped_active),
        "conflict_ranges": compact_lane_ranges(stats.conflicts),
        "skipped_invalid_samples": stats.skipped_invalid[:20],
    }


def merge_ledgers(
    source_folder: Path,
    destination_folder: Path,
    *,
    include_active: bool = False,
    dry_run: bool = False,
    report_path: Path | None = None,
) -> dict[str, Any]:
    """Merge one source `_phase3_lane_ledger.json` into a destination folder."""

    source_folder = source_folder.resolve()
    destination_folder = destination_folder.resolve()
    if source_folder == destination_folder:
        raise RuntimeError("source and destination folders are the same")
    source_path = source_folder / LEDGER_NAME
    destination_path = destination_folder / LEDGER_NAME
    if not source_path.is_file():
        raise RuntimeError(f"source ledger not found: {source_path}")
    if not destination_folder.is_dir():
        raise RuntimeError(f"destination folder not found: {destination_folder}")

    source_payload = read_json_object(source_path)
    destination_payload = load_destination_ledger(destination_path)
    destination_payload["target_lanes"] = max(
        ledger_target_lanes(source_payload),
        ledger_target_lanes(destination_payload),
        0x10000,
    )
    source_records, source_errors = normalized_records(source_payload)
    destination_records, destination_errors = normalized_records(destination_payload)
    merged_records, stats = merge_records(
        source_records,
        destination_records,
        source_folder=source_folder,
        destination_folder=destination_folder,
        include_active=include_active,
    )
    stats.skipped_invalid.extend(source_errors)
    stats.skipped_invalid.extend(f"destination: {item}" for item in destination_errors)
    final_payload = ledger_from_records(destination_payload, merged_records, source_path)

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = destination_path.with_name(f"{destination_path.name}.backup-{stamp}.json")
    report_path = report_path or destination_folder / f"_phase3_lane_ledger_merge_report_{stamp}.json"
    report = {
        "ok": True,
        "dry_run": dry_run,
        "include_active": include_active,
        "source_folder": str(source_folder),
        "destination_folder": str(destination_folder),
        "source_ledger": str(source_path),
        "destination_ledger": str(destination_path),
        "backup_path": str(backup_path) if destination_path.exists() and not dry_run else None,
        "report_path": str(report_path),
        "counts": stats_payload(
            stats,
            source_count=len(source_records),
            destination_before=len(destination_records),
            destination_after=len(merged_records),
        ),
    }

    if not dry_run:
        if destination_path.exists():
            shutil.copy2(destination_path, backup_path)
        write_json_atomic(destination_path, final_payload)
        write_json_atomic(report_path, report)
    return report


def pick_folder(title: str) -> Path | None:
    """Ask for one folder with the native OS folder picker."""

    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception as exc:  # noqa: BLE001 - missing Tk is an environment issue.
        raise RuntimeError(f"folder picker is unavailable: {exc}") from exc
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        selected = filedialog.askdirectory(parent=root, title=title, mustexist=True)
    finally:
        root.destroy()
    return Path(selected) if selected else None


def show_message(title: str, text: str) -> None:
    """Show a native message box when the script was driven by folder pickers."""

    try:
        import tkinter as tk
        from tkinter import messagebox
    except Exception:
        return
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        messagebox.showinfo(title, text, parent=root)
    finally:
        root.destroy()


def run_self_test() -> None:
    """Exercise merge behavior without requiring a real Phase 3 folder."""

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        source = root / "source"
        destination = root / "destination"
        source.mkdir()
        destination.mkdir()
        (source / "0x0001.spinda80.zip").write_bytes(b"x" * 2048)
        source_payload = empty_ledger(target_lanes=8)
        source_payload["records"] = {
            "0x0001": {"lane": "0x0001", "status": "done", "device_id": "helper", "zip_size": 2048, "pk3_count": 65536},
            "0x0002": {"lane": "0x0002", "status": "running", "device_id": "helper"},
            "0x0003": {"lane": "0x0003", "status": "verified", "verified_at_unix": time.time()},
        }
        destination_payload = empty_ledger(target_lanes=8)
        destination_payload["records"] = {
            "0x0003": {"lane": "0x0003", "status": "done", "finished_at_unix": 1.0},
            "0x0004": {"lane": "0x0004", "status": "done", "finished_at_unix": 2.0},
        }
        write_json_atomic(source / LEDGER_NAME, source_payload)
        write_json_atomic(destination / LEDGER_NAME, destination_payload)

        report = merge_ledgers(source, destination)
        merged = read_json_object(destination / LEDGER_NAME)
        records = merged["records"]
        assert report["counts"]["added"] == 1, report
        assert report["counts"]["replaced"] == 1, report
        assert report["counts"]["skipped_active"] == 1, report
        assert records["0x0001"]["status"] == "done"
        assert records["0x0001"]["zip_path"].endswith("0x0001.spinda80.zip")
        assert records["0x0003"]["status"] == "verified"
        assert records["0x0004"]["status"] == "done"


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description="Merge a helper's Phase 3 _phase3_lane_ledger.json into a destination folder.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--source", type=Path, help="Source helper Phase3SpindaBlocks folder. Opens picker if omitted.")
    parser.add_argument("--destination", type=Path, help="Destination Phase3SpindaBlocks folder. Opens picker if omitted.")
    parser.add_argument("--include-active", action="store_true", help="Also import claimed/running rows. Off by default to avoid stale helper claims.")
    parser.add_argument("--dry-run", action="store_true", help="Print the merge report without changing the destination ledger.")
    parser.add_argument("--report", type=Path, help="Optional merge report JSON path.")
    parser.add_argument("--no-messagebox", action="store_true", help="Do not show completion message after folder-picker runs.")
    parser.add_argument("--self-test", action="store_true", help="Run built-in merge tests and exit.")
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    """Run the standalone ledger merge utility."""

    args = parse_args(argv)
    if args.self_test:
        run_self_test()
        print("merge_phase3_json_ledgers self-test ok")
        return 0

    used_picker = args.source is None or args.destination is None
    source = args.source or pick_folder("Select source helper Phase3SpindaBlocks folder")
    if source is None:
        print("Canceled: no source folder selected", file=sys.stderr)
        return 1
    destination = args.destination or pick_folder("Select destination Phase3SpindaBlocks folder")
    if destination is None:
        print("Canceled: no destination folder selected", file=sys.stderr)
        return 1

    try:
        report = merge_ledgers(
            source,
            destination,
            include_active=args.include_active,
            dry_run=args.dry_run,
            report_path=args.report,
        )
    except RuntimeError as exc:
        if used_picker and not args.no_messagebox:
            show_message("Phase 3 ledger merge failed", str(exc))
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(report, indent=2, sort_keys=True))
    if used_picker and not args.no_messagebox:
        counts = report["counts"]
        show_message(
            "Phase 3 ledger merge complete",
            "\n".join(
                [
                    f"Added: {counts['added']}",
                    f"Replaced: {counts['replaced']}",
                    f"Kept existing: {counts['kept_existing']}",
                    f"Skipped active: {counts['skipped_active']}",
                    f"Report: {report['report_path']}",
                ]
            ),
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
