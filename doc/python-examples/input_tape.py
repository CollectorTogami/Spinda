r"""Record and replay simple frame-accurate input tapes.

Usage:
    <repo-root>\.venv-mgba\bin\python.exe input_tape.py
    <repo-root>\.venv-mgba\bin\python.exe input_tape.py C:\path\to\game.gba --output route.inputtape.json --replay
    <repo-root>\.venv-mgba\bin\python.exe input_tape.py --input route.inputtape.json C:\path\to\game.gba

What this is:
- a Python helper for the shared mGBA `mgba-input-tape-v1` route-tape format
- agnostic of ROM paths, save files, and savestates in the tape itself
- safe to import from project scripts that already control their own anchor state
- able to sample mapped Qt frontend GBA buttons, including keyboard bindings
  and the Custom Features Virtual Pad
- exposes Lua-parity helper names such as `fromRuns`, `load`, `replay`, and
  `recordCurrentKeys` in addition to the normal snake_case Python helpers

What this is not:
- a full VBA-RR-style movie format
- a recorder for raw host key names; it records only resolved GBA button masks

The important determinism rule is that every replayed frame gets one explicit
raw key mask. The helper clears keys before and after the tape so stale Qt or
script-owned input cannot leak into the next route segment. Starting and
stopping a tape ensures the visible Qt core is paused, but it skips that call
when the core already exposes itself as paused. Qt input capture samples the
already-mapped GBA button mask once per emulated frame and writes those samples
into the same anchor-agnostic tape format.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence


TAPE_FORMAT = "mgba-input-tape-v1"
FORMAT = TAPE_FORMAT
KEYINPUT_ADDR = 0x04000130
GBA_BUTTON_MASK = 0x03FF
DEFAULT_PLAN = "A:2,none:2,START:2,none:2"
DEFAULT_ROM = Path(__file__).resolve().parents[2] / "cinema" / "gba" / "irq" / "keyirq" / "test.gba"
DEFAULT_FORMAT_NOTE = "Anchor-agnostic tape: no ROM, save, or savestate path is stored."

# These are the raw mGBA/GBA key-mask bits used by core.set_keys(raw=...).
# Keeping the table local makes the tape usable without importing mGBA first.
BUTTON_BITS: dict[str, int] = {
    "A": 0,
    "B": 1,
    "SELECT": 2,
    "START": 3,
    "RIGHT": 4,
    "LEFT": 5,
    "UP": 6,
    "DOWN": 7,
    "R": 8,
    "L": 9,
}
BUTTON_ORDER: tuple[str, ...] = tuple(BUTTON_BITS)
KNOWN_BUTTON_MASK = sum(1 << bit for bit in BUTTON_BITS.values())
BUTTON_ALIASES: dict[str, str] = {
    "SEL": "SELECT",
    "RETURN": "START",
    "ENTER": "START",
    "NONE": "NONE",
    "NEUTRAL": "NONE",
    "NOINPUT": "NONE",
    "NO_INPUT": "NONE",
    ".": "NONE",
    "-": "NONE",
    "0": "NONE",
}


def require_non_empty_runs(runs: Sequence[InputRun], *, context: str = "input tape") -> None:
    """Reject empty exchange tapes to match the native Qt and Lua loaders."""
    if not runs:
        raise ValueError(f"{context} has no runs")


def checked_positive_frame_count(count: int) -> int:
    """Validate a strictly positive frame count."""
    frames = checked_frame_count(count)
    if frames < 1:
        raise ValueError("frame count must be positive")
    return frames


def default_metadata(*, created_by: str) -> dict[str, Any]:
    """Return the shared default metadata for this workspace tape format."""
    return {
        "created_by": created_by,
        "format_note": DEFAULT_FORMAT_NOTE,
    }


def merge_metadata(
    metadata: Mapping[str, Any] | None,
    *,
    created_by: str,
) -> dict[str, Any]:
    """Merge caller metadata with the shared tape-format defaults."""
    merged = default_metadata(created_by=created_by)
    merged.update(dict(metadata or {}))
    return merged


@dataclass(frozen=True)
class InputRun:
    """One run-length encoded button mask."""

    mask: int
    frames: int

    def __post_init__(self) -> None:
        if self.mask < 0 or self.mask > 0xFFFFFFFF:
            raise ValueError(f"mask must fit in uint32, got {self.mask!r}")
        object.__setattr__(self, "mask", int(self.mask) & GBA_BUTTON_MASK)
        if self.frames <= 0:
            raise ValueError(f"frames must be positive, got {self.frames!r}")

    def to_json(self) -> dict[str, Any]:
        """Return a readable JSON object for this run."""
        return {
            "mask": format_mask(self.mask),
            "buttons": mask_to_button_names(self.mask),
            "frames": self.frames,
        }

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "InputRun":
        """Parse one run from a tape JSON object."""
        if "mask" not in data:
            buttons = data.get("buttons")
            if buttons is None:
                raise ValueError("input run needs either 'mask' or 'buttons'")
            mask = mask_from_buttons(buttons)
        else:
            mask = parse_mask(data["mask"])
        return cls(mask=mask, frames=int(data["frames"]))


@dataclass
class InputTape:
    """A small, anchor-agnostic input tape.

    The tape intentionally stores no ROM/save/savestate path. Higher-level
    Spinda scripts should load or restore their own known-good state, then pass
    the live core here to replay the route segment.
    """

    runs: list[InputRun]
    metadata: dict[str, Any] = field(default_factory=dict)
    start_probe: dict[str, Any] = field(default_factory=dict)
    end_probe: dict[str, Any] = field(default_factory=dict)
    _frame_count: int = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self.runs = [run if isinstance(run, InputRun) else InputRun.from_json(run) for run in self.runs]
        # Tapes are treated as immutable snapshots after construction. Cache
        # the total once here so replay/status code does not rescan the full
        # RLE run list every time it needs a frame count.
        self._frame_count = sum(run.frames for run in self.runs)

    @property
    def frame_count(self) -> int:
        """Total emulated frames covered by this tape."""
        return self._frame_count

    def expand(self) -> list[int]:
        """Expand the RLE tape into one raw key mask per frame."""
        return [run.mask for run in self.runs for _ in range(run.frames)]

    def to_json(self) -> dict[str, Any]:
        """Serialize the tape to a stable, human-readable JSON object."""
        require_non_empty_runs(self.runs)
        return {
            "format": TAPE_FORMAT,
            # Match the native Qt and Lua helpers exactly so tapes round-trip
            # across all three paths without format drift.
            "frame_count": str(self.frame_count),
            "button_bits": dict(BUTTON_BITS),
            "metadata": merge_metadata(self.metadata, created_by="python-input-tape"),
            "start_probe": self.start_probe,
            "end_probe": self.end_probe,
            "runs": [run.to_json() for run in self.runs],
        }

    @classmethod
    def from_frames(
        cls,
        frames: Iterable[int],
        *,
        metadata: Mapping[str, Any] | None = None,
        start_probe: Mapping[str, Any] | None = None,
        end_probe: Mapping[str, Any] | None = None,
    ) -> "InputTape":
        """Create a compressed tape from one key mask per frame."""
        runs = compress_frames(frames)
        require_non_empty_runs(runs)
        return cls(
            runs=runs,
            metadata=dict(metadata or {}),
            start_probe=dict(start_probe or {}),
            end_probe=dict(end_probe or {}),
        )

    @classmethod
    def from_json(cls, data: Mapping[str, Any]) -> "InputTape":
        """Parse and validate a tape JSON object."""
        if data.get("format") != TAPE_FORMAT:
            raise ValueError(f"unsupported input tape format: {data.get('format')!r}")

        if "runs" in data:
            runs = [InputRun.from_json(item) for item in data["runs"]]
        elif "frames" in data:
            runs = compress_frames(parse_mask(mask) for mask in data["frames"])
        else:
            raise ValueError("input tape needs 'runs' or 'frames'")
        require_non_empty_runs(runs)

        tape = cls(
            runs=runs,
            metadata=dict(data.get("metadata", {})),
            start_probe=dict(data.get("start_probe", {})),
            end_probe=dict(data.get("end_probe", {})),
        )
        expected_frames = data.get("frame_count")
        if expected_frames is not None and int(expected_frames) != tape.frame_count:
            raise ValueError(
                f"frame_count mismatch: header={expected_frames!r} actual={tape.frame_count}"
            )
        return tape


@dataclass(frozen=True)
class ReplayResult:
    """Summary returned after a tape replay."""

    frames: int
    start_probe: dict[str, Any]
    end_probe: dict[str, Any]


class InputTapeRecorder:
    """Record masks emitted by a script while advancing a core.

    This proxy is the Python-only "record" path. A route script should call
    methods on the recorder instead of the core for input and frame stepping.
    That guarantees the tape captures the exact raw masks that were driven into
    the emulator, rather than trying to sample Qt keyboard state after the fact.
    """

    def __init__(
        self,
        core: Any,
        *,
        clear_before: bool = True,
        pause_before: bool = True,
        metadata: Mapping[str, Any] | None = None,
    ) -> None:
        self.core = core
        self.metadata = dict(metadata or {})
        self._mask = 0
        self._runs: list[InputRun] = []
        self._finished_tape: InputTape | None = None
        self.start_probe: dict[str, Any] = {}
        self.end_probe: dict[str, Any] = {}

        if pause_before:
            pause_core_if_available(core)
        if clear_before:
            set_exact_keys(core, 0)
        self.start_probe = probe_core(core)

    def __enter__(self) -> "InputTapeRecorder":
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, traceback: Any) -> None:
        set_exact_keys(self.core, 0)
        pause_core_if_available(self.core)
        if exc is None:
            self.finish()

    def __getattr__(self, name: str) -> Any:
        """Proxy non-input calls to the wrapped core."""
        return getattr(self.core, name)

    def set_keys(self, *keys: int, raw: int | None = None) -> None:
        """Set the current exact key mask for subsequent recorded frames."""
        self._mask = keys_to_mask(*keys, raw=raw)
        set_exact_keys(self.core, self._mask)

    def add_keys(self, *keys: int, raw: int | None = None) -> None:
        """Add buttons to the current recorded key mask."""
        self._mask |= keys_to_mask(*keys, raw=raw)
        set_exact_keys(self.core, self._mask)

    def clear_keys(self, *keys: int, raw: int | None = None) -> None:
        """Clear buttons from the current recorded key mask."""
        self._mask &= ~keys_to_mask(*keys, raw=raw)
        set_exact_keys(self.core, self._mask)

    def run_frame(self) -> None:
        """Run one frame and record the exact mask used for that frame."""
        self.run_frames(1)

    def run_sampled_frame(self, mask: int, *, use_batch: bool = True) -> None:
        """Run one frame with a freshly sampled raw mask.

        Live Qt capture already receives an exact GBA-button mask from the C++
        bridge. Drive that mask directly through the frame helper so we do not
        pay one extra set_keys() bridge call before every single sampled frame.
        """
        self._mask = int(mask)
        run_exact_frames(self.core, self._mask, 1, use_batch=use_batch)
        self._append_run(self._mask, 1)

    def run_frames(self, count: int, *, use_batch: bool = True) -> None:
        """Run several frames with the current exact mask."""
        count = checked_frame_count(count)
        if count == 0:
            return
        run_exact_frames(self.core, self._mask, count, use_batch=use_batch)
        self._append_run(self._mask, count)

    def pulse_keys(self, keys: int, count: int) -> None:
        """Hold one raw mask for count frames, then restore the prior mask."""
        previous_mask = self._mask
        try:
            self._mask = int(keys)
            self.run_frames(count)
        finally:
            # Restore even if the core aborts or the Qt bridge raises. Without
            # this, a failed route segment can poison the next replay attempt
            # with a stale held button.
            self._mask = previous_mask
            set_exact_keys(self.core, self._mask)

    def finish(self) -> InputTape:
        """Finish recording and return the immutable tape snapshot."""
        if self._finished_tape is None:
            set_exact_keys(self.core, 0)
            self.end_probe = probe_core(self.core)
            self._finished_tape = InputTape(
                runs=list(self._runs),
                metadata=dict(self.metadata),
                start_probe=dict(self.start_probe),
                end_probe=dict(self.end_probe),
            )
        return self._finished_tape

    def _append_run(self, mask: int, frames: int) -> None:
        if self._runs and self._runs[-1].mask == mask:
            previous = self._runs.pop()
            self._runs.append(InputRun(mask=mask, frames=previous.frames + frames))
        else:
            self._runs.append(InputRun(mask=mask, frames=frames))


def normalize_button_name(name: str) -> str:
    """Normalize one human button token to the tape's canonical name."""
    normalized = name.strip().upper().replace(" ", "").replace("-", "_")
    normalized = BUTTON_ALIASES.get(normalized, normalized)
    if normalized == "NONE":
        return normalized
    if normalized not in BUTTON_BITS:
        raise ValueError(f"unknown button name: {name!r}")
    return normalized


