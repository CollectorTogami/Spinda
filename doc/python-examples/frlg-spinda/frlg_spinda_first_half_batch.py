r"""Generate all first-half FR/LG Spinda lane saves.

This is the batch version of the proven first-half workflow:

1. read `firsthalf.csv` for the best initial seed and `t-18` row per raw egg-half roll
2. load the current post-seed savestate for the active initial-seed lane
3. keep that anchor's organic FR/LG `gRngValue`
4. replay `tape seed to step 1.json`
5. in loaded-state mode, keep one checkpointed post-setup runway and process
   targets in CSV `t-18` order instead of replaying setup from scratch
6. capture a preventative `t-18` checkpoint before each target branch
7. replay the first 18 frames of `hit 1st half walk to daycare man.json`
8. capture a pre-hit checkpoint and try nearby hit delays such as 17/18/19
9. resolve any bounded post-hit PRNG drift from the CSV `t-0` state
10. verify the converted live daycare lower-half RAM value
11. save a matching pre-daycare-man `.ss0` state
12. replay the remainder of the tape, which walks to the daycare man and saves
13. export the live save as `<repo-root>\1sthalves\0x####.sav`, where `####`
    is the actual daycare lower half stored by FR/LG

With `--preserve-raw-csv-targets`, the script still processes every raw CSV
row, but it now writes main files under `1sthalves\saves` and
`1sthalves\states` using the live FR/LG lower-half filename. The two
wraparound raw-half duplicates are kept under `1sthalves\_live_name_collisions`
with `__raw0x####`
suffixes so no target is lost and filenames match save contents.

Restart safety is artifact-driven. On startup the script scans the requested
targets before emulator work and skips only targets whose `.sav`,
pre-daycare-man `.ss0`, and manifest still agree by path, target identity, and
SHA-1. A crash-cut target is retried on the next run instead of requiring a
manual loop counter.

The script is meant to run inside the custom visible Qt build, but it keeps a
host-side path for tests and dry runs. Opening the script from the Qt UI now
defaults to the exact-seed loaded-state lane, using the premade post-seed
route anchor `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0` together with the replay
metadata exported earlier by `Seed-Bruteforcer.py`. In that default mode the script
does not brute-force initial seeds; it only targets the Spinda first-half
values that belong to that saved initial-seed lane. The wider full-CSV
initial-seed brute-force path remains available as explicit
`--seed-mode csv-bruteforce`.

When loaded-state mode is used, the script treats the premade post-seed route
anchor as both the position anchor and the organic PRNG starting point. The
locked baseline metadata is still used to identify which CSV initial-seed lane
the anchor belongs to, but the script no longer rewrites `gRngValue` back to
that seed. Instead, the loaded-state route model learns the anchor's frame
offset from the first `t-18` PRNG mismatch and reuses that calibration for the
rest of the lane, instead of relying on repeated broad recovery scans or RAM
patches. The script also handles small post-hit drift because the CSV `t-0`
row records the compatibility-roll state, while the live frame can consume the
target `Random()` call and later noisy calls before Python observes memory
again.

The terminology and lane assumptions in this file are part of the documented
project contract in:

- `WORKFLOW_DECISION_LOG.md`
- `INITIAL_SEED_CSV_REFERENCE.md`
- `doc/python-examples/frlg-spinda/SCRIPT_DOCUMENTATION.md`

For nearby hit-delay retries, the script prefers host-side raw savestates, then
visible-Qt scratch savestates, and finally one file-backed scratch savestate if
neither in-memory path is available.

Important FR/LG note:

- the CSV `target_half_16bit` is the raw 16-bit `Random()` result used for the
  lower PID half
- the live daycare value is `((Random() % 0xFFFE) + 1)`, so the stored lower
  half is usually one greater than the raw CSV half
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import sys
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Mapping, Sequence


SCRIPT_DIR = Path(__file__).parent
EXAMPLES_DIR = SCRIPT_DIR.parent
MGBA_ROOT = SCRIPT_DIR.parents[2]
SEED_BRUTE_FORCE_DIR = EXAMPLES_DIR / "frlg-seed-bruteforce"
DEFAULT_FIRSTHALF_SCRIPT = SEED_BRUTE_FORCE_DIR / "Seed-Bruteforcer.py"
DEFAULT_ROM = SEED_BRUTE_FORCE_DIR / "lg.gba"
LIVE_LANES_DIR = MGBA_ROOT / "live-lanes"
DEFAULT_FIRST_HALF_LANE_DIR = LIVE_LANES_DIR / "live-fbc7-lane16"
DEFAULT_BASE_SAVE = DEFAULT_FIRST_HALF_LANE_DIR / "1 from egg.sav"
DEFAULT_FIRST_HALF_STATE = DEFAULT_FIRST_HALF_LANE_DIR / "1 from egg.ss0"
DEFAULT_FIRST_HALF_METADATA = DEFAULT_FIRST_HALF_LANE_DIR / "1 from egg - replay-metadata.json"
DEFAULT_FIRST_HALF_CSV = MGBA_ROOT / "build-mingw64-python-qt" / "firsthalf.csv"
DEFAULT_SETUP_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "tape seed to step 1.json"
DEFAULT_HIT_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "hit 1st half walk to daycare man.json"
DEFAULT_OUTPUT_DIR = MGBA_ROOT / "1sthalves"
DEFAULT_SEED_MODE = "loaded-state"
DEFAULT_COMPATIBILITY_PERCENT = 70
DEFAULT_HIT_TAPE_TARGET_DELAY = 18
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_T_MINUS_RECOVERY_WINDOW = 240
DEFAULT_HIT_RNG_DRIFT_WINDOW = 64
DEFAULT_HIT_DELAY_SEARCH_RADIUS = 2
DEFAULT_MAX_DELAY = 500_000_000
# Match the shared first-half helper's very large title-input observation
# window so full CSV-driven runs do not abort merely because one long-delay
# branch exposes Timer 1 later than a normal title-screen search.
DEFAULT_SEED_TIMEOUT = 500_000
DEFAULT_INITIAL_SEED_AUTOTUNE_MAX_JUMP = 4096
DEFAULT_INITIAL_SEED_LCRNG_HINT_WINDOW = 32
RAW_CSV_OUTPUT_DIR_NAME = ""
RAW_CSV_COLLISION_DIR_NAME = "_live_name_collisions"
RAW_CSV_LIVE_OUTPUT_KEY_MODE = "raw-csv-live-name"

if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))

import input_tape
from spinda_frlg_common import (
    FirstHalfCsvRow,
    daycare_lower_half_from_random_half,
    export_save_file,
    format_u16,
    format_u32,
    lcrng_advance,
    lcrng_next_state,
    lcrng_previous_state,
    read_daycare_lower_half,
    read_rng_state,
    sha1_file,
    write_json_atomic,
)


@dataclass(frozen=True)
class FirstHalfTarget:
    """One lower-half target derived from `firsthalf.csv`.

    `lower_half` is the raw 16-bit `Random()` result stored in the CSV.
    `live_lower_half` is the actual pending daycare lower half after FR/LG
    applies `((Random() % 0xFFFE) + 1)`.
    `initial_seed` is the title-screen initial seed that produces this Spinda
    first-half route segment; it is not the first half itself.
    """

    lower_half: int
    live_lower_half: int
    initial_seed: int
    compatibility_percent: int
    sweep_index: int
    t_minus_frame: int
    t_minus_rng: int
    t_zero_frame: int
    t_zero_rng: int


@dataclass(frozen=True)
class TMinusReachResult:
    """How the script reached the CSV `t-18` state for one target."""

    post_setup_wait_frames: int
    recovered: bool
    observed_before_recovery: int | None = None
    anchor_frame_offset: int | None = None


@dataclass(frozen=True)
class HitDriftResult:
    """Observed PRNG drift after replaying the lower-half hit prefix.

    The CUDA history CSV records `t-0` as the compatibility-roll state. In the
    live game, the same rendered input frame can also consume the target
    `Random()` call and later noisy calls before Python regains control. This
    structure records the signed LCRNG distance between the CSV `t-0` state and
    the state we can actually observe after replay.
    """

    observed_rng: int
    signed_drift_from_t_zero: int
    target_call_offset: int | None
    target_call_rng: int | None


@dataclass(frozen=True)
class HitDelayResult:
    """Resolved live hit-tape timing for one target from a pre-hit checkpoint."""

    chosen_prefix_frames: int
    hit_drift: HitDriftResult
    observed_daycare_lower_half: int


@dataclass(frozen=True)
class HitDelayVariant:
    """One pre-split hit-tape retry candidate.

    Every target in a run uses the same hit tape and the same nearby delay
    window. Pre-splitting those candidate prefix/suffix pairs once avoids
    rebuilding identical `17/18/19`-style tape fragments for every target.
    """

    prefix_frames: int
    prefix_tape: input_tape.InputTape
    suffix_tape: input_tape.InputTape


@dataclass(frozen=True)
class RuntimeCheckpoint:
    """One captured pre-hit checkpoint for nearby delay retries.

    The batch prefers the fastest checkpoint type that the current core
    exposes:

    - `raw`: host-side `save_raw_state()` / `load_raw_state()`
    - `scratch`: visible Qt `save_scratch_state()` / `load_scratch_state()`
    - `file`: a file-backed scratch savestate through the shared helper
    """

    mode: str
    state: object | None = None
    path: Path | None = None


@dataclass(frozen=True)
class SeedAnchor:
    """Savestate and metadata for one post-initial-seed starting point.

    The batch can either create this anchor by brute-forcing the CSV row's
    initial seed, or load an explicitly requested existing savestate. The rest
    of the first-half lane process is intentionally identical for both modes.
    """

    mode: str
    initial_seed: int
    state_path: Path
    delay_frames: int | None = None
    button_name: str | None = None
    seed_frame: int | None = None
    rng_at_seed: int | None = None


@dataclass(frozen=True)
class InitialSeedObservation:
    """One exact observed title-screen seed result for auto-adjustment.

    Unlike later daycare-route work, the initial seed comes directly from
    Timer 1. That means the best correction signal is the exact observed seed
    and frame from a real title input branch, not a guessed delay offset.
    """

    delay_frames: int
    button_name: str
    seed_value: int
    seed_frame: int
    rng_at_seed: int


@dataclass(frozen=True)
class BatchPaths:
    """Derived paths for the batch run.

    The user-facing save files intentionally live directly under `1sthalves`.
    Internal manifests are kept in an underscore-prefixed subdirectory so the
    save folder remains easy to scan.
    """

    output_dir: Path
    manifest_dir: Path
    state_dir: Path
    baseline_checkpoint_path: Path
    rolling_checkpoint_path: Path
    candidate_checkpoint_path: Path
    hit_delay_checkpoint_path: Path
    post_seed_state_path: Path
    post_setup_checkpoint_path: Path
    status_path: Path
    resume_status_path: Path


@dataclass(frozen=True)
class BatchConfig:
    """Runtime settings for a first-half batch generation pass."""

    csv_path: Path
    rom_path: Path
    base_save_path: Path
    seed_mode: str
    first_half_state_path: Path
    first_half_metadata_path: Path | None
    state_initial_seed: int | None
    setup_tape_path: Path
    hit_tape_path: Path
    output_dir: Path
    firsthalf_script_path: Path
    target_start: int
    target_end: int
    compatibility_percent: int
    hit_tape_target_delay: int
    limit: int | None
    preserve_raw_csv_targets: bool
    overwrite: bool
    dry_run: bool
    order: str
    progress_every: int
    status_every: int
    t_minus_recovery_window: int
    hit_rng_drift_window: int
    hit_delay_search_radius: int
    max_delay: int
    seed_timeout: int


@dataclass(frozen=True)
class BatchInputHashes:
    """Hashes for input files that do not change during one batch run.

    These were originally computed while writing every target manifest. That
    was accurate but wasteful at 65,536 targets, especially for the base save.
    Cache them once per run and keep per-target hashing limited to the newly
    exported save file.
    """

    setup_tape_sha1: str
    hit_tape_sha1: str
    base_save_sha1: str


@dataclass(frozen=True)
class TargetResult:
    """Result for one exported or skipped live-lower-half target."""

    lower_half: int
    status: str
    save_path: Path
    manifest_path: Path
    csv_target_half_raw: int | None = None
    spinda_half_live: int | None = None
    pre_daycare_man_state_path: Path | None = None


@dataclass(frozen=True)
class TargetArtifactStatus:
    """Resume classification for one target's on-disk output triad.

    A target is complete only when the save, the pre-daycare-man savestate, and
    the manifest all agree. This prevents a crash after `.ss0` or `.sav` export
    from making the next run skip a half-finished lane.
    """

    complete: bool
    reason: str
    save_path: Path
    manifest_path: Path
    pre_daycare_man_state_path: Path


def parse_u16(text: str) -> int:
    """Parse one decimal or hex 16-bit value for CLI arguments."""

    value = int(text, 0)
    if not 0 <= value <= 0xFFFF:
        raise argparse.ArgumentTypeError("value must fit in 16 bits")
    return value


def absolute_path(path: Path) -> Path:
    r"""Make a path absolute without following the `<repo-root>` junction.

    `Path.resolve()` follows the local `<repo-root>` junction back into the
    larger workspace. For operator-facing output we want the stable path the
    user actually works with, especially `<repo-root>\1sthalves`.
    """

    expanded = path.expanduser()
    if expanded.is_absolute():
        return expanded
    return (Path.cwd() / expanded).absolute()


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI for the first-half batch generator."""

    parser = argparse.ArgumentParser(
        description="Generate FR/LG Spinda first-half save files into 1sthalves.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--csv", type=Path, default=DEFAULT_FIRST_HALF_CSV, help="Path to firsthalf.csv.")
    parser.add_argument("--rom", type=Path, default=DEFAULT_ROM, help="Path to the LeafGreen ROM.")
    parser.add_argument("--base-save", type=Path, default=DEFAULT_BASE_SAVE, help="Canonical base .sav.")
    parser.add_argument(
        "--seed-mode",
        choices=("csv-bruteforce", "loaded-state"),
        default=DEFAULT_SEED_MODE,
        help=(
            "loaded-state is the default exact-seed path: it reuses the"
            " predetermined post-seed savestate for one known-good initial"
            " seed and only targets the matching Spinda first halves."
            " csv-bruteforce is the wider production path: it searches each"
            " needed CSV initial seed and reuses each hit for every"
            " compatible Spinda first-half target in that seed group."
        ),
    )
    parser.add_argument(
        "--first-half-state",
        type=Path,
        default=DEFAULT_FIRST_HALF_STATE,
        help="Premade post-seed savestate used with --seed-mode loaded-state.",
    )
    parser.add_argument(
        "--first-half-metadata",
        type=Path,
        default=DEFAULT_FIRST_HALF_METADATA,
        help="Metadata JSON used to identify the initial seed of --first-half-state.",
    )
    parser.add_argument(
        "--state-initial-seed",
        type=parse_u16,
        default=None,
        help="Explicit initial seed for --first-half-state. Overrides --first-half-metadata.",
    )
    parser.add_argument("--setup-tape", type=Path, default=DEFAULT_SETUP_TAPE, help="Seed-to-step tape JSON.")
    parser.add_argument("--hit-tape", type=Path, default=DEFAULT_HIT_TAPE, help="Lower-half/walk/save tape JSON.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Folder for 0x####.sav files.")
    parser.add_argument(
        "--firsthalf-script",
        type=Path,
        default=DEFAULT_FIRSTHALF_SCRIPT,
        help="Path to Seed-Bruteforcer.py, used only for shared mGBA/Qt helper functions.",
    )
    parser.add_argument(
        "--start-half",
        type=parse_u16,
        default=0x0000,
        help="First live daycare lower half to process.",
    )
    parser.add_argument(
        "--end-half",
        type=parse_u16,
        default=0xFFFF,
        help="Last live daycare lower half to process.",
    )
    parser.add_argument("--compatibility-percent", type=int, default=DEFAULT_COMPATIBILITY_PERCENT)
    parser.add_argument("--hit-tape-target-delay", type=int, default=DEFAULT_HIT_TAPE_TARGET_DELAY)
    parser.add_argument("--limit", type=int, default=None, help="Optional target count limit for shakedown runs.")
    parser.add_argument(
        "--preserve-raw-csv-targets",
        action="store_true",
        help=(
            "Preserve every raw target_half_16bit row from firsthalf.csv. "
            "This keeps the full 65,536 CSV target space while naming main "
            "outputs by the live FR/LG daycare half and suffixing only the "
            "two raw-half collision duplicates."
        ),
    )
    parser.add_argument("--overwrite", action="store_true", help="Replace existing 1sthalves/0x####.sav files.")
    parser.add_argument("--dry-run", action="store_true", help="Plan the batch without launching or driving mGBA.")
    parser.add_argument(
        "--order",
        choices=("seed", "target"),
        default="target",
        help="Process by target number, or sorted by CSV seed metadata.",
    )
    parser.add_argument("--progress-every", type=int, default=DEFAULT_PROGRESS_EVERY)
    parser.add_argument(
        "--status-every",
        type=int,
        default=DEFAULT_PROGRESS_EVERY,
        help=(
            "Write _batch_status.json every N completed exports plus at the end. "
            "Output files remain the authoritative resume markers between writes."
        ),
    )
    parser.add_argument(
        "--t-minus-recovery-window",
        type=int,
        default=DEFAULT_T_MINUS_RECOVERY_WINDOW,
        help="Frames to scan after reloading the first-half state if the expected t-18 PRNG is missed.",
    )
    parser.add_argument(
        "--hit-rng-drift-window",
        type=int,
        default=DEFAULT_HIT_RNG_DRIFT_WINDOW,
        help=(
            "Signed LCRNG/LCRNG(R) window used to explain post-hit PRNG drift "
            "after replaying the first 18 frames of the hit tape."
        ),
    )
    parser.add_argument(
        "--hit-delay-search-radius",
        type=int,
        default=DEFAULT_HIT_DELAY_SEARCH_RADIUS,
        help=(
            "How many frames before/after the nominal hit delay to try from an "
            "in-memory pre-hit checkpoint."
        ),
    )
    parser.add_argument(
        "--max-delay",
        type=int,
        default=DEFAULT_MAX_DELAY,
        help="Maximum title-screen delay to try while brute-forcing each CSV initial seed.",
    )
    parser.add_argument(
        "--seed-timeout",
        type=int,
        default=DEFAULT_SEED_TIMEOUT,
        help="Frames to hold A/Start while waiting for Timer 1 to expose the initial seed.",
    )
    return parser


