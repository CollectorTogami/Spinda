"""Shared FR/LG Trainer Shiny Value save-bank helpers.

This module is deliberately emulator-free. The live builder imports it, but the
unit tests can also exercise all TSV math, wait-plan construction, status JSON
shape, and trainer-ID memory reads without loading a ROM.

Definitions used here:

- TID is the visible 16-bit Trainer ID.
- SID is the 16-bit Secret ID.
- TSV is ``(TID ^ SID) >> 3`` and therefore has 8192 possible values.
- A Pokemon PID's matching value is ``(PID_low ^ PID_high) >> 3``.

The save-bank goal is one FR/LG save for every TSV. For any generated Spinda
egg, the save whose TSV equals the egg's PSV can hatch it shiny; other saves
hatch the same egg non-shiny. Mass hatching is a later consumer stage that
packages shiny and non-shiny proof outputs separately.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073
UINT32_MASK = 0xFFFFFFFF
UINT16_MASK = 0xFFFF

TSV_COUNT = 8192
SID_LOW_VARIANTS_PER_TSV = 8

GRNG_VALUE_ADDR = 0x03005000
GSAVEBLOCK2_PTR_ADDR = 0x0300500C
PLAYER_TRAINER_ID_OFFSET = 0x000A
INITIAL_TID_MIRROR_ADDR = 0x02020000


@dataclass(frozen=True)
class TsvWaitPlanEntry:
    """One planned branch from the pre-SID checkpoint to a target TSV."""

    tsv: int
    wait_frames: int
    rng_advance: int
    predicted_tid: int
    predicted_sid: int
    predicted_tsv: int
    predicted_rng: int

    def to_json(self) -> dict[str, Any]:
        """Return a stable, readable JSON form."""

        return {
            "tsv": format_tsv(self.tsv),
            "wait_frames": self.wait_frames,
            "rng_advance": self.rng_advance,
            "predicted_tid": format_u16(self.predicted_tid),
            "predicted_sid": format_u16(self.predicted_sid),
            "predicted_tsv": format_tsv(self.predicted_tsv),
            "predicted_rng": format_u32(self.predicted_rng),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "TsvWaitPlanEntry":
        """Parse one wait-plan entry from status JSON."""

        return cls(
            tsv=parse_int(data["tsv"]),
            wait_frames=int(data["wait_frames"]),
            rng_advance=int(data["rng_advance"]),
            predicted_tid=parse_int(data["predicted_tid"]),
            predicted_sid=parse_int(data["predicted_sid"]),
            predicted_tsv=parse_int(data["predicted_tsv"]),
            predicted_rng=parse_int(data["predicted_rng"]),
        )


@dataclass(frozen=True)
class TsvStatusEntry:
    """One status row persisted while the live builder runs."""

    tsv: int
    done: bool = False
    wait_frames: int | None = None
    rng_advance: int | None = None
    tid: int | None = None
    sid: int | None = None
    save_path: str | None = None
    save_sha1: str | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return a compact JSON row with hex IDs."""

        row: dict[str, Any] = {
            "tsv": format_tsv(self.tsv),
            "done": bool(self.done),
        }
        optional = {
            "wait_frames": self.wait_frames,
            "rng_advance": self.rng_advance,
            "tid": format_u16(self.tid) if self.tid is not None else None,
            "sid": format_u16(self.sid) if self.sid is not None else None,
            "save_path": self.save_path,
            "save_sha1": self.save_sha1,
            "error": self.error,
        }
        row.update({key: value for key, value in optional.items() if value is not None})
        return row

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "TsvStatusEntry":
        """Parse one persisted status row."""

        return cls(
            tsv=parse_int(data["tsv"]),
            done=bool(data.get("done", False)),
            wait_frames=data.get("wait_frames"),
            rng_advance=data.get("rng_advance"),
            tid=parse_optional_int(data.get("tid")),
            sid=parse_optional_int(data.get("sid")),
            save_path=data.get("save_path"),
            save_sha1=data.get("save_sha1"),
            error=data.get("error"),
        )


def parse_int(value: Any) -> int:
    """Parse decimal or ``0x`` text into an integer."""

    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def parse_optional_int(value: Any) -> int | None:
    """Parse an optional integer field."""

    if value is None:
        return None
    return parse_int(value)


def format_u16(value: int) -> str:
    """Format a 16-bit value as uppercase hex."""

    return f"0x{int(value) & UINT16_MASK:04X}"


def format_u32(value: int) -> str:
    """Format a 32-bit value as uppercase hex."""

    return f"0x{int(value) & UINT32_MASK:08X}"


def format_tsv(value: int) -> str:
    """Format a TSV as ``0x0000`` through ``0x1FFF``."""

    return f"0x{int(value) & (TSV_COUNT - 1):04X}"


