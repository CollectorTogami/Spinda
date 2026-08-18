from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any


DEFAULT_CUSTOM_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_UPSTREAM_ROOT = Path(os.environ.get("MGBA_UPSTREAM_ROOT", Path(__file__).resolve().parents[2]))
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent / "out"

CORE_EMULATION_SUBTREES = (
    "src/gba",
    "src/arm",
    "include/mgba",
)

ACCURACY_RELEVANT_SUBTREES = (
    "src/gba",
    "src/arm",
    "include/mgba",
    "src/core",
    "src/platform/qt",
    "src/platform/python",
)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def to_json_ready(value: Any) -> Any:
    if is_dataclass(value):
        return to_json_ready(asdict(value))
    if isinstance(value, dict):
        return {str(key): to_json_ready(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [to_json_ready(item) for item in value]
    if isinstance(value, Path):
        return str(value)
    return value


def write_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(
        json.dumps(to_json_ready(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def rel_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()

