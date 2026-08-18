r"""Use mgba.vfs with both raw file access and core loading.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe vfs_demo.py C:\path\to\game.gba

What this demonstrates:
- opening a ROM as an mGBA VFile
- reading raw bytes through the VFS wrapper
- detecting a platform with find_vf(...)
- loading a core through load_vf(...)
"""

from __future__ import annotations

import mgba.core
import mgba.vfs

from _helpers import add_rom_argument, build_parser, load_core, platform_name, print_core_summary


def main() -> int:
    """Open a ROM through VFS and prove the VFile-backed load path works."""

    parser = build_parser("Open a ROM with mgba.vfs and load it via VFile.")
    add_rom_argument(parser)
    args = parser.parse_args()

    vf = mgba.vfs.open_path(args.rom, "r")
    if not vf:
        raise SystemExit(f"Could not open ROM through VFS: {args.rom}")

    header = vf.read_all(16)
    print("First 16 bytes:", " ".join(f"{byte:02X}" for byte in header))

    vf.seek(0, 0)
    detected = mgba.core.find_vf(vf)
    if not detected:
        raise SystemExit("find_vf(...) did not recognize this ROM.")
    print(f"find_vf(...) detected: {platform_name(detected)}")

    # Reopen the file for load_vf(...) and keep the VFile alive for the life of
    # the demo. The Python wrapper around ROM VFiles is thin, so it is safer not
    # to let the ROM VFile get collected immediately after load_vf(...).
    rom_vf = mgba.vfs.open_path(args.rom, "r")
    if not rom_vf:
        raise SystemExit(f"Could not reopen ROM through VFS: {args.rom}")

    core = mgba.core.load_vf(rom_vf)
    if not core:
        raise SystemExit("load_vf(...) failed")

    print_core_summary(core, load_core(args.rom)[1])
    core.reset()
    print("Core loaded through VFile and reset successfully.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
