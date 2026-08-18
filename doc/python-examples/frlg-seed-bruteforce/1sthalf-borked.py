r"""Brute-force the first-half FR/LG initial seed from the title screen.

This script works in two modes in this workspace:

- host-side Python from the dedicated `mgba` venv
- startup Python inside the visible Qt GUI build

It is meant to be readable first and fast second.

If you know the basics of Pokemon Gen 3 RNG, the idea is simple:

1. Boot LeafGreen and pulse `Start` through the intro so the opening cutscene
   falls out as quickly as the game allows.
2. Press the title-scene skip `A` automatically if the title task still needs
   it.
3. Stop at the checkpoint just before the second title input.
4. Save a checkpoint state there as `1sthalf-checkpoint`.
5. For each delay value, try `A` first and `Start` second from that exact
   no-input checkpoint.
6. If neither button matches, restore the same checkpoint, advance exactly one
   idle frame, and save the next no-input checkpoint for the following delay.
7. When the game generates a seed, read the stopped Timer 1 counter value.
8. If the seed matches the target, save a file-backed savestate named
   `1sthalf`.

Why Timer 1 here?

For a fresh New Game path, `gTrainerId` is a convenient stable copy of the
16-bit startup seed. For this first-half workflow, though, we are loading a
pre-existing save file, so the trainer id is already defined and cannot be used
as the seed signal.

FR/LG starts Timer 1 when the title screen initializes. At the seed event,
`SeedRngAndSetTrainerId()` reads `REG_TM1CNT_L` into `val`, uses that as the
seed, and then stops Timer 1. That makes the Timer 1 low register the cleanest
live source for this workflow: it does not depend on the loaded save's trainer
data, and it gives the exact 16-bit seed value directly.

Quick edit:

If you usually run this script without command-line arguments, change
`DEFAULT_TARGET_SEED` near the top of the file.

Local workflow for this copy:

- target seed defaults to `0xFBC7`
- the persistent in-game save is `1sthalf.sav`
- that save is expected in the main `<repo-root>` folder
- on success the script writes a file-backed savestate named `1sthalf`
  in that same `<repo-root>` folder and pauses emulation
- on success it also writes `working1st-readonly` plus
  `working1st-metadata.json` so replay scripts use the exact observed
  delay/button/seed contract
- if no locked baseline exists yet, that same hit is copied into
  `1sthalf-locked-baseline`, `1sthalf-locked-baseline-metadata.json`, and
  `1sthalf-locked-baseline.sav`; later searches leave that baseline alone
  unless `MGBA_OVERWRITE_LOCKED_FIRSTHALF_BASELINE=1` is set

This copy is intentionally separate from `frlg_seed_bruteforce.py` so the
first-half workflow can change without disturbing the general-purpose script.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe 1sthalf.py --target 0xFBC7 --max-delay 500000000

Custom workspace addition:

This same script can also run inside the visible Qt GUI in this workspace via
`mGBA.exe --script ...` or the sibling `run_frlg_seed_bruteforce_visible.ps1`
launcher. In that mode, `MGBA_*` environment variables can override the normal
defaults without changing mGBA's own command-line parsing.

In this copy, `1sthalf.sav` is the required in-game save file. The generated
`1sthalf-checkpoint` and `1sthalf` files are mGBA file-backed savestates
without an added extension.
"""

from __future__ import annotations

import argparse
import configparser
import json
import os
import shutil
import stat
import sys
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
DEFAULT_MGBA_DIR = Path(__file__).resolve().parents[3]
DEFAULT_SAVE_NAME = "1sthalf.sav"
DEFAULT_BASELINE_CHECKPOINT_STATE_NAME = "1sthalf-base-checkpoint"
DEFAULT_SUCCESS_STATE_NAME = "1sthalf"
DEFAULT_CHECKPOINT_STATE_NAME = "1sthalf-checkpoint"
DEFAULT_REPLAY_CHECKPOINT_STATE_NAME = "working1st"
DEFAULT_REPLAY_CANDIDATE_STATE_NAME = "working1st-candidate"
DEFAULT_READONLY_REPLAY_CHECKPOINT_STATE_NAME = "working1st-readonly"
DEFAULT_REPLAY_METADATA_NAME = "working1st-metadata.json"
DEFAULT_LOCKED_BASELINE_CHECKPOINT_STATE_NAME = "1sthalf-locked-baseline"
DEFAULT_LOCKED_BASELINE_METADATA_NAME = "1sthalf-locked-baseline-metadata.json"
DEFAULT_LOCKED_BASELINE_SAVE_NAME = "1sthalf-locked-baseline.sav"
REPLAY_METADATA_VERSION = 2
STATUS_ENV_NAME = "MGBA_FIRSTHALF_STATUS_PATH"
SUPPRESS_SUCCESS_WARNING_ENV_NAME = "MGBA_SUPPRESS_SUCCESS_WARNING"
OVERWRITE_LOCKED_BASELINE_ENV_NAME = "MGBA_OVERWRITE_LOCKED_FIRSTHALF_BASELINE"
USE_RUNTIME_CHECKPOINT_ENV_NAME = "MGBA_USE_RUNTIME_CHECKPOINT"
# Quick-edit defaults.
# Change DEFAULT_TARGET_SEED if you want a different target without passing
# --target on the command line.
DEFAULT_TARGET_SEED = 0xFBC7
DEFAULT_MAX_DELAY = 500_000_000
DEFAULT_SETTLE_FRAMES = 3
# The title-input observation window is intentionally very large in this
# workspace. Some long-delay branches only expose the stopped Timer 1 seed much
# later than a normal title-screen brute-force would expect, and for this
# project a false timeout is worse than a long wait.
DEFAULT_SEED_TIMEOUT = 500_000
# A missed title pulse should leave RUN/state=1 quickly. If it does not, the
# script is probably sitting on the title screen waiting through the full seed
# timeout while nothing useful is happening.
DEFAULT_TITLE_INPUT_TRANSITION_TIMEOUT = 120
# Once the title pulse has left the pre-input window, a valid seed should
# appear within a much smaller bound than the full branch timeout. Keeping this
# separate avoids apparent hangs when a branch advances into the wrong title
# state and never actually produces a seed.
DEFAULT_POST_TRANSITION_SEED_TIMEOUT = 2_000
DEFAULT_BRANCH_WAIT_HEARTBEAT_EVERY = 1_000
DEFAULT_PRE_INPUT_NEUTRAL_FRAMES = 1
DEFAULT_PROGRESS_EVERY = 50
DEFAULT_ATTEMPT_LOG_EVERY = 100
DEFAULT_TITLE_SKIP_TIMEOUT = 600
DEFAULT_CHECKPOINT_WAIT_TIMEOUT = 600
DEFAULT_PRNG_SEED_DISCERN_WINDOW = 131_072

GRNG_VALUE_ADDR = 0x03005000
GMAIN_VBLANK2_ADDR = 0x03003114
TIMER1_COUNT_ADDR = 0x04000104
TIMER1_CONTROL_ADDR = 0x04000106
KEYINPUT_ADDR = 0x04000130
TIMER_ENABLE_MASK = 0x0080
GTASKS_ADDR = 0x03005090
TASK_SIZE = 0x28
TASK_COUNT = 16
TASK_TITLE_SCREEN_MAIN = 0x08078C24 | 1
TITLESCENE_RUN = 3
SAVE_STATE_FLAGS = 0
INTRO_SKIP_KEY = GBA.KEY_START
TITLE_SKIP_KEY = GBA.KEY_START
TITLE_INPUT_ATTEMPTS = (
    ("A", GBA.KEY_A),
    ("Start", GBA.KEY_START),
)
GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073
GBA_LCRNG_MULTIPLIER_INVERSE = 0xEEB9EB65


def _looks_like_mgba_executable(path: Path) -> bool:
    """Return whether one candidate path looks like an mGBA binary."""

    name = path.name.lower()
    return path.is_file() and name in {"mgba.exe", "mgba-qt.exe", "mgba-sdl.exe"}


def _coerce_mgba_root(path: Path) -> Path:
    r"""Map an executable/build path back to the main `<repo-root>` folder.

    The user-facing workflow treats "the mGBA folder" as the project root,
    even though the actual executable lives under `build-mingw64-python-qt`.
    Keep the save and savestate files in that stable root so rebuilds and
    manual file drops do not depend on the current build subdirectory.
    """

    # Preserve the lexical Windows path the user passed in instead of resolving
    # junctions/symlinks. This workspace commonly exposes the repo as
    # `<repo-root>`, but `.resolve()` would expand that back to the backing
    # vendor path and make the script report/save against a different-looking
    # directory than the user actually chose.
    normalized = Path(os.path.abspath(os.fspath(path.expanduser())))
    if _looks_like_mgba_executable(normalized):
        normalized = normalized.parent
    if normalized.name.startswith("build-"):
        return normalized.parent
    return normalized


