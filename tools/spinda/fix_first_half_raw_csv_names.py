"""Rename first-half raw-CSV artifacts to live FR/LG daycare half names.

The first full hitter run wrote raw CSV target names:

    saves/0x0000.sav
    states/0x0000.ss0

FR/LG stores the pending daycare lower half as ``((raw_half % 0xFFFE) + 1)``.
This tool converts filenames to that live value without opening mGBA. It uses
a two-phase temporary rename so cyclic shifts such as ``0x0000 -> 0x0001`` and
``0x0001 -> 0x0002`` cannot overwrite still-unmoved files.
"""

from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


DEFAULT_RAW_CSV_DIR = Path(__file__).resolve().parents[2] / "1sthalves"
EXPECTED_TARGETS = 0x10000
NAME_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.(sav|ss0)$")
LAYOUT_AUTO = "auto"
LAYOUT_FLAT = "flat"
LAYOUT_SPLIT = "split"
COLLISION_QUARANTINE = "quarantine"
COLLISION_SUFFIX = "suffix"
COLLISION_FAIL = "fail"


class RenamePlanError(RuntimeError):
    """Raised when a safe two-phase rename plan cannot be built."""


@dataclass(frozen=True)
class RenameAction:
    """One source -> temp -> target rename action."""

    kind: str
    raw_half: int
    live_half: int
    source: str
    temp: str
    target: str
    collision: bool


@dataclass(frozen=True)
class ScannedArtifact:
    """One exact raw-CSV artifact discovered before planning."""

    source: Path
    main_dir: Path
    extension: str
    raw_half: int
    kind: str


