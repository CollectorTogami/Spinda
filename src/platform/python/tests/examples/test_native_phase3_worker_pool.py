from __future__ import annotations

import importlib.util
import os
import sys
import zipfile
from collections import deque
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[5]
SCRIPT_PATH = REPO_ROOT / "tools" / "spinda" / "native_phase3_worker_pool.py"
BENCHMARK_SCRIPT_PATH = REPO_ROOT / "tools" / "spinda" / "benchmark_phase3_worker_counts.py"


def _load_module():
    module_name = "testable_native_phase3_worker_pool"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_benchmark_module():
    module_name = "testable_benchmark_phase3_worker_counts"
    spec = importlib.util.spec_from_file_location(module_name, BENCHMARK_SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_parse_lanes_accepts_hex_lists_and_ranges() -> None:
    module = _load_module()

    assert module.parse_lanes(["0x0002", "0x0004-0x0006", "8,9"]) == [
        0x0002,
        0x0004,
        0x0005,
        0x0006,
        8,
        9,
    ]


def test_main_reports_bad_lane_ranges_as_argparse_errors(capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()

    with pytest.raises(SystemExit) as exc_info:
        module.main(["--lanes", "0x10000", "--allow-missing-inputs", "--dry-run"])

    assert exc_info.value.code == 2
    assert "lane must fit in 16 bits" in capsys.readouterr().err


def test_format_lane_bundle_keeps_non_contiguous_ids_explicit() -> None:
    module = _load_module()

    assert module.format_lane_bundle([0x0002, 0x0003]) == "0x0002..0x0003"
    assert module.format_lane_bundle([0x0002, 0x0004]) == "0x0002,0x0004"


def test_default_paths_stay_in_mgba_workspace() -> None:
    module = _load_module()

    assert module.ROOT == REPO_ROOT
    assert module.DEFAULT_PHASE2_DIR == REPO_ROOT / "Phase2PickupStates"
    assert module.DEFAULT_PHASE3_CLI_EXE == REPO_ROOT / "build-mingw64-spinda-cli-lto" / "mgba-spinda-phase3.exe"
    assert module.DEFAULT_MGBA_EXE == REPO_ROOT / "build-mingw64-python-qt" / "mGBA.exe"
    assert module.DEFAULT_CACHE_DIR == REPO_ROOT / "Phase3SpindaBlocks" / "_cache"


def test_platform_specific_default_executable_paths(tmp_path: Path) -> None:
    module = _load_module()

    assert module.platform_executable_name("mgba-spinda-phase3", "nt") == "mgba-spinda-phase3.exe"
    assert module.platform_executable_name("mgba-spinda-phase3", "posix") == "mgba-spinda-phase3"
    assert module.default_phase3_cli_exe(tmp_path, "nt") == tmp_path / "build-mingw64-spinda-cli-lto" / "mgba-spinda-phase3.exe"
    assert module.default_phase3_cli_exe(tmp_path, "posix") == tmp_path / "build-linux-spinda-cli" / "mgba-spinda-phase3"
    assert module.default_mgba_exe(tmp_path, "nt") == tmp_path / "build-mingw64-python-qt" / "mGBA.exe"
    assert module.default_mgba_exe(tmp_path, "posix") == tmp_path / "build-linux-qt" / "mgba-qt"


def test_portable_input_defaults_prefer_inputs_folder(tmp_path: Path) -> None:
    module = _load_module()
    inputs = tmp_path / "inputs"
    old_build = tmp_path / "build-mingw64-python-qt"
    old_rom_dir = tmp_path / "doc" / "python-examples" / "frlg-seed-bruteforce"
    inputs.mkdir()
    old_build.mkdir()
    old_rom_dir.mkdir(parents=True)
    (inputs / "lg.gba").write_bytes(b"rom")
    (inputs / "secondhalf.csv").write_text("seed\n", encoding="utf-8")
    (old_build / "secondhalf.csv").write_text("old\n", encoding="utf-8")
    (old_rom_dir / "lg.gba").write_bytes(b"old-rom")

    assert module.default_rom(tmp_path) == inputs / "lg.gba"
    assert module.default_secondhalf_csv(tmp_path) == inputs / "secondhalf.csv"


def test_cli_runtime_path_prepends_only_existing_directories(tmp_path: Path) -> None:
    module = _load_module()
    runtime_a = tmp_path / "mingw64" / "bin"
    runtime_b = tmp_path / "usr" / "bin"
    runtime_a.mkdir(parents=True)
    runtime_b.mkdir(parents=True)
    env = {"PATH": os.pathsep.join(["old-a", "old-b"])}

    module.prepend_existing_path_entries(
        env,
        [
            tmp_path / "missing",
            runtime_a,
            runtime_b,
        ],
    )

    assert env["PATH"].split(os.pathsep) == [str(runtime_a), str(runtime_b), "old-a", "old-b"]


def test_cli_runtime_path_entries_are_platform_specific(tmp_path: Path) -> None:
    module = _load_module()
    cli_path = tmp_path / "build-linux-spinda-cli" / "mgba-spinda-phase3"

    assert module.cli_runtime_path_entries(cli_path, "posix") == [cli_path.parent.absolute()]
    windows_entries = module.cli_runtime_path_entries(cli_path, "nt")
    assert windows_entries[0] == cli_path.parent.absolute()
    assert module.WINDOWS_RUNTIME_PATHS[0] in windows_entries


def test_linux_helper_scripts_keep_qt_out_of_helper_path() -> None:
    build_script = REPO_ROOT / "tools" / "spinda" / "build_phase3_cli_linux.sh"
    helper_script = REPO_ROOT / "tools" / "spinda" / "run_phase3_ledger_helper.sh"

    build_source = build_script.read_text(encoding="utf-8")
    helper_source = helper_script.read_text(encoding="utf-8")

    assert "-DBUILD_QT=OFF" in build_source
    assert "-DBUILD_SPINDA_PHASE3_CLI=ON" in build_source
    assert "mgba-spinda-phase3" in helper_source
    assert "phase3_ledger_worker_client.py" in helper_source
    assert "--runner cli" in helper_source
    assert "--output-dir \"$OUTPUT_DIR\"" in helper_source
    assert helper_source.count("--output-dir \"$OUTPUT_DIR\"") == 1
    assert "mGBA.exe" not in helper_source
    assert "mgba-qt" not in helper_source


def test_linux_build_script_keeps_frontend_and_scripting_disabled() -> None:
    build_script = REPO_ROOT / "tools" / "spinda" / "build_phase3_cli_linux.sh"
    build_source = build_script.read_text(encoding="utf-8")

    for disabled_flag in (
        "-DBUILD_QT=OFF",
        "-DBUILD_SDL=OFF",
        "-DBUILD_PYTHON=OFF",
        "-DENABLE_SCRIPTING=OFF",
        "-DUSE_LUA=OFF",
        "-DM_CORE_GB=OFF",
    ):
        assert disabled_flag in build_source
    assert "-DM_CORE_GBA=ON" in build_source
    assert "build-linux-spinda-cli" in build_source


def test_linux_helper_documentation_is_registered() -> None:
    docs_root = REPO_ROOT / "markdown-files"
    if not docs_root.is_dir():
        docs_root = REPO_ROOT / "docs"
    doc_index = (docs_root / "SPINDA_PROJECT_DOC_INDEX.md").read_text(encoding="utf-8")
    mirror_manifest = (docs_root / "MARKDOWN_MIRROR_MANIFEST.md").read_text(encoding="utf-8")
    script_inventory = (docs_root / "python_lua_scrips.md").read_text(encoding="utf-8")

    assert "PHASE3_LINUX_HELPER_NODE.md" in doc_index
    assert "PHASE3_LINUX_HELPER_NODE.md" in mirror_manifest
    assert "build_phase3_cli_linux.sh" in script_inventory
    assert "run_phase3_ledger_helper.sh" in script_inventory


def test_worker_pool_keeps_zipfile_import_out_of_production_startup() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")
    before_deep_validator = source.split("def is_complete_phase3_zip", 1)[0]
    deep_validator = source.split("def is_complete_phase3_zip", 1)[1].split("\ndef is_named_phase3_zip", 1)[0]

    assert "import zipfile" not in before_deep_validator
    assert "import zipfile" in deep_validator


def test_build_job_defaults_to_standalone_cli_runner(tmp_path: Path) -> None:
    module = _load_module()
    phase2_dir = tmp_path / "Phase2PickupStates"
    phase2_dir.mkdir()
    (phase2_dir / "0x0002.ss0").write_bytes(b"state")
    output_dir = tmp_path / "Phase3SpindaBlocks"
    cache_dir = tmp_path / "shared-native-cache"

    args = module.build_parser().parse_args(
        [
            "--lanes",
            "0x0002",
            "--phase3-cli-exe",
            str(tmp_path / "mgba-spinda-phase3.exe"),
            "--rom",
            str(tmp_path / "lg.gba"),
            "--phase2-dir",
            str(phase2_dir),
            "--secondhalf-csv",
            str(tmp_path / "secondhalf.csv"),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--limit",
            "16",
            "--overwrite",
            "--headless",
        ]
    )

    job = module.build_job(args, 0x0002)

    assert job.lane_hex == "0x0002"
    assert job.runner == "cli"
    assert job.worker_name == "spinda-phase3-0x0002"
    assert job.command[:5] == [
        str(tmp_path / "mgba-spinda-phase3.exe"),
        "--rom",
        str(tmp_path / "lg.gba"),
        "--secondhalf-csv",
        str(tmp_path / "secondhalf.csv"),
    ]
    lane_arg = job.command.index("--lane")
    state_arg = job.command.index("--phase2-state")
    assert job.command[lane_arg:lane_arg + 2] == ["--lane", "0x0002"]
    assert job.command[state_arg:state_arg + 2] == ["--phase2-state", str(phase2_dir / "0x0002.ss0")]
    assert "--limit" in job.command
    assert "--overwrite" in job.command
    assert "--cache-dir" in job.command
    assert str(cache_dir) in job.command
    assert "--learn-pickup-delay-samples" in job.command
    assert job.env["MGBA_WORKER_INSTANCE"] == "spinda-phase3-0x0002"
    assert "MGBA_SPINDA_NATIVE_PHASE3_AUTORUN" not in job.env
    assert "MGBA_SPINDA_NATIVE_PHASE3_HEADLESS" not in job.env
    assert job.cache_dir == cache_dir
    assert job.status_path == output_dir / "_0x0002.phase3_status.json"
    assert job.output_zip == output_dir / "0x0002.spinda80.zip"


def test_build_job_can_still_use_qt_autorun_for_inspection(tmp_path: Path) -> None:
    module = _load_module()
    phase2_dir = tmp_path / "Phase2PickupStates"
    phase2_dir.mkdir()
    (phase2_dir / "0x0002.ss0").write_bytes(b"state")
    output_dir = tmp_path / "Phase3SpindaBlocks"
    cache_dir = tmp_path / "shared-native-cache"

    args = module.build_parser().parse_args(
        [
            "--runner",
            "qt",
            "--lanes",
            "0x0002",
            "--mgba-exe",
            str(tmp_path / "mGBA.exe"),
            "--rom",
            str(tmp_path / "lg.gba"),
            "--phase2-dir",
            str(phase2_dir),
            "--secondhalf-csv",
            str(tmp_path / "secondhalf.csv"),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--limit",
            "16",
            "--overwrite",
            "--headless",
        ]
    )

    job = module.build_job(args, 0x0002)

    assert job.runner == "qt"
    assert job.command == [str(tmp_path / "mGBA.exe"), str(tmp_path / "lg.gba")]
    assert job.env["MGBA_WORKER_INSTANCE"] == "spinda-phase3-0x0002"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_AUTORUN"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_EXIT_ON_COMPLETE"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LANE_ID"] == "0x0002"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LIMIT"] == "16"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_CACHE_DIR"] == str(cache_dir)
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_ENABLE_AUDIO_KILLSWITCH"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_ENABLE_NO_RENDER"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_ENABLE_FAST_FORWARD"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_HEADLESS"] == "1"
    assert job.cache_dir == cache_dir
    assert job.status_path == output_dir / "_0x0002.phase3_status.json"
    assert job.output_zip == output_dir / "0x0002.spinda80.zip"