def normalize_config(args: argparse.Namespace) -> BatchConfig:
    """Resolve and validate CLI settings."""

    if args.start_half > args.end_half:
        raise SystemExit("--start-half must be <= --end-half.")
    if args.hit_tape_target_delay < 0:
        raise SystemExit("--hit-tape-target-delay must be non-negative.")
    if args.limit is not None and args.limit <= 0:
        raise SystemExit("--limit must be positive when provided.")
    if args.progress_every <= 0:
        raise SystemExit("--progress-every must be positive.")
    if args.status_every <= 0:
        raise SystemExit("--status-every must be positive.")
    if args.t_minus_recovery_window < 0:
        raise SystemExit("--t-minus-recovery-window must be non-negative.")
    if args.hit_rng_drift_window < 0:
        raise SystemExit("--hit-rng-drift-window must be non-negative.")
    if args.hit_delay_search_radius < 0:
        raise SystemExit("--hit-delay-search-radius must be non-negative.")
    if args.max_delay < 0:
        raise SystemExit("--max-delay must be non-negative.")
    if args.seed_timeout <= 0:
        raise SystemExit("--seed-timeout must be positive.")

    return BatchConfig(
        csv_path=absolute_path(args.csv),
        rom_path=absolute_path(args.rom),
        base_save_path=absolute_path(args.base_save),
        seed_mode=str(args.seed_mode),
        first_half_state_path=absolute_path(args.first_half_state),
        first_half_metadata_path=absolute_path(args.first_half_metadata)
        if args.first_half_metadata is not None
        else None,
        state_initial_seed=args.state_initial_seed,
        setup_tape_path=absolute_path(args.setup_tape),
        hit_tape_path=absolute_path(args.hit_tape),
        output_dir=absolute_path(args.output_dir),
        firsthalf_script_path=absolute_path(args.firsthalf_script),
        target_start=args.start_half,
        target_end=args.end_half,
        compatibility_percent=int(args.compatibility_percent),
        hit_tape_target_delay=int(args.hit_tape_target_delay),
        limit=args.limit,
        preserve_raw_csv_targets=bool(args.preserve_raw_csv_targets),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        order=str(args.order),
        progress_every=int(args.progress_every),
        status_every=int(args.status_every),
        t_minus_recovery_window=int(args.t_minus_recovery_window),
        hit_rng_drift_window=int(args.hit_rng_drift_window),
        hit_delay_search_radius=int(args.hit_delay_search_radius),
        max_delay=int(args.max_delay),
        seed_timeout=int(args.seed_timeout),
    )


def batch_paths(output_dir: Path) -> BatchPaths:
    """Return the output, manifest, and scratch paths for one batch run."""

    output_dir = absolute_path(output_dir)
    manifest_dir = output_dir / "_manifests"
    state_dir = output_dir / "_states"
    return BatchPaths(
        output_dir=output_dir,
        manifest_dir=manifest_dir,
        state_dir=state_dir,
        baseline_checkpoint_path=state_dir / "firsthalf-title-baseline",
        rolling_checkpoint_path=state_dir / "firsthalf-title-rolling",
        candidate_checkpoint_path=state_dir / "firsthalf-title-candidate",
        hit_delay_checkpoint_path=state_dir / "firsthalf-hit-delay-scratch",
        post_seed_state_path=state_dir / "firsthalf-post-seed-current",
        post_setup_checkpoint_path=state_dir / "firsthalf-post-setup-runway",
        status_path=output_dir / "_batch_status.json",
        resume_status_path=output_dir / "_resume_status.json",
    )


def ensure_batch_dirs(paths: BatchPaths) -> None:
    """Create the `1sthalves` output directory and internal metadata folders."""

    paths.output_dir.mkdir(parents=True, exist_ok=True)
    paths.manifest_dir.mkdir(parents=True, exist_ok=True)
    paths.state_dir.mkdir(parents=True, exist_ok=True)


def lane_save_path(output_dir: Path, live_lower_half: int) -> Path:
    """Return the required direct `1sthalves/0x####.sav` path.

    The filename is the actual live daycare lower half written by FR/LG, not
    the raw CSV `Random()` result before the game's `((x % 0xFFFE) + 1)`
    transform.
    """

    return output_dir / f"0x{live_lower_half & 0xFFFF:04X}.sav"


def lane_manifest_path(paths: BatchPaths, live_lower_half: int) -> Path:
    """Return the per-live-lower-half manifest path."""

    return paths.manifest_dir / f"0x{live_lower_half & 0xFFFF:04X}.json"


def raw_csv_output_root(output_dir: Path) -> Path:
    """Return the full raw-CSV corpus root."""

    if RAW_CSV_OUTPUT_DIR_NAME:
        return output_dir / RAW_CSV_OUTPUT_DIR_NAME
    return output_dir


def raw_csv_manifest_root(paths: BatchPaths) -> Path:
    """Return the full raw-CSV manifest root."""

    return paths.manifest_dir / "raw_csv"


def canonical_raw_half_for_live_half(live_lower_half: int) -> int:
    """Return the normal raw CSV half that maps to one live FR/LG half.

    FR/LG stores `((Random() % 0xFFFE) + 1)`. Most live halves have one
    obvious raw predecessor: `live - 1`. The two wraparound duplicates are
    still preserved separately by suffixing the non-canonical raw half.
    """

    live_lower_half &= 0xFFFF
    if not 1 <= live_lower_half <= 0xFFFE:
        raise ValueError(f"live_lower_half must be 0x0001..0xFFFE, got {format_u16(live_lower_half)}")
    return (live_lower_half - 1) & 0xFFFF


def raw_csv_target_is_collision(target: FirstHalfTarget) -> bool:
    """Return true when raw CSV identity needs a filename suffix."""

    return (target.lower_half & 0xFFFF) != canonical_raw_half_for_live_half(target.live_lower_half)


def raw_csv_filename_stem(target: FirstHalfTarget) -> str:
    """Return the live-name filename stem for a raw-CSV target."""

    live = f"0x{target.live_lower_half & 0xFFFF:04X}"
    if raw_csv_target_is_collision(target):
        return f"{live}__raw0x{target.lower_half & 0xFFFF:04X}"
    return live


def raw_csv_save_path(output_dir: Path, target: FirstHalfTarget) -> Path:
    """Return the live-name save path for preserving all raw CSV targets."""

    root = raw_csv_output_root(output_dir)
    if raw_csv_target_is_collision(target):
        return root / RAW_CSV_COLLISION_DIR_NAME / "saves" / f"{raw_csv_filename_stem(target)}.sav"
    return root / "saves" / f"{raw_csv_filename_stem(target)}.sav"


def raw_csv_state_path(output_dir: Path, target: FirstHalfTarget) -> Path:
    """Return the matching live-name pre-daycare-man savestate path."""

    root = raw_csv_output_root(output_dir)
    if raw_csv_target_is_collision(target):
        return root / RAW_CSV_COLLISION_DIR_NAME / "states" / f"{raw_csv_filename_stem(target)}.ss0"
    return root / "states" / f"{raw_csv_filename_stem(target)}.ss0"


def raw_csv_manifest_path(paths: BatchPaths, target: FirstHalfTarget) -> Path:
    """Return the live-name manifest path for a raw-CSV target."""

    root = raw_csv_manifest_root(paths)
    if raw_csv_target_is_collision(target):
        return root / RAW_CSV_COLLISION_DIR_NAME / f"{raw_csv_filename_stem(target)}.json"
    return root / f"{raw_csv_filename_stem(target)}.json"


def output_key_mode_name(preserve_raw_csv_targets: bool) -> str:
    """Return manifest/status output key mode for current path semantics."""

    return RAW_CSV_LIVE_OUTPUT_KEY_MODE if preserve_raw_csv_targets else "live"


def output_key_half_for_target(target: FirstHalfTarget, *, preserve_raw_csv_targets: bool) -> int:
    """Return the half used in output filenames and manifest resume checks."""

    return target.live_lower_half


def target_save_path(
    output_dir: Path,
    target: FirstHalfTarget,
    *,
    preserve_raw_csv_targets: bool = False,
) -> Path:
    """Return the canonical exported save path for one target."""

    if preserve_raw_csv_targets:
        return raw_csv_save_path(output_dir, target)
    return lane_save_path(output_dir, target.live_lower_half)


def target_manifest_path(
    paths: BatchPaths,
    target: FirstHalfTarget,
    *,
    preserve_raw_csv_targets: bool = False,
) -> Path:
    """Return the canonical manifest path for one target."""

    if preserve_raw_csv_targets:
        return raw_csv_manifest_path(paths, target)
    return lane_manifest_path(paths, target.live_lower_half)


def target_pre_daycare_man_state_path(
    output_dir: Path,
    target: FirstHalfTarget,
    *,
    preserve_raw_csv_targets: bool = False,
) -> Path:
    """Return the savestate path that matches the target save filename."""

    if preserve_raw_csv_targets:
        return raw_csv_state_path(output_dir, target)
    return target_save_path(
        output_dir,
        target,
        preserve_raw_csv_targets=preserve_raw_csv_targets,
    ).with_suffix(".ss0")


