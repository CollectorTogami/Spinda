"""Audit a Spinda corpus workspace without launching the emulator.

The roadmap already knows how to write saves, work states, lane blocks, and
JSON manifests. This auditor ties those pieces together so a long run can be
checked after interruption.

What it verifies:

- global manifest presence and basic consistency
- per-lane save/state/block paths
- manifest SHA-1 values for archive saves and work states when recorded
- block/bitmap readability
- whether the bitmap count matches the lane manifest's completed-upper-half
  count

This is intentionally offline-only. It is meant to answer "is the workspace in
one sane, resumable state?" before the next emulator-backed phase starts.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path

from spinda_frlg_archive import GlobalCorpusManifest, LaneBlockBuffer, global_manifest_path
from spinda_frlg_common import format_u16, lane_paths, load_lane_manifest, sha1_file


@dataclass(frozen=True)
class AuditFinding:
    """One workspace audit message."""

    level: str
    scope: str
    message: str


@dataclass
class AuditReport:
    """Aggregated result for one workspace audit."""

    workspace_root: Path
    findings: list[AuditFinding] = field(default_factory=list)
    audited_lane_count: int = 0

    def add(self, level: str, scope: str, message: str) -> None:
        self.findings.append(AuditFinding(level, scope, message))

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "warning")

    def ok(self) -> bool:
        return self.error_count == 0


def _manifest_paths(workspace_root: Path) -> list[Path]:
    """Return every canonical per-lane manifest path in the workspace."""

    manifests_dir = workspace_root.expanduser().resolve() / "manifests"
    if not manifests_dir.is_dir():
        return []
    return sorted(path for path in manifests_dir.glob("0x????.json") if path.is_file())


def _audit_global_manifest(workspace_root: Path, report: AuditReport) -> None:
    """Check the top-level manifest and its pointer to the current lane."""

    manifest_path = global_manifest_path(workspace_root)
    if not manifest_path.is_file():
        report.add("warning", "global", f"Global manifest does not exist yet: {manifest_path}")
        return

    manifest = GlobalCorpusManifest.load(manifest_path)
    if manifest.current_lane_id is not None:
        current_lane_manifest = workspace_root / "manifests" / f"{format_u16(manifest.current_lane_id)}.json"
        if not current_lane_manifest.is_file():
            report.add(
                "error",
                "global",
                "Global manifest points at a current lane that has no lane manifest: "
                f"{current_lane_manifest}",
            )


def _check_sha1(report: AuditReport, scope: str, path: Path, expected_sha1: str | None, label: str) -> None:
    """Validate one file against the SHA-1 recorded in a lane manifest."""

    if not path.is_file():
        if expected_sha1 is not None:
            report.add("error", scope, f"{label} is missing but the manifest records a SHA-1: {path}")
        else:
            report.add("warning", scope, f"{label} is missing: {path}")
        return

    if expected_sha1 is not None and sha1_file(path) != expected_sha1:
        report.add("error", scope, f"{label} SHA-1 does not match the lane manifest: {path}")


def _audit_lane_manifest(manifest_path: Path, report: AuditReport) -> None:
    """Audit one lane manifest plus its save/state/block artifacts."""

    manifest = load_lane_manifest(manifest_path)
    scope = format_u16(manifest.lane_id) or manifest_path.name
    report.audited_lane_count += 1

    expected_paths = lane_paths(report.workspace_root, manifest.lane_id)
    # The roadmap assumes a canonical on-disk layout for every lane. If a
    # manifest points somewhere else, resume tooling can start reading or
    # writing the wrong artifact even when the file contents themselves are fine.
    if manifest.manifest_path != expected_paths.manifest_path:
        report.add(
            "error",
            scope,
            "Lane manifest path is not the canonical location for this lane: "
            f"{manifest.manifest_path}",
        )
    if manifest.archive_save_path != expected_paths.archive_save_path:
        report.add(
            "error",
            scope,
            "Archive save path does not match the canonical lane save path: "
            f"{manifest.archive_save_path}",
        )
    if manifest.work_state_path != expected_paths.work_state_path:
        report.add(
            "error",
            scope,
            "Work-state path does not match the canonical lane state path: "
            f"{manifest.work_state_path}",
        )
    if manifest.block_path != expected_paths.block_path:
        report.add(
            "error",
            scope,
            "Block path does not match the canonical lane block path: "
            f"{manifest.block_path}",
        )

    _check_sha1(report, scope, manifest.archive_save_path, manifest.archive_save_sha1, "Archive save")
    _check_sha1(report, scope, manifest.work_state_path, manifest.work_state_sha1, "Work state")

    if manifest.block_path.is_file():
        try:
            block = LaneBlockBuffer.load(manifest.block_path)
        except Exception as exc:
            report.add(
                "error",
                scope,
                f"Could not load the lane block and bitmap cleanly: {manifest.block_path} ({exc})",
            )
            return

        present = block.count_present()
        # The bitmap is the authoritative "what has been written" signal for a
        # lane block. If it disagrees with the manifest counter, resume logic
        # could skip missing records or waste time redoing finished ones.
        if present != manifest.completed_upper_halves:
            report.add(
                "error",
                scope,
                "Lane manifest completed_upper_halves does not match the bitmap count: "
                f"manifest={manifest.completed_upper_halves} bitmap={present}",
            )
        if manifest.complete and present != 0x10000:
            report.add(
                "error",
                scope,
                f"Lane is marked complete but only {present} upper halves are present.",
            )
    else:
        if manifest.completed_upper_halves or manifest.complete:
            report.add(
                "error",
                scope,
                "Lane manifest says progress exists, but the lane block is missing: "
                f"{manifest.block_path}",
            )


def audit_workspace(workspace_root: Path) -> AuditReport:
    """Audit one Spinda workspace root."""

    workspace_root = workspace_root.expanduser().resolve()
    report = AuditReport(workspace_root)
    _audit_global_manifest(workspace_root, report)
    for manifest_path in _manifest_paths(workspace_root):
        _audit_lane_manifest(manifest_path, report)
    return report


def _print_report(report: AuditReport) -> None:
    """Print one human-readable workspace audit report."""

    print(f"Workspace: {report.workspace_root}")
    print(f"Audited lanes: {report.audited_lane_count}")
    print(f"Errors: {report.error_count}")
    print(f"Warnings: {report.warning_count}")
    for finding in report.findings:
        print(f"[{finding.level}] {finding.scope}: {finding.message}")


def build_parser() -> argparse.ArgumentParser:
    """Create the workspace-audit CLI."""

    parser = argparse.ArgumentParser(
        description="Audit a FR/LG Spinda workspace without launching mGBA.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--root", required=True, help="Workspace root directory.")
    return parser


def main() -> None:
    """Audit one workspace and exit non-zero on hard errors."""

    args = build_parser().parse_args()
    report = audit_workspace(Path(args.root))
    _print_report(report)
    raise SystemExit(0 if report.ok() else 1)


if __name__ == "__main__":
    main()
