#!/usr/bin/env python3
r"""Press the FR/LG SID-generation input, then pause for tape setup.

This is a setup helper, not the save-bank exporter. Start with the emulator
paused at the same pre-SID final-input point used by
``Secret-ID-Shiny-Value-Bot.py``. The helper presses the final input, waits for
SID generation to settle, pauses the visible Qt core, and prints the observed
TID/SID when SaveBlock2 is readable.

It intentionally does not care which SID is generated. Its purpose is to leave
the game paused immediately after SID generation so ``SiD_RNG_After.json`` can
be recorded from the correct post-SID anchor.
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
DEFAULT_POST_COMMIT_FRAMES = 360


@dataclass(frozen=True)
class SidPauseConfig:
    """Runtime configuration for one post-SID setup pause."""

    target_tid: int
    final_button: str
    final_press_frames: int
    final_pre_neutral_frames: int
    post_commit_frames: int
    skip_tid_check: bool
    keep_no_render_on: bool


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--target-tid",
        type=common.parse_int,
        default=DEFAULT_TARGET_TID,
        help="Expected pre-SID TID mirror. Defaults to 0.",
    )
    parser.add_argument("--final-button", default="A")
    parser.add_argument(
        "--final-press-frames",
        type=int,
        default=2,
        help="Frames to hold the SID-generation input.",
    )
    parser.add_argument(
        "--final-pre-neutral-frames",
        type=int,
        default=0,
        help="Neutral frames before final input if this branch needs a release edge.",
    )
    parser.add_argument(
        "--post-commit-frames",
        type=int,
        default=DEFAULT_POST_COMMIT_FRAMES,
        help=(
            "Frames after final input before pausing and reading final IDs. "
            "Defaults to 360 because the root TID-0-ready state updates IDs "
            "around post-frame 272."
        ),
    )
    parser.add_argument(
        "--skip-tid-check",
        action="store_true",
        help="Do not require the pre-SID TID mirror to match --target-tid.",
    )
    parser.add_argument(
        "--keep-no-render-on",
        action="store_true",
        help="Leave no-render enabled after pausing. Default turns it back off for visual tape setup.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> SidPauseConfig:
    """Normalize parsed args."""

    target_tid = common.checked_u16(args.target_tid, name="target_tid")
    if int(args.final_press_frames) < 1:
        raise ValueError("--final-press-frames must be positive")
    if int(args.final_pre_neutral_frames) < 0:
        raise ValueError("--final-pre-neutral-frames must be non-negative")
    if int(args.post_commit_frames) < 0:
        raise ValueError("--post-commit-frames must be non-negative")
    return SidPauseConfig(
        target_tid=target_tid,
        final_button=str(args.final_button),
        final_press_frames=int(args.final_press_frames),
        final_pre_neutral_frames=int(args.final_pre_neutral_frames),
        post_commit_frames=int(args.post_commit_frames),
        skip_tid_check=bool(args.skip_tid_check),
        keep_no_render_on=bool(args.keep_no_render_on),
    )


def current_qt_core() -> Any:
    """Return the visible Qt core."""

    try:
        import mgba.qt  # type: ignore
    except Exception as exc:  # noqa: BLE001 - command failure should be explicit.
        raise RuntimeError("mgba.qt is unavailable; run inside the Python-enabled Qt build") from exc
    return mgba.qt.current_core()


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


def configure_qt_runtime(core: Any) -> dict[str, str]:
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


def reveal_render_for_tape_setup(core: Any, *, keep_no_render_on: bool) -> str:
    """Disable no-render after pausing unless the caller asked to keep it."""

    if keep_no_render_on:
        return "kept-on"
    setter = getattr(core, "set_no_render_mode", None)
    if not callable(setter):
        return "unavailable"
    setter(False)
    return "disabled"


def pause_core(core: Any) -> str:
    """Pause the visible core when the Qt bridge exposes a pause hook."""

    if optional_bool_attr(core, "paused") is True:
        return "already-paused"
    pause = getattr(core, "pause", None)
    if not callable(pause):
        return "unavailable"
    pause()
    return "paused"


def verify_target_tid(config: SidPauseConfig, core: Any) -> None:
    """Check the live pre-SID TID mirror unless disabled."""

    if config.skip_tid_check:
        return
    observed = common.read_initial_tid_mirror(core)
    if observed != config.target_tid:
        raise RuntimeError(
            f"Pre-SID TID mirror is {common.format_u16(observed)}, "
            f"expected {common.format_u16(config.target_tid)}"
        )


def run_neutral_frames(core: Any, frames: int) -> None:
    """Run neutral-input frames."""

    if int(frames) < 0:
        raise ValueError("frames must be non-negative")
    input_tape.run_exact_frames(core, 0, int(frames), use_batch=True)


def commit_sid_generation(config: SidPauseConfig, core: Any) -> None:
    """Press the SID-generation input and wait at the post-SID point."""

    run_neutral_frames(core, config.final_pre_neutral_frames)
    mask = input_tape.mask_from_buttons(config.final_button)
    input_tape.run_exact_frames(core, mask, config.final_press_frames, use_batch=True)
    input_tape.set_exact_keys(core, 0)
    run_neutral_frames(core, config.post_commit_frames)


def read_ids_best_effort(core: Any) -> dict[str, str]:
    """Return final IDs if SaveBlock2 is readable."""

    try:
        tid, sid = common.read_trainer_id_from_saveblock2(core)
    except Exception as exc:  # noqa: BLE001 - setup helper should still pause.
        return {"id_read_error": str(exc)}
    return {
        "final_tid": common.format_u16(tid),
        "final_sid": common.format_u16(sid),
        "final_shiny_value": common.format_shiny_value(
            common.shiny_value_from_tid_sid(tid, sid)
        ),
    }


def pause_after_sid_generation(
    config: SidPauseConfig,
    *,
    core: Any | None = None,
) -> dict[str, Any]:
    """Run the setup helper and return a JSON-serializable summary."""

    live_core = core if core is not None else current_qt_core()
    started = time.monotonic()
    runtime_settings = configure_qt_runtime(live_core)
    verify_target_tid(config, live_core)
    try:
        branch_rng = common.format_u32(common.read_rng_state(live_core))
    except Exception:  # noqa: BLE001 - older states may not expose gRngValue.
        branch_rng = "unreadable"

    commit_sid_generation(config, live_core)
    final_ids = read_ids_best_effort(live_core)
    pause_state = pause_core(live_core)
    setup_render_mode = reveal_render_for_tape_setup(
        live_core,
        keep_no_render_on=config.keep_no_render_on,
    )

    result: dict[str, Any] = {
        "mode": "sid-generated-paused",
        "target_tid": common.format_u16(config.target_tid),
        "branch_rng": branch_rng,
        "final_button": config.final_button,
        "final_press_frames": config.final_press_frames,
        "final_pre_neutral_frames": config.final_pre_neutral_frames,
        "post_commit_frames": config.post_commit_frames,
        "runtime_settings": runtime_settings,
        "pause_state": pause_state,
        "setup_render_mode": setup_render_mode,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }
    result.update(final_ids)
    return result


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    config = config_from_args(build_parser().parse_args(argv))
    result = pause_after_sid_generation(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    # mGBA's embedded runner logs SystemExit(0) as an error, so return normally.
    main()