def lcrng_next_state(state: int) -> int:
    """Advance the GBA LCRNG once."""

    return (int(state) * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT) & UINT32_MASK


def lcrng_advance(state: int, steps: int) -> int:
    """Advance the GBA LCRNG by ``steps`` calls.

    The helper intentionally uses a simple loop because the save-bank planning
    search normally needs only tens of thousands of calls. Keeping it simple
    makes tests and audits easier, and the live emulator route dominates run
    time later.
    """

    if steps < 0:
        raise ValueError("steps must be non-negative")
    value = int(state) & UINT32_MASK
    for _ in range(int(steps)):
        value = lcrng_next_state(value)
    return value


def tsv_from_tid_sid(tid: int, sid: int) -> int:
    """Return the Trainer Shiny Value for a TID/SID pair."""

    return ((int(tid) & UINT16_MASK) ^ (int(sid) & UINT16_MASK)) >> 3


def psv_from_pid(pid: int) -> int:
    """Return the Pokemon shiny value for a 32-bit personality value."""

    value = int(pid) & UINT32_MASK
    low = value & UINT16_MASK
    high = (value >> 16) & UINT16_MASK
    return (low ^ high) >> 3


def acceptable_sids_for_tsv(tid: int, tsv: int) -> tuple[int, ...]:
    """Return the eight SID values that produce ``tsv`` for ``tid``."""

    if not 0 <= int(tsv) < TSV_COUNT:
        raise ValueError(f"tsv must be in 0..{TSV_COUNT - 1}, got {tsv!r}")
    tid16 = int(tid) & UINT16_MASK
    base = int(tsv) << 3
    return tuple((tid16 ^ (base | low3)) & UINT16_MASK for low3 in range(SID_LOW_VARIANTS_PER_TSV))


def build_wait_plan(
    *,
    tid: int,
    start_rng: int,
    sid_commit_offset: int = 1,
    rng_advances_per_neutral_frame: int = 1,
    target_count: int = TSV_COUNT,
    max_advances: int | None = None,
) -> list[TsvWaitPlanEntry]:
    """Build one wait-plan row for every reachable TSV.

    ``start_rng`` is the live ``gRngValue`` at the branch checkpoint before the
    final input that commits SID. ``rng_advances_per_neutral_frame`` says how
    many LCRNG calls the route burns during one neutral wait frame.

    ``sid_commit_offset`` is the number of LCRNG calls between the final input
    being accepted and the SID value being read from ``Random()`` when no
    neutral wait frames are inserted.
    """

    if not 1 <= int(target_count) <= TSV_COUNT:
        raise ValueError(f"target_count must be in 1..{TSV_COUNT}")
    if int(sid_commit_offset) < 0:
        raise ValueError("sid_commit_offset must be non-negative")
    if int(rng_advances_per_neutral_frame) < 1:
        raise ValueError("rng_advances_per_neutral_frame must be at least 1")

    seen: dict[int, TsvWaitPlanEntry] = {}
    wait_frames = 0
    rng_advance = int(sid_commit_offset)
    stride = int(rng_advances_per_neutral_frame)
    hard_limit = max_advances
    rng_value = lcrng_advance(start_rng, rng_advance)

    while len(seen) < int(target_count):
        if hard_limit is not None and rng_advance > hard_limit:
            missing = int(target_count) - len(seen)
            raise RuntimeError(
                f"wait-plan search stopped at advance {rng_advance}; "
                f"{missing} TSV(s) still missing"
            )

        sid = (rng_value >> 16) & UINT16_MASK
        actual_tsv = tsv_from_tid_sid(tid, sid)
        if actual_tsv not in seen:
            seen[actual_tsv] = TsvWaitPlanEntry(
                tsv=actual_tsv,
                wait_frames=wait_frames,
                rng_advance=rng_advance,
                predicted_tid=int(tid) & UINT16_MASK,
                predicted_sid=sid,
                predicted_tsv=actual_tsv,
                predicted_rng=rng_value,
            )
        wait_frames += 1
        rng_advance += stride
        if len(seen) < int(target_count):
            rng_value = lcrng_advance(rng_value, stride)

    return [seen[tsv] for tsv in sorted(seen)]


