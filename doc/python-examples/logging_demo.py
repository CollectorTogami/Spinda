r"""Install a custom Python logger for mGBA.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe logging_demo.py C:\path\to\game.gba --frames 10

What this demonstrates:
- subclassing mgba.log.Logger
- installing that logger as the default mGBA logger
- running a few frames while Python receives any emitted messages
"""

from __future__ import annotations

import mgba.log

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


class DemoLogger(mgba.log.Logger):
    """Print log messages in a compact tagged format."""

    def log(self, category, level, message):
        category_name = self.category_name(category)
        print(f"[{category_name}] level={level}: {message}")


def main() -> int:
    """Run a short emulation window while the custom logger collects messages."""

    parser = build_parser("Install a Python logger and run a few frames.")
    add_rom_argument(parser)
    parser.add_argument(
        "--frames",
        type=int,
        default=10,
        help="How many frames to run after installing the logger.",
    )
    args = parser.parse_args()

    mgba.log.install_default(DemoLogger())
    print("Installed DemoLogger as the default mGBA logger.")

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    for _ in range(args.frames):
        core.run_frame()

    print("Finished running frames with the custom logger installed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
