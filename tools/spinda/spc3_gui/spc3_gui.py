#!/usr/bin/env python3
"""Minimal SPC3 operator GUI.

This is a thin Tkinter wrapper around the verified `spc3_prototype.exe` CLI.
It does not implement compression logic itself.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk


ROOT = Path(__file__).resolve().parents[3]
DEFAULT_EXE = ROOT / "tools" / "spinda" / "spc3_prototype" / "spc3_prototype.exe"
DEFAULT_COMPRESSOR = ROOT / "tools" / "spinda" / "spc3_compress.py"
DEFAULT_ROOT = ROOT / "Phase3SpindaBlocks"
DEFAULT_PREDICTOR = DEFAULT_ROOT / "_phase3_pid_second_half_iv_reference.json"


def text_value(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def nested_value(data: dict[str, object], *keys: str) -> object:
    value: object = data
    for key in keys:
        if not isinstance(value, dict):
            return ""
        value = value.get(key, "")
    return value


def numeric_value(value: object) -> float | None:
    if value in ("", None) or isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def append_metric(lines: list[str], label: str, value: object) -> None:
    text = text_value(value)
    if text:
        lines.append(f"{label}: {text}")


def report_summary_lines(data: dict[str, object]) -> list[str]:
    lines: list[str] = []
    for label, keys in (
        ("schema", ("schema",)),
        ("ok", ("ok",)),
        ("mode", ("mode",)),
        ("version", ("version",)),
        ("level", ("level",)),
        ("status", ("status",)),
        ("target", ("target",)),
        ("failed count", ("failed_count",)),
        ("lanes", ("lane_count",)),
        ("codec", ("codec",)),
        ("codec profile", ("codec_profile",)),
        ("typed v0.2", ("typed_level3",)),
        ("size bytes", ("spc3_size_bytes",)),
        ("new size bytes", ("new_size_bytes",)),
        ("source zip bytes", ("source_zip_bytes",)),
        ("raw payload bytes", ("raw_payload_bytes",)),
        ("roundtrip mismatches", ("roundtrip_mismatches",)),
        ("mismatch count", ("mismatch_count",)),
        ("internal crc mismatches", ("internal_crc_mismatches",)),
        ("source compare enabled", ("source_compare_enabled",)),
        ("source mismatches", ("source_compare_mismatches",)),
        ("unpack crc mismatches", ("crc_mismatches",)),
        ("build ms", ("build_ms",)),
        ("total ms", ("total_ms",)),
    ):
        append_metric(lines, label, nested_value(data, *keys))

    gpu = data.get("gpu_rebuild")
    if isinstance(gpu, dict):
        gpu_line = (
            f"gpu: {text_value(gpu.get('status'))} "
            f"requested={text_value(gpu.get('requested'))} "
            f"used={text_value(gpu.get('used'))} "
            f"fallback={text_value(gpu.get('fallback_reason')) or 'none'} "
            f"mismatches={text_value(gpu.get('mismatched_lanes'))}/{text_value(gpu.get('mismatched_bytes'))}"
        )
        lines.append(gpu_line)
        append_metric(lines, "gpu device", gpu.get("device_name"))
        append_metric(lines, "gpu download mode", gpu.get("download_mode"))
        append_metric(lines, "gpu runtime cache hit", gpu.get("runtime_cache_hit"))
        append_metric(lines, "gpu runtime failure cached", gpu.get("runtime_failure_cached"))
        append_metric(lines, "gpu runtime initializations", gpu.get("runtime_initializations"))
        append_metric(lines, "gpu output bytes", gpu.get("output_bytes"))
        append_metric(lines, "gpu xor values", gpu.get("value_count"))
        append_metric(lines, "gpu compile ms", gpu.get("compile_ms"))
        append_metric(lines, "gpu upload ms", gpu.get("upload_ms"))
        append_metric(lines, "gpu kernel ms", gpu.get("kernel_ms"))
        append_metric(lines, "gpu download ms", gpu.get("download_ms"))
        append_metric(lines, "gpu host crc ms", gpu.get("host_crc_ms"))
        append_metric(lines, "gpu total ms", gpu.get("total_ms"))

    cpu = data.get("cpu_decode_profile")
    if isinstance(cpu, dict):
        lines.append(
            "cpu decode ms: "
            f"used={text_value(cpu.get('used'))} "
            f"lanes={text_value(cpu.get('lane_count'))} "
            f"typed={text_value(cpu.get('typed_lanes'))} "
            f"legacy={text_value(cpu.get('legacy_lanes'))} "
            f"stream={text_value(cpu.get('stream_decode_ms'))} "
            f"iv={text_value(cpu.get('iv_expand_ms'))} "
            f"rebuild={text_value(cpu.get('rebuild_encrypt_ms'))} "
            f"crc={text_value(cpu.get('crc_ms'))} "
            f"total={text_value(cpu.get('total_ms'))}"
        )
        append_metric(lines, "cpu crc backend", cpu.get("crc_backend"))
        append_metric(lines, "cpu crc bytes", cpu.get("crc_bytes"))

    return lines


def report_facts(data: dict[str, object]) -> dict[str, object]:
    return {
        "schema": nested_value(data, "schema"),
        "ok": nested_value(data, "ok"),
        "mode": nested_value(data, "mode"),
        "version": nested_value(data, "version"),
        "level": nested_value(data, "level"),
        "status": nested_value(data, "status"),
        "target": nested_value(data, "target"),
        "failed count": nested_value(data, "failed_count"),
        "lanes": nested_value(data, "lane_count"),
        "codec": nested_value(data, "codec"),
        "codec profile": nested_value(data, "codec_profile"),
        "typed v0.2": nested_value(data, "typed_level3"),
        "size bytes": nested_value(data, "spc3_size_bytes"),
        "new size bytes": nested_value(data, "new_size_bytes"),
        "source zip bytes": nested_value(data, "source_zip_bytes"),
        "raw payload bytes": nested_value(data, "raw_payload_bytes"),
        "roundtrip mismatches": nested_value(data, "roundtrip_mismatches"),
        "mismatch count": nested_value(data, "mismatch_count"),
        "internal crc mismatches": nested_value(data, "internal_crc_mismatches"),
        "source compare enabled": nested_value(data, "source_compare_enabled"),
        "source mismatches": nested_value(data, "source_compare_mismatches"),
        "unpack crc mismatches": nested_value(data, "crc_mismatches"),
        "build ms": nested_value(data, "build_ms"),
        "total ms": nested_value(data, "total_ms"),
        "gpu status": nested_value(data, "gpu_rebuild", "status"),
        "gpu device": nested_value(data, "gpu_rebuild", "device_name"),
        "gpu requested": nested_value(data, "gpu_rebuild", "requested"),
        "gpu used": nested_value(data, "gpu_rebuild", "used"),
        "gpu fallback": nested_value(data, "gpu_rebuild", "fallback_reason"),
        "gpu download mode": nested_value(data, "gpu_rebuild", "download_mode"),
        "gpu runtime cache hit": nested_value(data, "gpu_rebuild", "runtime_cache_hit"),
        "gpu runtime failure cached": nested_value(data, "gpu_rebuild", "runtime_failure_cached"),
        "gpu runtime initializations": nested_value(data, "gpu_rebuild", "runtime_initializations"),
        "gpu output bytes": nested_value(data, "gpu_rebuild", "output_bytes"),
        "gpu xor values": nested_value(data, "gpu_rebuild", "value_count"),
        "gpu lane mismatches": nested_value(data, "gpu_rebuild", "mismatched_lanes"),
        "gpu byte mismatches": nested_value(data, "gpu_rebuild", "mismatched_bytes"),
        "gpu compile ms": nested_value(data, "gpu_rebuild", "compile_ms"),
        "gpu upload ms": nested_value(data, "gpu_rebuild", "upload_ms"),
        "gpu kernel ms": nested_value(data, "gpu_rebuild", "kernel_ms"),
        "gpu download ms": nested_value(data, "gpu_rebuild", "download_ms"),
        "gpu host crc ms": nested_value(data, "gpu_rebuild", "host_crc_ms"),
        "gpu total ms": nested_value(data, "gpu_rebuild", "total_ms"),
        "cpu used": nested_value(data, "cpu_decode_profile", "used"),
        "cpu crc backend": nested_value(data, "cpu_decode_profile", "crc_backend"),
        "cpu lanes": nested_value(data, "cpu_decode_profile", "lane_count"),
        "cpu typed lanes": nested_value(data, "cpu_decode_profile", "typed_lanes"),
        "cpu legacy lanes": nested_value(data, "cpu_decode_profile", "legacy_lanes"),
        "cpu crc bytes": nested_value(data, "cpu_decode_profile", "crc_bytes"),
        "cpu stream ms": nested_value(data, "cpu_decode_profile", "stream_decode_ms"),
        "cpu iv ms": nested_value(data, "cpu_decode_profile", "iv_expand_ms"),
        "cpu rebuild ms": nested_value(data, "cpu_decode_profile", "rebuild_encrypt_ms"),
        "cpu crc ms": nested_value(data, "cpu_decode_profile", "crc_ms"),
        "cpu total ms": nested_value(data, "cpu_decode_profile", "total_ms"),
    }


def comparison_lines(left: dict[str, object], right: dict[str, object], left_name: str, right_name: str) -> list[str]:
    left_facts = report_facts(left)
    right_facts = report_facts(right)
    lines = [f"compare A: {left_name}", f"compare B: {right_name}", ""]
    for key in left_facts:
        a = left_facts[key]
        b = right_facts[key]
        if text_value(a) == "" and text_value(b) == "":
            continue
        delta = ""
        a_num = numeric_value(a)
        b_num = numeric_value(b)
        if a_num is not None and b_num is not None and a_num != b_num:
            delta = f" delta={b_num - a_num:.3f}"
        lines.append(f"{key}: {text_value(a) or '-'} | {text_value(b) or '-'}{delta}")
    return lines


class Spc3Gui(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("SPC3")
        self.geometry("1040x700")
        self.minsize(920, 620)
        self.output_queue: queue.Queue[str] = queue.Queue()
        self.active_process: subprocess.Popen[str] | None = None
        self.worker: threading.Thread | None = None
        self.cancel_requested = False

        self.mode = tk.StringVar(value="pack")
        self.exe = tk.StringVar(value=str(DEFAULT_EXE))
        self.compressor = tk.StringVar(value=str(DEFAULT_COMPRESSOR))
        self.compress_target = tk.StringVar(value="v8")
        self.root_dir = tk.StringVar(value=str(DEFAULT_ROOT))
        self.predictor = tk.StringVar(value=str(DEFAULT_PREDICTOR))
        self.input_path = tk.StringVar(value=str(DEFAULT_ROOT / "typed-v2.spc3"))
        self.output_path = tk.StringVar(value=str(DEFAULT_ROOT / "_spc3_gui_typed_v2.spc3"))
        self.unpack_dir = tk.StringVar(value=str(DEFAULT_ROOT / "_spc3_gui_unpacked"))
        self.report_path = tk.StringVar(value=str(DEFAULT_ROOT / "_spc3_gui_report.json"))
        self.compare_left = tk.StringVar(value=str(DEFAULT_ROOT / "_spc3_v02_typed_fast_real64_release_cpu_verify_report.json"))
        self.compare_right = tk.StringVar(value=str(DEFAULT_ROOT / "_spc3_v02_typed_fast_real64_release_gpu_verify_report.json"))
        self.limit_zips = tk.StringVar(value="20")
        self.level = tk.StringVar(value="3")
        self.codec_profile = tk.StringVar(value="fast")
        self.typed_level3 = tk.BooleanVar(value=True)
        self.gpu = tk.BooleanVar(value=False)
        self.no_source_compare = tk.BooleanVar(value=False)

        self._build_ui()
        self._refresh_mode_state()
        self.after(100, self._drain_output)

    def _build_ui(self) -> None:
        outer = ttk.Frame(self, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(3, weight=1)

        mode_row = ttk.Frame(outer)
        mode_row.grid(row=0, column=0, columnspan=3, sticky="ew", pady=(0, 8))
        ttk.Label(mode_row, text="Mode").pack(side=tk.LEFT)
        mode_box = ttk.Combobox(
            mode_row,
            textvariable=self.mode,
            values=("pack", "verify", "unpack", "inspect", "compress"),
            width=12,
            state="readonly",
        )
        mode_box.pack(side=tk.LEFT, padx=(8, 16))
        mode_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_mode_state())
        self.run_button = ttk.Button(mode_row, text="Run", command=self._start_command)
        self.run_button.pack(side=tk.LEFT)
        self.cancel_button = ttk.Button(mode_row, text="Cancel", command=self._cancel, state=tk.DISABLED)
        self.cancel_button.pack(side=tk.LEFT, padx=(8, 0))
        self.compare_button = ttk.Button(mode_row, text="Compare Reports", command=self._compare_reports)
        self.compare_button.pack(side=tk.LEFT, padx=(8, 0))

        form = ttk.Frame(outer)
        form.grid(row=1, column=0, columnspan=3, sticky="ew")
        form.columnconfigure(1, weight=1)

        self.rows: dict[str, tuple[ttk.Label, ttk.Entry, ttk.Button | None]] = {}
        row = 0
        row = self._path_row(form, row, "SPC3 exe", self.exe, file=True)
        row = self._path_row(form, row, "Compressor CLI", self.compressor, file=True)
        row = self._path_row(form, row, "Root", self.root_dir, directory=True)
        row = self._path_row(form, row, "Predictor", self.predictor, file=True)
        row = self._path_row(form, row, "Input", self.input_path, file=True)
        row = self._path_row(form, row, "Output", self.output_path, save=True)
        row = self._path_row(form, row, "Unpack dir", self.unpack_dir, directory=True)
        row = self._path_row(form, row, "Report", self.report_path, save=True)
        row = self._path_row(form, row, "Compare A", self.compare_left, file=True)
        row = self._path_row(form, row, "Compare B", self.compare_right, file=True)

        options = ttk.Frame(outer)
        options.grid(row=2, column=0, columnspan=3, sticky="ew", pady=8)
        for index in range(12):
            options.columnconfigure(index, weight=0)
        ttk.Label(options, text="Limit").grid(row=0, column=0, sticky="w")
        ttk.Entry(options, textvariable=self.limit_zips, width=8).grid(row=0, column=1, padx=(6, 18))
        ttk.Label(options, text="Level").grid(row=0, column=2, sticky="w")
        ttk.Combobox(options, textvariable=self.level, values=("0", "1", "2", "3"), width=5, state="readonly").grid(
            row=0, column=3, padx=(6, 18)
        )
        ttk.Label(options, text="Profile").grid(row=0, column=4, sticky="w")
        ttk.Combobox(
            options,
            textvariable=self.codec_profile,
            values=("auto", "compat", "fast", "small"),
            width=9,
            state="readonly",
        ).grid(row=0, column=5, padx=(6, 18))
        self.typed_check = ttk.Checkbutton(options, text="Typed v0.2", variable=self.typed_level3)
        self.typed_check.grid(row=0, column=6, padx=(0, 18))
        self.gpu_check = ttk.Checkbutton(options, text="Use GPU", variable=self.gpu)
        self.gpu_check.grid(row=0, column=7, padx=(0, 18))
        self.no_source_check = ttk.Checkbutton(options, text="Internal only", variable=self.no_source_compare)
        self.no_source_check.grid(row=0, column=8)
        ttk.Label(options, text="SPC3 target").grid(row=0, column=9, sticky="w", padx=(18, 0))
        self.compress_target_box = ttk.Combobox(
            options,
            textvariable=self.compress_target,
            values=("v2", "v3", "v4", "v5", "v6", "v7", "v8", "all"),
            width=6,
            state="readonly",
        )
        self.compress_target_box.grid(row=0, column=10, padx=(6, 0))
        self.compress_target_box.bind("<<ComboboxSelected>>", lambda _event: self._refresh_mode_state())

        panes = ttk.Panedwindow(outer, orient=tk.VERTICAL)
        panes.grid(row=3, column=0, columnspan=3, sticky="nsew")
        output_frame = ttk.Frame(panes, padding=(0, 0, 0, 6))
        summary_frame = ttk.Frame(panes)
        panes.add(output_frame, weight=3)
        panes.add(summary_frame, weight=1)

        output_frame.rowconfigure(0, weight=1)
        output_frame.columnconfigure(0, weight=1)
        self.console = tk.Text(output_frame, wrap=tk.WORD, height=18)
        self.console.grid(row=0, column=0, sticky="nsew")
        console_scroll = ttk.Scrollbar(output_frame, command=self.console.yview)
        console_scroll.grid(row=0, column=1, sticky="ns")
        self.console.configure(yscrollcommand=console_scroll.set)

        summary_frame.rowconfigure(0, weight=1)
        summary_frame.columnconfigure(0, weight=1)
        self.summary = tk.Text(summary_frame, wrap=tk.WORD, height=8)
        self.summary.grid(row=0, column=0, sticky="nsew")
        summary_scroll = ttk.Scrollbar(summary_frame, command=self.summary.yview)
        summary_scroll.grid(row=0, column=1, sticky="ns")
        self.summary.configure(yscrollcommand=summary_scroll.set)

    def _path_row(
        self,
        parent: ttk.Frame,
        row: int,
        label: str,
        var: tk.StringVar,
        *,
        file: bool = False,
        directory: bool = False,
        save: bool = False,
    ) -> int:
        label_widget = ttk.Label(parent, text=label)
        entry = ttk.Entry(parent, textvariable=var)
        label_widget.grid(row=row, column=0, sticky="w", pady=2)
        entry.grid(row=row, column=1, sticky="ew", padx=8, pady=2)
        button: ttk.Button | None = None
        if file or directory or save:
            button = ttk.Button(parent, text="Browse", command=lambda: self._browse(var, file=file, directory=directory, save=save))
            button.grid(row=row, column=2, sticky="ew", pady=2)
        self.rows[label] = (label_widget, entry, button)
        return row + 1

    def _browse(self, var: tk.StringVar, *, file: bool, directory: bool, save: bool) -> None:
        current = Path(var.get() or str(ROOT))
        if directory:
            selected = filedialog.askdirectory(initialdir=str(current if current.is_dir() else current.parent))
        elif save:
            selected = filedialog.asksaveasfilename(initialdir=str(current.parent), initialfile=current.name)
        else:
            selected = filedialog.askopenfilename(initialdir=str(current.parent), initialfile=current.name)
        if selected:
            var.set(selected)

    def _set_row_enabled(self, label: str, enabled: bool) -> None:
        state = tk.NORMAL if enabled else tk.DISABLED
        for widget in self.rows[label]:
            if widget is not None:
                widget.configure(state=state)

    def _refresh_mode_state(self) -> None:
        mode = self.mode.get()
        self._set_row_enabled("SPC3 exe", mode != "compress")
        self._set_row_enabled("Compressor CLI", mode == "compress")
        self._set_row_enabled("Root", mode in {"pack", "verify"})
        self._set_row_enabled("Predictor", mode in {"pack", "verify", "unpack"})
        self._set_row_enabled("Input", mode in {"verify", "unpack", "inspect"})
        self._set_row_enabled("Output", mode == "pack")
        self._set_row_enabled("Unpack dir", mode == "unpack")
        self._set_row_enabled("Report", True)
        self._set_row_enabled("Compare A", True)
        self._set_row_enabled("Compare B", True)
        self.typed_check.configure(state=tk.NORMAL if mode == "pack" else tk.DISABLED)
        self.gpu_check.configure(state=tk.NORMAL if mode in {"verify", "unpack"} else tk.DISABLED)
        self.no_source_check.configure(state=tk.NORMAL if mode == "verify" else tk.DISABLED)
        self.compress_target_box.configure(state="readonly" if mode == "compress" else tk.DISABLED)
        if mode == "compress":
            self._set_row_enabled("Root", True)
            self._set_row_enabled("Predictor", True)
            self._set_row_enabled("Input", True)
            self._set_row_enabled("Output", self.compress_target.get() != "all")
            self._set_row_enabled("Report", True)

    def _build_command(self) -> list[str]:
        mode = self.mode.get()
        if mode == "compress":
            cli = Path(self.compressor.get())
            if not cli.is_file():
                raise FileNotFoundError(f"SPC3 compressor CLI not found: {cli}")
            command = [
                sys.executable,
                str(cli),
                "--target",
                self.compress_target.get(),
                "--mode",
                "pack-verify",
                "--input",
                self.input_path.get(),
                "--root",
                self.root_dir.get(),
                "--predictor-json",
                self.predictor.get(),
                "--report",
                self.report_path.get(),
                "--limit-zips",
                self.limit_zips.get(),
                "--level",
                self.level.get(),
            ]
            if self.compress_target.get() != "all":
                command += ["--output", self.output_path.get()]
            if self.codec_profile.get() != "auto":
                command += ["--codec-profile", self.codec_profile.get()]
            if self.typed_level3.get():
                command.append("--typed-level3")
            else:
                command.append("--no-typed-level3")
            return command
        exe = Path(self.exe.get())
        if not exe.is_file():
            raise FileNotFoundError(f"SPC3 executable not found: {exe}")
        command = [str(exe), "--mode", mode, "--report", self.report_path.get()]
        if mode == "pack":
            command += [
                "--root",
                self.root_dir.get(),
                "--predictor",
                self.predictor.get(),
                "--limit-zips",
                self.limit_zips.get(),
                "--level",
                self.level.get(),
                "--output",
                self.output_path.get(),
            ]
            if self.typed_level3.get():
                command.append("--typed-level3")
            if self.codec_profile.get() != "auto":
                command += ["--codec-profile", self.codec_profile.get()]
        elif mode == "verify":
            command += ["--input", self.input_path.get(), "--root", self.root_dir.get(), "--predictor", self.predictor.get()]
            if self.no_source_compare.get():
                command.append("--no-source-compare")
            if self.gpu.get():
                command.append("--gpu-rebuild")
        elif mode == "unpack":
            command += ["--input", self.input_path.get(), "--predictor", self.predictor.get(), "--unpack-dir", self.unpack_dir.get()]
            if self.gpu.get():
                command.append("--gpu-rebuild")
        elif mode == "inspect":
            command += ["--input", self.input_path.get()]
        return command

    def _start_command(self) -> None:
        try:
            command = self._build_command()
        except Exception as error:
            messagebox.showerror("SPC3", str(error))
            return
        self.console.delete("1.0", tk.END)
        self.summary.delete("1.0", tk.END)
        self._append_console("> " + subprocess.list2cmdline(command) + "\n\n")
        self.cancel_requested = False
        self.run_button.configure(state=tk.DISABLED)
        self.cancel_button.configure(state=tk.NORMAL)
        self.worker = threading.Thread(target=self._run_command, args=(command,), daemon=True)
        self.worker.start()

    def _run_command(self, command: list[str]) -> None:
        env = os.environ.copy()
        env["PATH"] = r"C:\msys64\mingw64\bin;" + env.get("PATH", "")
        try:
            self.active_process = subprocess.Popen(
                command,
                cwd=ROOT,
                env=env,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            if self.cancel_requested and self.active_process.poll() is None:
                self.active_process.terminate()
            assert self.active_process.stdout is not None
            for line in self.active_process.stdout:
                self.output_queue.put(line)
            code = self.active_process.wait()
            if self.cancel_requested:
                self.output_queue.put("\nstatus=cancelled\n")
            self.output_queue.put(f"\nexit_code={code}\n")
            self.output_queue.put("__SPC3_DONE__")
        except Exception as error:
            self.output_queue.put(f"\n{error}\n")
            self.output_queue.put("__SPC3_DONE__")

    def _cancel(self) -> None:
        if self.cancel_requested:
            return
        if self.active_process is None:
            if self.worker is not None and self.worker.is_alive():
                self.cancel_requested = True
                self.cancel_button.configure(state=tk.DISABLED)
                self._append_console("\ncancel requested\n")
            return
        if self.active_process.poll() is not None:
            return
        self.cancel_requested = True
        self.cancel_button.configure(state=tk.DISABLED)
        self._append_console("\ncancel requested\n")
        try:
            self.active_process.terminate()
        except Exception as error:
            self._append_console(f"cancel failed: {error}\n")
            self.cancel_requested = False
            self.cancel_button.configure(state=tk.NORMAL)

    def _drain_output(self) -> None:
        while True:
            try:
                item = self.output_queue.get_nowait()
            except queue.Empty:
                break
            if item == "__SPC3_DONE__":
                self.active_process = None
                self.cancel_requested = False
                self.run_button.configure(state=tk.NORMAL)
                self.cancel_button.configure(state=tk.DISABLED)
                self._load_report_summary()
            else:
                self._append_console(item)
        self.after(100, self._drain_output)

    def _append_console(self, text: str) -> None:
        self.console.insert(tk.END, text)
        self.console.see(tk.END)

    def _load_report_summary(self) -> None:
        report = Path(self.report_path.get())
        if not report.is_file():
            return
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
        except Exception as error:
            self.summary.insert(tk.END, f"Could not read report: {error}\n")
            return
        self.summary.delete("1.0", tk.END)
        self.summary.insert(tk.END, "\n".join(report_summary_lines(data)) + "\n")

    def _compare_reports(self) -> None:
        left_path = Path(self.compare_left.get())
        right_path = Path(self.compare_right.get())
        try:
            left = json.loads(left_path.read_text(encoding="utf-8"))
            right = json.loads(right_path.read_text(encoding="utf-8"))
        except Exception as error:
            messagebox.showerror("SPC3", f"Could not compare reports: {error}")
            return
        lines = comparison_lines(left, right, left_path.name, right_path.name)
        self.summary.delete("1.0", tk.END)
        self.summary.insert(tk.END, "\n".join(lines) + "\n")


def main() -> int:
    app = Spc3Gui()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