def new_status(
    *,
    plan: Iterable[TsvWaitPlanEntry],
    tid: int,
    start_rng: int,
    sid_commit_offset: int,
    rng_advances_per_neutral_frame: int,
) -> dict[str, Any]:
    """Create a fresh status document with all TSV rows initially false."""

    entries = [
        TsvStatusEntry(
            tsv=entry.tsv,
            wait_frames=entry.wait_frames,
            rng_advance=entry.rng_advance,
        ).to_json()
        for entry in plan
    ]
    return {
        "format": "frlg-tsv-save-bank-status-v1",
        "tid": format_u16(tid),
        "start_rng": format_u32(start_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_neutral_frame": int(rng_advances_per_neutral_frame),
        "target_tsvs": len(entries),
        "complete_tsvs": 0,
        "entries": entries,
    }


def status_entries_by_tsv(status: Mapping[str, Any]) -> dict[int, TsvStatusEntry]:
    """Return status entries keyed by TSV."""

    return {entry.tsv: entry for entry in (TsvStatusEntry.from_json(row) for row in status.get("entries", []))}


def mark_status_hit(
    status: dict[str, Any],
    *,
    tsv: int,
    tid: int,
    sid: int,
    save_path: str | os.PathLike[str],
    save_sha1: str | None,
) -> None:
    """Mark one TSV as completed inside a mutable status document."""

    entries = status_entries_by_tsv(status)
    if int(tsv) not in entries:
        raise KeyError(f"status has no TSV {format_tsv(tsv)}")
    old = entries[int(tsv)]
    entries[int(tsv)] = TsvStatusEntry(
        tsv=int(tsv),
        done=True,
        wait_frames=old.wait_frames,
        rng_advance=old.rng_advance,
        tid=tid,
        sid=sid,
        save_path=str(save_path),
        save_sha1=save_sha1,
    )
    _rewrite_entries(status, entries)


def mark_status_error(status: dict[str, Any], *, tsv: int, error: str) -> None:
    """Record a TSV-specific error while preserving the plan fields."""

    entries = status_entries_by_tsv(status)
    if int(tsv) not in entries:
        raise KeyError(f"status has no TSV {format_tsv(tsv)}")
    old = entries[int(tsv)]
    entries[int(tsv)] = TsvStatusEntry(
        tsv=int(tsv),
        done=False,
        wait_frames=old.wait_frames,
        rng_advance=old.rng_advance,
        error=str(error),
    )
    _rewrite_entries(status, entries)


def _rewrite_entries(status: dict[str, Any], entries: Mapping[int, TsvStatusEntry]) -> None:
    """Rewrite sorted status rows and counters."""

    rows = [entries[tsv].to_json() for tsv in sorted(entries)]
    status["entries"] = rows
    status["complete_tsvs"] = sum(1 for row in rows if row.get("done"))
    status["target_tsvs"] = len(rows)


def status_summary(status: Mapping[str, Any]) -> dict[str, int]:
    """Return small counters for dashboards or tests."""

    rows = list(status.get("entries", []))
    complete = sum(1 for row in rows if row.get("done"))
    errors = sum(1 for row in rows if row.get("error"))
    return {"complete_tsvs": complete, "target_tsvs": len(rows), "errors": errors}


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """Write JSON with a temporary file and atomic replace."""

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return data


def sha1_file(path: Path) -> str:
    """Return the SHA-1 digest of a file."""

    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def read_rng_state(core: Any, *, address: int = GRNG_VALUE_ADDR) -> int:
    """Read ``gRngValue`` from an mGBA Python core-like object."""

    return int(core.memory.u32[int(address)]) & UINT32_MASK


def read_tid_from_initial_mirror(core: Any, *, address: int = INITIAL_TID_MIRROR_ADDR) -> int:
    """Read the already-hit TID from the pre-SID mirror used by this workflow."""

    return int(core.memory.u16[int(address)]) & UINT16_MASK


def read_trainer_id_from_saveblock2(
    core: Any,
    *,
    pointer_address: int = GSAVEBLOCK2_PTR_ADDR,
    trainer_id_offset: int = PLAYER_TRAINER_ID_OFFSET,
) -> tuple[int, int]:
    """Read final TID/SID from FR/LG SaveBlock2 via ``gSaveBlock2Ptr``."""

    pointer = int(core.memory.u32[int(pointer_address)]) & UINT32_MASK
    if pointer == 0:
        raise RuntimeError("gSaveBlock2Ptr is zero; trainer ID is not readable yet")
    tid = int(core.memory.u16[pointer + int(trainer_id_offset)]) & UINT16_MASK
    sid = int(core.memory.u16[pointer + int(trainer_id_offset) + 2]) & UINT16_MASK
    return tid, sid


def plan_digest(plan: Iterable[TsvWaitPlanEntry]) -> str:
    """Return a short digest for a wait plan."""

    digest = hashlib.sha256()
    for entry in plan:
        digest.update(json.dumps(entry.to_json(), sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()


def as_json_rows(plan: Iterable[TsvWaitPlanEntry]) -> list[dict[str, Any]]:
    """Serialize a plan into JSON rows."""

    return [entry.to_json() for entry in plan]
