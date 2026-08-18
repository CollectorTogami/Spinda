#!/usr/bin/env python3
r"""Precalculate SID and TSV delays from the root ``tid 0 ready.ss0`` state.

This script is a read-only companion to ``Secret-ID-Shiny-Value-Bot.py``. It
loads a prepared savestate that is one accepted ``A`` press away from FR/LG SID
generation, proves that the final TID remains ``0``, infers the branch-relative
SID commit offset from live observations, and writes earliest-delay ledgers for
all 65,536 SIDs and all 8,192 trainer shiny values.

The default state is:

    <repo-root>\tid 0 ready.ss0

The state is never overwritten. Live probing uses mGBA scratch/state loads only,
then emits JSON into ``<repo-root>\TSVs``.

Important timing fact for this specific state:

    rng_advance = sid_commit_offset + max(wait_frames, 1)

The first neutral frame acts as the release edge required before the final ``A``
can be consumed, so wait ``0`` and wait ``1`` intentionally map to the same SID.
Starting at wait ``2``, each extra neutral frame advances the LCRNG by one.
"""

from __future__ import annotations

import argparse
import json
import sys
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


DEFAULT_STATE_PATH = Path(__file__).resolve().parents[3] / "tid 0 ready.ss0"
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "TSVs"
DEFAULT_REPORT_NAME = "_sid_ready_tid0_precalc_report.json"
DEFAULT_SID_DELAY_NAME = "_sid_ready_tid0_all_sid_delays.json"
DEFAULT_TSV_DELAY_NAME = "_sid_ready_tid0_all_tsv_delays.json"
DEFAULT_WAIT_PROBES = (0, 1, 2, 3, 4, 10, 20, 31, 32, 100, 257)
DEFAULT_OFFSET_SEARCH_LIMIT = 512
DEFAULT_MAX_SCAN_FRAMES = 2_000_000
DEFAULT_POST_COMMIT_VERIFY_FRAMES = 360
WAIT_ADVANCE_FORMULA = "sid_commit_offset + max(wait_frames, 1)"
SID_TARGET_COUNT = 1 << 16


@dataclass(frozen=True)
class ReadyStatePrecalcConfig:
    """Runtime settings for one read-only ready-state proof and precalc."""

    state_path: Path
    output_dir: Path
    target_tid: int
    final_button: str
    final_press_frames: int
    post_commit_verify_frames: int
    wait_probes: tuple[int, ...]
    offset_search_limit: int
    max_scan_frames: int

    @property
    def report_path(self) -> Path:
        """Return summary report path."""

        return self.output_dir / DEFAULT_REPORT_NAME

    @property
    def sid_delay_path(self) -> Path:
        """Return all-SID delay table path."""

        return self.output_dir / DEFAULT_SID_DELAY_NAME

    @property
    def tsv_delay_path(self) -> Path:
        """Return all-TSV delay table path."""

        return self.output_dir / DEFAULT_TSV_DELAY_NAME


def parse_wait_probe_list(raw: str) -> tuple[int, ...]:
    """Parse comma-separated wait probes."""

    values = tuple(int(part.strip(), 0) for part in str(raw).split(",") if part.strip())
    if not values:
        raise ValueError("at least one wait probe is required")
    if any(value < 0 for value in values):
        raise ValueError("wait probes must be non-negative")
    return values


