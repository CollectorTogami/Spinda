"""Read-only validator for Phase 2 pickup-state output.

Default mode only scans `<repo-root>\\Phase2PickupStates`. Optional sample
verification loads selected `.ss0` files in a separate host-side mGBA core; it
does not attach to or control a running Qt GUI instance.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable


# Preserve the operator-facing `<repo-root>` alias instead of resolving the
# workspace junction back to the vendored backing path.
ROOT = Path(__file__).absolute().parents[2]
SPINDA_SCRIPT_DIR = ROOT / "doc" / "python-examples" / "frlg-spinda"
DEFAULT_FOLDER = ROOT / "Phase2PickupStates"
DEFAULT_ROM_PATH = ROOT / "doc" / "python-examples" / "frlg-seed-bruteforce" / "lg.gba"
DEFAULT_STATUS_PATH = DEFAULT_FOLDER / "_phase2_pickup_status.json"
DEFAULT_TARGET_STATES = 0x10000
DEFAULT_STATE_SIZE = 397_312
DEFAULT_EXPECTED_RNG = 0x80323CC6
DEFAULT_DRIFT_WINDOW = 4096
DEFAULT_SAMPLE_LIMIT = 20
DEFAULT_TMP_STALE_SECONDS = 300.0
STATE_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.ss0$")
TMP_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.ss0\.tmp$")
HEX_HINT_RE = re.compile(r"0x([0-9A-Fa-f]{4})")
IGNORED_NAMES = {
    "_phase2_pickup_status.json",
    "_phase2_pickup_errors.jsonl",
    "_phase2_pickup_control.json",
}
GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073
GBA_LCRNG_MULTIPLIER_INVERSE = 0xEEB9EB65
GRNG_VALUE_ADDR = 0x03005000
GSAVEBLOCK1_PTR_ADDR = 0x03005008
GPLAYER_PARTY_COUNT_ADDR = 0x02024029
GPLAYER_PARTY_ADDR = 0x02024284
PARTY_SLOT_SIZE = 100
DAYCARE_OFFSET = 0x2F80
DAYCARE_OFFSPRING_PERSONALITY_OFFSET = 0x118
DAYCARE_STEP_COUNTER_OFFSET = 0x11A
DAYCARE_MON2_STEPS_OFFSET = 0x114
KEYINPUT_ADDR = 0x04000130
GTASKS_ADDR = 0x03005090
TASK_SIZE = 0x28
TASK_COUNT = 16
EXPECTED_DIALOG_TASKS = (
    (0, 0x08079DE1),
    (1, 0x0806E811),
    (2, 0x0806E83D),
)
EXPECTED_DIALOG_TASK2_DATA0 = 0
EXPECTED_DIALOG_TASK2_DATA1 = 3
EXPECTED_PRE_PICKUP_PARTY_COUNT = 1
EXPECTED_NEUTRAL_KEYINPUT = 0x03FF


@dataclass(frozen=True)
class Phase2FolderReport:
    """Serializable folder health summary."""

    folder: str
    target_states: int
    expected_state_size: int
    complete_states: int = 0
    missing_states: int = 0
    final_state_files: int = 0
    bad_size_states: int = 0
    tmp_files: int = 0
    stale_tmp_files: int = 0
    bad_names: int = 0
    duplicate_weird_files: int = 0
    ignored_metadata_files: int = 0
    ignored_directories: int = 0
    samples: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Phase2FolderScan:
    """Folder report plus in-memory valid state map for sample selection."""

    report: Phase2FolderReport
    valid_state_paths: dict[int, Path] = field(default_factory=dict)


@dataclass(frozen=True)
class SampleVerification:
    """One optional host-side savestate RNG check."""

    target: str
    state_path: str
    expected_rng: str
    observed_rng: str | None
    drift: int | None
    status: str
    error: str | None = None


@dataclass(frozen=True)
class RuntimeStateObservation:
    """One loaded state observation used by the full runtime audit."""

    target: str
    state_path: str
    frame_counter: int
    rng: str
    drift: int | None
    daycare_lower: str | None
    daycare_step_counter: int | None
    mon2_low_byte: str | None
    stock_egg_waiting: bool | None
    party_count: int | None
    keyinput: str | None
    dialog_task_signature: str | None
    status: str = "ok"
    error: str | None = None


@dataclass(frozen=True)
class RuntimeAuditReport:
    """Summary of loading all Phase 2 states through a host-side mGBA core."""

    checked_states: int
    load_failed: int = 0
    rng_mismatches: int = 0
    daycare_lower_mismatches: int = 0
    stock_zero_lower_states: int = 0
    party_count_mismatches: int = 0
    keyinput_mismatches: int = 0
    dialog_task_mismatches: int = 0
    drift_counts: dict[str, int] = field(default_factory=dict)
    frame_counter_counts: dict[str, int] = field(default_factory=dict)
    dialog_task_signature_counts: dict[str, int] = field(default_factory=dict)
    samples: dict[str, list[str]] = field(default_factory=dict)


def _add_sample(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    """Append one bounded sample."""

    bucket = samples.setdefault(key, [])
    if len(bucket) < limit:
        bucket.append(value)


def _format_u16(value: int) -> str:
    return f"0x{value & 0xFFFF:04X}"


def _format_u32(value: int) -> str:
    return f"0x{value & 0xFFFFFFFF:08X}"


def lcrng_next_state(state: int) -> int:
    """Return one forward GBA LCRNG step."""

    return (GBA_LCRNG_MULTIPLIER * (state & 0xFFFFFFFF) + GBA_LCRNG_INCREMENT) & 0xFFFFFFFF


def lcrng_previous_state(state: int) -> int:
    """Return one backward GBA LCRNG step."""

    return (
        GBA_LCRNG_MULTIPLIER_INVERSE
        * (((state & 0xFFFFFFFF) - GBA_LCRNG_INCREMENT) & 0xFFFFFFFF)
    ) & 0xFFFFFFFF


def signed_lcrng_distance(expected: int, observed: int, max_steps: int) -> int | None:
    """Return signed LCRNG drift of observed relative to expected."""

    expected &= 0xFFFFFFFF
    observed &= 0xFFFFFFFF
    if expected == observed:
        return 0

    state = expected
    for steps in range(1, max_steps + 1):
        state = lcrng_next_state(state)
        if state == observed:
            return steps

    state = expected
    for steps in range(1, max_steps + 1):
        state = lcrng_previous_state(state)
        if state == observed:
            return -steps
    return None


def _read_status_expected_rng(status_path: Path) -> int | None:
    """Read expected baseline RNG from builder status JSON when present."""

    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    value = payload.get("expected_rng_at_baseline")
    if value is None:
        return None
    try:
        return int(str(value), 0) & 0xFFFFFFFF
    except ValueError:
        return None


def _scan_duplicate_weird_name(name: str) -> int | None:
    """Return target if a noncanonical file looks like an extra state variant."""

    if ".ss0" not in name.lower():
        return None
    match = HEX_HINT_RE.search(name)
    if not match:
        return None
    return int(match.group(1), 16)


def scan_phase2_folder(
    folder: Path = DEFAULT_FOLDER,
    *,
    target_states: int = DEFAULT_TARGET_STATES,
    expected_state_size: int = DEFAULT_STATE_SIZE,
    tmp_stale_seconds: float = DEFAULT_TMP_STALE_SECONDS,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> Phase2FolderScan:
    """Scan Phase 2 pickup states without reading file contents."""

    folder = folder.expanduser().absolute()
    samples: dict[str, list[str]] = {}
    valid_state_paths: dict[int, Path] = {}
    final_state_files = 0
    bad_size_states = 0
    tmp_files = 0
    stale_tmp_files = 0
    bad_names = 0
    duplicate_weird_files = 0
    ignored_metadata_files = 0
    ignored_directories = 0
    now = time.time()

    if not folder.is_dir():
        report = Phase2FolderReport(
            folder=str(folder),
            target_states=target_states,
            expected_state_size=expected_state_size,
            missing_states=target_states,
            samples={"missing_folder": [str(folder)]},
        )
        return Phase2FolderScan(report=report, valid_state_paths=valid_state_paths)

    for path in folder.iterdir():
        try:
            is_dir = path.is_dir()
        except OSError as exc:
            bad_names += 1
            _add_sample(samples, "read_errors", f"{path.name}: {exc}", sample_limit)
            continue

        if is_dir:
            ignored_directories += 1
            _add_sample(samples, "directories", path.name, sample_limit)
            continue

        state_match = STATE_RE.match(path.name)
        if state_match:
            final_state_files += 1
            target = int(state_match.group(1), 16)
            try:
                size = path.stat().st_size
            except OSError as exc:
                bad_size_states += 1
                _add_sample(samples, "read_errors", f"{path.name}: {exc}", sample_limit)
                continue
            if size == expected_state_size:
                valid_state_paths[target] = path
            else:
                bad_size_states += 1
                _add_sample(
                    samples,
                    "bad_size_states",
                    f"{path.name} size={size} expected={expected_state_size}",
                    sample_limit,
                )
            continue

        tmp_match = TMP_RE.match(path.name)
        if tmp_match:
            tmp_files += 1
            try:
                age = now - path.stat().st_mtime
            except OSError as exc:
                _add_sample(samples, "read_errors", f"{path.name}: {exc}", sample_limit)
                continue
            if age >= tmp_stale_seconds:
                stale_tmp_files += 1
                _add_sample(samples, "stale_tmp_files", path.name, sample_limit)
            else:
                _add_sample(samples, "tmp_files", path.name, sample_limit)
            continue

        if path.name in IGNORED_NAMES:
            ignored_metadata_files += 1
            continue

        bad_names += 1
        _add_sample(samples, "bad_names", path.name, sample_limit)
        duplicate_target = _scan_duplicate_weird_name(path.name)
        if duplicate_target is not None:
            duplicate_weird_files += 1
            _add_sample(
                samples,
                "duplicate_weird_files",
                f"{path.name} target={_format_u16(duplicate_target)}",
                sample_limit,
            )

    complete_states = len(valid_state_paths)
    report = Phase2FolderReport(
        folder=str(folder),
        target_states=target_states,
        expected_state_size=expected_state_size,
        complete_states=complete_states,
        missing_states=max(0, target_states - complete_states),
        final_state_files=final_state_files,
        bad_size_states=bad_size_states,
        tmp_files=tmp_files,
        stale_tmp_files=stale_tmp_files,
        bad_names=bad_names,
        duplicate_weird_files=duplicate_weird_files,
        ignored_metadata_files=ignored_metadata_files,
        ignored_directories=ignored_directories,
        samples=samples,
    )
    _add_missing_samples(report, valid_state_paths, sample_limit)
    return Phase2FolderScan(report=report, valid_state_paths=valid_state_paths)


def _add_missing_samples(
    report: Phase2FolderReport,
    valid_state_paths: dict[int, Path],
    sample_limit: int,
) -> None:
    """Add first missing target samples after scan."""

    if report.missing_states <= 0:
        return
    for target in range(report.target_states):
        if target not in valid_state_paths:
            _add_sample(report.samples, "missing_states", _format_u16(target), sample_limit)
            if len(report.samples.get("missing_states", [])) >= sample_limit:
                break


def parse_targets(text: str | None) -> list[int]:
    """Parse comma-separated `0x####` target list."""

    if not text:
        return []
    targets: list[int] = []
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        value = int(item, 0)
        if not 0 <= value <= 0xFFFF:
            raise ValueError(f"sample target out of range: {item}")
        targets.append(value)
    return targets


def select_sample_state_paths(
    valid_state_paths: dict[int, Path],
    *,
    sample_count: int,
    sample_targets: Iterable[int] = (),
) -> list[tuple[int, Path]]:
    """Select explicit targets first, then first valid states in hex order."""

    selected: list[tuple[int, Path]] = []
    seen: set[int] = set()
    for target in sample_targets:
        if target in valid_state_paths and target not in seen:
            selected.append((target, valid_state_paths[target]))
            seen.add(target)
    if sample_count <= 0:
        return selected
    for target in sorted(valid_state_paths):
        if target in seen:
            continue
        selected.append((target, valid_state_paths[target]))
        seen.add(target)
        if len(selected) >= sample_count:
            break
    return selected


def _load_runtime_readers():
    """Import mGBA helpers lazily for optional sample verification."""

    if str(SPINDA_SCRIPT_DIR) not in sys.path:
        sys.path.insert(0, str(SPINDA_SCRIPT_DIR))
    from spinda_frlg_common import load_gba_core, load_state_file, read_rng_state

    return load_gba_core, load_state_file, read_rng_state


RngReader = Callable[[Path], int]


def _ensure_core_memory_ready(core: object) -> None:
    """Start host-side cores once so their memory reader exists before state load."""

    if getattr(core, "memory", None) is not None:
        return
    reset = getattr(core, "reset", None)
    if callable(reset):
        reset()


def verify_sample_states(
    samples: Iterable[tuple[int, Path]],
    *,
    rom_path: Path = DEFAULT_ROM_PATH,
    expected_rng: int = DEFAULT_EXPECTED_RNG,
    drift_window: int = DEFAULT_DRIFT_WINDOW,
    rng_reader: RngReader | None = None,
) -> list[SampleVerification]:
    """Load sample states and compare `gRngValue` against expected LCRNG orbit."""

    expected_rng &= 0xFFFFFFFF
    reader = rng_reader
    if reader is None:
        load_gba_core, load_state_file, read_rng_state = _load_runtime_readers()
        core = load_gba_core(rom_path)
        _ensure_core_memory_ready(core)

        def reader(path: Path) -> int:
            load_state_file(core, path)
            return int(read_rng_state(core)) & 0xFFFFFFFF

    results: list[SampleVerification] = []
    for target, state_path in samples:
        try:
            observed_rng = int(reader(state_path)) & 0xFFFFFFFF
            drift = signed_lcrng_distance(expected_rng, observed_rng, drift_window)
            status = "ok" if drift is not None else "rng-mismatch"
            results.append(
                SampleVerification(
                    target=_format_u16(target),
                    state_path=str(state_path),
                    expected_rng=_format_u32(expected_rng),
                    observed_rng=_format_u32(observed_rng),
                    drift=drift,
                    status=status,
                )
            )
        except Exception as exc:  # pragma: no cover - host mGBA runtime failures vary.
            results.append(
                SampleVerification(
                    target=_format_u16(target),
                    state_path=str(state_path),
                    expected_rng=_format_u32(expected_rng),
                    observed_rng=None,
                    drift=None,
                    status="load-failed",
                    error=f"{type(exc).__name__}: {exc}",
                )
            )
    return results


def _read_u8(core: object, address: int) -> int:
    """Read one byte from the loaded host-side core."""

    return int(core.memory.u8[address]) & 0xFF


def _read_u16(core: object, address: int) -> int:
    """Read one little-endian halfword from the loaded host-side core."""

    return int(core.memory.u16[address]) & 0xFFFF


def _read_u32(core: object, address: int) -> int:
    """Read one little-endian word from the loaded host-side core."""

    return int(core.memory.u32[address]) & 0xFFFFFFFF


def _daycare_base_address(core: object) -> int:
    """Return the loaded FR/LG SaveBlock1 daycare address."""

    save_block1 = _read_u32(core, GSAVEBLOCK1_PTR_ADDR)
    if not save_block1:
        raise RuntimeError("SaveBlock1 pointer is zero")
    return save_block1 + DAYCARE_OFFSET


def _read_daycare_fields(core: object) -> tuple[int, int, int]:
    """Read the pending egg lower half, hatch counter, and Mon2 step counter."""

    daycare = _daycare_base_address(core)
    lower = _read_u16(core, daycare + DAYCARE_OFFSPRING_PERSONALITY_OFFSET)
    hatch_counter = _read_u8(core, daycare + DAYCARE_STEP_COUNTER_OFFSET)
    mon2_steps = _read_u32(core, daycare + DAYCARE_MON2_STEPS_OFFSET)
    return lower, hatch_counter, mon2_steps


def _read_dialog_task_signature(core: object) -> str:
    """Return a compact active `gTasks` signature for the daycare dialog frame."""

    parts: list[str] = []
    for task_id in range(TASK_COUNT):
        base = GTASKS_ADDR + task_id * TASK_SIZE
        if not _read_u8(core, base + 4):
            continue
        func = _read_u32(core, base)
        priority = _read_u8(core, base + 7)
        data0 = _read_u16(core, base + 8)
        data1 = _read_u16(core, base + 10)
        data2 = _read_u16(core, base + 12)
        parts.append(
            f"{task_id}:{func:08X}:p{priority:02X}:d0={data0:04X}:d1={data1:04X}:d2={data2:04X}"
        )
    return "|".join(parts)


def _dialog_task_stack_matches(core: object) -> bool:
    """Check the known active task stack at the pre-final-input daycare dialog."""

    active: list[tuple[int, int]] = []
    for task_id in range(TASK_COUNT):
        base = GTASKS_ADDR + task_id * TASK_SIZE
        if _read_u8(core, base + 4):
            active.append((task_id, _read_u32(core, base)))
    if tuple(active) != EXPECTED_DIALOG_TASKS:
        return False

    task2_base = GTASKS_ADDR + 2 * TASK_SIZE
    return (
        _read_u16(core, task2_base + 8) == EXPECTED_DIALOG_TASK2_DATA0
        and _read_u16(core, task2_base + 10) == EXPECTED_DIALOG_TASK2_DATA1
    )


def _runtime_observation(
    core: object,
    target: int,
    state_path: Path,
    *,
    expected_rng: int,
    drift_window: int,
) -> RuntimeStateObservation:
    """Read all runtime fields from one already-loaded savestate."""

    problems: list[str] = []
    frame_counter = int(getattr(core, "frame_counter", -1))
    rng = _read_u32(core, GRNG_VALUE_ADDR)
    drift = signed_lcrng_distance(expected_rng, rng, drift_window)
    if drift is None:
        problems.append("rng-mismatch")

    daycare_lower: int | None
    daycare_step_counter: int | None
    mon2_low_byte: int | None
    stock_egg_waiting: bool | None
    try:
        daycare_lower, daycare_step_counter, mon2_steps = _read_daycare_fields(core)
        mon2_low_byte = mon2_steps & 0xFF
        stock_egg_waiting = daycare_lower != 0
        if daycare_lower != target:
            problems.append("daycare-lower-mismatch")
        if not stock_egg_waiting and target != 0:
            problems.append("stock-no-pending-egg")
    except Exception as exc:
        daycare_lower = None
        daycare_step_counter = None
        mon2_low_byte = None
        stock_egg_waiting = None
        problems.append(f"daycare-read-failed:{type(exc).__name__}")

    party_count = _read_u8(core, GPLAYER_PARTY_COUNT_ADDR)
    if party_count != EXPECTED_PRE_PICKUP_PARTY_COUNT:
        problems.append("party-count-mismatch")

    keyinput = _read_u16(core, KEYINPUT_ADDR)
    if keyinput != EXPECTED_NEUTRAL_KEYINPUT:
        problems.append("keyinput-mismatch")

    dialog_signature = _read_dialog_task_signature(core)
    if not _dialog_task_stack_matches(core):
        problems.append("dialog-task-mismatch")

    return RuntimeStateObservation(
        target=_format_u16(target),
        state_path=str(state_path),
        frame_counter=frame_counter,
        rng=_format_u32(rng),
        drift=drift,
        daycare_lower=_format_u16(daycare_lower) if daycare_lower is not None else None,
        daycare_step_counter=daycare_step_counter,
        mon2_low_byte=f"0x{mon2_low_byte:02X}" if mon2_low_byte is not None else None,
        stock_egg_waiting=stock_egg_waiting,
        party_count=party_count,
        keyinput=_format_u16(keyinput),
        dialog_task_signature=dialog_signature,
        status="ok" if not problems else ",".join(problems),
    )


def verify_all_runtime_states(
    state_paths: dict[int, Path],
    *,
    rom_path: Path = DEFAULT_ROM_PATH,
    expected_rng: int = DEFAULT_EXPECTED_RNG,
    drift_window: int = DEFAULT_DRIFT_WINDOW,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    progress_every: int = 0,
) -> RuntimeAuditReport:
    """Load every state read-only and validate RAM-side RNG/daycare/dialog fields."""

    load_gba_core, load_state_file, _read_rng_state = _load_runtime_readers()
    core = load_gba_core(rom_path)
    _ensure_core_memory_ready(core)

    samples: dict[str, list[str]] = {}
    drift_counts: Counter[str] = Counter()
    frame_counter_counts: Counter[str] = Counter()
    dialog_signature_counts: Counter[str] = Counter()
    checked_states = 0
    load_failed = 0
    rng_mismatches = 0
    daycare_lower_mismatches = 0
    stock_zero_lower_states = 0
    party_count_mismatches = 0
    keyinput_mismatches = 0
    dialog_task_mismatches = 0

    targets = sorted(state_paths)
    total = len(targets)
    for index, target in enumerate(targets, start=1):
        state_path = state_paths[target]
        if progress_every and (index == 1 or index % progress_every == 0 or index == total):
            print(f"Runtime audit progress: {index}/{total} {state_path.name}", file=sys.stderr)

        try:
            load_state_file(core, state_path)
            observation = _runtime_observation(
                core,
                target,
                state_path,
                expected_rng=expected_rng,
                drift_window=drift_window,
            )
        except Exception as exc:  # pragma: no cover - host mGBA runtime failures vary.
            load_failed += 1
            _add_sample(
                samples,
                "load_failed",
                f"{_format_u16(target)} {state_path.name}: {type(exc).__name__}: {exc}",
                sample_limit,
            )
            continue

        checked_states += 1
        drift_counts[str(observation.drift)] += 1
        frame_counter_counts[str(observation.frame_counter)] += 1
        if observation.dialog_task_signature is not None:
            dialog_signature_counts[observation.dialog_task_signature] += 1

        if observation.drift is None:
            rng_mismatches += 1
            _add_sample(samples, "rng_mismatches", f"{observation.target} rng={observation.rng}", sample_limit)
        if observation.daycare_lower != observation.target:
            daycare_lower_mismatches += 1
            _add_sample(
                samples,
                "daycare_lower_mismatches",
                f"{observation.target} lower={observation.daycare_lower}",
                sample_limit,
            )
        if observation.stock_egg_waiting is False:
            stock_zero_lower_states += 1
            if target != 0:
                _add_sample(samples, "stock_no_pending_egg", observation.target, sample_limit)
        if observation.party_count != EXPECTED_PRE_PICKUP_PARTY_COUNT:
            party_count_mismatches += 1
            _add_sample(
                samples,
                "party_count_mismatches",
                f"{observation.target} party={observation.party_count}",
                sample_limit,
            )
        if observation.keyinput != _format_u16(EXPECTED_NEUTRAL_KEYINPUT):
            keyinput_mismatches += 1
            _add_sample(
                samples,
                "keyinput_mismatches",
                f"{observation.target} keyinput={observation.keyinput}",
                sample_limit,
            )
        if "dialog-task-mismatch" in observation.status:
            dialog_task_mismatches += 1
            _add_sample(
                samples,
                "dialog_task_mismatches",
                f"{observation.target} tasks={observation.dialog_task_signature}",
                sample_limit,
            )
        if observation.status != "ok":
            _add_sample(samples, "non_ok_observations", f"{observation.target} {observation.status}", sample_limit)

    return RuntimeAuditReport(
        checked_states=checked_states,
        load_failed=load_failed,
        rng_mismatches=rng_mismatches,
        daycare_lower_mismatches=daycare_lower_mismatches,
        stock_zero_lower_states=stock_zero_lower_states,
        party_count_mismatches=party_count_mismatches,
        keyinput_mismatches=keyinput_mismatches,
        dialog_task_mismatches=dialog_task_mismatches,
        drift_counts=dict(sorted(drift_counts.items())),
        frame_counter_counts=dict(sorted(frame_counter_counts.items())),
        dialog_task_signature_counts=dict(
            sorted(dialog_signature_counts.items(), key=lambda item: (-item[1], item[0]))[:sample_limit]
        ),
        samples=samples,
    )


def report_has_health_failures(report: Phase2FolderReport) -> bool:
    """Return True for corruption-style findings, excluding expected active-run missing states."""

    return any(
        (
            report.bad_size_states,
            report.stale_tmp_files,
            report.bad_names,
            report.duplicate_weird_files,
        )
    )


def sample_results_failed(results: Iterable[SampleVerification]) -> bool:
    """Return True if any optional sample verification failed."""

    return any(result.status != "ok" for result in results)


def runtime_audit_failed(report: RuntimeAuditReport | None) -> bool:
    """Return True when the full host-side runtime audit found a mismatch."""

    if report is None:
        return False
    return any(
        (
            report.load_failed,
            report.rng_mismatches,
            report.daycare_lower_mismatches,
            report.party_count_mismatches,
            report.keyinput_mismatches,
            report.dialog_task_mismatches,
        )
    )


def print_text(
    report: Phase2FolderReport,
    sample_results: list[SampleVerification],
    runtime_report: RuntimeAuditReport | None = None,
) -> None:
    """Print compact operator-facing report."""

    print(f"Folder: {report.folder}")
    print(f"Complete states: {report.complete_states} / {report.target_states}")
    print(f"Missing states: {report.missing_states}")
    print(f"Final .ss0 files: {report.final_state_files}")
    print(f"Bad-size final .ss0 files: {report.bad_size_states}")
    print(f"Temporary .ss0.tmp files: {report.tmp_files}")
    print(f"Stale temporary files: {report.stale_tmp_files}")
    print(f"Bad names: {report.bad_names}")
    print(f"Duplicate weird files: {report.duplicate_weird_files}")
    print(f"Ignored metadata files: {report.ignored_metadata_files}")
    print(f"Ignored directories: {report.ignored_directories}")
    for key, values in report.samples.items():
        if not values:
            continue
        print(f"\nSample {key}:")
        for value in values:
            print(f"  {value}")
    if sample_results:
        print("\nSample RNG verification:")
        for result in sample_results:
            drift = "none" if result.drift is None else str(result.drift)
            observed = result.observed_rng or "unread"
            print(f"  {result.target}: {result.status} observed={observed} drift={drift}")
            if result.error:
                print(f"    {result.error}")
    if runtime_report is not None:
        print("\nFull runtime audit:")
        print(f"  Checked states: {runtime_report.checked_states}")
        print(f"  Load failures: {runtime_report.load_failed}")
        print(f"  RNG mismatches: {runtime_report.rng_mismatches}")
        print(f"  Daycare lower mismatches: {runtime_report.daycare_lower_mismatches}")
        print(f"  Stock zero-lower states: {runtime_report.stock_zero_lower_states}")
        print(f"  Party-count mismatches: {runtime_report.party_count_mismatches}")
        print(f"  Neutral-keyinput mismatches: {runtime_report.keyinput_mismatches}")
        print(f"  Dialog-task mismatches: {runtime_report.dialog_task_mismatches}")
        print(f"  Drift counts: {runtime_report.drift_counts}")
        print(f"  Frame-counter counts: {runtime_report.frame_counter_counts}")
        if runtime_report.dialog_task_signature_counts:
            print("  Dialog task signatures:")
            for signature, count in runtime_report.dialog_task_signature_counts.items():
                print(f"    {count}: {signature}")
        for key, values in runtime_report.samples.items():
            if not values:
                continue
            print(f"\nRuntime sample {key}:")
            for value in values:
                print(f"  {value}")


def build_payload(
    report: Phase2FolderReport,
    sample_results: list[SampleVerification],
    runtime_report: RuntimeAuditReport | None = None,
) -> dict[str, object]:
    """Build JSON-serializable payload."""

    payload: dict[str, object] = {
        "folder_report": asdict(report),
        "sample_verification": [asdict(result) for result in sample_results],
    }
    if runtime_report is not None:
        payload["runtime_audit"] = asdict(runtime_report)
    return payload


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse CLI options."""

    parser = argparse.ArgumentParser(
        description="Validate Phase 2 pickup-state files without touching the running Qt emulator.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_FOLDER)
    parser.add_argument("--status", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--target-states", type=int, default=DEFAULT_TARGET_STATES)
    parser.add_argument("--expected-state-size", type=int, default=DEFAULT_STATE_SIZE)
    parser.add_argument("--tmp-stale-seconds", type=float, default=DEFAULT_TMP_STALE_SECONDS)
    parser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    parser.add_argument(
        "--verify-samples",
        type=int,
        default=0,
        help="Optional host-side savestate loads to verify baseline gRngValue.",
    )
    parser.add_argument(
        "--sample-targets",
        help="Comma-separated target list such as 0x0000,0x0001,0xFFFF for optional sample verification.",
    )
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM_PATH)
    parser.add_argument(
        "--verify-all-runtime",
        action="store_true",
        help=(
            "Load every valid .ss0 through a separate host-side mGBA core and "
            "read RAM fields only. This opens savestates read-only and never "
            "writes them back."
        ),
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=4096,
        help="Runtime-audit progress interval written to stderr; 0 disables progress.",
    )
    parser.add_argument(
        "--expected-rng",
        type=lambda text: int(text, 0),
        default=None,
        help="Expected baseline gRngValue. Defaults to status JSON, then 0x80323CC6.",
    )
    parser.add_argument("--drift-window", type=int, default=DEFAULT_DRIFT_WINDOW)
    parser.add_argument("--json", action="store_true")
    parser.add_argument(
        "--strict-health",
        action="store_true",
        help="Exit nonzero on bad size, stale tmp, bad name, duplicate weird file, or sample mismatch.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Also exit nonzero when not all target states are complete.",
    )
    return parser.parse_args(argv)


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    scan = scan_phase2_folder(
        args.folder,
        target_states=args.target_states,
        expected_state_size=args.expected_state_size,
        tmp_stale_seconds=args.tmp_stale_seconds,
        sample_limit=args.sample_limit,
    )
    expected_rng = args.expected_rng
    if expected_rng is None:
        expected_rng = _read_status_expected_rng(args.status) or DEFAULT_EXPECTED_RNG
    sample_targets = parse_targets(args.sample_targets)
    selected_samples = select_sample_state_paths(
        scan.valid_state_paths,
        sample_count=args.verify_samples,
        sample_targets=sample_targets,
    )
    sample_results: list[SampleVerification] = []
    if selected_samples:
        sample_results = verify_sample_states(
            selected_samples,
            rom_path=args.rom,
            expected_rng=expected_rng,
            drift_window=args.drift_window,
        )
    runtime_report: RuntimeAuditReport | None = None
    if args.verify_all_runtime:
        runtime_report = verify_all_runtime_states(
            scan.valid_state_paths,
            rom_path=args.rom,
            expected_rng=expected_rng,
            drift_window=args.drift_window,
            sample_limit=args.sample_limit,
            progress_every=args.progress_every,
        )

    if args.json:
        print(json.dumps(build_payload(scan.report, sample_results, runtime_report), indent=2, sort_keys=True))
    else:
        print_text(scan.report, sample_results, runtime_report)

    failed = False
    if args.strict_health:
        failed = (
            report_has_health_failures(scan.report)
            or sample_results_failed(sample_results)
            or runtime_audit_failed(runtime_report)
        )
    if args.require_complete:
        failed = failed or scan.report.missing_states > 0
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