def mask_from_buttons(buttons: str | Sequence[str]) -> int:
    """Convert button names such as A+START into a raw mGBA key mask."""
    if isinstance(buttons, str):
        text = buttons.strip()
        if text.lower().startswith("0x") or text.isdigit():
            return parse_mask(buttons)
        parts = [part for part in text.replace("|", "+").split("+") if part.strip()]
    else:
        parts = list(buttons)

    mask = 0
    for part in parts:
        name = normalize_button_name(str(part))
        if name == "NONE":
            continue
        mask |= 1 << BUTTON_BITS[name]
    return mask


def mask_to_button_names(mask: int) -> list[str]:
    """Return readable button names for a raw mask."""
    names = [name for name in BUTTON_ORDER if int(mask) & (1 << BUTTON_BITS[name])]
    unknown_mask = int(mask) & ~KNOWN_BUTTON_MASK
    if unknown_mask:
        names.append(format_mask(unknown_mask))
    if not names:
        return ["NONE"]
    return names


def format_mask(mask: int) -> str:
    """Format a raw key mask consistently for JSON and logs."""
    return f"0x{int(mask) & 0xFFFFFFFF:08X}"


def parse_mask(value: Any) -> int:
    """Parse an integer, hex mask, decimal mask, or button expression."""
    if isinstance(value, int):
        mask = value
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            mask = 0
        elif text.lower().startswith("0x"):
            mask = int(text, 16)
        elif text.isdigit():
            mask = int(text, 10)
        else:
            # Button combos such as A+START are not valid single button names,
            # so route non-numeric strings through the combo parser instead of
            # rejecting them during single-name normalization.
            mask = mask_from_buttons(text)
    else:
        raise TypeError(f"cannot parse key mask from {value!r}")

    if mask < 0 or mask > 0xFFFFFFFF:
        raise ValueError(f"mask must fit in uint32, got {value!r}")
    return mask


