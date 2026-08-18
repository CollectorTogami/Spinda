from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import mgba.core
import mgba.vfs
from mgba._pylib import lib

from accuracy_common import DEFAULT_OUTPUT_DIR, write_json


SAVE_STATE_FLAGS = 0

PROFILE_DEFINITIONS: dict[str, tuple[tuple[str, int, int], ...]] = {
    "minimal": (
        ("KEYINPUT", 16, 0x04000130),
        ("REG_TM1CNT_L", 16, 0x04000104),
        ("REG_TM1CNT_H", 16, 0x04000106),
        ("gRngValue", 32, 0x03005000),
    ),
    "frlg_title_seed": (
        ("KEYINPUT", 16, 0x04000130),
        ("REG_TM1CNT_L", 16, 0x04000104),
        ("REG_TM1CNT_H", 16, 0x04000106),
        ("gMain.vblankCounter2", 32, 0x03003114),
        ("gRngValue", 32, 0x03005000),
    ),
    "frlg_route": (
        ("KEYINPUT", 16, 0x04000130),
        ("REG_TM1CNT_L", 16, 0x04000104),
        ("REG_TM1CNT_H", 16, 0x04000106),
        ("gMain.vblankCounter2", 32, 0x03003114),
        ("gRngValue", 32, 0x03005000),
        ("gSaveBlock1Ptr", 32, 0x03005008),
    ),
}


@dataclass(frozen=True)
class ProbeField:
    name: str
    width: int
    address: int


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Capture a deterministic host-side memory/frame trace from one mGBA Python build. "
            "This is intended for later stock-vs-custom comparison and does not compare by itself."
        )
    )
    parser.add_argument("rom", type=Path, help="ROM path to load.")
    parser.add_argument(
        "--save",
        type=Path,
        help="Optional persistent save file to load before capture.",
    )
    parser.add_argument(
        "--state",
        type=Path,
        help="Optional savestate to load after reset/save load.",
    )
    parser.add_argument(
        "--profile",
        choices=sorted(PROFILE_DEFINITIONS),
        default="frlg_title_seed",
        help="Built-in memory probe profile.",
    )
    parser.add_argument(
        "--probe",
        action="append",
        default=[],
        help="Extra probe in NAME:WIDTH:ADDRESS form, e.g. TaskState:16:0x03005ABC",
    )
    parser.add_argument(
        "--input-tape",
        type=Path,
        help="Optional `mgba-input-tape-v1` JSON to replay during capture.",
    )
    parser.add_argument(
        "--frames",
        type=int,
        default=120,
        help="Number of frames to capture when no input tape is provided.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "trace_capture.json",
        help="Where to write the capture JSON.",
    )
    return parser.parse_args()


def parse_probe_spec(raw_spec: str) -> ProbeField:
    try:
        name, width_text, address_text = raw_spec.split(":", 2)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"Probe must use NAME:WIDTH:ADDRESS syntax: {raw_spec}"
        ) from exc
    width = int(width_text)
    if width not in (8, 16, 32):
        raise argparse.ArgumentTypeError(f"Unsupported probe width: {width}")
    address = int(address_text, 0)
    return ProbeField(name=name, width=width, address=address)


def _open_vfile(path: Path, mode: str):
    vfile = mgba.vfs.open_path(str(path), mode)
    if not vfile:
        raise RuntimeError(f"Could not open VFile for {path} with mode {mode}")
    return vfile


def load_save_file(core, path: Path) -> None:
    vfile = _open_vfile(path, "r")
    try:
        if not core.load_save(vfile):
            raise RuntimeError(f"mGBA refused save file: {path}")
    finally:
        vfile.close()


def load_state_file(core, path: Path) -> None:
    vfile = _open_vfile(path, "r")
    try:
        if not lib.mCoreLoadStateNamed(core._core, vfile.handle, SAVE_STATE_FLAGS):
            raise RuntimeError(f"mCoreLoadStateNamed(...) failed for {path}")
    finally:
        vfile.close()


def load_tape_masks(path: Path) -> list[int]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != "mgba-input-tape-v1":
        raise RuntimeError(f"Unsupported tape format in {path}")
    masks: list[int] = []
    for run in payload.get("runs", []):
        mask = int(run["mask"], 0) if isinstance(run["mask"], str) else int(run["mask"])
        frames = int(run["frames"])
        masks.extend(mask for _ in range(frames))
    return masks


def build_probe_fields(profile: str, extra_probes: Iterable[str]) -> list[ProbeField]:
    fields = [ProbeField(name, width, address) for name, width, address in PROFILE_DEFINITIONS[profile]]
    fields.extend(parse_probe_spec(raw_spec) for raw_spec in extra_probes)
    return fields


def read_probe_value(core, field: ProbeField) -> int:
    if field.width == 8:
        return int(core.memory.u8[field.address])
    if field.width == 16:
        return int(core.memory.u16[field.address])
    return int(core.memory.u32[field.address])


def snapshot(core, frame_index: int, applied_mask: int, fields: list[ProbeField]) -> dict[str, object]:
    return {
        "frame_index": frame_index,
        "frame_counter": int(getattr(core, "frame_counter", 0)),
        "applied_mask": applied_mask,
        "values": {
            field.name: read_probe_value(core, field)
            for field in fields
        },
    }


def main() -> int:
    args = parse_args()
    probe_fields = build_probe_fields(args.profile, args.probe)
    tape_masks = load_tape_masks(args.input_tape) if args.input_tape else None
    frame_count = len(tape_masks) if tape_masks is not None else args.frames

    core = mgba.core.load_path(str(args.rom))
    if core is None:
        raise SystemExit(f"Could not load ROM: {args.rom}")
    core.reset()
    if args.save:
        load_save_file(core, args.save)
    if args.state:
        load_state_file(core, args.state)
    core.set_keys(raw=0)

    samples = [snapshot(core, frame_index=-1, applied_mask=0, fields=probe_fields)]
    for frame_index in range(frame_count):
        applied_mask = tape_masks[frame_index] if tape_masks is not None else 0
        core.set_keys(raw=applied_mask)
        core.run_frame()
        samples.append(snapshot(core, frame_index=frame_index, applied_mask=applied_mask, fields=probe_fields))
    core.set_keys(raw=0)

    payload = {
        "meta": {
            "rom": str(args.rom),
            "save": str(args.save) if args.save else None,
            "state": str(args.state) if args.state else None,
            "profile": args.profile,
            "frame_count": frame_count,
            "input_tape": str(args.input_tape) if args.input_tape else None,
            "probe_fields": [
                {"name": field.name, "width": field.width, "address": field.address}
                for field in probe_fields
            ],
        },
        "samples": samples,
    }
    write_json(args.output_json, payload)
    print(f"Wrote trace capture: {args.output_json}")
    print(f"Frames captured: {frame_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
