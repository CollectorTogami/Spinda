r"""Save a PNG frame from the mGBA Python bindings.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe screenshot_demo.py C:\path\to\game.gba --output frame.png --frames 8

What this demonstrates:
- creating an mgba.image.Image buffer
- attaching it as the video buffer
- running a few frames
- writing the resulting frame to PNG without Pillow
"""

from __future__ import annotations

from pathlib import Path

import mgba.image

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def main() -> int:
    """Capture one rendered frame and write it to a PNG file."""

    parser = build_parser("Capture a frame to a PNG file.")
    add_rom_argument(parser)
    parser.add_argument(
        "--output",
        default="frame.png",
        help="Path to the PNG file to create.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=1,
        help="How many frames to run before saving the image.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)

    width, height = core.desired_video_dimensions()
    image = mgba.image.Image(width, height)
    core.set_video_buffer(image)
    core.reset()

    for _ in range(args.frames):
        core.run_frame()

    output = Path(args.output).expanduser().resolve()
    with output.open("wb") as handle:
        ok = image.save_png(handle)
    if not ok:
        raise SystemExit(f"PNG write failed: {output}")

    print(f"Wrote screenshot: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
