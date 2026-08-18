r"""Hit FR/LG egg personality first halves from the current CSV lane.

This is the operator wrapper around
`frlg_spinda_first_half_batch.py`. It defaults to the current verified
`0xFBC7` post-seed anchor in `<repo-root>\live-lanes\live-fbc7-lane16`, streams
`firsthalf.csv`, preserves all raw CSV first-half targets by default, then
delegates the actual route work to the batch primitives that already handle:

- keeping the anchor's organic `gRngValue`
- replaying the seed-to-step and hit/walk tapes
- checking `t-18`
- learning loaded-state frame offset when drift appears
- capturing a pre-hit checkpoint and trying nearby hit delays
- resolving bounded PRNG drift around CSV `t-0`

Default full-corpus output names main `saves` / `states` files under
`<repo-root>\1sthalves` by live FR/LG daycare half. The two raw CSV wrap
collisions are kept in `_live_name_collisions` with `__raw0x####` suffixes.

The script creates a read-only clean backup of `1 from egg.ss0` before doing
anything else. It never patches RAM to force a seed or RNG state.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import stat
import sys
import traceback
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Sequence


SCRIPT_DIR = Path(__file__).parent
MGBA_ROOT = SCRIPT_DIR.parents[2]
SEED_BRUTE_FORCE_DIR = SCRIPT_DIR.parent / "frlg-seed-bruteforce"
LIVE_LANES_DIR = MGBA_ROOT / "live-lanes"
CURRENT_ANCHOR_DIR = LIVE_LANES_DIR / "live-fbc7-lane16"

DEFAULT_FIRST_HALF_CSV = MGBA_ROOT / "build-mingw64-python-qt" / "firsthalf.csv"
DEFAULT_ROM = SEED_BRUTE_FORCE_DIR / "lg.gba"
DEFAULT_BASE_SAVE = CURRENT_ANCHOR_DIR / "1 from egg.sav"
DEFAULT_FIRST_HALF_STATE = CURRENT_ANCHOR_DIR / "1 from egg.ss0"
DEFAULT_CLEAN_BACKUP_STATE = CURRENT_ANCHOR_DIR / "1 from egg - clean-backup.ss0"
DEFAULT_FIRST_HALF_METADATA = CURRENT_ANCHOR_DIR / "1 from egg - replay-metadata.json"
DEFAULT_SETUP_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "tape seed to step 1.json"
DEFAULT_HIT_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "hit 1st half walk to daycare man.json"
DEFAULT_OUTPUT_DIR = MGBA_ROOT / "1sthalves"
DEFAULT_STATUS_PATH = DEFAULT_OUTPUT_DIR / "_egg_first_half_hitter_status.json"
DEFAULT_FIRSTHALF_SCRIPT = SEED_BRUTE_FORCE_DIR / "Seed-Bruteforcer.py"
DEFAULT_ORGANIC_CSV_DIR = MGBA_ROOT / "build-mingw64-python-qt"
DEFAULT_ORGANIC_FRAME_BUFFER = 20
DEFAULT_ORGANIC_SEED_DELAY_FRAMES = 500
EGG_HALF_COUNT = 0x10000

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import frlg_spinda_first_half_batch as batch


@dataclass(frozen=True)
class BackupResult:
    """Result of protecting the current post-seed anchor savestate."""

    source_path: Path
    backup_path: Path
    created: bool
    source_sha1: str
    backup_sha1: str


@dataclass(frozen=True)
class HitterConfig:
    """Hitter settings before conversion to `BatchConfig`."""

    target_half: int | None
    csv_path: Path
    rom_path: Path
    base_save_path: Path
    first_half_state_path: Path
    clean_backup_state_path: Path
    first_half_metadata_path: Path
    setup_tape_path: Path
    hit_tape_path: Path
    output_dir: Path
    status_path: Path
    firsthalf_script_path: Path
    state_initial_seed: int | None
    compatibility_percent: int
    hit_tape_target_delay: int
    t_minus_recovery_window: int
    hit_rng_drift_window: int
    hit_delay_search_radius: int
    limit: int | None
    auto_organic_csv: bool
    organic_csv_path: Path | None
    organic_seed_delay_frames: int | None
    organic_frame_buffer: int
    preserve_raw_csv_targets: bool
    progress_every: int
    status_every: int
    overwrite: bool
    dry_run: bool


def _absolute(path: Path) -> Path:
    """Return an absolute path without resolving workspace junctions."""

    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return (Path.cwd() / expanded).absolute()


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser for first-half hits."""

    parser = argparse.ArgumentParser(
        description=(
            "Hit FR/LG egg personality first halves from firsthalf.csv using "
            "the current 0xFBC7 post-seed anchor."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--target-half",
        type=batch.parse_u16,
        default=None,
        help=(
            "Live daycare lower half to hit. If omitted, all raw CSV first-half "
            "targets for the current lane are processed."
        ),
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_FIRST_HALF_CSV)
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM)
    parser.add_argument("--base-save", type=Path, default=DEFAULT_BASE_SAVE)
    parser.add_argument("--first-half-state", type=Path, default=DEFAULT_FIRST_HALF_STATE)
    parser.add_argument("--clean-backup-state", type=Path, default=DEFAULT_CLEAN_BACKUP_STATE)
    parser.add_argument("--first-half-metadata", type=Path, default=DEFAULT_FIRST_HALF_METADATA)
    parser.add_argument("--setup-tape", type=Path, default=DEFAULT_SETUP_TAPE)
    parser.add_argument("--hit-tape", type=Path, default=DEFAULT_HIT_TAPE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--status-path", type=Path, default=DEFAULT_STATUS_PATH)
    parser.add_argument("--firsthalf-script", type=Path, default=DEFAULT_FIRSTHALF_SCRIPT)
    parser.add_argument(
        "--state-initial-seed",
        type=batch.parse_u16,
        default=None,
        help="Explicit initial seed for the loaded anchor. Overrides metadata.",
    )
    parser.add_argument("--compatibility-percent", type=int, default=batch.DEFAULT_COMPATIBILITY_PERCENT)
    parser.add_argument("--hit-tape-target-delay", type=int, default=batch.DEFAULT_HIT_TAPE_TARGET_DELAY)
    parser.add_argument("--t-minus-recovery-window", type=int, default=batch.DEFAULT_T_MINUS_RECOVERY_WINDOW)
    parser.add_argument("--hit-rng-drift-window", type=int, default=batch.DEFAULT_HIT_RNG_DRIFT_WINDOW)
    parser.add_argument("--hit-delay-search-radius", type=int, default=batch.DEFAULT_HIT_DELAY_SEARCH_RADIUS)
    parser.add_argument("--limit", type=int, default=None, help="Optional target count limit for shakedown runs.")
    parser.add_argument(
        "--no-auto-organic-csv",
        action="store_true",
        help=(
            "Do not auto-generate a PRNG-origin CSV from replay metadata. "
            "By default, the wrapper uses prng_discerned_seed when present so "
            "the route checkpoints match the organic post-seed gRngValue."
        ),
    )
    parser.add_argument(
        "--organic-csv-path",
        type=Path,
        default=None,
        help="Optional output path for the auto-generated organic PRNG route CSV.",
    )
    parser.add_argument(
        "--organic-seed-delay-frames",
        type=int,
        default=None,
        help="Seed-delay frames for auto-generated organic CSVs. Defaults to the copied CSV's inferred delay.",
    )
    parser.add_argument(
        "--organic-frame-buffer",
        type=int,
        default=DEFAULT_ORGANIC_FRAME_BUFFER,
        help="Symmetric t-N/t--N row window to write in auto-generated organic CSVs.",
    )
    parser.add_argument(
        "--status-every",
        type=int,
        default=batch.DEFAULT_PROGRESS_EVERY,
        help="How often to refresh the resumability status file during real runs.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=batch.DEFAULT_PROGRESS_EVERY,
        help="How often to print progress during real runs.",
    )
    parser.add_argument(
        "--live-output",
        action="store_true",
        help=(
            "Collapse raw CSV collisions and write direct live-half outputs. "
            "Without this flag, the default full sweep preserves all raw CSV targets."
        ),
    )
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse CLI args, using defaults when loaded from Qt scripting."""

    env_argv = os.environ.get("MGBA_EGG_FIRST_HALF_HITTER_ARGS")
    if argv is None and env_argv:
        # Qt smoke runs commonly pass Windows paths through this environment
        # hook. POSIX shlex would treat backslashes as escapes and corrupt
        # paths like `<repo-root>\...`, so use Windows splitting on Windows.
        argv = shlex.split(env_argv, posix=(os.name != "nt"))
    if argv is None and batch.qt_mode_enabled():
        argv = []
    return build_parser().parse_args(argv)


def normalize_config(args: argparse.Namespace) -> HitterConfig:
    """Resolve and validate one-target hitter settings."""

    if args.compatibility_percent < 0:
        raise SystemExit("--compatibility-percent must be non-negative.")
    if args.hit_tape_target_delay < 0:
        raise SystemExit("--hit-tape-target-delay must be non-negative.")
    if args.t_minus_recovery_window < 0:
        raise SystemExit("--t-minus-recovery-window must be non-negative.")
    if args.hit_rng_drift_window < 0:
        raise SystemExit("--hit-rng-drift-window must be non-negative.")
    if args.hit_delay_search_radius < 0:
        raise SystemExit("--hit-delay-search-radius must be non-negative.")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive when provided.")
    if args.organic_seed_delay_frames is not None and args.organic_seed_delay_frames < 0:
        raise SystemExit("--organic-seed-delay-frames must be non-negative.")
    if args.organic_frame_buffer < args.hit_tape_target_delay:
        raise SystemExit("--organic-frame-buffer must cover at least --hit-tape-target-delay.")
    if args.status_every <= 0:
        raise SystemExit("--status-every must be positive.")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive.")

    return HitterConfig(
        target_half=args.target_half,
        csv_path=_absolute(args.csv),
        rom_path=_absolute(args.rom),
        base_save_path=_absolute(args.base_save),
        first_half_state_path=_absolute(args.first_half_state),
        clean_backup_state_path=_absolute(args.clean_backup_state),
        first_half_metadata_path=_absolute(args.first_half_metadata),
        setup_tape_path=_absolute(args.setup_tape),
        hit_tape_path=_absolute(args.hit_tape),
        output_dir=_absolute(args.output_dir),
        status_path=_absolute(args.status_path),
        firsthalf_script_path=_absolute(args.firsthalf_script),
        state_initial_seed=args.state_initial_seed,
        compatibility_percent=int(args.compatibility_percent),
        hit_tape_target_delay=int(args.hit_tape_target_delay),
        t_minus_recovery_window=int(args.t_minus_recovery_window),
        hit_rng_drift_window=int(args.hit_rng_drift_window),
        hit_delay_search_radius=int(args.hit_delay_search_radius),
        limit=args.limit,
        auto_organic_csv=not bool(args.no_auto_organic_csv),
        organic_csv_path=_absolute(args.organic_csv_path) if args.organic_csv_path is not None else None,
        organic_seed_delay_frames=(
            int(args.organic_seed_delay_frames)
            if args.organic_seed_delay_frames is not None
            else None
        ),
        organic_frame_buffer=int(args.organic_frame_buffer),
        preserve_raw_csv_targets=args.target_half is None and not bool(args.live_output),
        progress_every=int(args.progress_every),
        status_every=int(args.status_every),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
    )


def ensure_clean_state_backup(source_path: Path, backup_path: Path) -> BackupResult:
    """Create a read-only clean backup of the post-seed anchor if missing.

    Existing backups are never overwritten. If the active anchor is changed by a
    future run or operator action, the first backup remains preserved.
    """

    if not source_path.is_file():
        raise SystemExit(f"Post-seed anchor state not found: {source_path}")
    backup_path.parent.mkdir(parents=True, exist_ok=True)
    created = False
    if not backup_path.exists():
        shutil.copy2(source_path, backup_path)
        created = True
    backup_path.chmod(backup_path.stat().st_mode & ~stat.S_IWRITE)
    return BackupResult(
        source_path=source_path,
        backup_path=backup_path,
        created=created,
        source_sha1=batch.sha1_file(source_path),
        backup_sha1=batch.sha1_file(backup_path),
    )


def parse_metadata_int(value: object) -> int:
    """Parse metadata integers that may be JSON numbers or `0x` strings."""

    if isinstance(value, bool):
        raise ValueError("boolean is not a valid integer metadata value")
    return int(value, 0) if isinstance(value, str) else int(value)


def read_replay_metadata(path: Path) -> dict[str, object]:
    """Load replay metadata, returning an empty dict when it is unavailable."""

    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    return payload if isinstance(payload, dict) else {}


def metadata_u16(metadata: dict[str, object], key: str) -> int | None:
    """Read an optional 16-bit integer metadata field."""

    if key not in metadata or metadata[key] is None:
        return None
    value = parse_metadata_int(metadata[key])
    if not 0 <= value <= 0xFFFF:
        raise ValueError(f"metadata field {key!r} must fit in 16 bits")
    return value


def infer_seed_delay_from_csv(csv_path: Path, compatibility_percent: int) -> int | None:
    """Infer `seed_delay` from the first matching `t-0` CSV row."""

    if not csv_path.is_file():
        return None
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = batch.csv.DictReader(handle)
        for row in reader:
            if str(row.get("t_minus", "")).strip().lower() != "t-0":
                continue
            if int(row.get("compatibility_percent", "0"), 0) != compatibility_percent:
                continue
            frame = int(row["frame_from_initial_seed"], 0)
            sweep = int(row["sweep_index"], 0)
            return frame - sweep - 1
    return None


def organic_csv_path_for_seed(config: HitterConfig, route_seed: int) -> Path:
    """Return the route-compatible CSV path for one PRNG-origin seed."""

    if config.organic_csv_path is not None:
        return config.organic_csv_path
    return DEFAULT_ORGANIC_CSV_DIR / f"firsthalf-prng-{route_seed & 0xFFFF:04X}.csv"


def egg_roll_threshold_from_compatibility(compatibility_percent: int) -> int:
    """Match the CUDA route tool's FR/LG compatibility threshold."""

    return (int(compatibility_percent) * 0xFFFF + 99) // 100


def organic_csv_matches(path: Path, *, route_seed: int, compatibility_percent: int, seed_delay_frames: int) -> bool:
    """Return whether an existing generated CSV matches the required route."""

    inferred_delay = infer_seed_delay_from_csv(path, compatibility_percent)
    if inferred_delay != seed_delay_frames:
        return False
    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = batch.csv.DictReader(handle)
        for row in reader:
            return (
                int(row["initial_seed_16bit"], 0) == (route_seed & 0xFFFF)
                and int(row["compatibility_percent"], 0) == compatibility_percent
            )
    return False


def generate_organic_firsthalf_csv(
    path: Path,
    *,
    route_seed: int,
    compatibility_percent: int,
    seed_delay_frames: int,
    frame_buffer: int,
) -> None:
    """Generate a first-half route CSV on the organic PRNG-origin seed.

    This mirrors the producer-side single-seed route model. It changes only the
    CSV/checkpoint plan; the emulator RAM still advances naturally from the
    loaded savestate.
    """

    route_seed &= 0xFFFF
    threshold = egg_roll_threshold_from_compatibility(compatibility_percent)
    route_state = batch.lcrng_advance(route_seed, seed_delay_frames)
    seen: dict[int, tuple[int, int, int]] = {}
    attempt = 0
    while len(seen) < EGG_HALF_COUNT:
        roll_state = batch.lcrng_next_state(route_state)
        route_state = roll_state
        if (roll_state >> 16) < threshold:
            first_half_state = batch.lcrng_next_state(roll_state)
            first_half = (first_half_state >> 16) & 0xFFFF
            if first_half not in seen:
                seen[first_half] = (attempt, seed_delay_frames + attempt + 1, roll_state)
        attempt += 1

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    with temp_path.open("w", newline="", encoding="utf-8") as handle:
        writer = batch.csv.writer(handle)
        writer.writerow(
            [
                "initial_seed_16bit",
                "compatibility_percent",
                "target_half_16bit",
                "sweep_index",
                "frame_from_initial_seed",
                "t_minus",
                "rng_seed",
            ]
        )
        for half in range(EGG_HALF_COUNT):
            sweep_index, t_zero_frame, t_zero_state = seen[half]
            state = batch.lcrng_advance(t_zero_state, -frame_buffer)
            for relative_offset in range(-frame_buffer, frame_buffer + 1):
                if relative_offset < 0:
                    label = f"t-{-relative_offset}"
                elif relative_offset == 0:
                    label = "t-0"
                else:
                    label = f"t--{relative_offset}"
                writer.writerow(
                    [
                        f"0x{route_seed:04X}",
                        compatibility_percent,
                        f"0x{half:04X}",
                        sweep_index,
                        t_zero_frame + relative_offset,
                        label,
                        f"0x{state:08X}",
                    ]
                )
                if relative_offset != frame_buffer:
                    state = batch.lcrng_next_state(state)
    temp_path.replace(path)


def prepare_organic_csv_config(config: HitterConfig) -> HitterConfig:
    """Switch default runs to a CSV that matches the organic post-seed PRNG."""

    if not config.auto_organic_csv or config.state_initial_seed is not None:
        return config
    if config.csv_path != DEFAULT_FIRST_HALF_CSV:
        return config

    metadata = read_replay_metadata(config.first_half_metadata_path)
    timer1_seed = metadata_u16(metadata, "target_seed")
    prng_seed = metadata_u16(metadata, "prng_discerned_seed")
    if prng_seed is None or prng_seed == timer1_seed:
        return config

    seed_delay = (
        config.organic_seed_delay_frames
        if config.organic_seed_delay_frames is not None
        else infer_seed_delay_from_csv(config.csv_path, config.compatibility_percent)
    )
    if seed_delay is None:
        seed_delay = DEFAULT_ORGANIC_SEED_DELAY_FRAMES
    organic_csv_path = organic_csv_path_for_seed(config, prng_seed)
    if not organic_csv_matches(
        organic_csv_path,
        route_seed=prng_seed,
        compatibility_percent=config.compatibility_percent,
        seed_delay_frames=seed_delay,
    ):
        print(
            "Generating organic first-half CSV:"
            f" path={organic_csv_path}"
            f" timer1_seed={batch.format_u16(timer1_seed) if timer1_seed is not None else 'unknown'}"
            f" route_prng_seed={batch.format_u16(prng_seed)}"
            f" seed_delay_frames={seed_delay}"
        )
        generate_organic_firsthalf_csv(
            organic_csv_path,
            route_seed=prng_seed,
            compatibility_percent=config.compatibility_percent,
            seed_delay_frames=seed_delay,
            frame_buffer=config.organic_frame_buffer,
        )

    return replace(
        config,
        csv_path=organic_csv_path,
        state_initial_seed=prng_seed,
        organic_csv_path=organic_csv_path,
        organic_seed_delay_frames=seed_delay,
    )


def build_batch_config(config: HitterConfig) -> batch.BatchConfig:
    """Translate hitter settings into the shared batch config."""

    if config.target_half is None:
        target_start = 0x0000
        target_end = 0xFFFF
    else:
        target_start = config.target_half
        target_end = config.target_half

    return batch.BatchConfig(
        csv_path=config.csv_path,
        rom_path=config.rom_path,
        base_save_path=config.base_save_path,
        seed_mode="loaded-state",
        first_half_state_path=config.first_half_state_path,
        first_half_metadata_path=config.first_half_metadata_path,
        state_initial_seed=config.state_initial_seed,
        setup_tape_path=config.setup_tape_path,
        hit_tape_path=config.hit_tape_path,
        output_dir=config.output_dir,
        firsthalf_script_path=config.firsthalf_script_path,
        target_start=target_start,
        target_end=target_end,
        compatibility_percent=config.compatibility_percent,
        hit_tape_target_delay=config.hit_tape_target_delay,
        limit=config.limit,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
        overwrite=config.overwrite,
        dry_run=config.dry_run,
        order="target",
        progress_every=config.progress_every,
        status_every=config.status_every,
        t_minus_recovery_window=config.t_minus_recovery_window,
        hit_rng_drift_window=config.hit_rng_drift_window,
        hit_delay_search_radius=config.hit_delay_search_radius,
        max_delay=batch.DEFAULT_MAX_DELAY,
        seed_timeout=batch.DEFAULT_SEED_TIMEOUT,
    )


def write_hitter_status(
    config: HitterConfig,
    backup: BackupResult,
    results: Sequence[batch.TargetResult],
    *,
    run_status: str,
) -> None:
    """Write a small status file for operator resume/audit."""

    payload = {
        "script": "Egg-First-Half-Hitter.py",
        "run_status": run_status,
        "dry_run": config.dry_run,
        "target_half": batch.format_u16(config.target_half) if config.target_half is not None else None,
        "target_scope": "single-live-half" if config.target_half is not None else "all-raw-csv-first-halves",
        "limit": config.limit,
        "output_key_mode": batch.output_key_mode_name(config.preserve_raw_csv_targets),
        "csv_path": str(config.csv_path),
        "state_initial_seed": (
            batch.format_u16(config.state_initial_seed)
            if config.state_initial_seed is not None
            else None
        ),
        "auto_organic_csv": config.auto_organic_csv,
        "organic_csv_path": str(config.organic_csv_path) if config.organic_csv_path is not None else None,
        "organic_seed_delay_frames": config.organic_seed_delay_frames,
        "anchor_state": str(config.first_half_state_path),
        "clean_backup_state": str(config.clean_backup_state_path),
        "clean_backup_created": backup.created,
        "anchor_state_sha1": backup.source_sha1,
        "clean_backup_sha1": backup.backup_sha1,
        "base_save": str(config.base_save_path),
        "metadata": str(config.first_half_metadata_path),
        "output_dir": str(config.output_dir),
        "results": [
            {
                "spinda_half_live": batch.format_u16(result.lower_half),
                "csv_target_half_raw": (
                    batch.format_u16(result.csv_target_half_raw)
                    if result.csv_target_half_raw is not None
                    else None
                ),
                "status": result.status,
                "save_path": str(result.save_path),
                "pre_daycare_man_state_path": (
                    str(result.pre_daycare_man_state_path)
                    if result.pre_daycare_man_state_path is not None
                    else None
                ),
                "manifest_path": str(result.manifest_path),
            }
            for result in results
        ],
    }
    config.status_path.parent.mkdir(parents=True, exist_ok=True)
    batch.write_json_atomic(config.status_path, payload)


def hitter_error_status_path(config: HitterConfig) -> Path:
    """Return the per-run Qt traceback status path."""

    return config.status_path.with_name("_egg_first_half_hitter_error.json")


def clear_hitter_error_status(config: HitterConfig) -> None:
    """Remove a stale error marker before starting a new run."""

    error_path = hitter_error_status_path(config)
    try:
        error_path.unlink()
    except FileNotFoundError:
        return


def write_hitter_error_status(config: HitterConfig, exc: BaseException) -> None:
    """Persist the real exception from Qt runs before mGBA shows its dialog."""

    error_path = hitter_error_status_path(config)
    payload = {
        "script": "Egg-First-Half-Hitter.py",
        "status": "error",
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        "status_path": str(config.status_path),
        "output_dir": str(config.output_dir),
        "target_half": batch.format_u16(config.target_half) if config.target_half is not None else None,
        "target_scope": "single-live-half" if config.target_half is not None else "all-raw-csv-first-halves",
        "limit": config.limit,
        "csv_path": str(config.csv_path),
        "state_initial_seed": (
            batch.format_u16(config.state_initial_seed)
            if config.state_initial_seed is not None
            else None
        ),
    }
    error_path.parent.mkdir(parents=True, exist_ok=True)
    batch.write_json_atomic(error_path, payload)


def run_hitter(config: HitterConfig) -> list[batch.TargetResult]:
    """Protect the anchor, then run loaded-state first-half hits."""

    backup = ensure_clean_state_backup(
        config.first_half_state_path,
        config.clean_backup_state_path,
    )
    print(
        "Clean post-seed anchor backup:"
        f" path={backup.backup_path}"
        f" created={backup.created}"
        f" source_sha1={backup.source_sha1}"
        f" backup_sha1={backup.backup_sha1}"
    )
    if backup.source_sha1 != backup.backup_sha1:
        print(
            "Warning: active anchor differs from preserved clean backup;"
            " keeping backup untouched."
        )

    batch_config = build_batch_config(config)
    clear_hitter_error_status(config)
    write_hitter_status(config, backup, [], run_status="running")
    results = batch.run_batch(batch_config)
    write_hitter_status(config, backup, results, run_status="finished")
    print(f"Egg first-half hitter status: {config.status_path}")
    return list(results)


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint."""

    config = normalize_config(parse_args(argv))
    try:
        config = prepare_organic_csv_config(config)
        run_hitter(config)
    except BaseException as exc:
        write_hitter_error_status(config, exc)
        raise
    return 0


if __name__ == "__main__":
    exit_code = main()
    if exit_code:
        raise SystemExit(exit_code)