def parse_plan(plan: str) -> list[InputRun]:
    """Parse a compact plan string into runs.

    Example:
        A:2,none:1,START+A:3
    """
    runs: list[InputRun] = []
    for raw_item in plan.split(","):
        item = raw_item.strip()
        if not item:
            continue
        if ":" in item:
            buttons, frames_text = item.rsplit(":", 1)
            frames = int(frames_text.strip())
        else:
            buttons = item
            frames = 1
        runs.append(InputRun(mask=mask_from_buttons(buttons), frames=frames))
    if not runs:
        raise ValueError("input plan is empty")
    return runs


def compress_frames(frames: Iterable[int]) -> list[InputRun]:
    """Compress one mask per frame into RLE runs."""
    runs: list[InputRun] = []
    current_mask: int | None = None
    current_frames = 0

    for value in frames:
        mask = parse_mask(value)
        if current_mask is None:
            current_mask = mask
            current_frames = 1
        elif current_mask == mask:
            current_frames += 1
        else:
            # Append only when a run ends. Long routes can contain thousands of
            # identical masks, so avoid replacing one InputRun every frame.
            runs.append(InputRun(mask=current_mask, frames=current_frames))
            current_mask = mask
            current_frames = 1

    if current_mask is not None:
        runs.append(InputRun(mask=current_mask, frames=current_frames))
    return runs


