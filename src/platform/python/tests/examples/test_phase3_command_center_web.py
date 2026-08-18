from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
WEB_PATH = REPO_ROOT / "tools" / "spinda" / "phase3_command_center_web.py"
PS1_PATH = REPO_ROOT / "tools" / "spinda" / "phase3_command_center.ps1"
VALID_ZIP_BYTES = b"z" * 2048


def _load_web_module():
    module_name = "testable_phase3_command_center_web"
    spec = importlib.util.spec_from_file_location(module_name, WEB_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase3_command_center_api_reports_workers_totals_and_slot_timers(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (output_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (output_dir / "0x0003.spinda80.zip.pid123.tmp").write_bytes(b"tmp")
    running_status = output_dir / "_0x0003.phase3_status.json"
    running_status.write_text(
        json.dumps({"status": "running", "generated_records": 1234, "elapsed_seconds": 55.5}),
        encoding="utf-8",
    )
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": 1000,
                "running": [
                    {
                        "slot_id": 1,
                        "worker_name": "spinda-phase3-0x0003",
                        "pid": 123,
                        "lane_id": "0x0003",
                        "status_path": str(running_status),
                        "current_outer_elapsed_seconds": 66.6,
                    }
                ],
                "done": [
                    {
                        "slot_id": 1,
                        "lane_id": "0x0002",
                        "status": "complete",
                        "outer_elapsed_seconds": 777.7,
                        "generated_records": 65536,
                    }
                ],
                "counts": {
                    "pending": 10,
                    "running": 1,
                    "done": 1,
                    "completed_lanes": 1,
                    "failed_jobs": 0,
                    "generated_records": 65536,
                    "skipped_existing_complete": 2,
                },
            }
        ),
        encoding="utf-8",
    )
    watcher_status = output_dir / "_phase3_independent_watcher_status.json"
    watcher_status.write_text(
        json.dumps(
            {
                "generated_at_unix": 1000,
                "status": "warning",
                "summary": {
                    "check_count": 1,
                    "warning_count": 1,
                    "critical_count": 0,
                    "running_workers_reported": 1,
                    "phase3_worker_processes": 1,
                },
                "checks": [
                    {
                        "severity": "warning",
                        "code": "zip_output_stalled",
                        "message": "test warning",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    controller = web.Phase3WorkerController(
        output_dir=output_dir,
        pool_control_path=output_dir / "_native_phase3_worker_pool_control.json",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=pool_status,
        watcher_status_path=watcher_status,
        target_lanes=4,
        display_url="http://192.168.1.20:235/",
        controller=controller,
    )

    response = app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["progress"]["complete_lanes"] == 2
    assert payload["progress"]["target_lanes"] == 4
    assert payload["progress"]["completed_spindas"] == 2 * 65536
    assert payload["progress"]["target_spindas"] == 4 * 65536
    assert payload["progress"]["completed_since_pool_boot"] == 1
    assert payload["progress"]["projected_lanes_per_hour"] == round(3600 / 777.7, 3)
    assert payload["progress"]["projected_basis"] == "recent completed worker jobs"
    assert payload["workers"]["running_workers"] == 1
    assert payload["workers"]["stall_warning_count"] == 0
    assert payload["workers"]["worker_slots"][0]["current_lane"] == "0x0003"
    assert payload["workers"]["worker_slots"][0]["last_lane"] == "0x0002"
    assert payload["workers"]["worker_slots"][0]["last_iteration_seconds"] == 777.7
    assert payload["health"]["tmp_files"] == 1
    assert payload["health"]["bad_zip_artifacts"] == 1
    assert payload["health"]["last_good_lane"] == "0x0002"
    assert payload["host"]["disk"]["free_bytes"] >= 0
    assert "memory" in payload["host"]
    assert payload["validation_policy"]["pkhex_validator"]["status"] == "deferred"
    assert payload["validation_policy"]["pkhex_validator"]["ready"] is False
    assert payload["watcher"]["status"] == "warning"
    assert payload["watcher"]["summary"]["check_count"] == 1
    assert payload["watcher"]["checks"][0]["code"] == "zip_output_stalled"
    assert payload["server"]["display_url"] == "http://192.168.1.20:235/"


def test_phase3_command_center_reports_active_lane_inside_bundle(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    first_zip = output_dir / "0x0005.spinda80.zip"
    first_zip.write_bytes(VALID_ZIP_BYTES)
    first_status = output_dir / "_0x0005.phase3_status.json"
    first_status.write_text(
        json.dumps(
            {
                "status": "complete",
                "lane_id": "0x0005",
                "generated_records": 65536,
                "selected_targets": 65536,
                "timing": {
                    "frame_advance_seconds": 500.0,
                    "pickup_wait_detect_seconds": 100.0,
                    "zip_build_write_seconds": 1.5,
                },
            }
        ),
        encoding="utf-8",
    )
    second_status = output_dir / "_0x0006.phase3_status.json"
    second_status.write_text(
        json.dumps(
            {
                "status": "running",
                "lane_id": "0x0006",
                "generated_records": 0,
                "selected_targets": 65536,
            }
        ),
        encoding="utf-8",
    )
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": 1000,
                "running": [
                    {
                        "slot_id": 2,
                        "worker_name": "spinda-phase3-0x0005",
                        "pid": 456,
                        "lane_id": "0x0005..0x0006",
                        "lane_statuses": [
                            {
                                "lane_id": "0x0005",
                                "status": "complete",
                                "status_path": str(first_status),
                                "output_zip": str(first_zip),
                                "zip_exists": True,
                            },
                            {
                                "lane_id": "0x0006",
                                "status": "running",
                                "status_path": str(second_status),
                                "output_zip": str(output_dir / "0x0006.spinda80.zip"),
                                "zip_exists": False,
                            },
                        ],
                        "current_outer_elapsed_seconds": 900.0,
                    }
                ],
                "done": [],
                "counts": {"pending": 1, "running": 1, "done": 0},
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(output_dir=output_dir, pool_status_path=pool_status, target_lanes=8)

    response = app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    slot = payload["workers"]["worker_slots"][0]
    assert slot["bundle_lane"] == "0x0005..0x0006"
    assert slot["current_lane"] == "0x0006"
    assert slot["current_elapsed_seconds"] == 298.5
    assert slot["last_lane"] == "0x0005"
    assert slot["last_iteration_seconds"] == 601.5


def test_phase3_command_center_reports_slow_running_lane_warning(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    running_status = output_dir / "_0x0007.phase3_status.json"
    running_status.write_text(
        json.dumps(
            {
                "status": "running",
                "lane_id": "0x0007",
                "generated_records": 123,
                "elapsed_seconds": 6000.0,
            }
        ),
        encoding="utf-8",
    )
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": int(time.time()),
                "running": [
                    {
                        "slot_id": 3,
                        "pid": 789,
                        "lane_id": "0x0007",
                        "status_path": str(running_status),
                    }
                ],
                "done": [
                    {
                        "slot_id": 3,
                        "lane_id": "0x0006",
                        "status": "complete",
                        "outer_elapsed_seconds": 1000.0,
                    }
                ],
                "counts": {"pending": 1, "running": 1, "done": 0},
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(output_dir=output_dir, pool_status_path=pool_status, target_lanes=8)

    response = app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["workers"]["stall_warning_count"] == 1
    warning = payload["workers"]["stall_warnings"][0]
    assert warning["slot_id"] == 3
    assert warning["lane"] == "0x0007"
    assert "slowdown threshold" in warning["reason"]


def test_phase3_command_center_html_contains_controls_and_visible_total() -> None:
    web = _load_web_module()
    app = web.create_app()

    response = app.test_client().get("/")
    text = response.get_data(as_text=True)

    assert response.status_code == 200
    assert "/ 65536" in text
    assert "Exact Spindas generated" in text
    assert "done lanes x 65,536" in text
    assert "Apply / launch workers" in text
    assert "Killswitch: stop all workers" in text
    assert "Force kill running workers" in text
    assert "Independent watcher" in text
    assert "Last timer" in text
    assert "ZIP scan age" in text
    assert "Stall warnings" in text
    assert "ETA confidence" in text
    assert "Projected finish" in text
    assert "Validation policy" in text
    assert "PKHeX final audit deferred" in text
    assert "Multi-device coordination" in text
    assert "Registered subordinate panels" in text
    assert "Save network settings" in text
    assert "Dark mode" in text
    assert "theme-toggle" in text
    assert "Primary HTTP/HTTPS" in text
    assert "Advertise HTTP/HTTPS" in text
    assert "Trusted ledger/remote lanes" in text
    assert "Grand counter source" in text
    assert "Combined running workers" in text
    assert "Local running workers" in text
    assert "Remote running workers" in text
    assert "Grand total trusts lane ranges from local ZIPs" in text
    assert 'id="coord-primary-host-input"' in text
    assert "Lane ledger / claims" in text
    assert "Reconcile finished ZIPs into ledger" in text
    assert 'id="ledger-active-list"' in text
    assert "Disk free" in text
    assert "Worker warnings" in text
    assert 'id="samples-list"' in text
    assert 'id="recent-done-body"' in text
    assert '<pre id="details">' not in text


def test_phase3_command_center_api_reports_coordination_state(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        coordination=web.CoordinationConfig(
            role="subordinate",
            online=False,
            primary_host="192.168.1.10",
            primary_port=235,
            advertise_host="192.168.1.21",
            advertise_port=236,
            device_id="bench-two",
        ),
    )

    response = app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["coordination"]["role"] == "subordinate"
    assert payload["coordination"]["online"] is False
    assert payload["coordination"]["device_id"] == "bench-two"
    assert payload["coordination"]["primary_url"] == "http://192.168.1.10:235"
    assert payload["coordination"]["advertise_url"] == "http://192.168.1.21:236"


def test_phase3_command_center_coordinator_accepts_subordinate_heartbeat(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        coordination=web.CoordinationConfig(
            role="coordinator",
            online=True,
            primary_host="192.168.1.10",
            primary_port=235,
            advertise_host="192.168.1.10",
            advertise_port=235,
        ),
    )

    response = app.test_client().post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "bench-two",
            "advertise_url": "http://192.168.1.21:236",
            "progress": {"complete_lanes": 12},
            "workers": {"running_workers": 4},
        },
    )
    state = app.test_client().get("/api/coordination/state").get_json()

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert state["registered_device_count"] == 1
    assert state["registered_devices"][0]["device_id"] == "bench-two"
    assert state["registered_devices"][0]["progress"]["complete_lanes"] == 12


def test_phase3_command_center_grand_total_trusts_subordinate_ledger(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    heartbeat = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "helper-a",
            "advertise_url": "https://helper-a.local:235",
            "progress": {"complete_lanes": 3},
            "ledger": {"counts": {"done": 5}},
            "workers": {"running_workers": 2},
        },
    )
    payload = client.get("/api/status").get_json()

    assert heartbeat.status_code == 200
    assert payload["progress"]["local_complete_lanes"] == 1
    assert payload["progress"]["trusted_remote_lanes"] == 0
    assert payload["progress"]["complete_lanes"] == 1
    assert payload["progress"]["completed_spindas"] == 1 * 65536
    assert payload["progress"]["legacy_remote_count_without_ranges"] == 5
    assert payload["progress"]["trusted_remote_devices"][0]["source"] == "legacy_ledger_count"
    assert payload["coordination"]["registered_devices"][0]["ledger"]["counts"]["done"] == 5


def test_phase3_command_center_grand_total_dedupes_subordinate_done_ranges(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (output_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    response = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "helper-a",
            "progress": {"complete_lanes": 4},
            "ledger": {"counts": {"done": 4}, "done_ranges": ["0x0002-0x0005"]},
            "workers": {"running_workers": 3, "active_lane_ranges": ["0x0006-0x0007"]},
        },
    )
    payload = client.get("/api/status").get_json()
    ledger = client.get("/api/ledger/status").get_json()

    assert response.status_code == 200
    assert response.get_json()["ledger_import"]["remote_done_imported"] == 3
    assert response.get_json()["ledger_import"]["remote_active_imported"] == 2
    assert payload["progress"]["local_complete_lanes"] == 2
    assert payload["progress"]["trusted_remote_lanes"] == 3
    assert payload["progress"]["complete_lanes"] == 5
    assert payload["workers"]["combined_running_workers"] == 3
    assert payload["workers"]["remote_running_workers"] == 3
    assert ledger["counts"]["done"] == 4
    assert ledger["counts"]["running"] == 2


def test_phase3_command_center_ignores_stale_ledger_client_sidecar_in_local_status(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_client_status = output_dir / "_phase3_ledger_worker_client_status.json"
    ledger_client_status.write_text(
        json.dumps(
            {
                "status": "running_batch",
                "workers": 12,
                "active_lane_ranges": ["0x0008-0x0009"],
                "updated_at_unix": time.time() - 3600,
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        ledger_client_status_path=ledger_client_status,
        coordination=web.CoordinationConfig(role="subordinate", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )

    payload = app.test_client().get("/api/status").get_json()

    assert payload["ledger_client"]["exists"] is True
    assert payload["ledger_client"]["age_seconds"] >= 300
    assert payload["workers"]["local_running_workers"] == 0
    assert payload["workers"]["active_lane_ranges"] == []


def test_phase3_subordinate_heartbeat_ignores_stale_ledger_client_sidecar(tmp_path: Path, monkeypatch) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_client_status = output_dir / "_phase3_ledger_worker_client_status.json"
    ledger_client_status.write_text(
        json.dumps(
            {
                "status": "running_batch",
                "workers": 12,
                "active_lane_ranges": ["0x0008-0x0009"],
                "updated_at_unix": time.time() - 3600,
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    class FakePayloadCache:
        def get(self):
            return {"progress": {}, "health": {}, "workers": {"running_workers": 0, "pending_lanes": 0}}

    class FakeController:
        def state(self):
            return {}

    monkeypatch.setattr(web.urlrequest, "urlopen", fake_urlopen)
    client = web.SubordinateHeartbeatClient(
        config=web.CoordinationConfig(
            role="subordinate",
            online=True,
            primary_host="coordinator.local",
            primary_port=235,
            heartbeat_seconds=60,
        ),
        payload_cache=FakePayloadCache(),
        controller=FakeController(),
        ledger_client_status_path=ledger_client_status,
    )

    result = client.send_once()
    body = captured["payload"]

    assert result == {"ok": True, "status": 200}
    assert body["workers"]["running_workers"] == 0
    assert body["workers"]["active_lane_ranges"] == []
    assert body["workers"]["pool_active_lane_ranges"] == []
    assert body["workers"]["ledger_client_active_lane_ranges"] == []
    assert body["ledger_client"]["age_seconds"] >= 300


def test_phase3_subordinate_heartbeat_keeps_pool_ranges_when_ledger_client_stale(tmp_path: Path, monkeypatch) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_client_status = output_dir / "_phase3_ledger_worker_client_status.json"
    ledger_client_status.write_text(
        json.dumps(
            {
                "status": "running_batch",
                "workers": 12,
                "active_lane_ranges": ["0x0008-0x0009"],
                "updated_at_unix": time.time() - 3600,
            }
        ),
        encoding="utf-8",
    )
    captured: dict[str, object] = {}

    class FakeResponse:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, traceback):
            return False

    def fake_urlopen(request, timeout):
        captured["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    class FakePayloadCache:
        def get(self):
            return {
                "progress": {},
                "health": {},
                "workers": {
                    "running_workers": 2,
                    "pending_lanes": 0,
                    "running": [{"bundle_lane_id": "0x0008-0x0009"}],
                },
            }

    class FakeController:
        def state(self):
            return {}

    monkeypatch.setattr(web.urlrequest, "urlopen", fake_urlopen)
    client = web.SubordinateHeartbeatClient(
        config=web.CoordinationConfig(
            role="subordinate",
            online=True,
            primary_host="coordinator.local",
            primary_port=235,
            heartbeat_seconds=60,
        ),
        payload_cache=FakePayloadCache(),
        controller=FakeController(),
        ledger_client_status_path=ledger_client_status,
    )

    result = client.send_once()
    body = captured["payload"]

    assert result == {"ok": True, "status": 200}
    assert body["workers"]["running_workers"] == 2
    assert body["workers"]["pool_running_workers"] == 2
    assert body["workers"]["ledger_client_running_workers"] == 0
    assert body["workers"]["active_lane_ranges"] == ["0x0008-0x0009"]
    assert body["workers"]["pool_active_lane_ranges"] == ["0x0008-0x0009"]
    assert body["workers"]["ledger_client_active_lane_ranges"] == []
    assert body["ledger_client"]["age_seconds"] >= 300


def test_phase3_command_center_imports_health_ranges_as_done_backup(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    response = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "helper-a",
            "health": {"complete_lane_ranges": ["0x0003-0x0004"]},
            "workers": {"running_workers": 0},
        },
    )
    payload = client.get("/api/status").get_json()
    ledger = client.get("/api/ledger/status").get_json()

    assert response.status_code == 200
    assert response.get_json()["ledger_import"]["remote_done_imported"] == 2
    assert ledger["done_ranges"] == ["0x0003-0x0004"]
    assert payload["progress"]["complete_lanes"] == 2
    assert payload["progress"]["trusted_remote_lanes"] == 2


def test_phase3_command_center_persistent_ledger_done_survives_registry_loss(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    finish = client.post(
        "/api/ledger/finish",
        json={"device_id": "helper-a", "lane": "0x000A", "zip_size": 2048, "pk3_count": 65536},
    )
    payload = client.get("/api/status").get_json()

    assert finish.status_code == 200
    assert payload["progress"]["local_complete_lanes"] == 0
    assert payload["progress"]["ledger_done_lanes"] == 1
    assert payload["progress"]["trusted_remote_lanes"] == 1
    assert payload["progress"]["complete_lanes"] == 1


def test_phase3_command_center_active_import_does_not_shorten_long_claim(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    claim = client.post(
        "/api/ledger/claim",
        json={"device_id": "helper-a", "count": 1, "lanes": "0x0008", "lease_seconds": 3600},
    )
    before = client.get("/api/ledger/status").get_json()["active_claims"][0]["lease_until_unix"]
    heartbeat = client.post(
        "/api/coordination/heartbeat",
        json={"device_id": "helper-a", "workers": {"active_lane_ranges": ["0x0008"], "running_workers": 1}},
    )
    after = client.get("/api/ledger/status").get_json()["active_claims"][0]["lease_until_unix"]

    assert claim.status_code == 200
    assert heartbeat.status_code == 200
    assert after >= before


def test_phase3_command_center_coordinator_sanitizes_old_helper_stale_ledger_client_ranges(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    response = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "old-helper",
            "workers": {"running_workers": 4, "active_lane_ranges": ["0x0008-0x0009"]},
            "ledger_client": {
                "status": "running_batch",
                "workers": 4,
                "active_lane_ranges": ["0x0008-0x0009"],
                "age_seconds": 999.0,
            },
        },
    )
    ledger = client.get("/api/ledger/status").get_json()
    state = client.get("/api/coordination/state").get_json()
    payload = client.get("/api/status").get_json()

    assert response.status_code == 200
    assert response.get_json()["ledger_import"]["remote_active_imported"] == 0
    assert ledger["counts"]["running"] == 0
    assert ledger["active_claim_ranges"] == []
    registered_workers = state["registered_devices"][0]["workers"]
    assert registered_workers["running_workers"] == 0
    assert registered_workers["active_lane_ranges"] == []
    assert registered_workers["stale_ledger_client_ignored"] is True
    assert payload["workers"]["remote_running_workers"] == 0


def test_phase3_command_center_coordinator_preserves_explicit_pool_ranges_when_sidecar_stale(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    response = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "new-helper",
            "workers": {
                "running_workers": 4,
                "pool_running_workers": 4,
                "active_lane_ranges": ["0x0008-0x0009"],
                "pool_active_lane_ranges": ["0x0008-0x0009"],
            },
            "ledger_client": {
                "status": "running_batch",
                "workers": 4,
                "active_lane_ranges": ["0x0008-0x0009"],
                "age_seconds": 999.0,
            },
        },
    )
    ledger = client.get("/api/ledger/status").get_json()
    state = client.get("/api/coordination/state").get_json()
    payload = client.get("/api/status").get_json()

    assert response.status_code == 200
    assert response.get_json()["ledger_import"]["remote_active_imported"] == 2
    assert ledger["active_claim_ranges"] == ["0x0008-0x0009"]
    registered_workers = state["registered_devices"][0]["workers"]
    assert registered_workers["running_workers"] == 4
    assert registered_workers["active_lane_ranges"] == ["0x0008-0x0009"]
    assert registered_workers["stale_ledger_client_ignored"] is True
    assert payload["workers"]["remote_running_workers"] == 4


def test_phase3_command_center_legacy_stale_sidecar_infers_active_from_completed_bundle_lanes(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    response = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "legacy-helper",
            "health": {"complete_lane_ranges": ["0x0008", "0x000A"]},
            "workers": {"running_workers": 2, "active_lane_ranges": ["0x0008-0x000B"]},
            "ledger_client": {
                "status": "running_batch",
                "workers": 2,
                "active_lane_ranges": ["0x0008-0x000B"],
                "age_seconds": 999.0,
            },
        },
    )
    ledger = client.get("/api/ledger/status").get_json()
    state = client.get("/api/coordination/state").get_json()
    payload = client.get("/api/status").get_json()

    assert response.status_code == 200
    assert response.get_json()["ledger_import"]["remote_done_imported"] == 2
    assert response.get_json()["ledger_import"]["remote_active_imported"] == 2
    assert ledger["done_ranges"] == ["0x0008", "0x000A"]
    assert ledger["active_claim_ranges"] == ["0x0009", "0x000B"]
    registered_workers = state["registered_devices"][0]["workers"]
    assert registered_workers["running_workers"] == 2
    assert registered_workers["active_lane_ranges"] == ["0x0009", "0x000B"]
    assert registered_workers["legacy_pool_ranges_inferred_from_done"] is True
    assert payload["workers"]["remote_running_workers"] == 2


def test_phase3_command_center_stale_sidecar_releases_prior_remote_active_claims(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    first = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "old-helper",
            "workers": {"running_workers": 2, "active_lane_ranges": ["0x0008-0x0009"]},
        },
    )
    second = client.post(
        "/api/coordination/heartbeat",
        json={
            "device_id": "old-helper",
            "workers": {"running_workers": 2, "active_lane_ranges": ["0x0008-0x0009"]},
            "ledger_client": {
                "status": "running_batch",
                "workers": 2,
                "active_lane_ranges": ["0x0008-0x0009"],
                "age_seconds": 999.0,
            },
        },
    )
    ledger = client.get("/api/ledger/status").get_json()

    assert first.status_code == 200
    assert first.get_json()["ledger_import"]["remote_active_imported"] == 2
    assert second.status_code == 200
    assert second.get_json()["ledger_import"]["remote_active_imported"] == 0
    assert second.get_json()["ledger_import"]["remote_inactive_released"] == ["0x0008", "0x0009"]
    assert ledger["counts"]["running"] == 0
    assert ledger["counts"]["released"] == 2


def test_phase3_command_center_local_ledger_client_sync_releases_dead_local_claims(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_client_status = output_dir / "_phase3_ledger_worker_client_status.json"
    ledger_client_status.write_text(
        json.dumps(
            {
                "status": "running_batch",
                "device_id": "coord-pc",
                "worker_id": "command-center-worker-pool",
                "workers": 1,
                "active_lane_ranges": ["0x0002"],
                "updated_at_unix": time.time(),
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        ledger_client_status_path=ledger_client_status,
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, device_id="coord-pc"),
        start_heartbeat=False,
    )
    client = app.test_client()

    claim = client.post(
        "/api/ledger/claim",
        json={
            "device_id": "coord-pc",
            "worker_id": "command-center-worker-pool",
            "count": 2,
            "lanes": "0x0001-0x0002",
            "lease_seconds": 3600,
        },
    )
    payload = client.get("/api/status").get_json()
    ledger = client.get("/api/ledger/status").get_json()

    assert claim.status_code == 200
    assert payload["workers"]["active_lane_ranges"] == ["0x0002"]
    assert ledger["counts"]["running"] + ledger["counts"]["claimed"] == 1
    assert ledger["counts"]["released"] == 1
    assert ledger["active_claim_ranges"] == ["0x0002"]


def test_phase3_command_center_stale_local_ledger_client_keeps_current_pool_bundle_only(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_client_status = output_dir / "_phase3_ledger_worker_client_status.json"
    ledger_client_status.write_text(
        json.dumps(
            {
                "status": "running_batch",
                "device_id": "coord-pc",
                "worker_id": "command-center-worker-pool",
                "workers": 2,
                "active_lane_ranges": ["0x0001-0x0004"],
                "updated_at_unix": time.time() - 3600,
            }
        ),
        encoding="utf-8",
    )
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": time.time(),
                "counts": {"running": 1},
                "running": [
                    {
                        "slot_id": 1,
                        "worker_name": "spinda-phase3-0x0003",
                        "pid": 123,
                        "lane_id": "0x0003..0x0004",
                        "status": "running",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=pool_status,
        ledger_client_status_path=ledger_client_status,
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, device_id="coord-pc"),
        start_heartbeat=False,
    )
    client = app.test_client()

    claim = client.post(
        "/api/ledger/claim",
        json={
            "device_id": "coord-pc",
            "worker_id": "command-center-worker-pool",
            "count": 4,
            "lanes": "0x0001-0x0004",
            "lease_seconds": 3600,
        },
    )
    payload = client.get("/api/status").get_json()
    ledger = client.get("/api/ledger/status").get_json()

    assert claim.status_code == 200
    assert payload["workers"]["active_lane_ranges"] == ["0x0003-0x0004"]
    assert ledger["counts"]["running"] + ledger["counts"]["claimed"] == 2
    assert ledger["counts"]["released"] == 2
    assert ledger["active_claim_ranges"] == ["0x0003-0x0004"]


def test_phase3_command_center_tiny_zip_is_not_complete_or_claim_blocking(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"tiny")
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    payload = client.get("/api/status").get_json()
    reconcile = client.post("/api/ledger/reconcile", json={})
    status = client.get("/api/ledger/status").get_json()
    claim = client.post("/api/ledger/claim", json={"device_id": "bench-two", "count": 1, "lanes": "0x0001"})

    assert payload["progress"]["complete_lanes"] == 0
    assert payload["health"]["tiny_zips"] == 1
    assert reconcile.get_json()["reconciled_lanes"] == 0
    assert status["counts"]["done"] == 0
    assert claim.get_json()["claimed_lanes"] == ["0x0001"]


def test_phase3_command_center_rejects_tiny_finish_report(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )

    response = app.test_client().post(
        "/api/ledger/finish",
        json={"device_id": "bench-two", "lane": "0x0001", "zip_size": 12, "pk3_count": 65536},
    )

    assert response.status_code == 400
    assert "ZIP is too small" in response.get_json()["error"]


def test_phase3_command_center_finish_requires_size_and_pk3_count(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        start_heartbeat=False,
    )
    client = app.test_client()

    missing_size = client.post(
        "/api/ledger/finish",
        json={"device_id": "bench-two", "lane": "0x0001", "pk3_count": 65536},
    )
    missing_pk3 = client.post(
        "/api/ledger/finish",
        json={"device_id": "bench-two", "lane": "0x0001", "zip_size": 2048},
    )
    bad_pk3 = client.post(
        "/api/ledger/finish",
        json={"device_id": "bench-two", "lane": "0x0001", "zip_size": 2048, "pk3_count": 65535},
    )

    assert missing_size.status_code == 400
    assert "without ZIP size proof" in missing_size.get_json()["error"]
    assert missing_pk3.status_code == 400
    assert "pk3_count must be 65536" in missing_pk3.get_json()["error"]
    assert bad_pk3.status_code == 400
    assert "pk3_count must be 65536" in bad_pk3.get_json()["error"]


def test_phase3_command_center_ignores_stale_remote_worker_counts(tmp_path: Path, monkeypatch) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=16,
        coordination=web.CoordinationConfig(role="coordinator", online=True, heartbeat_seconds=60),
        start_heartbeat=False,
    )
    client = app.test_client()

    client.post(
        "/api/coordination/heartbeat",
        json={"device_id": "helper-a", "workers": {"running_workers": 4, "active_lane_ranges": ["0x0004"]}},
    )
    original_time = web.time.time()
    monkeypatch.setattr(web.time, "time", lambda: original_time + 301.0)
    payload = client.get("/api/status").get_json()

    assert payload["coordination"]["registered_devices"][0]["age_seconds"] >= 300
    assert payload["workers"]["remote_running_workers"] == 0
    assert payload["workers"]["combined_running_workers"] == 0


def test_phase3_command_center_updates_coordination_settings_from_ui(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    settings_path = output_dir / "_phase3_command_center_network.json"
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        coordination=web.CoordinationConfig(role="coordinator", online=False),
        coordination_settings_path=settings_path,
        start_heartbeat=False,
    )

    response = app.test_client().post(
        "/api/coordination/settings",
        json={
            "role": "subordinate",
            "online": True,
            "primary_host": "192.168.1.10",
            "primary_port": 235,
            "advertise_host": "192.168.1.21",
            "advertise_port": 236,
            "heartbeat_seconds": 30,
        },
    )
    state = app.test_client().get("/api/coordination/state").get_json()
    saved = json.loads(settings_path.read_text(encoding="utf-8"))

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    assert state["role"] == "subordinate"
    assert state["online"] is True
    assert state["primary_url"] == "http://192.168.1.10:235"
    assert state["advertise_url"] == "http://192.168.1.21:236"
    assert saved["role"] == "subordinate"
    assert saved["online"] is True


def test_phase3_command_center_coordination_endpoint_disabled_when_offline(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        coordination=web.CoordinationConfig(role="coordinator", online=False),
    )

    response = app.test_client().post("/api/coordination/heartbeat", json={"device_id": "bench-two"})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_phase3_command_center_ledger_claim_heartbeat_finish_release(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=8,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        ledger_path=output_dir / "_phase3_lane_ledger.json",
        start_heartbeat=False,
    )
    client = app.test_client()

    claim = client.post(
        "/api/ledger/claim",
        json={"device_id": "bench-two", "worker_id": "worker-a", "count": 2, "lanes": "0x0001-0x0004", "lease_seconds": 600},
    )
    heartbeat = client.post(
        "/api/ledger/heartbeat",
        json={"device_id": "bench-two", "lanes": ["0x0001"], "lease_seconds": 600},
    )
    finish = client.post(
        "/api/ledger/finish",
        json={"device_id": "bench-two", "lane": "0x0001", "zip_size": 2048, "zip_sha256": "abc", "pk3_count": 65536},
    )
    release = client.post(
        "/api/ledger/release",
        json={"device_id": "bench-two", "lanes": ["0x0002"]},
    )
    status = client.get("/api/ledger/status").get_json()

    assert claim.status_code == 200
    assert claim.get_json()["claimed_lanes"] == ["0x0001", "0x0002"]
    assert heartbeat.get_json()["updated_lanes"] == ["0x0001"]
    assert finish.get_json()["status"] == "done"
    assert release.get_json()["released_lanes"] == ["0x0002"]
    assert status["counts"]["done"] == 1
    assert status["counts"]["released"] == 1
    assert status["counts"]["pending"] == 7


def test_phase3_command_center_ledger_claim_skips_existing_zip_and_reconciles(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        ledger_path=output_dir / "_phase3_lane_ledger.json",
        start_heartbeat=False,
    )
    client = app.test_client()

    claim = client.post(
        "/api/ledger/claim",
        json={"device_id": "bench-two", "count": 2, "lanes": "0x0001-0x0003"},
    )
    reconcile = client.post("/api/ledger/reconcile", json={})
    status = client.get("/api/ledger/status").get_json()

    assert claim.status_code == 200
    assert claim.get_json()["claimed_lanes"] == ["0x0002", "0x0003"]
    assert reconcile.status_code == 200
    assert status["counts"]["done"] == 1
    assert status["counts"]["claimed"] == 2


def test_phase3_command_center_ledger_claim_rejected_on_subordinate(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        coordination=web.CoordinationConfig(role="subordinate", online=True),
        ledger_path=output_dir / "_phase3_lane_ledger.json",
        start_heartbeat=False,
    )

    response = app.test_client().post("/api/ledger/claim", json={"device_id": "bench-two"})

    assert response.status_code == 409
    assert response.get_json()["ok"] is False


def test_phase3_command_center_ledger_summary_excludes_expired_claims_from_active_ranges(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    ledger_path = output_dir / "_phase3_lane_ledger.json"
    old = time.time() - 3600
    ledger_path.write_text(
        json.dumps(
            {
                "version": 1,
                "target_lanes": 4,
                "records": {
                    "0x0001": {
                        "lane": "0x0001",
                        "status": "running",
                        "device_id": "stale-worker",
                        "lease_until_unix": old,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        coordination=web.CoordinationConfig(role="coordinator", online=True),
        ledger_path=ledger_path,
        start_heartbeat=False,
    )
    client = app.test_client()

    status = client.get("/api/ledger/status").get_json()
    claim = client.post("/api/ledger/claim", json={"device_id": "fresh-worker", "count": 1, "lanes": "0x0001"})

    assert status["counts"]["expired_claims"] == 1
    assert status["counts"]["running"] == 0
    assert status["counts"]["pending"] == 4
    assert status["active_claim_ranges"] == []
    assert status["active_claims"] == []
    assert claim.get_json()["claimed_lanes"] == ["0x0001"]


def test_phase3_command_center_launcher_starts_independent_watcher() -> None:
    source = PS1_PATH.read_text(encoding="utf-8")

    assert "phase3_independent_watcher.py" in source
    assert "function Start-Watcher" in source
    assert "Start-Watcher" in source.split('\"Start\"', 1)[1]
    assert "_phase3_independent_watcher_status.json" in source
    assert "--command-center-url" in source
    assert "--role" in source
    assert "--online" in source
    assert "--offline" in source
    assert "--primary-host" in source
    assert "portable-python\\python.exe" in source


def test_phase3_command_center_marks_pkhex_ready_only_when_all_lanes_complete(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    for lane in range(4):
        (output_dir / f"0x{lane:04X}.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
    )

    response = app.test_client().get("/api/status")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["validation_policy"]["pkhex_validator"]["status"] == "ready"
    assert payload["validation_policy"]["pkhex_validator"]["ready"] is True
    assert "final semantic audit can run" in payload["validation_policy"]["pkhex_validator"]["reason"]


def test_phase3_command_center_worker_rows_do_not_use_inner_html() -> None:
    source = WEB_PATH.read_text(encoding="utf-8")

    assert "row.innerHTML" not in source
    assert "document.createElement(\"td\")" in source


def test_phase3_command_center_launches_cli_with_bundle_zip_and_status_knobs() -> None:
    source = WEB_PATH.read_text(encoding="utf-8")

    assert "DEFAULT_BUNDLE_SIZE = 2" in source
    assert "DEFAULT_STATUS_WRITE_SECONDS = 10.0" in source
    assert "DEFAULT_LEDGER_WORKER_CLIENT_SCRIPT" in source
    assert '"--bundle-size"' in source
    assert '"--zip-method"' in source
    assert '"--status-write-seconds"' in source


def test_phase3_command_center_online_control_launches_ledger_client(
    tmp_path: Path, monkeypatch
) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    control_path = output_dir / "_native_phase3_worker_pool_control.json"
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(web.subprocess, "Popen", fake_popen)
    controller = web.Phase3WorkerController(
        python_exe=Path("python"),
        worker_pool_script=Path("pool.py"),
        ledger_worker_client_script=Path("ledger.py"),
        output_dir=output_dir,
        pool_control_path=control_path,
        ledger_client_status_path=output_dir / "_ledger_status.json",
        bundle_size=2,
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        controller=controller,
        coordination=web.CoordinationConfig(
            role="subordinate",
            online=True,
            primary_scheme="https",
            primary_host="coord.example",
            primary_port=443,
            device_id="bench-two",
        ),
        start_heartbeat=False,
    )

    response = app.test_client().post(
        "/api/control/workers",
        json={"workers": 3, "lanes": "0x0100-0x0105", "launch_if_needed": True},
    )

    assert response.status_code == 200
    assert commands
    command = commands[0]
    assert command[:2] == ["python", "ledger.py"]
    assert command[command.index("--coordinator-url") + 1] == "https://coord.example:443"
    assert command[command.index("--device-id") + 1] == "bench-two"
    assert command[command.index("--batch-size") + 1] == "6"
    assert command[command.index("--worker-pool-script") + 1] == "pool.py"
    assert "--control-file" in command
    assert response.get_json()["controller"]["managed_process_kind"] == "ledger-client"


def test_phase3_command_center_coordinator_local_workers_claim_from_own_panel_url(
    tmp_path: Path, monkeypatch
) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    control_path = output_dir / "_native_phase3_worker_pool_control.json"
    commands: list[list[str]] = []

    class FakeProcess:
        pid = 4321

        def poll(self):
            return None

        def terminate(self):
            return None

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeProcess()

    monkeypatch.setattr(web.subprocess, "Popen", fake_popen)
    controller = web.Phase3WorkerController(
        python_exe=Path("python"),
        worker_pool_script=Path("pool.py"),
        ledger_worker_client_script=Path("ledger.py"),
        output_dir=output_dir,
        pool_control_path=control_path,
        ledger_client_status_path=output_dir / "_ledger_status.json",
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        controller=controller,
        coordination=web.CoordinationConfig(
            role="coordinator",
            online=True,
            primary_scheme="https",
            primary_host="wrong.example",
            primary_port=443,
            device_id="coordinator-box",
        ),
        display_url="http://127.0.0.1:235/",
        start_heartbeat=False,
    )

    response = app.test_client().post(
        "/api/control/workers",
        json={"workers": 2, "lanes": "0x0100-0x0103", "launch_if_needed": True},
    )

    assert response.status_code == 200
    command = commands[0]
    assert command[command.index("--coordinator-url") + 1] == "http://127.0.0.1:235"
    assert "https://wrong.example:443" not in command


def test_phase3_command_center_folder_defaults_keep_status_files_inside_folder(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Assisted-baking" / "Phase3SpindaBlocks"
    output_dir.mkdir(parents=True)

    args = web.parse_args(["--folder", str(output_dir)])
    app = web.create_app(output_dir=output_dir, start_heartbeat=False)
    payload = app.test_client().get("/api/status").get_json()

    assert args.pool_status == output_dir / "_native_phase3_worker_pool_status.json"
    assert args.pool_control == output_dir / "_native_phase3_worker_pool_control.json"
    assert args.watcher_status == output_dir / "_phase3_independent_watcher_status.json"
    assert args.ledger_client_status == output_dir / "_phase3_ledger_worker_client_status.json"
    assert args.cache_dir == output_dir / "_cache"
    assert payload["control"]["control_file"] == str(output_dir / "_native_phase3_worker_pool_control.json")
    assert payload["control"]["ledger_client_status_path"] == str(output_dir / "_phase3_ledger_worker_client_status.json")
    assert payload["workers"]["pool_status_path"] == str(output_dir / "_native_phase3_worker_pool_status.json")
    assert payload["watcher"]["status_path"] == str(output_dir / "_phase3_independent_watcher_status.json")
    assert payload["ledger_client"]["path"] == str(output_dir / "_phase3_ledger_worker_client_status.json")
    assert str(output_dir) in payload["validation_policy"]["raw_zip_validator"]["active_run_command"]


def test_phase3_command_center_defaults_are_portable_not_hardcoded_to_main_workspace() -> None:
    source = WEB_PATH.read_text(encoding="utf-8")
    ps1_source = PS1_PATH.read_text(encoding="utf-8")

    assert 'Path(r"<repo-root>\\Phase3SpindaBlocks")' not in source
    assert 'ROOT / "Phase3SpindaBlocks"' in source
    assert '[string]$ProjectRoot = ""' in ps1_source
    assert 'Join-Path $PSScriptRoot "..\\.."' in ps1_source
    assert '"--folder", $OutputDir' in ps1_source
    assert '"--ledger-client-status", $LedgerClientStatusPath' in ps1_source
    assert '"--pool-status", $PoolStatusPath' in ps1_source


def test_phase3_command_center_default_lanes_include_endpoints(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()

    args = web.parse_args(["--folder", str(output_dir)])
    app = web.create_app(output_dir=output_dir, start_heartbeat=False)
    payload = app.test_client().get("/api/status").get_json()
    html = app.test_client().get("/").get_data(as_text=True)

    assert web.DEFAULT_LANES == "0x0000-0xFFFF"
    assert args.lanes == "0x0000-0xFFFF"
    assert payload["control"]["default_lanes"] == "0x0000-0xFFFF"
    assert 'id="lane-range" value="0x0000-0xFFFF"' in html


def test_phase3_command_center_payload_builder_defaults_use_output_folder(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps({"time_unix": time.time(), "counts": {"running": 0}, "running": []}),
        encoding="utf-8",
    )

    payload = web.build_command_center_payload(output_dir=output_dir)

    assert payload["workers"]["pool_status_path"] == str(pool_status)
    assert payload["watcher"]["status_path"] == str(output_dir / "_phase3_independent_watcher_status.json")


def test_phase3_command_center_idle_control_hides_stale_running_workers(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_bytes(
        b"\xef\xbb\xbf"
        + json.dumps(
            {
                "time_unix": 1000,
                "running": [{"pid": 111, "lane_id": "0x0002"}],
                "counts": {"running": 1, "pending": 10},
            }
        ).encode("utf-8")
    )
    (output_dir / "_native_phase3_worker_pool_control.json").write_bytes(
        b"\xef\xbb\xbf" + json.dumps({"desired_workers": 0, "shutdown": True}).encode("utf-8")
    )
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=pool_status,
        target_lanes=4,
    )

    payload = app.test_client().get("/api/status").get_json()

    assert payload["workers"]["running_workers"] == 0
    assert payload["workers"]["pool_idle_requested"] is True
    assert payload["workers"]["pool_status_stale"] is False


def test_phase3_command_center_forced_status_refresh_does_not_force_zip_scan(
    tmp_path: Path, monkeypatch
) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    calls: list[str] = []
    original_audit = web.audit_phase3_zips

    def counted_audit(*args, **kwargs):
        calls.append("scan")
        return original_audit(*args, **kwargs)

    monkeypatch.setattr(web, "audit_phase3_zips", counted_audit)
    cache = web.CommandCenterPayloadCache(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        sample_interval_seconds=0.5,
        zip_scan_interval_seconds=999.0,
    )

    first = cache.get(force=True)
    (output_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    second = cache.get(force=True)
    third = cache.get(force=True, force_zip=True)

    assert first["progress"]["complete_lanes"] == 1
    assert second["progress"]["complete_lanes"] == 1
    assert third["progress"]["complete_lanes"] == 2
    assert len(calls) == 2
    assert second["zip_scan_interval_seconds"] == 999.0
    assert second["zip_scan_age_seconds"] is not None


def test_phase3_command_center_caches_host_resource_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    calls: list[str] = []

    def counted_host_snapshot(path: Path):
        calls.append(str(path))
        return {
            "cpu_percent": None,
            "memory": {"available_bytes": None, "total_bytes": None, "used_percent": None},
            "disk": {"free_bytes": 123},
        }

    monkeypatch.setattr(web, "host_resource_snapshot", counted_host_snapshot)
    cache = web.CommandCenterPayloadCache(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
        sample_interval_seconds=0.5,
        zip_scan_interval_seconds=999.0,
        host_resource_interval_seconds=999.0,
    )

    first = cache.get(force=True)
    second = cache.get(force=True)

    assert first["host"]["disk"]["free_bytes"] == 123
    assert second["host"]["disk"]["free_bytes"] == 123
    assert len(calls) == 1
    assert second["host_resource_interval_seconds"] == 999.0


def test_phase3_command_center_control_endpoint_writes_desired_workers(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    control_path = output_dir / "_native_phase3_worker_pool_control.json"
    controller = web.Phase3WorkerController(output_dir=output_dir, pool_control_path=control_path)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        controller=controller,
    )

    response = app.test_client().post(
        "/api/control/workers",
        json={"workers": 5, "lanes": "0x0100-0x01FF", "launch_if_needed": False},
    )

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    payload = json.loads(control_path.read_text(encoding="utf-8"))
    assert payload["desired_workers"] == 5
    assert payload["shutdown"] is False
    assert payload["lanes"] == "0x0100-0x01FF"


def test_phase3_command_center_stop_endpoint_requests_shutdown(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    control_path = output_dir / "_native_phase3_worker_pool_control.json"
    controller = web.Phase3WorkerController(output_dir=output_dir, pool_control_path=control_path)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        controller=controller,
    )

    response = app.test_client().post("/api/control/stop", json={"force": False})

    assert response.status_code == 200
    assert response.get_json()["ok"] is True
    payload = json.loads(control_path.read_text(encoding="utf-8"))
    assert payload["desired_workers"] == 0
    assert payload["shutdown"] is True


def test_phase3_command_center_killswitch_stops_pool_and_host_workers(
    tmp_path: Path, monkeypatch
) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    running_status = output_dir / "_0x0003.phase3_status.json"
    running_status.write_text(json.dumps({"status": "running"}), encoding="utf-8")
    pool_status = output_dir / "_native_phase3_worker_pool_status.json"
    pool_status.write_text(
        json.dumps(
            {
                "time_unix": 1000,
                "running": [
                    {
                        "slot_id": 1,
                        "worker_name": "spinda-phase3-0x0003",
                        "pid": 333,
                        "lane_id": "0x0003",
                        "status_path": str(running_status),
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    stopped: dict[str, object] = {}

    class FakeController:
        def state(self) -> dict[str, object]:
            return {"managed_pool_pid": None}

        def stop(self, *, force_pids=()):
            stopped["force_pids"] = list(force_pids)
            return {"shutdown_requested": True, "force_killed_pids": list(force_pids)}

    monkeypatch.setattr(web, "_host_phase3_worker_pids", lambda: [111, 222])
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=pool_status,
        controller=FakeController(),
    )

    response = app.test_client().post("/api/control/killswitch", json={"confirm": True})

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ok"] is True
    assert payload["killswitch"] is True
    assert payload["pid_candidates"] == [111, 222, 333]
    assert stopped["force_pids"] == [111, 222, 333]


def test_phase3_command_center_sse_stream_emits_progress_event(tmp_path: Path) -> None:
    web = _load_web_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    app = web.create_app(
        output_dir=output_dir,
        pool_status_path=output_dir / "_native_phase3_worker_pool_status.json",
        target_lanes=4,
    )

    response = app.test_client().get("/events?interval=0.5", buffered=False)
    stream = response.response
    retry = next(stream).decode("utf-8")
    progress = next(stream).decode("utf-8")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    assert retry == "retry: 2500\n\n"
    assert progress.startswith("event: progress\n")
    assert '"complete_lanes":1' in progress
