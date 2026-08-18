r"""Basic mGBA host-side Python example.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe basic_core_demo.py C:\path\to\game.gba --frames 5

What this demonstrates:
- loading a ROM with mgba.core.load_path(...)
- resetting the core before runtime work
- printing basic metadata
- pressing a button for one frame
- advancing the emulator by a few frames
"""

from __future__ import annotations

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def main() -> int:
    """Run the smallest end-to-end core demo in this example set."""

    parser = build_parser("Load a ROM, reset it, and run a few frames.")
    add_rom_argument(parser)
    parser.add_argument(
        "--frames",
        type=int,
        default=3,
        help="How many extra frames to run after the one-frame button tap.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)

    core.reset()
    print("Core reset complete.")

    key_up = core.KEY_UP
    core.add_keys(key_up)
    core.run_frame()
    core.clear_keys(key_up)
    print("Tapped UP for one frame.")

    for frame in range(args.frames):
        core.run_frame()
        print(f"Ran extra frame {frame + 1}; frame_counter={core.frame_counter}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