@dataclass(frozen=True)
class RenameSummary:
    """Compact plan/execute summary for console and JSON output."""

    root: str
    layout: str
    collision_policy: str
    actions: int
    save_actions: int
    state_actions: int
    main_actions: int
    collision_actions: int
    source_files: int
    expected_targets: int
    report_path: str


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments."""

    parser = argparse.ArgumentParser(
        description=(
            "Rename first-half raw-CSV .sav/.ss0 files to live FR/LG daycare "
            "half filenames using a two-phase temp rename."
        ),
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("folder", nargs="?", type=Path, default=DEFAULT_RAW_CSV_DIR)
    parser.add_argument(
        "--layout",
        choices=(LAYOUT_AUTO, LAYOUT_FLAT, LAYOUT_SPLIT),
        default=LAYOUT_AUTO,
        help="Scan flat files, split saves/states folders, or auto-detect.",
    )
    parser.add_argument(
        "--collision-policy",
        choices=(COLLISION_QUARANTINE, COLLISION_SUFFIX, COLLISION_FAIL),
        default=COLLISION_QUARANTINE,
        help=(
            "How to preserve duplicate raw halves that convert to the same live "
            "daycare half. Quarantine keeps exact live names in the main folders."
        ),
    )
    parser.add_argument(
        "--expected-targets",
        type=int,
        default=EXPECTED_TARGETS,
        help="Expected raw target count per side when --require-complete is used.",
    )
    parser.add_argument(
        "--require-complete",
        action="store_true",
        help="Refuse to execute unless both .sav and .ss0 sides have expected target count.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually rename files. Without this flag, only a dry-run report is written.",
    )
    parser.add_argument(
        "--resume-report",
        type=Path,
        default=None,
        help=(
            "Resume an interrupted run from an existing JSON report. With --execute, "
            "finishes source/temp/target actions idempotently."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Optional JSON report path. Default writes under <folder>/_rename_reports.",
    )
    parser.add_argument(
        "--temp-token",
        default=None,
        help="Optional deterministic temp token for tests. Defaults to a UUID.",
    )
    parser.add_argument("--json", action="store_true", help="Emit summary as JSON.")
    return parser.parse_args(argv)


def format_half(value: int) -> str:
    """Return canonical four-digit hex half."""

    return f"0x{value:04X}"


def raw_half_to_live_half(raw_half: int) -> int:
    """Convert raw CSV Random() half to FR/LG pending daycare lower half."""

    return (raw_half % 0xFFFE) + 1


def canonical_raw_half_for_live_half(live_half: int) -> int:
    """Return the preferred raw half for a live-name slot."""

    if not 1 <= live_half <= 0xFFFE:
        raise ValueError(f"live half out of range: {live_half}")
    return live_half - 1


def normalize_folder(folder: Path) -> Path:
    """Resolve user-relative folder without requiring it to already exist."""

    folder = folder.expanduser()
    if not folder.is_absolute():
        folder = folder.absolute()
    return folder


def report_path_for(root: Path, explicit: Path | None) -> Path:
    """Return JSON report path."""

    if explicit is not None:
        return normalize_folder(explicit)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    return root / "_rename_reports" / f"first_half_live_namefix_{stamp}.json"


def scan_sources(root: Path, layout: str) -> list[tuple[Path, str | None, Path, str]]:
    """Return scan source, forced extension, main output dir, and logical kind."""

    sources: list[tuple[Path, str | None, Path, str]] = []
    saves_dir = root / "saves"
    states_dir = root / "states"
    if layout in {LAYOUT_FLAT, LAYOUT_AUTO}:
        sources.append((root, None, root, "flat"))
    if layout == LAYOUT_SPLIT or (
        layout == LAYOUT_AUTO and (saves_dir.is_dir() or states_dir.is_dir())
    ):
        sources.append((saves_dir, "sav", saves_dir, "save"))
        sources.append((states_dir, "ss0", states_dir, "state"))
    return sources


def action_kind(extension: str) -> str:
    """Map file extension to report kind."""

    if extension == "sav":
        return "save"
    if extension == "ss0":
        return "state"
    raise ValueError(f"unsupported extension: {extension}")


def collision_target(
    root: Path,
    main_dir: Path,
    extension: str,
    raw_half: int,
    live_half: int,
    collision_policy: str,
) -> Path:
    """Return target path for one collision-preserved artifact."""

    filename = f"{format_half(live_half)}__raw{format_half(raw_half)}.{extension}"
    if collision_policy == COLLISION_SUFFIX:
        return main_dir / filename
    if collision_policy == COLLISION_QUARANTINE:
        subdir = "saves" if extension == "sav" else "states"
        return root / "_live_name_collisions" / subdir / filename
    raise RenamePlanError(
        f"raw {format_half(raw_half)} and another target both map to live {format_half(live_half)}"
    )


def target_for(
    root: Path,
    main_dir: Path,
    extension: str,
    raw_half: int,
    collision_policy: str,
) -> tuple[Path, int, bool]:
    """Return final path, live half, and collision flag for one raw artifact."""

    live_half = raw_half_to_live_half(raw_half)
    collision = raw_half != canonical_raw_half_for_live_half(live_half)
    if collision:
        return (
            collision_target(root, main_dir, extension, raw_half, live_half, collision_policy),
            live_half,
            True,
        )
    return main_dir / f"{format_half(live_half)}.{extension}", live_half, False


def build_rename_plan(
    folder: Path = DEFAULT_RAW_CSV_DIR,
    *,
    layout: str = LAYOUT_AUTO,
    collision_policy: str = COLLISION_QUARANTINE,
    expected_targets: int = EXPECTED_TARGETS,
    require_complete: bool = False,
    report: Path | None = None,
    temp_token: str | None = None,
) -> tuple[list[RenameAction], RenameSummary]:
    """Build a validated two-phase rename plan."""

    root = normalize_folder(folder)
    if not root.is_dir():
        raise RenamePlanError(f"folder not found: {root}")

    token = temp_token or uuid.uuid4().hex
    artifacts: list[ScannedArtifact] = []
    seen_sources: set[str] = set()
    save_count = 0
    state_count = 0

    for source_dir, forced_extension, main_dir, _logical_kind in scan_sources(root, layout):
        if not source_dir.exists():
            continue
        if not source_dir.is_dir():
            raise RenamePlanError(f"scan source is not a folder: {source_dir}")
        for entry in source_dir.iterdir():
            if not entry.is_file():
                continue
            match = NAME_RE.match(entry.name)
            if match is None:
                continue
            raw_half = int(match.group(1), 16)
            extension = match.group(2).lower()
            if forced_extension is not None and extension != forced_extension:
                continue

            kind = action_kind(extension)
            if kind == "save":
                save_count += 1
            else:
                state_count += 1

            source = entry
            source_key = str(source).casefold()
            if source_key in seen_sources:
                raise RenamePlanError(f"source planned twice: {source}")
            seen_sources.add(source_key)
            artifacts.append(
                ScannedArtifact(
                    source=source,
                    main_dir=main_dir,
                    extension=extension,
                    raw_half=raw_half,
                    kind=kind,
                )
            )

    if not artifacts:
        raise RenamePlanError(f"no raw-CSV .sav/.ss0 files found under: {root}")

    if require_complete and (save_count != expected_targets or state_count != expected_targets):
        raise RenamePlanError(
            "refusing incomplete corpus: "
            f"saves={save_count}/{expected_targets} states={state_count}/{expected_targets}"
        )

    actions: list[RenameAction] = []
    seen_targets: set[str] = set()
    source_paths = {str(artifact.source).casefold() for artifact in artifacts}
    for artifact in artifacts:
        target, live_half, collision = target_for(
            root,
            artifact.main_dir,
            artifact.extension,
            artifact.raw_half,
            collision_policy,
        )
        target_key = str(target).casefold()
        if target_key in seen_targets:
            raise RenamePlanError(f"two sources target same path: {target}")
        seen_targets.add(target_key)

        temp = artifact.source.with_name(
            f".__first_half_namefix_tmp__{token}__{artifact.source.name}"
        )
        if temp.exists():
            raise RenamePlanError(f"temp path already exists: {temp}")
        if target_key not in source_paths and target.exists():
            raise RenamePlanError(f"target path already exists outside plan: {target}")
        actions.append(
            RenameAction(
                kind=artifact.kind,
                raw_half=artifact.raw_half,
                live_half=live_half,
                source=str(artifact.source),
                temp=str(temp),
                target=str(target),
                collision=collision,
            )
        )

    report_path = report_path_for(root, report)
    summary = RenameSummary(
        root=str(root),
        layout=layout,
        collision_policy=collision_policy,
        actions=len(actions),
        save_actions=save_count,
        state_actions=state_count,
        main_actions=sum(1 for action in actions if not action.collision),
        collision_actions=sum(1 for action in actions if action.collision),
        source_files=len(source_paths),
        expected_targets=expected_targets,
        report_path=str(report_path),
    )
    return actions, summary


def write_report(
    report_path: Path,
    *,
    status: str,
    summary: RenameSummary,
    actions: list[RenameAction],
) -> None:
    """Write durable JSON report for audit and rollback planning."""

    report_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "status": status,
        "summary": asdict(summary),
        "actions": [asdict(action) for action in actions],
    }
    report_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_report(report_path: Path) -> tuple[list[RenameAction], RenameSummary]:
    """Load a previously written rename report."""

    data = json.loads(report_path.read_text(encoding="utf-8"))
    summary = RenameSummary(**data["summary"])
    actions = [RenameAction(**item) for item in data["actions"]]
    return actions, summary


def execute_rename_plan(actions: list[RenameAction]) -> None:
    """Run source -> temp -> final rename phases."""

    for action in actions:
        Path(action.target).parent.mkdir(parents=True, exist_ok=True)

    for action in actions:
        Path(action.source).rename(action.temp)

    for action in actions:
        Path(action.temp).rename(action.target)


def resume_rename_plan(actions: list[RenameAction]) -> tuple[int, int, int]:
    """Finish an interrupted source -> temp -> final rename plan.

    Returns:
        Tuple of ``(already_done, source_to_target, temp_to_target)`` counts.
    """

    already_done = 0
    source_to_target = 0
    temp_to_target = 0
    planned_target_paths = {str(Path(action.target)).casefold() for action in actions}

    for action in actions:
        source = Path(action.source)
        temp = Path(action.temp)
        target = Path(action.target)
        source_is_another_target = str(source).casefold() in planned_target_paths
        target.parent.mkdir(parents=True, exist_ok=True)

        source_exists = source.exists()
        temp_exists = temp.exists()
        target_exists = target.exists()
        if target_exists:
            if temp_exists or (source_exists and not source_is_another_target):
                raise RenamePlanError(
                    f"conflicting duplicate state for target {target}: "
                    f"source_exists={source_exists} temp_exists={temp_exists}"
                )
            already_done += 1
            continue
        if temp_exists:
            temp.rename(target)
            temp_to_target += 1
            continue
        if source_exists:
            if source_is_another_target:
                raise RenamePlanError(
                    f"source path is already another planned target, but temp is missing: {source}"
                )
            source.rename(temp)
            temp.rename(target)
            source_to_target += 1
            continue
        raise RenamePlanError(f"missing source/temp/target for action target: {target}")

    return already_done, source_to_target, temp_to_target


def print_text(summary: RenameSummary, *, executed: bool) -> None:
    """Print compact summary."""

    mode = "EXECUTED" if executed else "DRY_RUN"
    print(f"Mode: {mode}")
    print(f"Folder: {summary.root}")
    print(f"Layout: {summary.layout}")
    print(f"Collision policy: {summary.collision_policy}")
    print(f"Planned files: {summary.actions}")
    print(f"  .sav: {summary.save_actions}")
    print(f"  .ss0: {summary.state_actions}")
    print(f"Main live-name files: {summary.main_actions}")
    print(f"Collision-preserved files: {summary.collision_actions}")
    print(f"Report: {summary.report_path}")


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entry point."""

    args = parse_args(argv)
    try:
        if args.resume_report is not None:
            report_path = normalize_folder(args.resume_report)
            actions, summary = load_report(report_path)
            if args.execute:
                done, source_to_target, temp_to_target = resume_rename_plan(actions)
                write_report(
                    report_path,
                    status=(
                        "executed "
                        f"resume_done={done} "
                        f"resume_source_to_target={source_to_target} "
                        f"resume_temp_to_target={temp_to_target}"
                    ),
                    summary=summary,
                    actions=actions,
                )
        else:
            actions, summary = build_rename_plan(
                args.folder,
                layout=args.layout,
                collision_policy=args.collision_policy,
                expected_targets=args.expected_targets,
                require_complete=args.require_complete,
                report=args.report,
                temp_token=args.temp_token,
            )
            report_path = Path(summary.report_path)
            write_report(report_path, status="planned", summary=summary, actions=actions)
            if args.execute:
                execute_rename_plan(actions)
                write_report(report_path, status="executed", summary=summary, actions=actions)
    except RenamePlanError as exc:
        print(f"ERROR: {exc}")
        return 2
    except OSError as exc:
        print(f"ERROR: filesystem rename failed: {exc}")
        return 1

    if args.json:
        print(json.dumps({"executed": args.execute, "summary": asdict(summary)}, indent=2))
    else:
        print_text(summary, executed=args.execute)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
