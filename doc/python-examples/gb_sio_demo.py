r"""Attach a custom GB serial/link driver.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe gb_sio_demo.py C:\path\to\game.gb --frames 120

What this demonstrates:
- subclassing mgba.gb.GBSIODriver
- attaching the driver to a GB core
- recording serial data written by the emulated game

This script is runnable, but activity depends on the ROM.
"""

from __future__ import annotations

from mgba.gb import GBSIODriver

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary, require_gb


class LoggingGBSIODriver(GBSIODriver):
    """Capture SB/SC writes so the script can print them later."""

    def __init__(self):
        super().__init__()
        self.sb_writes = []
        self.sc_writes = []

    def write_sb(self, value):
        self.sb_writes.append(value)
        print(f"SB write: 0x{value:02X}")

    def write_sc(self, value):
        self.sc_writes.append(value)
        print(f"SC write: 0x{value:02X}")
        return value


def main() -> int:
    """Run the GB serial-link example from a host Python session."""

    parser = build_parser("Attach a custom GB link driver.")
    add_rom_argument(parser)
    parser.add_argument(
        "--frames",
        type=int,
        default=120,
        help="How many frames to run while the driver is attached.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    core = require_gb(core)
    print_core_summary(core, rom)

    driver = LoggingGBSIODriver()
    core.attach_sio(driver)
    core.reset()

    for _ in range(args.frames):
        core.run_frame()

    print(f"Captured {len(driver.sb_writes)} SB write(s).")
    print(f"Captured {len(driver.sc_writes)} SC write(s).")
    core._link = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
