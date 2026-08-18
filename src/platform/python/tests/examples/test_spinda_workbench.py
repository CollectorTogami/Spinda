from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
WORKBENCH_PATH = REPO_ROOT / "tools" / "spinda" / "spinda_workbench" / "spinda_workbench.py"
VALID_ZIP_BYTES = b"z" * 2048


def _load_workbench_module():
    module_name = "testable_spinda_workbench"
    spec = importlib.util.spec_from_file_location(module_name, WORKBENCH_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def test_phase3_scan_counts_good_and_bad_artifacts(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0002.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0003.spinda80.zip").write_bytes(b"")
    (phase3_dir / "0x0004.spinda80.zip.pid123.tmp").write_bytes(b"tmp")
    (phase3_dir / "bad.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    config = workbench.WorkbenchConfig(phase3_dir=phase3_dir, target_phase3_lanes=4)
    summary = workbench.scan_phase3(config)

    assert summary.complete_lanes == 2
    assert summary.missing_lanes == 2
    assert summary.zero_size_zips == 1
    assert summary.tmp_files == 1
    assert summary.bad_names == 1
    assert summary.bad_artifacts == 3
    assert summary.complete_lane_ranges == ["0x0001-0x0002"]


def test_phase3_scan_rejects_out_of_scope_endpoint_and_extra_lanes(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x0000.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x0003.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    summary = workbench.scan_phase3(workbench.WorkbenchConfig(phase3_dir=phase3_dir, target_phase3_lanes=2))

    assert summary.target_lanes == 2
    assert summary.complete_lanes == 1
    assert summary.missing_lanes == 1
    assert summary.progress_percent == 50.0
    assert summary.out_of_scope_zips == 2
    assert summary.bad_artifacts == 2
    assert summary.complete_lane_ranges == ["0x0001"]


def test_phase3_scan_accepts_case_insensitive_zip_and_tmp_suffixes(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0X000A.spinda80.ZIP").write_bytes(VALID_ZIP_BYTES)
    (phase3_dir / "0x000B.spinda80.ZIP.pid123.TMP").write_bytes(b"tmp")
    (phase3_dir / "bad.SPINDa80.ZIP.extra").write_bytes(VALID_ZIP_BYTES)

    summary = workbench.scan_phase3(workbench.WorkbenchConfig(phase3_dir=phase3_dir, target_phase3_lanes=0x000A))

    assert summary.complete_lanes == 1
    assert summary.tmp_files == 1
    assert summary.bad_names == 1
    assert summary.bad_artifacts == 2
    assert summary.complete_lane_ranges == ["0x000A"]


def test_tsv_scan_counts_valid_duplicate_mismatch_and_ledger(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    tsv_dir = tmp_path / "TSVs"
    tsv_dir.mkdir()
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

    summary = workbench.scan_tsv(workbench.WorkbenchConfig(tsv_dir=tsv_dir))

    assert summary.complete_saves == 1
    assert summary.duplicate_tsvs == 1
    assert summary.duplicate_files == 1
    assert summary.mismatched_files == 1
    assert summary.invalid_files == 1
    assert summary.ledger_done == 1
    assert summary.ledger_errors == 1


def test_tsv_ledger_complete_count_rejects_bool_value(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    tsv_dir = tmp_path / "TSVs"
    tsv_dir.mkdir()
    (tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json").write_text(
        json.dumps({"complete_shiny_values": True}),
        encoding="utf-8",
    )

    summary = workbench.scan_tsv(workbench.WorkbenchConfig(tsv_dir=tsv_dir))

    assert summary.ledger_done is None
    assert summary.ledger_errors is None


def test_pid_locator_maps_pid_to_lane_entry_and_tsv(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x5678.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    location = workbench.locate_pid("0x12345678", phase3_dir)

    assert location.pid == "0x12345678"
    assert location.upper == "0x1234"
    assert location.lower == "0x5678"
    assert location.entry_name == "0x12345678.pk3"
    assert location.expected_psv == ((0x1234 ^ 0x5678) >> 3)
    assert location.matching_tsv == location.expected_psv
    assert location.matching_sid_min == location.expected_psv << 3
    assert location.matching_sid_max == (location.expected_psv << 3) | 7
    assert location.zip_exists is True


def test_pid_locator_requires_zip_path_to_be_file(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x5678.spinda80.zip").mkdir()

    location = workbench.locate_pid("0x12345678", phase3_dir)

    assert location.zip_exists is False
    assert location.note == "ZIP file not present yet."


def test_pid_locator_accepts_case_insensitive_pk3_suffix(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x5678.spinda80.zip").write_bytes(VALID_ZIP_BYTES)

    location = workbench.locate_pid("0X12345678.PK3", phase3_dir)

    assert location.pid == "0x12345678"
    assert location.entry_name == "0x12345678.pk3"
    assert location.zip_exists is True


def test_spinda_painter_uses_original_nibble_grid() -> None:
    workbench = _load_workbench_module()

    spots = workbench.spinda_spots(0x12345678)
    offsets = [(spot.offset_x, spot.offset_y) for spot in spots]
    anchors = [(spot.x, spot.y) for spot in spots]
    centers = [(spot.center_x, spot.center_y) for spot in spots]
    flat_centers = workbench._spot_centers_from_pid(0x12345678)

    assert offsets == [(8, 7), (6, 5), (4, 3), (2, 1)]
    assert anchors == [(18, 20), (40, 19), (20, 34), (30, 33)]
    assert centers == [(flat_centers[index], flat_centers[index + 1]) for index in range(0, 8, 2)]
    assert workbench.spinda_stats(0x12345678, tid=0, sid=0)["ability_slot"] == "First"


def test_pid_report_adds_painter_stats_traits_and_svg(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()
    (phase3_dir / "0x5678.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    sid = (((0x1234 ^ 0x5678) >> 3) << 3)

    report = workbench.pid_painter_report("0x12345678", phase3_dir, tid=0, sid=sid)

    assert report["pid"] == "0x12345678"
    assert report["zip_exists"] is True
    assert report["painter"]["source_reference"] == workbench.SPINDA_PAINTER_REFERENCE_URL
    assert report["painter"]["stats"]["is_shiny"] is True
    assert report["painter"]["spots"][0]["offset_x"] == 8
    assert "funny_score" in report["painter"]["traits"]
    for score_key, score in report["painter"]["traits"].items():
        assert score == workbench.spinda_trait_score(0x12345678, score_key)
    assert report["painter"]["svg"].startswith("<svg")


def test_mode_specific_scores_match_full_trait_scores() -> None:
    workbench = _load_workbench_module()

    for pid in (0x00000000, 0x12345678, 0x89ABCDEF, 0xFFFFFFFF):
        traits = workbench.spinda_traits(workbench.spinda_spots(pid))
        for score_key, expected_score in traits.items():
            assert workbench.spinda_trait_score(pid, score_key) == expected_score


def test_suggest_patterns_sorts_bounded_scan_results(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()

    payload = workbench.suggest_patterns(
        "funny",
        start_pid=0x12340000,
        scan_limit=128,
        count=8,
        phase3_dir=phase3_dir,
    )
    scores = [row["score"] for row in payload["results"]]

    assert payload["mode"] == "funny"
    assert payload["scan_limit"] == 128
    assert len(payload["results"]) == 8
    assert payload["elapsed_seconds"] > 0
    assert payload["pids_per_second"] > 0
    assert scores == sorted(scores, reverse=True)
    assert all(row["pid"].startswith("0x1234") for row in payload["results"])
    assert all("rarity" in row and "is_shiny" in row for row in payload["results"])


def test_suggest_patterns_uses_direct_pid_scoring_hot_loop(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()

    def fail_if_hot_loop_uses_center_tuple(pid: int):  # noqa: ANN202 - test sentinel.
        raise AssertionError(f"unexpected center tuple allocation for 0x{pid:08X}")

    workbench._spot_centers_from_pid = fail_if_hot_loop_uses_center_tuple
    payload = workbench.suggest_patterns(
        "centered",
        start_pid=0x00000000,
        scan_limit=16,
        count=4,
        phase3_dir=phase3_dir,
    )

    assert len(payload["results"]) == 4


def test_suggest_patterns_orders_by_unrounded_score(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    phase3_dir.mkdir()

    def close_score(pid: int, score_key: str) -> float:
        return 1.00049 if pid == 1 else 1.0004

    workbench._pid_score = close_score
    payload = workbench.suggest_patterns(
        "centered",
        start_pid=0,
        scan_limit=2,
        count=1,
        phase3_dir=phase3_dir,
    )

    assert payload["results"][0]["pid"] == "0x00000001"
    assert payload["results"][0]["score"] == 1.0


def test_workbench_api_reports_readiness_and_command_previews(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase3SpindaBlocks"
    tsv_dir = tmp_path / "TSVs"
    phase3_dir.mkdir()
    tsv_dir.mkdir()
    (phase3_dir / "0x0001.spinda80.zip").write_bytes(VALID_ZIP_BYTES)
    (tsv_dir / "TSV-0000-sid-00000.sav").write_bytes(b"a")
    config = workbench.WorkbenchConfig(
        phase3_dir=phase3_dir,
        tsv_dir=tsv_dir,
        target_phase3_lanes=1,
        display_url="http://127.0.0.1:8780/",
    )
    app = workbench.create_app(config)

    response = app.test_client().get("/api/status")
    payload = response.get_json()

    assert response.status_code == 200
    assert payload["phase3"]["complete_lanes"] == 1
    assert payload["phase3"]["completed_spindas"] == 65536
    assert payload["tsv"]["complete_saves"] == 1
    assert payload["readiness"]["ready_for_hatch_splitter"] is False
    assert "TSV save bank incomplete" in payload["readiness"]["blocked_by"]
    assert "phase3_zip_validator.py" in payload["commands"]["phase3_manifest"]

    pid_response = app.test_client().get("/api/pid/0x00010001")
    assert pid_response.status_code == 200
    assert pid_response.get_json()["matching_tsv"] == 0
    assert "painter" in pid_response.get_json()

    suggest_response = app.test_client().get("/api/suggest/centered?start=0x00000000&scan_limit=64&count=4")
    assert suggest_response.status_code == 200
    assert len(suggest_response.get_json()["results"]) == 4

    default_start_response = app.test_client().get("/api/suggest/centered?start=&scan_limit=2&count=1")
    assert default_start_response.status_code == 200
    assert default_start_response.get_json()["scan_limit"] == 2

    bad_suggest_response = app.test_client().get("/api/suggest/unknown?scan_limit=64")
    assert bad_suggest_response.status_code == 400
    assert "mode must be one of" in bad_suggest_response.get_json()["error"]


def test_command_previews_quote_paths_with_spaces(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Phase 3 Blocks"
    tsv_dir = tmp_path / "TSV Saves"
    hatch_dir = tmp_path / "Hatched ZIPs"

    commands = workbench.command_previews(
        workbench.WorkbenchConfig(phase3_dir=phase3_dir, tsv_dir=tsv_dir, hatch_output_dir=hatch_dir)
    )

    assert commands["workbench"].startswith(f"& '{sys.executable}' ")
    assert f"--phase3-dir '{phase3_dir}'" in commands["workbench"]
    assert f"--root '{phase3_dir}'" in commands["phase3_manifest"]
    assert f"--save-dir '{tsv_dir}'" in commands["tsv_party"]
    assert f"--input-dir '{phase3_dir}' --save-dir '{tsv_dir}'" in commands["hatch_splitter"]
    assert f"--shiny-output '{hatch_dir / 'spinda-hatched-shiny.zip'}'" in commands["hatch_splitter"]


def test_command_preview_quotes_single_quotes_for_powershell(tmp_path: Path) -> None:
    workbench = _load_workbench_module()
    phase3_dir = tmp_path / "Owner's Phase 3"

    commands = workbench.command_previews(workbench.WorkbenchConfig(phase3_dir=phase3_dir))

    assert "--phase3-dir '" + str(phase3_dir).replace("'", "''") + "'" in commands["workbench"]


def test_workbench_urls_bracket_ipv6_hosts() -> None:
    workbench = _load_workbench_module()

    assert workbench.workbench_urls("::1", 8780) == ("http://[::1]:8780/",)
    assert workbench.workbench_urls("[::1]", 8780) == ("http://[::1]:8780/",)
    assert workbench.workbench_urls("127.0.0.1", 8780) == ("http://127.0.0.1:8780/",)


def test_cli_numeric_options_are_range_checked() -> None:
    workbench = _load_workbench_module()

    args = workbench.parse_args(["--target-phase3-lanes", "0x10", "--sample-limit", "0", "--port", "65535"])

    assert args.target_phase3_lanes == 0x10
    assert args.sample_limit == 0
    assert args.port == 65_535
    with pytest.raises(argparse.ArgumentTypeError):
        workbench._target_phase3_lanes_arg("65537")
    with pytest.raises(argparse.ArgumentTypeError):
        workbench._sample_limit_arg("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        workbench._port_arg("0")


def test_zero_sample_limit_does_not_allocate_sample_keys() -> None:
    workbench = _load_workbench_module()
    samples: dict[str, list[str]] = {}

    workbench._sample_append(samples, "bad_names", "example", 0)

    assert samples == {}
