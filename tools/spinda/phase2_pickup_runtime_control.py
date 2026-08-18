"""Write safe-point runtime controls for the Phase 2 pickup-state builder.

The builder polls this JSON file between save jobs. Use this tool instead of
toggling mGBA Custom Features while the Python script owns the runtime.
"""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DEFAULT_CONTROL_FILE = Path(__file__).resolve().parents[2] / "Phase2PickupStates" / "_phase2_pickup_control.json"
CHOICES = ("on", "off", "leave")


def _utc_now() -> str:
    """Return a compact UTC timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _choice_to_bool(value: str) -> bool | None:
    """Convert one CLI choice to JSON boolean/omission semantics."""

    if value == "on":
        return True
    if value == "off":
        return False
    return None


def _set_if_requested(payload: dict[str, Any], key: str, value: str) -> None:
    """Add a key only when the operator requested a concrete value."""

    parsed = _choice_to_bool(value)
    if parsed is not None:
        payload[key] = parsed


def build_parser() -> argparse.ArgumentParser:
    """Build the runtime-control CLI."""

    parser = argparse.ArgumentParser(
        description="Write Phase 2 pickup runtime-control JSON for safe-point feature toggles.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--control-file", type=Path, default=DEFAULT_CONTROL_FILE)
    parser.add_argument(
        "--human-check",
        action="store_true",
        help="Disable audio killswitch, no-render, and fast-forward for visual checking.",
    )
    parser.add_argument(
        "--performance",
        action="store_true",
        help="Enable audio killswitch, no-render, and unbounded fast-forward.",
    )
    parser.add_argument("--audio-killswitch", choices=CHOICES, default="leave")
    parser.add_argument("--no-render", choices=CHOICES, default="leave")
    parser.add_argument("--fast-forward", choices=CHOICES, default="leave")
    parser.add_argument("--fast-forward-unbounded", choices=CHOICES, default="leave")
    parser.add_argument("--pause-builder", choices=CHOICES, default="leave")
    parser.add_argument("--stop-builder", choices=CHOICES, default="leave")
    return parser


def payload_from_args(args: argparse.Namespace) -> dict[str, Any]:
    """Create the control-file payload from profile and explicit flags."""

    if args.human_check and args.performance:
        raise ValueError("--human-check and --performance are mutually exclusive")

    payload: dict[str, Any] = {
        "schema_version": 1,
        "updated_at": _utc_now(),
        "source": Path(__file__).name,
    }

    if args.human_check:
        payload.update(
            {
                "audio_killswitch": False,
                "no_render_mode": False,
                "fast_forward": False,
                "fast_forward_unbounded": False,
                "pause_builder": False,
                "stop_builder": True,
            }
        )
    if args.performance:
        payload.update(
            {
                "audio_killswitch": True,
                "no_render_mode": True,
                "fast_forward": True,
                "fast_forward_unbounded": True,
                "pause_builder": False,
                "stop_builder": False,
            }
        )

    _set_if_requested(payload, "audio_killswitch", args.audio_killswitch)
    _set_if_requested(payload, "no_render_mode", args.no_render)
    _set_if_requested(payload, "fast_forward", args.fast_forward)
    _set_if_requested(payload, "fast_forward_unbounded", args.fast_forward_unbounded)
    _set_if_requested(payload, "pause_builder", args.pause_builder)
    _set_if_requested(payload, "stop_builder", args.stop_builder)

    if not any(
        key in payload
        for key in (
            "audio_killswitch",
            "no_render_mode",
            "fast_forward",
            "fast_forward_unbounded",
            "pause_builder",
            "stop_builder",
        )
    ):
        raise ValueError("No runtime feature controls were requested.")
    return payload


def write_control_file(path: Path, payload: dict[str, Any]) -> None:
    """Write the control file atomically."""

    path = path.expanduser().absolute()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_name(f"{path.name}.tmp")
    tmp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp_path.replace(path)


def main() -> int:
    """CLI entrypoint."""

    args = build_parser().parse_args()
    payload = payload_from_args(args)
    write_control_file(args.control_file, payload)
    print(f"Wrote runtime control: {args.control_file}")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
