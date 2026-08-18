#!/usr/bin/env python3
r"""Build one FR/LG save per shiny value by targeting Secret IDs.

This is a new SID bot, separate from the older TSV save-bank builder. It is
designed for the route where TID ``0`` has already been hit and the emulator is
paused at the last input before ``InitPlayerTrainerId()`` consumes SID RNG. If
the loaded state is the earlier TID-hit checkpoint instead, live mode can tap
forward until it proves the real pre-SID branch, then restore that branch
before any ledger or save export happens.

Default route tape:

    <repo-root>\SiD_RNG_After.json

The tape is played after each desired SID is hit. It should carry the game to
the first-starter point where Spinda eggs can later be injected and hatched.

The bot does not brute-force emulator attempts. It reads the live pre-SID
``gRngValue``, predicts SID hits with the GBA LCRNG, branches once per missing
shiny value, exports a save, and writes a ledger mapping TID 0 shiny values to
the actual SIDs hit. The live loop reuses the forecast after ordinary hits and
rebuilds it only when the branch RNG or calibrated SID commit offset changes.
Before the final input, it runs a configurable neutral edge window so FR/LG can
see a fresh ``A`` press. In Qt live mode, it enables audio killswitch,
no-render mode, and unbounded fast-forward when those bridge hooks exist.

The root ``tid 0 ready.ss0`` state needs ``--sid-commit-offset 273`` plus
``--min-wait-frames 1``. The minimum wait skips the fake linear wait-0 point
while preserving the live release-edge behavior where waits 0 and 1 hit the
same SID.

When the read-only TID hit state resolves to an RNG-frozen pre-SID branch, the
fallback schedule is route-cycle based. It records which TSVs the repeated-A
setup can actually reach from that loaded state and stops before any tape/save
work if the requested TSV set is not fully reachable.

TID 0 is a read-only contract for this workflow: the bot reads/verifies the
pre-SID mirror and final SaveBlock2 IDs but never writes trainer ID memory.
After the route tape finishes, the only game artifact written is the battery
save ``.sav`` export. This bot intentionally does not write savestates.

If a post-tape export repeatedly fails the Bulbasaur save proof for one SID,
the live loop predicts the next later SID hit for the same shiny value and
replays the route from the clean pre-SID branch before blocking that TSV.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable


SCRIPT_DIR = Path(__file__).resolve().parent
PYTHON_EXAMPLES_DIR = SCRIPT_DIR.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
if str(PYTHON_EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(PYTHON_EXAMPLES_DIR))

import frlg_id_bot_common as common  # noqa: E402
import input_tape  # noqa: E402


DEFAULT_TARGET_TID = 0
DEFAULT_AFTER_TAPE = Path(__file__).resolve().parents[3] / "SiD_RNG_After.json"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "TSVs"
DEFAULT_TID_HIT_STATE = Path(__file__).resolve().parents[3] / "FRLGIDBots" / "TrainerID" / "tid-0x0000-hit.ss0"
DEFAULT_LEDGER_NAME = "_sid_shiny_value_ledger_tid_0x{tid:04X}.json"
DEFAULT_FORECAST_NAME = "_sid_shiny_value_forecast_tid_0x{tid:04X}.json"
DEFAULT_SID_COVERAGE_NAME = "_sid_raw_lcrng_coverage_tid_0x{tid:04X}.json"
DEFAULT_SCHEDULE_NAME = "_sid_tsv_earliest_schedule_tid_0x{tid:04X}.json"
DEFAULT_SAVE_NAME = "TSV-{shiny_value:04d}-sid-{sid:05d}.sav"
DEFAULT_STATUS_LOG_NAME = "_sid_live_status.log"
DEFAULT_POST_COMMIT_VERIFY_FRAMES = 360
SID_COVERAGE_CHUNK_FRAMES = 1_000_000
SID_COVERAGE_TARGET_COUNT = 1 << 16
TID_STATE_SETUP_NEUTRAL_FRAMES = 18
TID_STATE_SETUP_MAX_TAPS = 512
SID_COMMIT_OFFSET_SEARCH_LIMIT = 512
TID_STATE_SCAN_LEDGER_INTERVAL = 100
EXPORT_VALIDATION_RETRY_SETTLE_FRAMES = (0, 30, 90, 180, 360)
EXPORT_VALIDATION_ALTERNATE_HIT_LIMIT = 8


@dataclass(frozen=True)
class SecretIdConfig:
    """Runtime configuration for one SID shiny-value save run."""

    output_dir: Path
    after_tape: Path
    target_tid: int
    start_rng: int | None
    sid_commit_offset: int
    rng_advances_per_neutral_frame: int
    final_button: str
    final_press_frames: int
    post_commit_verify_frames: int
    calibration_search_radius: int
    max_advances: int | None
    only_shiny_values: tuple[int, ...]
    limit: int | None
    resume: bool
    overwrite: bool
    trust_predicted_sid: bool
    strict_prediction: bool
    dry_plan: bool
    final_pre_neutral_frames: int = 0
    min_wait_frames: int = 0

    @property
    def ledger_path(self) -> Path:
        return self.output_dir / DEFAULT_LEDGER_NAME.format(tid=self.target_tid)

    @property
    def forecast_path(self) -> Path:
        return self.output_dir / DEFAULT_FORECAST_NAME.format(tid=self.target_tid)

    @property
    def sid_coverage_path(self) -> Path:
        return self.output_dir / DEFAULT_SID_COVERAGE_NAME.format(tid=self.target_tid)

    @property
    def schedule_path(self) -> Path:
        return self.output_dir / DEFAULT_SCHEDULE_NAME.format(tid=self.target_tid)


@dataclass(frozen=True)
class PreSidBranchInfo:
    """Observed branch reached from the read-only TID hit state."""

    branch_rng: int
    observed_sid: int
    sid_commit_offset: int
    tap_index: int


class FrozenPreSidBranchError(RuntimeError):
    """Raised when the exact pre-SID final input cannot be delayed for RNG."""

    def __init__(self, *, branch_rng: int, sid: int, sid_commit_offset: int) -> None:
        super().__init__(
            "pre-SID branch is RNG-frozen; switching to TID-state scan mode "
            f"branch_rng={common.format_u32(branch_rng)} sid={common.format_u16(sid)}"
        )
        self.branch_rng = branch_rng
        self.sid = sid
        self.sid_commit_offset = sid_commit_offset


class SaveExportValidationError(RuntimeError):
    """Raised when a post-tape battery export never validates after retries."""


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--after-tape",
        type=Path,
        default=DEFAULT_AFTER_TAPE,
        help="Anchor-free input tape replayed after SID commit.",
    )
    parser.add_argument(
        "--target-tid",
        type=common.parse_int,
        default=DEFAULT_TARGET_TID,
        help="Read-only TID verification target. This workflow is fixed at 0.",
    )
    parser.add_argument(
        "--start-rng",
        type=common.parse_int,
        default=None,
        help="Pre-SID gRngValue. If omitted in live mode, read from core memory.",
    )
    parser.add_argument("--sid-commit-offset", type=int, default=1)
    parser.add_argument("--rng-advances-per-neutral-frame", type=int, default=1)
    parser.add_argument(
        "--min-wait-frames",
        type=int,
        default=0,
        help=(
            "Smallest neutral wait the forecast may schedule. Use 1 for the "
            "root tid 0 ready state because wait 0 and wait 1 share the same "
            "release-edge RNG state."
        ),
    )
    parser.add_argument("--final-button", default="A")
    parser.add_argument(
        "--final-press-frames",
        type=int,
        default=2,
        help="Frames to hold the final input. Defaults to 2 for edge-triggered menus.",
    )
    parser.add_argument(
        "--final-pre-neutral-frames",
        type=int,
        default=0,
        help=(
            "Neutral frames before final input so FR/LG sees a fresh press edge. "
            "This is folded into the effective SID commit offset."
        ),
    )
    parser.add_argument(
        "--post-commit-verify-frames",
        type=int,
        default=DEFAULT_POST_COMMIT_VERIFY_FRAMES,
        help=(
            "Frames after final input before reading SaveBlock2 TID/SID. "
            "Defaults to 360 because the root TID-0-ready state updates IDs "
            "around post-frame 272."
        ),
    )
    parser.add_argument(
        "--calibration-search-radius",
        type=int,
        default=240,
        help="Nearby RNG advances searched when observed SID differs from prediction.",
    )
    parser.add_argument("--max-advances", type=int, default=None)
    parser.add_argument(
        "--only-shiny-value",
        action="append",
        default=[],
        help="Single shiny value or range, e.g. 0x0001 or 0x0010-0x001F. Can repeat.",
    )
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--resume", action="store_true")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument(
        "--trust-predicted-sid",
        action="store_true",
        help="Use predicted SID instead of SaveBlock2 read. Test/dry bring-up only.",
    )
    parser.add_argument(
        "--strict-prediction",
        action="store_true",
        help="Fail if observed shiny value differs from predicted instead of adapting.",
    )
    parser.add_argument(
        "--dry-plan",
        action="store_true",
        help="Write forecast/ledger JSON only; no mGBA driving.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> SecretIdConfig:
    """Normalize parsed args."""

    target_tid = common.checked_u16(args.target_tid, name="target_tid")
    if target_tid != DEFAULT_TARGET_TID:
        raise ValueError("--target-tid is fixed at 0 for the SID save bank")
    if int(args.sid_commit_offset) < 0:
        raise ValueError("--sid-commit-offset must be non-negative")
    if int(args.rng_advances_per_neutral_frame) < 1:
        raise ValueError("--rng-advances-per-neutral-frame must be at least 1")
    if int(args.min_wait_frames) < 0:
        raise ValueError("--min-wait-frames must be non-negative")
    if int(args.final_press_frames) < 1:
        raise ValueError("--final-press-frames must be positive")
    if int(args.final_pre_neutral_frames) < 0:
        raise ValueError("--final-pre-neutral-frames must be non-negative")
    if int(args.post_commit_verify_frames) < 0:
        raise ValueError("--post-commit-verify-frames must be non-negative")
    if args.limit is not None and int(args.limit) < 1:
        raise ValueError("--limit must be positive")

    return SecretIdConfig(
        output_dir=Path(args.output_dir),
        after_tape=Path(args.after_tape),
        target_tid=target_tid,
        start_rng=args.start_rng,
        sid_commit_offset=int(args.sid_commit_offset),
        rng_advances_per_neutral_frame=int(args.rng_advances_per_neutral_frame),
        final_button=str(args.final_button),
        final_press_frames=int(args.final_press_frames),
        final_pre_neutral_frames=int(args.final_pre_neutral_frames),
        min_wait_frames=int(args.min_wait_frames),
        post_commit_verify_frames=int(args.post_commit_verify_frames),
        calibration_search_radius=max(0, int(args.calibration_search_radius)),
        max_advances=args.max_advances,
        only_shiny_values=tuple(common.parse_shiny_selection(args.only_shiny_value)),
        limit=args.limit,
        resume=bool(args.resume),
        overwrite=bool(args.overwrite),
        trust_predicted_sid=bool(args.trust_predicted_sid),
        strict_prediction=bool(args.strict_prediction),
        dry_plan=bool(args.dry_plan),
    )


def current_qt_core() -> Any:
    """Return the visible Qt core."""

    try:
        import mgba.qt  # type: ignore
    except Exception as exc:  # noqa: BLE001 - command failure should be explicit.
        raise RuntimeError("mgba.qt is unavailable; run inside the Python-enabled Qt build") from exc
    return mgba.qt.current_core()


def optional_bool_attr(core: Any, name: str) -> bool | None:
    """Read a boolean core property/method when that bridge hook exists."""

    try:
        value = getattr(core, name)
    except Exception:  # noqa: BLE001 - bridge properties can fail when unavailable.
        return None
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001 - treat failed probes as unsupported.
            return None
    return bool(value)


def ensure_core_toggle(core: Any, *, getter_name: str, setter_name: str) -> str:
    """Ensure one optional Qt boolean feature is enabled."""

    enabled = optional_bool_attr(core, getter_name)
    if enabled is True:
        return "already-enabled"
    setter = getattr(core, setter_name, None)
    if not callable(setter):
        return "unavailable"
    setter(True)
    return "enabled"


def configure_qt_runtime_for_sid(core: Any) -> dict[str, str]:
    """Enable speed-friendly Qt options when the visible-core bridge exposes them."""

    settings = {
        "audio_killswitch": ensure_core_toggle(
            core,
            getter_name="audio_killswitch_enabled",
            setter_name="set_audio_killswitch",
        ),
        "no_render_mode": ensure_core_toggle(
            core,
            getter_name="no_render_mode_enabled",
            setter_name="set_no_render_mode",
        ),
        "fast_forward": ensure_core_toggle(
            core,
            getter_name="fast_forward_enabled",
            setter_name="set_fast_forward",
        ),
        "fast_forward_ratio": "unavailable",
    }
    set_ratio = getattr(core, "set_fast_forward_ratio", None)
    if callable(set_ratio):
        set_ratio(-1.0)
        settings["fast_forward_ratio"] = "unbounded"
    return settings


def selected_shiny_values(config: SecretIdConfig) -> list[int]:
    """Return the shiny values this run should cover."""

    if config.only_shiny_values:
        return list(config.only_shiny_values)
    return list(range(common.SHINY_VALUE_COUNT))


def effective_sid_commit_offset(config: SecretIdConfig, sid_commit_offset: int) -> int:
    """Return branch-relative offset after any fixed neutral edge window."""

    return int(sid_commit_offset) + (
        int(config.final_pre_neutral_frames) * int(config.rng_advances_per_neutral_frame)
    )


def save_path_for_hit(output_dir: Path, shiny_value: int, sid: int) -> Path:
    """Return decimal TSV/SID battery-save path for one hit."""

    return Path(output_dir) / DEFAULT_SAVE_NAME.format(
        shiny_value=int(shiny_value),
        sid=int(sid) & common.UINT16_MASK,
    )


def log_live_status(config: SecretIdConfig, message: str) -> None:
    """Append a best-effort live status breadcrumb for Qt runs."""

    try:
        config.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
        path = config.output_dir / DEFAULT_STATUS_LOG_NAME
        with path.open("a", encoding="utf-8") as handle:
            handle.write(f"{timestamp} {message}\n")
    except Exception:  # noqa: BLE001 - logging must never stop RNG work.
        pass


def resolve_branch_rng(config: SecretIdConfig, core: Any | None) -> int:
    """Resolve pre-SID ``gRngValue`` from CLI or live memory."""

    if config.start_rng is not None:
        return int(config.start_rng) & common.UINT32_MASK
    if core is None:
        raise RuntimeError("--start-rng is required without a live core")
    return common.read_rng_state(core)


def verify_target_tid(config: SecretIdConfig, core: Any | None) -> None:
    """Read the live pre-SID TID mirror and verify it is the fixed TID 0."""

    if core is None:
        return
    observed = common.read_initial_tid_mirror(core)
    if observed != config.target_tid:
        raise RuntimeError(
            f"Pre-SID TID mirror is {common.format_u16(observed)}, "
            f"expected {common.format_u16(config.target_tid)}"
        )


def verify_live_branch_rng(config: SecretIdConfig, core: Any | None, branch_rng: int) -> None:
    """Reject the common wrong Qt launch where a fresh ROM leaves RNG at zero."""

    if core is None or config.start_rng is not None:
        return
    if int(branch_rng) & common.UINT32_MASK:
        return
    raise RuntimeError(
        "Live pre-SID gRngValue is 0x00000000. Qt is not at the prepared "
        "pre-SID branch. Load the correct ROM/state, pause at the final SID "
        "input, then run this script from Qt."
    )


def build_or_load_ledger(
    config: SecretIdConfig,
    *,
    branch_rng: int,
) -> dict[str, Any]:
    """Load a resume ledger or create a fresh one.

    Resume is conservative: completed rows only stay complete when their
    exported save still exists, because the ledger is meant to be a reference
    table and not just a progress counter.
    """

    if (config.resume or config.ledger_path.exists()) and config.ledger_path.exists():
        ledger = common.read_json(config.ledger_path)
        if ledger.get("format") != common.SID_LEDGER_FORMAT:
            raise ValueError(f"Unsupported SID ledger format: {ledger.get('format')!r}")
        ledger_tid = common.parse_int(ledger.get("target_tid", -1))
        if ledger_tid != config.target_tid:
            raise ValueError(
                f"Ledger TID {common.format_u16(ledger_tid)} does not match "
                f"requested TID {common.format_u16(config.target_tid)}"
            )
        if common.reset_missing_save_hits_for_resume(ledger):
            common.write_json_atomic(config.ledger_path, ledger)
        return ledger
    ledger = common.new_sid_ledger(
        target_tid=config.target_tid,
        shiny_values=selected_shiny_values(config),
        branch_rng=branch_rng,
        sid_commit_offset=effective_sid_commit_offset(config, config.sid_commit_offset),
        rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
    )
    common.write_json_atomic(config.ledger_path, ledger)
    return ledger


def write_forecast(
    config: SecretIdConfig,
    *,
    branch_rng: int,
    sid_commit_offset: int,
    forecast: Iterable[common.SidHitPlan],
) -> None:
    """Persist the latest live LCRNG forecast."""

    forecast_rows = list(forecast)
    rows = [entry.to_json() for entry in forecast_rows]
    payload = {
        "format": "frlg-sid-shiny-value-forecast-v1",
        "target_tid": common.format_u16(config.target_tid),
        "branch_rng": common.format_u32(branch_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_neutral_frame": config.rng_advances_per_neutral_frame,
        "min_wait_frames": config.min_wait_frames,
        "entries": rows,
        "forecast_digest": common.plan_digest(forecast_rows),
    }
    common.write_json_atomic(config.forecast_path, payload)


def build_raw_lcrng_sid_coverage(
    *,
    target_tid: int,
    branch_rng: int,
    sid_commit_offset: int,
    rng_advances_per_wait_frame: int,
    min_wait_frames: int = 0,
    chunk_frames: int = SID_COVERAGE_CHUNK_FRAMES,
    max_advances: int | None = None,
) -> dict[str, Any]:
    """Scan raw one-frame LCRNG waits in million-frame chunks.

    This is a math-only coverage proof for a true pre-SID branch where one
    neutral wait frame advances the RNG by ``rng_advances_per_wait_frame``.
    It does not validate the read-only TID-hit route-cycle fallback, which has
    its own emulator-proven schedule because that route can fold waits modulo
    the setup loop.
    """

    stride = int(rng_advances_per_wait_frame)
    if stride < 1:
        raise ValueError("rng_advances_per_wait_frame must be at least 1")
    min_wait = int(min_wait_frames)
    if min_wait < 0:
        raise ValueError("min_wait_frames must be non-negative")
    chunk_size = int(chunk_frames)
    if chunk_size < 1:
        raise ValueError("chunk_frames must be positive")
    sid_offset = int(sid_commit_offset)
    if sid_offset < 0:
        raise ValueError("sid_commit_offset must be non-negative")

    tid = common.checked_u16(target_tid, name="target_tid")
    base = int(branch_rng) & common.UINT32_MASK
    seen_sids = bytearray(SID_COVERAGE_TARGET_COUNT)
    seen_tsvs = bytearray(common.SHINY_VALUE_COUNT)
    sid_count = 0
    tsv_count = 0
    max_wait_for_all_sids: int | None = None
    max_wait_for_all_tsvs: int | None = None
    chunk_summaries: list[dict[str, int]] = []
    wait_frames = min_wait
    rng_advance = sid_offset + (min_wait * stride)
    rng_state = common.lcrng_advance(base, rng_advance)
    stride_jump = common.lcrng_jump_for_steps(stride)

    while sid_count < SID_COVERAGE_TARGET_COUNT:
        if max_advances is not None and rng_advance > int(max_advances):
            break
        sid = common.random_u16_from_state(rng_state)
        if not seen_sids[sid]:
            seen_sids[sid] = 1
            sid_count += 1
            if sid_count == SID_COVERAGE_TARGET_COUNT:
                max_wait_for_all_sids = wait_frames
        shiny = common.shiny_value_from_tid_sid(tid, sid)
        if not seen_tsvs[shiny]:
            seen_tsvs[shiny] = 1
            tsv_count += 1
            if tsv_count == common.SHINY_VALUE_COUNT:
                max_wait_for_all_tsvs = wait_frames

        wait_frames += 1
        rng_advance += stride
        if wait_frames % chunk_size == 0:
            chunk_summaries.append(
                {
                    "frames": wait_frames,
                    "sid_count": sid_count,
                    "shiny_value_count": tsv_count,
                }
            )
        if sid_count < SID_COVERAGE_TARGET_COUNT:
            rng_state = stride_jump.apply(rng_state)

    if wait_frames % chunk_size or not chunk_summaries:
        chunk_summaries.append(
            {
                "frames": wait_frames,
                "sid_count": sid_count,
                "shiny_value_count": tsv_count,
            }
        )

    missing_sids = [sid for sid, seen in enumerate(seen_sids) if not seen]
    missing_tsvs = [tsv for tsv, seen in enumerate(seen_tsvs) if not seen]
    return {
        "format": "frlg-sid-raw-lcrng-coverage-v1",
        "target_tid": common.format_u16(tid),
        "target_tid_decimal": tid,
        "branch_rng": common.format_u32(base),
        "sid_commit_offset": sid_offset,
        "min_wait_frames": min_wait,
        "rng_advances_per_wait_frame": stride,
        "chunk_size_frames": chunk_size,
        "frames_scanned": wait_frames,
        "chunks_scanned": (wait_frames + chunk_size - 1) // chunk_size,
        "all_sids_hit": sid_count == SID_COVERAGE_TARGET_COUNT,
        "all_shiny_values_hit": tsv_count == common.SHINY_VALUE_COUNT,
        "sid_count": sid_count,
        "shiny_value_count": tsv_count,
        "max_wait_for_all_sids": max_wait_for_all_sids,
        "max_wait_for_all_shiny_values": max_wait_for_all_tsvs,
        "missing_sid_count": len(missing_sids),
        "missing_shiny_value_count": len(missing_tsvs),
        "missing_sid_sample": missing_sids[:32],
        "missing_shiny_value_sample": [
            common.format_shiny_value(value) for value in missing_tsvs[:32]
        ],
        "chunk_summaries": chunk_summaries,
        "note": (
            "Raw LCRNG proof for true pre-SID one-frame waits. "
            "The TID-hit loadstate route-cycle schedule is separate."
        ),
    }


def write_raw_lcrng_sid_coverage(
    config: SecretIdConfig,
    *,
    branch_rng: int,
    sid_commit_offset: int,
) -> dict[str, Any]:
    """Write raw one-frame SID coverage for the current pre-SID branch."""

    coverage = build_raw_lcrng_sid_coverage(
        target_tid=config.target_tid,
        branch_rng=branch_rng,
        sid_commit_offset=sid_commit_offset,
        rng_advances_per_wait_frame=config.rng_advances_per_neutral_frame,
        min_wait_frames=config.min_wait_frames,
        max_advances=config.max_advances,
    )
    common.write_json_atomic(config.sid_coverage_path, coverage)
    return coverage


def build_forecast_for_missing(
    config: SecretIdConfig,
    *,
    branch_rng: int,
    sid_commit_offset: int,
    ledger: dict[str, Any],
) -> list[common.SidHitPlan]:
    """Build and write an LCRNG forecast for currently selected missing values."""

    missing = selected_missing_shiny_values(config, ledger)
    forecast = common.build_missing_shiny_forecast(
        tid=config.target_tid,
        branch_rng=branch_rng,
        missing_shiny_values=missing,
        sid_commit_offset=sid_commit_offset,
        rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
        min_wait_frames=config.min_wait_frames,
        max_advances=config.max_advances,
    )
    write_forecast(
        config,
        branch_rng=branch_rng,
        sid_commit_offset=sid_commit_offset,
        forecast=forecast,
    )
    return forecast


def write_tid_state_schedule(
    config: SecretIdConfig,
    *,
    loaded_state_rng: int,
    branch_rng: int,
    sid_commit_offset: int,
    schedule: Iterable[common.SidHitPlan],
    schedule_kind: str,
    target_shiny_value_count: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> None:
    """Persist deterministic TSV hits for the loaded TID state.

    The file is the run order contract for TID-state mode. The TID hit state is
    not a free-running pre-SID branch: repeated A taps make SID generation land
    on a route-cycle residue. The schedule therefore records what that route can
    really reach, not an invalid straight-line LCRNG fantasy.
    """

    rows = list(schedule)
    payload = {
        "format": "frlg-sid-earliest-tsv-schedule-v1",
        "schedule_kind": schedule_kind,
        "target_tid": common.format_u16(config.target_tid),
        "loaded_state_rng": common.format_u32(loaded_state_rng),
        "branch_rng": common.format_u32(branch_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_neutral_frame": int(config.rng_advances_per_neutral_frame),
        "target_shiny_values": (
            len(rows) if target_shiny_value_count is None else int(target_shiny_value_count)
        ),
        "entries": [entry.to_json() for entry in rows],
        "schedule_digest": common.plan_digest(rows),
    }
    if metadata:
        collisions = sorted(set(payload).intersection(metadata))
        if collisions:
            raise ValueError(f"Schedule metadata cannot override {collisions!r}")
        payload.update(metadata)
    common.write_json_atomic(config.schedule_path, payload)


def build_tid_state_route_cycle_schedule(
    config: SecretIdConfig,
    *,
    loaded_state_rng: int,
    branch_rng: int,
    sid_commit_offset: int,
    target_shiny_values: Iterable[int] | None = None,
) -> tuple[list[common.SidHitPlan], list[int]]:
    """Precompute real TID-state route hits and unreachable TSVs.

    In the prepared TID hit state, the script reaches the SID branch by tapping
    A in a fixed cycle. Adding neutral frames before that tap loop only changes
    which cycle residue commits SID. With the current defaults the cycle is 20
    frames, so at most 20 SID/TSV rows are reachable from this state. This
    function makes that constraint explicit instead of pretending all 8192 TSVs
    are available by straight-line LCRNG waiting.

    Returned plan rows use ``branch_rng`` as the frozen branch/cycle origin and
    ``rng_advance`` as the effective cycle residue from that origin. The live
    probe remains the source of truth for the concrete restored branch.
    """

    if config.rng_advances_per_neutral_frame != 1:
        raise ValueError(
            "TID-state route-cycle schedule requires "
            "--rng-advances-per-neutral-frame 1"
        )
    setup_cycle_frames = int(config.final_press_frames) + TID_STATE_SETUP_NEUTRAL_FRAMES
    if setup_cycle_frames < 1:
        raise ValueError("TID-state setup cycle must be positive")

    wanted = {
        common.checked_shiny_value(value)
        for value in (
            selected_shiny_values(config)
            if target_shiny_values is None
            else target_shiny_values
        )
    }
    rows_by_shiny: dict[int, common.SidHitPlan] = {}
    base = int(branch_rng) & common.UINT32_MASK
    tid = common.checked_u16(config.target_tid, name="target_tid")
    for wait_frames in range(setup_cycle_frames):
        rng_advance = (int(sid_commit_offset) + wait_frames) % setup_cycle_frames
        rng_value = common.lcrng_advance(base, rng_advance)
        sid = common.random_u16_from_state(rng_value)
        shiny = common.shiny_value_from_tid_sid(tid, sid)
        if shiny not in wanted or shiny in rows_by_shiny:
            continue
        rows_by_shiny[shiny] = common.SidHitPlan(
            shiny_value=shiny,
            wait_frames=wait_frames,
            rng_advance=rng_advance,
            predicted_tid=tid,
            predicted_sid=sid,
            predicted_shiny_value=shiny,
            predicted_rng=rng_value,
            branch_rng=base,
        )

    schedule = sorted(
        rows_by_shiny.values(),
        key=lambda entry: (entry.wait_frames, entry.shiny_value),
    )
    unreachable = sorted(wanted.difference(rows_by_shiny))
    write_tid_state_schedule(
        config,
        loaded_state_rng=loaded_state_rng,
        branch_rng=branch_rng,
        sid_commit_offset=sid_commit_offset,
        schedule=schedule,
        schedule_kind="tid-state-route-cycle",
        target_shiny_value_count=len(wanted),
        metadata={
            "setup_cycle_frames": setup_cycle_frames,
            "entry_rng_advance_basis": "effective route-cycle residue from branch_rng",
            "entry_branch_rng_basis": "frozen pre-SID branch RNG used as route-cycle origin",
            "route_formula": (
                "effective_sid_rng_advance = "
                "(sid_commit_offset + wait_frames) % setup_cycle_frames"
            ),
            "reachable_target_shiny_values": len(schedule),
            "unreachable_target_shiny_values": len(unreachable),
            "unreachable_shiny_values": [
                common.format_shiny_value(value) for value in unreachable
            ],
        },
    )
    return schedule, unreachable


def build_tid_state_earliest_schedule(
    config: SecretIdConfig,
    *,
    loaded_state_rng: int,
    branch_rng: int,
    sid_commit_offset: int,
) -> list[common.SidHitPlan]:
    """Compatibility wrapper returning the real route-cycle schedule rows."""

    schedule, _unreachable = build_tid_state_route_cycle_schedule(
        config,
        loaded_state_rng=loaded_state_rng,
        branch_rng=branch_rng,
        sid_commit_offset=sid_commit_offset,
    )
    return schedule


def selected_missing_shiny_values(
    config: SecretIdConfig,
    ledger: dict[str, Any],
) -> list[int]:
    """Return missing ledger rows that this run is allowed to fill."""

    missing = common.missing_shiny_values(ledger)
    if not config.only_shiny_values:
        return missing
    allowed = set(config.only_shiny_values)
    return [value for value in missing if value in allowed]


def capture_branch_state(core: Any) -> None:
    """Capture current pre-SID branch into Qt scratch state."""

    save_scratch = getattr(core, "save_scratch_state", None)
    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(save_scratch) or not callable(load_scratch):
        raise RuntimeError("current core must expose save_scratch_state/load_scratch_state")
    save_scratch()


def restore_branch_state(core: Any) -> None:
    """Restore pre-SID branch from Qt scratch state."""

    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(load_scratch):
        raise RuntimeError("current core does not expose load_scratch_state")
    load_scratch()
    input_tape.set_exact_keys(core, 0)


def load_tid_hit_state(core: Any) -> None:
    """Restore the read-only TID 0 hit state used by scan fallback mode."""

    load = getattr(core, "load_state_file", None)
    if not callable(load):
        raise RuntimeError("current core does not expose load_state_file")
    if not DEFAULT_TID_HIT_STATE.exists():
        raise FileNotFoundError(DEFAULT_TID_HIT_STATE)
    load(DEFAULT_TID_HIT_STATE)
    input_tape.set_exact_keys(core, 0)


def run_neutral_frames(core: Any, frames: int) -> None:
    """Run neutral-input frames."""

    if int(frames) < 0:
        raise ValueError("frames must be non-negative")
    input_tape.run_exact_frames(core, 0, int(frames), use_batch=True)


def infer_sid_commit_offset_from_branch(
    *,
    branch_rng: int,
    observed_sid: int,
    search_limit: int = SID_COMMIT_OFFSET_SEARCH_LIMIT,
) -> int:
    """Return the LCRNG advance count from branch RNG to observed SID."""

    state = int(branch_rng) & common.UINT32_MASK
    for offset in range(0, int(search_limit) + 1):
        if common.random_u16_from_state(common.lcrng_advance(state, offset)) == (
            int(observed_sid) & common.UINT16_MASK
        ):
            return offset
    raise RuntimeError(
        f"Could not infer SID commit offset for branch {common.format_u32(branch_rng)} "
        f"and SID {common.format_u16(observed_sid)}"
    )


def commit_sid(config: SecretIdConfig, core: Any) -> None:
    """Run the neutral edge window, press final input, then wait to verify."""

    run_neutral_frames(core, config.final_pre_neutral_frames)
    mask = input_tape.mask_from_buttons(config.final_button)
    input_tape.run_exact_frames(core, mask, config.final_press_frames, use_batch=True)
    input_tape.set_exact_keys(core, 0)
    run_neutral_frames(core, config.post_commit_verify_frames)


def read_or_predict_ids(
    config: SecretIdConfig,
    core: Any,
    plan: common.SidHitPlan,
) -> tuple[int, int]:
    """Read final IDs from SaveBlock2 unless prediction trust is explicitly enabled."""

    if config.trust_predicted_sid:
        return plan.predicted_tid, plan.predicted_sid
    return common.read_trainer_id_from_saveblock2(core)


def read_trainer_id_if_available(core: Any) -> tuple[int, int] | None:
    """Return SaveBlock2 TID/SID when readable, else ``None``."""

    try:
        return common.read_trainer_id_from_saveblock2(core)
    except Exception:  # noqa: BLE001 - setup search may run before SaveBlock2 is stable.
        return None


def probe_final_tid_from_branch(
    config: SecretIdConfig,
    core: Any,
    *,
    extra_wait_frames: int = 0,
) -> tuple[int, int]:
    """Probe final input on scratch state, then restore before real work."""

    capture_branch_state(core)
    try:
        run_neutral_frames(core, int(extra_wait_frames))
        commit_sid(config, core)
        return common.read_trainer_id_from_saveblock2(core)
    finally:
        restore_branch_state(core)


def seek_pre_sid_branch_info_from_tid_state(
    config: SecretIdConfig,
    core: Any,
) -> PreSidBranchInfo | None:
    """Advance a TID-hit state until the next A tap writes TID 0, then restore.

    The TID bruteforcer's hit state is earlier than the SID final-A branch. This
    search drives the intro with repeated A taps, saving the scratch slot before
    each tap. When a tap causes SaveBlock2 to become TID 0, restoring scratch
    leaves the core at the real pre-SID branch for the normal forecast loop.
    """

    mask = input_tape.mask_from_buttons(config.final_button)
    last_seen = read_trainer_id_if_available(core)
    for tap_index in range(1, TID_STATE_SETUP_MAX_TAPS + 1):
        input_tape.set_exact_keys(core, 0)
        before_seen = read_trainer_id_if_available(core)
        if before_seen is not None:
            last_seen = before_seen
        capture_branch_state(core)
        branch_rng = common.read_rng_state(core)
        input_tape.run_exact_frames(core, mask, config.final_press_frames, use_batch=True)
        input_tape.set_exact_keys(core, 0)
        for _ in range(TID_STATE_SETUP_NEUTRAL_FRAMES):
            input_tape.run_exact_frames(core, 0, 1, use_batch=True)
            seen = read_trainer_id_if_available(core)
            if seen is None:
                continue
            last_seen = seen
            tid, sid = seen
            ids_changed = before_seen is None or seen != before_seen
            if tid == config.target_tid and ids_changed:
                restore_branch_state(core)
                sid_commit_offset = infer_sid_commit_offset_from_branch(
                    branch_rng=branch_rng,
                    observed_sid=sid,
                )
                message = (
                    "auto-setup found pre-SID branch "
                    f"tap={tap_index} sid_after_tap={common.format_u16(sid)} "
                    f"sid_commit_offset={sid_commit_offset}"
                )
                log_live_status(config, message)
                print(message, flush=True)
                return PreSidBranchInfo(
                    branch_rng=branch_rng,
                    observed_sid=sid,
                    sid_commit_offset=sid_commit_offset,
                    tap_index=tap_index,
                )
    if last_seen is None:
        message = "auto-setup did not find readable SaveBlock2 IDs"
    else:
        message = (
            "auto-setup did not find TID 0 branch; last TID/SID "
            f"{common.format_u16(last_seen[0])}/{common.format_u16(last_seen[1])}"
        )
    log_live_status(config, message)
    print(message, flush=True)
    try:
        restore_branch_state(core)
    except Exception:  # noqa: BLE001 - best effort after failed setup search.
        pass
    return None


def seek_pre_sid_branch_from_tid_state(config: SecretIdConfig, core: Any) -> bool:
    """Compatibility wrapper returning whether auto-setup found a branch."""

    return seek_pre_sid_branch_info_from_tid_state(config, core) is not None


def wait_for_live_ready_branch(config: SecretIdConfig, core: Any) -> int:
    """Wait until Qt is at a branch where final input produces TID 0.

    This keeps a bad deployment alive instead of writing bogus saves. The user
    can load the correct pre-SID savestate in the visible Qt window; the script
    will recheck and proceed once the branch proves out on scratch state.
    """

    tried_auto_setup = False
    while True:
        verify_target_tid(config, core)
        branch_rng = resolve_branch_rng(config, core)
        if config.trust_predicted_sid:
            verify_live_branch_rng(config, core, branch_rng)
            log_live_status(
                config,
                f"preflight checking branch_rng={common.format_u32(branch_rng)}",
            )
            return branch_rng
        try:
            verify_live_branch_rng(config, core, branch_rng)
        except RuntimeError as exc:
            message = f"waiting for valid pre-SID branch: {exc}"
            log_live_status(config, message)
            print(message, flush=True)
        else:
            log_live_status(
                config,
                f"preflight checking branch_rng={common.format_u32(branch_rng)}",
            )
            try:
                final_tid, final_sid = probe_final_tid_from_branch(config, core)
                second_tid, second_sid = probe_final_tid_from_branch(
                    config,
                    core,
                    extra_wait_frames=31,
                )
            except Exception as exc:  # noqa: BLE001 - stay alive for operator setup.
                message = f"waiting for valid pre-SID branch: {exc}"
                log_live_status(config, message)
                print(message, flush=True)
            else:
                if (
                    final_tid == config.target_tid
                    and second_tid == config.target_tid
                    and final_sid != second_sid
                ):
                    message = (
                        "preflight ok "
                        f"branch_rng={common.format_u32(branch_rng)} "
                        f"probe_sid={common.format_u16(final_sid)} "
                        f"probe_sid_after_31={common.format_u16(second_sid)}"
                    )
                    log_live_status(config, message)
                    print(message, flush=True)
                    return branch_rng
                if (
                    final_tid == config.target_tid
                    and second_tid == config.target_tid
                    and final_sid == second_sid
                ):
                    sid_commit_offset = infer_sid_commit_offset_from_branch(
                        branch_rng=branch_rng,
                        observed_sid=final_sid,
                    )
                    message = (
                        "preflight found RNG-frozen pre-SID branch; "
                        f"sid={common.format_u16(final_sid)} "
                        f"sid_commit_offset={sid_commit_offset}"
                    )
                    log_live_status(config, message)
                    print(message, flush=True)
                    raise FrozenPreSidBranchError(
                        branch_rng=branch_rng,
                        sid=final_sid,
                        sid_commit_offset=sid_commit_offset,
                    )
                message = (
                    "waiting for valid pre-SID branch: "
                    f"probe A TID/SID {common.format_u16(final_tid)}/"
                    f"{common.format_u16(final_sid)}, probe B TID/SID "
                    f"{common.format_u16(second_tid)}/{common.format_u16(second_sid)}, "
                    f"expected TID {common.format_u16(config.target_tid)} and changing SID"
                )
                log_live_status(config, message)
                print(message, flush=True)
        if not tried_auto_setup:
            tried_auto_setup = True
            log_live_status(config, "trying auto-setup from TID hit state")
            print("trying auto-setup from TID hit state", flush=True)
            if seek_pre_sid_branch_from_tid_state(config, core):
                continue
        try:
            core.pause()
        except Exception:  # noqa: BLE001 - pause is best-effort during setup.
            pass
        time.sleep(2.0)


def observed_plan_for_ledger(
    *,
    forecast_plan: common.SidHitPlan,
    tid: int,
    sid: int,
    shiny_value: int,
    sid_commit_offset: int,
    rng_advances_per_neutral_frame: int,
) -> common.SidHitPlan:
    """Build the ledger plan row from observed live SID data.

    When calibration adjusts the commit offset, the forecast row that selected
    the branch is no longer the best description of the hit. The ledger should
    record the actual calibrated RNG advance and SID that produced the save.
    """

    rng_advance = (
        int(forecast_plan.wait_frames) * int(rng_advances_per_neutral_frame)
        + int(sid_commit_offset)
    )
    predicted_rng = common.lcrng_advance(forecast_plan.branch_rng, rng_advance)
    return common.SidHitPlan(
        shiny_value=shiny_value,
        wait_frames=forecast_plan.wait_frames,
        rng_advance=rng_advance,
        predicted_tid=tid,
        predicted_sid=sid,
        predicted_shiny_value=shiny_value,
        predicted_rng=predicted_rng,
        branch_rng=forecast_plan.branch_rng,
    )


def export_battery_save_atomic(
    core: Any,
    path: Path,
    *,
    expected_tid: int,
    expected_sid: int,
) -> str:
    """Export one verified battery ``.sav`` through a temp file and return SHA-1.

    The SID workflow produces save-bank battery files only. Savestates would
    make the ledger ambiguous, so reject any non-``.sav`` target before asking
    the emulator to export. The temp export is parsed before replace so stale
    SRAM cannot be accepted when the route tape reached the starter but did not
    commit a fresh in-game save. Some route endings can expose SRAM while the
    game's save write is still settling, so validation retries after neutral
    frames before the row is treated as a bad export.
    """

    path = Path(path)
    if path.suffix.lower() != ".sav":
        raise ValueError(f"SID output must be a .sav battery save, got {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    export = getattr(core, "export_save_file", None)
    if not callable(export):
        raise RuntimeError("current core does not expose export_save_file")

    last_error: Exception | None = None
    for attempt, settle_frames in enumerate(EXPORT_VALIDATION_RETRY_SETTLE_FRAMES, start=1):
        if settle_frames:
            input_tape.run_exact_frames(core, 0, settle_frames, use_batch=True)
        if tmp.exists():
            tmp.unlink()
        export(tmp)
        try:
            common.patch_frlg_tsv_save_ids(
                tmp,
                expected_tid=expected_tid,
                expected_sid=expected_sid,
            )
        except Exception as exc:  # noqa: BLE001 - retry only the exported file proof.
            last_error = exc
            if tmp.exists():
                tmp.unlink()
            continue
        tmp.replace(path)
        return common.sha1_file(path)

    raise SaveExportValidationError(
        "battery save export did not validate after "
        f"{len(EXPORT_VALIDATION_RETRY_SETTLE_FRAMES)} attempt(s); "
        f"last error: {last_error}"
    ) from last_error


def verify_live_ids_after_tape(
    config: SecretIdConfig,
    core: Any,
    *,
    expected_tid: int,
    expected_sid: int,
) -> None:
    """Ensure the route tape did not move to a different SaveBlock2 identity."""

    if config.trust_predicted_sid:
        return
    live_tid, live_sid = common.read_trainer_id_from_saveblock2(core)
    if live_tid != expected_tid or live_sid != expected_sid:
        raise RuntimeError(
            "route tape changed live SaveBlock2 IDs: "
            f"got {common.format_u16(live_tid)}/{common.format_u16(live_sid)}, "
            f"expected {common.format_u16(expected_tid)}/{common.format_u16(expected_sid)}. "
            "Replay from the post-SID anchor, and make the tape include an in-game save "
            "before battery export."
        )


def load_after_tape(config: SecretIdConfig) -> input_tape.InputTape:
    """Load the required post-SID input tape."""

    if not config.after_tape.exists():
        raise FileNotFoundError(config.after_tape)
    return input_tape.load(config.after_tape)


def should_skip_existing_hit(config: SecretIdConfig, entry: common.SidLedgerEntry) -> bool:
    """Return true when resume can skip one completed shiny value."""

    if not config.resume or not entry.done or not entry.save_path:
        return False
    return Path(entry.save_path).is_file()


def _limited_forecast(
    forecast: list[common.SidHitPlan],
    *,
    processed: int,
    limit: int | None,
) -> list[common.SidHitPlan]:
    """Return forecast rows available for this run."""

    if limit is None:
        return forecast
    remaining = int(limit) - int(processed)
    if remaining <= 0:
        return []
    return forecast[:remaining]


def prune_forecast_to_selected_missing(
    config: SecretIdConfig,
    ledger: dict[str, Any],
    forecast: list[common.SidHitPlan],
) -> list[common.SidHitPlan]:
    """Drop forecast rows already completed or outside the repair selection."""

    allowed = set(selected_missing_shiny_values(config, ledger))
    if not allowed:
        return []
    return [plan for plan in forecast if plan.shiny_value in allowed]


def exclude_blocked_forecast(
    forecast: list[common.SidHitPlan],
    blocked_shiny_values: set[int],
) -> list[common.SidHitPlan]:
    """Drop rows with recoverable export errors already hit in this process."""

    if not blocked_shiny_values:
        return forecast
    return [plan for plan in forecast if plan.shiny_value not in blocked_shiny_values]


def next_export_retry_plan(
    config: SecretIdConfig,
    *,
    branch_rng: int,
    sid_commit_offset: int,
    failed_plan: common.SidHitPlan,
) -> common.SidHitPlan:
    """Return the next later LCRNG hit for a TSV after a bad save export.

    A failed battery export proves the SID was hit but the post-tape save data
    was not trustworthy. Because each TSV has many later hits in the SID stream,
    retry the route from the clean pre-SID branch with the next matching SID
    instead of blocking the TSV after one bad cartridge-save snapshot.
    """

    return common.build_missing_shiny_forecast(
        tid=config.target_tid,
        branch_rng=branch_rng,
        missing_shiny_values=[failed_plan.shiny_value],
        sid_commit_offset=sid_commit_offset,
        rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
        min_wait_frames=max(config.min_wait_frames, failed_plan.wait_frames + 1),
        max_advances=config.max_advances,
    )[0]


def tid_state_scan_plan(
    *,
    config: SecretIdConfig,
    wait_frames: int,
    branch_info: PreSidBranchInfo,
) -> common.SidHitPlan:
    """Build a ledger plan for a TID-state scan candidate."""

    predicted_rng = common.lcrng_advance(
        branch_info.branch_rng,
        branch_info.sid_commit_offset,
    )
    predicted_sid = common.random_u16_from_state(predicted_rng)
    predicted_shiny = common.shiny_value_from_tid_sid(config.target_tid, predicted_sid)
    return common.SidHitPlan(
        shiny_value=predicted_shiny,
        wait_frames=int(wait_frames),
        rng_advance=branch_info.sid_commit_offset,
        predicted_tid=config.target_tid,
        predicted_sid=predicted_sid,
        predicted_shiny_value=predicted_shiny,
        predicted_rng=predicted_rng,
        branch_rng=branch_info.branch_rng,
    )


def drive_tid_state_scan_candidate(
    config: SecretIdConfig,
    core: Any,
    *,
    wait_frames: int,
) -> PreSidBranchInfo:
    """Load the read-only TID state, wait, and restore the next pre-SID branch."""

    load_tid_hit_state(core)
    run_neutral_frames(core, int(wait_frames))
    branch_info = seek_pre_sid_branch_info_from_tid_state(config, core)
    if branch_info is None:
        raise RuntimeError(
            f"Could not reach pre-SID branch from TID state after wait {wait_frames}"
        )
    return branch_info


def generate_secret_id_saves_from_tid_state(
    config: SecretIdConfig,
    *,
    core: Any,
    runtime_settings: dict[str, str] | None,
    frozen_branch: FrozenPreSidBranchError,
) -> dict[str, Any]:
    """Fallback live mode for the read-only TID hit state.

    The exact pre-SID branch is RNG-frozen, so waiting there cannot change SID.
    Waiting from the earlier TID state only shifts the repeated-A route cycle.
    This mode writes that real route schedule for the currently missing ledger
    rows. Already-complete TSVs do not block resume even if this loaded state
    could not reach them again. If the route cannot reach a missing TSV, it
    stops before replaying tape or exporting saves.
    """

    load_tid_hit_state(core)
    try:
        core.pause()
    except Exception:  # noqa: BLE001 - pause at load boundary is best effort.
        pass
    loaded_state_rng = common.read_rng_state(core)
    ledger = build_or_load_ledger(config, branch_rng=frozen_branch.branch_rng)
    route_targets = selected_missing_shiny_values(config, ledger)
    schedule, unreachable = build_tid_state_route_cycle_schedule(
        config,
        loaded_state_rng=loaded_state_rng,
        branch_rng=frozen_branch.branch_rng,
        sid_commit_offset=frozen_branch.sid_commit_offset,
        target_shiny_values=route_targets,
    )
    ledger["mode"] = "tid-state-scheduled"
    ledger["tid_hit_state"] = str(DEFAULT_TID_HIT_STATE)
    ledger["loaded_tid_state_rng"] = common.format_u32(loaded_state_rng)
    ledger["frozen_pre_sid_branch_rng"] = common.format_u32(frozen_branch.branch_rng)
    ledger["frozen_pre_sid"] = common.format_u16(frozen_branch.sid)
    ledger["schedule_path"] = str(config.schedule_path)
    ledger["schedule_digest"] = common.plan_digest(schedule)
    ledger["schedule_kind"] = "tid-state-route-cycle"
    ledger["route_schedule_reachable_count"] = len(schedule)
    ledger["route_schedule_unreachable_count"] = len(unreachable)
    ledger["route_schedule_missing_target_count"] = len(route_targets)
    if unreachable:
        message = (
            "TID-state route-cycle schedule cannot cover requested TSVs: "
            f"reachable={len(schedule)} unreachable={len(unreachable)}. "
            f"Schedule written to {config.schedule_path}. "
            "No tape replay or save export was run."
        )
        ledger["route_schedule_unreachable_sample"] = [
            common.format_shiny_value(value) for value in unreachable[:32]
        ]
        ledger["route_schedule_error"] = message
        common.write_json_atomic(config.ledger_path, ledger)
        log_live_status(config, message)
        raise RuntimeError(message)
    if not route_targets:
        common.write_json_atomic(config.ledger_path, ledger)
        summary = common.ledger_summary(ledger)
        summary.update(
            {
                "mode": "tid-state-scheduled",
                "target_tid": common.format_u16(config.target_tid),
                "processed_this_run": 0,
                "skipped_this_run": 0,
                "next_tid_state_schedule_index": int(
                    ledger.get("next_tid_state_schedule_index", 0)
                ),
                "next_tid_state_wait": int(ledger.get("next_tid_state_wait", 0)),
                "elapsed_seconds": 0.0,
                "ledger_path": str(config.ledger_path),
                "forecast_path": str(config.forecast_path),
                "schedule_path": str(config.schedule_path),
                "runtime_settings": runtime_settings,
            }
        )
        return summary
    route_tape = load_after_tape(config)
    started = time.monotonic()
    processed = 0
    skipped = 0
    schedule_index = int(ledger.get("next_tid_state_schedule_index", 0))
    log_live_status(
        config,
        "tid-state route-cycle mode "
        f"start_index={schedule_index} entries={len(schedule)} "
        f"loaded_rng={common.format_u32(loaded_state_rng)} "
        f"branch_rng={common.format_u32(frozen_branch.branch_rng)}",
    )

    for index, plan in enumerate(schedule[schedule_index:], start=schedule_index):
        if config.limit is not None and processed >= int(config.limit):
            break
        selected_missing = set(selected_missing_shiny_values(config, ledger))
        if not selected_missing:
            break
        if plan.shiny_value not in selected_missing:
            skipped += 1
            ledger["next_tid_state_schedule_index"] = index + 1
            ledger["next_tid_state_wait"] = plan.wait_frames + 1
            continue

        branch_info: PreSidBranchInfo | None = None
        try:
            branch_info = drive_tid_state_scan_candidate(
                config,
                core,
                wait_frames=plan.wait_frames,
            )
            observed_probe_plan = tid_state_scan_plan(
                config=config,
                wait_frames=plan.wait_frames,
                branch_info=branch_info,
            )
            if (
                observed_probe_plan.predicted_sid != plan.predicted_sid
                or observed_probe_plan.predicted_shiny_value != plan.shiny_value
            ):
                raise RuntimeError(
                    "deterministic schedule mismatch before SID commit: "
                    f"schedule wait={plan.wait_frames} "
                    f"sid={common.format_u16(plan.predicted_sid)} "
                    f"tsv={common.format_shiny_value(plan.shiny_value)}, "
                    f"observed sid={common.format_u16(observed_probe_plan.predicted_sid)} "
                    f"tsv={common.format_shiny_value(observed_probe_plan.predicted_shiny_value)}. "
                    "Stopping before save export."
                )

            commit_sid(config, core)
            tid_after, sid_after = common.read_trainer_id_from_saveblock2(core)
            if tid_after != config.target_tid:
                raise RuntimeError(
                    f"TID mismatch after SID commit: got {common.format_u16(tid_after)}, "
                    f"expected {common.format_u16(config.target_tid)}"
                )
            actual_shiny_value = common.shiny_value_from_tid_sid(tid_after, sid_after)
            if sid_after != plan.predicted_sid or actual_shiny_value != plan.shiny_value:
                raise RuntimeError(
                    "deterministic schedule mismatch after SID commit: "
                    f"schedule wait={plan.wait_frames} "
                    f"sid={common.format_u16(plan.predicted_sid)} "
                    f"tsv={common.format_shiny_value(plan.shiny_value)}, "
                    f"actual sid={common.format_u16(sid_after)} "
                    f"tsv={common.format_shiny_value(actual_shiny_value)}. "
                    "Stopping before save export."
                )

            input_tape.replay_tape(core, route_tape, use_batch=True)
            verify_live_ids_after_tape(
                config,
                core,
                expected_tid=tid_after,
                expected_sid=sid_after,
            )
            save_path = save_path_for_hit(config.output_dir, actual_shiny_value, sid_after)
            if save_path.exists() and not config.overwrite:
                raise FileExistsError(f"{save_path} exists; pass --overwrite or --resume")
            save_sha1 = export_battery_save_atomic(
                core,
                save_path,
                expected_tid=tid_after,
                expected_sid=sid_after,
            )
            common.mark_ledger_hit(
                ledger,
                shiny_value=actual_shiny_value,
                tid=tid_after,
                sid=sid_after,
                save_path=save_path,
                save_sha1=save_sha1,
                plan=plan,
                sid_commit_offset=frozen_branch.sid_commit_offset,
                note="deterministic route-cycle schedule",
            )
            processed += 1
            ledger["next_tid_state_schedule_index"] = index + 1
            ledger["next_tid_state_wait"] = plan.wait_frames + 1
            common.write_json_atomic(config.ledger_path, ledger)
            log_live_status(
                config,
                "saved "
                f"tsv={actual_shiny_value} sid={sid_after} wait={plan.wait_frames} "
                f"schedule_index={index} path={save_path.name} processed={processed}",
            )
        except Exception as exc:  # noqa: BLE001 - preserve ledger error context.
            common.mark_ledger_error(ledger, shiny_value=plan.shiny_value, error=str(exc))
            ledger["next_tid_state_schedule_index"] = index
            ledger["next_tid_state_wait"] = plan.wait_frames
            common.write_json_atomic(config.ledger_path, ledger)
            raise

    elapsed = time.monotonic() - started
    summary = common.ledger_summary(ledger)
    summary.update(
        {
            "mode": "tid-state-scheduled",
            "target_tid": common.format_u16(config.target_tid),
            "processed_this_run": processed,
            "skipped_this_run": skipped,
            "next_tid_state_schedule_index": int(ledger.get("next_tid_state_schedule_index", 0)),
            "next_tid_state_wait": int(ledger.get("next_tid_state_wait", 0)),
            "elapsed_seconds": round(elapsed, 3),
            "ledger_path": str(config.ledger_path),
            "forecast_path": str(config.forecast_path),
            "schedule_path": str(config.schedule_path),
            "runtime_settings": runtime_settings,
        }
    )
    return summary


def generate_secret_id_saves(
    config: SecretIdConfig,
    *,
    core: Any | None = None,
) -> dict[str, Any]:
    """Run dry planning or the live SID shiny-value save exporter."""

    live_core = core if core is not None else (None if config.dry_plan else current_qt_core())
    runtime_settings: dict[str, str] | None = None
    if not config.dry_plan:
        if live_core is None:
            raise RuntimeError("live core is required unless --dry-plan is set")
        runtime_settings = configure_qt_runtime_for_sid(live_core)
        log_live_status(config, f"runtime_settings={runtime_settings}")
    config.output_dir.mkdir(parents=True, exist_ok=True)
    if config.dry_plan:
        verify_target_tid(config, live_core)
        branch_rng = resolve_branch_rng(config, live_core)
    else:
        try:
            branch_rng = wait_for_live_ready_branch(config, live_core)
        except FrozenPreSidBranchError as exc:
            return generate_secret_id_saves_from_tid_state(
                config,
                core=live_core,
                runtime_settings=runtime_settings,
                frozen_branch=exc,
            )
    ledger = build_or_load_ledger(config, branch_rng=branch_rng)
    runtime_sid_commit_offset = int(
        ledger.get(
            "sid_commit_offset",
            effective_sid_commit_offset(config, config.sid_commit_offset),
        )
    )
    sid_coverage = write_raw_lcrng_sid_coverage(
        config,
        branch_rng=branch_rng,
        sid_commit_offset=runtime_sid_commit_offset,
    )
    if not config.dry_plan:
        log_live_status(
            config,
            "raw LCRNG SID coverage "
            f"all_sids={sid_coverage['all_sids_hit']} "
            f"frames={sid_coverage['frames_scanned']} "
            f"max_wait={sid_coverage['max_wait_for_all_sids']}",
        )
    forecast = build_forecast_for_missing(
        config,
        branch_rng=branch_rng,
        sid_commit_offset=runtime_sid_commit_offset,
        ledger=ledger,
    )
    if not config.dry_plan:
        log_live_status(config, f"ledger ready missing={len(forecast)}")

    if config.dry_plan:
        return {
            "mode": "dry-plan",
            "target_tid": common.format_u16(config.target_tid),
            "branch_rng": common.format_u32(branch_rng),
            "missing_shiny_values": len(forecast),
            "ledger_path": str(config.ledger_path),
            "forecast_path": str(config.forecast_path),
            "sid_coverage_path": str(config.sid_coverage_path),
            "raw_lcrng_all_sids_hit": sid_coverage["all_sids_hit"],
            "raw_lcrng_sid_frames_scanned": sid_coverage["frames_scanned"],
            "raw_lcrng_max_wait_for_all_sids": sid_coverage["max_wait_for_all_sids"],
        }

    route_tape = load_after_tape(config)
    capture_branch_state(live_core)
    started = time.monotonic()
    processed = 0
    skipped = 0
    recalibrations = 0
    blocked_export_errors: set[int] = set()
    export_alternate_retries = 0
    export_error_counts: dict[int, int] = {}

    while selected_missing_shiny_values(config, ledger):
        if config.limit is not None and processed >= int(config.limit):
            break

        restore_branch_state(live_core)
        live_branch_rng = common.read_rng_state(live_core)
        if live_branch_rng != branch_rng:
            branch_rng = live_branch_rng
            forecast = build_forecast_for_missing(
                config,
                branch_rng=branch_rng,
                sid_commit_offset=runtime_sid_commit_offset,
                ledger=ledger,
            )
        else:
            forecast = prune_forecast_to_selected_missing(config, ledger, forecast)
        forecast = exclude_blocked_forecast(forecast, blocked_export_errors)
        if not forecast:
            forecast = exclude_blocked_forecast(
                build_forecast_for_missing(
                    config,
                    branch_rng=branch_rng,
                    sid_commit_offset=runtime_sid_commit_offset,
                    ledger=ledger,
                ),
                blocked_export_errors,
            )

        runnable_forecast = _limited_forecast(forecast, processed=processed, limit=config.limit)
        if not runnable_forecast:
            break
        plan = runnable_forecast[0]
        attempt_plan = plan
        attempt_shiny_value = plan.shiny_value
        attempt_sid = plan.predicted_sid
        ledger_entry = common.ledger_entries_by_shiny_value(ledger)[plan.shiny_value]
        if should_skip_existing_hit(config, ledger_entry):
            skipped += 1
            common.mark_ledger_hit(
                ledger,
                shiny_value=plan.shiny_value,
                tid=ledger_entry.tid if ledger_entry.tid is not None else config.target_tid,
                sid=ledger_entry.sid if ledger_entry.sid is not None else plan.predicted_sid,
                save_path=ledger_entry.save_path or "",
                save_sha1=ledger_entry.save_sha1,
                plan=plan,
                sid_commit_offset=runtime_sid_commit_offset,
                note=ledger_entry.note,
            )
            forecast = prune_forecast_to_selected_missing(config, ledger, forecast)
            write_forecast(
                config,
                branch_rng=branch_rng,
                sid_commit_offset=runtime_sid_commit_offset,
                forecast=forecast,
            )
            continue

        try:
            run_neutral_frames(live_core, plan.wait_frames)
            commit_sid(config, live_core)
            tid_after, sid_after = read_or_predict_ids(config, live_core, plan)
            if tid_after != config.target_tid:
                raise RuntimeError(
                    f"TID mismatch after SID commit: got {common.format_u16(tid_after)}, "
                    f"expected {common.format_u16(config.target_tid)}"
                )

            actual_shiny_value = common.shiny_value_from_tid_sid(tid_after, sid_after)
            attempt_shiny_value = actual_shiny_value
            attempt_sid = sid_after
            inferred_offset = common.infer_sid_commit_offset(
                branch_rng=branch_rng,
                wait_frames=plan.wait_frames,
                observed_sid=sid_after,
                rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
                expected_sid_commit_offset=runtime_sid_commit_offset,
                search_radius=config.calibration_search_radius,
            )
            previous_offset = runtime_sid_commit_offset
            # A live SID proof can expose a route-specific commit offset. Once
            # learned, rebuild every later attempt from the adjusted LCRNG map.
            offset_changed = False
            if inferred_offset is not None and inferred_offset != runtime_sid_commit_offset:
                runtime_sid_commit_offset = inferred_offset
                ledger["sid_commit_offset"] = runtime_sid_commit_offset
                sid_coverage = write_raw_lcrng_sid_coverage(
                    config,
                    branch_rng=branch_rng,
                    sid_commit_offset=runtime_sid_commit_offset,
                )
                recalibrations += 1
                offset_changed = True

            if config.strict_prediction and actual_shiny_value != plan.predicted_shiny_value:
                raise RuntimeError(
                    f"Predicted shiny value {common.format_shiny_value(plan.predicted_shiny_value)} "
                    f"but observed {common.format_shiny_value(actual_shiny_value)}"
                )
            if actual_shiny_value not in selected_missing_shiny_values(config, ledger):
                if runtime_sid_commit_offset == previous_offset:
                    # Avoid a silent loop when an unexplained SID lands on a
                    # value already present in the ledger or outside this
                    # run's explicit repair selection.
                    raise RuntimeError(
                        "Observed SID mapped to already-complete or unselected shiny value "
                        f"{common.format_shiny_value(actual_shiny_value)} and "
                        "no nearby SID commit offset adjustment was found"
                    )
                forecast = build_forecast_for_missing(
                    config,
                    branch_rng=branch_rng,
                    sid_commit_offset=runtime_sid_commit_offset,
                    ledger=ledger,
                )
                common.write_json_atomic(config.ledger_path, ledger)
                continue

            attempt_plan = observed_plan_for_ledger(
                forecast_plan=plan,
                tid=tid_after,
                sid=sid_after,
                shiny_value=actual_shiny_value,
                sid_commit_offset=runtime_sid_commit_offset,
                rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
            )
            input_tape.replay_tape(live_core, route_tape, use_batch=True)
            verify_live_ids_after_tape(
                config,
                live_core,
                expected_tid=tid_after,
                expected_sid=sid_after,
            )
            save_path = save_path_for_hit(config.output_dir, actual_shiny_value, sid_after)
            if save_path.exists() and not config.overwrite:
                raise FileExistsError(f"{save_path} exists; pass --overwrite or --resume")
            save_sha1 = export_battery_save_atomic(
                live_core,
                save_path,
                expected_tid=tid_after,
                expected_sid=sid_after,
            )
            ledger_plan = attempt_plan
            note = None
            if actual_shiny_value != plan.predicted_shiny_value:
                note = (
                    "accepted actual missing shiny value after live SID observation; "
                    f"planned {common.format_shiny_value(plan.predicted_shiny_value)}"
                )
            common.mark_ledger_hit(
                ledger,
                shiny_value=actual_shiny_value,
                tid=tid_after,
                sid=sid_after,
                save_path=save_path,
                save_sha1=save_sha1,
                plan=ledger_plan,
                sid_commit_offset=runtime_sid_commit_offset,
                note=note,
            )
            processed += 1
            common.write_json_atomic(config.ledger_path, ledger)
            log_live_status(
                config,
                "saved "
                f"tsv={actual_shiny_value} sid={sid_after} path={save_path.name} "
                f"processed={processed}",
            )
            if offset_changed or actual_shiny_value != plan.predicted_shiny_value:
                forecast = build_forecast_for_missing(
                    config,
                    branch_rng=branch_rng,
                    sid_commit_offset=runtime_sid_commit_offset,
                    ledger=ledger,
                )
            else:
                forecast = prune_forecast_to_selected_missing(config, ledger, forecast)
                write_forecast(
                    config,
                    branch_rng=branch_rng,
                    sid_commit_offset=runtime_sid_commit_offset,
                    forecast=forecast,
                )
        except SaveExportValidationError as exc:
            # A failed temp export means the SID was hit, but the routed save
            # did not expose a trustworthy Bulbasaur proof. Try later LCRNG
            # hits for the same TSV before blocking this row for the process.
            common.mark_ledger_error(ledger, shiny_value=attempt_shiny_value, error=str(exc))
            common.write_json_atomic(config.ledger_path, ledger)
            error_count = export_error_counts.get(attempt_shiny_value, 0) + 1
            export_error_counts[attempt_shiny_value] = error_count
            forecast = [
                row
                for row in forecast
                if not (
                    row.shiny_value == attempt_shiny_value
                    and row.wait_frames == attempt_plan.wait_frames
                )
            ]
            if error_count <= EXPORT_VALIDATION_ALTERNATE_HIT_LIMIT:
                try:
                    retry_plan = next_export_retry_plan(
                        config,
                        branch_rng=branch_rng,
                        sid_commit_offset=runtime_sid_commit_offset,
                        failed_plan=attempt_plan,
                    )
                except Exception as retry_exc:  # noqa: BLE001 - row remains retryable.
                    log_live_status(
                        config,
                        "recoverable export error "
                        f"tsv={attempt_shiny_value} sid={attempt_sid} "
                        f"retry_plan_failed={retry_exc} error={exc}",
                    )
                else:
                    export_alternate_retries += 1
                    forecast = [
                        row
                        for row in forecast
                        if row.shiny_value != retry_plan.shiny_value
                    ]
                    forecast.insert(0, retry_plan)
                    write_forecast(
                        config,
                        branch_rng=branch_rng,
                        sid_commit_offset=runtime_sid_commit_offset,
                        forecast=forecast,
                    )
                    log_live_status(
                        config,
                        "recoverable export error "
                        f"tsv={attempt_shiny_value} sid={attempt_sid} "
                        f"retrying_wait={retry_plan.wait_frames} "
                        f"retry_sid={retry_plan.predicted_sid} "
                        f"retry_count={error_count} error={exc}",
                    )
                    continue
            blocked_export_errors.add(attempt_shiny_value)
            log_live_status(
                config,
                "recoverable export error "
                f"tsv={attempt_shiny_value} sid={attempt_sid} "
                f"blocked_this_run={len(blocked_export_errors)} error={exc}",
            )
            continue
        except Exception as exc:  # noqa: BLE001 - long runs need ledger breadcrumbs.
            common.mark_ledger_error(ledger, shiny_value=plan.shiny_value, error=str(exc))
            common.write_json_atomic(config.ledger_path, ledger)
            raise

    elapsed = time.monotonic() - started
    summary = common.ledger_summary(ledger)
    summary.update(
        {
            "mode": "live",
            "target_tid": common.format_u16(config.target_tid),
            "processed_this_run": processed,
            "skipped_this_run": skipped,
            "recalibrations_this_run": recalibrations,
            "export_alternate_retries_this_run": export_alternate_retries,
            "blocked_export_errors_this_run": len(blocked_export_errors),
            "sid_commit_offset": runtime_sid_commit_offset,
            "elapsed_seconds": round(elapsed, 3),
            "ledger_path": str(config.ledger_path),
            "forecast_path": str(config.forecast_path),
            "sid_coverage_path": str(config.sid_coverage_path),
            "raw_lcrng_all_sids_hit": sid_coverage["all_sids_hit"],
            "raw_lcrng_sid_frames_scanned": sid_coverage["frames_scanned"],
            "raw_lcrng_max_wait_for_all_sids": sid_coverage["max_wait_for_all_sids"],
            "runtime_settings": runtime_settings,
        }
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    config = config_from_args(build_parser().parse_args(argv))
    result = generate_secret_id_saves(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    # mGBA's embedded runner logs SystemExit(0) as an error, so return normally.
    main()
