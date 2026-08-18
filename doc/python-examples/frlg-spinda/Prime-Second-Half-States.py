r"""Prime second-half FR/LG Spinda savestates from first-half save files.

This is the bridge step between the completed first-half save pile and phase 2.
For each existing `.sav` file it:

1. loads the source save as a temporary mGBA save
2. replays the known title route for the current phase-2 initial seed
3. observes the organic Timer 1 seed without writing `gRngValue`
4. replays `tape seed to step 2.json`
5. pads neutral input until exactly 740 rendered frames after the seed frame
6. validates the live `gRngValue` against a learned or configured checkpoint
7. saves `priomed-2nd\0x####.ss0`

The folder name `priomed-2nd` preserves the operator-requested spelling.

Important: this script deliberately defaults to the route anchor, not the
read-only checkpoint anchor. A checkpoint savestate can include old save RAM;
replaying the route from reset keeps the currently loaded first-half `.sav`
contents alive while still reproducing the desired initial seed.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import importlib.util
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).parent
EXAMPLES_DIR = SCRIPT_DIR.parent
MGBA_ROOT = SCRIPT_DIR.parents[2]
SEED_BRUTE_FORCE_DIR = EXAMPLES_DIR / "frlg-seed-bruteforce"
SAMPLE_REPLICATOR_PATH = SEED_BRUTE_FORCE_DIR / "Seed-Sample-Replicator.py"
DEFAULT_SAVE_DIR = MGBA_ROOT / "1sthalves" / "saves"
DEFAULT_OUTPUT_DIR = MGBA_ROOT / "1sthalves" / "priomed-2nd"
DEFAULT_METADATA_PATH = MGBA_ROOT / "live-lanes" / "live-cd39-lane21" / "1 from egg - replay-metadata.json"
DEFAULT_SECOND_STEP_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "tape seed to step 2.json"
DEFAULT_TARGET_FRAME_FROM_SEED = 740
DEFAULT_EXPECTED_SAVE_COUNT = 65_536
DEFAULT_RETRY_COUNT = 3
DEFAULT_RNG_DRIFT_WINDOW = 4096
DEFAULT_SEED_DELAY_SEARCH_START = 0
DEFAULT_SEED_DELAY_SEARCH_END = 5000
DEFAULT_SEED_OBSERVE_TIMEOUT = 600
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_STATUS_EVERY = 25
SAVE_NAME_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.sav$")
ENV_PREFIX = "MGBA_PRIME_SECOND_"

if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import input_tape
from spinda_frlg_common import (
    GRNG_VALUE_ADDR,
    format_u16,
    format_u32,
    lcrng_next_state,
    lcrng_previous_state,
    read_rng_state,
    write_json_atomic,
)


class SeedDelaySearchError(RuntimeError):
    """Raised when no organic title delay hits the requested initial seed."""


@dataclass(frozen=True)
class SaveFingerprint:
    """Cheap source-save fingerprint used to detect accidental writes."""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class PrimeConfig:
    """Runtime configuration for the priming pass."""

    save_dir: Path
    output_dir: Path
    metadata_path: Path
    reset_tape_path: Path | None
    pre_input_tape_path: Path | None
    second_step_tape_path: Path
    target_frame_from_seed: int
    expected_save_count: int
    require_expected_save_count: bool
    expected_rng_at_target: int | None
    calibrated_seed_delay_frames: int | None
    seed_delay_search_start: int
    seed_delay_search_end: int
    seed_observe_timeout: int
    retry_count: int
    rng_drift_window: int
    limit: int | None
    start_hex: int | None
    end_hex: int | None
    overwrite: bool
    dry_run: bool
    progress_every: int
    status_every: int
    stop_on_error: bool
    speed_toggles: bool


@dataclass(frozen=True)
class RuntimeCheckpoint:
    """Small wrapper around the fastest savestate path exposed by this core."""

    mode: str
    state: Any | None = None
    path: Path | None = None


@dataclass(frozen=True)
class SeedReplayResult:
    """Observed organic initial-seed hit from one loaded source save."""

    seed_value: int
    seed_frame: int
    rng_at_seed: int
    delay_frames: int
    searched: bool


@dataclass(frozen=True)
class PrimeResult:
    """One successfully primed source save."""

    save_name: str
    output_name: str
    observed_seed: str
    seed_delay_frames: int
    seed_delay_searched: bool
    seed_frame: int
    rng_at_seed: str
    final_frame_from_seed: int
    final_rng: str
    rng_calls_from_seed: int | None
    source_unchanged: bool
    attempts: int


@dataclass(frozen=True)
class PrimeFailure:
    """One failed source save, kept small for JSONL status."""

    save_name: str
    error: str
    attempts: int
    observed_final_rng: str | None = None
    rng_drift_from_expected: int | None = None


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_int(value: Any, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse decimal or hex integers from CLI, JSON, or environment values."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean.")
    parsed = int(value, 0) if isinstance(value, str) else int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}, got {parsed}.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} must be <= {maximum}, got {parsed}.")
    return parsed


def _display_path(path: Path) -> Path:
    """Return an absolute path without dereferencing the `<repo-root>` junction."""

    return path.expanduser().absolute()


def _optional_int(value: Any, field: str) -> int | None:
    """Parse an optional integer where blank means None."""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _parse_int(value, field)


def _load_module(path: Path, module_name: str) -> ModuleType:
    """Import one helper file whose filename is not a valid module name."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_sample_replicator(path: Path = SAMPLE_REPLICATOR_PATH) -> ModuleType:
    """Load the maintained no-bruteforce initial-seed replay helper."""

    return _load_module(path, "prime_second_sample_replicator")


