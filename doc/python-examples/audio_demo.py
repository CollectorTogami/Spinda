r"""Collect audio samples from the mGBA Python bindings.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe audio_demo.py C:\path\to\game.gba --frames 180 --output samples.raw

What this demonstrates:
- configuring the core audio buffer
- reading stereo audio samples through mgba.audio.StereoBuffer
- optionally writing the samples to a raw signed-16-bit little-endian file
"""

from __future__ import annotations

from array import array
from pathlib import Path

from _helpers import add_rom_argument, build_parser, load_core, print_core_summary


def main() -> int:
    """Run the audio capture demo from the command line."""

    parser = build_parser("Run frames and read stereo audio samples.")
    add_rom_argument(parser)
    parser.add_argument(
        "--frames",
        type=int,
        default=180,
        help="How many frames to run before reading audio.",
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=2048,
        help="How many stereo frames to request from the audio buffer.",
    )
    parser.add_argument(
        "--sample-rate",
        type=int,
        default=44100,
        help="Output sample rate used for the audio buffer reader.",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional raw PCM output file (.raw). Leave empty to skip writing.",
    )
    args = parser.parse_args()

    core, rom = load_core(args.rom)
    print_core_summary(core, rom)
    core.reset()

    core.set_audio_buffer_size(args.samples * 2)
    channels = core.get_audio_channels()
    channels.set_rate(args.sample_rate)

    for _ in range(args.frames):
        core.run_frame()

    samples = channels.read(args.samples)
    stereo_frames = len(samples) // 2
    print(f"Read {stereo_frames} stereo frame(s), {len(samples)} sample values total.")

    if args.output:
        output = Path(args.output).expanduser().resolve()
        pcm = array("h", samples)
        output.write_bytes(pcm.tobytes())
        print(f"Wrote raw PCM: {output}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
