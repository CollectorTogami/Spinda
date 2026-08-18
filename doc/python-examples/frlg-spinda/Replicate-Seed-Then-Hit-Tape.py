from __future__ import annotations

import importlib.util
import json
import os
import sys
import traceback
from pathlib import Path


SCRIPT_DIR = Path(__file__).parent
ROOT = SCRIPT_DIR.parents[2]
SEED_REPLICATOR = ROOT / "doc" / "python-examples" / "frlg-seed-bruteforce" / "Seed-Replicator.py"
INPUT_TAPE_HELPER = ROOT / "doc" / "python-examples" / "input_tape.py"
HIT_TAPE = Path(
    os.environ.get(
        "MGBA_SPINDA_HIT_TAPE",
        str(ROOT / "build-mingw64-python-qt" / "hit 1st half walk to daycare man.json"),
    )
)
STATUS_PATH = Path(
    os.environ.get(
        "MGBA_SPINDA_SEED_TAPE_STATUS_PATH",
        str(ROOT / "userdata" / "logs" / "replicate-seed-then-hit-tape-status.json"),
    )
)
AFTER_TAPE_STATE = Path(
    os.environ.get(
        "MGBA_SPINDA_AFTER_TAPE_STATE_PATH",
        str(ROOT / "userdata" / "savestates" / "after-seed-hit-tape.ss0"),
    )
)


def load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def write_status(**fields: object) -> None:
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "script": str(Path(__file__)),
        **fields,
    }
    temp = STATUS_PATH.with_name(f".{STATUS_PATH.name}.tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temp.replace(STATUS_PATH)


def core_probe(core) -> dict[str, object]:
    probe: dict[str, object] = {
        "frame_counter": getattr(core, "frame_counter", None),
        "platform": getattr(core, "platform", None),
    }
    try:
        probe["rng"] = f"0x{int(core.memory.u32[0x03005000]) & 0xFFFFFFFF:08X}"
    except Exception:
        pass
    try:
        probe["keyinput"] = f"0x{int(core.memory.u16[0x04000130]) & 0xFFFF:04X}"
    except Exception:
        pass
    return probe


def main() -> int:
    write_status(stage="starting", hit_tape=str(HIT_TAPE), after_tape_state=str(AFTER_TAPE_STATE))
    replicator = load_module("spinda_seed_replicator_for_hit_tape", SEED_REPLICATOR)
    input_tape = load_module("spinda_input_tape_for_hit_tape", INPUT_TAPE_HELPER)
    helper = replicator._firsthalf()

    # Normal replay success dialog blocks chained automation in visible Qt.
    helper.notify_success_in_qt = lambda *args, **kwargs: None

    write_status(stage="replicating_seed", hit_tape=str(HIT_TAPE), after_tape_state=str(AFTER_TAPE_STATE))
    replay_exit = replicator.run_replay()

    from mgba import qt as mgba_qt

    core = mgba_qt.current_core()
    before_tape_probe = core_probe(core)
    tape = input_tape.read_tape(HIT_TAPE)
    write_status(
        stage="running_hit_tape",
        replay_exit=int(replay_exit),
        hit_tape=str(HIT_TAPE),
        hit_tape_frames=int(tape.frame_count),
        before_tape_probe=before_tape_probe,
        after_tape_state=str(AFTER_TAPE_STATE),
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
    AFTER_TAPE_STATE.parent.mkdir(parents=True, exist_ok=True)
    helper.save_state_file(core, AFTER_TAPE_STATE)
    write_status(
        stage="finished",
        replay_exit=int(replay_exit),
        hit_tape=str(HIT_TAPE),
        hit_tape_frames=int(tape.frame_count),
        before_tape_probe=before_tape_probe,
        tape_start_probe=result.start_probe,
        tape_end_probe=result.end_probe,
        after_tape_probe=core_probe(core),
        after_tape_state=str(AFTER_TAPE_STATE),
    )
    return 0


try:
    main()
except BaseException as exc:
    write_status(
        stage="error",
        error_type=type(exc).__name__,
        error=str(exc),
        traceback="".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
        hit_tape=str(HIT_TAPE),
        after_tape_state=str(AFTER_TAPE_STATE),
    )
    try:
        from mgba import qt as mgba_qt

        if hasattr(mgba_qt, "show_warning"):
            mgba_qt.show_warning("Seed/Tape attempt failed", f"{type(exc).__name__}: {exc}")
    except Exception:
        pass
