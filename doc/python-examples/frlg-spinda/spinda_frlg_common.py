"""Shared FR/LG helpers for the long-form Spinda roadmap scripts.

These helpers are intentionally practical rather than generic. The goal is to
support the two roadmap phases we understand well enough to start building now:

- Phase 1: create one `.sav` lane per lower 16-bit PID half
- Phase 2 scaffolding: keep consistent lane manifests, work-state paths, and
  block paths so later scripts can resume cleanly

For the FR/LG daycare-man segment, frame counts are not a trustworthy
correctness signal by themselves because NPC activity can advance the PRNG while
the player is walking into position. Route steps therefore support explicit
PRNG-state checkpoints.

Two conservative timing anchors are still useful for planning:

- about 375 frames from seed generation to the first half of the egg PID
- about 700 frames from seed generation to receiving the egg itself

Those numbers are intentionally conservative to absorb Four Island NPC noise.
They are planning baselines, not correctness checks. Real routes should still
validate against PRNG state where the area is noisy.
"""

from __future__ import annotations

import hashlib
import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from mgba._pylib import ffi, lib
import mgba.core
import mgba.log
import mgba.vfs
from mgba.gba import GBA

try:
    import mgba.qt as mgba_qt
except ImportError:  # pragma: no cover - host-only build fallback
    mgba_qt = None


GTRAINER_ID_ADDR = 0x02020000
GRNG_VALUE_ADDR = 0x03005000
GSAVEBLOCK1_PTR_ADDR = 0x03005008
GPLAYER_PARTY_COUNT_ADDR = 0x02024029
GPLAYER_PARTY_ADDR = 0x02024284
PARTY_SLOT_SIZE = 100
BOX_SLOT_SIZE = 80
DAYCARE_OFFSET = 0x2F80
DAYCARE_BLOCK_SIZE = 0x120
DAYCARE_OFFSPRING_PERSONALITY_OFFSET = 0x118
DAYCARE_STEP_COUNTER_OFFSET = 0x11A
SAVE_STATE_FLAGS = 0
LANE_BLOCK_RECORDS = 0x10000
LANE_BLOCK_RECORD_SIZE = 80
SHA1_CHUNK_SIZE = 1024 * 1024

# Conservative planning anchors for the current FR/LG roadmap scaffold. These
# are not treated as exact guarantees because Four Island NPC activity can add
# small timing noise; route validation should still prefer PRNG checkpoints.
SEED_TO_FIRST_HALF_CONSERVATIVE_FRAMES = 375
SEED_TO_EGG_CONSERVATIVE_FRAMES = 700
GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073
GBA_LCRNG_MULTIPLIER_INVERSE = 0xEEB9EB65

_KEY_ATTRIBUTE_BY_NAME = {
    "A": "KEY_A",
    "B": "KEY_B",
    "SELECT": "KEY_SELECT",
    "START": "KEY_START",
    "UP": "KEY_UP",
    "DOWN": "KEY_DOWN",
    "LEFT": "KEY_LEFT",
    "RIGHT": "KEY_RIGHT",
    "L": "KEY_L",
    "R": "KEY_R",
}


def format_u16(value: int | None) -> str | None:
    """Render one optional 16-bit value in the usual hex form."""

    if value is None:
        return None
    return f"0x{value & 0xFFFF:04X}"


def format_u32(value: int | None) -> str | None:
    """Render one optional 32-bit value in the usual hex form."""

    if value is None:
        return None
    return f"0x{value & 0xFFFFFFFF:08X}"


def compose_pid(lower_half: int, upper_half: int) -> int:
    """Combine two 16-bit PID halves into one 32-bit personality value."""

    return ((_parse_int(upper_half, bits=16) << 16) | _parse_int(lower_half, bits=16)) & 0xFFFFFFFF


def _parse_int(value: Any, *, bits: int | None = None) -> int:
    """Parse either an integer or a `0x`-prefixed string."""

    if isinstance(value, int):
        result = value
    elif isinstance(value, str):
        result = int(value, 0)
    else:
        raise ValueError(f"Unsupported integer value: {value!r}")

    if bits is not None and not 0 <= result < (1 << bits):
        raise ValueError(f"Value {value!r} does not fit in {bits} bits.")
    return result


