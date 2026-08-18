from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
CLIENT_PATH = REPO_ROOT / "tools" / "spinda" / "phase3_ledger_worker_client.py"
VALID_ZIP_BYTES = b"z" * 2048


def _load_client_module():
    module_name = "testable_phase3_ledger_worker_client"
    spec = importlib.util.spec_from_file_location(module_name, CLIENT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase3_ledger_worker_client_parses_args_and_passthrough(tmp_path: Path) -> None:
    client = _load_client_module()

    args, passthrough = client.parse_args(
        [
            "--coordinator-url",
            "http://192.168.1.10:235",
            "--device-id",
            "bench-two",
            "--batch-size",
            "4",
            "--workers",
            "2",
            "--output-dir",
            str(tmp_path),
            "--",
            "--rom",
            "lg.gba",
        ]
    )

    assert args.coordinator_url == "http://192.168.1.10:235"
    assert args.device_id == "bench-two"
    assert args.batch_size == 4
    assert passthrough == ["--rom", "lg.gba"]


def test_phase3_ledger_worker_client_builds_worker_pool_command(tmp_path: Path) -> None:
    client = _load_client_module()
    args, passthrough = client.parse_args(
        [
            "--python-exe",
            "python",
            "--worker-pool-script",
            "pool.py",
            "--workers",
            "3",
            "--bundle-size",
            "2",
            "--output-dir",
            str(tmp_path),
            "--",
            "--rom",
            "lg.gba",
        ]
    )

    command = client.worker_pool_command(args, ["0x0001", "0x0002"], passthrough)

    assert command[:6] == ["python", "pool.py", "--lanes", "0x0001,0x0002", "--workers", "3"]
    assert "--skip-existing-by-name" in command
    assert "--overwrite" in command
    assert command[-2:] == ["--rom", "lg.gba"]


def test_phase3_ledger_worker_client_passes_linux_helper_cli_options(tmp_path: Path) -> None:
    client = _load_client_module()
    args, passthrough = client.parse_args(
        [
            "--python-exe",
            "python3",
            "--worker-pool-script",
            "tools/spinda/native_phase3_worker_pool.py",
            "--workers",
            "6",
            "--bundle-size",
            "2",
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

    command = client.worker_pool_command(args, ["0x0100"], passthrough)

    assert command[0] == "python3"
    assert Path(command[1]) == Path("tools/spinda/native_phase3_worker_pool.py")
    assert command[2:4] == ["--lanes", "0x0100"]
    assert "--phase3-cli-exe" in command
    assert "build-linux-spinda-cli/mgba-spinda-phase3" in command
    assert "--rom" in command
    assert "inputs/lg.gba" in command


def test_phase3_ledger_worker_client_reports_finish_and_fail(tmp_path: Path, monkeypatch) -> None:
    client = _load_client_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post_json(base_url: str, path: str, payload: dict[str, object], *, timeout: float = 15.0):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(client, "post_json", fake_post_json)
    args, _ = client.parse_args(
        [
            "--coordinator-url",
            "http://coordinator:235",
            "--device-id",
            "bench-two",
            "--output-dir",
            str(output_dir),
        ]
    )

    client.report_results(args, ["0x0001", "0x0002"])

    assert calls[0][0] == "/api/ledger/finish"
    assert calls[0][1]["lane"] == "0x0001"
    assert calls[0][1]["zip_size"] == len(VALID_ZIP_BYTES)
    assert calls[1][0] == "/api/ledger/fail"
    assert calls[1][1]["lane"] == "0x0002"
    assert calls[1][1]["retryable"] is True


def test_phase3_ledger_worker_client_valid_local_zip_requires_minimum_size(tmp_path: Path) -> None:
    client = _load_client_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(b"tiny")
    (output_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    assert client.valid_local_zip(output_dir, "0x0001") is False
    assert client.valid_local_zip(output_dir, "0x0002") is True


def test_phase3_ledger_worker_client_reports_available_results_once(tmp_path: Path, monkeypatch) -> None:
    client = _load_client_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    calls: list[tuple[str, dict[str, object]]] = []

    def fake_post_json(base_url: str, path: str, payload: dict[str, object], *, timeout: float = 15.0):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(client, "post_json", fake_post_json)
    args, _ = client.parse_args(["--output-dir", str(output_dir)])
    reported: set[str] = set()

    first = client.report_available_results(args, ["0x0001", "0x0002"], reported)
    second = client.report_available_results(args, ["0x0001", "0x0002"], reported)

    assert first == ["0x0001"]
    assert second == []
    assert reported == {"0x0001"}
    assert calls == [
        (
            "/api/ledger/finish",
            {
                "device_id": args.device_id,
                "worker_id": args.worker_id,
                "lane": "0x0001",
                "zip_path": str(output_dir / "0x0001.spinda80.zip"),
                "zip_size": len(VALID_ZIP_BYTES),
                "pk3_count": 65536,
            },
        )
    ]


def test_phase3_ledger_worker_client_reports_completed_zip_during_running_batch(tmp_path: Path, monkeypatch) -> None:
    client = _load_client_module()
    output_dir = tmp_path / "Phase3SpindaBlocks"
    output_dir.mkdir()
    status_path = output_dir / "_phase3_ledger_worker_client_status.json"
    calls: list[tuple[str, dict[str, object]]] = []
    sleeps: list[float] = []

    class FakeProcess:
        returncode = 0

        def __init__(self) -> None:
            self.poll_count = 0

        def poll(self):
            self.poll_count += 1
            if self.poll_count == 1:
                (output_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
                return None
            return 0

    def fake_post_json(base_url: str, path: str, payload: dict[str, object], *, timeout: float = 15.0):
        calls.append((path, payload))
        return {"ok": True}

    monkeypatch.setattr(client.subprocess, "Popen", lambda *args, **kwargs: FakeProcess())
    monkeypatch.setattr(client, "post_json", fake_post_json)
    monkeypatch.setattr(client.time, "sleep", lambda seconds: sleeps.append(seconds))
    args, passthrough = client.parse_args(
        [
            "--output-dir",
            str(output_dir),
            "--status-out",
            str(status_path),
            "--heartbeat-seconds",
            "5",
        ]
    )
    reported: set[str] = set()

    exit_code = client.run_claimed_batch(
        args,
        ["0x0001"],
        passthrough,
        all_claimed_lanes=["0x0001"],
        reported_lanes=reported,
    )

    assert exit_code == 0
    assert reported == {"0x0001"}
    assert any(path == "/api/ledger/heartbeat" for path, _payload in calls)
    assert any(path == "/api/ledger/finish" and payload["lane"] == "0x0001" for path, payload in calls)
    payload = json.loads(status_path.read_text(encoding="utf-8"))
    assert payload["reported_lanes"] == ["0x0001"]
    assert sleeps


def test_phase3_ledger_worker_client_writes_active_status_ranges(tmp_path: Path) -> None:
    client = _load_client_module()
    status_path = tmp_path / "_phase3_ledger_worker_client_status.json"
    args, _ = client.parse_args(
        [
            "--device-id",
            "bench-two",
            "--worker-id",
            "pool-a",
            "--output-dir",
            str(tmp_path),
            "--status-out",
            str(status_path),
        ]
    )

    client.write_client_status(args, status="claimed", lanes=["0x0001", "0x0002", "0x0004"])
    payload = json.loads(status_path.read_text(encoding="utf-8"))

    assert payload["status"] == "claimed"
    assert payload["device_id"] == "bench-two"
    assert payload["active_lane_ranges"] == ["0x0001-0x0002", "0x0004"]


def test_phase3_ledger_worker_client_clears_active_ranges_after_report(tmp_path: Path) -> None:
    client = _load_client_module()
    status_path = tmp_path / "_phase3_ledger_worker_client_status.json"
    args, _ = client.parse_args(["--output-dir", str(tmp_path), "--status-out", str(status_path)])

    client.write_client_status(args, status="reported_batch", lanes=["0x0001", "0x0002"])
    payload = json.loads(status_path.read_text(encoding="utf-8"))

    assert payload["claimed_lanes"] == ["0x0001", "0x0002"]
    assert payload["active_lane_ranges"] == []


def test_phase3_ledger_worker_client_retries_reporting_before_new_claim(tmp_path: Path, monkeypatch) -> None:
    client = _load_client_module()
    status_path = tmp_path / "_phase3_ledger_worker_client_status.json"
    args, _ = client.parse_args(
        [
            "--output-dir",
            str(tmp_path),
            "--status-out",
            str(status_path),
            "--sleep-error-seconds",
            "5",
        ]
    )
    calls = {"reports": 0, "sleeps": 0}

    def flaky_report(_args, lanes):
        calls["reports"] += 1
        if calls["reports"] == 1:
            raise RuntimeError("coordinator down")

    monkeypatch.setattr(client, "report_results", flaky_report)
    monkeypatch.setattr(client.time, "sleep", lambda _seconds: calls.__setitem__("sleeps", calls["sleeps"] + 1))

    client.report_results_until_success(args, ["0x0001"], batch_code=0)
    payload = json.loads(status_path.read_text(encoding="utf-8"))

    assert calls == {"reports": 2, "sleeps": 1}
    assert payload["status"] == "reported_batch"
    assert payload["active_lane_ranges"] == []
