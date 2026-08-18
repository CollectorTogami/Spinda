# Copyright (c) 2013-2017 Jeffrey Pfau
#
# Original mGBA source is credited to Jeffrey Pfau and contributors.
# Local custom modifications in this fork were added for this workspace and
# are not upstream mGBA work or authored by Jeffrey Pfau.
#
# This Source Code Form is subject to the terms of the Mozilla Public
# License, v. 2.0. If a copy of the MPL was not distributed with this
# file, You can obtain one at http://mozilla.org/MPL/2.0/.
"""Local bridge to the already-visible Qt mGBA core.

This is the custom path that lets Python scripts drive the same window you are
watching, which is useful for frame-accurate FR/LG seed work without spinning
up a second hidden emulator instance. In this workspace it also covers the
runtime path where a script loaded from `Tools > Scripting...` can load the ROM
and save files it needs after the GUI is already open.
"""
from pathlib import Path

from ._pylib import lib  # pylint: disable=no-name-in-module
from .gba import GBA

THEME_WINDOW = getattr(lib, "MPYQT_THEME_WINDOW", 0)
THEME_BASE = getattr(lib, "MPYQT_THEME_BASE", 1)
THEME_TEXT = getattr(lib, "MPYQT_THEME_TEXT", 2)
THEME_BUTTON = getattr(lib, "MPYQT_THEME_BUTTON", 3)
THEME_HIGHLIGHT = getattr(lib, "MPYQT_THEME_HIGHLIGHT", 4)


def is_available():
    try:
        return bool(lib.mPythonQtIsBound())
    except AttributeError:
        # Host-side imports can see an older _pylib build that does not yet
        # expose the Qt bridge. Treat that as "not available" instead of
        # crashing the caller.
        return False


class _QtMemoryView(object):
    def __init__(self, reader, writer):
        self._reader = reader
        self._writer = writer

    def __getitem__(self, address):
        return int(self._reader(address))

    def __setitem__(self, address, value):
        self._writer(address, value)


class _QtMemory(object):
    def __init__(self):
        self.u8 = _QtMemoryView(lib.mPythonQtRead8, lib.mPythonQtWrite8)
        self.u16 = _QtMemoryView(lib.mPythonQtRead16, lib.mPythonQtWrite16)
        self.u32 = _QtMemoryView(lib.mPythonQtRead32, lib.mPythonQtWrite32)


def _decode_rgba(color):
    color = int(color)
    return {
        "a": (color >> 24) & 0xFF,
        "r": (color >> 16) & 0xFF,
        "g": (color >> 8) & 0xFF,
        "b": color & 0xFF,
    }


def dark_mode_enabled():
    return bool(lib.mPythonQtDarkModeEnabled())


def has_style_sheet():
    return bool(lib.mPythonQtHasStyleSheet())


def theme_color(which):
    return _decode_rgba(lib.mPythonQtThemeColor(int(which)))


def theme_snapshot():
    # Small runtime probe used by the local dark-mode deployment tests.
    return {
        "dark_mode": dark_mode_enabled(),
        "has_style_sheet": has_style_sheet(),
        "window": theme_color(THEME_WINDOW),
        "base": theme_color(THEME_BASE),
        "text": theme_color(THEME_TEXT),
        "button": theme_color(THEME_BUTTON),
        "highlight": theme_color(THEME_HIGHLIGHT),
    }


def pause_current_core():
    """Pause the live visible Qt core from a startup script."""

    return bool(lib.mPythonQtPause())


def core_paused():
    """Report whether the live visible Qt core is already paused."""

    return bool(getattr(lib, "mPythonQtIsPaused", lambda: 0)())


def run_frames(count):
    """Run several visible-core frames through one native bridge call.

    This is a custom workspace optimization. Large deterministic waits are much
    faster when the C bridge loops internally instead of bouncing through the
    Python/Qt boundary once per frame.
    """

    count = int(count)
    if count < 0:
        raise ValueError("count must be non-negative")
    return bool(lib.mPythonQtRunFrames(count))


def run_frames_with_keys(keys, count):
    """Set an exact key mask and run several frames in one native call."""

    count = int(count)
    if count < 0:
        raise ValueError("count must be non-negative")
    return bool(lib.mPythonQtRunFramesWithKeys(int(keys), count))


def pulse_keys(keys, count):
    """Hold an exact key mask for several frames, then restore the old keys."""

    count = int(count)
    if count < 0:
        raise ValueError("count must be non-negative")
    return bool(lib.mPythonQtPulseKeys(int(keys), count))