def _resolve_path(raw_path: str | None, base_dir: Path) -> Path | None:
    """Resolve one optional path relative to the recipe or manifest file."""

    if raw_path in (None, ""):
        return None
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        path = (base_dir / path).resolve()
    return path


def sha1_bytes(data: bytes) -> str:
    """Return the SHA-1 checksum for one in-memory payload."""

    return hashlib.sha1(data).hexdigest()


def sha1_file(path: Path) -> str:
    """Return the SHA-1 checksum for one on-disk file.

    The audit path can touch a lot of lane artifacts, so this is streamed
    instead of reading the entire file into memory at once.
    """

    digest = hashlib.sha1()
    with path.open("rb") as handle:
        while chunk := handle.read(SHA1_CHUNK_SIZE):
            digest.update(chunk)
    return digest.hexdigest()


def write_bytes_atomic(path: Path, data: bytes) -> None:
    """Atomically replace one file with new bytes."""

    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_name(f"{path.name}.tmp")
    temp_path.write_bytes(data)
    temp_path.replace(path)


def write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    """Atomically replace one JSON file."""

    text = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    write_bytes_atomic(path, text.encode("utf-8"))


def read_json(path: Path) -> dict[str, Any]:
    """Read a UTF-8 JSON file into a plain dictionary."""

    return json.loads(path.read_text(encoding="utf-8"))


def qt_mode_enabled() -> bool:
    """Return whether the visible Qt scripting bridge is active."""

    if not mgba_qt:
        return False
    try:
        return bool(mgba_qt.is_available())
    except Exception:
        return False


@dataclass(frozen=True)
class FirstHalfCsvRow:
    """One streamed row from the first-half seed-finder CSV export.

    The seed finder writes a long table around each target lower PID half. For
    example, `t-18` is the PRNG state 18 LCRNG advances before the target event
    row `t-0`. Keeping this as a typed object makes route calibration tests less
    error-prone than indexing raw CSV strings.
    """

    initial_seed_16bit: int
    compatibility_percent: int
    target_half_16bit: int
    sweep_index: int
    frame_from_initial_seed: int
    t_minus: str
    rng_seed: int

    @classmethod
    def from_csv_row(cls, row: Mapping[str, str]) -> "FirstHalfCsvRow":
        """Parse one first-half CSV row into validated integers."""

        return cls(
            initial_seed_16bit=_parse_int(row["initial_seed_16bit"], bits=16),
            compatibility_percent=int(row["compatibility_percent"]),
            target_half_16bit=_parse_int(row["target_half_16bit"], bits=16),
            sweep_index=int(row["sweep_index"]),
            frame_from_initial_seed=int(row["frame_from_initial_seed"]),
            t_minus=str(row["t_minus"]),
            rng_seed=_parse_int(row["rng_seed"], bits=32),
        )


def lcrng_next_state(state: int) -> int:
    """Return one forward GBA LCRNG step."""

    return (GBA_LCRNG_MULTIPLIER * _parse_int(state, bits=32) + GBA_LCRNG_INCREMENT) & 0xFFFFFFFF


def lcrng_previous_state(state: int) -> int:
    """Return one backward GBA LCRNG step."""

    return (GBA_LCRNG_MULTIPLIER_INVERSE * ((_parse_int(state, bits=32) - GBA_LCRNG_INCREMENT) & 0xFFFFFFFF)) & 0xFFFFFFFF


def lcrng_advance(state: int, frames: int) -> int:
    """Advance or rewind a GBA LCRNG state by a small frame delta.

    The roadmap CSVs use adjacent LCRNG states as their T-minus history. This
    helper intentionally stays simple and readable because the route scripts use
    it for calibration-sized deltas such as the current 18-frame buffer.
    """

    result = _parse_int(state, bits=32)
    if frames >= 0:
        for _ in range(frames):
            result = lcrng_next_state(result)
    else:
        for _ in range(-frames):
            result = lcrng_previous_state(result)
    return result


def daycare_lower_half_from_random_half(random_half: int) -> int:
    """Convert FR/LG's raw egg-half roll into the stored daycare lower half.

    FireRed/LeafGreen does not store the raw `Random()` return value directly.
    The pending egg personality uses `((Random()) % 0xFFFE) + 1`, so the live
    daycare lower half is usually one greater than the raw 16-bit roll and wraps
    for the top two raw values.
    """

    return (((_parse_int(random_half, bits=16)) % 0xFFFE) + 1) & 0xFFFF


