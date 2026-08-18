r"""Create or inspect lane workspace metadata for the Spinda roadmap.

This utility does not run the emulator. It exists so the longer-running phase-2
automation has a consistent place to keep:

- the exported first-half `.sav`
- the work-state path for upper-half sweeps
- the raw `65536 * 80` block path
- the resume metadata for `next_upper_half`

Usage:
    <repo-root>\.venv-mgba\bin\python.exe frlg_spinda_lane_workspace.py init --root workspace --lane-id 0x1234
    <repo-root>\.venv-mgba\bin\python.exe frlg_spinda_lane_workspace.py status --root workspace --lane-id 0x1234
"""

from __future__ import annotations

import argparse
from pathlib import Path

from spinda_frlg_common import (
    LANE_BLOCK_RECORD_SIZE,
    LANE_BLOCK_RECORDS,
    LaneWorkspaceManifest,
    ensure_workspace_dirs,
    format_u16,
    lane_paths,
    load_lane_manifest,
    write_lane_manifest,
)


def parse_lane_id(text: str) -> int:
    """Parse one lane id as decimal or hex."""

    value = int(text, 0)
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("Lane ids must fit in 16 bits.")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI with `init` and `status` subcommands."""

    parser = argparse.ArgumentParser(
        description="Manage lane manifests for the FR/LG Spinda roadmap.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Create or update a lane workspace manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    init_parser.add_argument("--root", required=True, help="Workspace root directory.")
    init_parser.add_argument("--lane-id", required=True, type=parse_lane_id, help="Lower-half lane id.")
    init_parser.add_argument(
        "--notes",
        default="",
        help="Optional operator notes stored in the manifest.",
    )
    init_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace an existing lane manifest.",
    )
    init_parser.add_argument(
        "--allocate-block",
        action="store_true",
        help="Create the raw block file at its canonical final size right now.",
    )

    status_parser = subparsers.add_parser(
        "status",
        help="Print a short summary for an existing lane manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    status_parser.add_argument("--root", required=True, help="Workspace root directory.")
    status_parser.add_argument("--lane-id", required=True, type=parse_lane_id, help="Lower-half lane id.")
    return parser


def init_lane_workspace(
    workspace_root: Path,
    lane_id: int,
    *,
    notes: str = "",
    overwrite: bool = False,
    allocate_block: bool = False,
) -> LaneWorkspaceManifest:
    """Create the filesystem layout and manifest for one lane."""

    paths = lane_paths(workspace_root, lane_id)
    ensure_workspace_dirs(paths.workspace_root)
    if paths.manifest_path.exists() and not overwrite:
        raise SystemExit(
            "Refusing to overwrite an existing lane manifest without --overwrite: "
            f"{paths.manifest_path}"
        )

    if allocate_block and not paths.block_path.exists():
        paths.block_path.parent.mkdir(parents=True, exist_ok=True)
        with paths.block_path.open("wb") as handle:
            handle.truncate(LANE_BLOCK_RECORDS * LANE_BLOCK_RECORD_SIZE)

    manifest = LaneWorkspaceManifest(
        lane_id=lane_id,
        manifest_path=paths.manifest_path,
        archive_save_path=paths.archive_save_path,
        work_state_path=paths.work_state_path,
        block_path=paths.block_path,
        notes=notes,
    )
    write_lane_manifest(manifest)
    return manifest


def print_lane_status(manifest: LaneWorkspaceManifest) -> None:
    """Render one lane manifest in a short human-readable form."""

    print(f"Lane: {format_u16(manifest.lane_id)}")
    print(f"Archive save: {manifest.archive_save_path}")
    print(f"Archive save exists: {manifest.archive_save_path.exists()}")
    print(f"Work state: {manifest.work_state_path}")
    print(f"Work state exists: {manifest.work_state_path.exists()}")
    print(f"Block: {manifest.block_path}")
    print(f"Block exists: {manifest.block_path.exists()}")
    print(f"Next upper half: {format_u16(manifest.next_upper_half)}")
    print(f"Completed upper halves: {manifest.completed_upper_halves}")
    print(f"Complete: {manifest.complete}")
    if manifest.observed_lower_half is not None:
        print(f"Observed lower half: {format_u16(manifest.observed_lower_half)}")


def main() -> None:
    """Dispatch the requested lane-workspace subcommand."""

    args = build_parser().parse_args()
    if args.command == "init":
        manifest = init_lane_workspace(
            Path(args.root),
            args.lane_id,
            notes=args.notes,
            overwrite=args.overwrite,
            allocate_block=args.allocate_block,
        )
        print(f"Initialized lane workspace: {manifest.manifest_path}")
        return

    manifest_path = lane_paths(Path(args.root), args.lane_id).manifest_path
    if not manifest_path.is_file():
        raise SystemExit(f"Lane manifest does not exist yet: {manifest_path}")
    print_lane_status(load_lane_manifest(manifest_path))


if __name__ == "__main__":
    main()