def load_rom(path):
    """Load or replace the visible Qt ROM from a runtime Python script."""

    path = str(Path(path))
    return bool(lib.mPythonQtLoadRomFile(path.encode("utf-8")))


def load_save_file(path, temporary=False):
    """Load a save file into the visible Qt core."""

    path = str(Path(path))
    return bool(lib.mPythonQtLoadSaveFile(path.encode("utf-8"), int(bool(temporary))))


def export_save_file(path):
    """Write the visible Qt core's current raw save data to disk."""

    path = str(Path(path))
    return bool(lib.mPythonQtExportSaveFile(path.encode("utf-8")))


def save_scratch_state():
    """Capture one in-memory visible-core checkpoint for hot runtime loops."""

    return bool(lib.mPythonQtSaveScratchState())


def load_scratch_state():
    """Restore the current in-memory visible-core checkpoint."""

    return bool(lib.mPythonQtLoadScratchState())


def show_warning(title, message):
    """Show a warning dialog attached to the visible Qt window."""

    return bool(
        lib.mPythonQtShowWarning(
            str(title).encode("utf-8"),
            str(message).encode("utf-8"),
        )
    )


def set_text_buffer(name, text, cols=80, rows=24):
    """Replace one named scripting buffer in the visible Qt window."""

    lib.mPythonQtSetTextBuffer(
        str(name).encode("utf-8"),
        str(text).encode("utf-8"),
        int(cols),
        int(rows),
    )


def audio_killswitch_enabled():
    """Report whether the live Qt Audio killswitch is currently active.

    This mirrors the `Tools > Custom Features > Audio killswitch` checkbox on
    the running visible Qt window.
    """

    return bool(lib.mPythonQtAudioKillswitchEnabled())


def set_audio_killswitch(enable):
    """Toggle the live Qt Audio killswitch from a Python script."""

    # The C bridge returns a success flag here because the module-level helper
    # is used in setup scripts that prefer boolean checks over exceptions.
    return bool(lib.mPythonQtSetAudioKillswitch(int(bool(enable))))


def no_render_mode_enabled():
    """Report whether the live Qt no-render mode is currently active."""

    return bool(lib.mPythonQtNoRenderModeEnabled())


def set_no_render_mode(enable):
    """Toggle the live Qt no-render mode from a Python script."""

    return bool(lib.mPythonQtSetNoRenderMode(int(bool(enable))))


def fast_forward_enabled():
    """Report whether the live Qt forced fast-forward toggle is active."""

    return bool(lib.mPythonQtFastForwardEnabled())


def set_fast_forward(enable):
    """Toggle the live Qt forced fast-forward state."""

    return bool(lib.mPythonQtSetFastForward(int(bool(enable))))


def set_fast_forward_ratio(ratio):
    """Set the live Qt fast-forward speed ratio.

    mGBA treats any non-positive value as the menu's "Unbounded" speed.
    """

    return bool(lib.mPythonQtSetFastForwardRatio(float(ratio)))


def open_virtual_pad():
    """Open the live Custom Features Virtual Pad window."""

    return bool(lib.mPythonQtOpenVirtualPad())


def open_virtual_pad_settings():
    """Open the live Virtual Pad settings dialog."""

    return bool(lib.mPythonQtOpenVirtualPadSettings())


def virtual_pad_hold(key, enable=True):
    """Set or clear one Virtual Pad hold by GBA key index."""

    return bool(lib.mPythonQtVirtualPadSetHeld(int(key), int(bool(enable))))


def virtual_pad_autofire(key, enable=True):
    """Set or clear one Virtual Pad autofire toggle by GBA key index."""

    return bool(lib.mPythonQtVirtualPadSetAutofire(int(key), int(bool(enable))))


def virtual_pad_press_for_frames(key, frames):
    """Press one Virtual Pad key for N frames, release it, then pause."""

    frames = int(frames)
    if frames < 1:
        raise ValueError("frames must be positive")
    return bool(lib.mPythonQtVirtualPadPressForFrames(int(key), frames))


def virtual_pad_key_mask():
    """Return the currently visible Custom Features Virtual Pad button mask."""

    return int(getattr(lib, "mPythonQtVirtualPadKeyMask", lambda: 0)())


def controller_key_mask():
    """Return the visible Qt controller's next-frame GBA button mask.

    Keyboard keys have already been resolved through mGBA's input map here. For
    example, if the user's keyboard binding maps X to GBA A, this reports the A
    bit and never exposes the raw X key code.
    """

    return int(getattr(lib, "mPythonQtControllerKeyMask", lambda: 0)())


