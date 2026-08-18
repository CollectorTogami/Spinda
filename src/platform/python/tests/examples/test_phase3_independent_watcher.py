from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
WATCHER_PATH = REPO_ROOT / "tools" / "spinda" / "phase3_independent_watcher.py"


def _load_watcher_module():
    module_name = "testable_phase3_independent_watcher"
    spec = importlib.util.spec_from_file_location(module_name, WATCHER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase3_watcher_default_root_is_current_workspace() -> None:
    watcher = _load_watcher_module()

    assert watcher.ROOT == REPO_ROOT
    assert watcher.DEFAULT_OUTPUT_DIR == REPO_ROOT / "Phase3SpindaBlocks"


def test_phase3_watcher_scans_output_folder_without_opening_zips(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"zip-data")
    (output_dir / "0x0002.spinda80.zip").write_bytes(b"")
    (output_dir / "0x0003.spinda80.zip.pid123.tmp").write_bytes(b"partial")
    (output_dir / "0x0004.bad.spinda80.zip").write_bytes(b"bad")

    result = watcher.scan_phase3_outputs(
        output_dir,
        target_lanes=4,
        tiny_zip_bytes=4,
        stale_tmp_seconds=0,
        now=time.time() + 1.0,
    )

    assert result["complete_lanes"] == 1
    assert result["zip_files"] == 2
    assert result["zero_size_zips"] == 1
    assert result["tmp_files"] == 1
    assert result["stale_tmp_files"] == 1
    assert result["bad_names"] == 1
    assert result["last_good_lane"] == "0x0001"
    assert "stale_tmp_files" in result["samples"]
    assert "bad_names" in result["samples"]


def test_phase3_watcher_compares_pool_pids_command_center_and_processes(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"zip-data")
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": 900,
                "running": [{"slot_id": 1, "pid": 111, "lane_id": "0x0002"}],
                "counts": {"running": 1, "failed_jobs": 0},
            }
        ),
        encoding="utf-8",
    )

    payload = watcher.build_watcher_payload(
        output_dir=output_dir,
        pool_status_path=pool_status,
        command_center_snapshot={
            "enabled": True,
            "reachable": True,
            "payload": {"progress": {"complete_lanes": 2}},
        },
        process_snapshot={
            "pids": [],
            "phase3_workers": [],
            "worker_pools": [],
            "command_centers": [],
            "rows": [],
            "source": "test",
        },
        now=1000.0,
        no_zip_warning_seconds=10_000,
        no_zip_critical_seconds=20_000,
        disk_warning_bytes=0,
        disk_critical_bytes=0,
        tiny_zip_bytes=4,
    )

    codes = {check["code"] for check in payload["checks"]}
    assert payload["status"] == "critical"
    assert "reported_worker_pid_missing" in codes
    assert "command_center_folder_mismatch" in codes
    assert payload["summary"]["complete_lanes"] == 1
    assert payload["summary"]["running_workers_reported"] == 1
    assert payload["summary"]["phase3_worker_processes"] == 0


def test_phase3_watcher_compares_folder_to_local_progress_not_grand_total(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"zip-data")

    payload = watcher.build_watcher_payload(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        command_center_snapshot={
            "enabled": True,
            "reachable": True,
            "payload": {
                "progress": {
                    "local_complete_lanes": 1,
                    "complete_lanes": 100,
                    "trusted_remote_lanes": 99,
                }
            },
        },
        process_snapshot={
            "pids": [],
            "phase3_workers": [],
            "worker_pools": [],
            "ledger_clients": [],
            "command_centers": [],
            "rows": [],
            "source": "test",
        },
        now=1000.0,
        expected_running=False,
        disk_warning_bytes=0,
        disk_critical_bytes=0,
        tiny_zip_bytes=4,
    )

    codes = {check["code"] for check in payload["checks"]}
    assert "command_center_folder_mismatch" not in codes


def test_phase3_watcher_run_once_writes_status_and_events(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"zip-data")
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps({"time_unix": 1000, "running": [], "counts": {"running": 0}}),
        encoding="utf-8",
    )
    status_out = output_dir / "_watcher_status.json"
    events_out = output_dir / "_watcher_events.jsonl"
    args = watcher.parse_args(
        [
            "--folder",
            str(output_dir),
            "--pool-status",
            str(pool_status),
            "--status-out",
            str(status_out),
            "--events-out",
            str(events_out),
            "--no-command-center-api",
            "--allow-idle",
            "--once",
            "--disk-warning-gib",
            "0",
            "--disk-critical-gib",
            "0",
            "--no-zip-warning-seconds",
            "10000",
            "--no-zip-critical-seconds",
            "20000",
            "--tiny-zip-bytes",
            "4",
        ]
    )

    payload, signature = watcher.run_once(args)

    assert signature
    assert status_out.is_file()
    assert events_out.is_file()
    assert payload["summary"]["complete_lanes"] == 1
    assert json.loads(status_out.read_text(encoding="utf-8"))["summary"]["complete_lanes"] == 1
    assert json.loads(events_out.read_text(encoding="utf-8").splitlines()[0])["summary"]["complete_lanes"] == 1