def checked_frame_count(count: int) -> int:
    """Validate a non-negative frame count."""
    frames = int(count)
    if frames < 0:
        raise ValueError("frame count must be non-negative")
    return frames


def keys_to_mask(*keys: int, raw: int | None = None) -> int:
    """Convert core-style key arguments into a raw key mask."""
    mask = int(raw or 0)
    for key in keys:
        mask |= 1 << int(key)
    if mask < 0 or mask > 0xFFFFFFFF:
        raise ValueError(f"key mask must fit in uint32, got {mask!r}")
    return mask


def core_is_paused(core: Any) -> bool:
    """Return True when a wrapper exposes a current paused state.

    Host-side cores usually do not expose pause state because they only advance
    when Python calls `run_frame()`. The visible Qt wrapper now exposes
    `paused`, which lets route helpers avoid redundant pause calls.
    """
    for attr_name in ("paused", "is_paused", "isPaused"):
        try:
            value = getattr(core, attr_name)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value:
            return True
        # Keep scanning other common pause-state spellings. Some wrappers expose
        # a stale property plus a live method; a single false value should not
        # force a redundant pause when another source says the core is paused.
    return False


def pause_core_if_available(core: Any) -> bool:
    """Ensure a Qt core is paused without disturbing an already-paused core."""
    if core_is_paused(core):
        return False
    pause = getattr(core, "pause", None)
    if not callable(pause):
        return False
    pause()
    return True


