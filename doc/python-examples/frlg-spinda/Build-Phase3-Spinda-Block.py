"""Build one compressed Phase 3 Spinda PK3 ZIP from a Phase 2 pickup state.

This is the first Phase 3 producer. It consumes one already-validated
`Phase2PickupStates/0x####.ss0` state, sweeps the precomputed
`secondhalf.csv` pickup targets in PRNG/frame order, extracts each resulting
party Pokemon, drops the final 20 party-only bytes, keeps the boxed 80-byte
PK3 records in RAM, and writes one PID-named `.pk3` ZIP archive atomically at
the end. The ZIP stream is also built in RAM before the final `.tmp` write and
rename.

The script intentionally requires the visible Qt mGBA bridge for live runs.
Phase 3 is meant to drive the same window the operator can inspect, not a
second hidden emulator instance.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence


ROOT = Path(__file__).absolute().parents[3]
EXAMPLES_DIR = ROOT / "doc" / "python-examples"
SPINDA_DIR = EXAMPLES_DIR / "frlg-spinda"
if str(EXAMPLES_DIR) not in sys.path:
    sys.path.insert(0, str(EXAMPLES_DIR))
if str(SPINDA_DIR) not in sys.path:
    sys.path.insert(0, str(SPINDA_DIR))

import input_tape
from spinda_frlg_common import (
    BOX_SLOT_SIZE,
    GPLAYER_PARTY_ADDR,
    GPLAYER_PARTY_COUNT_ADDR,
    GRNG_VALUE_ADDR,
    lcrng_next_state,
    PARTY_SLOT_SIZE,
    format_u16,
    format_u32,
    load_gba_core,
    load_state_file,
    personality_value_from_box_record,
    qt_mode_enabled,
    read_rng_state,
    sha1_bytes,
    sha1_file,
    write_json_atomic,
)


DEFAULT_ROM_PATH = ROOT / "doc" / "python-examples" / "frlg-seed-bruteforce" / "lg.gba"
DEFAULT_PHASE2_STATE_DIR = ROOT / "Phase2PickupStates"
DEFAULT_SECOND_HALF_CSV = ROOT / "build-mingw64-python-qt" / "secondhalf.csv"
DEFAULT_OUTPUT_DIR = ROOT / "Phase3SpindaBlocks"
DEFAULT_LANE_ID = 0x0001
DEFAULT_BASELINE_FRAME = 700
DEFAULT_BASELINE_RNG_DRIFT_FRAMES = 1
DEFAULT_PICKUP_INPUT_LEAD_FRAMES = 3
DEFAULT_PICKUP_HOLD_FRAMES = 1
DEFAULT_POST_PICKUP_FRAMES = 24
DEFAULT_PROGRESS_EVERY = 0
DEFAULT_EXPECTED_RECORDS = 0x10000
DEFAULT_EXPECTED_BASELINE_RNG = 0x2B0C94C1
DEFAULT_SCHEDULE_SOURCE = "runtime-rng"
DEFAULT_RUNTIME_SCHEDULE_MAX_STEPS = 4_000_000
DEFAULT_ENABLE_AUDIO_KILLSWITCH = True
DEFAULT_ENABLE_NO_RENDER_MODE = True
DEFAULT_ENABLE_FAST_FORWARD = True
DEFAULT_FAST_FORWARD_RATIO = -1.0
ZIP_DEFLATE_LEVEL = 1
FIXED_ZIP_DATETIME = (2026, 1, 1, 0, 0, 0)
SECONDHALF_REQUIRED_COLUMNS = (
    "initial_seed_16bit",
    "target_half_16bit",
    "sweep_index",
    "frame_from_initial_seed",
    "t_minus",
    "rng_seed",
)

TRUNCATED_RECORD_SIZE = BOX_SLOT_SIZE
TRUNCATED_TAIL_BYTES = PARTY_SLOT_SIZE - BOX_SLOT_SIZE
LANE_BITMAP_BYTES = DEFAULT_EXPECTED_RECORDS // 8
PHASE3_SCHEMA_VERSION = 1
PHASE3_CACHE_SCHEMA_VERSION = 1
A_BUTTON_MASK = input_tape.mask_from_buttons("A")


@dataclass(frozen=True)
class SecondHalfCsvContract:
    """Small summary proving the CSV is the expected Phase 3 authority."""

    path: Path
    initial_seed_16bit: int
    row_count: int
    t_zero_rows: int
    min_frame_from_initial_seed: int
    max_frame_from_initial_seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": str(self.path),
            "initial_seed_16bit": format_u16(self.initial_seed_16bit),
            "row_count": self.row_count,
            "t_zero_rows": self.t_zero_rows,
            "min_frame_from_initial_seed": self.min_frame_from_initial_seed,
            "max_frame_from_initial_seed": self.max_frame_from_initial_seed,
        }


@dataclass(frozen=True)
class SecondHalfTarget:
    """One `t-0` pickup target from `secondhalf.csv`."""

    upper_half: int
    sweep_index: int
    frame_from_initial_seed: int
    rng_seed: int
    input_frame_from_initial_seed: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "upper_half": format_u16(self.upper_half),
            "sweep_index": self.sweep_index,
            "frame_from_initial_seed": self.frame_from_initial_seed,
            "rng_seed": format_u32(self.rng_seed),
            "input_frame_from_initial_seed": self.input_frame_from_initial_seed,
        }


@dataclass(frozen=True)
class Phase3PickupTarget:
    """One live pickup target after scheduling against the loaded state."""

    upper_half: int
    csv_sweep_index: int
    csv_frame_from_initial_seed: int
    csv_rng_seed: int
    event_step_from_start: int
    input_delta_from_start: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "upper_half": format_u16(self.upper_half),
            "csv_sweep_index": self.csv_sweep_index,
            "csv_frame_from_initial_seed": self.csv_frame_from_initial_seed,
            "csv_rng_seed": format_u32(self.csv_rng_seed),
            "event_step_from_start": self.event_step_from_start,
            "input_delta_from_start": self.input_delta_from_start,
        }


@dataclass(frozen=True)
class Phase3Config:
    """Runtime options for one Phase 3 lane build."""

    lane_id: int = DEFAULT_LANE_ID
    rom_path: Path = DEFAULT_ROM_PATH
    phase2_state_path: Path = DEFAULT_PHASE2_STATE_DIR / "0x0001.ss0"
    secondhalf_csv: Path = DEFAULT_SECOND_HALF_CSV
    output_dir: Path = DEFAULT_OUTPUT_DIR
    baseline_frame: int = DEFAULT_BASELINE_FRAME
    baseline_rng_drift_frames: int = DEFAULT_BASELINE_RNG_DRIFT_FRAMES
    pickup_input_lead_frames: int = DEFAULT_PICKUP_INPUT_LEAD_FRAMES
    pickup_hold_frames: int = DEFAULT_PICKUP_HOLD_FRAMES
    post_pickup_frames: int = DEFAULT_POST_PICKUP_FRAMES
    expected_records: int = DEFAULT_EXPECTED_RECORDS
    expected_baseline_rng: int | None = DEFAULT_EXPECTED_BASELINE_RNG
    schedule_source: str = DEFAULT_SCHEDULE_SOURCE
    runtime_schedule_max_steps: int = DEFAULT_RUNTIME_SCHEDULE_MAX_STEPS
    enable_audio_killswitch: bool = DEFAULT_ENABLE_AUDIO_KILLSWITCH
    enable_no_render_mode: bool = DEFAULT_ENABLE_NO_RENDER_MODE
    enable_fast_forward: bool = DEFAULT_ENABLE_FAST_FORWARD
    fast_forward_ratio: float = DEFAULT_FAST_FORWARD_RATIO
    progress_every: int = DEFAULT_PROGRESS_EVERY
    limit: int | None = None
    overwrite: bool = False
    require_qt: bool = True
    use_batch: bool = True

    @property
    def lane_hex(self) -> str:
        return format_u16(self.lane_id) or "0x0000"

    @property
    def output_zip_path(self) -> Path:
        return self.output_dir / f"{self.lane_hex}.spinda80.zip"

    @property
    def status_path(self) -> Path:
        return self.output_dir / f"_{self.lane_hex}.phase3_status.json"

    @property
    def error_path(self) -> Path:
        return self.output_dir / f"_{self.lane_hex}.phase3_errors.jsonl"

    @property
    def cache_dir(self) -> Path:
        return self.output_dir / "_cache"

    @property
    def effective_start_frame_from_initial_seed(self) -> int:
        return self.baseline_frame + self.baseline_rng_drift_frames


@dataclass
class Phase3LaneBlock:
    """One in-memory `65536 * 80` boxed-PK3 lane block plus presence bitmap."""

    record_count: int = DEFAULT_EXPECTED_RECORDS
    record_size: int = TRUNCATED_RECORD_SIZE
    data: bytearray | None = None
    bitmap: bytearray | None = None
    written: int = 0

    def __post_init__(self) -> None:
        expected_data_size = self.record_count * self.record_size
        expected_bitmap_size = (self.record_count + 7) // 8
        if self.data is None:
            self.data = bytearray(expected_data_size)
        if self.bitmap is None:
            self.bitmap = bytearray(expected_bitmap_size)
        if len(self.data) != expected_data_size:
            raise ValueError(f"lane data must be {expected_data_size} bytes, not {len(self.data)}")
        if len(self.bitmap) != expected_bitmap_size:
            raise ValueError(f"lane bitmap must be {expected_bitmap_size} bytes, not {len(self.bitmap)}")

    def is_present(self, upper_half: int) -> bool:
        byte_index = upper_half >> 3
        bit_index = upper_half & 7
        return bool(self.bitmap[byte_index] & (1 << bit_index))

    def set_record(self, upper_half: int, truncated_record: bytes) -> None:
        if not 0 <= upper_half < self.record_count:
            raise ValueError(f"upper half out of range: {upper_half!r}")
        if len(truncated_record) != self.record_size:
            raise ValueError(
                f"boxed PK3 record must be {self.record_size} bytes, not {len(truncated_record)}"
            )
        if self.is_present(upper_half):
            raise ValueError(f"duplicate upper-half record: {format_u16(upper_half)}")

        offset = upper_half * self.record_size
        self.data[offset : offset + self.record_size] = truncated_record
        self.bitmap[upper_half >> 3] |= 1 << (upper_half & 7)
        self.written += 1

    def bitmap_bytes(self) -> bytes:
        return bytes(self.bitmap)

    def data_bytes(self) -> bytes:
        return bytes(self.data)

    def iter_present_records(self) -> Iterator[tuple[int, bytes]]:
        """Yield present upper-half records in ascending upper-half order."""

        for upper_half in range(self.record_count):
            if self.is_present(upper_half):
                yield upper_half, bytes(self.data[upper_half * self.record_size : (upper_half + 1) * self.record_size])

    def pk3_records_sha1(self) -> str:
        """Hash only the present PK3 entries in ZIP write order."""

        digest = hashlib.sha1()
        for _upper_half, record in self.iter_present_records():
            digest.update(record)
        return digest.hexdigest()


@dataclass(frozen=True)
class Phase3Result:
    """Summary of one completed or limited Phase 3 run."""

    lane_id: int
    generated_records: int
    output_zip_path: Path
    pk3_records_sha1: str
    presence_bitmap_sha1: str
    zip_sha1: str
    elapsed_seconds: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "lane_id": format_u16(self.lane_id),
            "generated_records": self.generated_records,
            "output_zip_path": str(self.output_zip_path),
            "archive_format": "explicit-pid-pk3",
            "pk3_entry_count": self.generated_records,
            "pk3_record_size": TRUNCATED_RECORD_SIZE,
            "pk3_records_sha1": self.pk3_records_sha1,
            "presence_bitmap_sha1": self.presence_bitmap_sha1,
            "zip_sha1": self.zip_sha1,
            "elapsed_seconds": self.elapsed_seconds,
        }


def _parse_int(value: Any, *, bits: int | None = None) -> int:
    if isinstance(value, int):
        result = value
    else:
        result = int(str(value), 0)
    if bits is not None and not 0 <= result < (1 << bits):
        raise ValueError(f"value does not fit in {bits} bits: {value!r}")
    return result


def _parse_decimal_or_int(value: str) -> int:
    raw = value.strip()
    return int(raw) if raw.isdecimal() else int(raw, 0)


def _env_path(name: str) -> Path | None:
    raw = os.environ.get(f"MGBA_SPINDA_PHASE3_{name}")
    return None if not raw else Path(raw)


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(f"MGBA_SPINDA_PHASE3_{name}")
    return default if raw in (None, "") else int(raw, 0)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(f"MGBA_SPINDA_PHASE3_{name}")
    if raw in (None, ""):
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_float(name: str, default: float) -> float:
    raw = os.environ.get(f"MGBA_SPINDA_PHASE3_{name}")
    return default if raw in (None, "") else float(raw)


def _env_optional_int(name: str, default: int | None) -> int | None:
    raw = os.environ.get(f"MGBA_SPINDA_PHASE3_{name}")
    if raw in (None, ""):
        return default
    if raw.strip().lower() in {"none", "off", "skip"}:
        return None
    return int(raw, 0)


def _append_jsonl(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(dict(payload), sort_keys=True) + "\n")


def _now_status_base(config: Phase3Config) -> dict[str, Any]:
    return {
        "schema_version": PHASE3_SCHEMA_VERSION,
        "lane_id": config.lane_hex,
        "phase2_state_path": str(config.phase2_state_path),
        "secondhalf_csv": str(config.secondhalf_csv),
        "output_zip_path": str(config.output_zip_path),
        "record_size": TRUNCATED_RECORD_SIZE,
        "source_box_record_size": BOX_SLOT_SIZE,
        "truncated_tail_bytes": TRUNCATED_TAIL_BYTES,
        "baseline_frame": config.baseline_frame,
        "baseline_rng_drift_frames": config.baseline_rng_drift_frames,
        "effective_start_frame_from_initial_seed": config.effective_start_frame_from_initial_seed,
        "pickup_input_lead_frames": config.pickup_input_lead_frames,
        "pickup_hold_frames": config.pickup_hold_frames,
        "post_pickup_frames": config.post_pickup_frames,
        "expected_records": config.expected_records,
        "schedule_source": config.schedule_source,
        "runtime_schedule_max_steps": config.runtime_schedule_max_steps,
        "progress_every": config.progress_every,
        "cache_dir": str(config.cache_dir),
        "enable_audio_killswitch": config.enable_audio_killswitch,
        "enable_no_render_mode": config.enable_no_render_mode,
        "enable_fast_forward": config.enable_fast_forward,
        "fast_forward_ratio": config.fast_forward_ratio,
    }


def write_status(config: Phase3Config, payload: Mapping[str, Any]) -> None:
    status = _now_status_base(config)
    status.update(dict(payload))
    write_json_atomic(config.status_path, status)


def _path_stamp(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise FileNotFoundError(f"secondhalf.csv not found: {path}")
    stat = path.stat()
    return {
        "path": str(path),
        "size": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _cache_key(payload: Mapping[str, Any]) -> str:
    data = json.dumps(dict(payload), sort_keys=True, separators=(",", ":")).encode("utf-8")
    return sha1_bytes(data)


def _target_cache_key_payload(config: Phase3Config) -> dict[str, Any]:
    return {
        "schema_version": PHASE3_CACHE_SCHEMA_VERSION,
        "kind": "phase3-secondhalf-t0-targets",
        "secondhalf_csv": _path_stamp(config.secondhalf_csv),
        "pickup_input_lead_frames": config.pickup_input_lead_frames,
        "expected_records": config.expected_records,
    }


def _target_cache_path(config: Phase3Config, key_payload: Mapping[str, Any]) -> Path:
    return config.cache_dir / f"secondhalf-t0-{_cache_key(key_payload)}.json"


def _contract_from_cache(payload: Mapping[str, Any]) -> SecondHalfCsvContract:
    return SecondHalfCsvContract(
        path=Path(str(payload["path"])),
        initial_seed_16bit=_parse_int(payload["initial_seed_16bit"], bits=16),
        row_count=int(payload["row_count"]),
        t_zero_rows=int(payload["t_zero_rows"]),
        min_frame_from_initial_seed=int(payload["min_frame_from_initial_seed"]),
        max_frame_from_initial_seed=int(payload["max_frame_from_initial_seed"]),
    )


def _target_from_cache(row: Sequence[Any]) -> SecondHalfTarget:
    if len(row) != 5:
        raise ValueError(f"bad cached target row length: {len(row)}")
    return SecondHalfTarget(
        upper_half=_parse_int(row[0], bits=16),
        sweep_index=int(row[1]),
        frame_from_initial_seed=int(row[2]),
        rng_seed=_parse_int(row[3], bits=32),
        input_frame_from_initial_seed=int(row[4]),
    )


def _target_to_cache_row(target: SecondHalfTarget) -> list[int]:
    return [
        target.upper_half,
        target.sweep_index,
        target.frame_from_initial_seed,
        target.rng_seed,
        target.input_frame_from_initial_seed,
    ]


def read_phase3_secondhalf_targets_cached(
    config: Phase3Config,
) -> tuple[SecondHalfCsvContract, list[SecondHalfTarget], dict[str, Any]]:
    """Load parsed `secondhalf.csv` targets from cache, or build and cache them."""

    key_payload = _target_cache_key_payload(config)
    cache_key = _cache_key(key_payload)
    cache_path = _target_cache_path(config, key_payload)
    if cache_path.is_file():
        with cache_path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if cached.get("key") == cache_key:
            contract = _contract_from_cache(cached["contract"])
            targets = [_target_from_cache(row) for row in cached["targets"]]
            if len(targets) != config.expected_records:
                raise ValueError(
                    f"cached target count mismatch: expected={config.expected_records} "
                    f"observed={len(targets)}"
                )
            return contract, targets, {
                "hit": True,
                "path": str(cache_path),
                "key": cache_key,
                "target_count": len(targets),
            }

    contract, targets = read_phase3_secondhalf_targets(
        config.secondhalf_csv,
        pickup_input_lead_frames=config.pickup_input_lead_frames,
        expected_records=config.expected_records,
    )
    cache_payload = {
        "schema_version": PHASE3_CACHE_SCHEMA_VERSION,
        "kind": "phase3-secondhalf-t0-targets",
        "key": cache_key,
        "key_payload": key_payload,
        "contract": contract.to_dict(),
        "targets": [_target_to_cache_row(target) for target in targets],
    }
    write_json_atomic(cache_path, cache_payload)
    return contract, targets, {
        "hit": False,
        "path": str(cache_path),
        "key": cache_key,
        "target_count": len(targets),
    }


def _column_indexes(header: Sequence[str], required: Sequence[str]) -> dict[str, int]:
    columns = {name: index for index, name in enumerate(header)}
    missing = set(required).difference(columns)
    if missing:
        raise ValueError(f"secondhalf.csv missing required column(s): {sorted(missing)}")
    return {name: columns[name] for name in required}


def _row_value(row: Sequence[str], index: int, *, line_number: int, column: str) -> str:
    if index >= len(row):
        raise ValueError(f"secondhalf.csv line {line_number} is missing column {column!r}")
    return row[index]


def read_phase3_secondhalf_targets(
    path: Path,
    *,
    pickup_input_lead_frames: int,
    expected_records: int = DEFAULT_EXPECTED_RECORDS,
) -> tuple[SecondHalfCsvContract, list[SecondHalfTarget]]:
    """Stream `secondhalf.csv`, keep exact `t-0` pickup rows, and sort them.

    The real CSV is about 2.4 million rows because it includes history around
    each target. Use positional `csv.reader` access instead of `DictReader` so
    non-`t-0` rows are cheap to skip.
    """

    if pickup_input_lead_frames < 0:
        raise ValueError("pickup input lead frames must be non-negative")
    if not path.is_file():
        raise FileNotFoundError(f"secondhalf.csv not found: {path}")

    initial_seeds: set[int] = set()
    initial_seed_tokens: set[str] = set()
    targets_by_upper: dict[int, SecondHalfTarget] = {}
    row_count = 0
    min_frame: int | None = None
    max_frame: int | None = None

    with path.open("r", newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            header = next(reader)
        except StopIteration as exc:
            raise ValueError(f"secondhalf.csv is empty: {path}") from exc

        columns = _column_indexes(header, SECONDHALF_REQUIRED_COLUMNS)
        seed_i = columns["initial_seed_16bit"]
        target_i = columns["target_half_16bit"]
        sweep_i = columns["sweep_index"]
        frame_i = columns["frame_from_initial_seed"]
        t_minus_i = columns["t_minus"]
        rng_i = columns["rng_seed"]

        for line_number, row in enumerate(reader, start=2):
            if not row or not any(cell.strip() for cell in row):
                continue
            row_count += 1

            raw_seed = _row_value(row, seed_i, line_number=line_number, column="initial_seed_16bit")
            seed_token = raw_seed.strip().lower()
            if seed_token not in initial_seed_tokens:
                initial_seed_tokens.add(seed_token)
                initial_seeds.add(_parse_int(seed_token, bits=16))

            raw_t_minus = _row_value(row, t_minus_i, line_number=line_number, column="t_minus")
            if raw_t_minus != "t-0" and raw_t_minus.strip().lower() != "t-0":
                continue

            raw_upper = _row_value(row, target_i, line_number=line_number, column="target_half_16bit")
            upper_half = _parse_int(raw_upper, bits=16)
            if upper_half in targets_by_upper:
                raise ValueError(
                    f"duplicate t-0 upper-half row: {format_u16(upper_half)} "
                    f"at line {line_number}"
                )

            raw_frame = _row_value(
                row,
                frame_i,
                line_number=line_number,
                column="frame_from_initial_seed",
            )
            frame = _parse_decimal_or_int(raw_frame)
            if frame < pickup_input_lead_frames:
                raise ValueError(
                    f"{format_u16(upper_half)} t-0 frame {frame} is before pickup lead "
                    f"{pickup_input_lead_frames}"
                )
            raw_rng = _row_value(row, rng_i, line_number=line_number, column="rng_seed")
            rng_seed = _parse_int(raw_rng, bits=32)
            raw_sweep = _row_value(row, sweep_i, line_number=line_number, column="sweep_index")
            target = SecondHalfTarget(
                upper_half=upper_half,
                sweep_index=_parse_decimal_or_int(raw_sweep),
                frame_from_initial_seed=frame,
                rng_seed=rng_seed,
                input_frame_from_initial_seed=frame - pickup_input_lead_frames,
            )
            targets_by_upper[upper_half] = target
            min_frame = frame if min_frame is None else min(min_frame, frame)
            max_frame = frame if max_frame is None else max(max_frame, frame)

    if len(initial_seeds) != 1:
        raise ValueError(
            "secondhalf.csv must contain exactly one initial seed, found "
            f"{[format_u16(seed) for seed in sorted(initial_seeds)]}"
        )
    if len(targets_by_upper) != expected_records:
        raise ValueError(
            f"secondhalf.csv must contain {expected_records} unique t-0 rows, "
            f"found {len(targets_by_upper)}"
        )
    if min_frame is None or max_frame is None:
        raise ValueError("secondhalf.csv contains no t-0 pickup rows")

    targets = sorted(
        targets_by_upper.values(),
        key=lambda target: (target.input_frame_from_initial_seed, target.upper_half),
    )
    contract = SecondHalfCsvContract(
        path=path,
        initial_seed_16bit=next(iter(initial_seeds)),
        row_count=row_count,
        t_zero_rows=len(targets),
        min_frame_from_initial_seed=min_frame,
        max_frame_from_initial_seed=max_frame,
    )
    return contract, targets


def maybe_limit_targets(targets: Sequence[SecondHalfTarget], limit: int | None) -> list[SecondHalfTarget]:
    if limit is None:
        return list(targets)
    if limit < 0:
        raise ValueError("limit must be non-negative")
    return list(targets[:limit])


def build_csv_frame_schedule(
    targets: Sequence[SecondHalfTarget],
    *,
    effective_start_frame: int,
) -> list[Phase3PickupTarget]:
    """Build the older direct CSV-frame schedule.

    Kept for calibration/debugging. The live 0x0001 pilot proved this is not
    the correct default for the current Phase 2 states because their `gRngValue`
    sequence does not line up with CSV frame 701.
    """

    scheduled: list[Phase3PickupTarget] = []
    for target in targets:
        event_step = target.frame_from_initial_seed - effective_start_frame
        input_delta = target.input_frame_from_initial_seed - effective_start_frame
        if input_delta < 0:
            raise RuntimeError(
                "CSV-frame schedule target is before loaded state: "
                f"target={format_u16(target.upper_half)} input_delta={input_delta}"
            )
        scheduled.append(
            Phase3PickupTarget(
                upper_half=target.upper_half,
                csv_sweep_index=target.sweep_index,
                csv_frame_from_initial_seed=target.frame_from_initial_seed,
                csv_rng_seed=target.rng_seed,
                event_step_from_start=event_step,
                input_delta_from_start=input_delta,
            )
        )
    return scheduled


def build_runtime_rng_schedule(
    targets: Sequence[SecondHalfTarget],
    *,
    start_rng: int,
    pickup_input_lead_frames: int,
    max_steps: int,
) -> list[Phase3PickupTarget]:
    """Build pickup order from the loaded state's actual `gRngValue` sequence."""

    if pickup_input_lead_frames < 0:
        raise ValueError("pickup input lead frames must be non-negative")
    if max_steps < pickup_input_lead_frames:
        raise ValueError("runtime schedule max steps must cover pickup lead frames")

    by_upper = {target.upper_half: target for target in targets}
    pending = set(by_upper)
    scheduled: list[Phase3PickupTarget] = []
    state = start_rng & 0xFFFFFFFF

    for event_step in range(1, max_steps + 1):
        state = lcrng_next_state(state)
        upper_half = (state >> 16) & 0xFFFF
        if event_step < pickup_input_lead_frames or upper_half not in pending:
            continue

        target = by_upper[upper_half]
        scheduled.append(
            Phase3PickupTarget(
                upper_half=upper_half,
                csv_sweep_index=target.sweep_index,
                csv_frame_from_initial_seed=target.frame_from_initial_seed,
                csv_rng_seed=target.rng_seed,
                event_step_from_start=event_step,
                input_delta_from_start=event_step - pickup_input_lead_frames,
            )
        )
        pending.remove(upper_half)
        if not pending:
            return scheduled

    sample_missing = ", ".join(format_u16(value) or "0x0000" for value in sorted(pending)[:8])
    raise RuntimeError(
        "runtime RNG schedule did not cover all target upper halves: "
        f"covered={len(scheduled)} missing={len(pending)} max_steps={max_steps} "
        f"sample_missing=[{sample_missing}]"
    )


