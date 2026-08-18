"""Build uniform Phase 2 FR/LG Spinda pickup savestates.

This standalone bridge takes completed first-half lane saves, reproduces the
known Phase 2 title seed from `secondhalf.csv`, runs the Day-Care Man pickup
tape to the final pre-input point, pads to one seed-relative baseline frame,
and writes `Phase2PickupStates\\0x####.ss0`.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable, Mapping


SCRIPT_DIR = Path(__file__).parent
EXAMPLES_DIR = SCRIPT_DIR.parent
MGBA_ROOT = SCRIPT_DIR.parents[2]
SEED_BRUTE_FORCE_DIR = EXAMPLES_DIR / "frlg-seed-bruteforce"
SAMPLE_REPLICATOR_PATH = SEED_BRUTE_FORCE_DIR / "Seed-Sample-Replicator.py"

DEFAULT_SAVE_DIR = MGBA_ROOT / "1sthalves" / "saves"
DEFAULT_OUTPUT_DIR = MGBA_ROOT / "Phase2PickupStates"
DEFAULT_SECOND_HALF_CSV = MGBA_ROOT / "build-mingw64-python-qt" / "secondhalf.csv"
DEFAULT_PICKUP_TAPE = MGBA_ROOT / "build-mingw64-python-qt" / "tape seed to step 2.json"
DEFAULT_ZERO_PICKUP_TAPE = MGBA_ROOT / "0x0000 special tape.json"
DEFAULT_METADATA_PATH = MGBA_ROOT / "live-lanes" / "live-cd39-lane21" / "1 from egg - replay-metadata.json"
DEFAULT_CONTROL_FILE_NAME = "_phase2_pickup_control.json"
DEFAULT_BASELINE_FRAME = 700
DEFAULT_EXPECTED_SAVE_COUNT = 65_536
DEFAULT_RETRY_COUNT = 3
DEFAULT_RNG_DRIFT_WINDOW = 4096
DEFAULT_PROGRESS_EVERY = 25
DEFAULT_STATUS_EVERY = 25
DEFAULT_STATE_SIZE = 397_312
SAVE_NAME_RE = re.compile(r"^0x([0-9A-Fa-f]{4})\.sav$")
ENV_PREFIX = "MGBA_PHASE2_PICKUP_"

if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

import input_tape  # noqa: E402
from spinda_frlg_common import (  # noqa: E402
    GRNG_VALUE_ADDR,
    format_u16,
    format_u32,
    lcrng_next_state,
    lcrng_previous_state,
    read_rng_state,
    write_json_atomic,
)


@dataclass(frozen=True)
class SaveFingerprint:
    """Cheap fingerprint used to detect accidental source-save writes."""

    size: int
    mtime_ns: int


@dataclass(frozen=True)
class SecondHalfCsvContract:
    """Small summary of the streamed `secondhalf.csv` route contract."""

    path: str
    initial_seed: int
    row_count: int
    t0_row_count: int
    unique_t0_targets: int


@dataclass(frozen=True)
class Phase2PickupConfig:
    """Runtime configuration for the pickup-state builder."""

    save_dir: Path
    output_dir: Path
    secondhalf_csv: Path
    pickup_tape_path: Path
    zero_pickup_tape_path: Path | None
    metadata_path: Path
    reset_tape_path: Path | None
    pre_input_tape_path: Path | None
    control_path: Path | None
    baseline_frame: int
    expected_save_count: int
    expected_state_size: int
    require_expected_save_count: bool
    expected_rng_at_baseline: int | None
    rng_drift_window: int
    retry_count: int
    limit: int | None
    start_hex: int | None
    end_hex: int | None
    overwrite: bool
    dry_run: bool
    progress_every: int
    status_every: int
    stop_on_error: bool
    speed_toggles: bool
    unbounded_fast_forward: bool


@dataclass(frozen=True)
class Phase2PickupResult:
    """One successfully written Phase 2 pickup savestate."""

    save_name: str
    output_name: str
    observed_seed: str
    seed_frame: int
    rng_at_seed: str
    pickup_tape_frames: int
    baseline_frame: int
    neutral_frames: int
    expected_rng_at_baseline: str
    final_rng: str
    rng_drift_from_expected: int
    source_unchanged: bool
    attempts: int


@dataclass(frozen=True)
class Phase2PickupFailure:
    """One failed lane result, compact enough for JSONL status."""

    save_name: str
    error: str
    attempts: int
    observed_final_rng: str | None = None
    rng_drift_from_expected: int | None = None


def _utc_now() -> str:
    """Return a compact UTC ISO timestamp."""

    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _parse_int(value: Any, field: str, *, minimum: int | None = None, maximum: int | None = None) -> int:
    """Parse decimal or `0x` integers from CLI, JSON, CSV, or environment."""

    if isinstance(value, bool):
        raise ValueError(f"{field} must be an integer, not a boolean.")
    parsed = int(value, 0) if isinstance(value, str) else int(value)
    if minimum is not None and parsed < minimum:
        raise ValueError(f"{field} must be >= {minimum}, got {parsed}.")
    if maximum is not None and parsed > maximum:
        raise ValueError(f"{field} must be <= {maximum}, got {parsed}.")
    return parsed


def _optional_int(value: Any, field: str) -> int | None:
    """Parse an optional integer where absent or blank means None."""

    if value is None:
        return None
    if isinstance(value, str) and not value.strip():
        return None
    return _parse_int(value, field)


def _display_path(path: Path) -> Path:
    """Return an absolute lexical path without following workspace junctions."""

    return path.expanduser().absolute()


def load_module(path: Path, module_name: str) -> ModuleType:
    """Import one helper file whose filename is not a module identifier."""

    spec = importlib.util.spec_from_file_location(module_name, path)  # type: ignore[name-defined]
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)  # type: ignore[name-defined]
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


# Keep importlib lazy in normal module import; tests can import this file
# without loading the mGBA helper until they exercise runtime paths.
import importlib.util  # noqa: E402  pylint: disable=wrong-import-position


def load_sample_replicator(path: Path = SAMPLE_REPLICATOR_PATH) -> ModuleType:
    """Load the maintained no-bruteforce title-seed replay helper."""

    return load_module(path, "phase2_pickup_sample_replicator")


def cheap_fingerprint(path: Path) -> SaveFingerprint:
    """Return a quick source-save fingerprint."""

    stat_result = path.stat()
    return SaveFingerprint(size=stat_result.st_size, mtime_ns=stat_result.st_mtime_ns)


def source_key_from_save_name(path: Path) -> int:
    """Parse `0x####.sav` into an integer lane key."""

    match = SAVE_NAME_RE.match(path.name)
    if not match:
        raise ValueError(f"Bad source save name: {path.name}")
    return int(match.group(1), 16)


def output_state_path(output_dir: Path, save_path: Path) -> Path:
    """Map `0x####.sav` to `Phase2PickupStates\\0x####.ss0`."""

    source_key_from_save_name(save_path)
    return output_dir / save_path.with_suffix(".ss0").name


def select_source_saves(
    save_dir: Path,
    *,
    start_hex: int | None = None,
    end_hex: int | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Select lane saves in numeric hex order."""

    save_dir = save_dir.expanduser()
    if not save_dir.is_dir():
        raise RuntimeError(f"Source save directory not found: {save_dir}")

    bad_names = sorted(path.name for path in save_dir.glob("*.sav") if not SAVE_NAME_RE.match(path.name))
    if bad_names:
        shown = ", ".join(bad_names[:8])
        more = "" if len(bad_names) <= 8 else f", ... +{len(bad_names) - 8}"
        raise RuntimeError(f"Bad source save name(s) in {save_dir}: {shown}{more}")

    saves = sorted(save_dir.glob("0x*.sav"), key=source_key_from_save_name)
    if start_hex is not None:
        saves = [path for path in saves if source_key_from_save_name(path) >= start_hex]
    if end_hex is not None:
        saves = [path for path in saves if source_key_from_save_name(path) <= end_hex]
    if limit is not None:
        saves = saves[:limit]
    return saves


