#!/usr/bin/env python3
"""Manual ZIP-to-7z conversion GUI for final archive compaction.

The tool intentionally uses only the Python standard library and a user
installed 7-Zip command-line executable. Python's standard library can read ZIP
archives, but it cannot write `.7z` archives, so the conversion path extracts
each input ZIP into a temporary directory and then asks `7z`/`7za`/`7zz` to
create one matching `.7z` archive.

No input ZIP is deleted. Each output is first written as a temporary `.tmp`
archive and atomically moved into place after 7-Zip reports success.
"""

from __future__ import annotations

import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Callable, Iterable


COMMON_7Z_PATHS = (
    Path(r"C:\Program Files\7-Zip\7z.exe"),
    Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
)
SEVEN_ZIP_NAMES = ("7z", "7za", "7zz")
VALID_METHODS = {"lzma", "lzma2"}


class ConversionError(RuntimeError):
    """Raised when a ZIP cannot be converted."""


class CancelledError(RuntimeError):
    """Raised when the user cancels the active conversion."""


@dataclass(frozen=True)
class SevenZipSettings:
    """Compression settings passed to the 7-Zip CLI."""

    method: str = "lzma2"
    level: int = 9
    dictionary: str = "64m"
    solid: bool = True
    threads: str = "on"

    def normalized(self) -> "SevenZipSettings":
        """Return validated settings with normalized method text."""

        method = self.method.strip().lower()
        if method not in VALID_METHODS:
            raise ValueError(f"method must be one of {sorted(VALID_METHODS)}, got {self.method!r}")
        if not 0 <= self.level <= 9:
            raise ValueError(f"level must be 0..9, got {self.level}")
        dictionary = self.dictionary.strip().lower()
        if not dictionary:
            raise ValueError("dictionary cannot be empty")
        threads = self.threads.strip().lower() or "on"
        return SevenZipSettings(
            method=method,
            level=self.level,
            dictionary=dictionary,
            solid=self.solid,
            threads=threads,
        )


@dataclass(frozen=True)
class ConversionJob:
    """One source ZIP and its destination `.7z` archive."""

    zip_path: Path
    output_path: Path


@dataclass(frozen=True)
class ZipPreflight:
    """Validated ZIP metadata collected before extraction."""

    entry_count: int
    compressed_bytes: int
    uncompressed_bytes: int


def is_relative_to(path: Path, root: Path, *, resolved: bool = False) -> bool:
    """Return whether `path` is inside `root`.

    Set `resolved=True` when both inputs are already absolute/resolved. This is
    used by the hot job scanner to avoid resolving the output directory for
    every candidate ZIP.
    """

    try:
        child = path if resolved else path.resolve()
        parent = root if resolved else root.resolve()
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def resolve_7z_executable(env: dict[str, str] | None = None) -> Path | None:
    """Find a 7-Zip CLI executable without bundling one.

    Search order:

    1. `ZIP_TO_7Z_EXE`
    2. `7z`, `7za`, then `7zz` on `PATH`
    3. common Windows install paths
    """

    env = env or os.environ
    override = env.get("ZIP_TO_7Z_EXE")
    if override:
        path = Path(override).expanduser()
        if path.is_file():
            return path

    for name in SEVEN_ZIP_NAMES:
        found = shutil.which(name, path=env.get("PATH"))
        if found:
            return Path(found)

    for path in COMMON_7Z_PATHS:
        if path.is_file():
            return path
    return None


def output_path_for_zip(zip_path: Path, input_dir: Path, output_dir: Path) -> Path:
    """Map `input_dir/sub/file.zip` to `output_dir/sub/file.7z`."""

    relative = zip_path.resolve().relative_to(input_dir.resolve())
    return output_dir / relative.with_suffix(".7z")


