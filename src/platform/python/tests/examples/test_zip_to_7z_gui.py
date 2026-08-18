from __future__ import annotations

import importlib.util
import sys
import zipfile
from types import SimpleNamespace
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).absolute().parents[5]
SCRIPT_PATH = REPO_ROOT / "tools" / "spinda" / "zip_to_7z_gui" / "zip_to_7z_gui.py"
README_PATH = REPO_ROOT / "tools" / "spinda" / "zip_to_7z_gui" / "README.md"


def _load_module():
    module_name = "testable_zip_to_7z_gui"
    spec = importlib.util.spec_from_file_location(module_name, SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def _write_zip(path: Path, members: dict[str, bytes]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members.items():
            archive.writestr(name, data)


def test_find_zip_jobs_preserves_relative_layout_and_skips_existing(tmp_path: Path) -> None:
    module = _load_module()
    input_dir = tmp_path / "in"
    output_dir = tmp_path / "out"
    _write_zip(input_dir / "root.zip", {"a.txt": b"a"})
    _write_zip(input_dir / "sub" / "child.ZIP", {"b.txt": b"b"})
    (input_dir / "not-zip.txt").write_text("ignore", encoding="utf-8")
    (output_dir / "root.7z").parent.mkdir(parents=True)
    (output_dir / "root.7z").write_bytes(b"existing")

    jobs = module.find_zip_jobs(input_dir, output_dir, recursive=True, overwrite=False)

    assert [(job.zip_path.name, job.output_path.relative_to(output_dir)) for job in jobs] == [
        ("child.ZIP", Path("sub") / "child.7z"),
    ]


def test_find_zip_jobs_ignores_output_folder_inside_input(tmp_path: Path) -> None:
    module = _load_module()
    input_dir = tmp_path / "in"
    output_dir = input_dir / "converted"
    _write_zip(input_dir / "source.zip", {"a.txt": b"a"})
    _write_zip(output_dir / "already-output.zip", {"b.txt": b"b"})

    jobs = module.find_zip_jobs(input_dir, output_dir, recursive=True, overwrite=True)

    assert [job.zip_path.name for job in jobs] == ["source.zip"]
    assert jobs[0].output_path == output_dir / "source.7z"


def test_archive_command_uses_7z_lzma_family_flags(tmp_path: Path) -> None:
    module = _load_module()
    command = module.build_archive_command(
        tmp_path / "7z.exe",
        tmp_path / "out.7z.tmp",
        tmp_path / "files.txt",
        module.SevenZipSettings(method="LZMA", level=9, dictionary="64m", solid=True, threads="on"),
    )

    assert command[:2] == [str(tmp_path / "7z.exe"), "a"]
    assert "-t7z" in command
    assert "-m0=lzma" in command
    assert "-mx=9" in command
    assert "-md=64m" in command
    assert "-ms=on" in command
    assert "-mmt=on" in command
    assert command[-1] == f"@{tmp_path / 'files.txt'}"


def test_inspect_zip_returns_metadata_and_keeps_legacy_entry_count(tmp_path: Path) -> None:
    module = _load_module()
    archive_path = tmp_path / "input.zip"
    _write_zip(
        archive_path,
        {
            "folder/a.txt": b"AAAA",
            "folder/b.txt": b"BBBBBB",
        },
    )

    metadata = module.inspect_zip(archive_path)

    assert metadata.entry_count == 2
    assert metadata.uncompressed_bytes == 10
    assert metadata.compressed_bytes > 0
    assert module.check_zip_has_entries(archive_path) == 2


def test_zip_preflight_rejects_traversal_and_absolute_members(tmp_path: Path) -> None:
    module = _load_module()
    unsafe_names = ["../escape.txt", "/absolute.txt", r"C:\absolute.txt", "safe/../../escape.txt"]

    for index, name in enumerate(unsafe_names):
        archive_path = tmp_path / f"unsafe-{index}.zip"
        _write_zip(archive_path, {name: b"x"})

        with pytest.raises(module.ConversionError):
            module.inspect_zip(archive_path)


def test_zip_preflight_rejects_newline_members(tmp_path: Path) -> None:
    module = _load_module()
    archive_path = tmp_path / "newline.zip"
    _write_zip(archive_path, {"bad\nname.txt": b"x"})

    with pytest.raises(module.ConversionError, match="newline"):
        module.inspect_zip(archive_path)


def test_listfile_uses_top_level_entries_without_polluting_archive(tmp_path: Path) -> None:
    module = _load_module()
    extracted = tmp_path / "extract"
    extracted.mkdir()
    (extracted / "root.txt").write_text("root", encoding="utf-8")
    (extracted / "folder").mkdir()
    (extracted / "folder" / "nested.txt").write_text("nested", encoding="utf-8")
    listfile = tmp_path / "files.txt"

    count = module.write_7z_listfile(extracted, listfile)

    assert count == 2
    assert listfile.read_text(encoding="utf-8").splitlines() == ["folder", "root.txt"]


def test_listfile_rejects_newline_top_level_entries(tmp_path: Path) -> None:
    module = _load_module()
    extracted = SimpleNamespace(iterdir=lambda: [SimpleNamespace(name="bad\nname.txt")])

    with pytest.raises(module.ConversionError, match="newline"):
        module.write_7z_listfile(extracted, tmp_path / "files.txt")


def test_check_zip_has_entries_rejects_empty_zip(tmp_path: Path) -> None:
    module = _load_module()
    empty_zip = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty_zip, "w"):
        pass

    with pytest.raises(module.ConversionError, match="empty ZIP"):
        module.check_zip_has_entries(empty_zip)


def test_compression_ratio_formats_output_share() -> None:
    module = _load_module()

    assert module.compression_ratio(200, 50) == "25.0%"
    assert module.compression_ratio(0, 50) == "n/a"


def test_license_docs_describe_external_7zip_boundary() -> None:
    readme = README_PATH.read_text(encoding="utf-8")
    licenses = (REPO_ROOT / "markdown-files" / "LICENSES.md").read_text(encoding="utf-8")

    assert "Python standard-library modules" in readme
    assert "does not vendor 7-Zip code" in readme
    assert "GNU LGPL" in readme
    assert "unRAR license restriction" in readme
    assert "rejects unsafe ZIP member paths" in readme
    assert "7-Zip command-line executable" in licenses