def read_secondhalf_csv_contract(path: Path) -> SecondHalfCsvContract:
    """Stream `secondhalf.csv` and verify it names exactly one initial seed."""

    path = path.expanduser()
    if not path.is_file():
        raise RuntimeError(f"secondhalf.csv not found: {path}")

    initial_seed: int | None = None
    row_count = 0
    t0_row_count = 0
    t0_targets: set[int] = set()
    with path.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle)
        required = {"initial_seed_16bit", "target_half_16bit", "t_minus"}
        missing = required.difference(reader.fieldnames or ())
        if missing:
            raise RuntimeError(f"secondhalf.csv missing required column(s): {', '.join(sorted(missing))}")
        for row in reader:
            row_count += 1
            seed = _parse_int(row["initial_seed_16bit"], "initial_seed_16bit", minimum=0, maximum=0xFFFF)
            if initial_seed is None:
                initial_seed = seed
            elif seed != initial_seed:
                raise RuntimeError(
                    "secondhalf.csv contains more than one initial seed: "
                    f"{format_u16(initial_seed)} and {format_u16(seed)}"
                )
            if row.get("t_minus") == "t-0":
                t0_row_count += 1
                t0_targets.add(
                    _parse_int(row["target_half_16bit"], "target_half_16bit", minimum=0, maximum=0xFFFF)
                )

    if initial_seed is None or row_count == 0:
        raise RuntimeError(f"secondhalf.csv has no route rows: {path}")
    return SecondHalfCsvContract(
        path=str(_display_path(path)),
        initial_seed=initial_seed,
        row_count=row_count,
        t0_row_count=t0_row_count,
        unique_t0_targets=len(t0_targets),
    )


def validate_recipe_seed(contract: SecondHalfCsvContract, recipe: Any) -> None:
    """Require replay metadata to match the `secondhalf.csv` seed authority."""

    target_seed = _parse_int(getattr(recipe, "target_seed"), "metadata.target_seed", minimum=0, maximum=0xFFFF)
    if target_seed != contract.initial_seed:
        raise RuntimeError(
            "Replay metadata target seed does not match secondhalf.csv: "
            f"metadata={format_u16(target_seed)} csv={format_u16(contract.initial_seed)}"
        )


def load_recipe_for_contract(
    sample_replicator: ModuleType,
    contract: SecondHalfCsvContract,
    *,
    metadata_path: Path,
    reset_tape_path: Path | None = None,
    pre_input_tape_path: Path | None = None,
) -> Any:
    """Load replay metadata and check it against `secondhalf.csv`."""

    recipe = sample_replicator.load_known_seed_recipe(
        metadata_path,
        reset_tape_path=reset_tape_path,
        pre_input_tape_path=pre_input_tape_path,
    )
    validate_recipe_seed(contract, recipe)
    return recipe


def _enable_speed_toggles(
    helper: ModuleType,
    core: Any,
    *,
    unbounded_fast_forward: bool = True,
) -> None:
    """Enable visible-Qt speed helpers when the bridge is present."""

    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    if not qt_mode:
        return
    feature_names = [
        "ensure_live_audio_killswitch",
        "ensure_live_no_render_mode",
    ]
    if unbounded_fast_forward:
        feature_names.append("ensure_live_unbounded_fast_forward")
    for name in feature_names:
        func = getattr(helper, name, None)
        if callable(func):
            func(core, qt_mode=qt_mode)
    if not unbounded_fast_forward:
        _disable_live_fast_forward_for_monitoring(core)


def _core_bool_property(core: Any, name: str) -> bool | None:
    """Read one optional live Qt boolean property/method from a core wrapper."""

    try:
        value = getattr(core, name)
    except Exception:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    return bool(value)


