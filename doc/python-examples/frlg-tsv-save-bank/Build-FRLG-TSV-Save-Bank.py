#!/usr/bin/env python3
r"""Build the FR/LG Trainer Shiny Value save bank.

This script is for the final extra-save side project: one LeafGreen/FireRed save
per Trainer Shiny Value, so any Spinda egg from the main corpus can be hatched
as shiny by exactly one matching TSV save and non-shiny by the others.

Exports use the current TID0 save-bank convention:
``<repo-root>\TSVs\TSV-xxxx-sid-xxxxx.sav`` with decimal TSV and SID fields.

Live usage is meant for the Python-enabled visible Qt build:

    <repo-root>\build-mingw64-python-qt\mGBA.exe --script ^
      <repo-root>\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py ^
      --post-sid-tape <repo-root>\routes\frlg-post-sid-to-save-point.json

The emulator must already be paused at the final input point before SID is
generated, with the desired TID already hit. The script captures that state in
the in-memory Qt scratch slot, branches from it for every TSV, waits neutral
frames, presses the final input, verifies the generated TID/SID/TSV, replays a
post-SID route tape to a stable save point, and exports a `.sav`.

No ROM, save, or savestate artifact is embedded in this script or in the input
tape. The tape remains an anchor-free route segment.
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

import frlg_tsv_common as common  # noqa: E402
import input_tape  # noqa: E402


DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parents[3] / "TSVs"
DEFAULT_STATUS_NAME = "_frlg_tsv_save_bank_status.json"
DEFAULT_WAIT_PLAN_NAME = "_frlg_tsv_wait_plan.json"
DEFAULT_SAVE_NAME_TEMPLATE = "TSV-{tsv:04d}-sid-{sid:05d}.sav"


@dataclass(frozen=True)
class TsvSaveBankConfig:
    """Runtime configuration for one save-bank generation run."""

    output_dir: Path
    post_sid_tape: Path | None
    tid: int | None
    start_rng: int | None
    sid_commit_offset: int
    rng_advances_per_neutral_frame: int
    commit_button: str
    commit_press_frames: int
    post_commit_verify_frames: int
    limit: int | None
    only_tsvs: tuple[int, ...]
    resume: bool
    overwrite: bool
    trust_predicted_sid: bool
    dry_plan: bool
    max_advances: int | None

    @property
    def status_path(self) -> Path:
        return self.output_dir / DEFAULT_STATUS_NAME

    @property
    def wait_plan_path(self) -> Path:
        return self.output_dir / DEFAULT_WAIT_PLAN_NAME


def build_parser() -> argparse.ArgumentParser:
    """Create the command-line parser."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--post-sid-tape",
        type=Path,
        default=None,
        help="Anchor-free input tape from after SID commit to the stable save/export point.",
    )
    parser.add_argument(
        "--tid",
        type=common.parse_int,
        default=None,
        help="Known 16-bit Trainer ID. If omitted, the live core mirror at 0x02020000 is read.",
    )
    parser.add_argument(
        "--start-rng",
        type=common.parse_int,
        default=None,
        help="gRngValue at the pre-SID branch point. If omitted, live memory is read.",
    )
    parser.add_argument(
        "--sid-commit-offset",
        type=int,
        default=1,
        help="LCRNG calls from final input acceptance to SID Random() when wait_frames=0.",
    )
    parser.add_argument(
        "--rng-advances-per-neutral-frame",
        type=int,
        default=1,
        help="LCRNG calls consumed by one neutral wait frame at the branch point.",
    )
    parser.add_argument("--commit-button", default="A")
    parser.add_argument("--commit-press-frames", type=int, default=1)
    parser.add_argument(
        "--post-commit-verify-frames",
        type=int,
        default=180,
        help="Frames to run after pressing the final input before reading SaveBlock2.",
    )
    parser.add_argument("--limit", type=int, default=None, help="Process only the first N selected TSVs.")
    parser.add_argument(
        "--only-tsv",
        action="append",
        default=[],
        help="Single TSV or inclusive range, e.g. 0x0001 or 0x0010-0x001F. Can repeat.",
    )
    parser.add_argument("--resume", action="store_true", help="Skip completed status rows with existing saves.")
    parser.add_argument("--overwrite", action="store_true", help="Replace existing TSV save files.")
    parser.add_argument(
        "--trust-predicted-sid",
        action="store_true",
        help="Use planner SID instead of SaveBlock2 verification. Intended only for dry bring-up.",
    )
    parser.add_argument(
        "--dry-plan",
        action="store_true",
        help="Write wait-plan/status JSON only. Does not require or drive mGBA.",
    )
    parser.add_argument(
        "--max-advances",
        type=int,
        default=None,
        help="Optional safety cap for wait-plan search.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> TsvSaveBankConfig:
    """Normalize parsed arguments into an immutable config."""

    return TsvSaveBankConfig(
        output_dir=Path(args.output_dir),
        post_sid_tape=Path(args.post_sid_tape) if args.post_sid_tape else None,
        tid=args.tid,
        start_rng=args.start_rng,
        sid_commit_offset=int(args.sid_commit_offset),
        rng_advances_per_neutral_frame=int(args.rng_advances_per_neutral_frame),
        commit_button=str(args.commit_button),
        commit_press_frames=int(args.commit_press_frames),
        post_commit_verify_frames=int(args.post_commit_verify_frames),
        limit=args.limit,
        only_tsvs=tuple(parse_tsv_selection(args.only_tsv)),
        resume=bool(args.resume),
        overwrite=bool(args.overwrite),
        trust_predicted_sid=bool(args.trust_predicted_sid),
        dry_plan=bool(args.dry_plan),
        max_advances=args.max_advances,
    )


def parse_tsv_selection(values: Iterable[str]) -> list[int]:
    """Parse repeated ``--only-tsv`` selectors."""

    selected: set[int] = set()
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        if "-" in text:
            start_text, end_text = text.split("-", 1)
            start = common.parse_int(start_text)
            end = common.parse_int(end_text)
            if end < start:
                raise ValueError(f"TSV range is backwards: {text}")
            selected.update(range(start, end + 1))
        else:
            selected.add(common.parse_int(text))
    for tsv in selected:
        if not 0 <= tsv < common.TSV_COUNT:
            raise ValueError(f"TSV out of range: {common.format_tsv(tsv)}")
    return sorted(selected)


def save_path_for_tsv(output_dir: Path, tsv: int, sid: int) -> Path:
    """Return the decimal TSV/SID save path for one exported branch."""

    return Path(output_dir) / DEFAULT_SAVE_NAME_TEMPLATE.format(tsv=int(tsv), sid=int(sid))


def current_qt_core() -> Any:
    """Return the visible Qt core handle, imported lazily for source tests."""

    try:
        import mgba.qt  # type: ignore
    except Exception as exc:  # noqa: BLE001 - command-line failure should be explicit.
        raise RuntimeError("mgba.qt is unavailable; run this in the Python-enabled Qt build") from exc
    return mgba.qt.current_core()


def resolve_tid_and_rng(config: TsvSaveBankConfig, core: Any | None) -> tuple[int, int]:
    """Resolve the TID and pre-SID gRngValue from CLI or live memory."""

    if config.tid is not None:
        tid = int(config.tid) & common.UINT16_MASK
    elif core is not None:
        tid = common.read_tid_from_initial_mirror(core)
    else:
        raise RuntimeError("--tid is required for --dry-plan when no core is available")

    if config.start_rng is not None:
        start_rng = int(config.start_rng) & common.UINT32_MASK
    elif core is not None:
        start_rng = common.read_rng_state(core)
    else:
        raise RuntimeError("--start-rng is required for --dry-plan when no core is available")

    return tid, start_rng


def build_and_write_plan(config: TsvSaveBankConfig, *, tid: int, start_rng: int) -> list[common.TsvWaitPlanEntry]:
    """Build the wait plan and persist both plan JSON and initial status JSON."""

    plan = common.build_wait_plan(
        tid=tid,
        start_rng=start_rng,
        sid_commit_offset=config.sid_commit_offset,
        rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
        max_advances=config.max_advances,
    )
    plan_document = {
        "format": "frlg-tsv-wait-plan-v1",
        "tid": common.format_u16(tid),
        "start_rng": common.format_u32(start_rng),
        "sid_commit_offset": config.sid_commit_offset,
        "rng_advances_per_neutral_frame": config.rng_advances_per_neutral_frame,
        "target_tsvs": len(plan),
        "plan_digest": common.plan_digest(plan),
        "entries": common.as_json_rows(plan),
    }
    common.write_json_atomic(config.wait_plan_path, plan_document)

    if not (config.resume and config.status_path.exists()):
        status = common.new_status(
            plan=plan,
            tid=tid,
            start_rng=start_rng,
            sid_commit_offset=config.sid_commit_offset,
            rng_advances_per_neutral_frame=config.rng_advances_per_neutral_frame,
        )
        common.write_json_atomic(config.status_path, status)
    return plan


def select_plan_entries(
    plan: Iterable[common.TsvWaitPlanEntry],
    *,
    only_tsvs: Iterable[int] = (),
    limit: int | None = None,
) -> list[common.TsvWaitPlanEntry]:
    """Select and sort plan rows for execution."""

    only = set(int(tsv) for tsv in only_tsvs)
    rows = [entry for entry in plan if not only or entry.tsv in only]
    rows.sort(key=lambda entry: (entry.wait_frames, entry.tsv))
    if limit is not None:
        rows = rows[: int(limit)]
    return rows


def load_input_tape(path: Path | None) -> input_tape.InputTape:
    """Load the required post-SID route tape."""

    if path is None:
        raise RuntimeError("--post-sid-tape is required for a live save-bank run")
    return input_tape.load(path)


def capture_branch_state(core: Any) -> None:
    """Capture the current pre-SID checkpoint into the Qt scratch slot."""

    save_scratch = getattr(core, "save_scratch_state", None)
    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(save_scratch) or not callable(load_scratch):
        raise RuntimeError("current core must expose save_scratch_state/load_scratch_state")
    save_scratch()


def restore_branch_state(core: Any) -> None:
    """Restore the pre-SID checkpoint from the Qt scratch slot."""

    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(load_scratch):
        raise RuntimeError("current core does not expose load_scratch_state")
    load_scratch()


def run_neutral_frames(core: Any, frames: int) -> None:
    """Run neutral-input frames with the fastest exact-key path available."""

    if frames < 0:
        raise ValueError("frames must be non-negative")
    if frames == 0:
        return
    input_tape.run_exact_frames(core, 0, int(frames), use_batch=True)


def commit_sid(config: TsvSaveBankConfig, core: Any) -> None:
    """Press the final input that allows SID generation, then wait to verify."""

    mask = input_tape.mask_from_buttons(config.commit_button)
    input_tape.run_exact_frames(core, mask, config.commit_press_frames, use_batch=True)
    input_tape.set_exact_keys(core, 0)
    run_neutral_frames(core, config.post_commit_verify_frames)


def read_or_predict_final_ids(
    config: TsvSaveBankConfig,
    core: Any,
    entry: common.TsvWaitPlanEntry,
) -> tuple[int, int]:
    """Return final TID/SID, preferring live SaveBlock2 verification."""

    if config.trust_predicted_sid:
        return entry.predicted_tid, entry.predicted_sid
    return common.read_trainer_id_from_saveblock2(core)


def export_save_atomic(core: Any, path: Path) -> str:
    """Export a `.sav` through `.tmp` and return its SHA-1."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    if tmp.exists():
        tmp.unlink()
    export = getattr(core, "export_save_file", None)
    if not callable(export):
        raise RuntimeError("current core does not expose export_save_file")
    export(tmp)
    tmp.replace(path)
    return common.sha1_file(path)


def should_skip_entry(config: TsvSaveBankConfig, status: dict[str, Any], entry: common.TsvWaitPlanEntry) -> bool:
    """Return true when resume can skip a completed TSV safely."""

    if not config.resume:
        return False
    row = common.status_entries_by_tsv(status).get(entry.tsv)
    if row is None or not row.done or not row.save_path:
        return False
    return Path(row.save_path).is_file()


def generate_tsv_save_bank(config: TsvSaveBankConfig, *, core: Any | None = None) -> dict[str, Any]:
    """Run the full TSV save-bank workflow."""

    live_core = core if core is not None else (None if config.dry_plan else current_qt_core())
    tid, start_rng = resolve_tid_and_rng(config, live_core)
    plan = build_and_write_plan(config, tid=tid, start_rng=start_rng)
    if config.dry_plan:
        return {
            "mode": "dry-plan",
            "tid": common.format_u16(tid),
            "start_rng": common.format_u32(start_rng),
            "target_tsvs": len(plan),
            "wait_plan_path": str(config.wait_plan_path),
            "status_path": str(config.status_path),
        }
    if live_core is None:
        raise RuntimeError("live core is required unless --dry-plan is set")

    route_tape = load_input_tape(config.post_sid_tape)
    status = common.read_json(config.status_path)
    selected_entries = select_plan_entries(plan, only_tsvs=config.only_tsvs, limit=config.limit)
    capture_branch_state(live_core)

    started = time.monotonic()
    processed = 0
    skipped = 0
    failed = 0
    for entry in selected_entries:
        if should_skip_entry(config, status, entry):
            skipped += 1
            continue

        try:
            restore_branch_state(live_core)
            run_neutral_frames(live_core, entry.wait_frames)
            commit_sid(config, live_core)
            tid_after, sid_after = read_or_predict_final_ids(config, live_core, entry)
            actual_tsv = common.tsv_from_tid_sid(tid_after, sid_after)
            if actual_tsv != entry.tsv:
                raise RuntimeError(
                    f"TSV mismatch: wanted {common.format_tsv(entry.tsv)}, "
                    f"got {common.format_tsv(actual_tsv)} from "
                    f"TID={common.format_u16(tid_after)} SID={common.format_u16(sid_after)}"
                )
            save_path = save_path_for_tsv(config.output_dir, entry.tsv, sid_after)
            if save_path.exists() and not config.overwrite:
                raise FileExistsError(f"{save_path} already exists; pass --overwrite or --resume")
            input_tape.replay_tape(live_core, route_tape, use_batch=True)
            save_sha1 = export_save_atomic(live_core, save_path)
            common.mark_status_hit(
                status,
                tsv=entry.tsv,
                tid=tid_after,
                sid=sid_after,
                save_path=save_path,
                save_sha1=save_sha1,
            )
            processed += 1
        except Exception as exc:  # noqa: BLE001 - long live runs need TSV-specific status.
            failed += 1
            common.mark_status_error(status, tsv=entry.tsv, error=str(exc))
            common.write_json_atomic(config.status_path, status)
            raise
        common.write_json_atomic(config.status_path, status)

    elapsed = time.monotonic() - started
    summary = common.status_summary(status)
    summary.update(
        {
            "mode": "live",
            "processed_this_run": processed,
            "skipped_this_run": skipped,
            "failed_this_run": failed,
            "elapsed_seconds": round(elapsed, 3),
            "status_path": str(config.status_path),
            "wait_plan_path": str(config.wait_plan_path),
        }
    )
    return summary


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""

    config = config_from_args(build_parser().parse_args(argv))
    result = generate_tsv_save_bank(config)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
