r"""Replay the known-good first-half FR/LG title recipe exactly once.

This script is the fixed-recipe companion to `Seed-Bruteforcer.py`.

It does not search for an unknown hit. Instead, it replays the exact hit that
`Seed-Bruteforcer.py` last exported into its read-only checkpoint metadata.

It keeps the same practical workflow as the main first-half script:

- load `1 from egg.sav` from the main `<repo-root>` folder
- prefer replay metadata whose `target_seed` matches the current configured
  target in `Seed-Bruteforcer.py`
- use the locked `1 from egg - locked-baseline-metadata.json` plus
  `1 from egg - locked-baseline` pair only when it still matches that current
  target seed
- otherwise fall back to the latest `1 from egg - replay-metadata.json`
- require the exact read-only checkpoint named by the selected metadata file
- load that checkpoint as the calibrated pre-input title state
- perform only the final known-good input described by that metadata
- save a success savestate and pause the visible Qt core on success

This replay copy intentionally reuses the proven helper code from `Seed-Bruteforcer.py`
so the title-skip logic, Timer 1 seed detection, Audio killswitch behavior,
and rolling-checkpoint/save handling stay aligned with the brute-force path
that produced the logged match, while still skipping all losing attempts. When
the successful search has already exported `1 from egg - replay-readonly`, this replay
uses that exact calibrated no-input checkpoint instead of trying to derive it
again from the base save.

The policy behind that behavior is documented in:

- `WORKFLOW_DECISION_LOG.md`
- `INITIAL_SEED_CSV_REFERENCE.md`
- `timer1_observations.md`
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import sys
from pathlib import Path


DEFAULT_METADATA_NAME = "1 from egg - replay-metadata.json"
DEFAULT_SOURCE_CHECKPOINT_NAME = "1 from egg - replay-readonly"
DEFAULT_LOCKED_METADATA_NAME = "1 from egg - locked-baseline-metadata.json"
DEFAULT_LOCKED_SOURCE_CHECKPOINT_NAME = "1 from egg - locked-baseline"
DEFAULT_LOCKED_SAVE_NAME = "1 from egg.sav"
DEFAULT_BASELINE_CHECKPOINT_STATE_NAME = "1 from egg - replication-base-checkpoint"
DEFAULT_SUCCESS_STATE_NAME = "1 from egg - replication.ss0"
DEFAULT_CHECKPOINT_STATE_NAME = "1 from egg - replication-checkpoint"
STATUS_ENV_NAME = "MGBA_FIRSTHALF_REPLICATION_STATUS_PATH"
METADATA_OVERRIDE_ENV_NAME = "MGBA_FIRSTHALF_REPLICATION_METADATA_PATH"
SAVE_OVERRIDE_ENV_NAME = "MGBA_FIRSTHALF_REPLICATION_SAVE_PATH"
POST_REPLAY_TAPE_ENV_NAME = "MGBA_FIRSTHALF_POST_REPLAY_TAPE_PATH"
POST_REPLAY_STATUS_ENV_NAME = "MGBA_FIRSTHALF_POST_REPLAY_STATUS_PATH"
POST_REPLAY_STATE_ENV_NAME = "MGBA_FIRSTHALF_POST_REPLAY_STATE_PATH"
LIVE_ARTIFACT_DIR_GLOB = "live-*"
LIVE_ARTIFACT_ROOT_NAME = "live-lanes"
REQUIRED_METADATA_FIELDS = frozenset(
    {
        "metadata_version",
        "readonly_checkpoint_name",
        "target_seed",
        "delay_frames",
        "button_name",
        "seed_frame",
        "rng_at_seed",
        "timer1_count_pre",
        "timer1_control_pre",
        "pre_input_neutral_frames",
        "seed_timeout",
    }
)
_UINT16_METADATA_FIELDS = ("target_seed", "timer1_count_pre", "timer1_control_pre")
_UINT32_METADATA_FIELDS = ("rng_at_seed",)
_NON_NEGATIVE_METADATA_FIELDS = ("delay_frames", "seed_frame", "pre_input_neutral_frames")
_POSITIVE_METADATA_FIELDS = ("seed_timeout",)
_OPTIONAL_UINT16_METADATA_FIELDS = ("prng_discerned_seed",)
_OPTIONAL_SIGNED_METADATA_FIELDS = ("prng_discerned_steps_from_rng",)

_HELPER_MODULE = None


def _load_firsthalf_helpers():
    """Load the sibling `Seed-Bruteforcer.py` helper module without running its `main()`.

    This stays as an explicit file import instead of a package-style import so
    the maintained brute-force and replay copies can keep living as operator-
    visible standalone scripts in the example folder while still sharing the
    exact same helper logic.
    """

    script_path = Path(__file__).with_name("Seed-Bruteforcer.py")
    spec = importlib.util.spec_from_file_location("firsthalf_helpers", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _firsthalf():
    """Return the cached helper module for `Seed-Bruteforcer.py`."""

    global _HELPER_MODULE
    if _HELPER_MODULE is None:
        _HELPER_MODULE = _load_firsthalf_helpers()
    return _HELPER_MODULE


def _probe_state(core, helper, label: str) -> None:
    """Log vblank/timer1 state snapshots to diagnose drift."""

    try:
        vblank2 = core.memory.u32[helper.GMAIN_VBLANK2_ADDR]
    except Exception:
        vblank2 = 0
    try:
        timer1_count = core.memory.u16[helper.TIMER1_COUNT_ADDR]
        timer1_control = core.memory.u16[helper.TIMER1_CONTROL_ADDR]
    except Exception:
        timer1_count = 0
        timer1_control = 0
    try:
        rng_value = core.memory.u32[helper.GRNG_VALUE_ADDR]
    except Exception:
        rng_value = 0
    try:
        keyinput_addr = getattr(helper, "KEYINPUT_ADDR", 0x04000130)
        keyinput = core.memory.u16[keyinput_addr]
    except Exception:
        keyinput = 0
    frame_counter = getattr(core, "frame_counter", 0)
    print(
        f"Probe {label}: frame_counter={frame_counter}"
        f" vblank2={vblank2} timer1_count=0x{timer1_count:04X}"
        f" timer1_control=0x{timer1_control:04X}"
        f" keyinput=0x{keyinput:04X} rng=0x{rng_value:08X}"
    )


def load_replay_metadata(metadata_path: Path) -> dict[str, object]:
    """Load and validate the immutable checkpoint metadata from `Seed-Bruteforcer.py`.

    Replay determinism depends on this file matching the exact checkpoint that
    produced the hit. Validate the fields once up front so the hot replay path
    does not need repeated range checks while stepping frames.
    """

    if not metadata_path.is_file():
        raise RuntimeError(
            "The fixed first-half replay requires metadata from a successful"
            f" brute-force hit: {metadata_path}. Run Seed-Bruteforcer.py until it"
            " finds the target and exports the read-only replay artifacts."
        )

    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise RuntimeError("Replay metadata must be a JSON object.")
    missing = REQUIRED_METADATA_FIELDS.difference(metadata)
    if missing:
        raise RuntimeError(
            "Replay metadata is missing required field(s):"
            f" {', '.join(sorted(missing))}"
        )
    if _metadata_int(metadata, "metadata_version") != _firsthalf().REPLAY_METADATA_VERSION:
        raise RuntimeError(
            "Unsupported replay metadata version:"
            f" {metadata['metadata_version']}"
        )
    if str(metadata["button_name"]) not in {"A", "Start"}:
        raise RuntimeError(f"Unsupported replay button in metadata: {metadata['button_name']}")
    _metadata_checkpoint_name(metadata)
    for field in _UINT16_METADATA_FIELDS:
        _require_metadata_range(metadata, field, 0, 0xFFFF)
    for field in _UINT32_METADATA_FIELDS:
        _require_metadata_range(metadata, field, 0, 0xFFFFFFFF)
    for field in _NON_NEGATIVE_METADATA_FIELDS:
        _require_metadata_range(metadata, field, 0, None)
    for field in _POSITIVE_METADATA_FIELDS:
        _require_metadata_range(metadata, field, 1, None)
    # Older replay metadata will not have the PRNG failsafe fields yet, so
    # only validate them when they are present. Replay stays backward-compatible
    # with those older hits instead of treating the new PRNG diagnostics as
    # required fields.
    for field in _OPTIONAL_UINT16_METADATA_FIELDS:
        if field in metadata:
            _require_metadata_range(metadata, field, 0, 0xFFFF)
    for field in _OPTIONAL_SIGNED_METADATA_FIELDS:
        if field in metadata and metadata[field] is not None:
            _metadata_int(metadata, field)
    return metadata


def _configured_target_seed() -> int | None:
    """Return the current target seed configured by `Seed-Bruteforcer.py`, if available."""

    helper = _firsthalf()
    default_target = getattr(helper, "DEFAULT_TARGET_SEED", None)
    if default_target is None:
        return None
    env_default_seed = getattr(helper, "_env_default_seed", None)
    if callable(env_default_seed):
        return int(env_default_seed("MGBA_TARGET_SEED", default_target)) & 0xFFFF
    return int(default_target) & 0xFFFF


def _env_override_path(env_name: str) -> Path | None:
    """Read one optional replay path override from the environment.

    The real Qt deployment harness stages replay artifacts into a temporary
    maintained mGBA directory. This override keeps that run hermetic even when
    the source metadata still contains absolute paths from the main workspace.
    The returned path intentionally stays lexical: resolving it can follow the
    local `<repo-root>` junction back into the vendored source tree and make
    status/provenance files report the wrong workspace.
    """

    raw_value = os.environ.get(env_name)
    if not raw_value:
        return None
    return Path(raw_value).expanduser()


def _candidate_metadata_paths(mgba_dir: Path) -> list[tuple[str, Path]]:
    """Return stashed root metadata first, then matching live-lane sidecars.

    The project root keeps old one-off artifacts under `Artifacts`, while
    maintained live runs live under `live-lanes/live-...`. Root-level live
    folders are no longer scanned.
    """

    helper = _firsthalf()
    artifact_dir_for = getattr(helper, "artifact_dir_for", lambda path: path)
    root_artifact_dir = artifact_dir_for(mgba_dir)
    candidates: list[tuple[str, Path]] = [
        ("locked baseline", root_artifact_dir / DEFAULT_LOCKED_METADATA_NAME),
        ("latest replay", root_artifact_dir / DEFAULT_METADATA_NAME),
    ]
    live_root = mgba_dir / LIVE_ARTIFACT_ROOT_NAME
    live_dirs = sorted(
        path for path in live_root.glob(LIVE_ARTIFACT_DIR_GLOB) if path.is_dir()
    ) if live_root.is_dir() else []
    for live_dir in live_dirs:
        candidates.extend(
            [
                (f"{live_dir.name} locked baseline", live_dir / DEFAULT_LOCKED_METADATA_NAME),
                (f"{live_dir.name} latest replay", live_dir / DEFAULT_METADATA_NAME),
            ]
        )
    return candidates


def select_replay_metadata_path(mgba_dir: Path) -> Path:
    """Pick replay metadata that matches the current configured target seed.

    The replay script should follow the same target seed that `Seed-Bruteforcer.py`
    currently searches for. If the older locked baseline is for another seed,
    replaying it silently would no longer match the current first-half workflow.
    """

    metadata_override = _env_override_path(METADATA_OVERRIDE_ENV_NAME)
    if metadata_override is not None:
        return metadata_override

    desired_target_seed = _configured_target_seed()
    available: list[tuple[str, Path, int]] = []

    for label, candidate_path in _candidate_metadata_paths(mgba_dir):
        if not candidate_path.is_file():
            continue
        metadata = load_replay_metadata(candidate_path)
        available.append((label, candidate_path, _metadata_int(metadata, "target_seed")))

    if desired_target_seed is None:
        for label, candidate_path, _target_seed in available:
            if label.endswith("locked baseline"):
                return candidate_path
        return mgba_dir / DEFAULT_METADATA_NAME

    for label, candidate_path, target_seed in available:
        if label.endswith("locked baseline") and target_seed == desired_target_seed:
            return candidate_path
    for _label, candidate_path, target_seed in available:
        if target_seed == desired_target_seed:
            return candidate_path

    if available:
        available_targets = ", ".join(
            f"{label}=0x{target_seed:04X} ({candidate_path.name})"
            for label, candidate_path, target_seed in available
        )
        raise RuntimeError(
            "No replay metadata matches the current configured first-half target:"
            f" target=0x{desired_target_seed:04X}; available {available_targets}."
            " Run Seed-Bruteforcer.py for the current target so it can export matching"
            " replay metadata and a read-only checkpoint."
        )
    return mgba_dir / DEFAULT_METADATA_NAME


def _metadata_int(metadata: dict[str, object], field: str) -> int:
    """Read an integer metadata value, accepting JSON ints or 0x-prefixed text."""

    value = metadata[field]
    try:
        if isinstance(value, bool):
            raise ValueError("booleans are not valid replay metadata integers")
        return int(value, 0) if isinstance(value, str) else int(value)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(
            f"Replay metadata field {field!r} must be an integer or"
            f" 0x-prefixed integer string, got {value!r}."
        ) from exc


def _metadata_bool(metadata: dict[str, object], field: str, default: bool) -> bool:
    """Return one optional boolean metadata field with strict type checking."""

    value = metadata.get(field, default)
    if isinstance(value, bool):
        return value
    raise RuntimeError(f"Replay metadata field {field!r} must be a boolean.")


def _require_metadata_range(
    metadata: dict[str, object],
    field: str,
    minimum: int,
    maximum: int | None,
) -> None:
    """Validate one integer field from replay metadata."""

    value = _metadata_int(metadata, field)
    if value < minimum or (maximum is not None and value > maximum):
        if maximum is None:
            bounds = f">= {minimum}"
        else:
            bounds = f"between {minimum} and {maximum}"
        raise RuntimeError(
            f"Replay metadata field {field!r} must be {bounds}, got {value}."
        )


def _optional_metadata_int(metadata: dict[str, object], field: str) -> int | None:
    """Return one optional integer field from replay metadata.

    The replay path must keep accepting older metadata that predates newer
    verification fields, so optional integers need a helper that cleanly
    distinguishes "missing" from "present but malformed".
    """

    if field not in metadata or metadata[field] is None:
        return None
    return _metadata_int(metadata, field)


def _metadata_button_key(helper, button_name: str) -> int:
    if button_name == "A":
        return helper.GBA.KEY_A
    if button_name == "Start":
        return helper.GBA.KEY_START
    raise RuntimeError(f"Unsupported replay button: {button_name}")


def _metadata_checkpoint_name(metadata: dict[str, object]) -> str:
    """Return the local checkpoint filename recorded by the metadata.

    `readonly_checkpoint_name` should be a filename inside the maintained mGBA
    directory, not an arbitrary path. Keeping it local avoids accidental replay
    against stale checkpoints from another workspace.
    """

    checkpoint_name = str(metadata.get("readonly_checkpoint_name", DEFAULT_SOURCE_CHECKPOINT_NAME))
    checkpoint_path = Path(checkpoint_name)
    if not checkpoint_name or checkpoint_path.is_absolute() or checkpoint_path.name != checkpoint_name:
        raise RuntimeError(
            "Replay metadata field 'readonly_checkpoint_name' must be a local"
            f" checkpoint filename, got {checkpoint_name!r}."
        )
    return checkpoint_name


def _metadata_checkpoint_path(artifact_dir: Path, metadata: dict[str, object]) -> Path:
    return artifact_dir / _metadata_checkpoint_name(metadata)


def _write_json_file(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f".{path.name}.tmp")
    temp_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp_path.replace(path)


def _probe_core_for_status(core) -> dict[str, object]:
    probe: dict[str, object] = {
        "frame_counter": getattr(core, "frame_counter", None),
        "platform": getattr(core, "platform", None),
    }
    try:
        probe["rng"] = f"0x{int(core.memory.u32[_firsthalf().GRNG_VALUE_ADDR]) & 0xFFFFFFFF:08X}"
    except Exception:
        pass
    try:
        keyinput_addr = getattr(_firsthalf(), "KEYINPUT_ADDR", 0x04000130)
        probe["keyinput"] = f"0x{int(core.memory.u16[keyinput_addr]) & 0xFFFF:04X}"
    except Exception:
        pass
    return probe


def _load_input_tape_helper():
    helper_path = Path(__file__).parents[1] / "input_tape.py"
    spec = importlib.util.spec_from_file_location("firsthalf_post_replay_input_tape", helper_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {helper_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def maybe_run_post_replay_input_tape(core, helper) -> None:
    """Optionally run one input tape immediately after seed replication."""

    raw_tape_path = os.environ.get(POST_REPLAY_TAPE_ENV_NAME)
    if not raw_tape_path:
        return

    tape_path = Path(raw_tape_path).expanduser()
    status_path = _env_override_path(POST_REPLAY_STATUS_ENV_NAME)
    state_path = _env_override_path(POST_REPLAY_STATE_ENV_NAME)

    def write_status(payload: dict[str, object]) -> None:
        if status_path is None:
            return
        _write_json_file(
            status_path,
            {
                "script": "Seed-Replicator.py",
                "post_replay_tape": str(tape_path),
                "post_replay_state": None if state_path is None else str(state_path),
                **payload,
            },
        )

    try:
        write_status({"stage": "loading_tape", "before_tape_probe": _probe_core_for_status(core)})
        input_tape = _load_input_tape_helper()
        tape = input_tape.read_tape(tape_path)
        before_probe = _probe_core_for_status(core)
        write_status(
            {
                "stage": "running_tape",
                "tape_frames": int(tape.frame_count),
                "before_tape_probe": before_probe,
            }
        )
        result = input_tape.replay_tape(
            core,
            tape,
            clear_before=True,
            clear_after=True,
            pause_before=True,
            pause_after=True,
            use_batch=True,
            verify_frame_counter=False,
        )
        if state_path is not None:
            helper.save_state_file(core, state_path)
        write_status(
            {
                "stage": "finished",
                "tape_frames": int(tape.frame_count),
                "before_tape_probe": before_probe,
                "tape_start_probe": dict(result.start_probe),
                "tape_end_probe": dict(result.end_probe),
                "after_tape_probe": _probe_core_for_status(core),
            }
        )
    except BaseException as exc:
        write_status(
            {
                "stage": "error",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        )


def _metadata_path(artifact_dir: Path, metadata: dict[str, object], field: str, fallback: object) -> Path:
    """Resolve a path stored in metadata, relative to its artifact directory."""

    path = Path(str(metadata.get(field, fallback))).expanduser()
    return path if path.is_absolute() else artifact_dir / path


def _require_read_only(path: Path, description: str = "calibrated replay checkpoint") -> None:
    """Fail if one baseline artifact is mutable."""

    if path.stat().st_mode & stat.S_IWRITE:
        raise RuntimeError(
            f"The {description} is not read-only:"
            f" {path}. Rerun Seed-Bruteforcer.py so it can export a protected"
            " replay baseline artifact."
        )


def replay_known_recipe(
    core,
    helper,
    *,
    checkpoint_path: Path,
    use_runtime_checkpoint: bool,
    metadata: dict[str, object],
):
    """Run the metadata-described title input from the prepared checkpoint."""

    qt_mode = False
    if hasattr(helper, "_qt_mode_enabled"):
        try:
            qt_mode = bool(helper._qt_mode_enabled())
        except Exception:
            qt_mode = False
    try:
        helper.restore_checkpoint(core, checkpoint_path, use_runtime_checkpoint, qt_mode=qt_mode)
    except TypeError:
        helper.restore_checkpoint(core, checkpoint_path, use_runtime_checkpoint)
    _probe_state(core, helper, "after_checkpoint_restore")
    core.set_keys(raw=0)
    if helper.observe_initial_seed_from_timer1(core) is not None:
        raise RuntimeError(
            "Replay checkpoint is no longer in the pre-seed title state:"
            " Timer 1 was already stopped before the calibrated replay input."
        )

    neutral_frames = _metadata_int(metadata, "pre_input_neutral_frames")
    neutral_frames_included = _metadata_bool(
        metadata,
        "pre_input_neutral_frames_included",
        True,
    )
    if neutral_frames:
        if neutral_frames_included:
            # Current `Seed-Bruteforcer.py` saves the replay checkpoint after
            # this neutral window, immediately before the winning input.
            print(
                "Replay checkpoint already includes pre-input neutral frame(s):"
                f" {neutral_frames}; not reapplying them."
            )
            _probe_state(core, helper, "after_included_neutral_frames")
        else:
            # Older locked metadata used this field as work still to do from
            # the restored checkpoint. Preserve that contract instead of
            # making historical replay artifacts silently drift.
            print(f"Applying legacy pre-input neutral frame(s): {neutral_frames}.")
            core.set_keys(raw=0)
            run_frames_fast = getattr(helper, "run_frames_fast", None)
            if callable(run_frames_fast):
                run_frames_fast(core, neutral_frames)
            else:
                for _ in range(neutral_frames):
                    core.run_frame()
            _probe_state(core, helper, "after_legacy_neutral_frames")
            if helper.observe_initial_seed_from_timer1(core) is not None:
                raise RuntimeError(
                    "Replay checkpoint drifted during legacy neutral frames:"
                    " Timer 1 stopped before the calibrated replay input."
                )

    button_name = str(metadata["button_name"])
    target_button_key = _metadata_button_key(helper, button_name)
    seed_timeout = _metadata_int(metadata, "seed_timeout")
    core.set_keys(target_button_key)
    core.run_frame()
    seed_value = helper.observe_initial_seed_from_timer1(core)
    core.set_keys(raw=0)
    if seed_value is not None:
        return seed_value, core.frame_counter, core.memory.u32[helper.GRNG_VALUE_ADDR], button_name

    for _ in range(max(seed_timeout - 1, 0)):
        core.run_frame()
        seed_value = helper.observe_initial_seed_from_timer1(core)
        if seed_value is not None:
            return seed_value, core.frame_counter, core.memory.u32[helper.GRNG_VALUE_ADDR], button_name

    raise RuntimeError(
        "The fixed first-half replay did not observe a seed within"
        f" {seed_timeout} frames after the calibrated replay input."
    )


def prepare_replay_checkpoint(
    core,
    helper,
    *,
    artifact_dir: Path | None = None,
    mgba_dir: Path | None = None,
    metadata: dict[str, object],
) -> bool:
    """Prepare the winning no-input checkpoint used by the replay.

    The read-only checkpoint named in metadata is treated as the calibrated
    source of truth. The replay path intentionally reads it but never writes to
    it, and it does not mirror it into the runtime scratch slot. The scratch
    slot is process/session state in the visible Qt bridge, so reusing it across
    repeated replay launches can resurrect the deterministic-but-wrong branch
    from an earlier run.
    """

    if artifact_dir is None:
        if mgba_dir is None:
            raise RuntimeError("prepare_replay_checkpoint requires an artifact directory.")
        artifact_dir = mgba_dir

    source_checkpoint_path = _metadata_checkpoint_path(artifact_dir, metadata)
    if not source_checkpoint_path.is_file():
        raise RuntimeError(
            "The fixed first-half replay requires the calibrated read-only"
            f" checkpoint {source_checkpoint_path}. Run Seed-Bruteforcer.py until it"
            " finds the hit and exports the read-only replay artifacts before"
            " replaying."
        )
    _require_read_only(source_checkpoint_path)

    expected_count = _metadata_int(metadata, "timer1_count_pre")
    expected_control = _metadata_int(metadata, "timer1_control_pre")
    qt_mode = False
    if hasattr(helper, "_qt_mode_enabled"):
        try:
            qt_mode = bool(helper._qt_mode_enabled())
        except Exception:
            qt_mode = False
    last_count = None
    last_control = None
    for attempt in range(3):
        core.set_keys(raw=0)
        try:
            helper.load_state_file(core, source_checkpoint_path, qt_mode=qt_mode)
        except TypeError:
            helper.load_state_file(core, source_checkpoint_path)
        _probe_state(core, helper, "after_checkpoint_load")
        core.set_keys(raw=0)
        last_count, last_control = helper.read_timer1_state(core)
        if last_count == expected_count and last_control == expected_control:
            print(
                "Using read-only calibrated checkpoint from the successful"
                f" brute-force run: {source_checkpoint_path}"
            )
            print("Runtime scratch checkpoints are disabled for fixed replay repeatability.")
            return False
        print(
            "Warning: replay checkpoint Timer 1 mismatch"
            f" (attempt {attempt + 1}/3):"
            f" expected count=0x{expected_count:04X}"
            f" control=0x{expected_control:04X}"
            f" observed count=0x{last_count:04X}"
            f" control=0x{last_control:04X}"
        )

    raise RuntimeError(
        "The read-only replay checkpoint does not match its recorded Timer 1"
        " pre-input state. Rerun Seed-Bruteforcer.py so it can regenerate the"
        " calibrated checkpoint and metadata from the exact winning branch."
    )


def run_replay(*, save_path_override: Path | None = None) -> int:
    """Run the fixed first-half replay once.

    `save_path_override` is used by the Windows save-picker wrapper. When a
    caller supplies an arbitrary `.sav`, load it as a temporary save so replay
    can test that file without writing back to the user's selected save.
    """

    helper = _firsthalf()
    mgba_dir = helper.resolve_mgba_dir()
    configured_target_seed = _configured_target_seed()
    metadata_path = select_replay_metadata_path(mgba_dir)
    metadata = load_replay_metadata(metadata_path)
    artifact_dir = metadata_path.parent
    metadata_target_seed = _metadata_int(metadata, "target_seed")
    if (
        configured_target_seed is not None
        and metadata_target_seed != configured_target_seed
    ):
        raise RuntimeError(
            "Selected replay metadata does not match the current configured"
            " first-half target:"
            f" configured=0x{configured_target_seed:04X}"
            f" metadata=0x{metadata_target_seed:04X}."
            " Run Seed-Bruteforcer.py for the configured target so replay stays aligned"
            " with the current workflow."
        )
    rom_path = _metadata_path(artifact_dir, metadata, "rom_path", helper._env_default_rom()).resolve()
    env_save_override = _env_override_path(SAVE_OVERRIDE_ENV_NAME)
    if save_path_override is not None and env_save_override is not None:
        raise RuntimeError(
            "Both an explicit replay save override argument and"
            f" {SAVE_OVERRIDE_ENV_NAME} were provided. Use only one override"
            " source so the replay save stays unambiguous."
        )
    effective_save_override = (
        save_path_override if save_path_override is not None else env_save_override
    )
    save_path = (
        Path(effective_save_override).expanduser()
        if effective_save_override is not None
        else _metadata_path(artifact_dir, metadata, "save_path", artifact_dir / helper.DEFAULT_SAVE_NAME)
    )
    source_checkpoint_path = _metadata_checkpoint_path(artifact_dir, metadata)
    done_path = artifact_dir / DEFAULT_SUCCESS_STATE_NAME
    locked_baseline = bool(metadata.get("locked_baseline", False))
    if locked_baseline:
        _require_read_only(metadata_path, "locked replay metadata")
        if effective_save_override is None and save_path.is_file():
            _require_read_only(save_path, "locked replay save")

    qt_mode = helper._qt_mode_enabled()
    core = helper.load_runtime_core(rom_path)
    _probe_state(core, helper, "after_rom_load")
    helper.ensure_audio_killswitch_defaults(mgba_dir)
    helper.ensure_no_render_defaults(mgba_dir)
    helper.ensure_fast_forward_defaults(mgba_dir)
    helper.ensure_live_audio_killswitch(core, qt_mode=qt_mode)
    helper.ensure_live_no_render_mode(core, qt_mode=qt_mode)
    helper.ensure_live_unbounded_fast_forward(core, qt_mode=qt_mode)

    if (not qt_mode and core.platform != helper.GBA.PLATFORM_GBA) or (
        qt_mode and core.platform not in (0, helper.GBA.PLATFORM_GBA)
    ):
        raise SystemExit("This script requires a GBA ROM.")

    try:
        helper.load_required_save_file(
            core,
            save_path,
            qt_mode=qt_mode,
            temporary=True,
        )
    except TypeError:
        helper.load_required_save_file(core, save_path, qt_mode=qt_mode)
    _probe_state(core, helper, "after_save_load")

    if done_path.exists():
        done_path.unlink()

    print(f"ROM: {rom_path}")
    print(f"mGBA directory: {mgba_dir}")
    print(f"Replay artifact directory: {artifact_dir}")
    print(f"Persistent save file: {save_path}")
    if effective_save_override is not None:
        print("Using user-selected save file for this replay run.")
    if qt_mode:
        print("Running against the visible Qt GUI core.")
    if locked_baseline:
        print("Using locked first-half replay baseline.")
    if configured_target_seed is not None:
        print(f"Configured target seed: 0x{configured_target_seed:04X}")
    print(f"Replay metadata: {metadata_path}")
    print(f"Target seed: 0x{metadata_target_seed:04X}")
    print(f"Fixed delay: {_metadata_int(metadata, 'delay_frames')}")
    print(f"Fixed button: {metadata['button_name']}")
    print(f"Expected seed_frame from metadata: {_metadata_int(metadata, 'seed_frame')}")
    print(f"Expected rng_at_seed from metadata: 0x{_metadata_int(metadata, 'rng_at_seed'):08X}")
    print(f"Read-only working checkpoint savestate: {source_checkpoint_path}")
    print(f"Done savestate: {done_path}")

    use_runtime_checkpoint = prepare_replay_checkpoint(
        core=core,
        helper=helper,
        artifact_dir=artifact_dir,
        metadata=metadata,
    )

    seed_value, seed_frame, rng_value, observed_button = replay_known_recipe(
        core=core,
        helper=helper,
        checkpoint_path=source_checkpoint_path,
        use_runtime_checkpoint=use_runtime_checkpoint,
        metadata=metadata,
    )

    print(
        "Observed fixed replay result:"
        f" delay={_metadata_int(metadata, 'delay_frames')}"
        f" button={observed_button}"
        f" seed_frame={seed_frame}"
        f" seed=0x{seed_value:04X}"
        f" rng_at_seed=0x{rng_value:08X}"
    )

    discern_seed = getattr(helper, "discern_initial_seed_from_rng_state", None)
    if callable(discern_seed):
        prng_discerned = discern_seed(rng_value)
    else:
        prng_discerned = None
    if prng_discerned is None:
        prng_discerned_seed = None
        prng_discerned_steps = None
        print(
            "PRNG failsafe could not infer a nearby 16-bit seed state"
            f" from rng_at_seed=0x{rng_value:08X}."
        )
    else:
        prng_discerned_seed, prng_discerned_steps = prng_discerned
        # Keep the same interpretation as `Seed-Bruteforcer.py`: this is supporting
        # diagnostics around the observed seeded PRNG orbit, not a replacement
        # for the Timer 1 seed comparison that decides success.
        print(
            "PRNG failsafe inferred a nearby 16-bit seed candidate:"
            f" seed=0x{prng_discerned_seed:04X}"
            f" signed_steps_from_rng={prng_discerned_steps}"
            f" rng_at_seed=0x{rng_value:08X}"
        )

    expected_target_seed = _metadata_int(metadata, "target_seed")
    expected_seed_frame = _metadata_int(metadata, "seed_frame")
    expected_rng_at_seed = _metadata_int(metadata, "rng_at_seed")
    expected_prng_discerned_seed = _optional_metadata_int(metadata, "prng_discerned_seed")
    expected_prng_discerned_steps = _optional_metadata_int(
        metadata,
        "prng_discerned_steps_from_rng",
    )
    expected_delay = _metadata_int(metadata, "delay_frames")
    matched = seed_value == expected_target_seed
    status_writer = getattr(helper, "write_status_marker_from_env", None)
    if callable(status_writer):
        status_writer(
            STATUS_ENV_NAME,
            {
                "script": "Seed-Replicator.py",
                "matched": bool(matched),
                "configured_target_seed": None
                if configured_target_seed is None
                else int(configured_target_seed) & 0xFFFF,
                "target_seed": int(expected_target_seed) & 0xFFFF,
                "seed": int(seed_value) & 0xFFFF,
                "expected_seed_frame": int(expected_seed_frame),
                "seed_frame": int(seed_frame),
                "expected_rng_at_seed": int(expected_rng_at_seed) & 0xFFFFFFFF,
                "rng_at_seed": int(rng_value) & 0xFFFFFFFF,
                "expected_prng_discerned_seed": None
                if expected_prng_discerned_seed is None
                else int(expected_prng_discerned_seed) & 0xFFFF,
                "prng_discerned_seed": None
                if prng_discerned_seed is None
                else int(prng_discerned_seed) & 0xFFFF,
                "expected_prng_discerned_steps_from_rng": expected_prng_discerned_steps,
                "prng_discerned_steps_from_rng": prng_discerned_steps,
                "delay_frames": int(expected_delay),
                "button_name": str(observed_button),
                "readonly_checkpoint": str(source_checkpoint_path),
                "metadata": str(metadata_path),
                "locked_baseline": bool(locked_baseline),
                "save_path_override": effective_save_override is not None,
                "save_path": str(save_path),
                "done_savestate": str(done_path),
            },
        )

    if not matched:
        raise RuntimeError(
            "The fixed first-half replay did not reproduce the expected seed:"
            f" expected=0x{expected_target_seed:04X}"
            f" observed=0x{seed_value:04X}"
            ". Rerun Seed-Bruteforcer.py so it can regenerate the read-only checkpoint"
            " and metadata from the exact pre-input winning state."
        )

    helper.save_state_file(core, done_path)
    if seed_frame != expected_seed_frame:
        print(
            "Warning:"
            f" expected seed_frame={expected_seed_frame}"
            f" but observed {seed_frame}."
        )
    if rng_value != expected_rng_at_seed:
        print(
            "Warning:"
            f" expected rng_at_seed=0x{expected_rng_at_seed:08X}"
            f" but observed 0x{rng_value:08X}."
        )
    if expected_prng_discerned_seed is not None and prng_discerned_seed is None:
        print(
            "Warning:"
            f" metadata expected prng_discerned_seed=0x{expected_prng_discerned_seed:04X}"
            " but the current replay could not infer one from rng_at_seed."
        )
    elif (
        expected_prng_discerned_seed is not None
        and prng_discerned_seed != expected_prng_discerned_seed
    ):
        print(
            "Warning:"
            f" expected prng_discerned_seed=0x{expected_prng_discerned_seed:04X}"
            f" but observed 0x{prng_discerned_seed:04X}."
        )
    if expected_prng_discerned_steps is not None and prng_discerned_steps is None:
        print(
            "Warning:"
            f" metadata expected prng_discerned_steps_from_rng={expected_prng_discerned_steps}"
            " but the current replay could not infer a nearby 16-bit seed state."
        )
    elif (
        expected_prng_discerned_steps is not None
        and prng_discerned_steps != expected_prng_discerned_steps
    ):
        print(
            "Warning:"
            f" expected prng_discerned_steps_from_rng={expected_prng_discerned_steps}"
            f" but observed {prng_discerned_steps}."
        )

    print(
        "Fixed replay matched:"
        f" delay={expected_delay}"
        f" button={observed_button}"
        f" seed_frame={seed_frame}"
        f" seed=0x{seed_value:04X}"
        f" prng_discerned_seed={'none' if prng_discerned_seed is None else f'0x{prng_discerned_seed:04X}'}"
        f" saved={done_path}"
    )
    maybe_run_post_replay_input_tape(core, helper)
    helper.notify_success_in_qt(
        core,
        qt_mode=qt_mode,
        target_seed=expected_target_seed,
        observed_seed=seed_value,
        delay_frames=expected_delay,
        button_name=observed_button,
        seed_frame=seed_frame,
        done_path=done_path,
    )
    return 0


def main() -> int:
    """Run the fixed first-half replay once with metadata-selected save data."""

    return run_replay()


if __name__ == "__main__":
    exit_code = main()
    if not _firsthalf()._qt_mode_enabled():
        raise SystemExit(exit_code)
