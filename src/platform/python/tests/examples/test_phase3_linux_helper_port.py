from __future__ import annotations

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[5]
WORKER_POOL = REPO_ROOT / "tools" / "spinda" / "native_phase3_worker_pool.py"
LEDGER_CLIENT = REPO_ROOT / "tools" / "spinda" / "phase3_ledger_worker_client.py"
LINUX_BUILD = REPO_ROOT / "tools" / "spinda" / "build_phase3_cli_linux.sh"
LINUX_HELPER = REPO_ROOT / "tools" / "spinda" / "run_phase3_ledger_helper.sh"
LINUX_VALIDATOR = REPO_ROOT / "tools" / "spinda" / "check_linux_helper_port.py"
LINUX_TEST = REPO_ROOT / "src" / "platform" / "python" / "tests" / "examples" / "test_phase3_linux_helper_port.py"


def _load_module(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _docs_root() -> Path:
    main_docs = REPO_ROOT / "markdown-files"
    if main_docs.is_dir():
        return main_docs
    return REPO_ROOT / "docs"


def test_linux_defaults_use_native_cli_without_windows_runtime_paths(tmp_path: Path) -> None:
    worker = _load_module(WORKER_POOL, "testable_linux_worker_pool")
    linux_cli = worker.default_phase3_cli_exe(tmp_path, "posix")
    linux_qt = worker.default_mgba_exe(tmp_path, "posix")
    runtime_entries = worker.cli_runtime_path_entries(linux_cli, "posix")

    assert linux_cli == tmp_path / "build-linux-spinda-cli" / "mgba-spinda-phase3"
    assert linux_qt == tmp_path / "build-linux-qt" / "mgba-qt"
    assert linux_cli.suffix != ".exe"
    assert all("msys" not in str(path).lower() for path in runtime_entries)
    assert all("devkitpro" not in str(path).lower() for path in runtime_entries)


def test_linux_launcher_keeps_ledger_args_and_worker_passthrough_separate() -> None:
    helper = LINUX_HELPER.read_text(encoding="utf-8")

    assert 'PHASE3_CLI_EXE="${PHASE3_CLI_EXE:-$ROOT/build-linux-spinda-cli/mgba-spinda-phase3}"' in helper
    assert 'ROM="${ROM:-$ROOT/inputs/lg.gba}"' in helper
    assert 'SECONDHALF_CSV="${SECONDHALF_CSV:-$ROOT/inputs/secondhalf.csv}"' in helper
    assert 'PHASE2_DIR="${PHASE2_DIR:-$ROOT/Phase2PickupStates}"' in helper
    assert "--runner cli" in helper
    assert "--phase3-cli-exe \"$PHASE3_CLI_EXE\"" in helper
    assert "--rom \"$ROM\"" in helper
    assert "--phase2-dir \"$PHASE2_DIR\"" in helper
    assert "--secondhalf-csv \"$SECONDHALF_CSV\"" in helper
    assert helper.count("--output-dir \"$OUTPUT_DIR\"") == 1
    assert "mGBA.exe" not in helper
    assert "mgba-qt" not in helper


def test_linux_build_script_is_headless_phase3_only() -> None:
    build = LINUX_BUILD.read_text(encoding="utf-8")

    assert "build-linux-spinda-cli" in build
    assert "--target mgba-spinda-phase3" in build
    assert "-DBUILD_SPINDA_PHASE3_CLI=ON" in build
    for disabled in (
        "-DBUILD_QT=OFF",
        "-DBUILD_SDL=OFF",
        "-DBUILD_PYTHON=OFF",
        "-DENABLE_SCRIPTING=OFF",
        "-DUSE_LUA=OFF",
        "-DUSE_FFMPEG=OFF",
        "-DUSE_SQLITE3=OFF",
        "-DM_CORE_GB=OFF",
    ):
        assert disabled in build
    assert "-DM_CORE_GBA=ON" in build
    assert "PHASE3_CLI_PGO_GENERATE" in build
    assert "PHASE3_CLI_PGO_USE" in build
    assert "PHASE3_CLI_LTO" in build


def test_ledger_client_forwards_linux_worker_options_without_claim_logic_knowing_platform(tmp_path: Path) -> None:
    client = _load_module(LEDGER_CLIENT, "testable_linux_ledger_client")
    args, passthrough = client.parse_args(
        [
            "--python-exe",
            "python3",
            "--worker-pool-script",
            "tools/spinda/native_phase3_worker_pool.py",
            "--output-dir",
            str(tmp_path / "Phase3SpindaBlocks"),
            "--",
            "--runner",
            "cli",
            "--phase3-cli-exe",
            "build-linux-spinda-cli/mgba-spinda-phase3",
            "--rom",
            "inputs/lg.gba",
            "--phase2-dir",
            "Phase2PickupStates",
            "--secondhalf-csv",
            "inputs/secondhalf.csv",
        ]
    )

    command = client.worker_pool_command(args, ["0x1234"], passthrough)

    assert command[0] == "python3"
    assert Path(command[1]) == Path("tools/spinda/native_phase3_worker_pool.py")
    assert command[2:4] == ["--lanes", "0x1234"]
    assert "--skip-existing-by-name" in command
    assert "--overwrite" in command
    assert "build-linux-spinda-cli/mgba-spinda-phase3" in command
    assert "inputs/lg.gba" in command
    assert "Phase2PickupStates" in command
    assert "inputs/secondhalf.csv" in command


def test_linux_helper_docs_state_proof_gate_and_no_qt_port() -> None:
    docs_root = _docs_root()
    linux_doc = (docs_root / "PHASE3_LINUX_HELPER_NODE.md").read_text(encoding="utf-8")
    command_center_doc = (docs_root / "PHASE3_COMMAND_CENTER_GUIDE.md").read_text(encoding="utf-8")
    run_guide = (REPO_ROOT / "RUN_GUIDE.md" if (REPO_ROOT / "RUN_GUIDE.md").is_file() else docs_root / "RUN_GUIDE.md").read_text(encoding="utf-8")

    assert "It does not port Qt" in linux_doc
    assert "No live Linux workstation lane has been run yet" in linux_doc
    assert "First Proof Run" in linux_doc
    assert "build_phase3_cli_linux.sh" in linux_doc
    assert "run_phase3_ledger_helper.sh" in linux_doc
    assert "Linux helper nodes use the same ledger API but skip Qt entirely" in command_center_doc
    assert "Linux Phase 3 Helper Node" in run_guide


def test_clean_repo_layout_has_linux_helper_but_no_private_artifacts() -> None:
    if REPO_ROOT.name != "github-clean":
        pytest.skip("clean-repo-only artifact test")

    assert LINUX_BUILD.is_file()
    assert LINUX_HELPER.is_file()
    assert not (REPO_ROOT / "Phase2PickupStates").exists()
    assert not (REPO_ROOT / "Phase3SpindaBlocks").exists()
    assert not (REPO_ROOT / "Assisted-baking").exists()
    assert not list(REPO_ROOT.rglob("*.gba"))
    assert not list(REPO_ROOT.rglob("*.sav"))
    assert not list(REPO_ROOT.rglob("*.ss0"))
    assert not list(REPO_ROOT.rglob("*.spinda80.zip"))


def test_assisted_baking_layout_has_linux_helper_and_personal_inputs_when_present() -> None:
    if REPO_ROOT.name != "Assisted-baking":
        pytest.skip("assisted-package-only readiness test")

    manifest = json.loads((REPO_ROOT / "ASSISTED_PACKAGE_MANIFEST.json").read_text(encoding="utf-8-sig"))
    audit = (REPO_ROOT / "ASSISTED_PACKAGE_AUDIT.md").read_text(encoding="utf-8")

    assert LINUX_BUILD.is_file()
    assert LINUX_HELPER.is_file()
    assert (REPO_ROOT / "docs" / "PHASE3_LINUX_HELPER_NODE.md").is_file()
    assert (REPO_ROOT / "inputs" / "lg.gba").is_file()
    assert (REPO_ROOT / "inputs" / "secondhalf.csv").is_file()
    assert (REPO_ROOT / "Phase2PickupStates" / "0x0000.ss0").is_file()
    assert (REPO_ROOT / "Phase2PickupStates" / "0xFFFF.ss0").is_file()
    assert manifest["linux_helper_included"] is True
    assert "Linux helper path still needs one live Linux proof lane" in audit
    start_command_center = (REPO_ROOT / "START_COMMAND_CENTER.cmd").read_text(encoding="utf-8")
    start_ledger = (REPO_ROOT / "START_LEDGER_ASSISTED_WORKERS.cmd").read_text(encoding="utf-8")
    assert "phase3_command_center.cmd" in start_command_center
    assert "phase3_command_center_web.py" not in start_command_center
    assert "--status-out" in start_ledger
    assert "_phase3_ledger_worker_client_status.json" in start_ledger
    assert "OWNER-IP" not in start_ledger
    assert "Coordinator URL is required" in start_ledger


def test_path_separator_handling_uses_os_pathsep_not_hardcoded_semicolon() -> None:
    worker_source = WORKER_POOL.read_text(encoding="utf-8")

    assert "os.pathsep" in worker_source
    assert "split(os.pathsep)" in worker_source
    assert "join(prepend + current)" in worker_source


def test_linux_helper_validator_passes_for_current_tree() -> None:
    validator = _load_module(LINUX_VALIDATOR, "testable_linux_helper_validator")
    mode = validator.detect_mode(REPO_ROOT)
    results = validator.validate(REPO_ROOT, mode, skip_phase2_count=True)

    failed = [result for result in results if not result.ok]
    assert not failed, [f"{result.name}: {result.detail}" for result in failed]


def test_linux_helper_validator_detects_clean_artifacts(tmp_path: Path) -> None:
    validator = _load_module(LINUX_VALIDATOR, "testable_linux_helper_validator_artifacts")
    (tmp_path / "tools" / "spinda").mkdir(parents=True)
    (tmp_path / "src" / "platform" / "python" / "tests" / "examples").mkdir(parents=True)
    (tmp_path / "docs").mkdir()
    for source in (LINUX_BUILD, LINUX_HELPER, WORKER_POOL, LEDGER_CLIENT, LINUX_TEST):
        rel = source.relative_to(REPO_ROOT)
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "docs" / "PHASE3_LINUX_HELPER_NODE.md").write_text("Linux helper", encoding="utf-8")
    (tmp_path / "inputs.gba").write_bytes(b"rom")

    results = validator.validate(tmp_path, "clean", skip_phase2_count=True)
    artifact_result = next(result for result in results if result.name == "clean repo has no private artifacts")

    assert not artifact_result.ok
    assert "inputs.gba" in artifact_result.detail


def test_linux_helper_validator_rejects_crlf_shell_script(tmp_path: Path) -> None:
    validator = _load_module(LINUX_VALIDATOR, "testable_linux_helper_validator_crlf")
    for source in (LINUX_BUILD, LINUX_HELPER, WORKER_POOL, LEDGER_CLIENT, LINUX_TEST):
        rel = source.relative_to(REPO_ROOT)
        target = tmp_path / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    doc = tmp_path / "docs" / "PHASE3_LINUX_HELPER_NODE.md"
    doc.parent.mkdir(parents=True, exist_ok=True)
    doc.write_text("Linux helper", encoding="utf-8")
    (tmp_path / LINUX_HELPER.relative_to(REPO_ROOT)).write_text("#!/usr/bin/env bash\r\nset -euo pipefail\r\n", encoding="utf-8", newline="")

    results = validator.validate(tmp_path, "clean", skip_phase2_count=True, bash=None)
    line_endings = next(result for result in results if result.name == f"linux shell LF endings {validator.LINUX_HELPER}")
    shebang = next(result for result in results if result.name == f"linux shell shebang {validator.LINUX_HELPER}")

    assert not line_endings.ok
    assert not shebang.ok
