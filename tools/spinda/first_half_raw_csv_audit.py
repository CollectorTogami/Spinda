"""Read-only audit for the live FR/LG Spinda first-half raw CSV corpus.

The live hitter writes pairs named `0x####.sav` and `0x####.ss0` under
`<repo-root>\\1sthalves`. This tool never opens mGBA, never writes
files, and never repairs anything. It only lists directory entries and checks
whether the currently visible files look like complete first-half artifacts.

Organic FR/LG daycare lanes are only `0x0001..0xFFFE`. The two endpoint
values, `0x0000` and `0xFFFF`, are project-approved ACE exceptions and must be
stored in an explicit endpoint-exception folder so the audit can distinguish
"not organic" from "missing by accident".
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Iterable

DEFAULT_RAW_CSV_DIR = Path(__file__).resolve().parents[2] / "1sthalves"
EXPECTED_TARGETS = 0x10000
ENDPOINT_EXCEPTION_TARGETS = (0x0000, 0xFFFF)
EXPECTED_SAVE_SIZE = 128 * 1024
EXPECTED_STATE_SIZE = 397_312
NAME_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.(sav|ss0)$")
MANIFEST_NAME_RE = re.compile(r"^0x([0-9A-Fa-f]{4})(?:__raw0x[0-9A-Fa-f]{4})?\.json$")
LAYOUT_AUTO = "auto"
LAYOUT_FLAT = "flat"
LAYOUT_SPLIT = "split"
SOURCE_ORGANIC = "organic"
SOURCE_ENDPOINT = "endpoint"


@dataclass
class AuditResult:
    """Point-in-time health summary for one raw CSV output directory."""

    folder: str
    endpoint_folder: str | None = None
    expected_targets: int = EXPECTED_TARGETS
    organic_expected_targets: int = EXPECTED_TARGETS - len(ENDPOINT_EXCEPTION_TARGETS)
    endpoint_expected_targets: int = len(ENDPOINT_EXCEPTION_TARGETS)
    save_files: int = 0
    state_files: int = 0
    organic_save_files: int = 0
    organic_state_files: int = 0
    endpoint_save_files: int = 0
    endpoint_state_files: int = 0
    organic_lanes_present: int = 0
    organic_lanes_missing: int = 0
    endpoint_exceptions_present: int = 0
    endpoint_exceptions_missing: int = 0
    complete_pairs: int = 0
    missing_pairs: int = 0
    missing_save_for_state: int = 0
    missing_state_for_save: int = 0
    absent_targets: int = 0
    duplicate_target_entries: int = 0
    bad_names: int = 0
    bad_target_naming: int = 0
    bad_sizes: int = 0
    unsettled_files: int = 0
    ignored_directories: int = 0
    hash_check_enabled: bool = False
    hashes_checked: int = 0
    hash_mismatches: int = 0
    missing_hash_files: int = 0
    layout: str = LAYOUT_AUTO
    samples: dict[str, list[str]] = field(default_factory=dict)

    @property
    def progress_percent(self) -> float:
        """Return complete-pair progress through the 65536 raw CSV targets."""

        if self.expected_targets <= 0:
            return 0.0
        return (self.complete_pairs / self.expected_targets) * 100.0


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse CLI arguments."""

    parser = argparse.ArgumentParser(
        description="Audit first-half raw CSV .sav/.ss0 output pairs without touching mGBA.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_RAW_CSV_DIR)
    parser.add_argument("--expected-targets", type=int, default=EXPECTED_TARGETS)
    parser.add_argument(
        "--endpoint-folder",
        type=Path,
        default=None,
        help="Folder for labeled endpoint exceptions. Default is FOLDER/_endpoint_exceptions.",
    )
    parser.add_argument(
        "--disable-endpoint-exceptions",
        action="store_true",
        help="Treat every target under --expected-targets as one ordinary target set.",
    )
    parser.add_argument(
        "--manifest-folder",
        type=Path,
        default=None,
        help="Optional manifest folder for organic raw-CSV targets. Auto-detects 1sthalves/_manifests/raw_csv.",
    )
    parser.add_argument(
        "--endpoint-manifest-folder",
        type=Path,
        default=None,
        help="Optional manifest folder for endpoint exceptions. Auto-detects 1sthalves/_manifests/endpoint_exceptions.",
    )
    parser.add_argument(
        "--check-hashes",
        action="store_true",
        help="Read manifest JSON and compare recorded SHA-1 values to files. Slower on full corpora.",
    )
    parser.add_argument("--expected-save-size", type=int, default=EXPECTED_SAVE_SIZE)
    parser.add_argument("--expected-state-size", type=int, default=EXPECTED_STATE_SIZE)
    parser.add_argument(
        "--layout",
        choices=(LAYOUT_AUTO, LAYOUT_FLAT, LAYOUT_SPLIT),
        default=LAYOUT_AUTO,
        help="Read flat files, split saves/states subfolders, or auto-detect both.",
    )
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Treat very recently modified files as unsettled instead of bad-size.",
    )
    parser.add_argument("--sample-limit", type=int, default=20)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit nonzero on bad names, bad sizes, duplicates, or incomplete target set.",
    )
    return parser.parse_args(argv)