def _utc_now() -> str:
    """Return an ISO-8601 UTC timestamp for manifests."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_tape(path: Path) -> input_tape.InputTape:
    """Read and validate one input tape JSON file."""

    data = json.loads(path.read_text(encoding="utf-8"))
    try:
        return input_tape.InputTape.from_json(data)
    except ValueError as exc:
        if "frame_count mismatch" not in str(exc):
            raise
        actual_frames = sum(int(run["frames"]) for run in data.get("runs", ()))
        # Keep the tape file itself untouched. The stale header is useful
        # diagnostic information, and rewriting user-authored route JSON during
        # a generation run would make later audits harder.
        print(
            "Warning: input tape frame_count header does not match its runs;"
            f" trusting runs for replay. path={path}"
            f" header={data.get('frame_count')!r} actual={actual_frames}"
        )
        data["frame_count"] = actual_frames
        return input_tape.InputTape.from_json(data)


def _parse_optional_int(value: object) -> int:
    """Parse JSON integer fields that may be numbers or `0x` strings."""

    if isinstance(value, bool):
        raise ValueError("boolean values are not valid integers here")
    return int(value, 0) if isinstance(value, str) else int(value)


def _parse_manifest_u16(payload: Mapping[str, object], key: str) -> int | None:
    """Parse an optional manifest hex field as an unsigned 16-bit integer."""

    value = payload.get(key)
    if value is None:
        return None
    try:
        parsed = _parse_optional_int(value)
    except (TypeError, ValueError):
        return None
    if not 0 <= parsed <= 0xFFFF:
        return None
    return parsed


def _manifest_path_matches(payload: Mapping[str, object], key: str, expected_path: Path) -> bool:
    """Return whether a manifest path field points at the expected artifact."""

    raw_path = payload.get(key)
    return isinstance(raw_path, str) and raw_path == str(expected_path)


def classify_target_artifacts(
    paths: BatchPaths,
    config: BatchConfig,
    target: FirstHalfTarget,
) -> TargetArtifactStatus:
    """Return whether one target's save/state/manifest triad is resume-complete.

    The batch writes the `.ss0`, then the `.sav`, then the manifest. A crash can
    leave any prefix of that sequence on disk, so restart safety must validate
    the full triad instead of treating a save/state pair as complete.
    """

    save_path = target_save_path(
        paths.output_dir,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    pre_daycare_man_state_path = target_pre_daycare_man_state_path(
        paths.output_dir,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    manifest_path = target_manifest_path(
        paths,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )

    def status(complete: bool, reason: str) -> TargetArtifactStatus:
        return TargetArtifactStatus(
            complete,
            reason,
            save_path,
            manifest_path,
            pre_daycare_man_state_path,
        )

    if config.overwrite:
        return status(False, "overwrite-requested")
    if not save_path.is_file():
        return status(False, "missing-save")
    if not pre_daycare_man_state_path.is_file():
        return status(False, "missing-pre-daycare-man-state")
    if not manifest_path.is_file():
        return status(False, "missing-manifest")

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return status(False, "unreadable-manifest")
    if not isinstance(manifest, dict):
        return status(False, "non-object-manifest")

    expected_output_key_mode = output_key_mode_name(config.preserve_raw_csv_targets)
    expected_output_key_half = output_key_half_for_target(
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    if manifest.get("schema_version") != 1:
        return status(False, "manifest-schema-mismatch")
    if manifest.get("output_key_mode") != expected_output_key_mode:
        return status(False, "manifest-output-key-mode-mismatch")
    if _parse_manifest_u16(manifest, "output_key_half") != expected_output_key_half:
        return status(False, "manifest-output-key-half-mismatch")
    if _parse_manifest_u16(manifest, "csv_target_half_raw") != target.lower_half:
        return status(False, "manifest-csv-target-mismatch")
    if _parse_manifest_u16(manifest, "spinda_half_live") != target.live_lower_half:
        return status(False, "manifest-live-half-mismatch")
    if _parse_manifest_u16(manifest, "initial_seed") != target.initial_seed:
        return status(False, "manifest-initial-seed-mismatch")
    if not _manifest_path_matches(manifest, "save_path", save_path):
        return status(False, "manifest-save-path-mismatch")
    if not _manifest_path_matches(manifest, "pre_daycare_man_state_path", pre_daycare_man_state_path):
        return status(False, "manifest-state-path-mismatch")

    try:
        if manifest.get("save_sha1") != sha1_file(save_path):
            return status(False, "save-sha1-mismatch")
        if manifest.get("pre_daycare_man_state_sha1") != sha1_file(pre_daycare_man_state_path):
            return status(False, "state-sha1-mismatch")
    except OSError:
        return status(False, "artifact-read-failed")

    return status(True, "complete")


def build_target_result_from_artifacts(
    target: FirstHalfTarget,
    artifact_status: TargetArtifactStatus,
    *,
    status: str,
) -> TargetResult:
    """Create a `TargetResult` for a target whose paths were already derived."""

    return TargetResult(
        target.live_lower_half,
        status,
        artifact_status.save_path,
        artifact_status.manifest_path,
        csv_target_half_raw=target.lower_half,
        spinda_half_live=target.live_lower_half,
        pre_daycare_man_state_path=artifact_status.pre_daycare_man_state_path,
    )


def scan_resume_targets(
    paths: BatchPaths,
    config: BatchConfig,
    targets: Sequence[FirstHalfTarget],
) -> tuple[list[TargetResult], list[FirstHalfTarget], Counter[str]]:
    """Split targets into already-complete and pending work for restart safety."""

    skipped_targets: list[TargetResult] = []
    remaining_targets: list[FirstHalfTarget] = []
    reasons: Counter[str] = Counter()

    for target in targets:
        artifact_status = classify_target_artifacts(paths, config, target)
        reasons[artifact_status.reason] += 1
        if artifact_status.complete:
            skipped_targets.append(
                build_target_result_from_artifacts(
                    target,
                    artifact_status,
                    status="skipped-existing",
                )
            )
        else:
            remaining_targets.append(target)

    return skipped_targets, remaining_targets, reasons


def write_resume_scan_status(
    paths: BatchPaths,
    config: BatchConfig,
    *,
    total_targets_loaded: int,
    skipped_existing: int,
    pending_targets: int,
    reason_counts: Mapping[str, int],
) -> None:
    """Persist the startup resume scan before any emulator work begins."""

    write_json_atomic(
        paths.resume_status_path,
        {
            "updated_at_utc": _utc_now(),
            "run_status": "resume-scan",
            "resume_model": "save+pre-daycare-state+manifest-sha1",
            "resume_compatible_with_existing_outputs": True,
            "seed_mode": config.seed_mode,
            "output_key_mode": output_key_mode_name(config.preserve_raw_csv_targets),
            "total_targets_loaded": total_targets_loaded,
            "skipped_existing_complete": skipped_existing,
            "pending_targets": pending_targets,
            "artifact_reason_counts": dict(sorted(reason_counts.items())),
        },
    )


def resolve_state_initial_seed(config: BatchConfig) -> int | None:
    """Return the initial seed represented by the loaded `1 from egg.ss0` savestate."""

    if config.state_initial_seed is not None:
        return config.state_initial_seed
    metadata_path = config.first_half_metadata_path
    if metadata_path is None or not metadata_path.is_file():
        return None

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    try:
        seed = _parse_optional_int(metadata["target_seed"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError(f"Could not read target_seed from metadata: {metadata_path}") from exc
    if not 0 <= seed <= 0xFFFF:
        raise ValueError(f"Metadata target_seed must fit in 16 bits: {metadata_path}")
    return seed


def validate_loaded_state_anchor(config: BatchConfig) -> None:
    r"""Reject title-screen calibration checkpoints in loaded-state mode.

    The batch workflow expects `--first-half-state` to be a post-seed route
    anchor such as `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0`.
    The metadata exported by `Seed-Bruteforcer.py`
    also names several title-screen calibration checkpoints; using one of those
    here silently starts from the wrong phase of the workflow and makes the
    later `t-18` replay impossible.
    """

    if config.seed_mode != "loaded-state":
        return
    metadata_path = config.first_half_metadata_path
    if metadata_path is None or not metadata_path.is_file():
        return

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        return

    calibration_checkpoint_names = {
        str(name)
        for key in (
            "readonly_checkpoint_name",
            "locked_baseline_checkpoint_name",
            "working_checkpoint_name",
            "source_readonly_checkpoint_name",
        )
        if (name := metadata.get(key))
    }
    if config.first_half_state_path.name not in calibration_checkpoint_names:
        return

    raise ValueError(
        "Loaded-state mode requires a post-seed route anchor such as "
        f"{DEFAULT_FIRST_HALF_STATE}, but {config.first_half_state_path} matches "
        "a title-screen calibration checkpoint named in "
        f"{metadata_path}. Keep --first-half-metadata pointed at the locked "
        "baseline metadata, but point --first-half-state at the post-seed "
        "route savestate."
    )


def split_tape(tape: input_tape.InputTape, first_frames: int) -> tuple[input_tape.InputTape, input_tape.InputTape]:
    """Split a tape into prefix and suffix tapes without expanding all frames."""

    if not 0 <= first_frames <= tape.frame_count:
        raise ValueError(
            f"split point must be between 0 and {tape.frame_count}, got {first_frames}"
        )

    head: list[input_tape.InputRun] = []
    tail: list[input_tape.InputRun] = []
    remaining_head = first_frames
    for run in tape.runs:
        if remaining_head <= 0:
            tail.append(run)
            continue
        if run.frames <= remaining_head:
            head.append(run)
            remaining_head -= run.frames
            continue
        head.append(input_tape.InputRun(mask=run.mask, frames=remaining_head))
        tail.append(input_tape.InputRun(mask=run.mask, frames=run.frames - remaining_head))
        remaining_head = 0

    return (
        input_tape.InputTape(runs=head, metadata={**tape.metadata, "split": "prefix"}),
        input_tape.InputTape(runs=tail, metadata={**tape.metadata, "split": "suffix"}),
    )


def expected_daycare_lower_half_from_csv_half(csv_half: int) -> int:
    """Convert the CSV raw Random() half into FR/LG's stored daycare lower half.

    FR/LG does not store the raw `Random()` result directly. The pending egg
    personality uses `((Random()) % 0xFFFE) + 1`, so the live daycare lower half
    is usually the CSV half plus one, with the documented wrap for the top two
    raw values.
    """

    return daycare_lower_half_from_random_half(csv_half)


def signed_lcrng_distance(expected_state: int, observed_state: int, max_steps: int) -> int | None:
    """Return signed LCRNG distance from expected to observed within a window.

    Positive values mean the live game is ahead of the CSV state. Negative
    values mean it is behind and are found with LCRNG(R). `None` means the
    states are unrelated within the bounded diagnostic window.
    """

    expected_state &= 0xFFFFFFFF
    observed_state &= 0xFFFFFFFF
    if expected_state == observed_state:
        return 0

    forward = expected_state
    backward = expected_state
    for step in range(1, max_steps + 1):
        forward = lcrng_next_state(forward)
        if forward == observed_state:
            return step
        backward = lcrng_previous_state(backward)
        if backward == observed_state:
            return -step
    return None


def target_random_call_from_t_zero(
    t_zero_rng: int,
    target_half: int,
    *,
    max_steps: int,
) -> tuple[int, int] | None:
    """Find the first post-`t-0` Random() call that yields the target half."""

    state = t_zero_rng & 0xFFFFFFFF
    for offset in range(1, max_steps + 1):
        state = lcrng_next_state(state)
        if ((state >> 16) & 0xFFFF) == (target_half & 0xFFFF):
            return offset, state
    return None


def signed_u16_distance(from_value: int, to_value: int) -> int:
    """Return the shortest signed distance in 16-bit seed space."""

    return (((to_value & 0xFFFF) - (from_value & 0xFFFF) + 0x8000) & 0xFFFF) - 0x8000


def initial_seed_lcrng_hint(observation: InitialSeedObservation, target_seed: int) -> int | None:
    """Try a bounded LCRNG/LCRNG(R) hint from one observed initial-seed state.

    The initial seed itself is chosen by Timer 1, so this is only a secondary
    hint. It can occasionally say "the seeded PRNG state is a few steps away
    from the target seed state", but the main title-delay estimate still comes
    from real Timer 1 observations across neighboring delays.
    """

    return signed_lcrng_distance(
        observation.rng_at_seed & 0xFFFFFFFF,
        target_seed & 0xFFFF,
        DEFAULT_INITIAL_SEED_LCRNG_HINT_WINDOW,
    )


def estimate_initial_seed_delay_candidates(
    target_seed: int,
    observation: InitialSeedObservation,
    previous_observation: InitialSeedObservation | None,
    *,
    max_delay: int,
) -> list[tuple[int, str, int | None]]:
    """Estimate title-delay candidates from exact observed seed misses.

    The exact seed and frame from a miss tell us more than a raw timeout. We
    therefore derive two bounded candidate delays:

    - a Timer-1 delta estimate based on two neighboring observed seeds for the
      same button
    - a small optional LCRNG/LCRNG(R) hint from the observed seeded PRNG state

    Both are only hints. The linear rolling search still continues afterward.
    """

    candidates: list[tuple[int, str, int | None]] = []
    seen: set[int] = set()

    def _append(candidate_delay: int, reason: str, detail: int | None) -> None:
        if candidate_delay == observation.delay_frames:
            return
        if not 0 <= candidate_delay <= max_delay:
            return
        if candidate_delay in seen:
            return
        seen.add(candidate_delay)
        candidates.append((candidate_delay, reason, detail))

    if previous_observation is not None and previous_observation.delay_frames != observation.delay_frames:
        delay_delta = observation.delay_frames - previous_observation.delay_frames
        seed_delta = signed_u16_distance(previous_observation.seed_value, observation.seed_value)
        target_delta = signed_u16_distance(observation.seed_value, target_seed)
        if delay_delta != 0 and seed_delta != 0:
            estimated_offset = round((target_delta * delay_delta) / seed_delta)
            if 0 < abs(estimated_offset) <= DEFAULT_INITIAL_SEED_AUTOTUNE_MAX_JUMP:
                _append(observation.delay_frames + estimated_offset, "timer1-delta", estimated_offset)

    lcrng_hint = initial_seed_lcrng_hint(observation, target_seed)
    if lcrng_hint is not None and 0 < abs(lcrng_hint) <= DEFAULT_INITIAL_SEED_AUTOTUNE_MAX_JUMP:
        _append(observation.delay_frames + lcrng_hint, "lcrng-hint", lcrng_hint)

    return candidates


def classify_initial_seed_branch_error(exc: RuntimeError) -> str | None:
    """Classify one title-input branch failure for the batch search loop.

    The shared `Seed-Bruteforcer.py` helper raises a few branch-local errors that should
    not abort the entire CSV seed group:

    - the branch never exposed a seed within the observation window
    - the rebuilt checkpoint was still outside the legal pre-seed title window

    Everything else remains a hard failure because it implies a deeper helper
    or checkpoint integrity problem.
    """

    message = str(exc)
    if "Initial seed was not observed within" in message:
        return "timeout"
    if message.startswith("Checkpoint is no longer in the pre-seed title state:"):
        return "checkpoint-drift"
    return None


def resolve_hit_rng_drift(target: FirstHalfTarget, observed_rng: int, *, max_steps: int) -> HitDriftResult:
    """Explain the live post-hit PRNG state relative to the CSV `t-0` row."""

    signed_drift = signed_lcrng_distance(target.t_zero_rng, observed_rng, max_steps)
    if signed_drift is None:
        raise RuntimeError(
            f"t-0 PRNG mismatch for {format_u16(target.lower_half)}: "
            f"expected={format_u32(target.t_zero_rng)} observed={format_u32(observed_rng)} "
            f"and no LCRNG/LCRNG(R) drift <= {max_steps} explains it."
        )

    target_call = target_random_call_from_t_zero(
        target.t_zero_rng,
        target.lower_half,
        max_steps=max_steps,
    )
    target_call_offset = target_call[0] if target_call is not None else None
    target_call_rng = target_call[1] if target_call is not None else None

    if signed_drift < 0:
        raise RuntimeError(
            f"Hit tape stopped before CSV t-0 for {format_u16(target.lower_half)}: "
            f"drift={signed_drift} expected={format_u32(target.t_zero_rng)} "
            f"observed={format_u32(observed_rng)}."
        )
    if signed_drift > 0 and (
        target_call_offset is None or target_call_offset > signed_drift
    ):
        raise RuntimeError(
            f"Hit tape advanced past CSV t-0 for {format_u16(target.lower_half)}, "
            "but the target Random() call was not inside the observed drift: "
            f"drift={signed_drift} expected={format_u32(target.t_zero_rng)} "
            f"observed={format_u32(observed_rng)}."
        )

    if signed_drift != 0:
        print(
            "Accepted post-hit PRNG drift:"
            f" target={format_u16(target.lower_half)}"
            f" drift={signed_drift}"
            f" csv_t0={format_u32(target.t_zero_rng)}"
            f" observed={format_u32(observed_rng)}"
            f" target_call_offset={target_call_offset}"
            f" target_call_rng={format_u32(target_call_rng)}"
        )
    return HitDriftResult(
        observed_rng=observed_rng,
        signed_drift_from_t_zero=signed_drift,
        target_call_offset=target_call_offset,
        target_call_rng=target_call_rng,
    )


def _call_helper_load_state_file(helper, core, path: Path, *, qt_mode: bool) -> None:
    """Call the shared file-backed load helper with optional Qt kwargs."""

    try:
        helper.load_state_file(core, path, qt_mode=qt_mode)
    except TypeError:
        helper.load_state_file(core, path)


def capture_runtime_state(
    helper,
    core,
    *,
    scratch_path: Path | None = None,
) -> RuntimeCheckpoint:
    """Capture one pre-hit checkpoint using the best mode this core supports."""

    save_raw_state = getattr(core, "save_raw_state", None)
    load_raw_state = getattr(core, "load_raw_state", None)
    if callable(save_raw_state) and callable(load_raw_state):
        state = save_raw_state()
        if state is None:
            raise RuntimeError("save_raw_state() failed while capturing the pre-hit checkpoint.")
        return RuntimeCheckpoint(mode="raw", state=state)

    save_scratch_state = getattr(core, "save_scratch_state", None)
    load_scratch_state = getattr(core, "load_scratch_state", None)
    if callable(save_scratch_state) and callable(load_scratch_state):
        save_scratch_state()
        return RuntimeCheckpoint(mode="scratch")

    if (
        helper is not None
        and scratch_path is not None
        and hasattr(helper, "save_state_file")
        and hasattr(helper, "load_state_file")
    ):
        scratch_path.parent.mkdir(parents=True, exist_ok=True)
        helper.save_state_file(core, scratch_path)
        return RuntimeCheckpoint(mode="file", path=scratch_path)

    raise RuntimeError(
        "The current core does not support raw or scratch savestates, and no "
        "file-backed scratch checkpoint helper was available."
    )


def restore_runtime_state(
    helper,
    core,
    checkpoint: RuntimeCheckpoint,
    *,
    qt_mode: bool,
) -> None:
    """Restore one checkpoint captured with `capture_runtime_state()`."""

    if checkpoint.mode == "raw":
        load_raw_state = getattr(core, "load_raw_state", None)
        if not callable(load_raw_state):
            raise RuntimeError("The current core does not support in-memory savestate restore.")
        if not load_raw_state(checkpoint.state):
            raise RuntimeError("load_raw_state() failed while restoring the pre-hit checkpoint.")
        return

    if checkpoint.mode == "scratch":
        load_scratch_state = getattr(core, "load_scratch_state", None)
        if not callable(load_scratch_state):
            raise RuntimeError("The current core does not support scratch savestate restore.")
        load_scratch_state()
        return

    if checkpoint.mode == "file":
        if helper is None or checkpoint.path is None:
            raise RuntimeError("Missing helper/path for file-backed checkpoint restore.")
        _call_helper_load_state_file(helper, core, checkpoint.path, qt_mode=qt_mode)
        return

    raise RuntimeError(f"Unsupported checkpoint mode: {checkpoint.mode}")


def capture_post_setup_runway_checkpoint(helper, core, paths: BatchPaths) -> RuntimeCheckpoint:
    """Capture the shared post-setup runway without risking scratch overwrite.

    The optimized loaded-state sweep later captures per-target pre-hit
    checkpoints. Visible-Qt scratch state is a single slot, so the long-lived
    post-setup rollback point must be raw memory or a file-backed state rather
    than the scratch slot that hit-delay retry will reuse.
    """

    save_raw_state = getattr(core, "save_raw_state", None)
    load_raw_state = getattr(core, "load_raw_state", None)
    if callable(save_raw_state) and callable(load_raw_state):
        state = save_raw_state()
        if state is None:
            raise RuntimeError("save_raw_state() failed while capturing the post-setup runway.")
        return RuntimeCheckpoint(mode="raw", state=state)

    if helper is not None and hasattr(helper, "save_state_file") and hasattr(helper, "load_state_file"):
        paths.post_setup_checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
        helper.save_state_file(core, paths.post_setup_checkpoint_path)
        return RuntimeCheckpoint(mode="file", path=paths.post_setup_checkpoint_path)

    return capture_runtime_state(
        helper,
        core,
        scratch_path=paths.post_setup_checkpoint_path,
    )


def hit_delay_candidates(base_delay: int, radius: int, tape_frame_count: int) -> list[int]:
    """Return nearby prefix lengths in a practical search order."""

    candidates: list[int] = []
    for delta in range(radius + 1):
        for signed in ((0,) if delta == 0 else (-delta, delta)):
            candidate = base_delay + signed
            if not 0 <= candidate <= tape_frame_count:
                continue
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def build_hit_delay_variants(
    full_hit_tape: input_tape.InputTape,
    *,
    base_delay: int,
    radius: int,
) -> tuple[HitDelayVariant, ...]:
    """Pre-split the nearby hit-delay prefixes/suffixes for this run.

    The hit tape and the operator-selected retry window are batch-level inputs,
    so their prefix/suffix tape pairs can be built once and reused across all
    targets in the current run.
    """

    variants: list[HitDelayVariant] = []
    for prefix_frames in hit_delay_candidates(base_delay, radius, full_hit_tape.frame_count):
        prefix_tape, suffix_tape = split_tape(full_hit_tape, prefix_frames)
        variants.append(
            HitDelayVariant(
                prefix_frames=prefix_frames,
                prefix_tape=prefix_tape,
                suffix_tape=suffix_tape,
            )
        )
    return tuple(variants)


def search_hit_delay_from_checkpoint(
    helper,
    core,
    config: BatchConfig,
    target: FirstHalfTarget,
    *,
    full_hit_tape: input_tape.InputTape,
    hit_delay_variants: Sequence[HitDelayVariant] | None = None,
    scratch_checkpoint_path: Path | None = None,
    pre_hit_checkpoint: RuntimeCheckpoint | None = None,
    qt_mode: bool = False,
) -> tuple[HitDelayResult, input_tape.InputTape]:
    """Try nearby hit delays from one pre-hit checkpoint.

    Host-side runs usually use raw in-memory states. The visible Qt core uses
    the dedicated scratch-state bridge when available and falls back to one
    file-backed scratch savestate if needed.
    """

    checkpoint = pre_hit_checkpoint or capture_runtime_state(
        helper,
        core,
        scratch_path=scratch_checkpoint_path,
    )
    if checkpoint.mode == "scratch":
        print("Using Qt scratch checkpoint for nearby hit-delay search.")
    elif checkpoint.mode == "file" and checkpoint.path is not None:
        print(f"Using file-backed scratch checkpoint for nearby hit-delay search: {checkpoint.path}")
    expected_daycare_lower_half = expected_daycare_lower_half_from_csv_half(target.lower_half)
    attempts: list[str] = []
    if hit_delay_variants is None:
        hit_delay_variants = build_hit_delay_variants(
            full_hit_tape,
            base_delay=config.hit_tape_target_delay,
            radius=config.hit_delay_search_radius,
        )

    for variant in hit_delay_variants:
        prefix_frames = variant.prefix_frames
        restore_runtime_state(helper, core, checkpoint, qt_mode=qt_mode)
        replay_input_tape(core, variant.prefix_tape, label=f"hit tape prefix through delay {prefix_frames}")
        observed_rng = read_rng_state(core)
        try:
            hit_drift = resolve_hit_rng_drift(
                target,
                observed_rng,
                max_steps=config.hit_rng_drift_window,
            )
        except RuntimeError as exc:
            attempts.append(f"delay={prefix_frames} drift_error={exc}")
            continue

        observed_lower_half = read_daycare_lower_half(core)
        attempts.append(
            "delay="
            f"{prefix_frames} observed_lower={format_u16(observed_lower_half)}"
            f" drift={hit_drift.signed_drift_from_t_zero}"
            f" observed_rng={format_u32(observed_rng)}"
        )
        if observed_lower_half == expected_daycare_lower_half:
            if prefix_frames != config.hit_tape_target_delay:
                print(
                    "Adjusted hit delay from the pre-hit checkpoint:"
                    f" csv_half={format_u16(target.lower_half)}"
                    f" expected_daycare_half={format_u16(expected_daycare_lower_half)}"
                    f" chosen_delay={prefix_frames}"
                    f" nominal_delay={config.hit_tape_target_delay}"
                )
            return (
                HitDelayResult(
                    chosen_prefix_frames=prefix_frames,
                    hit_drift=hit_drift,
                    observed_daycare_lower_half=observed_lower_half,
                ),
                variant.suffix_tape,
            )

    attempt_summary = "\n".join(attempts[-8:])
    raise RuntimeError(
        f"Could not match the expected daycare lower half for CSV target {format_u16(target.lower_half)} "
        f"(expected live daycare half {format_u16(expected_daycare_lower_half)}) within "
        f"+/-{config.hit_delay_search_radius} frames of the nominal delay {config.hit_tape_target_delay}.\n"
        f"Recent attempts:\n{attempt_summary}"
    )


def _csv_target_allowed(target: int, *, start: int, end: int) -> bool:
    """Return whether one CSV target is inside the requested inclusive range."""

    return start <= target <= end


def target_live_lower_half(target: FirstHalfTarget) -> int:
    """Return the actual FR/LG daycare lower half for one CSV target."""

    return target.live_lower_half


def target_priority_key(target: FirstHalfTarget) -> tuple[int, int, int, int]:
    """Return a deterministic priority when raw CSV rows collide onto one live half.

    FR/LG's `((Random() % 0xFFFE) + 1)` mapping causes raw CSV halves `0x0000`
    and `0xFFFE` to both hit live `0x0001`, and raw `0x0001` and `0xFFFF` to
    both hit live `0x0002`. When the operator wants saves named by the actual
    live daycare half, keep only the earliest/shortest known route for that
    live value instead of overwriting files nondeterministically.
    """

    return (target.t_minus_frame, target.initial_seed, target.sweep_index, target.lower_half)


def target_route_order_key(target: FirstHalfTarget) -> tuple[int, int, int]:
    """Sort targets by route time so a loaded-state sweep can run forward once."""

    return (target.t_minus_frame, target.lower_half, target.live_lower_half)


def load_first_half_targets(config: BatchConfig, *, anchor_seed: int | None = None) -> list[FirstHalfTarget]:
    """Stream `firsthalf.csv` into target rows for the requested range."""

    if not config.csv_path.is_file():
        raise SystemExit(f"firsthalf.csv not found: {config.csv_path}")

    rows_by_target: dict[int, dict[str, FirstHalfCsvRow]] = defaultdict(dict)
    skipped_for_anchor: set[int] = set()
    with config.csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames is None:
            raise ValueError("firsthalf.csv is empty.")
        for raw_row in reader:
            t_minus_label = str(raw_row.get("t_minus", "")).strip().lower()
            if t_minus_label not in {"t-18", "t-0"}:
                continue
            row = FirstHalfCsvRow.from_csv_row(raw_row)
            if row.compatibility_percent != config.compatibility_percent:
                continue
            # Range selection is operator-facing, so it follows the actual
            # live daycare half written by FR/LG rather than the raw CSV half.
            live_lower_half = expected_daycare_lower_half_from_csv_half(row.target_half_16bit)
            if not _csv_target_allowed(live_lower_half, start=config.target_start, end=config.target_end):
                continue
            if anchor_seed is not None and row.initial_seed_16bit != anchor_seed:
                skipped_for_anchor.add(row.target_half_16bit)
                continue
            rows_by_target[row.target_half_16bit][t_minus_label] = row

    raw_targets: list[FirstHalfTarget] = []
    for lower_half in sorted(rows_by_target):
        target_rows = rows_by_target[lower_half]
        try:
            t_minus_18 = target_rows["t-18"]
            t_zero = target_rows["t-0"]
        except KeyError as exc:
            raise ValueError(f"Missing required CSV T-minus row for {format_u16(lower_half)}.") from exc

        if (
            t_minus_18.initial_seed_16bit,
            t_minus_18.compatibility_percent,
            t_minus_18.target_half_16bit,
            t_minus_18.sweep_index,
        ) != (
            t_zero.initial_seed_16bit,
            t_zero.compatibility_percent,
            t_zero.target_half_16bit,
            t_zero.sweep_index,
        ):
            raise ValueError(f"Mismatched t-18/t-0 CSV rows for {format_u16(lower_half)}.")

        target_delay = t_zero.frame_from_initial_seed - t_minus_18.frame_from_initial_seed
        if target_delay != config.hit_tape_target_delay:
            raise ValueError(
                f"CSV target delay for {format_u16(lower_half)} is {target_delay}, "
                f"expected {config.hit_tape_target_delay}."
            )
        if lcrng_advance(t_minus_18.rng_seed, config.hit_tape_target_delay) != t_zero.rng_seed:
            raise ValueError(f"CSV PRNG history does not bridge t-18 to t-0 for {format_u16(lower_half)}.")

        raw_targets.append(
            FirstHalfTarget(
                lower_half=lower_half,
                live_lower_half=expected_daycare_lower_half_from_csv_half(lower_half),
                initial_seed=t_minus_18.initial_seed_16bit,
                compatibility_percent=t_minus_18.compatibility_percent,
                sweep_index=t_minus_18.sweep_index,
                t_minus_frame=t_minus_18.frame_from_initial_seed,
                t_minus_rng=t_minus_18.rng_seed,
                t_zero_frame=t_zero.frame_from_initial_seed,
                t_zero_rng=t_zero.rng_seed,
            )
        )

    targets_by_live_lower_half: dict[int, FirstHalfTarget] = {}
    dropped_collisions: list[tuple[int, int, int]] = []
    for target in raw_targets:
        live_lower_half = target_live_lower_half(target)
        previous = targets_by_live_lower_half.get(live_lower_half)
        if previous is None:
            targets_by_live_lower_half[live_lower_half] = target
            continue
        # Two raw CSV halves can collapse onto the same live daycare half. Pick
        # one deterministic winner so the exported archive stays keyed to the
        # actual in-game result instead of whichever row happened to load last.
        if target_priority_key(target) < target_priority_key(previous):
            dropped_collisions.append((live_lower_half, previous.lower_half, target.lower_half))
            targets_by_live_lower_half[live_lower_half] = target
            continue
        dropped_collisions.append((live_lower_half, target.lower_half, previous.lower_half))

    if dropped_collisions:
        if config.preserve_raw_csv_targets:
            print(
                "Preserving CSV raw-half collisions as separate suffixed targets:"
                f" raw_csv_targets={len(raw_targets)}"
                f" unique_live_targets={len(targets_by_live_lower_half)}"
                f" collision_pairs={len(dropped_collisions)}"
            )
        else:
            print(
                "Collapsed CSV raw-half collisions onto live daycare halves:"
                f" unique_live_targets={len(targets_by_live_lower_half)}"
                f" dropped_raw_duplicates={len(dropped_collisions)}"
            )

    if anchor_seed is not None:
        print(
            "Filtered firsthalf.csv by loaded initial-seed anchor:"
            f" initial_seed={format_u16(anchor_seed)}"
            f" compatible_spinda_first_halves={len(targets_by_live_lower_half)}"
            f" compatible_raw_csv_first_halves={len(rows_by_target)}"
            f" selected_targets={len(raw_targets) if config.preserve_raw_csv_targets else len(targets_by_live_lower_half)}"
            f" skipped_other_initial_seed_rows={len(skipped_for_anchor)}"
        )
    else:
        # Report both counts so the operator can see the intentional mismatch
        # between the full raw CSV target space and the de-duplicated live
        # export space caused by FR/LG's `((Random() % 0xFFFE) + 1)` formula.
        unique_seed_groups = len({target.initial_seed for target in raw_targets})
        print(
            "Loaded full firsthalf.csv target space:"
            f" raw_csv_first_halves={len(rows_by_target)}"
            f" unique_live_spinda_first_halves={len(targets_by_live_lower_half)}"
            f" selected_targets={len(raw_targets) if config.preserve_raw_csv_targets else len(targets_by_live_lower_half)}"
            f" unique_initial_seed_groups={unique_seed_groups}"
        )

    targets = raw_targets if config.preserve_raw_csv_targets else list(targets_by_live_lower_half.values())
    targets = order_targets(targets, config.order)
    if config.limit is not None:
        targets = targets[: config.limit]
    return targets


def order_targets(targets: Sequence[FirstHalfTarget], order: str) -> list[FirstHalfTarget]:
    """Return targets in the requested processing order."""

    if order == "target":
        return sorted(targets, key=lambda target: (target.live_lower_half, target.lower_half))
    if order == "seed":
        return sorted(
            targets,
            key=lambda target: (target.initial_seed, target.t_minus_frame, target.live_lower_half, target.lower_half),
        )
    raise ValueError(f"unsupported order: {order!r}")


def group_targets_by_initial_seed(targets: Sequence[FirstHalfTarget]) -> list[tuple[int, list[FirstHalfTarget]]]:
    """Group ordered targets so each CSV initial seed is brute-forced once."""

    grouped: dict[int, list[FirstHalfTarget]] = {}
    for target in targets:
        grouped.setdefault(target.initial_seed, []).append(target)
    return list(grouped.items())


def post_setup_wait_frames(target: FirstHalfTarget, setup_tape: input_tape.InputTape) -> int:
    """Calculate the neutral wait after the setup tape before starting the hit tape."""

    return adjusted_post_setup_wait_frames(target, setup_tape, anchor_frame_offset=0)


def adjusted_post_setup_wait_frames(
    target: FirstHalfTarget,
    setup_tape: input_tape.InputTape,
    *,
    anchor_frame_offset: int,
) -> int:
    """Calculate the neutral wait after setup, accounting for anchor phase.

    CSV frame counts are measured from the initial-seed event. Loaded-state mode
    starts from a later post-seed route anchor, so the route model carries a
    learned frame offset that is subtracted from the planned neutral wait.
    """

    wait_frames = target.t_minus_frame - setup_tape.frame_count - anchor_frame_offset
    if wait_frames < 0:
        raise ValueError(
            f"Setup tape is already past t-18 for {format_u16(target.lower_half)}: "
            f"setup_frames={setup_tape.frame_count}"
            f" t_minus_frame={target.t_minus_frame}"
            f" anchor_frame_offset={anchor_frame_offset}"
        )
    return wait_frames


def load_firsthalf_helper(path: Path):
    """Import the existing `Seed-Bruteforcer.py` proof-of-concept as a helper module."""

    if not path.is_file():
        raise SystemExit(f"Seed-Bruteforcer.py helper not found: {path}")
    spec = importlib.util.spec_from_file_location("spinda_firsthalf_batch_helper", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import helper script: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def qt_mode_enabled() -> bool:
    """Return whether this script is running inside the visible Qt bridge."""

    try:
        from mgba import qt as mgba_qt
    except ImportError:
        return False
    try:
        return bool(mgba_qt.is_available())
    except Exception:
        return False


def parse_args() -> argparse.Namespace:
    """Parse CLI args, ignoring mGBA's own argv when loaded in Qt."""

    parser = build_parser()
    if qt_mode_enabled():
        return parser.parse_args([])
    return parser.parse_args()


