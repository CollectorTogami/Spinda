from __future__ import annotations

import importlib.util
import json
import sys
import zipfile
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT_PATH = REPO_ROOT / "tools" / "spinda" / "phase3_zip_validator.py"


def _load_module():
    module_name = "testable_phase3_zip_validator"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase3_zip_validator_manifest_reports_missing_and_artifacts(tmp_path: Path) -> None:
    module = _load_module()
    (tmp_path / "0x0000.spinda80.zip").write_bytes(b"zip")
    (tmp_path / "0x0001.spinda80.zip").write_bytes(b"zip")
    (tmp_path / "0x0002.spinda80.zip.pid123.tmp").write_bytes(b"tmp")
    (tmp_path / "0x0004.spinda80.zip").write_bytes(b"")
    (tmp_path / "bad.spinda80.zip").write_bytes(b"bad")

    paths, audit = module.scan_output_folder(tmp_path, target_lanes=4, sample_limit=8)

    assert [path.name for path in paths] == ["0x0000.spinda80.zip", "0x0001.spinda80.zip"]
    assert audit["valid_zip_count"] == 2
    assert audit["missing_lane_count"] == 2
    assert audit["samples"]["missing_lanes"] == ["0x0002", "0x0003"]
    assert audit["tmp_file_count"] == 1
    assert audit["zero_size_zip_count"] == 1
    assert audit["bad_name_count"] == 1
    assert audit["bad_artifact_count"] == 3


def test_phase3_zip_validator_manifest_only_allows_incomplete_when_requested(
    tmp_path: Path,
    monkeypatch,
) -> None:
    module = _load_module()
    report = tmp_path / "report.json"
    (tmp_path / "0x0000.spinda80.zip").write_bytes(b"zip")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "phase3_zip_validator.py",
            "--root",
            str(tmp_path),
            "--target-lanes",
            "4",
            "--manifest-only",
            "--allow-incomplete",
            "--report",
            str(report),
            "--quiet",
        ],
    )

    exit_code = module.main()
    payload = json.loads(report.read_text(encoding="utf-8"))

    assert exit_code == 0
    assert payload["mode"] == "manifest-only"
    assert payload["folder_audit"]["missing_lane_count"] == 3
    assert payload["pkhex_validation"]["status"] == "deferred"


def test_phase3_zip_validator_deep_audit_checks_pid_bytes_in_ram(tmp_path: Path) -> None:
    module = _load_module()
    path = tmp_path / "0x0001.spinda80.zip"
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("0x00000001.pk3", (0x00000002).to_bytes(4, "little") + bytes(76))

    result = module.audit_zip(path, sample_limit=4)

    assert "entry_count:1" in result["errors"]
    assert "bad_content_pid:1" in result["errors"]
    assert result["samples"]["bad_content_pid"] == ["0x00000001.pk3"]


def test_phase3_zip_validator_never_extracts_pk3_to_disk() -> None:
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert "archive.read(info)" in source
    assert "ExtractToFile" not in source
    assert "extractall" not in source
    assert ".pk3').write" not in source


def test_phase3_zip_validator_default_root_is_portable() -> None:
    module = _load_module()
    source = SCRIPT_PATH.read_text(encoding="utf-8")

    assert module.DEFAULT_OUTPUT_DIR == REPO_ROOT / "Phase3SpindaBlocks"
    assert 'Path(r"<repo-root>\\Phase3SpindaBlocks")' not in source
