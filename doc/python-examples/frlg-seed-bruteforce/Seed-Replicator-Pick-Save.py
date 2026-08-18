r"""Replay the locked first-half seed recipe after choosing a `.sav` file.

This is the save-picker companion to `Seed-Replicator.py`. It uses the same
metadata, checkpoint, Audio killswitch, no-render, and unbounded fast-forward
workflow, but asks the user to pick the save file at runtime with the native
Windows Explorer open-file dialog.

The selected save is loaded as a temporary save by the underlying replay helper
so the original `.sav` file is not written back to during replication.
"""

from __future__ import annotations

import ctypes
import importlib.util
import os
import sys
from ctypes import wintypes
from pathlib import Path


PICKED_SAVE_ENV_NAME = "MGBA_FIRSTHALF_REPLICATION_PICKED_SAVE"
DEFAULT_DIALOG_TITLE = "Select FR/LG .sav file for first-half seed replay"
_HELPER_MODULE = None


class OPENFILENAMEW(ctypes.Structure):
    """Win32 common-dialog structure for the native Explorer file picker."""

    _fields_ = [
        ("lStructSize", wintypes.DWORD),
        ("hwndOwner", wintypes.HWND),
        ("hInstance", wintypes.HINSTANCE),
        ("lpstrFilter", wintypes.LPCWSTR),
        ("lpstrCustomFilter", wintypes.LPWSTR),
        ("nMaxCustFilter", wintypes.DWORD),
        ("nFilterIndex", wintypes.DWORD),
        ("lpstrFile", wintypes.LPWSTR),
        ("nMaxFile", wintypes.DWORD),
        ("lpstrFileTitle", wintypes.LPWSTR),
        ("nMaxFileTitle", wintypes.DWORD),
        ("lpstrInitialDir", wintypes.LPCWSTR),
        ("lpstrTitle", wintypes.LPCWSTR),
        ("Flags", wintypes.DWORD),
        ("nFileOffset", wintypes.WORD),
        ("nFileExtension", wintypes.WORD),
        ("lpstrDefExt", wintypes.LPCWSTR),
        ("lCustData", wintypes.LPARAM),
        ("lpfnHook", wintypes.LPVOID),
        ("lpTemplateName", wintypes.LPCWSTR),
        ("pvReserved", wintypes.LPVOID),
        ("dwReserved", wintypes.DWORD),
        ("FlagsEx", wintypes.DWORD),
    ]


def _load_replication_helpers():
    """Load sibling `Seed-Replicator.py` without requiring a legal import name."""

    script_path = Path(__file__).with_name("Seed-Replicator.py")
    spec = importlib.util.spec_from_file_location("firsthalf_replication_helpers", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not build an import spec for {script_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _replication():
    """Return the cached replication helper module."""

    global _HELPER_MODULE
    if _HELPER_MODULE is None:
        _HELPER_MODULE = _load_replication_helpers()
    return _HELPER_MODULE


def _validate_save_path(path: Path) -> Path:
    """Normalize and validate the selected save path before replay starts."""

    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise SystemExit(f"Selected save file does not exist: {resolved}")
    if resolved.suffix.lower() != ".sav":
        raise SystemExit(f"Selected file must have a .sav extension: {resolved}")
    return resolved


def choose_save_file(*, initial_dir: Path | None = None) -> Path:
    """Open the native Windows Explorer file picker and return one `.sav` path.

    Tests and automation can set `MGBA_FIRSTHALF_REPLICATION_PICKED_SAVE` to
    bypass the dialog, but normal GUI use always goes through the Windows common
    open-file dialog.
    """

    override = os.environ.get(PICKED_SAVE_ENV_NAME)
    if override:
        return _validate_save_path(Path(override))
    if sys.platform != "win32":
        raise SystemExit("The save picker variant requires Windows.")

    comdlg32 = ctypes.WinDLL("Comdlg32.dll")
    comdlg32.GetOpenFileNameW.argtypes = [ctypes.POINTER(OPENFILENAMEW)]
    comdlg32.GetOpenFileNameW.restype = wintypes.BOOL
    comdlg32.CommDlgExtendedError.argtypes = []
    comdlg32.CommDlgExtendedError.restype = wintypes.DWORD

    buffer_len = 32768
    file_buffer = ctypes.create_unicode_buffer(buffer_len)
    save_filter = "GBA save files (*.sav)\0*.sav\0All files (*.*)\0*.*\0\0"
    start_dir = str(initial_dir.expanduser().resolve()) if initial_dir else None
    ofn = OPENFILENAMEW()
    ofn.lStructSize = ctypes.sizeof(OPENFILENAMEW)
    ofn.lpstrFilter = save_filter
    ofn.nFilterIndex = 1
    ofn.lpstrFile = ctypes.cast(file_buffer, wintypes.LPWSTR)
    ofn.nMaxFile = buffer_len
    ofn.lpstrInitialDir = start_dir
    ofn.lpstrTitle = DEFAULT_DIALOG_TITLE
    ofn.lpstrDefExt = "sav"
    # Use the Explorer-style native dialog, require an existing file, and avoid
    # changing mGBA's process working directory as a side effect of selection.
    ofn.Flags = 0x00080000 | 0x00001000 | 0x00000800 | 0x00000008

    if not comdlg32.GetOpenFileNameW(ctypes.byref(ofn)):
        error = int(comdlg32.CommDlgExtendedError())
        if error == 0:
            raise SystemExit("Save selection canceled.")
        raise RuntimeError(f"Windows save picker failed: CommDlgExtendedError=0x{error:04X}")
    return _validate_save_path(Path(file_buffer.value))


def main() -> int:
    """Prompt for a `.sav`, then run the normal metadata-driven replay."""

    replication = _replication()
    helper = replication._firsthalf()
    selected_save = choose_save_file(initial_dir=helper.resolve_mgba_dir())
    print(f"Selected replay save file: {selected_save}")
    return replication.run_replay(save_path_override=selected_save)


if __name__ == "__main__":
    exit_code = main()
    if not _replication()._firsthalf()._qt_mode_enabled():
        raise SystemExit(exit_code)