def replay_input_tape(core, tape: input_tape.InputTape, *, label: str) -> input_tape.ReplayResult:
    """Replay one tape segment with stale input clearing before and after."""

    print(f"Replaying {label}: frames={tape.frame_count}")
    return input_tape.replay_tape(
        core,
        tape,
        clear_before=True,
        clear_after=True,
        pause_before=True,
        pause_after=True,
        use_batch=True,
        verify_frame_counter=False,
    )


def run_no_input_frames(core, helper, frames: int) -> None:
    """Advance neutral frames through the fastest exact-key path available."""

    if frames <= 0:
        core.set_keys(raw=0)
        return
    run_with_keys = getattr(helper, "run_frames_with_keys", None)
    if callable(run_with_keys):
        run_with_keys(core, 0, frames)
    else:
        core.set_keys(raw=0)
        run_frames = getattr(core, "run_frames", None)
        if callable(run_frames):
            run_frames(frames)
        else:
            for _ in range(frames):
                core.run_frame()
    core.set_keys(raw=0)


def load_base_save(helper, core, config: BatchConfig, *, qt_mode: bool) -> None:
    """Load the canonical first-half base save without mutating the source file."""

    helper.load_required_save_file(core, config.base_save_path, qt_mode=qt_mode, temporary=True)


