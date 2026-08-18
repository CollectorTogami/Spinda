"""Shared FR/LG trainer-ID and secret-ID bot helpers.

The helpers in this module are emulator-light on purpose. Live bots import
them, while unit tests can exercise the LCRNG math, TID/SID memory reads, wait
planning, calibration, and ledger JSON without loading a ROM.

FR/LG ID facts used here:

- the visible Trainer ID (TID) is the Timer 1 low half at player-name exit
- the Secret ID (SID) is the high half of the first ``Random()`` consumed later
  by ``InitPlayerTrainerId()``
- for a fixed TID, the Trainer Shiny Value is ``(TID ^ SID) >> 3``

For this workspace's SID bank, the intended TID is ``0``. That means the shiny
value is simply ``SID >> 3``, with eight SID variants for each shiny value.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


GBA_LCRNG_MULTIPLIER = 0x41C64E6D
GBA_LCRNG_INCREMENT = 0x6073
UINT32_MASK = 0xFFFFFFFF
UINT16_MASK = 0xFFFF

SHINY_VALUE_COUNT = 8192
SID_LOW_VARIANTS_PER_SHINY_VALUE = 8

GRNG_VALUE_ADDR = 0x03005000
GSAVEBLOCK2_PTR_ADDR = 0x0300500C
PLAYER_TRAINER_ID_OFFSET = 0x000A
INITIAL_TID_MIRROR_ADDR = 0x02020000
TIMER1_COUNT_ADDR = 0x04000104
TIMER1_CONTROL_ADDR = 0x04000106
TIMER_ENABLE_MASK = 0x0080

SID_LEDGER_FORMAT = "frlg-sid-shiny-value-ledger-v1"

FRLG_BULBASAUR_SPECIES = 1
GEN3_SAVE_SECTOR_SIZE = 0x1000
GEN3_SAVE_SECTOR_USED = 0x0F80
GEN3_MAIN_SECTOR_COUNT = 14
GEN3_MAIN_SAVE_SIZE = GEN3_SAVE_SECTOR_SIZE * GEN3_MAIN_SECTOR_COUNT
GEN3_SECTOR_ID_OFFSET = 0x0FF4
GEN3_SECTOR_CHECKSUM_OFFSET = 0x0FF6
GEN3_SAVE_COUNTER_OFFSET = 0x0FFC
FRLG_LARGE_PARTY_COUNT_OFFSET = 0x0034
FRLG_LARGE_PARTY_OFFSET = 0x0038
PK3_STORED_SIZE = 80
PK3_PARTY_SIZE = 100
PK3_DATA_OFFSET = 0x20
PK3_DATA_SIZE = 48
PK3_BLOCK_SIZE = 12
PK3_CHECKSUM_OFFSET = 0x1C
PK3_EGG_FLAG_OFFSET = 0x13
PK3_EGG_FLAG = 0x04
PK3_BLOCK_POSITION = (
    0, 1, 2, 3,  0, 1, 3, 2,  0, 2, 1, 3,  0, 3, 1, 2,
    0, 2, 3, 1,  0, 3, 2, 1,  1, 0, 2, 3,  1, 0, 3, 2,
    2, 0, 1, 3,  3, 0, 1, 2,  2, 0, 3, 1,  3, 0, 2, 1,
    1, 2, 0, 3,  1, 3, 0, 2,  2, 1, 0, 3,  3, 1, 0, 2,
    2, 3, 0, 1,  3, 2, 0, 1,  1, 2, 3, 0,  1, 3, 2, 0,
    2, 1, 3, 0,  3, 1, 2, 0,  2, 3, 1, 0,  3, 2, 1, 0,
)
PK3_BLOCK_POSITION_INVERT = (
    0, 1, 2, 4, 3, 5, 6, 7, 12, 18, 13, 19,
    8, 10, 14, 20, 16, 22, 9, 11, 15, 21, 17, 23,
)


@dataclass(frozen=True)
class LcrngJump:
    """Affine transform for advancing the GBA LCRNG by a fixed step count."""

    multiplier: int
    increment: int

    def apply(self, state: int) -> int:
        """Apply this jump to one RNG state."""

        return ((int(state) & UINT32_MASK) * self.multiplier + self.increment) & UINT32_MASK


@dataclass(frozen=True)
class SidHitPlan:
    """One predicted branch from the pre-SID checkpoint to a missing shiny value."""

    shiny_value: int
    wait_frames: int
    rng_advance: int
    predicted_tid: int
    predicted_sid: int
    predicted_shiny_value: int
    predicted_rng: int
    branch_rng: int

    def to_json(self) -> dict[str, Any]:
        """Return stable JSON for the forecast file and ledger provenance."""

        return {
            "shiny_value": format_shiny_value(self.shiny_value),
            "wait_frames": self.wait_frames,
            "rng_advance": self.rng_advance,
            "predicted_tid": format_u16(self.predicted_tid),
            "predicted_sid": format_u16(self.predicted_sid),
            "predicted_shiny_value": format_shiny_value(self.predicted_shiny_value),
            "predicted_rng": format_u32(self.predicted_rng),
            "branch_rng": format_u32(self.branch_rng),
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SidHitPlan":
        """Parse a forecast row."""

        return cls(
            shiny_value=parse_int(data["shiny_value"]),
            wait_frames=int(data["wait_frames"]),
            rng_advance=int(data["rng_advance"]),
            predicted_tid=parse_int(data["predicted_tid"]),
            predicted_sid=parse_int(data["predicted_sid"]),
            predicted_shiny_value=parse_int(data["predicted_shiny_value"]),
            predicted_rng=parse_int(data["predicted_rng"]),
            branch_rng=parse_int(data["branch_rng"]),
        )


@dataclass(frozen=True)
class SidLedgerEntry:
    """One persisted SID/save result for a shiny value."""

    shiny_value: int
    done: bool = False
    tid: int | None = None
    sid: int | None = None
    save_path: str | None = None
    save_sha1: str | None = None
    wait_frames: int | None = None
    rng_advance: int | None = None
    predicted_sid: int | None = None
    predicted_shiny_value: int | None = None
    branch_rng: int | None = None
    predicted_rng: int | None = None
    sid_commit_offset: int | None = None
    note: str | None = None
    error: str | None = None

    def to_json(self) -> dict[str, Any]:
        """Return a compact ledger row."""

        row: dict[str, Any] = {
            "shiny_value": format_shiny_value(self.shiny_value),
            "done": bool(self.done),
        }
        optional: dict[str, Any] = {
            "tid": format_u16(self.tid) if self.tid is not None else None,
            "sid": format_u16(self.sid) if self.sid is not None else None,
            "save_path": self.save_path,
            "save_sha1": self.save_sha1,
            "wait_frames": self.wait_frames,
            "rng_advance": self.rng_advance,
            "predicted_sid": format_u16(self.predicted_sid)
            if self.predicted_sid is not None
            else None,
            "predicted_shiny_value": format_shiny_value(self.predicted_shiny_value)
            if self.predicted_shiny_value is not None
            else None,
            "branch_rng": format_u32(self.branch_rng) if self.branch_rng is not None else None,
            "predicted_rng": format_u32(self.predicted_rng)
            if self.predicted_rng is not None
            else None,
            "sid_commit_offset": self.sid_commit_offset,
            "note": self.note,
            "error": self.error,
        }
        row.update({key: value for key, value in optional.items() if value is not None})
        return row

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "SidLedgerEntry":
        """Parse one ledger row."""

        return cls(
            shiny_value=parse_int(data["shiny_value"]),
            done=bool(data.get("done", False)),
            tid=parse_optional_int(data.get("tid")),
            sid=parse_optional_int(data.get("sid")),
            save_path=data.get("save_path"),
            save_sha1=data.get("save_sha1"),
            wait_frames=data.get("wait_frames"),
            rng_advance=data.get("rng_advance"),
            predicted_sid=parse_optional_int(data.get("predicted_sid")),
            predicted_shiny_value=parse_optional_int(data.get("predicted_shiny_value")),
            branch_rng=parse_optional_int(data.get("branch_rng")),
            predicted_rng=parse_optional_int(data.get("predicted_rng")),
            sid_commit_offset=data.get("sid_commit_offset"),
            note=data.get("note"),
            error=data.get("error"),
        )


@dataclass(frozen=True)
class ExportedTsvSaveSummary:
    """Small FR/LG save proof used before accepting a SID bank export."""

    path: str
    active_slot: int
    tid: int
    sid: int
    party_count: int
    slot1_species: int | None
    slot1_tid: int | None
    slot1_sid: int | None
    slot1_checksum_valid: bool | None
    slot1_is_egg: bool | None


def parse_int(value: Any) -> int:
    """Parse an integer from decimal or ``0x`` text."""

    if isinstance(value, int):
        return value
    text = str(value).strip()
    return int(text, 16) if text.lower().startswith("0x") else int(text, 10)


def parse_optional_int(value: Any) -> int | None:
    """Parse an optional integer field."""

    if value is None:
        return None
    return parse_int(value)


def checked_u16(value: int, *, name: str) -> int:
    """Validate a 16-bit integer."""

    parsed = int(value)
    if not 0 <= parsed <= UINT16_MASK:
        raise ValueError(f"{name} must fit in 16 bits, got {value!r}")
    return parsed


def checked_shiny_value(value: int) -> int:
    """Validate a Gen 3 shiny value."""

    parsed = int(value)
    if not 0 <= parsed < SHINY_VALUE_COUNT:
        raise ValueError(f"shiny value must be in 0..{SHINY_VALUE_COUNT - 1}, got {value!r}")
    return parsed


def format_u16(value: int) -> str:
    """Format one 16-bit value."""

    return f"0x{int(value) & UINT16_MASK:04X}"


def format_u32(value: int) -> str:
    """Format one 32-bit value."""

    return f"0x{int(value) & UINT32_MASK:08X}"


def format_shiny_value(value: int) -> str:
    """Format a shiny value as ``0x0000`` through ``0x1FFF``."""

    return f"0x{int(value) & (SHINY_VALUE_COUNT - 1):04X}"


def lcrng_next_state(state: int) -> int:
    """Advance the GBA LCRNG one call."""

    return (int(state) * GBA_LCRNG_MULTIPLIER + GBA_LCRNG_INCREMENT) & UINT32_MASK


def lcrng_jump_for_steps(steps: int) -> LcrngJump:
    """Return the affine transform for ``steps`` GBA LCRNG calls.

    The LCRNG recurrence is ``state = state * A + C (mod 2^32)``. Composing
    that affine transform with exponentiation by squaring gives a reusable jump
    for fixed neutral-frame strides.
    """

    delta = int(steps)
    if delta < 0:
        raise ValueError("steps must be non-negative")

    acc_mult = 1
    acc_plus = 0
    cur_mult = GBA_LCRNG_MULTIPLIER
    cur_plus = GBA_LCRNG_INCREMENT
    while delta:
        if delta & 1:
            acc_mult = (acc_mult * cur_mult) & UINT32_MASK
            acc_plus = (acc_plus * cur_mult + cur_plus) & UINT32_MASK
        cur_plus = (cur_plus * (cur_mult + 1)) & UINT32_MASK
        cur_mult = (cur_mult * cur_mult) & UINT32_MASK
        delta >>= 1
    return LcrngJump(multiplier=acc_mult, increment=acc_plus)


def lcrng_advance(state: int, steps: int) -> int:
    """Advance the GBA LCRNG by ``steps`` calls in ``O(log steps)`` time."""

    return lcrng_jump_for_steps(steps).apply(state)


def random_u16_from_state(state: int) -> int:
    """Return the ``Random()`` value represented by an advanced RNG state."""

    return (int(state) >> 16) & UINT16_MASK


def shiny_value_from_tid_sid(tid: int, sid: int) -> int:
    """Return ``(TID ^ SID) >> 3``."""

    return ((int(tid) & UINT16_MASK) ^ (int(sid) & UINT16_MASK)) >> 3


def acceptable_sids_for_shiny_value(tid: int, shiny_value: int) -> tuple[int, ...]:
    """Return all eight SIDs that produce one shiny value for ``tid``."""

    shiny = checked_shiny_value(shiny_value)
    tid16 = int(tid) & UINT16_MASK
    base = shiny << 3
    return tuple((tid16 ^ (base | low3)) & UINT16_MASK for low3 in range(8))


def build_missing_shiny_forecast(
    *,
    tid: int,
    branch_rng: int,
    missing_shiny_values: Iterable[int],
    sid_commit_offset: int = 1,
    rng_advances_per_neutral_frame: int = 1,
    min_wait_frames: int = 0,
    max_advances: int | None = None,
) -> list[SidHitPlan]:
    """Predict earliest waits that hit each missing shiny value.

    This is the SID bot's non-brute-force core: it scans the mathematical
    LCRNG stream, not emulator branches, and returns one planned emulator
    attempt per missing shiny value.
    """

    if int(sid_commit_offset) < 0:
        raise ValueError("sid_commit_offset must be non-negative")
    stride = int(rng_advances_per_neutral_frame)
    if stride < 1:
        raise ValueError("rng_advances_per_neutral_frame must be at least 1")
    min_wait = int(min_wait_frames)
    if min_wait < 0:
        raise ValueError("min_wait_frames must be non-negative")

    missing = {checked_shiny_value(value) for value in missing_shiny_values}
    if not missing:
        return []

    seen: dict[int, SidHitPlan] = {}
    tid16 = checked_u16(tid, name="tid")
    start = int(branch_rng) & UINT32_MASK
    wait_frames = min_wait
    rng_advance = int(sid_commit_offset) + (min_wait * stride)
    rng_value = lcrng_advance(start, rng_advance)
    stride_jump = lcrng_jump_for_steps(stride)

    while len(seen) < len(missing):
        if max_advances is not None and rng_advance > int(max_advances):
            remaining = len(missing) - len(seen)
            raise RuntimeError(
                f"SID forecast stopped at advance {rng_advance}; "
                f"{remaining} shiny value(s) still missing"
            )

        sid = random_u16_from_state(rng_value)
        shiny = shiny_value_from_tid_sid(tid16, sid)
        if shiny in missing and shiny not in seen:
            seen[shiny] = SidHitPlan(
                shiny_value=shiny,
                wait_frames=wait_frames,
                rng_advance=rng_advance,
                predicted_tid=tid16,
                predicted_sid=sid,
                predicted_shiny_value=shiny,
                predicted_rng=rng_value,
                branch_rng=start,
            )

        wait_frames += 1
        rng_advance += stride
        if len(seen) < len(missing):
            rng_value = stride_jump.apply(rng_value)

    return sorted(seen.values(), key=lambda entry: (entry.wait_frames, entry.shiny_value))


def infer_sid_commit_offset(
    *,
    branch_rng: int,
    wait_frames: int,
    observed_sid: int,
    rng_advances_per_neutral_frame: int,
    expected_sid_commit_offset: int,
    search_radius: int,
) -> int | None:
    """Infer the SID commit offset from one observed SID.

    The search stays near the expected advance so repeated high-16 collisions
    in the LCRNG do not produce far-away calibration jumps.
    """

    stride = int(rng_advances_per_neutral_frame)
    if stride < 1:
        raise ValueError("rng_advances_per_neutral_frame must be at least 1")
    radius = max(0, int(search_radius))
    base_advance = int(wait_frames) * stride
    expected_advance = base_advance + int(expected_sid_commit_offset)
    low = max(base_advance, expected_advance - radius)
    high = expected_advance + radius
    observed = int(observed_sid) & UINT16_MASK

    candidates: list[tuple[int, int]] = []
    state = lcrng_advance(branch_rng, low)
    for advance in range(low, high + 1):
        if random_u16_from_state(state) == observed:
            candidates.append((abs(advance - expected_advance), advance))
        state = lcrng_next_state(state)
    if not candidates:
        return None
    _, matched_advance = min(candidates)
    return matched_advance - base_advance


def parse_shiny_selection(values: Iterable[str]) -> list[int]:
    """Parse repeated shiny-value selectors such as ``0x10`` or ``0x20-0x2F``."""

    selected: set[int] = set()
    for raw in values:
        text = str(raw).strip()
        if not text:
            continue
        if "-" in text:
            start_text, end_text = text.split("-", 1)
            start = parse_int(start_text)
            end = parse_int(end_text)
            if end < start:
                raise ValueError(f"shiny-value range is backwards: {text}")
            selected.update(range(start, end + 1))
        else:
            selected.add(parse_int(text))
    return sorted(checked_shiny_value(value) for value in selected)


def new_sid_ledger(
    *,
    target_tid: int,
    shiny_values: Sequence[int],
    branch_rng: int,
    sid_commit_offset: int,
    rng_advances_per_neutral_frame: int,
) -> dict[str, Any]:
    """Create an empty SID ledger."""

    rows = [SidLedgerEntry(shiny_value=checked_shiny_value(value)).to_json() for value in shiny_values]
    return {
        "format": SID_LEDGER_FORMAT,
        "target_tid": format_u16(target_tid),
        "branch_rng": format_u32(branch_rng),
        "sid_commit_offset": int(sid_commit_offset),
        "rng_advances_per_neutral_frame": int(rng_advances_per_neutral_frame),
        "target_shiny_values": len(rows),
        "complete_shiny_values": 0,
        "entries": rows,
    }


def ledger_entries_by_shiny_value(ledger: Mapping[str, Any]) -> dict[int, SidLedgerEntry]:
    """Return ledger rows keyed by shiny value."""

    return {
        entry.shiny_value: entry
        for entry in (SidLedgerEntry.from_json(row) for row in ledger.get("entries", []))
    }


def missing_shiny_values(ledger: Mapping[str, Any]) -> list[int]:
    """Return incomplete shiny values from a ledger."""

    entries = ledger_entries_by_shiny_value(ledger)
    return [value for value in sorted(entries) if not entries[value].done]


def mark_ledger_hit(
    ledger: dict[str, Any],
    *,
    shiny_value: int,
    tid: int,
    sid: int,
    save_path: str | os.PathLike[str],
    save_sha1: str | None,
    plan: SidHitPlan,
    sid_commit_offset: int,
    note: str | None = None,
) -> None:
    """Mark one shiny value complete."""

    entries = ledger_entries_by_shiny_value(ledger)
    shiny = checked_shiny_value(shiny_value)
    if shiny not in entries:
        raise KeyError(f"ledger has no shiny value {format_shiny_value(shiny)}")
    entries[shiny] = SidLedgerEntry(
        shiny_value=shiny,
        done=True,
        tid=tid,
        sid=sid,
        save_path=str(save_path),
        save_sha1=save_sha1,
        wait_frames=plan.wait_frames,
        rng_advance=plan.rng_advance,
        predicted_sid=plan.predicted_sid,
        predicted_shiny_value=plan.predicted_shiny_value,
        branch_rng=plan.branch_rng,
        predicted_rng=plan.predicted_rng,
        sid_commit_offset=sid_commit_offset,
        note=note,
    )
    _rewrite_ledger_entries(ledger, entries)


def mark_ledger_error(ledger: dict[str, Any], *, shiny_value: int, error: str) -> None:
    """Record a per-shiny-value error and make the row retryable."""

    entries = ledger_entries_by_shiny_value(ledger)
    shiny = checked_shiny_value(shiny_value)
    if shiny not in entries:
        raise KeyError(f"ledger has no shiny value {format_shiny_value(shiny)}")
    old = entries[shiny]
    entries[shiny] = SidLedgerEntry(
        shiny_value=shiny,
        done=False,
        tid=old.tid,
        sid=old.sid,
        save_path=old.save_path,
        save_sha1=old.save_sha1,
        wait_frames=old.wait_frames,
        rng_advance=old.rng_advance,
        predicted_sid=old.predicted_sid,
        predicted_shiny_value=old.predicted_shiny_value,
        branch_rng=old.branch_rng,
        predicted_rng=old.predicted_rng,
        sid_commit_offset=old.sid_commit_offset,
        note=old.note,
        error=str(error),
    )
    _rewrite_ledger_entries(ledger, entries)


def reset_missing_save_hits_for_resume(ledger: dict[str, Any]) -> int:
    """Repair or clear completed rows whose exported save is not trustworthy.

    Resume must trust the ledger only when the referenced save exists and its
    FR/LG trainer, party slot 1, and filename SID proof still match the row.
    ID-only stale exports are patched in place and keep the row complete. Rows
    that cannot be repaired retain previous SID/path details as audit
    breadcrumbs and become retryable.
    """

    entries = ledger_entries_by_shiny_value(ledger)
    changed_count = 0
    for shiny, old in list(entries.items()):
        if not old.done:
            continue
        reset_error: str | None = None
        repaired_entry: SidLedgerEntry | None = None
        if not old.save_path or not Path(old.save_path).is_file():
            reset_error = "completed ledger row reset on resume because save file is missing"
        elif old.tid is None or old.sid is None:
            reset_error = "completed ledger row reset on resume because ledger TID/SID is missing"
        else:
            try:
                validate_frlg_tsv_save(
                    Path(old.save_path),
                    expected_tid=old.tid,
                    expected_sid=old.sid,
                )
            except Exception as exc:  # noqa: BLE001 - keep resume audit text.
                try:
                    patch_frlg_tsv_save_ids(
                        Path(old.save_path),
                        expected_tid=old.tid,
                        expected_sid=old.sid,
                    )
                except Exception as repair_exc:  # noqa: BLE001 - keep resume audit text.
                    reset_error = (
                        "completed ledger row reset on resume because save "
                        f"verification failed: {exc}; repair failed: {repair_exc}"
                    )
                else:
                    repaired_entry = SidLedgerEntry(
                        shiny_value=shiny,
                        done=True,
                        tid=old.tid,
                        sid=old.sid,
                        save_path=old.save_path,
                        save_sha1=sha1_file(Path(old.save_path)),
                        wait_frames=old.wait_frames,
                        rng_advance=old.rng_advance,
                        predicted_sid=old.predicted_sid,
                        predicted_shiny_value=old.predicted_shiny_value,
                        branch_rng=old.branch_rng,
                        predicted_rng=old.predicted_rng,
                        sid_commit_offset=old.sid_commit_offset,
                        note=old.note or "exported save IDs repaired on resume",
                    )
        if repaired_entry is not None:
            entries[shiny] = repaired_entry
            changed_count += 1
            continue
        if reset_error is None:
            continue
        entries[shiny] = SidLedgerEntry(
            shiny_value=shiny,
            done=False,
            tid=old.tid,
            sid=old.sid,
            save_path=old.save_path,
            save_sha1=old.save_sha1,
            wait_frames=old.wait_frames,
            rng_advance=old.rng_advance,
            predicted_sid=old.predicted_sid,
            predicted_shiny_value=old.predicted_shiny_value,
            branch_rng=old.branch_rng,
            predicted_rng=old.predicted_rng,
            sid_commit_offset=old.sid_commit_offset,
            note=old.note,
            error=reset_error,
        )
        changed_count += 1
    if changed_count:
        _rewrite_ledger_entries(ledger, entries)
    return changed_count


def _rewrite_ledger_entries(
    ledger: dict[str, Any],
    entries: Mapping[int, SidLedgerEntry],
) -> None:
    """Rewrite sorted ledger rows and counters."""

    rows = [entries[value].to_json() for value in sorted(entries)]
    ledger["entries"] = rows
    ledger["complete_shiny_values"] = sum(1 for row in rows if row.get("done"))
    ledger["target_shiny_values"] = len(rows)


def ledger_summary(ledger: Mapping[str, Any]) -> dict[str, int]:
    """Return small ledger counters."""

    rows = list(ledger.get("entries", []))
    return {
        "complete_shiny_values": sum(1 for row in rows if row.get("done")),
        "target_shiny_values": len(rows),
        "errors": sum(1 for row in rows if row.get("error")),
    }


def write_json_atomic(
    path: Path,
    data: Mapping[str, Any],
    *,
    replace_attempts: int = 25,
    retry_delay_seconds: float = 0.04,
) -> None:
    """Write JSON via a temporary file then atomic replace.

    Windows can briefly deny ``os.replace`` while the Flask tracker, verifier,
    antivirus, or a browser-triggered scan has the old JSON open. Retry the
    replace so those short read locks do not stop a long SID run.
    """

    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    attempts = max(1, int(replace_attempts))
    for attempt in range(1, attempts + 1):
        try:
            tmp.replace(path)
            return
        except PermissionError:
            if attempt >= attempts:
                raise
            time.sleep(max(0.0, float(retry_delay_seconds)))


def read_json(path: Path) -> dict[str, Any]:
    """Read a JSON object."""

    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{path} did not contain a JSON object")
    return data


def sha1_file(path: Path) -> str:
    """Return a file SHA-1 digest."""

    digest = hashlib.sha1()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest().upper()


def parse_frlg_tsv_save(path: Path) -> ExportedTsvSaveSummary:
    """Parse the small proof fields needed from a FR/LG battery save.

    This is intentionally narrow. The full standalone verifier uses PKHeX.Core;
    the live bot only needs enough local structure knowledge to reject a stale
    or mismatched SRAM export before it is accepted into the ledger.
    """

    path = Path(path)
    data = path.read_bytes()
    active_slot = active_gen3_main_save_slot(data)
    small, large = read_gen3_main_save_blocks(data, active_slot)
    tid = read_u16_le(small, PLAYER_TRAINER_ID_OFFSET)
    sid = read_u16_le(small, PLAYER_TRAINER_ID_OFFSET + 2)
    party_count = large[FRLG_LARGE_PARTY_COUNT_OFFSET]

    slot1_species: int | None = None
    slot1_tid: int | None = None
    slot1_sid: int | None = None
    slot1_checksum_valid: bool | None = None
    slot1_is_egg: bool | None = None
    if party_count >= 1:
        slot = bytes(
            large[
                FRLG_LARGE_PARTY_OFFSET : FRLG_LARGE_PARTY_OFFSET + PK3_PARTY_SIZE
            ]
        )
        pk = decrypt_pk3_if_needed(slot)
        slot1_species = read_u16_le(pk, PK3_DATA_OFFSET)
        slot1_tid = read_u16_le(pk, 0x04)
        slot1_sid = read_u16_le(pk, 0x06)
        slot1_checksum_valid = pk3_checksum(pk) == read_u16_le(pk, PK3_CHECKSUM_OFFSET)
        slot1_is_egg = bool(pk[PK3_EGG_FLAG_OFFSET] & PK3_EGG_FLAG)

    return ExportedTsvSaveSummary(
        path=str(path),
        active_slot=active_slot,
        tid=tid,
        sid=sid,
        party_count=party_count,
        slot1_species=slot1_species,
        slot1_tid=slot1_tid,
        slot1_sid=slot1_sid,
        slot1_checksum_valid=slot1_checksum_valid,
        slot1_is_egg=slot1_is_egg,
    )


def validate_frlg_tsv_save(
    path: Path,
    *,
    expected_tid: int,
    expected_sid: int,
    expected_species: int = FRLG_BULBASAUR_SPECIES,
) -> ExportedTsvSaveSummary:
    """Raise if an exported FR/LG save does not match the intended TSV row."""

    proof = parse_frlg_tsv_save(path)
    issues: list[str] = []
    expected_tid &= UINT16_MASK
    expected_sid &= UINT16_MASK
    if proof.tid != expected_tid:
        issues.append(f"save TID {format_u16(proof.tid)} != {format_u16(expected_tid)}")
    if proof.sid != expected_sid:
        issues.append(f"save SID {format_u16(proof.sid)} != {format_u16(expected_sid)}")
    if proof.party_count < 1:
        issues.append("party slot 1 missing")
    if proof.slot1_species != expected_species:
        issues.append(f"slot 1 species {proof.slot1_species} != {expected_species}")
    if proof.slot1_tid != expected_tid:
        issues.append(
            f"slot 1 TID {format_u16(proof.slot1_tid or 0)} != {format_u16(expected_tid)}"
        )
    if proof.slot1_sid != expected_sid:
        issues.append(
            f"slot 1 SID {format_u16(proof.slot1_sid or 0)} != {format_u16(expected_sid)}"
        )
    if proof.slot1_checksum_valid is not True:
        issues.append("slot 1 Pokemon checksum invalid")
    if proof.slot1_is_egg:
        issues.append("slot 1 is still an egg")
    if issues:
        raise RuntimeError(f"exported save verification failed for {path}: {', '.join(issues)}")
    return proof


def patch_frlg_tsv_save_ids(
    path: Path,
    *,
    expected_tid: int,
    expected_sid: int,
    expected_species: int = FRLG_BULBASAUR_SPECIES,
) -> ExportedTsvSaveSummary:
    """Patch exported FR/LG save IDs to the observed hit, then validate.

    This edits only the exported battery-save file. It does not write emulator
    WRAM or live SaveBlock memory. The exported file may be stale SRAM from a
    previous in-game save; patching keeps the stable route state while making
    the save-level trainer ID and party-slot-1 owner match the SID hit that the
    bot just observed in live RAM.
    """

    path = Path(path)
    data = bytearray(path.read_bytes())
    active_slot = active_gen3_main_save_slot(bytes(data))
    offsets = gen3_main_sector_offsets(bytes(data), active_slot)
    patch_frlg_tsv_save_data(
        data,
        offsets=offsets,
        expected_tid=expected_tid,
        expected_sid=expected_sid,
        expected_species=expected_species,
    )
    for sector_offset in offsets.values():
        set_gen3_sector_checksum(data, sector_offset)
    path.write_bytes(data)
    return validate_frlg_tsv_save(
        path,
        expected_tid=expected_tid,
        expected_sid=expected_sid,
        expected_species=expected_species,
    )


def patch_frlg_tsv_save_data(
    data: bytearray,
    *,
    offsets: Mapping[int, int],
    expected_tid: int,
    expected_sid: int,
    expected_species: int,
) -> None:
    """Patch trainer and party slot IDs inside one assembled FR/LG save slot."""

    expected_tid &= UINT16_MASK
    expected_sid &= UINT16_MASK
    small_offset = offsets[0]
    large0_offset = offsets[1]
    write_u16_le(data, small_offset + PLAYER_TRAINER_ID_OFFSET, expected_tid)
    write_u16_le(data, small_offset + PLAYER_TRAINER_ID_OFFSET + 2, expected_sid)

    party_count = data[large0_offset + FRLG_LARGE_PARTY_COUNT_OFFSET]
    if party_count < 1:
        raise RuntimeError("cannot patch exported save: party slot 1 missing")

    slot_offset = large0_offset + FRLG_LARGE_PARTY_OFFSET
    slot = bytes(data[slot_offset : slot_offset + PK3_PARTY_SIZE])
    patched = patch_pk3_owner_ids(
        slot,
        expected_tid=expected_tid,
        expected_sid=expected_sid,
        expected_species=expected_species,
    )
    data[slot_offset : slot_offset + PK3_PARTY_SIZE] = patched


def patch_pk3_owner_ids(
    record: bytes,
    *,
    expected_tid: int,
    expected_sid: int,
    expected_species: int,
) -> bytes:
    """Return a Gen 3 party Pokemon with owner IDs rewritten and re-encrypted."""

    if len(record) < PK3_PARTY_SIZE:
        raise ValueError("PK3 party record is shorter than 100 bytes")
    decrypted = bytearray(decrypt_pk3_if_needed(record))
    species = read_u16_le(decrypted, PK3_DATA_OFFSET)
    if species != expected_species:
        raise RuntimeError(
            f"cannot patch exported save: slot 1 species {species} != {expected_species}"
        )
    write_u16_le(decrypted, 0x04, expected_tid)
    write_u16_le(decrypted, 0x06, expected_sid)
    write_u16_le(decrypted, PK3_CHECKSUM_OFFSET, pk3_checksum(decrypted))
    encrypted = encrypt_pk3_decrypted(decrypted)
    return encrypted + bytes(record[PK3_STORED_SIZE:PK3_PARTY_SIZE])


def active_gen3_main_save_slot(data: bytes) -> int:
    """Return active Gen 3 main save slot index, matching PKHeX slot choice."""

    slot0_ok, sector0_a = gen3_main_sectors_present(data, 0)
    slot1_ok, sector0_b = gen3_main_sectors_present(data, 1)
    if slot0_ok and not slot1_ok:
        return 0
    if slot1_ok and not slot0_ok:
        return 1
    if not slot0_ok and not slot1_ok:
        raise ValueError("save does not contain a complete Gen 3 main save slot")

    counter0 = read_u32_le(data, sector0_a + GEN3_SAVE_COUNTER_OFFSET)
    counter1 = read_u32_le(data, sector0_b + GEN3_SAVE_COUNTER_OFFSET)
    if counter0 == UINT32_MASK and counter1 != UINT32_MASK - 1:
        return 1
    if counter1 == UINT32_MASK and counter0 != UINT32_MASK - 1:
        return 0
    return 0 if counter0 >= counter1 else 1


def gen3_main_sectors_present(data: bytes, slot: int) -> tuple[bool, int]:
    """Return whether all 14 main sectors exist and where sector 0 lives."""

    start = GEN3_MAIN_SAVE_SIZE * int(slot)
    end = start + GEN3_MAIN_SAVE_SIZE
    if len(data) < end:
        return False, 0
    seen = 0
    sector0 = 0
    for offset in range(start, end, GEN3_SAVE_SECTOR_SIZE):
        sector_id = read_u16_le(data, offset + GEN3_SECTOR_ID_OFFSET)
        if sector_id >= GEN3_MAIN_SECTOR_COUNT:
            return False, 0
        seen |= 1 << sector_id
        if sector_id == 0:
            sector0 = offset
    return seen == ((1 << GEN3_MAIN_SECTOR_COUNT) - 1), sector0


def gen3_main_sector_offsets(data: bytes, slot: int) -> dict[int, int]:
    """Return sector ID to file offset for one complete Gen 3 main save slot."""

    start = GEN3_MAIN_SAVE_SIZE * int(slot)
    end = start + GEN3_MAIN_SAVE_SIZE
    if len(data) < end:
        raise ValueError("save is shorter than one Gen 3 main slot")
    offsets: dict[int, int] = {}
    for offset in range(start, end, GEN3_SAVE_SECTOR_SIZE):
        sector_id = read_u16_le(data, offset + GEN3_SECTOR_ID_OFFSET)
        if sector_id >= GEN3_MAIN_SECTOR_COUNT:
            raise ValueError(f"invalid Gen 3 sector id {sector_id}")
        offsets[sector_id] = offset
    if len(offsets) != GEN3_MAIN_SECTOR_COUNT:
        raise ValueError("save does not contain all Gen 3 main sectors")
    return offsets


def read_gen3_main_save_blocks(data: bytes, slot: int) -> tuple[bytes, bytes]:
    """Assemble FR/LG small and large save blocks from Gen 3 sectors."""

    start = GEN3_MAIN_SAVE_SIZE * int(slot)
    end = start + GEN3_MAIN_SAVE_SIZE
    small = bytearray(GEN3_SAVE_SECTOR_USED)
    large = bytearray(4 * GEN3_SAVE_SECTOR_USED)
    for offset in range(start, end, GEN3_SAVE_SECTOR_SIZE):
        sector_id = read_u16_le(data, offset + GEN3_SECTOR_ID_OFFSET)
        chunk = data[offset : offset + GEN3_SAVE_SECTOR_USED]
        if sector_id == 0:
            small[:] = chunk
        elif 1 <= sector_id <= 4:
            large_offset = (sector_id - 1) * GEN3_SAVE_SECTOR_USED
            large[large_offset : large_offset + GEN3_SAVE_SECTOR_USED] = chunk
    return bytes(small), bytes(large)


def decrypt_pk3_if_needed(record: bytes) -> bytes:
    """Return decrypted first 80 bytes from a Gen 3 party/stored Pokemon."""

    if len(record) < PK3_STORED_SIZE:
        raise ValueError("PK3 record is shorter than 80 bytes")
    data = bytearray(record[:PK3_STORED_SIZE])
    expected = read_u16_le(data, PK3_CHECKSUM_OFFSET)
    if pk3_checksum(data) == expected:
        return bytes(data)

    seed = read_u32_le(data, 0x00) ^ read_u32_le(data, 0x04)
    for offset in range(PK3_DATA_OFFSET, PK3_STORED_SIZE, 4):
        write_u32_le(data, offset, read_u32_le(data, offset) ^ seed)
    shuffle_pk3_blocks(data, read_u32_le(data, 0x00) % 24)
    return bytes(data)


def encrypt_pk3_decrypted(record: bytes | bytearray) -> bytes:
    """Encrypt the first 80 bytes of a decrypted Gen 3 Pokemon record."""

    if len(record) < PK3_STORED_SIZE:
        raise ValueError("PK3 record is shorter than 80 bytes")
    data = bytearray(record[:PK3_STORED_SIZE])
    pid = read_u32_le(data, 0x00)
    seed = pid ^ read_u32_le(data, 0x04)
    shuffle_pk3_blocks(data, PK3_BLOCK_POSITION_INVERT[pid % 24])
    for offset in range(PK3_DATA_OFFSET, PK3_STORED_SIZE, 4):
        write_u32_le(data, offset, read_u32_le(data, offset) ^ seed)
    return bytes(data)


def shuffle_pk3_blocks(data: bytearray, shuffle_value: int) -> None:
    """Apply PKHeX's Gen 3 unshuffle operation to the encrypted data region."""

    if int(shuffle_value) == 0:
        return
    order = PK3_BLOCK_POSITION[int(shuffle_value) * 4 : int(shuffle_value) * 4 + 4]
    blocks = [
        bytes(
            data[
                PK3_DATA_OFFSET + index * PK3_BLOCK_SIZE :
                PK3_DATA_OFFSET + (index + 1) * PK3_BLOCK_SIZE
            ]
        )
        for index in range(4)
    ]
    perm = [0, 1, 2, 3]
    slot_of = [0, 1, 2, 3]
    for index in range(3):
        desired = order[index]
        other = slot_of[desired]
        if other == index:
            continue
        blocks[index], blocks[other] = blocks[other], blocks[index]
        block_at_index = perm[index]
        perm[other] = block_at_index
        slot_of[block_at_index] = other
    for index, block in enumerate(blocks):
        start = PK3_DATA_OFFSET + index * PK3_BLOCK_SIZE
        data[start : start + PK3_BLOCK_SIZE] = block


