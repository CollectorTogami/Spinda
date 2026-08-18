from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

from accuracy_common import (
    ACCURACY_RELEVANT_SUBTREES,
    CORE_EMULATION_SUBTREES,
    DEFAULT_CUSTOM_ROOT,
    DEFAULT_OUTPUT_DIR,
    DEFAULT_UPSTREAM_ROOT,
    rel_posix,
    sha256_file,
    write_json,
)


@dataclass(frozen=True)
class FileDelta:
    relative_path: str
    status: str
    custom_sha256: str | None = None
    upstream_sha256: str | None = None


@dataclass(frozen=True)
class SubtreeSummary:
    subtree: str
    custom_files: int
    upstream_files: int
    added_files: int
    removed_files: int
    changed_files: int
    unchanged_files: int
    identical: bool
    deltas: list[FileDelta]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare the custom mGBA source tree to the upstream 0.10.5 source tree "
            "for accuracy-relevant subtrees."
        )
    )
    parser.add_argument(
        "--custom-root",
        type=Path,
        default=DEFAULT_CUSTOM_ROOT,
        help=f"Custom fork root. Default: {DEFAULT_CUSTOM_ROOT}",
    )
    parser.add_argument(
        "--upstream-root",
        type=Path,
        default=DEFAULT_UPSTREAM_ROOT,
        help=f"Upstream reference root. Default: {DEFAULT_UPSTREAM_ROOT}",
    )
    parser.add_argument(
        "--subtree",
        action="append",
        dest="subtrees",
        help=(
            "Subtree to compare. Repeat to limit the audit. "
            f"Defaults to: {', '.join(ACCURACY_RELEVANT_SUBTREES)}"
        ),
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "source_tree_accuracy_report.json",
        help="Where to write the JSON report.",
    )
    return parser.parse_args()


def _collect_files(root: Path, subtree: str) -> dict[str, Path]:
    subtree_root = root / subtree
    if not subtree_root.exists():
        return {}
    return {
        rel_posix(path, root): path
        for path in subtree_root.rglob("*")
        if path.is_file()
    }


def compare_subtree(custom_root: Path, upstream_root: Path, subtree: str) -> SubtreeSummary:
    custom_files = _collect_files(custom_root, subtree)
    upstream_files = _collect_files(upstream_root, subtree)
    all_paths = sorted(set(custom_files) | set(upstream_files))
    deltas: list[FileDelta] = []
    added = removed = changed = unchanged = 0

    for relative_path in all_paths:
        custom_path = custom_files.get(relative_path)
        upstream_path = upstream_files.get(relative_path)
        if custom_path and not upstream_path:
            added += 1
            deltas.append(
                FileDelta(
                    relative_path=relative_path,
                    status="added",
                    custom_sha256=sha256_file(custom_path),
                )
            )
            continue
        if upstream_path and not custom_path:
            removed += 1
            deltas.append(
                FileDelta(
                    relative_path=relative_path,
                    status="removed",
                    upstream_sha256=sha256_file(upstream_path),
                )
            )
            continue
        assert custom_path is not None and upstream_path is not None
        custom_sha = sha256_file(custom_path)
        upstream_sha = sha256_file(upstream_path)
        if custom_sha == upstream_sha:
            unchanged += 1
            continue
        changed += 1
        deltas.append(
            FileDelta(
                relative_path=relative_path,
                status="changed",
                custom_sha256=custom_sha,
                upstream_sha256=upstream_sha,
            )
        )

    return SubtreeSummary(
        subtree=subtree,
        custom_files=len(custom_files),
        upstream_files=len(upstream_files),
        added_files=added,
        removed_files=removed,
        changed_files=changed,
        unchanged_files=unchanged,
        identical=(added == 0 and removed == 0 and changed == 0),
        deltas=deltas,
    )


def main() -> int:
    args = parse_args()
    subtrees = tuple(args.subtrees or ACCURACY_RELEVANT_SUBTREES)
    summaries = [
        compare_subtree(args.custom_root, args.upstream_root, subtree)
        for subtree in subtrees
    ]
    core_identical = all(
        summary.identical for summary in summaries if summary.subtree in CORE_EMULATION_SUBTREES
    )
    payload = {
        "custom_root": args.custom_root,
        "upstream_root": args.upstream_root,
        "subtrees": summaries,
        "core_emulation_subtrees_identical": core_identical,
        "accuracy_interpretation": {
            "high_confidence_core_match": core_identical,
            "note": (
                "This is a source-tree comparison harness. It does not prove runtime parity "
                "by itself, but it quickly shows whether the hardware-emulation subtrees have changed."
            ),
        },
    }
    write_json(args.output_json, payload)
    print(f"Wrote source-tree accuracy report: {args.output_json}")
    print(f"Core emulation subtrees identical: {core_identical}")
    for summary in summaries:
        print(
            f"{summary.subtree}: changed={summary.changed_files}"
            f" added={summary.added_files} removed={summary.removed_files}"
            f" unchanged={summary.unchanged_files}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