def build_phase3_schedule(
    targets: Sequence[SecondHalfTarget],
    config: Phase3Config,
    *,
    observed_start_rng: int,
) -> list[Phase3PickupTarget]:
    """Choose the configured schedule source and return live pickup targets."""

    if config.schedule_source == "csv-frame":
        return build_csv_frame_schedule(
            targets,
            effective_start_frame=config.effective_start_frame_from_initial_seed,
        )
    if config.schedule_source == "runtime-rng":
        return build_runtime_rng_schedule(
            targets,
            start_rng=observed_start_rng,
            pickup_input_lead_frames=config.pickup_input_lead_frames,
            max_steps=config.runtime_schedule_max_steps,
        )
    raise ValueError(f"unsupported schedule source: {config.schedule_source!r}")


def _schedule_cache_key_payload(
    config: Phase3Config,
    *,
    observed_start_rng: int,
    target_cache_key: str,
) -> dict[str, Any]:
    return {
        "schema_version": PHASE3_CACHE_SCHEMA_VERSION,
        "kind": "phase3-pickup-schedule",
        "target_cache_key": target_cache_key,
        "schedule_source": config.schedule_source,
        "observed_start_rng": observed_start_rng & 0xFFFFFFFF,
        "pickup_input_lead_frames": config.pickup_input_lead_frames,
        "runtime_schedule_max_steps": config.runtime_schedule_max_steps,
        "effective_start_frame_from_initial_seed": config.effective_start_frame_from_initial_seed,
        "expected_records": config.expected_records,
    }


