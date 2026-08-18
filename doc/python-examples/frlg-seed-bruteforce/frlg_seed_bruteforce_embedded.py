"""Visible-window FR/LG seed brute force for mGBA's embedded SDL Python path.

This script is not meant to be run with bare Python. It is meant to be loaded
through the SDL CLI debugger:

    source frlg_seed_bruteforce_embedded.py
    continue

Expected environment variables are set by the sibling PowerShell launcher.
"""

from __future__ import annotations

import os
from pathlib import Path

_GLOBALS = globals()

from mgba._pylib import lib as _lib
import mgba.vfs as _mgba_vfs
from mgba.gba import GBA as _GBA

# mGBA's embedded Python runner executes scripts with separate globals/locals.
# Publish imported runtime objects into the true globals table so methods and
# callbacks can resolve them later.
_GLOBALS["lib"] = _lib
_GLOBALS["mgba_vfs"] = _mgba_vfs
_GLOBALS["GBA"] = _GBA


GTRAINER_ID_ADDR = 0x02020000
GRNG_VALUE_ADDR = 0x03005000
GMAIN_VBLANK2_ADDR = 0x03003114
GTASKS_ADDR = 0x03005090
TASK_SIZE = 0x28
TASK_COUNT = 16
TASK_TITLE_SCREEN_MAIN = 0x08078C24 | 1
TITLESCENE_RUN = 3
SAVE_STATE_FLAGS = 0

TARGET_SEED = int(os.environ.get("MGBA_TARGET_SEED", "0x1234"), 0) & 0xFFFF
MAX_DELAY = max(0, int(os.environ.get("MGBA_MAX_DELAY", "1024"), 0))
SETTLE_FRAMES = max(0, int(os.environ.get("MGBA_SETTLE_FRAMES", "3"), 0))
SEED_TIMEOUT = max(1, int(os.environ.get("MGBA_SEED_TIMEOUT", "240"), 0))
PROGRESS_EVERY = max(1, int(os.environ.get("MGBA_PROGRESS_EVERY", "50"), 0))
OUTPUT_DIR = Path(os.environ.get("MGBA_BRUTE_FORCE_DIR", ".")).resolve()

# mGBA's embedded runner keeps these assignments in a separate locals mapping.
# Publish the values that methods look up at runtime into the real globals table.
_GLOBALS.update(
    {
        "GTRAINER_ID_ADDR": GTRAINER_ID_ADDR,
        "GRNG_VALUE_ADDR": GRNG_VALUE_ADDR,
        "GMAIN_VBLANK2_ADDR": GMAIN_VBLANK2_ADDR,
        "GTASKS_ADDR": GTASKS_ADDR,
        "TASK_SIZE": TASK_SIZE,
        "TASK_COUNT": TASK_COUNT,
        "TASK_TITLE_SCREEN_MAIN": TASK_TITLE_SCREEN_MAIN,
        "TITLESCENE_RUN": TITLESCENE_RUN,
        "SAVE_STATE_FLAGS": SAVE_STATE_FLAGS,
        "TARGET_SEED": TARGET_SEED,
        "MAX_DELAY": MAX_DELAY,
        "SETTLE_FRAMES": SETTLE_FRAMES,
        "SEED_TIMEOUT": SEED_TIMEOUT,
        "PROGRESS_EVERY": PROGRESS_EVERY,
        "OUTPUT_DIR": OUTPUT_DIR,
    }
)


