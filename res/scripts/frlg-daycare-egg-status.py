"""FR/LG daycare egg status monitor for the Qt Python scripting path.

This is the Python counterpart to `frlg-daycare-egg-status.lua`.

It updates one named scripting buffer once per frame and shows:

- whether FR/LG currently has a pending daycare egg
- the stored lower 16-bit offspring personality value
- steps until the next daycare egg-generation check
- the daycare egg hatch-cycle counter byte

Like the Lua version, this is meant for live calibration work rather than batch
automation.
"""

from __future__ import annotations

import os

from mgba import qt as mgba_qt
from mgba._pylib import lib
from mgba.gba import GBA


SAVEBLOCK1_PTR = 0x03005008
DAYCARE_OFFSET = 0x2F80
OFFSPRING_PERSONALITY_OFFSET = 0x118
STEP_COUNTER_OFFSET = 0x11A
MON2_STEPS_OFFSET = 0x114

ROM_TITLE_ADDR = 0x080000A0
ROM_TITLE_LEN = 12
ROM_CODE_ADDR = 0x080000AC
ROM_CODE_LEN = 4

BUFFER_NAME = "FRLG Daycare Egg"
BUFFER_COLS = 44
BUFFER_ROWS = 8
MARKER_ENV = "MGBA_PYTHON_DAYCARE_STATUS_MARKER"
EXIT_AFTER_UPDATES_ENV = "MGBA_PYTHON_DAYCARE_STATUS_EXIT_AFTER_UPDATES"


def _abort_requested() -> bool:
    """Report whether the live Qt scripting session is being torn down."""

    return bool(hasattr(lib, "mPythonQtAbortRequested") and lib.mPythonQtAbortRequested())


def _exit_after_updates() -> int | None:
    """Parse an optional test hook that ends the monitor after N updates."""

    raw = os.getenv(EXIT_AFTER_UPDATES_ENV, "").strip()
    if not raw:
        return None
    value = int(raw, 0)
    if value <= 0:
        return None
    return value


def _read_ascii(core, address: int, size: int) -> str:
    """Read one ASCII header field from ROM space."""

    data = bytes(core.memory.u8[address + offset] for offset in range(size))
    return data.split(b"\0", 1)[0].decode("ascii", errors="ignore").rstrip()


def detect_frlg(core) -> tuple[bool, str, str]:
    """Identify FireRed/LeafGreen from the ROM header exposed to the live core."""

    if core.platform not in (0, GBA.PLATFORM_GBA):
        return False, "", ""

    game_code = _read_ascii(core, ROM_CODE_ADDR, ROM_CODE_LEN)
    game_title = _read_ascii(core, ROM_TITLE_ADDR, ROM_TITLE_LEN)
    is_frlg = game_code in {"BPRE", "BPGE"} or game_title in {"POKEMON FIRE", "POKEMON LEAF"}
    return is_frlg, game_code, game_title


def read_daycare_status(core):
    """Read the FR/LG daycare fields needed by the live status panel."""

    save_block1 = core.memory.u32[SAVEBLOCK1_PTR]
    if save_block1 == 0:
        return None, "SaveBlock1 pointer is not ready yet"

    daycare = save_block1 + DAYCARE_OFFSET
    offspring_personality = core.memory.u16[daycare + OFFSPRING_PERSONALITY_OFFSET]
    hatch_step_counter = core.memory.u8[daycare + STEP_COUNTER_OFFSET]
    mon2_steps = core.memory.u32[daycare + MON2_STEPS_OFFSET]
    low_byte = mon2_steps & 0xFF

    if offspring_personality != 0:
        steps_until_next_egg_check = 0
    elif low_byte == 0xFF:
        steps_until_next_egg_check = 256
    else:
        # FR/LG checks for egg generation when the low byte reaches 0xFF.
        steps_until_next_egg_check = 0xFF - low_byte

    return {
        "save_block1": save_block1,
        "offspring_personality": offspring_personality,
        "egg_waiting": offspring_personality != 0,
        "hatch_step_counter": hatch_step_counter,
        "steps_until_next_egg_check": steps_until_next_egg_check,
        "mon2_steps": mon2_steps,
    }, None


def render_message(lines) -> None:
    """Write the current status text into the named Qt scripting buffer."""

    text = "\n".join(lines) + "\n"
    mgba_qt.set_text_buffer(BUFFER_NAME, text, cols=BUFFER_COLS, rows=BUFFER_ROWS)

    marker_path = os.getenv(MARKER_ENV)
    if marker_path:
        with open(marker_path, "w", encoding="utf-8") as handle:
            handle.write(text)


def build_lines(core) -> list[str]:
    """Build the human-readable status panel for the current frame."""

    is_frlg, game_code, game_title = detect_frlg(core)
    if core.platform != GBA.PLATFORM_GBA or not is_frlg:
        return [
            "FR/LG Daycare Egg Status",
            "",
            "Unsupported game.",
            "Load FireRed or LeafGreen.",
            "Code: {}".format(game_code or "(none)"),
            "Title: {}".format(game_title or "(none)"),
        ]

    status, error = read_daycare_status(core)
    if not status:
        return [
            "FR/LG Daycare Egg Status",
            "",
            "Waiting: {}".format(error),
        ]

    if status["egg_waiting"]:
        check_text = "waiting for pickup"
    else:
        check_text = str(status["steps_until_next_egg_check"])

    return [
        "FR/LG Daycare Egg Status",
        "",
        "Egg waiting: {}".format("YES" if status["egg_waiting"] else "NO"),
        "Lower half: 0x{:04X}".format(status["offspring_personality"]),
        "Steps to egg check: {}".format(check_text),
        "Daycare hatch counter: {}".format(status["hatch_step_counter"]),
        "Mon2 steps low byte: 0x{:02X}".format(status["mon2_steps"] & 0xFF),
        "Frame: {}".format(core.frame_counter),
    ]


def main() -> int:
    """Keep the daycare status panel live until the Qt script session aborts."""

    core = mgba_qt.current_core()
    exit_after_updates = _exit_after_updates()
    updates = 0
    print("FRLG Daycare Egg Status (Python): monitoring active")

    while True:
        # Closing the Qt window raises the abort flag before the live core is
        # fully torn down. Poll it between each stage of the loop so the script
        # stops touching memory/buffers as soon as shutdown starts.
        if _abort_requested():
            break
        lines = build_lines(core)
        if _abort_requested():
            break
        render_message(lines)
        updates += 1
        if exit_after_updates is not None and updates >= exit_after_updates:
            break
        if _abort_requested():
            break
        try:
            core.run_frame()
        except RuntimeError:
            # Window close / script abort tears down the live Qt bridge by
            # making frame stepping fail. Treat that as a normal exit path.
            if _abort_requested():
                break
            raise

    return 0


if __name__ == "__main__":
    # Host-side Python examples still use SystemExit for normal CLI behavior,
    # but the embedded Qt scripting path treats a self-raised SystemExit as a
    # script error. Return normally there so short test runs look successful.
    if mgba_qt.is_available():
        main()
    else:
        raise SystemExit(main())