def find_zip_jobs(input_dir: Path, output_dir: Path, *, recursive: bool, overwrite: bool) -> list[ConversionJob]:
    """Return sorted conversion jobs, preserving relative directory layout."""

    if not input_dir.is_dir():
        raise FileNotFoundError(f"input directory not found: {input_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)

    input_root = input_dir.resolve()
    output_root = output_dir.resolve()
    iterator: Iterable[Path] = input_dir.rglob("*") if recursive else input_dir.iterdir()
    jobs: list[ConversionJob] = []
    for path in iterator:
        if not path.is_file() or path.suffix.lower() != ".zip":
            continue
        zip_path = path.resolve()
        # Avoid re-reading input ZIPs that already live under the chosen output
        # directory during recursive scans.
        if is_relative_to(zip_path, output_root, resolved=True):
            continue
        relative = zip_path.relative_to(input_root)
        output_path = (output_root / relative.with_suffix(".7z")).resolve()
        if output_path.exists() and not overwrite:
            continue
        jobs.append(ConversionJob(zip_path, output_path))
    return sorted(jobs, key=lambda job: str(job.zip_path).lower())


def build_extract_command(seven_zip: Path, zip_path: Path, temp_dir: Path) -> list[str]:
    """Build the 7-Zip extraction command for one source ZIP."""

    return [
        str(seven_zip),
        "x",
        str(zip_path),
        f"-o{temp_dir}",
        "-y",
        "-bb0",
        "-bd",
    ]


def build_archive_command(
    seven_zip: Path,
    output_tmp: Path,
    listfile: Path,
    settings: SevenZipSettings,
) -> list[str]:
    """Build the 7-Zip archive creation command."""

    normalized = settings.normalized()
    solid_flag = "-ms=on" if normalized.solid else "-ms=off"
    return [
        str(seven_zip),
        "a",
        "-t7z",
        f"-m0={normalized.method}",
        f"-mx={normalized.level}",
        f"-md={normalized.dictionary}",
        f"-mmt={normalized.threads}",
        solid_flag,
        "-y",
        "-bb0",
        "-bd",
        "-scsUTF-8",
        str(output_tmp),
        f"@{listfile}",
    ]


def write_7z_listfile(extracted_dir: Path, listfile: Path) -> int:
    """Write a UTF-8 7-Zip listfile of top-level extracted entries.

    Top-level entries are enough because 7-Zip descends into directories. This
    keeps huge archives from producing one listfile line per contained file.
    """

    entries: list[str] = []
    for path in extracted_dir.iterdir():
        name = path.name
        if "\n" in name or "\r" in name:
            raise ConversionError(f"extracted top-level path contains a newline: {name!r}")
        entries.append(name)
    entries.sort()
    if not entries:
        raise ConversionError("ZIP extracted to an empty directory")
    listfile.write_text("\n".join(entries) + "\n", encoding="utf-8")
    return len(entries)


def validate_zip_member_name(name: str) -> None:
    """Reject member names that could extract outside the temp directory."""

    normalized = name.replace("\\", "/")
    parts = PurePosixPath(normalized).parts
    windows = PureWindowsPath(name)
    if not name or not normalized.strip("/"):
        raise ConversionError("ZIP contains an empty member name")
    if "\x00" in name:
        raise ConversionError(f"ZIP member contains NUL byte: {name!r}")
    if "\n" in name or "\r" in name:
        raise ConversionError(f"ZIP member contains a newline: {name!r}")
    if normalized.startswith("/") or windows.drive or windows.root:
        raise ConversionError(f"ZIP member uses an absolute path: {name!r}")
    if any(part == ".." for part in parts):
        raise ConversionError(f"ZIP member escapes the extraction directory: {name!r}")


def inspect_zip(zip_path: Path) -> ZipPreflight:
    """Validate a ZIP and return compact metadata used for logging."""

    try:
        with zipfile.ZipFile(zip_path) as archive:
            entry_count = 0
            compressed_bytes = 0
            uncompressed_bytes = 0
            for info in archive.infolist():
                validate_zip_member_name(info.filename)
                entry_count += 1
                compressed_bytes += info.compress_size
                uncompressed_bytes += info.file_size
    except zipfile.BadZipFile as exc:
        raise ConversionError(f"bad ZIP file: {zip_path}") from exc
    if entry_count == 0:
        raise ConversionError(f"empty ZIP file: {zip_path}")
    return ZipPreflight(
        entry_count=entry_count,
        compressed_bytes=compressed_bytes,
        uncompressed_bytes=uncompressed_bytes,
    )


def check_zip_has_entries(zip_path: Path) -> int:
    """Return ZIP entry count, raising a clear error for bad/empty archives."""

    return inspect_zip(zip_path).entry_count


def run_7z_command(command: list[str], *, cwd: Path | None, cancel_event: threading.Event) -> str:
    """Run one 7-Zip command and return combined output.

    The loop exists so Cancel can terminate the current 7-Zip process instead of
    waiting for a large compression job to finish.
    """

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    process = subprocess.Popen(
        command,
        cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    while True:
        if cancel_event.is_set():
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()
            raise CancelledError("conversion cancelled")
        try:
            output, _ = process.communicate(timeout=0.25)
            break
        except subprocess.TimeoutExpired:
            continue

    if process.returncode != 0:
        tail = "\n".join((output or "").splitlines()[-25:])
        raise ConversionError(f"7-Zip failed with exit code {process.returncode}\n{tail}")
    return output or ""


def format_size(byte_count: int) -> str:
    """Return a compact human-readable byte count."""

    value = float(byte_count)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if value < 1024 or unit == "TiB":
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{byte_count} B"


def compression_ratio(input_size: int, output_size: int) -> str:
    """Return output/input percentage for progress logs."""

    if input_size <= 0:
        return "n/a"
    return f"{(output_size / input_size) * 100:.1f}%"


def convert_zip_to_7z(
    job: ConversionJob,
    *,
    seven_zip: Path,
    settings: SevenZipSettings,
    overwrite: bool,
    cancel_event: threading.Event,
    log: Callable[[str], None],
) -> None:
    """Convert one ZIP archive into one `.7z` archive."""

    if job.output_path.exists() and not overwrite:
        log(f"skip existing {job.output_path}")
        return

    preflight = inspect_zip(job.zip_path)
    job.output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_output = job.output_path.with_name(f"{job.output_path.name}.tmp")
    if tmp_output.exists():
        tmp_output.unlink()

    listfile_path: Path | None = None
    try:
        with tempfile.TemporaryDirectory(prefix="_zip_to_7z_", dir=str(job.output_path.parent)) as temp_name:
            temp_dir = Path(temp_name)
            log(
                "extract "
                f"{job.zip_path} ({preflight.entry_count} ZIP entries, "
                f"{format_size(preflight.uncompressed_bytes)} unpacked)"
            )
            run_7z_command(build_extract_command(seven_zip, job.zip_path, temp_dir), cwd=None, cancel_event=cancel_event)

            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                prefix=f".{job.output_path.stem}.",
                suffix=".7z-list.txt",
                dir=str(job.output_path.parent),
                delete=False,
            ) as handle:
                listfile_path = Path(handle.name)
            top_count = write_7z_listfile(temp_dir, listfile_path)

            log(f"archive {job.output_path} ({top_count} top-level entries)")
            run_7z_command(
                build_archive_command(seven_zip, tmp_output, listfile_path, settings),
                cwd=temp_dir,
                cancel_event=cancel_event,
            )

        if overwrite and job.output_path.exists():
            job.output_path.unlink()
        os.replace(tmp_output, job.output_path)
    finally:
        if listfile_path and listfile_path.exists():
            listfile_path.unlink()
        if tmp_output.exists():
            tmp_output.unlink()
    output_size = job.output_path.stat().st_size
    log(
        f"wrote {job.output_path} "
        f"({format_size(output_size)}, {compression_ratio(preflight.compressed_bytes, output_size)} of ZIP size)"
    )


def run_gui() -> None:
    """Launch the Tkinter GUI."""

    import tkinter as tk
    from tkinter import filedialog, messagebox, scrolledtext, ttk

    class ZipTo7zGui:
        """Small Tkinter wrapper around the conversion functions."""

        def __init__(self, root: tk.Tk) -> None:
            self.root = root
            self.root.title("ZIP to 7z LZMA Converter")
            self.events: queue.Queue[tuple[str, object]] = queue.Queue()
            self.cancel_event = threading.Event()
            self.worker: threading.Thread | None = None

            detected = resolve_7z_executable()
            self.input_var = tk.StringVar()
            self.output_var = tk.StringVar()
            self.exe_var = tk.StringVar(value=str(detected) if detected else "")
            self.recursive_var = tk.BooleanVar(value=True)
            self.overwrite_var = tk.BooleanVar(value=False)
            self.method_var = tk.StringVar(value="lzma2")

            self._build_widgets()
            self.root.after(100, self._poll_events)

        def _build_widgets(self) -> None:
            frame = ttk.Frame(self.root, padding=12)
            frame.grid(row=0, column=0, sticky="nsew")
            self.root.columnconfigure(0, weight=1)
            self.root.rowconfigure(0, weight=1)
            frame.columnconfigure(1, weight=1)

            self._path_row(frame, 0, "Input ZIP folder", self.input_var, self._browse_input)
            self._path_row(frame, 1, "Output 7z folder", self.output_var, self._browse_output)
            self._path_row(frame, 2, "7-Zip exe", self.exe_var, self._browse_7z)

            options = ttk.Frame(frame)
            options.grid(row=3, column=0, columnspan=3, sticky="ew", pady=(6, 6))
            ttk.Checkbutton(options, text="Recursive", variable=self.recursive_var).pack(side="left")
            ttk.Checkbutton(options, text="Overwrite existing .7z", variable=self.overwrite_var).pack(side="left", padx=(12, 0))
            ttk.Label(options, text="Method").pack(side="left", padx=(12, 4))
            method = ttk.Combobox(options, textvariable=self.method_var, values=("lzma2", "lzma"), width=8, state="readonly")
            method.pack(side="left")

            buttons = ttk.Frame(frame)
            buttons.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(0, 6))
            self.start_button = ttk.Button(buttons, text="Start", command=self._start)
            self.start_button.pack(side="left")
            self.cancel_button = ttk.Button(buttons, text="Cancel", command=self._cancel, state="disabled")
            self.cancel_button.pack(side="left", padx=(8, 0))

            self.progress = ttk.Progressbar(frame, mode="determinate")
            self.progress.grid(row=5, column=0, columnspan=3, sticky="ew")

            self.console = scrolledtext.ScrolledText(frame, height=12, width=92, state="disabled")
            self.console.grid(row=6, column=0, columnspan=3, sticky="nsew", pady=(8, 0))
            frame.rowconfigure(6, weight=1)

        def _path_row(
            self,
            parent: ttk.Frame,
            row: int,
            label: str,
            variable: tk.StringVar,
            browse: Callable[[], None],
        ) -> None:
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=2)
            ttk.Entry(parent, textvariable=variable).grid(row=row, column=1, sticky="ew", padx=6, pady=2)
            ttk.Button(parent, text="Browse", command=browse).grid(row=row, column=2, pady=2)

        def _browse_input(self) -> None:
            path = filedialog.askdirectory(title="Choose input folder containing ZIP files")
            if path:
                self.input_var.set(path)

        def _browse_output(self) -> None:
            path = filedialog.askdirectory(title="Choose output folder for .7z files")
            if path:
                self.output_var.set(path)

        def _browse_7z(self) -> None:
            path = filedialog.askopenfilename(
                title="Choose 7-Zip executable",
                filetypes=(("7-Zip executables", "7z.exe 7za.exe 7zz.exe"), ("All files", "*.*")),
            )
            if path:
                self.exe_var.set(path)

        def _log(self, message: str) -> None:
            self.console.configure(state="normal")
            self.console.insert("end", message.rstrip() + "\n")
            self.console.see("end")
            self.console.configure(state="disabled")

        def _start(self) -> None:
            if self.worker and self.worker.is_alive():
                return
            try:
                input_dir = Path(self.input_var.get()).expanduser()
                output_dir = Path(self.output_var.get()).expanduser()
                seven_zip = Path(self.exe_var.get()).expanduser()
                if not seven_zip.is_file():
                    raise FileNotFoundError("7-Zip executable not found")
                settings = SevenZipSettings(method=self.method_var.get()).normalized()
                jobs = find_zip_jobs(
                    input_dir,
                    output_dir,
                    recursive=self.recursive_var.get(),
                    overwrite=self.overwrite_var.get(),
                )
            except Exception as exc:  # noqa: BLE001 - GUI should show validation errors directly.
                messagebox.showerror("Cannot start", str(exc))
                return

            if not jobs:
                self._log("no ZIP files to convert")
                return

            self.progress.configure(maximum=len(jobs), value=0)
            self.cancel_event.clear()
            self.start_button.configure(state="disabled")
            self.cancel_button.configure(state="normal")
            self._log(f"start {len(jobs)} ZIP file(s)")
            self.worker = threading.Thread(
                target=self._worker_main,
                args=(jobs, seven_zip, settings, self.overwrite_var.get()),
                daemon=True,
            )
            self.worker.start()

        def _cancel(self) -> None:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            self._log("cancel requested")

        def _worker_main(
            self,
            jobs: list[ConversionJob],
            seven_zip: Path,
            settings: SevenZipSettings,
            overwrite: bool,
        ) -> None:
            done = 0
            failed = 0
            for index, job in enumerate(jobs, start=1):
                if self.cancel_event.is_set():
                    self.events.put(("cancelled", None))
                    return
                self.events.put(("log", f"[{index}/{len(jobs)}] {job.zip_path.name}"))
                try:
                    convert_zip_to_7z(
                        job,
                        seven_zip=seven_zip,
                        settings=settings,
                        overwrite=overwrite,
                        cancel_event=self.cancel_event,
                        log=lambda message: self.events.put(("log", message)),
                    )
                    done += 1
                except CancelledError:
                    self.events.put(("cancelled", None))
                    return
                except Exception as exc:  # noqa: BLE001 - keep batch moving after one bad input.
                    failed += 1
                    self.events.put(("log", f"ERROR {job.zip_path}: {exc}"))
                self.events.put(("progress", index))
            self.events.put(("done", (done, failed)))

        def _poll_events(self) -> None:
            try:
                while True:
                    kind, payload = self.events.get_nowait()
                    if kind == "log":
                        self._log(str(payload))
                    elif kind == "progress":
                        self.progress.configure(value=int(payload))
                    elif kind == "cancelled":
                        self._finish_controls()
                        self._log("cancelled")
                    elif kind == "done":
                        done, failed = payload  # type: ignore[misc]
                        self._finish_controls()
                        self._log(f"done converted={done} failed={failed}")
            except queue.Empty:
                pass
            self.root.after(100, self._poll_events)

        def _finish_controls(self) -> None:
            self.start_button.configure(state="normal")
            self.cancel_button.configure(state="disabled")

    root = tk.Tk()
    root.geometry("860x430")
    ZipTo7zGui(root)
    root.mainloop()


def main(argv: list[str] | None = None) -> int:
    """Program entry point."""

    if argv is None:
        argv = sys.argv[1:]
    if argv:
        print("This tool is GUI-only. Run without arguments.", file=sys.stderr)
        return 2
    run_gui()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
