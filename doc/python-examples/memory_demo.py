r"""Memory read/write/search example for the mGBA Python bindings.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe memory_demo.py C:\path\to\game.gba

What this demonstrates:
- reading typed memory views
- writing to a known writable region
- restoring the original value after the demo write
- running a small memory search
"""

from __future__ import annotations

from mgba.memory import Memory

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def _pick_writable_region(core):
    """Choose a small writable memory view that is safe for the demo write."""

    if hasattr(core.memory, "iwram"):
        return core.memory.iwram, "iwram"
    if hasattr(core.memory, "hram"):
        return core.memory.hram, "hram"
    return core.memory, "memory"


def main() -> int:
    """Demonstrate typed memory access plus one reversible write."""

    parser = build_parser("Read, write, and search emulated memory.")
    add_rom_argument(parser)
    parser.add_argument(
        "--search-limit",
        type=int,
        default=16,
        help="Maximum number of memory search results to print.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    region, region_name = _pick_writable_region(core)
    print(f"Using writable region: {region_name} at base=0x{region.base:X}")

    original = region.u8[0]
    print(f"Original first byte: 0x{original:02X}")

    demo_value = original ^ 0x01
    region.u8[0] = demo_value
    print(f"Wrote 0x{demo_value:02X} to {region_name}[0]")

    reread = region.u8[0]
    print(f"Read back value: 0x{reread:02X}")

    results = core.memory.search(
        demo_value,
        type=Memory.SEARCH_INT,
        flags=Memory.RW,
        limit=args.search_limit,
    )
    print(f"Search results for {demo_value}: {len(results)} hit(s)")
    for result in results[: args.search_limit]:
        print(f"  address=0x{result.address:X} value={result.value}")

    region.u8[0] = original
    print("Restored original byte.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