class VisibleSeedBruteForce:
    """Drive the brute-force loop from core callbacks while SDL renders live."""

    def __init__(self, debugger_obj) -> None:
        self.debugger = debugger_obj
        self.core = debugger_obj._core
        if not isinstance(self.core, GBA):
            raise SystemExit("This embedded script requires a GBA core.")

        self.target_seed = TARGET_SEED
        self.seed_tag = f"{self.target_seed:04x}"
        self.checkpoint_path = OUTPUT_DIR / f"seed{self.seed_tag}test.sav"
        self.done_path = OUTPUT_DIR / f"seed{self.seed_tag}done.sav"

        self.current_keys = 0
        self.delay_frames = 0
        self.delay_remaining = 0
        self.seed_wait_remaining = SEED_TIMEOUT
        self.settle_remaining = 0
        self.last_frame = -1
        self.phase = "wait_title"
        self.finished = False
        self.seed_frame = 0
        self.last_seed_value = 0

    def install(self) -> None:
        """Attach callbacks and print the run configuration."""

        if self.done_path.exists():
            self.done_path.unlink()

        self.core._callbacks.keys_read.append(self.on_keys_read)
        self.core.add_frame_callback(self.on_frame)

        print(f"Target seed: 0x{self.target_seed:04X}")
        print(f"Checkpoint savestate: {self.checkpoint_path}")
        print(f"Done savestate: {self.done_path}")
        print("Embedded brute-force runner installed. Use `continue` to start.")

    def finish(self, message: str) -> None:
        """Stop mutating the core and leave process lifetime to the caller/tests."""

        if self.finished:
            return
        self.finished = True
        self.current_keys = 0
        print(message)

    def save_state_file(self, path: Path) -> None:
        vf = mgba_vfs.open_path(str(path), "w+")
        if not vf:
            raise RuntimeError(f"Could not open savestate path for writing: {path}")
        try:
            if not lib.mCoreSaveStateNamed(self.core._core, vf.handle, SAVE_STATE_FLAGS):
                raise RuntimeError(f"mCoreSaveStateNamed(...) failed for {path}")
        finally:
            vf.close()

    def load_state_file(self, path: Path) -> None:
        vf = mgba_vfs.open_path(str(path), "r")
        if not vf:
            raise RuntimeError(f"Could not open savestate path for reading: {path}")
        try:
            if not lib.mCoreLoadStateNamed(self.core._core, vf.handle, SAVE_STATE_FLAGS):
                raise RuntimeError(f"mCoreLoadStateNamed(...) failed for {path}")
        finally:
            vf.close()

    def find_title_task(self):
        # FR/LG's title logic lives in gTasks. Matching the task function lets
        # the script find the same pre-second-A checkpoint as the host script.
        for task_id in range(TASK_COUNT):
            base = GTASKS_ADDR + task_id * TASK_SIZE
            if self.core.memory.u8[base + 4] and self.core.memory.u32[base] == TASK_TITLE_SCREEN_MAIN:
                return (
                    task_id,
                    base,
                    self.core.memory.u16[base + 8],
                    self.core.memory.u16[base + 10],
                )
        return None

    def begin_attempt(self, delay_frames: int, *, load_checkpoint: bool) -> None:
        """Reset attempt-local state and optionally reload the saved checkpoint."""

        self.delay_frames = delay_frames
        self.delay_remaining = delay_frames
        self.seed_wait_remaining = SEED_TIMEOUT
        self.settle_remaining = 0
        self.last_seed_value = 0
        self.seed_frame = 0

        if load_checkpoint:
            self.load_state_file(self.checkpoint_path)
            # Loading the state rewinds the frame counter, so drop the guard.
            self.last_frame = -1

        if self.delay_remaining == 0:
            self.current_keys = self.core.KEY_A
            self.phase = "await_seed"
        else:
            self.current_keys = 0
            self.phase = "delay_before_second_a"

    def on_keys_read(self) -> None:
        """Apply the current held buttons at the moment the core samples input."""

        if not self.finished:
            self.core.set_keys(raw=self.current_keys)

    def on_frame(self) -> None:
        """Advance the state machine once per finished frame."""

        try:
            self._on_frame()
        except Exception as exc:  # pragma: no cover - embedded runtime safety
            self.finish(f"Embedded brute-force failed: {exc!r}")

    def _on_frame(self) -> None:
        if self.finished:
            return

        frame_counter = self.core.frame_counter
        if frame_counter == self.last_frame:
            return
        self.last_frame = frame_counter

        info = self.find_title_task()

        if self.phase == "wait_title":
            if info is None:
                return
            _, _, scene, state = info
            print(
                "Title screen detected:"
                f" frame_counter={frame_counter}"
                f" vblank2={self.core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={scene} state={state}"
            )
            self.current_keys = self.core.KEY_A
            self.phase = "first_a_frame"
            return

        if self.phase == "first_a_frame":
            if info is None:
                self.finish("Lost the title task immediately after the first title A press.")
                return
            _, _, scene, state = info
            print(
                "Pressed first title A:"
                f" frame_counter={frame_counter}"
                f" scene={scene} state={state}"
            )
            self.current_keys = 0
            self.phase = "wait_checkpoint"
            return

        if self.phase == "wait_checkpoint":
            if info is None:
                self.finish("Lost the title task while waiting for the checkpoint.")
                return
            _, _, scene, state = info
            if scene != TITLESCENE_RUN or state != 1:
                return

            print(
                "Checkpoint reached before second title A:"
                f" frame_counter={frame_counter}"
                f" vblank2={self.core.memory.u32[GMAIN_VBLANK2_ADDR]}"
                f" scene={scene} state={state}"
            )
            self.save_state_file(self.checkpoint_path)
            print(f"Saved checkpoint: {self.checkpoint_path}")
            self.begin_attempt(0, load_checkpoint=False)
            return

        if self.phase == "delay_before_second_a":
            self.delay_remaining -= 1
            if self.delay_remaining > 0:
                return

            self.current_keys = self.core.KEY_A
            self.phase = "await_seed"
            return

        if self.phase == "await_seed":
            seed_value = self.core.memory.u16[GTRAINER_ID_ADDR]
            if seed_value:
                self.last_seed_value = seed_value
                self.seed_frame = frame_counter
                self.settle_remaining = SETTLE_FRAMES
                self.phase = "settle_after_seed"
                return

            self.seed_wait_remaining -= 1
            if self.seed_wait_remaining > 0:
                return

            self.finish(
                "Initial seed was not observed within"
                f" {SEED_TIMEOUT} frames after delay {self.delay_frames}."
            )
            return

        if self.phase == "settle_after_seed":
            if self.settle_remaining > 0:
                self.settle_remaining -= 1
                return

            rng_value = self.core.memory.u32[GRNG_VALUE_ADDR]
            if self.delay_frames % PROGRESS_EVERY == 0 or self.last_seed_value == self.target_seed:
                print(
                    "Attempt"
                    f" delay={self.delay_frames}"
                    f" seed=0x{self.last_seed_value:04X}"
                    f" seed_frame={self.seed_frame}"
                    f" rng_after_settle=0x{rng_value:08X}"
                )

            if self.last_seed_value == self.target_seed:
                self.save_state_file(self.done_path)
                self.finish(
                    "Match found:"
                    f" delay={self.delay_frames}"
                    f" seed=0x{self.last_seed_value:04X}"
                    f" saved={self.done_path}"
                )
                return

            next_delay = self.delay_frames + 1
            if next_delay > MAX_DELAY:
                self.finish(
                    "No match found:"
                    f" target=0x{self.target_seed:04X}"
                    f" searched_delays=0..{MAX_DELAY}"
                )
                return

            self.begin_attempt(next_delay, load_checkpoint=True)


if "debugger" in globals():
    if hasattr(debugger, "install_print"):
        debugger.install_print()

    runner = VisibleSeedBruteForce(debugger)
    runner.install()