def _write_test_phase3_zip(path: Path, lane_id: int, *, count: int = 2, record_size: int = 80) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for upper in range(count):
            pid = (upper << 16) | lane_id
            archive.writestr(f"0x{pid:08X}.pk3", bytes(record_size))


def test_complete_zip_validator_requires_pid_named_80_byte_entries(tmp_path: Path) -> None:
    module = _load_module()
    valid_zip = tmp_path / "0x00AB.spinda80.zip"
    bad_size_zip = tmp_path / "0x00AC.spinda80.zip"
    wrong_lane_zip = tmp_path / "0x00AD.spinda80.zip"

    _write_test_phase3_zip(valid_zip, 0x00AB)
    _write_test_phase3_zip(bad_size_zip, 0x00AC, record_size=79)
    _write_test_phase3_zip(wrong_lane_zip, 0x00AE)

    assert module.is_complete_phase3_zip(valid_zip, 0x00AB, expected_count=2)
    assert not module.is_complete_phase3_zip(bad_size_zip, 0x00AC, expected_count=2)
    assert not module.is_complete_phase3_zip(wrong_lane_zip, 0x00AD, expected_count=2)
    assert not module.is_complete_phase3_zip(valid_zip, 0x00AB, expected_count=65536)


def test_split_existing_complete_lanes_skips_only_valid_zip(tmp_path: Path) -> None:
    module = _load_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    _write_test_phase3_zip(output_dir / "0x0001.spinda80.zip", 0x0001)
    _write_test_phase3_zip(output_dir / "0x0002.spinda80.zip", 0x0002, record_size=79)

    pending, skipped = module.split_existing_complete_lanes(
        [0x0001, 0x0002, 0x0003],
        output_dir=output_dir,
        expected_count=2,
    )

    assert skipped == [0x0001]
    assert pending == [0x0002, 0x0003]


