#!/usr/bin/env python3
"""Regression tests for the SPC3 umbrella compressor CLI and GUI wrapper."""

from __future__ import annotations

import argparse
import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from types import ModuleType
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
COMPRESS = ROOT / "tools" / "spinda" / "spc3_compress.py"
GUI = ROOT / "tools" / "spinda" / "spc3_gui" / "spc3_gui.py"


def load_module(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise AssertionError(f"could not load module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_default_pack_outputs_stay_in_output_dir() -> None:
    compress = load_module(COMPRESS, "spc3_compress_paths_under_test")
    with tempfile.TemporaryDirectory() as tmp:
        output_dir = Path(tmp)
        for target in compress.TARGETS:
            pack_output = compress.default_output_for(target, output_dir, True)
            verify_output = compress.default_output_for(target, output_dir, False)
            assert pack_output.parent == output_dir
            assert pack_output.name == compress.SPECS[target].default_output.name
            assert verify_output == compress.SPECS[target].default_output


def test_native_v2_verify_uses_input_not_output() -> None:
    compress = load_module(COMPRESS, "spc3_compress_v2_under_test")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        native = tmp_path / "spc3_prototype.exe"
        native.write_text("", encoding="utf-8")
        input_spc3 = tmp_path / "input.spc3"
        output_spc3 = tmp_path / "wrong-output.spc3"
        report = tmp_path / "report.json"
        input_spc3.write_bytes(b"input")
        output_spc3.write_bytes(b"output")
        calls: list[list[str]] = []

        def fake_run(command: list[str], *, cwd: Path, check: bool) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            report.write_text(
                json.dumps(
                    {
                        "schema": "spc3_verify_report.v1",
                        "ok": True,
                        "internal_crc_mismatches": 0,
                        "source_compare_mismatches": 0,
                        "total_ms": 2500.0,
                    }
                ),
                encoding="utf-8",
            )
            return subprocess.CompletedProcess(command, 0)

        args = argparse.Namespace(
            native_exe=native,
            root=tmp_path / "root",
            predictor_json=tmp_path / "predictor.json",
            limit_zips="all",
            level=3,
            typed_level3=True,
            codec_profile="fast",
            source_compare=False,
            output=output_spc3,
            input=input_spc3,
            sample_lanes=None,
        )
        with mock.patch.object(compress.subprocess, "run", side_effect=fake_run):
            result = compress.run_native_v2(args=args, mode="verify", output_path=output_spc3, report_path=report)

        assert result["ok"] is True
        assert calls
        command = calls[0]
        assert command[command.index("--input") + 1] == str(input_spc3)
        assert str(output_spc3) not in command


def test_summarize_native_report_converts_milliseconds() -> None:
    compress = load_module(COMPRESS, "spc3_compress_summary_under_test")
    report = {
        "schema": "spc3_verify_report.v1",
        "ok": True,
        "internal_crc_mismatches": 2,
        "source_compare_mismatches": 3,
        "total_ms": 1234.5,
        "spc3_size_bytes": 999,
    }
    summary = compress.summarize_report(report, Path("missing.spc3"))
    assert summary["status"] == "ok"
    assert summary["mismatch_count"] == 5
    assert summary["elapsed_seconds"] == 1.2345
    assert summary["size_bytes"] == 999


def test_all_target_summary_uses_per_target_reports() -> None:
    compress = load_module(COMPRESS, "spc3_compress_main_under_test")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        summary_report = tmp_path / "all.json"

        def fake_run_target(
            *,
            target: str,
            args: argparse.Namespace,
            mode: str,
            output_path: Path,
            report_path: Path,
        ) -> dict[str, object]:
            assert mode == "audit"
            assert report_path.name == f"all.{target}.json"
            return {
                "schema": f"fake_{target}",
                "status": "ok",
                "mismatch_count": 0,
                "new_size_bytes": 100 + compress.TARGETS.index(target),
                "elapsed_seconds": 0.01,
            }

        argv = [
            "spc3_compress.py",
            "--target",
            "all",
            "--mode",
            "audit",
            "--report",
            str(summary_report),
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(compress, "run_target", side_effect=fake_run_target):
            code = compress.main()

        data = json.loads(summary_report.read_text(encoding="utf-8"))
        assert code == 0
        assert data["schema"] == "spc3_umbrella_compress.v1"
        assert data["failed_count"] == 0
        assert [item["target"] for item in data["results"]] == list(compress.TARGETS)
        assert [item["report"] for item in data["results"]] == [
            str(summary_report.with_name(f"all.{target}.json")) for target in compress.TARGETS
        ]


def test_v2_verify_main_ignores_output_path_for_result_sizing() -> None:
    compress = load_module(COMPRESS, "spc3_compress_v2_main_under_test")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        input_spc3 = tmp_path / "input.spc3"
        output_spc3 = tmp_path / "wrong-output.spc3"
        report = tmp_path / "verify.json"
        input_spc3.write_bytes(b"12345")
        captured: dict[str, Path] = {}

        def fake_run_target(
            *,
            target: str,
            args: argparse.Namespace,
            mode: str,
            output_path: Path,
            report_path: Path,
        ) -> dict[str, object]:
            captured["output_path"] = output_path
            assert target == "v2"
            assert mode == "audit"
            assert report_path == report
            return {
                "schema": "spc3_verify_report.v1",
                "ok": True,
                "internal_crc_mismatches": 0,
                "source_compare_mismatches": 0,
                "total_ms": 1000.0,
            }

        argv = [
            "spc3_compress.py",
            "--target",
            "v2",
            "--mode",
            "audit",
            "--input",
            str(input_spc3),
            "--output",
            str(output_spc3),
            "--report",
            str(report),
        ]
        with mock.patch.object(sys, "argv", argv), mock.patch.object(compress, "run_target", side_effect=fake_run_target):
            code = compress.main()

        assert code == 0
        assert captured["output_path"] == input_spc3


class Value:
    def __init__(self, value: object) -> None:
        self.value = value

    def get(self) -> object:
        return self.value


class Widget:
    def __init__(self) -> None:
        self.states: list[object] = []

    def configure(self, *, state: object) -> None:
        self.states.append(state)


def make_gui_shell(gui: ModuleType) -> object:
    shell = gui.Spc3Gui.__new__(gui.Spc3Gui)
    shell.mode = Value("compress")
    shell.compressor = Value(str(COMPRESS))
    shell.compress_target = Value("v8")
    shell.input_path = Value("input.spc3")
    shell.root_dir = Value("root")
    shell.predictor = Value("predictor.json")
    shell.report_path = Value("report.json")
    shell.limit_zips = Value("all")
    shell.level = Value("3")
    shell.output_path = Value("output.spc3")
    shell.codec_profile = Value("fast")
    shell.typed_level3 = Value(True)
    shell.rows = {
        label: (Widget(), Widget(), Widget())
        for label in ("SPC3 exe", "Compressor CLI", "Root", "Predictor", "Input", "Output", "Unpack dir", "Report", "Compare A", "Compare B")
    }
    shell.typed_check = Widget()
    shell.gpu_check = Widget()
    shell.no_source_check = Widget()
    shell.compress_target_box = Widget()
    return shell


def test_gui_compress_command_and_all_target_output_state() -> None:
    gui = load_module(GUI, "spc3_gui_under_test")
    shell = make_gui_shell(gui)

    command = gui.Spc3Gui._build_command(shell)
    assert command[command.index("--target") + 1] == "v8"
    assert command[command.index("--mode") + 1] == "pack-verify"
    assert "--output" in command
    assert "--typed-level3" in command

    shell.compress_target = Value("all")
    all_command = gui.Spc3Gui._build_command(shell)
    assert "--output" not in all_command

    gui.Spc3Gui._refresh_mode_state(shell)
    output_widgets = shell.rows["Output"]
    assert output_widgets[0].states[-1] == gui.tk.DISABLED
    assert shell.compress_target_box.states[-1] == "readonly"


def test_gui_umbrella_summary_fields_are_visible() -> None:
    gui = load_module(GUI, "spc3_gui_summary_under_test")
    lines = gui.report_summary_lines(
        {
            "schema": "spc3_umbrella_compress.v1",
            "mode": "audit",
            "target": "all",
            "failed_count": 0,
            "status": "ok",
            "mismatch_count": 0,
            "new_size_bytes": 103403124,
        }
    )
    text = "\n".join(lines)
    assert "target: all" in text
    assert "failed count: 0" in text
    assert "mismatch count: 0" in text
    assert "new size bytes: 103403124" in text


def test_documentation_mentions_umbrella_cli() -> None:
    readme = (ROOT / "tools" / "spinda" / "spc3_gui" / "README.md").read_text(encoding="utf-8")
    inventory_path = next(
        path
        for path in (
            ROOT / "markdown-files" / "python_lua_scrips.md",
            ROOT / "docs" / "python_lua_scrips.md",
        )
        if path.is_file()
    )
    inventory = inventory_path.read_text(encoding="utf-8")
    assert "spc3_compress.py" in readme
    assert "compress" in readme
    assert "spc3_compress.py" in inventory


def main() -> int:
    tests = [
        test_default_pack_outputs_stay_in_output_dir,
        test_native_v2_verify_uses_input_not_output,
        test_summarize_native_report_converts_milliseconds,
        test_all_target_summary_uses_per_target_reports,
        test_v2_verify_main_ignores_output_path_for_result_sizing,
        test_gui_compress_command_and_all_target_output_state,
        test_gui_umbrella_summary_fields_are_visible,
        test_documentation_mentions_umbrella_cli,
    ]
    for test in tests:
        test()
        print(f"ok {test.__name__}")
    print(f"{len(tests)} tests passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