def _schedule_cache_path(config: Phase3Config, key_payload: Mapping[str, Any]) -> Path:
    return config.cache_dir / f"phase3-schedule-{_cache_key(key_payload)}.json"


def _scheduled_target_to_cache_row(target: Phase3PickupTarget) -> list[int]:
    return [
        target.upper_half,
        target.csv_sweep_index,
        target.csv_frame_from_initial_seed,
        target.csv_rng_seed,
        target.event_step_from_start,
        target.input_delta_from_start,
    ]


def _scheduled_target_from_cache(row: Sequence[Any]) -> Phase3PickupTarget:
    if len(row) != 6:
        raise ValueError(f"bad cached scheduled target row length: {len(row)}")
    return Phase3PickupTarget(
        upper_half=_parse_int(row[0], bits=16),
        csv_sweep_index=int(row[1]),
        csv_frame_from_initial_seed=int(row[2]),
        csv_rng_seed=_parse_int(row[3], bits=32),
        event_step_from_start=int(row[4]),
        input_delta_from_start=int(row[5]),
    )


def build_phase3_schedule_cached(
    targets: Sequence[SecondHalfTarget],
    config: Phase3Config,
    *,
    observed_start_rng: int,
    target_cache_key: str,
) -> tuple[list[Phase3PickupTarget], dict[str, Any]]:
    """Load the live pickup schedule from cache, or build and cache it."""

    key_payload = _schedule_cache_key_payload(
        config,
        observed_start_rng=observed_start_rng,
        target_cache_key=target_cache_key,
    )
    cache_key = _cache_key(key_payload)
    cache_path = _schedule_cache_path(config, key_payload)
    if cache_path.is_file():
        with cache_path.open("r", encoding="utf-8") as handle:
            cached = json.load(handle)
        if cached.get("key") == cache_key:
            scheduled = [_scheduled_target_from_cache(row) for row in cached["targets"]]
            if len(scheduled) != len(targets):
                raise ValueError(
                    f"cached schedule count mismatch: expected={len(targets)} "
                    f"observed={len(scheduled)}"
                )
            return scheduled, {
                "hit": True,
                "path": str(cache_path),
                "key": cache_key,
                "target_count": len(scheduled),
            }

    scheduled = build_phase3_schedule(
        targets,
        config,
        observed_start_rng=observed_start_rng,
    )
    cache_payload = {
        "schema_version": PHASE3_CACHE_SCHEMA_VERSION,
        "kind": "phase3-pickup-schedule",
        "key": cache_key,
        "key_payload": key_payload,
        "targets": [_scheduled_target_to_cache_row(target) for target in scheduled],
    }
    write_json_atomic(cache_path, cache_payload)
    return scheduled, {
        "hit": False,
        "path": str(cache_path),
        "key": cache_key,
        "target_count": len(scheduled),
    }