def _append_sample(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    """Keep bounded examples so live runs do not flood console output."""

    bucket = samples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(value)


def _normalize_folder(folder: Path) -> Path:
    """Resolve user-relative audit folders without following junction aliases."""

    folder = folder.expanduser()
    if not folder.is_absolute():
        folder = folder.absolute()
    return folder


def _scan_sources(folder: Path, layout: str, source_role: str) -> list[tuple[Path, str | None, str, str]]:
    """Return folders to scan and optional expected extension for each folder."""

    sources: list[tuple[Path, str | None, str, str]] = []
    saves_dir = folder / "saves"
    states_dir = folder / "states"
    if layout in {LAYOUT_FLAT, LAYOUT_AUTO}:
        sources.append((folder, None, "", source_role))
    if layout == LAYOUT_SPLIT or (
        layout == LAYOUT_AUTO and (saves_dir.is_dir() or states_dir.is_dir())
    ):
        sources.append((saves_dir, "sav", "saves/", source_role))
        sources.append((states_dir, "ss0", "states/", source_role))
    return sources


def _endpoint_targets(expected_targets: int, endpoint_exceptions: bool) -> set[int]:
    """Return endpoint targets that are in the current target universe."""

    if not endpoint_exceptions:
        return set()
    return {target for target in ENDPOINT_EXCEPTION_TARGETS if 0 <= target < expected_targets}


def _organic_targets(expected_targets: int, endpoint_targets: set[int]) -> set[int]:
    """Return organic targets after removing explicit endpoint exceptions."""

    return set(range(max(0, expected_targets))) - endpoint_targets


def _default_endpoint_folder(folder: Path) -> Path:
    """Return default endpoint-exception source folder for one raw CSV root."""

    return folder / "_endpoint_exceptions"


def _default_manifest_folder(folder: Path, source_role: str) -> Path | None:
    """Return default manifest folder if the surrounding 1sthalves layout exists."""

    if folder.name == "_raw_csv":
        manifest_root = folder.parent / "_manifests"
        return manifest_root / ("endpoint_exceptions" if source_role == SOURCE_ENDPOINT else "raw_csv")
    if folder.name == "1sthalves":
        manifest_root = folder / "_manifests"
        return manifest_root / ("endpoint_exceptions" if source_role == SOURCE_ENDPOINT else "raw_csv")
    return None


def _sha1_file(path: Path) -> str:
    """Return SHA-1 hex digest for one file without loading it all at once."""

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_u16_text(value: object) -> int | None:
    """Parse strings such as 0x1234 from manifest payloads."""

    if value is None:
        return None
    try:
        parsed = int(str(value), 0)
    except ValueError:
        return None
    if not 0 <= parsed <= 0xFFFF:
        return None
    return parsed


def _path_leaf_target(path_text: object, suffix: str) -> int | None:
    """Return target half encoded by a manifest path leaf."""

    if not path_text:
        return None
    path = Path(str(path_text))
    match = NAME_RE.match(path.name)
    if not match or match.group(2).lower() != suffix:
        return None
    return int(match.group(1), 16)


def _audit_manifest_folder(
    result: AuditResult,
    manifest_folder: Path | None,
    *,
    source_role: str,
    expected_targets: set[int],
    check_hashes: bool,
    sample_limit: int,
) -> None:
    """Check optional per-target manifests for path naming and SHA-1 drift."""

    if manifest_folder is None or not manifest_folder.is_dir():
        return

    for manifest_path in sorted(manifest_folder.glob("*.json")):
        match = MANIFEST_NAME_RE.match(manifest_path.name)
        if not match:
            result.bad_names += 1
            _append_sample(
                result.samples,
                "bad_manifest_names",
                str(manifest_path),
                sample_limit,
            )
            continue

        target = int(match.group(1), 16)
        if target not in expected_targets:
            result.bad_target_naming += 1
            _append_sample(
                result.samples,
                "bad_target_naming",
                f"{source_role} manifest target outside bucket: {manifest_path.name}",
                sample_limit,
            )

        try:
            payload = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            _append_sample(result.samples, "read_errors", f"{manifest_path}: {exc}", sample_limit)
            continue

        output_key_mode = str(payload.get("output_key_mode", "")).strip().lower()
        keys_that_must_match_filename = ["output_key_half"]
        if output_key_mode in {"live", "raw-csv-live-name", "endpoint-exception"}:
            keys_that_must_match_filename.extend(("spinda_half_live", "expected_daycare_lower_half"))

        for key in keys_that_must_match_filename:
            parsed = _parse_u16_text(payload.get(key))
            if parsed is None:
                continue
            if parsed != target:
                result.bad_target_naming += 1
                _append_sample(
                    result.samples,
                    "bad_target_naming",
                    f"{manifest_path.name} {key}={parsed:#06x} filename=0x{target:04X}",
                    sample_limit,
                )

        save_target = _path_leaf_target(payload.get("save_path"), "sav")
        state_target = _path_leaf_target(payload.get("pre_daycare_man_state_path"), "ss0")
        if save_target != target:
            result.bad_target_naming += 1
            _append_sample(
                result.samples,
                "bad_target_naming",
                f"{manifest_path.name} save_path target does not match filename",
                sample_limit,
            )
        if state_target != target:
            result.bad_target_naming += 1
            _append_sample(
                result.samples,
                "bad_target_naming",
                f"{manifest_path.name} pre_daycare_man_state_path target does not match filename",
                sample_limit,
            )

        if not check_hashes:
            continue

        for path_key, hash_key, label in (
            ("save_path", "save_sha1", "save"),
            ("pre_daycare_man_state_path", "pre_daycare_man_state_sha1", "state"),
        ):
            expected_hash = payload.get(hash_key)
            path_text = payload.get(path_key)
            if not expected_hash or not path_text:
                continue
            artifact_path = Path(str(path_text))
            if not artifact_path.is_file():
                result.missing_hash_files += 1
                _append_sample(
                    result.samples,
                    "missing_hash_files",
                    f"{manifest_path.name} {label}: {artifact_path}",
                    sample_limit,
                )
                continue
            result.hashes_checked += 1
            try:
                actual_hash = _sha1_file(artifact_path)
            except OSError as exc:
                _append_sample(result.samples, "read_errors", f"{artifact_path}: {exc}", sample_limit)
                continue
            if actual_hash.lower() != str(expected_hash).lower():
                result.hash_mismatches += 1
                _append_sample(
                    result.samples,
                    "hash_mismatches",
                    f"{manifest_path.name} {label}: actual={actual_hash} expected={expected_hash}",
                    sample_limit,
                )


def audit_raw_csv_folder(
    folder: Path = DEFAULT_RAW_CSV_DIR,
    *,
    expected_targets: int = EXPECTED_TARGETS,
    endpoint_folder: Path | None = None,
    endpoint_exceptions: bool = True,
    manifest_folder: Path | None = None,
    endpoint_manifest_folder: Path | None = None,
    check_hashes: bool = False,
    expected_save_size: int = EXPECTED_SAVE_SIZE,
    expected_state_size: int = EXPECTED_STATE_SIZE,
    settle_seconds: float = 2.0,
    sample_limit: int = 20,
    layout: str = LAYOUT_AUTO,
) -> AuditResult:
    """Return read-only pair/name/size audit for one raw CSV output folder."""

    folder = _normalize_folder(folder)
    manifest_folder_was_explicit = manifest_folder is not None
    endpoint_manifest_folder_was_explicit = endpoint_manifest_folder is not None
    endpoint_targets = _endpoint_targets(expected_targets, endpoint_exceptions)
    organic_targets = _organic_targets(expected_targets, endpoint_targets)
    endpoint_folder = (
        _normalize_folder(endpoint_folder)
        if endpoint_folder is not None
        else _default_endpoint_folder(folder)
    )
    manifest_folder = (
        _normalize_folder(manifest_folder)
        if manifest_folder is not None
        else _default_manifest_folder(folder, SOURCE_ORGANIC)
    )
    endpoint_manifest_folder = (
        _normalize_folder(endpoint_manifest_folder)
        if endpoint_manifest_folder is not None
        else _default_manifest_folder(folder, SOURCE_ENDPOINT)
    )
    now = time.time()
    result = AuditResult(
        folder=str(folder),
        endpoint_folder=str(endpoint_folder) if endpoint_exceptions else None,
        expected_targets=expected_targets,
        organic_expected_targets=len(organic_targets),
        endpoint_expected_targets=len(endpoint_targets),
        hash_check_enabled=check_hashes,
        layout=layout,
    )
    organic_save_targets: set[int] = set()
    organic_state_targets: set[int] = set()
    endpoint_save_targets: set[int] = set()
    endpoint_state_targets: set[int] = set()
    duplicate_targets: set[int] = set()
    read_any_source = False

    sources = _scan_sources(folder, layout, SOURCE_ORGANIC)
    if endpoint_exceptions and endpoint_folder is not None:
        sources.extend(_scan_sources(endpoint_folder, layout, SOURCE_ENDPOINT))

    for source, expected_kind, sample_prefix, source_role in sources:
        if not source.exists():
            if layout == LAYOUT_SPLIT:
                _append_sample(result.samples, "missing_folders", str(source), sample_limit)
            continue
        if not source.is_dir():
            _append_sample(result.samples, "bad_folders", str(source), sample_limit)
            continue
        read_any_source = True
        try:
            entries = source.iterdir()
        except OSError as exc:
            raise SystemExit(f"Could not read folder: {source} ({exc})") from exc
        for entry in entries:
            try:
                if entry.is_dir():
                    result.ignored_directories += 1
                    continue
                stat = entry.stat()
            except OSError as exc:
                _append_sample(result.samples, "read_errors", f"{entry}: {exc}", sample_limit)
                continue

            match = NAME_RE.match(entry.name)
            kind = match.group(2).lower() if match else None
            if not match or (expected_kind is not None and kind != expected_kind):
                result.bad_names += 1
                _append_sample(
                    result.samples,
                    "bad_names",
                    f"{sample_prefix}{entry.name}",
                    sample_limit,
                )
                continue

            target = int(match.group(1), 16)
            expected_size = expected_save_size if kind == "sav" else expected_state_size
            is_unsettled = now - stat.st_mtime < settle_seconds
            if stat.st_size != expected_size:
                if is_unsettled:
                    result.unsettled_files += 1
                    _append_sample(
                        result.samples,
                        "unsettled_files",
                        f"{sample_prefix}{entry.name}",
                        sample_limit,
                    )
                else:
                    result.bad_sizes += 1
                    _append_sample(
                        result.samples,
                        "bad_sizes",
                        f"{sample_prefix}{entry.name} size={stat.st_size} "
                        f"expected={expected_size}",
                        sample_limit,
                    )

            if kind == "sav":
                result.save_files += 1
            else:
                result.state_files += 1

            target_bucket = endpoint_targets if source_role == SOURCE_ENDPOINT else organic_targets
            if target not in target_bucket:
                result.bad_target_naming += 1
                expected_label = "endpoint exception" if source_role == SOURCE_ENDPOINT else "organic"
                _append_sample(
                    result.samples,
                    "bad_target_naming",
                    f"{sample_prefix}{entry.name} is not a valid {expected_label} target",
                    sample_limit,
                )
                continue

            if kind == "sav":
                target_set = endpoint_save_targets if source_role == SOURCE_ENDPOINT else organic_save_targets
            else:
                target_set = endpoint_state_targets if source_role == SOURCE_ENDPOINT else organic_state_targets
            if target in target_set:
                duplicate_targets.add(target)
            target_set.add(target)

    if not read_any_source:
        raise SystemExit(f"Could not read any raw CSV folders under: {folder}")

    result.duplicate_target_entries = len(duplicate_targets)
    for target in sorted(duplicate_targets)[:sample_limit]:
        _append_sample(result.samples, "duplicate_targets", f"0x{target:04X}", sample_limit)

    organic_paired = organic_save_targets & organic_state_targets
    endpoint_paired = endpoint_save_targets & endpoint_state_targets
    only_save = (organic_save_targets | endpoint_save_targets) - (
        organic_state_targets | endpoint_state_targets
    )
    only_state = (organic_state_targets | endpoint_state_targets) - (
        organic_save_targets | endpoint_save_targets
    )
    all_seen = (
        organic_save_targets
        | organic_state_targets
        | endpoint_save_targets
        | endpoint_state_targets
    )
    result.organic_save_files = len(organic_save_targets)
    result.organic_state_files = len(organic_state_targets)
    result.endpoint_save_files = len(endpoint_save_targets)
    result.endpoint_state_files = len(endpoint_state_targets)
    result.organic_lanes_present = len(organic_paired)
    result.endpoint_exceptions_present = len(endpoint_paired)
    result.organic_lanes_missing = len(organic_targets - organic_paired)
    result.endpoint_exceptions_missing = len(endpoint_targets - endpoint_paired)
    result.complete_pairs = result.organic_lanes_present + result.endpoint_exceptions_present
    result.missing_state_for_save = len(only_save)
    result.missing_save_for_state = len(only_state)
    result.absent_targets = len((organic_targets | endpoint_targets) - all_seen)
    result.missing_pairs = result.organic_lanes_missing + result.endpoint_exceptions_missing

    for target in sorted(only_save)[:sample_limit]:
        _append_sample(result.samples, "missing_state_for_save", f"0x{target:04X}", sample_limit)
    for target in sorted(only_state)[:sample_limit]:
        _append_sample(result.samples, "missing_save_for_state", f"0x{target:04X}", sample_limit)
    for target in sorted(organic_targets - organic_paired)[:sample_limit]:
        _append_sample(result.samples, "organic_lanes_missing", f"0x{target:04X}", sample_limit)
    for target in sorted(endpoint_targets - endpoint_paired)[:sample_limit]:
        _append_sample(result.samples, "endpoint_exceptions_missing", f"0x{target:04X}", sample_limit)
    for target in sorted((organic_targets | endpoint_targets) - all_seen)[:sample_limit]:
        _append_sample(result.samples, "absent_targets", f"0x{target:04X}", sample_limit)

    check_manifests = (
        check_hashes or manifest_folder_was_explicit or endpoint_manifest_folder_was_explicit
    )
    if check_manifests:
        _audit_manifest_folder(
            result,
            manifest_folder,
            source_role=SOURCE_ORGANIC,
            expected_targets=organic_targets,
            check_hashes=check_hashes,
            sample_limit=sample_limit,
        )
        if endpoint_exceptions:
            _audit_manifest_folder(
                result,
                endpoint_manifest_folder,
                source_role=SOURCE_ENDPOINT,
                expected_targets=endpoint_targets,
                check_hashes=check_hashes,
                sample_limit=sample_limit,
            )

    return result


def print_text(result: AuditResult) -> None:
    """Print compact human-readable audit output."""

    print(f"Folder: {result.folder}")
    if result.endpoint_folder:
        print(f"Endpoint exception folder: {result.endpoint_folder}")
    print(f"Layout: {result.layout}")
    print(f"Expected targets: {result.expected_targets}")
    print(f"Organic expected lanes: {result.organic_expected_targets}")
    print(f"Endpoint expected exceptions: {result.endpoint_expected_targets}")
    print(f".sav files: {result.save_files}")
    print(f".ss0 files: {result.state_files}")
    print(f"Organic lanes present: {result.organic_lanes_present}")
    print(f"Organic lanes missing: {result.organic_lanes_missing}")
    print(f"Endpoint exceptions present: {result.endpoint_exceptions_present}")
    print(f"Endpoint exceptions missing: {result.endpoint_exceptions_missing}")
    print(f"Complete .sav/.ss0 pairs: {result.complete_pairs}")
    print(f"Progress: {result.progress_percent:.3f}%")
    print(f"Missing pairs: {result.missing_pairs}")
    print(f"Missing .sav for existing .ss0: {result.missing_save_for_state}")
    print(f"Missing .ss0 for existing .sav: {result.missing_state_for_save}")
    print(f"Targets with neither file yet: {result.absent_targets}")
    print(f"Duplicate target entries: {result.duplicate_target_entries}")
    print(f"Bad names: {result.bad_names}")
    print(f"Bad target naming: {result.bad_target_naming}")
    print(f"Bad settled sizes: {result.bad_sizes}")
    print(f"Unsettled recent files: {result.unsettled_files}")
    print(f"Ignored directories: {result.ignored_directories}")
    if result.hash_check_enabled:
        print(f"Hashes checked: {result.hashes_checked}")
        print(f"Hash mismatches: {result.hash_mismatches}")
        print(f"Missing hash files: {result.missing_hash_files}")
    else:
        print("Hash mismatches: not checked (use --check-hashes)")

    for key, values in result.samples.items():
        if not values:
            continue
        print(f"\nSample {key}:")
        for value in values:
            print(f"  {value}")


def strict_failed(result: AuditResult) -> bool:
    """Return whether strict mode should fail this audit."""

    return any(
        (
            result.bad_names,
            result.bad_target_naming,
            result.bad_sizes,
            result.duplicate_target_entries,
            result.hash_mismatches,
            result.missing_hash_files,
            result.missing_pairs,
        )
    )


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    result = audit_raw_csv_folder(
        args.folder,
        expected_targets=args.expected_targets,
        endpoint_folder=args.endpoint_folder,
        endpoint_exceptions=not args.disable_endpoint_exceptions,
        manifest_folder=args.manifest_folder,
        endpoint_manifest_folder=args.endpoint_manifest_folder,
        check_hashes=args.check_hashes,
        expected_save_size=args.expected_save_size,
        expected_state_size=args.expected_state_size,
        settle_seconds=args.settle_seconds,
        sample_limit=args.sample_limit,
        layout=args.layout,
    )
    if args.json:
        print(json.dumps(asdict(result), indent=2, sort_keys=True))
    else:
        print_text(result)
    return 1 if args.strict and strict_failed(result) else 0


if __name__ == "__main__":
    raise SystemExit(main())
