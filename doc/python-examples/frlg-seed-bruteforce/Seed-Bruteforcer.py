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
4. Save the fixed early route as reusable input tapes plus one rolling
   checkpoint state there as `1 from egg - checkpoint`.
5. For each delay value, try `A` first and `Start` second from that exact
   no-input checkpoint.
6. If neither button matches, restore the same checkpoint, advance exactly one
   idle frame after both button attempts, and save the next no-input checkpoint
   for the following delay.
7. When the game generates a seed, read the stopped Timer 1 counter value.
8. If the seed matches the target, save a file-backed mGBA savestate named
   `1 from egg.ss0`.

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

- target seed defaults to `0xCD39`
- the maintained default widened search scans title-skip lanes `0..31` because
  the old `0xFBC7`-specific lane 16 pin is not valid for the new target until a
  fresh recipe is found
- the previous verified `0xFBC7` hit recipe was:
  `title_skip_start_delay = 16`, `delay_frames = 561`, final button `A`
- the persistent in-game save is `1 from egg.sav`
- that save is expected in the main `<repo-root>` folder
- the fixed setup route is cached as
  `1 from egg - reset-to-title-baseline.inputtape.json` and
  `1 from egg - title-baseline-to-checkpoint.inputtape.json`
- on success the script writes a file-backed savestate named `1 from egg.ss0`
  in that same `<repo-root>` folder and pauses emulation
- on success it also writes `1 from egg - replay-readonly` plus
  `1 from egg - replay-metadata.json` so replay scripts use the exact observed
  delay/button/seed contract
- if no locked baseline exists yet, that same hit is copied into the
  read-only locked checkpoint `1 from egg - locked-baseline` and the matching
  metadata file `1 from egg - locked-baseline-metadata.json`; both point back
  at the shared canonical save `1 from egg.sav`
  and later searches leave that baseline alone
  unless `MGBA_OVERWRITE_LOCKED_FIRSTHALF_BASELINE=1` is set

This copy is intentionally separate from `frlg_seed_bruteforce.py` so the
first-half workflow can change without disturbing the general-purpose script.
It is the specialized title-seed tool for the current paper trail in:

- `WORKFLOW_DECISION_LOG.md`
- `INITIAL_SEED_CSV_REFERENCE.md`
- `timer1_observations.md`

Usage:
    <repo-root>\.venv-mgba\bin\python.exe Seed-Bruteforcer.py --target 0xCD39 --max-delay 500000000

Custom workspace addition:

This same script can also run inside the visible Qt GUI in this workspace via
`mGBA.exe --script Seed-Bruteforcer.py ...` or by loading it from
`Tools > Scripting...`. The sibling `run_frlg_seed_bruteforce_visible.ps1`
launcher remains the generic visible-Qt launcher for
`frlg_seed_bruteforce.py`, not this first-half copy. In Qt mode, `MGBA_*`
environment variables can override the normal defaults without changing mGBA's
own command-line parsing.

In this copy, `1 from egg.sav` is the required in-game save file. The generated
`1 from egg - checkpoint` file is a private workflow checkpoint, while
`1 from egg.ss0` uses the normal mGBA `.ss0` savestate extension so downstream
tools can recognize it as a save state without guessing from context.
"""

from __future__ import annotations

import argparse
import importlib.util
import configparser
import json
import os
import shutil
import stat
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

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
PROJECT_ROOT_ARTIFACT_DIR_NAME = "Artifacts"
DEFAULT_SAVE_NAME = "1 from egg.sav"
DEFAULT_TITLE_BASELINE_CHECKPOINT_STATE_NAME = "1 from egg - title-baseline"
DEFAULT_TITLE_BASELINE_TAPE_NAME = "1 from egg - reset-to-title-baseline.inputtape.json"
DEFAULT_BASELINE_CHECKPOINT_STATE_NAME = "1 from egg - base-checkpoint"
DEFAULT_PRE_INPUT_TAPE_NAME = "1 from egg - title-baseline-to-checkpoint.inputtape.json"
DEFAULT_SUCCESS_STATE_NAME = "1 from egg.ss0"
DEFAULT_CHECKPOINT_STATE_NAME = "1 from egg - checkpoint"
DEFAULT_CHECKPOINT_METADATA_NAME = "1 from egg - checkpoint-metadata.json"
DEFAULT_REPLAY_CHECKPOINT_STATE_NAME = "1 from egg - replay-working"
DEFAULT_REPLAY_CANDIDATE_STATE_NAME = "1 from egg - replay-candidate"
DEFAULT_READONLY_REPLAY_CHECKPOINT_STATE_NAME = "1 from egg - replay-readonly"
DEFAULT_REPLAY_METADATA_NAME = "1 from egg - replay-metadata.json"
DEFAULT_LOCKED_BASELINE_CHECKPOINT_STATE_NAME = "1 from egg - locked-baseline"
DEFAULT_LOCKED_BASELINE_METADATA_NAME = "1 from egg - locked-baseline-metadata.json"
DEFAULT_LOCKED_BASELINE_SAVE_NAME = "1 from egg.sav"
REPLAY_METADATA_VERSION = 2
CHECKPOINT_METADATA_VERSION = 1
STATUS_ENV_NAME = "MGBA_FIRSTHALF_STATUS_PATH"
PROGRESS_ENV_NAME = "MGBA_FIRSTHALF_PROGRESS_PATH"
SUPPRESS_SUCCESS_WARNING_ENV_NAME = "MGBA_SUPPRESS_SUCCESS_WARNING"
OVERWRITE_LOCKED_BASELINE_ENV_NAME = "MGBA_OVERWRITE_LOCKED_FIRSTHALF_BASELINE"
USE_RUNTIME_CHECKPOINT_ENV_NAME = "MGBA_USE_RUNTIME_CHECKPOINT"
RUNTIME_CHECKPOINT_SAVE_EVERY_ENV_NAME = "MGBA_RUNTIME_CHECKPOINT_SAVE_EVERY"
# Quick-edit defaults.
# Change DEFAULT_TARGET_SEED if you want a different target without passing
# --target on the command line.
DEFAULT_TARGET_SEED = 0xCD39
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
DEFAULT_PRE_INPUT_NEUTRAL_FRAMES = 0
# The current `0xCD39` target is not tied to the old `0xFBC7` lane-16 recipe.
# Search a bounded title-skip lane band by default until a fresh CD39 recipe is
# found and replay metadata can pin the exact winning branch.
DEFAULT_TITLE_SKIP_START_DELAY_START = 0
DEFAULT_TITLE_SKIP_START_DELAY_MAX = 31
DEFAULT_PROGRESS_EVERY = 50
DEFAULT_ATTEMPT_LOG_EVERY = 100
DEFAULT_TITLE_SKIP_TIMEOUT = 600
DEFAULT_CHECKPOINT_WAIT_TIMEOUT = 600
DEFAULT_PRNG_SEED_DISCERN_WINDOW = 131_072
# In Qt scratch-checkpoint mode, non-matching delays do not need a file-backed
# savestate every frame. Keep periodic disk checkpoints for crash recovery while
# letting the hot path stay in memory.
DEFAULT_RUNTIME_CHECKPOINT_SAVE_EVERY = 100

_INPUT_TAPE_MODULE: Any | None = None

GRNG_VALUE_ADDR = 0x03005000
GMAIN_VBLANK2_ADDR = 0x03003114
TIMER1_COUNT_ADDR = 0x04000104
TIMER1_CONTROL_ADDR = 0x04000106
KEYINPUT_ADDR = 0x04000130
INITIAL_SEED_MIRROR_ADDR = 0x02020000
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
    r"""Find the main `<repo-root>` folder for `1 from egg.sav` and savestates.

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


def artifact_dir_for(mgba_dir: Path) -> Path:
    """Return where root-level brute-force artifacts should live.

    The main project root keeps one-off `.sav`, `.ss0`, and replay JSON files
    under `Artifacts` so the source root stays readable. Dedicated live-lane
    work directories still keep their artifacts directly inside the lane.
    """

    normalized = Path(os.path.abspath(os.fspath(mgba_dir.expanduser())))
    default_root = Path(os.path.abspath(os.fspath(DEFAULT_MGBA_DIR)))
    if normalized == default_root:
        return mgba_dir / PROJECT_ROOT_ARTIFACT_DIR_NAME
    return mgba_dir


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

    Both the brute-force and replay workflows now use the shared read-only
    `1 from egg.sav` file. They load it with `temporary=True` so mGBA treats it
    as an input artifact instead of a save it should mutate in place.
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

    if getattr(core, "memory", None) is None and hasattr(core, "reset"):
        # Host-side cores created by `mgba.core.load_path(...)` do not expose a
        # stable memory view until they have been reset once. Warm the core
        # first so later save/state inspection never touches a `None` memory
        # object after a seemingly successful save load.
        core.reset()

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

    This helper is intentionally diagnostic only. The workflow decisions in the
    markdown treat Timer 1 as authoritative and the PRNG orbit only as a
    failsafe for drift explanation and replay comparisons.
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
class SeedObservation:
    """One observed post-title seed event plus secondary witness data.

    Timer 1 remains the primary source of truth for the first-half workflow.
    The extra fields exist only to explain the same event more completely:

    - `timer1_seed` is the direct 16-bit seed read from the stopped timer
    - `initial_seed_mirror_value` is the 16-bit value sitting at `0x02020000`
      after the hit; Real96's fresh-game Lua script uses that address as its
      main signal, but in this loaded-save workflow it is only a second witness
    - `prng_discerned_seed` is the nearest 16-bit seeded state inferred from
      the current `gRngValue` by walking the LCRNG orbit backward/forward
    - `seed_source` records which value won if the script ever has to fall back

    Today that source is expected to stay `"timer1"` for valid FR/LG title hits.
    """

    seed_value: int
    seed_source: str
    timer1_seed: int | None
    rng_value: int
    initial_seed_mirror_value: int | None
    initial_seed_mirror_changed_from_pre_input: bool | None
    initial_seed_mirror_matches_timer1: bool | None
    prng_discerned_seed: int | None
    prng_discerned_steps_from_rng: int | None


@dataclass(frozen=True)
class AttemptObservation:
    """Structured return value for one brute-force branch attempt.

    The hot loop now carries more than just the Timer 1 seed and raw RNG value.
    Bundling the seed event into one object keeps the main loop readable while
    preserving the extra diagnostics used for logs, status JSON, and replay
    metadata.
    """

    seed_observation: SeedObservation
    seed_frame: int
    button_name: str