def build_input_hashes(config: BatchConfig) -> BatchInputHashes:
    """Hash invariant batch inputs once instead of once per generated save."""

    return BatchInputHashes(
        setup_tape_sha1=sha1_file(config.setup_tape_path),
        hit_tape_sha1=sha1_file(config.hit_tape_path),
        base_save_sha1=sha1_file(config.base_save_path),
    )


def prepare_title_baseline(helper, core, config: BatchConfig, paths: BatchPaths, *, qt_mode: bool) -> bool:
    """Boot once to the reusable pre-seed title checkpoint.

    The expensive intro/title navigation should happen once per batch run. The
    rolling checkpoint is then advanced one neutral frame at a time while each
    CSV initial seed is searched.
    """

    load_base_save(helper, core, config, qt_mode=qt_mode)
    helper.boot_to_pre_second_press_checkpoint(core)
    helper.save_state_file(core, paths.baseline_checkpoint_path)
    helper.save_state_file(core, paths.rolling_checkpoint_path)
    return bool(helper.capture_runtime_checkpoint(core))


def reset_rolling_checkpoint_to_baseline(helper, core, paths: BatchPaths, *, qt_mode: bool) -> bool:
    """Restore the title search checkpoint before starting a new seed search."""

    helper.load_state_file(core, paths.baseline_checkpoint_path, qt_mode=qt_mode)
    core.set_keys(raw=0)
    helper.save_state_file(core, paths.rolling_checkpoint_path)
    return bool(helper.capture_runtime_checkpoint(core))


def position_rolling_checkpoint_for_delay(
    helper,
    core,
    paths: BatchPaths,
    *,
    use_runtime_checkpoint: bool,
    current_delay: int,
    target_delay: int,
) -> int:
    """Move the rolling title checkpoint to one requested delay.

    Sequential `delay -> delay + 1` steps keep the original one-frame rolling
    optimization. Larger jumps, including auto-adjustment candidates, rebuild
    from the untouched baseline so the hot checkpoint still matches the exact
    requested delay.
    """

    if target_delay == current_delay:
        return current_delay
    if target_delay == current_delay + 1:
        helper.advance_checkpoint_one_frame(
            core,
            baseline_checkpoint_path=paths.baseline_checkpoint_path,
            checkpoint_path=paths.rolling_checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            next_delay_frames=target_delay,
        )
        return target_delay

    rebuild = getattr(helper, "rebuild_delay_checkpoint", None)
    if callable(rebuild):
        rebuild(
            core,
            baseline_checkpoint_path=paths.baseline_checkpoint_path,
            checkpoint_path=paths.rolling_checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            delay_frames=target_delay,
        )
        return target_delay

    if target_delay < current_delay:
        raise RuntimeError(
            "Title-delay auto-adjustment needed to rewind the rolling checkpoint,"
            " but the helper does not expose rebuild_delay_checkpoint(...)."
        )

    while current_delay < target_delay:
        next_delay = current_delay + 1
        helper.advance_checkpoint_one_frame(
            core,
            baseline_checkpoint_path=paths.baseline_checkpoint_path,
            checkpoint_path=paths.rolling_checkpoint_path,
            use_runtime_checkpoint=use_runtime_checkpoint,
            next_delay_frames=next_delay,
        )
        current_delay = next_delay
    return current_delay


