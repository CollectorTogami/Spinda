r"""CPU register inspection example for GBA and GB cores.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe registers_demo.py C:\path\to\game.gba

What this demonstrates:
- reading CPU registers after reset
- the register wrappers exposed by mgba.gba.GBA and mgba.gb.GB
- safe no-op writes back to writable register properties
"""

from __future__ import annotations

from mgba.gba import GBA
from mgba.gb import GB

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def _show_gba(core: GBA) -> None:
    """Print a small GBA register snapshot and exercise safe setter writes."""

    print("ARM registers:")
    for reg in range(4):
        print(f"  r{reg}=0x{core.cpu.gprs[reg]:08X}")
    print(f"  sp=0x{core.cpu.sp:08X}")
    print(f"  lr=0x{core.cpu.lr:08X}")
    print(f"  pc=0x{core.cpu.pc:08X}")
    print(f"  cpsr=0x{core.cpu.cpsr:08X}")

    core.cpu.sp = core.cpu.sp
    core.cpu.pc = core.cpu.pc
    print("Wrote SP and PC back to themselves as a safe setter demo.")


def _show_gb(core: GB) -> None:
    """Print a small GB register snapshot and exercise safe setter writes."""

    print("SM83 registers:")
    print(f"  a=0x{core.cpu.a:02X}")
    print(f"  f=0x{core.cpu.f:02X}")
    print(f"  b=0x{core.cpu.b:02X}")
    print(f"  c=0x{core.cpu.c:02X}")
    print(f"  d=0x{core.cpu.d:02X}")
    print(f"  e=0x{core.cpu.e:02X}")
    print(f"  h=0x{core.cpu.h:02X}")
    print(f"  l=0x{core.cpu.l:02X}")
    print(f"  af=0x{core.cpu.af:04X}")
    print(f"  bc=0x{core.cpu.bc:04X}")
    print(f"  de=0x{core.cpu.de:04X}")
    print(f"  hl=0x{core.cpu.hl:04X}")
    print(f"  sp=0x{core.cpu.sp:04X}")
    print(f"  pc=0x{core.cpu.pc:04X}")

    core.cpu.a = core.cpu.a
    core.cpu.hl = core.cpu.hl
    print("Wrote A and HL back to themselves as a safe setter demo.")


def main() -> int:
    """Dispatch to the GBA or GB register view for the loaded ROM."""

    parser = build_parser("Inspect the exposed CPU register wrappers.")
    add_rom_argument(parser)
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    if isinstance(core, GBA):
        _show_gba(core)
    elif isinstance(core, GB):
        _show_gb(core)
    else:
        raise SystemExit("Unsupported core type for this example.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