def test_split_existing_by_name_skips_without_opening_zip_entries(tmp_path: Path) -> None:
    module = _load_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    # This is not a valid ZIP. Filename-only resume is deliberately operator-fast
    # and trusts the completed-lane filename instead of the central directory.
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"already audited elsewhere")
    (output_dir / "0x0002.wrong-name.zip").write_bytes(b"wrong")

    pending, skipped = module.split_existing_complete_lanes(
        [0x0001, 0x0002, 0x0003],
        output_dir=output_dir,
        by_name_only=True,
    )

    assert skipped == [0x0001]
    assert pending == [0x0002, 0x0003]


def test_main_defaults_to_filename_only_resume_skip(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    # Invalid ZIP bytes still skip because production startup trusts final
    # filenames and leaves deep archive validation to post-run auditors.
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"not a zip")

    assert module.main(
        [
            "--lanes",
            "0x0001-0x0002",
            "--output-dir",
            str(output_dir),
            "--allow-missing-inputs",
            "--overwrite",
            "--dry-run",
            "--dry-run-preview",
            "8",
        ]
    ) == 0

    payload = capsys.readouterr().out
    assert '"skipped_existing_complete": [\n    "0x0001"\n  ]' in payload
    assert '"pending_lane_count": 1' in payload
    assert '"lane_id": "0x0002"' in payload


