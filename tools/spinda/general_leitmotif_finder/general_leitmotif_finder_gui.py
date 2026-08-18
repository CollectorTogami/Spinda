#!/usr/bin/env python3
"""Find repeated musical motifs across an audio or video corpus.

The tool is intentionally self-contained: Tkinter for the interface, FFmpeg for
decoding, and NumPy for the analysis. Users select one or more reference time
ranges, scan a folder or file list, then get Markdown, CSV, and JSON reports.

The matcher uses chroma features rather than waveform matching. That makes it
useful for melodic recurrence, including simple key changes when transposition
is enabled. It is not a copyright or fingerprinting tool, and results should be
checked by ear before editorial use.
"""

from __future__ import annotations

import argparse
import bisect
import ctypes
import csv
from dataclasses import dataclass
import datetime as _dt
from functools import lru_cache
import json
import math
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Callable, Iterable
import uuid

try:
    import winsound
except ImportError:  # pragma: no cover - Windows build uses winsound.
    winsound = None

import numpy as np


SUPPORTED_EXTS = {
    ".mp3",
    ".wav",
    ".flac",
    ".m4a",
    ".aac",
    ".ogg",
    ".opus",
    ".wma",
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".webm",
}

ANALYSIS_SAMPLE_RATE = 11025
DISPLAY_SAMPLE_RATE = 8000
FRAME_SIZE = 4096
HOP_SIZE = 1024
MIN_REFERENCE_SECONDS = 1.0
MAX_REFERENCE_SECONDS = 30.0
SCAN_CHUNK_WINDOWS = 512
PREVIEW_SEEK_BACK_SECONDS = 2.0
MAX_PREVIEW_FILES = 24
MAX_LOG_LINES = 1000
MOTIF_SET_SCHEMA = "general-leitmotif-finder-motif-set-v1"
SEQUENCE_SILENCE_SECONDS = 0.12

BG = "#061421"
PANEL = "#0B1D32"
PANEL_2 = "#0E2742"
ENTRY_BG = "#071A2D"
FG = "#D8E8FF"
MUTED = "#91A9C4"
ACCENT = "#2D7DD2"
ACCENT_2 = "#49B6FF"
WARN = "#FFB454"
GRID = "#183B5C"


@dataclass(frozen=True)
class Reference:
    """One user-selected source segment that becomes a motif template."""

    file: Path
    start: float
    end: float
    label: str


@dataclass(frozen=True)
class MotifSet:
    """A reusable collection of reference clips and scan setup fields."""

    references: tuple[Reference, ...]
    corpus_paths: tuple[Path, ...] = ()
    title: str | None = None
    output_folder: Path | None = None
    threshold: str | None = None
    step: str | None = None
    nms: str | None = None
    transpose: bool | None = None
    recursive: bool | None = None


@dataclass(frozen=True)
class SequenceSegment:
    """One source range to render into an exported leitmotif sequence."""

    file: Path
    start: float
    end: float
    hit_count: int = 1
    max_score: float = 0.0


@dataclass(frozen=True)
class TemplateGroup:
    """Templates that share the same frame length and can be scored together."""

    duration_seconds: float
    window_frames: int
    templates: np.ndarray
    labels: tuple[str, ...]


def resource_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return Path(__file__).resolve().parent


def app_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def strip_wrapping_quotes(value: str) -> str:
    """Remove one matching pair of outer quotes without touching path text."""

    text = value.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def ffmpeg_env_candidates(value: str | None) -> list[Path]:
    """Return FFmpeg candidates from an environment variable value.

    Windows users often paste quoted paths or set `FFMPEG` to a `bin` folder.
    Accepting both forms keeps the portable build easier to move between
    machines without weakening the explicit executable lookup.
    """

    if not value or not value.strip():
        return []
    path = Path(strip_wrapping_quotes(value)).expanduser()
    return [path, path / "ffmpeg.exe", path / "ffmpeg"]


def find_ffmpeg() -> str:
    """Return the FFmpeg executable path using portable and common Windows locations."""

    candidates = ffmpeg_env_candidates(os.environ.get("FFMPEG"))
    candidates.extend(
        [
            app_dir() / "ffmpeg.exe",
            resource_dir() / "ffmpeg.exe",
            # Allows a portable folder with source/ beside a bundled ffmpeg.exe.
            app_dir().parent / "ffmpeg.exe",
            Path(r"C:\Program Files\ShareX\ffmpeg.exe"),
            Path(r"C:\msys64\mingw64\bin\ffmpeg.exe"),
        ]
    )
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    found = shutil.which("ffmpeg")
    if found:
        return found
    raise RuntimeError(
        "ffmpeg not found. Put ffmpeg.exe beside this tool, set FFMPEG to its full path, "
        "or set FFMPEG to the folder that contains it."
    )


def parse_timestamp(text: str) -> float:
    """Parse seconds, MM:SS, or HH:MM:SS into seconds."""

    value = text.strip()
    if not value:
        raise ValueError("timestamp is empty")
    parts = value.split(":")
    try:
        if len(parts) == 1:
            seconds = float(parts[0])
            if not math.isfinite(seconds) or seconds < 0:
                raise ValueError
            return seconds
        if len(parts) == 2:
            minutes = int(parts[0])
            seconds = float(parts[1])
            if minutes < 0 or not math.isfinite(seconds) or not (0 <= seconds < 60):
                raise ValueError
            return minutes * 60 + seconds
        if len(parts) == 3:
            hours = int(parts[0])
            minutes = int(parts[1])
            seconds = float(parts[2])
            if (
                hours < 0
                or not (0 <= minutes < 60)
                or not math.isfinite(seconds)
                or not (0 <= seconds < 60)
            ):
                raise ValueError
            return hours * 3600 + minutes * 60 + seconds
    except ValueError as exc:
        raise ValueError(f"invalid timestamp: {text}") from exc
    raise ValueError(f"invalid timestamp: {text}")