def resolve_mgba_dir() -> Path:
    r"""Find the main `<repo-root>` folder for `1sthalf.sav` and savestates.

    Preference order:

    1. explicit `MGBA_EXE_DIR` override
    2. the current embedded executable path
    3. the current embedded `sys.argv[0]`
    4. the maintained project root used elsewhere in this workspace
    """

    override = os.environ.get("MGBA_EXE_DIR")
    if override:
        return _coerce_mgba_root(Path(override))

    executable = Path(sys.executable).expanduser()
    if _looks_like_mgba_executable(executable):
        return _coerce_mgba_root(executable)

    argv0 = Path(sys.argv[0]).expanduser()
    if _looks_like_mgba_executable(argv0):
        return _coerce_mgba_root(argv0)

    return DEFAULT_MGBA_DIR.resolve()


def _set_ini_option(path: Path, section: str, option: str, value: str) -> bool:
    """Persist one plain-text INI option if the target file is writable.

    These setup helpers intentionally write simple Qt options instead of going
    through the settings UI. That lets the automation keep future launches on
    the same speed/audio settings even before the live Python bridge is bound.
    """

    parser = configparser.RawConfigParser()
    parser.optionxform = str
    if path.exists():
        parser.read(path, encoding="utf-8")
    if not parser.has_section(section):
        parser.add_section(section)
    previous = parser.get(section, option, fallback=None)
    if previous == value:
        return False
    parser.set(section, option, value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        parser.write(handle, space_around_delimiters=False)
    return True


def ensure_audio_killswitch_defaults(mgba_dir: Path) -> None:
    r"""Best-effort persistence for the maintained Qt Audio killswitch setting.

    This keeps future launches aligned with the workflow even before the script
    gets a chance to touch the live Qt bridge.
    """

    config_targets = (
        mgba_dir / "userdata" / "config" / "config.ini",
        mgba_dir / "build-mingw64-python-qt" / "config.ini",
    )
    changed = []
    for config_path in config_targets:
        if _set_ini_option(config_path, "ports.qt", "customAudioKillswitch", "1"):
            changed.append(config_path)
    if changed:
        for config_path in changed:
            print(f"Enabled persistent Audio killswitch in config: {config_path}")


def ensure_no_render_defaults(mgba_dir: Path) -> None:
    r"""Best-effort persistence for the maintained Qt no-render setting."""

    config_targets = (
        mgba_dir / "userdata" / "config" / "config.ini",
        mgba_dir / "build-mingw64-python-qt" / "config.ini",
    )
    changed = []
    for config_path in config_targets:
        if _set_ini_option(config_path, "ports.qt", "customNoRenderMode", "1"):
            changed.append(config_path)
    if changed:
        for config_path in changed:
            print(f"Enabled persistent no-render mode in config: {config_path}")


def ensure_fast_forward_defaults(mgba_dir: Path) -> None:
    r"""Persist forced, unbounded fast-forward for maintained Qt launches."""

    config_targets = (
        mgba_dir / "userdata" / "config" / "config.ini",
        mgba_dir / "build-mingw64-python-qt" / "config.ini",
    )
    changed = []
    for config_path in config_targets:
        any_changed = False
        any_changed |= _set_ini_option(config_path, "ports.qt", "fastForward", "1")
        # A non-positive ratio is the Qt settings value for "Unbounded". Keep
        # the held ratio unbounded too, so an accidental held-fast-forward state
        # cannot silently reintroduce the old 10x cap.
        any_changed |= _set_ini_option(config_path, "ports.qt", "fastForwardRatio", "-1")
        any_changed |= _set_ini_option(config_path, "ports.qt", "fastForwardHeldRatio", "-1")
        if any_changed:
            changed.append(config_path)
    if changed:
        for config_path in changed:
            print(f"Enabled persistent unbounded fast-forward in config: {config_path}")


def _read_live_bool_feature_state(core: GBA, attribute: str) -> bool | None:
    """Read one live Qt custom-feature flag from either a property or method.

    The Qt bridge has used both property-style and method-style wrappers during
    development. Centralizing the probe keeps the setup path fast and prevents
    the feature toggles from repeating the same `getattr` work several times.
    """

    getter = getattr(core, attribute, None)
    if getter is None:
        return None
    value = getter() if callable(getter) else getter
    if value is None:
        return None
    return bool(value)


def ensure_live_audio_killswitch(core: GBA, *, qt_mode: bool | None = None) -> None:
    """Enable the live Qt Audio killswitch when the bridge exposes it.

    This copy should behave correctly in all launch modes:

    - host-side Python: no live Qt toggle exists, so do nothing
    - startup Qt script: flip the running window immediately
    - mid-session Qt script: flip the running window immediately

    The persisted config is still handled separately so future launches inherit
    the same silent setup.
    """

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if not qt_mode:
        return

    setter = getattr(core, "set_audio_killswitch", None)
    current = _read_live_bool_feature_state(core, "audio_killswitch_enabled")
    if current is None or not callable(setter):
        # Older host-side paths and older Qt bridge builds simply do not expose
        # this live custom feature. Keep the persisted-config path working
        # instead of failing the whole setup in that case.
        print("Audio killswitch bridge is unavailable in this runtime; relying on persisted config only.")
        return

    if current:
        print("Audio killswitch is already active in the live Qt session.")
        return

    setter(True)
    current = _read_live_bool_feature_state(core, "audio_killswitch_enabled")
    if not current:
        raise SystemExit("Could not enable the live Audio killswitch through the Qt bridge.")
    print("Enabled live Audio killswitch in the visible Qt session.")


def ensure_live_no_render_mode(core: GBA, *, qt_mode: bool | None = None) -> None:
    """Enable live Qt no-render mode when the bridge exposes it."""

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if not qt_mode:
        return

    setter = getattr(core, "set_no_render_mode", None)
    current = _read_live_bool_feature_state(core, "no_render_mode_enabled")
    if current is None or not callable(setter):
        print("No-render bridge is unavailable in this runtime; relying on persisted config only.")
        return

    if current:
        # No-render has a QWidget overlay in addition to a core flag. Re-apply
        # the idempotent setter so a rebuilt display cannot leave that overlay
        # stale while the controller flag still reads as enabled.
        if setter(True) is False:
            raise SystemExit("Could not resync live no-render mode through the Qt bridge.")
        print("No-render mode is already active in the live Qt session.")
        return

    setter(True)
    if not _read_live_bool_feature_state(core, "no_render_mode_enabled"):
        raise SystemExit("Could not enable live no-render mode through the Qt bridge.")
    print("Enabled live no-render mode in the visible Qt session.")


def ensure_live_unbounded_fast_forward(core: GBA, *, qt_mode: bool | None = None) -> None:
    """Enable live forced fast-forward and set its speed to unbounded."""

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if not qt_mode:
        return

    ratio_setter = getattr(core, "set_fast_forward_ratio", None)
    toggle_setter = getattr(core, "set_fast_forward", None)
    current = _read_live_bool_feature_state(core, "fast_forward_enabled")
    if current is None or not callable(ratio_setter) or not callable(toggle_setter):
        print("Fast-forward bridge is unavailable in this runtime; relying on persisted config only.")
        return

    if ratio_setter(-1.0) is False:
        raise SystemExit("Could not set the live fast-forward speed to unbounded through the Qt bridge.")
    if toggle_setter(True) is False:
        raise SystemExit("Could not enable the live fast-forward toggle through the Qt bridge.")
    if not _read_live_bool_feature_state(core, "fast_forward_enabled"):
        raise SystemExit("Could not verify the live fast-forward toggle through the Qt bridge.")
    print("Enabled live unbounded fast-forward in the visible Qt session.")


def load_required_save_file(
    core: GBA,
    path: Path,
    *,
    qt_mode: bool | None = None,
    temporary: bool = False,
) -> None:
    """Load a first-half save file into the current emulator core.

    The locked baseline replay uses `temporary=True` so its read-only save copy
    cannot be opened for writing or changed by the emulator.
    """

    if not path.is_file():
        raise SystemExit(f"Required save file not found: {path}")

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if qt_mode:
        # The Qt wrapper raises on failure instead of returning a success bool.
        core.load_save_file(path, temporary=temporary)
        pause_live_core(core, qt_mode=qt_mode, reason="after save load")
        return

    vf = mgba.vfs.open_path(str(path), "r")
    if not vf:
        raise SystemExit(f"Could not open save file for reading: {path}")
    try:
        if temporary and hasattr(core, "load_temporary_save"):
            ok = core.load_temporary_save(vf)
        else:
            ok = core.load_save(vf)
        if not ok:
            load_kind = "temporary save" if temporary else "save"
            raise SystemExit(f"mCoreLoadSave(...) failed for {load_kind}: {path}")
    finally:
        vf.close()


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


def run_frames_with_keys(core: GBA, keys: int, frames: int) -> None:
    """Run a fixed number of frames while holding an exact key mask."""

    if frames <= 0:
        return
    run_frames = getattr(core, "run_frames_with_keys", None)
    if callable(run_frames):
        run_frames(keys, frames)
        try:
            core.set_keys(raw=0)
        except Exception:
            pass
        return
    core.set_keys(raw=keys)
    for _ in range(frames):
        core.run_frame()
    core.set_keys(raw=0)


def pulse_keys_once(core: GBA, keys: int) -> None:
    """Press one exact key mask for one frame, then clear it again.

    The intro and title-scene skip paths only care about `JOY_NEW(...)`, not a
    long held button. One-frame pulses keep the intent explicit and prevent the
    live Qt runtime from carrying a held key into the next scripted state.
    """

    pulse_keys = getattr(core, "pulse_keys", None)
    if callable(pulse_keys):
        pulse_keys(keys, 1)
        try:
            core.set_keys(raw=0)
        except Exception:
            pass
        return
    core.set_keys(raw=keys)
    core.run_frame()
    core.set_keys(raw=0)


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


def lcrng_next_state(state: int) -> int:
    """Return one forward GBA LCRNG step."""

    return (GBA_LCRNG_MULTIPLIER * (int(state) & 0xFFFFFFFF) + GBA_LCRNG_INCREMENT) & 0xFFFFFFFF


def lcrng_previous_state(state: int) -> int:
    """Return one backward GBA LCRNG step."""

    return (
        GBA_LCRNG_MULTIPLIER_INVERSE
        * (((int(state) & 0xFFFFFFFF) - GBA_LCRNG_INCREMENT) & 0xFFFFFFFF)
    ) & 0xFFFFFFFF


def discern_initial_seed_from_rng_state(
    observed_rng: int,
    *,
    max_steps: int = DEFAULT_PRNG_SEED_DISCERN_WINDOW,
) -> tuple[int, int] | None:
    """Infer a plausible 16-bit seeded state from one live `gRngValue`.

    `SeedRng(seed)` starts `gRngValue` from one 16-bit state, but by the time
    Python samples memory after the title transition the live PRNG may already
    have moved away from that seeded state. As a secondary sanity check, search
    the nearby LCRNG/LCRNG(R) orbit for the closest state whose upper 16 bits
    are zero and return:

    - the inferred 16-bit seed candidate
    - the signed step distance from the observed state to that candidate

    Negative distances mean the candidate was found by rewinding with
    LCRNG(R); positive distances mean it was found by advancing forward.
    """

    state = int(observed_rng) & 0xFFFFFFFF
    if state <= 0xFFFF:
        return state, 0

    backward = state
    forward = state
    for steps in range(1, max_steps + 1):
        backward = lcrng_previous_state(backward)
        if backward <= 0xFFFF:
            return backward & 0xFFFF, -steps
        forward = lcrng_next_state(forward)
        if forward <= 0xFFFF:
            return forward & 0xFFFF, steps
    return None


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
        pause_live_core(core, reason="after state save")
        return

    vf = mgba.vfs.open_path(str(path), "w+")
    if not vf:
        raise SystemExit(f"Could not open savestate path for writing: {path}")
    try:
        if not save_state_named(core, vf.handle):
            raise SystemExit(f"mCoreSaveStateNamed(...) failed for {path}")
    finally:
        vf.close()
    pause_live_core(core, reason="after state save")


def load_state_file(core: GBA, path: Path, *, qt_mode: bool | None = None) -> None:
    """Load one file-backed mGBA savestate."""

    if hasattr(core, "load_state_file") and not hasattr(core, "_core"):
        core.load_state_file(path, SAVE_STATE_FLAGS)
        pause_live_core(core, qt_mode=qt_mode, reason="after state load")
        return

    vf = mgba.vfs.open_path(str(path), "r")
    if not vf:
        raise SystemExit(f"Could not open savestate path for reading: {path}")
    try:
        if not load_state_named(core, vf.handle):
            raise SystemExit(f"mCoreLoadStateNamed(...) failed for {path}")
    finally:
        vf.close()
    pause_live_core(core, qt_mode=qt_mode, reason="after state load")


def _call_load_state_file(core: GBA, path: Path, *, qt_mode: bool | None = None) -> None:
    """Call load_state_file with optional qt_mode for test doubles."""

    try:
        load_state_file(core, path, qt_mode=qt_mode)
    except TypeError:
        load_state_file(core, path)


def capture_runtime_checkpoint(core: GBA, *, qt_mode: bool | None = None) -> bool:
    """Capture one in-memory checkpoint when the live Qt bridge supports it."""

    if hasattr(core, "save_scratch_state") and not hasattr(core, "_core"):
        core.save_scratch_state()
        pause_live_core(core, qt_mode=qt_mode, reason="after scratch checkpoint save")
        return True
    return False


def restore_checkpoint(
    core: GBA,
    path: Path,
    use_runtime_checkpoint: bool,
    *,
    qt_mode: bool | None = None,
) -> None:
    """Restore the exact brute-force checkpoint.

    The first-half brute-force workflow is centered on explicit file-backed
    no-input savestates. The runtime scratch checkpoint remains available as an
    opt-in acceleration path, but it is no longer the default because the
    intended workflow is "restore a real non-input savestate, then press one
    button to trigger seed generation".
    """

    if use_runtime_checkpoint and hasattr(core, "load_scratch_state") and not hasattr(core, "_core"):
        core.load_scratch_state()
        pause_live_core(core, qt_mode=qt_mode, reason="after scratch checkpoint")
        return
    _call_load_state_file(core, path, qt_mode=qt_mode)


def _restore_checkpoint_qt_aware(
    core: GBA,
    path: Path,
    use_runtime_checkpoint: bool,
    *,
    qt_mode: bool | None = None,
) -> None:
    """Call restore_checkpoint with optional qt_mode for test doubles."""

    try:
        restore_checkpoint(core, path, use_runtime_checkpoint, qt_mode=qt_mode)
    except TypeError:
        restore_checkpoint(core, path, use_runtime_checkpoint)


def export_replay_checkpoint(
    core: GBA,
    *,
    source_checkpoint_path: Path,
    replay_checkpoint_path: Path,
    use_runtime_checkpoint: bool,
) -> None:
    """Write the exact pre-button working checkpoint to a file.

    When a target is found, the replay script needs that exact pre-button
    no-input state, not just the post-seed success frame. This helper reloads
    whichever checkpoint source was actually active for the search, writes it
    to the dedicated `working1st` file, and lets the caller restore the
    already-saved success state afterward.
    """

    _restore_checkpoint_qt_aware(
        core,
        source_checkpoint_path,
        use_runtime_checkpoint,
        qt_mode=_qt_mode_enabled(),
    )
    core.set_keys(raw=0)
    if observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError(
            "The active checkpoint was already post-seed while exporting"
            f" the working checkpoint: {replay_checkpoint_path}"
        )
    save_state_file(core, replay_checkpoint_path)


def commit_working_checkpoint(candidate_path: Path, working_checkpoint_path: Path) -> None:
    """Promote the pre-input candidate savestate after a verified seed hit.

    `working1st-candidate` is written before each attempted title input. Only
    the candidate from the branch that actually hit the target should become
    `working1st`, so the final handoff is a filesystem replace instead of a
    post-hit savestate restore/re-save.
    """

    if not candidate_path.is_file():
        raise RuntimeError(
            "The replay candidate checkpoint was not written before the seed hit:"
            f" {candidate_path}"
        )
    _remove_file_if_present(working_checkpoint_path)
    candidate_path.replace(working_checkpoint_path)


def _remove_file_if_present(path: Path) -> None:
    """Remove a file even if a previous run left it marked read-only."""

    if not path.exists():
        return
    # The immutable replay artifacts are deliberately read-only. Clear the
    # owner-write bit before replacement so rerunning a successful search can
    # refresh the metadata atomically instead of failing on Windows.
    path.chmod(path.stat().st_mode | stat.S_IWRITE)
    path.unlink()


def _make_file_read_only(path: Path) -> None:
    """Mark one replay artifact read-only as a guard against accidental reuse."""

    write_bits = stat.S_IWRITE | stat.S_IWGRP | stat.S_IWOTH
    path.chmod(path.stat().st_mode & ~write_bits)


def is_file_read_only(path: Path) -> bool:
    """Return whether the owner-write bit is absent on one artifact file."""

    return not bool(path.stat().st_mode & stat.S_IWRITE)


def build_replay_metadata(
    *,
    mgba_dir: Path,
    rom_path: Path,
    save_path: Path,
    working_checkpoint_path: Path,
    readonly_checkpoint_path: Path,
    target_seed: int,
    delay_frames: int,
    button_name: str,
    seed_frame: int,
    rng_at_seed: int,
    prng_discerned_seed: int | None = None,
    prng_discerned_steps_from_rng: int | None = None,
    timer1_count_pre: int,
    timer1_control_pre: int,
    pre_input_neutral_frames: int,
    seed_timeout: int,
) -> dict[str, object]:
    """Build the replay contract consumed by `1sthalfreplication.py`.

    The replay script should not carry hardcoded hit data. The brute-force
    script is the source of truth because it is the one that observed the seed,
    the exact pre-input checkpoint, and the final button used for that hit.
    """

    metadata = {
        "metadata_version": REPLAY_METADATA_VERSION,
        "producer": "1sthalf.py",
        "mgba_dir": str(mgba_dir),
        "rom_path": str(rom_path),
        "save_name": DEFAULT_SAVE_NAME,
        "save_path": str(save_path),
        "working_checkpoint_name": working_checkpoint_path.name,
        "readonly_checkpoint_name": readonly_checkpoint_path.name,
        "target_seed": int(target_seed) & 0xFFFF,
        "delay_frames": int(delay_frames),
        "button_name": str(button_name),
        "seed_frame": int(seed_frame),
        "rng_at_seed": int(rng_at_seed) & 0xFFFFFFFF,
        "timer1_count_pre": int(timer1_count_pre) & 0xFFFF,
        "timer1_control_pre": int(timer1_control_pre) & 0xFFFF,
        "pre_input_neutral_frames": int(pre_input_neutral_frames),
        "seed_timeout": int(seed_timeout),
        "audio_killswitch": True,
        "no_render_mode": True,
        "fast_forward": True,
        "fast_forward_ratio": "unbounded",
        "unthrottled_frame_advance": True,
        "locked_baseline": False,
    }
    if prng_discerned_seed is not None:
        metadata["prng_discerned_seed"] = int(prng_discerned_seed) & 0xFFFF
    if prng_discerned_steps_from_rng is not None:
        metadata["prng_discerned_steps_from_rng"] = int(prng_discerned_steps_from_rng)
    return metadata


def build_locked_baseline_metadata(
    metadata: dict[str, object],
    *,
    mgba_dir: Path,
    baseline_checkpoint_path: Path,
    baseline_save_path: Path,
) -> dict[str, object]:
    """Retarget one replay contract at the stable locked baseline files."""

    locked = dict(metadata)
    locked.update(
        {
            "mgba_dir": str(mgba_dir),
            "save_name": baseline_save_path.name,
            "save_path": str(baseline_save_path),
            "readonly_checkpoint_name": baseline_checkpoint_path.name,
            "locked_baseline": True,
            "locked_baseline_checkpoint_name": baseline_checkpoint_path.name,
            "locked_baseline_save_name": baseline_save_path.name,
            "locked_baseline_metadata_name": DEFAULT_LOCKED_BASELINE_METADATA_NAME,
            "source_readonly_checkpoint_name": str(
                metadata.get("readonly_checkpoint_name", DEFAULT_READONLY_REPLAY_CHECKPOINT_STATE_NAME)
            ),
            "source_save_name": str(metadata.get("save_name", DEFAULT_SAVE_NAME)),
        }
    )
    return locked


def write_json_read_only(path: Path, payload: dict[str, object]) -> None:
    """Write a small JSON artifact and make the final file read-only."""

    temp_path = path.with_name(f"{path.name}.tmp")
    _remove_file_if_present(temp_path)
    _remove_file_if_present(path)
    temp_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temp_path.replace(path)
    _make_file_read_only(path)


def write_status_marker_from_env(env_name: str, payload: dict[str, object]) -> None:
    """Write optional JSON status for real Qt deployment tests."""

    marker = os.environ.get(env_name)
    if not marker:
        return
    marker_path = Path(marker).expanduser()
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def write_readonly_replay_artifacts(
    *,
    source_checkpoint_path: Path,
    readonly_checkpoint_path: Path,
    metadata_path: Path,
    metadata: dict[str, object],
) -> None:
    """Copy the matching checkpoint into immutable replay artifacts."""

    if not source_checkpoint_path.is_file():
        raise RuntimeError(
            "Cannot export read-only replay artifacts because the working"
            f" checkpoint is missing: {source_checkpoint_path}"
        )

    _remove_file_if_present(readonly_checkpoint_path)
    shutil.copy2(source_checkpoint_path, readonly_checkpoint_path)
    _make_file_read_only(readonly_checkpoint_path)
    write_json_read_only(metadata_path, metadata)


def write_locked_baseline_artifacts(
    *,
    source_checkpoint_path: Path,
    source_metadata: dict[str, object],
    source_save_path: Path,
    baseline_checkpoint_path: Path,
    baseline_metadata_path: Path,
    baseline_save_path: Path,
    mgba_dir: Path,
    overwrite: bool = False,
) -> bool:
    """Write the stable first-half replay baseline, without mutating it later.

    `working1st-*` remains the latest brute-force output. These baseline files
    are a separate protected copy so future calibration runs can update the
    working artifacts without invalidating the known-good replay contract.
    """

    if not source_checkpoint_path.is_file():
        raise RuntimeError(f"Cannot lock missing replay checkpoint: {source_checkpoint_path}")
    if not source_save_path.is_file():
        raise RuntimeError(f"Cannot lock missing first-half save: {source_save_path}")

    baseline_paths = (baseline_checkpoint_path, baseline_metadata_path, baseline_save_path)
    existing_paths = [path for path in baseline_paths if path.exists()]
    if existing_paths and not overwrite:
        if len(existing_paths) == len(baseline_paths):
            # Reassert read-only on all three files. On Windows this is the
            # practical guard that prevents the replay script or a later search
            # from accidentally mutating the locked baseline in place.
            for path in baseline_paths:
                _make_file_read_only(path)
            return False
        missing = sorted(str(path) for path in baseline_paths if not path.exists())
        raise RuntimeError(
            "Locked first-half baseline is incomplete. Missing:"
            f" {', '.join(missing)}. Set {OVERWRITE_LOCKED_BASELINE_ENV_NAME}=1"
            " after deciding the current hit should replace the baseline."
        )

    for path in baseline_paths:
        _remove_file_if_present(path)

    shutil.copy2(source_checkpoint_path, baseline_checkpoint_path)
    shutil.copy2(source_save_path, baseline_save_path)
    _make_file_read_only(baseline_checkpoint_path)
    _make_file_read_only(baseline_save_path)
    write_json_read_only(
        baseline_metadata_path,
        build_locked_baseline_metadata(
            source_metadata,
            mgba_dir=mgba_dir,
            baseline_checkpoint_path=baseline_checkpoint_path,
            baseline_save_path=baseline_save_path,
        ),
    )
    return True


def rebuild_delay_checkpoint(
    core: GBA,
    *,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    use_runtime_checkpoint: bool,
    delay_frames: int,
) -> None:
    """Rebuild the rolling checkpoint from the untouched baseline checkpoint."""

    load_state_file(core, baseline_checkpoint_path)
    core.set_keys(raw=0)
    run_frames_fast(core, delay_frames)
    if observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError(
            "Rebuilding the rolling checkpoint reached a post-seed state"
            f" while targeting delay {delay_frames}."
        )
    save_state_file(core, checkpoint_path)
    if use_runtime_checkpoint:
        capture_runtime_checkpoint(core)


def advance_checkpoint_one_frame(
    core: GBA,
    *,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    use_runtime_checkpoint: bool,
    next_delay_frames: int,
) -> None:
    """Promote the rolling no-input checkpoint by exactly one frame.

    The search should not replay `delay` idle frames from the beginning on
    every attempt. Instead, one no-input checkpoint is reused for both button
    branches at that delay, then rewound, advanced by one idle frame, and
    saved again as the starting point for the next delay.

    In the visible Qt runtime path this hot checkpoint stays in the in-memory
    scratch slot for speed. Host-side runs still fall back to the file-backed
    checkpoint path.

    A valid rolling checkpoint is not just "Timer 1 still running"; it also has
    to stay in the title task's RUN/state=1 window. If that window drifts while
    promoting the checkpoint, rebuild the next delay directly from the baseline
    instead of propagating a bad no-input state.
    """

    _restore_checkpoint_qt_aware(
        core,
        checkpoint_path,
        use_runtime_checkpoint,
        qt_mode=_qt_mode_enabled(),
    )
    core.set_keys(raw=0)
    core.run_frame()
    if observe_initial_seed_from_timer1(core) is not None or not title_input_checkpoint_ready(core):
        rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=next_delay_frames,
        )
        return
    save_state_file(core, checkpoint_path)
    if use_runtime_checkpoint:
        capture_runtime_checkpoint(core)


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
    """Boot the game to the frame window just before the second title input.

    In plain RNG terms, this function does the boring setup work:

    - power on/reset the ROM
    - pulse `Start` through the intro so the opening cutscene exits quickly
    - wait until the title logic appears
    - pulse `Start` until the title task itself actually reaches the RUN scene
    - wait until FR/LG reaches RUN/state=1

    That final RUN/state=1 point is the checkpoint we want, because every brute
    force attempt can start from the exact same place.
    """

    core.reset()
    core.set_keys(raw=0)

    intro_skip_pulses = 0
    while True:
        info = find_title_task(core)
        if info is not None:
            print(
                "Title screen detected:"
                f" frame_counter={core.frame_counter}"
                f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={info.scene} state={info.state}"
            )
            if intro_skip_pulses:
                print(
                    "Skipped the opening intro with"
                    f" {intro_skip_pulses} one-frame Start pulses before the title task appeared."
                )
            break
        pulse_keys_once(core, INTRO_SKIP_KEY)
        intro_skip_pulses += 1

    if info.scene != TITLESCENE_RUN:
        title_skip_pulses = 0
        for _ in range(DEFAULT_TITLE_SKIP_TIMEOUT):
            # FR/LG accepts Start as a "jump to RUN" title-scene skip before
            # the main RUN scene is active. Using the same key for intro skip
            # and title-scene skip is more reliable than assuming one A pulse
            # will always land after the task is ready to accept it.
            pulse_keys_once(core, TITLE_SKIP_KEY)
            title_skip_pulses += 1
            info = find_title_task(core)
            if info is None:
                raise SystemExit("Lost the title task while applying the title-scene skip input.")
            if info.scene == TITLESCENE_RUN:
                print(
                    "Reached the title RUN scene after"
                    f" {title_skip_pulses} title-skip Start pulse(s):"
                    f" frame_counter={core.frame_counter}"
                    f" scene={info.scene} state={info.state}"
                )
                break
        else:
            raise SystemExit("Could not force the title task into the RUN scene.")
    else:
        print(
            "Title task already reached the RUN scene"
            f" at frame_counter={core.frame_counter}; no extra title-skip A was needed."
        )

    if info.scene == TITLESCENE_RUN and info.state == 1:
        print(
            "Checkpoint reached before second title input:"
            f" frame_counter={core.frame_counter}"
            f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
            f" scene={info.scene} state={info.state}"
        )
        return info

    # The title task is now in the RUN scene. The seed-generating title input
    # is legal once RUN/state=1 becomes visible.
    for _ in range(DEFAULT_CHECKPOINT_WAIT_TIMEOUT):
        core.set_keys(raw=0)
        core.run_frame()
        info = find_title_task(core)
        if info is None:
            raise SystemExit("Lost the title task while waiting for the checkpoint.")
        if info.scene == TITLESCENE_RUN and info.state == 1:
            print(
                "Checkpoint reached before second title input:"
                f" frame_counter={core.frame_counter}"
                f" vblank2={core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={info.scene} state={info.state}"
            )
            return info

    raise SystemExit(
        "Could not reach the pre-second-title-input checkpoint"
        f" within {DEFAULT_CHECKPOINT_WAIT_TIMEOUT} neutral frames."
    )


