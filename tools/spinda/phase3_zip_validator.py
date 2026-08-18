#!/usr/bin/env python3
"""Read-only validator for Phase 3 Spinda lane ZIPs.

The production runner keeps hot validation light. This tool is the slower
post-run audit path: it opens each completed lane ZIP, checks central-directory
shape, reads every PK3 entry to force CRC/decompression validation, and checks
that each PK3's first four bytes match the PID in its filename.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import re
import time
import zipfile
from pathlib import Path
from typing import Any


ENTRY_RE = re.compile(r"^0x([0-9A-Fa-f]{8})\.pk3$")
ZIP_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.spinda80\.zip$")
EXPECTED_RECORDS = 65536
RECORD_SIZE = 80
SPINDA_RAW_SPECIES = 308
TOGAMI_OT_BYTES = bytes([0xCE, 0xE3, 0xDB, 0xD5, 0xE1, 0xDD, 0xFF])
BLOCK_ORDERS = [
    (0, 1, 2, 3), (0, 1, 3, 2), (0, 2, 1, 3), (0, 2, 3, 1),
    (0, 3, 1, 2), (0, 3, 2, 1), (1, 0, 2, 3), (1, 0, 3, 2),
    (1, 2, 0, 3), (1, 2, 3, 0), (1, 3, 0, 2), (1, 3, 2, 0),
    (2, 0, 1, 3), (2, 0, 3, 1), (2, 1, 0, 3), (2, 1, 3, 0),
    (2, 3, 0, 1), (2, 3, 1, 0), (3, 0, 1, 2), (3, 0, 2, 1),
    (3, 1, 0, 2), (3, 1, 2, 0), (3, 2, 0, 1), (3, 2, 1, 0),
]
METHOD_NAMES = {
    zipfile.ZIP_DEFLATED: "deflate",
    zipfile.ZIP_STORED: "store",
}
ROOT = Path(__file__).absolute().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "Phase3SpindaBlocks"


def bit_seen(bits: bytearray, index: int) -> bool:
    return bool(bits[index >> 3] & (1 << (index & 7)))


def set_bit(bits: bytearray, index: int) -> None:
    bits[index >> 3] |= 1 << (index & 7)


def all_bits_set(bits: bytearray) -> bool:
    return all(value == 0xFF for value in bits)


def missing_upper_sample(bits: bytearray, limit: int = 32) -> list[str]:
    missing: list[str] = []
    for upper in range(EXPECTED_RECORDS):
        if not bit_seen(bits, upper):
            missing.append(f"0x{upper:04X}")
            if len(missing) >= limit:
                break
    return missing


def load_json(path: Path) -> dict[str, Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001 - report-only validator
        return {"_load_error": str(error)}


def append_limited(values: list[Any], value: Any, *, limit: int) -> None:
    if len(values) < limit:
        values.append(value)


def read_u32_le(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def pk3_species(data: bytes) -> int:
    pid = read_u32_le(data, 0)
    key = pid ^ read_u32_le(data, 4)
    growth_slot = BLOCK_ORDERS[pid % 24].index(0)
    first_word = read_u32_le(data, 32 + growth_slot * 12) ^ key
    return first_word & 0xFFFF


def scan_output_folder(
    root: Path,
    *,
    target_lanes: int,
    sample_limit: int,
) -> tuple[list[Path], dict[str, Any]]:
    """Scan final ZIP names without opening archives.

    This is the manifest stage of the post-run verifier. It catches missing
    lanes and bad output artifacts before the slower ZIP-entry pass. No `.pk3`
    files are ever written to disk.
    """

    seen = bytearray(EXPECTED_RECORDS // 8)
    valid_pairs: list[tuple[int, Path]] = []
    samples: dict[str, list[Any]] = {
        "missing_lanes": [],
        "bad_names": [],
        "zero_size_zips": [],
        "tmp_files": [],
    }
    counters = {
        "valid_zip_count": 0,
        "bad_name_count": 0,
        "zero_size_zip_count": 0,
        "tmp_file_count": 0,
        "duplicate_lane_count": 0,
    }

    try:
        with os.scandir(root) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                name = entry.name
                zip_match = ZIP_RE.match(name)
                if zip_match:
                    lane = int(zip_match.group(1), 16)
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    if size <= 0:
                        counters["zero_size_zip_count"] += 1
                        append_limited(samples["zero_size_zips"], name, limit=sample_limit)
                        continue
                    if bit_seen(seen, lane):
                        counters["duplicate_lane_count"] += 1
                        continue
                    set_bit(seen, lane)
                    counters["valid_zip_count"] += 1
                    valid_pairs.append((lane, root / name))
                    continue
                if name.endswith(".spinda80.zip") or ".spinda80.zip." in name:
                    if name.endswith(".tmp") or ".tmp" in name:
                        counters["tmp_file_count"] += 1
                        append_limited(samples["tmp_files"], name, limit=sample_limit)
                    else:
                        counters["bad_name_count"] += 1
                        append_limited(samples["bad_names"], name, limit=sample_limit)
    except FileNotFoundError:
        samples["folder_errors"] = [f"missing folder: {root}"]
    except OSError as error:
        samples["folder_errors"] = [str(error)]

    missing_count = 0
    for lane in range(target_lanes):
        if not bit_seen(seen, lane):
            missing_count += 1
            append_limited(samples["missing_lanes"], f"0x{lane:04X}", limit=sample_limit)

    artifact_count = (
        counters["bad_name_count"]
        + counters["zero_size_zip_count"]
        + counters["tmp_file_count"]
        + counters["duplicate_lane_count"]
    )
    audit = {
        **counters,
        "target_lanes": target_lanes,
        "missing_lane_count": missing_count,
        "bad_artifact_count": artifact_count,
        "samples": samples,
    }
    return [path for _, path in sorted(valid_pairs)], audit


def audit_zip(path: Path, *, sample_limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    match = ZIP_RE.match(path.name)
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

    status_path = path.with_name(f"_0x{lane:04X}.phase3_status.json")
    if status_path.exists():
        status = load_json(status_path)
        result["status_json"] = {
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
        if status.get("generated_records") not in (None, EXPECTED_RECORDS):
            errors.append(f"status_generated_records:{status.get('generated_records')}")
    else:
        result["status_json"] = {"exists": False}
        warnings.append("missing_status_json")

    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            result["entry_count"] = len(infos)
            if len(infos) != EXPECTED_RECORDS:
                errors.append(f"entry_count:{len(infos)}")

            seen_names: set[str] = set()
            seen_upper = bytearray(EXPECTED_RECORDS // 8)
            methods: dict[int, int] = {}
            samples: dict[str, list[Any]] = {
                "bad_names": [],
                "bad_sizes": [],
                "bad_methods": [],
                "bad_lower": [],
                "duplicate_names": [],
                "duplicate_upper": [],
                "bad_content_pid": [],
                "bad_species": [],
                "bad_ot_trash": [],
                "read_errors": [],
            }
            counters = {key: 0 for key in samples}

            for info in infos:
                methods[info.compress_type] = methods.get(info.compress_type, 0) + 1
                if info.filename in seen_names:
                    counters["duplicate_names"] += 1
                    append_limited(samples["duplicate_names"], info.filename, limit=sample_limit)
                else:
                    seen_names.add(info.filename)

                entry_match = ENTRY_RE.match(info.filename)
                if not entry_match:
                    counters["bad_names"] += 1
                    append_limited(samples["bad_names"], info.filename, limit=sample_limit)
                    continue

                pid = int(entry_match.group(1), 16)
                upper = pid >> 16
                lower = pid & 0xFFFF
                if lower != lane:
                    counters["bad_lower"] += 1
                    append_limited(samples["bad_lower"], info.filename, limit=sample_limit)
                if bit_seen(seen_upper, upper):
                    counters["duplicate_upper"] += 1
                    append_limited(samples["duplicate_upper"], f"0x{upper:04X}", limit=sample_limit)
                else:
                    set_bit(seen_upper, upper)
                if info.file_size != RECORD_SIZE:
                    counters["bad_sizes"] += 1
                    append_limited(
                        samples["bad_sizes"],
                        {"name": info.filename, "size": info.file_size},
                        limit=sample_limit,
                    )
                if info.compress_type not in METHOD_NAMES:
                    counters["bad_methods"] += 1
                    append_limited(
                        samples["bad_methods"],
                        {"name": info.filename, "method": info.compress_type},
                        limit=sample_limit,
                    )

                try:
                    data = archive.read(info)
                except Exception as error:  # noqa: BLE001 - report every bad entry
                    counters["read_errors"] += 1
                    append_limited(
                        samples["read_errors"],
                        {"name": info.filename, "error": str(error)},
                        limit=sample_limit,
                    )
                    continue

                if len(data) != RECORD_SIZE:
                    counters["bad_sizes"] += 1
                    append_limited(
                        samples["bad_sizes"],
                        {"name": info.filename, "read_size": len(data)},
                        limit=sample_limit,
                    )
                elif int.from_bytes(data[:4], "little") != pid:
                    counters["bad_content_pid"] += 1
                    append_limited(samples["bad_content_pid"], info.filename, limit=sample_limit)
                else:
                    species = pk3_species(data)
                    if species != SPINDA_RAW_SPECIES:
                        counters["bad_species"] += 1
                        append_limited(
                            samples["bad_species"],
                            {"name": info.filename, "species": species},
                            limit=sample_limit,
                        )
                    if data[20:27] != TOGAMI_OT_BYTES:
                        counters["bad_ot_trash"] += 1
                        append_limited(
                            samples["bad_ot_trash"],
                            {"name": info.filename, "raw": data[20:27].hex(" ").upper()},
                            limit=sample_limit,
                        )

            coverage_complete = all_bits_set(seen_upper)
            result["methods"] = {
                METHOD_NAMES.get(method, str(method)): count
                for method, count in sorted(methods.items())
            }
            result["coverage_complete"] = coverage_complete
            result["counters"] = counters
            result["samples"] = samples
            if not coverage_complete:
                result["missing_upper_sample"] = missing_upper_sample(seen_upper, sample_limit)
                errors.append("upper_coverage_incomplete")
            for key, count in counters.items():
                if count:
                    errors.append(f"{key}:{count}")
    except Exception as error:  # noqa: BLE001 - keep auditing other ZIPs
        errors.append(f"bad_zip:{error}")

    result["elapsed_seconds"] = round(time.perf_counter() - started, 3)
    return result


def audit_zip_worker(args: tuple[Path, int]) -> dict[str, Any]:
    path, sample_limit = args
    return audit_zip(path, sample_limit=sample_limit)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--target-lanes", type=int, default=EXPECTED_RECORDS)
    parser.add_argument("--sample-limit", type=int, default=32)
    parser.add_argument(
        "--manifest-only",
        action="store_true",
        help="Only scan final ZIP filenames/sizes and missing lanes; do not open ZIP contents.",
    )
    parser.add_argument(
        "--allow-incomplete",
        action="store_true",
        help="Return success when only missing lanes are found. Useful during an active production run.",
    )
    parser.add_argument("--workers", type=int, default=1, help="ZIP audit worker processes; default is serial.")
    parser.add_argument("--quiet", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    if args.sample_limit < 1:
        raise SystemExit("--sample-limit must be positive")
    if not 1 <= args.target_lanes <= EXPECTED_RECORDS:
        raise SystemExit("--target-lanes must be between 1 and 65536")
    if args.workers < 1:
        raise SystemExit("--workers must be positive")
    report_path = args.report or args.root / f"_phase3_zip_audit_{time.strftime('%Y%m%d_%H%M%S')}.json"
    zips, folder_audit = scan_output_folder(
        args.root,
        target_lanes=args.target_lanes,
        sample_limit=args.sample_limit,
    )
    started = time.time()
    results: list[dict[str, Any]] = []
    if not args.manifest_only:
        if args.workers == 1:
            iterator = (audit_zip(path, sample_limit=args.sample_limit) for path in zips)
            for index, result in enumerate(iterator, 1):
                results.append(result)
                if not args.quiet:
                    status = "OK" if not result["errors"] else "BAD"
                    print(
                        f"{index:03d}/{len(zips):03d} {status} {result['name']} "
                        f"entries={result.get('entry_count')} sec={result.get('elapsed_seconds')}",
                        flush=True,
                    )
        else:
            work_items = [(path, args.sample_limit) for path in zips]
            with concurrent.futures.ProcessPoolExecutor(max_workers=args.workers) as executor:
                for index, result in enumerate(executor.map(audit_zip_worker, work_items, chunksize=1), 1):
                    results.append(result)
                    if not args.quiet:
                        status = "OK" if not result["errors"] else "BAD"
                        print(
                            f"{index:03d}/{len(zips):03d} {status} {result['name']} "
                            f"entries={result.get('entry_count')} sec={result.get('elapsed_seconds')}",
                            flush=True,
                        )

    bad = [result for result in results if result["errors"]]
    warnings = [result for result in results if result["warnings"]]
    folder_has_errors = bool(folder_audit["bad_artifact_count"])
    incomplete = bool(folder_audit["missing_lane_count"])
    report = {
        "root": str(args.root),
        "mode": "manifest-only" if args.manifest_only else "deep-zip",
        "audit_started_unix": started,
        "audit_finished_unix": time.time(),
        "folder_audit": folder_audit,
        "zip_count": len(zips),
        "bad_zip_count": len(bad),
        "warning_zip_count": len(warnings),
        "total_entries_expected": 0 if args.manifest_only else len(zips) * EXPECTED_RECORDS,
        "total_entries_observed": sum(result.get("entry_count", 0) or 0 for result in results),
        "pkhex_validation": {
            "status": "deferred",
            "reason": "PKHeX.Core semantic validation is intentionally outside hot production and should run after all lanes finish.",
            "tool": str(Path(__file__).with_name("phase3_pkhex_validator")),
        },
        "bad": [
            {"name": result["name"], "errors": result["errors"], "warnings": result["warnings"]}
            for result in bad
        ],
        "warnings": [
            {"name": result["name"], "warnings": result["warnings"]}
            for result in warnings
        ],
        "results": results,
    }
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"REPORT {report_path}", flush=True)
    print(
        f"SUMMARY zips={len(zips)} missing={folder_audit['missing_lane_count']} "
        f"artifacts={folder_audit['bad_artifact_count']} bad={len(bad)} "
        f"warnings={len(warnings)} entries={report['total_entries_observed']}",
        flush=True,
    )
    if bad or folder_has_errors:
        return 1
    if incomplete and not args.allow_incomplete:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