def fmt_time(seconds: float) -> str:
    sec = max(0.0, float(seconds))
    hours = int(sec // 3600)
    minutes = int((sec % 3600) // 60)
    rest = sec - hours * 3600 - minutes * 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{rest:05.2f}"
    return f"{minutes:02d}:{rest:05.2f}"


def safe_filename(text: str) -> str:
    cleaned = re.sub(r"[^\w .-]+", "_", text, flags=re.UNICODE).strip()
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = cleaned.rstrip(" ._")
    if not cleaned:
        return "leitmotif corpus"
    reserved = {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }
    cleaned = cleaned[:120].rstrip(" ._") or "leitmotif corpus"
    stem, dot, suffix = cleaned.partition(".")
    if stem.upper() in reserved:
        cleaned = f"{stem}_report{dot}{suffix}"[:120].rstrip(" ._")
    return cleaned or "leitmotif corpus"


def markdown_text(value: object, *, table_cell: bool = False) -> str:
    text = str(value)
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    text = text.replace("`", "'")
    if table_cell:
        text = text.replace("|", "\\|")
    return text


def parse_path_list(text: str) -> list[Path]:
    if not text.strip():
        return []
    try:
        items = next(csv.reader([text], delimiter=";", quotechar='"', skipinitialspace=True, strict=True))
    except csv.Error as exc:
        raise ValueError(f"invalid path list: {exc}") from exc
    paths: list[Path] = []
    for item in items:
        cleaned = item.strip()
        if cleaned:
            paths.append(Path(cleaned).expanduser().resolve())
    return paths


def format_path_list(paths: Iterable[Path]) -> str:
    formatted: list[str] = []
    for path in paths:
        text = str(path)
        if any(char in text for char in ';"\r\n'):
            escaped = text.replace('"', '""')
            text = f'"{escaped}"'
        formatted.append(text)
    return "; ".join(formatted)


def read_seconds_value(value: object, *, label: str) -> float:
    """Read a JSON timestamp stored either as seconds or as formatted text."""

    if isinstance(value, bool):
        raise ValueError(f"{label} must be a timestamp string or seconds number")
    if isinstance(value, (int, float)):
        seconds = float(value)
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError(f"{label} must be a non-negative finite time")
        return seconds
    if isinstance(value, str):
        return parse_timestamp(value)
    raise ValueError(f"{label} must be a timestamp string or seconds number")


def read_optional_string(value: object, *, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{field} must be text")
    text = value.strip()
    return text or None


def read_optional_number_text(value: object, *, field: str) -> str | None:
    """Read a GUI numeric setting saved as text or as a JSON number."""

    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError(f"{field} must be a number or numeric text")
    if isinstance(value, (int, float)):
        number = float(value)
        if not math.isfinite(number):
            raise ValueError(f"{field} must be finite")
        return f"{number:.3g}"
    return read_optional_string(value, field=field)


def saved_scan_settings_as_numbers(
    threshold: str | None,
    step: str | None,
    nms: str | None,
) -> tuple[float, float, float]:
    """Validate optional motif-preset scan settings at load time."""

    defaults = {
        "threshold": 0.60,
        "step": 0.50,
        "nms": 5.00,
    }
    values: dict[str, float] = {}
    for field, text in (("threshold", threshold), ("step", step), ("nms", nms)):
        if text is None:
            values[field] = defaults[field]
            continue
        try:
            values[field] = float(text)
        except ValueError as exc:
            raise ValueError(f"{field} must be numeric") from exc
    validate_scan_settings(values["threshold"], values["step"], values["nms"])
    return values["threshold"], values["step"], values["nms"]


def read_optional_bool(value: object, *, field: str) -> bool | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    raise ValueError(f"{field} must be true or false")


def reference_to_motif_data(ref: Reference) -> dict[str, object]:
    """Convert one reference into the motif-set JSON shape."""

    return {
        "file": str(ref.file),
        "start_seconds": round(ref.start, 6),
        "end_seconds": round(ref.end, 6),
        "start": fmt_time(ref.start),
        "end": fmt_time(ref.end),
        "label": ref.label,
    }


def motif_set_to_data(motif_set: MotifSet) -> dict[str, object]:
    """Return a stable JSON object for a motif-set preset."""

    validate_motif_set(motif_set)
    settings: dict[str, object] = {}
    if motif_set.title is not None:
        settings["title"] = motif_set.title
    if motif_set.output_folder is not None:
        settings["output_folder"] = str(motif_set.output_folder)
    if motif_set.threshold is not None:
        settings["threshold"] = motif_set.threshold
    if motif_set.step is not None:
        settings["step"] = motif_set.step
    if motif_set.nms is not None:
        settings["nms"] = motif_set.nms
    if motif_set.transpose is not None:
        settings["transpose"] = motif_set.transpose
    if motif_set.recursive is not None:
        settings["recursive"] = motif_set.recursive
    return {
        "schema": MOTIF_SET_SCHEMA,
        "created": _dt.datetime.now().isoformat(timespec="seconds"),
        "references": [reference_to_motif_data(ref) for ref in motif_set.references],
        "corpus_paths": [str(path) for path in motif_set.corpus_paths],
        "settings": settings,
    }


def motif_set_from_data(data: object) -> MotifSet:
    """Parse and validate motif-set JSON data."""

    if not isinstance(data, dict):
        raise ValueError("motif data must be a JSON object")
    schema = data.get("schema")
    if schema != MOTIF_SET_SCHEMA:
        raise ValueError(f"unsupported motif data schema: {schema or '(missing)'}")

    raw_references = data.get("references")
    if not isinstance(raw_references, list):
        raise ValueError("motif data references must be a list")
    references: list[Reference] = []
    for index, item in enumerate(raw_references, 1):
        if not isinstance(item, dict):
            raise ValueError(f"reference {index} must be an object")
        file_value = item.get("file")
        if not isinstance(file_value, str) or not file_value.strip():
            raise ValueError(f"reference {index} file must be text")
        path = Path(file_value).expanduser().resolve()
        if path.suffix.lower() not in SUPPORTED_EXTS:
            raise ValueError(f"reference {index} has unsupported file type: {path.suffix or '(none)'}")
        start = read_seconds_value(
            item.get("start_seconds", item.get("start")),
            label=f"reference {index} start",
        )
        end = read_seconds_value(
            item.get("end_seconds", item.get("end")),
            label=f"reference {index} end",
        )
        validate_time_range(
            start,
            end,
            label=f"reference {index}",
            min_seconds=MIN_REFERENCE_SECONDS,
            max_seconds=MAX_REFERENCE_SECONDS,
        )
        label = read_optional_string(item.get("label"), field=f"reference {index} label")
        if label is None:
            label = f"ref{index}: {path.name} {fmt_time(start)}-{fmt_time(end)}"
        references.append(Reference(file=path, start=start, end=end, label=label))
    if not references:
        raise ValueError("motif data must contain at least one reference")

    raw_corpus_paths = data.get("corpus_paths", [])
    if not isinstance(raw_corpus_paths, list):
        raise ValueError("motif data corpus_paths must be a list")
    corpus_paths: list[Path] = []
    for index, item in enumerate(raw_corpus_paths, 1):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"corpus path {index} must be text")
        corpus_paths.append(Path(item).expanduser().resolve())

    settings = data.get("settings", {})
    if settings is None:
        settings = {}
    if not isinstance(settings, dict):
        raise ValueError("motif data settings must be an object")
    output_folder_text = read_optional_string(settings.get("output_folder"), field="output_folder")
    threshold = read_optional_number_text(settings.get("threshold"), field="threshold")
    step = read_optional_number_text(settings.get("step"), field="step")
    nms = read_optional_number_text(settings.get("nms"), field="nms")
    saved_scan_settings_as_numbers(threshold, step, nms)
    return MotifSet(
        references=tuple(references),
        corpus_paths=tuple(corpus_paths),
        title=read_optional_string(settings.get("title"), field="title"),
        output_folder=Path(output_folder_text).expanduser().resolve() if output_folder_text else None,
        threshold=threshold,
        step=step,
        nms=nms,
        transpose=read_optional_bool(settings.get("transpose"), field="transpose"),
        recursive=read_optional_bool(settings.get("recursive"), field="recursive"),
    )


def save_motif_set(path: Path, motif_set: MotifSet) -> None:
    """Write a reusable motif-set preset to disk."""

    atomic_write_text(
        path,
        json.dumps(motif_set_to_data(motif_set), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_motif_set(path: Path) -> MotifSet:
    """Load a reusable motif-set preset from disk."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid motif data JSON: {exc}") from exc
    return motif_set_from_data(data)


def missing_reference_files(references: Iterable[Reference]) -> list[Path]:
    """Return references whose files no longer exist at their saved path."""

    return [ref.file for ref in references if not ref.file.is_file()]


def validate_motif_set(motif_set: MotifSet) -> None:
    """Validate a motif preset before writing or serializing it."""

    if not motif_set.references:
        raise ValueError("motif data must contain at least one reference")
    for index, ref in enumerate(motif_set.references, 1):
        if ref.file.suffix.lower() not in SUPPORTED_EXTS:
            raise ValueError(f"reference {index} has unsupported file type: {ref.file.suffix or '(none)'}")
        validate_time_range(
            ref.start,
            ref.end,
            label=f"reference {index}",
            min_seconds=MIN_REFERENCE_SECONDS,
            max_seconds=MAX_REFERENCE_SECONDS,
        )
    saved_scan_settings_as_numbers(motif_set.threshold, motif_set.step, motif_set.nms)


def validate_scan_settings(threshold: float, step_seconds: float, nms_seconds: float) -> None:
    if not all(math.isfinite(value) for value in (threshold, step_seconds, nms_seconds)):
        raise ValueError("threshold, step, and NMS must be finite numbers")
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be between 0 and 1")
    if step_seconds <= 0:
        raise ValueError("step must be positive")
    if nms_seconds < 0:
        raise ValueError("NMS must not be negative")


def validate_reference_file(path: Path) -> None:
    validate_media_file(path, role="reference")


def validate_media_file(path: Path, *, role: str) -> None:
    if not path.is_file():
        raise ValueError(f"{role} file does not exist")
    if path.suffix.lower() not in SUPPORTED_EXTS:
        raise ValueError(f"unsupported {role} file type: {path.suffix or '(none)'}")


def validate_time_range(
    start: float,
    end: float,
    *,
    label: str = "range",
    min_seconds: float | None = None,
    max_seconds: float | None = None,
) -> None:
    if not math.isfinite(start) or not math.isfinite(end):
        raise ValueError(f"{label} start and end must be finite")
    if start < 0:
        raise ValueError(f"{label} start must not be negative")
    if end <= start:
        raise ValueError(f"{label} end must be after start")
    duration = end - start
    if min_seconds is not None and duration < min_seconds:
        raise ValueError(f"{label} must be at least {min_seconds:.0f}s")
    if max_seconds is not None and duration > max_seconds:
        raise ValueError(f"{label} must be at most {max_seconds:.0f}s")


def clamp_volume_percent(value: object, *, default: float = 100.0) -> float:
    try:
        percent = float(value)
    except (TypeError, ValueError):
        percent = default
    if not math.isfinite(percent):
        percent = default
    return max(0.0, min(100.0, percent))


def volume_percent_to_gain(value: object) -> float:
    """Return a linear FFmpeg gain from a 0-100 preview-volume value."""

    return clamp_volume_percent(value) / 100.0


def volume_percent_to_mci(value: object) -> int:
    """Return a Windows MCI per-device volume value from a 0-100 slider."""

    return int(round(clamp_volume_percent(value) * 10.0))


def build_preview_command(
    ffmpeg: str,
    path: Path,
    start: float,
    end: float,
    output: Path,
    volume_percent: float = 100.0,
) -> list[str]:
    """Build an FFmpeg command that renders the chosen segment as playable WAV."""

    pre_seek = max(0.0, start - PREVIEW_SEEK_BACK_SECONDS)
    fine_seek = start - pre_seek
    duration = end - start
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{pre_seek:.3f}",
        "-i",
        str(path),
        "-ss",
        f"{fine_seek:.3f}",
        "-t",
        f"{duration:.3f}",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
    ]
    gain = volume_percent_to_gain(volume_percent)
    if gain < 0.999 or gain > 1.001:
        command.extend(["-filter:a", f"volume={gain:.4f}"])
    command.extend(
        [
            "-c:a",
            "pcm_s16le",
            str(output),
        ]
    )
    return command


def cleanup_preview_files(
    preview_dir: Path,
    *,
    keep: Path | None = None,
    max_files: int = MAX_PREVIEW_FILES,
) -> None:
    """Keep the preview temp folder small while preserving the active preview file."""

    if max_files < 0:
        raise ValueError("max_files must not be negative")
    entries: list[tuple[float, str, Path]] = []
    keep_path = keep.resolve() if keep is not None else None
    try:
        preview_paths = list(preview_dir.glob("leitmotif_preview_*.wav"))
    except OSError:
        return
    for path in preview_paths:
        try:
            if keep_path is not None and path.resolve() == keep_path:
                continue
            entries.append((path.stat().st_mtime, path.name, path))
        except OSError:
            continue
    entries.sort(reverse=True)
    retained_slots = max(0, max_files - (1 if keep_path is not None else 0))
    for _mtime, _name, path in entries[retained_slots:]:
        try:
            path.unlink()
        except OSError:
            pass


def render_preview_wav(
    ffmpeg: str,
    path: Path,
    start: float,
    end: float,
    output_dir: Path,
    volume_percent: float = 100.0,
) -> Path:
    """Render a selected source range to a temporary WAV for local playback."""

    validate_reference_file(path)
    validate_time_range(start, end, label="preview")
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"leitmotif_preview_{uuid.uuid4().hex}.wav"
    command = build_preview_command(ffmpeg, path, start, end, output, volume_percent)
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg preview failed for {path}: {error or result.returncode}")
    if not output.exists() or output.stat().st_size <= 44:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg preview produced no playable WAV for {path}")
    cleanup_preview_files(output_dir, keep=output)
    return output


def play_preview_wav(path: Path) -> str:
    if winsound is not None:
        try:
            winsound.PlaySound(str(path), winsound.SND_FILENAME | winsound.SND_ASYNC)
            return "winsound"
        except Exception:
            pass
    open_path(path)
    return "system player"


def mci_available() -> bool:
    return sys.platform.startswith("win") and hasattr(ctypes, "WinDLL")


@lru_cache(maxsize=1)
def winmm_dll() -> object:
    if not mci_available():
        raise RuntimeError("Windows MCI playback is unavailable on this platform")
    dll = ctypes.WinDLL("winmm")
    dll.mciSendStringW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
    dll.mciSendStringW.restype = ctypes.c_uint
    dll.mciGetErrorStringW.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
    dll.mciGetErrorStringW.restype = ctypes.c_bool
    return dll


def mci_error_text(code: int) -> str:
    if not mci_available():
        return str(code)
    buffer = ctypes.create_unicode_buffer(256)
    try:
        ok = winmm_dll().mciGetErrorStringW(code, buffer, len(buffer))
    except Exception:
        ok = False
    return buffer.value if ok and buffer.value else str(code)


def mci_send(command: str) -> str:
    """Send one Windows MCI command and return any response text."""

    buffer = ctypes.create_unicode_buffer(512)
    code = winmm_dll().mciSendStringW(command, buffer, len(buffer), None)
    if code:
        raise RuntimeError(f"MCI command failed ({mci_error_text(code)}): {command}")
    return buffer.value


class PreviewPlayer:
    """Small preview player with app-local volume and pause on Windows."""

    def __init__(self) -> None:
        self.alias: str | None = None
        self.backend: str | None = None
        self.paused = False
        self.volume_percent = 100.0

    def play(self, path: Path, *, volume_percent: float = 100.0) -> str:
        self.stop()
        self.volume_percent = clamp_volume_percent(volume_percent)
        if mci_available():
            try:
                self._play_mci(path)
                self.backend = "mci"
                self.paused = False
                return "mci"
            except Exception:
                self.stop()
        self.backend = play_preview_wav(path)
        self.paused = False
        return self.backend

    def _play_mci(self, path: Path) -> None:
        alias = f"glf_preview_{uuid.uuid4().hex}"
        escaped = str(path).replace('"', '\\"')
        try:
            mci_send(f'open "{escaped}" type waveaudio alias {alias}')
        except Exception:
            mci_send(f'open "{escaped}" alias {alias}')
        self.alias = alias
        self.set_volume(self.volume_percent)
        try:
            mci_send(f"set {alias} time format milliseconds")
        except Exception:
            pass
        mci_send(f"play {alias}")

    def can_pause(self) -> bool:
        return self.backend == "mci" and self.alias is not None

    def set_volume(self, volume_percent: float) -> None:
        self.volume_percent = clamp_volume_percent(volume_percent)
        if self.alias is None:
            return
        try:
            mci_send(f"setaudio {self.alias} volume to {volume_percent_to_mci(self.volume_percent)}")
        except Exception:
            pass

    def pause(self) -> None:
        if not self.can_pause():
            raise RuntimeError("pause is only available for the internal Windows preview player")
        mci_send(f"pause {self.alias}")
        self.paused = True

    def resume(self) -> None:
        if not self.can_pause():
            raise RuntimeError("resume is only available for the internal Windows preview player")
        mci_send(f"play {self.alias}")
        self.paused = False

    def stop(self) -> None:
        alias = self.alias
        backend = self.backend
        self.alias = None
        self.backend = None
        self.paused = False
        if alias is not None:
            for command in (f"stop {alias}", f"close {alias}"):
                try:
                    mci_send(command)
                except Exception:
                    pass
            return
        if backend == "winsound" and winsound is not None:
            try:
                winsound.PlaySound(None, 0)
            except Exception:
                pass


def open_path(path: Path) -> None:
    if not path.exists():
        raise FileNotFoundError(str(path))
    if hasattr(os, "startfile"):
        os.startfile(str(path))  # type: ignore[attr-defined]
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    subprocess.Popen([opener, str(path)])


def confidence_from_score(score: float) -> int:
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    points = [(0.0, 0), (0.60, 20), (0.70, 55), (0.80, 80), (0.90, 95), (1.0, 100)]
    if score <= points[0][0]:
        return points[0][1]
    for (x0, y0), (x1, y1) in zip(points, points[1:]):
        if score <= x1:
            frac = (score - x0) / (x1 - x0)
            return int(round(y0 + frac * (y1 - y0)))
    return 100


def strength_from_score(score: float) -> str:
    if not math.isfinite(score):
        raise ValueError("score must be finite")
    if score >= 0.90:
        return "anchor"
    if score >= 0.80:
        return "strong"
    if score >= 0.70:
        return "clear"
    return "echo"


def decode_audio(path: Path, ffmpeg: str, sample_rate: int) -> np.ndarray:
    """Decode media to mono float32 PCM at the requested sample rate.

    FFmpeg writes little-endian float32 (`f32le`). Non-finite samples are muted
    before peak normalization so decode glitches cannot dominate the waveform.
    """

    if sample_rate <= 0:
        raise ValueError("sample rate must be positive")
    command = [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        str(sample_rate),
        "-f",
        "f32le",
        "pipe:1",
    ]
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg failed for {path}: {error or result.returncode}")
    data = result.stdout
    if len(data) % np.dtype("<f4").itemsize:
        raise RuntimeError(f"ffmpeg produced malformed float32 audio for {path}")
    audio = np.frombuffer(data, dtype="<f4").astype(np.float32, copy=True)
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    if peak > 0:
        audio /= peak
    return audio


@lru_cache(maxsize=8)
def make_pitch_map(frame_size: int, sample_rate: int) -> tuple[np.ndarray, np.ndarray]:
    """Map FFT bins into 12 pitch classes for chroma feature extraction."""

    if frame_size <= 0 or sample_rate <= 0:
        raise ValueError("frame size and sample rate must be positive")
    freqs = np.fft.rfftfreq(frame_size, 1 / sample_rate)
    valid = np.where((freqs >= 65) & (freqs <= 5000))[0]
    if len(valid) == 0:
        raise ValueError("sample rate/frame size leaves no usable pitch bins")
    pitch_class = np.round(69 + 12 * np.log2(freqs[valid] / 440.0)).astype(int) % 12
    weights = np.zeros((len(valid), 12), np.float32)
    weights[np.arange(len(valid)), pitch_class] = 1.0
    return valid, weights


@lru_cache(maxsize=8)
def hann_window(frame_size: int) -> np.ndarray:
    if frame_size <= 0:
        raise ValueError("frame size must be positive")
    return np.hanning(frame_size).astype(np.float32)


def validate_feature_matrix(features: np.ndarray) -> None:
    if features.ndim != 2:
        raise ValueError("features must be a 2D matrix")
    if features.shape[1] <= 0:
        raise ValueError("features must have at least one column")
    if features.size and not np.all(np.isfinite(features)):
        raise ValueError("features must contain only finite values")


def validate_pitch_inputs(valid_bins: np.ndarray, pitch_weights: np.ndarray, spectrum_bins: int) -> None:
    if valid_bins.ndim != 1 or valid_bins.size == 0:
        raise ValueError("valid pitch bins must be a non-empty 1D array")
    if not np.issubdtype(valid_bins.dtype, np.integer):
        raise ValueError("valid pitch bins must be integer indices")
    if int(np.min(valid_bins)) < 0 or int(np.max(valid_bins)) >= spectrum_bins:
        raise ValueError("valid pitch bins exceed spectrum size")
    if pitch_weights.shape != (len(valid_bins), 12):
        raise ValueError("pitch weights must have shape (valid bins, 12)")
    if not np.all(np.isfinite(pitch_weights)):
        raise ValueError("pitch weights must contain only finite values")


def validate_template_group(group: TemplateGroup, feature_width: int) -> None:
    if not math.isfinite(group.duration_seconds) or group.duration_seconds <= 0:
        raise ValueError("template duration must be positive and finite")
    if group.window_frames <= 0:
        raise ValueError("template window must be positive")
    if group.templates.ndim != 2 or group.templates.shape[0] == 0:
        raise ValueError("template matrix must be non-empty and 2D")
    expected_width = group.window_frames * feature_width
    if group.templates.shape[1] != expected_width:
        raise ValueError(
            f"template width mismatch: expected {expected_width}, got {group.templates.shape[1]}"
        )
    if len(group.labels) != group.templates.shape[0]:
        raise ValueError("template label count must match template rows")
    if not np.all(np.isfinite(group.templates)):
        raise ValueError("template matrix must contain only finite values")


def smooth_feature_rows(features: np.ndarray, width: int = 5) -> np.ndarray:
    validate_feature_matrix(features)
    if len(features) == 0 or width <= 1:
        return features
    pad_left = width // 2
    pad_right = width - 1 - pad_left
    padded = np.pad(features, ((pad_left, pad_right), (0, 0)), mode="edge")
    cumsum = np.vstack(
        [
            np.zeros((1, padded.shape[1]), dtype=np.float32),
            np.cumsum(padded, axis=0, dtype=np.float32),
        ]
    )
    smoothed = (cumsum[width:] - cumsum[:-width]) / float(width)
    smoothed = smoothed.astype(np.float32, copy=False)
    smoothed /= np.linalg.norm(smoothed, axis=1, keepdims=True) + 1e-6
    return smoothed


def chroma_features(
    audio: np.ndarray,
    sample_rate: int,
    frame_size: int,
    hop_size: int,
    valid_bins: np.ndarray,
    pitch_weights: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Convert audio samples into normalized chroma rows and their center times."""

    if sample_rate <= 0 or frame_size <= 0 or hop_size <= 0:
        raise ValueError("sample rate, frame size, and hop size must be positive")
    validate_pitch_inputs(valid_bins, pitch_weights, frame_size // 2 + 1)
    if len(audio) < frame_size:
        return np.empty((0, 12), np.float32), np.empty((0,), np.float32)

    audio = np.ascontiguousarray(audio, dtype=np.float32)
    audio = np.nan_to_num(audio, nan=0.0, posinf=0.0, neginf=0.0, copy=False)
    frame_count = 1 + (len(audio) - frame_size) // hop_size
    frames = np.lib.stride_tricks.as_strided(
        audio,
        shape=(frame_count, frame_size),
        strides=(audio.strides[0] * hop_size, audio.strides[0]),
    )
    window = hann_window(frame_size)
    features = np.empty((frame_count, 12), dtype=np.float32)
    for start in range(0, frame_count, 512):
        frame_chunk = frames[start : start + 512] * window
        spectrum = np.abs(np.fft.rfft(frame_chunk, axis=1)).astype(np.float32)
        chroma = np.log1p(spectrum[:, valid_bins]) @ pitch_weights
        chroma = np.log1p(chroma)
        chroma -= chroma.mean(axis=1, keepdims=True)
        chroma /= np.linalg.norm(chroma, axis=1, keepdims=True) + 1e-6
        features[start : start + len(chroma)] = chroma.astype(np.float32, copy=False)

    features = smooth_feature_rows(features, width=5)
    times = (np.arange(len(features)) * hop_size + frame_size / 2) / sample_rate
    return features, times


def segment_embedding(features: np.ndarray, start: int, frames: int, shift: int = 0) -> np.ndarray:
    """Flatten one feature segment into a normalized vector template."""

    validate_feature_matrix(features)
    if start < 0 or frames <= 0 or start + frames > len(features):
        raise ValueError("segment embedding range exceeds feature length")
    segment = features[start : start + frames]
    if shift:
        segment = np.roll(segment, shift, axis=1)
    vector = segment.reshape(-1).astype(np.float32)
    vector -= vector.mean()
    vector /= np.linalg.norm(vector) + 1e-6
    return vector


def window_embeddings(
    features: np.ndarray,
    first_start: int,
    count: int,
    step_frames: int,
    window_frames: int,
) -> np.ndarray:
    """Build normalized vectors for sliding windows in one vectorized batch."""

    validate_feature_matrix(features)
    if first_start < 0 or step_frames <= 0 or window_frames <= 0:
        raise ValueError("window embedding parameters must be positive and in range")
    if count <= 0:
        return np.empty((0, window_frames * features.shape[1]), dtype=np.float32)
    last_required = first_start + (count - 1) * step_frames + window_frames
    if last_required > len(features):
        raise ValueError("window embedding range exceeds feature length")
    source = np.ascontiguousarray(features)
    windows = np.lib.stride_tricks.as_strided(
        source[first_start:],
        shape=(count, window_frames, source.shape[1]),
        strides=(source.strides[0] * step_frames, source.strides[0], source.strides[1]),
        writeable=False,
    )
    matrix = windows.reshape(count, window_frames * source.shape[1]).astype(np.float32, copy=True)
    matrix -= matrix.mean(axis=1, keepdims=True)
    matrix /= np.linalg.norm(matrix, axis=1, keepdims=True) + 1e-6
    return matrix


def build_template_groups(
    references: list[Reference],
    ffmpeg: str,
    transpose: bool,
    log: Callable[[str], None] | None = None,
) -> list[TemplateGroup]:
    """Decode references and group template vectors by duration for fast scoring."""

    if not references:
        raise ValueError("add at least one reference sample")
    valid_bins, pitch_weights = make_pitch_map(FRAME_SIZE, ANALYSIS_SAMPLE_RATE)
    grouped_vectors: dict[int, list[np.ndarray]] = {}
    grouped_labels: dict[int, list[str]] = {}
    grouped_duration: dict[int, float] = {}
    feature_cache: dict[Path, tuple[np.ndarray, float]] = {}

    for ref_index, ref in enumerate(references, 1):
        validate_time_range(
            ref.start,
            ref.end,
            label=ref.label,
            min_seconds=MIN_REFERENCE_SECONDS,
            max_seconds=MAX_REFERENCE_SECONDS,
        )
        validate_reference_file(ref.file)
        duration = ref.end - ref.start
        if log:
            log(f"Template {ref_index}/{len(references)}: {ref.label}")
        if ref.file not in feature_cache:
            audio = decode_audio(ref.file, ffmpeg, ANALYSIS_SAMPLE_RATE)
            audio_duration = len(audio) / ANALYSIS_SAMPLE_RATE if ANALYSIS_SAMPLE_RATE else 0.0
            features, _times = chroma_features(
                audio,
                ANALYSIS_SAMPLE_RATE,
                FRAME_SIZE,
                HOP_SIZE,
                valid_bins,
                pitch_weights,
            )
            feature_cache[ref.file] = (features, audio_duration)
        features, audio_duration = feature_cache[ref.file]
        if ref.start >= audio_duration or ref.end > audio_duration + 0.05:
            raise ValueError(
                f"Reference segment outside audio duration: {ref.label} "
                f"ends at {fmt_time(ref.end)}, file is {fmt_time(audio_duration)}"
            )
        frames = max(3, round(duration * ANALYSIS_SAMPLE_RATE / HOP_SIZE))
        start_frame = int(round((ref.start * ANALYSIS_SAMPLE_RATE - FRAME_SIZE / 2) / HOP_SIZE))
        start_frame = max(0, min(start_frame, max(0, len(features) - frames)))
        if len(features) < frames:
            raise ValueError(f"Reference segment exceeds decoded feature length: {ref.label}")
        shifts = range(12) if transpose else range(1)
        for shift in shifts:
            grouped_vectors.setdefault(frames, []).append(
                segment_embedding(features, start_frame, frames, shift)
            )
            label = f"{ref.label} shift {shift}" if transpose else ref.label
            grouped_labels.setdefault(frames, []).append(label)
            grouped_duration.setdefault(frames, frames * HOP_SIZE / ANALYSIS_SAMPLE_RATE)

    groups = []
    for frames in sorted(grouped_vectors):
        groups.append(
            TemplateGroup(
                duration_seconds=grouped_duration[frames],
                window_frames=frames,
                templates=np.vstack(grouped_vectors[frames]).astype(np.float32),
                labels=tuple(grouped_labels[frames]),
            )
        )
    return groups


def collect_corpus(paths: Iterable[Path], recursive: bool) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        try:
            if path.is_dir():
                iterator = path.rglob("*") if recursive else path.iterdir()
                for item in iterator:
                    try:
                        if item.is_file() and item.suffix.lower() in SUPPORTED_EXTS:
                            files.append(item.resolve())
                    except OSError:
                        continue
            elif path.is_file() and path.suffix.lower() in SUPPORTED_EXTS:
                files.append(path.resolve())
        except OSError:
            continue
    seen: set[Path] = set()
    unique = []
    for path in files:
        if path not in seen:
            seen.add(path)
            unique.append(path)
    return sorted(unique, key=lambda p: str(p).lower())


def select_hits(candidates: list[dict[str, object]], nms_seconds: float) -> list[dict[str, object]]:
    """Keep the best hit among overlapping or near-duplicate candidate windows."""

    if not math.isfinite(nms_seconds) or nms_seconds < 0:
        raise ValueError("NMS must be a finite non-negative number")
    if not candidates:
        return []
    validated: list[dict[str, object]] = []
    max_span = 0.0
    for candidate in candidates:
        start = float(candidate["start_seconds"])
        end = float(candidate["end_seconds"])
        score = float(candidate["score"])
        if not all(math.isfinite(value) for value in (start, end, score)):
            raise ValueError("candidate start, end, and score must be finite")
        if start < 0 or end < start:
            raise ValueError("candidate end must be at or after start")
        max_span = max(max_span, end - start)
        validated.append(candidate)
    ranked = sorted(validated, key=lambda item: float(item["score"]), reverse=True)
    guard = max(max_span, nms_seconds)
    selected_by_start: list[tuple[float, float, int, dict[str, object]]] = []
    selected: list[dict[str, object]] = []
    for order, candidate in enumerate(ranked):
        start = float(candidate["start_seconds"])
        end = float(candidate["end_seconds"])
        keep = True
        lower = start - guard
        upper = max(end, start + nms_seconds)
        left = bisect.bisect_left(selected_by_start, (lower, -math.inf, -1))
        right = bisect.bisect_left(selected_by_start, (upper, -math.inf, -1))
        for _hit_start, _hit_end, _hit_order, hit in selected_by_start[left:right]:
            hit_start = float(hit["start_seconds"])
            hit_end = float(hit["end_seconds"])
            starts_close = abs(start - hit_start) < nms_seconds
            overlaps = max(start, hit_start) < min(end, hit_end)
            if starts_close or overlaps:
                keep = False
                break
        if keep:
            selected.append(candidate)
            bisect.insort(selected_by_start, (start, end, order, candidate))
    selected.sort(key=lambda item: float(item["start_seconds"]))
    return selected


def scan_file(
    path: Path,
    ffmpeg: str,
    template_groups: list[TemplateGroup],
    threshold: float,
    step_seconds: float,
    nms_seconds: float,
) -> tuple[list[dict[str, object]], float]:
    """Scan one media file and return de-duplicated motif hits plus duration."""

    validate_scan_settings(threshold, step_seconds, nms_seconds)
    if not template_groups:
        raise ValueError("add at least one reference template")
    validate_media_file(path, role="corpus")
    audio = decode_audio(path, ffmpeg, ANALYSIS_SAMPLE_RATE)
    duration = len(audio) / ANALYSIS_SAMPLE_RATE if ANALYSIS_SAMPLE_RATE else 0.0
    valid_bins, pitch_weights = make_pitch_map(FRAME_SIZE, ANALYSIS_SAMPLE_RATE)
    features, times = chroma_features(
        audio,
        ANALYSIS_SAMPLE_RATE,
        FRAME_SIZE,
        HOP_SIZE,
        valid_bins,
        pitch_weights,
    )
    if len(features) == 0:
        return [], duration

    features = np.ascontiguousarray(features)
    step_frames = max(1, round(step_seconds * ANALYSIS_SAMPLE_RATE / HOP_SIZE))
    candidates: list[dict[str, object]] = []
    for group in template_groups:
        validate_template_group(group, features.shape[1])
        if len(features) < group.window_frames:
            continue
        total_windows = ((len(features) - group.window_frames) // step_frames) + 1
        for offset in range(0, total_windows, SCAN_CHUNK_WINDOWS):
            count = min(SCAN_CHUNK_WINDOWS, total_windows - offset)
            first_start = offset * step_frames
            embeddings = window_embeddings(features, first_start, count, step_frames, group.window_frames)
            score_matrix = embeddings @ group.templates.T
            best_indices = np.argmax(score_matrix, axis=1)
            best_scores = score_matrix[np.arange(count), best_indices]
            hit_rows = np.flatnonzero(best_scores >= threshold)
            for row in hit_rows:
                start_frame = first_start + int(row) * step_frames
                score = float(best_scores[row])
                best_index = int(best_indices[row])
                start_time = float(times[start_frame])
                end_time = min(start_time + group.duration_seconds, duration)
                candidates.append(
                    {
                        "file": str(path),
                        "name": path.name,
                        "start": fmt_time(start_time),
                        "end": fmt_time(end_time),
                        "start_seconds": round(start_time, 3),
                        "end_seconds": round(end_time, 3),
                        "score": round(score, 4),
                        "confidence": confidence_from_score(score),
                        "strength": strength_from_score(score),
                        "duration_seconds": round(duration, 3),
                        "template_duration_seconds": round(group.duration_seconds, 3),
                        "matched_template": group.labels[best_index],
                    }
                )
    return select_hits(candidates, nms_seconds), duration


def finite_float(value: object, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if math.isfinite(number) else default


def normalize_hit(hit: dict[str, object]) -> dict[str, object]:
    """Coerce hit rows into stable output fields for Markdown, CSV, and JSON.

    Display timestamps are rebuilt from sanitized numeric seconds. This keeps
    externally supplied or partially malformed hit dictionaries from leaking
    stale text into reports after the numeric range has been clamped.
    """

    file_text = str(hit.get("file", ""))
    name = str(hit.get("name") or (Path(file_text).name if file_text else "(unknown)"))
    start_seconds = max(0.0, finite_float(hit.get("start_seconds")))
    end_seconds = finite_float(hit.get("end_seconds"), start_seconds)
    if end_seconds < start_seconds:
        end_seconds = start_seconds
    score = finite_float(hit.get("score"))
    confidence_value = hit.get("confidence")
    try:
        confidence = int(confidence_value) if confidence_value is not None else confidence_from_score(score)
    except (TypeError, ValueError):
        confidence = confidence_from_score(score)
    confidence = max(0, min(100, confidence))
    strength = str(hit.get("strength") or strength_from_score(score))
    return {
        "file": file_text,
        "name": name,
        "start": fmt_time(start_seconds),
        "end": fmt_time(end_seconds),
        "start_seconds": round(start_seconds, 3),
        "end_seconds": round(end_seconds, 3),
        "score": round(score, 4),
        "confidence": confidence,
        "strength": strength,
        "template_duration_seconds": round(finite_float(hit.get("template_duration_seconds")), 3),
        "matched_template": str(hit.get("matched_template", "")),
        "duration_seconds": round(finite_float(hit.get("duration_seconds")), 3),
    }


def normalize_error(error: dict[str, object]) -> dict[str, str]:
    return {
        "file": str(error.get("file", "(unknown)")),
        "error": str(error.get("error", "")),
    }


def sequence_segments_from_hits(
    hits: list[dict[str, object]],
    *,
    minimize_overlap: bool,
) -> list[SequenceSegment]:
    """Convert hit rows into source segments for an audio sequence export."""

    segments: list[SequenceSegment] = []
    for hit in (normalize_hit(item) for item in hits):
        file_text = str(hit["file"])
        start = float(hit["start_seconds"])
        end = float(hit["end_seconds"])
        duration = float(hit["duration_seconds"])
        if not file_text or end <= start:
            continue
        if duration > 0:
            end = min(end, duration)
        if end <= start:
            continue
        segments.append(
            SequenceSegment(
                file=Path(file_text),
                start=round(start, 3),
                end=round(end, 3),
                hit_count=1,
                max_score=float(hit["score"]),
            )
        )
    segments.sort(key=lambda segment: (str(segment.file).lower(), segment.start, segment.end))
    if not minimize_overlap:
        return segments

    merged: list[SequenceSegment] = []
    for segment in segments:
        if not merged or segment.file != merged[-1].file or segment.start > merged[-1].end:
            merged.append(segment)
            continue
        previous = merged[-1]
        merged[-1] = SequenceSegment(
            file=previous.file,
            start=previous.start,
            end=max(previous.end, segment.end),
            hit_count=previous.hit_count + segment.hit_count,
            max_score=max(previous.max_score, segment.max_score),
        )
    return merged


def ffmpeg_concat_line(path: Path) -> str:
    """Return one concat-demuxer file line with FFmpeg-compatible quoting.

    FFmpeg treats backslash-escaped apostrophes inside single quotes poorly on
    Windows paths. Closing the quote, escaping the apostrophe, then reopening it
    matches FFmpeg's own quoting rules and keeps sequence exports working when
    folder or file names contain apostrophes.
    """

    escaped = path.as_posix().replace("'", "'\\''")
    return f"file '{escaped}'"


def build_sequence_segment_command(
    ffmpeg: str,
    segment: SequenceSegment,
    output: Path,
) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{segment.start:.3f}",
        "-i",
        str(segment.file),
        "-t",
        f"{segment.end - segment.start:.3f}",
        "-vn",
        "-ac",
        "2",
        "-ar",
        "44100",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "3",
        str(output),
    ]


def validate_sequence_silence(seconds: float) -> None:
    """Validate the optional pause inserted between exported sequence clips."""

    if not math.isfinite(seconds) or seconds < 0:
        raise ValueError("silence seconds must be a finite non-negative number")


def build_sequence_silence_command(ffmpeg: str, output: Path, seconds: float) -> list[str]:
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError("silence render seconds must be finite and positive")
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "lavfi",
        "-i",
        "anullsrc=r=44100:cl=stereo",
        "-t",
        f"{seconds:.3f}",
        "-c:a",
        "libmp3lame",
        "-q:a",
        "4",
        str(output),
    ]


def build_sequence_concat_command(ffmpeg: str, concat_file: Path, output: Path) -> list[str]:
    return [
        ffmpeg,
        "-nostdin",
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_file),
        "-c:a",
        "libmp3lame",
        "-q:a",
        "3",
        str(output),
    ]


def run_ffmpeg_checked(command: list[str], *, output: Path, label: str) -> None:
    """Run FFmpeg and require this invocation to create a non-empty output."""

    try:
        output.unlink(missing_ok=True)
    except OSError as exc:
        raise RuntimeError(f"ffmpeg {label} could not clear stale output: {exc}") from exc
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False)
    if result.returncode != 0:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        error = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"ffmpeg {label} failed: {error or result.returncode}")
    if not output.exists() or output.stat().st_size == 0:
        try:
            output.unlink(missing_ok=True)
        except OSError:
            pass
        raise RuntimeError(f"ffmpeg {label} produced no output: {output}")


def write_sequence_manifest(
    path: Path,
    segments: list[SequenceSegment],
    *,
    silence_seconds: float,
) -> None:
    fields = [
        "index",
        "file",
        "name",
        "source_start",
        "source_end",
        "source_start_seconds",
        "source_end_seconds",
        "output_start",
        "output_end",
        "output_start_seconds",
        "output_end_seconds",
        "hit_count",
        "max_score",
    ]
    rows: list[dict[str, object]] = []
    cursor = 0.0
    for index, segment in enumerate(segments, 1):
        duration = segment.end - segment.start
        rows.append(
            {
                "index": index,
                "file": str(segment.file),
                "name": segment.file.name,
                "source_start": fmt_time(segment.start),
                "source_end": fmt_time(segment.end),
                "source_start_seconds": round(segment.start, 3),
                "source_end_seconds": round(segment.end, 3),
                "output_start": fmt_time(cursor),
                "output_end": fmt_time(cursor + duration),
                "output_start_seconds": round(cursor, 3),
                "output_end_seconds": round(cursor + duration, 3),
                "hit_count": segment.hit_count,
                "max_score": round(segment.max_score, 4),
            }
        )
        cursor += duration
        if index != len(segments):
            cursor += silence_seconds
    atomic_write_csv(path, fields, rows)


def export_leitmotif_sequence(
    ffmpeg: str,
    output_dir: Path,
    title: str,
    hits: list[dict[str, object]],
    *,
    minimize_overlap: bool,
    silence_seconds: float = SEQUENCE_SILENCE_SECONDS,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, Path, int]:
    """Render a sequential MP3 preview of all detected leitmotif hits."""

    validate_sequence_silence(silence_seconds)
    segments = sequence_segments_from_hits(hits, minimize_overlap=minimize_overlap)
    if not segments:
        raise ValueError("no hit ranges available for sequence export")
    output_dir.mkdir(parents=True, exist_ok=True)
    for source in sorted({segment.file for segment in segments}, key=lambda path: str(path).lower()):
        validate_media_file(source, role="sequence source")
    base = safe_filename(title)
    sequence_path = output_dir / f"{base} leitmotif sequence.mp3"
    manifest_path = output_dir / f"{base} leitmotif sequence.csv"
    with tempfile.TemporaryDirectory(prefix="general_leitmotif_sequence_", dir=output_dir) as tmp:
        temp_dir = Path(tmp)
        silence_path: Path | None = None
        if len(segments) > 1 and silence_seconds > 0:
            silence_path = temp_dir / "silence.mp3"
            run_ffmpeg_checked(
                build_sequence_silence_command(ffmpeg, silence_path, silence_seconds),
                output=silence_path,
                label="silence render",
            )
        concat_lines: list[str] = []
        for index, segment in enumerate(segments, 1):
            segment_path = temp_dir / f"{index:06d}.mp3"
            run_ffmpeg_checked(
                build_sequence_segment_command(ffmpeg, segment, segment_path),
                output=segment_path,
                label=f"sequence segment {index}",
            )
            concat_lines.append(ffmpeg_concat_line(segment_path))
            if index != len(segments) and silence_path is not None:
                concat_lines.append(ffmpeg_concat_line(silence_path))
            if log is not None and (index == len(segments) or index % 25 == 0):
                log(f"  sequence segments rendered: {index}/{len(segments)}")
        concat_path = temp_dir / "concat.txt"
        concat_path.write_text("\n".join(concat_lines) + "\n", encoding="utf-8")
        temp_sequence_path = temp_dir / "sequence.mp3"
        run_ffmpeg_checked(
            build_sequence_concat_command(ffmpeg, concat_path, temp_sequence_path),
            output=temp_sequence_path,
            label="sequence concat",
        )
        temp_sequence_path.replace(sequence_path)
    write_sequence_manifest(manifest_path, segments, silence_seconds=silence_seconds)
    return sequence_path, manifest_path, len(segments)


def write_outputs(
    output_dir: Path,
    title: str,
    references: list[Reference],
    corpus_files: list[Path],
    hits: list[dict[str, object]],
    errors: list[dict[str, object]],
    settings: dict[str, object],
) -> tuple[Path, Path]:
    """Write Markdown, CSV, and JSON reports for one scan run."""

    output_dir.mkdir(parents=True, exist_ok=True)
    base = safe_filename(title)
    csv_path = output_dir / f"{base}.csv"
    md_path = output_dir / f"{base}.md"
    created = _dt.datetime.now().isoformat(timespec="seconds")

    fields = [
        "file",
        "name",
        "start",
        "end",
        "start_seconds",
        "end_seconds",
        "score",
        "confidence",
        "strength",
        "template_duration_seconds",
        "matched_template",
        "duration_seconds",
    ]
    normalized_hits = [normalize_hit(hit) for hit in hits]
    normalized_errors = [normalize_error(error) for error in errors]
    atomic_write_csv(csv_path, fields, normalized_hits)

    json_path = output_dir / f"{base}.json"
    atomic_write_text(
        json_path,
        json.dumps(
            {
                "created": created,
                "title": title,
                "settings": settings,
                "references": [
                    {
                        "file": str(ref.file),
                        "start": fmt_time(ref.start),
                        "end": fmt_time(ref.end),
                        "label": ref.label,
                    }
                    for ref in references
                ],
                "corpus_files": [str(path) for path in corpus_files],
                "hits": normalized_hits,
                "errors": normalized_errors,
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        encoding="utf-8",
    )

    strength_counts: dict[str, int] = {}
    for hit in normalized_hits:
        strength = str(hit["strength"])
        strength_counts[strength] = strength_counts.get(strength, 0) + 1

    lines = [
        f"# {markdown_text(title)}",
        "",
        f"Created: `{created}`",
        "",
        f"Files scanned: `{len(corpus_files)}`",
        f"Hits found: `{len(normalized_hits)}`",
        f"CSV output: `{markdown_text(csv_path)}`",
        "",
        "## Settings",
        "",
    ]
    for key, value in settings.items():
        lines.append(f"- `{markdown_text(key)}`: `{markdown_text(value)}`")
    lines.extend(["", "## References", ""])
    lines.append("| Label | File | Time |")
    lines.append("|---|---|---:|")
    for ref in references:
        lines.append(
            f"| `{markdown_text(ref.label, table_cell=True)}` | "
            f"`{markdown_text(ref.file, table_cell=True)}` | "
            f"`{fmt_time(ref.start)}-{fmt_time(ref.end)}` |"
        )
    lines.extend(["", "## Strength Summary", ""])
    if strength_counts:
        for name in ("anchor", "strong", "clear", "echo"):
            lines.append(f"- `{name}`: `{strength_counts.get(name, 0)}`")
    else:
        lines.append("No hits.")
    lines.extend(["", "## Hits", ""])
    if normalized_hits:
        lines.append("| File | Time | Score | Confidence | Strength | Matched template |")
        lines.append("|---|---:|---:|---:|---|---|")
        for hit in normalized_hits:
            lines.append(
                f"| `{markdown_text(hit['name'], table_cell=True)}` | "
                f"`{markdown_text(hit['start'])}-{markdown_text(hit['end'])}` | "
                f"{float(hit['score']):.4f} | {hit['confidence']} | "
                f"{markdown_text(hit['strength'], table_cell=True)} | "
                f"`{markdown_text(hit['matched_template'], table_cell=True)}` |"
            )
    else:
        lines.append("No hits above threshold.")
    if normalized_errors:
        lines.extend(["", "## Errors", ""])
        for error in normalized_errors:
            lines.append(f"- `{markdown_text(error['file'])}`: `{markdown_text(error['error'])}`")
    atomic_write_text(md_path, "\n".join(lines) + "\n", encoding="utf-8")
    return md_path, csv_path


def safe_print(message: str) -> None:
    stream = getattr(sys, "stdout", None)
    if stream is not None:
        try:
            print(message)
        except Exception:
            return


def atomic_write_text(path: Path, text: str, *, encoding: str = "utf-8") -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        temp_path.write_text(text, encoding=encoding)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def atomic_write_csv(path: Path, fields: list[str], rows: list[dict[str, object]]) -> None:
    temp_path = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temp_path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
            writer.writeheader()
            writer.writerows(rows)
        temp_path.replace(path)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


class WaveformView(tk.Canvas):
    """Small waveform selector used to choose reference start and end times."""

    def __init__(self, master: tk.Widget, on_selection: Callable[[float, float], None], **kwargs: object):
        super().__init__(master, bg=ENTRY_BG, highlightthickness=1, highlightbackground=GRID, **kwargs)
        self.on_selection = on_selection
        self.duration = 0.0
        self.envelope: list[tuple[float, float]] = []
        self.start = 0.0
        self.end = 0.0
        self._dragging: str | None = None
        self.bind("<Configure>", lambda _event: self.redraw())
        self.bind("<Button-1>", self._mouse_down)
        self.bind("<B1-Motion>", self._mouse_drag)
        self.bind("<ButtonRelease-1>", self._mouse_up)

    def load_audio(self, path: Path, ffmpeg: str) -> None:
        audio = decode_audio(path, ffmpeg, DISPLAY_SAMPLE_RATE)
        self.duration = len(audio) / DISPLAY_SAMPLE_RATE if DISPLAY_SAMPLE_RATE else 0.0
        self.envelope = self._build_envelope(audio)
        self.start, self.end = self._clamp_selection(0.0, min(6.0, self.duration))
        self.redraw()
        self.on_selection(self.start, self.end)

    def set_selection(self, start: float, end: float) -> None:
        if self.duration <= 0:
            return
        self.start, self.end = self._clamp_selection(start, end)
        self.redraw()
        self.on_selection(self.start, self.end)

    def _clamp_selection(self, start: float, end: float) -> tuple[float, float]:
        if self.duration <= 0:
            return 0.0, 0.0
        minimum = min(0.1, self.duration)
        start = max(0.0, min(start, max(0.0, self.duration - minimum)))
        end = max(start + minimum, min(end, self.duration))
        if end > self.duration:
            end = self.duration
            start = max(0.0, end - minimum)
        return start, end

    def _build_envelope(self, audio: np.ndarray) -> list[tuple[float, float]]:
        if audio.size == 0:
            return []
        canvas_width = int(self.winfo_width() or 0)
        width = canvas_width if canvas_width >= 32 else 900
        block = max(1, math.ceil(len(audio) / width))
        full_blocks, remainder = divmod(len(audio), block)
        envelope: list[tuple[float, float]] = []
        if full_blocks:
            blocks = audio[: full_blocks * block].reshape(full_blocks, block)
            lows = np.min(blocks, axis=1)
            highs = np.max(blocks, axis=1)
            envelope.extend((float(low), float(high)) for low, high in zip(lows, highs))
        if remainder:
            chunk = audio[full_blocks * block :]
            envelope.append((float(np.min(chunk)), float(np.max(chunk))))
        return envelope

    def _time_to_x(self, seconds: float) -> float:
        width = max(1, self.winfo_width())
        return (seconds / self.duration) * width if self.duration else 0.0

    def _x_to_time(self, x_coord: float) -> float:
        width = max(1, self.winfo_width())
        return max(0.0, min(self.duration, (x_coord / width) * self.duration))

    def redraw(self) -> None:
        self.delete("all")
        width = max(1, self.winfo_width())
        height = max(1, self.winfo_height())
        mid = height / 2
        self.create_rectangle(0, 0, width, height, fill=ENTRY_BG, outline=GRID)
        if not self.envelope or self.duration <= 0:
            self.create_text(
                width / 2,
                height / 2,
                fill=MUTED,
                text="Load a reference file to view waveform",
            )
            return
        x_scale = width / max(1, len(self.envelope))
        for index, (low, high) in enumerate(self.envelope):
            x = index * x_scale
            y1 = mid - high * (height * 0.42)
            y2 = mid - low * (height * 0.42)
            self.create_line(x, y1, x, y2, fill=ACCENT_2)
        start_x = self._time_to_x(self.start)
        end_x = self._time_to_x(self.end)
        self.create_rectangle(start_x, 0, end_x, height, fill="#0C3357", stipple="gray50", outline="")
        self.create_line(start_x, 0, start_x, height, fill=ACCENT_2, width=3)
        self.create_line(end_x, 0, end_x, height, fill=WARN, width=3)
        self.create_text(start_x + 6, 12, anchor="w", fill=ACCENT_2, text=fmt_time(self.start))
        self.create_text(end_x - 6, 28, anchor="e", fill=WARN, text=fmt_time(self.end))

    def _mouse_down(self, event: tk.Event) -> None:
        if self.duration <= 0:
            return
        start_x = self._time_to_x(self.start)
        end_x = self._time_to_x(self.end)
        self._dragging = "start" if abs(event.x - start_x) <= abs(event.x - end_x) else "end"
        self._mouse_drag(event)

    def _mouse_drag(self, event: tk.Event) -> None:
        if self.duration <= 0 or self._dragging is None:
            return
        value = self._x_to_time(float(event.x))
        if self._dragging == "start":
            self.start, self.end = self._clamp_selection(value, self.end)
        else:
            self.start, self.end = self._clamp_selection(self.start, value)
        self.redraw()
        self.on_selection(self.start, self.end)

    def _mouse_up(self, _event: tk.Event) -> None:
        self._dragging = None


class LeitmotifFinderApp:
    """Tkinter application shell for reference selection, scan setup, and reports."""

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("General Leitmotif Finder")
        self.root.geometry("1180x760")
        self.root.configure(bg=BG)
        self.ffmpeg = find_ffmpeg()
        self.references: list[Reference] = []
        self.corpus_paths: list[Path] = []
        self.log_queue: queue.Queue[object] = queue.Queue()
        self.scan_thread: threading.Thread | None = None
        self.closing = False
        self.preview_dir = Path(tempfile.gettempdir()) / "general_leitmotif_finder_previews"
        self.preview_player = PreviewPlayer()
        self.preview_file: Path | None = None
        self.last_md_path: Path | None = None
        self.last_csv_path: Path | None = None
        self.last_sequence_path: Path | None = None
        self.last_output_dir: Path | None = None
        self.output_buttons: list[ttk.Button] = []

        self.ref_file_var = tk.StringVar()
        self.start_var = tk.StringVar(value="00:00.00")
        self.end_var = tk.StringVar(value="00:06.00")
        self.corpus_var = tk.StringVar()
        self.output_var = tk.StringVar(value=str(app_dir() / "leitmotif_results"))
        self.title_var = tk.StringVar(value="leitmotif corpus")
        self.threshold_var = tk.StringVar(value="0.60")
        self.step_var = tk.StringVar(value="0.50")
        self.nms_var = tk.StringVar(value="5.00")
        self.transpose_var = tk.BooleanVar(value=True)
        self.recursive_var = tk.BooleanVar(value=True)
        self.preview_volume_var = tk.DoubleVar(value=80.0)
        self.preview_volume_label_var = tk.StringVar(value="80%")
        self.export_sequence_var = tk.BooleanVar(value=False)
        self.sequence_min_overlap_var = tk.BooleanVar(value=True)

        self._configure_styles()
        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._close)
        self.root.after(100, self._drain_log_queue)

    def _configure_styles(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, fieldbackground=ENTRY_BG)
        style.configure("TFrame", background=BG)
        style.configure("Panel.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Panel.TLabel", background=PANEL, foreground=FG)
        style.configure("Muted.TLabel", background=PANEL, foreground=MUTED)
        style.configure("TButton", background=PANEL_2, foreground=FG, bordercolor=GRID)
        style.map("TButton", background=[("active", ACCENT), ("pressed", "#1E5F9E")])
        style.configure("TEntry", fieldbackground=ENTRY_BG, foreground=FG, insertcolor=FG)
        style.configure("TCheckbutton", background=PANEL, foreground=FG)
        style.map("TCheckbutton", background=[("active", PANEL)], foreground=[("active", FG)])
        style.configure("TLabelframe", background=PANEL, foreground=FG, bordercolor=GRID)
        style.configure("TLabelframe.Label", background=PANEL, foreground=ACCENT_2)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self.root, style="TFrame", padding=10)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)
        outer.rowconfigure(1, weight=0)

        left = ttk.LabelFrame(outer, text="Reference samples", padding=10)
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        right = ttk.LabelFrame(outer, text="Corpus scan", padding=10)
        right.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self._build_reference_panel(left)
        self._build_scan_panel(right)
        self._build_log_panel(outer)

    def _build_reference_panel(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Reference file:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.ref_file_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(parent, text="Browse...", command=self._browse_reference).grid(row=0, column=2)

        self.waveform = WaveformView(parent, self._waveform_selection_changed, height=220)
        self.waveform.grid(row=1, column=0, columnspan=3, sticky="nsew", pady=10)
        parent.rowconfigure(1, weight=1)

        ttk.Label(parent, text="Start:", style="Panel.TLabel").grid(row=2, column=0, sticky="w")
        start_entry = ttk.Entry(parent, textvariable=self.start_var, width=12)
        start_entry.grid(row=2, column=1, sticky="w", padx=6)
        ttk.Label(parent, text="End:", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(6, 0))
        end_entry = ttk.Entry(parent, textvariable=self.end_var, width=12)
        end_entry.grid(row=3, column=1, sticky="w", padx=6, pady=(6, 0))
        ttk.Button(parent, text="Apply boxes", command=self._apply_time_boxes).grid(
            row=2, column=2, rowspan=2, sticky="ew"
        )

        button_row = ttk.Frame(parent, style="Panel.TFrame")
        button_row.grid(row=4, column=0, columnspan=3, sticky="ew", pady=10)
        ttk.Button(button_row, text="Add reference", command=self._add_reference).pack(side="left")
        ttk.Button(button_row, text="Remove selected", command=self._remove_reference).pack(
            side="left", padx=6
        )
        ttk.Button(button_row, text="Preview boxes", command=self._preview_time_boxes).pack(
            side="left", padx=(12, 6)
        )
        ttk.Button(button_row, text="Preview selected", command=self._preview_selected_reference).pack(
            side="left", padx=(0, 6)
        )
        self.preview_pause_button = ttk.Button(
            button_row,
            text="Pause",
            command=self._toggle_preview_pause,
            state="disabled",
        )
        self.preview_pause_button.pack(side="left", padx=(0, 6))
        self.stop_preview_button = ttk.Button(button_row, text="Stop preview", command=self._stop_preview)
        self.stop_preview_button.pack(side="left")

        playback_row = ttk.Frame(parent, style="Panel.TFrame")
        playback_row.grid(row=5, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        playback_row.columnconfigure(1, weight=1)
        ttk.Label(playback_row, text="Preview volume:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Scale(
            playback_row,
            from_=0,
            to=100,
            variable=self.preview_volume_var,
            command=self._preview_volume_changed,
        ).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Label(playback_row, textvariable=self.preview_volume_label_var, style="Panel.TLabel", width=5).grid(
            row=0,
            column=2,
            sticky="e",
        )

        self.ref_list = tk.Listbox(
            parent,
            height=8,
            bg=ENTRY_BG,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground=GRID,
            borderwidth=0,
        )
        self.ref_list.grid(row=6, column=0, columnspan=3, sticky="nsew")
        parent.rowconfigure(6, weight=1)

        data_row = ttk.Frame(parent, style="Panel.TFrame")
        data_row.grid(row=7, column=0, columnspan=3, sticky="ew", pady=(8, 0))
        ttk.Button(data_row, text="Load motif data...", command=self._load_motif_data).pack(side="left")
        ttk.Button(data_row, text="Save motif data...", command=self._save_motif_data).pack(
            side="left", padx=6
        )

    def _build_scan_panel(self, parent: ttk.LabelFrame) -> None:
        parent.columnconfigure(1, weight=1)
        ttk.Label(parent, text="Full song corpus:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(parent, textvariable=self.corpus_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(parent, text="Full song folder...", command=self._browse_corpus_folder).grid(row=0, column=2)
        ttk.Button(parent, text="Files...", command=self._browse_corpus_files).grid(row=0, column=3, padx=(6, 0))

        self.corpus_list = tk.Listbox(
            parent,
            height=7,
            bg=ENTRY_BG,
            fg=FG,
            selectbackground=ACCENT,
            selectforeground="#FFFFFF",
            highlightthickness=1,
            highlightbackground=GRID,
            borderwidth=0,
        )
        self.corpus_list.grid(row=1, column=0, columnspan=4, sticky="nsew", pady=8)
        parent.rowconfigure(1, weight=1)
        ttk.Button(parent, text="Clear corpus", command=self._clear_corpus).grid(row=2, column=0, sticky="w")
        ttk.Checkbutton(parent, text="Recursive folders", variable=self.recursive_var).grid(
            row=2, column=1, sticky="w"
        )

        ttk.Label(parent, text="Output folder:", style="Panel.TLabel").grid(row=3, column=0, sticky="w", pady=(12, 0))
        ttk.Entry(parent, textvariable=self.output_var).grid(row=3, column=1, columnspan=2, sticky="ew", padx=6, pady=(12, 0))
        ttk.Button(parent, text="Browse...", command=self._browse_output).grid(row=3, column=3, pady=(12, 0))

        ttk.Label(parent, text="Report title:", style="Panel.TLabel").grid(row=4, column=0, sticky="w", pady=(8, 0))
        ttk.Entry(parent, textvariable=self.title_var).grid(row=4, column=1, columnspan=3, sticky="ew", padx=6, pady=(8, 0))

        settings = ttk.Frame(parent, style="Panel.TFrame")
        settings.grid(row=5, column=0, columnspan=4, sticky="ew", pady=12)
        for index in range(6):
            settings.columnconfigure(index, weight=1)
        ttk.Label(settings, text="Threshold:", style="Panel.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.threshold_var, width=8).grid(row=0, column=1, sticky="w")
        ttk.Label(settings, text="Step:", style="Panel.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Entry(settings, textvariable=self.step_var, width=8).grid(row=0, column=3, sticky="w")
        ttk.Label(settings, text="NMS:", style="Panel.TLabel").grid(row=0, column=4, sticky="w")
        ttk.Entry(settings, textvariable=self.nms_var, width=8).grid(row=0, column=5, sticky="w")
        ttk.Checkbutton(settings, text="Allow transposition", variable=self.transpose_var).grid(
            row=1, column=0, columnspan=3, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(settings, text="Export sequence", variable=self.export_sequence_var).grid(
            row=1, column=3, columnspan=2, sticky="w", pady=(8, 0)
        )
        ttk.Checkbutton(settings, text="Minimize overlap", variable=self.sequence_min_overlap_var).grid(
            row=1, column=5, sticky="w", pady=(8, 0)
        )

        self.run_button = ttk.Button(parent, text="Run scan", command=self._run_scan)
        self.run_button.grid(row=6, column=0, sticky="w", pady=(4, 0))
        self.open_md_button = ttk.Button(
            parent,
            text="Open Markdown",
            command=lambda: self._open_last_output("md"),
            state="disabled",
        )
        self.open_md_button.grid(row=6, column=1, sticky="w", pady=(4, 0))
        self.open_csv_button = ttk.Button(
            parent,
            text="Open CSV",
            command=lambda: self._open_last_output("csv"),
            state="disabled",
        )
        self.open_csv_button.grid(row=6, column=2, sticky="w", pady=(4, 0))
        self.open_output_folder_button = ttk.Button(
            parent,
            text="Open output folder",
            command=lambda: self._open_last_output("folder"),
            state="disabled",
        )
        self.open_output_folder_button.grid(row=6, column=3, sticky="w", pady=(4, 0))
        self.open_sequence_button = ttk.Button(
            parent,
            text="Open sequence",
            command=lambda: self._open_last_output("sequence"),
            state="disabled",
        )
        self.open_sequence_button.grid(row=7, column=1, sticky="w", pady=(6, 0))
        self.output_buttons = [
            self.open_md_button,
            self.open_csv_button,
            self.open_output_folder_button,
            self.open_sequence_button,
        ]

    def _build_log_panel(self, parent: ttk.Frame) -> None:
        frame = ttk.LabelFrame(parent, text="Log", padding=6)
        frame.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        self.log_text = tk.Text(
            frame,
            height=8,
            bg=ENTRY_BG,
            fg=FG,
            insertbackground=FG,
            relief="flat",
            wrap="word",
        )
        self.log_text.pack(fill="both", expand=True)
        self._log(f"ffmpeg: {self.ffmpeg}")

    def _log(self, message: str) -> None:
        self.log_text.insert("end", message + "\n")
        try:
            current_lines = int(self.log_text.index("end-1c").split(".", 1)[0])
        except (tk.TclError, ValueError):
            current_lines = 0
        excess_lines = current_lines - MAX_LOG_LINES
        if excess_lines > 0:
            self.log_text.delete("1.0", f"{excess_lines + 1}.0")
        self.log_text.see("end")

    def _queue_log(self, message: str) -> None:
        self.log_queue.put(message)

    def _queue_scan_done(self) -> None:
        self.log_queue.put(("scan_done", None))

    def _queue_scan_outputs(
        self,
        md_path: Path,
        csv_path: Path,
        output_dir: Path,
        sequence_path: Path | None,
    ) -> None:
        self.log_queue.put(("scan_outputs", md_path, csv_path, output_dir, sequence_path))

    def _close(self) -> None:
        self.closing = True
        self._stop_preview(show_errors=False)
        self.root.destroy()

    def _drain_log_queue(self) -> None:
        if self.closing:
            return
        while True:
            try:
                event = self.log_queue.get_nowait()
            except queue.Empty:
                break
            if event == ("scan_done", None):
                self.run_button.configure(state="normal")
            elif isinstance(event, tuple) and len(event) >= 4 and event[0] == "scan_outputs":
                self.last_md_path = event[1]
                self.last_csv_path = event[2]
                self.last_output_dir = event[3]
                self.last_sequence_path = event[4] if len(event) >= 5 else None
                self._set_output_buttons_state("normal")
                if hasattr(self, "open_sequence_button"):
                    self.open_sequence_button.configure(
                        state="normal" if self.last_sequence_path else "disabled"
                    )
            else:
                self._log(str(event))
        if not self.closing:
            try:
                self.root.after(100, self._drain_log_queue)
            except tk.TclError:
                self.closing = True

    def _refresh_reference_list(self) -> None:
        self.ref_list.delete(0, "end")
        for ref in self.references:
            self.ref_list.insert("end", ref.label)

    def _current_motif_set(self) -> MotifSet:
        if not self.references:
            raise ValueError("add at least one reference sample before saving motif data")
        threshold, step, nms = self._read_scan_settings()
        typed = parse_path_list(self.corpus_var.get())
        if typed != self.corpus_paths:
            self._replace_corpus_paths(typed)
        output_text = self.output_var.get().strip()
        return MotifSet(
            references=tuple(self.references),
            corpus_paths=tuple(self.corpus_paths),
            title=self.title_var.get().strip() or "leitmotif corpus",
            output_folder=Path(output_text).expanduser().resolve() if output_text else None,
            threshold=f"{threshold:.3g}",
            step=f"{step:.3g}",
            nms=f"{nms:.3g}",
            transpose=bool(self.transpose_var.get()),
            recursive=bool(self.recursive_var.get()),
        )

    def _save_motif_data(self) -> None:
        try:
            motif_set = self._current_motif_set()
        except ValueError as exc:
            messagebox.showerror("Cannot save motif data", str(exc))
            return
        initial_dir = motif_set.output_folder if motif_set.output_folder and motif_set.output_folder.is_dir() else app_dir()
        initial_file = f"{safe_filename(motif_set.title or 'leitmotif corpus')} motif data.json"
        path = filedialog.asksaveasfilename(
            title="Save motif data",
            defaultextension=".json",
            initialdir=str(initial_dir),
            initialfile=initial_file,
            filetypes=[("Motif data JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            save_motif_set(Path(path).expanduser().resolve(), motif_set)
        except Exception as exc:
            messagebox.showerror("Save motif data failed", str(exc))
            return
        self._log(f"Saved motif data: {path}")

    def _apply_motif_set(self, motif_set: MotifSet) -> None:
        self.references = list(motif_set.references)
        self._refresh_reference_list()
        self._replace_corpus_paths(list(motif_set.corpus_paths))
        if motif_set.title is not None:
            self.title_var.set(motif_set.title)
        if motif_set.output_folder is not None:
            self.output_var.set(str(motif_set.output_folder))
        if motif_set.threshold is not None:
            self.threshold_var.set(motif_set.threshold)
        if motif_set.step is not None:
            self.step_var.set(motif_set.step)
        if motif_set.nms is not None:
            self.nms_var.set(motif_set.nms)
        if motif_set.transpose is not None:
            self.transpose_var.set(motif_set.transpose)
        if motif_set.recursive is not None:
            self.recursive_var.set(motif_set.recursive)
        if not self.references:
            return
        first = self.references[0]
        self.ref_file_var.set(str(first.file))
        self.start_var.set(fmt_time(first.start))
        self.end_var.set(fmt_time(first.end))
        if first.file.is_file():
            try:
                self.waveform.load_audio(first.file, self.ffmpeg)
                self.waveform.set_selection(first.start, first.end)
            except Exception as exc:
                self._log(f"Waveform load skipped: {exc}")

    def _load_motif_data(self) -> None:
        path = filedialog.askopenfilename(
            title="Load motif data",
            filetypes=[("Motif data JSON", "*.json"), ("All files", "*.*")],
        )
        if not path:
            return
        try:
            motif_set = load_motif_set(Path(path).expanduser().resolve())
            self._apply_motif_set(motif_set)
        except Exception as exc:
            messagebox.showerror("Load motif data failed", str(exc))
            return
        missing = missing_reference_files(motif_set.references)
        self._log(f"Loaded motif data: {path}")
        self._log(f"References loaded: {len(motif_set.references)}")
        if missing:
            messagebox.showwarning(
                "Motif data loaded",
                f"Loaded {len(motif_set.references)} references, but "
                f"{len(missing)} source file(s) were not found at their saved paths.",
            )

    def _browse_reference(self) -> None:
        path = filedialog.askopenfilename(
            title="Choose reference audio/video",
            filetypes=[("Audio/video", " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTS))), ("All files", "*.*")],
        )
        if not path:
            return
        self.ref_file_var.set(path)
        try:
            self.waveform.load_audio(Path(path), self.ffmpeg)
        except Exception as exc:
            messagebox.showerror("Waveform load failed", str(exc))

    def _waveform_selection_changed(self, start: float, end: float) -> None:
        self.start_var.set(fmt_time(start))
        self.end_var.set(fmt_time(end))

    def _apply_time_boxes(self) -> None:
        try:
            start = parse_timestamp(self.start_var.get())
            end = parse_timestamp(self.end_var.get())
            self.waveform.set_selection(start, end)
        except ValueError as exc:
            messagebox.showerror("Invalid timestamp", str(exc))

    def _read_reference_from_boxes(self, label: str = "preview") -> Reference:
        file_path = Path(self.ref_file_var.get()).expanduser().resolve()
        validate_reference_file(file_path)
        start = parse_timestamp(self.start_var.get())
        end = parse_timestamp(self.end_var.get())
        validate_time_range(
            start,
            end,
            label="reference",
            min_seconds=MIN_REFERENCE_SECONDS,
            max_seconds=MAX_REFERENCE_SECONDS,
        )
        return Reference(file=file_path, start=start, end=end, label=label)

    def _add_reference(self) -> None:
        try:
            ref = self._read_reference_from_boxes()
            label = f"ref{len(self.references) + 1}: {ref.file.name} {fmt_time(ref.start)}-{fmt_time(ref.end)}"
            ref = Reference(file=ref.file, start=ref.start, end=ref.end, label=label)
            self.references.append(ref)
            self.ref_list.insert("end", label)
        except ValueError as exc:
            messagebox.showerror("Cannot add reference", str(exc))

    def _remove_reference(self) -> None:
        selected = list(self.ref_list.curselection())
        for index in reversed(selected):
            del self.references[index]
            self.ref_list.delete(index)

    def _preview_time_boxes(self) -> None:
        try:
            ref = self._read_reference_from_boxes()
            self._play_reference(ref)
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc))

    def _preview_selected_reference(self) -> None:
        selected = list(self.ref_list.curselection())
        if not selected:
            messagebox.showinfo("Preview reference", "Select a reference first.")
            return
        try:
            self._play_reference(self.references[selected[0]])
        except Exception as exc:
            messagebox.showerror("Preview failed", str(exc))

    def _current_preview_volume(self) -> float:
        volume = clamp_volume_percent(self.preview_volume_var.get(), default=80.0)
        if volume != self.preview_volume_var.get():
            self.preview_volume_var.set(volume)
        return volume

    def _preview_volume_changed(self, _value: object = None) -> None:
        volume = self._current_preview_volume()
        self.preview_volume_label_var.set(f"{int(round(volume))}%")
        self.preview_player.set_volume(volume)

    def _set_preview_pause_button(self, *, enabled: bool, paused: bool = False) -> None:
        if not hasattr(self, "preview_pause_button"):
            return
        self.preview_pause_button.configure(
            state="normal" if enabled else "disabled",
            text="Resume" if paused else "Pause",
        )

    def _toggle_preview_pause(self) -> None:
        try:
            if self.preview_player.paused:
                self.preview_player.resume()
                self._set_preview_pause_button(enabled=True, paused=False)
                self._log("Preview resumed.")
            else:
                self.preview_player.pause()
                self._set_preview_pause_button(enabled=True, paused=True)
                self._log("Preview paused.")
        except Exception as exc:
            messagebox.showerror("Preview pause failed", str(exc))

    def _play_reference(self, ref: Reference) -> None:
        self._stop_preview(show_errors=False)
        if self.preview_file and self.preview_file.exists():
            try:
                self.preview_file.unlink()
            except OSError:
                pass
        volume = self._current_preview_volume()
        preview_file = render_preview_wav(
            self.ffmpeg,
            ref.file,
            ref.start,
            ref.end,
            self.preview_dir,
            volume,
        )
        self.preview_file = preview_file
        player = self.preview_player.play(preview_file, volume_percent=volume)
        self._set_preview_pause_button(enabled=self.preview_player.can_pause(), paused=False)
        self._log(
            f"Preview playing via {player}: "
            f"{ref.file.name} {fmt_time(ref.start)}-{fmt_time(ref.end)}"
        )

    def _stop_preview(self, show_errors: bool = True) -> None:
        try:
            if hasattr(self, "preview_player"):
                self.preview_player.stop()
            elif winsound is not None:
                winsound.PlaySound(None, 0)
            self._set_preview_pause_button(enabled=False, paused=False)
        except Exception as exc:
            if show_errors:
                messagebox.showerror("Stop preview failed", str(exc))

    def _browse_corpus_folder(self) -> None:
        path = filedialog.askdirectory(title="Choose full song corpus folder")
        if not path:
            return
        self._add_corpus_paths([Path(path)])

    def _browse_corpus_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="Choose corpus audio/video files",
            filetypes=[("Audio/video", " ".join(f"*{ext}" for ext in sorted(SUPPORTED_EXTS))), ("All files", "*.*")],
        )
        if paths:
            self._add_corpus_paths([Path(path) for path in paths])

    def _add_corpus_paths(self, paths: list[Path]) -> None:
        for path in paths:
            resolved = path.expanduser().resolve()
            if resolved not in self.corpus_paths:
                self.corpus_paths.append(resolved)
                self.corpus_list.insert("end", str(resolved))
        self.corpus_var.set(format_path_list(self.corpus_paths))

    def _replace_corpus_paths(self, paths: list[Path]) -> None:
        self.corpus_paths.clear()
        self.corpus_list.delete(0, "end")
        self.corpus_var.set("")
        self._add_corpus_paths(paths)

    def _clear_corpus(self) -> None:
        self.corpus_paths.clear()
        self.corpus_list.delete(0, "end")
        self.corpus_var.set("")

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Choose output folder")
        if path:
            self.output_var.set(path)

    def _read_scan_settings(self) -> tuple[float, float, float]:
        try:
            threshold = float(self.threshold_var.get())
            step = float(self.step_var.get())
            nms = float(self.nms_var.get())
        except ValueError as exc:
            raise ValueError("threshold, step, and NMS must be numbers") from exc
        validate_scan_settings(threshold, step, nms)
        return threshold, step, nms

    def _run_scan(self) -> None:
        if self.scan_thread and self.scan_thread.is_alive():
            messagebox.showinfo("Scan running", "A scan is already running.")
            return
        try:
            threshold, step, nms = self._read_scan_settings()
            if not self.references:
                raise ValueError("add at least one reference sample")
            typed = parse_path_list(self.corpus_var.get())
            if typed != self.corpus_paths:
                self._replace_corpus_paths(typed)
            if not self.corpus_paths:
                raise ValueError("add corpus files or a corpus folder")
            output_dir = Path(self.output_var.get()).expanduser().resolve()
            title = self.title_var.get().strip() or "leitmotif corpus"
            references = list(self.references)
            corpus_roots = list(self.corpus_paths)
            recursive = bool(self.recursive_var.get())
            transpose = bool(self.transpose_var.get())
            export_sequence = bool(self.export_sequence_var.get())
            sequence_min_overlap = bool(self.sequence_min_overlap_var.get())
        except ValueError as exc:
            messagebox.showerror("Cannot run scan", str(exc))
            return

        self.run_button.configure(state="disabled")
        self._set_output_buttons_state("disabled")
        self.last_sequence_path = None
        self._log("Starting scan...")
        args = (
            threshold,
            step,
            nms,
            output_dir,
            title,
            references,
            corpus_roots,
            recursive,
            transpose,
            export_sequence,
            sequence_min_overlap,
        )
        self.scan_thread = threading.Thread(target=self._scan_worker, args=args, daemon=True)
        self.scan_thread.start()

    def _scan_worker(
        self,
        threshold: float,
        step: float,
        nms: float,
        output_dir: Path,
        title: str,
        references: list[Reference],
        corpus_roots: list[Path],
        recursive: bool,
        transpose: bool,
        export_sequence: bool,
        sequence_min_overlap: bool,
    ) -> None:
        try:
            corpus_files = collect_corpus(corpus_roots, recursive)
            if not corpus_files:
                raise ValueError("no supported audio/video files found in corpus")
            self._queue_log(f"Corpus files: {len(corpus_files)}")
            template_groups = build_template_groups(
                references,
                self.ffmpeg,
                transpose,
                log=self._queue_log,
            )
            self._queue_log(
                f"Template groups: {len(template_groups)}; vectors: "
                f"{sum(group.templates.shape[0] for group in template_groups)}"
            )
            settings = {
                "threshold": threshold,
                "step_seconds": step,
                "nms_seconds": nms,
                "transpose": transpose,
                "sample_rate": ANALYSIS_SAMPLE_RATE,
                "frame_size": FRAME_SIZE,
                "hop_size": HOP_SIZE,
                "ffmpeg": self.ffmpeg,
                "export_sequence": export_sequence,
                "sequence_minimize_overlap": sequence_min_overlap,
            }
            all_hits: list[dict[str, object]] = []
            errors: list[dict[str, object]] = []
            for index, path in enumerate(corpus_files, 1):
                self._queue_log(f"[{index}/{len(corpus_files)}] {path.name}")
                try:
                    hits, duration = scan_file(path, self.ffmpeg, template_groups, threshold, step, nms)
                    all_hits.extend(hits)
                    self._queue_log(f"  duration {fmt_time(duration)}; hits {len(hits)}")
                except Exception as exc:
                    errors.append({"file": str(path), "error": str(exc)})
                    self._queue_log(f"  ERROR: {exc}")
            all_hits.sort(key=lambda hit: (str(hit["file"]).lower(), float(hit["start_seconds"])))
            sequence_path: Path | None = None
            if export_sequence and all_hits:
                self._queue_log(
                    "Exporting leitmotif sequence "
                    f"(minimize overlap: {sequence_min_overlap})..."
                )
                try:
                    sequence_path, sequence_manifest_path, sequence_segments = export_leitmotif_sequence(
                        self.ffmpeg,
                        output_dir,
                        title,
                        all_hits,
                        minimize_overlap=sequence_min_overlap,
                        log=self._queue_log,
                    )
                    settings["sequence_output"] = str(sequence_path)
                    settings["sequence_manifest"] = str(sequence_manifest_path)
                    settings["sequence_segments"] = sequence_segments
                    self._queue_log(f"Sequence MP3: {sequence_path}")
                    self._queue_log(f"Sequence CSV: {sequence_manifest_path}")
                except Exception as exc:
                    message = f"sequence export failed: {exc}"
                    errors.append({"file": "(sequence export)", "error": message})
                    settings["sequence_output"] = f"not written; {message}"
                    sequence_path = None
                    self._queue_log(f"  ERROR: {message}")
            elif export_sequence:
                settings["sequence_output"] = "not written; no hits found"
            md_path, csv_path = write_outputs(
                output_dir,
                title,
                references,
                corpus_files,
                all_hits,
                errors,
                settings,
            )
            self._queue_scan_outputs(md_path, csv_path, output_dir, sequence_path)
            self._queue_log(f"Markdown: {md_path}")
            self._queue_log(f"CSV: {csv_path}")
            self._queue_log(f"Hits found: {len(all_hits)}; errors: {len(errors)}")
        except Exception as exc:
            self._queue_log(f"Fatal: {exc}")
        finally:
            self._queue_scan_done()

    def _set_output_buttons_state(self, state: str) -> None:
        for button in self.output_buttons:
            button.configure(state=state)

    def _open_last_output(self, kind: str) -> None:
        targets = {
            "md": self.last_md_path,
            "csv": self.last_csv_path,
            "sequence": self.last_sequence_path,
            "folder": self.last_output_dir,
        }
        target = targets.get(kind)
        if target is None:
            messagebox.showinfo("No output yet", "Run a scan first.")
            return
        try:
            open_path(target)
        except Exception as exc:
            messagebox.showerror("Open output failed", str(exc))


def run_self_test(args: argparse.Namespace) -> int:
    """Run a non-GUI scan used by tests, packaging checks, and CLI smoke tests."""

    validate_scan_settings(args.threshold, args.step, args.nms)
    ref_file = Path(args.reference).expanduser().resolve()
    corpus = Path(args.corpus).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    try:
        validate_reference_file(ref_file)
    except ValueError as exc:
        raise RuntimeError(str(exc)) from exc
    if not corpus.exists():
        raise RuntimeError(f"corpus path not found: {corpus}")
    start = parse_timestamp(args.start)
    end = parse_timestamp(args.end)
    validate_time_range(
        start,
        end,
        label="self-test reference",
        min_seconds=MIN_REFERENCE_SECONDS,
        max_seconds=MAX_REFERENCE_SECONDS,
    )
    ref = Reference(
        file=ref_file,
        start=start,
        end=end,
        label=f"self-test {ref_file.name} {args.start}-{args.end}",
    )
    corpus_files = collect_corpus([corpus], recursive=True)
    if not corpus_files:
        raise RuntimeError("no corpus files found")
    ffmpeg = find_ffmpeg()
    groups = build_template_groups([ref], ffmpeg, transpose=True, log=safe_print)
    hits: list[dict[str, object]] = []
    errors: list[dict[str, object]] = []
    for path in corpus_files:
        try:
            file_hits, duration = scan_file(path, ffmpeg, groups, args.threshold, args.step, args.nms)
            safe_print(f"{path.name}: {fmt_time(duration)} hits={len(file_hits)}")
            hits.extend(file_hits)
        except Exception as exc:
            errors.append({"file": str(path), "error": str(exc)})
            safe_print(f"{path.name}: ERROR {exc}")
    md_path, csv_path = write_outputs(
        output,
        "general leitmotif self-test",
        [ref],
        corpus_files,
        sorted(hits, key=lambda hit: (str(hit["file"]).lower(), float(hit["start_seconds"]))),
        errors,
        {
            "threshold": args.threshold,
            "step_seconds": args.step,
            "nms_seconds": args.nms,
            "transpose": True,
            "sample_rate": ANALYSIS_SAMPLE_RATE,
            "frame_size": FRAME_SIZE,
            "hop_size": HOP_SIZE,
            "ffmpeg": ffmpeg,
        },
    )
    safe_print(f"Markdown: {md_path}")
    safe_print(f"CSV: {csv_path}")
    safe_print(f"Hits: {len(hits)} Errors: {len(errors)}")
    return 0 if not errors else 1


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="General leitmotif finder GUI.")
    parser.add_argument("--self-test", action="store_true", help="Run non-GUI self-test scan.")
    parser.add_argument("--reference", default="", help="Reference file for --self-test.")
    parser.add_argument("--start", default="00:00.00", help="Reference start for --self-test.")
    parser.add_argument("--end", default="00:06.00", help="Reference end for --self-test.")
    parser.add_argument("--corpus", default="", help="Corpus file/folder for --self-test.")
    parser.add_argument("--output", default=str(app_dir() / "_self_test"), help="Output folder.")
    parser.add_argument("--threshold", type=float, default=0.60)
    parser.add_argument("--step", type=float, default=0.50)
    parser.add_argument("--nms", type=float, default=5.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(sys.argv[1:] if argv is None else argv)
    try:
        if args.self_test:
            return run_self_test(args)
    except Exception as exc:
        safe_print(f"Fatal: {exc}")
        return 1
    root: tk.Tk | None = None
    try:
        root = tk.Tk()
        LeitmotifFinderApp(root)
    except Exception as exc:
        try:
            messagebox.showerror("General Leitmotif Finder failed to start", str(exc))
        except Exception:
            safe_print(f"Fatal: {exc}")
        if root is not None:
            root.destroy()
        return 1
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