def _parse_control_bool(value: Any, field: str) -> bool | None:
    """Parse a runtime-control boolean where `None` means leave unchanged."""

    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        if value in (0, 1):
            return bool(value)
        raise ValueError(f"{field} must be a boolean-like value.")
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"", "leave", "none", "unchanged"}:
            return None
        if normalized in {"1", "true", "yes", "on", "enable", "enabled"}:
            return True
        if normalized in {"0", "false", "no", "off", "disable", "disabled"}:
            return False
    raise ValueError(f"{field} must be a boolean-like value.")


def _control_value(payload: Mapping[str, Any], *names: str) -> bool | None:
    """Return the first control value present under one of several aliases."""

    for name in names:
        if name in payload:
            return _parse_control_bool(payload[name], name)
    return None


def _call_live_feature_setter(core: Any, setter_name: str, value: bool, label: str) -> bool:
    """Call one optional Qt feature setter and verify bridge-level success."""

    setter = getattr(core, setter_name, None)
    if not callable(setter):
        print(f"{label} bridge is unavailable; requested runtime control was not applied.")
        return False
    result = setter(bool(value))
    if result is False:
        raise RuntimeError(f"{label} bridge rejected requested value {value!r}.")
    return True


def _apply_live_feature_bool(
    core: Any,
    *,
    setter_name: str,
    state_name: str,
    label: str,
    value: bool | None,
) -> dict[str, Any] | None:
    """Apply one optional live feature toggle and return status details."""

    if value is None:
        return None
    available = _call_live_feature_setter(core, setter_name, value, label)
    observed = _core_bool_property(core, state_name)
    if available and observed is not None and observed != bool(value):
        raise RuntimeError(f"{label} did not reach requested value {value!r}.")
    return {
        "requested": bool(value),
        "observed": observed,
        "available": available,
    }


def apply_live_feature_control(core: Any, payload: Mapping[str, Any]) -> dict[str, Any]:
    """Apply one runtime-control payload from inside the script-owned loop."""

    applied: dict[str, Any] = {}

    pause_builder = _control_value(payload, "pause_builder", "pause", "paused")
    if pause_builder is not None:
        applied["pause_builder"] = {"requested": pause_builder}
    stop_builder = _control_value(payload, "stop_builder", "stop", "exit_builder", "exit")
    if stop_builder is not None:
        applied["stop_builder"] = {"requested": stop_builder}

    fast_forward = _control_value(payload, "fast_forward", "fastForward")
    fast_forward_unbounded = _control_value(
        payload,
        "fast_forward_unbounded",
        "unbounded_fast_forward",
        "fastForwardUnbounded",
    )
    if fast_forward is not None:
        ratio_setter = getattr(core, "set_fast_forward_ratio", None)
        toggle_setter = getattr(core, "set_fast_forward", None)
        if not callable(ratio_setter) or not callable(toggle_setter):
            print("Fast-forward bridge is unavailable; requested runtime control was not applied.")
            applied["fast_forward"] = {
                "requested": fast_forward,
                "observed": _core_bool_property(core, "fast_forward_enabled"),
                "available": False,
            }
        else:
            ratio = -1.0 if fast_forward and fast_forward_unbounded else 1.0
            if ratio_setter(ratio) is False:
                raise RuntimeError("Fast-forward ratio bridge rejected requested runtime control.")
            if toggle_setter(fast_forward) is False:
                raise RuntimeError("Fast-forward toggle bridge rejected requested runtime control.")
            observed = _core_bool_property(core, "fast_forward_enabled")
            if observed is not None and observed != fast_forward:
                raise RuntimeError(f"Fast-forward did not reach requested value {fast_forward!r}.")
            applied["fast_forward"] = {
                "requested": fast_forward,
                "observed": observed,
                "available": True,
                "ratio": "unbounded" if ratio < 0 else "bounded",
            }
    elif fast_forward_unbounded is not None:
        ratio_setter = getattr(core, "set_fast_forward_ratio", None)
        if callable(ratio_setter):
            ratio = -1.0 if fast_forward_unbounded else 1.0
            if ratio_setter(ratio) is False:
                raise RuntimeError("Fast-forward ratio bridge rejected requested runtime control.")
            applied["fast_forward_ratio"] = "unbounded" if ratio < 0 else "bounded"
        else:
            applied["fast_forward_ratio"] = "unavailable"

    audio = _apply_live_feature_bool(
        core,
        setter_name="set_audio_killswitch",
        state_name="audio_killswitch_enabled",
        label="Audio killswitch",
        value=_control_value(payload, "audio_killswitch", "audio", "audioKillswitch"),
    )
    if audio is not None:
        applied["audio_killswitch"] = audio

    no_render = _apply_live_feature_bool(
        core,
        setter_name="set_no_render_mode",
        state_name="no_render_mode_enabled",
        label="No-render mode",
        value=_control_value(payload, "no_render_mode", "no_render", "noRenderMode"),
    )
    if no_render is not None:
        applied["no_render_mode"] = no_render

    return applied


def runtime_control_requests_pause(applied: dict[str, Any] | None) -> bool:
    """Return whether the latest runtime-control payload paused the builder."""

    if not applied:
        return False
    pause = applied.get("pause_builder")
    if isinstance(pause, Mapping):
        return bool(pause.get("requested"))
    return bool(pause)


def runtime_control_requests_stop(applied: dict[str, Any] | None) -> bool:
    """Return whether the latest runtime-control payload stops the builder."""

    if not applied:
        return False
    stop = applied.get("stop_builder")
    if isinstance(stop, Mapping):
        return bool(stop.get("requested"))
    return bool(stop)