def pk3_checksum(record: bytes) -> int:
    """Return Gen 3 Pokemon checksum for the decrypted data region."""

    checksum = 0
    for offset in range(PK3_DATA_OFFSET, PK3_STORED_SIZE, 2):
        checksum = (checksum + read_u16_le(record, offset)) & UINT16_MASK
    return checksum


def gen3_sector_checksum(sector: bytes | bytearray) -> int:
    """Return Gen 3 sector checksum for the first 0xF80 bytes."""

    checksum = 0
    for offset in range(0, GEN3_SAVE_SECTOR_USED, 4):
        checksum = (checksum + read_u32_le(sector, offset)) & UINT32_MASK
    return (checksum + (checksum >> 16)) & UINT16_MASK


def set_gen3_sector_checksum(data: bytearray, sector_offset: int) -> None:
    """Recompute one Gen 3 main-sector checksum."""

    checksum = gen3_sector_checksum(
        data[int(sector_offset) : int(sector_offset) + GEN3_SAVE_SECTOR_USED]
    )
    write_u16_le(data, int(sector_offset) + GEN3_SECTOR_CHECKSUM_OFFSET, checksum)


def read_u16_le(data: bytes | bytearray, offset: int) -> int:
    """Read one little-endian unsigned 16-bit value."""

    return int.from_bytes(data[int(offset) : int(offset) + 2], "little")