def build_parser() -> argparse.ArgumentParser:
    """Create CLI parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE_PATH)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--target-tid", type=common.parse_int, default=0)
    parser.add_argument("--final-button", default="A")
    parser.add_argument("--final-press-frames", type=int, default=2)
    parser.add_argument("--post-commit-verify-frames", type=int, default=DEFAULT_POST_COMMIT_VERIFY_FRAMES)
    parser.add_argument("--wait-probes", default=",".join(str(value) for value in DEFAULT_WAIT_PROBES))
    parser.add_argument("--offset-search-limit", type=int, default=DEFAULT_OFFSET_SEARCH_LIMIT)
    parser.add_argument("--max-scan-frames", type=int, default=DEFAULT_MAX_SCAN_FRAMES)
    return parser


def config_from_args(args: argparse.Namespace) -> ReadyStatePrecalcConfig:
    """Normalize CLI args."""

    target_tid = common.checked_u16(args.target_tid, name="target_tid")
    if target_tid != 0:
        raise ValueError("--target-tid is fixed at 0 for this ready-state precalc")
    if int(args.final_press_frames) < 1:
        raise ValueError("--final-press-frames must be positive")
    if int(args.post_commit_verify_frames) < 0:
        raise ValueError("--post-commit-verify-frames must be non-negative")
    if int(args.offset_search_limit) < 0:
        raise ValueError("--offset-search-limit must be non-negative")
    if int(args.max_scan_frames) < 0:
        raise ValueError("--max-scan-frames must be non-negative")
    return ReadyStatePrecalcConfig(
        state_path=Path(args.state),
        output_dir=Path(args.output_dir),
        target_tid=target_tid,
        final_button=str(args.final_button),
        final_press_frames=int(args.final_press_frames),
        post_commit_verify_frames=int(args.post_commit_verify_frames),
        wait_probes=parse_wait_probe_list(args.wait_probes),
        offset_search_limit=int(args.offset_search_limit),
        max_scan_frames=int(args.max_scan_frames),
    )


def optional_bool_attr(core: Any, name: str) -> bool | None:
    """Read an optional boolean bridge property or getter."""

    try:
        value = getattr(core, name)
    except Exception:  # noqa: BLE001 - mGBA bridge support differs by build.
        return None
    if callable(value):
        try:
            value = value()
        except Exception:  # noqa: BLE001 - unsupported getter.
            return None
    return bool(value)


def ensure_core_toggle(core: Any, *, getter_name: str, setter_name: str) -> str:
    """Enable one optional Qt boolean feature."""

    enabled = optional_bool_attr(core, getter_name)
    if enabled is True:
        return "already-enabled"
    setter = getattr(core, setter_name, None)
    if not callable(setter):
        return "unavailable"
    setter(True)
    return "enabled"


def configure_qt_runtime(core: Any) -> dict[str, str]:
    """Enable speed-friendly Qt options while proving the ready state."""

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


def call_core(core: Any, name: str, *args: Any) -> Any:
    """Call a required mGBA bridge method and reject false returns."""

    fn = getattr(core, name, None)
    if not callable(fn):
        raise RuntimeError(f"current core does not expose {name}")
    result = fn(*args)
    if result is False:
        raise RuntimeError(f"{name} returned false for {args!r}")
    return result


def run_neutral_frames(core: Any, frames: int) -> None:
    """Run exact neutral frames."""

    if int(frames) < 0:
        raise ValueError("frames must be non-negative")
    input_tape.run_exact_frames(core, 0, int(frames), use_batch=True)


def read_ids(core: Any) -> dict[str, Any] | None:
    """Read SaveBlock2 trainer IDs when available."""

    try:
        tid, sid = common.read_trainer_id_from_saveblock2(core)
    except Exception:
        return None
    tsv = common.shiny_value_from_tid_sid(tid, sid)
    return {
        "tid": common.format_u16(tid),
        "tid_decimal": tid,
        "sid": common.format_u16(sid),
        "sid_decimal": sid,
        "tsv": common.format_shiny_value(tsv),
        "tsv_decimal": tsv,
    }


def read_branch_header(core: Any) -> dict[str, Any]:
    """Read branch memory values once for report/debug proof."""

    rng = common.read_rng_state(core)
    header: dict[str, Any] = {
        "rng": common.format_u32(rng),
        "rng_decimal": rng,
        "saveblock_ids": read_ids(core),
    }
    try:
        initial_tid = common.read_initial_tid_mirror(core)
        header["initial_tid_mirror"] = common.format_u16(initial_tid)
        header["initial_tid_mirror_decimal"] = initial_tid
    except Exception as exc:  # noqa: BLE001 - report unsupported/unready memory.
        header["initial_tid_mirror_error"] = f"{type(exc).__name__}: {exc}"
    try:
        header["timer1_running"] = common.timer1_running(core)
    except Exception as exc:  # noqa: BLE001
        header["timer1_running_error"] = f"{type(exc).__name__}: {exc}"
    return header


def effective_wait_advance(wait_frames: int) -> int:
    """Return LCRNG wait contribution for the ready-state release edge."""

    if int(wait_frames) < 0:
        raise ValueError("wait_frames must be non-negative")
    return max(int(wait_frames), 1)


def rng_advance_for_wait(*, wait_frames: int, sid_commit_offset: int) -> int:
    """Return branch-relative RNG advance for one wait."""

    return int(sid_commit_offset) + effective_wait_advance(wait_frames)


def next_lcrng_state(state: int) -> int:
    """Advance the GBA LCRNG by one call with minimal overhead."""

    return (
        ((int(state) & common.UINT32_MASK) * common.GBA_LCRNG_MULTIPLIER)
        + common.GBA_LCRNG_INCREMENT
    ) & common.UINT32_MASK


def matching_offsets(
    *,
    branch_rng: int,
    observations: Iterable[dict[str, Any]],
    offset_search_limit: int,
) -> list[int]:
    """Return SID commit offsets that match every live wait/SID observation."""

    rows = tuple(observations)
    matches: list[int] = []
    for offset in range(int(offset_search_limit) + 1):
        for row in rows:
            after_ids = row.get("after_ids")
            if after_ids is None:
                break
            sid = int(after_ids["sid_decimal"])
            wait = int(row["wait_frames"])
            predicted = common.random_u16_from_state(
                common.lcrng_advance(
                    branch_rng,
                    rng_advance_for_wait(wait_frames=wait, sid_commit_offset=offset),
                )
            )
            if predicted != sid:
                break
        else:
            matches.append(offset)
    return matches


def build_delay_tables(
    *,
    state_path: Path,
    branch_rng: int,
    sid_commit_offset: int,
    max_scan_frames: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Scan earliest wait rows for every SID and TSV.

    Wait ``0`` and wait ``1`` intentionally inspect the same RNG state. After
    wait ``1`` the scan advances the LCRNG one call per waited frame.
    """

    seen_sid: dict[int, dict[str, Any]] = {}
    seen_tsv: dict[int, dict[str, Any]] = {}
    state = common.lcrng_advance(
        branch_rng,
        rng_advance_for_wait(wait_frames=0, sid_commit_offset=sid_commit_offset),
    )

    for wait in range(int(max_scan_frames) + 1):
        sid = common.random_u16_from_state(state)
        tsv = common.shiny_value_from_tid_sid(0, sid)
        rng_advance = rng_advance_for_wait(wait_frames=wait, sid_commit_offset=sid_commit_offset)
        if sid not in seen_sid:
            seen_sid[sid] = {
                "sid": common.format_u16(sid),
                "sid_decimal": sid,
                "tsv": common.format_shiny_value(tsv),
                "tsv_decimal": tsv,
                "wait_frames": wait,
                "rng_advance": rng_advance,
                "predicted_rng": common.format_u32(state),
            }
        if tsv not in seen_tsv:
            seen_tsv[tsv] = {
                "tsv": common.format_shiny_value(tsv),
                "tsv_decimal": tsv,
                "sid": common.format_u16(sid),
                "sid_decimal": sid,
                "wait_frames": wait,
                "rng_advance": rng_advance,
                "predicted_rng": common.format_u32(state),
            }
        if len(seen_sid) == SID_TARGET_COUNT and len(seen_tsv) == common.SHINY_VALUE_COUNT:
            break
        if wait >= 1:
            state = next_lcrng_state(state)

    sid_rows = sorted(seen_sid.values(), key=lambda row: int(row["wait_frames"]))
    tsv_rows = sorted(seen_tsv.values(), key=lambda row: int(row["wait_frames"]))
    sid_max = int(sid_rows[-1]["wait_frames"]) if sid_rows else None
    tsv_max = int(tsv_rows[-1]["wait_frames"]) if tsv_rows else None

    sid_payload = {
        "format": "frlg-sid-ready-tid0-all-sid-delays-v1",
        "state_path": str(state_path),
        "target_tid": common.format_u16(0),
        "target_tid_decimal": 0,
        "branch_rng": common.format_u32(branch_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_wait_frame_after_release": 1,
        "wait_to_rng_advance_formula": WAIT_ADVANCE_FORMULA,
        "sid_count": len(sid_rows),
        "all_sids_hit": len(sid_rows) == SID_TARGET_COUNT,
        "max_wait_frames": sid_max,
        "rows": sid_rows,
    }
    tsv_payload = {
        "format": "frlg-sid-ready-tid0-all-tsv-delays-v1",
        "state_path": str(state_path),
        "target_tid": common.format_u16(0),
        "target_tid_decimal": 0,
        "branch_rng": common.format_u32(branch_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_wait_frame_after_release": 1,
        "wait_to_rng_advance_formula": WAIT_ADVANCE_FORMULA,
        "tsv_count": len(tsv_rows),
        "all_tsvs_hit": len(tsv_rows) == common.SHINY_VALUE_COUNT,
        "max_wait_frames": tsv_max,
        "rows": tsv_rows,
    }
    return sid_payload, tsv_payload


def write_json(path: Path, payload: dict[str, Any]) -> None:
    """Write stable JSON, retrying transient Windows file locks."""

    common.write_json_atomic(path, payload)


def commit_sid(config: ReadyStatePrecalcConfig, core: Any) -> None:
    """Press final input and wait long enough for SaveBlock2 to update."""

    mask = input_tape.mask_from_buttons(config.final_button)
    input_tape.run_exact_frames(core, mask, config.final_press_frames, use_batch=True)
    input_tape.set_exact_keys(core, 0)
    run_neutral_frames(core, config.post_commit_verify_frames)


def probe_live_ready_state(config: ReadyStatePrecalcConfig, core: Any) -> dict[str, Any]:
    """Load the read-only savestate repeatedly and gather live SID observations."""

    call_core(core, "load_state_file", config.state_path)
    input_tape.set_exact_keys(core, 0)
    header = read_branch_header(core)
    branch_rng = int(header["rng_decimal"])
    observations: list[dict[str, Any]] = []

    for wait in config.wait_probes:
        call_core(core, "load_state_file", config.state_path)
        input_tape.set_exact_keys(core, 0)
        before = read_branch_header(core)
        run_neutral_frames(core, wait)
        try:
            commit_sid(config, core)
            after_ids = read_ids(core)
            error = None
        except Exception as exc:  # noqa: BLE001 - keep failed proof in report.
            after_ids = read_ids(core)
            error = f"{type(exc).__name__}: {exc}"
        observations.append(
            {
                "wait_frames": wait,
                "before": before,
                "after_ids": after_ids,
                "after_rng": common.format_u32(common.read_rng_state(core)),
                "error": error,
            }
        )

    return {
        "header": header,
        "branch_rng": branch_rng,
        "observations": observations,
    }


def validate_observations(
    *,
    config: ReadyStatePrecalcConfig,
    branch_rng: int,
    observations: list[dict[str, Any]],
) -> tuple[int, list[int]]:
    """Validate live proof and return chosen SID commit offset."""

    tid_ok = all(
        row.get("after_ids") is not None and int(row["after_ids"]["tid_decimal"]) == config.target_tid
        for row in observations
    )
    sid_values = {
        int(row["after_ids"]["sid_decimal"])
        for row in observations
        if row.get("after_ids") is not None
    }
    offsets = matching_offsets(
        branch_rng=branch_rng,
        observations=observations,
        offset_search_limit=config.offset_search_limit,
    )
    if not tid_ok:
        raise RuntimeError("loaded state did not keep final TID at 0 for every probe")
    if len(sid_values) <= 1:
        raise RuntimeError("loaded state is not wait-sensitive; probe waits produced one SID")
    if not offsets:
        raise RuntimeError("could not infer an LCRNG SID commit offset from probes")
    return offsets[0], offsets


def build_success_report(
    *,
    config: ReadyStatePrecalcConfig,
    state_size_bytes: int,
    runtime_settings: dict[str, str],
    header: dict[str, Any],
    observations: list[dict[str, Any]],
    offsets: list[int],
    sid_payload: dict[str, Any],
    tsv_payload: dict[str, Any],
) -> dict[str, Any]:
    """Return compact proof report for the generated delay tables."""

    sid_commit_offset = int(sid_payload["sid_commit_offset"])
    return {
        "format": "frlg-sid-ready-tid0-precalc-report-v1",
        "state_path": str(config.state_path),
        "state_size_bytes": state_size_bytes,
        "runtime_settings": runtime_settings,
        "header": header,
        "observations": observations,
        "matching_sid_commit_offsets": offsets,
        "sid_commit_offset": sid_commit_offset,
        "wait_to_rng_advance_formula": WAIT_ADVANCE_FORMULA,
        "wait_sensitive": True,
        "tid0_all_probes": True,
        "sid_delay_path": str(config.sid_delay_path),
        "tsv_delay_path": str(config.tsv_delay_path),
        "sid_count": sid_payload["sid_count"],
        "all_sids_hit": sid_payload["all_sids_hit"],
        "max_wait_for_all_sids": sid_payload["max_wait_frames"],
        "tsv_count": tsv_payload["tsv_count"],
        "all_tsvs_hit": tsv_payload["all_tsvs_hit"],
        "max_wait_for_all_tsvs": tsv_payload["max_wait_frames"],
    }


def generate_ready_state_precalc(config: ReadyStatePrecalcConfig) -> dict[str, Any]:
    """Run live proof, write delay tables, and return the summary report."""

    if not config.state_path.exists():
        raise FileNotFoundError(config.state_path)

    try:
        import mgba.qt  # type: ignore
    except Exception as exc:  # noqa: BLE001 - command failure should be explicit.
        raise RuntimeError("mgba.qt is unavailable; run inside the Python-enabled Qt build") from exc

    core = mgba.qt.current_core()
    runtime_settings = configure_qt_runtime(core)
    live = probe_live_ready_state(config, core)
    try:
        sid_commit_offset, offsets = validate_observations(
            config=config,
            branch_rng=live["branch_rng"],
            observations=live["observations"],
        )
    except Exception as exc:
        payload = {
            "format": "frlg-sid-ready-tid0-precalc-error-v1",
            "state_path": str(config.state_path),
            "runtime_settings": runtime_settings,
            "header": live["header"],
            "observations": live["observations"],
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        write_json(config.report_path, payload)
        raise

    sid_payload, tsv_payload = build_delay_tables(
        state_path=config.state_path,
        branch_rng=live["branch_rng"],
        sid_commit_offset=sid_commit_offset,
        max_scan_frames=config.max_scan_frames,
    )
    write_json(config.sid_delay_path, sid_payload)
    write_json(config.tsv_delay_path, tsv_payload)
    report = build_success_report(
        config=config,
        state_size_bytes=config.state_path.stat().st_size,
        runtime_settings=runtime_settings,
        header=live["header"],
        observations=live["observations"],
        offsets=offsets,
        sid_payload=sid_payload,
        tsv_payload=tsv_payload,
    )
    write_json(config.report_path, report)
    return report


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for mGBA Qt."""

    config = config_from_args(build_parser().parse_args(argv))
    try:
        result = generate_ready_state_precalc(config)
    except Exception as exc:  # noqa: BLE001 - avoid scary mGBA SystemExit/error UI.
        if config.report_path.exists():
            print(config.report_path.read_text(encoding="utf-8"), flush=True)
        else:
            payload = {
                "format": "frlg-sid-ready-tid0-precalc-error-v1",
                "state_path": str(config.state_path),
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            write_json(config.report_path, payload)
            print(json.dumps(payload, indent=2, sort_keys=True), flush=True)
        return 1
    print(json.dumps(result, indent=2, sort_keys=True), flush=True)
    return 0


if __name__ == "__main__":
    main()
