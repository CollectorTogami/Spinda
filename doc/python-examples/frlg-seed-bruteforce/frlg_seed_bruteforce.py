r"""Brute-force an FR/LG initial seed from the title screen.

This script works in two modes in this workspace:

- host-side Python from the dedicated `mgba` venv
- startup Python inside the visible Qt GUI build

It is meant to be readable first and fast second.

If you know the basics of Pokemon Gen 3 RNG, the idea is simple:

1. Boot LeafGreen with no menu input.
2. Press the first title-screen `A` automatically.
3. Stop at the checkpoint just before the second title-screen `A`.
4. Save earlier title baseline plus untouched baseline plus rolling checkpoint.
5. Treat rolling checkpoint as no-input state instead of restarting from
   opening every time.
6. If one `RUN/state=1` lane exhausts, rebuild fresh lane from earlier title
   baseline by waiting more neutral frames before first title `A`.
7. For each delay value, reload current rolling checkpoint, try `A` first and
   `Start` second on same frame, then advance checkpoint by one neutral frame
   and save it back for next delay. If rolling copy drifts out of legal
   pre-seed title window, rebuild it from untouched baseline instead of
   replaying opening from scratch.
8. When game generates seed, read stable 16-bit copy from
   `gTrainerId`.
9. If seed matches target, save `seed####done.sav`.

Why `gTrainerId` instead of `gRngValue`?

`gRngValue` starts advancing immediately after seeding, so it is not a stable
"what seed did I hit?" value. FR/LG also copies the 16-bit startup seed into
`gTrainerId`, which stays fixed and is much easier to compare.

Quick edit:

If you usually run this script without command-line arguments, change
`DEFAULT_TARGET_SEED` near the top of the file.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe frlg_seed_bruteforce.py
    <repo-root>\.venv-mgba\bin\python.exe frlg_seed_bruteforce.py --target 0xFBC7 --max-delay 500000000

Custom workspace addition:

This same script can also run inside the visible Qt GUI in this workspace via
`mGBA.exe --script ...` or the sibling `run_frlg_seed_bruteforce_visible.ps1`
launcher. In that mode, `MGBA_*` environment variables can override the normal
defaults without changing mGBA's own command-line parsing.

The `.sav` files created here are mGBA savestates, even though the filenames
use `.sav` because that was the requested naming scheme.
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path

from mgba._pylib import lib
import mgba.core
import mgba.log
import mgba.vfs
from mgba.gba import GBA

try:
    import mgba.qt as mgba_qt
except ImportError:  # pragma: no cover - host-only build fallback
    mgba_qt = None


DEFAULT_ROM = Path(__file__).with_name("lg.gba")
# Quick-edit defaults.
# Change DEFAULT_TARGET_SEED if you want a different target without passing
# --target on the command line.
DEFAULT_TARGET_SEED = 0xFBC7
DEFAULT_MAX_DELAY = 500_000_000
DEFAULT_SETTLE_FRAMES = 3
DEFAULT_SEED_TIMEOUT = 240
DEFAULT_FIRST_A_START_DELAY_START = 0
DEFAULT_FIRST_A_START_DELAY_MAX = 0
DEFAULT_PROGRESS_EVERY = 50

GTRAINER_ID_ADDR = 0x02020000
GRNG_VALUE_ADDR = 0x03005000
GMAIN_VBLANK2_ADDR = 0x03003114
GTASKS_ADDR = 0x03005090
TASK_SIZE = 0x28
TASK_COUNT = 16
TASK_TITLE_SCREEN_MAIN = 0x08078C24 | 1
TITLESCENE_RUN = 3
SAVE_STATE_FLAGS = 0
TITLE_INPUT_ATTEMPTS = (
    ("A", GBA.KEY_A),
    ("Start", GBA.KEY_START),
)


def run_frames_fast(core: GBA, frames: int) -> None:
    """Advance a fixed frame count with the fastest path the current core has.

    The visible Qt bridge in this workspace exposes a native `run_frames`
    method. Host-side cores still only provide `run_frame`, so fall back to the
    portable one-frame loop there.
    """

    if frames <= 0:
        return
    run_frames = getattr(core, "run_frames", None)
    if callable(run_frames):
        run_frames(frames)
        return
    for _ in range(frames):
        core.run_frame()


def _env_default_int(name: str, fallback: int) -> int:
    """Read one optional integer environment override.

    The Qt launcher uses environment variables because `--script` runs inside
    mGBA's own process, where `sys.argv` belongs to the emulator rather than to
    this script.
    """

    value = os.environ.get(name)
    if not value:
        return fallback
    return int(value, 0)


def _env_default_seed(name: str, fallback: int) -> int:
    """Read one 16-bit seed override from the environment."""

    value = _env_default_int(name, fallback)
    if not 0 <= value <= 0xFFFF:
        raise SystemExit(f"{name} must fit in 16 bits.")
    return value


def _env_default_rom() -> str:
    """Return the launcher-provided ROM path, or the local `lg.gba` fallback."""

    return os.environ.get("MGBA_ROM_PATH", str(DEFAULT_ROM))


@dataclass(frozen=True)
class TitleTaskState:
    """Small snapshot of FR/LG's title-screen task state.

    We only care about the task id, where it lives in memory, and the
    `scene/state` pair that tells us whether we are at the checkpoint before
    the second title-screen `A`.
    """

    task_id: int
    base: int
    scene: int
    state: int


def parse_target(text: str) -> int:
    """Parse a decimal or hex seed value and keep it within 16 bits."""

    value = int(text, 0)
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("Target seed must fit in 16 bits.")
    return value


def save_state_file(core: GBA, path: Path) -> None:
    """Write one file-backed mGBA savestate."""

    if hasattr(core, "save_state_file") and not hasattr(core, "_core"):
        core.save_state_file(path, SAVE_STATE_FLAGS)
        return

    vf = mgba.vfs.open_path(str(path), "w+")
    if not vf:
        raise SystemExit(f"Could not open savestate path for writing: {path}")
    try:
        if not save_state_named(core, vf.handle):
            raise SystemExit(f"mCoreSaveStateNamed(...) failed for {path}")
    finally:
        vf.close()


def load_state_file(core: GBA, path: Path) -> None:
    """Load one file-backed mGBA savestate."""

    if hasattr(core, "load_state_file") and not hasattr(core, "_core"):
        core.load_state_file(path, SAVE_STATE_FLAGS)
        return

    vf = mgba.vfs.open_path(str(path), "r")
    if not vf:
        raise SystemExit(f"Could not open savestate path for reading: {path}")
    try:
        if not load_state_named(core, vf.handle):
            raise SystemExit(f"mCoreLoadStateNamed(...) failed for {path}")
    finally:
        vf.close()


def save_state_named(core: GBA, vf_handle) -> bool:
    """Small wrapper around the native savestate call.

    This exists mostly so the unit tests can monkeypatch it without touching the
    raw CFFI `lib` object directly.
    """

    return lib.mCoreSaveStateNamed(core._core, vf_handle, SAVE_STATE_FLAGS)


def load_state_named(core: GBA, vf_handle) -> bool:
    """Small wrapper around the native savestate load call."""

    return lib.mCoreLoadStateNamed(core._core, vf_handle, SAVE_STATE_FLAGS)


def find_title_task(core: GBA) -> TitleTaskState | None:
    """Find FR/LG's title-screen task in `gTasks`.

    FR/LG's opening flow is driven by tasks in RAM. Once the title-screen task
    appears, we can inspect its `scene` and `state` values to know exactly when
    the second title-screen `A` is legal.
    """

    # FR/LG's title screen lives in the global task list. We use its
    # scene/state pair to detect the exact checkpoint before the second A press.
    for task_id in range(TASK_COUNT):
        base = GTASKS_ADDR + task_id * TASK_SIZE
        if core.memory.u8[base + 4] and core.memory.u32[base] == TASK_TITLE_SCREEN_MAIN:
            return TitleTaskState(
                task_id=task_id,
                base=base,
                scene=core.memory.u16[base + 8],
                state=core.memory.u16[base + 10],
            )
    return None


def boot_to_pre_second_press_checkpoint(core: GBA) -> TitleTaskState:
    """Boot the game to the frame window just before the second title `A`.

    In plain RNG terms, this function does the boring setup work:

    - power on/reset the ROM
    - wait until the title logic appears
    - press the first title-screen `A` automatically
    - wait until FR/LG reaches RUN/state=1

    That final RUN/state=1 point is the checkpoint we want, because every brute
    force attempt can start from the exact same place.
    """

    core.reset()
    core.set_keys(raw=0)

    while True:
        core.run_frame()
        info = find_title_task(core)
        if info is not None:
            print(
                "Title screen detected:"
                f" frame_counter={core.frame_counter}"
                f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={info.scene} state={info.state}"
            )
            break

    core.set_keys(core.KEY_A)
    core.run_frame()
    info = find_title_task(core)
    if info is None:
        raise SystemExit("Lost the title task immediately after the first title A press.")

    print(
        "Pressed first title A:"
        f" frame_counter={core.frame_counter}"
        f" scene={info.scene} state={info.state}"
    )

    # The first title A only advances the title task into the RUN scene.
    # The second seed-generating A is legal once RUN/state=1 is visible.
    for _ in range(120):
        core.set_keys(raw=0)
        core.run_frame()
        info = find_title_task(core)
        if info is None:
            raise SystemExit("Lost the title task while waiting for the checkpoint.")
        if info.scene == TITLESCENE_RUN and info.state == 1:
            print(
                "Checkpoint reached before second title A:"
                f" frame_counter={core.frame_counter}"
                f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={info.scene} state={info.state}"
            )
            return info

    raise SystemExit("Could not reach the pre-second-title-A checkpoint.")


def boot_to_title_detected_checkpoint(core: GBA) -> TitleTaskState:
    """Boot game only far enough for title task to exist."""

    core.reset()
    core.set_keys(raw=0)

    while True:
        core.run_frame()
        info = find_title_task(core)
        if info is not None:
            print(
                "Title screen detected:"
                f" frame_counter={core.frame_counter}"
                f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={info.scene} state={info.state}"
            )
            return info


def title_input_checkpoint_ready(core: GBA) -> bool:
    """Report whether the current state is still the legal pre-seed title window."""

    info = find_title_task(core)
    return info is not None and info.scene == TITLESCENE_RUN and info.state == 1


def build_pre_second_press_checkpoint_from_title_baseline(
    core: GBA,
    title_baseline_checkpoint_path: Path,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    first_a_start_delay: int,
) -> tuple[bool, str]:
    """Build one pre-second-title-input lane from earlier title baseline."""

    load_state_file(core, title_baseline_checkpoint_path)
    core.set_keys(raw=0)
    if first_a_start_delay:
        run_frames_fast(core, first_a_start_delay)

    info = find_title_task(core)
    if info is None:
        return False, "Lost title task before first title A."

    core.set_keys(core.KEY_A)
    core.run_frame()
    info = find_title_task(core)
    if info is None:
        return False, "Lost title task immediately after first title A."
    core.set_keys(raw=0)

    checkpoint_wait_frames = 0
    while not (info.scene == TITLESCENE_RUN and info.state == 1):
        core.run_frame()
        checkpoint_wait_frames += 1
        info = find_title_task(core)
        if info is None:
            return False, "Lost title task while waiting for pre-second-title-A checkpoint."
        if checkpoint_wait_frames > 120:
            return False, "Could not reach pre-second-title-A checkpoint within 120 neutral frames."

    save_state_file(core, baseline_checkpoint_path)
    save_state_file(core, checkpoint_path)
    return True, (
        f"first_a_start_delay={first_a_start_delay}"
        f" checkpoint_wait_frames={checkpoint_wait_frames}"
        f" frame_counter={getattr(core, 'frame_counter', 0)}"
    )


def rebuild_delay_checkpoint(
    core: GBA,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    delay_frames: int,
) -> bool:
    """Rebuild the rolling checkpoint from the untouched baseline."""

    load_state_file(core, baseline_checkpoint_path)
    core.set_keys(raw=0)
    run_frames_fast(core, delay_frames)
    if not title_input_checkpoint_ready(core):
        return False
    save_state_file(core, checkpoint_path)
    return True


def brute_force_attempt(
    core: GBA,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    delay_frames: int,
    button_name: str,
    button_key: int,
    settle_frames: int,
    seed_timeout: int,
) -> tuple[int, int, int, str]:
    """Try one delay value and report what seed it produced.

    Each attempt reloads the current rolling checkpoint state for
    `delay_frames`, then holds exactly one title input (`A` or `Start`) until
    FR/LG seeds the game.
    The returned tuple is:

    - observed 16-bit initial seed from `gTrainerId`
    - frame counter when that seed first appeared
    - current `gRngValue` after the optional settle buffer
    - the button name used for this attempt
    """

    retried_from_baseline = False

    while True:
        load_state_file(core, checkpoint_path)
        core.set_keys(raw=0)
        if not title_input_checkpoint_ready(core):
            if retried_from_baseline:
                raise RuntimeError(
                    "Checkpoint is no longer at the pre-second-title-A window"
                    f" for delay {delay_frames} using {button_name}."
                )
            print(
                "Rolling checkpoint drift detected before final input;"
                f" rebuilding delay {delay_frames} and retrying {button_name} once."
            )
            rebuilt = rebuild_delay_checkpoint(
                core,
                baseline_checkpoint_path,
                checkpoint_path,
                delay_frames,
            )
            if not rebuilt:
                raise RuntimeError(
                    "Rebuilding that exact delay checkpoint no longer lands in"
                    " the pre-second-title-A window."
                )
            retried_from_baseline = True
            continue

        core.set_keys(button_key)
        for _ in range(seed_timeout):
            core.run_frame()
            # SeedRngAndSetTrainerId copies the 16-bit initial seed into
            # gTrainerId once, making it a stable value to compare after the
            # transition.
            seed_value = core.memory.u16[GTRAINER_ID_ADDR]
            if seed_value:
                seed_frame = core.frame_counter
                run_frames_fast(core, settle_frames)
                return (
                    seed_value,
                    seed_frame,
                    core.memory.u32[GRNG_VALUE_ADDR],
                    button_name,
                )

        core.set_keys(raw=0)
        if retried_from_baseline:
            raise RuntimeError(
                f"Initial seed was not observed within {seed_timeout} frames"
                f" after delay {delay_frames} using {button_name}, even after"
                " rebuilding that exact delay from the untouched baseline."
            )
        print(
            "Title input attempt timed out;"
            f" rebuilding delay {delay_frames} and retrying {button_name} once."
        )
        rebuilt = rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path,
            checkpoint_path,
            delay_frames,
        )
        if not rebuilt:
            raise RuntimeError(
                "Rebuilding that exact delay checkpoint no longer lands in"
                " the pre-second-title-A window."
            )
        retried_from_baseline = True


def advance_checkpoint_one_frame(
    core: GBA,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    next_delay_frames: int,
) -> bool:
    """Promote the rolling no-input checkpoint by exactly one neutral frame."""

    load_state_file(core, checkpoint_path)
    core.set_keys(raw=0)
    if not title_input_checkpoint_ready(core):
        return rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path,
            checkpoint_path,
            next_delay_frames,
        )

    core.run_frame()
    if not title_input_checkpoint_ready(core):
        return rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path,
            checkpoint_path,
            next_delay_frames,
        )

    save_state_file(core, checkpoint_path)
    return True


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI.

    The script is easiest to tweak by editing the `DEFAULT_*` constants near the
    top of this file, but every important setting can also be overridden from
    the command line.
    """

    parser = argparse.ArgumentParser(
        description="Brute-force an FRLG initial seed from a pre-second-title-A checkpoint.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--target",
        type=parse_target,
        default=_env_default_seed("MGBA_TARGET_SEED", DEFAULT_TARGET_SEED),
        help="Desired 16-bit target seed. Accepts decimal or 0x-prefixed hex.",
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=_env_default_int("MGBA_MAX_DELAY", DEFAULT_MAX_DELAY),
        help="Maximum number of rolling checkpoint-delay frames to try before giving up.",
    )
    parser.add_argument(
        "--settle-frames",
        type=int,
        default=_env_default_int("MGBA_SETTLE_FRAMES", DEFAULT_SETTLE_FRAMES),
        help="How many extra frames to hold A after the seed appears before checking it.",
    )
    parser.add_argument(
        "--seed-timeout",
        type=int,
        default=_env_default_int("MGBA_SEED_TIMEOUT", DEFAULT_SEED_TIMEOUT),
        help="Maximum frames to hold A while waiting for the seed to appear in gTrainerId.",
    )
    parser.add_argument(
        "--first-a-start-delay-start",
        type=int,
        default=_env_default_int(
            "MGBA_FIRST_A_START_DELAY_START",
            DEFAULT_FIRST_A_START_DELAY_START,
        ),
        help="Minimum neutral-frame delay before first title A when rebuilding earlier title lanes.",
    )
    parser.add_argument(
        "--first-a-start-delay-max",
        type=int,
        default=_env_default_int(
            "MGBA_FIRST_A_START_DELAY_MAX",
            DEFAULT_FIRST_A_START_DELAY_MAX,
        ),
        help="Maximum neutral-frame delay before first title A when rebuilding earlier title lanes.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=_env_default_int("MGBA_PROGRESS_EVERY", DEFAULT_PROGRESS_EVERY),
        help="How often to print progress while brute-forcing delays.",
    )
    parser.add_argument(
        "--rom",
        default=_env_default_rom(),
        help="Path to the LeafGreen ROM. Defaults to lg.gba beside this script.",
    )
    return parser