def hit_initial_seed(
    helper,
    core,
    config: BatchConfig,
    paths: BatchPaths,
    *,
    target_seed: int,
    use_runtime_checkpoint: bool,
) -> SeedAnchor:
    """Brute-force one CSV-requested initial seed and save the post-seed state."""

    attempts_checked = 0
    timed_out_branches = 0
    drifted_branches = 0
    observed_by_button: dict[str, deque[InitialSeedObservation]] = defaultdict(lambda: deque(maxlen=2))
    pending_delays: deque[int] = deque([0])
    queued_delays: set[int] = {0}
    visited_delays: set[int] = set()
    current_checkpoint_delay = 0

    while pending_delays:
        delay_frames = pending_delays.popleft()
        queued_delays.discard(delay_frames)
        if delay_frames in visited_delays:
            continue
        if not 0 <= delay_frames <= config.max_delay:
            continue

        # Keep the proven rolling-checkpoint optimization from `Seed-Bruteforcer.py`:
        # adjacent delays still advance one untouched frame at a time, while
        # larger auto-adjustment jumps rebuild from the untouched baseline so
        # the hot checkpoint still matches the requested delay exactly.
        current_checkpoint_delay = position_rolling_checkpoint_for_delay(
            helper,
            core,
            paths,
            use_runtime_checkpoint=use_runtime_checkpoint,
            current_delay=current_checkpoint_delay,
            target_delay=delay_frames,
        )
        visited_delays.add(delay_frames)

        for button_name, button_key in helper.TITLE_INPUT_ATTEMPTS:
            try:
                seed_value, seed_frame, rng_at_seed, observed_button = helper.brute_force_attempt(
                    core=core,
                    baseline_checkpoint_path=paths.baseline_checkpoint_path,
                    checkpoint_path=paths.rolling_checkpoint_path,
                    use_runtime_checkpoint=use_runtime_checkpoint,
                    delay_frames=delay_frames,
                    button_name=button_name,
                    button_key=button_key,
                    seed_timeout=config.seed_timeout,
                    pre_input_checkpoint_path=paths.candidate_checkpoint_path,
                )
            except RuntimeError as exc:
                branch_failure = classify_initial_seed_branch_error(exc)
                if branch_failure is None:
                    raise
                # Long title searches can legitimately produce one bad branch
                # at a usable delay. Record the miss and continue so the other
                # button, nearby delays, and any auto-adjust candidate can
                # still salvage the seed group.
                if branch_failure == "timeout":
                    timed_out_branches += 1
                    print(
                        "Initial seed branch timed out; continuing search:"
                        f" target={format_u16(target_seed)}"
                        f" delay={delay_frames}"
                        f" button={button_name}"
                        f" timeout_frames={config.seed_timeout}"
                        f" total_branch_timeouts={timed_out_branches}"
                    )
                else:
                    drifted_branches += 1
                    print(
                        "Initial seed branch drifted out of the pre-seed title window;"
                        " continuing search:"
                        f" target={format_u16(target_seed)}"
                        f" delay={delay_frames}"
                        f" button={button_name}"
                        f" total_branch_drifts={drifted_branches}"
                        f" detail={exc}"
                    )
                continue
            attempts_checked += 1
            if seed_value == target_seed:
                helper.save_state_file(core, paths.post_seed_state_path)
                print(
                    "Initial seed hit:"
                    f" initial_seed={format_u16(seed_value)}"
                    f" delay={delay_frames}"
                    f" button={observed_button}"
                    f" seed_frame={seed_frame}"
                    f" rng_at_seed={format_u32(rng_at_seed)}"
                    f" saved={paths.post_seed_state_path}"
                )
                return SeedAnchor(
                    mode="csv_bruteforce",
                    initial_seed=target_seed,
                    state_path=paths.post_seed_state_path,
                    delay_frames=delay_frames,
                    button_name=observed_button,
                    seed_frame=seed_frame,
                    rng_at_seed=rng_at_seed,
                )

            observation = InitialSeedObservation(
                delay_frames=delay_frames,
                button_name=observed_button,
                seed_value=seed_value,
                seed_frame=seed_frame,
                rng_at_seed=rng_at_seed,
            )
            history = observed_by_button[observed_button]
            previous_observation = history[-1] if history else None
            history.append(observation)
            candidates = estimate_initial_seed_delay_candidates(
                target_seed,
                observation,
                previous_observation,
                max_delay=config.max_delay,
            )
            # `appendleft()` would reverse the helper's candidate priority if we
            # iterated in the natural order. Reverse the list here so the deque
            # still pops the stronger Timer-1 estimate before any weaker
            # secondary hint such as the bounded LCRNG guess.
            for candidate_delay, reason, detail in reversed(candidates):
                if candidate_delay in visited_delays or candidate_delay in queued_delays:
                    continue
                pending_delays.appendleft(candidate_delay)
                queued_delays.add(candidate_delay)
                print(
                    "Initial seed auto-adjust candidate:"
                    f" target={format_u16(target_seed)}"
                    f" button={observed_button}"
                    f" observed_seed={format_u16(seed_value)}"
                    f" seed_frame={seed_frame}"
                    f" rng_at_seed={format_u32(rng_at_seed)}"
                    f" current_delay={delay_frames}"
                    f" next_delay={candidate_delay}"
                    f" reason={reason}"
                    f" detail={detail}"
                )

            if attempts_checked % max(1, config.progress_every * 2) == 0:
                print(
                    "Initial seed search progress:"
                    f" target_initial_seed={format_u16(target_seed)}"
                    f" attempts={attempts_checked}"
                    f" delay={delay_frames}"
                    f" latest_observed_initial_seed={format_u16(seed_value)}"
                )

        next_linear_delay = delay_frames + 1
        if next_linear_delay <= config.max_delay and next_linear_delay not in visited_delays and next_linear_delay not in queued_delays:
            pending_delays.append(next_linear_delay)
            queued_delays.add(next_linear_delay)

    raise RuntimeError(
        f"Could not brute-force CSV initial seed {format_u16(target_seed)} "
        f"within max_delay={config.max_delay}."
    )


def loaded_state_anchor(config: BatchConfig, initial_seed: int) -> SeedAnchor:
    """Build the seed anchor for explicit loaded-state mode."""

    return SeedAnchor(
        mode="loaded_existing_savestate",
        initial_seed=initial_seed,
        state_path=config.first_half_state_path,
    )


def load_seed_anchor_state(
    helper,
    core,
    config: BatchConfig,
    anchor: SeedAnchor,
    *,
    qt_mode: bool,
) -> None:
    """Load the base save and the post-seed anchor for one lane attempt.

    Loaded-state mode keeps the savestate's organic `gRngValue` exactly as the
    game stored it. The batch reconciles anchor phase through later
    observation/calibration instead of rewriting emulator RAM here.
    """

    load_base_save(helper, core, config, qt_mode=qt_mode)
    helper.load_state_file(core, anchor.state_path, qt_mode=qt_mode)


def seed_anchor_manifest(anchor: SeedAnchor) -> dict[str, object]:
    """Serialize one seed anchor for per-lane manifests."""

    payload: dict[str, object] = {
        "mode": anchor.mode,
        "initial_seed": format_u16(anchor.initial_seed),
        "state_path": str(anchor.state_path),
        "state_sha1": sha1_file(anchor.state_path),
    }
    if anchor.mode == "csv_bruteforce":
        payload.update(
            {
                "delay_frames": anchor.delay_frames,
                "button_name": anchor.button_name,
                "seed_frame": anchor.seed_frame,
                "rng_at_seed": format_u32(anchor.rng_at_seed),
            }
        )
    return payload


