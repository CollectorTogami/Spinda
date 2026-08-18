#!/usr/bin/env python3
"""Rewrite Phase 3 lane ZIPs into the production canonical container shape.

The Phase 3 runner writes one ZIP per lane with 65,536 PID-named 80-byte PK3
entries. This tool keeps the PK3 payload bytes unchanged, rebuilds the ZIP
container in RAM using the same layout as the native CLI writer, and atomically
replaces the original only when the bytes differ.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import struct
import time
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable
from zipfile import BadZipFile, ZipFile


ROOT = Path(__file__).absolute().parents[2]
DEFAULT_PHASE3_DIR = ROOT / "Phase3SpindaBlocks"
LANE_ZIP_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.spinda80\.zip$")
BOX_SLOT_SIZE = 80
EXPECTED_RECORDS = 0x10000
ZIP_METHOD_DEFLATE = 8
ZIP_DOS_TIME_MIDNIGHT = 0
ZIP_DOS_DATE_2026_01_01 = (46 << 9) | (1 << 5) | 1
ZIP_DEFLATE_LEVEL = 1


@dataclass(frozen=True)
class ZipEntry:
    """One canonical ZIP entry plus metadata needed for central directory."""

    name: str
    record: bytes
    crc: int
    compressed: bytes
    local_header_offset: int


@dataclass(frozen=True)
class CanonicalizeResult:
    path: Path
    lane_id: int
    entry_count: int
    old_size: int
    new_size: int
    changed: bool
    skipped: bool = False
    error: str | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "lane": f"0x{self.lane_id:04X}",
            "entry_count": self.entry_count,
            "old_size": self.old_size,
            "new_size": self.new_size,
            "changed": self.changed,
            "skipped": self.skipped,
            "error": self.error,
        }


def append_le16(out: bytearray, value: int) -> None:
    out.extend(struct.pack("<H", value & 0xFFFF))


def append_le32(out: bytearray, value: int) -> None:
    out.extend(struct.pack("<I", value & 0xFFFFFFFF))


def append_le64(out: bytearray, value: int) -> None:
    out.extend(struct.pack("<Q", value & 0xFFFFFFFFFFFFFFFF))


def format_pid(pid: int) -> str:
    return f"0x{pid:08X}.pk3"


def parse_lane_from_path(path: Path) -> int | None:
    match = LANE_ZIP_RE.match(path.name)
    if not match:
        return None
    return int(match.group(1), 16)


def parse_lane_token(raw: str) -> list[int]:
    text = raw.strip()
    if not text:
        raise ValueError("empty lane token")
    separator = ".." if ".." in text else "-" if "-" in text else None
    if separator is None:
        value = int(text, 0)
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"lane out of range: {raw}")
        return [value]
    left, right = text.split(separator, 1)
    first = int(left, 0)
    last = int(right, 0)
    if not 0 <= first <= last <= 0xFFFF:
        raise ValueError(f"bad lane range: {raw}")
    return list(range(first, last + 1))


def parse_lane_selection(values: list[str] | None) -> set[int] | None:
    if not values:
        return None
    lanes: set[int] = set()
    for value in values:
        for token in value.split(","):
            lanes.update(parse_lane_token(token))
    return lanes


def deflate_raw(record: bytes) -> bytes:
    compressor = zlib.compressobj(
        ZIP_DEFLATE_LEVEL,
        zlib.DEFLATED,
        -zlib.MAX_WBITS,
        8,
        zlib.Z_DEFAULT_STRATEGY,
    )
    return compressor.compress(record) + compressor.flush(zlib.Z_FINISH)


def read_pk3_records(path: Path, lane_id: int, expected_count: int | None) -> dict[int, bytes]:
    """Read and validate all PK3 records from one lane ZIP into RAM."""

    records: dict[int, bytes] = {}
    with ZipFile(path, "r") as archive:
        infos = archive.infolist()
        if expected_count is not None and len(infos) != expected_count:
            raise ValueError(f"expected {expected_count} entries, found {len(infos)}")
        for info in infos:
            name = info.filename
            if not (name.startswith("0x") and name.endswith(".pk3") and len(name) == len("0x00000000.pk3")):
                raise ValueError(f"bad entry name: {name}")
            try:
                pid = int(name[2:10], 16)
            except ValueError as error:
                raise ValueError(f"bad PID in entry name: {name}") from error
            if (pid & 0xFFFF) != lane_id:
                raise ValueError(f"entry {name} lower half does not match lane 0x{lane_id:04X}")
            record = archive.read(info)
            if len(record) != BOX_SLOT_SIZE:
                raise ValueError(f"entry {name} has {len(record)} bytes, expected {BOX_SLOT_SIZE}")
            actual_pid = int.from_bytes(record[:4], "little")
            if actual_pid != pid:
                raise ValueError(f"entry {name} content PID is 0x{actual_pid:08X}")
            upper = pid >> 16
            if upper in records:
                raise ValueError(f"duplicate upper half 0x{upper:04X}")
            records[upper] = record
    return records


def build_canonical_zip_bytes(records_by_upper: dict[int, bytes]) -> bytes:
    """Build ZIP bytes matching the native Phase 3 CLI writer."""

    zip_bytes = bytearray()
    central: list[ZipEntry] = []
    for upper in sorted(records_by_upper):
        record = records_by_upper[upper]
        pid = int.from_bytes(record[:4], "little")
        name = format_pid(pid)
        compressed = deflate_raw(record)
        crc = zlib.crc32(record) & 0xFFFFFFFF
        local_header_offset = len(zip_bytes)

        append_le32(zip_bytes, 0x04034B50)
        append_le16(zip_bytes, 20)
        append_le16(zip_bytes, 0)
        append_le16(zip_bytes, ZIP_METHOD_DEFLATE)
        append_le16(zip_bytes, ZIP_DOS_TIME_MIDNIGHT)
        append_le16(zip_bytes, ZIP_DOS_DATE_2026_01_01)
        append_le32(zip_bytes, crc)
        append_le32(zip_bytes, len(compressed))
        append_le32(zip_bytes, BOX_SLOT_SIZE)
        append_le16(zip_bytes, len(name))
        append_le16(zip_bytes, 0)
        zip_bytes.extend(name.encode("ascii"))
        zip_bytes.extend(compressed)
        central.append(ZipEntry(name, record, crc, compressed, local_header_offset))

    central_offset = len(zip_bytes)
    for entry in central:
        append_le32(zip_bytes, 0x02014B50)
        append_le16(zip_bytes, 20)
        append_le16(zip_bytes, 20)
        append_le16(zip_bytes, 0)
        append_le16(zip_bytes, ZIP_METHOD_DEFLATE)
        append_le16(zip_bytes, ZIP_DOS_TIME_MIDNIGHT)
        append_le16(zip_bytes, ZIP_DOS_DATE_2026_01_01)
        append_le32(zip_bytes, entry.crc)
        append_le32(zip_bytes, len(entry.compressed))
        append_le32(zip_bytes, BOX_SLOT_SIZE)
        append_le16(zip_bytes, len(entry.name))
        append_le16(zip_bytes, 0)
        append_le16(zip_bytes, 0)
        append_le16(zip_bytes, 0)
        append_le16(zip_bytes, 0)
        append_le32(zip_bytes, 0)
        append_le32(zip_bytes, entry.local_header_offset)
        zip_bytes.extend(entry.name.encode("ascii"))

    central_size = len(zip_bytes) - central_offset
    if len(central) >= 0xFFFF:
        zip64_offset = len(zip_bytes)
        append_le32(zip_bytes, 0x06064B50)
        append_le64(zip_bytes, 44)
        append_le16(zip_bytes, 45)
        append_le16(zip_bytes, 45)
        append_le32(zip_bytes, 0)
        append_le32(zip_bytes, 0)
        append_le64(zip_bytes, len(central))
        append_le64(zip_bytes, len(central))
        append_le64(zip_bytes, central_size)
        append_le64(zip_bytes, central_offset)
        append_le32(zip_bytes, 0x07064B50)
        append_le32(zip_bytes, 0)
        append_le64(zip_bytes, zip64_offset)
        append_le32(zip_bytes, 1)

    append_le32(zip_bytes, 0x06054B50)
    append_le16(zip_bytes, 0)
    append_le16(zip_bytes, 0)
    entry_count_16 = 0xFFFF if len(central) >= 0xFFFF else len(central)
    append_le16(zip_bytes, entry_count_16)
    append_le16(zip_bytes, entry_count_16)
    append_le32(zip_bytes, central_size)
    append_le32(zip_bytes, central_offset)
    append_le16(zip_bytes, 0)
    return bytes(zip_bytes)


def temp_path_for(path: Path) -> Path:
    return path.with_name(f"{path.name}.canonicalize.pid{os.getpid()}.tmp")


def atomic_replace(path: Path, payload: bytes) -> None:
    tmp_path = temp_path_for(path)
    try:
        with tmp_path.open("wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    finally:
        try:
            tmp_path.unlink()
        except FileNotFoundError:
            pass


def canonicalize_zip(path: Path, *, expected_count: int | None, dry_run: bool = False) -> CanonicalizeResult:
    lane_id = parse_lane_from_path(path)
    if lane_id is None:
        return CanonicalizeResult(path, -1, 0, path.stat().st_size, path.stat().st_size, False, True, "not a lane ZIP name")
    old_size = path.stat().st_size
    try:
        records = read_pk3_records(path, lane_id, expected_count)
        canonical = build_canonical_zip_bytes(records)
        old_bytes = path.read_bytes()
        changed = old_bytes != canonical
        if changed and not dry_run:
            atomic_replace(path, canonical)
        return CanonicalizeResult(path, lane_id, len(records), old_size, len(canonical), changed)
    except (BadZipFile, OSError, ValueError, zlib.error) as error:
        return CanonicalizeResult(path, lane_id, 0, old_size, old_size, False, True, str(error))


def iter_lane_zips(root: Path, *, recursive: bool, selected_lanes: set[int] | None = None) -> Iterable[Path]:
    pattern = "**/*.zip" if recursive else "*.zip"
    for path in sorted(root.glob(pattern)):
        lane_id = parse_lane_from_path(path)
        if path.is_file() and lane_id is not None and (selected_lanes is None or lane_id in selected_lanes):
            yield path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_PHASE3_DIR)
    parser.add_argument("--recursive", action="store_true", help="Also scan benchmark/subdirectories.")
    parser.add_argument("--expected-count", type=int, default=EXPECTED_RECORDS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--report", type=Path)
    parser.add_argument("--limit", type=int, help="Process only this many lane ZIPs.")
    parser.add_argument("--lanes", nargs="+", help="Optional lane ids/ranges, e.g. 0x0001 0x0002-0x0004.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        selected_lanes = parse_lane_selection(args.lanes)
    except ValueError as error:
        raise SystemExit(str(error)) from error
    targets = list(iter_lane_zips(args.root, recursive=args.recursive, selected_lanes=selected_lanes))
    if args.limit is not None:
        targets = targets[: args.limit]
    started = time.time()
    results = [
        canonicalize_zip(path, expected_count=args.expected_count, dry_run=args.dry_run)
        for path in targets
    ]
    payload = {
        "started_unix": started,
        "finished_unix": time.time(),
        "root": str(args.root),
        "recursive": args.recursive,
        "dry_run": args.dry_run,
        "selected_lanes": [f"0x{lane:04X}" for lane in sorted(selected_lanes)] if selected_lanes else None,
        "target_count": len(targets),
        "changed_count": sum(1 for result in results if result.changed),
        "skipped_count": sum(1 for result in results if result.skipped),
        "error_count": sum(1 for result in results if result.error and not result.error.startswith("not a lane ZIP")),
        "old_total_bytes": sum(result.old_size for result in results),
        "new_total_bytes": sum(result.new_size for result in results),
        "results": [result.as_json() for result in results],
    }
    text = json.dumps(payload, indent=2, sort_keys=True)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 1 if payload["error_count"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