def test_build_job_can_bundle_lanes_inside_one_qt_worker_process(tmp_path: Path) -> None:
    module = _load_module()
    phase2_dir = tmp_path / "Phase2PickupStates"
    phase2_dir.mkdir()
    (phase2_dir / "0x0002.ss0").write_bytes(b"state")
    (phase2_dir / "0x0003.ss0").write_bytes(b"state")
    output_dir = tmp_path / "Phase3SpindaBlocks"
    cache_dir = tmp_path / "shared-native-cache"

    args = module.build_parser().parse_args(
        [
            "--runner",
            "qt",
            "--lanes",
            "0x0002-0x0003",
            "--bundle-size",
            "2",
            "--mgba-exe",
            str(tmp_path / "mGBA.exe"),
            "--rom",
            str(tmp_path / "lg.gba"),
            "--phase2-dir",
            str(phase2_dir),
            "--secondhalf-csv",
            str(tmp_path / "secondhalf.csv"),
            "--output-dir",
            str(output_dir),
            "--cache-dir",
            str(cache_dir),
            "--overwrite",
            "--headless",
        ]
    )

    job = module.build_job(args, 0x0002, [0x0002, 0x0003])

    assert job.lane_hex == "0x0002..0x0003"
    assert job.lane_ids == [0x0002, 0x0003]
    assert job.phase2_states == [phase2_dir / "0x0002.ss0", phase2_dir / "0x0003.ss0"]
    assert job.output_zips == [output_dir / "0x0002.spinda80.zip", output_dir / "0x0003.spinda80.zip"]
    assert job.status_paths == [output_dir / "_0x0002.phase3_status.json", output_dir / "_0x0003.phase3_status.json"]
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LANE_ID"] == "0x0002"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LANE_IDS"] == "0x0002,0x0003"
    assert "MGBA_SPINDA_NATIVE_PHASE3_PHASE2_STATE" not in job.env
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_MIN_PICKUP_DETECT_FRAME"] == "4"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECKS"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECK_FIRST_FRAME"] == "4"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_FAST_PICKUP_CHECK_SECOND_FRAME"] == "5"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LEARN_PICKUP_DELAY"] == "1"
    assert job.env["MGBA_SPINDA_NATIVE_PHASE3_LEARN_PICKUP_DELAY_SAMPLES"] == "32"


