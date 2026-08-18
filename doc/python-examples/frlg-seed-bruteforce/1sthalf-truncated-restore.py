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
# Quick-edit defaults.
# Change DEFAULT_TARGET_SEED if you want a different target without passing
# --target on the command line.
DEFAULT_TARGET_SEED = 0xFBC7
DEFAULT_MAX_DELAY = 500_000_000
DEFAULT_SETTLE_FRAMES = 3
DEFAULT_SEED_TIMEOUT = 600
DEFAULT_PRE_INPUT_NEUTRAL_FRAMES = 1
DEFAULT_PROGRESS_EVERY = 50
DEFAULT_ATTEMPT_LOG_EVERY = 100
DEFAULT_TITLE_SKIP_TIMEOUT = 600
DEFAULT_CHECKPOINT_WAIT_TIMEOUT = 600

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

    resolved = path.expanduser().resolve()
    if _looks_like_mgba_executable(resolved):
        resolved = resolved.parent
    if resolved.name.startswith("build-"):
        return resolved.parent
    return resolved


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