def cheap_fingerprint(path: Path) -> SaveFingerprint:
    """Return size/mtime fingerprint without hashing every 128 KiB save."""

    stat_result = path.stat()
    return SaveFingerprint(size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns)


def source_key_from_save_name(path: Path) -> int:
    """Return the 16-bit hex key from a canonical first-half save filename."""

    match = SAVE_NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"Bad source save name: {path.name}")
    return int(match.group(1), 16)


def output_state_path(output_dir: Path, save_path: Path) -> Path:
    """Map `0x####.sav` to `priomed-2nd\0x####.ss0`."""

    source_key_from_save_name(save_path)
    return output_dir / save_path.with_suffix(".ss0").name


def select_source_saves(
    save_dir: Path,
    *,
    start_hex: int | None = None,
    end_hex: int | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Select canonical `.sav` inputs in numeric first-half order."""

    save_dir = _display_path(save_dir)
    if not save_dir.is_dir():
        raise FileNotFoundError(f"Save folder not found: {save_dir}")

    bad_names = sorted(path.name for path in save_dir.glob("*.sav") if SAVE_NAME_RE.match(path.name) is None)
    if bad_names:
        shown = ", ".join(bad_names[:8])
        more = "" if len(bad_names) <= 8 else f", ... +{len(bad_names) - 8}"
        raise RuntimeError(f"Bad source save name(s) in {save_dir}: {shown}{more}")

    saves = sorted(save_dir.glob("0x*.sav"), key=source_key_from_save_name)
    if start_hex is not None:
        saves = [path for path in saves if source_key_from_save_name(path) >= start_hex]
    if end_hex is not None:
        saves = [path for path in saves if source_key_from_save_name(path) <= end_hex]
    if limit is not None:
        saves = saves[:limit]
    return saves


def tape_prefix(tape: input_tape.InputTape, frame_count: int) -> input_tape.InputTape | None:
    """Return a tape containing the first `frame_count` frames."""

    frame_count = _parse_int(frame_count, "frame_count", minimum=0)
    if frame_count == 0:
        return None
    if frame_count >= tape.frame_count:
        return tape

    remaining = frame_count
    runs: list[input_tape.InputRun] = []
    for run in tape.runs:
        if remaining <= 0:
            break
        take = min(run.frames, remaining)
        runs.append(input_tape.InputRun(mask=run.mask, frames=take))
        remaining -= take

    metadata = dict(tape.metadata)
    metadata["prefix_frame_count"] = frame_count
    return input_tape.from_runs(runs, metadata=metadata)


def replay_second_step_to_target(
    core: Any,
    second_step_tape: input_tape.InputTape,
    *,
    seed_frame: int,
    target_frame_from_seed: int,
) -> int:
    """Replay step-2 input, then neutral-pad to the exact seed-relative frame."""

    current_from_seed = int(getattr(core, "frame_counter", 0)) - seed_frame
    if current_from_seed > target_frame_from_seed:
        raise RuntimeError(
            "Already past target frame after seed observation: "
            f"current={current_from_seed} target={target_frame_from_seed}"
        )

    remaining = target_frame_from_seed - current_from_seed
    tape_frames = min(remaining, second_step_tape.frame_count)
    prefix = tape_prefix(second_step_tape, tape_frames)
    if prefix is not None:
        input_tape.replay_tape(core, prefix, use_batch=True)

    current_from_seed = int(getattr(core, "frame_counter", 0)) - seed_frame
    neutral_frames = target_frame_from_seed - current_from_seed
    if neutral_frames < 0:
        raise RuntimeError(
            "Step-2 tape overshot target frame: "
            f"current={current_from_seed} target={target_frame_from_seed}"
        )
    if neutral_frames:
        input_tape.run_exact_frames(core, 0, neutral_frames, use_batch=True)
        input_tape.set_exact_keys(core, 0)

    final_from_seed = int(getattr(core, "frame_counter", 0)) - seed_frame
    if final_from_seed != target_frame_from_seed:
        raise RuntimeError(
            "Frame calibration failed: "
            f"final={final_from_seed} target={target_frame_from_seed}"
        )
    return final_from_seed


def forward_lcrng_distance(start_state: int, target_state: int, max_steps: int) -> int | None:
    """Return forward LCRNG calls from start to target within `max_steps`."""

    state = start_state & 0xFFFFFFFF
    target = target_state & 0xFFFFFFFF
    if state == target:
        return 0
    for steps in range(1, max_steps + 1):
        state = lcrng_next_state(state)
        if state == target:
            return steps
    return None


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


def read_status_expected_rng(status_path: Path) -> int | None:
    """Load prior learned target RNG from the small resume/status file."""

    if not status_path.is_file():
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _optional_int(payload.get("expected_rng_at_target"), "expected_rng_at_target")


def read_status_calibrated_delay(status_path: Path) -> int | None:
    """Load prior learned source-save title delay from status JSON."""

    if not status_path.is_file():
        return None
    try:
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return _optional_int(payload.get("calibrated_seed_delay_frames"), "calibrated_seed_delay_frames")


def write_status(status_path: Path, payload: Mapping[str, Any]) -> None:
    """Write small progress JSON for resume/web watchers."""

    write_json_atomic(status_path, payload)


def append_failure(error_path: Path, failure: PrimeFailure) -> None:
    """Append one compact JSONL failure record."""

    error_path.parent.mkdir(parents=True, exist_ok=True)
    with error_path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(failure), sort_keys=True) + "\n")


def _save_state_file(helper: ModuleType, core: Any, path: Path) -> None:
    """Save a file-backed mGBA state through the maintained helper."""

    path.parent.mkdir(parents=True, exist_ok=True)
    helper.save_state_file(core, path)


def capture_runtime_state(helper: ModuleType, core: Any, *, scratch_path: Path) -> RuntimeCheckpoint:
    """Capture a rewind point with raw, scratch, or file savestate fallback."""

    save_raw_state = getattr(core, "save_raw_state", None)
    load_raw_state = getattr(core, "load_raw_state", None)
    if callable(save_raw_state) and callable(load_raw_state):
        state = save_raw_state()
        if state is None:
            raise RuntimeError("save_raw_state() failed while capturing runtime checkpoint.")
        return RuntimeCheckpoint(mode="raw", state=state)

    save_scratch_state = getattr(core, "save_scratch_state", None)
    load_scratch_state = getattr(core, "load_scratch_state", None)
    if callable(save_scratch_state) and callable(load_scratch_state):
        save_scratch_state()
        return RuntimeCheckpoint(mode="scratch")

    scratch_path.parent.mkdir(parents=True, exist_ok=True)
    helper.save_state_file(core, scratch_path)
    return RuntimeCheckpoint(mode="file", path=scratch_path)


def restore_runtime_state(
    helper: ModuleType,
    core: Any,
    checkpoint: RuntimeCheckpoint,
    *,
    qt_mode: bool,
) -> None:
    """Restore a checkpoint captured by `capture_runtime_state`."""

    if checkpoint.mode == "raw":
        load_raw_state = getattr(core, "load_raw_state", None)
        if not callable(load_raw_state) or not load_raw_state(checkpoint.state):
            raise RuntimeError("load_raw_state() failed while restoring runtime checkpoint.")
        return

    if checkpoint.mode == "scratch":
        load_scratch_state = getattr(core, "load_scratch_state", None)
        if not callable(load_scratch_state):
            raise RuntimeError("load_scratch_state() is unavailable.")
        load_scratch_state()
        return

    if checkpoint.mode == "file" and checkpoint.path is not None:
        try:
            helper.load_state_file(core, checkpoint.path, qt_mode=qt_mode)
        except TypeError:
            helper.load_state_file(core, checkpoint.path)
        return

    raise RuntimeError(f"Unsupported runtime checkpoint mode: {checkpoint.mode}")


def _enable_speed_toggles(helper: ModuleType, core: Any) -> None:
    """Enable live fast settings when the visible Qt bridge exposes them."""

    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    if not qt_mode:
        return
    for name in (
        "ensure_live_audio_killswitch",
        "ensure_live_no_render_mode",
        "ensure_live_unbounded_fast_forward",
    ):
        func = getattr(helper, name, None)
        if callable(func):
            func(core, qt_mode=qt_mode)


def observe_seed_after_title_press(
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    *,
    seed_timeout: int,
    suppress_output: bool = True,
) -> tuple[int, int, int]:
    """Press the configured title button and observe the organic Timer 1 seed."""

    button_key = sample_replicator._button_key(helper, recipe.button_name)
    context = contextlib.redirect_stdout(io.StringIO()) if suppress_output else contextlib.nullcontext()
    with context:
        return sample_replicator._observe_seed_after_title_press(
            core,
            helper,
            button_key=button_key,
            seed_timeout=seed_timeout,
        )


def replay_title_route_to_preinput(
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
) -> None:
    """Replay route tapes from reset to the pre-final-title-input checkpoint."""

    sample_replicator._replay_known_title_route_from_tapes(core, helper, recipe)
    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 stopped before the calibrated final title input.")
    if not helper.title_input_checkpoint_ready(core):
        raise RuntimeError("Title task was not in RUN/state=1 before final title input.")


def try_seed_delay_from_preinput(
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    *,
    delay_frames: int,
    seed_timeout: int,
) -> tuple[int, int, int] | None:
    """Try one no-input delay from the current pre-input state."""

    helper.run_frames_fast(core, delay_frames)
    if recipe.pre_input_neutral_frames:
        helper.run_frames_with_keys(core, 0, recipe.pre_input_neutral_frames)
    try:
        return observe_seed_after_title_press(
            core,
            helper,
            sample_replicator,
            recipe,
            seed_timeout=seed_timeout,
            suppress_output=True,
        )
    except Exception:
        input_tape.set_exact_keys(core, 0)
        return None


def search_seed_delay_from_preinput(
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    *,
    config: PrimeConfig,
    scratch_path: Path,
) -> SeedReplayResult:
    """Search organic title delay using a rolling pre-input checkpoint."""

    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    if config.seed_delay_search_start > 0:
        helper.run_frames_fast(core, config.seed_delay_search_start)
    checkpoint = capture_runtime_state(helper, core, scratch_path=scratch_path)

    for delay in range(config.seed_delay_search_start, config.seed_delay_search_end + 1):
        restore_runtime_state(helper, core, checkpoint, qt_mode=qt_mode)
        observation = try_seed_delay_from_preinput(
            core,
            helper,
            sample_replicator,
            recipe,
            delay_frames=0,
            seed_timeout=config.seed_observe_timeout,
        )
        if observation is not None:
            seed_value, seed_frame, rng_at_seed = observation
            if seed_value == recipe.target_seed:
                return SeedReplayResult(
                    seed_value=seed_value,
                    seed_frame=seed_frame,
                    rng_at_seed=rng_at_seed,
                    delay_frames=delay,
                    searched=True,
                )

        if delay >= config.seed_delay_search_end:
            break
        restore_runtime_state(helper, core, checkpoint, qt_mode=qt_mode)
        helper.run_frames_fast(core, 1)
        checkpoint = capture_runtime_state(helper, core, scratch_path=scratch_path)

    raise SeedDelaySearchError(
        "Target seed not found in source-save delay search: "
        f"target={format_u16(recipe.target_seed)}"
        f" range={config.seed_delay_search_start}..{config.seed_delay_search_end}"
    )


def replicate_initial_seed_for_save(
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    *,
    config: PrimeConfig,
    calibrated_delay_frames: int | None,
    scratch_path: Path,
) -> SeedReplayResult:
    """Reproduce the configured initial seed from the loaded source save."""

    replay_title_route_to_preinput(core, helper, sample_replicator, recipe)
    delay = calibrated_delay_frames
    if delay is not None:
        observation = try_seed_delay_from_preinput(
            core,
            helper,
            sample_replicator,
            recipe,
            delay_frames=delay,
            seed_timeout=config.seed_observe_timeout,
        )
        if observation is not None:
            seed_value, seed_frame, rng_at_seed = observation
            if seed_value == recipe.target_seed:
                return SeedReplayResult(
                    seed_value=seed_value,
                    seed_frame=seed_frame,
                    rng_at_seed=rng_at_seed,
                    delay_frames=delay,
                    searched=False,
                )

    replay_title_route_to_preinput(core, helper, sample_replicator, recipe)
    return search_seed_delay_from_preinput(
        core,
        helper,
        sample_replicator,
        recipe,
        config=config,
        scratch_path=scratch_path,
    )


def prime_one_save_attempt(
    save_path: Path,
    output_path: Path,
    *,
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    second_step_tape: input_tape.InputTape,
    config: PrimeConfig,
    expected_rng_at_target: int | None,
    calibrated_seed_delay_frames: int | None,
) -> PrimeResult:
    """Load one source save, reproduce seed, run tape, validate, save state."""

    before = cheap_fingerprint(save_path)
    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    helper.load_required_save_file(core, save_path, qt_mode=qt_mode, temporary=True)

    scratch_path = config.output_dir / "_scratch" / "prime-second-preinput.ss0"
    seed_result = replicate_initial_seed_for_save(
        core,
        helper,
        sample_replicator,
        recipe,
        config=config,
        calibrated_delay_frames=calibrated_seed_delay_frames,
        scratch_path=scratch_path,
    )
    if seed_result.seed_value != recipe.target_seed:
        raise RuntimeError(
            "Seed mismatch after known route replay: "
            f"observed={format_u16(seed_result.seed_value)} target={format_u16(recipe.target_seed)}"
        )

    final_from_seed = replay_second_step_to_target(
        core,
        second_step_tape,
        seed_frame=seed_result.seed_frame,
        target_frame_from_seed=config.target_frame_from_seed,
    )
    final_rng = read_rng_state(core)
    rng_calls = forward_lcrng_distance(
        seed_result.rng_at_seed,
        final_rng,
        max(config.rng_drift_window, config.target_frame_from_seed * 8),
    )

    if expected_rng_at_target is not None and final_rng != expected_rng_at_target:
        drift = signed_lcrng_distance(expected_rng_at_target, final_rng, config.rng_drift_window)
        raise RuntimeError(
            "Final gRngValue did not match calibration checkpoint: "
            f"expected={format_u32(expected_rng_at_target)} observed={format_u32(final_rng)}"
            f" drift={drift}"
        )

    input_tape.set_exact_keys(core, 0)
    _save_state_file(helper, core, output_path)

    after = cheap_fingerprint(save_path)
    if before != after:
        raise RuntimeError(f"Source save changed on disk while priming: {save_path}")

    return PrimeResult(
        save_name=save_path.name,
        output_name=output_path.name,
        observed_seed=format_u16(seed_result.seed_value) or "0x0000",
        seed_delay_frames=seed_result.delay_frames,
        seed_delay_searched=seed_result.searched,
        seed_frame=seed_result.seed_frame,
        rng_at_seed=format_u32(seed_result.rng_at_seed) or "0x00000000",
        final_frame_from_seed=final_from_seed,
        final_rng=format_u32(final_rng) or "0x00000000",
        rng_calls_from_seed=rng_calls,
        source_unchanged=True,
        attempts=1,
    )


def prime_one_save(
    save_path: Path,
    output_path: Path,
    *,
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    second_step_tape: input_tape.InputTape,
    config: PrimeConfig,
    expected_rng_at_target: int | None,
    calibrated_seed_delay_frames: int | None,
) -> PrimeResult:
    """Retry one source save if live RNG noise misses the checkpoint."""

    last_error: Exception | None = None
    for attempt in range(1, config.retry_count + 1):
        try:
            result = prime_one_save_attempt(
                save_path,
                output_path,
                core=core,
                helper=helper,
                sample_replicator=sample_replicator,
                recipe=recipe,
                second_step_tape=second_step_tape,
                config=config,
                expected_rng_at_target=expected_rng_at_target,
                calibrated_seed_delay_frames=calibrated_seed_delay_frames,
            )
            return PrimeResult(**{**asdict(result), "attempts": attempt})
        except Exception as exc:
            last_error = exc
            if isinstance(exc, SeedDelaySearchError):
                break
            if attempt >= config.retry_count:
                break
            print(f"Retry {attempt + 1}/{config.retry_count} for {save_path.name}: {type(exc).__name__}: {exc}")
    assert last_error is not None
    raise last_error


def build_status_payload(
    *,
    config: PrimeConfig,
    recipe: Any,
    second_step_tape: input_tape.InputTape,
    total: int,
    processed: int,
    skipped_existing: int,
    written: int,
    failed: int,
    expected_rng_at_target: int | None,
    calibrated_seed_delay_frames: int | None,
    started_at: str,
    start_monotonic: float,
    current: str | None = None,
    last_result: PrimeResult | None = None,
    last_error: PrimeFailure | None = None,
) -> dict[str, Any]:
    """Build the compact progress/status JSON document."""

    elapsed = max(0.001, time.monotonic() - start_monotonic)
    active_done = max(0, processed - skipped_existing)
    rate = active_done / elapsed if active_done else 0.0
    remaining = max(0, total - processed)
    eta_seconds = None if rate <= 0 else remaining / rate
    return {
        "script": Path(__file__).name,
        "status": "running" if processed < total and failed == 0 else "updated",
        "started_at": started_at,
        "updated_at": _utc_now(),
        "save_dir": str(config.save_dir),
        "output_dir": str(config.output_dir),
        "metadata_path": str(config.metadata_path),
        "target_seed": format_u16(recipe.target_seed),
        "target_frame_from_seed": config.target_frame_from_seed,
        "second_step_tape": str(config.second_step_tape_path),
        "second_step_tape_frames": second_step_tape.frame_count,
        "expected_save_count": config.expected_save_count,
        "source_save_count": total,
        "processed": processed,
        "skipped_existing": skipped_existing,
        "written": written,
        "failed": failed,
        "remaining": remaining,
        "rate_saves_per_second": round(rate, 4),
        "eta_seconds": None if eta_seconds is None else round(eta_seconds, 1),
        "current": current,
        "expected_rng_at_target": format_u32(expected_rng_at_target),
        "calibrated_seed_delay_frames": calibrated_seed_delay_frames,
        "last_result": None if last_result is None else asdict(last_result),
        "last_error": None if last_error is None else asdict(last_error),
    }


def run_prime(config: PrimeConfig) -> int:
    """Run the full priming pass."""

    sample_replicator = load_sample_replicator()
    helper = sample_replicator.load_firsthalf_helper()
    recipe = sample_replicator.load_known_seed_recipe(
        config.metadata_path,
        reset_tape_path=config.reset_tape_path,
        pre_input_tape_path=config.pre_input_tape_path,
    )
    second_step_tape = input_tape.read_tape(config.second_step_tape_path)
    saves = select_source_saves(
        config.save_dir,
        start_hex=config.start_hex,
        end_hex=config.end_hex,
        limit=config.limit,
    )
    if config.require_expected_save_count and len(saves) != config.expected_save_count:
        raise RuntimeError(
            f"Expected {config.expected_save_count} source save(s), found {len(saves)} in {config.save_dir}"
        )

    config.output_dir.mkdir(parents=True, exist_ok=True)
    status_path = config.output_dir / "_prime_second_status.json"
    error_path = config.output_dir / "_prime_second_errors.jsonl"
    expected_rng = (
        config.expected_rng_at_target
        if config.expected_rng_at_target is not None
        else read_status_expected_rng(status_path)
    )
    calibrated_delay = (
        config.calibrated_seed_delay_frames
        if config.calibrated_seed_delay_frames is not None
        else read_status_calibrated_delay(status_path)
    )

    print(f"Source saves: {len(saves)} from {config.save_dir}")
    if len(saves) != config.expected_save_count:
        print(
            f"Source count note: expected={config.expected_save_count}"
            f" found={len(saves)}; processing found saves."
        )
    print(f"Output states: {config.output_dir}")
    print(f"Target seed: {format_u16(recipe.target_seed)} via route anchor")
    print(f"Target frame from seed: {config.target_frame_from_seed}")
    print(f"Step-2 tape frames: {second_step_tape.frame_count}")
    print(f"Expected gRngValue at target: {format_u32(expected_rng)}")
    print(f"Calibrated source-save seed delay: {calibrated_delay}")
    print(f"Seed delay search range: {config.seed_delay_search_start}..{config.seed_delay_search_end}")

    if config.dry_run:
        print("Dry run: no emulator work, no states written.")
        return 0

    core = helper.load_runtime_core(recipe.rom_path)
    if config.speed_toggles:
        _enable_speed_toggles(helper, core)

    started_at = _utc_now()
    start_monotonic = time.monotonic()
    skipped_existing = 0
    written = 0
    failed = 0
    last_result: PrimeResult | None = None
    last_error: PrimeFailure | None = None

    for index, save_path in enumerate(saves, start=1):
        state_path = output_state_path(config.output_dir, save_path)
        if state_path.exists() and not config.overwrite:
            skipped_existing += 1
            if index == 1 or skipped_existing % config.progress_every == 0:
                print(f"[{index}/{len(saves)}] skip existing {state_path.name}")
            if index % config.status_every == 0:
                write_status(
                    status_path,
                    build_status_payload(
                        config=config,
                        recipe=recipe,
                        second_step_tape=second_step_tape,
                        total=len(saves),
                        processed=index,
                        skipped_existing=skipped_existing,
                        written=written,
                        failed=failed,
                        expected_rng_at_target=expected_rng,
                        calibrated_seed_delay_frames=calibrated_delay,
                        started_at=started_at,
                        start_monotonic=start_monotonic,
                        current=save_path.name,
                        last_result=last_result,
                        last_error=last_error,
                    ),
                )
            continue

        try:
            result = prime_one_save(
                save_path,
                state_path,
                core=core,
                helper=helper,
                sample_replicator=sample_replicator,
                recipe=recipe,
                second_step_tape=second_step_tape,
                config=config,
                expected_rng_at_target=expected_rng,
                calibrated_seed_delay_frames=calibrated_delay,
            )
            if expected_rng is None:
                expected_rng = _parse_int(result.final_rng, "learned final_rng", minimum=0, maximum=0xFFFFFFFF)
                print(f"Learned target gRngValue at frame {config.target_frame_from_seed}: {result.final_rng}")
            if calibrated_delay is None or result.seed_delay_searched:
                calibrated_delay = result.seed_delay_frames
                print(f"Calibrated source-save seed delay: {calibrated_delay}")
            written += 1
            last_result = result
            last_error = None
            if index == 1 or written % config.progress_every == 0:
                print(
                    f"[{index}/{len(saves)}] wrote {state_path.name}"
                    f" seed={result.observed_seed}"
                    f" seed_delay={result.seed_delay_frames}"
                    f" frame={result.final_frame_from_seed}"
                    f" rng={result.final_rng}"
                    f" attempts={result.attempts}"
                )
        except Exception as exc:
            failed += 1
            failure = PrimeFailure(
                save_name=save_path.name,
                error=f"{type(exc).__name__}: {exc}",
                attempts=config.retry_count,
            )
            last_error = failure
            append_failure(error_path, failure)
            print(f"[{index}/{len(saves)}] FAIL {save_path.name}: {failure.error}")
            if config.stop_on_error:
                write_status(
                    status_path,
                    build_status_payload(
                        config=config,
                        recipe=recipe,
                        second_step_tape=second_step_tape,
                        total=len(saves),
                        processed=index,
                        skipped_existing=skipped_existing,
                        written=written,
                        failed=failed,
                        expected_rng_at_target=expected_rng,
                        calibrated_seed_delay_frames=calibrated_delay,
                        started_at=started_at,
                        start_monotonic=start_monotonic,
                        current=save_path.name,
                        last_result=last_result,
                        last_error=last_error,
                    ),
                )
                return 1

        if index % config.status_every == 0 or index == len(saves):
            write_status(
                status_path,
                build_status_payload(
                    config=config,
                    recipe=recipe,
                    second_step_tape=second_step_tape,
                    total=len(saves),
                    processed=index,
                    skipped_existing=skipped_existing,
                    written=written,
                    failed=failed,
                    expected_rng_at_target=expected_rng,
                    calibrated_seed_delay_frames=calibrated_delay,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    current=save_path.name,
                    last_result=last_result,
                    last_error=last_error,
                ),
            )

    final_payload = build_status_payload(
        config=config,
        recipe=recipe,
        second_step_tape=second_step_tape,
        total=len(saves),
        processed=len(saves),
        skipped_existing=skipped_existing,
        written=written,
        failed=failed,
        expected_rng_at_target=expected_rng,
        calibrated_seed_delay_frames=calibrated_delay,
        started_at=started_at,
        start_monotonic=start_monotonic,
        current=None,
        last_result=last_result,
        last_error=last_error,
    )
    final_payload["status"] = "complete" if failed == 0 else "complete_with_errors"
    write_status(status_path, final_payload)

    print(
        "Summary:"
        f" written={written}"
        f" skipped_existing={skipped_existing}"
        f" failed={failed}"
        f" expected_rng_at_target={format_u32(expected_rng)}"
        f" calibrated_seed_delay={calibrated_delay}"
    )
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    """Build CLI and environment defaults for Qt script runs."""

    def env_path(name: str) -> Path | None:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        return None if not value else Path(value)

    def env_int(name: str, default: int | None = None) -> int | None:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        if value is None or value == "":
            return default
        return int(value, 0)

    def env_bool(name: str, default: bool = False) -> bool:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    parser = argparse.ArgumentParser(
        description="Prime second-half savestates from existing first-half FR/LG save files.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--save-dir", type=Path, default=env_path("SAVE_DIR") or DEFAULT_SAVE_DIR)
    parser.add_argument("--output-dir", type=Path, default=env_path("OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)
    parser.add_argument("--metadata", type=Path, default=env_path("METADATA") or DEFAULT_METADATA_PATH)
    parser.add_argument("--reset-tape", type=Path, default=env_path("RESET_TAPE"))
    parser.add_argument("--pre-input-tape", type=Path, default=env_path("PRE_INPUT_TAPE"))
    parser.add_argument(
        "--second-step-tape",
        type=Path,
        default=env_path("SECOND_STEP_TAPE") or DEFAULT_SECOND_STEP_TAPE,
    )
    parser.add_argument(
        "--target-frame-from-seed",
        type=int,
        default=env_int("TARGET_FRAME_FROM_SEED", DEFAULT_TARGET_FRAME_FROM_SEED),
    )
    parser.add_argument(
        "--expected-save-count",
        type=int,
        default=env_int("EXPECTED_SAVE_COUNT", DEFAULT_EXPECTED_SAVE_COUNT),
    )
    parser.add_argument(
        "--require-expected-save-count",
        action="store_true",
        default=env_bool("REQUIRE_EXPECTED_SAVE_COUNT"),
    )
    parser.add_argument(
        "--expected-rng-at-target",
        type=lambda text: int(text, 0),
        default=env_int("EXPECTED_RNG_AT_TARGET"),
    )
    parser.add_argument(
        "--calibrated-seed-delay",
        type=int,
        default=env_int("CALIBRATED_SEED_DELAY"),
        help="Known source-save title delay for the target seed. If omitted, learn it from the first unskipped save.",
    )
    parser.add_argument(
        "--seed-delay-search-start",
        type=int,
        default=env_int("SEED_DELAY_SEARCH_START", DEFAULT_SEED_DELAY_SEARCH_START),
    )
    parser.add_argument(
        "--seed-delay-search-end",
        type=int,
        default=env_int("SEED_DELAY_SEARCH_END", DEFAULT_SEED_DELAY_SEARCH_END),
    )
    parser.add_argument(
        "--seed-observe-timeout",
        type=int,
        default=env_int("SEED_OBSERVE_TIMEOUT", DEFAULT_SEED_OBSERVE_TIMEOUT),
    )
    parser.add_argument("--retry-count", type=int, default=env_int("RETRY_COUNT", DEFAULT_RETRY_COUNT))
    parser.add_argument("--rng-drift-window", type=int, default=env_int("RNG_DRIFT_WINDOW", DEFAULT_RNG_DRIFT_WINDOW))
    parser.add_argument("--limit", type=int, default=env_int("LIMIT"))
    parser.add_argument("--start-hex", type=lambda text: int(text, 0), default=env_int("START_HEX"))
    parser.add_argument("--end-hex", type=lambda text: int(text, 0), default=env_int("END_HEX"))
    parser.add_argument("--overwrite", action="store_true", default=env_bool("OVERWRITE"))
    parser.add_argument("--dry-run", action="store_true", default=env_bool("DRY_RUN"))
    parser.add_argument("--progress-every", type=int, default=env_int("PROGRESS_EVERY", DEFAULT_PROGRESS_EVERY))
    parser.add_argument("--status-every", type=int, default=env_int("STATUS_EVERY", DEFAULT_STATUS_EVERY))
    parser.add_argument("--stop-on-error", action="store_true", default=env_bool("STOP_ON_ERROR"))
    parser.add_argument(
        "--no-speed-toggles",
        action="store_true",
        default=env_bool("NO_SPEED_TOGGLES"),
        help="Do not enable live audio-kill/no-render/unbounded fast-forward toggles.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> PrimeConfig:
    """Convert parsed CLI args into validated config."""

    if args.target_frame_from_seed < 0:
        raise ValueError("--target-frame-from-seed must be >= 0")
    if args.retry_count < 1:
        raise ValueError("--retry-count must be >= 1")
    if args.rng_drift_window < 0:
        raise ValueError("--rng-drift-window must be >= 0")
    if args.calibrated_seed_delay is not None and args.calibrated_seed_delay < 0:
        raise ValueError("--calibrated-seed-delay must be >= 0")
    if args.seed_delay_search_start < 0:
        raise ValueError("--seed-delay-search-start must be >= 0")
    if args.seed_delay_search_end < args.seed_delay_search_start:
        raise ValueError("--seed-delay-search-end must be >= --seed-delay-search-start")
    if args.seed_observe_timeout < 1:
        raise ValueError("--seed-observe-timeout must be >= 1")
    if args.limit is not None and args.limit < 1:
        raise ValueError("--limit must be >= 1 when provided")
    if args.start_hex is not None and not 0 <= args.start_hex <= 0xFFFF:
        raise ValueError("--start-hex must fit in 16 bits")
    if args.end_hex is not None and not 0 <= args.end_hex <= 0xFFFF:
        raise ValueError("--end-hex must fit in 16 bits")
    if args.start_hex is not None and args.end_hex is not None and args.start_hex > args.end_hex:
        raise ValueError("--start-hex must be <= --end-hex")

    return PrimeConfig(
        save_dir=_display_path(args.save_dir),
        output_dir=_display_path(args.output_dir),
        metadata_path=_display_path(args.metadata),
        reset_tape_path=None if args.reset_tape is None else _display_path(args.reset_tape),
        pre_input_tape_path=None if args.pre_input_tape is None else _display_path(args.pre_input_tape),
        second_step_tape_path=_display_path(args.second_step_tape),
        target_frame_from_seed=args.target_frame_from_seed,
        expected_save_count=args.expected_save_count,
        require_expected_save_count=args.require_expected_save_count,
        expected_rng_at_target=args.expected_rng_at_target,
        calibrated_seed_delay_frames=args.calibrated_seed_delay,
        seed_delay_search_start=args.seed_delay_search_start,
        seed_delay_search_end=args.seed_delay_search_end,
        seed_observe_timeout=args.seed_observe_timeout,
        retry_count=args.retry_count,
        rng_drift_window=args.rng_drift_window,
        limit=args.limit,
        start_hex=args.start_hex,
        end_hex=args.end_hex,
        overwrite=args.overwrite,
        dry_run=args.dry_run,
        progress_every=max(1, args.progress_every),
        status_every=max(1, args.status_every),
        stop_on_error=args.stop_on_error,
        speed_toggles=not args.no_speed_toggles,
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    return run_prime(config)


if __name__ == "__main__":
    raise SystemExit(main())