def test_phase3_watcher_treats_shutdown_control_as_intentional_idle(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"zip-data")
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_control = output_dir / "_native_phase3_worker_pool_control.json"
    pool_control.write_text(
        json.dumps({"desired_workers": 0, "shutdown": True, "source": "test"}),
        encoding="utf-8",
    )

    payload = watcher.build_watcher_payload(
        output_dir=output_dir,
        pool_status_path=pool_status,
        command_center_snapshot={"enabled": False, "reachable": False},
        process_snapshot={
            "pids": [],
            "phase3_workers": [],
            "worker_pools": [],
            "command_centers": [],
            "rows": [],
            "source": "test",
        },
        now=1000.0,
        disk_warning_bytes=0,
        disk_critical_bytes=0,
        tiny_zip_bytes=4,
    )

    assert payload["status"] == "ok"
    assert payload["checks"] == []
    assert payload["control"]["idle_requested"] is True


def test_phase3_watcher_shutdown_control_ignores_stale_running_pool_json(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {
                "time_unix": 1000,
                "running": [{"pid": 111, "lane_id": "0x0002"}],
                "counts": {"running": 1},
            }
        ).encode("utf-8")
    )
    (output_dir / "_native_phase3_worker_pool_control.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"desired_workers": 0, "shutdown": True}).encode("utf-8")
    )

    payload = watcher.build_watcher_payload(
        output_dir=output_dir,
        pool_status_path=pool_status,
        command_center_snapshot={"enabled": False, "reachable": False},
        process_snapshot={
            "pids": [],
            "phase3_workers": [],
            "worker_pools": [],
            "ledger_clients": [],
            "command_centers": [],
            "rows": [],
            "source": "test",
        },
        now=2000.0,
        disk_warning_bytes=0,
        disk_critical_bytes=0,
    )

    codes = {check["code"] for check in payload["checks"]}
    assert "pool_status_stale" not in codes
    assert "reported_worker_pid_missing" not in codes
    assert payload["summary"]["running_workers_reported"] == 0
    assert payload["status"] == "ok"


def test_phase3_watcher_does_not_call_fresh_workers_zip_stalled(tmp_path: Path) -> None:
    watcher = _load_watcher_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    old_zip = output_dir / "0x0001.spinda80.zip"
    old_zip.write_bytes(b"zip-data")
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": 1000,
                "running": [
                    {
                        "slot_id": 1,
                        "pid": 111,
                        "lane_id": "0x0002",
                        "current_outer_elapsed_seconds": 120.0,
                    }
                ],
                "counts": {"running": 1},
            }
        ),
        encoding="utf-8",
    )

    payload = watcher.build_watcher_payload(
        output_dir=output_dir,
        pool_status_path=pool_status,
        command_center_snapshot={"enabled": False, "reachable": False},
        process_snapshot={
            "pids": [111],
            "phase3_workers": [],
            "worker_pools": [],
            "command_centers": [],
            "rows": [],
            "source": "test",
        },
        now=5000.0,
        no_zip_warning_seconds=3600.0,
        no_zip_critical_seconds=7200.0,
        disk_warning_bytes=0,
        disk_critical_bytes=0,
        tiny_zip_bytes=4,
    )

    codes = {check["code"] for check in payload["checks"]}
    assert "zip_output_stalled" not in codes
    assert payload["summary"]["oldest_running_lane_elapsed_seconds"] == 120.0


def test_phase3_watcher_runtime_cache_reuses_expensive_snapshots(monkeypatch) -> None:
    watcher = _load_watcher_module()
    calls = {"process": 0, "command_center": 0}

    def fake_processes():
        calls["process"] += 1
        return {"pids": [10], "phase3_workers": [], "worker_pools": [], "command_centers": [], "rows": []}

    def fake_command_center(url: str):
        calls["command_center"] += 1
        return {"enabled": True, "reachable": True, "url": url, "payload": {"progress": {"complete_lanes": 1}}}

    monkeypatch.setattr(watcher, "host_phase3_processes", fake_processes)
    monkeypatch.setattr(watcher, "fetch_command_center_status", fake_command_center)
    cache = watcher.WatcherRuntimeCache(
        process_check_interval_seconds=999.0,
        command_center_check_interval_seconds=999.0,
    )

    assert cache.process_snapshot()["pids"] == [10]
    assert cache.process_snapshot()["pids"] == [10]
    assert cache.command_center_snapshot("http://127.0.0.1:235/api/status", False)["reachable"] is True
    assert cache.command_center_snapshot("http://127.0.0.1:235/api/status", False)["reachable"] is True
    assert calls == {"process": 1, "command_center": 1}