def read_memory_bytes(core: Any, address: int, size: int) -> bytes:
    """Read bytes from either host GBA memory or the visible Qt bridge."""

    memory = getattr(core, "memory")
    u8 = getattr(memory, "u8", None)
    if u8 is not None:
        return bytes(int(u8[address + offset]) & 0xFF for offset in range(size))
    return bytes(memory[address : address + size])


def read_party_count(core: Any) -> int:
    return read_memory_bytes(core, GPLAYER_PARTY_COUNT_ADDR, 1)[0]


def read_party_slot_record_prefix(core: Any, slot_number: int, size: int) -> bytes:
    """Read only the boxed-record prefix Phase 3 stores.

    Full Gen 3 party data is 100 bytes. The first 80 bytes are the boxed PK3
    record; the final 20 are party-only battle stats. Phase 3 stores the boxed
    record so checksum/species validation still works.
    """

    if not 1 <= slot_number <= 6:
        raise ValueError("party slot must be in range 1..6")
    if not 1 <= size <= BOX_SLOT_SIZE:
        raise ValueError(f"party slot prefix size must be in range 1..{BOX_SLOT_SIZE}")
    address = GPLAYER_PARTY_ADDR + (slot_number - 1) * PARTY_SLOT_SIZE
    return read_memory_bytes(core, address, size)


