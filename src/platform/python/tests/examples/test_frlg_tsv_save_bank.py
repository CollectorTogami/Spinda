from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[5]
SCRIPT_DIR = REPO_ROOT / "doc" / "python-examples" / "frlg-tsv-save-bank"
SCRIPT_PATH = SCRIPT_DIR / "Build-FRLG-TSV-Save-Bank.py"
COMMON_PATH = SCRIPT_DIR / "frlg_tsv_common.py"


def _load_common():
    module_name = "testable_frlg_tsv_common"
    spec = importlib.util.spec_from_file_location(module_name, COMMON_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _load_script():
    module_name = "testable_frlg_tsv_save_bank"
    sys.path.insert(0, str(SCRIPT_DIR))
    sys.path.insert(0, str(REPO_ROOT / "doc" / "python-examples"))
    try:
        spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
        assert spec is not None
        assert spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        return module
    finally:
        for path in (str(REPO_ROOT / "doc" / "python-examples"), str(SCRIPT_DIR)):
            while path in sys.path:
                sys.path.remove(path)


@pytest.fixture
def common():
    return _load_common()


@pytest.fixture
def script():
    return _load_script()


class _MemoryView:
    def __init__(self):
        self.values: dict[int, int] = {}

    def __getitem__(self, address: int) -> int:
        return self.values.get(int(address), 0)

    def __setitem__(self, address: int, value: int) -> None:
        self.values[int(address)] = int(value)


class _Memory:
    def __init__(self):
        self.u16 = _MemoryView()
        self.u32 = _MemoryView()


class _FakeCore:
    def __init__(self, common):
        self.memory = _Memory()
        self.calls: list[tuple[str, int | str, int | None]] = []
        self.scratch_saved = False
        self.exported: list[Path] = []
        self.memory.u16[common.INITIAL_TID_MIRROR_ADDR] = 0x1234
        self.memory.u32[common.GRNG_VALUE_ADDR] = 0x89ABCDEF

    def save_scratch_state(self):
        self.calls.append(("save_scratch_state", 0, None))
        self.scratch_saved = True

    def load_scratch_state(self):
        self.calls.append(("load_scratch_state", 0, None))
        assert self.scratch_saved

    def run_frames_with_keys(self, mask: int, frames: int):
        self.calls.append(("run_frames_with_keys", int(mask), int(frames)))

    def set_keys(self, *args, **kwargs):
        self.calls.append(("set_keys", int(kwargs.get("raw", 0)), None))

    def export_save_file(self, path: Path):
        path = Path(path)
        self.exported.append(path)
        path.write_bytes(b"SAVE")


def test_tsv_and_psv_formulas(common) -> None:
    assert common.tsv_from_tid_sid(0x1234, 0x5678) == ((0x1234 ^ 0x5678) >> 3)
    assert common.psv_from_pid(0xABCD1234) == ((0x1234 ^ 0xABCD) >> 3)


def test_acceptable_sids_for_tsv(common) -> None:
    sids = common.acceptable_sids_for_tsv(0x1234, 0x01A2)

    assert len(sids) == 8
    assert len(set(sids)) == 8
    assert all(common.tsv_from_tid_sid(0x1234, sid) == 0x01A2 for sid in sids)


def test_wait_plan_covers_all_tsvs_and_records_first_hit(common) -> None:
    plan = common.build_wait_plan(tid=0x1234, start_rng=0x89ABCDEF)

    assert len(plan) == common.TSV_COUNT
    assert [entry.tsv for entry in plan] == list(range(common.TSV_COUNT))
    assert all(entry.predicted_tsv == entry.tsv for entry in plan)
    assert all(common.tsv_from_tid_sid(entry.predicted_tid, entry.predicted_sid) == entry.tsv for entry in plan)
    assert len({entry.tsv for entry in plan}) == common.TSV_COUNT


def test_wait_plan_uses_commit_offset_and_neutral_frame_stride(common) -> None:
    plan = common.build_wait_plan(
        tid=0x0000,
        start_rng=0x12345678,
        sid_commit_offset=5,
        rng_advances_per_neutral_frame=2,
        target_count=4,
    )

    assert min(entry.rng_advance for entry in plan) >= 5
    assert all((entry.rng_advance - 5) % 2 == 0 for entry in plan)


def test_wait_plan_max_advances_failure(common) -> None:
    with pytest.raises(RuntimeError, match="wait-plan search stopped"):
        common.build_wait_plan(
            tid=0x1234,
            start_rng=0x89ABCDEF,
            target_count=common.TSV_COUNT,
            max_advances=3,
        )


def test_status_json_marks_hit_and_errors(common, tmp_path: Path) -> None:
    plan = common.build_wait_plan(tid=0x1234, start_rng=0x89ABCDEF, target_count=3)
    status = common.new_status(
        plan=plan,
        tid=0x1234,
        start_rng=0x89ABCDEF,
        sid_commit_offset=1,
        rng_advances_per_neutral_frame=1,
    )
    target = plan[0]

    common.mark_status_hit(
        status,
        tsv=target.tsv,
        tid=0x1234,
        sid=target.predicted_sid,
        save_path=tmp_path / "save.sav",
        save_sha1="ABC",
    )
    common.mark_status_error(status, tsv=plan[1].tsv, error="bad branch")

    summary = common.status_summary(status)
    assert summary == {"complete_tsvs": 1, "target_tsvs": 3, "errors": 1}

    path = tmp_path / "status.json"
    common.write_json_atomic(path, status)
    loaded = common.read_json(path)
    assert loaded["complete_tsvs"] == 1
    assert not path.with_name("status.json.tmp").exists()


def test_memory_helpers_read_tid_rng_and_final_saveblock2(common) -> None:
    core = _FakeCore(common)
    saveblock2 = 0x02037000
    core.memory.u32[common.GSAVEBLOCK2_PTR_ADDR] = saveblock2
    core.memory.u16[saveblock2 + common.PLAYER_TRAINER_ID_OFFSET] = 0xCAFE
    core.memory.u16[saveblock2 + common.PLAYER_TRAINER_ID_OFFSET + 2] = 0xBEEF

    assert common.read_tid_from_initial_mirror(core) == 0x1234
    assert common.read_rng_state(core) == 0x89ABCDEF
    assert common.read_trainer_id_from_saveblock2(core) == (0xCAFE, 0xBEEF)


def test_parser_defaults_are_frlg_only(script) -> None:
    config = script.config_from_args(
        script.build_parser().parse_args(["--dry-plan", "--tid", "0x1234", "--start-rng", "0x89ABCDEF"])
    )

    assert config.dry_plan is True
    assert config.sid_commit_offset == 1
    assert config.rng_advances_per_neutral_frame == 1
    assert config.output_dir == Path(__file__).resolve().parents[5] / "TSVs"


def test_save_path_uses_decimal_tsv_and_sid(script, tmp_path: Path) -> None:
    assert script.save_path_for_tsv(tmp_path, 860, 6883) == tmp_path / "TSV-0860-sid-06883.sav"


def test_tsv_selection_supports_values_and_ranges(script) -> None:
    assert script.parse_tsv_selection(["0x0001", "0x0003-0x0005"]) == [1, 3, 4, 5]
    with pytest.raises(ValueError, match="out of range"):
        script.parse_tsv_selection(["0x2000"])


def test_dry_plan_writes_wait_plan_and_status(script, tmp_path: Path) -> None:
    config = script.TsvSaveBankConfig(
        output_dir=tmp_path,
        post_sid_tape=None,
        tid=0x1234,
        start_rng=0x89ABCDEF,
        sid_commit_offset=1,
        rng_advances_per_neutral_frame=1,
        commit_button="A",
        commit_press_frames=1,
        post_commit_verify_frames=1,
        limit=None,
        only_tsvs=(),
        resume=False,
        overwrite=False,
        trust_predicted_sid=False,
        dry_plan=True,
        max_advances=None,
    )

    result = script.generate_tsv_save_bank(config)

    assert result["mode"] == "dry-plan"
    assert Path(result["wait_plan_path"]).is_file()
    assert Path(result["status_path"]).is_file()
    status = script.common.read_json(Path(result["status_path"]))
    assert status["target_tsvs"] == script.common.TSV_COUNT


def test_runtime_helpers_capture_restore_commit_and_export(script, common, tmp_path: Path) -> None:
    core = _FakeCore(common)
    config = script.TsvSaveBankConfig(
        output_dir=tmp_path,
        post_sid_tape=None,
        tid=0x1234,
        start_rng=0x89ABCDEF,
        sid_commit_offset=1,
        rng_advances_per_neutral_frame=1,
        commit_button="A",
        commit_press_frames=2,
        post_commit_verify_frames=3,
        limit=None,
        only_tsvs=(),
        resume=False,
        overwrite=True,
        trust_predicted_sid=True,
        dry_plan=False,
        max_advances=None,
    )

    script.capture_branch_state(core)
    script.restore_branch_state(core)
    script.commit_sid(config, core)
    digest = script.export_save_atomic(core, tmp_path / "tsv-0x0001.sav")

    assert ("save_scratch_state", 0, None) in core.calls
    assert ("load_scratch_state", 0, None) in core.calls
    assert any(call == ("run_frames_with_keys", 1, 2) for call in core.calls)
    assert any(call == ("run_frames_with_keys", 0, 3) for call in core.calls)
    assert (tmp_path / "tsv-0x0001.sav").read_bytes() == b"SAVE"
    assert not (tmp_path / "tsv-0x0001.sav.tmp").exists()
    assert digest
