"""Manage the top-level corpus manifest for the FR/LG Spinda roadmap.

This script is intentionally boring. It does not launch mGBA. Its only job is
to make the long-running corpus process resumable by keeping one small global
JSON file up to date.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from spinda_frlg_archive import GlobalCorpusManifest, global_manifest_path, init_global_manifest
from spinda_frlg_common import format_u16


def parse_u16(text: str) -> int:
    """Parse one 16-bit lane or upper-half value."""

    value = int(text, 0)
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("Value must fit in 16 bits.")
    return value


def build_parser() -> argparse.ArgumentParser:
    """Create the global-manifest CLI."""

    parser = argparse.ArgumentParser(
        description="Manage the FR/LG Spinda corpus global manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser(
        "init",
        help="Create a new global manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    init_parser.add_argument("--root", required=True, help="Workspace root directory.")
    init_parser.add_argument("--notes", default="", help="Optional operator notes.")

    status_parser = subparsers.add_parser(
        "status",
        help="Print the current global manifest.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    status_parser.add_argument("--root", required=True, help="Workspace root directory.")

    mark_parser = subparsers.add_parser(
        "mark-lane",
        help="Update the current lane and upper-half position.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    mark_parser.add_argument("--root", required=True, help="Workspace root directory.")
    mark_parser.add_argument("--lane-id", type=parse_u16, help="Current active lane id.")
    mark_parser.add_argument("--upper-half", type=parse_u16, help="Current active upper-half index.")
    mark_parser.add_argument("--stage", help="Optional new stage string.")
    mark_parser.add_argument(
        "--increment-completed-lanes",
        action="store_true",
        help="Increase the completed-lane count by one.",
    )
    mark_parser.add_argument("--next-lane-id", type=parse_u16, help="Next lane to process.")

    return parser


def _load_manifest(workspace_root: Path) -> GlobalCorpusManifest:
    """Load the global manifest or fail with a clear operator-facing message."""

    manifest_path = global_manifest_path(workspace_root)
    if not manifest_path.is_file():
        raise SystemExit(f"Global manifest does not exist yet: {manifest_path}")
    return GlobalCorpusManifest.load(manifest_path)


def print_manifest(manifest: GlobalCorpusManifest) -> None:
    """Render one short human-readable status view."""

    print(f"Manifest: {manifest.manifest_path}")
    print(f"Workspace: {manifest.workspace_root}")
    print(f"Stage: {manifest.stage}")
    print(f"Current lane: {format_u16(manifest.current_lane_id)}")
    print(f"Current upper half: {format_u16(manifest.current_upper_half)}")
    print(f"Next lane: {format_u16(manifest.next_lane_id)}")
    print(f"Completed lanes: {manifest.completed_lane_count}")
    if manifest.notes:
        print(f"Notes: {manifest.notes}")


def main() -> None:
    """Dispatch the requested global-manifest command."""

    args = build_parser().parse_args()
    workspace_root = Path(args.root).expanduser().resolve()

    if args.command == "init":
        manifest = init_global_manifest(workspace_root, notes=args.notes)
        print(f"Initialized global manifest: {manifest.manifest_path}")
        return

    manifest = _load_manifest(workspace_root)

    if args.command == "status":
        print_manifest(manifest)
        return

    if args.lane_id is not None:
        manifest.current_lane_id = args.lane_id
    if args.upper_half is not None:
        manifest.current_upper_half = args.upper_half
    if args.stage:
        manifest.stage = args.stage
    if args.increment_completed_lanes:
        manifest.completed_lane_count += 1
    if args.next_lane_id is not None:
        manifest.next_lane_id = args.next_lane_id
    manifest.save()
    print_manifest(manifest)


if __name__ == "__main__":
    main()