def expected_pid_for_lane(lane_id: int, upper_half: int) -> int:
    return ((upper_half & 0xFFFF) << 16) | (lane_id & 0xFFFF)


def pk3_filename_for_pid(pid: int) -> str:
    return f"{format_u32(pid)}.pk3"


def require_visible_qt(config: Phase3Config, core: Any | None) -> None:
    if core is not None or not config.require_qt:
        return
    if not qt_mode_enabled():
        raise RuntimeError(
            "Phase 3 live generation requires visible Qt mGBA scripting. "
            "Launch mGBA Qt and run this script from the visible session."
        )


def _core_bool_property(core: Any, name: str) -> bool | None:
    try:
        value = getattr(core, name)
    except Exception:
        return None
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if value is None:
        return None
    return bool(value)


def _set_live_feature_bool(
    core: Any,
    *,
    setter_name: str,
    state_name: str,
    label: str,
    value: bool,
) -> dict[str, Any]:
    setter = getattr(core, setter_name, None)
    if not callable(setter):
        raise RuntimeError(f"{label} setter is unavailable on this mGBA core.")
    result = setter(value)
    if result is False:
        raise RuntimeError(f"{label} setter returned failure.")
    observed = _core_bool_property(core, state_name)
    if observed is not None and observed != value:
        raise RuntimeError(f"{label} did not reach requested value {value!r}.")
    return {"requested": value, "observed": observed}


def apply_runtime_feature_toggles(config: Phase3Config, core: Any) -> dict[str, Any]:
    """Apply requested visible Qt speed features before generation starts."""

    applied: dict[str, Any] = {}
    if config.enable_audio_killswitch:
        applied["audio_killswitch"] = _set_live_feature_bool(
            core,
            setter_name="set_audio_killswitch",
            state_name="audio_killswitch_enabled",
            label="Audio killswitch",
            value=True,
        )
    if config.enable_no_render_mode:
        applied["no_render_mode"] = _set_live_feature_bool(
            core,
            setter_name="set_no_render_mode",
            state_name="no_render_mode_enabled",
            label="No-render mode",
            value=True,
        )
    if config.enable_fast_forward:
        ratio_setter = getattr(core, "set_fast_forward_ratio", None)
        if not callable(ratio_setter):
            raise RuntimeError("Fast-forward ratio setter is unavailable on this mGBA core.")
        ratio_result = ratio_setter(config.fast_forward_ratio)
        if ratio_result is False:
            raise RuntimeError("Fast-forward ratio setter returned failure.")
        applied["fast_forward_ratio"] = config.fast_forward_ratio
        applied["fast_forward"] = _set_live_feature_bool(
            core,
            setter_name="set_fast_forward",
            state_name="fast_forward_enabled",
            label="Fast-forward",
            value=True,
        )
    return applied


