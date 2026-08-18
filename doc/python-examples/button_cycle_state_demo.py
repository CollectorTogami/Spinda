r"""Cycle GBA buttons, then save and reload two file-backed save states.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe button_cycle_state_demo.py
    <repo-root>\.venv-mgba\bin\python.exe button_cycle_state_demo.py C:\path\to\game.gba

What this demonstrates:
- opening the bundled sample GBA ROM by default, or a caller-supplied ROM
- cycling UP, DOWN, LEFT, RIGHT, B, A, L, R, START in that order
- holding each button for two frames, then releasing for two frames
- saving file-backed states named `state` and `state2` next to the ROM
- loading `state`, then loading `state2`
"""

from __future__ import annotations

from pathlib import Path

from mgba._pylib import lib
import mgba.vfs

from _helpers import build_parser, load_core, print_core_summary, require_gba


REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ROM = REPO_ROOT / "cinema" / "gba" / "irq" / "keyirq" / "test.gba"
BUTTON_SEQUENCE = [
    ("UP", "KEY_UP"),
    ("DOWN", "KEY_DOWN"),
    ("LEFT", "KEY_LEFT"),
    ("RIGHT", "KEY_RIGHT"),
    ("B", "KEY_B"),
    ("A", "KEY_A"),
    ("L", "KEY_L"),
    ("R", "KEY_R"),
    ("START", "KEY_START"),
]
SAVE_STATE_FLAGS = 0


def save_state_file(core, path: Path) -> None:
    """Save a file-backed state using the low-level mGBA C API."""
    vf = mgba.vfs.open_path(str(path), "w+")
    if not vf:
        raise SystemExit(f"Could not open savestate path for writing: {path}")
    try:
        if not lib.mCoreSaveStateNamed(core._core, vf.handle, SAVE_STATE_FLAGS):
            raise SystemExit(f"mCoreSaveStateNamed(...) failed for {path}")
    finally:
        vf.close()


def load_state_file(core, path: Path) -> None:
    """Load a file-backed state using the low-level mGBA C API."""
    vf = mgba.vfs.open_path(str(path), "r")
    if not vf:
        raise SystemExit(f"Could not open savestate path for reading: {path}")
    try:
        if not lib.mCoreLoadStateNamed(core._core, vf.handle, SAVE_STATE_FLAGS):
            raise SystemExit(f"mCoreLoadStateNamed(...) failed for {path}")
    finally:
        vf.close()


def main() -> int:
    """Cycle buttons and prove file-backed savestates load back correctly."""

    parser = build_parser(
        "Cycle GBA buttons, save two file-backed savestates, then load both."
    )
    parser.add_argument(
        "rom",
        nargs="?",
        default=str(DEFAULT_ROM),
        help="Path to a GBA ROM. Defaults to the bundled keyirq sample ROM.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    core = require_gba(core)
    print_core_summary(core, rom)

    state_path = rom.parent / "state"
    state2_path = rom.parent / "state2"

    core.reset()
    core.set_keys(raw=0)
    print("Core reset complete.")

    for button_name, attr_name in BUTTON_SEQUENCE:
        hold_start = core.frame_counter
        core.set_keys(getattr(core, attr_name))
        core.run_frame()
        core.run_frame()
        hold_end = core.frame_counter

        release_start = core.frame_counter
        core.set_keys(raw=0)
        core.run_frame()
        core.run_frame()
        release_end = core.frame_counter

        print(
            "Button"
            f" {button_name}: hold_start={hold_start} hold_end={hold_end}"
            f" release_start={release_start} release_end={release_end}"
        )

    print(f"Completed button cycle; frame_counter={core.frame_counter}")

    save_state_file(core, state_path)
    print(f"Saved state: {state_path} frame_counter={core.frame_counter}")

    core.run_frame()
    print(f"Advanced one frame; frame_counter={core.frame_counter}")

    save_state_file(core, state2_path)
    print(f"Saved state2: {state2_path} frame_counter={core.frame_counter}")

    load_state_file(core, state_path)
    print(f"Loaded state: {state_path} frame_counter={core.frame_counter}")

    load_state_file(core, state2_path)
    print(f"Loaded state2: {state2_path} frame_counter={core.frame_counter}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
