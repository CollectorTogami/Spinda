"""Read-only progress and ETA monitor for the first-half raw CSV corpus."""

from __future__ import annotations

import argparse
import datetime as dt
import math
import sys
import time
from pathlib import Path
from typing import Iterable

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from first_half_raw_csv_audit import (  # noqa: E402
    DEFAULT_RAW_CSV_DIR,
    EXPECTED_TARGETS,
    audit_raw_csv_folder,
)


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse CLI options for one-shot or watch-mode ETA output."""

    parser = argparse.ArgumentParser(
        description="Watch first-half raw CSV output count and estimate finish time.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_RAW_CSV_DIR)
    parser.add_argument("--target-pairs", type=int, default=EXPECTED_TARGETS)
    parser.add_argument("--interval", type=float, default=30.0, help="Seconds between samples.")
    parser.add_argument(
        "--samples",
        type=int,
        default=1,
        help="Number of samples to print. Use 0 with --watch for endless monitoring.",
    )
    parser.add_argument("--watch", action="store_true", help="Keep sampling until complete or stopped.")
    parser.add_argument(
        "--settle-seconds",
        type=float,
        default=2.0,
        help="Pass-through size-settle window for the underlying auditor.",
    )
    return parser.parse_args(argv)


def _format_duration(seconds: float | None) -> str:
    """Format an ETA duration without pretending weak data is precise."""

    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    whole = int(seconds + 0.5)
    hours, rem = divmod(whole, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def _format_finish_time(seconds: float | None) -> str:
    """Return local finish timestamp if rate supports one."""

    if seconds is None or not math.isfinite(seconds) or seconds < 0:
        return "unknown"
    finish = dt.datetime.now().astimezone() + dt.timedelta(seconds=seconds)
    return finish.strftime("%Y-%m-%d %H:%M:%S %Z")


def _line(
    *,
    folder: Path,
    complete_pairs: int,
    save_files: int,
    state_files: int,
    target_pairs: int,
    elapsed: float,
    delta_pairs: int,
    delta_elapsed: float,
    bad_names: int,
    bad_sizes: int,
    unsettled_files: int,
    bad_target_naming: int = 0,
    organic_lanes_missing: int = 0,
    endpoint_exceptions_missing: int = 0,
) -> str:
    """Build one status line from current and prior sample data."""

    remaining = max(0, target_pairs - complete_pairs)
    rate = delta_pairs / delta_elapsed if delta_elapsed > 0 and delta_pairs > 0 else None
    eta_seconds = remaining / rate if rate and rate > 0 else None
    percent = (complete_pairs / target_pairs) * 100.0 if target_pairs else 0.0
    rate_text = f"{rate * 60.0:.2f} pairs/min" if rate else "unknown"
    timestamp = dt.datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S %Z")
    return (
        f"[{timestamp}] folder={folder} pairs={complete_pairs}/{target_pairs} "
        f"({percent:.3f}%) sav={save_files} ss0={state_files} "
        f"delta_pairs={delta_pairs} elapsed={elapsed:.1f}s rate={rate_text} "
        f"eta={_format_duration(eta_seconds)} finish={_format_finish_time(eta_seconds)} "
        f"organic_missing={organic_lanes_missing} endpoint_missing={endpoint_exceptions_missing} "
        f"bad_names={bad_names} bad_target_names={bad_target_naming} "
        f"bad_sizes={bad_sizes} unsettled={unsettled_files}"
    )


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint."""

    args = parse_args(argv)
    folder = args.folder.expanduser()
    if not folder.is_absolute():
        folder = folder.absolute()
    target_pairs = args.target_pairs
    max_samples = args.samples
    if args.watch and max_samples == 1:
        max_samples = 0

    started = time.time()
    previous_time: float | None = None
    previous_pairs: int | None = None
    printed = 0

    while True:
        result = audit_raw_csv_folder(
            folder,
            expected_targets=target_pairs,
            settle_seconds=args.settle_seconds,
            sample_limit=5,
        )
        now = time.time()
        if previous_time is None or previous_pairs is None:
            delta_elapsed = 0.0
            delta_pairs = 0
        else:
            delta_elapsed = now - previous_time
            delta_pairs = result.complete_pairs - previous_pairs

        print(
            _line(
                folder=folder,
                complete_pairs=result.complete_pairs,
                save_files=result.save_files,
                state_files=result.state_files,
                target_pairs=target_pairs,
                elapsed=now - started,
                delta_pairs=delta_pairs,
                delta_elapsed=delta_elapsed,
                bad_names=result.bad_names,
                bad_target_naming=result.bad_target_naming,
                bad_sizes=result.bad_sizes,
                unsettled_files=result.unsettled_files,
                organic_lanes_missing=result.organic_lanes_missing,
                endpoint_exceptions_missing=result.endpoint_exceptions_missing,
            ),
            flush=True,
        )

        printed += 1
        previous_time = now
        previous_pairs = result.complete_pairs
        if result.complete_pairs >= target_pairs:
            return 0
        if max_samples and printed >= max_samples:
            return 0
        time.sleep(max(0.1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