def load_phase3_core(config: Phase3Config) -> Any:
    require_visible_qt(config, None)
    core = load_gba_core(config.rom_path)
    apply_runtime_feature_toggles(config, core)
    if getattr(core, "memory", None) is None:
        # Visible Qt cores are already live here. Host-side test cores need one
        # reset before mGBA exposes memory and can load file-backed states.
        reset = getattr(core, "reset", None)
        if callable(reset):
            reset()
    load_state_file(core, config.phase2_state_path)
    return core


def _save_scratch_state(core: Any) -> None:
    save_scratch = getattr(core, "save_scratch_state", None)
    if not callable(save_scratch):
        raise RuntimeError("core does not expose save_scratch_state; visible Qt mGBA is required")
    save_scratch()


def _load_scratch_state(core: Any) -> None:
    load_scratch = getattr(core, "load_scratch_state", None)
    if not callable(load_scratch):
        raise RuntimeError("core does not expose load_scratch_state; visible Qt mGBA is required")
    load_scratch()


def _run_frames(config: Phase3Config, core: Any, mask: int, frames: int) -> None:
    input_tape.run_exact_frames(core, mask, frames, use_batch=config.use_batch)


def _clear_keys(core: Any) -> None:
    input_tape.set_exact_keys(core, 0)


def validate_starting_rng(config: Phase3Config, core: Any) -> int:
    observed = read_rng_state(core)
    if config.expected_baseline_rng is not None and observed != config.expected_baseline_rng:
        raise RuntimeError(
            "Phase 3 starting RNG mismatch: "
            f"expected={format_u32(config.expected_baseline_rng)} observed={format_u32(observed)}"
        )
    return observed


def extract_target_record(
    config: Phase3Config,
    core: Any,
    target: Phase3PickupTarget,
    *,
    party_slot: int = 2,
) -> bytes:
    """Press A, wait for pickup completion, validate PID, and return 80 bytes."""

    _run_frames(config, core, A_BUTTON_MASK, config.pickup_hold_frames)
    if config.post_pickup_frames:
        _run_frames(config, core, 0, config.post_pickup_frames)

    party_count = read_party_count(core)
    if party_count < party_slot:
        raise RuntimeError(
            f"pickup did not place egg in party slot {party_slot}; party_count={party_count}"
        )

    truncated_record = read_party_slot_record_prefix(core, party_slot, TRUNCATED_RECORD_SIZE)
    pid = personality_value_from_box_record(truncated_record)
    expected_pid = expected_pid_for_lane(config.lane_id, target.upper_half)
    if pid != expected_pid:
        raise RuntimeError(
            "extracted PID mismatch: "
            f"target_upper={format_u16(target.upper_half)} "
            f"expected_pid={format_u32(expected_pid)} observed_pid={format_u32(pid)} "
            f"csv_t0_frame={target.csv_frame_from_initial_seed} "
            f"event_step_from_start={target.event_step_from_start} "
            f"input_delta_from_start={target.input_delta_from_start} "
            f"pickup_input_lead_frames={config.pickup_input_lead_frames}"
        )
    return truncated_record


def build_phase3_manifest(
    config: Phase3Config,
    contract: SecondHalfCsvContract,
    targets: Sequence[Phase3PickupTarget],
    *,
    observed_start_rng: int,
    block: Phase3LaneBlock,
    pk3_records_sha1: str,
    presence_bitmap_sha1: str,
    elapsed_seconds: float,
    csv_parse_seconds: float,
    schedule_seconds: float,
    target_cache: Mapping[str, Any],
    schedule_cache: Mapping[str, Any],
) -> dict[str, Any]:
    first_target = targets[0].to_dict() if targets else None
    last_target = targets[-1].to_dict() if targets else None
    return {
        "schema_version": PHASE3_SCHEMA_VERSION,
        "created_at_unix": time.time(),
        "lane_id": config.lane_hex,
        "phase2_state_path": str(config.phase2_state_path),
        "phase2_state_sha1": sha1_file(config.phase2_state_path) if config.phase2_state_path.is_file() else None,
        "secondhalf_csv_contract": contract.to_dict(),
        "rom_path": str(config.rom_path),
        "baseline_frame": config.baseline_frame,
        "baseline_rng_drift_frames": config.baseline_rng_drift_frames,
        "effective_start_frame_from_initial_seed": config.effective_start_frame_from_initial_seed,
        "pickup_input_lead_frames": config.pickup_input_lead_frames,
        "pickup_input_lead_note": (
            "secondhalf.csv t-0 is the pickup Random() state. "
            "The visible A input is pressed this many frames before that event; "
            "0x0001 probing measured a 2-4 frame delay, defaulting to 3."
        ),
        "pickup_hold_frames": config.pickup_hold_frames,
        "post_pickup_frames": config.post_pickup_frames,
        "expected_baseline_rng": format_u32(config.expected_baseline_rng),
        "observed_start_rng": format_u32(observed_start_rng),
        "schedule_source": config.schedule_source,
        "runtime_schedule_max_steps": config.runtime_schedule_max_steps,
        "record_count": config.expected_records,
        "generated_records": block.written,
        "archive_format": "explicit-pid-pk3",
        "pk3_entry_count": block.written,
        "record_size": TRUNCATED_RECORD_SIZE,
        "source_box_record_size": BOX_SLOT_SIZE,
        "truncated_tail_bytes": TRUNCATED_TAIL_BYTES,
        "pk3_records_sha1": pk3_records_sha1,
        "presence_bitmap_sha1": presence_bitmap_sha1,
        "csv_parse_seconds": csv_parse_seconds,
        "schedule_seconds": schedule_seconds,
        "target_cache": dict(target_cache),
        "schedule_cache": dict(schedule_cache),
        "sort_order": (
            "runtime RNG first-hit order from loaded gRngValue"
            if config.schedule_source == "runtime-rng"
            else "secondhalf.csv t-0 input frame ascending, then upper half"
        ),
        "first_target": first_target,
        "last_target": last_target,
        "elapsed_seconds": elapsed_seconds,
    }


