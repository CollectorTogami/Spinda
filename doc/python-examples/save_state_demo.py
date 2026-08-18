r"""Save, restore, and optionally export raw state bytes.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe save_state_demo.py C:\path\to\game.gba --output state.bin

What this demonstrates:
- saving raw state bytes in memory
- advancing the emulator after the save
- restoring the raw state back into the core
- optionally exporting the raw bytes to a file
"""

from __future__ import annotations

from pathlib import Path

from mgba._pylib import ffi

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def main() -> int:
    """Save one raw state blob, restore it, and optionally export the bytes."""

    parser = build_parser("Save raw state bytes, then restore them.")
    add_rom_argument(parser)
    parser.add_argument(
        "--pre-frames",
        type=int,
        default=10,
        help="Frames to run before saving state.",
    )
    parser.add_argument(
        "--post-frames",
        type=int,
        default=5,
        help="Frames to run after saving and before restoring.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional file to write the raw state bytes to.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    for _ in range(args.pre_frames):
        core.run_frame()

    saved = core.save_raw_state()
    if saved is None:
        raise SystemExit("save_raw_state() returned None")

    saved_frame = core.frame_counter
    saved_size = len(ffi.buffer(saved))
    print(f"Saved raw state at frame {saved_frame}; size={saved_size} bytes")

    for _ in range(args.post_frames):
        core.run_frame()
    print(f"Frame before restore: {core.frame_counter}")

    if not core.load_raw_state(saved):
        raise SystemExit("load_raw_state() failed")
    print(f"Frame after restore: {core.frame_counter}")

    if args.output:
        output = Path(args.output).expanduser().resolve()
        output.write_bytes(bytes(ffi.buffer(saved)))
        print(f"Wrote raw state bytes: {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
