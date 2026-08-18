r"""Export one FR/LG lower-half lane save for the Spinda roadmap.

This is the first concrete roadmap script. It is built around the phase-1 loop:

1. load a canonical ROM/save/state recipe
2. replay the pre-egg route until the lower PID half is generated
3. verify that lower half directly from daycare RAM
4. walk from the daycare building to the daycare man
5. save in-game
6. export the live `.sav` as `0x####.sav`
7. write a lane manifest that later second-half scripts can resume from

The walk-to-man segment is intentionally modeled with PRNG checkpoints instead
of relying on frame counts alone. That area can have slight NPC RNG noise, so
the PRNG state is the correctness signal we trust.

For planning, the current scaffold treats roughly 375 frames from seed
generation to the lower PID half as a conservative Four Island estimate. The
route should still verify against PRNG state when it matters.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe frlg_spinda_first_half_lane.py recipe.json

Qt runtime usage:
    - set `MGBA_SPINDA_FIRST_HALF_RECIPE`, or
    - place `first_half_recipe.json` beside this script, or
    - load the file in the scripting window and call
      `run_recipe_file(r"path\\to\\recipe.json")` from the Python prompt
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from spinda_frlg_common import (
    FirstHalfRecipe,
    LaneWorkspaceManifest,
    ensure_workspace_dirs,
    export_save_file,
    format_u16,
    format_u32,
    lane_paths,
    load_gba_core,
    load_state_file,
    qt_mode_enabled,
    read_daycare_lower_half,
    read_rng_state,
    run_route,
    save_state_file,
    sha1_file,
    write_lane_manifest,
)

RUNTIME_RECIPE_ENV = "MGBA_SPINDA_FIRST_HALF_RECIPE"
RUNTIME_OVERWRITE_ENV = "MGBA_SPINDA_FIRST_HALF_OVERWRITE"
DEFAULT_RUNTIME_RECIPE = Path(__file__).with_name("first_half_recipe.json")


def build_parser() -> argparse.ArgumentParser:
    """Create the CLI for the first-half export script."""

    parser = argparse.ArgumentParser(
        description="Export one FR/LG lower-half lane save from a roadmap recipe.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "recipe",
        help="Path to a first-half recipe JSON file.",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Allow an existing `0x####.sav` or manifest to be replaced.",
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
    )


def export_first_half_lane(recipe: FirstHalfRecipe, *, overwrite: bool = False) -> LaneWorkspaceManifest:
    """Run the phase-1 lower-half export flow for one recipe."""

    paths = lane_paths(recipe.workspace_root, recipe.target_lower_half)
    ensure_workspace_dirs(paths.workspace_root)

    for protected_path in (paths.archive_save_path, paths.manifest_path, paths.work_state_path):
        if protected_path.exists() and not overwrite:
            raise SystemExit(
                "Refusing to overwrite an existing lane artifact without --overwrite: "
                f"{protected_path}"
            )

    core = load_gba_core(recipe.rom_path, recipe.base_save_path)
    load_state_file(core, recipe.base_state_path)

    pre_generation_results = run_route(core, recipe.pre_generation_route)
    observed_lower_half = read_daycare_lower_half(core)
    if observed_lower_half != recipe.target_lower_half:
        raise RuntimeError(
            "The recipe did not produce the requested lower half. "
            f"target={format_u16(recipe.target_lower_half)} "
            f"observed={format_u16(observed_lower_half)}"
        )

    # Record the PRNG around the noisy outside segment and the in-game save so
    # later replay work can compare against state, not only frame counts.
    observed_rng_before_walk = read_rng_state(core)
    post_generation_results = run_route(core, recipe.post_generation_route)
    observed_rng_after_walk = read_rng_state(core)
    save_sequence_results = run_route(core, recipe.save_sequence)
    observed_rng_after_save = read_rng_state(core)

    export_save_file(core, paths.archive_save_path)

    work_state_sha1: str | None = None
    if recipe.create_lane_work_state:
        # The optional work state is the hand-off point for phase 2. It should
        # be captured after the lane save exists, so upper-half sweeps can start
        # from the exact archive state that belongs to this lower half.
        save_state_file(core, paths.work_state_path)
        work_state_sha1 = sha1_file(paths.work_state_path)

    manifest = LaneWorkspaceManifest(
        lane_id=recipe.target_lower_half,
        manifest_path=paths.manifest_path,
        archive_save_path=paths.archive_save_path,
        work_state_path=paths.work_state_path,
        block_path=paths.block_path,
        rom_path=recipe.rom_path,
        recipe_path=recipe.source_path,
        base_save_path=recipe.base_save_path,
        base_state_path=recipe.base_state_path,
        archive_save_sha1=sha1_file(paths.archive_save_path),
        work_state_sha1=work_state_sha1,
        observed_lower_half=observed_lower_half,
        observed_rng_before_walk=observed_rng_before_walk,
        observed_rng_after_walk=observed_rng_after_walk,
        observed_rng_after_save=observed_rng_after_save,
        next_upper_half=0,
        completed_upper_halves=0,
        complete=False,
        pre_generation_results=pre_generation_results,
        post_generation_results=post_generation_results,
        save_sequence_results=save_sequence_results,
        notes=recipe.notes,
    )
    write_lane_manifest(manifest)
    return manifest


def _print_summary(manifest: LaneWorkspaceManifest) -> None:
    """Print a compact operator-facing summary after one lane export."""

    print(f"Exported lane: {format_u16(manifest.lane_id)}")
    print(f"Archive save: {manifest.archive_save_path}")
    print(f"Manifest: {manifest.manifest_path}")
    print(f"Work state: {manifest.work_state_path}")
    print(f"Observed lower half: {format_u16(manifest.observed_lower_half)}")
    print(f"RNG before walk: {format_u32(manifest.observed_rng_before_walk)}")
    print(f"RNG after walk: {format_u32(manifest.observed_rng_after_walk)}")
    print(f"RNG after save: {format_u32(manifest.observed_rng_after_save)}")


def run_recipe_file(recipe_path: str | Path, *, overwrite: bool = False) -> LaneWorkspaceManifest:
    """Run one recipe file directly.

    This is the callable runtime entrypoint for the Qt scripting window. The
    script can be loaded mid-session, then the operator can point it at a real
    recipe without depending on `sys.argv`.
    """

    recipe = FirstHalfRecipe.load(Path(recipe_path))
    manifest = export_first_half_lane(recipe, overwrite=overwrite)
    _print_summary(manifest)
    return manifest


def main() -> None:
    """Load the recipe, export the lane, and print a short summary."""

    args = _parse_args(build_parser())
    if args is None:
        print(
            "Qt runtime mode: no first-half recipe configured. "
            f"Set {RUNTIME_RECIPE_ENV} or place first_half_recipe.json beside this script."
        )
        return
    run_recipe_file(args.recipe, overwrite=args.overwrite)


if __name__ == "__main__":
    main()
