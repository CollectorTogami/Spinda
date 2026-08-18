"""Lint FR/LG Spinda roadmap recipe files without launching the emulator.

This script exists to catch the kinds of mistakes that would waste hours in a
long corpus run:

- duplicate upper-half targets
- route steps that are missing
- obvious PID mismatches in the second-half sweep recipe
- missing ROM/save/state files in a recipe that is about to be used

The goal is not to prove a route is correct. The emulator is still required for
that. The goal is to reject the easy-to-avoid recipe mistakes before any live
run starts.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from frlg_spinda_second_half_lane import SecondHalfRecipe
from spinda_frlg_common import FirstHalfRecipe, RouteStep, compose_pid, format_u16, format_u32


@dataclass(frozen=True)
class LintFinding:
    """One lint message with a machine-usable severity."""

    level: str
    message: str


@dataclass
class LintReport:
    """Summary of one lint pass."""

    recipe_kind: str
    recipe_path: Path
    findings: list[LintFinding] = field(default_factory=list)

    def add(self, level: str, message: str) -> None:
        self.findings.append(LintFinding(level, message))

    @property
    def error_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "error")

    @property
    def warning_count(self) -> int:
        return sum(1 for finding in self.findings if finding.level == "warning")

    def ok(self) -> bool:
        return self.error_count == 0


def _warn_missing_path(report: LintReport, path: Path | None, label: str) -> None:
    """Add one warning when a recipe path has not been created yet."""

    if path is not None and not path.exists():
        report.add("warning", f"{label} does not exist yet: {path}")


def _lint_route_labels(
    report: LintReport,
    steps: Sequence[RouteStep],
    *,
    scope: str,
) -> None:
    """Warn when a route reuses labels that should usually stay unique."""

    seen: set[str] = set()
    for step in steps:
        if step.label in seen:
            report.add("warning", f"{scope} reuses the route label {step.label!r}.")
        seen.add(step.label)


def _lint_first_half_recipe(recipe: FirstHalfRecipe) -> LintReport:
    """Lint the structure of one phase-1 lower-half recipe."""

    report = LintReport("first-half", recipe.source_path)

    _warn_missing_path(report, recipe.rom_path, "ROM path")
    _warn_missing_path(report, recipe.base_save_path, "Base save path")
    _warn_missing_path(report, recipe.base_state_path, "Base state path")

    if not recipe.pre_generation_route:
        report.add("warning", "The pre-generation route is empty.")
    if not recipe.post_generation_route:
        report.add("warning", "The post-generation route is empty.")
    if not recipe.save_sequence:
        report.add("warning", "The save sequence is empty.")

    _lint_route_labels(report, recipe.pre_generation_route, scope="pre_generation_route")
    _lint_route_labels(report, recipe.post_generation_route, scope="post_generation_route")
    _lint_route_labels(report, recipe.save_sequence, scope="save_sequence")

    return report


def _lint_second_half_recipe(recipe: SecondHalfRecipe) -> LintReport:
    """Lint the structure and PID assumptions of one phase-2 recipe."""

    report = LintReport("second-half", recipe.source_path)

    _warn_missing_path(report, recipe.rom_path, "ROM path")
    _warn_missing_path(report, recipe.lane_save_path, "Lane save path")

    if not recipe.create_work_state_route:
        report.add("warning", "The create_work_state_route is empty.")
    _lint_route_labels(report, recipe.create_work_state_route, scope="create_work_state_route")

    if not recipe.targets:
        report.add("warning", "The recipe contains no upper-half targets.")

    seen_upper_halves: set[int] = set()
    for target in recipe.targets:
        if target.upper_half in seen_upper_halves:
            report.add(
                "error",
                f"Duplicate upper-half target found: {format_u16(target.upper_half)}",
            )
        seen_upper_halves.add(target.upper_half)

        if not target.route:
            report.add(
                "warning",
                f"Target {format_u16(target.upper_half)} has an empty route.",
            )
        _lint_route_labels(
            report,
            target.route,
            scope=f"target {format_u16(target.upper_half)} route",
        )

        if target.expected_rng_before_route is None:
            report.add(
                "warning",
                f"Target {format_u16(target.upper_half)} does not declare "
                "`expected_rng_before_route`.",
            )

        if target.expected_pid is not None:
            # The roadmap assumes one lower-half lane plus one upper-half index
            # maps to exactly one canonical PID. If a recipe claims a different
            # full PID here, that is almost certainly a schedule/config mistake.
            canonical_pid = compose_pid(recipe.lane_id, target.upper_half)
            if target.expected_pid != canonical_pid:
                report.add(
                    "error",
                    "Target "
                    f"{format_u16(target.upper_half)} declares expected_pid="
                    f"{format_u32(target.expected_pid)} but the canonical PID for "
                    f"lane {format_u16(recipe.lane_id)} would be {format_u32(canonical_pid)}.",
                )

    return report


def _detect_recipe_kind(path: Path) -> str:
    """Infer the recipe type from its top-level JSON keys."""

    data = json.loads(path.read_text(encoding="utf-8"))
    if "target_lower_half" in data:
        return "first-half"
    if "lane_id" in data:
        return "second-half"
    raise SystemExit(f"Could not infer recipe kind from {path}")


def lint_recipe(path: Path, recipe_kind: str) -> LintReport:
    """Load and lint one recipe file."""

    path = path.expanduser().resolve()
    if recipe_kind == "auto":
        recipe_kind = _detect_recipe_kind(path)

    if recipe_kind == "first-half":
        return _lint_first_half_recipe(FirstHalfRecipe.load(path))
    if recipe_kind == "second-half":
        return _lint_second_half_recipe(SecondHalfRecipe.load(path))
    raise ValueError(f"Unsupported recipe kind: {recipe_kind}")


def _print_report(report: LintReport) -> None:
    """Print one human-readable lint report."""

    print(f"Recipe: {report.recipe_path}")
    print(f"Kind: {report.recipe_kind}")
    print(f"Errors: {report.error_count}")
    print(f"Warnings: {report.warning_count}")
    for finding in report.findings:
        print(f"[{finding.level}] {finding.message}")


def build_parser() -> argparse.ArgumentParser:
    """Create the recipe-linter CLI."""

    parser = argparse.ArgumentParser(
        description="Lint FR/LG Spinda roadmap recipe files without launching mGBA.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "recipe",
        help="Path to a first-half or second-half recipe JSON file.",
    )
    parser.add_argument(
        "--kind",
        choices=("auto", "first-half", "second-half"),
        default="auto",
        help="Recipe type. `auto` inspects the JSON keys.",
    )
    return parser


def main() -> None:
    """Lint one recipe and exit non-zero on hard errors."""

    args = build_parser().parse_args()
    report = lint_recipe(Path(args.recipe), args.kind)
    _print_report(report)
    raise SystemExit(0 if report.ok() else 1)


if __name__ == "__main__":
    main()
