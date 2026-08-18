#!/usr/bin/env python3
"""Split a typed level-3 SPC3 v2 file into native-readable lane shards."""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import spc3_rule_bitmap_repack as base  # noqa: E402


ROOT = SCRIPT_DIR.parents[1]
DEFAULT_INPUT = (
    ROOT
    / "Helper-PC-Artifacts"
    / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.materialized-v2.spc3"
)
DEFAULT_OUTPUT_DIR = ROOT / "Helper-PC-Artifacts" / "v7-materialized-v2-shards"
DEFAULT_REPORT = DEFAULT_OUTPUT_DIR / "_shard_report.json"


def parse_lane_arg(value: str) -> int:
    try:
        lane = int(value, 0)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"invalid lane value: {value!r}") from exc
    if lane < 0 or lane > 0xFFFF:
        raise argparse.ArgumentTypeError(f"lane out of range 0..0xFFFF: {value!r}")
    return lane


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--chunk-lanes", type=int, default=256)
    parser.add_argument("--lane-from", type=parse_lane_arg, default=0)
    parser.add_argument("--lane-to", type=parse_lane_arg, default=0xFFFF)
    parser.add_argument("--progress-every", type=int, default=16)
    return parser.parse_args()


def selected_entries(
    entries: list[base.LaneEntry],
    lane_from: int,
    lane_to: int,
) -> list[base.LaneEntry]:
    if lane_to < lane_from:
        raise ValueError("--lane-to must be greater than or equal to --lane-from")
    return [entry for entry in entries if lane_from <= entry.lane <= lane_to]


def chunk_entries(
    entries: list[base.LaneEntry],
    chunk_lanes: int,
) -> list[list[base.LaneEntry]]:
    if chunk_lanes < 1:
        raise ValueError("--chunk-lanes must be positive")
    return [entries[index : index + chunk_lanes] for index in range(0, len(entries), chunk_lanes)]


def write_shard(
    *,
    src,
    old_header: base.Header,
    predictor_stream: bytes,
    entries: list[base.LaneEntry],
    output_path: Path,
) -> dict[str, object]:
    table_offset = base.SPC3_HEADER_SIZE + len(predictor_stream)
    table_size = len(entries) * base.SPC3_TABLE_ENTRY_SIZE
    data_offset = table_offset + table_size

    temp_path, out = base.open_temp_output(output_path)
    new_entries: list[base.LaneEntry] = []
    try:
        out.write(b"\x00" * base.SPC3_HEADER_SIZE)
        out.write(predictor_stream)
        out.write(b"\x00" * table_size)

        for entry in entries:
            lane_stream = base.read_exact_at(src, entry.stream_offset, entry.stream_size)
            stream_offset = out.tell()
            out.write(lane_stream)
            new_entries.append(replace(entry, stream_offset=stream_offset))

        data_size = out.tell() - data_offset
        new_header = replace(
            old_header,
            lane_count=len(new_entries),
            predictor_offset=base.SPC3_HEADER_SIZE,
            predictor_size=len(predictor_stream),
            table_offset=table_offset,
            table_entry_size=base.SPC3_TABLE_ENTRY_SIZE,
            data_offset=data_offset,
            data_size=data_size,
        )
        out.seek(0)
        out.write(base.pack_header(new_header))
        out.seek(table_offset)
        for entry in new_entries:
            out.write(base.pack_lane_entry(entry))
        out.close()
        base.atomic_replace(temp_path, output_path)
    except Exception:
        out.close()
        try:
            temp_path.unlink()
        except FileNotFoundError:
            pass
        raise

    return {
        "path": str(output_path),
        "lane_from": f"0x{entries[0].lane:04X}",
        "lane_to": f"0x{entries[-1].lane:04X}",
        "lane_count": len(entries),
        "size_bytes": output_path.stat().st_size,
        "data_size_bytes": data_size,
    }


def split_shards(
    *,
    input_path: Path,
    output_dir: Path,
    report_path: Path,
    chunk_lanes: int,
    lane_from: int,
    lane_to: int,
    progress_every: int,
) -> dict[str, object]:
    started = time.perf_counter()
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("rb") as src:
        old_header = base.parse_header(src)
        if old_header.version != base.SPC3_VERSION_V2:
            raise RuntimeError(f"input is not SPC3 v2: version={old_header.version}")
        if old_header.level != base.SPC3_LEVEL or old_header.flags & base.SPC3_FLAG_PREDICTOR_EMBEDDED == 0:
            raise RuntimeError("input must be embedded-predictor level-3 SPC3")
        old_entries = base.parse_lane_entries(src, old_header)
        entries = selected_entries(old_entries, lane_from, lane_to)
        chunks = chunk_entries(entries, chunk_lanes)
        predictor_stream = base.read_exact_at(src, old_header.predictor_offset, old_header.predictor_size)

        shard_reports = []
        for index, chunk in enumerate(chunks, 1):
            output_path = output_dir / f"typed-v2-0x{chunk[0].lane:04X}-0x{chunk[-1].lane:04X}.spc3"
            shard_reports.append(
                write_shard(
                    src=src,
                    old_header=old_header,
                    predictor_stream=predictor_stream,
                    entries=chunk,
                    output_path=output_path,
                )
            )
            if progress_every and (index % progress_every == 0 or index == len(chunks)):
                print(f"sharded {index}/{len(chunks)} files", flush=True)

    elapsed = time.perf_counter() - started
    report = {
        "schema": "spc3_typed_v2_shards.v1",
        "input": str(input_path),
        "output_dir": str(output_dir),
        "chunk_lanes": chunk_lanes,
        "lane_from": f"0x{lane_from:04X}",
        "lane_to": f"0x{lane_to:04X}",
        "elapsed_seconds": elapsed,
        "input_header": asdict(old_header),
        "shard_count": len(shard_reports),
        "lane_count": len(entries),
        "total_size_bytes": sum(int(shard["size_bytes"]) for shard in shard_reports),
        "shards": shard_reports,
    }
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"wrote {len(shard_reports)} shards for {len(entries)} lanes "
        f"({report['total_size_bytes']:,} bytes) in {elapsed:.1f}s",
        flush=True,
    )
    return report


def main() -> int:
    args = parse_args()
    split_shards(
        input_path=args.input,
        output_dir=args.output_dir,
        report_path=args.report,
        chunk_lanes=args.chunk_lanes,
        lane_from=args.lane_from,
        lane_to=args.lane_to,
        progress_every=args.progress_every,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