def load_first_half_csv_row(
    csv_path: Path,
    target_half: int,
    t_minus: str,
    *,
    compatibility_percent: int | None = None,
    sweep_index: int | None = None,
) -> FirstHalfCsvRow:
    """Stream `firsthalf.csv` until the requested target/T-minus row is found.

    `firsthalf.csv` is large enough that roadmap scripts should not load it as a
    spreadsheet. Streaming keeps the calibration path cheap and mirrors how the
    later lane generator should consume these tables.
    """

    target_half = _parse_int(target_half, bits=16)
    normalized_t_minus = str(t_minus).strip().lower()
    with csv_path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        required_columns = {
            "initial_seed_16bit",
            "compatibility_percent",
            "target_half_16bit",
            "sweep_index",
            "frame_from_initial_seed",
            "t_minus",
            "rng_seed",
        }
        missing_columns = required_columns.difference(reader.fieldnames or ())
        if missing_columns:
            raise ValueError(f"first-half CSV is missing required columns: {sorted(missing_columns)}")

        for raw_row in reader:
            row = FirstHalfCsvRow.from_csv_row(raw_row)
            if row.target_half_16bit != target_half:
                continue
            if row.t_minus.strip().lower() != normalized_t_minus:
                continue
            if compatibility_percent is not None and row.compatibility_percent != compatibility_percent:
                continue
            if sweep_index is not None and row.sweep_index != sweep_index:
                continue
            return row

    raise ValueError(
        "Could not find requested first-half CSV row: "
        f"target={format_u16(target_half)} t_minus={t_minus!r} "
        f"compatibility={compatibility_percent!r} sweep={sweep_index!r}"
    )


def first_half_csv_frame_delta(start: FirstHalfCsvRow, end: FirstHalfCsvRow) -> int:
    """Return the rendered-frame delta between two compatible CSV rows."""

    if (
        start.initial_seed_16bit,
        start.compatibility_percent,
        start.target_half_16bit,
        start.sweep_index,
    ) != (
        end.initial_seed_16bit,
        end.compatibility_percent,
        end.target_half_16bit,
        end.sweep_index,
    ):
        raise ValueError("First-half CSV rows are from different target lanes.")
    return end.frame_from_initial_seed - start.frame_from_initial_seed