def maybe_apply_live_feature_control(
    core: Any,
    control_path: Path | None,
    previous_fingerprint: SaveFingerprint | None,
) -> tuple[SaveFingerprint | None, dict[str, Any] | None]:
    """Apply changed runtime-control JSON once at safe points between saves."""

    if control_path is None or not control_path.is_file():
        return None, None
    fingerprint = cheap_fingerprint(control_path)
    if fingerprint == previous_fingerprint:
        return previous_fingerprint, None
    try:
        payload = json.loads(control_path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise RuntimeError(f"Runtime control file must contain a JSON object: {control_path}")
        applied = apply_live_feature_control(core, payload)
    except Exception as exc:
        applied = {"error": f"{type(exc).__name__}: {exc}"}
    if applied:
        print(f"Applied runtime feature control from {control_path.name}: {json.dumps(applied, sort_keys=True)}")
    return fingerprint, applied or {}


def _disable_live_fast_forward_for_monitoring(core: Any) -> None:
    """Disable live Qt fast-forward so monitoring does not require UI toggles."""

    ratio_setter = getattr(core, "set_fast_forward_ratio", None)
    toggle_setter = getattr(core, "set_fast_forward", None)
    if not callable(ratio_setter) or not callable(toggle_setter):
        print("Fast-forward bridge is unavailable; current live speed setting is unchanged.")
        return

    if ratio_setter(1.0) is False:
        raise SystemExit("Could not set the live fast-forward speed to bounded monitoring mode.")
    if toggle_setter(False) is False:
        raise SystemExit("Could not disable the live fast-forward toggle through the Qt bridge.")
    if _core_bool_property(core, "fast_forward_enabled"):
        raise SystemExit("Could not verify that live fast-forward was disabled.")
    print("Disabled live fast-forward for monitoring.")


def replay_pickup_tape_to_baseline(
    core: Any,
    pickup_tape: input_tape.InputTape,
    *,
    seed_frame: int,
    baseline_frame: int,
) -> tuple[int, int]:
    """Run the seed-to-pre-pickup bridge tape, then pad to baseline."""

    input_tape.replay_tape(core, pickup_tape, use_batch=True)
    current_from_seed = int(getattr(core, "frame_counter", 0)) - int(seed_frame)
    if current_from_seed > baseline_frame:
        raise RuntimeError(
            "Pickup tape passed requested baseline frame: "
            f"current={current_from_seed} baseline={baseline_frame}"
        )
    neutral_frames = baseline_frame - current_from_seed
    if neutral_frames:
        input_tape.run_exact_frames(core, 0, neutral_frames, use_batch=True)
    input_tape.set_exact_keys(core, 0)

    final_from_seed = int(getattr(core, "frame_counter", 0)) - int(seed_frame)
    if final_from_seed != baseline_frame:
        raise RuntimeError(
            "Phase 2 baseline frame calibration failed: "
            f"final={final_from_seed} baseline={baseline_frame}"
        )
    return final_from_seed, neutral_frames


def signed_lcrng_distance(expected: int, observed: int, max_steps: int) -> int | None:
    """Return signed LCRNG drift of observed relative to expected."""

    expected &= 0xFFFFFFFF
    observed &= 0xFFFFFFFF
    if expected == observed:
        return 0

    state = expected
    for steps in range(1, max_steps + 1):
        state = lcrng_next_state(state)
        if state == observed:
            return steps

    state = expected
    for steps in range(1, max_steps + 1):
        state = lcrng_previous_state(state)
        if state == observed:
            return -steps
    return None


def validate_baseline_rng(expected: int, observed: int, drift_window: int) -> int:
    """Check final RNG against expected state and return signed drift."""

    drift = signed_lcrng_distance(expected, observed, drift_window)
    if drift is None:
        raise RuntimeError(
            "Final gRngValue did not match baseline within LCRNG/R window: "
            f"expected={format_u32(expected)} observed={format_u32(observed)} window={drift_window}"
        )
    return drift


def temporary_state_path(path: Path) -> Path:
    """Return the crash-safe temporary name used before final publish."""

    return path.with_name(f"{path.name}.tmp")


def existing_state_is_complete(path: Path, expected_state_size: int) -> bool:
    """Return True only when an existing final `.ss0` has the expected size."""

    try:
        return path.is_file() and path.stat().st_size == expected_state_size
    except OSError:
        return False


def _save_state_file(helper: ModuleType, core: Any, path: Path, *, expected_state_size: int) -> None:
    """Save one file-backed state to `.tmp`, validate size, then publish."""

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = temporary_state_path(path)
    try:
        tmp_path.unlink()
    except FileNotFoundError:
        pass
    helper.save_state_file(core, tmp_path)
    actual_size = tmp_path.stat().st_size
    if actual_size != expected_state_size:
        try:
            tmp_path.unlink()
        finally:
            raise RuntimeError(
                "Savestate temporary file had wrong size: "
                f"{tmp_path.name} size={actual_size} expected={expected_state_size}"
            )
    tmp_path.replace(path)


def pickup_tape_for_save(
    save_path: Path,
    *,
    default_tape: input_tape.InputTape,
    zero_tape: input_tape.InputTape | None,
) -> input_tape.InputTape:
    """Return the special 0x0000 bridge tape only for the ACE endpoint lane."""

    if source_key_from_save_name(save_path) != 0:
        return default_tape
    if zero_tape is None:
        raise RuntimeError("0x0000.sav requires the special ACE bridge tape, but it was not loaded.")
    return zero_tape


def build_one_pickup_state(
    save_path: Path,
    output_path: Path,
    *,
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    pickup_tape: input_tape.InputTape,
    baseline_frame: int,
    expected_state_size: int,
    expected_rng_at_baseline: int | None,
    rng_drift_window: int,
) -> Phase2PickupResult:
    """Load one save, replicate seed, build baseline state, and save `.ss0`."""

    before = cheap_fingerprint(save_path)
    qt_mode = bool(helper._qt_mode_enabled()) if hasattr(helper, "_qt_mode_enabled") else False
    helper.load_required_save_file(core, save_path, qt_mode=qt_mode, temporary=True)

    observed_seed, seed_frame, rng_at_seed = sample_replicator.replay_known_steps(
        core,
        helper,
        recipe,
        anchor_mode="route",
    )
    if observed_seed != recipe.target_seed:
        raise RuntimeError(
            "Seed mismatch after route replay: "
            f"observed={format_u16(observed_seed)} target={format_u16(recipe.target_seed)}"
        )

    _final_from_seed, neutral_frames = replay_pickup_tape_to_baseline(
        core,
        pickup_tape,
        seed_frame=seed_frame,
        baseline_frame=baseline_frame,
    )
    final_rng = int(read_rng_state(core)) & 0xFFFFFFFF
    expected_rng = final_rng if expected_rng_at_baseline is None else expected_rng_at_baseline
    rng_drift = validate_baseline_rng(expected_rng, final_rng, rng_drift_window)
    _save_state_file(helper, core, output_path, expected_state_size=expected_state_size)

    after = cheap_fingerprint(save_path)
    if before != after:
        raise RuntimeError(f"Source save changed on disk while building pickup state: {save_path}")

    return Phase2PickupResult(
        save_name=save_path.name,
        output_name=output_path.name,
        observed_seed=format_u16(observed_seed) or "0x0000",
        seed_frame=int(seed_frame),
        rng_at_seed=format_u32(rng_at_seed) or "0x00000000",
        pickup_tape_frames=int(pickup_tape.frame_count),
        baseline_frame=int(baseline_frame),
        neutral_frames=int(neutral_frames),
        expected_rng_at_baseline=format_u32(expected_rng) or "0x00000000",
        final_rng=format_u32(final_rng) or "0x00000000",
        rng_drift_from_expected=int(rng_drift),
        source_unchanged=True,
        attempts=1,
    )


def build_one_pickup_state_with_retries(
    save_path: Path,
    output_path: Path,
    *,
    core: Any,
    helper: ModuleType,
    sample_replicator: ModuleType,
    recipe: Any,
    pickup_tape: input_tape.InputTape,
    config: Phase2PickupConfig,
    expected_rng_at_baseline: int | None,
) -> Phase2PickupResult:
    """Retry one save a small number of times around transient runtime misses."""

    last_error: Exception | None = None
    for attempt in range(1, config.retry_count + 1):
        try:
            result = build_one_pickup_state(
                save_path,
                output_path,
                core=core,
                helper=helper,
                sample_replicator=sample_replicator,
                recipe=recipe,
                pickup_tape=pickup_tape,
                baseline_frame=config.baseline_frame,
                expected_state_size=config.expected_state_size,
                expected_rng_at_baseline=expected_rng_at_baseline,
                rng_drift_window=config.rng_drift_window,
            )
            return Phase2PickupResult(**{**asdict(result), "attempts": attempt})
        except Exception as exc:
            last_error = exc
            if attempt >= config.retry_count:
                break
            print(f"Retry {attempt + 1}/{config.retry_count} for {save_path.name}: {type(exc).__name__}: {exc}")
    assert last_error is not None
    raise last_error


def _read_status_expected_rng(status_path: Path) -> int | None:
    """Read learned baseline RNG from a previous status file."""

    try:
        if not status_path.is_file():
            return None
        payload = json.loads(status_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return _optional_int(payload.get("expected_rng_at_baseline"), "expected_rng_at_baseline")


def _append_error(path: Path, failure: Phase2PickupFailure) -> None:
    """Append one failure line to JSONL."""

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(asdict(failure), sort_keys=True) + "\n")


def _status_payload(
    *,
    config: Phase2PickupConfig,
    contract: SecondHalfCsvContract,
    total: int,
    processed: int,
    skipped_existing: int,
    written: int,
    failed: int,
    expected_rng_at_baseline: int | None,
    started_at: str,
    start_monotonic: float,
    current: str | None = None,
    last_result: Phase2PickupResult | None = None,
    last_error: Phase2PickupFailure | None = None,
    last_feature_control: dict[str, Any] | None = None,
    status_override: str | None = None,
) -> dict[str, Any]:
    """Build compact progress JSON for dashboard and resume checks."""

    elapsed = max(0.001, time.monotonic() - start_monotonic)
    active_done = max(0, processed - skipped_existing)
    rate = active_done / elapsed if active_done else 0.0
    remaining = max(0, total - processed)
    eta_seconds = None if rate <= 0 else remaining / rate
    return {
        "script": Path(__file__).name,
        "status": status_override
        if status_override is not None
        else ("running" if processed < total and failed == 0 else "updated"),
        "started_at": started_at,
        "updated_at": _utc_now(),
        "save_dir": str(_display_path(config.save_dir)),
        "output_dir": str(_display_path(config.output_dir)),
        "secondhalf_csv": str(_display_path(config.secondhalf_csv)),
        "pickup_tape": str(_display_path(config.pickup_tape_path)),
        "control_file": None if config.control_path is None else str(_display_path(config.control_path)),
        "zero_pickup_tape": None
        if config.zero_pickup_tape_path is None
        else str(_display_path(config.zero_pickup_tape_path)),
        "metadata_path": str(_display_path(config.metadata_path)),
        "csv_initial_seed": format_u16(contract.initial_seed),
        "csv_row_count": contract.row_count,
        "csv_t0_row_count": contract.t0_row_count,
        "csv_unique_t0_targets": contract.unique_t0_targets,
        "baseline_frame": config.baseline_frame,
        "target_states": config.expected_save_count,
        "expected_state_size": config.expected_state_size,
        "speed_toggles": config.speed_toggles,
        "unbounded_fast_forward": config.unbounded_fast_forward,
        "source_save_count": total,
        "processed": processed,
        "skipped_existing": skipped_existing,
        "written": written,
        "failed": failed,
        "remaining": remaining,
        "rate_saves_per_second": round(rate, 4),
        "eta_seconds": None if eta_seconds is None else round(eta_seconds, 1),
        "current": current,
        "expected_rng_at_baseline": format_u32(expected_rng_at_baseline),
        "last_result": None if last_result is None else asdict(last_result),
        "last_error": None if last_error is None else asdict(last_error),
        "last_feature_control": last_feature_control,
    }


def run_builder(config: Phase2PickupConfig) -> int:
    """Run the Phase 2 pickup-state build."""

    contract = read_secondhalf_csv_contract(config.secondhalf_csv)
    sample_replicator = load_sample_replicator()
    helper = sample_replicator.load_firsthalf_helper()
    recipe = load_recipe_for_contract(
        sample_replicator,
        contract,
        metadata_path=config.metadata_path,
        reset_tape_path=config.reset_tape_path,
        pre_input_tape_path=config.pre_input_tape_path,
    )
    saves = select_source_saves(
        config.save_dir,
        start_hex=config.start_hex,
        end_hex=config.end_hex,
        limit=config.limit,
    )
    if config.require_expected_save_count and len(saves) != config.expected_save_count:
        raise RuntimeError(
            f"Expected {config.expected_save_count} source save(s), found {len(saves)} in {config.save_dir}"
        )
    includes_zero_lane = any(source_key_from_save_name(save_path) == 0 for save_path in saves)
    pickup_tape = input_tape.read_tape(config.pickup_tape_path)
    zero_pickup_tape = None
    if config.zero_pickup_tape_path is not None and config.zero_pickup_tape_path.is_file():
        zero_pickup_tape = input_tape.read_tape(config.zero_pickup_tape_path)
    elif includes_zero_lane:
        raise RuntimeError(
            "0x0000.sav selected but special ACE bridge tape was not found: "
            f"{config.zero_pickup_tape_path}"
        )

    print(f"Source saves: {len(saves)} from {_display_path(config.save_dir)}")
    print(f"Output states: {_display_path(config.output_dir)}")
    print(f"CSV initial seed: {format_u16(contract.initial_seed)}")
    print(f"Replay metadata seed: {format_u16(recipe.target_seed)}")
    print(f"Default bridge tape frames: {pickup_tape.frame_count}")
    if zero_pickup_tape is not None:
        print(f"0x0000 special bridge tape frames: {zero_pickup_tape.frame_count}")
    print(f"Baseline frame: {config.baseline_frame}")
    if len(saves) != config.expected_save_count:
        print(
            f"Source count note: expected={config.expected_save_count}"
            f" found={len(saves)}; processing found saves."
        )
    if config.dry_run:
        print("Dry run: no emulator work and no states written.")
        return 0

    config.output_dir.mkdir(parents=True, exist_ok=True)
    status_path = config.output_dir / "_phase2_pickup_status.json"
    error_path = config.output_dir / "_phase2_pickup_errors.jsonl"
    expected_rng = (
        config.expected_rng_at_baseline
        if config.expected_rng_at_baseline is not None
        else _read_status_expected_rng(status_path)
    )

    core = helper.load_runtime_core(recipe.rom_path)
    if config.speed_toggles:
        _enable_speed_toggles(
            helper,
            core,
            unbounded_fast_forward=config.unbounded_fast_forward,
        )
        if not config.unbounded_fast_forward:
            print("Live unbounded fast-forward setup disabled by configuration.")

    started_at = _utc_now()
    start_monotonic = time.monotonic()
    skipped_existing = 0
    written = 0
    failed = 0
    last_result: Phase2PickupResult | None = None
    last_error: Phase2PickupFailure | None = None
    last_feature_control: dict[str, Any] | None = None
    feature_control_fingerprint: SaveFingerprint | None = None

    feature_control_fingerprint, applied_control = maybe_apply_live_feature_control(
        core,
        config.control_path,
        feature_control_fingerprint,
    )
    if applied_control is not None:
        last_feature_control = applied_control

    for index, save_path in enumerate(saves, start=1):
        feature_control_fingerprint, applied_control = maybe_apply_live_feature_control(
            core,
            config.control_path,
            feature_control_fingerprint,
        )
        if applied_control is not None:
            last_feature_control = applied_control

        if runtime_control_requests_stop(last_feature_control):
            write_json_atomic(
                status_path,
                _status_payload(
                    config=config,
                    contract=contract,
                    total=len(saves),
                    processed=max(0, index - 1),
                    skipped_existing=skipped_existing,
                    written=written,
                    failed=failed,
                    expected_rng_at_baseline=expected_rng,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    current=save_path.name,
                    last_result=last_result,
                    last_error=last_error,
                    last_feature_control=last_feature_control,
                    status_override="stopped_for_human_check",
                ),
            )
            print("Runtime control requested human-check stop; leaving mGBA open.")
            return 0

        while runtime_control_requests_pause(last_feature_control):
            write_json_atomic(
                status_path,
                _status_payload(
                    config=config,
                    contract=contract,
                    total=len(saves),
                    processed=max(0, index - 1),
                    skipped_existing=skipped_existing,
                    written=written,
                    failed=failed,
                    expected_rng_at_baseline=expected_rng,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    current=save_path.name,
                    last_result=last_result,
                    last_error=last_error,
                    last_feature_control=last_feature_control,
                    status_override="paused_for_human_check",
                ),
            )
            time.sleep(1.0)
            feature_control_fingerprint, applied_control = maybe_apply_live_feature_control(
                core,
                config.control_path,
                feature_control_fingerprint,
            )
            if applied_control is not None:
                last_feature_control = applied_control

        state_path = output_state_path(config.output_dir, save_path)
        if state_path.exists() and not config.overwrite:
            if not existing_state_is_complete(state_path, config.expected_state_size):
                print(
                    f"[{index}/{len(saves)}] rebuild bad-size existing {state_path.name}"
                    f" size={state_path.stat().st_size} expected={config.expected_state_size}"
                )
            else:
                skipped_existing += 1
                if index == 1 or skipped_existing % config.progress_every == 0:
                    print(f"[{index}/{len(saves)}] skip existing {state_path.name}")
                if index % config.status_every == 0:
                    write_json_atomic(
                        status_path,
                        _status_payload(
                            config=config,
                            contract=contract,
                            total=len(saves),
                            processed=index,
                            skipped_existing=skipped_existing,
                            written=written,
                            failed=failed,
                            expected_rng_at_baseline=expected_rng,
                            started_at=started_at,
                            start_monotonic=start_monotonic,
                            current=save_path.name,
                            last_result=last_result,
                            last_error=last_error,
                            last_feature_control=last_feature_control,
                        ),
                    )
                continue

        try:
            active_pickup_tape = pickup_tape_for_save(
                save_path,
                default_tape=pickup_tape,
                zero_tape=zero_pickup_tape,
            )
            result = build_one_pickup_state_with_retries(
                save_path,
                state_path,
                core=core,
                helper=helper,
                sample_replicator=sample_replicator,
                recipe=recipe,
                pickup_tape=active_pickup_tape,
                config=config,
                expected_rng_at_baseline=expected_rng,
            )
            if expected_rng is None:
                expected_rng = _parse_int(result.final_rng, "final_rng", minimum=0, maximum=0xFFFFFFFF)
            written += 1
            last_result = result
            if index == 1 or written % config.progress_every == 0:
                print(
                    f"[{index}/{len(saves)}] wrote {state_path.name}"
                    f" rng={result.final_rng} drift={result.rng_drift_from_expected}"
                )
        except Exception as exc:
            failed += 1
            last_error = Phase2PickupFailure(
                save_name=save_path.name,
                error=f"{type(exc).__name__}: {exc}",
                attempts=config.retry_count,
            )
            _append_error(error_path, last_error)
            print(f"[{index}/{len(saves)}] FAIL {save_path.name}: {last_error.error}")
            if config.stop_on_error:
                write_json_atomic(
                    status_path,
                    _status_payload(
                        config=config,
                        contract=contract,
                        total=len(saves),
                        processed=index,
                        skipped_existing=skipped_existing,
                        written=written,
                        failed=failed,
                        expected_rng_at_baseline=expected_rng,
                        started_at=started_at,
                        start_monotonic=start_monotonic,
                        current=save_path.name,
                        last_result=last_result,
                        last_error=last_error,
                        last_feature_control=last_feature_control,
                    ),
                )
                return 1

        if index == len(saves) or index % config.status_every == 0:
            write_json_atomic(
                status_path,
                _status_payload(
                    config=config,
                    contract=contract,
                    total=len(saves),
                    processed=index,
                    skipped_existing=skipped_existing,
                    written=written,
                    failed=failed,
                    expected_rng_at_baseline=expected_rng,
                    started_at=started_at,
                    start_monotonic=start_monotonic,
                    current=save_path.name,
                    last_result=last_result,
                    last_error=last_error,
                    last_feature_control=last_feature_control,
                ),
            )

    final_status = _status_payload(
        config=config,
        contract=contract,
        total=len(saves),
        processed=len(saves),
        skipped_existing=skipped_existing,
        written=written,
        failed=failed,
        expected_rng_at_baseline=expected_rng,
        started_at=started_at,
        start_monotonic=start_monotonic,
        current=None,
        last_result=last_result,
        last_error=last_error,
        last_feature_control=last_feature_control,
    )
    final_status["status"] = "finished" if failed == 0 else "finished_with_errors"
    write_json_atomic(status_path, final_status)
    print(f"Finished: written={written} skipped={skipped_existing} failed={failed}")
    return 0 if failed == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""

    def env_path(name: str) -> Path | None:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        return Path(value).expanduser() if value else None

    def env_int(name: str, default: int | None = None) -> int | None:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        if value is None or value == "":
            return default
        return int(value, 0)

    def env_bool(name: str, default: bool = False) -> bool:
        value = os.environ.get(f"{ENV_PREFIX}{name}")
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    parser = argparse.ArgumentParser(
        description="Build Phase 2 pre-pickup baseline savestates from first-half saves.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--save-dir", type=Path, default=env_path("SAVE_DIR") or DEFAULT_SAVE_DIR)
    parser.add_argument("--output-dir", type=Path, default=env_path("OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--secondhalf-csv",
        type=Path,
        default=env_path("SECONDHALF_CSV") or DEFAULT_SECOND_HALF_CSV,
    )
    parser.add_argument("--pickup-tape", type=Path, default=env_path("PICKUP_TAPE") or DEFAULT_PICKUP_TAPE)
    parser.add_argument(
        "--zero-pickup-tape",
        type=Path,
        default=env_path("ZERO_PICKUP_TAPE") or DEFAULT_ZERO_PICKUP_TAPE,
        help="ACE-specific seed-to-pre-pickup bridge tape used only for 0x0000.sav.",
    )
    parser.add_argument("--metadata", type=Path, default=env_path("METADATA") or DEFAULT_METADATA_PATH)
    parser.add_argument("--reset-tape", type=Path, default=env_path("RESET_TAPE"))
    parser.add_argument("--pre-input-tape", type=Path, default=env_path("PRE_INPUT_TAPE"))
    parser.add_argument(
        "--control-file",
        type=Path,
        default=env_path("CONTROL_FILE"),
        help=(
            "Runtime feature-control JSON file polled between saves. "
            "Defaults to _phase2_pickup_control.json in the output directory."
        ),
    )
    parser.add_argument("--baseline-frame", type=int, default=env_int("BASELINE_FRAME", DEFAULT_BASELINE_FRAME))
    parser.add_argument(
        "--expected-save-count",
        type=int,
        default=env_int("EXPECTED_SAVE_COUNT", DEFAULT_EXPECTED_SAVE_COUNT),
    )
    parser.add_argument(
        "--expected-state-size",
        type=int,
        default=env_int("EXPECTED_STATE_SIZE", DEFAULT_STATE_SIZE),
        help="Required byte size for completed Phase 2 `.ss0` files when resuming.",
    )
    parser.add_argument(
        "--require-expected-save-count",
        action="store_true",
        default=env_bool("REQUIRE_EXPECTED_SAVE_COUNT"),
    )
    parser.add_argument(
        "--expected-rng-at-baseline",
        type=lambda text: int(text, 0),
        default=env_int("EXPECTED_RNG_AT_BASELINE"),
    )
    parser.add_argument("--rng-drift-window", type=int, default=env_int("RNG_DRIFT_WINDOW", DEFAULT_RNG_DRIFT_WINDOW))
    parser.add_argument("--retry-count", type=int, default=env_int("RETRY_COUNT", DEFAULT_RETRY_COUNT))
    parser.add_argument("--limit", type=int, default=env_int("LIMIT"))
    parser.add_argument("--start-hex", type=lambda text: int(text, 0), default=env_int("START_HEX"))
    parser.add_argument("--end-hex", type=lambda text: int(text, 0), default=env_int("END_HEX"))
    parser.add_argument("--overwrite", action="store_true", default=env_bool("OVERWRITE"))
    parser.add_argument("--dry-run", action="store_true", default=env_bool("DRY_RUN"))
    parser.add_argument("--progress-every", type=int, default=env_int("PROGRESS_EVERY", DEFAULT_PROGRESS_EVERY))
    parser.add_argument("--status-every", type=int, default=env_int("STATUS_EVERY", DEFAULT_STATUS_EVERY))
    parser.add_argument("--stop-on-error", action="store_true", default=env_bool("STOP_ON_ERROR"))
    parser.add_argument(
        "--no-speed-toggles",
        action="store_true",
        default=env_bool("NO_SPEED_TOGGLES"),
        help="Do not enable live audio-kill/no-render/unbounded fast-forward toggles.",
    )
    parser.add_argument(
        "--unbounded-fast-forward",
        dest="unbounded_fast_forward",
        action="store_true",
        default=env_bool("UNBOUNDED_FAST_FORWARD", True),
        help="Force live Qt fast-forward to unbounded speed during startup setup.",
    )
    parser.add_argument(
        "--no-unbounded-fast-forward",
        dest="unbounded_fast_forward",
        action="store_false",
        help="Keep audio-kill/no-render setup, but disable live fast-forward for monitoring.",
    )
    return parser


def config_from_args(args: argparse.Namespace) -> Phase2PickupConfig:
    """Validate parsed CLI args."""

    if args.baseline_frame < 0:
        raise ValueError("--baseline-frame must be >= 0")
    if args.expected_save_count < 0:
        raise ValueError("--expected-save-count must be >= 0")
    if args.expected_state_size < 1:
        raise ValueError("--expected-state-size must be >= 1")
    if args.rng_drift_window < 0:
        raise ValueError("--rng-drift-window must be >= 0")
    if args.retry_count < 1:
        raise ValueError("--retry-count must be >= 1")
    if args.progress_every < 1:
        raise ValueError("--progress-every must be >= 1")
    if args.status_every < 1:
        raise ValueError("--status-every must be >= 1")
    if args.start_hex is not None and not 0 <= args.start_hex <= 0xFFFF:
        raise ValueError("--start-hex must fit in 16 bits")
    if args.end_hex is not None and not 0 <= args.end_hex <= 0xFFFF:
        raise ValueError("--end-hex must fit in 16 bits")
    if args.start_hex is not None and args.end_hex is not None and args.start_hex > args.end_hex:
        raise ValueError("--start-hex must be <= --end-hex")

    output_dir = _display_path(args.output_dir)

    return Phase2PickupConfig(
        save_dir=_display_path(args.save_dir),
        output_dir=output_dir,
        secondhalf_csv=_display_path(args.secondhalf_csv),
        pickup_tape_path=_display_path(args.pickup_tape),
        zero_pickup_tape_path=None
        if args.zero_pickup_tape is None
        else _display_path(args.zero_pickup_tape),
        metadata_path=_display_path(args.metadata),
        reset_tape_path=None if args.reset_tape is None else _display_path(args.reset_tape),
        pre_input_tape_path=None if args.pre_input_tape is None else _display_path(args.pre_input_tape),
        control_path=output_dir / DEFAULT_CONTROL_FILE_NAME
        if args.control_file is None
        else _display_path(args.control_file),
        baseline_frame=int(args.baseline_frame),
        expected_save_count=int(args.expected_save_count),
        expected_state_size=int(args.expected_state_size),
        require_expected_save_count=bool(args.require_expected_save_count),
        expected_rng_at_baseline=None
        if args.expected_rng_at_baseline is None
        else int(args.expected_rng_at_baseline) & 0xFFFFFFFF,
        rng_drift_window=int(args.rng_drift_window),
        retry_count=int(args.retry_count),
        limit=None if args.limit is None else int(args.limit),
        start_hex=None if args.start_hex is None else int(args.start_hex),
        end_hex=None if args.end_hex is None else int(args.end_hex),
        overwrite=bool(args.overwrite),
        dry_run=bool(args.dry_run),
        progress_every=max(1, int(args.progress_every)),
        status_every=max(1, int(args.status_every)),
        stop_on_error=bool(args.stop_on_error),
        speed_toggles=not bool(args.no_speed_toggles),
        unbounded_fast_forward=bool(args.unbounded_fast_forward),
    )


def main(argv: Iterable[str] | None = None) -> int:
    """CLI entrypoint."""

    args = build_parser().parse_args(argv)
    config = config_from_args(args)
    return run_builder(config)


if __name__ == "__main__":
    _exit_code = main()
    if _exit_code:
        raise SystemExit(_exit_code)