def _qt_mode_enabled() -> bool:
    """Report whether the script is running inside the live Qt bridge."""

    if not mgba_qt:
        return False
    try:
        return bool(mgba_qt.is_available())
    except Exception:
        return False


def _parse_args(parser: argparse.ArgumentParser):
    """Parse CLI args normally, or use in-file defaults inside Qt runtime mode."""

    if _qt_mode_enabled():
        # When the script runs inside the Qt GUI, sys.argv belongs to mGBA
        # itself. Use the in-file defaults instead of trying to parse the
        # emulator's command line.
        return parser.parse_args([])
    return parser.parse_args()


def load_runtime_core(rom_path: Path):
    """Choose the live Qt core when available, otherwise load the ROM directly."""

    if not rom_path.is_file():
        raise SystemExit(f"ROM not found: {rom_path}")

    if _qt_mode_enabled():
        # Runtime scripting should not depend on whichever game was already
        # open in the window. Load the requested ROM into the visible core.
        if not mgba_qt.load_rom(rom_path):
            raise SystemExit(f"Could not load ROM into the visible Qt core: {rom_path}")
        return mgba_qt.current_core()

    mgba.log.silence()
    core = mgba.core.load_path(str(rom_path))
    if not core:
        raise SystemExit(f"Could not load ROM: {rom_path}")
    return core


