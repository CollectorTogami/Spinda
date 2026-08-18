"""Sweep upper PID halves for one FR/LG lower-half lane.

This is a scaffold for the roadmap's second-half phase. It is written so the
remaining future work is mostly about supplying real route tables and validating
them in the emulator, not about inventing file formats or control flow from
scratch.

The intended runtime flow is:

1. load one `0x####.sav` lane
2. create or reuse the canonical pre-pickup work savestate
3. for each target upper half:
   - reload the work savestate
   - replay the route for that target
   - validate the resulting full PID
   - extract party slot 2 as an 80-byte boxed record
   - place it into the correct block offset
4. flush the block and manifest periodically

This script is not a finished route generator. It is the execution scaffold
that future GPU-generated route schedules can plug into.

For planning, the current scaffold treats roughly 700 frames from seed
generation to receiving the egg as a conservative Four Island estimate. The
actual sweep routes should still validate against PRNG state and PID results,
not only a raw frame count.

Qt runtime usage:
    - set `MGBA_SPINDA_SECOND_HALF_RECIPE`, or
    - place `second_half_recipe.json` beside this script, or
    - load the file in the scripting window and call
      `run_recipe_file(r"path\\to\\recipe.json")` from the Python prompt
"""

from __future__ import annotations

import argparse
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from spinda_frlg_archive import LaneBlockBuffer
from spinda_frlg_common import (
    LaneWorkspaceManifest,
    RouteStep,
    compose_pid,
    format_u16,
    format_u32,
    lane_paths,
    load_gba_core,
    load_lane_manifest,
    load_state_file,
    personality_value_from_box_record,
    qt_mode_enabled,
    read_box_bytes_from_party_slot,
    read_json,
    read_rng_state,
    run_route,
    save_state_file,
    sha1_file,
    write_lane_manifest,
)

RUNTIME_RECIPE_ENV = "MGBA_SPINDA_SECOND_HALF_RECIPE"
RUNTIME_OVERWRITE_ENV = "MGBA_SPINDA_SECOND_HALF_OVERWRITE"
DEFAULT_RUNTIME_RECIPE = Path(__file__).with_name("second_half_recipe.json")


def _parse_int(value: Any, *, bits: int | None = None) -> int:
    """Parse one integer field from JSON or CLI data, with optional bit bounds."""

    if isinstance(value, int):
        parsed = value
    else:
        parsed = int(value, 0)
    if bits is not None and not 0 <= parsed < (1 << bits):
        raise ValueError(f"Value {value!r} does not fit in {bits} bits.")
    return parsed


def _resolve_path(raw_path: str | None, base_dir: Path) -> Path | None:
    """Resolve one optional recipe path relative to the recipe file itself."""

    if raw_path in (None, ""):
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