def set_exact_keys(core: Any, mask: int) -> None:
    """Push one exact raw mask through the same deterministic path as replay."""
    core.set_keys(raw=int(mask))


def run_exact_frames(core: Any, mask: int, count: int, *, use_batch: bool = True) -> None:
    """Run frames while forcing the same exact raw mask for every frame."""
    frames = checked_frame_count(count)
    if frames == 0:
        return

    if use_batch:
        batch = getattr(core, "run_frames_with_keys", None)
        if callable(batch):
            batch(int(mask), frames)
            return

    set_exact_keys(core, int(mask))
    for _ in range(frames):
        core.run_frame()


def probe_core(core: Any) -> dict[str, Any]:
    """Capture lightweight debug state without depending on a specific game."""
    probe: dict[str, Any] = {}
    for name in ("frame_counter", "platform", "game_title", "game_code"):
        try:
            value = getattr(core, name)
        except Exception:
            continue
        if callable(value):
            continue
        try:
            json.dumps(value)
        except TypeError:
            value = str(value)
        probe[name] = value

    memory = getattr(core, "memory", None)
    u16 = getattr(memory, "u16", None)
    if u16 is not None:
        try:
            keyinput = int(u16[KEYINPUT_ADDR])
        except Exception:
            pass
        else:
            probe["keyinput"] = format_mask(keyinput)
            probe["held_from_keyinput"] = format_mask((~keyinput) & GBA_BUTTON_MASK)
    return probe


def replay_tape(
    core: Any,
    tape: InputTape,
    *,
    clear_before: bool = True,
    clear_after: bool = True,
    pause_before: bool = True,
    pause_after: bool = True,
    use_batch: bool = True,
    verify_frame_counter: bool = True,
) -> ReplayResult:
    """Replay a tape from the core's current state.

    The caller owns state anchoring. This function deliberately does not load a
    ROM, save file, savestate, or scratch state.
    """
    require_non_empty_runs(tape.runs)
    if pause_before:
        pause_core_if_available(core)
    if clear_before:
        set_exact_keys(core, 0)

    start_probe = probe_core(core)
    start_frame = start_probe.get("frame_counter")
    try:
        for run in tape.runs:
            run_exact_frames(core, run.mask, run.frames, use_batch=use_batch)
    finally:
        if clear_after:
            set_exact_keys(core, 0)
        if pause_after:
            pause_core_if_available(core)

    end_probe = probe_core(core)
    end_frame = end_probe.get("frame_counter")
    if verify_frame_counter and isinstance(start_frame, int) and isinstance(end_frame, int):
        advanced = end_frame - start_frame
        if advanced != tape.frame_count:
            raise RuntimeError(
                f"input tape replay advanced {advanced} frame(s), expected {tape.frame_count}"
            )

    return ReplayResult(frames=tape.frame_count, start_probe=start_probe, end_probe=end_probe)


def record_plan(core: Any, runs: Sequence[InputRun], *, metadata: Mapping[str, Any] | None = None) -> InputTape:
    """Apply a plan to a core and return the recorded tape."""
    tape = from_runs(runs, metadata=metadata)
    result = replay_tape(core, tape)
    tape.start_probe = dict(result.start_probe)
    tape.end_probe = dict(result.end_probe)
    return tape