def notify_success_in_qt(
    core: GBA,
    *,
    target_seed: int,
    observed_seed: int,
    delay_frames: int,
    button_name: str,
    seed_frame: int,
    done_path: Path,
) -> None:
    """Pause the visible Qt core and show a dark-mode success warning.

    This is a workspace-specific helper for the visible Qt GUI path. The host
    venv path does not have a live Qt window to pause or to attach a dialog to,
    so in that mode we simply return.
    """

    if not _qt_mode_enabled() or not mgba_qt:
        return

    # Clear A before pausing so the live window is left in a clean state if the
    # user resumes from the success dialog.
    core.set_keys(raw=0)
    if hasattr(core, "pause"):
        core.pause()
    else:
        mgba_qt.pause_current_core()

    title = "Desired Outcome Found"
    message = (
        "The emulator found the desired outcome.\n\n"
        f"Observed 16-bit seed hit: 0x{observed_seed:04X}\n"
        f"Target seed: 0x{target_seed:04X}\n"
        f"Delay searched: {delay_frames} frames\n"
        f"Final button press: {button_name}\n"
        f"Frame that hit the seed: {seed_frame}\n"
        f"Savestate: {done_path}"
    )
    print("Visible Qt core paused; showing success warning.")
    mgba_qt.show_warning(title, message)