def test_build_job_can_bundle_lanes_inside_one_cli_worker_process(tmp_path: Path) -> None:
    module = _load_module()
    phase2_dir = tmp_path / "Phase2PickupStates"
    phase2_dir.mkdir()
    (phase2_dir / "0x0002.ss0").write_bytes(b"state")
    (phase2_dir / "0x0003.ss0").write_bytes(b"state")
    output_dir = tmp_path / "Phase3SpindaBlocks"

    args = module.build_parser().parse_args(
        [
            "--lanes",
            "0x0002-0x0003",
            "--bundle-size",
            "2",
            "--phase3-cli-exe",
            str(tmp_path / "mgba-spinda-phase3.exe"),
            "--rom",
            str(tmp_path / "lg.gba"),
            "--phase2-dir",
            str(phase2_dir),
            "--secondhalf-csv",
            str(tmp_path / "secondhalf.csv"),
            "--output-dir",
            str(output_dir),
            "--overwrite",
        ]
    )

    job = module.build_job(args, 0x0002, [0x0002, 0x0003])

    assert job.runner == "cli"
    assert "--lanes" in job.command
    assert "0x0002,0x0003" in job.command
    assert "--phase2-dir" in job.command
    assert str(phase2_dir) in job.command
    assert "--phase2-state" not in job.command
    assert job.status_paths == [output_dir / "_0x0002.phase3_status.json", output_dir / "_0x0003.phase3_status.json"]