def get_exact_keys(core: Any) -> int:
    """Read the core's currently held GBA-button mask when available.

    This is the Python-side counterpart to Lua's `emu:getKeys()` path used by
    `inputTape.recordCurrentKeys(...)`. It prefers explicit key getters, then
    falls back to wrapper-owned key caches or KEYINPUT-derived state.
    """
    for attr_name in ("get_keys", "getKeys"):
        try:
            value = getattr(core, attr_name)
        except Exception:
            continue
        if callable(value):
            try:
                value = value()
            except Exception:
                continue
        if value is not None:
            return int(value) & GBA_BUTTON_MASK

    for attr_name in ("current_keys", "_current_keys"):
        try:
            value = getattr(core, attr_name)
        except Exception:
            continue
        return int(value) & GBA_BUTTON_MASK

    memory = getattr(core, "memory", None)
    u16 = getattr(memory, "u16", None)
    if u16 is not None:
        try:
            keyinput = int(u16[KEYINPUT_ADDR])
        except Exception:
            pass
        else:
            return (~keyinput) & GBA_BUTTON_MASK

    raise RuntimeError("Core does not expose current key state for input-tape recording.")


def record_current_keys_tape(
    core: Any,
    frames: int,
    *,
    metadata: Mapping[str, Any] | None = None,
    clear_before: bool = False,
    clear_after: bool = False,
    pause_before: bool = False,
    pause_after: bool = False,
) -> InputTape:
    """Sample the core's current held keys once per frame into a tape.

    This matches the built-in Lua `inputTape.recordCurrentKeys(...)` behavior:
    sample the already-held exact mask, advance one frame, and compress the
    observed per-frame masks into one anchor-agnostic tape snapshot.
    """
    frame_count = checked_positive_frame_count(frames)
    if pause_before:
        pause_core_if_available(core)
    if clear_before:
        set_exact_keys(core, 0)

    start_probe = probe_core(core)
    per_frame = []
    for _ in range(frame_count):
        per_frame.append(get_exact_keys(core))
        core.run_frame()

    if clear_after:
        set_exact_keys(core, 0)
    if pause_after:
        pause_core_if_available(core)

    tape = from_frames(
        per_frame,
        metadata=metadata,
        start_probe=start_probe,
        end_probe=probe_core(core),
    )
    return tape


def _record_sampled_key_mask_tape(
    core: Any,
    frames: int,
    sampler: Callable[[], int],
    *,
    source: str,
    metadata: Mapping[str, Any] | None = None,
    clear_before: bool = True,
    clear_after: bool = True,
    pause_before: bool = True,
    pause_after: bool = True,
    use_batch: bool = True,
) -> InputTape:
    """Sample one already-mapped GBA key mask per frame into an input tape."""
    frame_count = checked_positive_frame_count(frames)
    recorder = InputTapeRecorder(
        core,
        clear_before=clear_before,
        pause_before=pause_before,
        metadata={
            "source": source,
            "requested_frames": frame_count,
            **dict(metadata or {}),
        },
    )
    try:
        for _ in range(frame_count):
            recorder.run_sampled_frame(int(sampler()), use_batch=use_batch)
        tape = recorder.finish()
    finally:
        if clear_after:
            set_exact_keys(core, 0)
        if pause_after:
            pause_core_if_available(core)
    return tape


def record_virtual_pad_tape(
    core: Any,
    frames: int,
    *,
    metadata: Mapping[str, Any] | None = None,
    qt_bridge: Any | None = None,
    open_pad: bool = True,
    clear_before: bool = True,
    clear_after: bool = True,
    pause_before: bool = True,
    pause_after: bool = True,
    use_batch: bool = True,
) -> InputTape:
    """Record the visible Qt Virtual Pad state for a fixed number of frames.

    This is the manual-input capture path for the local Custom Features Virtual
    Pad. It does not sample physical keyboard/controller state. The Qt bridge
    pumps UI events before each sample, then the recorder applies that exact
    mask for one emulated frame so the saved tape and the live core see the same
    buttons.
    """
    if qt_bridge is None:
        from mgba import qt as qt_bridge

    if open_pad:
        opener = getattr(qt_bridge, "open_virtual_pad", None)
        if not callable(opener) or not opener():
            raise RuntimeError("Could not open the visible Qt Virtual Pad for capture.")

    sampler = getattr(qt_bridge, "virtual_pad_key_mask", None)
    if not callable(sampler):
        raise RuntimeError("The Qt bridge does not expose virtual_pad_key_mask().")

    return _record_sampled_key_mask_tape(
        core,
        frames,
        sampler,
        source="qt-virtual-pad",
        metadata=metadata,
        clear_before=clear_before,
        clear_after=clear_after,
        pause_before=pause_before,
        pause_after=pause_after,
        use_batch=use_batch,
    )


