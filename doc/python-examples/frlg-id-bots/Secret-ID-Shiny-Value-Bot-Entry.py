#!/usr/bin/env python3
"""mGBA script entry wrapper for Secret-ID-Shiny-Value-Bot.

This wrapper keeps the command invocation simple because this mGBA build does not
forward script-specific CLI flags after --script. The launcher writes desired
bot args into SECRET_ID_BOT_ARGS_JSON and this wrapper passes them to main().
"""

from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

from mgba import qt as mgba_qt


def _load_initial_state() -> None:
    state_path_raw = os.environ.get("SECRET_ID_INITIAL_STATE_PATH", "").strip()
    if not state_path_raw:
        return
    state_path = Path(state_path_raw)
    if not state_path.exists():
        raise FileNotFoundError(f"Cannot find initial state file: {state_path}")

    core = mgba_qt.current_core()
    if core is None:
        raise RuntimeError("mGBA core is not available yet; failed to apply initial state")
    loaded = core.load_state_file(str(state_path))
    if loaded is False:
        raise RuntimeError(f"Failed to load initial state file: {state_path}")


def main() -> int:
    _load_initial_state()

    script_dir = Path(__file__).resolve().parent
    bot_script = script_dir / "Secret-ID-Shiny-Value-Bot.py"
    if not bot_script.exists():
        raise FileNotFoundError(f"Cannot find SID bot script: {bot_script}")

    spec = importlib.util.spec_from_file_location("secret_id_shiny_value_bot", bot_script)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load Secret-ID-Shiny-Value-Bot module from file")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)

    raw_args = os.environ.get("SECRET_ID_BOT_ARGS_JSON", "[]")
    args = json.loads(raw_args)
    if not isinstance(args, list):
        raise TypeError("SECRET_ID_BOT_ARGS_JSON must be a JSON list of strings")
    if not all(isinstance(item, str) for item in args):
        raise TypeError("SECRET_ID_BOT_ARGS_JSON must be a JSON list of strings")

    return int(module.main(args))


if __name__ == "__main__":
    main()