def write_target_manifest(
    paths: BatchPaths,
    config: BatchConfig,
    target: FirstHalfTarget,
    *,
    save_path: Path,
    pre_daycare_man_state_path: Path,
    setup_tape: input_tape.InputTape,
    hit_tape: input_tape.InputTape,
    t_minus_reach: TMinusReachResult,
    hit_delay: HitDelayResult,
    seed_anchor_payload: Mapping[str, object],
    input_hashes: BatchInputHashes,
) -> Path:
    """Write one JSON manifest beside the generated first-half save."""

    manifest_path = target_manifest_path(
        paths,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    payload = {
        "schema_version": 1,
        "created_at_utc": _utc_now(),
        # Keep the older `lower_half` key for compatibility with existing
        # tooling, but also write the explicit `spinda_half_live` name so the
        # manifest no longer overloads "half" and "seed" terminology.
        "lower_half": format_u16(target.live_lower_half),
        "spinda_half_live": format_u16(target.live_lower_half),
        "output_key_mode": output_key_mode_name(config.preserve_raw_csv_targets),
        "output_key_half": format_u16(
            output_key_half_for_target(
                target,
                preserve_raw_csv_targets=config.preserve_raw_csv_targets,
            )
        ),
        "save_path": str(save_path),
        "save_sha1": sha1_file(save_path),
        "pre_daycare_man_state_path": str(pre_daycare_man_state_path),
        "pre_daycare_man_state_sha1": sha1_file(pre_daycare_man_state_path),
        "initial_seed": format_u16(target.initial_seed),
        "compatibility_percent": target.compatibility_percent,
        "sweep_index": target.sweep_index,
        "t_minus_frame": target.t_minus_frame,
        "t_minus_rng": format_u32(target.t_minus_rng),
        "t_zero_frame": target.t_zero_frame,
        "t_zero_rng": format_u32(target.t_zero_rng),
        "csv_target_half_raw": format_u16(target.lower_half),
        "expected_daycare_lower_half": format_u16(target.live_lower_half),
        "post_setup_wait_frames": t_minus_reach.post_setup_wait_frames,
        "t_minus_recovered": t_minus_reach.recovered,
        "observed_t_minus_rng_before_recovery": format_u32(t_minus_reach.observed_before_recovery),
        "observed_hit_rng": format_u32(hit_delay.hit_drift.observed_rng),
        "hit_rng_drift_from_csv_t_zero": hit_delay.hit_drift.signed_drift_from_t_zero,
        "target_random_call_offset_from_t_zero": hit_delay.hit_drift.target_call_offset,
        "target_random_call_rng": format_u32(hit_delay.hit_drift.target_call_rng),
        "chosen_hit_prefix_frames": hit_delay.chosen_prefix_frames,
        "observed_daycare_lower_half": format_u16(hit_delay.observed_daycare_lower_half),
        "hit_tape_target_delay": config.hit_tape_target_delay,
        "seed_anchor": dict(seed_anchor_payload),
        "inputs": {
            "firsthalf_csv": str(config.csv_path),
            "setup_tape": str(config.setup_tape_path),
            "setup_tape_sha1": input_hashes.setup_tape_sha1,
            "setup_tape_frames": setup_tape.frame_count,
            "hit_tape": str(config.hit_tape_path),
            "hit_tape_sha1": input_hashes.hit_tape_sha1,
            "hit_tape_frames": hit_tape.frame_count,
            "base_save": str(config.base_save_path),
            "base_save_sha1": input_hashes.base_save_sha1,
            "rom": str(config.rom_path),
        },
    }
    write_json_atomic(manifest_path, payload)
    return manifest_path


def _replay_setup_and_wait_to_expected_t_minus(
    helper,
    core,
    target: FirstHalfTarget,
    setup_tape: input_tape.InputTape,
    *,
    anchor_frame_offset: int,
) -> TMinusReachResult:
    """Replay setup and the planned neutral wait, returning the observed state."""

    replay_input_tape(core, setup_tape, label="seed-to-step setup tape")
    wait_frames = adjusted_post_setup_wait_frames(
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    run_no_input_frames(core, helper, wait_frames)
    observed_rng = read_rng_state(core)
    if observed_rng == target.t_minus_rng:
        return TMinusReachResult(
            post_setup_wait_frames=wait_frames,
            recovered=False,
            anchor_frame_offset=anchor_frame_offset if anchor_frame_offset != 0 else None,
        )
    return TMinusReachResult(
        post_setup_wait_frames=wait_frames,
        recovered=True,
        observed_before_recovery=observed_rng,
    )


def infer_loaded_state_anchor_frame_offset(
    target: FirstHalfTarget,
    observed_rng: int,
    *,
    max_steps: int,
) -> int | None:
    """Infer the loaded-state anchor frame offset from one observed miss.

    When the post-seed route anchor is already some frames past the CSV's
    initial-seed origin, the nominal wait lands on a later PRNG state on the
    same organic route orbit. A bounded signed distance gives the missing frame
    offset directly, which lets the batch tighten the route model instead of
    scanning every target from scratch.

    In other words: the anchor is trusted for world state and live RNG, while
    the CSV is trusted for the expected checkpoint state. This helper estimates
    the frame offset between them without rewriting RAM.
    """

    return signed_lcrng_distance(target.t_minus_rng, observed_rng, max_steps)


def t_minus_recovery_search_limit(
    config: BatchConfig,
    target: FirstHalfTarget,
    setup_tape: input_tape.InputTape,
    *,
    anchor_frame_offset: int,
) -> int:
    """Return a drift-search bound large enough to cover the planned route span.

    `t_minus_recovery_window` is still the local slop around the expected
    checkpoint, but a loaded-state anchor can have an unknown organic frame
    offset larger than that. The search therefore covers the planned
    post-setup wait plus the local window instead of scanning only a tiny slice
    immediately after the setup tape.
    """

    planned_wait = adjusted_post_setup_wait_frames(
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    return max(config.t_minus_recovery_window, planned_wait + config.t_minus_recovery_window)


def reach_t_minus_state(
    helper,
    core,
    config: BatchConfig,
    target: FirstHalfTarget,
    *,
    seed_anchor: SeedAnchor,
    setup_tape: input_tape.InputTape,
    qt_mode: bool,
    anchor_frame_offset: int = 0,
) -> TMinusReachResult:
    """Reach the CSV `t-18` PRNG state, reloading and scanning if necessary."""

    first_try = _replay_setup_and_wait_to_expected_t_minus(
        helper,
        core,
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    if not first_try.recovered:
        return first_try

    print(
        "Warning: expected t-18 PRNG was not at the planned frame;"
        " reloading the current post-seed anchor and scanning forward."
        f" target={format_u16(target.lower_half)}"
        f" expected={format_u32(target.t_minus_rng)}"
        f" observed={format_u32(first_try.observed_before_recovery)}"
    )
    recovery_search_limit = t_minus_recovery_search_limit(
        config,
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    if (
        seed_anchor.mode == "loaded_existing_savestate"
        and first_try.observed_before_recovery is not None
    ):
        inferred_offset = infer_loaded_state_anchor_frame_offset(
            target,
            first_try.observed_before_recovery,
            max_steps=recovery_search_limit,
        )
        if inferred_offset is not None and inferred_offset != anchor_frame_offset:
            corrected_wait = adjusted_post_setup_wait_frames(
                target,
                setup_tape,
                anchor_frame_offset=inferred_offset,
            )
            print(
                "Calibrated loaded-state route frame offset:"
                f" target={format_u16(target.lower_half)}"
                f" previous_offset={anchor_frame_offset:+d}"
                f" calibrated_offset={inferred_offset:+d}"
                f" corrected_post_setup_wait={corrected_wait}"
            )
            load_seed_anchor_state(helper, core, config, seed_anchor, qt_mode=qt_mode)
            replay_input_tape(core, setup_tape, label="seed-to-step setup tape recalibrated")
            run_no_input_frames(core, helper, corrected_wait)
            recalibrated_rng = read_rng_state(core)
            if recalibrated_rng == target.t_minus_rng:
                print(
                    "Reached t-18 with calibrated loaded-state route model:"
                    f" target={format_u16(target.lower_half)}"
                    f" setup_plus_wait_frames={setup_tape.frame_count + corrected_wait}"
                )
                return TMinusReachResult(
                    post_setup_wait_frames=corrected_wait,
                    recovered=True,
                    observed_before_recovery=first_try.observed_before_recovery,
                    anchor_frame_offset=inferred_offset,
                )
            print(
                "Warning: calibrated loaded-state offset did not land on t-18;"
                f" target={format_u16(target.lower_half)}"
                f" calibrated_offset={inferred_offset:+d}"
                f" observed={format_u32(recalibrated_rng)}"
                " falling back to forward scan."
            )
    load_seed_anchor_state(helper, core, config, seed_anchor, qt_mode=qt_mode)
    replay_input_tape(core, setup_tape, label="seed-to-step setup tape recovery")

    for extra_frames in range(recovery_search_limit + 1):
        observed_rng = read_rng_state(core)
        if observed_rng == target.t_minus_rng:
            print(
                "Recovered t-18 PRNG state:"
                f" target={format_u16(target.lower_half)}"
                f" setup_plus_extra_frames={setup_tape.frame_count + extra_frames}"
                f" extra_frames={extra_frames}"
            )
            learned_anchor_frame_offset = None
            if seed_anchor.mode == "loaded_existing_savestate":
                learned_anchor_frame_offset = target.t_minus_frame - (
                    setup_tape.frame_count + extra_frames
                )
            return TMinusReachResult(
                post_setup_wait_frames=extra_frames,
                recovered=True,
                observed_before_recovery=first_try.observed_before_recovery,
                anchor_frame_offset=learned_anchor_frame_offset,
            )
        if extra_frames == recovery_search_limit:
            break
        run_no_input_frames(core, helper, 1)

    raise RuntimeError(
        f"Could not recover t-18 PRNG for {format_u16(target.lower_half)} within "
        f"{recovery_search_limit} frames after reloading the post-seed anchor. "
        f"local_window={config.t_minus_recovery_window} "
        f"target_rng={format_u32(target.t_minus_rng)}"
    )


def advance_runway_to_t_minus(
    helper,
    core,
    config: BatchConfig,
    paths: BatchPaths,
    target: FirstHalfTarget,
    *,
    seed_anchor: SeedAnchor,
    setup_tape: input_tape.InputTape,
    post_setup_checkpoint: RuntimeCheckpoint,
    qt_mode: bool,
    csv_frame_cursor: int,
    anchor_frame_offset: int,
) -> tuple[TMinusReachResult, int, int]:
    """Advance the shared route runway to one target's validated `t-18`.

    The fast path moves forward from the previous target's `t-18` checkpoint.
    If the observed PRNG shows drift, the function restores the durable
    post-setup checkpoint, recalibrates the loaded-state frame offset, and only
    returns once the expected CSV `t-18` state is proven.
    """

    wait_frames = target.t_minus_frame - csv_frame_cursor
    if wait_frames < 0:
        restore_runtime_state(helper, core, post_setup_checkpoint, qt_mode=qt_mode)
        wait_frames = adjusted_post_setup_wait_frames(
            target,
            setup_tape,
            anchor_frame_offset=anchor_frame_offset,
        )
    run_no_input_frames(core, helper, wait_frames)
    observed_rng = read_rng_state(core)
    post_setup_wait = adjusted_post_setup_wait_frames(
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    if observed_rng == target.t_minus_rng:
        return (
            TMinusReachResult(
                post_setup_wait_frames=post_setup_wait,
                recovered=False,
                anchor_frame_offset=anchor_frame_offset if anchor_frame_offset != 0 else None,
            ),
            target.t_minus_frame,
            anchor_frame_offset,
        )

    print(
        "Warning: optimized runway missed t-18; restoring preventative checkpoint."
        f" target={format_u16(target.lower_half)}"
        f" expected={format_u32(target.t_minus_rng)}"
        f" observed={format_u32(observed_rng)}"
    )
    recovery_search_limit = t_minus_recovery_search_limit(
        config,
        target,
        setup_tape,
        anchor_frame_offset=anchor_frame_offset,
    )
    drift_delta = signed_lcrng_distance(
        target.t_minus_rng,
        observed_rng,
        recovery_search_limit,
    )
    if drift_delta is not None:
        calibrated_offset = anchor_frame_offset + drift_delta
        corrected_wait = adjusted_post_setup_wait_frames(
            target,
            setup_tape,
            anchor_frame_offset=calibrated_offset,
        )
        restore_runtime_state(helper, core, post_setup_checkpoint, qt_mode=qt_mode)
        run_no_input_frames(core, helper, corrected_wait)
        recalibrated_rng = read_rng_state(core)
        if recalibrated_rng == target.t_minus_rng:
            print(
                "Recovered optimized runway with calibrated frame offset:"
                f" target={format_u16(target.lower_half)}"
                f" previous_offset={anchor_frame_offset:+d}"
                f" calibrated_offset={calibrated_offset:+d}"
                f" corrected_post_setup_wait={corrected_wait}"
            )
            return (
                TMinusReachResult(
                    post_setup_wait_frames=corrected_wait,
                    recovered=True,
                    observed_before_recovery=observed_rng,
                    anchor_frame_offset=calibrated_offset,
                ),
                target.t_minus_frame,
                calibrated_offset,
            )

    # Last-resort recovery keeps correctness over speed. It reuses the older
    # anchor reload/scan path, then the caller captures a new target checkpoint
    # before branching so the forward runway remains safe.
    load_seed_anchor_state(helper, core, config, seed_anchor, qt_mode=qt_mode)
    recovered = reach_t_minus_state(
        helper,
        core,
        config,
        target,
        seed_anchor=seed_anchor,
        setup_tape=setup_tape,
        qt_mode=qt_mode,
        anchor_frame_offset=anchor_frame_offset,
    )
    return (
        recovered,
        target.t_minus_frame,
        recovered.anchor_frame_offset
        if recovered.anchor_frame_offset is not None
        else anchor_frame_offset,
    )


def process_target_from_reached_t_minus(
    helper,
    core,
    config: BatchConfig,
    paths: BatchPaths,
    target: FirstHalfTarget,
    *,
    t_minus_reach: TMinusReachResult,
    seed_anchor_payload: Mapping[str, object],
    input_hashes: BatchInputHashes,
    setup_tape: input_tape.InputTape,
    full_hit_tape: input_tape.InputTape,
    hit_delay_variants: Sequence[HitDelayVariant],
    qt_mode: bool,
    pre_hit_checkpoint: RuntimeCheckpoint | None = None,
) -> TargetResult:
    """Branch from a verified `t-18` point and export the target artifacts.

    The caller must already have advanced the core to the target's validated
    pre-hit PRNG state. This split lets the optimized loaded-state runway reuse
    one forward route and one preventative checkpoint per target instead of
    reloading the original post-seed anchor for every CSV row.
    """

    save_path = target_save_path(
        paths.output_dir,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    pre_daycare_man_state_path = target_pre_daycare_man_state_path(
        paths.output_dir,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    hit_delay, hit_tape_suffix = search_hit_delay_from_checkpoint(
        helper,
        core,
        config,
        target,
        full_hit_tape=full_hit_tape,
        hit_delay_variants=hit_delay_variants,
        scratch_checkpoint_path=paths.hit_delay_checkpoint_path,
        pre_hit_checkpoint=pre_hit_checkpoint,
        qt_mode=qt_mode,
    )
    expected_daycare_lower_half = target.live_lower_half

    # At this point the target lower PID half is already present in daycare RAM.
    # This preventative state is the fastest safe rollback point for manual
    # inspection or for rebuilding the later daycare-man/save suffix.
    pre_daycare_man_state_path.parent.mkdir(parents=True, exist_ok=True)
    helper.save_state_file(core, pre_daycare_man_state_path)
    print(f"Saved pre-daycare-man first-half state: {pre_daycare_man_state_path}")

    replay_input_tape(core, hit_tape_suffix, label="hit tape suffix walk/save")
    final_lower_half = read_daycare_lower_half(core)
    if final_lower_half != expected_daycare_lower_half:
        raise RuntimeError(
            f"Daycare lower-half drifted before export: csv_target={format_u16(target.lower_half)} "
            f"expected_live={format_u16(expected_daycare_lower_half)} "
            f"observed={format_u16(final_lower_half)}"
        )

    save_preexisted = save_path.exists()
    artifact_status = classify_target_artifacts(paths, config, target)
    preserve_existing_save = save_preexisted and not config.overwrite and artifact_status.complete
    if preserve_existing_save:
        print(f"Preserved existing first-half save after writing matching state: {save_path}")
    else:
        export_save_file(core, save_path)
    written_manifest = write_target_manifest(
        paths,
        config,
        target,
        save_path=save_path,
        pre_daycare_man_state_path=pre_daycare_man_state_path,
        setup_tape=setup_tape,
        hit_tape=full_hit_tape,
        t_minus_reach=t_minus_reach,
        hit_delay=hit_delay,
        seed_anchor_payload=seed_anchor_payload,
        input_hashes=input_hashes,
    )
    status = "state-exported-existing-save" if preserve_existing_save else "exported"
    if status == "exported":
        print(f"Exported first-half save: {save_path}")
    return TargetResult(
        target.live_lower_half,
        status,
        save_path,
        written_manifest,
        csv_target_half_raw=target.lower_half,
        spinda_half_live=target.live_lower_half,
        pre_daycare_man_state_path=pre_daycare_man_state_path,
    )


def process_target_from_anchor(
    helper,
    core,
    config: BatchConfig,
    paths: BatchPaths,
    target: FirstHalfTarget,
    *,
    seed_anchor: SeedAnchor,
    seed_anchor_payload: Mapping[str, object],
    input_hashes: BatchInputHashes,
    setup_tape: input_tape.InputTape,
    full_hit_tape: input_tape.InputTape,
    hit_delay_variants: Sequence[HitDelayVariant],
    qt_mode: bool,
    anchor_frame_offset: int = 0,
) -> tuple[TargetResult, int | None]:
    """Generate one `1sthalves/0x####.sav` from one post-seed anchor state."""

    save_path = target_save_path(
        paths.output_dir,
        target,
        preserve_raw_csv_targets=config.preserve_raw_csv_targets,
    )
    artifact_status = classify_target_artifacts(paths, config, target)
    if artifact_status.complete:
        print(f"Skipping existing first-half save: {save_path}")
        return (
            build_target_result_from_artifacts(
                target,
                artifact_status,
                status="skipped-existing",
            ),
            None,
        )

    t_minus_reach = reach_t_minus_state(
        helper,
        core,
        config,
        target,
        seed_anchor=seed_anchor,
        setup_tape=setup_tape,
        qt_mode=qt_mode,
        anchor_frame_offset=anchor_frame_offset,
    )

    return (
        process_target_from_reached_t_minus(
            helper,
            core,
            config,
            paths,
            target,
            t_minus_reach=t_minus_reach,
            seed_anchor_payload=seed_anchor_payload,
            input_hashes=input_hashes,
            setup_tape=setup_tape,
            full_hit_tape=full_hit_tape,
            hit_delay_variants=hit_delay_variants,
            qt_mode=qt_mode,
        ),
        t_minus_reach.anchor_frame_offset,
    )


def should_write_status(config: BatchConfig, completed: int, completion_goal: int) -> bool:
    """Throttle status writes while still writing the last completed target."""

    return completed == completion_goal or completed % config.status_every == 0


def batch_status_payload(
    config: BatchConfig,
    target: FirstHalfTarget,
    result: TargetResult,
    *,
    seed_anchor: SeedAnchor,
    seed_anchor_payload: Mapping[str, object],
    completed: int,
    total_targets_loaded: int,
) -> dict[str, object]:
    """Build the resumability payload shared by normal and optimized sweeps."""

    return {
        "updated_at_utc": _utc_now(),
        "seed_mode": config.seed_mode,
        "completed_this_run": completed,
        # Preserve the original short keys for compatibility, but mirror them
        # into explicit operator-facing names so tooling can distinguish the
        # initial seed from the Spinda first-half value.
        "last_lower_half": format_u16(target.live_lower_half),
        "last_spinda_half_live": format_u16(target.live_lower_half),
        "last_csv_target_half_raw": format_u16(target.lower_half),
        "output_key_mode": output_key_mode_name(config.preserve_raw_csv_targets),
        "last_output_key_half": format_u16(
            output_key_half_for_target(
                target,
                preserve_raw_csv_targets=config.preserve_raw_csv_targets,
            )
        ),
        "last_status": result.status,
        "last_save": str(result.save_path),
        "last_pre_daycare_man_state": str(result.pre_daycare_man_state_path)
        if result.pre_daycare_man_state_path is not None
        else None,
        "last_seed": format_u16(seed_anchor.initial_seed),
        "last_initial_seed": format_u16(seed_anchor.initial_seed),
        "last_seed_anchor": seed_anchor_payload,
        "total_targets_loaded": total_targets_loaded,
    }


def write_batch_status(paths: BatchPaths, payload: Mapping[str, object]) -> None:
    """Persist a small resumability status file after each exported target."""

    write_json_atomic(paths.status_path, dict(payload))


def process_loaded_state_targets_with_runway(
    helper,
    core,
    config: BatchConfig,
    paths: BatchPaths,
    targets: Sequence[FirstHalfTarget],
    *,
    seed_anchor: SeedAnchor,
    seed_anchor_payload: Mapping[str, object],
    input_hashes: BatchInputHashes,
    setup_tape: input_tape.InputTape,
    full_hit_tape: input_tape.InputTape,
    hit_delay_variants: Sequence[HitDelayVariant],
    qt_mode: bool,
    completed: int,
    completion_goal: int,
    total_targets_loaded: int,
) -> tuple[list[TargetResult], int]:
    """Process one loaded-state group with a forward-only checkpointed runway.

    All targets in the current loaded-state lane share the same post-seed
    anchor and setup tape. Sorting them by CSV `t-18` frame lets the script
    advance neutral frames once across the lane. Each target still gets a
    preventative `t-18` checkpoint before the hit branch, so drift or export
    work cannot poison the runway for the next target.
    """

    route_targets = sorted(targets, key=target_route_order_key)
    load_seed_anchor_state(helper, core, config, seed_anchor, qt_mode=qt_mode)
    replay_input_tape(core, setup_tape, label="seed-to-step setup tape runway")
    post_setup_checkpoint = capture_post_setup_runway_checkpoint(helper, core, paths)
    csv_frame_cursor = setup_tape.frame_count
    anchor_frame_offset = 0
    results: list[TargetResult] = []

    for target in route_targets:
        print(
            "Processing Spinda first-half target:"
            f" spinda_half_live={format_u16(target.live_lower_half)}"
            f" spinda_half_csv_raw={format_u16(target.lower_half)}"
            f" initial_seed={format_u16(target.initial_seed)}"
            f" route_order=t-18:{target.t_minus_frame}"
        )
        t_minus_reach, csv_frame_cursor, anchor_frame_offset = advance_runway_to_t_minus(
            helper,
            core,
            config,
            paths,
            target,
            seed_anchor=seed_anchor,
            setup_tape=setup_tape,
            post_setup_checkpoint=post_setup_checkpoint,
            qt_mode=qt_mode,
            csv_frame_cursor=csv_frame_cursor,
            anchor_frame_offset=anchor_frame_offset,
        )
        pre_hit_checkpoint = capture_runtime_state(
            helper,
            core,
            scratch_path=paths.hit_delay_checkpoint_path,
        )
        result = process_target_from_reached_t_minus(
            helper,
            core,
            config,
            paths,
            target,
            t_minus_reach=t_minus_reach,
            seed_anchor_payload=seed_anchor_payload,
            input_hashes=input_hashes,
            setup_tape=setup_tape,
            full_hit_tape=full_hit_tape,
            hit_delay_variants=hit_delay_variants,
            qt_mode=qt_mode,
            pre_hit_checkpoint=pre_hit_checkpoint,
        )
        # Restore the validated `t-18` checkpoint after the export branch so
        # the next target advances from the clean route runway, not from the
        # daycare-man/save suffix.
        restore_runtime_state(helper, core, pre_hit_checkpoint, qt_mode=qt_mode)
        results.append(result)
        completed += 1
        if should_write_status(config, completed, completion_goal):
            write_batch_status(
                paths,
                batch_status_payload(
                    config,
                    target,
                    result,
                    seed_anchor=seed_anchor,
                    seed_anchor_payload=seed_anchor_payload,
                    completed=completed,
                    total_targets_loaded=total_targets_loaded,
                ),
            )
        if completed % config.progress_every == 0:
            print(f"Progress: completed_or_exported={completed} total_loaded={total_targets_loaded}")

    return results, completed


def run_batch(config: BatchConfig) -> list[TargetResult]:
    """Run the batch generation workflow."""

    paths = batch_paths(config.output_dir)
    ensure_batch_dirs(paths)
    validate_loaded_state_anchor(config)
    setup_tape = load_tape(config.setup_tape_path)
    full_hit_tape = load_tape(config.hit_tape_path)
    hit_delay_variants = build_hit_delay_variants(
        full_hit_tape,
        base_delay=config.hit_tape_target_delay,
        radius=config.hit_delay_search_radius,
    )
    anchor_seed = resolve_state_initial_seed(config) if config.seed_mode == "loaded-state" else None
    if config.seed_mode == "loaded-state" and anchor_seed is None:
        raise SystemExit(
            "--seed-mode loaded-state requires --state-initial-seed or readable "
            "--first-half-metadata with target_seed."
        )
    targets = load_first_half_targets(config, anchor_seed=anchor_seed)

    print(f"Loaded Spinda first-half targets: {len(targets)}")
    print(f"Output folder: {paths.output_dir}")
    print(f"Seed mode: {config.seed_mode}")
    print(f"Output key mode: {output_key_mode_name(config.preserve_raw_csv_targets)}")
    if config.seed_mode == "loaded-state":
        print(f"Loaded post-seed anchor state: {config.first_half_state_path}")
        print(f"Loaded initial seed: {format_u16(anchor_seed)}")
    else:
        print(f"Title baseline state: {paths.baseline_checkpoint_path}")
        print(f"Rolling title state: {paths.rolling_checkpoint_path}")
        print(f"Current post-seed state: {paths.post_seed_state_path}")
    print(f"Setup tape frames: {setup_tape.frame_count}")
    print(f"Hit tape frames: {full_hit_tape.frame_count}")
    print(f"Hit tape target delay: {config.hit_tape_target_delay}")
    print(f"Hit delay search radius: +/-{config.hit_delay_search_radius}")
    print(f"Precomputed hit-delay variants: {len(hit_delay_variants)}")
    if not targets:
        print("No matching first-half targets; no emulator work was performed.")
        return []
    skipped_targets, pending_targets, resume_reasons = scan_resume_targets(paths, config, targets)
    write_resume_scan_status(
        paths,
        config,
        total_targets_loaded=len(targets),
        skipped_existing=len(skipped_targets),
        pending_targets=len(pending_targets),
        reason_counts=resume_reasons,
    )
    print(
        "Resume scan:"
        f" complete_existing={len(skipped_targets)}"
        f" pending={len(pending_targets)}"
        f" reasons={dict(sorted(resume_reasons.items()))}"
    )
    if not pending_targets and not config.dry_run:
        print("All requested first-half targets already have complete resume artifacts.")
        return skipped_targets
    if config.dry_run:
        for target in pending_targets[:10]:
            print(
                "Plan:"
                f" spinda_half_live={format_u16(target.live_lower_half)}"
                f" spinda_half_csv_raw={format_u16(target.lower_half)}"
                f" initial_seed={format_u16(target.initial_seed)}"
                f" t-18-frame={target.t_minus_frame}"
                f" post-setup-wait={post_setup_wait_frames(target, setup_tape)}"
                f" expected_daycare_half={format_u16(target.live_lower_half)}"
                f" save={target_save_path(paths.output_dir, target, preserve_raw_csv_targets=config.preserve_raw_csv_targets)}"
                f" pre_daycare_man_state={target_pre_daycare_man_state_path(paths.output_dir, target, preserve_raw_csv_targets=config.preserve_raw_csv_targets)}"
            )
        print("Dry run only; no emulator work was performed.")
        return []

    required_paths = [config.rom_path, config.base_save_path, config.firsthalf_script_path]
    if config.seed_mode == "loaded-state":
        required_paths.append(config.first_half_state_path)
    for required_path in required_paths:
        if not required_path.is_file():
            raise SystemExit(f"Required input file not found: {required_path}")
    input_hashes = build_input_hashes(config)

    helper = load_firsthalf_helper(config.firsthalf_script_path)
    mgba_dir = helper.resolve_mgba_dir()
    qt_mode = helper._qt_mode_enabled()
    core = helper.load_runtime_core(config.rom_path)
    helper.ensure_audio_killswitch_defaults(mgba_dir)
    helper.ensure_no_render_defaults(mgba_dir)
    helper.ensure_fast_forward_defaults(mgba_dir)
    helper.ensure_live_audio_killswitch(core, qt_mode=qt_mode)
    helper.ensure_live_no_render_mode(core, qt_mode=qt_mode)
    helper.ensure_live_unbounded_fast_forward(core, qt_mode=qt_mode)

    results: list[TargetResult] = list(skipped_targets)
    completed = 0
    if config.seed_mode == "csv-bruteforce":
        use_runtime_checkpoint = prepare_title_baseline(helper, core, config, paths, qt_mode=qt_mode)
        groups = group_targets_by_initial_seed(pending_targets)
    else:
        assert anchor_seed is not None
        use_runtime_checkpoint = False
        groups = [(anchor_seed, pending_targets)]

    for group_seed, group_targets in groups:
        loaded_state_anchor_frame_offset = 0
        if not group_targets:
            continue

        if config.seed_mode == "csv-bruteforce":
            print(
                "Brute-forcing CSV initial seed:"
                f" initial_seed={format_u16(group_seed)}"
                f" targets_in_group={len(group_targets)}"
                f" pending={len(group_targets)}"
            )
            use_runtime_checkpoint = reset_rolling_checkpoint_to_baseline(
                helper,
                core,
                paths,
                qt_mode=qt_mode,
            )
            seed_anchor = hit_initial_seed(
                helper,
                core,
                config,
                paths,
                target_seed=group_seed,
                use_runtime_checkpoint=use_runtime_checkpoint,
            )
        else:
            assert anchor_seed is not None
            seed_anchor = loaded_state_anchor(config, anchor_seed)
        # Hash the seed-anchor state once per seed group. In csv-bruteforce mode
        # this file is overwritten for the next seed, so a global path cache
        # would be wrong; group-local caching is both fast and accurate.
        seed_anchor_payload = seed_anchor_manifest(seed_anchor)

        if config.seed_mode == "loaded-state":
            group_results, completed = process_loaded_state_targets_with_runway(
                helper,
                core,
                config,
                paths,
                group_targets,
                seed_anchor=seed_anchor,
                seed_anchor_payload=seed_anchor_payload,
                input_hashes=input_hashes,
                setup_tape=setup_tape,
                full_hit_tape=full_hit_tape,
                hit_delay_variants=hit_delay_variants,
                qt_mode=qt_mode,
                completed=completed,
                completion_goal=completed + len(group_targets),
                total_targets_loaded=len(targets),
            )
            results.extend(group_results)
            continue

        group_completion_goal = completed + len(group_targets)
        for target in group_targets:
            print(
                "Processing Spinda first-half target:"
                f" spinda_half_live={format_u16(target.live_lower_half)}"
                f" spinda_half_csv_raw={format_u16(target.lower_half)}"
                f" initial_seed={format_u16(target.initial_seed)}"
            )
            load_seed_anchor_state(helper, core, config, seed_anchor, qt_mode=qt_mode)
            result, learned_anchor_frame_offset = process_target_from_anchor(
                helper,
                core,
                config,
                paths,
                target,
                seed_anchor=seed_anchor,
                seed_anchor_payload=seed_anchor_payload,
                input_hashes=input_hashes,
                setup_tape=setup_tape,
                full_hit_tape=full_hit_tape,
                hit_delay_variants=hit_delay_variants,
                qt_mode=qt_mode,
                anchor_frame_offset=loaded_state_anchor_frame_offset,
            )
            if (
                config.seed_mode == "loaded-state"
                and learned_anchor_frame_offset is not None
                and learned_anchor_frame_offset != loaded_state_anchor_frame_offset
            ):
                print(
                    "Reusing calibrated loaded-state route frame offset:"
                    f" initial_seed={format_u16(group_seed)}"
                    f" offset={learned_anchor_frame_offset:+d}"
                )
                loaded_state_anchor_frame_offset = learned_anchor_frame_offset
            results.append(result)
            completed += 1
            if should_write_status(config, completed, group_completion_goal):
                write_batch_status(
                    paths,
                    batch_status_payload(
                        config,
                        target,
                        result,
                        seed_anchor=seed_anchor,
                        seed_anchor_payload=seed_anchor_payload,
                        completed=completed,
                        total_targets_loaded=len(targets),
                    ),
                )
            if completed % config.progress_every == 0:
                print(f"Progress: completed_or_exported={completed} total_loaded={len(targets)}")

    print(f"Batch finished. Results recorded: {len(results)}")
    return results


def main() -> int:
    """CLI entrypoint."""

    config = normalize_config(parse_args())
    run_batch(config)
    return 0


if __name__ == "__main__":
    exit_code = main()
    # The visible Qt Python runner reports any SystemExit as an error, even
    # when the exit code is 0. Only raise for real failures so successful
    # in-emulator runs finish quietly after exporting their saves.
    if exit_code:
        raise SystemExit(exit_code)
