"""Verify the known FR/LG initial-seed recipe across sample saves.

This is a read-only cross-save verifier for the maintained first-half title
recipe. It does not brute-force. For each sampled `.sav` file it:

1. loads the save as a temporary save
2. loads the calibrated read-only pre-input checkpoint, or optionally replays
   the exact recorded opening and pre-input route tapes
3. applies the one-frame final button, with delay work already represented by
   the checkpoint anchor or replayed by the route anchor
4. observes the Timer 1 seed and `gRngValue`
5. hashes the source save again to prove it was not changed on disk

Default sample pile:
    <repo-root>\\1sthalves\\saves

Default metadata:
    <repo-root>\\live-lanes\\live-fbc7-lane16\\1 from egg - replay-metadata.json
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_SAMPLE_DIR = Path(__file__).resolve().parents[3] / "1sthalves" / "saves"
DEFAULT_METADATA_PATH = Path(__file__).resolve().parents[3] / "live-lanes" / "live-fbc7-lane16" / "1 from egg - replay-metadata.json"
DEFAULT_SAMPLE_COUNT = 15
DEFAULT_SAMPLE_OFFSET = 0
DEFAULT_SAMPLE_STRIDE = 1
FIRSTHALF_HELPER_PATH = SCRIPT_DIR / "Seed-Bruteforcer.py"
DEFAULT_RESET_TAPE_NAME = "1 from egg - reset-to-title-baseline.inputtape.json"
DEFAULT_PRE_INPUT_TAPE_NAME = "1 from egg - title-baseline-to-checkpoint.inputtape.json"
DEFAULT_ANCHOR_MODE = "checkpoint"
ENV_PREFIX = "MGBA_SEED_SAMPLE_"


@dataclass(frozen=True)
class SaveFingerprint:
    """Small immutable fingerprint used to prove a sampled save did not change."""

    size: int
    mtime_ns: int
    sha1: str


@dataclass(frozen=True)
class KnownSeedRecipe:
    """Metadata fields needed to replay the known first-half hit."""

    metadata_path: Path
    rom_path: Path
    target_seed: int
    delay_frames: int
    button_name: str
    seed_timeout: int
    title_skip_start_delay: int
    pre_input_neutral_frames: int
    reset_tape_path: Path
    pre_input_tape_path: Path
    checkpoint_path: Path | None
    expected_timer1_count_pre: int | None
    expected_timer1_control_pre: int | None
    expected_rng_at_seed: int | None
    expected_prng_discerned_seed: int | None
    expected_prng_discerned_steps_from_rng: int | None


@dataclass(frozen=True)
class SampleReplayResult:
    """One sampled save result."""

    save_path: str
    save_sha1: str
    source_unchanged: bool
    matched: bool
    target_seed: str
    observed_seed: str | None
    observed_rng_at_seed: str | None
    seed_frame: int | None
    button_name: str
    delay_frames: int
    title_skip_start_delay: int
    anchor_mode: str
    checkpoint_path: str | None
    checkpoint_sha1: str | None
    checkpoint_unchanged: bool | None
    prng_discerned_seed: str | None
    prng_discerned_steps_from_rng: int | None
    error: str | None = None


def _load_module(path: Path, module_name: str) -> ModuleType:
    """Import a Python file whose filename is not a valid module identifier."""

    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not create import spec for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def load_firsthalf_helper(path: Path = FIRSTHALF_HELPER_PATH) -> ModuleType:
    """Load the maintained brute-force helper without running its CLI."""

    return _load_module(path, "firsthalf_seed_sample_helper")


def _parse_int(value: Any, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse one JSON/CLI integer, accepting decimal and `0x` strings."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean.")
    parsed = int(value, 0) if isinstance(value, str) else int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}, got {parsed}.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} must be <= {maximum}, got {parsed}.")
    return parsed


def _optional_int(metadata: Mapping[str, Any], field: str) -> int | None:
    """Return optional integer metadata, preserving absent/None as None."""

    if field not in metadata or metadata[field] is None:
        return None
    return _parse_int(metadata[field], field)


def _resolve_recipe_sidecar_path(metadata_path: Path, override: Path | None, default_name: str) -> Path:
    """Resolve one route-tape sidecar path next to replay metadata."""

    path = override if override is not None else metadata_path.parent / default_name
    return path.expanduser().resolve()


def _resolve_checkpoint_path(
    metadata_path: Path,
    metadata: Mapping[str, Any],
    override: Path | None,
) -> Path | None:
    """Resolve optional calibrated replay checkpoint path."""

    if override is not None:
        return override.expanduser().resolve()
    checkpoint_name = metadata.get("readonly_checkpoint_name")
    if checkpoint_name is None:
        return None
    checkpoint_path = Path(str(checkpoint_name))
    if (
        not str(checkpoint_name)
        or checkpoint_path.is_absolute()
        or checkpoint_path.name != str(checkpoint_name)
    ):
        raise RuntimeError(
            "Replay metadata field 'readonly_checkpoint_name' must be a local filename,"
            f" got {checkpoint_name!r}."
        )
    return (metadata_path.parent / checkpoint_path).resolve()


def load_known_seed_recipe(
    metadata_path: Path,
    *,
    reset_tape_path: Path | None = None,
    pre_input_tape_path: Path | None = None,
    checkpoint_path: Path | None = None,
) -> KnownSeedRecipe:
    """Load the fixed recipe exported by the successful brute-force run."""

    metadata_path = metadata_path.expanduser().resolve()
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    required = {
        "rom_path",
        "target_seed",
        "delay_frames",
        "button_name",
        "seed_timeout",
        "title_skip_start_delay",
        "pre_input_neutral_frames",
    }
    missing = sorted(required.difference(metadata))
    if missing:
        raise RuntimeError(f"Replay metadata missing required field(s): {', '.join(missing)}")

    button_name = str(metadata["button_name"])
    if button_name not in {"A", "Start"}:
        raise RuntimeError(f"Unsupported button_name in replay metadata: {button_name}")

    resolved_reset_tape_path = _resolve_recipe_sidecar_path(
        metadata_path,
        reset_tape_path,
        DEFAULT_RESET_TAPE_NAME,
    )
    resolved_pre_input_tape_path = _resolve_recipe_sidecar_path(
        metadata_path,
        pre_input_tape_path,
        DEFAULT_PRE_INPUT_TAPE_NAME,
    )
    resolved_checkpoint_path = _resolve_checkpoint_path(metadata_path, metadata, checkpoint_path)
    missing_tapes = [
        str(path)
        for path in (resolved_reset_tape_path, resolved_pre_input_tape_path)
        if not path.is_file()
    ]
    if missing_tapes:
        raise RuntimeError(f"Replay route tape(s) missing: {', '.join(missing_tapes)}")
    if checkpoint_path is not None and (resolved_checkpoint_path is None or not resolved_checkpoint_path.is_file()):
        raise RuntimeError(f"Replay checkpoint missing: {checkpoint_path}")

    return KnownSeedRecipe(
        metadata_path=metadata_path,
        rom_path=Path(str(metadata["rom_path"])).expanduser().resolve(),
        target_seed=_parse_int(metadata["target_seed"], "target_seed", minimum=0, maximum=0xFFFF),
        delay_frames=_parse_int(metadata["delay_frames"], "delay_frames", minimum=0),
        button_name=button_name,
        seed_timeout=_parse_int(metadata["seed_timeout"], "seed_timeout", minimum=1),
        title_skip_start_delay=_parse_int(
            metadata["title_skip_start_delay"],
            "title_skip_start_delay",
            minimum=0,
        ),
        pre_input_neutral_frames=_parse_int(
            metadata["pre_input_neutral_frames"],
            "pre_input_neutral_frames",
            minimum=0,
        ),
        reset_tape_path=resolved_reset_tape_path,
        pre_input_tape_path=resolved_pre_input_tape_path,
        checkpoint_path=resolved_checkpoint_path,
        expected_timer1_count_pre=_optional_int(metadata, "timer1_count_pre"),
        expected_timer1_control_pre=_optional_int(metadata, "timer1_control_pre"),
        expected_rng_at_seed=_optional_int(metadata, "rng_at_seed"),
        expected_prng_discerned_seed=_optional_int(metadata, "prng_discerned_seed"),
        expected_prng_discerned_steps_from_rng=_optional_int(
            metadata,
            "prng_discerned_steps_from_rng",
        ),
    )


def file_fingerprint(path: Path) -> SaveFingerprint:
    """Return size, mtime, and SHA-1 for a small source save file."""

    stat_result = path.stat()
    digest = hashlib.sha1()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return SaveFingerprint(
        size=stat_result.st_size,
        mtime_ns=stat_result.st_mtime_ns,
        sha1=digest.hexdigest(),
    )


def select_sample_saves(
    sample_dir: Path,
    *,
    count: int = DEFAULT_SAMPLE_COUNT,
    offset: int = DEFAULT_SAMPLE_OFFSET,
    stride: int = DEFAULT_SAMPLE_STRIDE,
) -> list[Path]:
    """Pick deterministic samples from an existing save pile."""

    if count < 1:
        raise ValueError("count must be >= 1.")
    if offset < 0:
        raise ValueError("offset must be >= 0.")
    if stride < 1:
        raise ValueError("stride must be >= 1.")

    sample_dir = sample_dir.expanduser().resolve()
    saves = sorted(path for path in sample_dir.glob("*.sav") if path.is_file())
    selected = saves[offset::stride][:count]
    if len(selected) < count:
        raise RuntimeError(
            f"Only found {len(selected)} sample save(s) in {sample_dir}; need {count}."
        )
    return selected


def _button_key(helper: ModuleType, button_name: str) -> int:
    """Map metadata button name to helper GBA key mask."""

    if button_name == "A":
        return int(helper.GBA.KEY_A)
    if button_name == "Start":
        return int(helper.GBA.KEY_START)
    raise RuntimeError(f"Unsupported button_name: {button_name}")


def _observe_seed_after_title_press(
    core: Any,
    helper: ModuleType,
    *,
    button_key: int,
    seed_timeout: int,
) -> tuple[int, int, int]:
    """Pulse the final title key once and wait for Timer 1 seed capture."""

    pre_input_seed_mirror = helper.observe_initial_seed_mirror(core)
    core.set_keys(button_key)
    core.run_frame()
    seed_observation = helper.observe_seed_generation(
        core,
        pre_input_seed_mirror=pre_input_seed_mirror,
    )
    core.set_keys(raw=0)
    if seed_observation is not None:
        return (
            int(seed_observation.seed_value) & 0xFFFF,
            int(getattr(core, "frame_counter", 0)),
            int(seed_observation.rng_value) & 0xFFFFFFFF,
        )

    for _ in range(max(seed_timeout - 1, 0)):
        core.run_frame()
        seed_observation = helper.observe_seed_generation(
            core,
            pre_input_seed_mirror=pre_input_seed_mirror,
        )
        if seed_observation is not None:
            core.set_keys(raw=0)
            return (
                int(seed_observation.seed_value) & 0xFFFF,
                int(getattr(core, "frame_counter", 0)),
                int(seed_observation.rng_value) & 0xFFFFFFFF,
            )

    core.set_keys(raw=0)
    raise RuntimeError(
        f"Initial seed was not observed within {seed_timeout} frames after final title input."
    )


def _replay_known_title_route_from_tapes(
    core: Any,
    helper: ModuleType,
    recipe: KnownSeedRecipe,
) -> None:
    """Replay the same two route tapes used by the successful brute-force hit."""

    input_tape = helper._load_input_tape_module()

    reset_tape = input_tape.read_tape(recipe.reset_tape_path)
    core.reset()
    core.set_keys(raw=0)
    input_tape.replay_tape(core, reset_tape)
    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 already stopped after replaying opening route tape.")
    if helper.find_title_task(core) is None:
        raise RuntimeError("Opening route tape did not leave the title task alive.")

    route_tape = input_tape.read_tape(recipe.pre_input_tape_path)
    widened_tape = helper._prepend_neutral_frames_to_tape(
        route_tape,
        neutral_frames=recipe.title_skip_start_delay,
    )
    input_tape.replay_tape(core, widened_tape)
    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 stopped before the pre-second-title-input checkpoint.")
    if not helper.title_input_checkpoint_ready(core):
        raise RuntimeError("Title task was not in RUN/state=1 after replaying route tapes.")


def _read_timer1_state(core: Any, helper: ModuleType) -> tuple[int, int]:
    """Read Timer 1 count/control with helper fallback."""

    read_timer1_state = getattr(helper, "read_timer1_state", None)
    if callable(read_timer1_state):
        count, control = read_timer1_state(core)
        return int(count) & 0xFFFF, int(control) & 0xFFFF
    return (
        int(core.memory.u16[helper.TIMER1_COUNT_ADDR]) & 0xFFFF,
        int(core.memory.u16[helper.TIMER1_CONTROL_ADDR]) & 0xFFFF,
    )


def _load_known_checkpoint_anchor(core: Any, helper: ModuleType, recipe: KnownSeedRecipe) -> None:
    """Load the calibrated pre-input checkpoint without writing any state file."""

    if recipe.checkpoint_path is None:
        raise RuntimeError("Checkpoint anchor requested, but replay metadata has no checkpoint path.")
    if not recipe.checkpoint_path.is_file():
        raise RuntimeError(f"Checkpoint anchor is missing: {recipe.checkpoint_path}")

    # Host-side mGBA cores are more stable if the just-loaded temporary save has
    # been reset into a live core state before a file-backed savestate restore.
    reset = getattr(core, "reset", None)
    if callable(reset):
        reset()
        core.set_keys(raw=0)

    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    try:
        helper.load_state_file(core, recipe.checkpoint_path, qt_mode=qt_mode)
    except TypeError:
        helper.load_state_file(core, recipe.checkpoint_path)
    core.set_keys(raw=0)

    count, control = _read_timer1_state(core, helper)
    if (
        recipe.expected_timer1_count_pre is not None
        and recipe.expected_timer1_control_pre is not None
        and (
            count != (recipe.expected_timer1_count_pre & 0xFFFF)
            or control != (recipe.expected_timer1_control_pre & 0xFFFF)
        )
    ):
        raise RuntimeError(
            "Checkpoint Timer 1 state does not match replay metadata:"
            f" expected count=0x{recipe.expected_timer1_count_pre:04X}"
            f" control=0x{recipe.expected_timer1_control_pre:04X};"
            f" observed count=0x{count:04X} control=0x{control:04X}."
        )
    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 already stopped after loading checkpoint anchor.")
    if not helper.title_input_checkpoint_ready(core):
        raise RuntimeError("Title task was not in RUN/state=1 after loading checkpoint anchor.")


def replay_known_steps(
    core: Any,
    helper: ModuleType,
    recipe: KnownSeedRecipe,
    *,
    anchor_mode: str = DEFAULT_ANCHOR_MODE,
) -> tuple[int, int, int]:
    """Follow the known first-half steps from a freshly loaded temporary save."""

    if anchor_mode == "checkpoint":
        _load_known_checkpoint_anchor(core, helper, recipe)
    elif anchor_mode == "route":
        _replay_known_title_route_from_tapes(core, helper, recipe)
        helper.run_frames_fast(core, recipe.delay_frames)
        if recipe.pre_input_neutral_frames:
            helper.run_frames_with_keys(core, 0, recipe.pre_input_neutral_frames)
    else:
        raise RuntimeError(f"Unsupported anchor mode: {anchor_mode}")

    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError("Timer 1 stopped before calibrated final title input.")
    if not helper.title_input_checkpoint_ready(core):
        raise RuntimeError("Title task was not in RUN/state=1 before calibrated final title input.")

    return _observe_seed_after_title_press(
        core,
        helper,
        button_key=_button_key(helper, recipe.button_name),
        seed_timeout=recipe.seed_timeout,
    )


def verify_sample_save(
    save_path: Path,
    helper: ModuleType,
    recipe: KnownSeedRecipe,
    *,
    anchor_mode: str = DEFAULT_ANCHOR_MODE,
) -> SampleReplayResult:
    """Verify one sampled save and prove its source bytes stayed unchanged."""

    save_path = save_path.expanduser().resolve()
    before = file_fingerprint(save_path)
    checkpoint_before = (
        file_fingerprint(recipe.checkpoint_path)
        if anchor_mode == "checkpoint" and recipe.checkpoint_path is not None and recipe.checkpoint_path.is_file()
        else None
    )
    observed_seed: int | None = None
    observed_rng: int | None = None
    seed_frame: int | None = None
    error: str | None = None
    prng_discerned_seed: int | None = None
    prng_discerned_steps: int | None = None

    try:
        core = helper.load_runtime_core(recipe.rom_path)
        qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
        helper.load_required_save_file(core, save_path, qt_mode=qt_mode, temporary=True)
        observed_seed, seed_frame, observed_rng = replay_known_steps(
            core,
            helper,
            recipe,
            anchor_mode=anchor_mode,
        )
        discern = getattr(helper, "discern_initial_seed_from_rng_state", None)
        if callable(discern):
            prng_discerned = discern(observed_rng)
            if prng_discerned is not None:
                prng_discerned_seed, prng_discerned_steps = prng_discerned
    except Exception as exc:  # Keep going across all 15 sample saves.
        error = f"{type(exc).__name__}: {exc}"

    after = file_fingerprint(save_path)
    checkpoint_after = (
        file_fingerprint(recipe.checkpoint_path)
        if checkpoint_before is not None and recipe.checkpoint_path is not None and recipe.checkpoint_path.is_file()
        else None
    )
    unchanged = before == after
    checkpoint_unchanged = (
        None if checkpoint_before is None else checkpoint_before == checkpoint_after
    )
    matched = (
        observed_seed == recipe.target_seed
        and unchanged
        and checkpoint_unchanged is not False
        and error is None
    )
    if checkpoint_unchanged is False:
        error = f"{error or 'replay checkpoint changed on disk'}"
    return SampleReplayResult(
        save_path=str(save_path),
        save_sha1=before.sha1,
        source_unchanged=unchanged,
        matched=matched,
        target_seed=f"0x{recipe.target_seed:04X}",
        observed_seed=None if observed_seed is None else f"0x{observed_seed:04X}",
        observed_rng_at_seed=None if observed_rng is None else f"0x{observed_rng:08X}",
        seed_frame=seed_frame,
        button_name=recipe.button_name,
        delay_frames=recipe.delay_frames,
        title_skip_start_delay=recipe.title_skip_start_delay,
        anchor_mode=anchor_mode,
        checkpoint_path=None if recipe.checkpoint_path is None else str(recipe.checkpoint_path),
        checkpoint_sha1=None if checkpoint_before is None else checkpoint_before.sha1,
        checkpoint_unchanged=checkpoint_unchanged,
        prng_discerned_seed=None
        if prng_discerned_seed is None
        else f"0x{int(prng_discerned_seed) & 0xFFFF:04X}",
        prng_discerned_steps_from_rng=prng_discerned_steps,
        error=error if unchanged else f"{error or 'source save changed on disk'}",
    )


def verify_samples(
    sample_paths: Iterable[Path],
    *,
    helper: ModuleType,
    recipe: KnownSeedRecipe,
    anchor_mode: str = DEFAULT_ANCHOR_MODE,
) -> list[SampleReplayResult]:
    """Run the known-seed verification over selected samples."""

    results: list[SampleReplayResult] = []
    for index, save_path in enumerate(sample_paths, start=1):
        print(f"[{index:02d}] verifying {save_path}")
        result = verify_sample_save(save_path, helper, recipe, anchor_mode=anchor_mode)
        status = "PASS" if result.matched else "FAIL"
        print(
            f"[{index:02d}] {status}"
            f" seed={result.observed_seed}"
            f" target={result.target_seed}"
            f" rng={result.observed_rng_at_seed}"
            f" unchanged={result.source_unchanged}"
            f" checkpoint_unchanged={result.checkpoint_unchanged}"
            f" error={result.error}"
        )
        results.append(result)
    return results


def _failed_child_result(
    save_path: Path,
    recipe: KnownSeedRecipe,
    *,
    anchor_mode: str,
    returncode: int,
    stdout: str,
    stderr: str,
) -> SampleReplayResult:
    """Build one failure result after an isolated child process crash/failure."""

    before = file_fingerprint(save_path)
    after = file_fingerprint(save_path)
    unchanged = before == after
    detail = stderr.strip() or stdout.strip()
    if len(detail) > 1200:
        detail = detail[-1200:]
    error = f"child process failed with returncode={returncode}"
    if detail:
        error = f"{error}: {detail}"
    if not unchanged:
        error = f"{error}; source save changed on disk"

    return SampleReplayResult(
        save_path=str(save_path),
        save_sha1=before.sha1,
        source_unchanged=unchanged,
        matched=False,
        target_seed=f"0x{recipe.target_seed:04X}",
        observed_seed=None,
        observed_rng_at_seed=None,
        seed_frame=None,
        button_name=recipe.button_name,
        delay_frames=recipe.delay_frames,
        title_skip_start_delay=recipe.title_skip_start_delay,
        anchor_mode=anchor_mode,
        checkpoint_path=None if recipe.checkpoint_path is None else str(recipe.checkpoint_path),
        checkpoint_sha1=None,
        checkpoint_unchanged=None,
        prng_discerned_seed=None,
        prng_discerned_steps_from_rng=None,
        error=error,
    )


def _result_from_report(path: Path) -> SampleReplayResult | None:
    """Load one child result from a report file, if the child wrote it."""

    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload.get("results")
    if not isinstance(results, list) or len(results) != 1:
        return None
    return SampleReplayResult(**results[0])


def verify_sample_in_subprocess(
    save_path: Path,
    *,
    recipe: KnownSeedRecipe,
    metadata_path: Path,
    anchor_mode: str,
) -> SampleReplayResult:
    """Run one sample in a child process to isolate native mGBA crashes."""

    with tempfile.TemporaryDirectory(prefix="mgba-seed-sample-") as temp_dir:
        child_report = Path(temp_dir) / "sample-result.json"
        command = [
            sys.executable,
            str(Path(__file__).resolve()),
            "--single-save",
            str(save_path),
            "--metadata",
            str(metadata_path),
            "--anchor",
            anchor_mode,
            "--reset-tape",
            str(recipe.reset_tape_path),
            "--pre-input-tape",
            str(recipe.pre_input_tape_path),
            "--report",
            str(child_report),
            "--no-isolate",
        ]
        if recipe.checkpoint_path is not None:
            command.extend(["--checkpoint", str(recipe.checkpoint_path)])
        completed = subprocess.run(
            command,
            cwd=str(SCRIPT_DIR),
            text=True,
            capture_output=True,
            check=False,
        )
        if completed.stdout:
            print(completed.stdout, end="")
        if completed.stderr:
            print(completed.stderr, end="", file=sys.stderr)

        result = _result_from_report(child_report)
        if result is None:
            return _failed_child_result(
                save_path,
                recipe,
                anchor_mode=anchor_mode,
                returncode=completed.returncode,
                stdout=completed.stdout,
                stderr=completed.stderr,
            )
        if completed.returncode != 0 and result.matched:
            return SampleReplayResult(
                **{
                    **asdict(result),
                    "matched": False,
                    "error": f"child process returned {completed.returncode} after writing a matched report",
                }
            )
        return result


def verify_samples_isolated(
    sample_paths: Iterable[Path],
    *,
    recipe: KnownSeedRecipe,
    metadata_path: Path,
    anchor_mode: str = DEFAULT_ANCHOR_MODE,
) -> list[SampleReplayResult]:
    """Run each sample in a separate child process."""

    results: list[SampleReplayResult] = []
    for index, save_path in enumerate(sample_paths, start=1):
        print(f"[{index:02d}] isolated verify {save_path}", flush=True)
        result = verify_sample_in_subprocess(
            save_path,
            recipe=recipe,
            metadata_path=metadata_path,
            anchor_mode=anchor_mode,
        )
        status = "PASS" if result.matched else "FAIL"
        print(
            f"[{index:02d}] isolated {status}"
            f" seed={result.observed_seed}"
            f" target={result.target_seed}"
            f" rng={result.observed_rng_at_seed}"
            f" unchanged={result.source_unchanged}"
            f" checkpoint_unchanged={result.checkpoint_unchanged}"
            f" error={result.error}",
            flush=True,
        )
        results.append(result)
    return results


def write_report(path: Path, recipe: KnownSeedRecipe, results: list[SampleReplayResult]) -> None:
    """Write optional JSON report outside the sampled save pile."""

    payload = {
        "script": Path(__file__).name,
        "metadata_path": str(recipe.metadata_path),
        "rom_path": str(recipe.rom_path),
        "target_seed": f"0x{recipe.target_seed:04X}",
        "delay_frames": recipe.delay_frames,
        "button_name": recipe.button_name,
        "title_skip_start_delay": recipe.title_skip_start_delay,
        "reset_tape_path": str(recipe.reset_tape_path),
        "pre_input_tape_path": str(recipe.pre_input_tape_path),
        "checkpoint_path": None if recipe.checkpoint_path is None else str(recipe.checkpoint_path),
        "sample_count": len(results),
        "matched_count": sum(1 for result in results if result.matched),
        "source_unchanged_count": sum(1 for result in results if result.source_unchanged),
        "results": [asdict(result) for result in results],
    }
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    """Build CLI for host-side verification."""

    def env_path(name: str) -> Path | None:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        return None if not value else Path(value)

    def env_int(name: str, default: int) -> int:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        return default if not value else int(value, 0)

    env_anchor = os.environ.get(f"{ENV_PREFIX}ANCHOR", DEFAULT_ANCHOR_MODE)
    env_no_isolate = os.environ.get(f"{ENV_PREFIX}NO_ISOLATE", "").strip().lower()

    parser = argparse.ArgumentParser(
        description="Verify a known FR/LG initial-seed recipe across sample saves without brute-forcing.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--sample-dir", type=Path, default=env_path("SAMPLE_DIR") or DEFAULT_SAMPLE_DIR)
    parser.add_argument("--metadata", type=Path, default=env_path("METADATA") or DEFAULT_METADATA_PATH)
    parser.add_argument(
        "--reset-tape",
        type=Path,
        default=env_path("RESET_TAPE"),
        help="Opening route tape. Defaults to the known sidecar next to --metadata.",
    )
    parser.add_argument(
        "--pre-input-tape",
        type=Path,
        default=env_path("PRE_INPUT_TAPE"),
        help="Title-baseline-to-pre-input route tape. Defaults to the known sidecar next to --metadata.",
    )
    parser.add_argument(
        "--checkpoint",
        type=Path,
        default=env_path("CHECKPOINT"),
        help="Calibrated pre-input checkpoint. Defaults to readonly_checkpoint_name from --metadata.",
    )
    parser.add_argument(
        "--anchor",
        choices=("checkpoint", "route"),
        default=env_anchor,
        help=(
            "checkpoint loads the proven read-only pre-input savestate; route replays"
            " opening/pre-input tapes from reset as an audit path."
        ),
    )
    parser.add_argument("--count", type=int, default=env_int("COUNT", DEFAULT_SAMPLE_COUNT))
    parser.add_argument("--offset", type=int, default=env_int("OFFSET", DEFAULT_SAMPLE_OFFSET))
    parser.add_argument("--stride", type=int, default=env_int("STRIDE", DEFAULT_SAMPLE_STRIDE))
    parser.add_argument("--report", type=Path, default=env_path("REPORT"), help="Optional JSON report path.")
    parser.add_argument(
        "--single-save",
        type=Path,
        default=env_path("SINGLE_SAVE"),
        help="Verify exactly one save path instead of sampling from --sample-dir.",
    )
    parser.add_argument(
        "--no-isolate",
        action="store_true",
        default=env_no_isolate in {"1", "true", "yes", "on"},
        help="Run all samples in this process instead of one child process per sample.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Run the read-only sample verifier."""

    try:
        sys.stdout.reconfigure(line_buffering=True)
    except Exception:
        pass

    args = build_parser().parse_args(argv)
    recipe = load_known_seed_recipe(
        args.metadata,
        reset_tape_path=args.reset_tape,
        pre_input_tape_path=args.pre_input_tape,
        checkpoint_path=args.checkpoint,
    )
    sample_paths = (
        [args.single_save.expanduser().resolve()]
        if args.single_save is not None
        else select_sample_saves(
            args.sample_dir,
            count=args.count,
            offset=args.offset,
            stride=args.stride,
        )
    )
    helper: ModuleType | None = None
    qt_mode = False
    if len(sample_paths) > 1 or args.anchor == "checkpoint":
        helper = load_firsthalf_helper()
        qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    isolate = not args.no_isolate and len(sample_paths) > 1 and not qt_mode

    print(f"Metadata: {recipe.metadata_path}")
    print(f"ROM: {recipe.rom_path}")
    print(f"Target seed: 0x{recipe.target_seed:04X}")
    print(f"Known recipe: lane={recipe.title_skip_start_delay} delay={recipe.delay_frames} button={recipe.button_name}")
    print(f"Anchor mode: {args.anchor}")
    if recipe.checkpoint_path is not None:
        print(f"Checkpoint anchor: {recipe.checkpoint_path}")
    print(f"Opening tape: {recipe.reset_tape_path}")
    print(f"Pre-input tape: {recipe.pre_input_tape_path}")
    print(f"Sample saves: {len(sample_paths)} from {args.sample_dir}")
    print(
        "Mode: read-only source saves; no savestate output"
        + ("; isolated child process per sample" if isolate else "")
    )

    if isolate:
        results = verify_samples_isolated(
            sample_paths,
            recipe=recipe,
            metadata_path=args.metadata,
            anchor_mode=args.anchor,
        )
    else:
        if helper is None:
            helper = load_firsthalf_helper()
        results = verify_samples(
            sample_paths,
            helper=helper,
            recipe=recipe,
            anchor_mode=args.anchor,
        )
    if args.report:
        write_report(args.report, recipe, results)
        print(f"Report: {args.report.expanduser().resolve()}")

    matched_count = sum(1 for result in results if result.matched)
    unchanged_count = sum(1 for result in results if result.source_unchanged)
    checkpoint_unchanged_count = sum(
        1 for result in results if result.checkpoint_unchanged is not False
    )
    print(
        "Summary:"
        f" matched={matched_count}/{len(results)}"
        f" source_unchanged={unchanged_count}/{len(results)}"
        f" checkpoint_unchanged={checkpoint_unchanged_count}/{len(results)}"
    )
    return 0 if (
        matched_count == len(results)
        and unchanged_count == len(results)
        and checkpoint_unchanged_count == len(results)
    ) else 1


if __name__ == "__main__":
    raise SystemExit(main())