def observe_initial_seed_from_timer1(core: GBA) -> int | None:
    """Read the startup seed directly from Timer 1 once the title path stops it.

    `CB2_InitTitleScreen()` starts Timer 1. Later, `SeedRngAndSetTrainerId()`
    reads `REG_TM1CNT_L` and then clears `REG_TM1CNT_H`, leaving the captured
    low counter value behind as the exact 16-bit seed we want.
    """

    if core.memory.u16[TIMER1_CONTROL_ADDR] & TIMER_ENABLE_MASK:
        return None
    return core.memory.u16[TIMER1_COUNT_ADDR]


def title_input_checkpoint_ready(core: GBA) -> bool:
    """Report whether the checkpoint still matches RUN/state=1.

    Timer 1 still running is necessary, but not sufficient, for the final
    title input to behave correctly. On long delay searches the rolling
    checkpoint can drift into another title-task state where `A` or `Start`
    no longer means "seed the game now". In real FR/LG cores we can inspect the
    title task directly; unit-test doubles without task RAM simply skip this
    check so the tests only need to model the state they care about.
    """

    status = title_input_checkpoint_status(core)
    if status is None:
        return True
    return status


def title_input_checkpoint_status(core: GBA) -> bool | None:
    """Report the exact pre-input title-window status when task RAM is readable.

    Returns:
    - `True` when the core is still at the RUN/state=1 title-input checkpoint
    - `False` when the title task is readable and no longer at that checkpoint
    - `None` when this runtime cannot inspect the title task at all
    """

    try:
        info = find_title_task(core)
    except Exception:
        return None
    return info is not None and info.scene == TITLESCENE_RUN and info.state == 1