def read_u32_le(data: bytes | bytearray, offset: int) -> int:
    """Read one little-endian unsigned 32-bit value."""

    return int.from_bytes(data[int(offset) : int(offset) + 4], "little")


def write_u32_le(data: bytearray, offset: int, value: int) -> None:
    """Write one little-endian unsigned 32-bit value."""

    data[int(offset) : int(offset) + 4] = (int(value) & UINT32_MASK).to_bytes(4, "little")


def write_u16_le(data: bytearray, offset: int, value: int) -> None:
    """Write one little-endian unsigned 16-bit value."""

    data[int(offset) : int(offset) + 2] = (int(value) & UINT16_MASK).to_bytes(2, "little")


def read_rng_state(core: Any, *, address: int = GRNG_VALUE_ADDR) -> int:
    """Read ``gRngValue`` from a core-like object."""

    return int(core.memory.u32[int(address)]) & UINT32_MASK


def read_initial_tid_mirror(core: Any, *, address: int = INITIAL_TID_MIRROR_ADDR) -> int:
    """Read the temporary TID mirror written at player-name exit."""

    return int(core.memory.u16[int(address)]) & UINT16_MASK


def read_timer1_low(core: Any, *, address: int = TIMER1_COUNT_ADDR) -> int:
    """Read Timer 1 low."""

    return int(core.memory.u16[int(address)]) & UINT16_MASK