def virtual_pad_clear():
    """Clear Virtual Pad holds, timed presses, and autofire toggles."""

    return bool(lib.mPythonQtVirtualPadClear())


class QtGBA(object):
    KEY_A = GBA.KEY_A
    KEY_B = GBA.KEY_B
    KEY_SELECT = GBA.KEY_SELECT
    KEY_START = GBA.KEY_START
    KEY_DOWN = GBA.KEY_DOWN
    KEY_UP = GBA.KEY_UP
    KEY_LEFT = GBA.KEY_LEFT
    KEY_RIGHT = GBA.KEY_RIGHT
    KEY_L = GBA.KEY_L
    KEY_R = GBA.KEY_R

    def __init__(self):
        if not is_available():
            raise RuntimeError("Qt scripting bridge is not active")
        # Mirror just enough of the normal GBA wrapper for visible-window
        # automation: memory, keys, frame stepping, and file-backed states.
        self.memory = _QtMemory()
        self._current_keys = 0

    @staticmethod
    def _keys_to_int(*args, **kwargs):
        keys = int(kwargs.get("raw", 0))
        for key in args:
            keys |= 1 << key
        return keys

    @property
    def platform(self):
        # A runtime ROM swap can briefly leave the visible controller in a
        # "started but not fully identified" state. Callers that drive the Qt
        # session should treat zero as "not ready yet", not as a different
        # console family.
        return int(lib.mPythonQtPlatform())

    @property
    def frame_counter(self):
        return int(lib.mPythonQtFrameCounter())

    def reset(self):
        if not lib.mPythonQtReset():
            raise RuntimeError("Qt core reset failed")
        self._current_keys = 0

    def load_rom(self, path):
        path = str(Path(path))
        if not lib.mPythonQtLoadRomFile(path.encode("utf-8")):
            raise RuntimeError("Qt core load_rom failed for {}".format(path))
        self._current_keys = 0

    def load_save_file(self, path, temporary=False):
        path = str(Path(path))
        if not lib.mPythonQtLoadSaveFile(path.encode("utf-8"), int(bool(temporary))):
            raise RuntimeError("Qt core load_save_file failed for {}".format(path))

    def load_temporary_save_file(self, path):
        self.load_save_file(path, temporary=True)

    def export_save_file(self, path):
        path = str(Path(path))
        if not lib.mPythonQtExportSaveFile(path.encode("utf-8")):
            raise RuntimeError("Qt core export_save_file failed for {}".format(path))

    def run_frame(self):
        if not lib.mPythonQtRunFrame():
            raise RuntimeError("Qt core run_frame failed")

    def run_frames(self, count):
        """Run several frames in one native bridge call."""

        if int(count) < 0:
            raise ValueError("count must be non-negative")
        if not lib.mPythonQtRunFrames(int(count)):
            raise RuntimeError("Qt core run_frames failed")

    def run_frames_with_keys(self, keys, count):
        """Set an exact key mask and run several frames in one native call."""

        if int(count) < 0:
            raise ValueError("count must be non-negative")
        self._current_keys = int(keys)
        if not lib.mPythonQtRunFramesWithKeys(int(keys), int(count)):
            raise RuntimeError("Qt core run_frames_with_keys failed")

    def pulse_keys(self, keys, count):
        """Hold an exact key mask for several frames, then restore the old keys."""

        if int(count) < 0:
            raise ValueError("count must be non-negative")
        previous_keys = self._current_keys
        if not lib.mPythonQtPulseKeys(int(keys), int(count)):
            raise RuntimeError("Qt core pulse_keys failed")
        self._current_keys = previous_keys

    def set_keys(self, *args, **kwargs):
        self._current_keys = self._keys_to_int(*args, **kwargs)
        lib.mPythonQtSetKeys(self._current_keys)

    def add_keys(self, *args, **kwargs):
        self._current_keys |= self._keys_to_int(*args, **kwargs)
        lib.mPythonQtSetKeys(self._current_keys)

    def clear_keys(self, *args, **kwargs):
        self._current_keys &= ~self._keys_to_int(*args, **kwargs)
        lib.mPythonQtSetKeys(self._current_keys)

    def get_keys(self):
        # The visible-Qt bridge does not currently expose a native getter for
        # the script-owned exact mask, so keep the last committed raw mask here.
        # Frontend keyboard/controller capture uses controller_key_mask()
        # instead when scripts want mapped live Qt input.
        return int(self._current_keys)

    def pause(self):
        if not pause_current_core():
            raise RuntimeError("Qt core pause failed")

    @property
    def paused(self):
        # Input-tape helpers use this property to avoid re-pausing an already
        # paused Qt core at route boundaries.
        return core_paused()

    @property
    def audio_killswitch_enabled(self):
        # Expose the current custom-feature state on the live visible core so
        # scripts can branch on it without reaching back through the Qt menus.
        return bool(lib.mPythonQtAudioKillswitchEnabled())

    def set_audio_killswitch(self, enable):
        # The object-style wrapper matches the rest of the QtGBA API and raises
        # on failure so higher-level scripts do not silently continue with live
        # audio still enabled.
        if not lib.mPythonQtSetAudioKillswitch(int(bool(enable))):
            raise RuntimeError("Qt core set_audio_killswitch failed")

    @property
    def no_render_mode_enabled(self):
        return bool(lib.mPythonQtNoRenderModeEnabled())

    def set_no_render_mode(self, enable):
        # Route through the Qt window bridge so the black overlay, menu action,
        # and video-layer disable state stay synchronized.
        if not lib.mPythonQtSetNoRenderMode(int(bool(enable))):
            raise RuntimeError("Qt core set_no_render_mode failed")

    @property
    def fast_forward_enabled(self):
        return bool(lib.mPythonQtFastForwardEnabled())

    def set_fast_forward(self, enable):
        if not lib.mPythonQtSetFastForward(int(bool(enable))):
            raise RuntimeError("Qt core set_fast_forward failed")

    def set_fast_forward_ratio(self, ratio):
        # Non-positive values intentionally map to unbounded speed, matching
        # the Qt menu. The first-half scripts pass -1.0 for that exact behavior.
        if not lib.mPythonQtSetFastForwardRatio(float(ratio)):
            raise RuntimeError("Qt core set_fast_forward_ratio failed")

    def open_virtual_pad(self):
        if not lib.mPythonQtOpenVirtualPad():
            raise RuntimeError("Qt core open_virtual_pad failed")

    def open_virtual_pad_settings(self):
        if not lib.mPythonQtOpenVirtualPadSettings():
            raise RuntimeError("Qt core open_virtual_pad_settings failed")

    def virtual_pad_hold(self, key, enable=True):
        if not lib.mPythonQtVirtualPadSetHeld(int(key), int(bool(enable))):
            raise RuntimeError("Qt core virtual_pad_hold failed")

    def virtual_pad_autofire(self, key, enable=True):
        if not lib.mPythonQtVirtualPadSetAutofire(int(key), int(bool(enable))):
            raise RuntimeError("Qt core virtual_pad_autofire failed")

    def virtual_pad_press_for_frames(self, key, frames):
        if int(frames) < 1:
            raise ValueError("frames must be positive")
        if not lib.mPythonQtVirtualPadPressForFrames(int(key), int(frames)):
            raise RuntimeError("Qt core virtual_pad_press_for_frames failed")

    @property
    def virtual_pad_key_mask(self):
        # This samples only the Custom Features Virtual Pad UI state. It is
        # intentionally separate from physical keyboard/controller polling.
        return virtual_pad_key_mask()

    @property
    def controller_key_mask(self):
        # This samples the mapped GBA-button mask queued on the visible Qt
        # controller, including keyboard bindings and Virtual Pad/scripted keys.
        return controller_key_mask()

    def virtual_pad_clear(self):
        if not lib.mPythonQtVirtualPadClear():
            raise RuntimeError("Qt core virtual_pad_clear failed")

    def load_state_file(self, path, flags=0):
        path = str(Path(path))
        if not lib.mPythonQtLoadStateFile(path.encode("utf-8"), int(flags)):
            raise RuntimeError("Qt core load_state_file failed for {}".format(path))

    def save_state_file(self, path, flags=0):
        path = str(Path(path))
        if not lib.mPythonQtSaveStateFile(path.encode("utf-8"), int(flags)):
            raise RuntimeError("Qt core save_state_file failed for {}".format(path))

    def save_scratch_state(self):
        """Capture one in-memory visible-core checkpoint."""

        if not lib.mPythonQtSaveScratchState():
            raise RuntimeError("Qt core save_scratch_state failed")

    def load_scratch_state(self):
        """Restore the previously captured in-memory checkpoint."""

        if not lib.mPythonQtLoadScratchState():
            raise RuntimeError("Qt core load_scratch_state failed")


def current_core():
    # Return a lightweight handle to the already-visible Qt core instead of
    # loading a second hidden emulator instance.
    return QtGBA()
