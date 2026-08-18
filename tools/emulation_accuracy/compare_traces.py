from __future__ import annotations

import argparse
import json
from pathlib import Path

from accuracy_common import DEFAULT_OUTPUT_DIR, write_json


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Compare two capture_trace.py outputs and report the first divergence."
        )
    )
    parser.add_argument("left", type=Path, help="Left-hand trace JSON.")
    parser.add_argument("right", type=Path, help="Right-hand trace JSON.")
    parser.add_argument(
        "--ignore-field",
        action="append",
        default=[],
        help="Top-level sample field to ignore during comparison.",
    )
    parser.add_argument(
        "--output-json",
        type=Path,
        default=DEFAULT_OUTPUT_DIR / "trace_comparison.json",
        help="Where to write the comparison JSON.",
    )
    return parser.parse_args()


def load_trace(path: Path) -> dict[str, object]:
    return json.loads(path.read_text(encoding="utf-8"))


def filtered_sample(sample: dict[str, object], ignored: set[str]) -> dict[str, object]:
    return {key: value for key, value in sample.items() if key not in ignored}


def main() -> int:
    args = parse_args()
    left = load_trace(args.left)
    right = load_trace(args.right)
    ignored = set(args.ignore_field)
    left_samples = left.get("samples", [])
    right_samples = right.get("samples", [])

    mismatch: dict[str, object] | None = None
    sample_count_equal = len(left_samples) == len(right_samples)
    compared = min(len(left_samples), len(right_samples))
    for index in range(compared):
        left_sample = filtered_sample(left_samples[index], ignored)
        right_sample = filtered_sample(right_samples[index], ignored)
        if left_sample != right_sample:
            mismatch = {
                "index": index,
                "left": left_sample,
                "right": right_sample,
            }
            break

    traces_match = sample_count_equal and mismatch is None
    payload = {
        "left": str(args.left),
        "right": str(args.right),
        "sample_count_equal": sample_count_equal,
        "left_sample_count": len(left_samples),
        "right_sample_count": len(right_samples),
        "ignored_fields": sorted(ignored),
        "traces_match": traces_match,
        "first_mismatch": mismatch,
        "note": (
            "Matching traces are stronger runtime evidence than source diffs alone. "
            "A mismatch means the two builds diverged for the captured scenario."
        ),
    }
    write_json(args.output_json, payload)
    print(f"Wrote trace comparison: {args.output_json}")
    print(f"Traces match: {traces_match}")
    if mismatch is not None:
        print(f"First mismatch at sample index {mismatch['index']}")
    return 0 if traces_match else 1


if __name__ == "__main__":
    raise SystemExit(main())
