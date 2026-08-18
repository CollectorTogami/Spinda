#!/usr/bin/env python3
"""Fast physical validator for stored Phase 3 Spinda lane ZIPs.

This validates the native SPC3 extractor's ZIP output without using
`zipfile.read()` 4.29 billion times. It reads each ZIP as bytes, walks local
file records sequentially, computes CRC32 over every 80-byte PK3 payload, and
cross-checks the central directory entries.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import struct
import time
import zlib
from pathlib import Path
from typing import Any

import phase3_zip_validator as slow


LOCAL_SIG = 0x04034B50
CENTRAL_SIG = 0x02014B50
EOCD_SIG = 0x06054B50
ZIP64_EOCD_SIG = 0x06064B50
LOCAL_STRUCT = struct.Struct("<IHHHHHIIIHH")
CENTRAL_STRUCT = struct.Struct("<IHHHHHHIIIHHHHHII")


def append_limited(values: list[Any], value: Any, *, limit: int) -> None:
    if len(values) < limit:
        values.append(value)


def read_u16_hex(raw: bytes) -> int | None:
    try:
        return int(raw, 16)
    except ValueError:
        return None


def parse_entry_name(name: bytes) -> int | None:
    if len(name) != 14 or not name.startswith(b"0x") or name[10:] != b".pk3":
        return None
    return read_u16_hex(name[2:10])


def validate_status_json(path: Path, lane: int, errors: list[str], warnings: list[str]) -> dict[str, Any]:
    status_path = path.with_name(f"_0x{lane:04X}.phase3_status.json")
    if not status_path.exists():
        warnings.append("missing_status_json")
        return {"exists": False}
    status = slow.load_json(status_path)
    result = {
        "exists": True,
        "status": status.get("status"),
        "generated_records": status.get("generated_records"),
        "selected_targets": status.get("selected_targets"),
        "zip_method": status.get("zip_method"),
        "status_hashes": status.get("status_hashes"),
        "output_zip_path": status.get("output_zip_path"),
        "load_error": status.get("_load_error"),
    }
    if status.get("_load_error"):
        errors.append(f"status_load_error:{status['_load_error']}")
    if status.get("status") not in (None, "complete"):
        errors.append(f"status_not_complete:{status.get('status')}")
    if status.get("generated_records") not in (None, slow.EXPECTED_RECORDS):
        errors.append(f"status_generated_records:{status.get('generated_records')}")
    return result


def audit_stored_zip(path: Path, sample_limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    match = slow.ZIP_RE.match(path.name)
    lane = int(match.group(1), 16) if match else None
    result: dict[str, Any] = {
        "zip": str(path),
        "name": path.name,
        "lane": f"0x{lane:04X}" if lane is not None else None,
        "zip_size": path.stat().st_size,
        "errors": [],
        "warnings": [],
    }
    errors: list[str] = result["errors"]
    warnings: list[str] = result["warnings"]
    if lane is None:
        errors.append("bad_zip_name")
        return result

    result["status_json"] = validate_status_json(path, lane, errors, warnings)
    counters = {
        "bad_names": 0,
        "bad_sizes": 0,
        "bad_methods": 0,
        "bad_lower": 0,
        "duplicate_names": 0,
        "duplicate_upper": 0,
        "bad_content_pid": 0,
        "bad_species": 0,
        "bad_ot_trash": 0,
        "crc_errors": 0,
        "central_errors": 0,
        "parse_errors": 0,
    }
    samples: dict[str, list[Any]] = {key: [] for key in counters}
    methods: dict[int, int] = {}
    seen_upper = bytearray(slow.EXPECTED_RECORDS // 8)
    local_by_name: dict[bytes, tuple[int, int, int, int]] = {}

    try:
        raw = path.read_bytes()
        view = memoryview(raw)
        offset = 0
        entry_count = 0
        while offset + LOCAL_STRUCT.size <= len(raw):
            sig = struct.unpack_from("<I", raw, offset)[0]
            if sig == CENTRAL_SIG:
                break
            if sig != LOCAL_SIG:
                counters["parse_errors"] += 1
                append_limited(samples["parse_errors"], f"bad local sig 0x{sig:08X} at {offset}", limit=sample_limit)
                break
            (
                _sig,
                _version_needed,
                flags,
                method,
                _mtime,
                _mdate,
                crc32_expected,
                compressed_size,
                uncompressed_size,
                name_len,
                extra_len,
            ) = LOCAL_STRUCT.unpack_from(raw, offset)
            header_end = offset + LOCAL_STRUCT.size + name_len + extra_len
            data_end = header_end + compressed_size
            if flags & 0x08:
                counters["parse_errors"] += 1
                append_limited(samples["parse_errors"], "data descriptor flag set", limit=sample_limit)
                break
            if data_end > len(raw):
                counters["parse_errors"] += 1
                append_limited(samples["parse_errors"], f"entry exceeds EOF at {offset}", limit=sample_limit)
                break

            name = bytes(view[offset + LOCAL_STRUCT.size : offset + LOCAL_STRUCT.size + name_len])
            data = view[header_end:data_end]
            entry_count += 1
            methods[method] = methods.get(method, 0) + 1

            pid = parse_entry_name(name)
            if pid is None:
                counters["bad_names"] += 1
                append_limited(samples["bad_names"], name.decode("ascii", "replace"), limit=sample_limit)
            else:
                upper = pid >> 16
                lower = pid & 0xFFFF
                if lower != lane:
                    counters["bad_lower"] += 1
                    append_limited(samples["bad_lower"], name.decode("ascii", "replace"), limit=sample_limit)
                if slow.bit_seen(seen_upper, upper):
                    counters["duplicate_upper"] += 1
                    append_limited(samples["duplicate_upper"], f"0x{upper:04X}", limit=sample_limit)
                else:
                    slow.set_bit(seen_upper, upper)

            if name in local_by_name:
                counters["duplicate_names"] += 1
                append_limited(samples["duplicate_names"], name.decode("ascii", "replace"), limit=sample_limit)
            else:
                local_by_name[name] = (crc32_expected, compressed_size, uncompressed_size, offset)

            if compressed_size != slow.RECORD_SIZE or uncompressed_size != slow.RECORD_SIZE or len(data) != slow.RECORD_SIZE:
                counters["bad_sizes"] += 1
                append_limited(
                    samples["bad_sizes"],
                    {"name": name.decode("ascii", "replace"), "compressed": compressed_size, "uncompressed": uncompressed_size},
                    limit=sample_limit,
                )
            if method != 0:
                counters["bad_methods"] += 1
                append_limited(samples["bad_methods"], {"name": name.decode("ascii", "replace"), "method": method}, limit=sample_limit)
            if zlib.crc32(data) & 0xFFFFFFFF != crc32_expected:
                counters["crc_errors"] += 1
                append_limited(samples["crc_errors"], name.decode("ascii", "replace"), limit=sample_limit)

            if pid is not None and len(data) == slow.RECORD_SIZE:
                data_bytes = data.tobytes()
                if int.from_bytes(data_bytes[:4], "little") != pid:
                    counters["bad_content_pid"] += 1
                    append_limited(samples["bad_content_pid"], name.decode("ascii", "replace"), limit=sample_limit)
                else:
                    species = slow.pk3_species(data_bytes)
                    if species != slow.SPINDA_RAW_SPECIES:
                        counters["bad_species"] += 1
                        append_limited(
                            samples["bad_species"],
                            {"name": name.decode("ascii", "replace"), "species": species},
                            limit=sample_limit,
                        )
                    if data_bytes[20:27] != slow.TOGAMI_OT_BYTES:
                        counters["bad_ot_trash"] += 1
                        append_limited(
                            samples["bad_ot_trash"],
                            {"name": name.decode("ascii", "replace"), "raw": data_bytes[20:27].hex(" ").upper()},
                            limit=sample_limit,
                        )

            offset = data_end

        result["entry_count"] = entry_count
        if entry_count != slow.EXPECTED_RECORDS:
            errors.append(f"entry_count:{entry_count}")
        coverage_complete = slow.all_bits_set(seen_upper)
        result["coverage_complete"] = coverage_complete
        if not coverage_complete:
            result["missing_upper_sample"] = slow.missing_upper_sample(seen_upper, sample_limit)
            errors.append("upper_coverage_incomplete")

        central_count = 0
        if offset + 4 > len(raw) or struct.unpack_from("<I", raw, offset)[0] != CENTRAL_SIG:
            counters["central_errors"] += 1
            append_limited(samples["central_errors"], "central directory not found", limit=sample_limit)
        else:
            while offset + CENTRAL_STRUCT.size <= len(raw):
                sig = struct.unpack_from("<I", raw, offset)[0]
                if sig != CENTRAL_SIG:
                    break
                fields = CENTRAL_STRUCT.unpack_from(raw, offset)
                (
                    _sig,
                    _made_by,
                    _needed,
                    flags,
                    method,
                    _mtime,
                    _mdate,
                    crc32_expected,
                    compressed_size,
                    uncompressed_size,
                    name_len,
                    extra_len,
                    comment_len,
                    _disk_start,
                    _internal_attr,
                    _external_attr,
                    local_offset,
                ) = fields
                name_start = offset + CENTRAL_STRUCT.size
                name_end = name_start + name_len
                entry_end = name_end + extra_len + comment_len
                if entry_end > len(raw):
                    counters["central_errors"] += 1
                    append_limited(samples["central_errors"], "central entry exceeds EOF", limit=sample_limit)
                    break
                name = bytes(view[name_start:name_end])
                local = local_by_name.get(name)
                if (
                    local is None
                    or local != (crc32_expected, compressed_size, uncompressed_size, local_offset)
                    or method != 0
                    or flags & 0x08
                ):
                    counters["central_errors"] += 1
                    append_limited(samples["central_errors"], name.decode("ascii", "replace"), limit=sample_limit)
                central_count += 1
                offset = entry_end
            if central_count != slow.EXPECTED_RECORDS:
                counters["central_errors"] += 1
                append_limited(samples["central_errors"], f"central_count:{central_count}", limit=sample_limit)
            if offset + 4 <= len(raw):
                end_sig = struct.unpack_from("<I", raw, offset)[0]
                if end_sig not in (EOCD_SIG, ZIP64_EOCD_SIG):
                    counters["central_errors"] += 1
                    append_limited(samples["central_errors"], f"bad end sig 0x{end_sig:08X}", limit=sample_limit)
            else:
                counters["central_errors"] += 1
                append_limited(samples["central_errors"], "missing end signature", limit=sample_limit)
        result["central_entry_count"] = central_count

    except Exception as error:  # noqa: BLE001 - keep auditing other ZIPs
        errors.append(f"bad_zip:{error}")

    result["methods"] = {slow.METHOD_NAMES.get(method, str(method)): count for method, count in sorted(methods.items())}
    result["counters"] = counters
    result["samples"] = samples
    for key, count in counters.items():
        if count:
            errors.append(f"{key}:{count}")
    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return result


def worker(args: tuple[Path, int]) -> dict[str, Any]:
    return audit_stored_zip(*args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=slow.DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-lanes", type=int, default=slow.EXPECTED_RECORDS)
    parser.add_argument("--sample-limit", type=int, default=32)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.sample_limit < 1:
        raise SystemExit("--sample-limit must be positive")
    if args.workers < 1:
        raise SystemExit("--workers must be positive")
    if not 1 <= args.target_lanes <= slow.EXPECTED_RECORDS:
        raise SystemExit("--target-lanes must be between 1 and 65536")

    report_path = args.report or args.root / f"_phase3_zip_fast_audit_{time.strftime('%Y%m%d_%H%M%S')}.json"
    zips, folder_audit = slow.scan_output_folder(args.root, target_lanes=args.target_lanes, sample_limit=args.sample_limit)
    started = time.time()
    results: list[dict[str, Any]] = []
    work_items = [(path, args.sample_limit) for path in zips]
    if args.workers == 1:
        iterator = map(worker, work_items)
        for index, result in enumerate(iterator, 1):
            results.append(result)
            if not args.quiet:
                status = "OK" if not result["errors"] else "BAD"
                print(f"{index:05d}/{len(zips):05d} {status} {result['name']} sec={result['elapsed_seconds']}", flush=True)
    else:
        with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
            for index, result in enumerate(executor.map(worker, work_items, chunksize=1), 1):
                results.append(result)
                if not args.quiet and (index % 256 == 0 or index == len(zips) or result["errors"]):
                    status = "OK" if not result["errors"] else "BAD"
                    print(f"{index:05d}/{len(zips):05d} {status} {result['name']} sec={result['elapsed_seconds']}", flush=True)

    bad = [result for result in results if result["errors"]]
    warnings = [result for result in results if result["warnings"]]
    folder_has_errors = bool(folder_audit["bad_artifact_count"] or folder_audit["missing_lane_count"])
    report = {
        "schema": "phase3_zip_fast_stored_validator.v1",
        "root": str(args.root),
        "mode": "fast-stored-deep",
        "audit_started_unix": started,
        "audit_finished_unix": time.time(),
        "workers": args.workers,
        "folder_audit": folder_audit,
        "zip_count": len(zips),
        "bad_zip_count": len(bad),
        "warning_zip_count": len(warnings),
        "total_entries_expected": len(zips) * slow.EXPECTED_RECORDS,
        "total_entries_observed": sum(result.get("entry_count", 0) or 0 for result in results),
        "total_central_entries_observed": sum(result.get("central_entry_count", 0) or 0 for result in results),
        "bad": [{"name": result["name"], "errors": result["errors"], "warnings": result["warnings"]} for result in bad],
        "warnings": [{"name": result["name"], "warnings": result["warnings"]} for result in warnings],
        "bad_samples": [result for result in bad[: args.sample_limit]],
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"REPORT {report_path}", flush=True)
    print(
        f"SUMMARY zips={len(zips)} missing={folder_audit['missing_lane_count']} "
        f"artifacts={folder_audit['bad_artifact_count']} bad={len(bad)} "
        f"warnings={len(warnings)} entries={report['total_entries_observed']} "
        f"central_entries={report['total_central_entries_observed']}",
        flush=True,
    )
    return 1 if bad or warnings or folder_has_errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