def record_qt_input_tape(
    core: Any,
    frames: int,
    *,
    metadata: Mapping[str, Any] | None = None,
    qt_bridge: Any | None = None,
    clear_before: bool = True,
    clear_after: bool = True,
    pause_before: bool = True,
    pause_after: bool = True,
    use_batch: bool = True,
) -> InputTape:
    """Record mapped Qt frontend controller buttons for a fixed frame count.

    This captures GBA button bits after Qt resolves keyboard bindings. If the X
    key is mapped to GBA A, the tape records A, not X. It also sees Virtual Pad
    and scripted held keys because they feed the same controller button mask.
    """
    if qt_bridge is None:
        from mgba import qt as qt_bridge

    sampler = getattr(qt_bridge, "controller_key_mask", None)
    if not callable(sampler):
        raise RuntimeError("The Qt bridge does not expose controller_key_mask().")

    return _record_sampled_key_mask_tape(
        core,
        frames,
        sampler,
        source="qt-controller-input",
        metadata=metadata,
        clear_before=clear_before,
        clear_after=clear_after,
        pause_before=pause_before,
        pause_after=pause_after,
        use_batch=use_batch,
    )


def read_tape(path: Path | str) -> InputTape:
    """Read an input tape JSON file."""
    return InputTape.from_json(json.loads(Path(path).read_text(encoding="utf-8")))


def write_tape(path: Path | str, tape: InputTape) -> None:
    """Write a tape JSON file atomically enough for local tooling."""
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    temp = output.with_name(f"{output.name}.tmp")
    temp.write_text(json.dumps(tape.to_json(), indent=2) + "\n", encoding="utf-8")
    temp.replace(output)


def from_runs(
    runs: Sequence[InputRun] | Sequence[Mapping[str, Any]],
    *,
    metadata: Mapping[str, Any] | None = None,
    start_probe: Mapping[str, Any] | None = None,
    end_probe: Mapping[str, Any] | None = None,
) -> InputTape:
    """Build a tape from pre-compressed runs."""
    require_non_empty_runs(runs, context="input tape")
    return InputTape(
        runs=list(runs),
        metadata=dict(metadata or {}),
        start_probe=dict(start_probe or {}),
        end_probe=dict(end_probe or {}),
    )


def from_frames(
    frames: Iterable[int],
    *,
    metadata: Mapping[str, Any] | None = None,
    start_probe: Mapping[str, Any] | None = None,
    end_probe: Mapping[str, Any] | None = None,
) -> InputTape:
    """Build a tape from one exact mask per frame."""
    return InputTape.from_frames(frames, metadata=metadata, start_probe=start_probe, end_probe=end_probe)


# Common cross-language helpers. These camelCase aliases intentionally mirror
# the built-in Lua `inputTape` library so small route helpers can use the same
# conceptual tape surface in either language.
formatMask = format_mask
maskFromButtons = mask_from_buttons
buttonNames = mask_to_button_names
fromRuns = from_runs
fromFrames = from_frames
load = read_tape
save = write_tape
replay = replay_tape
recordPlan = record_plan
recordCurrentKeys = record_current_keys_tape
recordQtInput = record_qt_input_tape
recordVirtualPad = record_virtual_pad_tape


def _load_host_core(rom_path: str):
    from _helpers import load_core, print_core_summary

    core, rom = load_core(rom_path)
    print_core_summary(core, rom)
    core.reset()
    return core


def _load_qt_core():
    from mgba import qt as mgba_qt

    core = mgba_qt.current_core()
    pause_core_if_available(core)
    return core


