from __future__ import annotations

import json
import socket
import subprocess
import time
import urllib.error
import urllib.request
import importlib.util
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
NATIVE_DIR = REPO_ROOT / "tools" / "spinda" / "spinda_workbench_native"
SOURCE = NATIVE_DIR / "spinda_workbench_native.cpp"
BUILD_SCRIPT = NATIVE_DIR / "build_spinda_workbench_native.bat"
EXE = NATIVE_DIR / "spinda_workbench_native.exe"
LEGACY_WORKBENCH = REPO_ROOT / "tools" / "spinda" / "spinda_workbench" / "spinda_workbench.py"
VALID_ZIP_BYTES = b"z" * 2048


def _ensure_native_exe() -> None:
    if not EXE.exists() or SOURCE.stat().st_mtime > EXE.stat().st_mtime:
        subprocess.run(["cmd", "/c", str(BUILD_SCRIPT)], cwd=REPO_ROOT, check=True)


def _run_json(*args: str) -> dict:
    _ensure_native_exe()
    result = subprocess.run(
        [str(EXE), *args],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    return json.loads(result.stdout)


def _load_legacy_workbench():
    module_name = "testable_spinda_workbench_native_parity"
    spec = importlib.util.spec_from_file_location(module_name, LEGACY_WORKBENCH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _make_workbench_tree(tmp_path: Path) -> tuple[Path, Path]:
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    tsv_dir = tmp_path / "TSVs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    (phase3_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0003.spinda80.zip").write_bytes(b"")
    (phase3_dir / "0x0004.spinda80.zip.pid123.tmp").write_bytes(b"tmp")
    (phase3_dir / "bad.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (tsv_dir / "TSV-0001-sid-00008.sav").write_bytes(b"a")
    (tsv_dir / "TSV-0001-sid-00009.sav").write_bytes(b"b")
    (tsv_dir / "TSV-0002-sid-00099.sav").write_bytes(b"c")
    (tsv_dir / "old-name.sav").write_bytes(b"d")
    (tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json").write_text(
        json.dumps(
            {
                "entries": [
                    {"done": True, "shiny_value": "0x0001"},
                    {"done": False, "shiny_value": "0x0002", "error": "retry"},
                ]
            }
        ),
        encoding="utf-8",
    )
    return phase3_dir, tsv_dir


def test_native_self_test_passes() -> None:
    _ensure_native_exe()
    result = subprocess.run([str(EXE), "--self-test"], cwd=REPO_ROOT, text=True, capture_output=True, check=True)

    assert "self-test passed" in result.stdout


def test_native_status_scan_matches_workbench_contract(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)

    payload = _run_json(
        "--status-json",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
        "--target-phase3-lanes",
        "4",
    )

    assert payload["server"]["runtime"] == "native-cpp"
    assert payload["phase3"]["complete_lanes"] == 2
    assert payload["phase3"]["missing_lanes"] == 2
    assert payload["phase3"]["zero_size_zips"] == 1
    assert payload["phase3"]["tmp_files"] == 1
    assert payload["phase3"]["bad_names"] == 1
    assert payload["phase3"]["bad_artifacts"] == 3
    assert payload["phase3"]["complete_lane_ranges"] == ["0x0001-0x0002"]
    assert payload["tsv"]["complete_saves"] == 1
    assert payload["tsv"]["duplicate_tsvs"] == 1
    assert payload["tsv"]["duplicate_files"] == 1
    assert payload["tsv"]["mismatched_files"] == 1
    assert payload["tsv"]["invalid_files"] == 1
    assert payload["tsv"]["ledger_done"] == 1
    assert payload["tsv"]["ledger_errors"] == 1


def test_native_phase3_scan_handles_case_scope_and_zero_sample_limit(tmp_path: Path) -> None:
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    tsv_dir = tmp_path / "TSVs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    (phase3_dir / "0X000A.spinda80.ZIP").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x000B.spinda80.ZIP.pid123.TMP").write_bytes(b"tmp")
    (phase3_dir / "bad.SPINDa80.ZIP.extra").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0000.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0xFFFF.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    payload = _run_json(
        "--status-json",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
        "--target-phase3-lanes",
        "0x000A",
        "--sample-limit",
        "0",
    )

    assert payload["phase3"]["complete_lanes"] == 1
    assert payload["phase3"]["complete_lane_ranges"] == ["0x000A"]
    assert payload["phase3"]["tmp_files"] == 1
    assert payload["phase3"]["bad_names"] == 1
    assert payload["phase3"]["out_of_scope_zips"] == 2
    assert payload["phase3"]["bad_artifacts"] == 4
    assert payload["phase3"]["samples"] == {}
    assert payload["tsv"]["samples"] == {}


def test_native_phase3_target_65535_includes_ffff_lane(tmp_path: Path) -> None:
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    tsv_dir = tmp_path / "TSVs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    (phase3_dir / "0xFFFF.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    payload = _run_json(
        "--status-json",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
        "--target-phase3-lanes",
        "65535",
    )

    assert payload["phase3"]["target_lanes"] == 65535
    assert payload["phase3"]["complete_lanes"] == 1
    assert payload["phase3"]["out_of_scope_zips"] == 0
    assert payload["phase3"]["last_good_lane"] == "0xFFFF"


def test_native_ledger_truthiness_legacy_count_and_load_errors(tmp_path: Path) -> None:
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    tsv_dir = tmp_path / "TSVs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    ledger = tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json"
    base_args = ("--status-json", "--phase3-dir", str(phase3_dir), "--tsv-dir", str(tsv_dir))

    ledger.write_text(
        json.dumps(
            {
                "entries": [
                    {"done": True, "error": ""},
                    {"done": True, "error": 0},
                    {"done": False, "error": None},
                    {"route_schedule_error": False},
                    {"route_schedule_error": {"message": "retry"}},
                    {"error": "retry"},
                ]
            }
        ),
        encoding="utf-8",
    )
    payload = _run_json(*base_args)
    assert payload["tsv"]["ledger_done"] == 2
    assert payload["tsv"]["ledger_errors"] == 2

    ledger.write_text(json.dumps({"complete_shiny_values": True}), encoding="utf-8")
    payload = _run_json(*base_args)
    assert payload["tsv"]["ledger_done"] is None
    assert payload["tsv"]["ledger_errors"] is None

    ledger.write_text(json.dumps({"complete_shiny_values": 17}), encoding="utf-8")
    payload = _run_json(*base_args)
    assert payload["tsv"]["ledger_done"] == 17

    ledger.write_text("[1, 2, 3]", encoding="utf-8")
    payload = _run_json(*base_args)
    assert payload["tsv"]["ledger_load_error"] == "top-level JSON is not an object"


def test_native_pid_report_and_pattern_search(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)
    (phase3_dir / "0x5678.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    report = _run_json("--pid", "0X12345678.PK3", "--phase3-dir", str(phase3_dir), "--tsv-dir", str(tsv_dir))

    assert report["pid"] == "0x12345678"
    assert report["entry_name"] == "0x12345678.pk3"
    assert report["matching_tsv"] == ((0x1234 ^ 0x5678) >> 3)
    assert report["zip_exists"] is True
    assert report["painter"]["spots"][0]["offset_x"] == 8
    assert report["painter"]["spots"][0]["offset_y"] == 7
    assert report["painter"]["stats"]["ability_slot"] == "First"
    assert "funny_score" in report["painter"]["traits"]
    assert report["painter"]["svg"].startswith("<svg")

    suggestions = _run_json(
        "--suggest",
        "centered",
        "--start",
        "0x00000000",
        "--scan-limit",
        "64",
        "--count",
        "4",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
    )

    scores = [row["score"] for row in suggestions["results"]]
    assert suggestions["mode"] == "centered"
    assert suggestions["scan_limit"] == 64
    assert len(suggestions["results"]) == 4
    assert scores == sorted(scores, reverse=True)
    assert all(row["pid"].startswith("0x000000") for row in suggestions["results"])


def test_native_pattern_search_wraparound_and_count_clamp(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)

    suggestions = _run_json(
        "--suggest",
        "centered",
        "--start",
        "0xFFFFFFFE",
        "--scan-limit",
        "4",
        "--count",
        "10",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
    )

    result_pids = {row["pid"] for row in suggestions["results"]}
    assert suggestions["count"] == 4
    assert suggestions["scan_limit"] == 4
    assert result_pids == {"0xFFFFFFFE", "0xFFFFFFFF", "0x00000000", "0x00000001"}


def test_native_command_previews_quote_paths_and_prefer_repo_python(tmp_path: Path) -> None:
    phase3_dir = tmp_path / "Owner's Phase 3"
    tsv_dir = tmp_path / "TSV Saves"
    hatch_dir = tmp_path / "Hatched ZIPs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    hatch_dir.mkdir()

    commands = _run_json(
        "--commands-json",
        "--phase3-dir",
        str(phase3_dir),
        "--tsv-dir",
        str(tsv_dir),
        "--hatch-output-dir",
        str(hatch_dir),
    )

    assert "--phase3-dir '" + str(phase3_dir).replace("'", "''") + "'" in commands["workbench_native"]
    assert "--root '" + str(phase3_dir).replace("'", "''") + "'" in commands["phase3_manifest"]
    assert f"--save-dir '{tsv_dir}'" in commands["tsv_party"]
    assert f"--shiny-output '{hatch_dir / 'spinda-hatched-shiny.zip'}'" in commands["hatch_splitter"]
    repo_python = REPO_ROOT / ".venv-mgba" / "bin" / "python.exe"
    if repo_python.exists():
        assert commands["phase3_manifest"].startswith(f"& '{repo_python}' ")


def test_native_reports_tool_ages_for_existing_files(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)

    payload = _run_json("--status-json", "--phase3-dir", str(phase3_dir), "--tsv-dir", str(tsv_dir))

    native_tool = payload["tools"]["workbench_native"]
    assert native_tool["exists"] is True
    assert isinstance(native_tool["age_seconds"], (int, float))
    assert native_tool["age_seconds"] >= 0


def test_native_reports_file_paths_as_folder_errors(tmp_path: Path) -> None:
    phase3_path = tmp_path / "Phase3SpindaBlocks"
    tsv_path = tmp_path / "TSVs"
    phase3_path.write_text("not a directory", encoding="utf-8")
    tsv_path.write_text("not a directory", encoding="utf-8")

    payload = _run_json("--status-json", "--phase3-dir", str(phase3_path), "--tsv-dir", str(tsv_path))

    assert payload["phase3"]["complete_lanes"] == 0
    assert payload["phase3"]["samples"]["folder_errors"] == [f"not a directory: {phase3_path}"]
    assert payload["tsv"]["complete_saves"] == 0
    assert payload["tsv"]["samples"]["folder_errors"] == [f"not a directory: {tsv_path}"]


def test_native_rejects_bad_pid_text() -> None:
    _ensure_native_exe()
    result = subprocess.run(
        [str(EXE), "--pid", "not-a-pid"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "PID must be 8 hex digits" in result.stderr


def test_native_numeric_parser_uses_decimal_unless_hex_prefixed() -> None:
    _ensure_native_exe()
    leading_zero = _run_json("--suggest", "centered", "--start", "010", "--scan-limit", "1", "--count", "1")
    assert leading_zero["start_pid"] == "0x0000000A"

    upper_hex = _run_json("--suggest", "centered", "--start", "0X10", "--scan-limit", "1", "--count", "1")
    assert upper_hex["start_pid"] == "0x00000010"

    result = subprocess.run(
        [str(EXE), "--suggest", "centered", "--start", "12abc", "--scan-limit", "1", "--count", "1"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "start has trailing characters" in result.stderr


def test_native_rejects_out_of_range_numeric_options() -> None:
    _ensure_native_exe()
    result = subprocess.run(
        [str(EXE), "--suggest", "centered", "--start", "-1", "--scan-limit", "1", "--count", "1"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "start must be between 0 and 4294967295" in result.stderr

    result = subprocess.run(
        [str(EXE), "--status-json", "--port", "70000"],
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 2
    assert "port must be between 1 and 65535" in result.stderr


def test_native_trait_scores_match_legacy_python_reference(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)
    legacy = _load_legacy_workbench()

    for pid in (0x00000000, 0x12345678, 0x89ABCDEF, 0xFFFFFFFF):
        native = _run_json("--pid", f"0x{pid:08X}", "--phase3-dir", str(phase3_dir), "--tsv-dir", str(tsv_dir))
        legacy_traits = legacy.spinda_traits(legacy.spinda_spots(pid))
        for key, expected in legacy_traits.items():
            assert abs(native["painter"]["traits"][key] - expected) <= 0.002


def test_native_http_server_serves_status_and_pid(tmp_path: Path) -> None:
    phase3_dir, tsv_dir = _make_workbench_tree(tmp_path)
    _ensure_native_exe()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]

    process = subprocess.Popen(
        [
            str(EXE),
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--phase3-dir",
            str(phase3_dir),
            "--tsv-dir",
            str(tsv_dir),
            "--target-phase3-lanes",
            "4",
        ],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        base = f"http://127.0.0.1:{port}"
        last_error: Exception | None = None
        for _ in range(40):
            try:
                with urllib.request.urlopen(f"{base}/api/status", timeout=1) as response:
                    status = json.loads(response.read().decode("utf-8"))
                break
            except Exception as error:  # noqa: BLE001 - polling a just-started local process.
                last_error = error
                time.sleep(0.1)
        else:
            raise AssertionError(f"native workbench server did not answer: {last_error}")

        assert status["phase3"]["complete_lanes"] == 2
        with urllib.request.urlopen(f"{base}/api/pid/0x00010001", timeout=2) as response:
            pid_payload = json.loads(response.read().decode("utf-8"))
        assert pid_payload["matching_tsv"] == 0
        with urllib.request.urlopen(
            f"{base}/api/suggest/centered?start=0xFFFFFFFE&start=0&scan_limit=1&count=1",
            timeout=2,
        ) as response:
            duplicate_query_payload = json.loads(response.read().decode("utf-8"))
        assert duplicate_query_payload["start_pid"] == "0xFFFFFFFE"
        with urllib.request.urlopen(urllib.request.Request(f"{base}/api/status", method="HEAD"), timeout=2) as response:
            assert response.status == 200
            assert response.read() == b""
        with urllib.request.urlopen(f"{base}/favicon.ico", timeout=2) as response:
            assert response.status == 204
            assert response.read() == b""
        try:
            urllib.request.urlopen(
                urllib.request.Request(f"{base}/api/status", data=b"", method="POST"),
                timeout=2,
            )
        except urllib.error.HTTPError as error:
            assert error.code == 405
            assert "GET or HEAD only" in error.read().decode("utf-8")
        else:
            raise AssertionError("POST should return HTTP 405")
        try:
            urllib.request.urlopen(f"{base}/api/suggest/centered?scan_limit=0", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "scan_limit must be between 1" in error.read().decode("utf-8")
        else:
            raise AssertionError("zero scan_limit should return HTTP 400")
        with urllib.request.urlopen(
            f"{base}/api/suggest/centered?start=010&scan_limit=1&count=1",
            timeout=2,
        ) as response:
            leading_zero_payload = json.loads(response.read().decode("utf-8"))
        assert leading_zero_payload["start_pid"] == "0x0000000A"
        try:
            urllib.request.urlopen(f"{base}/api/pid/bad", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 400
            assert "PID must be 8 hex digits" in error.read().decode("utf-8")
        else:
            raise AssertionError("bad PID request should return HTTP 400")
        try:
            urllib.request.urlopen(f"{base}/api/pid/0x00010001+", timeout=2)
        except urllib.error.HTTPError as error:
            assert error.code == 400
        else:
            raise AssertionError("literal plus in path should not decode as whitespace")
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"BAD\r\n\r\n")
            raw = client.recv(256)
        assert raw.startswith(b"HTTP/1.1 400 Bad Request")
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"GET /api/status?bad=%ZZ HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
            raw = client.recv(512)
        assert raw.startswith(b"HTTP/1.1 400 Bad Request")
        assert b"URL percent escape is invalid" in raw
        with urllib.request.urlopen(f"{base}/", timeout=2) as response:
            html = response.read().decode("utf-8")
        assert "Native C++ read-only panel" in html
        assert "async function fetchJson" in html
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