def infer_seed_from_current_rng_state(core: GBA) -> tuple[int, int, int] | None:
    """Infer a 16-bit seed candidate from the live PRNG state.

    This is the explicit LCRNG/LCRNG(R) failsafe requested for the first-half
    workflow. It does not replace Timer 1; it provides a second line of
    evidence from the current `gRngValue` at the instant the seed was observed.
    """

    try:
        rng_value = core.memory.u32[GRNG_VALUE_ADDR]
    except Exception:
        return None
    inferred = discern_initial_seed_from_rng_state(rng_value)
    if inferred is None:
        return None
    inferred_seed, signed_steps = inferred
    return inferred_seed, signed_steps, rng_value


def observe_initial_seed_mirror(core: GBA) -> int | None:
    """Read FR/LG's 16-bit temp-seed/TID mirror when it is available.

    The external Real96 Lua script watches `0x02020000` directly. That address
    is still useful here, but only as a witness after Timer 1 already proved
    the seed event happened. This keeps the loaded-save workflow anchored to
    hardware timing instead of to a RAM copy that may already hold unrelated
    save-derived data before the title hit.
    """

    try:
        return core.memory.u16[INITIAL_SEED_MIRROR_ADDR]
    except Exception:
        return None


def observe_seed_generation(
    core: GBA,
    *,
    pre_input_seed_mirror: int | None = None,
) -> SeedObservation | None:
    """Observe one seed event with Timer 1 first and secondary witnesses after it.

    The only reliable sign that FR/LG has generated the title seed is that
    Timer 1 stopped. Once that happens, capture the timer seed and the current
    PRNG state together, plus the temp-seed mirror at `0x02020000`, so the
    script can both make the decision now and explain the same decision later
    in replay metadata or failure logs.
    """

    timer1_seed = observe_initial_seed_from_timer1(core)
    if timer1_seed is None:
        return None

    initial_seed_mirror_value = observe_initial_seed_mirror(core)
    initial_seed_mirror_changed_from_pre_input = None
    if initial_seed_mirror_value is not None and pre_input_seed_mirror is not None:
        initial_seed_mirror_changed_from_pre_input = (
            initial_seed_mirror_value != pre_input_seed_mirror
        )
    initial_seed_mirror_matches_timer1 = None
    if initial_seed_mirror_value is not None:
        initial_seed_mirror_matches_timer1 = initial_seed_mirror_value == timer1_seed
        if not initial_seed_mirror_matches_timer1:
            print(
                "Initial-seed mirror disagrees with Timer 1:"
                f" timer1_seed=0x{timer1_seed:04X}"
                f" initial_seed_mirror=0x{initial_seed_mirror_value:04X}"
                " Timer 1 remains authoritative."
            )

    inferred = infer_seed_from_current_rng_state(core)
    if inferred is None:
        return SeedObservation(
            seed_value=timer1_seed,
            seed_source="timer1",
            timer1_seed=timer1_seed,
            rng_value=0,
            initial_seed_mirror_value=initial_seed_mirror_value,
            initial_seed_mirror_changed_from_pre_input=initial_seed_mirror_changed_from_pre_input,
            initial_seed_mirror_matches_timer1=initial_seed_mirror_matches_timer1,
            prng_discerned_seed=None,
            prng_discerned_steps_from_rng=None,
        )

    prng_discerned_seed, prng_discerned_steps, rng_value = inferred
    if prng_discerned_seed != timer1_seed:
        print(
            "PRNG failsafe disagrees with Timer 1:"
            f" timer1_seed=0x{timer1_seed:04X}"
            f" prng_discerned_seed=0x{prng_discerned_seed:04X}"
            f" signed_steps_from_rng={prng_discerned_steps}"
            f" rng=0x{rng_value:08X}"
            " Timer 1 remains authoritative."
        )
    return SeedObservation(
        seed_value=timer1_seed,
        seed_source="timer1",
        timer1_seed=timer1_seed,
        rng_value=rng_value,
        initial_seed_mirror_value=initial_seed_mirror_value,
        initial_seed_mirror_changed_from_pre_input=initial_seed_mirror_changed_from_pre_input,
        initial_seed_mirror_matches_timer1=initial_seed_mirror_matches_timer1,
        prng_discerned_seed=prng_discerned_seed,
        prng_discerned_steps_from_rng=prng_discerned_steps,
    )


def _format_initial_seed_mirror_log_parts(seed_observation: SeedObservation) -> list[str]:
    """Return stable log fragments for the optional temp-seed mirror witness."""

    if seed_observation.initial_seed_mirror_value is None:
        return []

    parts = [f" initial_seed_mirror=0x{seed_observation.initial_seed_mirror_value:04X}"]
    if seed_observation.initial_seed_mirror_changed_from_pre_input is not None:
        parts.append(
            " initial_seed_mirror_changed_from_pre_input="
            + ("yes" if seed_observation.initial_seed_mirror_changed_from_pre_input else "no")
        )
    if seed_observation.initial_seed_mirror_matches_timer1 is not None:
        parts.append(
            " initial_seed_mirror_matches_timer1="
            + ("yes" if seed_observation.initial_seed_mirror_matches_timer1 else "no")
        )
    return parts


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


@dataclass(frozen=True)
class FirstHalfPaths:
    r"""Named on-disk artifacts for one first-half brute-force run.

    The markdown docs describe the workflow in terms of stable files under the
    main `<repo-root>` root. Bundling those paths in one dataclass makes the
    orchestration read the same way:

    - load one persistent save
    - create one baseline checkpoint
    - reuse one rolling checkpoint
    - export one success state and one replay contract
    """

    mgba_dir: Path
    save_path: Path
    title_baseline_checkpoint_path: Path
    title_baseline_tape_path: Path
    baseline_checkpoint_path: Path
    pre_input_tape_path: Path
    checkpoint_path: Path
    checkpoint_metadata_path: Path
    success_path: Path
    replay_checkpoint_path: Path
    replay_candidate_path: Path
    readonly_replay_checkpoint_path: Path
    replay_metadata_path: Path
    locked_baseline_checkpoint_path: Path
    locked_baseline_metadata_path: Path
    locked_baseline_save_path: Path


def _build_paths(mgba_dir: Path) -> FirstHalfPaths:
    """Resolve the canonical first-half artifact layout under one root."""

    artifact_dir = artifact_dir_for(mgba_dir)
    return FirstHalfPaths(
        mgba_dir=mgba_dir,
        save_path=artifact_dir / DEFAULT_SAVE_NAME,
        title_baseline_checkpoint_path=artifact_dir / DEFAULT_TITLE_BASELINE_CHECKPOINT_STATE_NAME,
        title_baseline_tape_path=artifact_dir / DEFAULT_TITLE_BASELINE_TAPE_NAME,
        baseline_checkpoint_path=artifact_dir / DEFAULT_BASELINE_CHECKPOINT_STATE_NAME,
        pre_input_tape_path=artifact_dir / DEFAULT_PRE_INPUT_TAPE_NAME,
        checkpoint_path=artifact_dir / DEFAULT_CHECKPOINT_STATE_NAME,
        checkpoint_metadata_path=artifact_dir / DEFAULT_CHECKPOINT_METADATA_NAME,
        success_path=artifact_dir / DEFAULT_SUCCESS_STATE_NAME,
        replay_checkpoint_path=artifact_dir / DEFAULT_REPLAY_CHECKPOINT_STATE_NAME,
        replay_candidate_path=artifact_dir / DEFAULT_REPLAY_CANDIDATE_STATE_NAME,
        readonly_replay_checkpoint_path=artifact_dir / DEFAULT_READONLY_REPLAY_CHECKPOINT_STATE_NAME,
        replay_metadata_path=artifact_dir / DEFAULT_REPLAY_METADATA_NAME,
        locked_baseline_checkpoint_path=artifact_dir / DEFAULT_LOCKED_BASELINE_CHECKPOINT_STATE_NAME,
        locked_baseline_metadata_path=artifact_dir / DEFAULT_LOCKED_BASELINE_METADATA_NAME,
        locked_baseline_save_path=artifact_dir / DEFAULT_LOCKED_BASELINE_SAVE_NAME,
    )