def build_parser() -> argparse.ArgumentParser:
    """Build the command-line parser for the standalone demo."""
    parser = argparse.ArgumentParser(
        description="Record/replay a simple save-state-agnostic mGBA input tape.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "rom",
        nargs="?",
        default=str(DEFAULT_ROM),
        help="ROM to load for host-side demos. Ignored with --qt.",
    )
    parser.add_argument(
        "--qt",
        action="store_true",
        help="Use the already-visible Qt core instead of loading a host-side core.",
    )
    parser.add_argument(
        "--input",
        type=Path,
        help="Replay an existing input tape instead of recording --plan.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="Where to write the recorded tape JSON.",
    )
    parser.add_argument(
        "--plan",
        default=DEFAULT_PLAN,
        help="Compact recording plan such as 'A:2,none:1,START+A:3'.",
    )
    parser.add_argument(
        "--replay",
        action="store_true",
        help="Replay the newly recorded tape immediately.",
    )
    parser.add_argument(
        "--record-virtual-pad-frames",
        type=int,
        help="With --qt, sample the Custom Features Virtual Pad for this many frames and write an input tape.",
    )
    parser.add_argument(
        "--record-qt-input-frames",
        type=int,
        help="With --qt, sample the mapped Qt frontend GBA button mask for this many frames.",
    )
    parser.add_argument(
        "--no-batch",
        action="store_true",
        help="Use one Python run_frame() call per frame instead of batch helpers.",
    )
    parser.add_argument(
        "--no-verify-frame-counter",
        action="store_true",
        help="Do not verify that replay advanced the expected number of frames.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run a small host-side or visible-Qt input-tape operation."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.record_virtual_pad_frames is not None and args.record_qt_input_frames is not None:
        raise SystemExit("Choose only one live Qt input capture mode.")
    if args.record_qt_input_frames is not None and not args.qt:
        raise SystemExit("--record-qt-input-frames requires --qt.")
    if args.record_virtual_pad_frames is not None and not args.qt:
        raise SystemExit("--record-virtual-pad-frames requires --qt.")

    core = _load_qt_core() if args.qt else _load_host_core(args.rom)
    if args.record_qt_input_frames is not None:
        tape = record_qt_input_tape(
            core,
            args.record_qt_input_frames,
            metadata={"mode": "qt-controller-input-capture"},
            use_batch=not args.no_batch,
        )
        print(
            "Recorded Qt input tape:"
            f" runs={len(tape.runs)} frames={tape.frame_count}"
            f" start_frame={tape.start_probe.get('frame_counter')}"
            f" end_frame={tape.end_probe.get('frame_counter')}"
        )
        if args.output:
            write_tape(args.output, tape)
            print(f"Wrote input tape: {args.output}")
        return 0
    if args.record_virtual_pad_frames is not None:
        tape = record_virtual_pad_tape(
            core,
            args.record_virtual_pad_frames,
            metadata={"mode": "virtual-pad-capture"},
            use_batch=not args.no_batch,
        )
        print(
            "Recorded Virtual Pad input tape:"
            f" runs={len(tape.runs)} frames={tape.frame_count}"
            f" start_frame={tape.start_probe.get('frame_counter')}"
            f" end_frame={tape.end_probe.get('frame_counter')}"
        )
        if args.output:
            write_tape(args.output, tape)
            print(f"Wrote input tape: {args.output}")
        return 0

    if args.input:
        tape = read_tape(args.input)
        print(f"Loaded input tape: {args.input} frames={tape.frame_count}")
        result = replay_tape(
            core,
            tape,
            use_batch=not args.no_batch,
            verify_frame_counter=not args.no_verify_frame_counter,
        )
        print(
            "Replayed input tape:"
            f" frames={result.frames}"
            f" start_frame={result.start_probe.get('frame_counter')}"
            f" end_frame={result.end_probe.get('frame_counter')}"
        )
        return 0

    runs = parse_plan(args.plan)
    tape = record_plan(core, runs, metadata={"plan": args.plan})
    print(
        "Recorded input tape:"
        f" runs={len(tape.runs)} frames={tape.frame_count}"
        f" start_frame={tape.start_probe.get('frame_counter')}"
        f" end_frame={tape.end_probe.get('frame_counter')}"
    )

    if args.output:
        write_tape(args.output, tape)
        print(f"Wrote input tape: {args.output}")

    if args.replay:
        result = replay_tape(
            core,
            tape,
            use_batch=not args.no_batch,
            verify_frame_counter=not args.no_verify_frame_counter,
        )
        print(
            "Replayed input tape:"
            f" frames={result.frames}"
            f" start_frame={result.start_probe.get('frame_counter')}"
            f" end_frame={result.end_probe.get('frame_counter')}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