def main() -> int:
    """Run the complete FR/LG seed search.

    Human-readable summary:

    - load the ROM
    - reach the pre-second-`A` checkpoint once
    - save `seed####titlebase.sav`, `seed####base.sav`, and `seed####test.sav`
    - brute-force delays from `0` to `max_delay`
    - if needed, rebuild new inner lane by waiting longer before first title A
    - for each delay, try `A` first and `Start` second from that checkpoint
    - reload, advance one neutral frame, and save back before next delay
    - rebuild rolling checkpoint from `seed####base.sav` only if it drifts
    - print progress as seeds are observed
    - save `seed####done.sav` if the desired seed is found
    """

    parser = build_parser()
    args = _parse_args(parser)

    if args.max_delay < 0:
        raise SystemExit("--max-delay must be non-negative.")
    if args.settle_frames < 0:
        raise SystemExit("--settle-frames must be non-negative.")
    if args.seed_timeout <= 0:
        raise SystemExit("--seed-timeout must be positive.")
    if args.first_a_start_delay_start < 0:
        raise SystemExit("--first-a-start-delay-start must be non-negative.")
    if args.first_a_start_delay_max < args.first_a_start_delay_start:
        raise SystemExit(
            "--first-a-start-delay-max must be greater than or equal to"
            " --first-a-start-delay-start."
        )
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive.")

    rom_path = Path(args.rom).expanduser().resolve()
    core = load_runtime_core(rom_path)
    # The runtime Qt bridge can report a transient or missing platform value
    # immediately after swapping ROMs in the visible window. The FR/LG-specific
    # reads below are the real source of truth in that mode, so keep the strict
    # platform guard for the host-side loader and only enforce a mismatch when
    # the bridge reports a non-zero platform.
    if (not _qt_mode_enabled() and core.platform != GBA.PLATFORM_GBA) or (
        _qt_mode_enabled() and core.platform not in (0, GBA.PLATFORM_GBA)
    ):
        raise SystemExit("This script requires a GBA ROM.")

    seed_tag = f"{args.target:04x}"
    output_dir = rom_path.parent
    title_baseline_checkpoint_path = output_dir / f"seed{seed_tag}titlebase.sav"
    baseline_checkpoint_path = output_dir / f"seed{seed_tag}base.sav"
    checkpoint_path = output_dir / f"seed{seed_tag}test.sav"
    done_path = output_dir / f"seed{seed_tag}done.sav"

    if done_path.exists():
        done_path.unlink()

    print(f"ROM: {rom_path}")
    if _qt_mode_enabled():
        print("Running against the visible Qt GUI core.")
        print("Qt mode uses MGBA_* environment overrides or the DEFAULT_* values in this file.")
    print(f"Target seed: 0x{args.target:04X}")
    print(f"Title baseline checkpoint savestate: {title_baseline_checkpoint_path}")
    print(f"Baseline checkpoint savestate: {baseline_checkpoint_path}")
    print(f"Checkpoint savestate: {checkpoint_path}")
    print(f"Done savestate: {done_path}")
    print(
        "Tip: edit DEFAULT_TARGET_SEED near the top of this file"
        " if you want a different built-in target."
    )

    boot_to_title_detected_checkpoint(core)
    save_state_file(core, title_baseline_checkpoint_path)
    print(f"Saved title baseline checkpoint: {title_baseline_checkpoint_path}")

    first_a_start_delay_range = range(
        args.first_a_start_delay_start,
        args.first_a_start_delay_max + 1,
    )
    if args.first_a_start_delay_start == 0 and args.first_a_start_delay_max == 0:
        first_a_start_delay_range = range(0, 1)

    for first_a_start_delay in first_a_start_delay_range:
        built, detail = build_pre_second_press_checkpoint_from_title_baseline(
            core,
            title_baseline_checkpoint_path,
            baseline_checkpoint_path,
            checkpoint_path,
            first_a_start_delay,
        )
        if not built:
            print(
                "Skipping first-title-A start delay lane:"
                f" first_a_start_delay={first_a_start_delay}"
                f" detail={detail}"
            )
            continue

        print(
            "Prepared first-title-A start delay lane:"
            f" first_a_start_delay={first_a_start_delay}"
            f" detail={detail}"
        )
        print(f"Saved untouched baseline checkpoint: {baseline_checkpoint_path}")
        print(f"Saved rolling checkpoint: {checkpoint_path}")

        for delay_frames in range(args.max_delay + 1):
            # Current checkpoint already represents this exact no-input delay.
            for button_name, button_key in TITLE_INPUT_ATTEMPTS:
                try:
                    seed_value, seed_frame, rng_value, observed_button = brute_force_attempt(
                        core=core,
                        baseline_checkpoint_path=baseline_checkpoint_path,
                        checkpoint_path=checkpoint_path,
                        delay_frames=delay_frames,
                        button_name=button_name,
                        button_key=button_key,
                        settle_frames=args.settle_frames,
                        seed_timeout=args.seed_timeout,
                    )
                except RuntimeError as exc:
                    print(
                        "Branch failed; continuing search:"
                        f" first_a_start_delay={first_a_start_delay}"
                        f" delay={delay_frames}"
                        f" button={button_name}"
                        f" detail={exc}"
                    )
                    continue

                if delay_frames % args.progress_every == 0 or seed_value == args.target:
                    print(
                        "Attempt"
                        f" first_a_start_delay={first_a_start_delay}"
                        f" delay={delay_frames}"
                        f" button={observed_button}"
                        f" seed=0x{seed_value:04X}"
                        f" seed_frame={seed_frame}"
                        f" rng_after_settle=0x{rng_value:08X}"
                    )

                if seed_value == args.target:
                    save_state_file(core, done_path)
                    print(
                        "Match found:"
                        f" first_a_start_delay={first_a_start_delay}"
                        f" delay={delay_frames}"
                        f" button={observed_button}"
                        f" seed_frame={seed_frame}"
                        f" seed=0x{seed_value:04X}"
                        f" saved={done_path}"
                    )
                    notify_success_in_qt(
                        core,
                        target_seed=args.target,
                        observed_seed=seed_value,
                        delay_frames=delay_frames,
                        button_name=observed_button,
                        seed_frame=seed_frame,
                        done_path=done_path,
                    )
                    return 0

            if delay_frames != args.max_delay:
                checkpoint_ready = advance_checkpoint_one_frame(
                    core,
                    baseline_checkpoint_path,
                    checkpoint_path,
                    delay_frames + 1,
                )
                if not checkpoint_ready:
                    print(
                        "Rolling checkpoint can no longer be rebuilt for next delay in this lane:"
                        f" first_a_start_delay={first_a_start_delay}"
                        f" next_delay={delay_frames + 1}"
                    )
                    break

    print(
        "No match found:"
        f" target=0x{args.target:04X}"
        f" searched_delays=0..{args.max_delay}"
        f" first_a_start_delay_range={args.first_a_start_delay_start}..{args.first_a_start_delay_max}"
    )
    return 1


if __name__ == "__main__":
    exit_code = main()
    if not _qt_mode_enabled():
        raise SystemExit(exit_code)