@dataclass(frozen=True)
class SecondHalfTarget:
    """One upper-half target plus the route that should hit it."""

    upper_half: int
    route: tuple[RouteStep, ...]
    expected_pid: int | None = None
    expected_rng_before_route: int | None = None
    notes: str = ""

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "SecondHalfTarget":
        return cls(
            upper_half=_parse_int(data["upper_half"], bits=16),
            route=tuple(RouteStep.from_dict(step) for step in data.get("route", ())),
            expected_pid=None
            if data.get("expected_pid") is None
            else _parse_int(data["expected_pid"], bits=32),
            expected_rng_before_route=None
            if data.get("expected_rng_before_route") is None
            else _parse_int(data["expected_rng_before_route"], bits=32),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class SecondHalfRecipe:
    """Recipe for sweeping one lower-half lane's upper-half targets."""

    source_path: Path
    rom_path: Path
    workspace_root: Path
    lane_id: int
    lane_save_path: Path | None = None
    create_work_state_route: tuple[RouteStep, ...] = ()
    targets: tuple[SecondHalfTarget, ...] = ()
    flush_every: int = 256
    notes: str = ""

    @classmethod
    def load(cls, path: Path) -> "SecondHalfRecipe":
        source_path = path.expanduser().resolve()
        data = read_json(source_path)
        base_dir = source_path.parent
        return cls(
            source_path=source_path,
            rom_path=_resolve_path(data["rom_path"], base_dir) or Path(),
            workspace_root=_resolve_path(data.get("workspace_root", "."), base_dir) or Path(),
            lane_id=_parse_int(data["lane_id"], bits=16),
            lane_save_path=_resolve_path(data.get("lane_save_path"), base_dir),
            create_work_state_route=tuple(
                RouteStep.from_dict(step) for step in data.get("create_work_state_route", ())
            ),
            targets=tuple(
                SecondHalfTarget.from_dict(target) for target in data.get("targets", ())
            ),
            flush_every=max(1, int(data.get("flush_every", 256))),
            notes=str(data.get("notes", "")),
        )


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI for the second-half lane runner."""

    parser = argparse.ArgumentParser(
        description="Sweep upper PID halves for one FR/LG lower-half lane.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("recipe", help="Path to a second-half recipe JSON file.")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow already-present upper-half records to be replaced.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Only process the first N targets from the recipe.",
    )
    return parser


def _runtime_recipe_path() -> Path | None:
    """Return the runtime recipe path, if one was configured for Qt use."""

    raw_path = os.environ.get(RUNTIME_RECIPE_ENV)
    if raw_path:
        return Path(raw_path).expanduser().resolve()
    if DEFAULT_RUNTIME_RECIPE.is_file():
        return DEFAULT_RUNTIME_RECIPE.resolve()
    return None


def _runtime_overwrite_enabled() -> bool:
    """Parse one optional overwrite toggle for the Qt runtime path."""

    value = os.environ.get(RUNTIME_OVERWRITE_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_args(parser: argparse.ArgumentParser):
    """Use normal CLI args off-Qt, but runtime defaults inside the Qt session."""

    if not qt_mode_enabled():
        return parser.parse_args()

    recipe_path = _runtime_recipe_path()
    if recipe_path is None:
        return None
    return argparse.Namespace(
        recipe=str(recipe_path),
        overwrite=_runtime_overwrite_enabled(),
        limit=None,
    )


def _resolve_lane_manifest(recipe: SecondHalfRecipe) -> tuple[LaneWorkspaceManifest, Path]:
    """Load and validate the canonical manifest for the requested lane."""

    paths = lane_paths(recipe.workspace_root, recipe.lane_id)
    manifest_path = paths.manifest_path
    if not manifest_path.is_file():
        raise SystemExit(
            f"Lane manifest does not exist yet for {format_u16(recipe.lane_id)}: {manifest_path}"
        )
    manifest = load_lane_manifest(manifest_path)
    if manifest.lane_id != recipe.lane_id:
        raise RuntimeError(
            "The lane manifest does not match the recipe lane id. "
            f"recipe={format_u16(recipe.lane_id)} manifest={format_u16(manifest.lane_id)}"
        )
    return manifest, paths.work_state_path


def _ensure_work_state(recipe: SecondHalfRecipe, manifest: LaneWorkspaceManifest, work_state_path: Path) -> None:
    """Create the lane work state if it does not exist yet."""

    if work_state_path.is_file():
        if manifest.work_state_sha1 is None:
            manifest.work_state_sha1 = sha1_file(work_state_path)
            write_lane_manifest(manifest)
        return

    lane_save_path = recipe.lane_save_path or manifest.archive_save_path
    core = load_gba_core(recipe.rom_path, lane_save_path)
    # This route is where the later real project will absorb any noisy segment
    # between loading the lane save and reaching the stable pre-pickup point.
    run_route(core, recipe.create_work_state_route)
    save_state_file(core, work_state_path)
    manifest.work_state_sha1 = sha1_file(work_state_path)
    write_lane_manifest(manifest)


def _target_pid(manifest: LaneWorkspaceManifest, target: SecondHalfTarget) -> int:
    """Return the PID the route should produce for this lane/upper-half pair."""

    if target.expected_pid is not None:
        return target.expected_pid
    return compose_pid(manifest.lane_id, target.upper_half)


def _next_pending_upper_half(recipe: SecondHalfRecipe, block: LaneBlockBuffer) -> int:
    """Return the next useful upper-half hint for resume metadata.

    Prefer the first target in the current recipe that is still missing. If the
    recipe has been exhausted but the lane is not globally complete yet, fall
    back to the first missing slot in the full block.
    """

    for target in recipe.targets:
        if not block.is_present(target.upper_half):
            return target.upper_half

    next_missing = block.next_missing_upper_half()
    return 0 if next_missing is None else next_missing


def sweep_second_half_lane(
    recipe: SecondHalfRecipe,
    *,
    overwrite: bool = False,
    limit: int | None = None,
) -> LaneWorkspaceManifest:
    """Run the second-half sweep scaffold for one lane recipe."""

    manifest, work_state_path = _resolve_lane_manifest(recipe)
    _ensure_work_state(recipe, manifest, work_state_path)

    block = (
        LaneBlockBuffer.load(manifest.block_path)
        if manifest.block_path.is_file()
        else LaneBlockBuffer()
    )

    lane_save_path = recipe.lane_save_path or manifest.archive_save_path
    core = load_gba_core(recipe.rom_path, lane_save_path)

    targets = recipe.targets if limit is None else recipe.targets[:limit]
    processed_since_flush = 0
    for target in targets:
        if block.is_present(target.upper_half) and not overwrite:
            continue

        # Every target starts from the same per-lane checkpoint so differences
        # come from the scripted route, not from state carried over by the last
        # upper-half attempt.
        load_state_file(core, work_state_path)
        observed_rng_before_route = read_rng_state(core)
        if (
            target.expected_rng_before_route is not None
            and observed_rng_before_route != target.expected_rng_before_route
        ):
            raise RuntimeError(
                "The work state did not restore to the expected PRNG checkpoint. "
                f"target={format_u16(target.upper_half)} "
                f"expected={format_u32(target.expected_rng_before_route)} "
                f"observed={format_u32(observed_rng_before_route)}"
            )

        run_route(core, target.route)
        box_record = read_box_bytes_from_party_slot(core, slot_number=2)
        observed_pid = personality_value_from_box_record(box_record)
        expected_pid = _target_pid(manifest, target)
        if observed_pid != expected_pid:
            raise RuntimeError(
                "The sweep route produced the wrong PID. "
                f"lane={format_u16(manifest.lane_id)} "
                f"upper={format_u16(target.upper_half)} "
                f"expected={format_u32(expected_pid)} "
                f"observed={format_u32(observed_pid)}"
            )

        block.set_record(target.upper_half, box_record)
        manifest.completed_upper_halves = block.count_present()
        processed_since_flush += 1

        if processed_since_flush >= recipe.flush_every:
            # Flush in batches so the real run can resume cleanly without
            # paying the cost of rewriting the lane block after every record.
            block.save(manifest.block_path)
            write_lane_manifest(manifest)
            processed_since_flush = 0

    if processed_since_flush or not manifest.block_path.is_file():
        block.save(manifest.block_path)

    manifest.completed_upper_halves = block.count_present()
    manifest.complete = manifest.completed_upper_halves == 0x10000
    manifest.next_upper_half = _next_pending_upper_half(recipe, block)
    write_lane_manifest(manifest)
    return manifest


def _print_summary(manifest: LaneWorkspaceManifest) -> None:
    """Print one compact lane-sweep summary."""

    print(f"Lane: {format_u16(manifest.lane_id)}")
    print(f"Block: {manifest.block_path}")
    print(f"Completed upper halves: {manifest.completed_upper_halves}")
    print(f"Next upper half: {format_u16(manifest.next_upper_half)}")
    print(f"Complete: {manifest.complete}")


def run_recipe_file(
    recipe_path: str | Path,
    *,
    overwrite: bool = False,
    limit: int | None = None,
) -> LaneWorkspaceManifest:
    """Run one second-half recipe file directly from CLI or Qt prompt."""

    recipe = SecondHalfRecipe.load(Path(recipe_path))
    manifest = sweep_second_half_lane(recipe, overwrite=overwrite, limit=limit)
    _print_summary(manifest)
    return manifest


def main() -> None:
    """Load the recipe, sweep targets, and print a short summary."""

    args = _parse_args(build_parser())
    if args is None:
        print(
            "Qt runtime mode: no second-half recipe configured. "
            f"Set {RUNTIME_RECIPE_ENV} or place second_half_recipe.json beside this script."
        )
        return
    run_recipe_file(args.recipe, overwrite=args.overwrite, limit=args.limit)


if __name__ == "__main__":
    main()
