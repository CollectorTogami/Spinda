"""Exhaustively test FR/LG final-frame button combinations from one checkpoint.

This helper is for the local mGBA Python workspace. It reuses the same
pre-second-title-press checkpoint logic as `frlg_seed_bruteforce.py`, then
replays every physically valid held-button combination from that exact
checkpoint state.

The main use case is comparing which held combinations collapse to the same
observed initial seed and which ones produce a seed that no other combination
hits from the same frame.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, dataclass
import csv
import json
from pathlib import Path
from typing import Iterable

import frlg_seed_bruteforce as seed_script
from mgba.gba import GBA


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_ROM = SCRIPT_DIR / "lg.gba"
CHECKPOINT_PATH = SCRIPT_DIR / "button-combo-checkpoint.sav"
CSV_PATH = SCRIPT_DIR / "button-combo-results.csv"
JSON_PATH = SCRIPT_DIR / "button-combo-results.json"
SUMMARY_PATH = SCRIPT_DIR / "button-combo-summary.md"

BUTTON_ORDER = ("A", "B", "Select", "Start", "Up", "Down", "Left", "Right", "L", "R")
D_PAD_STATES = (
    (),
    ("Up",),
    ("Down",),
    ("Left",),
    ("Right",),
    ("Up", "Left"),
    ("Up", "Right"),
    ("Down", "Left"),
    ("Down", "Right"),
)
EXTRA_BUTTONS = ("A", "B", "Select", "Start", "L", "R")
BUTTON_BITS = {
    "A": 1 << GBA.KEY_A,
    "B": 1 << GBA.KEY_B,
    "Select": 1 << GBA.KEY_SELECT,
    "Start": 1 << GBA.KEY_START,
    "Down": 1 << GBA.KEY_DOWN,
    "Up": 1 << GBA.KEY_UP,
    "Left": 1 << GBA.KEY_LEFT,
    "Right": 1 << GBA.KEY_RIGHT,
    "L": 1 << GBA.KEY_L,
    "R": 1 << GBA.KEY_R,
}


@dataclass(frozen=True)
class ComboCase:
    """One physically valid held-button combination for the final seed frame."""

    name: str
    buttons: tuple[str, ...]
    raw_mask: int
    triggers_seed: bool


@dataclass(frozen=True)
class ComboResult:
    """Observed output for one final-frame combination."""

    combo: str
    buttons: tuple[str, ...]
    raw_mask_hex: str
    triggers_seed: bool
    outcome: str
    seed_hex: str
    seed_frame: int | None
    rng_after_settle_hex: str
    unique_seed: bool


def combo_name(buttons: Iterable[str]) -> str:
    """Return a stable human-readable combination name."""

    ordered = [button for button in BUTTON_ORDER if button in set(buttons)]
    if not ordered:
        return "(none)"
    return "+".join(ordered)


def is_reserved_combo(buttons: set[str]) -> bool:
    """Return True when the held mask hits a non-seeding title-screen shortcut."""

    if {"A", "B", "Select", "Start"} <= buttons:
        return True
    if {"B", "Select", "Up"} <= buttons:
        return True
    if {"B", "Select"} <= buttons:
        return True
    return False


def build_cases() -> list[ComboCase]:
    """Enumerate the physically valid held-button combinations to test."""

    cases: list[ComboCase] = []
    for dpad_buttons in D_PAD_STATES:
        for extra_mask in range(1 << len(EXTRA_BUTTONS)):
            buttons = set(dpad_buttons)
            for index, button in enumerate(EXTRA_BUTTONS):
                if extra_mask & (1 << index):
                    buttons.add(button)
            if is_reserved_combo(buttons):
                continue

            ordered = tuple(button for button in BUTTON_ORDER if button in buttons)
            raw_mask = 0
            for button in ordered:
                raw_mask |= BUTTON_BITS[button]
            cases.append(
                ComboCase(
                    name=combo_name(ordered),
                    buttons=ordered,
                    raw_mask=raw_mask,
                    triggers_seed=("A" in buttons or "Start" in buttons),
                )
            )
    return cases


def run_case(core: GBA, checkpoint_path: Path, case: ComboCase) -> ComboResult:
    """Replay one held-button combination from the shared checkpoint."""

    seed_script.load_state_file(core, checkpoint_path)
    core.set_keys(raw=case.raw_mask)

    seed_value = 0
    seed_frame: int | None = None
    rng_after_settle = 0
    for _ in range(seed_script.DEFAULT_SEED_TIMEOUT):
        core.run_frame()
        seed_value = core.memory.u16[seed_script.GTRAINER_ID_ADDR]
        if seed_value:
            seed_frame = core.frame_counter
            for _ in range(seed_script.DEFAULT_SETTLE_FRAMES):
                core.run_frame()
            rng_after_settle = core.memory.u32[seed_script.GRNG_VALUE_ADDR]
            break

    core.set_keys(raw=0)

    if seed_frame is None:
        return ComboResult(
            combo=case.name,
            buttons=case.buttons,
            raw_mask_hex=f"0x{case.raw_mask:04X}",
            triggers_seed=case.triggers_seed,
            outcome="no-seed",
            seed_hex="",
            seed_frame=None,
            rng_after_settle_hex="",
            unique_seed=False,
        )

    return ComboResult(
        combo=case.name,
        buttons=case.buttons,
        raw_mask_hex=f"0x{case.raw_mask:04X}",
        triggers_seed=case.triggers_seed,
        outcome="seed",
        seed_hex=f"0x{seed_value:04X}",
        seed_frame=seed_frame,
        rng_after_settle_hex=f"0x{rng_after_settle:08X}",
        unique_seed=False,
    )


def main() -> int:
    """Create one checkpoint, test every valid combo, and write grouped reports."""

    rom_path = DEFAULT_ROM.resolve()
    if not rom_path.is_file():
        raise SystemExit(f"ROM not found: {rom_path}")

    seed_script.mgba.log.silence()
    core = seed_script.mgba.core.load_path(str(rom_path))
    if not core:
        raise SystemExit(f"Could not load ROM: {rom_path}")
    if core.platform != GBA.PLATFORM_GBA:
        raise SystemExit("This script requires a GBA ROM.")

    cases = build_cases()
    seed_script.boot_to_pre_second_press_checkpoint(core)
    seed_script.save_state_file(core, CHECKPOINT_PATH)

    results = [run_case(core, CHECKPOINT_PATH, case) for case in cases]

    combos_by_seed: dict[str, list[str]] = defaultdict(list)
    for result in results:
        combos_by_seed[result.seed_hex].append(result.combo)

    enriched_results: list[ComboResult] = []
    unique_seed_results: list[ComboResult] = []
    for result in results:
        if result.outcome != "seed":
            enriched_results.append(result)
            continue
        unique_seed = len(combos_by_seed[result.seed_hex]) == 1
        updated = ComboResult(
            combo=result.combo,
            buttons=result.buttons,
            raw_mask_hex=result.raw_mask_hex,
            triggers_seed=result.triggers_seed,
            outcome=result.outcome,
            seed_hex=result.seed_hex,
            seed_frame=result.seed_frame,
            rng_after_settle_hex=result.rng_after_settle_hex,
            unique_seed=unique_seed,
        )
        enriched_results.append(updated)
        if unique_seed:
            unique_seed_results.append(updated)

    unique_seed_results.sort(key=lambda item: item.seed_hex)

    with CSV_PATH.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "combo",
                "buttons",
                "raw_mask_hex",
                "triggers_seed",
                "outcome",
                "seed_hex",
                "seed_frame",
                "rng_after_settle_hex",
                "unique_seed",
            ],
        )
        writer.writeheader()
        for result in enriched_results:
            row = asdict(result)
            row["buttons"] = ",".join(result.buttons)
            writer.writerow(row)

    groups = {
        seed_hex or "NO_SEED": sorted(combo_names)
        for seed_hex, combo_names in sorted(combos_by_seed.items(), key=lambda item: item[0] or "NO_SEED")
    }
    with JSON_PATH.open("w", encoding="utf-8") as handle:
        json.dump(
            {
                "rom": str(rom_path),
                "checkpoint": str(CHECKPOINT_PATH),
                "result_count": len(enriched_results),
                "unique_seed_count": len(unique_seed_results),
                "results": [asdict(result) for result in enriched_results],
                "groups_by_seed": groups,
            },
            handle,
            indent=2,
        )

    seeded_results = [result for result in enriched_results if result.outcome == "seed"]
    no_seed_results = [result for result in enriched_results if result.outcome == "no-seed"]
    distinct_seed_count = len({result.seed_hex for result in seeded_results})

    lines = [
        "# FR/LG Final-Frame Button Combo Report",
        "",
        f"- ROM: `{rom_path}`",
        f"- Shared checkpoint state: `{CHECKPOINT_PATH}`",
        f"- Valid physically possible combinations tested: `{len(enriched_results)}`",
        f"- Combinations that produced a seed within {seed_script.DEFAULT_SEED_TIMEOUT} frames: `{len(seeded_results)}`",
        f"- Combinations that produced no seed within {seed_script.DEFAULT_SEED_TIMEOUT} frames: `{len(no_seed_results)}`",
        f"- Distinct initial seeds observed: `{distinct_seed_count}`",
        f"- Seeds hit by exactly one combination: `{len(unique_seed_results)}`",
        "",
        "## Unique Seeds",
        "",
    ]

    if unique_seed_results:
        for result in unique_seed_results:
            lines.append(f"- `{result.combo}` -> `{result.seed_hex}`")
    else:
        lines.append("- None")

    lines.extend(
        [
            "",
            "## Files",
            "",
            f"- Full CSV: `{CSV_PATH}`",
            f"- Full JSON: `{JSON_PATH}`",
        ]
    )

    SUMMARY_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")

    print(f"Tested combinations: {len(enriched_results)}")
    print(f"Seed-producing combinations: {len(seeded_results)}")
    print(f"No-seed combinations: {len(no_seed_results)}")
    print(f"Distinct seeds: {distinct_seed_count}")
    print(f"Unique seeds: {len(unique_seed_results)}")
    print(f"Summary written to: {SUMMARY_PATH}")
    print(f"CSV written to: {CSV_PATH}")
    print(f"JSON written to: {JSON_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