def read_timer1_state(core: GBA) -> tuple[int, int]:
    """Return the raw Timer 1 count/control pair from the current core."""

    return core.memory.u16[TIMER1_COUNT_ADDR], core.memory.u16[TIMER1_CONTROL_ADDR]


def read_timer1_state_from_checkpoint(core: GBA, checkpoint_path: Path) -> tuple[int, int]:
    """Load a checkpoint and read the Timer 1 count/control pair from it."""

    load_state_file(core, checkpoint_path)
    core.set_keys(raw=0)
    _probe_state(core, f"firsthalf_loaded_checkpoint:{checkpoint_path.name}")
    return read_timer1_state(core)


def _probe_state(core: GBA, label: str) -> None:
    """Log vblank/timer1 state snapshots to diagnose drift."""

    try:
        vblank2 = core.memory.u32[GMAIN_VBLANK2_ADDR]
    except Exception:
        vblank2 = 0
    try:
        timer1_count = core.memory.u16[TIMER1_COUNT_ADDR]
        timer1_control = core.memory.u16[TIMER1_CONTROL_ADDR]
    except Exception:
        timer1_count = 0
        timer1_control = 0
    try:
        rng_value = core.memory.u32[GRNG_VALUE_ADDR]
    except Exception:
        rng_value = 0
    try:
        keyinput = core.memory.u16[KEYINPUT_ADDR]
    except Exception:
        keyinput = 0
    frame_counter = getattr(core, "frame_counter", 0)
    print(
        f"Probe {label}: frame_counter={frame_counter}"
        f" vblank2={vblank2} timer1_count=0x{timer1_count:04X}"
        f" timer1_control=0x{timer1_control:04X}"
        f" keyinput=0x{keyinput:04X} rng=0x{rng_value:08X}"
    )