def write_phase3_zip_atomic(
    config: Phase3Config,
    *,
    block: Phase3LaneBlock,
    manifest: Mapping[str, Any],
) -> Phase3Result:
    """Build ZIP bytes in RAM, then write one final PID-named `.pk3` archive."""

    config.output_dir.mkdir(parents=True, exist_ok=True)
    output_path = config.output_zip_path
    if output_path.exists() and not config.overwrite:
        raise FileExistsError(f"output already exists; pass --overwrite to replace: {output_path}")

    pk3_records_sha1 = block.pk3_records_sha1()
    presence_bitmap_sha1 = sha1_bytes(block.bitmap_bytes())

    temp_path = output_path.with_name(f"{output_path.name}.tmp")
    try:
        zip_buffer = io.BytesIO()
        with zipfile.ZipFile(zip_buffer, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=ZIP_DEFLATE_LEVEL, allowZip64=True) as archive:
            written = 0
            for upper_half, record in block.iter_present_records():
                pid = personality_value_from_box_record(record)
                expected_pid = expected_pid_for_lane(config.lane_id, upper_half)
                if pid != expected_pid:
                    raise RuntimeError(
                        "refusing to write bad PK3 entry: "
                        f"upper={format_u16(upper_half)} expected_pid={format_u32(expected_pid)} "
                        f"observed_pid={format_u32(pid)}"
                    )
                zip_info = zipfile.ZipInfo(pk3_filename_for_pid(pid), date_time=FIXED_ZIP_DATETIME)
                zip_info.compress_type = zipfile.ZIP_DEFLATED
                zip_info.create_system = 0
                zip_info.external_attr = 0
                archive.writestr(zip_info, record)
                written += 1
            if written != block.written:
                raise RuntimeError(f"ZIP PK3 entry count mismatch: expected={block.written} written={written}")
        with temp_path.open("wb") as output_file:
            output_file.write(zip_buffer.getbuffer())
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    temp_path.replace(output_path)
    return Phase3Result(
        lane_id=config.lane_id,
        generated_records=block.written,
        output_zip_path=output_path,
        pk3_records_sha1=pk3_records_sha1,
        presence_bitmap_sha1=presence_bitmap_sha1,
        zip_sha1=sha1_file(output_path),
        elapsed_seconds=float(manifest.get("elapsed_seconds", 0.0)),
    )


def generate_phase3_lane_block(
    config: Phase3Config,
    *,
    core: Any | None = None,
    party_slot: int = 2,
) -> Phase3Result:
    """Run one Phase 3 lane build."""

    start_time = time.monotonic()
    if config.output_zip_path.exists() and not config.overwrite:
        raise FileExistsError(f"output already exists; pass --overwrite to replace: {config.output_zip_path}")

    require_visible_qt(config, core)
    csv_parse_start = time.monotonic()
    contract, all_csv_targets, target_cache = read_phase3_secondhalf_targets_cached(config)
    csv_parse_seconds = time.monotonic() - csv_parse_start

    if core is None:
        core = load_phase3_core(config)

    observed_start_rng = validate_starting_rng(config, core)
    schedule_start = time.monotonic()
    all_scheduled_targets, schedule_cache = build_phase3_schedule_cached(
        all_csv_targets,
        config,
        observed_start_rng=observed_start_rng,
        target_cache_key=str(target_cache["key"]),
    )
    schedule_seconds = time.monotonic() - schedule_start
    targets = maybe_limit_targets(all_scheduled_targets, config.limit)
    block = Phase3LaneBlock(record_count=config.expected_records)
    current_input_delta = 0

    write_status(
        config,
        {
            "status": "running",
            "generated_records": 0,
            "selected_targets": len(targets),
            "csv_contract": contract.to_dict(),
            "csv_parse_seconds": csv_parse_seconds,
            "schedule_seconds": schedule_seconds,
            "target_cache": target_cache,
            "schedule_cache": schedule_cache,
            "observed_start_rng": format_u32(observed_start_rng),
        },
    )

    try:
        _clear_keys(core)
        _save_scratch_state(core)
        for target in targets:
            delta = target.input_delta_from_start - current_input_delta
            if delta < 0:
                raise RuntimeError(
                    "Phase 3 schedule moved backward after pickup-lead adjustment: "
                    f"target={format_u16(target.upper_half)} delta={delta}"
                )
            if delta:
                _run_frames(config, core, 0, delta)
                current_input_delta = target.input_delta_from_start

            # Pickup mutates party, RNG, and dialog state. Save a one-slot
            # in-memory checkpoint at the exact pre-input frame, extract, then
            # restore so the next target can continue from the same runway.
            _save_scratch_state(core)
            truncated_record = extract_target_record(config, core, target, party_slot=party_slot)
            block.set_record(target.upper_half, truncated_record)
            _load_scratch_state(core)
            _clear_keys(core)
    except Exception as exc:
        _append_jsonl(
            config.error_path,
            {
                "time_unix": time.time(),
                "error": str(exc),
                "generated_records": block.written,
                "current_input_delta_from_start": current_input_delta,
            },
        )
        write_status(
            config,
            {
                "status": "failed",
                "generated_records": block.written,
                "selected_targets": len(targets),
                "error": str(exc),
                "csv_parse_seconds": csv_parse_seconds,
                "schedule_seconds": schedule_seconds,
                "target_cache": target_cache,
                "schedule_cache": schedule_cache,
                "elapsed_seconds": time.monotonic() - start_time,
            },
        )
        raise

    elapsed = time.monotonic() - start_time
    pk3_records_sha1 = block.pk3_records_sha1()
    presence_bitmap_sha1 = sha1_bytes(block.bitmap_bytes())
    manifest = build_phase3_manifest(
        config,
        contract,
        targets,
        observed_start_rng=observed_start_rng,
        block=block,
        pk3_records_sha1=pk3_records_sha1,
        presence_bitmap_sha1=presence_bitmap_sha1,
        elapsed_seconds=elapsed,
        csv_parse_seconds=csv_parse_seconds,
        schedule_seconds=schedule_seconds,
        target_cache=target_cache,
        schedule_cache=schedule_cache,
    )
    result = write_phase3_zip_atomic(config, block=block, manifest=manifest)
    write_status(
        config,
        {
            "status": "complete",
            "generated_records": block.written,
            "selected_targets": len(targets),
            "result": result.to_dict(),
            "csv_parse_seconds": csv_parse_seconds,
            "schedule_seconds": schedule_seconds,
            "target_cache": target_cache,
            "schedule_cache": schedule_cache,
            "elapsed_seconds": elapsed,
        },
    )
    return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Build one compressed Phase 3 Spinda PK3 ZIP from a visible Qt mGBA "
            "Phase 2 pickup savestate."
        )
    )
    parser.add_argument("--lane-id", type=lambda raw: _parse_int(raw, bits=16), default=_env_int("LANE_ID", DEFAULT_LANE_ID))
    parser.add_argument("--rom", type=Path, default=_env_path("ROM") or DEFAULT_ROM_PATH)
    parser.add_argument("--phase2-state", type=Path, default=_env_path("PHASE2_STATE"))
    parser.add_argument("--secondhalf-csv", type=Path, default=_env_path("SECONDHALF_CSV") or DEFAULT_SECOND_HALF_CSV)
    parser.add_argument("--output-dir", type=Path, default=_env_path("OUTPUT_DIR") or DEFAULT_OUTPUT_DIR)
    parser.add_argument("--baseline-frame", type=int, default=_env_int("BASELINE_FRAME", DEFAULT_BASELINE_FRAME))
    parser.add_argument(
        "--baseline-rng-drift-frames",
        type=int,
        default=_env_int("BASELINE_RNG_DRIFT_FRAMES", DEFAULT_BASELINE_RNG_DRIFT_FRAMES),
        help=(
            "Signed LCRNG/frame drift of loaded Phase 2 state relative to --baseline-frame. "
            "Current validated 0x0001 state is +1, so waits subtract one frame."
        ),
    )
    parser.add_argument(
        "--pickup-input-lead-frames",
        type=int,
        default=_env_int("PICKUP_INPUT_LEAD_FRAMES", DEFAULT_PICKUP_INPUT_LEAD_FRAMES),
        help=(
            "Frames between visible A press and pickup Random() t-0. "
            "0x0001 probing saw 2-4; default is 3."
        ),
    )
    parser.add_argument("--pickup-hold-frames", type=int, default=_env_int("PICKUP_HOLD_FRAMES", DEFAULT_PICKUP_HOLD_FRAMES))
    parser.add_argument("--post-pickup-frames", type=int, default=_env_int("POST_PICKUP_FRAMES", DEFAULT_POST_PICKUP_FRAMES))
    parser.add_argument("--expected-records", type=int, default=_env_int("EXPECTED_RECORDS", DEFAULT_EXPECTED_RECORDS))
    parser.add_argument(
        "--expected-baseline-rng",
        default=os.environ.get("MGBA_SPINDA_PHASE3_EXPECTED_BASELINE_RNG", format_u32(DEFAULT_EXPECTED_BASELINE_RNG)),
        help="Expected starting gRngValue, or 'none' to skip.",
    )
    parser.add_argument(
        "--schedule-source",
        choices=("runtime-rng", "csv-frame"),
        default=os.environ.get("MGBA_SPINDA_PHASE3_SCHEDULE_SOURCE", DEFAULT_SCHEDULE_SOURCE),
        help=(
            "runtime-rng derives pickup order from the loaded state's live gRngValue. "
            "csv-frame keeps the direct secondhalf.csv frame model for debugging."
        ),
    )
    parser.add_argument(
        "--runtime-schedule-max-steps",
        type=int,
        default=_env_int("RUNTIME_SCHEDULE_MAX_STEPS", DEFAULT_RUNTIME_SCHEDULE_MAX_STEPS),
    )
    parser.add_argument(
        "--enable-audio-killswitch",
        dest="enable_audio_killswitch",
        action="store_true",
        default=_env_bool("ENABLE_AUDIO_KILLSWITCH", DEFAULT_ENABLE_AUDIO_KILLSWITCH),
    )
    parser.add_argument("--disable-audio-killswitch", dest="enable_audio_killswitch", action="store_false")
    parser.add_argument(
        "--enable-no-render",
        dest="enable_no_render",
        action="store_true",
        default=_env_bool("ENABLE_NO_RENDER", DEFAULT_ENABLE_NO_RENDER_MODE),
    )
    parser.add_argument("--disable-no-render", dest="enable_no_render", action="store_false")
    parser.add_argument(
        "--enable-fast-forward",
        dest="enable_fast_forward",
        action="store_true",
        default=_env_bool("ENABLE_FAST_FORWARD", DEFAULT_ENABLE_FAST_FORWARD),
    )
    parser.add_argument("--disable-fast-forward", dest="enable_fast_forward", action="store_false")
    parser.add_argument(
        "--fast-forward-ratio",
        type=float,
        default=_env_float("FAST_FORWARD_RATIO", DEFAULT_FAST_FORWARD_RATIO),
        help="mGBA fast-forward ratio; non-positive means unbounded.",
    )
    parser.add_argument(
        "--progress-every",
        type=int,
        default=_env_int("PROGRESS_EVERY", DEFAULT_PROGRESS_EVERY),
        help=(
            "Compatibility knob only. Phase 3 now writes start/fail/complete status, "
            "not per-record progress, so live generation is not slowed by status IO."
        ),
    )
    parser.add_argument("--limit", type=int, default=_env_optional_int("LIMIT", None))
    parser.add_argument("--overwrite", action="store_true", default=bool(_env_int("OVERWRITE", 0)))
    parser.add_argument("--allow-non-qt-for-tests", action="store_true")
    parser.add_argument("--no-batch", action="store_true")
    return parser


