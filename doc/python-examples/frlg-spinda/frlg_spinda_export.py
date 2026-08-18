"""Export raw lane-block records as `.pk3`, directories, or ZIP archives.

This script operates entirely on the archive layer. It does not use the
emulator. The canonical input is the headerless lane block plus its bitmap
sidecar.

The default `.pk3` export here writes the stored 80-byte boxed record exactly
as it appears in the corpus. That matches the current roadmap assumption that
the canonical archive object is the authentic 80-byte boxed Gen 3 record.
"""

from __future__ import annotations

import argparse
import io
from collections.abc import Iterator
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile

from spinda_frlg_archive import (
    LaneBitmap,
    LaneBlockBuffer,
    lane_bitmap_path,
    lane_record_offset,
    pk3_filename,
)
from spinda_frlg_common import (
    LANE_BLOCK_RECORD_SIZE,
    format_u16,
    lane_paths,
    load_lane_manifest,
    write_bytes_atomic,
)


def parse_u16(text: str) -> int:
    """Parse one 16-bit lane or upper-half value."""

    try:
        value = int(text, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Value must be a 16-bit integer.") from exc
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("Value must fit in 16 bits.")
    return value


def _compression(method: str) -> int:
    """Map one friendly CLI compression name to the zipfile constant."""

    if method == "deflated":
        return ZIP_DEFLATED
    if method == "stored":
        return ZIP_STORED
    raise ValueError(f"Unsupported compression method: {method}")


def build_parser() -> argparse.ArgumentParser:
    """Create the export CLI."""

    parser = argparse.ArgumentParser(
        description="Export FR/LG Spinda lane blocks as .pk3 files, directories, ZIPs, or nested ZIPs.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    pk3_parser = subparsers.add_parser(
        "pk3",
        help="Export one upper-half record from one lane block.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    pk3_parser.add_argument("--root", required=True, help="Workspace root directory.")
    pk3_parser.add_argument("--lane-id", required=True, type=parse_u16, help="Lower-half lane id.")
    pk3_parser.add_argument("--upper-half", required=True, type=parse_u16, help="Upper-half index.")
    pk3_parser.add_argument(
        "--output",
        help="Output path. Defaults to a `.pk3` file in the current directory.",
    )

    lane_zip_parser = subparsers.add_parser(
        "lane-zip",
        help="Export one lane as a ZIP of `.pk3` files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    lane_zip_parser.add_argument("--root", required=True, help="Workspace root directory.")
    lane_zip_parser.add_argument("--lane-id", required=True, type=parse_u16, help="Lower-half lane id.")
    lane_zip_parser.add_argument(
        "--output",
        help="ZIP output path. Defaults to `0x####.zip` in the current directory.",
    )
    lane_zip_parser.add_argument(
        "--compression",
        choices=("deflated", "stored"),
        default="deflated",
        help="ZIP compression mode.",
    )

    lane_dir_parser = subparsers.add_parser(
        "lane-dir",
        help="Export one lane as loose `.pk3` files in a directory.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    lane_dir_parser.add_argument("--root", required=True, help="Workspace root directory.")
    lane_dir_parser.add_argument("--lane-id", required=True, type=parse_u16, help="Lower-half lane id.")
    lane_dir_parser.add_argument(
        "--output",
        help="Output directory. Defaults to `0x####-pk3` in the current directory.",
    )
    lane_dir_parser.add_argument("--start", type=parse_u16, default=0, help="First upper-half index to include.")
    lane_dir_parser.add_argument("--end", type=parse_u16, default=0xFFFF, help="Last upper-half index to include.")

    range_zip_parser = subparsers.add_parser(
        "range-zip",
        help="Export one inclusive upper-half range from one lane as a ZIP of `.pk3` files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    range_zip_parser.add_argument("--root", required=True, help="Workspace root directory.")
    range_zip_parser.add_argument("--lane-id", required=True, type=parse_u16, help="Lower-half lane id.")
    range_zip_parser.add_argument("--start", required=True, type=parse_u16, help="First upper-half index to include.")
    range_zip_parser.add_argument("--end", required=True, type=parse_u16, help="Last upper-half index to include.")
    range_zip_parser.add_argument(
        "--output",
        help="ZIP output path. Defaults to `0x####-0x####-0x####.zip` in the current directory.",
    )
    range_zip_parser.add_argument(
        "--compression",
        choices=("deflated", "stored"),
        default="deflated",
        help="ZIP compression mode.",
    )

    nested_parser = subparsers.add_parser(
        "nested-zip",
        help="Export many lanes as one outer ZIP containing one inner ZIP per lane.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    nested_parser.add_argument("--root", required=True, help="Workspace root directory.")
    nested_parser.add_argument(
        "--output",
        required=True,
        help="Outer ZIP path.",
    )
    nested_parser.add_argument(
        "--lane-id",
        dest="lane_ids",
        action="append",
        type=parse_u16,
        help="One lane id to include. Repeat to limit the export; omit to include all manifests.",
    )
    nested_parser.add_argument(
        "--compression",
        choices=("deflated", "stored"),
        default="deflated",
        help="ZIP compression mode for both the outer and inner archives.",
    )

    return parser


def _load_lane_buffer(workspace_root: Path, lane_id: int) -> LaneBlockBuffer:
    """Load one lane block from the canonical workspace layout."""

    paths = lane_paths(workspace_root, lane_id)
    return LaneBlockBuffer.load(paths.block_path)


def _read_one_record(workspace_root: Path, lane_id: int, upper_half: int) -> bytes:
    """Read one present record without loading the whole 5 MiB lane block."""

    paths = lane_paths(workspace_root, lane_id)
    bitmap = LaneBitmap.load(lane_bitmap_path(paths.block_path))
    if not bitmap.is_present(upper_half):
        raise SystemExit(
            f"Lane {format_u16(lane_id)} does not contain upper half {format_u16(upper_half)}."
        )

    with paths.block_path.open("rb") as handle:
        handle.seek(lane_record_offset(upper_half))
        record = handle.read(LANE_BLOCK_RECORD_SIZE)
    if len(record) != LANE_BLOCK_RECORD_SIZE:
        raise SystemExit(
            f"Lane {format_u16(lane_id)} block ended before upper half {format_u16(upper_half)}."
        )
    return record


def _validate_upper_half_range(start: int, end: int) -> None:
    """Reject an invalid inclusive upper-half range before writing output."""

    if start > end:
        raise SystemExit(
            f"Upper-half range start {format_u16(start)} is greater than end {format_u16(end)}."
        )


def _iter_present_range(buffer: LaneBlockBuffer, start: int = 0, end: int = 0xFFFF) -> Iterator[int]:
    """Yield present upper halves within one inclusive range."""

    _validate_upper_half_range(start, end)
    for upper_half in buffer.bitmap.iter_present():
        if upper_half < start:
            continue
        if upper_half > end:
            # The bitmap iterator is sorted, so range exports can stop as soon
            # as they pass the requested upper-half window.
            break
        yield upper_half


def export_pk3(workspace_root: Path, lane_id: int, upper_half: int, output_path: Path) -> Path:
    """Write one raw 80-byte record to a `.pk3` file."""

    record = _read_one_record(workspace_root, lane_id, upper_half)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    write_bytes_atomic(output_path, record)
    return output_path


def _write_lane_zip_to_handle(
    workspace_root: Path,
    lane_id: int,
    handle,
    *,
    compression: int,
    start: int = 0,
    end: int = 0xFFFF,
) -> int:
    """Write present records from one lane into an already-open ZIP handle."""

    _validate_upper_half_range(start, end)
    buffer = _load_lane_buffer(workspace_root, lane_id)
    written = 0
    with ZipFile(handle, "w", compression=compression, allowZip64=True) as archive:
        # Stream one record at a time out of the fixed-width lane block. That
        # keeps range and nested exports from materializing a list of records.
        for upper_half in _iter_present_range(buffer, start, end):
            archive.writestr(pk3_filename(lane_id, upper_half), buffer.get_record(upper_half))
            written += 1
    return written


def export_lane_zip(workspace_root: Path, lane_id: int, output_path: Path, *, compression: int) -> tuple[Path, int]:
    """Write one lane ZIP containing every present `.pk3` record."""

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        count = _write_lane_zip_to_handle(workspace_root, lane_id, handle, compression=compression)
    return output_path, count


def export_lane_directory(
    workspace_root: Path,
    lane_id: int,
    output_dir: Path,
    *,
    start: int = 0,
    end: int = 0xFFFF,
) -> tuple[Path, int]:
    """Write present records from one lane as loose `.pk3` files."""

    _validate_upper_half_range(start, end)
    buffer = _load_lane_buffer(workspace_root, lane_id)
    output_dir.mkdir(parents=True, exist_ok=True)

    written = 0
    for upper_half in _iter_present_range(buffer, start, end):
        write_bytes_atomic(output_dir / pk3_filename(lane_id, upper_half), buffer.get_record(upper_half))
        written += 1
    return output_dir, written


def export_range_zip(
    workspace_root: Path,
    lane_id: int,
    output_path: Path,
    *,
    start: int,
    end: int,
    compression: int,
) -> tuple[Path, int]:
    """Write one lane ZIP containing only an inclusive upper-half range."""

    _validate_upper_half_range(start, end)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("wb") as handle:
        count = _write_lane_zip_to_handle(
            workspace_root,
            lane_id,
            handle,
            compression=compression,
            start=start,
            end=end,
        )
    return output_path, count


def _discover_lane_ids(workspace_root: Path) -> list[int]:
    """Discover lane ids by scanning canonical lane manifests."""

    manifests_dir = workspace_root / "manifests"
    if not manifests_dir.is_dir():
        return []

    lane_ids: list[int] = []
    for manifest_path in sorted(manifests_dir.glob("0x????.json")):
        lane_ids.append(load_lane_manifest(manifest_path).lane_id)
    return lane_ids


def export_nested_zip(
    workspace_root: Path,
    output_path: Path,
    *,
    lane_ids: list[int] | None = None,
    compression: int,
) -> tuple[Path, list[tuple[int, int]]]:
    """Write one outer ZIP containing one inner lane ZIP per lane."""

    if lane_ids is None:
        lane_ids = _discover_lane_ids(workspace_root)
    # Nested exports are meant to be operator-facing deliverables. If there are
    # no lanes to include, failing loudly is more helpful than silently writing
    # an empty outer ZIP that looks valid at a glance.
    lane_ids = sorted(set(lane_ids))
    if not lane_ids:
        raise SystemExit("No lane manifests were found to export.")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    summary: list[tuple[int, int]] = []
    with ZipFile(output_path, "w", compression=compression, allowZip64=True) as outer:
        for lane_id in lane_ids:
            inner_buffer = io.BytesIO()
            count = _write_lane_zip_to_handle(
                workspace_root,
                lane_id,
                inner_buffer,
                compression=compression,
            )
            outer.writestr(f"{format_u16(lane_id)}.zip", inner_buffer.getvalue())
            summary.append((lane_id, count))
    return output_path, summary


def main() -> None:
    """Dispatch the requested export subcommand."""

    args = build_parser().parse_args()
    workspace_root = Path(args.root).expanduser().resolve()

    if args.command == "pk3":
        output = Path(args.output) if args.output else Path(pk3_filename(args.lane_id, args.upper_half))
        output = output.expanduser().resolve()
        export_pk3(workspace_root, args.lane_id, args.upper_half, output)
        print(f"Wrote PK3: {output}")
        return

    if args.command == "lane-zip":
        default_name = f"{format_u16(args.lane_id)}.zip"
        output = Path(args.output) if args.output else Path(default_name)
        output = output.expanduser().resolve()
        output, count = export_lane_zip(
            workspace_root,
            args.lane_id,
            output,
            compression=_compression(args.compression),
        )
        print(f"Wrote lane ZIP: {output}")
        print(f"Contained records: {count}")
        return

    if args.command == "lane-dir":
        default_name = f"{format_u16(args.lane_id)}-pk3"
        output = Path(args.output) if args.output else Path(default_name)
        output = output.expanduser().resolve()
        output, count = export_lane_directory(
            workspace_root,
            args.lane_id,
            output,
            start=args.start,
            end=args.end,
        )
        print(f"Wrote lane directory: {output}")
        print(f"Contained records: {count}")
        return

    if args.command == "range-zip":
        default_name = f"{format_u16(args.lane_id)}-{format_u16(args.start)}-{format_u16(args.end)}.zip"
        output = Path(args.output) if args.output else Path(default_name)
        output = output.expanduser().resolve()
        output, count = export_range_zip(
            workspace_root,
            args.lane_id,
            output,
            start=args.start,
            end=args.end,
            compression=_compression(args.compression),
        )
        print(f"Wrote range ZIP: {output}")
        print(f"Contained records: {count}")
        return

    output = Path(args.output).expanduser().resolve()
    output, summary = export_nested_zip(
        workspace_root,
        output,
        lane_ids=args.lane_ids,
        compression=_compression(args.compression),
    )
    print(f"Wrote nested ZIP: {output}")
    print(f"Lanes included: {len(summary)}")
    for lane_id, count in summary:
        print(f"  {format_u16(lane_id)} -> {count} record(s)")


if __name__ == "__main__":
    main()
