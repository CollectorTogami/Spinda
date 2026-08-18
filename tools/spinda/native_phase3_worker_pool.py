#!/usr/bin/env python3
"""Launch Phase 3 Spinda lanes across isolated workers.

Production defaults to the standalone Phase 3 CLI so bulk
lanes avoid Qt startup and frontend overhead. The Qt autorun path remains
available through ``--runner qt`` for visual inspection.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections import deque
from dataclasses import dataclass
from itertools import islice
from pathlib import Path
from typing import Any, Sequence


ROOT = Path(__file__).absolute().parents[2]
DEFAULT_PHASE2_DIR = ROOT / "Phase2PickupStates"
DEFAULT_OUTPUT_DIR = ROOT / "Phase3SpindaBlocks"
DEFAULT_CACHE_DIR = DEFAULT_OUTPUT_DIR / "_cache"
POOL_STATUS_NAME = "_native_phase3_worker_pool_status.json"
POOL_CONTROL_NAME = "_native_phase3_worker_pool_control.json"
DEFAULT_BUNDLE_SIZE = 2
PENDING_STATUS_PREVIEW = 256
DONE_STATUS_PREVIEW = 256
SKIPPED_STATUS_PREVIEW = 256
WINDOWS_RUNTIME_PATHS = (
    Path("C:/msys64/mingw64/bin"),
    Path("C:/msys64/usr/bin"),
    Path("C:/devkitPro/msys2/usr/bin"),
)
MappingLike = dict[str, Any]


def is_windows_platform(os_name: str | None = None) -> bool:
    """Return true for Windows-specific executable/path defaults."""

    return (os_name or os.name) == "nt"


def platform_executable_name(stem: str, os_name: str | None = None) -> str:
    """Append `.exe` only for Windows helper paths."""

    if is_windows_platform(os_name) and not stem.lower().endswith(".exe"):
        return f"{stem}.exe"
    return stem


def first_existing_path(candidates: Sequence[Path], fallback: Path) -> Path:
    """Use helper-package paths when present, otherwise keep old workspace fallback."""

    for candidate in candidates:
        if candidate.exists():
            return candidate
    return fallback


def default_phase3_cli_exe(root: Path = ROOT, os_name: str | None = None) -> Path:
    """Return the native Phase 3 CLI path for the current platform.

    Helper nodes should pass `--phase3-cli-exe` explicitly after packaging, but
    platform-aware defaults keep dry-runs and local Linux source checkouts sane.
    """

    if is_windows_platform(os_name):
        return root / "build-mingw64-spinda-cli-lto" / platform_executable_name("mgba-spinda-phase3", os_name)
    return root / "build-linux-spinda-cli" / platform_executable_name("mgba-spinda-phase3", os_name)


def default_mgba_exe(root: Path = ROOT, os_name: str | None = None) -> Path:
    """Return Qt executable path for optional inspection mode."""

    if is_windows_platform(os_name):
        return root / "build-mingw64-python-qt" / platform_executable_name("mGBA", os_name)
    return root / "build-linux-qt" / platform_executable_name("mgba-qt", os_name)


def default_rom(root: Path = ROOT) -> Path:
    """Prefer portable helper-package input paths without breaking old local runs.

    The assisted package stores personal inputs under `inputs/`, while the
    original Windows workspace kept the ROM near the seed-bruteforce scripts.
    """

    return first_existing_path(
        (
            root / "inputs" / "lg.gba",
            root / "doc" / "python-examples" / "frlg-seed-bruteforce" / "lg.gba",
        ),
        root / "inputs" / "lg.gba",
    )


def default_secondhalf_csv(root: Path = ROOT) -> Path:
    """Prefer portable helper-package CSV paths, then existing local build output."""

    return first_existing_path(
        (
            root / "inputs" / "secondhalf.csv",
            root / "build-data" / "secondhalf.csv",
            root / "build-mingw64-python-qt" / "secondhalf.csv",
            root / "secondhalf.csv",
        ),
        root / "inputs" / "secondhalf.csv",
    )


DEFAULT_PHASE3_CLI_EXE = default_phase3_cli_exe()
DEFAULT_MGBA_EXE = default_mgba_exe()
DEFAULT_ROM = default_rom()
DEFAULT_SECONDHALF_CSV = default_secondhalf_csv()


@dataclass(frozen=True)
class NativePhase3Job:
    lane_id: int
    lane_ids: list[int]
    lane_hex: str
    command: list[str]
    env: dict[str, str]
    phase2_state: Path
    phase2_states: list[Path]
    cache_dir: Path
    output_zip: Path
    output_zips: list[Path]
    status_path: Path
    status_paths: list[Path]
    worker_name: str
    keep_open: bool
    runner: str


@dataclass
class RunningJob:
    job: NativePhase3Job
    process: subprocess.Popen[Any]
    launched_at: float
    slot_id: int


def format_lane(value: int) -> str:
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"lane must fit in 16 bits: {value!r}")
    return f"0x{value:04X}"


def parse_int(raw: str) -> int:
    return int(raw.strip(), 0)


def parse_lane_token(token: str) -> list[int]:
    token = token.strip()
    if not token:
        return []
    for separator in ("..", "-"):
        if separator in token:
            start_raw, end_raw = token.split(separator, 1)
            start = parse_int(start_raw)
            end = parse_int(end_raw)
            if end < start:
                raise ValueError(f"lane range ends before it starts: {token}")
            return list(range(start, end + 1))
    return [parse_int(token)]


def parse_lanes(values: Sequence[str]) -> list[int]:
    lanes: list[int] = []
    seen: set[int] = set()
    for value in values:
        for part in value.split(","):
            for lane in parse_lane_token(part):
                if not 0 <= lane <= 0xFFFF:
                    raise ValueError(f"lane must fit in 16 bits: {part}")
                if lane not in seen:
                    seen.add(lane)
                    lanes.append(lane)
    return lanes


def write_json_atomic(path: Path, payload: MappingLike) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def prepend_existing_path_entries(env: dict[str, str], entries: Sequence[Path]) -> None:
    """Prepend runtime DLL search paths without adding dead directories."""
    existing = env.get("PATH", "")
    current = [item for item in existing.split(os.pathsep) if item]
    prepend = [str(path) for path in entries if path.is_dir()]
    if prepend:
        env["PATH"] = os.pathsep.join(prepend + current)


def cli_runtime_path_entries(phase3_cli_exe: Path, os_name: str | None = None) -> list[Path]:
    """Return PATH entries needed when launching the standalone CLI.

    Linux helper nodes normally use system shared libraries, so only the CLI
    directory is prepended. Windows keeps the MSYS runtime search fallback.
    """

    entries = [Path(phase3_cli_exe).absolute().parent]
    if is_windows_platform(os_name):
        entries.extend(WINDOWS_RUNTIME_PATHS)
    return entries


def load_status(path: Path) -> MappingLike | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None


def is_complete_phase3_zip(path: Path, lane_id: int, *, expected_count: int = 65536) -> bool:
    """Return true only for the final production ZIP shape for one lane.

    This is intentionally kept out of the production startup path. Opening a
    65,536-entry ZIP per completed lane is post-run auditor work, not hot queue
    setup work.
    """
    import zipfile

    if not path.is_file():
        return False
    expected_suffix = f"{lane_id:04X}.PK3"
    try:
        with zipfile.ZipFile(path, "r") as archive:
            infos = archive.infolist()
            if len(infos) != expected_count:
                return False
            seen: set[str] = set()
            for info in infos:
                name = info.filename
                if name in seen:
                    return False
                seen.add(name)
                if not (name.startswith("0x") and name.endswith(".pk3")):
                    return False
                if len(name) != len("0x00000000.pk3"):
                    return False
                if not name[2:10].isalnum():
                    return False
                try:
                    pid = int(name[2:10], 16)
                except ValueError:
                    return False
                if (pid & 0xFFFF) != lane_id:
                    return False
                if not name.upper().endswith(expected_suffix):
                    return False
                if info.file_size != 80:
                    return False
        return True
    except (OSError, zipfile.BadZipFile):
        return False


def is_named_phase3_zip(path: Path, lane_id: int) -> bool:
    """Fast resume check: only require the correctly named final ZIP to exist."""
    try:
        return path.is_file() and path.name == f"{format_lane(lane_id)}.spinda80.zip" and path.stat().st_size > 0
    except OSError:
        return False


def split_existing_complete_lanes(
    lanes: Sequence[int],
    *,
    output_dir: Path,
    expected_count: int = 65536,
    by_name_only: bool = False,
) -> tuple[list[int], list[int]]:
    """Split pending/skipped lanes.

    Production launchers pass ``by_name_only=True`` so resume checks do not open
    ZIP central directories before workers start.
    """
    pending: list[int] = []
    skipped: list[int] = []
    for lane_id in lanes:
        output_zip = output_dir / f"{format_lane(lane_id)}.spinda80.zip"
        if by_name_only and is_named_phase3_zip(output_zip, lane_id):
            skipped.append(lane_id)
        elif not by_name_only and is_complete_phase3_zip(output_zip, lane_id, expected_count=expected_count):
            skipped.append(lane_id)
        else:
            pending.append(lane_id)
    return pending, skipped


def format_lane_bundle(lane_ids: Sequence[int]) -> str:
    if len(lane_ids) == 1:
        return format_lane(lane_ids[0])
    if all(lane_ids[index + 1] == lane_ids[index] + 1 for index in range(len(lane_ids) - 1)):
        return f"{format_lane(lane_ids[0])}..{format_lane(lane_ids[-1])}"
    return ",".join(format_lane(lane_id) for lane_id in lane_ids)


def build_job(args: argparse.Namespace, lane_id: int, lane_ids: Sequence[int] | None = None) -> NativePhase3Job:
    bundle_lane_ids = list(lane_ids or [lane_id])
    lane_hex = format_lane_bundle(bundle_lane_ids)
    primary_lane_hex = format_lane(bundle_lane_ids[0])
    phase2_state = Path(args.phase2_dir) / f"{primary_lane_hex}.ss0"
    phase2_states = [Path(args.phase2_dir) / f"{format_lane(bundle_lane_id)}.ss0" for bundle_lane_id in bundle_lane_ids]
    output_dir = Path(args.output_dir)
    cache_dir = Path(args.cache_dir)
    output_zip = output_dir / f"{primary_lane_hex}.spinda80.zip"
    output_zips = [output_dir / f"{format_lane(bundle_lane_id)}.spinda80.zip" for bundle_lane_id in bundle_lane_ids]
    status_path = output_dir / f"_{primary_lane_hex}.phase3_status.json"
    status_paths = [output_dir / f"_{format_lane(bundle_lane_id)}.phase3_status.json" for bundle_lane_id in bundle_lane_ids]
    worker_name = f"spinda-phase3-{primary_lane_hex.lower()}"

    for bundle_lane_id in bundle_lane_ids:
        bundle_phase2_state = Path(args.phase2_dir) / f"{format_lane(bundle_lane_id)}.ss0"
        bundle_output_zip = output_dir / f"{format_lane(bundle_lane_id)}.spinda80.zip"
        if not args.allow_missing_inputs and not bundle_phase2_state.is_file():
            raise FileNotFoundError(f"missing Phase 2 pickup state: {bundle_phase2_state}")
        if not args.overwrite and bundle_output_zip.exists():
            raise FileExistsError(f"output ZIP exists; pass --overwrite to rerun: {bundle_output_zip}")

    env = os.environ.copy()
    env["MGBA_WORKER_INSTANCE"] = worker_name

    if args.runner == "cli":
        prepend_existing_path_entries(env, cli_runtime_path_entries(args.phase3_cli_exe))
        command = [
            str(args.phase3_cli_exe),
            "--rom",
            str(args.rom),
            "--secondhalf-csv",
            str(args.secondhalf_csv),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--min-pickup-detect-frame",
            str(args.min_pickup_detect_frame),
            "--fast-pickup-check-first-frame",
            str(args.fast_pickup_check_first_frame),
            "--fast-pickup-check-second-frame",
            str(args.fast_pickup_check_second_frame),
        ]
        if len(bundle_lane_ids) == 1:
            command.extend(["--lane", primary_lane_hex, "--phase2-state", str(phase2_state)])
        else:
            command.extend(
                [
                    "--lanes",
                    ",".join(format_lane(bundle_lane_id) for bundle_lane_id in bundle_lane_ids),
                    "--phase2-dir",
                    str(args.phase2_dir),
                ]
            )
        if args.limit is not None:
            command.extend(["--limit", str(args.limit)])
        if args.max_pickup_detect_frames is not None:
            command.extend(["--max-pickup-detect-frames", str(args.max_pickup_detect_frames)])
        if not args.fast_pickup_checks:
            command.append("--no-fast-pickup-checks")
        if args.learn_pickup_delay:
            command.extend(["--learn-pickup-delay-samples", str(args.learn_pickup_delay_samples)])
        else:
            command.append("--no-learn-pickup-delay")
        if args.zip_method == "store":
            command.append("--zip-store")
        if args.overwrite:
            command.append("--overwrite")
    else:
        command = [str(args.mgba_exe), str(args.rom)]
        env.update(
            {
                "MGBA_SPINDA_NATIVE_PHASE3_AUTORUN": "1",
                "MGBA_SPINDA_NATIVE_PHASE3_SUPPRESS_DIALOGS": "1",
                "MGBA_SPINDA_NATIVE_PHASE3_EXIT_ON_COMPLETE": "0" if args.keep_open else "1",
                "MGBA_SPINDA_NATIVE_PHASE3_LANE_ID": primary_lane_hex,
                "MGBA_SPINDA_NATIVE_PHASE3_SECONDHALF_CSV": str(args.secondhalf_csv),
                "MGBA_SPINDA_NATIVE_PHASE3_OUTPUT_DIR": str(output_dir),
                "MGBA_SPINDA_NATIVE_PHASE3_CACHE_DIR": str(cache_dir),
                "MGBA_SPINDA_NATIVE_PHASE3_OVERWRITE": "1" if args.overwrite else "0",
                "MGBA_SPINDA_NATIVE_PHASE3_ENABLE_AUDIO_KILLSWITCH": "1",
                "MGBA_SPINDA_NATIVE_PHASE3_ENABLE_NO_RENDER": "1",
                "MGBA_SPINDA_NATIVE_PHASE3_ENABLE_FAST_FORWARD": "1",
                "MGBA_SPINDA_NATIVE_PHASE3_MIN_PICKUP_DETECT_FRAME": str(args.min_pickup_detect_frame),
                "MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECKS": "1" if args.fast_pickup_checks else "0",
                "MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECK_FIRST_FRAME": str(args.fast_pickup_check_first_frame),
                "MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECK_SECOND_FRAME": str(args.fast_pickup_check_second_frame),
                "MGBA_SPINDA_NATIVE_PHASE3_LEARN_PICKUP_DELAY": "1" if args.learn_pickup_delay else "0",
                "MGBA_SPINDA_NATIVE_PHASE3_LEARN_PICKUP_DELAY_SAMPLES": str(args.learn_pickup_delay_samples),
            }
        )
        if len(bundle_lane_ids) == 1:
            env["MGBA_SPINDA_NATIVE_PHASE3_PHASE2_STATE"] = str(Path(args.phase2_dir) / f"{primary_lane_hex}.ss0")
        else:
            env["MGBA_SPINDA_NATIVE_PHASE3_LANE_IDS"] = ",".join(format_lane(lane) for lane in bundle_lane_ids)
        if args.limit is not None:
            env["MGBA_SPINDA_NATIVE_PHASE3_LIMIT"] = str(args.limit)
        if args.max_pickup_detect_frames is not None:
            env["MGBA_SPINDA_NATIVE_PHASE3_MAX_PICKUP_DETECT_FRAMES"] = str(args.max_pickup_detect_frames)
        if args.headless:
            env["MGBA_SPINDA_NATIVE_PHASE3_HEADLESS"] = "1"

    return NativePhase3Job(
        lane_id=bundle_lane_ids[0],
        lane_ids=bundle_lane_ids,
        lane_hex=lane_hex,
        command=command,
        env=env,
        phase2_state=phase2_state,
        phase2_states=phase2_states,
        cache_dir=cache_dir,
        output_zip=output_zip,
        output_zips=output_zips,
        status_path=status_path,
        status_paths=status_paths,
        worker_name=worker_name,
        keep_open=bool(args.keep_open),
        runner=args.runner,
    )


def summarize_job(
    job: NativePhase3Job,
    process: subprocess.Popen[Any] | None = None,
    *,
    slot_id: int | None = None,
    launched_at: float | None = None,
) -> MappingLike:
    """Summarize one worker job, aggregating all lanes in a bundle.

    Bundle summaries must read every per-lane status file. Otherwise a process
    that completes lane A and fails lane B can look complete if lane A is the
    primary lane.
    """

    lane_statuses = []
    complete_count = 0
    failed_count = 0
    generated_records = 0
    selected_targets = 0
    elapsed_seconds = 0.0
    have_elapsed = False
    for lane_id, status_path, output_zip in zip(job.lane_ids, job.status_paths, job.output_zips):
        lane_status = load_status(status_path) or {}
        lane_state = lane_status.get("status", "pending")
        if lane_state == "complete" and output_zip.exists():
            complete_count += 1
        elif lane_state == "failed":
            failed_count += 1
        generated_records += int(lane_status.get("generated_records") or 0)
        selected_targets += int(lane_status.get("selected_targets") or 0)
        if lane_status.get("elapsed_seconds") is not None:
            elapsed_seconds += float(lane_status.get("elapsed_seconds") or 0.0)
            have_elapsed = True
        lane_statuses.append(
            {
                "lane_id": format_lane(lane_id),
                "status": lane_state,
                "generated_records": lane_status.get("generated_records", 0),
                "selected_targets": lane_status.get("selected_targets"),
                "elapsed_seconds": lane_status.get("elapsed_seconds"),
                "output_zip": str(output_zip),
                "status_path": str(status_path),
                "zip_exists": output_zip.exists(),
            }
        )
    if complete_count == len(job.lane_ids):
        aggregate_status = "complete"
    elif failed_count:
        aggregate_status = "failed"
    else:
        aggregate_status = "pending"
    return {
        "lane_id": job.lane_hex,
        "lane_ids": [format_lane(lane_id) for lane_id in job.lane_ids],
        "runner": job.runner,
        "worker_name": job.worker_name,
        "slot_id": slot_id,
        "pid": process.pid if process else None,
        "returncode": process.poll() if process else None,
        "status": aggregate_status,
        "generated_records": generated_records,
        "selected_targets": selected_targets if selected_targets else None,
        "elapsed_seconds": round(elapsed_seconds, 3) if have_elapsed else None,
        "phase2_state": str(job.phase2_state),
        "phase2_states": [str(path) for path in job.phase2_states],
        "cache_dir": str(job.cache_dir),
        "output_zip": str(job.output_zip),
        "output_zips": [str(path) for path in job.output_zips],
        "status_path": str(job.status_path),
        "status_paths": [str(path) for path in job.status_paths],
        "lane_statuses": lane_statuses,
        "zip_exists": job.output_zip.exists(),
        "complete_lanes": complete_count,
        "current_outer_elapsed_seconds": round(time.monotonic() - launched_at, 3) if launched_at is not None else None,
    }


def write_pool_status(
    path: Path,
    pending: Sequence[NativePhase3Job],
    running: Sequence[RunningJob],
    done: Sequence[MappingLike],
    skipped: Sequence[str] = (),
) -> None:
    done_preview, done_omitted = done_status(done)
    skipped_preview, skipped_omitted = skipped_status(skipped)
    payload = {
        "time_unix": int(time.time()),
        "pending": [job.lane_hex for job in pending],
        "running": [summarize_job(item.job, item.process, slot_id=item.slot_id, launched_at=item.launched_at) for item in running],
        "done": done_preview,
        "done_omitted": done_omitted,
        "skipped_existing_complete": skipped_preview,
        "skipped_existing_complete_omitted": skipped_omitted,
        "counts": {
            "pending": len(pending),
            "running": len(running),
            "done": len(done),
            "skipped_existing_complete": len(skipped),
        },
    }
    write_json_atomic(path, payload)


def pending_lane_status(lanes: Sequence[int] | deque[int]) -> tuple[list[str], int]:
    preview = [format_lane(lane) for lane in islice(lanes, PENDING_STATUS_PREVIEW)]
    return preview, max(0, len(lanes) - len(preview))


def done_status(done: Sequence[MappingLike]) -> tuple[list[MappingLike], int]:
    """Return recent completed jobs only, keeping total counts authoritative."""
    if len(done) <= DONE_STATUS_PREVIEW:
        return list(done), 0
    return list(done[-DONE_STATUS_PREVIEW:]), len(done) - DONE_STATUS_PREVIEW


def skipped_status(skipped: Sequence[str]) -> tuple[list[str], int]:
    """Preview skipped lanes without rewriting a huge resume list every poll."""
    preview = list(skipped[:SKIPPED_STATUS_PREVIEW])
    return preview, max(0, len(skipped) - len(preview))


def done_summary_counts(done: Sequence[MappingLike]) -> MappingLike:
    """Return exact small counters for completed worker jobs.

    The visible `done` list is preview-limited in pool status JSON. These
    counters stay exact and cheap so external dashboards can show boot-session
    totals without expanding or rewriting the full historical list.
    """

    completed_lanes = 0
    failed_jobs = 0
    generated_records = 0
    for item in done:
        status = item.get("status")
        if status == "complete":
            completed_lanes += int(item.get("complete_lanes") or len(item.get("lane_ids", ())) or 1)
        else:
            failed_jobs += 1
        generated_records += int(item.get("generated_records") or 0)
    return {
        "completed_lanes": completed_lanes,
        "failed_jobs": failed_jobs,
        "generated_records": generated_records,
    }


def pending_bundle_lane_status(pending_bundles: deque[list[int]], pending_lane_count: int) -> tuple[list[str], int]:
    """Preview pending lane bundles without expanding the whole remaining queue."""
    preview: list[str] = []
    for bundle in pending_bundles:
        for lane in bundle:
            if len(preview) >= PENDING_STATUS_PREVIEW:
                return preview, max(0, pending_lane_count - len(preview))
            preview.append(format_lane(lane))
    return preview, max(0, pending_lane_count - len(preview))


def chunk_lanes(lanes: Sequence[int], bundle_size: int) -> list[list[int]]:
    if bundle_size < 1:
        raise ValueError("bundle size must be positive")
    return [list(lanes[index:index + bundle_size]) for index in range(0, len(lanes), bundle_size)]


def write_lazy_pool_status(
    path: Path,
    pending_lanes: Sequence[int],
    running: Sequence[RunningJob],
    done: Sequence[MappingLike],
    skipped: Sequence[str] = (),
    desired_workers: int | None = None,
) -> None:
    pending_preview, pending_omitted = pending_lane_status(pending_lanes)
    done_preview, done_omitted = done_status(done)
    skipped_preview, skipped_omitted = skipped_status(skipped)
    done_counts = done_summary_counts(done)
    payload = {
        "time_unix": int(time.time()),
        "pending": pending_preview,
        "pending_omitted": pending_omitted,
        "running": [summarize_job(item.job, item.process, slot_id=item.slot_id, launched_at=item.launched_at) for item in running],
        "done": done_preview,
        "done_omitted": done_omitted,
        "skipped_existing_complete": skipped_preview,
        "skipped_existing_complete_omitted": skipped_omitted,
        "counts": {
            "pending": len(pending_lanes),
            "running": len(running),
            "done": len(done),
            "skipped_existing_complete": len(skipped),
            **done_counts,
            "desired_workers": desired_workers,
        },
        "status_note": "pending, done, and skipped lists are preview-limited to avoid rewriting huge status JSON",
    }
    write_json_atomic(path, payload)


def write_lazy_bundle_pool_status(
    path: Path,
    pending_bundles: deque[list[int]],
    pending_lane_count: int,
    running: Sequence[RunningJob],
    done: Sequence[MappingLike],
    skipped: Sequence[str] = (),
    desired_workers: int | None = None,
) -> None:
    pending_preview, pending_omitted = pending_bundle_lane_status(pending_bundles, pending_lane_count)
    done_preview, done_omitted = done_status(done)
    skipped_preview, skipped_omitted = skipped_status(skipped)
    done_counts = done_summary_counts(done)
    payload = {
        "time_unix": int(time.time()),
        "pending": pending_preview,
        "pending_omitted": pending_omitted,
        "running": [summarize_job(item.job, item.process, slot_id=item.slot_id, launched_at=item.launched_at) for item in running],
        "done": done_preview,
        "done_omitted": done_omitted,
        "skipped_existing_complete": skipped_preview,
        "skipped_existing_complete_omitted": skipped_omitted,
        "counts": {
            "pending": pending_lane_count,
            "running": len(running),
            "done": len(done),
            "skipped_existing_complete": len(skipped),
            **done_counts,
            "desired_workers": desired_workers,
        },
        "status_note": "pending, done, and skipped lists are preview-limited to avoid rewriting huge status JSON",
    }
    write_json_atomic(path, payload)


def launch_job(job: NativePhase3Job, *, slot_id: int) -> RunningJob:
    process = subprocess.Popen(
        job.command,
        cwd=str(Path(job.command[0]).absolute().parent),
        env=job.env,
    )
    return RunningJob(job=job, process=process, launched_at=time.monotonic(), slot_id=slot_id)


def job_terminal_status(job: NativePhase3Job) -> bool:
    statuses = [load_status(path) for path in job.status_paths]
    return all(status and status.get("status") in {"complete", "failed"} for status in statuses)


def job_outputs_complete(job: NativePhase3Job) -> bool:
    statuses = [load_status(path) for path in job.status_paths]
    return all(
        status
        and status.get("status") == "complete"
        and output_zip.exists()
        for status, output_zip in zip(statuses, job.output_zips)
    )


def run_pool(
    jobs: list[NativePhase3Job],
    *,
    workers: int,
    poll_seconds: float,
    pool_status_path: Path,
    skipped_existing_complete: Sequence[str] = (),
) -> int:
    pending = list(jobs)
    running: list[RunningJob] = []
    done: list[MappingLike] = []
    failed = False

    while pending or running:
        while pending and len(running) < workers:
            active_slots = {item.slot_id for item in running}
            slot_id = next(slot for slot in range(1, workers + 1) if slot not in active_slots)
            running.append(launch_job(pending.pop(0), slot_id=slot_id))

        next_running: list[RunningJob] = []
        for item in running:
            process_done = item.process.poll() is not None
            terminal_status = job_terminal_status(item.job)
            if terminal_status or process_done:
                if terminal_status and item.process.poll() is None and not item.job.keep_open:
                    try:
                        item.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        item.process.terminate()
                summary = summarize_job(item.job, item.process, slot_id=item.slot_id, launched_at=item.launched_at)
                summary["outer_elapsed_seconds"] = round(time.monotonic() - item.launched_at, 3)
                if not job_outputs_complete(item.job) or item.process.returncode not in (0, None):
                    failed = True
                done.append(summary)
            else:
                next_running.append(item)
        running = next_running
        write_pool_status(pool_status_path, pending, running, done, skipped_existing_complete)
        if pending or running:
            time.sleep(poll_seconds)

    write_pool_status(pool_status_path, pending, running, done, skipped_existing_complete)
    return 1 if failed else 0


def build_failure_summary(args: argparse.Namespace, lane_id: int, error: Exception) -> MappingLike:
    lane_hex = format_lane(lane_id)
    output_dir = Path(args.output_dir)
    phase2_state = Path(args.phase2_dir) / f"{lane_hex}.ss0"
    output_zip = output_dir / f"{lane_hex}.spinda80.zip"
    status_path = output_dir / f"_{lane_hex}.phase3_status.json"
    return {
        "lane_id": lane_hex,
        "runner": args.runner,
        "worker_name": f"spinda-phase3-{lane_hex.lower()}",
        "pid": None,
        "returncode": None,
        "status": "failed",
        "error": str(error),
        "generated_records": 0,
        "selected_targets": None,
        "elapsed_seconds": None,
        "phase2_state": str(phase2_state),
        "cache_dir": str(args.cache_dir),
        "output_zip": str(output_zip),
        "status_path": str(status_path),
        "zip_exists": output_zip.exists(),
    }


def read_pool_control(path: Path | None, *, default_workers: int, max_workers: int) -> MappingLike:
    """Read optional live worker-control JSON.

    Missing or malformed control leaves the current worker target unchanged.
    `shutdown=true` requests all running workers stop and the pool exit.
    """

    if path is None or not path.is_file():
        return {"desired_workers": default_workers, "shutdown": False}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"desired_workers": default_workers, "shutdown": False}
    raw_workers = payload.get("desired_workers", default_workers)
    try:
        desired_workers = int(raw_workers)
    except (TypeError, ValueError):
        desired_workers = default_workers
    desired_workers = max(0, min(max_workers, desired_workers))
    return {
        "desired_workers": desired_workers,
        "shutdown": bool(payload.get("shutdown", False)),
        "updated_at_unix": payload.get("updated_at_unix"),
        "source": payload.get("source"),
    }


def terminate_worker(item: RunningJob, *, timeout_seconds: float = 5.0) -> None:
    """Terminate one worker process for control-plane scale-down/shutdown."""

    if item.process.poll() is not None:
        return
    item.process.terminate()
    try:
        item.process.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        item.process.kill()
        item.process.wait(timeout=timeout_seconds)


def scale_down_running_workers(
    running: list[RunningJob],
    *,
    desired_workers: int,
    pending_bundles: deque[list[int]],
) -> int:
    """Stop extra workers and requeue their lanes at the front.

    This makes a command-center worker-count reduction affect the host machine
    immediately without losing lane work. Killed lanes have no final ZIP and are
    retried later from their Phase 2 state.
    """

    if len(running) <= desired_workers:
        return 0
    stopped = 0
    candidates = sorted(running, key=lambda item: item.slot_id, reverse=True)
    for item in candidates:
        if len(running) <= desired_workers:
            break
        terminate_worker(item)
        pending_bundles.appendleft(list(item.job.lane_ids))
        running.remove(item)
        stopped += len(item.job.lane_ids)
    return stopped


def run_pool_lanes(
    args: argparse.Namespace,
    lane_ids: Sequence[int],
    *,
    workers: int,
    poll_seconds: float,
    pool_status_path: Path,
    skipped_existing_complete: Sequence[str] = (),
) -> int:
    pending_bundles = deque(chunk_lanes(lane_ids, args.bundle_size))
    pending_lane_count = len(lane_ids)
    running: list[RunningJob] = []
    done: list[MappingLike] = []
    failed = False
    control_file = Path(args.control_file) if args.control_file else None
    max_workers = max(workers, args.control_max_workers)
    desired_workers = workers
    status_write_seconds = max(0.0, float(getattr(args, "status_write_seconds", poll_seconds)))
    next_status_write = 0.0
    status_dirty = True

    while pending_bundles or running:
        control = read_pool_control(control_file, default_workers=desired_workers, max_workers=max_workers)
        next_desired_workers = int(control["desired_workers"])
        if next_desired_workers != desired_workers:
            status_dirty = True
        desired_workers = next_desired_workers
        if control.get("shutdown"):
            for item in list(running):
                terminate_worker(item)
            running.clear()
            pending_bundles.clear()
            pending_lane_count = 0
            break
        requeued = scale_down_running_workers(
            running,
            desired_workers=desired_workers,
            pending_bundles=pending_bundles,
        )
        pending_lane_count += requeued
        if requeued:
            status_dirty = True

        while pending_bundles and len(running) < desired_workers:
            active_slots = {item.slot_id for item in running}
            free_slots = [slot for slot in range(1, max_workers + 1) if slot not in active_slots and slot <= desired_workers]
            if not free_slots:
                break
            slot_id = free_slots[0]
            bundle = pending_bundles.popleft()
            pending_lane_count -= len(bundle)
            lane_id = bundle[0]
            try:
                running.append(launch_job(build_job(args, lane_id, bundle), slot_id=slot_id))
                status_dirty = True
            except Exception as error:
                failed = True
                for failed_lane_id in bundle:
                    done.append(build_failure_summary(args, failed_lane_id, error))
                status_dirty = True

        next_running: list[RunningJob] = []
        for item in running:
            process_done = item.process.poll() is not None
            terminal_status = job_terminal_status(item.job)
            if terminal_status or process_done:
                if terminal_status and item.process.poll() is None and not item.job.keep_open:
                    try:
                        item.process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        item.process.terminate()
                summary = summarize_job(item.job, item.process, slot_id=item.slot_id, launched_at=item.launched_at)
                summary["outer_elapsed_seconds"] = round(time.monotonic() - item.launched_at, 3)
                if not job_outputs_complete(item.job) or item.process.returncode not in (0, None):
                    failed = True
                done.append(summary)
                status_dirty = True
            else:
                next_running.append(item)
        running = next_running
        now = time.monotonic()
        if status_dirty or status_write_seconds <= 0.0 or now >= next_status_write:
            write_lazy_bundle_pool_status(
                pool_status_path,
                pending_bundles,
                pending_lane_count,
                running,
                done,
                skipped_existing_complete,
                desired_workers=desired_workers,
            )
            status_dirty = False
            next_status_write = now + status_write_seconds
        if pending_bundles or running:
            time.sleep(poll_seconds)

    write_lazy_bundle_pool_status(
        pool_status_path,
        pending_bundles,
        0,
        running,
        done,
        skipped_existing_complete,
        desired_workers=desired_workers,
    )
    return 1 if failed else 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Phase 3 Spinda lanes across isolated worker processes.")
    parser.add_argument("--lanes", nargs="+", required=True, help="Lane ids or ranges, e.g. 0x0002 0x0003-0x0005.")
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--runner", choices=("cli", "qt"), default="cli", help="Bulk production defaults to the standalone CLI. Use qt only for visual inspection.")
    parser.add_argument("--phase3-cli-exe", type=Path, default=DEFAULT_PHASE3_CLI_EXE, help="Standalone Phase 3 CLI executable used by --runner cli.")
    parser.add_argument("--mgba-exe", type=Path, default=DEFAULT_MGBA_EXE, help="Qt mGBA executable used by --runner qt.")
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--phase2-dir", type=Path, default=DEFAULT_PHASE2_DIR)
    parser.add_argument("--secondhalf-csv", type=Path, default=DEFAULT_SECONDHALF_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--cache-dir", type=Path, default=DEFAULT_CACHE_DIR, help="Shared native target/schedule cache directory.")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--max-pickup-detect-frames", type=int)
    parser.add_argument("--min-pickup-detect-frame", type=int, default=4)
    parser.add_argument("--fast-pickup-checks", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--fast-pickup-check-first-frame", type=int, default=4)
    parser.add_argument("--fast-pickup-check-second-frame", type=int, default=5)
    parser.add_argument("--learn-pickup-delay", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--learn-pickup-delay-samples", type=int, default=32)
    parser.add_argument("--zip-method", choices=("deflate", "store"), default="deflate")
    parser.add_argument("--bundle-size", type=int, default=DEFAULT_BUNDLE_SIZE, help="Run this many lanes sequentially inside one mGBA worker process.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--keep-open", action="store_true", help="Leave worker windows open after each native run finishes.")
    parser.add_argument("--headless", action="store_true", help="Run native autorun without showing the main window or Spinda dialog.")
    parser.add_argument("--allow-missing-inputs", action="store_true")
    parser.add_argument(
        "--skip-existing-complete",
        action="store_true",
        help="Deprecated alias for filename-only resume. Deep ZIP validation belongs in a post-run auditor, not production startup.",
    )
    parser.add_argument(
        "--skip-existing-by-name",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Fast resume mode: skip lanes when the correctly named final ZIP exists, without opening ZIP entries.",
    )
    parser.add_argument("--dry-run-preview", type=int, default=32, help="Only print this many jobs in dry-run output; use negative for all.")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--poll-seconds", type=float, default=2.0)
    parser.add_argument(
        "--status-write-seconds",
        type=float,
        default=10.0,
        help="Minimum seconds between routine pool-status JSON writes. Launch, completion, failure, and scale events still write immediately.",
    )
    parser.add_argument(
        "--control-file",
        type=Path,
        help="Optional JSON file used by the Flask command center to change desired workers or request shutdown.",
    )
    parser.add_argument(
        "--control-max-workers",
        type=int,
        default=64,
        help="Maximum live worker count accepted from --control-file.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be positive")
    if args.status_write_seconds < 0:
        parser.error("--status-write-seconds must be non-negative")
    if args.control_max_workers < args.workers:
        parser.error("--control-max-workers must be >= --workers")
    if args.bundle_size < 1:
        parser.error("--bundle-size must be positive")
    for attr in ("min_pickup_detect_frame", "fast_pickup_check_first_frame", "fast_pickup_check_second_frame"):
        if getattr(args, attr) < 0:
            parser.error(f"--{attr.replace('_', '-')} must be non-negative")
    if args.learn_pickup_delay_samples < 1:
        parser.error("--learn-pickup-delay-samples must be positive")
    if args.max_pickup_detect_frames is not None:
        if args.max_pickup_detect_frames < 1:
            parser.error("--max-pickup-detect-frames must be positive")
        if args.min_pickup_detect_frame > args.max_pickup_detect_frames:
            parser.error("--min-pickup-detect-frame must be <= --max-pickup-detect-frames")
        if args.fast_pickup_check_first_frame > args.max_pickup_detect_frames or args.fast_pickup_check_second_frame > args.max_pickup_detect_frames:
            parser.error("fast pickup check frames must be <= --max-pickup-detect-frames")
    if args.runner == "cli" and args.keep_open:
        parser.error("--keep-open requires --runner qt")
    if args.runner == "qt" and args.zip_method != "deflate":
        parser.error("--zip-method store is only implemented by --runner cli")
    if args.headless and args.keep_open:
        parser.error("--headless and --keep-open conflict; a kept-open headless worker has no window to inspect")

    try:
        lanes = parse_lanes(args.lanes)
    except ValueError as error:
        parser.error(str(error))
    skipped_existing_complete: list[int] = []
    if args.skip_existing_complete or args.skip_existing_by_name:
        lanes, skipped_existing_complete = split_existing_complete_lanes(
            lanes,
            output_dir=Path(args.output_dir),
            by_name_only=True,
        )
    skipped_hex = [format_lane(lane) for lane in skipped_existing_complete]
    if args.dry_run:
        bundles = chunk_lanes(lanes, args.bundle_size)
        preview_bundles = bundles if args.dry_run_preview < 0 else bundles[:args.dry_run_preview]
        jobs = [build_job(args, bundle[0], bundle) for bundle in preview_bundles]
        print(
            json.dumps(
                {
                    "jobs": [summarize_job(job) | {"command": job.command} for job in jobs],
                    "job_count": len(bundles),
                    "dry_run_preview": len(preview_bundles),
                    "pending_lane_count": len(lanes),
                    "skipped_existing_complete": skipped_hex,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    args.output_dir.mkdir(parents=True, exist_ok=True)
    pool_status_path = args.output_dir / POOL_STATUS_NAME
    if args.control_file is None:
        args.control_file = args.output_dir / POOL_CONTROL_NAME
    return run_pool_lanes(
        args,
        lanes,
        workers=args.workers,
        poll_seconds=args.poll_seconds,
        pool_status_path=pool_status_path,
        skipped_existing_complete=skipped_hex,
    )


if __name__ == "__main__":
    raise SystemExit(main())