def _load_input_tape_module():
    """Load the shared Python input-tape helper once for this script copy.

    `Seed-Bruteforcer.py` lives under `frlg-seed-bruteforce`, while the shared
    tape helper lives one directory up at `doc/python-examples/input_tape.py`.
    Keep the import lazy so host-side callers that never touch the route-tape
    setup path do not pay extra startup work.
    """

    global _INPUT_TAPE_MODULE
    if _INPUT_TAPE_MODULE is not None:
        return _INPUT_TAPE_MODULE

    module_path = Path(__file__).resolve().parents[1] / "input_tape.py"
    module_name = "mgba_firsthalf_input_tape"
    cached_module = sys.modules.get(module_name)
    if cached_module is not None:
        _INPUT_TAPE_MODULE = cached_module
        return cached_module

    spec = importlib.util.spec_from_file_location(module_name, module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not load shared input_tape helper from {module_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    _INPUT_TAPE_MODULE = module
    return module


def _record_route_tape(
    core: GBA,
    *,
    metadata: dict[str, object],
    runner: Callable[[Any], Any],
):
    """Record one deterministic setup route into a portable input tape.

    The early intro/title work is fixed route data, not hot-loop work. Record
    it once through the shared tape helper so later rebuilds can replay the
    same exact button masks without open-coding the whole route again.
    """

    input_tape = _load_input_tape_module()
    recorder = input_tape.InputTapeRecorder(core, metadata=metadata)
    with recorder as route_core:
        result = runner(route_core)
    return recorder.finish(), result


def _prepend_neutral_frames_to_tape(tape, *, neutral_frames: int):
    """Return one derived tape with extra leading no-input frames.

    Widened first-half lanes only differ by how long the script waits before
    the first title-scene skip pulse. Prefixing `NONE` frames keeps that lane
    difference in data instead of duplicating the whole title route.
    """

    frames = int(neutral_frames)
    if frames <= 0:
        return tape
    input_tape = _load_input_tape_module()
    runs = [input_tape.InputRun(mask=0, frames=frames), *list(tape.runs)]
    metadata = dict(getattr(tape, "metadata", {}))
    metadata["prepended_neutral_frames"] = frames
    return input_tape.from_runs(runs, metadata=metadata)


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
        try:
            core.load_scratch_state()
        except RuntimeError:
            # Scratch checkpoints live only inside one visible Qt process. A
            # fresh restart must fall back to the persisted file-backed
            # checkpoint instead of treating the missing scratch state as fatal.
            pass
        else:
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


def prime_runtime_checkpoint(
    core: GBA,
    checkpoint_path: Path,
    *,
    qt_mode: bool | None = None,
) -> bool:
    """Seed the Qt scratch slot from the prepared file-backed checkpoint.

    The scratch checkpoint is process-local. A fresh visible Qt launch starts
    with no useful scratch state, so the hot loop must prime it from the
    already-validated rolling checkpoint, not from the raw post-save-load core
    state that exists before checkpoint preparation.
    """

    _call_load_state_file(core, checkpoint_path, qt_mode=qt_mode)
    core.set_keys(raw=0)
    return capture_runtime_checkpoint(core, qt_mode=qt_mode)


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
    to the dedicated `1 from egg - replay-working` file, and lets the caller restore the
    already-saved success state afterward.

    That split is deliberate:
    - `1 from egg.ss0` is the operator-facing post-hit success state
    - `1 from egg - replay-working` is the replay-facing pre-input checkpoint
    - `1 from egg - replay-readonly` is the immutable copy a later replay can trust
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

    `1 from egg - replay-candidate` is written before each attempted title input. Only
    the candidate from the branch that actually hit the target should become
    `1 from egg - replay-working`, so the final handoff is a filesystem replace instead of a
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
    seed_source: str = "timer1",
    timer1_seed_observed: int | None = None,
    initial_seed_mirror: int | None = None,
    initial_seed_mirror_changed_from_pre_input: bool | None = None,
    initial_seed_mirror_matches_timer1: bool | None = None,
    prng_discerned_seed: int | None = None,
    prng_discerned_steps_from_rng: int | None = None,
    timer1_count_pre: int,
    timer1_control_pre: int,
    pre_input_neutral_frames: int,
    seed_timeout: int,
    title_skip_start_delay: int = 0,
) -> dict[str, object]:
    """Build the replay contract consumed by `Seed-Replicator.py`.

    The replay script should not carry hardcoded hit data. The brute-force
    script is the source of truth because it is the one that observed the seed,
    the exact pre-input checkpoint, and the final button used for that hit.
    """

    metadata = {
        "metadata_version": REPLAY_METADATA_VERSION,
        "producer": "Seed-Bruteforcer.py",
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
        "seed_source": str(seed_source),
        "timer1_count_pre": int(timer1_count_pre) & 0xFFFF,
        "timer1_control_pre": int(timer1_control_pre) & 0xFFFF,
        "pre_input_neutral_frames": int(pre_input_neutral_frames),
        "pre_input_neutral_frames_included": True,
        "title_skip_start_delay": int(title_skip_start_delay),
        "seed_timeout": int(seed_timeout),
        "audio_killswitch": True,
        "no_render_mode": True,
        "fast_forward": True,
        "fast_forward_ratio": "unbounded",
        "unthrottled_frame_advance": True,
        "locked_baseline": False,
    }
    if timer1_seed_observed is not None:
        metadata["timer1_seed_observed"] = int(timer1_seed_observed) & 0xFFFF
    if initial_seed_mirror is not None:
        metadata["initial_seed_mirror"] = int(initial_seed_mirror) & 0xFFFF
    if initial_seed_mirror_changed_from_pre_input is not None:
        metadata["initial_seed_mirror_changed_from_pre_input"] = bool(
            initial_seed_mirror_changed_from_pre_input
        )
    if initial_seed_mirror_matches_timer1 is not None:
        metadata["initial_seed_mirror_matches_timer1"] = bool(
            initial_seed_mirror_matches_timer1
        )
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
    """Retarget one replay contract at the stable locked baseline artifacts.

    The locked baseline no longer owns a separate `.sav` copy. It is a
    read-only checkpoint plus metadata pair that both refer back to the shared
    canonical `1 from egg.sav` file.
    """

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


def write_progress_marker(payload: dict[str, object]) -> None:
    """Write optional in-flight progress for long live brute-force sessions.

    The status marker is only written on success or terminal failure. During a
    real GUI run that can leave the operator blind for several minutes, so this
    side marker exposes the latest known delay / seed pair when explicitly
    requested via `MGBA_FIRSTHALF_PROGRESS_PATH`.
    """

    write_status_marker_from_env(PROGRESS_ENV_NAME, payload)


def write_checkpoint_metadata(path: Path, *, delay_frames: int, target_seed: int) -> None:
    """Persist the rolling checkpoint's current delay frame.

    `1 from egg - checkpoint` is intentionally reused across launches so the script
    does not need to replay the intro/title skips every time it is restarted.
    The sidecar JSON records which neutral-frame delay that checkpoint already
    represents, and which 16-bit target seed that search was pursuing, so a
    resumed run can continue from the right point instead of silently relabeling
    a later checkpoint as delay zero for a different seed target.
    """

    path.write_text(
        json.dumps(
            {
                "metadata_version": CHECKPOINT_METADATA_VERSION,
                "producer": "Seed-Bruteforcer.py",
                "delay_frames": int(delay_frames),
                "target_seed": int(target_seed) & 0xFFFF,
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
        newline="\n",
    )


def read_checkpoint_metadata(path: Path, *, expected_target_seed: int) -> int | None:
    """Return the saved rolling-checkpoint delay, or None if it is unusable."""

    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if payload.get("metadata_version") != CHECKPOINT_METADATA_VERSION:
        return None
    target_seed = payload.get("target_seed")
    if not isinstance(target_seed, int) or (target_seed & 0xFFFF) != (int(expected_target_seed) & 0xFFFF):
        return None
    delay_frames = payload.get("delay_frames")
    if not isinstance(delay_frames, int) or delay_frames < 0:
        return None
    return delay_frames


def runtime_checkpoint_save_every() -> int:
    """Return how often scratch-checkpoint searches refresh disk recovery."""

    return max(
        1,
        _env_default_int(
            RUNTIME_CHECKPOINT_SAVE_EVERY_ENV_NAME,
            DEFAULT_RUNTIME_CHECKPOINT_SAVE_EVERY,
        ),
    )


def inspect_checkpoint_state(
    core: GBA,
    checkpoint_path: Path,
    *,
    qt_mode: bool | None = None,
) -> tuple[bool, str]:
    """Report whether one saved checkpoint is reusable for the title search.

    A usable first-half checkpoint must still be pre-seed and, when title-task
    RAM is readable, it must still sit in the RUN/state=1 no-input window just
    before the final title button press.
    """

    if not checkpoint_path.is_file():
        return False, "missing"
    try:
        _call_load_state_file(core, checkpoint_path, qt_mode=qt_mode)
    except Exception as exc:
        return False, f"load failed: {exc}"
    core.set_keys(raw=0)
    timer1_count, timer1_control = read_timer1_state(core)
    if observe_initial_seed_from_timer1(core) is not None:
        return (
            False,
            "Timer 1 already stopped"
            f" (count=0x{timer1_count:04X} control=0x{timer1_control:04X})",
        )
    title_status = title_input_checkpoint_status(core)
    if title_status is False:
        return (
            False,
            "title task no longer at RUN/state=1"
            f" (count=0x{timer1_count:04X} control=0x{timer1_control:04X})",
        )
    frame_counter = getattr(core, "frame_counter", 0)
    return (
        True,
        f"frame_counter={frame_counter}"
        f" timer1_count=0x{timer1_count:04X}"
        f" timer1_control=0x{timer1_control:04X}",
    )


def inspect_title_baseline_state(
    core: GBA,
    checkpoint_path: Path,
    *,
    qt_mode: bool | None = None,
) -> tuple[bool, str]:
    """Report whether one saved title-detected checkpoint is reusable.

    The widened title search needs a reusable no-input checkpoint at the moment
    the title task first exists. It must still have Timer 1 running and the
    title task present, but it does not need to be at RUN/state=1 yet.
    """

    if not checkpoint_path.is_file():
        return False, "missing"
    try:
        _call_load_state_file(core, checkpoint_path, qt_mode=qt_mode)
    except Exception as exc:
        return False, f"load failed: {exc}"
    core.set_keys(raw=0)
    try:
        timer1_count, timer1_control = read_timer1_state(core)
    except Exception as exc:
        return False, f"memory unavailable after load: {exc}"
    if observe_initial_seed_from_timer1(core) is not None:
        return (
            False,
            "Timer 1 already stopped"
            f" (count=0x{timer1_count:04X} control=0x{timer1_control:04X})",
        )
    info = find_title_task(core)
    if info is None:
        return (
            False,
            "title task missing"
            f" (count=0x{timer1_count:04X} control=0x{timer1_control:04X})",
        )
    frame_counter = getattr(core, "frame_counter", 0)
    return (
        True,
        f"frame_counter={frame_counter}"
        f" timer1_count=0x{timer1_count:04X}"
        f" timer1_control=0x{timer1_control:04X}"
        f" scene={info.scene} state={info.state}",
    )


def _save_search_checkpoints(
    core: GBA,
    *,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    checkpoint_metadata_path: Path,
    use_runtime_checkpoint: bool,
    target_seed: int,
) -> None:
    """Persist the canonical baseline plus rolling checkpoint pair."""

    save_state_file(core, baseline_checkpoint_path)
    save_state_file(core, checkpoint_path)
    write_checkpoint_metadata(
        checkpoint_metadata_path,
        delay_frames=0,
        target_seed=target_seed,
    )
    if use_runtime_checkpoint:
        capture_runtime_checkpoint(core)


def _record_title_baseline_tape(
    core: GBA,
    *,
    tape_path: Path,
) -> str:
    """Record and save the opening route that stops at title-task detection."""

    input_tape = _load_input_tape_module()
    tape, _ = _record_route_tape(
        core,
        metadata={
            "workflow": "firsthalf-seed-bruteforce",
            "route_segment": "reset-to-title-baseline",
            "script": "Seed-Bruteforcer.py",
        },
        runner=boot_to_title_detected_checkpoint,
    )
    input_tape.write_tape(tape_path, tape)
    return f"recorded_tape_frames={tape.frame_count}"


def _rebuild_title_baseline_checkpoint_from_tape(
    core: GBA,
    *,
    tape_path: Path,
) -> str:
    """Replay the saved opening route tape back to the title baseline."""

    input_tape = _load_input_tape_module()
    tape = input_tape.read_tape(tape_path)
    core.reset()
    input_tape.replay_tape(core, tape)
    if observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 already stopped after replaying the title-baseline tape.")
    info = find_title_task(core)
    if info is None:
        raise RuntimeError("Title-baseline tape did not leave the title task alive.")
    return (
        f"replayed_tape_frames={tape.frame_count}"
        f" frame_counter={getattr(core, 'frame_counter', 0)}"
        f" scene={info.scene} state={info.state}"
    )


def prepare_title_baseline_checkpoint(
    core: GBA,
    *,
    paths: FirstHalfPaths,
    qt_mode: bool,
) -> str:
    """Ensure the earlier title-detected baseline exists and is reusable."""

    title_baseline_ok, title_baseline_detail = inspect_title_baseline_state(
        core,
        paths.title_baseline_checkpoint_path,
        qt_mode=qt_mode,
    )
    if title_baseline_ok:
        print(
            "Reusing saved title baseline checkpoint:"
            f" checkpoint={paths.title_baseline_checkpoint_path}"
            f" detail={title_baseline_detail}"
        )
        return title_baseline_detail

    tape_detail = ""
    if paths.title_baseline_tape_path.is_file():
        print(
            "Saved title baseline checkpoint is missing or stale; replaying the"
            " saved opening route tape to rebuild it."
        )
        try:
            tape_detail = _rebuild_title_baseline_checkpoint_from_tape(
                core,
                tape_path=paths.title_baseline_tape_path,
            )
        except Exception as exc:
            print(
                "Saved title-baseline tape could not rebuild the checkpoint;"
                f" recording a fresh opening route instead: {exc}"
            )
            tape_detail = _record_title_baseline_tape(
                core,
                tape_path=paths.title_baseline_tape_path,
            )
    else:
        print(
            "No usable saved title baseline checkpoint found; recording the"
            " opening route once so widened title lanes can reuse it later."
        )
        tape_detail = _record_title_baseline_tape(
            core,
            tape_path=paths.title_baseline_tape_path,
        )

    save_state_file(core, paths.title_baseline_checkpoint_path)
    title_baseline_ok, title_baseline_detail = inspect_title_baseline_state(
        core,
        paths.title_baseline_checkpoint_path,
        qt_mode=qt_mode,
    )
    if not title_baseline_ok:
        raise SystemExit(
            "Could not verify the saved title baseline checkpoint:"
            f" {title_baseline_detail}"
        )
    return f"{title_baseline_detail} {tape_detail}".strip()


def prepare_search_checkpoints(
    core: GBA,
    *,
    paths: FirstHalfPaths,
    qt_mode: bool,
    target_seed: int,
) -> int:
    """Prepare the persisted first-half checkpoints and return the start delay.

    The first successful intro/title skip sequence should be reused. This keeps
    the expensive cutscene out of the hot loop and out of later restarts:

    - if the rolling checkpoint plus its delay metadata are still usable, reuse
      them and resume from that delay
    - otherwise, if the baseline checkpoint is still usable, rebuild the
      rolling checkpoint from it at delay zero
    - otherwise, replay the intro once, create both checkpoints, and begin at
      delay zero
    """

    baseline_ok, baseline_detail = inspect_checkpoint_state(
        core,
        paths.baseline_checkpoint_path,
        qt_mode=qt_mode,
    )
    checkpoint_ok, checkpoint_detail = inspect_checkpoint_state(
        core,
        paths.checkpoint_path,
        qt_mode=qt_mode,
    )
    checkpoint_delay = read_checkpoint_metadata(
        paths.checkpoint_metadata_path,
        expected_target_seed=target_seed,
    )

    if checkpoint_ok and checkpoint_delay is not None:
        print(
            "Reusing rolling checkpoint:"
            f" checkpoint={paths.checkpoint_path}"
            f" delay={checkpoint_delay}"
            f" detail={checkpoint_detail}"
        )
        return checkpoint_delay

    if checkpoint_ok and checkpoint_delay is None:
        print(
            "Rolling checkpoint exists but its delay metadata is missing or stale;"
            " falling back to the saved baseline so the resumed delay numbering"
            " stays correct."
        )

    if baseline_ok:
        print(
            "Reusing saved baseline checkpoint and rebuilding rolling checkpoint"
            " at delay zero:"
            f" baseline={paths.baseline_checkpoint_path}"
            f" detail={baseline_detail}"
        )
        _call_load_state_file(core, paths.baseline_checkpoint_path, qt_mode=qt_mode)
        core.set_keys(raw=0)
        save_state_file(core, paths.checkpoint_path)
        write_checkpoint_metadata(
            paths.checkpoint_metadata_path,
            delay_frames=0,
            target_seed=target_seed,
        )
        return 0

    print(
        "No usable saved pre-input checkpoint found; replaying the intro/title"
        " path once to create the persistent baseline and rolling checkpoints."
    )
    prepare_title_baseline_checkpoint(
        core,
        paths=paths,
        qt_mode=qt_mode,
    )
    built, detail = build_pre_second_press_checkpoint_from_title_baseline(
        core,
        title_baseline_checkpoint_path=paths.title_baseline_checkpoint_path,
        pre_input_tape_path=paths.pre_input_tape_path,
        baseline_checkpoint_path=paths.baseline_checkpoint_path,
        checkpoint_path=paths.checkpoint_path,
        checkpoint_metadata_path=paths.checkpoint_metadata_path,
        use_runtime_checkpoint=False,
        title_skip_start_delay=0,
        target_seed=target_seed,
    )
    if not built:
        raise SystemExit(
            "Could not create the persistent pre-input checkpoints from the"
            f" saved title baseline route: {detail}"
        )
    return 0


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

    `1 from egg - replay-working-*` remains the latest brute-force output. The
    locked baseline is now a protected checkpoint+metadata pair that reuses the
    shared canonical save file instead of cloning another `.sav`.
    """

    if not source_checkpoint_path.is_file():
        raise RuntimeError(f"Cannot lock missing replay checkpoint: {source_checkpoint_path}")
    if not source_save_path.is_file():
        raise RuntimeError(f"Cannot lock missing first-half save: {source_save_path}")

    shared_save_path = baseline_save_path.resolve() == source_save_path.resolve()
    baseline_paths = (
        (baseline_checkpoint_path, baseline_metadata_path)
        if shared_save_path
        else (baseline_checkpoint_path, baseline_metadata_path, baseline_save_path)
    )
    existing_paths = [path for path in baseline_paths if path.exists()]
    if existing_paths and not overwrite:
        if len(existing_paths) == len(baseline_paths):
            # Reassert read-only on the locked checkpoint, locked metadata, and
            # the shared canonical save file. On Windows this is the practical
            # guard that prevents replay or a later search from mutating the
            # baseline contract in place.
            for path in baseline_paths:
                _make_file_read_only(path)
            _make_file_read_only(source_save_path)
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
    _make_file_read_only(baseline_checkpoint_path)
    if not shared_save_path:
        shutil.copy2(source_save_path, baseline_save_path)
        _make_file_read_only(baseline_save_path)
    else:
        _make_file_read_only(source_save_path)
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
    checkpoint_metadata_path: Path,
    use_runtime_checkpoint: bool,
    delay_frames: int,
    target_seed: int,
) -> bool:
    """Rebuild the rolling checkpoint from the untouched baseline checkpoint."""

    load_state_file(core, baseline_checkpoint_path)
    core.set_keys(raw=0)
    run_frames_fast(core, delay_frames)
    if observe_initial_seed_from_timer1(core) is not None:
        return False
    if not title_input_checkpoint_ready(core):
        return False
    save_state_file(core, checkpoint_path)
    write_checkpoint_metadata(
        checkpoint_metadata_path,
        delay_frames=delay_frames,
        target_seed=target_seed,
    )
    if use_runtime_checkpoint:
        capture_runtime_checkpoint(core)
    return True


def advance_checkpoint_one_frame(
    core: GBA,
    *,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    checkpoint_metadata_path: Path,
    use_runtime_checkpoint: bool,
    next_delay_frames: int,
    target_seed: int,
) -> bool:
    """Promote the rolling no-input checkpoint by exactly one frame.

    The search should not replay `delay` idle frames from the beginning on
    every attempt. Instead, one no-input checkpoint is reused for both button
    branches at that delay, then rewound, advanced by one idle frame, and
    saved again as the starting point for the next delay.

    In the visible Qt runtime path this hot checkpoint stays in the in-memory
    scratch slot for speed. Host-side runs still fall back to the file-backed
    checkpoint path.

    The user-facing workflow for this script is intentionally simple: once the
    no-input checkpoint exists, advance it one neutral frame at a time and keep
    searching. The rolling checkpoint still has to stay in the exact pre-seed
    RUN/state=1 title window, though. If it drifts out of that window or if
    Timer 1 has already stopped, rebuild from the untouched baseline instead of
    persisting a bad no-input state that would later sit on the opening/title
    flow and wait forever for a seed event that can no longer happen.
    """

    _restore_checkpoint_qt_aware(
        core,
        checkpoint_path,
        use_runtime_checkpoint,
        qt_mode=_qt_mode_enabled(),
    )
    core.set_keys(raw=0)
    if not title_input_checkpoint_ready(core):
        rebuilt = rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            checkpoint_metadata_path=checkpoint_metadata_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=next_delay_frames,
            target_seed=target_seed,
        )
        return rebuilt is not False
    core.run_frame()
    if observe_initial_seed_from_timer1(core) is not None or not title_input_checkpoint_ready(core):
        rebuilt = rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            checkpoint_metadata_path=checkpoint_metadata_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=next_delay_frames,
            target_seed=target_seed,
        )
        return rebuilt is not False
    if use_runtime_checkpoint:
        capture_runtime_checkpoint(core)
        if next_delay_frames % runtime_checkpoint_save_every() != 0:
            return True

    save_state_file(core, checkpoint_path)
    write_checkpoint_metadata(
        checkpoint_metadata_path,
        delay_frames=next_delay_frames,
        target_seed=target_seed,
    )
    return True


def boot_to_title_detected_checkpoint(core: GBA) -> TitleTaskState:
    """Boot the game only far enough to make the title task exist.

    This is an earlier reusable baseline than RUN/state=1. The current project
    target searches have repeatedly shown that the pure RUN/state=1 inner search
    can exhaust one valid title-input window without finding the desired seed.
    Keeping this earlier no-input title baseline lets the script also search
    earlier title timing by inserting neutral frames before the title-scene
    skip pulses that push the task into RUN.
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
            return info
        pulse_keys_once(core, INTRO_SKIP_KEY)
        intro_skip_pulses += 1


def _drive_from_title_baseline_to_pre_second_press(
    core: GBA,
    *,
    title_skip_start_delay: int,
) -> tuple[bool, str]:
    """Drive title-baseline state forward until the final title input is legal.

    This is the fixed "pre-press" route segment. The hot loop still searches
    delay frames from a rolling checkpoint. This helper only defines how to get
    from "title task just appeared" to "RUN/state=1 and ready for the final
    input" so that segment can be recorded once and replayed later.
    """

    core.set_keys(raw=0)
    if title_skip_start_delay:
        run_frames_fast(core, title_skip_start_delay)
    if observe_initial_seed_from_timer1(core) is not None:
        return False, "Timer 1 already stopped before title-scene skip pulses."

    info = find_title_task(core)
    if info is None:
        return False, "Lost the title task before starting the title-scene skip."

    title_skip_pulses = 0
    while info.scene != TITLESCENE_RUN:
        pulse_keys_once(core, TITLE_SKIP_KEY)
        title_skip_pulses += 1
        info = find_title_task(core)
        if info is None:
            return False, "Lost the title task while applying the title-scene skip."
        if observe_initial_seed_from_timer1(core) is not None:
            return False, "Timer 1 stopped before reaching the RUN title scene."

    checkpoint_wait_frames = 0
    while not (info.scene == TITLESCENE_RUN and info.state == 1):
        core.set_keys(raw=0)
        core.run_frame()
        checkpoint_wait_frames += 1
        info = find_title_task(core)
        if info is None:
            return False, "Lost the title task while waiting for RUN/state=1."
        if observe_initial_seed_from_timer1(core) is not None:
            return False, "Timer 1 stopped before the pre-second-title-input checkpoint."
        if checkpoint_wait_frames > DEFAULT_CHECKPOINT_WAIT_TIMEOUT:
            return False, (
                "Could not reach the pre-second-title-input checkpoint"
                f" within {DEFAULT_CHECKPOINT_WAIT_TIMEOUT} neutral frames."
            )

    return True, (
        f"title_skip_start_delay={title_skip_start_delay}"
        f" title_skip_pulses={title_skip_pulses}"
        f" checkpoint_wait_frames={checkpoint_wait_frames}"
        f" frame_counter={getattr(core, 'frame_counter', 0)}"
    )


def _record_pre_input_template_tape(
    core: GBA,
    *,
    title_baseline_checkpoint_path: Path,
    pre_input_tape_path: Path,
) -> str:
    """Record the zero-delay title-baseline route segment once.

    The widened-lane search only changes the neutral wait before the first
    title-scene skip pulse. Record the zero-delay route once, then derive later
    lanes by prefixing extra `NONE` frames instead of re-recording the whole
    segment.
    """

    input_tape = _load_input_tape_module()
    load_state_file(core, title_baseline_checkpoint_path)
    tape, (built, detail) = _record_route_tape(
        core,
        metadata={
            "workflow": "firsthalf-seed-bruteforce",
            "route_segment": "title-baseline-to-checkpoint",
            "title_skip_start_delay": 0,
            "script": "Seed-Bruteforcer.py",
        },
        runner=lambda route_core: _drive_from_title_baseline_to_pre_second_press(
            route_core,
            title_skip_start_delay=0,
        ),
    )
    if not built:
        raise RuntimeError(f"Could not record the pre-input route tape: {detail}")
    input_tape.write_tape(pre_input_tape_path, tape)
    return f"recorded_template_tape_frames={tape.frame_count}"


def _replay_pre_input_checkpoint_tape(
    core: GBA,
    *,
    tape,
) -> tuple[bool, str]:
    """Replay one pre-press route tape from the current title baseline."""

    input_tape = _load_input_tape_module()
    input_tape.replay_tape(core, tape)
    if observe_initial_seed_from_timer1(core) is not None:
        return False, "Timer 1 stopped before the pre-second-title-input checkpoint."
    if not title_input_checkpoint_ready(core):
        return False, "Title task no longer at RUN/state=1 after replaying the route tape."
    return True, (
        f"tape_frames={tape.frame_count}"
        f" frame_counter={getattr(core, 'frame_counter', 0)}"
    )


def build_pre_second_press_checkpoint_from_title_baseline(
    core: GBA,
    *,
    title_baseline_checkpoint_path: Path,
    pre_input_tape_path: Path,
    baseline_checkpoint_path: Path,
    checkpoint_path: Path,
    checkpoint_metadata_path: Path,
    use_runtime_checkpoint: bool,
    title_skip_start_delay: int,
    target_seed: int,
) -> tuple[bool, str]:
    """Build one RUN/state=1 checkpoint from the earlier title baseline.

    The current search can miss a valid target when it only varies neutral
    frames after RUN/state=1 already exists. This helper widens the search by
    delaying the first title-scene skip `Start` pulse after the title task
    becomes visible, then rebuilding the usual RUN/state=1 baseline from that
    earlier title checkpoint.

    The route segment itself is now data-backed: one shared zero-delay tape is
    recorded once, and later widened lanes are derived by prefixing neutral
    frames before replaying that tape from the saved title baseline checkpoint.
    """

    input_tape = _load_input_tape_module()
    route_tape = None
    tape_detail = ""

    if pre_input_tape_path.is_file():
        try:
            route_tape = input_tape.read_tape(pre_input_tape_path)
            tape_detail = f"template_tape={pre_input_tape_path.name}"
        except Exception as exc:
            print(
                "Saved pre-input route tape is unreadable; recording a fresh"
                f" template instead: {exc}"
            )

    if route_tape is None:
        try:
            tape_detail = _record_pre_input_template_tape(
                core,
                title_baseline_checkpoint_path=title_baseline_checkpoint_path,
                pre_input_tape_path=pre_input_tape_path,
            )
            route_tape = input_tape.read_tape(pre_input_tape_path)
        except Exception as exc:
            return False, f"Could not create the saved pre-input route tape: {exc}"

    widened_tape = _prepend_neutral_frames_to_tape(
        route_tape,
        neutral_frames=title_skip_start_delay,
    )

    for attempt_index in range(2):
        load_state_file(core, title_baseline_checkpoint_path)
        built, detail = _replay_pre_input_checkpoint_tape(
            core,
            tape=widened_tape,
        )
        if built:
            _save_search_checkpoints(
                core,
                baseline_checkpoint_path=baseline_checkpoint_path,
                checkpoint_path=checkpoint_path,
                checkpoint_metadata_path=checkpoint_metadata_path,
                use_runtime_checkpoint=use_runtime_checkpoint,
                target_seed=target_seed,
            )
            return True, (
                f"title_skip_start_delay={title_skip_start_delay}"
                f" {detail}"
                f" {tape_detail}"
            ).strip()

        if attempt_index == 1:
            return False, detail

        print(
            "Saved pre-input route tape did not land in the legal title-input"
            " window; recording a fresh zero-delay template and retrying once."
        )
        try:
            tape_detail = _record_pre_input_template_tape(
                core,
                title_baseline_checkpoint_path=title_baseline_checkpoint_path,
                pre_input_tape_path=pre_input_tape_path,
            )
            route_tape = input_tape.read_tape(pre_input_tape_path)
            widened_tape = _prepend_neutral_frames_to_tape(
                route_tape,
                neutral_frames=title_skip_start_delay,
            )
        except Exception as exc:
            return False, f"Could not refresh the pre-input route tape: {exc}"

    return False, "unreachable"


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

    info = boot_to_title_detected_checkpoint(core)

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

    This is the core contract for the whole script: once the timer stops, the
    low counter register is the seed source of truth. Everything else logged by
    the script exists to explain or reproduce that same event.
    """

    try:
        timer1_control = core.memory.u16[TIMER1_CONTROL_ADDR]
        timer1_count = core.memory.u16[TIMER1_COUNT_ADDR]
    except Exception:
        return None
    if timer1_control & TIMER_ENABLE_MASK:
        return None
    return timer1_count


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
    target_seed: int | None = None,
) -> AttemptObservation:
    """Try one button branch from the current rolling delay checkpoint.

    Each attempt reloads the current no-input checkpoint for `delay_frames`,
    then applies exactly one final title-button press (`A` or `Start`) from
    that no-input state and waits for FR/LG to expose the seed.
    If `pre_input_checkpoint_path` is provided, the script saves the exact live
    pre-button state immediately before pressing the title input. This avoids
    reconstructing the winning replay state after the seed hit, which can drift
    in the visible Qt runtime.
    The returned observation carries the full seed event plus the frame/button
    pair that produced it.

    `pre_input_checkpoint_path` is intentionally optional. The hot search path
    leaves it as `None` so non-matching branches do not spend most of their time
    writing throwaway savestates. When a branch matches, the caller reruns that
    exact delay/button once with this path set so replay still receives the
    exact organic pre-input checkpoint.
    """

    retried_from_baseline = False
    if target_seed is None:
        target_seed = _env_default_seed("MGBA_TARGET_SEED", DEFAULT_TARGET_SEED)

    while True:
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
            rebuilt = rebuild_delay_checkpoint(
                core,
                baseline_checkpoint_path=baseline_checkpoint_path,
                checkpoint_path=checkpoint_path,
                checkpoint_metadata_path=checkpoint_path.with_name(
                    DEFAULT_CHECKPOINT_METADATA_NAME
                ),
                use_runtime_checkpoint=use_runtime_checkpoint,
                delay_frames=delay_frames,
                target_seed=target_seed,
            )
            if rebuilt is False:
                raise RuntimeError(
                    "Rebuilding that exact delay checkpoint no longer lands in"
                    " the pre-seed RUN/state=1 title window."
                )
            retried_from_baseline = True
            continue
        if pre_input_neutral_frames:
            run_frames_with_keys(core, 0, pre_input_neutral_frames)
        pre_input_seed_mirror = observe_initial_seed_mirror(core)
        if pre_input_checkpoint_path is not None:
            _probe_state(core, f"firsthalf_pre_input_candidate:{pre_input_checkpoint_path.name}")
            save_state_file(core, pre_input_checkpoint_path)

        core.set_keys(button_key)
        core.run_frame()
        seed_observation = observe_seed_generation(
            core,
            pre_input_seed_mirror=pre_input_seed_mirror,
        )
        core.set_keys(raw=0)
        if seed_observation is not None:
            _probe_state(core, f"firsthalf_seed_hit:{button_name}")
            return AttemptObservation(
                seed_observation=seed_observation,
                seed_frame=core.frame_counter,
                button_name=button_name,
            )

        remaining_wait_frames = max(seed_timeout - 1, 0)
        title_window_status = title_input_checkpoint_status(core)
        if title_window_status is True:
            transition_budget = min(remaining_wait_frames, title_transition_timeout)
            for _ in range(transition_budget):
                core.run_frame()
                remaining_wait_frames -= 1
                seed_observation = observe_seed_generation(
                    core,
                    pre_input_seed_mirror=pre_input_seed_mirror,
                )
                if seed_observation is not None:
                    _probe_state(core, f"firsthalf_seed_hit:{button_name}")
                    return AttemptObservation(
                        seed_observation=seed_observation,
                        seed_frame=core.frame_counter,
                        button_name=button_name,
                    )
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
                rebuilt = rebuild_delay_checkpoint(
                    core,
                    baseline_checkpoint_path=baseline_checkpoint_path,
                    checkpoint_path=checkpoint_path,
                    checkpoint_metadata_path=checkpoint_path.with_name(
                        DEFAULT_CHECKPOINT_METADATA_NAME
                    ),
                    use_runtime_checkpoint=use_runtime_checkpoint,
                    delay_frames=delay_frames,
                    target_seed=target_seed,
                )
                if rebuilt is False:
                    raise RuntimeError(
                        "Rebuilding that exact delay checkpoint no longer lands in"
                        " the pre-seed RUN/state=1 title window."
                    )
                retried_from_baseline = True
                continue

        effective_wait_frames = remaining_wait_frames
        if title_window_status is not True:
            effective_wait_frames = min(remaining_wait_frames, post_transition_seed_timeout)

        for waited_frames in range(1, effective_wait_frames + 1):
            core.run_frame()
            seed_observation = observe_seed_generation(
                core,
                pre_input_seed_mirror=pre_input_seed_mirror,
            )
            if seed_observation is not None:
                _probe_state(core, f"firsthalf_seed_hit:{button_name}")
                return AttemptObservation(
                    seed_observation=seed_observation,
                    seed_frame=core.frame_counter,
                    button_name=button_name,
                )
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

        core.set_keys(raw=0)
        inferred = infer_seed_from_current_rng_state(core)
        inferred_detail = ""
        if inferred is not None:
            inferred_seed, inferred_steps, inferred_rng = inferred
            inferred_detail = (
                f" Last PRNG-orbit candidate was 0x{inferred_seed:04X}"
                f" at signed_steps_from_rng={inferred_steps}"
                f" from rng=0x{inferred_rng:08X}."
            )
        current_seed_mirror = observe_initial_seed_mirror(core)
        if current_seed_mirror is not None:
            inferred_detail += (
                f" Last initial-seed mirror was 0x{current_seed_mirror:04X}"
                " at 0x02020000."
            )
            if pre_input_seed_mirror is not None:
                inferred_detail += (
                    " It "
                    + (
                        "changed"
                        if current_seed_mirror != pre_input_seed_mirror
                        else "did not change"
                    )
                    + " from the pre-input checkpoint value."
                )
        if retried_from_baseline:
            raise RuntimeError(
                f"Initial seed was not observed within {effective_wait_frames + 1} frames"
                f" after delay {delay_frames} using {button_name}, even after"
                " rebuilding that exact delay checkpoint from the title baseline."
                f"{inferred_detail}"
            )
        print(
            "Title input attempt timed out;"
            f" rebuilding delay {delay_frames} and retrying {button_name} once."
            f" effective_wait_frames={effective_wait_frames + 1}"
        )
        rebuilt = rebuild_delay_checkpoint(
            core,
            baseline_checkpoint_path=baseline_checkpoint_path,
            checkpoint_path=checkpoint_path,
            checkpoint_metadata_path=checkpoint_path.with_name(
                DEFAULT_CHECKPOINT_METADATA_NAME
            ),
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=delay_frames,
            target_seed=target_seed,
        )
        if rebuilt is False:
            raise RuntimeError(
                "Rebuilding that exact delay checkpoint no longer lands in"
                " the pre-seed RUN/state=1 title window."
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
        "--pre-input-neutral-frames",
        type=int,
        default=_env_default_int(
            "MGBA_PRE_INPUT_NEUTRAL_FRAMES", DEFAULT_PRE_INPUT_NEUTRAL_FRAMES
        ),
        help=(
            "Neutral frames to spend at the saved no-input checkpoint immediately"
            " before the final title input."
        ),
    )
    parser.add_argument(
        "--title-skip-start-delay-start",
        type=int,
        default=_env_default_int(
            "MGBA_TITLE_SKIP_START_DELAY_START", DEFAULT_TITLE_SKIP_START_DELAY_START
        ),
        help=(
            "First extra neutral-frame offset to test after the title task becomes"
            " visible and before the title-scene skip Start pulses begin."
        ),
    )
    parser.add_argument(
        "--title-skip-start-delay-max",
        type=int,
        default=_env_default_int(
            "MGBA_TITLE_SKIP_START_DELAY_MAX", DEFAULT_TITLE_SKIP_START_DELAY_MAX
        ),
        help=(
            "Maximum extra neutral-frame offset to test after the title task"
            " becomes visible and before the title-scene skip Start pulses begin."
        ),
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
    """Choose the live Qt core when available, otherwise load the ROM directly.

    Silence native mGBA logging in both host and visible-Qt modes. The first-half
    brute-force loop can issue thousands of state loads and frame advances, so
    leaving BIOS/DMA spam enabled in the live Qt path burns I/O for no search
    value and slows long runs substantially.
    """

    if not rom_path.is_file():
        raise SystemExit(f"ROM not found: {rom_path}")

    if hasattr(mgba, "log"):
        try:
            mgba.log.silence()
        except Exception:
            pass

    if _qt_mode_enabled():
        # Runtime scripting should not depend on whichever game was already
        # open in the window. Load the requested ROM into the visible core.
        if not mgba_qt.load_rom(rom_path):
            raise SystemExit(f"Could not load ROM into the visible Qt core: {rom_path}")
        core = mgba_qt.current_core()
        pause_live_core(core, reason="after ROM load")
        return core

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
        f"Observed 16-bit seed hit: 0x{observed_seed:04X}\n"
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


def _log_runtime_banner(
    *,
    args,
    rom_path: Path,
    paths: FirstHalfPaths,
    qt_mode: bool,
    use_runtime_checkpoint: bool,
    start_delay: int,
    post_transition_seed_timeout: int,
    title_skip_start_delay_start: int,
    title_skip_start_delay_max: int,
) -> None:
    """Print the run configuration in the same terms the docs now use."""

    pre_input_neutral_frames = getattr(
        args,
        "pre_input_neutral_frames",
        DEFAULT_PRE_INPUT_NEUTRAL_FRAMES,
    )
    print(f"ROM: {rom_path}")
    print(f"mGBA directory: {paths.mgba_dir}")
    print(f"Persistent save file: {paths.save_path}")
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
        " treated as a dead title-screen input."
    )
    print(
        "Post-transition seed timeout:"
        f" {post_transition_seed_timeout} frames once the title task has left"
        " the pre-input window."
    )
    print(
        "Neutral pre-input frames before the final title press:"
        f" {pre_input_neutral_frames}"
    )
    print(
        "Title-skip start delay search range:"
        f" {title_skip_start_delay_start}..{title_skip_start_delay_max}"
        " extra neutral frame(s) after the title task first appears and before"
        " the title-scene skip Start pulses begin."
    )
    print(f"Additional attempt counter log: every {DEFAULT_ATTEMPT_LOG_EVERY} button attempts.")
    print(f"Title baseline checkpoint savestate: {paths.title_baseline_checkpoint_path}")
    print(f"Baseline checkpoint savestate: {paths.baseline_checkpoint_path}")
    print(f"Rolling checkpoint savestate: {paths.checkpoint_path}")
    print(f"Rolling checkpoint metadata: {paths.checkpoint_metadata_path}")
    print(f"Starting delay frame: {start_delay}")
    print(f"Working replay checkpoint savestate: {paths.replay_checkpoint_path}")
    print(f"Working replay candidate savestate: {paths.replay_candidate_path}")
    print(f"Read-only replay checkpoint savestate: {paths.readonly_replay_checkpoint_path}")
    print(f"Replay metadata: {paths.replay_metadata_path}")
    print(f"Locked baseline checkpoint savestate: {paths.locked_baseline_checkpoint_path}")
    print(f"Locked baseline metadata: {paths.locked_baseline_metadata_path}")
    print(f"Locked baseline save file: {paths.locked_baseline_save_path}")
    print(f"Done savestate: {paths.success_path}")
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


def _record_success(
    *,
    core: GBA,
    args,
    qt_mode: bool,
    rom_path: Path,
    paths: FirstHalfPaths,
    delay_frames: int,
    attempt: AttemptObservation,
    pre_input_neutral_frames: int,
    title_skip_start_delay: int,
) -> int:
    """Persist the documented first-half success artifacts and status."""

    seed_observation = attempt.seed_observation
    observed_button = attempt.button_name
    seed_value = seed_observation.seed_value
    seed_frame = attempt.seed_frame
    rng_value = seed_observation.rng_value
    prng_discerned_seed = seed_observation.prng_discerned_seed
    prng_discerned_steps = seed_observation.prng_discerned_steps_from_rng

    if prng_discerned_seed is None:
        print(
            "PRNG failsafe could not infer a nearby 16-bit seed state"
            f" from rng_at_seed=0x{rng_value:08X}"
            f" within +/-{DEFAULT_PRNG_SEED_DISCERN_WINDOW} LCRNG steps."
        )
    else:
        print(
            "PRNG failsafe inferred a nearby 16-bit seed candidate:"
            f" seed=0x{prng_discerned_seed:04X}"
            f" signed_steps_from_rng={prng_discerned_steps}"
            f" rng_at_seed=0x{rng_value:08X}"
        )
        if prng_discerned_seed != seed_value:
            print(
                "PRNG failsafe did not match the Timer 1 seed on the success frame:"
                f" timer1_seed=0x{seed_value:04X}"
                f" prng_discerned_seed=0x{prng_discerned_seed:04X}"
                f" signed_steps_from_rng={prng_discerned_steps}"
                " Timer 1 remains authoritative."
            )

    _probe_state(core, "firsthalf_target_hit_before_done_save")
    save_state_file(core, paths.success_path)
    commit_working_checkpoint(paths.replay_candidate_path, paths.replay_checkpoint_path)
    timer1_count_pre, timer1_control_pre = read_timer1_state_from_checkpoint(
        core,
        paths.replay_checkpoint_path,
    )
    load_state_file(core, paths.success_path)
    _probe_state(core, "firsthalf_restored_done_after_checkpoint_probe")

    replay_metadata = build_replay_metadata(
        mgba_dir=paths.mgba_dir,
        rom_path=rom_path,
        save_path=paths.save_path,
        working_checkpoint_path=paths.replay_checkpoint_path,
        readonly_checkpoint_path=paths.readonly_replay_checkpoint_path,
        target_seed=args.target,
        delay_frames=delay_frames,
        button_name=observed_button,
        seed_frame=seed_frame,
        rng_at_seed=rng_value,
        seed_source="timer1",
        timer1_seed_observed=seed_value,
        initial_seed_mirror=seed_observation.initial_seed_mirror_value,
        initial_seed_mirror_changed_from_pre_input=seed_observation.initial_seed_mirror_changed_from_pre_input,
        initial_seed_mirror_matches_timer1=seed_observation.initial_seed_mirror_matches_timer1,
        prng_discerned_seed=prng_discerned_seed,
        prng_discerned_steps_from_rng=prng_discerned_steps,
        timer1_count_pre=timer1_count_pre,
        timer1_control_pre=timer1_control_pre,
        pre_input_neutral_frames=pre_input_neutral_frames,
        title_skip_start_delay=title_skip_start_delay,
        seed_timeout=args.seed_timeout,
    )
    write_readonly_replay_artifacts(
        source_checkpoint_path=paths.replay_checkpoint_path,
        readonly_checkpoint_path=paths.readonly_replay_checkpoint_path,
        metadata_path=paths.replay_metadata_path,
        metadata=replay_metadata,
    )
    locked_baseline_created = write_locked_baseline_artifacts(
        source_checkpoint_path=paths.readonly_replay_checkpoint_path,
        source_metadata=replay_metadata,
        source_save_path=paths.save_path,
        baseline_checkpoint_path=paths.locked_baseline_checkpoint_path,
        baseline_metadata_path=paths.locked_baseline_metadata_path,
        baseline_save_path=paths.locked_baseline_save_path,
        mgba_dir=paths.mgba_dir,
        overwrite=os.environ.get(OVERWRITE_LOCKED_BASELINE_ENV_NAME) == "1",
    )
    if locked_baseline_created:
        print(
            "Locked first-half baseline created:"
            f" checkpoint={paths.locked_baseline_checkpoint_path}"
            f" metadata={paths.locked_baseline_metadata_path}"
            f" save={paths.locked_baseline_save_path}"
        )
    else:
        print(
            "Locked first-half baseline already exists; leaving it unchanged:"
            f" checkpoint={paths.locked_baseline_checkpoint_path}"
            f" metadata={paths.locked_baseline_metadata_path}"
            f" save={paths.locked_baseline_save_path}"
        )

    timer1_count_readonly, timer1_control_readonly = read_timer1_state_from_checkpoint(
        core,
        paths.readonly_replay_checkpoint_path,
    )
    print(
        "Probe firsthalf_readonly_timer1:"
        f" count=0x{timer1_count_readonly:04X}"
        f" control=0x{timer1_control_readonly:04X}"
    )
    load_state_file(core, paths.success_path)
    _probe_state(core, "firsthalf_restored_done_after_readonly_probe")
    match_log_parts = [
        "Match found:",
        f" delay={delay_frames}",
        f" button={observed_button}",
        f" seed_frame={seed_frame}",
        f" seed=0x{seed_value:04X}",
        " seed_source=timer1",
        f" rng_at_seed=0x{rng_value:08X}",
    ]
    match_log_parts.extend(_format_initial_seed_mirror_log_parts(seed_observation))
    match_log_parts.extend(
        [
            f" saved={paths.success_path}",
            f" working_checkpoint={paths.replay_checkpoint_path}",
            f" readonly_checkpoint={paths.readonly_replay_checkpoint_path}",
            f" metadata={paths.replay_metadata_path}",
        ]
    )
    print("".join(match_log_parts))
    write_status_marker_from_env(
        STATUS_ENV_NAME,
        {
            "script": "Seed-Bruteforcer.py",
            "matched": True,
            "target_seed": int(args.target) & 0xFFFF,
            "seed": int(seed_value) & 0xFFFF,
            "seed_source": "timer1",
            "timer1_seed_observed": int(seed_value) & 0xFFFF,
            "delay_frames": int(delay_frames),
            "title_skip_start_delay": int(title_skip_start_delay),
            "button_name": str(observed_button),
            "seed_frame": int(seed_frame),
            "rng_at_seed": int(rng_value) & 0xFFFFFFFF,
            "initial_seed_mirror": None
            if seed_observation.initial_seed_mirror_value is None
            else int(seed_observation.initial_seed_mirror_value) & 0xFFFF,
            "initial_seed_mirror_changed_from_pre_input": seed_observation.initial_seed_mirror_changed_from_pre_input,
            "initial_seed_mirror_matches_timer1": seed_observation.initial_seed_mirror_matches_timer1,
            "prng_discerned_seed": None
            if prng_discerned_seed is None
            else int(prng_discerned_seed) & 0xFFFF,
            "prng_discerned_steps_from_rng": prng_discerned_steps,
            "timer1_count_pre": int(timer1_count_pre) & 0xFFFF,
            "timer1_control_pre": int(timer1_control_pre) & 0xFFFF,
            "working_checkpoint": str(paths.replay_checkpoint_path),
            "readonly_checkpoint": str(paths.readonly_replay_checkpoint_path),
            "metadata": str(paths.replay_metadata_path),
            "locked_baseline_checkpoint": str(paths.locked_baseline_checkpoint_path),
            "locked_baseline_metadata": str(paths.locked_baseline_metadata_path),
            "locked_baseline_save": str(paths.locked_baseline_save_path),
            "locked_baseline_created": bool(locked_baseline_created),
            "done_savestate": str(paths.success_path),
        },
    )
    notify_success_in_qt(
        core,
        qt_mode=qt_mode,
        target_seed=args.target,
        observed_seed=seed_value,
        delay_frames=delay_frames,
        button_name=observed_button,
        seed_frame=seed_frame,
        done_path=paths.success_path,
    )
    return 0


def main() -> int:
    """Run the documented first-half checkpoint-and-button workflow."""

    parser = build_parser()
    args = _parse_args(parser)

    if args.max_delay < 0:
        raise SystemExit("--max-delay must be non-negative.")
    if args.settle_frames < 0:
        raise SystemExit("--settle-frames must be non-negative.")
    if args.seed_timeout <= 0:
        raise SystemExit("--seed-timeout must be positive.")
    pre_input_neutral_frames = getattr(
        args,
        "pre_input_neutral_frames",
        DEFAULT_PRE_INPUT_NEUTRAL_FRAMES,
    )
    if pre_input_neutral_frames < 0:
        raise SystemExit("--pre-input-neutral-frames must be non-negative.")
    title_skip_start_delay_start = getattr(
        args,
        "title_skip_start_delay_start",
        DEFAULT_TITLE_SKIP_START_DELAY_START,
    )
    title_skip_start_delay_max = getattr(
        args,
        "title_skip_start_delay_max",
        DEFAULT_TITLE_SKIP_START_DELAY_MAX,
    )
    if title_skip_start_delay_start < 0:
        raise SystemExit("--title-skip-start-delay-start must be non-negative.")
    if title_skip_start_delay_max < title_skip_start_delay_start:
        raise SystemExit(
            "--title-skip-start-delay-max must be greater than or equal to"
            " --title-skip-start-delay-start."
        )
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
    paths = _build_paths(resolve_mgba_dir())
    paths.save_path.parent.mkdir(parents=True, exist_ok=True)

    # The Qt bridge probe crosses into the embedded scripting layer, so do it
    # once up front and thread that mode flag through the rest of the run.
    qt_mode = _qt_mode_enabled()

    core = load_runtime_core(rom_path)
    ensure_audio_killswitch_defaults(paths.mgba_dir)
    ensure_no_render_defaults(paths.mgba_dir)
    ensure_fast_forward_defaults(paths.mgba_dir)
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

    load_required_save_file(core, paths.save_path, qt_mode=qt_mode, temporary=True)

    _remove_file_if_present(paths.success_path)
    _remove_file_if_present(paths.replay_candidate_path)
    use_runtime_checkpoint = bool(_env_default_int(USE_RUNTIME_CHECKPOINT_ENV_NAME, 0))
    title_baseline_detail = ""
    start_delay = 0
    if title_skip_start_delay_max > 0 or title_skip_start_delay_start > 0:
        title_baseline_detail = prepare_title_baseline_checkpoint(
            core,
            paths=paths,
            qt_mode=qt_mode,
        )
    else:
        start_delay = prepare_search_checkpoints(
            core,
            paths=paths,
            qt_mode=qt_mode,
            target_seed=args.target,
        )
        if start_delay > args.max_delay:
            print(
                "Saved rolling checkpoint is already beyond the requested max delay:"
                f" start_delay={start_delay}"
                f" max_delay={args.max_delay}"
                " Delete 1 from egg - checkpoint/1 from egg - checkpoint-metadata.json or raise"
                " --max-delay if you want the search to continue from that checkpoint."
            )
            write_status_marker_from_env(
                STATUS_ENV_NAME,
                {
                    "script": "Seed-Bruteforcer.py",
                    "matched": False,
                    "target_seed": int(args.target) & 0xFFFF,
                    "start_delay": int(start_delay),
                    "searched_max_delay": int(args.max_delay),
                    "error": "rolling checkpoint already beyond requested max delay",
                },
            )
            return 1
        if use_runtime_checkpoint:
            use_runtime_checkpoint = prime_runtime_checkpoint(
                core,
                paths.checkpoint_path,
                qt_mode=qt_mode,
            )
    _log_runtime_banner(
        args=args,
        rom_path=rom_path,
        paths=paths,
        qt_mode=qt_mode,
        use_runtime_checkpoint=use_runtime_checkpoint,
        start_delay=start_delay,
        post_transition_seed_timeout=post_transition_seed_timeout,
        title_skip_start_delay_start=title_skip_start_delay_start,
        title_skip_start_delay_max=title_skip_start_delay_max,
    )
    if title_baseline_detail:
        print(
            "Title baseline checkpoint detail:"
            f" {title_baseline_detail}"
        )
    write_progress_marker(
        {
            "script": "Seed-Bruteforcer.py",
            "phase": "search_started",
            "matched": False,
            "target_seed": int(args.target) & 0xFFFF,
            "start_delay": int(start_delay),
            "searched_max_delay": int(args.max_delay),
            "title_skip_start_delay_start": int(title_skip_start_delay_start),
            "title_skip_start_delay_max": int(title_skip_start_delay_max),
            "attempts": 0,
            "branch_failures": 0,
            "rolling_checkpoint": str(paths.checkpoint_path),
            "checkpoint_metadata": str(paths.checkpoint_metadata_path),
        }
    )

    attempts_checked = 0
    branch_failures = 0
    title_skip_start_delay_range = range(
        title_skip_start_delay_start,
        title_skip_start_delay_max + 1,
    )
    if title_skip_start_delay_start == 0 and title_skip_start_delay_max == 0:
        title_skip_start_delay_range = range(0, 1)

    for title_skip_start_delay in title_skip_start_delay_range:
        current_start_delay = start_delay if title_skip_start_delay == 0 and title_skip_start_delay_max == 0 else 0
        if title_skip_start_delay_max > 0 or title_skip_start_delay_start > 0:
            built, detail = build_pre_second_press_checkpoint_from_title_baseline(
                core,
                title_baseline_checkpoint_path=paths.title_baseline_checkpoint_path,
                pre_input_tape_path=paths.pre_input_tape_path,
                baseline_checkpoint_path=paths.baseline_checkpoint_path,
                checkpoint_path=paths.checkpoint_path,
                checkpoint_metadata_path=paths.checkpoint_metadata_path,
                use_runtime_checkpoint=use_runtime_checkpoint,
                title_skip_start_delay=title_skip_start_delay,
                target_seed=args.target,
            )
            if not built:
                print(
                    "Skipping title-skip start delay lane:"
                    f" title_skip_start_delay={title_skip_start_delay}"
                    f" detail={detail}"
                )
                write_progress_marker(
                    {
                        "script": "Seed-Bruteforcer.py",
                        "phase": "searching",
                        "matched": False,
                        "target_seed": int(args.target) & 0xFFFF,
                        "title_skip_start_delay": int(title_skip_start_delay),
                        "title_skip_start_delay_start": int(title_skip_start_delay_start),
                        "title_skip_start_delay_max": int(title_skip_start_delay_max),
                        "current_delay": 0,
                        "attempts": int(attempts_checked),
                        "branch_failures": int(branch_failures),
                        "latest_event": "title_lane_unusable",
                        "latest_error": str(detail),
                        "rolling_checkpoint": str(paths.checkpoint_path),
                        "checkpoint_metadata": str(paths.checkpoint_metadata_path),
                    }
                )
                continue
            print(
                "Prepared title-skip start delay lane:"
                f" title_skip_start_delay={title_skip_start_delay}"
                f" detail={detail}"
            )

        for delay_frames in range(current_start_delay, args.max_delay + 1):
            for button_name, button_key in TITLE_INPUT_ATTEMPTS:
                try:
                    attempt = brute_force_attempt(
                        core=core,
                        baseline_checkpoint_path=paths.baseline_checkpoint_path,
                        checkpoint_path=paths.checkpoint_path,
                        use_runtime_checkpoint=use_runtime_checkpoint,
                        delay_frames=delay_frames,
                        button_name=button_name,
                        button_key=button_key,
                        seed_timeout=args.seed_timeout,
                        pre_input_checkpoint_path=None,
                        pre_input_neutral_frames=pre_input_neutral_frames,
                        title_transition_timeout=args.title_transition_timeout,
                        post_transition_seed_timeout=post_transition_seed_timeout,
                        target_seed=args.target,
                    )
                except RuntimeError as exc:
                    attempts_checked += 1
                    branch_failures += 1
                    print(
                        "Branch failed; continuing search:"
                        f" title_skip_start_delay={title_skip_start_delay}"
                        f" delay={delay_frames}"
                        f" button={button_name}"
                        f" total_branch_failures={branch_failures}"
                        f" detail={exc}"
                    )
                    if attempts_checked % DEFAULT_ATTEMPT_LOG_EVERY == 0:
                        print(
                            "Attempt counter:"
                            f" attempts={attempts_checked}"
                            f" title_skip_start_delay={title_skip_start_delay}"
                            f" current_delay={delay_frames}"
                            f" latest_button={button_name}"
                            f" branch_failures={branch_failures}"
                        )
                    if attempts_checked % args.progress_every == 0:
                        write_progress_marker(
                            {
                                "script": "Seed-Bruteforcer.py",
                                "phase": "searching",
                                "matched": False,
                                "target_seed": int(args.target) & 0xFFFF,
                                "start_delay": int(current_start_delay),
                                "searched_max_delay": int(args.max_delay),
                                "title_skip_start_delay": int(title_skip_start_delay),
                                "title_skip_start_delay_start": int(title_skip_start_delay_start),
                                "title_skip_start_delay_max": int(title_skip_start_delay_max),
                                "current_delay": int(delay_frames),
                                "attempts": int(attempts_checked),
                                "branch_failures": int(branch_failures),
                                "latest_button": str(button_name),
                                "latest_event": "branch_failure",
                                "latest_error": str(exc),
                                "rolling_checkpoint": str(paths.checkpoint_path),
                                "checkpoint_metadata": str(paths.checkpoint_metadata_path),
                            }
                        )
                    continue
                attempts_checked += 1
                seed_observation = attempt.seed_observation
                seed_value = seed_observation.seed_value
                seed_frame = attempt.seed_frame
                rng_value = seed_observation.rng_value
                observed_button = attempt.button_name

                should_log = delay_frames % args.progress_every == 0 or seed_value == args.target
                post_settle_rng = None
                if should_log and seed_value != args.target and args.settle_frames:
                    run_frames_fast(core, args.settle_frames)
                    post_settle_rng = core.memory.u32[GRNG_VALUE_ADDR]

                if should_log:
                    log_parts = [
                        "Attempt",
                        f" title_skip_start_delay={title_skip_start_delay}",
                        f" delay={delay_frames}",
                        f" button={observed_button}",
                        f" seed=0x{seed_value:04X}",
                        f" seed_frame={seed_frame}",
                        f" rng_at_seed=0x{rng_value:08X}",
                    ]
                    log_parts.extend(_format_initial_seed_mirror_log_parts(seed_observation))
                    if post_settle_rng is not None:
                        log_parts.append(f" rng_after_settle=0x{post_settle_rng:08X}")
                    print("".join(log_parts))
                    progress_payload = {
                        "script": "Seed-Bruteforcer.py",
                        "phase": "searching",
                        "matched": False,
                        "target_seed": int(args.target) & 0xFFFF,
                        "start_delay": int(current_start_delay),
                        "searched_max_delay": int(args.max_delay),
                        "title_skip_start_delay": int(title_skip_start_delay),
                        "title_skip_start_delay_start": int(title_skip_start_delay_start),
                        "title_skip_start_delay_max": int(title_skip_start_delay_max),
                        "current_delay": int(delay_frames),
                        "attempts": int(attempts_checked),
                        "branch_failures": int(branch_failures),
                        "latest_button": str(observed_button),
                        "latest_seed": int(seed_value) & 0xFFFF,
                        "latest_seed_frame": int(seed_frame),
                        "latest_rng_at_seed": int(rng_value) & 0xFFFFFFFF,
                        "latest_rng_after_settle": None
                        if post_settle_rng is None
                        else int(post_settle_rng) & 0xFFFFFFFF,
                        "latest_initial_seed_mirror": None
                        if seed_observation.initial_seed_mirror_value is None
                        else int(seed_observation.initial_seed_mirror_value) & 0xFFFF,
                        "latest_initial_seed_mirror_changed_from_pre_input": seed_observation.initial_seed_mirror_changed_from_pre_input,
                        "latest_initial_seed_mirror_matches_timer1": seed_observation.initial_seed_mirror_matches_timer1,
                        "rolling_checkpoint": str(paths.checkpoint_path),
                        "checkpoint_metadata": str(paths.checkpoint_metadata_path),
                    }
                    write_progress_marker(progress_payload)

                if attempts_checked % DEFAULT_ATTEMPT_LOG_EVERY == 0:
                    print(
                        "Attempt counter:"
                        f" attempts={attempts_checked}"
                        f" title_skip_start_delay={title_skip_start_delay}"
                        f" current_delay={delay_frames}"
                        f" latest_button={observed_button}"
                        f" latest_seed=0x{seed_value:04X}"
                    )
                    write_progress_marker(
                        {
                            "script": "Seed-Bruteforcer.py",
                            "phase": "searching",
                            "matched": False,
                            "target_seed": int(args.target) & 0xFFFF,
                            "start_delay": int(current_start_delay),
                            "searched_max_delay": int(args.max_delay),
                            "title_skip_start_delay": int(title_skip_start_delay),
                            "title_skip_start_delay_start": int(title_skip_start_delay_start),
                            "title_skip_start_delay_max": int(title_skip_start_delay_max),
                            "current_delay": int(delay_frames),
                            "attempts": int(attempts_checked),
                            "branch_failures": int(branch_failures),
                            "latest_button": str(observed_button),
                            "latest_seed": int(seed_value) & 0xFFFF,
                            "latest_seed_frame": int(seed_frame),
                            "latest_rng_at_seed": int(rng_value) & 0xFFFFFFFF,
                            "latest_initial_seed_mirror": None
                            if seed_observation.initial_seed_mirror_value is None
                            else int(seed_observation.initial_seed_mirror_value) & 0xFFFF,
                            "latest_initial_seed_mirror_changed_from_pre_input": seed_observation.initial_seed_mirror_changed_from_pre_input,
                            "latest_initial_seed_mirror_matches_timer1": seed_observation.initial_seed_mirror_matches_timer1,
                            "rolling_checkpoint": str(paths.checkpoint_path),
                            "checkpoint_metadata": str(paths.checkpoint_metadata_path),
                        }
                    )

                if seed_value == args.target:
                    replay_attempt = brute_force_attempt(
                        core=core,
                        baseline_checkpoint_path=paths.baseline_checkpoint_path,
                        checkpoint_path=paths.checkpoint_path,
                        use_runtime_checkpoint=use_runtime_checkpoint,
                        delay_frames=delay_frames,
                        button_name=button_name,
                        button_key=button_key,
                        seed_timeout=args.seed_timeout,
                        pre_input_checkpoint_path=paths.replay_candidate_path,
                        pre_input_neutral_frames=pre_input_neutral_frames,
                        title_transition_timeout=args.title_transition_timeout,
                        post_transition_seed_timeout=post_transition_seed_timeout,
                        target_seed=args.target,
                    )
                    replay_seed = replay_attempt.seed_observation.seed_value
                    if replay_seed != args.target:
                        raise RuntimeError(
                            "Replay-candidate capture rerun did not reproduce"
                            f" target seed 0x{args.target:04X}:"
                            f" got 0x{replay_seed:04X}"
                            f" delay={delay_frames}"
                            f" button={button_name}"
                            f" title_skip_start_delay={title_skip_start_delay}"
                        )
                    return _record_success(
                        core=core,
                        args=args,
                        qt_mode=qt_mode,
                        rom_path=rom_path,
                        paths=paths,
                        delay_frames=delay_frames,
                        attempt=replay_attempt,
                        pre_input_neutral_frames=pre_input_neutral_frames,
                        title_skip_start_delay=title_skip_start_delay,
                    )

            if delay_frames == args.max_delay:
                break
            checkpoint_ready = advance_checkpoint_one_frame(
                core,
                baseline_checkpoint_path=paths.baseline_checkpoint_path,
                checkpoint_path=paths.checkpoint_path,
                checkpoint_metadata_path=paths.checkpoint_metadata_path,
                use_runtime_checkpoint=use_runtime_checkpoint,
                next_delay_frames=delay_frames + 1,
                target_seed=args.target,
            )
            if checkpoint_ready is False:
                print(
                    "Current title-skip start delay lane exhausted:"
                    f" title_skip_start_delay={title_skip_start_delay}"
                    f" last_valid_delay={delay_frames}"
                )
                break

    print(
        "No match found:"
        f" target=0x{args.target:04X}"
        f" searched_delays={start_delay}..{args.max_delay}"
        f" title_skip_start_delay_range={title_skip_start_delay_start}..{title_skip_start_delay_max}"
        f" branch_failures={branch_failures}"
    )
    _remove_file_if_present(paths.replay_candidate_path)
    write_status_marker_from_env(
        STATUS_ENV_NAME,
        {
            "script": "Seed-Bruteforcer.py",
            "matched": False,
            "target_seed": int(args.target) & 0xFFFF,
            "start_delay": int(start_delay),
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