def brute_force_attempt(
    core: GBA,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    use_runtime_checkpoint: bool,
    delay_frames: int,
    button_name: str,
    button_key: int,
    seed_timeout: int,
    pre_input_checkpoint_path: Path | None = None,
    pre_input_neutral_frames: int = DEFAULT_PRE_INPUT_NEUTRAL_FRAMES,
    title_transition_timeout: int = DEFAULT_TITLE_INPUT_TRANSITION_TIMEOUT,
    post_transition_seed_timeout: int = DEFAULT_POST_TRANSITION_SEED_TIMEOUT,
) -> tuple[int, int, int, str]:
    """Try one button branch from the current rolling delay checkpoint.

    Each attempt reloads the current no-input checkpoint for `delay_frames`,
    then applies exactly one final title-button press (`A` or `Start`) from
    that no-input state and waits for FR/LG to expose the seed.
    If `pre_input_checkpoint_path` is provided, the script saves the exact live
    pre-button state immediately before pressing the title input. This avoids
    reconstructing the winning replay state after the seed hit, which can drift
    in the visible Qt runtime.
    The returned tuple is:

    - observed 16-bit initial seed from Timer 1
    - frame counter when that seed first appeared
    - raw `gRngValue` at that same frame for diagnostics
    - the button name used for this attempt
    """

    retried_from_baseline = False

    while True:
        _restore_checkpoint_qt_aware(
            core,
            checkpoint_path,
            use_runtime_checkpoint,
            qt_mode=_qt_mode_enabled(),
        )
        # The rolling checkpoint is a no-input savestate by design, but the live
        # Qt session keeps its current host key mask outside the savestate
        # payload. Clear keys here so each button branch really starts from
        # "no input yet".
        core.set_keys(raw=0)
        # The saved checkpoint should still be inside the title-screen window
        # where Timer 1 is running and waiting to become the seed. If the timer
        # is already stopped here, the script is no longer searching from the
        # intended pre-second-input state.
        if observe_initial_seed_from_timer1(core) is not None:
            rebuild_delay_checkpoint(
                core,
                baseline_checkpoint_path=baseline_checkpoint_path,
                checkpoint_path=checkpoint_path,
                use_runtime_checkpoint=use_runtime_checkpoint,
                delay_frames=delay_frames,
            )
            _restore_checkpoint_qt_aware(
                core,
                checkpoint_path,
                use_runtime_checkpoint,
                qt_mode=_qt_mode_enabled(),
            )
            core.set_keys(raw=0)
            if observe_initial_seed_from_timer1(core) is not None:
                raise RuntimeError(
                    "Checkpoint is no longer in the pre-seed title state:"
                    " Timer 1 was already stopped before the final title input."
                )
        if not title_input_checkpoint_ready(core):
            if retried_from_baseline:
                raise RuntimeError(
                    "Checkpoint is no longer in the pre-seed title state:"
                    " the title task was not in RUN/state=1 before the final title input."
                )
            print(
                "Title checkpoint drift detected before final input;"
                f" rebuilding delay {delay_frames} and retrying {button_name} once."
            )
            rebuild_delay_checkpoint(
                core,
                baseline_checkpoint_path=baseline_checkpoint_path,
                checkpoint_path=checkpoint_path,
                use_runtime_checkpoint=use_runtime_checkpoint,
                delay_frames=delay_frames,
            )
            retried_from_baseline = True
            continue
        if pre_input_neutral_frames:
            # Ensure a neutral frame so JOY_NEW detects the upcoming title input
            # deterministically even if the host key mask was previously held.
            run_frames_with_keys(core, 0, pre_input_neutral_frames)
        if pre_input_checkpoint_path is not None:
            _probe_state(core, f"firsthalf_pre_input_candidate:{pre_input_checkpoint_path.name}")
            save_state_file(core, pre_input_checkpoint_path)
        # The seed-generating action is a button press from the no-input
        # checkpoint, not a long held title input. Press once, then wait with
        # keys cleared so the search path matches the intended recipe.
        core.set_keys(button_key)
        core.run_frame()
        seed_value = observe_initial_seed_from_timer1(core)
        core.set_keys(raw=0)
        if seed_value is not None:
            _probe_state(core, f"firsthalf_seed_hit:{button_name}")
            return seed_value, core.frame_counter, core.memory.u32[GRNG_VALUE_ADDR], button_name

        remaining_wait_frames = max(seed_timeout - 1, 0)
        title_window_status = title_input_checkpoint_status(core)
        if title_window_status is True:
            transition_budget = min(remaining_wait_frames, title_transition_timeout)
            for _ in range(transition_budget):
                core.run_frame()
                remaining_wait_frames -= 1
                seed_value = observe_initial_seed_from_timer1(core)
                if seed_value is not None:
                    _probe_state(core, f"firsthalf_seed_hit:{button_name}")
                    return seed_value, core.frame_counter, core.memory.u32[GRNG_VALUE_ADDR], button_name
                title_window_status = title_input_checkpoint_status(core)
                if title_window_status is not True:
                    break

            if title_window_status is True:
                core.set_keys(raw=0)
                if retried_from_baseline:
                    raise RuntimeError(
                        "Title input never left the pre-seed title window within"
                        f" {title_transition_timeout} frames after delay"
                        f" {delay_frames} using {button_name}, even after"
                        " rebuilding that exact delay checkpoint from the"
                        " title baseline."
                    )
                print(
                    "Title input did not leave the pre-seed title window;"
                    f" rebuilding delay {delay_frames} and retrying {button_name} once."
                )
                rebuild_delay_checkpoint(
                    core,
                    baseline_checkpoint_path=baseline_checkpoint_path,
                    checkpoint_path=checkpoint_path,
                    use_runtime_checkpoint=use_runtime_checkpoint,
                    delay_frames=delay_frames,
                )
                retried_from_baseline = True
                continue

        effective_wait_frames = remaining_wait_frames
        if title_window_status is not True:
            effective_wait_frames = min(remaining_wait_frames, post_transition_seed_timeout)

        for waited_frames in range(1, effective_wait_frames + 1):
            core.run_frame()
            seed_value = observe_initial_seed_from_timer1(core)
            if seed_value is not None:
                _probe_state(core, f"firsthalf_seed_hit:{button_name}")
                return seed_value, core.frame_counter, core.memory.u32[GRNG_VALUE_ADDR], button_name
            if waited_frames % DEFAULT_BRANCH_WAIT_HEARTBEAT_EVERY == 0:
                timer1_count, timer1_control = read_timer1_state(core)
                print(
                    "Still waiting for initial seed:"
                    f" delay={delay_frames}"
                    f" button={button_name}"
                    f" waited_frames={waited_frames}"
                    f" timer1_count=0x{timer1_count:04X}"
                    f" timer1_control=0x{timer1_control:04X}"
                    f" title_window_status={title_window_status}"
                )

        # Keep the live key mask neutral before rebuilding or failing. The
        # visible Qt controller state lives outside the savestate payload, so a
        # timeout branch must actively release its title input before the next
        # restore.
        core.set_keys(raw=0)
        if retried_from_baseline:
            raise RuntimeError(
                f"Initial seed was not observed within {effective_wait_frames + 1} frames"
                f" after delay {delay_frames} using {button_name}, even after"
                " rebuilding that exact delay checkpoint from the title baseline."
            )
        print(
            "Title input attempt timed out;"
            f" rebuilding delay {delay_frames} and retrying {button_name} once."
            f" effective_wait_frames={effective_wait_frames + 1}"
        )
        rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=delay_frames,
        )
        retried_from_baseline = True


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI.

    The script is easiest to tweak by editing the `DEFAULT_*` constants near the
    top of this file, but every important setting can also be overridden from
    the command line.
    """

    parser = argparse.ArgumentParser(
        description="Brute-force an FRLG initial seed from a pre-second-title-input checkpoint.",
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
        help="How many extra frames to sample after a non-match when printing diagnostic progress output.",
    )
    parser.add_argument(
        "--seed-timeout",
        type=int,
        default=_env_default_int("MGBA_SEED_TIMEOUT", DEFAULT_SEED_TIMEOUT),
        help="Maximum frames to wait after the final title input while Timer 1 runs before giving up.",
    )
    parser.add_argument(
        "--title-transition-timeout",
        type=int,
        default=_env_default_int(
            "MGBA_TITLE_INPUT_TRANSITION_TIMEOUT", DEFAULT_TITLE_INPUT_TRANSITION_TIMEOUT
        ),
        help=(
            "Maximum frames to allow the title pulse to leave RUN/state=1 before"
            " treating the branch as a dead title-screen input."
        ),
    )
    parser.add_argument(
        "--post-transition-seed-timeout",
        type=int,
        default=_env_default_int(
            "MGBA_POST_TRANSITION_SEED_TIMEOUT", DEFAULT_POST_TRANSITION_SEED_TIMEOUT
        ),
        help=(
            "Maximum frames to wait for Timer 1 to stop after the title task has"
            " already left the pre-input window."
        ),
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
        core = mgba_qt.current_core()
        pause_live_core(core, reason="after ROM load")
        return core

    mgba.log.silence()
    core = mgba.core.load_path(str(rom_path))
    if not core:
        raise SystemExit(f"Could not load ROM: {rom_path}")
    return core


def pause_live_core(core: GBA, *, qt_mode: bool | None = None, reason: str = "") -> None:
    """Pause the visible Qt core to prevent background free-run.

    The live Qt window may already be running when a runtime script starts,
    which can advance RNG state between ROM/save/state loads and the first
    scripted frame. Pausing here keeps the core deterministic until we
    explicitly advance frames via run_frame/run_frames.
    """

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if not qt_mode:
        return

    try:
        core.set_keys(raw=0)
    except Exception:
        pass

    if hasattr(core, "pause"):
        try:
            core.pause()
            return
        except Exception:
            pass

    if mgba_qt and hasattr(mgba_qt, "pause_current_core"):
        if not mgba_qt.pause_current_core():
            suffix = f" ({reason})" if reason else ""
            raise SystemExit(f"Could not pause the visible Qt core{suffix}.")


def notify_success_in_qt(
    core: GBA,
    *,
    qt_mode: bool | None = None,
    target_seed: int,
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

    if qt_mode is None:
        qt_mode = _qt_mode_enabled()
    if not qt_mode or not mgba_qt:
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
        f"Target seed: 0x{target_seed:04X}\n"
        f"Delay searched: {delay_frames} frames\n"
        f"Final button press: {button_name}\n"
        f"Frame that hit the seed: {seed_frame}\n"
        f"Savestate: {done_path}"
    )
    if os.environ.get(SUPPRESS_SUCCESS_WARNING_ENV_NAME) == "1":
        print("Visible Qt core paused; success warning suppressed by test environment.")
        return
    print("Visible Qt core paused; showing success warning.")
    mgba_qt.show_warning(title, message)


def main() -> int:
    """Run the complete FR/LG seed search.

    Human-readable summary:

    - load the ROM
    - reach the pre-second-input checkpoint once
    - save `1sthalf-checkpoint`
    - brute-force delays from `0` to `max_delay`
    - for each delay, try `A` first and `Start` second from the same
      no-input checkpoint
    - write `working1st-candidate` immediately before each attempted final
      input so the successful branch has an exact replay source
    - if neither matches, advance that checkpoint by one idle frame
    - print progress as seeds are observed
    - save `1sthalf` and promote the matching candidate to `working1st` if the
      desired seed is found
    """

    parser = build_parser()
    args = _parse_args(parser)

    if args.max_delay < 0:
        raise SystemExit("--max-delay must be non-negative.")
    if args.settle_frames < 0:
        raise SystemExit("--settle-frames must be non-negative.")
    if args.seed_timeout <= 0:
        raise SystemExit("--seed-timeout must be positive.")
    if args.title_transition_timeout <= 0:
        raise SystemExit("--title-transition-timeout must be positive.")
    post_transition_seed_timeout = getattr(
        args, "post_transition_seed_timeout", DEFAULT_POST_TRANSITION_SEED_TIMEOUT
    )
    if post_transition_seed_timeout <= 0:
        raise SystemExit("--post-transition-seed-timeout must be positive.")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive.")

    rom_path = Path(args.rom).expanduser().resolve()
    mgba_dir = resolve_mgba_dir()
    save_path = mgba_dir / DEFAULT_SAVE_NAME
    baseline_checkpoint_path = mgba_dir / DEFAULT_BASELINE_CHECKPOINT_STATE_NAME
    checkpoint_path = mgba_dir / DEFAULT_CHECKPOINT_STATE_NAME
    replay_checkpoint_path = mgba_dir / DEFAULT_REPLAY_CHECKPOINT_STATE_NAME
    replay_candidate_path = mgba_dir / DEFAULT_REPLAY_CANDIDATE_STATE_NAME
    readonly_replay_checkpoint_path = mgba_dir / DEFAULT_READONLY_REPLAY_CHECKPOINT_STATE_NAME
    replay_metadata_path = mgba_dir / DEFAULT_REPLAY_METADATA_NAME
    locked_baseline_checkpoint_path = mgba_dir / DEFAULT_LOCKED_BASELINE_CHECKPOINT_STATE_NAME
    locked_baseline_metadata_path = mgba_dir / DEFAULT_LOCKED_BASELINE_METADATA_NAME
    locked_baseline_save_path = mgba_dir / DEFAULT_LOCKED_BASELINE_SAVE_NAME
    done_path = mgba_dir / DEFAULT_SUCCESS_STATE_NAME

    # The Qt bridge probe crosses into the embedded scripting layer, so do it
    # once up front and thread that mode flag through the rest of the run.
    qt_mode = _qt_mode_enabled()

    core = load_runtime_core(rom_path)
    ensure_audio_killswitch_defaults(mgba_dir)
    ensure_no_render_defaults(mgba_dir)
    ensure_fast_forward_defaults(mgba_dir)
    ensure_live_audio_killswitch(core, qt_mode=qt_mode)
    ensure_live_no_render_mode(core, qt_mode=qt_mode)
    ensure_live_unbounded_fast_forward(core, qt_mode=qt_mode)
    # The runtime Qt bridge can report a transient or missing platform value
    # immediately after swapping ROMs in the visible window. The FR/LG-specific
    # reads below are the real source of truth in that mode, so keep the strict
    # platform guard for the host-side loader and only enforce a mismatch when
    # the bridge reports a non-zero platform.
    if (not qt_mode and core.platform != GBA.PLATFORM_GBA) or (qt_mode and core.platform not in (0, GBA.PLATFORM_GBA)):
        raise SystemExit("This script requires a GBA ROM.")

    load_required_save_file(core, save_path, qt_mode=qt_mode)

    if done_path.exists():
        done_path.unlink()
    if replay_candidate_path.exists():
        replay_candidate_path.unlink()

    print(f"ROM: {rom_path}")
    print(f"mGBA directory: {mgba_dir}")
    print(f"Persistent save file: {save_path}")
    if qt_mode:
        print("Running against the visible Qt GUI core.")
        print("Qt mode uses MGBA_* environment overrides or the DEFAULT_* values in this file.")
    print(f"Target seed: 0x{args.target:04X}")
    print(
        "Progress logging interval:"
        f" every {args.progress_every} delay values."
        " The brute-force loop still checks every single delay frame."
    )
    print(
        "Title-input transition timeout:"
        f" {args.title_transition_timeout} frames before a title pulse is"
        " treated as a dead input."
    )
    print(
        "Post-transition seed timeout:"
        f" {post_transition_seed_timeout} frames once the title task has left"
        " the pre-input window."
    )
    print(f"Additional attempt counter log: every {DEFAULT_ATTEMPT_LOG_EVERY} button attempts.")
    print(f"Baseline checkpoint savestate: {baseline_checkpoint_path}")
    print(f"Rolling checkpoint savestate: {checkpoint_path}")
    print(f"Working replay checkpoint savestate: {replay_checkpoint_path}")
    print(f"Working replay candidate savestate: {replay_candidate_path}")
    print(f"Read-only replay checkpoint savestate: {readonly_replay_checkpoint_path}")
    print(f"Replay metadata: {replay_metadata_path}")
    print(f"Locked baseline checkpoint savestate: {locked_baseline_checkpoint_path}")
    print(f"Locked baseline metadata: {locked_baseline_metadata_path}")
    print(f"Locked baseline save file: {locked_baseline_save_path}")
    print(f"Done savestate: {done_path}")
    print("This copy is pinned to the first-half workflow and uses 1sthalf.sav in the main <repo-root> folder.")

    boot_to_pre_second_press_checkpoint(core)
    save_state_file(core, baseline_checkpoint_path)
    save_state_file(core, checkpoint_path)
    use_runtime_checkpoint = False
    if _env_default_int(USE_RUNTIME_CHECKPOINT_ENV_NAME, 0):
        use_runtime_checkpoint = capture_runtime_checkpoint(core)
    print(f"Saved baseline checkpoint: {baseline_checkpoint_path}")
    print(f"Saved rolling checkpoint: {checkpoint_path}")
    if use_runtime_checkpoint:
        print(
            "Captured the optional in-memory scratch checkpoint for the rolling search."
            f" This opt-in path is controlled by {USE_RUNTIME_CHECKPOINT_ENV_NAME}=1."
        )
    else:
        print(
            "Using file-backed rolling savestates for the brute-force loop."
            " Each delay is restored from a saved non-input checkpoint instead of"
            " replaying the intro sequence."
        )

    attempts_checked = 0
    branch_failures = 0

    for delay_frames in range(args.max_delay + 1):
        # Every attempt starts from the same saved checkpoint so the only
        # changing variable is the delay before the second title-screen input.
        # For each delay, test A first and Start second from the same no-input
        # checkpoint before promoting that checkpoint by one idle frame for the
        # next delay value.
        for button_name, button_key in TITLE_INPUT_ATTEMPTS:
            try:
                seed_value, seed_frame, rng_value, observed_button = brute_force_attempt(
                    core=core,
                    baseline_checkpoint_path=baseline_checkpoint_path,
                    checkpoint_path=checkpoint_path,
                    use_runtime_checkpoint=use_runtime_checkpoint,
                    delay_frames=delay_frames,
                    button_name=button_name,
                    button_key=button_key,
                    seed_timeout=args.seed_timeout,
                    pre_input_checkpoint_path=replay_candidate_path,
                    title_transition_timeout=args.title_transition_timeout,
                    post_transition_seed_timeout=post_transition_seed_timeout,
                )
            except RuntimeError as exc:
                attempts_checked += 1
                branch_failures += 1
                print(
                    "Branch failed; continuing search:"
                    f" delay={delay_frames}"
                    f" button={button_name}"
                    f" total_branch_failures={branch_failures}"
                    f" detail={exc}"
                )
                if attempts_checked % DEFAULT_ATTEMPT_LOG_EVERY == 0:
                    print(
                        "Attempt counter:"
                        f" attempts={attempts_checked}"
                        f" current_delay={delay_frames}"
                        f" latest_button={button_name}"
                        f" branch_failures={branch_failures}"
                    )
                continue
            attempts_checked += 1

            should_log = delay_frames % args.progress_every == 0 or seed_value == args.target
            post_settle_rng = None
            if should_log and seed_value != args.target and args.settle_frames:
                # The settle value is only diagnostic, so do not burn extra
                # frames on every attempt. We only sample it for printed
                # progress lines and never before the success savestate.
                run_frames_fast(core, args.settle_frames)
                post_settle_rng = core.memory.u32[GRNG_VALUE_ADDR]

            if should_log:
                log_parts = [
                    "Attempt",
                    f" delay={delay_frames}",
                    f" button={observed_button}",
                    f" seed=0x{seed_value:04X}",
                    f" seed_frame={seed_frame}",
                    f" rng_at_seed=0x{rng_value:08X}",
                ]
                if post_settle_rng is not None:
                    log_parts.append(f" rng_after_settle=0x{post_settle_rng:08X}")
                print(
                    "".join(log_parts)
                )

            if attempts_checked % DEFAULT_ATTEMPT_LOG_EVERY == 0:
                print(
                    "Attempt counter:"
                    f" attempts={attempts_checked}"
                    f" current_delay={delay_frames}"
                    f" latest_button={observed_button}"
                    f" latest_seed=0x{seed_value:04X}"
                )

            if seed_value == args.target:
                prng_discerned = discern_initial_seed_from_rng_state(rng_value)
                if prng_discerned is None:
                    prng_discerned_seed = None
                    prng_discerned_steps = None
                    print(
                        "PRNG failsafe could not infer a nearby 16-bit seed state"
                        f" from rng_at_seed=0x{rng_value:08X}"
                        f" within +/-{DEFAULT_PRNG_SEED_DISCERN_WINDOW} LCRNG steps."
                    )
                else:
                    prng_discerned_seed, prng_discerned_steps = prng_discerned
                    print(
                        "PRNG failsafe inferred a nearby 16-bit seed candidate:"
                        f" seed=0x{prng_discerned_seed:04X}"
                        f" signed_steps_from_rng={prng_discerned_steps}"
                        f" rng_at_seed=0x{rng_value:08X}"
                    )
                # Save the success state at the exact hit frame before any
                # optional post-hit settle buffer can burn extra time.
                _probe_state(core, "firsthalf_target_hit_before_done_save")
                save_state_file(core, done_path)
                # `working1st-candidate` was saved immediately before this
                # exact winning A/Start press. Promote that file instead of
                # trying to restore and re-save the pre-button checkpoint after
                # the fact; the visible Qt runtime can otherwise reproduce a
                # nearby but wrong branch.
                commit_working_checkpoint(replay_candidate_path, replay_checkpoint_path)
                print(f"Probe firsthalf_committed_working_checkpoint: {replay_checkpoint_path}")
                timer1_count_pre, timer1_control_pre = read_timer1_state_from_checkpoint(
                    core,
                    replay_checkpoint_path,
                )
                # Restore the hit frame so the visible UI still reflects the
                # exact success state when we pause and show the alert.
                load_state_file(core, done_path)
                _probe_state(core, "firsthalf_restored_done_after_checkpoint_probe")
                replay_metadata = build_replay_metadata(
                    mgba_dir=mgba_dir,
                    rom_path=rom_path,
                    save_path=save_path,
                    working_checkpoint_path=replay_checkpoint_path,
                    readonly_checkpoint_path=readonly_replay_checkpoint_path,
                    target_seed=args.target,
                    delay_frames=delay_frames,
                    button_name=observed_button,
                    seed_frame=seed_frame,
                    rng_at_seed=rng_value,
                    prng_discerned_seed=prng_discerned_seed,
                    prng_discerned_steps_from_rng=prng_discerned_steps,
                    timer1_count_pre=timer1_count_pre,
                    timer1_control_pre=timer1_control_pre,
                    pre_input_neutral_frames=DEFAULT_PRE_INPUT_NEUTRAL_FRAMES,
                    seed_timeout=args.seed_timeout,
                )
                write_readonly_replay_artifacts(
                    source_checkpoint_path=replay_checkpoint_path,
                    readonly_checkpoint_path=readonly_replay_checkpoint_path,
                    metadata_path=replay_metadata_path,
                    metadata=replay_metadata,
                )
                locked_baseline_created = write_locked_baseline_artifacts(
                    source_checkpoint_path=readonly_replay_checkpoint_path,
                    source_metadata=replay_metadata,
                    source_save_path=save_path,
                    baseline_checkpoint_path=locked_baseline_checkpoint_path,
                    baseline_metadata_path=locked_baseline_metadata_path,
                    baseline_save_path=locked_baseline_save_path,
                    mgba_dir=mgba_dir,
                    overwrite=os.environ.get(OVERWRITE_LOCKED_BASELINE_ENV_NAME) == "1",
                )
                if locked_baseline_created:
                    print(
                        "Locked first-half baseline created:"
                        f" checkpoint={locked_baseline_checkpoint_path}"
                        f" metadata={locked_baseline_metadata_path}"
                        f" save={locked_baseline_save_path}"
                    )
                else:
                    print(
                        "Locked first-half baseline already exists; leaving it unchanged:"
                        f" checkpoint={locked_baseline_checkpoint_path}"
                        f" metadata={locked_baseline_metadata_path}"
                        f" save={locked_baseline_save_path}"
                    )
                timer1_count_readonly, timer1_control_readonly = read_timer1_state_from_checkpoint(
                    core,
                    readonly_replay_checkpoint_path,
                )
                print(
                    "Probe firsthalf_readonly_timer1:"
                    f" count=0x{timer1_count_readonly:04X}"
                    f" control=0x{timer1_control_readonly:04X}"
                )
                load_state_file(core, done_path)
                _probe_state(core, "firsthalf_restored_done_after_readonly_probe")
                print(
                    "Match found:"
                    f" delay={delay_frames}"
                    f" button={observed_button}"
                    f" seed_frame={seed_frame}"
                    f" seed=0x{seed_value:04X}"
                    f" rng_at_seed=0x{rng_value:08X}"
                    f" saved={done_path}"
                    f" working_checkpoint={replay_checkpoint_path}"
                    f" readonly_checkpoint={readonly_replay_checkpoint_path}"
                    f" metadata={replay_metadata_path}"
                )
                write_status_marker_from_env(
                    STATUS_ENV_NAME,
                    {
                        "script": "1sthalf.py",
                        "matched": True,
                        "target_seed": int(args.target) & 0xFFFF,
                        "seed": int(seed_value) & 0xFFFF,
                        "delay_frames": int(delay_frames),
                        "button_name": str(observed_button),
                        "seed_frame": int(seed_frame),
                        "rng_at_seed": int(rng_value) & 0xFFFFFFFF,
                        "prng_discerned_seed": None
                        if prng_discerned_seed is None
                        else int(prng_discerned_seed) & 0xFFFF,
                        "prng_discerned_steps_from_rng": prng_discerned_steps,
                        "timer1_count_pre": int(timer1_count_pre) & 0xFFFF,
                        "timer1_control_pre": int(timer1_control_pre) & 0xFFFF,
                        "working_checkpoint": str(replay_checkpoint_path),
                        "readonly_checkpoint": str(readonly_replay_checkpoint_path),
                        "metadata": str(replay_metadata_path),
                        "locked_baseline_checkpoint": str(locked_baseline_checkpoint_path),
                        "locked_baseline_metadata": str(locked_baseline_metadata_path),
                        "locked_baseline_save": str(locked_baseline_save_path),
                        "locked_baseline_created": bool(locked_baseline_created),
                        "done_savestate": str(done_path),
                    },
                )
                notify_success_in_qt(
                    core,
                    qt_mode=qt_mode,
                    target_seed=args.target,
                    delay_frames=delay_frames,
                    button_name=observed_button,
                    seed_frame=seed_frame,
                    done_path=done_path,
                )
                return 0

        if delay_frames == args.max_delay:
            break
        advance_checkpoint_one_frame(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            next_delay_frames=delay_frames + 1,
        )

    print(
        "No match found:"
        f" target=0x{args.target:04X}"
        f" searched_delays=0..{args.max_delay}"
        f" branch_failures={branch_failures}"
    )
    _remove_file_if_present(replay_candidate_path)
    write_status_marker_from_env(
        STATUS_ENV_NAME,
        {
            "script": "1sthalf.py",
            "matched": False,
            "target_seed": int(args.target) & 0xFFFF,
            "searched_max_delay": int(args.max_delay),
            "attempts": int(attempts_checked),
            "branch_failures": int(branch_failures),
            "working_candidate_removed": True,
        },
    )
    return 1


if __name__ == "__main__":
    exit_code = main()
    if not _qt_mode_enabled():
        raise SystemExit(exit_code)
