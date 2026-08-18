r"""Attach a custom GBA SIO driver.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe gba_sio_demo.py C:\path\to\game.gba --frames 60

What this demonstrates:
- subclassing mgba.gba.GBASIODriver
- attaching the driver to a GBA core
- observing any SIO register writes the game performs

This script is runnable, but whether the game actually touches link hardware
depends on the ROM you load.
"""

from __future__ import annotations

from mgba.gba import GBA, GBASIODriver

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary, require_gba


class LoggingGBASIODriver(GBASIODriver):
    """Capture register writes so the script can print them later."""

    def __init__(self):
        super().__init__()
        self.writes = []

    def write_register(self, address, value):
        self.writes.append((address, value))
        print(f"SIO write: address=0x{address:X} value=0x{value:04X}")
        return value


def main() -> int:
    """Run the GBA SIO example and clean up the attached device afterward."""

    parser = build_parser("Attach a custom GBA SIO driver.")
    add_rom_argument(parser)
    parser.add_argument(
        "--frames",
        type=int,
        default=60,
        help="How many frames to run while the driver is attached.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    core = require_gba(core)
    print_core_summary(core, rom)

    driver = LoggingGBASIODriver()
    core.attach_sio(driver, mode=GBA.SIO_MULTI)
    try:
        core.reset()

        for _ in range(args.frames):
            core.run_frame()

        print(f"Captured {len(driver.writes)} SIO register write(s).")
    finally:
        # Custom local addition: detach the native driver explicitly so the
        # Windows teardown path cannot outlive the Python driver object.
        core.detach_sio(GBA.SIO_MULTI)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