def config_from_args(args: argparse.Namespace) -> Phase3Config:
    lane_id = int(args.lane_id)
    phase2_state_path = args.phase2_state
    if phase2_state_path is None:
        phase2_state_path = DEFAULT_PHASE2_STATE_DIR / f"{format_u16(lane_id)}.ss0"

    expected_baseline_rng: int | None
    raw_expected = str(args.expected_baseline_rng).strip().lower()
    if raw_expected in {"none", "off", "skip", ""}:
        expected_baseline_rng = None
    else:
        expected_baseline_rng = _parse_int(args.expected_baseline_rng, bits=32)

    config = Phase3Config(
        lane_id=lane_id,
        rom_path=Path(args.rom),
        phase2_state_path=Path(phase2_state_path),
        secondhalf_csv=Path(args.secondhalf_csv),
        output_dir=Path(args.output_dir),
        baseline_frame=int(args.baseline_frame),
        baseline_rng_drift_frames=int(args.baseline_rng_drift_frames),
        pickup_input_lead_frames=int(args.pickup_input_lead_frames),
        pickup_hold_frames=int(args.pickup_hold_frames),
        post_pickup_frames=int(args.post_pickup_frames),
        expected_records=int(args.expected_records),
        expected_baseline_rng=expected_baseline_rng,
        schedule_source=str(args.schedule_source),
        runtime_schedule_max_steps=int(args.runtime_schedule_max_steps),
        enable_audio_killswitch=bool(args.enable_audio_killswitch),
        enable_no_render_mode=bool(args.enable_no_render),
        enable_fast_forward=bool(args.enable_fast_forward),
        fast_forward_ratio=float(args.fast_forward_ratio),
        progress_every=int(args.progress_every),
        limit=args.limit,
        overwrite=bool(args.overwrite),
        require_qt=not bool(args.allow_non_qt_for_tests),
        use_batch=not bool(args.no_batch),
    )
    validate_config(config)
    return config


def validate_config(config: Phase3Config) -> None:
    if not 0 <= config.lane_id <= 0xFFFF:
        raise ValueError("--lane-id must fit in 16 bits")
    if config.baseline_frame < 0:
        raise ValueError("--baseline-frame must be non-negative")
    if config.effective_start_frame_from_initial_seed < 0:
        raise ValueError("--baseline-frame + --baseline-rng-drift-frames must be non-negative")
    if config.pickup_input_lead_frames < 0:
        raise ValueError("--pickup-input-lead-frames must be non-negative")
    if config.pickup_hold_frames < 1:
        raise ValueError("--pickup-hold-frames must be positive")
    if config.post_pickup_frames < 0:
        raise ValueError("--post-pickup-frames must be non-negative")
    if config.expected_records < 1 or config.expected_records > DEFAULT_EXPECTED_RECORDS:
        raise ValueError("--expected-records must be in range 1..65536")
    if config.schedule_source not in {"runtime-rng", "csv-frame"}:
        raise ValueError("--schedule-source must be runtime-rng or csv-frame")
    if config.runtime_schedule_max_steps < config.pickup_input_lead_frames:
        raise ValueError("--runtime-schedule-max-steps must cover pickup lead frames")
    if config.limit is not None and config.limit < 0:
        raise ValueError("--limit must be non-negative")
    if config.progress_every < 0:
        raise ValueError("--progress-every must be non-negative")


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    config = config_from_args(args)
    result = generate_phase3_lane_block(config)
    print(json.dumps(result.to_dict(), indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    if qt_mode_enabled():
        main()
    else:
        raise SystemExit(main())