def test_bundle_job_summary_aggregates_lane_statuses(tmp_path: Path) -> None:
    module = _load_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    first_zip = output_dir / "0x0002.spinda80.zip"
    second_zip = output_dir / "0x0003.spinda80.zip"
    first_zip.write_bytes(b"zip")
    (output_dir / "_0x0002.phase3_status.json").write_text(
        module.json.dumps(
            {
                "status": "complete",
                "generated_records": 65536,
                "selected_targets": 65536,
                "elapsed_seconds": 10.5,
            }
        ),
        encoding="utf-8",
    )
    (output_dir / "_0x0003.phase3_status.json").write_text(
        module.json.dumps(
            {
                "status": "failed",
                "generated_records": 123,
                "selected_targets": 65536,
                "elapsed_seconds": 2.25,
            }
        ),
        encoding="utf-8",
    )
    job = module.NativePhase3Job(
        lane_id=0x0002,
        lane_ids=[0x0002, 0x0003],
        lane_hex="0x0002..0x0003",
        command=["dummy"],
        env={},
        phase2_state=tmp_path / "0x0002.ss0",
        phase2_states=[tmp_path / "0x0002.ss0", tmp_path / "0x0003.ss0"],
        cache_dir=tmp_path,
        output_zip=first_zip,
        output_zips=[first_zip, second_zip],
        status_path=output_dir / "_0x0002.phase3_status.json",
        status_paths=[output_dir / "_0x0002.phase3_status.json", output_dir / "_0x0003.phase3_status.json"],
        worker_name="spinda-phase3-0x0002",
        keep_open=False,
        runner="cli",
    )

    summary = module.summarize_job(job)

    assert summary["status"] == "failed"
    assert summary["generated_records"] == 65536 + 123
    assert summary["selected_targets"] == 65536 * 2
    assert summary["elapsed_seconds"] == 12.75
    assert summary["complete_lanes"] == 1
    assert summary["lane_statuses"][0]["status"] == "complete"
    assert summary["lane_statuses"][1]["status"] == "failed"


def test_worker_pool_direct_default_bundle_size_matches_production_launcher() -> None:
    module = _load_module()

    args = module.build_parser().parse_args(
        ["--lanes", "0x0001-0x0004", "--allow-missing-inputs", "--dry-run"]
    )

    assert module.DEFAULT_BUNDLE_SIZE == 2
    assert args.bundle_size == 2


