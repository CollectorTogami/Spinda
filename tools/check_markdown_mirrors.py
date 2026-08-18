#!/usr/bin/env python3
"""Check Markdown mirror pairs listed in MARKDOWN_MIRROR_MANIFEST.md.

This tool is intentionally small and dependency-free. It reads the mirror table
that humans maintain in the manifest, hashes both sides of each pair, and exits
nonzero when a byte-identical mirror drifts or either side is missing.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
import re
import sys


DEFAULT_MANIFEST = Path("markdown-files") / "MARKDOWN_MIRROR_MANIFEST.md"
PATH_RE = re.compile(r"`([^`]+)`")


@dataclass(frozen=True)
class MirrorRow:
    main_path: str
    clean_path: str
    sync_direction: str
    expected_state: str


@dataclass(frozen=True)
class MirrorResult:
    main_path: str
    clean_path: str
    expected_state: str
    main_exists: bool
    clean_exists: bool
    main_hash: str | None
    clean_hash: str | None
    status: str
    message: str


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def clean_cell(cell: str) -> str:
    cell = cell.strip()
    match = PATH_RE.fullmatch(cell)
    if match:
        return match.group(1)
    return cell.strip("`").strip()


def parse_manifest_table(manifest_text: str) -> list[MirrorRow]:
    rows: list[MirrorRow] = []
    in_table = False
    for raw_line in manifest_text.splitlines():
        line = raw_line.strip()
        if line == "## Mirror Table":
            in_table = True
            continue
        if in_table and line.startswith("## "):
            break
        if not in_table or not line.startswith("|"):
            continue
        if line.startswith("| ---") or "Main workspace path" in line:
            continue

        cells = [clean_cell(part) for part in line.strip("|").split("|")]
        if len(cells) < 4:
            continue
        rows.append(
            MirrorRow(
                main_path=cells[0],
                clean_path=cells[1],
                sync_direction=cells[2],
                expected_state=cells[3],
            )
        )
    if not rows:
        raise ValueError("no mirror rows found in manifest")
    return rows


def check_row(root: Path, row: MirrorRow) -> MirrorResult:
    main = root / row.main_path
    clean = root / row.clean_path
    main_exists = main.is_file()
    clean_exists = clean.is_file()
    main_hash = sha256(main) if main_exists else None
    clean_hash = sha256(clean) if clean_exists else None

    if not main_exists or not clean_exists:
        missing = []
        if not main_exists:
            missing.append(row.main_path)
        if not clean_exists:
            missing.append(row.clean_path)
        return MirrorResult(
            row.main_path,
            row.clean_path,
            row.expected_state,
            main_exists,
            clean_exists,
            main_hash,
            clean_hash,
            "FAIL",
            "missing: " + ", ".join(missing),
        )

    if row.expected_state == "hash-match":
        if main_hash == clean_hash:
            return MirrorResult(
                row.main_path,
                row.clean_path,
                row.expected_state,
                main_exists,
                clean_exists,
                main_hash,
                clean_hash,
                "PASS",
                "byte-identical",
            )
        return MirrorResult(
            row.main_path,
            row.clean_path,
            row.expected_state,
            main_exists,
            clean_exists,
            main_hash,
            clean_hash,
            "FAIL",
            f"hash drift: {main_hash[:12]} != {clean_hash[:12]}",
        )

    if row.expected_state == "intentional-divergent":
        message = "topic-sync pair exists"
        if main_hash == clean_hash:
            message = "topic-sync pair exists, currently byte-identical"
        return MirrorResult(
            row.main_path,
            row.clean_path,
            row.expected_state,
            main_exists,
            clean_exists,
            main_hash,
            clean_hash,
            "PASS",
            message,
        )

    return MirrorResult(
        row.main_path,
        row.clean_path,
        row.expected_state,
        main_exists,
        clean_exists,
        main_hash,
        clean_hash,
        "FAIL",
        "unknown expected hash state",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="workspace root; default: current directory",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"manifest path relative to root; default: {DEFAULT_MANIFEST}",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    root = args.root.resolve()
    manifest = args.manifest
    if not manifest.is_absolute():
        manifest = root / manifest

    try:
        rows = parse_manifest_table(manifest.read_text(encoding="utf-8"))
        results = [check_row(root, row) for row in rows]
    except Exception as exc:  # noqa: BLE001 - command-line tool should report all parse errors cleanly.
        print(f"FAIL: {exc}", file=sys.stderr)
        return 2

    if args.json:
        print(json.dumps([asdict(result) for result in results], indent=2))
    else:
        for result in results:
            print(
                f"{result.status:4} {result.main_path} <=> "
                f"{result.clean_path}: {result.message}"
            )

    return 1 if any(result.status != "PASS" for result in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
