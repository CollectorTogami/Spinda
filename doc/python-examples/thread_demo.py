r"""Run the emulator in mgba.thread.Thread and inspect it safely.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe thread_demo.py C:\path\to\game.gba --seconds 0.1

What this demonstrates:
- starting a core on the mGBA thread wrapper
- pausing and unpausing the thread
- using thread.use_core() to inspect the core safely
"""

from __future__ import annotations

import time

import mgba.thread

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def main() -> int:
    """Start the threaded core wrapper, inspect it, and stop it cleanly."""

    parser = build_parser("Run a core through mgba.thread.Thread.")
    add_rom_argument(parser)
    parser.add_argument(
        "--seconds",
        type=float,
        default=0.1,
        help="How long to let the emulation thread run between inspections.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    thread = mgba.thread.Thread()
    thread.start(core)
    print("Thread started.")

    try:
        time.sleep(args.seconds)
        thread.pause()
        time.sleep(0.05)

        with thread.use_core() as paused_core:
            print("Paused thread state:")
            print(f"  frame_counter={paused_core.frame_counter}")
            print(f"  title={paused_core.game_title!r}")

        thread.unpause()
        print("Thread resumed.")
        time.sleep(args.seconds)
    finally:
        thread.end()
        print("Thread stopped.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