def test_dry_run_preview_limits_full_range_output(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    module = _load_module()

    assert module.main(
        [
            "--lanes",
            "0x0001-0x0004",
            "--workers",
            "2",
            "--bundle-size",
            "2",
            "--output-dir",
            str(tmp_path / "Phase3SpindaBlocks"),
            "--allow-missing-inputs",
            "--overwrite",
            "--dry-run",
            "--dry-run-preview",
            "1",
        ]
    ) == 0

    payload = capsys.readouterr().out
    assert '"job_count": 2' in payload
    assert '"dry_run_preview": 1' in payload
    assert '"pending_lane_count": 4' in payload
    assert '"lane_id": "0x0001"' in payload
    assert '"runner": "cli"' in payload
    assert '"--lanes",' in payload
    assert '"0x0001,0x0002"' in payload
    assert "--phase2-dir" in payload


def test_headless_worker_rejects_keep_open() -> None:
    module = _load_module()

    with pytest.raises(SystemExit):
        module.main(["--lanes", "0x0002", "--headless", "--keep-open", "--allow-missing-inputs"])


def test_worker_pool_status_limits_done_and_skipped_lists(tmp_path: Path) -> None:
    module = _load_module()
    status_path = tmp_path / "pool.json"
    done = [{"lane_id": f"0x{lane:04X}", "status": "complete"} for lane in range(module.DONE_STATUS_PREVIEW + 2)]
    skipped = [f"0x{lane:04X}" for lane in range(module.SKIPPED_STATUS_PREVIEW + 3)]
    pending_bundles = deque([[0x0100 + lane] for lane in range(module.PENDING_STATUS_PREVIEW + 4)])

    module.write_lazy_bundle_pool_status(
        status_path,
        pending_bundles,
        len(pending_bundles),
        running=[],
        done=done,
        skipped=skipped,
    )

    payload = module.json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["counts"]["done"] == module.DONE_STATUS_PREVIEW + 2
    assert payload["counts"]["skipped_existing_complete"] == module.SKIPPED_STATUS_PREVIEW + 3
    assert payload["counts"]["pending"] == module.PENDING_STATUS_PREVIEW + 4
    assert len(payload["done"]) == module.DONE_STATUS_PREVIEW
    assert payload["done_omitted"] == 2
    assert payload["done"][0]["lane_id"] == "0x0002"
    assert len(payload["skipped_existing_complete"]) == module.SKIPPED_STATUS_PREVIEW
    assert payload["skipped_existing_complete_omitted"] == 3
    assert len(payload["pending"]) == module.PENDING_STATUS_PREVIEW
    assert payload["pending_omitted"] == 4
    assert "preview-limited" in payload["status_note"]
    assert payload["counts"]["completed_lanes"] == module.DONE_STATUS_PREVIEW + 2


def test_worker_pool_status_write_interval_is_configurable() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "--status-write-seconds" in source
    assert "status_dirty" in source
    assert "next_status_write" in source


def test_worker_pool_control_file_and_slot_timers(tmp_path: Path) -> None:
    module = _load_module()
    control_path = tmp_path / "control.json"
    control_path.write_text(
        module.json.dumps({"desired_workers": 5, "shutdown": False}),
        encoding="utf-8",
    )

    control = module.read_pool_control(control_path, default_workers=2, max_workers=8)

    assert control["desired_workers"] == 5
    assert control["shutdown"] is False

    job = module.NativePhase3Job(
        lane_id=1,
        lane_ids=[1],
        lane_hex="0x0001",
        command=["dummy"],
        env={},
        phase2_state=tmp_path / "0x0001.ss0",
        phase2_states=[tmp_path / "0x0001.ss0"],
        cache_dir=tmp_path,
        output_zip=tmp_path / "0x0001.spinda80.zip",
        output_zips=[tmp_path / "0x0001.spinda80.zip"],
        status_path=tmp_path / "_0x0001.phase3_status.json",
        status_paths=[tmp_path / "_0x0001.phase3_status.json"],
        worker_name="spinda-phase3-0x0001",
        keep_open=False,
        runner="cli",
    )
    summary = module.summarize_job(job, slot_id=3, launched_at=module.time.monotonic() - 12.5)

    assert summary["slot_id"] == 3
    assert summary["current_outer_elapsed_seconds"] >= 12


def test_benchmark_worker_counts_script_uses_existing_worker_pool() -> None:
    source = BENCHMARK_SCRIPT_PATH.read_text(encoding="utf-8")

    assert "native_phase3_worker_pool.py" in source
    assert "--worker-counts" in source
    assert "--zip-method" in source
    assert "--learn-pickup-delay-samples" in source
    assert "--reuse-output" in source
    assert "old lane ZIPs would be skipped" in source
    assert "_worker_count_benchmark_summary.json" in source
    assert "lanes_per_hour" in source


def test_batch_launcher_defaults_to_small_cli_bundles_and_status_throttle() -> None:
    batch = (REPO_ROOT / "tools" / "spinda" / "run_phase3_remaining_workers.bat").read_text(encoding="utf-8")

    assert 'set "LANES=0x0000-0xFFFF"' in batch
    assert 'set "BUNDLE_SIZE=2"' in batch
    assert "--zip-method !ZIP_METHOD!" in batch
    assert "--status-write-seconds !STATUS_WRITE_SECONDS!" in batch


def test_benchmark_worker_counts_refuses_stale_output_dirs(tmp_path: Path) -> None:
    module = _load_benchmark_module()
    output_root = tmp_path / "bench"
    stale_dir = output_root / "workers_1"
    stale_dir.mkdir(parents=True)
    (stale_dir / "0x0001.spinda80.zip").write_bytes(b"old")

    with pytest.raises(ValueError, match="pass --reuse-output"):
        module.validate_output_dirs(output_root, [1], reuse_output=False)

    module.validate_output_dirs(output_root, [1], reuse_output=True)


def test_benchmark_worker_counts_reads_exact_lane_status_files(tmp_path: Path) -> None:
    module = _load_benchmark_module()
    output_dir = tmp_path / "workers_1"
    output_dir.mkdir()
    for lane in range(300):
        status_path = output_dir / f"_0x{lane:04X}.phase3_status.json"
        status_path.write_text(
            module.json.dumps({"status": "complete", "generated_records": 2}),
            encoding="utf-8",
        )
    (output_dir / "_0xFFFF.phase3_status.json").write_text(
        module.json.dumps({"status": "failed", "generated_records": 99}),
        encoding="utf-8",
    )

    complete_lanes, generated_records = module.summarize_lane_status_files(output_dir)

    assert complete_lanes > 256
    assert generated_records == complete_lanes * 2