@dataclass(frozen=True)
class RouteStep:
    """One scripted input segment.

    Exactly one of `frames` or `wait_for_rng` must be provided.

    `frames` is useful for deterministic menu taps and known walking bursts.
    `wait_for_rng` is the safer mode for the noisy daycare-man segment because
    it lets the script stop on the PRNG state that matters, not only on a raw
    frame count.
    """

    label: str
    keys: tuple[str, ...] = ()
    frames: int | None = None
    wait_for_rng: int | None = None
    max_frames: int | None = None
    expected_rng_after: int | None = None

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> "RouteStep":
        """Build one route step from JSON-compatible data."""

        keys = tuple(str(key).upper() for key in data.get("keys", ()))
        frames = data.get("frames")
        wait_for_rng = data.get("wait_for_rng")
        max_frames = data.get("max_frames")
        expected_rng_after = data.get("expected_rng_after")

        if (frames is None) == (wait_for_rng is None):
            raise ValueError(
                "Each route step must provide exactly one of `frames` or `wait_for_rng`."
            )
        if frames is not None and int(frames) < 0:
            raise ValueError("`frames` must be non-negative.")
        if wait_for_rng is not None:
            wait_for_rng = _parse_int(wait_for_rng, bits=32)
            if max_frames is None or int(max_frames) < 0:
                raise ValueError("`wait_for_rng` steps require a non-negative `max_frames`.")
            max_frames = int(max_frames)

        if expected_rng_after is not None:
            expected_rng_after = _parse_int(expected_rng_after, bits=32)

        return cls(
            label=str(data["label"]),
            keys=keys,
            frames=None if frames is None else int(frames),
            wait_for_rng=wait_for_rng,
            max_frames=max_frames,
            expected_rng_after=expected_rng_after,
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly view of the step."""

        payload: dict[str, Any] = {
            "label": self.label,
            "keys": list(self.keys),
        }
        if self.frames is not None:
            payload["frames"] = self.frames
        if self.wait_for_rng is not None:
            payload["wait_for_rng"] = format_u32(self.wait_for_rng)
            payload["max_frames"] = self.max_frames
        if self.expected_rng_after is not None:
            payload["expected_rng_after"] = format_u32(self.expected_rng_after)
        return payload


@dataclass(frozen=True)
class RouteStepResult:
    """What actually happened when the script executed one route step."""

    label: str
    keys: tuple[str, ...]
    mode: str
    frames_used: int
    frame_counter_after: int
    observed_rng_after: int
    target_rng: int | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return one JSON-friendly route log entry."""

        payload = {
            "label": self.label,
            "keys": list(self.keys),
            "mode": self.mode,
            "frames_used": self.frames_used,
            "frame_counter_after": self.frame_counter_after,
            "observed_rng_after": format_u32(self.observed_rng_after),
        }
        if self.target_rng is not None:
            payload["target_rng"] = format_u32(self.target_rng)
        return payload


@dataclass(frozen=True)
class FirstHalfRecipe:
    """Recipe for exporting one lower-half lane save."""

    source_path: Path
    rom_path: Path
    base_state_path: Path
    workspace_root: Path
    target_lower_half: int
    base_save_path: Path | None = None
    pre_generation_route: tuple[RouteStep, ...] = ()
    post_generation_route: tuple[RouteStep, ...] = ()
    save_sequence: tuple[RouteStep, ...] = ()
    create_lane_work_state: bool = True
    notes: str = ""

    @classmethod
    def load(cls, path: Path) -> "FirstHalfRecipe":
        """Load one first-half recipe JSON file."""

        source_path = path.expanduser().resolve()
        data = read_json(source_path)
        base_dir = source_path.parent

        return cls(
            source_path=source_path,
            rom_path=_resolve_path(data["rom_path"], base_dir) or Path(),
            base_state_path=_resolve_path(data["base_state_path"], base_dir) or Path(),
            workspace_root=_resolve_path(data.get("workspace_root", "."), base_dir) or Path(),
            target_lower_half=_parse_int(data["target_lower_half"], bits=16),
            base_save_path=_resolve_path(data.get("base_save_path"), base_dir),
            pre_generation_route=tuple(
                RouteStep.from_dict(step) for step in data.get("pre_generation_route", ())
            ),
            post_generation_route=tuple(
                RouteStep.from_dict(step) for step in data.get("post_generation_route", ())
            ),
            save_sequence=tuple(
                RouteStep.from_dict(step) for step in data.get("save_sequence", ())
            ),
            create_lane_work_state=bool(data.get("create_lane_work_state", True)),
            notes=str(data.get("notes", "")),
        )


@dataclass(frozen=True)
class LanePaths:
    """Canonical on-disk locations for one lower-half lane."""

    workspace_root: Path
    lane_id: int
    saves_dir: Path
    manifests_dir: Path
    states_dir: Path
    blocks_dir: Path
    archive_save_path: Path
    manifest_path: Path
    work_state_path: Path
    block_path: Path

    @property
    def lane_hex(self) -> str:
        """Return the lane id in the archive-friendly `0x####` form."""

        return format_u16(self.lane_id) or "0x0000"


@dataclass
class LaneWorkspaceManifest:
    """Persistent metadata for one lower-half lane."""

    lane_id: int
    manifest_path: Path
    archive_save_path: Path
    work_state_path: Path
    block_path: Path
    rom_path: Path | None = None
    recipe_path: Path | None = None
    base_save_path: Path | None = None
    base_state_path: Path | None = None
    archive_save_sha1: str | None = None
    work_state_sha1: str | None = None
    observed_lower_half: int | None = None
    observed_rng_before_walk: int | None = None
    observed_rng_after_walk: int | None = None
    observed_rng_after_save: int | None = None
    next_upper_half: int = 0
    completed_upper_halves: int = 0
    complete: bool = False
    pre_generation_results: list[RouteStepResult] = field(default_factory=list)
    post_generation_results: list[RouteStepResult] = field(default_factory=list)
    save_sequence_results: list[RouteStepResult] = field(default_factory=list)
    notes: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly lane manifest."""

        return {
            "schema_version": self.schema_version,
            "lane_id": format_u16(self.lane_id),
            "archive_save_path": str(self.archive_save_path),
            "archive_save_sha1": self.archive_save_sha1,
            "work_state_path": str(self.work_state_path),
            "work_state_sha1": self.work_state_sha1,
            "block_path": str(self.block_path),
            "block_record_count": LANE_BLOCK_RECORDS,
            "block_record_size": LANE_BLOCK_RECORD_SIZE,
            "rom_path": None if self.rom_path is None else str(self.rom_path),
            "recipe_path": None if self.recipe_path is None else str(self.recipe_path),
            "base_save_path": None if self.base_save_path is None else str(self.base_save_path),
            "base_state_path": None if self.base_state_path is None else str(self.base_state_path),
            "observed_lower_half": format_u16(self.observed_lower_half),
            "observed_rng_before_walk": format_u32(self.observed_rng_before_walk),
            "observed_rng_after_walk": format_u32(self.observed_rng_after_walk),
            "observed_rng_after_save": format_u32(self.observed_rng_after_save),
            "next_upper_half": format_u16(self.next_upper_half),
            "completed_upper_halves": self.completed_upper_halves,
            "complete": self.complete,
            "pre_generation_results": [result.to_dict() for result in self.pre_generation_results],
            "post_generation_results": [result.to_dict() for result in self.post_generation_results],
            "save_sequence_results": [result.to_dict() for result in self.save_sequence_results],
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], manifest_path: Path) -> "LaneWorkspaceManifest":
        """Rebuild one lane manifest from JSON data."""

        return cls(
            lane_id=_parse_int(data["lane_id"], bits=16),
            manifest_path=manifest_path,
            archive_save_path=Path(str(data["archive_save_path"])),
            work_state_path=Path(str(data["work_state_path"])),
            block_path=Path(str(data["block_path"])),
            rom_path=_resolve_path(data.get("rom_path"), manifest_path.parent),
            recipe_path=_resolve_path(data.get("recipe_path"), manifest_path.parent),
            base_save_path=_resolve_path(data.get("base_save_path"), manifest_path.parent),
            base_state_path=_resolve_path(data.get("base_state_path"), manifest_path.parent),
            archive_save_sha1=data.get("archive_save_sha1"),
            work_state_sha1=data.get("work_state_sha1"),
            observed_lower_half=None
            if data.get("observed_lower_half") is None
            else _parse_int(data["observed_lower_half"], bits=16),
            observed_rng_before_walk=None
            if data.get("observed_rng_before_walk") is None
            else _parse_int(data["observed_rng_before_walk"], bits=32),
            observed_rng_after_walk=None
            if data.get("observed_rng_after_walk") is None
            else _parse_int(data["observed_rng_after_walk"], bits=32),
            observed_rng_after_save=None
            if data.get("observed_rng_after_save") is None
            else _parse_int(data["observed_rng_after_save"], bits=32),
            next_upper_half=_parse_int(data.get("next_upper_half", 0), bits=16),
            completed_upper_halves=int(data.get("completed_upper_halves", 0)),
            complete=bool(data.get("complete", False)),
            pre_generation_results=[
                RouteStepResult(
                    label=str(entry["label"]),
                    keys=tuple(entry.get("keys", ())),
                    mode=str(entry["mode"]),
                    frames_used=int(entry["frames_used"]),
                    frame_counter_after=int(entry["frame_counter_after"]),
                    observed_rng_after=_parse_int(entry["observed_rng_after"], bits=32),
                    target_rng=None
                    if entry.get("target_rng") is None
                    else _parse_int(entry["target_rng"], bits=32),
                )
                for entry in data.get("pre_generation_results", ())
            ],
            post_generation_results=[
                RouteStepResult(
                    label=str(entry["label"]),
                    keys=tuple(entry.get("keys", ())),
                    mode=str(entry["mode"]),
                    frames_used=int(entry["frames_used"]),
                    frame_counter_after=int(entry["frame_counter_after"]),
                    observed_rng_after=_parse_int(entry["observed_rng_after"], bits=32),
                    target_rng=None
                    if entry.get("target_rng") is None
                    else _parse_int(entry["target_rng"], bits=32),
                )
                for entry in data.get("post_generation_results", ())
            ],
            save_sequence_results=[
                RouteStepResult(
                    label=str(entry["label"]),
                    keys=tuple(entry.get("keys", ())),
                    mode=str(entry["mode"]),
                    frames_used=int(entry["frames_used"]),
                    frame_counter_after=int(entry["frame_counter_after"]),
                    observed_rng_after=_parse_int(entry["observed_rng_after"], bits=32),
                    target_rng=None
                    if entry.get("target_rng") is None
                    else _parse_int(entry["target_rng"], bits=32),
                )
                for entry in data.get("save_sequence_results", ())
            ],
            notes=str(data.get("notes", "")),
            schema_version=int(data.get("schema_version", 1)),
        )


def lane_paths(workspace_root: Path, lane_id: int) -> LanePaths:
    """Return the canonical path layout for one lower-half lane."""

    workspace_root = workspace_root.expanduser().resolve()
    lane_hex = format_u16(lane_id) or "0x0000"
    saves_dir = workspace_root / "saves"
    manifests_dir = workspace_root / "manifests"
    states_dir = workspace_root / "states"
    blocks_dir = workspace_root / "blocks"
    return LanePaths(
        workspace_root=workspace_root,
        lane_id=lane_id,
        saves_dir=saves_dir,
        manifests_dir=manifests_dir,
        states_dir=states_dir,
        blocks_dir=blocks_dir,
        archive_save_path=saves_dir / f"{lane_hex}.sav",
        manifest_path=manifests_dir / f"{lane_hex}.json",
        work_state_path=states_dir / f"{lane_hex}.state",
        block_path=blocks_dir / f"{lane_hex}.bin",
    )


def ensure_workspace_dirs(workspace_root: Path) -> None:
    """Create the canonical save/manifest/state/block directories."""

    for directory in (
        workspace_root / "saves",
        workspace_root / "manifests",
        workspace_root / "states",
        workspace_root / "blocks",
    ):
        directory.mkdir(parents=True, exist_ok=True)


def write_lane_manifest(manifest: LaneWorkspaceManifest) -> None:
    """Persist one lane manifest atomically."""

    write_json_atomic(manifest.manifest_path, manifest.to_dict())


def load_lane_manifest(manifest_path: Path) -> LaneWorkspaceManifest:
    """Load one previously written lane manifest."""

    manifest_path = manifest_path.expanduser().resolve()
    return LaneWorkspaceManifest.from_dict(read_json(manifest_path), manifest_path)


def load_gba_core(rom_path: Path, base_save_path: Path | None = None) -> GBA:
    """Load a GBA ROM and optionally inject one temporary save file.

    Temporary save loading is used here on purpose. The canonical input save for
    a lane is an input artifact; the exported `0x####.sav` is the thing we want
    to preserve permanently.
    """

    if qt_mode_enabled():
        # Runtime Qt scripting needs to reload the target ROM explicitly so the
        # lane recipes do not depend on whatever game happened to be open when
        # the user loaded the script.
        if not mgba_qt.load_rom(rom_path):
            raise SystemExit(f"Could not load ROM into the visible Qt core: {rom_path}")
        core = mgba_qt.current_core()
        if base_save_path is not None:
            core.load_temporary_save_file(base_save_path)
        return core

    mgba.log.silence()
    core = mgba.core.load_path(str(rom_path))
    if not core:
        raise SystemExit(f"Could not load ROM: {rom_path}")
    if not isinstance(core, GBA):
        raise SystemExit(f"This roadmap helper expects a GBA ROM, not {core.game_code!r}.")

    if base_save_path is not None:
        vf = mgba.vfs.open_path(str(base_save_path), "r")
        if not vf:
            raise SystemExit(f"Could not open save file: {base_save_path}")
        try:
            if not core.load_temporary_save(vf):
                raise SystemExit(f"Could not load temporary save file: {base_save_path}")
        finally:
            vf.close()

    return core


def save_state_file(core: GBA, path: Path) -> None:
    """Write one file-backed mGBA savestate."""

    if hasattr(core, "save_state_file") and not hasattr(core, "_core"):
        core.save_state_file(path, SAVE_STATE_FLAGS)
        return

    vf = mgba.vfs.open_path(str(path), "w+")
    if not vf:
        raise SystemExit(f"Could not open savestate path for writing: {path}")
    try:
        if not lib.mCoreSaveStateNamed(core._core, vf.handle, SAVE_STATE_FLAGS):
            raise SystemExit(f"mCoreSaveStateNamed(...) failed for {path}")
    finally:
        vf.close()


def load_state_file(core: GBA, path: Path) -> None:
    """Load one file-backed mGBA savestate."""

    if hasattr(core, "load_state_file") and not hasattr(core, "_core"):
        core.load_state_file(path, SAVE_STATE_FLAGS)
        return

    vf = mgba.vfs.open_path(str(path), "r")
    if not vf:
        raise SystemExit(f"Could not open savestate path for reading: {path}")
    try:
        if not lib.mCoreLoadStateNamed(core._core, vf.handle, SAVE_STATE_FLAGS):
            raise SystemExit(f"mCoreLoadStateNamed(...) failed for {path}")
    finally:
        vf.close()


def clone_save_data(core: GBA) -> bytes:
    """Read the live SRAM/Flash contents as raw `.sav` bytes."""

    if hasattr(core, "export_save_file") and not hasattr(core, "_core"):
        raise RuntimeError(
            "Qt runtime scripts should use export_save_file(...) instead of "
            "clone_save_data(...), because the visible-core bridge writes the "
            "raw save directly to disk."
        )

    # This clones the save data that exists after the scripted in-game save.
    # That matters for the roadmap: the archived `0x####.sav` should reflect
    # the lane we just created, not the base save we booted from.
    buffer_ptr = ffi.new("void**")
    size = core._core.savedataClone(core._core, buffer_ptr)
    if size <= 0 or buffer_ptr[0] == ffi.NULL:
        raise RuntimeError("The core did not return any save data to clone.")
    try:
        return bytes(ffi.buffer(buffer_ptr[0], size))
    finally:
        lib.free(buffer_ptr[0])


def export_save_file(core: GBA, path: Path) -> None:
    """Write the live raw save data to disk in either host or Qt mode."""

    if hasattr(core, "export_save_file") and not hasattr(core, "_core"):
        core.export_save_file(path)
        return
    write_bytes_atomic(path, clone_save_data(core))


def read_rng_state(core: GBA) -> int:
    """Read FR/LG's current 32-bit PRNG state."""

    return core.memory.u32[GRNG_VALUE_ADDR]


def read_initial_seed_copy(core: GBA) -> int:
    """Read the stable 16-bit startup seed copy in `gTrainerId`."""

    return core.memory.u16[GTRAINER_ID_ADDR]


def read_save_block1_ptr(core: GBA) -> int:
    """Read the current SaveBlock1 pointer from IWRAM."""

    return core.memory.u32[GSAVEBLOCK1_PTR_ADDR]


def daycare_base_address(core: GBA) -> int:
    """Return the live absolute address of the FR/LG daycare struct."""

    save_block1 = read_save_block1_ptr(core)
    if not save_block1:
        raise RuntimeError("SaveBlock1 pointer is zero; the game is not ready for daycare reads.")
    return save_block1 + DAYCARE_OFFSET


def read_daycare_lower_half(core: GBA) -> int:
    """Read the FR/LG lower PID half stored in daycare RAM."""

    return core.memory.u16[daycare_base_address(core) + DAYCARE_OFFSPRING_PERSONALITY_OFFSET]


def read_daycare_step_counter(core: GBA) -> int:
    """Read FR/LG's daycare egg step counter byte."""

    return core.memory.u8[daycare_base_address(core) + DAYCARE_STEP_COUNTER_OFFSET]


def read_party_slot_bytes(core: GBA, slot_number: int = 2) -> bytes:
    """Read one raw 100-byte FR/LG party slot.

    The roadmap stores boxed 80-byte records later, but reading the full 100
    bytes first makes validation easier during development.
    """

    if not 1 <= slot_number <= 6:
        raise ValueError("Party slot numbers are 1-based and must be in the range 1..6.")
    address = GPLAYER_PARTY_ADDR + (slot_number - 1) * PARTY_SLOT_SIZE
    return bytes(core.memory[address : address + PARTY_SLOT_SIZE])


def read_box_bytes_from_party_slot(core: GBA, slot_number: int = 2) -> bytes:
    """Read one party slot and trim it down to an authentic boxed record."""

    return read_party_slot_bytes(core, slot_number)[:BOX_SLOT_SIZE]


def personality_value_from_box_record(record: bytes) -> int:
    """Read the 32-bit personality value from one boxed record."""

    if len(record) < 4:
        raise ValueError("A boxed Pokemon record must be at least 4 bytes long.")
    return int.from_bytes(record[:4], byteorder="little", signed=False)


def read_party_slot_pid(core: GBA, slot_number: int = 2) -> int:
    """Read the current personality value from one party slot."""

    return personality_value_from_box_record(read_box_bytes_from_party_slot(core, slot_number))


def key_names_to_values(core: GBA, names: Sequence[str]) -> list[int]:
    """Map readable key names like `A` or `START` to mGBA key ids."""

    values: list[int] = []
    for name in names:
        normalized = str(name).upper()
        attribute = _KEY_ATTRIBUTE_BY_NAME.get(normalized)
        if attribute is None:
            raise ValueError(f"Unsupported GBA key name: {name!r}")
        values.append(getattr(core, attribute))
    return values


def set_named_keys(core: GBA, names: Sequence[str]) -> None:
    """Apply one readable key combination to the core."""

    if names:
        core.set_keys(*key_names_to_values(core, names))
    else:
        core.set_keys(raw=0)


def run_frames_fast(core: GBA, frames: int) -> None:
    """Advance a fixed frame count with the fastest supported core API.

    Runtime Qt scripts in this workspace can batch frame stepping natively.
    Host-side cores still use the portable one-frame loop.
    """

    if frames <= 0:
        return
    run_frames = getattr(core, "run_frames", None)
    if callable(run_frames):
        run_frames(frames)
        return
    for _ in range(frames):
        core.run_frame()


def wait_for_rng_state(
    core: GBA,
    target_rng: int,
    *,
    max_frames: int,
    keys: Sequence[str] = (),
) -> int:
    """Advance until the live PRNG reaches the requested value.

    This is the important helper for the noisy daycare-man segment. The route is
    allowed to spend up to `max_frames`, but the stop condition is the PRNG
    state, not the raw frame count.
    """

    set_named_keys(core, keys)
    for frames_used in range(max_frames + 1):
        # Check before advancing so a recipe can intentionally stop on the
        # current PRNG value when the save/savestate is already aligned.
        if read_rng_state(core) == target_rng:
            return frames_used
        core.run_frame()
    raise RuntimeError(
        "Timed out before the requested PRNG state appeared: "
        f"target={format_u32(target_rng)} max_frames={max_frames}"
    )


def run_route(core: GBA, route: Sequence[RouteStep]) -> list[RouteStepResult]:
    """Execute a route and return per-step telemetry."""

    results: list[RouteStepResult] = []
    for step in route:
        if step.frames is not None:
            set_named_keys(core, step.keys)
            # Fixed waits are the cheapest place to batch frame stepping. The
            # noisy daycare-man segments still use wait_for_rng_state() above
            # because those checkpoints must stop on PRNG state, not on time.
            run_frames_fast(core, step.frames)
            frames_used = step.frames
            mode = "frames"
            target_rng = None
        else:
            target_rng = step.wait_for_rng
            if target_rng is None or step.max_frames is None:
                raise RuntimeError(f"Invalid wait-for-rng route step: {step.label}")
            frames_used = wait_for_rng_state(
                core,
                target_rng,
                max_frames=step.max_frames,
                keys=step.keys,
            )
            mode = "wait_for_rng"

        # Clear keys between steps so each segment is explicit in the recipe.
        # For RNG work, hidden carry-over inputs are a fast way to make a route
        # look reproducible on paper while drifting in practice.
        set_named_keys(core, ())
        observed_rng_after = read_rng_state(core)
        if step.expected_rng_after is not None and observed_rng_after != step.expected_rng_after:
            raise RuntimeError(
                "Route step failed its PRNG checkpoint: "
                f"{step.label!r} expected={format_u32(step.expected_rng_after)} "
                f"observed={format_u32(observed_rng_after)}"
            )

        results.append(
            RouteStepResult(
                label=step.label,
                keys=step.keys,
                mode=mode,
                frames_used=frames_used,
                frame_counter_after=core.frame_counter,
                observed_rng_after=observed_rng_after,
                target_rng=target_rng,
            )
        )
    return results