def read_timer1_control(core: Any, *, address: int = TIMER1_CONTROL_ADDR) -> int:
    """Read Timer 1 control."""

    return int(core.memory.u16[int(address)]) & UINT16_MASK


def timer1_running(core: Any) -> bool:
    """Return whether Timer 1 is enabled."""

    return bool(read_timer1_control(core) & TIMER_ENABLE_MASK)


def read_trainer_id_from_saveblock2(
    core: Any,
    *,
    pointer_address: int = GSAVEBLOCK2_PTR_ADDR,
    trainer_id_offset: int = PLAYER_TRAINER_ID_OFFSET,
) -> tuple[int, int]:
    """Read final TID/SID from FR/LG ``gSaveBlock2Ptr->playerTrainerId``."""

    pointer = int(core.memory.u32[int(pointer_address)]) & UINT32_MASK
    if pointer == 0:
        raise RuntimeError("gSaveBlock2Ptr is zero; trainer ID is not readable yet")
    tid = int(core.memory.u16[pointer + int(trainer_id_offset)]) & UINT16_MASK
    sid = int(core.memory.u16[pointer + int(trainer_id_offset) + 2]) & UINT16_MASK
    return tid, sid


def plan_digest(plan: Iterable[SidHitPlan]) -> str:
    """Return a short digest for a forecast."""

    digest = hashlib.sha256()
    for entry in plan:
        digest.update(json.dumps(entry.to_json(), sort_keys=True).encode("utf-8"))
        digest.update(b"\n")
    return digest.hexdigest().upper()
