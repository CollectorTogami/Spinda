#!/usr/bin/env python3
r"""Brute-force the FR/LG visible Trainer ID from the final A input.

This is a new bot, intentionally separate from the older initial-seed
brute-forcers in ``frlg-seed-bruteforce``.

Assumption:

- the emulator starts at the exact final-input point before player-name exit
- no physical button is held; the bot still gives FR/LG one neutral frame by
  default so the game can see a fresh ``A`` edge
- pressing ``A`` lets ``SeedRngAndSetTrainerId()`` run
- Timer 1 is running at the branch point

The bot captures that branch point once, then brute-forces delay frames before
the final ``A``. Before pressing, it runs a configurable neutral release window
because FR/LG menu input is edge-triggered. It uses a rolling no-input savestate
so each miss advances one frame instead of replaying all previous frames from
the original anchor. The observed target is the temporary TID mirror written by
FR/LG at ``0x02020000`` after Timer 1 stops. In Qt mode it enables audio
killswitch, no-render mode, and unbounded fast-forward when available, then
pauses the visible core after the hit state is saved. Default target is ``0``.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXAMPLES_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PYTHON_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_EXAMPLES_DIR))

import frlg_id_bot_common as common  # noqa: E402
import input_tape  # noqa: E402


DEFAULT_TARGET_TID = 0
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "FRLGIDBots" / "TrainerID"
DEFAULT_METADATA_NAME = "tid-0x{tid:04X}-hit-metadata.json"
DEFAULT_SUCCESS_STATE_NAME = "tid-0x{tid:04X}-hit.ss0"
DEFAULT_ROLLING_STATE_NAME = "_tid-bruteforce-rolling.ss0"


@dataclass(frozen=True)
class HitOutputPaths:
    """Resolved hit output paths for one run."""

    success_state: Path
    metadata: Path
    collision_policy: str


@dataclass(frozen=True)
class RollingCheckpoint:
    """Storage choice for the hot-loop no-input checkpoint."""

    use_scratch: bool
    path: Path | None = None

    def label(self) -> str:
        """Return a readable result label."""

        return "qt-scratch" if self.use_scratch else str(self.path)


@dataclass(frozen=True)
class TrainerIdConfig:
    """Runtime configuration for one TID brute-force run."""

    output_dir: Path
    target_tid: int
    start_delay: int
    max_delay: int
    press_frames: int
    tid_timeout_frames: int
    anchor_state: Path | None
    success_state: Path | None
    progress_every: int
    overwrite: bool
    pre_press_neutral_frames: int = 1

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / DEFAULT_METADATA_NAME.format(tid=self.target_tid)

    @property
    def success_state_path(self) -> Path:
        if self.success_state is not None:
            return self.success_state
        return self.output_dir / DEFAULT_SUCCESS_STATE_NAME.format(tid=self.target_tid)

    @property
    def rolling_state_path(self) -> Path:
        return self.output_dir / DEFAULT_ROLLING_STATE_NAME


def hit_output_paths(config: TrainerIdConfig) -> HitOutputPaths:
    """Return hit paths; TID bot intentionally overwrites prior hit outputs."""

    return HitOutputPaths(
        success_state=config.success_state_path,
        metadata=config.metadata_path,
        collision_policy="overwrite",
    )


@dataclass(frozen=True)
class TrainerIdAttempt:
    """One attempted final-A delay."""

    delay_frames: int
    observed_tid: int
    timer1_low: int
    frames_after_press: int
    success: bool

    def to_json(self) -> dict[str, Any]:
        """Return a readable JSON attempt summary."""

        return {
            "delay_frames": self.delay_frames,
            "observed_tid": common.format_u16(self.observed_tid),
            "timer1_low": common.format_u16(self.timer1_low),
            "frames_after_press": self.frames_after_press,
            "success": self.success,
        }


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--target-tid",
        type=common.parse_int,
        default=DEFAULT_TARGET_TID,
        help="16-bit visible Trainer ID target. Defaults to 0.",
    )
    parser.add_argument("--start-delay", type=int, default=0)
    parser.add_argument("--max-delay", type=int, default=500_000_000)
    parser.add_argument(
        "--press-frames",
        type=int,
        default=2,
        help="Frames to hold the final A input. Defaults to 2 for edge-triggered menus.",
    )
    parser.add_argument(
        "--pre-press-neutral-frames",
        type=int,
        default=1,
        help="Neutral frames before A so FR/LG sees a fresh press edge. Use 0 only from a proven neutral branch.",
    )
    parser.add_argument(
        "--tid-timeout-frames",
        type=int,
        default=300,
        help="Frames after A to wait for Timer 1 to stop and TID to appear.",
    )
    parser.add_argument(
        "--anchor-state",
        type=Path,
        default=None,
        help="Optional savestate at the final-A branch point. Omit in Qt scratch mode.",
    )
    parser.add_argument(
        "--success-state",
        type=Path,
        default=None,
        help="Optional output savestate path for the hit.",
    )
    parser.add_argument("--progress-every", type=int, default=1_000)
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Compatibility flag. Hit outputs are always overwritten.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> TrainerIdConfig:
    """Normalize parsed args."""

    target_tid = common.checked_u16(args.target_tid, name="target_tid")
    if int(args.start_delay) < 0:
        raise ValueError("--start-delay must be non-negative")
    if int(args.max_delay) < int(args.start_delay):
        raise ValueError("--max-delay must be >= --start-delay")
    if int(args.press_frames) < 1:
        raise ValueError("--press-frames must be positive")
    if int(args.pre_press_neutral_frames) < 0:
        raise ValueError("--pre-press-neutral-frames must be non-negative")
    if int(args.tid_timeout_frames) < 1:
        raise ValueError("--tid-timeout-frames must be positive")

    return TrainerIdConfig(
        output_dir=Path(args.output_dir),
        target_tid=target_tid,
        start_delay=int(args.start_delay),
        max_delay=int(args.max_delay),
        press_frames=int(args.press_frames),
        tid_timeout_frames=int(args.tid_timeout_frames),
        anchor_state=Path(args.anchor_state) if args.anchor_state else None,
        success_state=Path(args.success_state) if args.success_state else None,
        progress_every=max(1, int(args.progress_every)),
        overwrite=bool(args.overwrite),
        pre_press_neutral_frames=int(args.pre_press_neutral_frames),
    )


def current_qt_core() -> Any:
    """Return the visible Qt core."""

    try:
        import mgba.qt  # type: ignore
    except Exception as exc:  # noqa: BLE001 - command failure should be explicit.
        raise RuntimeError("mgba.qt is unavailable; run inside the Python-enabled Qt build") from exc
    return mgba.qt.current_core()


def save_state_file(core: Any, path: Path) -> None:
    """Save a file-backed mGBA savestate through the current core wrapper."""

    save = getattr(core, "save_state_file", None)
    if not callable(save):
        raise RuntimeError("current core does not expose save_state_file")
    path.parent.mkdir(parents=True, exist_ok=True)
    save(path)


def load_state_file(core: Any, path: Path) -> None:
    """Load a file-backed mGBA savestate through the current core wrapper."""

    load = getattr(core, "load_state_file", None)
    if not callable(load):
        raise RuntimeError("current core does not expose load_state_file")
    load(path)


def optional_bool_attr(core: Any, name: str) -> bool | None:
    """Read a boolean core property/method when that bridge hook exists."""

    try:
        value = getattr(core, name)
    except Exception:  # noqa: BLE001 - bridge properties can fail when unavailable.
        return None
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001 - treat failed probes as unsupported.
            return None
    return bool(value)


def ensure_core_toggle(core: Any, *, getter_name: str, setter_name: str) -> str:
    """Ensure one optional Qt boolean feature is enabled."""

    enabled = optional_bool_attr(core, getter_name)
    if enabled is True:
        return "already-enabled"
    setter = getattr(core, setter_name, None)
    if not callable(setter):
        return "unavailable"
    setter(True)
    return "enabled"


def configure_qt_runtime_for_bruteforce(core: Any) -> dict[str, str]:
    """Enable speed-friendly Qt options when the visible-core bridge exposes them."""

    settings = {
        "audio_killswitch": ensure_core_toggle(
            core,
            getter_name="audio_killswitch_enabled",
            setter_name="set_audio_killswitch",
        ),
        "no_render_mode": ensure_core_toggle(
            core,
            getter_name="no_render_mode_enabled",
            setter_name="set_no_render_mode",
        ),
        "fast_forward": ensure_core_toggle(
            core,
            getter_name="fast_forward_enabled",
            setter_name="set_fast_forward",
        ),
        "fast_forward_ratio": "unavailable",
    }
    set_ratio = getattr(core, "set_fast_forward_ratio", None)
    if callable(set_ratio):
        set_ratio(-1.0)
        settings["fast_forward_ratio"] = "unbounded"
    return settings


def pause_core_after_hit(core: Any) -> str:
    """Pause the visible core after the hit state is safely written."""

    if optional_bool_attr(core, "paused") is True:
        return "already-paused"
    pause = getattr(core, "pause", None)
    if not callable(pause):
        return "unavailable"
    pause()
    return "paused"


def capture_anchor_state(config: TrainerIdConfig, core: Any) -> None:
    """Capture or validate the final-A branch state."""

    if config.anchor_state is not None:
        if not config.anchor_state.exists():
            raise FileNotFoundError(config.anchor_state)
        return
    save_scratch = getattr(core, "save_scratch_state", None)
    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(save_scratch) or not callable(load_scratch):
        raise RuntimeError("current core must expose save_scratch_state/load_scratch_state")
    save_scratch()


def restore_anchor_state(config: TrainerIdConfig, core: Any) -> None:
    """Restore the final-A branch state."""

    if config.anchor_state is not None:
        load_state_file(core, config.anchor_state)
        input_tape.set_exact_keys(core, 0)
        return
    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(load_scratch):
        raise RuntimeError("current core does not expose load_scratch_state")
    load_scratch()
    input_tape.set_exact_keys(core, 0)


def core_supports_scratch_state(core: Any) -> bool:
    """Return true when the core has fast in-memory scratch savestates."""

    return callable(getattr(core, "save_scratch_state", None)) and callable(
        getattr(core, "load_scratch_state", None)
    )


def run_neutral_frames(core: Any, frames: int) -> None:
    """Advance exact neutral frames."""

    if int(frames) < 0:
        raise ValueError("frames must be non-negative")
    input_tape.run_exact_frames(core, 0, int(frames), use_batch=True)


def press_final_a(core: Any, *, frames: int, pre_neutral_frames: int) -> None:
    """Give the game a neutral edge window, then press final A and release it."""

    run_neutral_frames(core, int(pre_neutral_frames))
    mask = input_tape.mask_from_buttons("A")
    input_tape.run_exact_frames(core, mask, int(frames), use_batch=True)
    input_tape.set_exact_keys(core, 0)


def wait_for_tid_after_press(core: Any, timeout_frames: int) -> tuple[int, int, int]:
    """Wait until Timer 1 stops, then return TID, Timer 1 low, and wait frames."""

    for frames_after_press in range(int(timeout_frames) + 1):
        if not common.timer1_running(core):
            return (
                common.read_initial_tid_mirror(core),
                common.read_timer1_low(core),
                frames_after_press,
            )
        input_tape.run_exact_frames(core, 0, 1, use_batch=True)
    raise TimeoutError(f"TID did not appear within {timeout_frames} frame(s) after A")


def attempt_trainer_id(
    config: TrainerIdConfig,
    core: Any,
    *,
    delay_frames: int,
    recorded_delay_frames: int | None = None,
) -> TrainerIdAttempt:
    """Run one final-A delay attempt from an already restored branch."""

    if not common.timer1_running(core):
        raise RuntimeError("Timer 1 is not running; this is not the pre-TID final-A branch")
    run_neutral_frames(core, delay_frames)
    press_final_a(
        core,
        frames=config.press_frames,
        pre_neutral_frames=config.pre_press_neutral_frames,
    )
    try:
        observed_tid, timer1_low, frames_after_press = wait_for_tid_after_press(
            core,
            config.tid_timeout_frames,
        )
    except TimeoutError as exc:
        raise TimeoutError(
            "TID did not appear after final A. FR/LG did not accept the press from this "
            f"branch using pre_press_neutral_frames={config.pre_press_neutral_frames}, "
            f"press_frames={config.press_frames}, timeout_frames={config.tid_timeout_frames}. "
            "Try --pre-press-neutral-frames 2 or --press-frames 4, and verify the branch "
            "is exactly before the final A that exits naming."
        ) from exc
    return TrainerIdAttempt(
        delay_frames=int(recorded_delay_frames if recorded_delay_frames is not None else delay_frames),
        observed_tid=observed_tid,
        timer1_low=timer1_low,
        frames_after_press=frames_after_press,
        success=observed_tid == config.target_tid,
    )


def save_rolling_checkpoint(
    core: Any,
    *,
    neutral_frames: int,
    checkpoint: RollingCheckpoint,
) -> None:
    """Persist the no-input branch for the next delay.

    The brute-force scan must not replay ``delay`` neutral frames from the
    original anchor for every attempt. A rolling checkpoint makes the scan
    linear in frame count. Qt scratch state is preferred for the hot loop; the
    file path is the fallback when scratch state is unavailable.
    """

    if neutral_frames < 0:
        raise ValueError("neutral_frames must be non-negative")
    run_neutral_frames(core, neutral_frames)
    if checkpoint.use_scratch:
        core.save_scratch_state()
        return
    if checkpoint.path is None:
        raise RuntimeError("file-backed rolling checkpoint has no path")
    save_state_file(core, checkpoint.path)


def prepare_rolling_checkpoint(config: TrainerIdConfig, core: Any) -> RollingCheckpoint:
    """Create the first rolling no-input checkpoint for ``start_delay``."""

    use_scratch = core_supports_scratch_state(core)
    checkpoint = RollingCheckpoint(
        use_scratch=use_scratch,
        path=None if use_scratch else config.rolling_state_path,
    )
    restore_anchor_state(config, core)
    save_rolling_checkpoint(
        core,
        neutral_frames=config.start_delay,
        checkpoint=checkpoint,
    )
    return checkpoint


def load_rolling_checkpoint(core: Any, checkpoint: RollingCheckpoint) -> None:
    """Load the current rolling no-input checkpoint and clear held keys."""

    if checkpoint.use_scratch:
        core.load_scratch_state()
    else:
        if checkpoint.path is None:
            raise RuntimeError("file-backed rolling checkpoint has no path")
        load_state_file(core, checkpoint.path)
    input_tape.set_exact_keys(core, 0)


def advance_rolling_checkpoint_one_frame(
    core: Any,
    checkpoint: RollingCheckpoint,
) -> None:
    """Advance the rolling no-input checkpoint by one neutral frame."""

    load_rolling_checkpoint(core, checkpoint)
    save_rolling_checkpoint(core, neutral_frames=1, checkpoint=checkpoint)


def write_hit_metadata(
    config: TrainerIdConfig,
    *,
    attempt: TrainerIdAttempt,
    elapsed_seconds: float,
    runtime_settings: dict[str, str],
    pause_state: str,
    hit_paths: HitOutputPaths,
) -> None:
    """Persist the hit contract next to the success savestate."""

    payload = {
        "format": "frlg-trainer-id-bruteforce-hit-v1",
        "target_tid": common.format_u16(config.target_tid),
        "final_input": "A",
        "pre_press_neutral_frames": config.pre_press_neutral_frames,
        "press_frames": config.press_frames,
        "success_state": str(hit_paths.success_state),
        "collision_policy": hit_paths.collision_policy,
        "elapsed_seconds": round(elapsed_seconds, 3),
        "runtime_settings": runtime_settings,
        "pause_state": pause_state,
        "attempt": attempt.to_json(),
    }
    common.write_json_atomic(hit_paths.metadata, payload)


def brute_force_trainer_id(
    config: TrainerIdConfig,
    *,
    core: Any | None = None,
) -> dict[str, Any]:
    """Brute-force delay frames until the target TID is hit."""

    live_core = core if core is not None else current_qt_core()
    runtime_settings = configure_qt_runtime_for_bruteforce(live_core)
    config.output_dir.mkdir(parents=True, exist_ok=True)
    hit_paths = hit_output_paths(config)

    capture_anchor_state(config, live_core)
    rolling_checkpoint = prepare_rolling_checkpoint(config, live_core)
    started = time.monotonic()
    attempts = 0
    for delay in range(config.start_delay, config.max_delay + 1):
        load_rolling_checkpoint(live_core, rolling_checkpoint)
        attempt = attempt_trainer_id(
            config,
            live_core,
            delay_frames=0,
            recorded_delay_frames=delay,
        )
        attempts += 1
        if attempt.success:
            save_state_file(live_core, hit_paths.success_state)
            pause_state = pause_core_after_hit(live_core)
            elapsed = time.monotonic() - started
            write_hit_metadata(
                config,
                attempt=attempt,
                elapsed_seconds=elapsed,
                runtime_settings=runtime_settings,
                pause_state=pause_state,
                hit_paths=hit_paths,
            )
            return {
                "mode": "hit",
                "target_tid": common.format_u16(config.target_tid),
                "delay_frames": attempt.delay_frames,
                "observed_tid": common.format_u16(attempt.observed_tid),
                "attempts": attempts,
                "success_state": str(hit_paths.success_state),
                "metadata_path": str(hit_paths.metadata),
                "rolling_state": rolling_checkpoint.label(),
                "collision_policy": hit_paths.collision_policy,
                "runtime_settings": runtime_settings,
                "pause_state": pause_state,
                "elapsed_seconds": round(elapsed, 3),
            }
        if delay != config.max_delay:
            advance_rolling_checkpoint_one_frame(live_core, rolling_checkpoint)
        if attempts % config.progress_every == 0:
            print(
                "progress "
                f"attempts={attempts} delay={delay} "
                f"observed_tid={common.format_u16(attempt.observed_tid)}",
                flush=True,
            )

    raise RuntimeError(
        f"Target TID {common.format_u16(config.target_tid)} not found in "
        f"delay range {config.start_delay}..{config.max_delay}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    config = config_from_args(build_parser().parse_args(argv))
    result = brute_force_trainer_id(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    # mGBA's embedded runner logs SystemExit(0) as an error, so return normally.
    main()
