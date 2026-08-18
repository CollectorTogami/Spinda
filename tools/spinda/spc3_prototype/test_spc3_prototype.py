#!/usr/bin/env python3
"""Regression tests for the SPC3 CPU prototype.

The generated good lane is intentionally full-size: 65,536 entries forces the
same ZIP64 central-directory path as production Phase 3 lane ZIPs. All test
artifacts live in a TemporaryDirectory and are removed by Python after the run.
"""

from __future__ import annotations

import importlib.util
import json
import os
import struct
import subprocess
import sys
import tempfile
import warnings
import zlib
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZIP_STORED, ZipFile


ROOT = Path(__file__).resolve().parents[3]
TOOL_DIR = ROOT / "tools" / "spinda" / "spc3_prototype"
BUILD = TOOL_DIR / "build_spc3_prototype.bat"
EXE = TOOL_DIR / "spc3_prototype.exe"
REPORT_TOOLS = TOOL_DIR / "spc3_report_tools.py"
GUI = ROOT / "tools" / "spinda" / "spc3_gui" / "spc3_gui.py"
RECORD_SIZE = 80
EXPECTED_RECORDS = 0x10000
LANE_ID = 0x00A5
SPC3_PREDICTOR_SIZE_OFFSET = 40
SPC3_TABLE_OFFSET_OFFSET = 48
SPC3_DATA_OFFSET_OFFSET = 64
SPC3_TABLE_LEVEL_OFFSET = 4
SPC3_TABLE_FLAGS_OFFSET = 12
SPC3_TABLE_ORIGINAL_CRC_OFFSET = 40
SPC3_TABLE_STREAM_OFFSET_OFFSET = 56
SPC3_TABLE_UNCOMPRESSED_SIZE_OFFSET = 72
SPC3_TYPED_SUBSTREAM_ENTRY_SIZE = 32
SPC3_TYPED_VALUES_RAW_SIZE_OFFSET = 2 * SPC3_TYPED_SUBSTREAM_ENTRY_SIZE + 24

BLOCK_POSITION: tuple[tuple[int, int, int, int], ...] = (
    (0, 1, 2, 3),
    (0, 1, 3, 2),
    (0, 2, 1, 3),
    (0, 3, 1, 2),
    (0, 2, 3, 1),
    (0, 3, 2, 1),
    (1, 0, 2, 3),
    (1, 0, 3, 2),
    (2, 0, 1, 3),
    (3, 0, 1, 2),
    (2, 0, 3, 1),
    (3, 0, 2, 1),
    (1, 2, 0, 3),
    (1, 3, 0, 2),
    (2, 1, 0, 3),
    (3, 1, 0, 2),
    (2, 3, 0, 1),
    (3, 2, 0, 1),
    (1, 2, 3, 0),
    (1, 3, 2, 0),
    (2, 1, 3, 0),
    (3, 1, 2, 0),
    (2, 3, 1, 0),
    (3, 2, 1, 0),
)

BLOCK_POSITION_INVERT_SELECTOR = (
    0,
    1,
    2,
    4,
    3,
    5,
    6,
    7,
    12,
    18,
    13,
    19,
    8,
    10,
    14,
    20,
    16,
    22,
    9,
    11,
    15,
    21,
    17,
    23,
)


def run_command(
    args: list[str],
    *,
    check: bool = True,
    env_overlay: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PATH"] = r"C:\msys64\mingw64\bin;" + env.get("PATH", "")
    if env_overlay:
        env.update(env_overlay)
    result = subprocess.run(
        args,
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if check and result.returncode != 0:
        raise AssertionError(
            f"command failed {result.returncode}: {' '.join(args)}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
    return result


def run_server_command(process: subprocess.Popen[str], args: list[str]) -> tuple[int, str]:
    assert process.stdin is not None
    assert process.stdout is not None
    line = "RUN" + "".join("\t" + arg.encode("utf-8").hex() for arg in args) + "\n"
    process.stdin.write(line)
    process.stdin.flush()
    output: list[str] = []
    while True:
        text = process.stdout.readline()
        if text == "":
            raise AssertionError("SPC3 server exited before completion marker")
        if text.startswith("SPC3_SERVER_DONE exit_code="):
            return int(text.rsplit("=", 1)[1]), "".join(output)
        output.append(text)


def test_server_mode_reuses_one_process() -> None:
    env = os.environ.copy()
    env["PATH"] = r"C:\msys64\mingw64\bin;" + env.get("PATH", "")
    process = subprocess.Popen(
        [str(EXE), "--server"],
        cwd=ROOT,
        env=env,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    try:
        first_code, first_output = run_server_command(process, ["--self-test"])
        help_code, help_output = run_server_command(process, ["--help"])
        second_code, second_output = run_server_command(process, ["--self-test"])
        second_help_code, second_help_output = run_server_command(process, ["--help"])
        assert first_code == 0
        assert help_code == 0
        assert second_code == 0
        assert second_help_code == 0
        assert "self-test ok" in first_output
        assert "Usage:" in help_output
        assert "self-test ok" in second_output
        assert "Usage:" in second_help_output
    finally:
        if process.stdin is not None:
            try:
                process.stdin.write("STOP\n")
                process.stdin.flush()
            except BrokenPipeError:
                pass
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


def load_gui_module() -> object:
    spec = importlib.util.spec_from_file_location("spc3_gui_under_test", GUI)
    if spec is None or spec.loader is None:
        raise AssertionError("could not load SPC3 GUI module")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_u32(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "little")


def read_u64(data: bytes | bytearray, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 8], "little")


def write_u16(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 2] = int(value & 0xFFFF).to_bytes(2, "little")


def write_u32(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 4] = int(value & 0xFFFFFFFF).to_bytes(4, "little")


def write_u64(data: bytearray, offset: int, value: int) -> None:
    data[offset : offset + 8] = int(value & 0xFFFFFFFFFFFFFFFF).to_bytes(8, "little")


def mutate_spc3_file(source: Path, target: Path, mutator) -> Path:
    data = bytearray(source.read_bytes())
    mutator(data)
    target.write_bytes(data)
    return target


def expect_command_failure(args: list[str], expected_text: str) -> subprocess.CompletedProcess[str]:
    result = run_command(args, check=False)
    assert result.returncode != 0, result.stdout + result.stderr
    combined = result.stdout + result.stderr
    assert expected_text in combined, combined
    return result


def cuda_available() -> bool:
    try:
        result = subprocess.run(
            ["nvidia-smi"],
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except FileNotFoundError:
        return False
    return result.returncode == 0


def pk3_checksum(record: bytes | bytearray) -> int:
    total = 0
    for offset in range(0x20, 0x50, 2):
        total = (total + int.from_bytes(record[offset : offset + 2], "little")) & 0xFFFF
    return total


def shuffle_blocks(record: bytearray, selector: int) -> None:
    order = BLOCK_POSITION[selector]
    if order == (0, 1, 2, 3):
        return
    source = bytes(record[0x20:0x50])
    for block, source_block in enumerate(order):
        dst = 0x20 + block * 12
        src = source_block * 12
        record[dst : dst + 12] = source[src : src + 12]


def xor_gen3_data(record: bytearray, seed: int) -> None:
    for offset in range(0x20, 0x50, 4):
        write_u32(record, offset, read_u32(record, offset) ^ seed)


def predictor_iv32(upper: int) -> int:
    return ((upper * 0x045D9F3B) ^ 0xA5A5C3C3) & 0xFFFFFFFF


def actual_iv32(upper: int, exception_every: int = 0) -> int:
    iv32 = predictor_iv32(upper)
    if exception_every and upper % exception_every == 0:
        return iv32 ^ 0x01020304
    return iv32


def expected_exception_stream_parts(exception_every: int) -> tuple[bytes, bytes]:
    bitmap = bytearray(EXPECTED_RECORDS // 8)
    values = bytearray()
    for upper in range(EXPECTED_RECORDS):
        predicted = predictor_iv32(upper)
        actual = actual_iv32(upper, exception_every)
        if predicted != actual:
            bitmap[upper // 8] |= 1 << (upper % 8)
            values += (predicted ^ actual).to_bytes(4, "little")
    return bytes(bitmap), bytes(values)


def expected_exception_values_zlib_size(exception_every: int, level: int) -> int:
    _, values = expected_exception_stream_parts(exception_every)
    return len(zlib.compress(values, level))


def make_encrypted_record(
    lane: int,
    upper: int,
    exception_every: int = 0,
    corrupt_checksum: bool = False,
    template_tweak: bool = False,
) -> bytes:
    pid = (upper << 16) | lane
    record = bytearray(RECORD_SIZE)
    write_u32(record, 0x00, pid)
    write_u32(record, 0x04, 0x12345678)

    # Constant logical payload except IV32. This tests template detection and checksum rebuild.
    for offset in range(0x20, 0x50):
        record[offset] = (offset * 7 + 0x31) & 0xFF
    write_u16(record, 0x20, 308)
    if template_tweak:
        record[0x30] ^= 0x40
    write_u32(record, 0x48, actual_iv32(upper, exception_every))
    checksum = pk3_checksum(record)
    if corrupt_checksum:
        checksum ^= 0x0001
    write_u16(record, 0x1C, checksum)

    selector = BLOCK_POSITION_INVERT_SELECTOR[pid % 24]
    shuffle_blocks(record, selector)
    xor_gen3_data(record, pid ^ read_u32(record, 0x04))
    return bytes(record)


def write_predictor(path: Path) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write('{"iv32_by_pid_second_half_hex": [\n')
        for upper in range(EXPECTED_RECORDS):
            comma = "," if upper + 1 != EXPECTED_RECORDS else ""
            handle.write(f'  "{predictor_iv32(upper):08X}"{comma}\n')
        handle.write("]}\n")


def write_short_predictor_with_later_strings(path: Path) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write('{"iv32_by_pid_second_half_hex": [\n')
        handle.write(f'  "{predictor_iv32(0):08X}"\n')
        handle.write('], "unrelated": [\n')
        for upper in range(1, 32):
            comma = "," if upper != 31 else ""
            handle.write(f'  "{predictor_iv32(upper):08X}"{comma}\n')
        handle.write("]}\n")


def write_extra_value_predictor(path: Path) -> None:
    with path.open("w", encoding="ascii", newline="\n") as handle:
        handle.write('{"iv32_by_pid_second_half_hex": [\n')
        for upper in range(EXPECTED_RECORDS + 1):
            comma = "," if upper != EXPECTED_RECORDS else ""
            handle.write(f'  "{predictor_iv32(upper & 0xFFFF):08X}"{comma}\n')
        handle.write("]}\n")


def write_missing_colon_predictor(path: Path) -> None:
    path.write_text('{"iv32_by_pid_second_half_hex" ["00000000"]}\n', encoding="ascii")


def write_bad_hex_predictor(path: Path) -> None:
    path.write_text('{"iv32_by_pid_second_half_hex": ["ZZZZZZZZ"]}\n', encoding="ascii")


def write_full_good_zip(
    root: Path,
    *,
    compression: int = ZIP_DEFLATED,
    exception_every: int = 0,
    lane_id: int = LANE_ID,
) -> None:
    path = root / f"0x{lane_id:04X}.spinda80.zip"
    kwargs = {"compression": compression, "allowZip64": True}
    if compression == ZIP_DEFLATED:
        kwargs["compresslevel"] = 1
    with ZipFile(path, "w", **kwargs) as archive:
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | lane_id
            archive.writestr(f"0x{pid:08X}.pk3", make_encrypted_record(lane_id, upper, exception_every))


def read_concatenated_lane_payload(root: Path, lane_id: int = LANE_ID) -> bytes:
    path = root / f"0x{lane_id:04X}.spinda80.zip"
    payload = bytearray()
    with ZipFile(path, "r") as archive:
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | lane_id
            payload += archive.read(f"0x{pid:08X}.pk3")
    return bytes(payload)


def assert_lane_zip_contains_encrypted_payload(root: Path, lane_id: int, expected_payload: bytes) -> None:
    path = root / f"0x{lane_id:04X}.spinda80.zip"
    assert path.is_file()
    payload = bytearray()
    with ZipFile(path, "r") as archive:
        assert len(archive.infolist()) == EXPECTED_RECORDS
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | lane_id
            info = archive.getinfo(f"0x{pid:08X}.pk3")
            assert info.compress_type == ZIP_STORED
            assert info.file_size == RECORD_SIZE
            payload += archive.read(info)
    assert bytes(payload) == expected_payload


def write_full_content_pid_mismatch_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    bad_upper = 1234
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | LANE_ID
            if upper == bad_upper:
                archive.writestr(f"0x{pid:08X}.pk3", make_encrypted_record((LANE_ID + 1) & 0xFFFF, upper))
            else:
                archive.writestr(f"0x{pid:08X}.pk3", make_encrypted_record(LANE_ID, upper))


def write_full_bad_checksum_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    bad_upper = 4321
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | LANE_ID
            archive.writestr(
                f"0x{pid:08X}.pk3",
                make_encrypted_record(LANE_ID, upper, corrupt_checksum=(upper == bad_upper)),
            )


def write_full_template_mismatch_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    bad_upper = 9876
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        for upper in range(EXPECTED_RECORDS):
            pid = (upper << 16) | LANE_ID
            archive.writestr(
                f"0x{pid:08X}.pk3",
                make_encrypted_record(LANE_ID, upper, template_tweak=(upper == bad_upper)),
            )


def write_bad_incomplete_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    record = bytearray(RECORD_SIZE)
    write_u32(record, 0, LANE_ID)
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(f"0x{LANE_ID:08X}.pk3", bytes(record))


def write_bad_duplicate_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3"
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
            archive.writestr(name, make_encrypted_record(LANE_ID, 0))
            archive.writestr(name, make_encrypted_record(LANE_ID, 0))


def write_single_entry_manual_zip(root: Path, *, name_pid: int, method: int = ZIP_STORED, flags: int = 0) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{name_pid:08X}.pk3".encode("ascii")
    upper = (name_pid >> 16) & 0xFFFF
    record = make_encrypted_record(LANE_ID, upper)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, method, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        flags,
        method,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_local_header_name_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    record = make_encrypted_record(LANE_ID, 0)
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(f"0x{LANE_ID:08X}.pk3", record)

    data = bytearray(path.read_bytes())
    assert data[:4] == b"PK\x03\x04"
    name_len = int.from_bytes(data[26:28], "little")
    assert name_len == len(f"0x{LANE_ID:08X}.pk3")
    # Mutate only the local header filename. Central directory still names the
    # entry correctly, so this catches central/local trust bugs.
    data[30 + 9] = ord("6") if data[30 + 9] != ord("6") else ord("7")
    path.write_bytes(data)


def deflate_raw(data: bytes) -> bytes:
    compressor = zlib.compressobj(level=1, wbits=-zlib.MAX_WBITS)
    return compressor.compress(data) + compressor.flush(zlib.Z_FINISH)


def write_bad_trailing_deflate_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    compressed = deflate_raw(record) + b"JUNK"
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 8, 0, 0, crc, len(compressed), len(record), len(name), 0)
    local += name
    local += compressed

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        8,
        0,
        0,
        crc,
        len(compressed),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)

    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        1,
        1,
        central_size,
        central_offset,
        0,
    )
    path.write_bytes(bytes(local + central + eocd))


def write_bad_crc_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    wrong_crc = (zlib.crc32(record) ^ 0x00000001) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, wrong_crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        wrong_crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_local_method_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    record = make_encrypted_record(LANE_ID, 0)
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(f"0x{LANE_ID:08X}.pk3", record)

    data = bytearray(path.read_bytes())
    assert data[:4] == b"PK\x03\x04"
    data[8:10] = (ZIP_DEFLATED).to_bytes(2, "little")
    path.write_bytes(data)


def write_bad_local_flags_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    record = make_encrypted_record(LANE_ID, 0)
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(f"0x{LANE_ID:08X}.pk3", record)

    data = bytearray(path.read_bytes())
    assert data[:4] == b"PK\x03\x04"
    data[6:8] = (0x0800).to_bytes(2, "little")
    path.write_bytes(data)


def write_bad_local_size_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    record = make_encrypted_record(LANE_ID, 0)
    with ZipFile(path, "w", compression=ZIP_STORED, allowZip64=True) as archive:
        archive.writestr(f"0x{LANE_ID:08X}.pk3", record)

    data = bytearray(path.read_bytes())
    assert data[:4] == b"PK\x03\x04"
    data[22:26] = (RECORD_SIZE - 1).to_bytes(4, "little")
    path.write_bytes(data)


def write_bad_data_descriptor_flag_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF
    flags = 0x0008

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, flags, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        flags,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_eocd_comment_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    comment = b"spc3 eocd comment"
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, len(comment))
    path.write_bytes(bytes(local + central + eocd + comment))


def write_bad_central_trailing_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central) + 1
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + b"X" + eocd))


def write_bad_short_zip64_extra_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    # Both central sizes claim ZIP64, but the extra field contains only the
    # uncompressed size. Parser must reject before trusting local metadata.
    extra = struct.pack("<HHQ", 0x0001, 8, len(record))
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        45,
        45,
        0,
        0,
        0,
        0,
        crc,
        0xFFFFFFFF,
        0xFFFFFFFF,
        len(name),
        len(extra),
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central += extra
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_valid_local_zip64_size_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF
    local_extra = struct.pack("<HHQQ", 0x0001, 16, len(record), len(record))

    local = bytearray()
    local += struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        45,
        0,
        0,
        0,
        0,
        crc,
        0xFFFFFFFF,
        0xFFFFFFFF,
        len(name),
        len(local_extra),
    )
    local += name
    local += local_extra
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        45,
        45,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_central_truncated_extra_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record

    central_offset = len(local)
    extra = struct.pack("<HHB", 0xCAFE, 8, 0)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        len(extra),
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central += extra
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_local_truncated_extra_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF
    local_extra = struct.pack("<HHB", 0xCAFE, 8, 0)

    local = bytearray()
    local += struct.pack(
        "<IHHHHHIIIHH",
        0x04034B50,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        len(local_extra),
    )
    local += name
    local += local_extra
    local += record

    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_multidisk_eocd_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    name = f"0x{LANE_ID:08X}.pk3".encode("ascii")
    record = make_encrypted_record(LANE_ID, 0)
    crc = zlib.crc32(record) & 0xFFFFFFFF

    local = bytearray()
    local += struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc, len(record), len(record), len(name), 0)
    local += name
    local += record
    central_offset = len(local)
    central = bytearray()
    central += struct.pack(
        "<IHHHHHHIIIHHHHHII",
        0x02014B50,
        20,
        20,
        0,
        0,
        0,
        0,
        crc,
        len(record),
        len(record),
        len(name),
        0,
        0,
        0,
        0,
        0,
        0,
    )
    central += name
    central_size = len(central)
    eocd = struct.pack("<IHHHHIIH", 0x06054B50, 1, 0, 1, 1, central_size, central_offset, 0)
    path.write_bytes(bytes(local + central + eocd))


def write_bad_zip64_multidisk_locator_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 1, 0, 2)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path.write_bytes(zip64_eocd + locator + eocd)


def write_bad_zip64_eocd_size_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        1000,
        45,
        45,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 0, 0, 1)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path.write_bytes(zip64_eocd + locator + eocd)


def write_bad_zip64_locator_gap_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    zip64_eocd = struct.pack(
        "<IQHHIIQQQQ",
        0x06064B50,
        44,
        45,
        45,
        0,
        0,
        0,
        0,
        0,
        0,
    )
    locator = struct.pack("<IIQI", 0x07064B50, 0, 0, 1)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path.write_bytes(zip64_eocd + b"GAP!" + locator + eocd)


def write_bad_zip64_locator_zip(root: Path) -> None:
    path = root / f"0x{LANE_ID:04X}.spinda80.zip"
    locator = struct.pack("<IIQI", 0x07064B50, 0, 0xFFFFFFFFFFFFFFFF, 1)
    eocd = struct.pack(
        "<IHHHHIIH",
        0x06054B50,
        0,
        0,
        0xFFFF,
        0xFFFF,
        0xFFFFFFFF,
        0xFFFFFFFF,
        0,
    )
    path.write_bytes(locator + eocd)


def test_full_good_lane(work: Path) -> None:
    root = work / "good"
    root.mkdir()
    predictor = work / "predictor.json"
    report = work / "good_report.json"
    write_predictor(predictor)
    write_full_good_zip(root, compression=ZIP_DEFLATED, exception_every=4096)

    run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
        ]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    totals = data["totals"]
    assert data["config"]["hotloop_backend"] == "x86_64_asm"
    assert totals["audit_failed"] is False
    assert totals["records_processed"] == EXPECTED_RECORDS
    assert totals["checksum_failures"] == 0
    assert totals["content_pid_mismatches"] == 0
    assert totals["template_mismatches"] == 0
    assert totals["predictor_exceptions"] == 16
    assert totals["predictor_roundtrip_mismatches"] == 0
    assert totals["rebuild_mismatches"] == 0
    bitmap, values = expected_exception_stream_parts(4096)
    assert totals["predictor_exception_raw_bytes"] == len(bitmap) + len(values)
    assert expected_exception_values_zlib_size(4096, 1) < totals["exception_stream_zlib1_bytes"] <= len(bitmap) + len(values)
    assert expected_exception_values_zlib_size(4096, 9) < totals["exception_stream_zlib9_bytes"] <= len(bitmap) + len(values)
    assert totals["exception_stream_zlib9_bytes"] <= totals["exception_stream_zlib1_bytes"]
    assert data["lanes"][0]["zip64"] is True
    assert data["lanes"][0]["deflate_entries"] == EXPECTED_RECORDS


def test_full_stored_lane(work: Path) -> None:
    root = work / "stored"
    root.mkdir()
    report = work / "stored_report.json"
    write_full_good_zip(root, compression=ZIP_STORED)

    run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--no-predictor",
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ]
    )
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["config"]["predictor_loaded"] is False
    assert data["totals"]["audit_failed"] is False
    assert data["totals"]["records_processed"] == EXPECTED_RECORDS
    assert data["totals"]["predictor_matches"] == 0
    assert data["totals"]["predictor_exceptions"] == 0
    assert data["totals"]["predictor_exception_raw_bytes"] == 0
    assert data["totals"]["exception_stream_zlib1_bytes"] == 0
    assert data["totals"]["exception_stream_zlib9_bytes"] == 0
    assert data["totals"]["rebuild_mismatches"] == 0
    assert data["lanes"][0]["stored_entries"] == EXPECTED_RECORDS


def test_spc3_pack_unpack_verify_and_bench_modes(work: Path) -> None:
    root = work / "spc3_modes"
    root.mkdir()
    predictor = work / "spc3_modes_predictor.json"
    write_predictor(predictor)
    write_full_good_zip(root, compression=ZIP_STORED, exception_every=4096)
    expected_payload = read_concatenated_lane_payload(root)
    packed_spc3: dict[int, Path] = {}

    for level in range(4):
        spc3 = work / f"lane_level{level}.spc3"
        pack_report = work / f"pack_level{level}.json"
        verify_report = work / f"verify_level{level}.json"
        unpack_report = work / f"unpack_level{level}.json"
        unpack_dir = work / f"unpack_level{level}"

        run_command(
            [
                str(EXE),
                "--mode",
                "pack",
                "--root",
                str(root),
                "--predictor",
                str(predictor),
                "--limit-zips",
                "1",
                "--level",
                str(level),
                "--output",
                str(spc3),
                "--report",
                str(pack_report),
                "--no-entropy-probe",
            ]
        )
        pack_data = json.loads(pack_report.read_text(encoding="utf-8"))
        assert pack_data["ok"] is True
        assert pack_data["level"] == level
        assert pack_data["lane_count"] == 1
        assert pack_data["roundtrip_mismatches"] == 0
        assert pack_data["predictor_embedded"] is (level == 3)
        assert pack_data["external_predictor_required"] is False
        assert pack_data["codec"] == ("none" if level == 0 else "zlib")
        assert pack_data["codec_level"] == (0 if level == 0 else 9)
        assert spc3.stat().st_size == pack_data["spc3_size_bytes"]
        assert pack_data["lanes"][0]["payload_crc32"] == pack_data["lanes"][0]["rebuilt_payload_crc32"]
        assert pack_data["lanes"][0]["codec"] == pack_data["codec"]
        packed_spc3[level] = spc3

        run_command(
            [
                str(EXE),
                "--mode",
                "verify",
                "--input",
                str(spc3),
                "--root",
                str(root),
                "--report",
                str(verify_report),
            ]
        )
        verify_data = json.loads(verify_report.read_text(encoding="utf-8"))
        assert verify_data["ok"] is True
        assert verify_data["internal_crc_mismatches"] == 0
        assert verify_data["source_compare_mismatches"] == 0

        run_command(
            [
                str(EXE),
                "--mode",
                "unpack",
                "--input",
                str(spc3),
                "--unpack-dir",
                str(unpack_dir),
                "--unpack-format",
                "raw",
                "--report",
                str(unpack_report),
            ]
        )
        unpack_data = json.loads(unpack_report.read_text(encoding="utf-8"))
        assert unpack_data["ok"] is True
        assert unpack_data["crc_mismatches"] == 0
        assert (unpack_dir / f"0x{LANE_ID:04X}.pk3raw").read_bytes() == expected_payload

    assert expected_payload[:RECORD_SIZE] == make_encrypted_record(LANE_ID, 0, exception_every=4096)

    zip_unpack_report = work / "unpack_level3_zip_default.json"
    zip_unpack_dir = work / "unpack_level3_zip_default"
    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(packed_spc3[3]),
            "--unpack-dir",
            str(zip_unpack_dir),
            "--report",
            str(zip_unpack_report),
        ]
    )
    zip_unpack_data = json.loads(zip_unpack_report.read_text(encoding="utf-8"))
    assert zip_unpack_data["ok"] is True
    assert zip_unpack_data["unpack_format"] == "zip"
    assert zip_unpack_data["lane_select_mode"] == "all"
    assert zip_unpack_data["crc_mismatches"] == 0
    assert zip_unpack_data["outputs"][0]["file"].endswith(".spinda80.zip")
    assert_lane_zip_contains_encrypted_payload(zip_unpack_dir, LANE_ID, expected_payload)

    one_lane_unpack_report = work / "unpack_level3_zip_one_lane.json"
    one_lane_unpack_dir = work / "unpack_level3_zip_one_lane"
    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(packed_spc3[3]),
            "--unpack-dir",
            str(one_lane_unpack_dir),
            "--lane",
            f"{LANE_ID:04X}",
            "--report",
            str(one_lane_unpack_report),
        ]
    )
    one_lane_unpack_data = json.loads(one_lane_unpack_report.read_text(encoding="utf-8"))
    assert one_lane_unpack_data["ok"] is True
    assert one_lane_unpack_data["lane_select_mode"] == "one"
    assert one_lane_unpack_data["lane_select_value"] == f"0x{LANE_ID:04X}"
    assert one_lane_unpack_data["lane_count"] == 1
    assert_lane_zip_contains_encrypted_payload(one_lane_unpack_dir, LANE_ID, expected_payload)

    range_unpack_report = work / "unpack_level3_zip_range.json"
    range_unpack_dir = work / "unpack_level3_zip_range"
    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(packed_spc3[3]),
            "--unpack-dir",
            str(range_unpack_dir),
            "--lane-from",
            f"{LANE_ID:04X}",
            "--lane-to",
            f"{LANE_ID:04X}",
            "--report",
            str(range_unpack_report),
        ]
    )
    range_unpack_data = json.loads(range_unpack_report.read_text(encoding="utf-8"))
    assert range_unpack_data["ok"] is True
    assert range_unpack_data["lane_select_mode"] == "range"
    assert range_unpack_data["lane_select_from"] == f"0x{LANE_ID:04X}"
    assert range_unpack_data["lane_select_to"] == f"0x{LANE_ID:04X}"
    assert range_unpack_data["lane_count"] == 1
    assert_lane_zip_contains_encrypted_payload(range_unpack_dir, LANE_ID, expected_payload)

    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(packed_spc3[3]),
            "--unpack-dir",
            str(work / "unpack_level3_zip_missing_lane"),
            "--lane",
            "FFFF",
            "--report",
            str(work / "unpack_level3_zip_missing_lane.json"),
        ],
        "unpack lane selection matched no lanes",
    )

    inspect_report = work / "inspect_level3.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(packed_spc3[3]),
            "--report",
            str(inspect_report),
        ]
    )
    inspect_data = json.loads(inspect_report.read_text(encoding="utf-8"))
    assert inspect_data["schema"] == "spc3_inspect_report.v1"
    assert inspect_data["level"] == 3
    assert inspect_data["predictor"]["embedded"] is True
    assert inspect_data["predictor"]["external_required"] is False
    assert inspect_data["totals"]["predictor_exceptions"] == 16
    assert inspect_data["lanes"][0]["codec"] == "zlib"
    assert inspect_data["lanes"][0]["codec_level"] == 9

    zstd_spc3 = work / "lane_level3_zstd3.spc3"
    zstd_pack_report = work / "pack_level3_zstd3.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--codec",
            "zstd",
            "--codec-level",
            "3",
            "--output",
            str(zstd_spc3),
            "--report",
            str(zstd_pack_report),
            "--no-entropy-probe",
        ]
    )
    zstd_pack_data = json.loads(zstd_pack_report.read_text(encoding="utf-8"))
    assert zstd_pack_data["ok"] is True
    assert zstd_pack_data["codec"] == "zstd"
    assert zstd_pack_data["codec_level"] == 3
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(zstd_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(work / "verify_level3_zstd3.json"),
        ]
    )

    nested_spc3 = work / "nested" / "pack" / "lane_level0.spc3"
    nested_pack_report = work / "nested" / "reports" / "pack_level0.json"
    nested_unpack_report = work / "nested" / "reports" / "unpack_level0.json"
    nested_unpack_dir = work / "nested" / "outputs" / "unpack_level0"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--level",
            "0",
            "--no-predictor",
            "--output",
            str(nested_spc3),
            "--report",
            str(nested_pack_report),
            "--no-entropy-probe",
        ]
    )
    assert nested_spc3.is_file()
    assert nested_pack_report.is_file()
    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(nested_spc3),
            "--unpack-dir",
            str(nested_unpack_dir),
            "--unpack-format",
            "raw",
            "--report",
            str(nested_unpack_report),
        ]
    )
    assert nested_unpack_report.is_file()
    assert (nested_unpack_dir / f"0x{LANE_ID:04X}.pk3raw").read_bytes() == expected_payload

    typed_spc3 = work / "lane_level3_typed_zstd9.spc3"
    typed_pack_report = work / "pack_level3_typed_zstd9.json"
    typed_unpack_report = work / "unpack_level3_typed_zstd9.json"
    typed_unpack_dir = work / "unpack_level3_typed_zstd9"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--codec-profile",
            "fast",
            "--output",
            str(typed_spc3),
            "--report",
            str(typed_pack_report),
            "--no-entropy-probe",
        ]
    )
    typed_pack_data = json.loads(typed_pack_report.read_text(encoding="utf-8"))
    assert typed_pack_data["ok"] is True
    assert typed_pack_data["version"] == 2
    assert typed_pack_data["typed_level3"] is True
    assert typed_pack_data["codec_profile"] == "fast"
    assert typed_pack_data["codec"] == "typed-level3"
    assert typed_pack_data["lanes"][0]["stream_kind"] == "typed_level3"
    typed_substreams = typed_pack_data["lanes"][0]["typed_substreams"]
    assert [item["kind"] for item in typed_substreams] == ["template", "exception_bitmap", "xor_values"]
    assert all(item["codec"] == "zstd" and item["codec_level"] == 9 for item in typed_substreams)
    assert typed_substreams[0]["raw_size"] == RECORD_SIZE
    assert typed_substreams[1]["raw_size"] == EXPECTED_RECORDS // 8
    assert typed_substreams[2]["raw_size"] == 16 * 4

    typed_auto_spc3 = work / "lane_level3_typed_auto_compat.spc3"
    typed_auto_pack_report = work / "pack_level3_typed_auto_compat.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--output",
            str(typed_auto_spc3),
            "--report",
            str(typed_auto_pack_report),
            "--no-entropy-probe",
        ]
    )
    typed_auto_pack_data = json.loads(typed_auto_pack_report.read_text(encoding="utf-8"))
    auto_substreams = typed_auto_pack_data["lanes"][0]["typed_substreams"]
    assert typed_auto_pack_data["codec_profile"] == "none"
    assert all(item["codec"] == "zlib" and item["codec_level"] == 9 for item in auto_substreams)

    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--codec-profile",
            "fast",
            "--codec",
            "zstd",
            "--output",
            str(work / "bad_profile_mix.spc3"),
            "--report",
            str(work / "bad_profile_mix.json"),
            "--no-entropy-probe",
        ],
        "--codec-profile cannot combine",
    )

    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(typed_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(work / "verify_level3_typed_zstd9.json"),
        ]
    )
    typed_cpu_verify_data = json.loads((work / "verify_level3_typed_zstd9.json").read_text(encoding="utf-8"))
    cpu_profile = typed_cpu_verify_data["cpu_decode_profile"]
    assert cpu_profile["used"] is True
    assert cpu_profile["typed_lanes"] == 1
    assert cpu_profile["legacy_lanes"] == 0
    assert cpu_profile["crc_backend"] == "zlib_crc32"
    assert cpu_profile["crc_bytes"] == len(expected_payload)
    assert cpu_profile["stream_decode_ms"] >= 0
    assert cpu_profile["iv_expand_ms"] >= 0
    assert cpu_profile["rebuild_encrypt_ms"] >= 0
    asm_recommendation = typed_cpu_verify_data["asm_recommendation"]
    assert asm_recommendation["policy"] == "targeted_asm_unpaused_profile_guided"
    assert asm_recommendation["implemented_target"] == "pk3_shuffle48_x86_64_asm"
    assert asm_recommendation["profile_used"] is True
    assert asm_recommendation["largest_slice"] in {
        "stream_decode",
        "iv_expand",
        "rebuild_encrypt",
        "crc",
    }
    assert asm_recommendation["next_action"]
    if cuda_available():
        typed_gpu_verify_report = work / "verify_level3_typed_zstd9_gpu.json"
        run_command(
            [
                str(EXE),
                "--mode",
                "verify",
                "--gpu-rebuild",
                "--input",
                str(typed_spc3),
                "--root",
                str(root),
                "--predictor",
                str(predictor),
                "--report",
                str(typed_gpu_verify_report),
            ]
        )
        typed_gpu_verify_data = json.loads(typed_gpu_verify_report.read_text(encoding="utf-8"))
        assert typed_gpu_verify_data["ok"] is True
        assert typed_gpu_verify_data["gpu_rebuild"]["used"] is True
        assert typed_gpu_verify_data["gpu_rebuild"]["status"] == "ok"
        assert typed_gpu_verify_data["gpu_rebuild"]["mismatched_lanes"] == 0
        assert typed_gpu_verify_data["gpu_rebuild"]["download_mode"] in {"bulk", "per_lane"}
        assert typed_gpu_verify_data["gpu_rebuild"]["host_crc_ms"] >= 0

        gpu_cache_report = work / "bench_gpu_cache_1_1.json"
        run_command(
            [
                str(EXE),
                "--mode",
                "bench",
                "--bench-gpu",
                "--bench-limits",
                "1,1",
                "--root",
                str(root),
                "--predictor",
                str(predictor),
                "--report",
                str(gpu_cache_report),
            ]
        )
        gpu_cache_data = json.loads(gpu_cache_report.read_text(encoding="utf-8"))
        first_gpu = gpu_cache_data["samples"][0]["gpu_offload"]
        second_gpu = gpu_cache_data["samples"][1]["gpu_offload"]
        assert first_gpu["used"] is True
        assert second_gpu["used"] is True
        assert first_gpu["runtime_cache_hit"] is False
        assert second_gpu["runtime_cache_hit"] is True
        assert second_gpu["compile_ms"] == 0

    gpu_fallback_verify_report = work / "verify_level3_zstd3_gpu_fallback.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--gpu-rebuild",
            "--input",
            str(zstd_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(gpu_fallback_verify_report),
        ]
    )
    gpu_fallback_verify_data = json.loads(gpu_fallback_verify_report.read_text(encoding="utf-8"))
    assert gpu_fallback_verify_data["ok"] is True
    assert gpu_fallback_verify_data["gpu_rebuild"]["used"] is False
    assert gpu_fallback_verify_data["gpu_rebuild"]["requested"] is True
    assert gpu_fallback_verify_data["gpu_rebuild"]["status"] == "fallback_cpu"
    assert "not v0.2" in gpu_fallback_verify_data["gpu_rebuild"]["fallback_reason"]

    gpu_disabled_verify_report = work / "verify_level3_typed_zstd9_gpu_disabled.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--gpu-rebuild",
            "--input",
            str(typed_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(gpu_disabled_verify_report),
        ],
        env_overlay={"SPC3_DISABLE_CUDA": "1"},
    )
    gpu_disabled_verify_data = json.loads(gpu_disabled_verify_report.read_text(encoding="utf-8"))
    assert gpu_disabled_verify_data["ok"] is True
    assert gpu_disabled_verify_data["gpu_rebuild"]["used"] is False
    assert gpu_disabled_verify_data["gpu_rebuild"]["requested"] is True
    assert gpu_disabled_verify_data["gpu_rebuild"]["status"] == "fallback_cpu"
    assert "SPC3_DISABLE_CUDA" in gpu_disabled_verify_data["gpu_rebuild"]["fallback_reason"]
    assert gpu_disabled_verify_data["cpu_decode_profile"]["used"] is True
    assert gpu_disabled_verify_data["cpu_decode_profile"]["typed_lanes"] == 1

    gpu_forced_failure_report = work / "verify_level3_typed_zstd9_gpu_forced_failure.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--gpu-rebuild",
            "--input",
            str(typed_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(gpu_forced_failure_report),
        ],
        env_overlay={"SPC3_FORCE_GPU_REBUILD_FAILURE": "1"},
    )
    gpu_forced_failure_data = json.loads(gpu_forced_failure_report.read_text(encoding="utf-8"))
    assert gpu_forced_failure_data["ok"] is True
    assert gpu_forced_failure_data["gpu_rebuild"]["used"] is False
    assert gpu_forced_failure_data["gpu_rebuild"]["requested"] is True
    assert gpu_forced_failure_data["gpu_rebuild"]["status"] == "fallback_cpu"
    assert "SPC3_FORCE_GPU_REBUILD_FAILURE" in gpu_forced_failure_data["gpu_rebuild"]["fallback_reason"]

    gpu_disabled_bench_report = work / "bench_gpu_disabled.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "bench",
            "--bench-gpu",
            "--bench-limits",
            "1",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(gpu_disabled_bench_report),
        ],
        env_overlay={"SPC3_DISABLE_CUDA": "1"},
    )
    gpu_disabled_bench_data = json.loads(gpu_disabled_bench_report.read_text(encoding="utf-8"))
    gpu_disabled_bench = gpu_disabled_bench_data["samples"][0]["gpu_offload"]
    assert gpu_disabled_bench["used"] is False
    assert gpu_disabled_bench["requested"] is True
    assert gpu_disabled_bench["status"] == "cuda_disabled_by_environment"
    assert "SPC3_DISABLE_CUDA" in gpu_disabled_bench["fallback_reason"]
    assert gpu_disabled_bench["download_mode"] == "none"

    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(typed_spc3),
            "--unpack-dir",
            str(typed_unpack_dir),
            "--unpack-format",
            "raw",
            "--report",
            str(typed_unpack_report),
        ]
    )
    typed_unpack_data = json.loads(typed_unpack_report.read_text(encoding="utf-8"))
    assert typed_unpack_data["ok"] is True
    assert (typed_unpack_dir / f"0x{LANE_ID:04X}.pk3raw").read_bytes() == expected_payload

    typed_zip_unpack_report = work / "unpack_level3_typed_zstd9_zip.json"
    typed_zip_unpack_dir = work / "unpack_level3_typed_zstd9_zip"
    run_command(
        [
            str(EXE),
            "--mode",
            "unpack",
            "--input",
            str(typed_spc3),
            "--unpack-dir",
            str(typed_zip_unpack_dir),
            "--lane",
            f"0x{LANE_ID:04X}",
            "--report",
            str(typed_zip_unpack_report),
        ]
    )
    typed_zip_unpack_data = json.loads(typed_zip_unpack_report.read_text(encoding="utf-8"))
    assert typed_zip_unpack_data["ok"] is True
    assert typed_zip_unpack_data["unpack_format"] == "zip"
    assert typed_zip_unpack_data["lane_select_mode"] == "one"
    assert typed_zip_unpack_data["lane_count"] == 1
    assert typed_zip_unpack_data["outputs"][0]["file"].endswith(".spinda80.zip")
    assert_lane_zip_contains_encrypted_payload(typed_zip_unpack_dir, LANE_ID, expected_payload)

    shard_root = work / "consolidate_second_lane"
    shard_root.mkdir()
    write_full_good_zip(shard_root, compression=ZIP_STORED, exception_every=4096, lane_id=LANE_ID + 1)
    shard2_spc3 = work / "lane_level3_typed_zstd9_second.spc3"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(shard_root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--codec-profile",
            "fast",
            "--output",
            str(shard2_spc3),
            "--report",
            str(work / "pack_level3_typed_zstd9_second.json"),
            "--no-entropy-probe",
        ]
    )

    consolidate_root = work / "precompressed_shards"
    consolidate_root.mkdir()
    (consolidate_root / "part-a.spc3").write_bytes(typed_spc3.read_bytes())
    (consolidate_root / "part-b.spc3").write_bytes(shard2_spc3.read_bytes())
    consolidated_spc3 = work / "consolidated_typed_zstd9.spc3"
    consolidate_report = work / "consolidated_typed_zstd9.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "consolidate",
            "--consolidate-root",
            str(consolidate_root),
            "--output",
            str(consolidated_spc3),
            "--report",
            str(consolidate_report),
        ]
    )
    consolidate_data = json.loads(consolidate_report.read_text(encoding="utf-8"))
    assert consolidate_data["schema"] == "spc3_consolidate_report.v1"
    assert consolidate_data["ok"] is True
    assert consolidate_data["copy_mode"] == "compressed_stream_copy_no_payload_decode"
    assert consolidate_data["input_spc3_count"] == 2
    assert consolidate_data["lane_count"] == 2
    assert [lane["lane"] for lane in consolidate_data["lanes"]] == [f"0x{LANE_ID:04X}", f"0x{LANE_ID + 1:04X}"]

    consolidated_verify_report = work / "consolidated_typed_zstd9_verify.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(consolidated_spc3),
            "--predictor",
            str(predictor),
            "--no-source-compare",
            "--report",
            str(consolidated_verify_report),
        ]
    )
    consolidated_verify_data = json.loads(consolidated_verify_report.read_text(encoding="utf-8"))
    assert consolidated_verify_data["ok"] is True
    assert consolidated_verify_data["lane_count"] == 2
    assert consolidated_verify_data["internal_crc_mismatches"] == 0

    duplicate_root = work / "duplicate_precompressed_shards"
    duplicate_root.mkdir()
    (duplicate_root / "dup-a.spc3").write_bytes(typed_spc3.read_bytes())
    (duplicate_root / "dup-b.spc3").write_bytes(typed_spc3.read_bytes())
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "consolidate",
            "--consolidate-root",
            str(duplicate_root),
            "--output",
            str(work / "duplicate_consolidated.spc3"),
            "--report",
            str(work / "duplicate_consolidated.json"),
        ],
        "duplicate lane",
    )

    if cuda_available():
        typed_gpu_unpack_report = work / "unpack_level3_typed_zstd9_gpu.json"
        typed_gpu_unpack_dir = work / "unpack_level3_typed_zstd9_gpu"
        run_command(
            [
                str(EXE),
                "--mode",
                "unpack",
                "--gpu-rebuild",
                "--input",
                str(typed_spc3),
                "--unpack-dir",
                str(typed_gpu_unpack_dir),
                "--unpack-format",
                "raw",
                "--report",
                str(typed_gpu_unpack_report),
            ]
        )
        typed_gpu_unpack_data = json.loads(typed_gpu_unpack_report.read_text(encoding="utf-8"))
        assert typed_gpu_unpack_data["ok"] is True
        assert typed_gpu_unpack_data["gpu_rebuild"]["used"] is True
        assert (typed_gpu_unpack_dir / f"0x{LANE_ID:04X}.pk3raw").read_bytes() == expected_payload

    typed_inspect_report = work / "inspect_level3_typed_zstd9.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(typed_spc3),
            "--report",
            str(typed_inspect_report),
        ]
    )
    typed_inspect_data = json.loads(typed_inspect_report.read_text(encoding="utf-8"))
    assert typed_inspect_data["version"] == 2
    assert typed_inspect_data["lanes"][0]["codec"] == "typed-level3"
    assert typed_inspect_data["lanes"][0]["typed_substreams"][1]["kind"] == "exception_bitmap"

    typed_rans_spc3 = work / "lane_level3_typed_rans.spc3"
    typed_rans_pack_report = work / "pack_level3_typed_rans.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--codec",
            "rans",
            "--output",
            str(typed_rans_spc3),
            "--report",
            str(typed_rans_pack_report),
            "--no-entropy-probe",
        ]
    )
    typed_rans_pack_data = json.loads(typed_rans_pack_report.read_text(encoding="utf-8"))
    assert typed_rans_pack_data["ok"] is True
    rans_substreams = typed_rans_pack_data["lanes"][0]["typed_substreams"]
    assert rans_substreams[0]["codec"] == "none"
    assert rans_substreams[1]["codec"] == "rans"
    assert rans_substreams[2]["codec"] == "rans"
    typed_rans_verify_report = work / "verify_level3_typed_rans.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(typed_rans_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(typed_rans_verify_report),
        ]
    )
    typed_rans_verify_data = json.loads(typed_rans_verify_report.read_text(encoding="utf-8"))
    assert typed_rans_verify_data["ok"] is True

    typed_external_spc3 = work / "lane_level3_typed_external_predictor.spc3"
    typed_external_pack_report = work / "pack_level3_typed_external_predictor.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--typed-level3",
            "--codec-profile",
            "fast",
            "--external-predictor",
            "--output",
            str(typed_external_spc3),
            "--report",
            str(typed_external_pack_report),
            "--no-entropy-probe",
        ]
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "verify",
            "--gpu-rebuild",
            "--input",
            str(typed_external_spc3),
            "--root",
            str(root),
            "--predictor",
            str(work / "missing_predictor.json"),
            "--report",
            str(work / "verify_level3_typed_external_missing_predictor.json"),
            "--no-source-compare",
        ],
        "could not open",
    )

    external_spc3 = work / "lane_level3_external_predictor.spc3"
    external_pack_report = work / "pack_level3_external_predictor.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--level",
            "3",
            "--external-predictor",
            "--output",
            str(external_spc3),
            "--report",
            str(external_pack_report),
            "--no-entropy-probe",
        ]
    )
    external_pack_data = json.loads(external_pack_report.read_text(encoding="utf-8"))
    assert external_pack_data["ok"] is True
    assert external_pack_data["predictor_embedded"] is False
    assert external_pack_data["external_predictor_required"] is True
    assert external_spc3.stat().st_size < packed_spc3[3].stat().st_size

    external_inspect_report = work / "inspect_level3_external_predictor.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(external_spc3),
            "--report",
            str(external_inspect_report),
        ]
    )
    external_inspect_data = json.loads(external_inspect_report.read_text(encoding="utf-8"))
    assert external_inspect_data["predictor"]["embedded"] is False
    assert external_inspect_data["predictor"]["external_required"] is True

    external_verify_report = work / "verify_level3_external_predictor.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(external_spc3),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--report",
            str(external_verify_report),
        ]
    )
    assert json.loads(external_verify_report.read_text(encoding="utf-8"))["ok"] is True

    malformed_dir = work / "malformed_spc3"
    malformed_dir.mkdir()
    valid_level3 = packed_spc3[3]
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(mutate_spc3_file(valid_level3, malformed_dir / "bad_magic.spc3", lambda data: data.__setitem__(0, 0))),
            "--report",
            str(malformed_dir / "bad_magic.json"),
        ],
        "not an SPC3 file",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "bad_table_offset.spc3",
                    lambda data: write_u64(data, SPC3_TABLE_OFFSET_OFFSET, read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + 1),
                )
            ),
            "--report",
            str(malformed_dir / "bad_table_offset.json"),
        ],
        "SPC3 table is not adjacent",
    )
    truncated = malformed_dir / "truncated_stream.spc3"
    truncated.write_bytes(valid_level3.read_bytes()[:-1])
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(truncated),
            "--root",
            str(root),
            "--report",
            str(malformed_dir / "truncated_stream.json"),
        ],
        "SPC3 data section is truncated",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "wrong_predictor_size.spc3",
                    lambda data: write_u64(data, SPC3_PREDICTOR_SIZE_OFFSET, 0),
                )
            ),
            "--report",
            str(malformed_dir / "wrong_predictor_size.json"),
        ],
        "embedded predictor has no data",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "level_mismatch.spc3",
                    lambda data: write_u32(data, read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_LEVEL_OFFSET, 2),
                )
            ),
            "--report",
            str(malformed_dir / "level_mismatch.json"),
        ],
        "bad SPC3 table entry",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "level3_impossible_model_size.spc3",
                    lambda data: write_u64(
                        data,
                        read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_UNCOMPRESSED_SIZE_OFFSET,
                        RECORD_SIZE + EXPECTED_RECORDS // 8 + EXPECTED_RECORDS * 4 + 4,
                    ),
                )
            ),
            "--report",
            str(malformed_dir / "level3_impossible_model_size.json"),
        ],
        "level 3 table uncompressed size is invalid",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "bad_codec_id.spc3",
                    lambda data: write_u32(data, read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_FLAGS_OFFSET, 99),
                )
            ),
            "--report",
            str(malformed_dir / "bad_codec_id.json"),
        ],
        "unsupported SPC3 codec id",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "wrong_codec.spc3",
                    lambda data: write_u32(
                        data,
                        read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_FLAGS_OFFSET,
                        0x00000303,
                    ),
                )
            ),
            "--root",
            str(root),
            "--report",
            str(malformed_dir / "wrong_codec.json"),
        ],
        "zstd decompression failed",
    )
    bad_typed_substream = mutate_spc3_file(
        typed_spc3,
        malformed_dir / "bad_typed_substream_kind.spc3",
        lambda data: write_u32(data, read_u64(data, SPC3_DATA_OFFSET_OFFSET), 99),
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(bad_typed_substream),
            "--report",
            str(malformed_dir / "bad_typed_substream_kind.json"),
        ],
        "typed level 3 substream kind is invalid",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    typed_spc3,
                    malformed_dir / "typed_values_impossible_raw_size.spc3",
                    lambda data: (
                        write_u64(
                            data,
                            read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_UNCOMPRESSED_SIZE_OFFSET,
                            RECORD_SIZE + EXPECTED_RECORDS // 8 + EXPECTED_RECORDS * 4 + 4,
                        ),
                        write_u64(
                            data,
                            read_u64(data, SPC3_DATA_OFFSET_OFFSET) + SPC3_TYPED_VALUES_RAW_SIZE_OFFSET,
                            EXPECTED_RECORDS * 4 + 4,
                        ),
                    ),
                )
            ),
            "--report",
            str(malformed_dir / "typed_values_impossible_raw_size.json"),
        ],
        "level 3 table uncompressed size is invalid",
    )
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "verify",
            "--gpu-rebuild",
            "--input",
            str(bad_typed_substream),
            "--root",
            str(root),
            "--report",
            str(malformed_dir / "bad_typed_substream_kind_gpu.json"),
        ],
        "typed level 3 substream kind is invalid",
    )
    trailing = malformed_dir / "trailing_stream_bytes.spc3"
    trailing.write_bytes(valid_level3.read_bytes() + b"X")
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(trailing),
            "--report",
            str(malformed_dir / "trailing_stream_bytes.json"),
        ],
        "SPC3 file has trailing bytes",
    )
    header_fuzz_cases = [
        ("bad_record_count", 16, "unsupported SPC3 header"),
        ("bad_record_size", 20, "unsupported SPC3 header"),
        ("bad_header_size", 28, "unsupported SPC3 header"),
    ]
    expect_command_failure(
        [
            str(EXE),
            "--mode",
            "inspect",
            "--input",
            str(
                mutate_spc3_file(
                    valid_level3,
                    malformed_dir / "bad_version.spc3",
                    lambda data: write_u32(data, 4, 99),
                )
            ),
            "--report",
            str(malformed_dir / "bad_version.json"),
        ],
        "unsupported SPC3 header",
    )
    for name, offset, expected_text in header_fuzz_cases:
        expect_command_failure(
            [
                str(EXE),
                "--mode",
                "inspect",
                "--input",
                str(
                    mutate_spc3_file(
                        valid_level3,
                        malformed_dir / f"{name}.spc3",
                        lambda data, field_offset=offset: write_u32(data, field_offset, read_u32(data, field_offset) + 1),
                    )
                ),
                "--report",
                str(malformed_dir / f"{name}.json"),
            ],
            expected_text,
        )
    wrong_crc = mutate_spc3_file(
        valid_level3,
        malformed_dir / "wrong_crc.spc3",
        lambda data: write_u64(
            data,
            read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_ORIGINAL_CRC_OFFSET,
            read_u64(data, read_u64(data, SPC3_TABLE_OFFSET_OFFSET) + SPC3_TABLE_ORIGINAL_CRC_OFFSET) ^ 1,
        ),
    )
    wrong_crc_report = malformed_dir / "wrong_crc_verify.json"
    wrong_crc_result = run_command(
        [
            str(EXE),
            "--mode",
            "verify",
            "--input",
            str(wrong_crc),
            "--root",
            str(root),
            "--report",
            str(wrong_crc_report),
        ],
        check=False,
    )
    assert wrong_crc_result.returncode != 0, wrong_crc_result.stdout + wrong_crc_result.stderr
    wrong_crc_data = json.loads(wrong_crc_report.read_text(encoding="utf-8"))
    assert wrong_crc_data["ok"] is False
    assert wrong_crc_data["internal_crc_mismatches"] == 1

    bench_report = work / "bench_report.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "bench",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--bench-limits",
            "1",
            "--bench-native-codecs",
            "--report",
            str(bench_report),
        ]
    )
    bench_data = json.loads(bench_report.read_text(encoding="utf-8"))
    assert bench_data["schema"] == "spc3_compression_oracle.v1"
    assert bench_data["samples"][0]["lane_count"] == 1
    assert {item["level"] for item in bench_data["samples"][0]["spc3_levels"]} == {0, 1, 2, 3}
    assert "external_models" in bench_data["samples"][0]
    assert bench_data["samples"][0]["solid_7z_lzma2"]["status"] == "not_run"
    assert bench_data["samples"][0]["zstd"]["status"] == "not_run"
    assert bench_data["native_codecs"]["enabled"] is True
    native_rows = bench_data["samples"][0]["native_codec_matrix"]
    assert {(item["codec"], item["codec_level"], item["spc3_level"]) for item in native_rows} >= {
        ("zstd", 3, 1),
        ("zstd", 9, 2),
        ("zstd", 19, 3),
        ("lzma2", 9, 3),
    }
    assert all(item["status"] == "ok" for item in native_rows)
    for item in bench_data["samples"][0]["spc3_levels"]:
        assert item["decode_crc_mismatches"] == 0
        assert item["unpack_ms"] >= 0
        assert item["verify_ms"] >= 0
        assert item["decode_mib_s"] > 0

    streaming_bench_report = work / "streaming_bench_report.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "bench",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--bench-limits",
            "1",
            "--bench-streaming",
            "--report",
            str(streaming_bench_report),
        ]
    )
    streaming_data = json.loads(streaming_bench_report.read_text(encoding="utf-8"))
    assert streaming_data["schema"] == "spc3_streaming_compression_oracle.v1"
    assert streaming_data["streaming"] is True
    assert streaming_data["samples"][0]["lane_count"] == 1
    assert streaming_data["samples"][0]["native_codec_matrix"] == []
    normal_sizes = {item["level"]: item["size_bytes"] for item in bench_data["samples"][0]["spc3_levels"]}
    streaming_sizes = {item["level"]: item["size_bytes"] for item in streaming_data["samples"][0]["spc3_levels"]}
    assert streaming_sizes == normal_sizes
    for item in streaming_data["samples"][0]["spc3_levels"]:
        assert item["decode_crc_mismatches"] == 0
        assert item["decode_mib_s"] > 0

    if cuda_available():
        gpu_streaming_report = work / "gpu_streaming_bench_report.json"
        run_command(
            [
                str(EXE),
                "--mode",
                "bench",
                "--root",
                str(root),
                "--predictor",
                str(predictor),
                "--bench-limits",
                "1",
                "--bench-gpu",
                "--report",
                str(gpu_streaming_report),
            ]
        )
        gpu_data = json.loads(gpu_streaming_report.read_text(encoding="utf-8"))
        gpu_result = gpu_data["samples"][0]["gpu_offload"]
        assert gpu_data["gpu_offload"]["enabled"] is True
        assert gpu_result["status"] == "ok"
        assert gpu_result["lane_count"] == 1
        assert gpu_result["value_count"] == 16
        assert gpu_result["mismatched_lanes"] == 0
        assert gpu_result["mismatched_bytes"] == 0
        assert gpu_result["kernel_ms"] >= 0

    filtered_streaming_report = work / "filtered_streaming_bench_report.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "bench",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--bench-limits",
            "1",
            "--bench-streaming",
            "--bench-typed-level3",
            "--bench-levels",
            "3",
            "--bench-codecs",
            "zlib-9,zstd-9",
            "--report",
            str(filtered_streaming_report),
        ]
    )
    filtered_data = json.loads(filtered_streaming_report.read_text(encoding="utf-8"))
    assert filtered_data["native_codecs"]["enabled"] is True
    assert filtered_data["native_codecs"]["codec_filter"] == ["zlib-9", "zstd-9"]
    assert filtered_data["native_codecs"]["level_filter"] == [3]
    filtered_rows = filtered_data["samples"][0]["native_codec_matrix"]
    assert [(item["codec"], item["codec_level"], item["spc3_level"]) for item in filtered_rows] == [
        ("zlib", 9, 3),
        ("zstd", 9, 3),
    ]
    assert all(item["status"] == "ok" for item in filtered_rows)
    assert all(item["decode_crc_mismatches"] == 0 for item in filtered_rows)
    assert filtered_data["typed_level3"]["enabled"] is True
    typed_rows = filtered_data["samples"][0]["typed_level3_matrix"]
    assert [item["policy"] for item in typed_rows] == [
        "raw",
        "all-zlib-9",
        "exceptions-zlib-9",
        "all-zstd-9",
        "exceptions-zstd-9",
    ]
    assert all(item["status"] == "ok" for item in typed_rows)
    assert all(item["decode_crc_mismatches"] == 0 for item in typed_rows)
    raw_typed = typed_rows[0]
    assert raw_typed["substream_bytes"] == RECORD_SIZE + (EXPECTED_RECORDS // 8) + (16 * 4)
    assert raw_typed["template_stream_bytes"] == RECORD_SIZE
    assert raw_typed["bitmap_stream_bytes"] == EXPECTED_RECORDS // 8
    assert raw_typed["values_stream_bytes"] == 16 * 4
    assert raw_typed["size_bytes"] > raw_typed["substream_bytes"]

    rans_streaming_report = work / "rans_streaming_bench_report.json"
    run_command(
        [
            str(EXE),
            "--mode",
            "bench",
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--bench-limits",
            "1",
            "--bench-rans-fse",
            "--bench-levels",
            "3",
            "--bench-codecs",
            "zstd-9,lzma2-9",
            "--report",
            str(rans_streaming_report),
        ]
    )
    rans_data = json.loads(rans_streaming_report.read_text(encoding="utf-8"))
    assert rans_data["typed_level3"]["rans_fse_enabled"] is True
    rans_rows = rans_data["samples"][0]["typed_level3_matrix"]
    assert "exceptions-rans" in [item["policy"] for item in rans_rows]
    rans_row = next(item for item in rans_rows if item["policy"] == "exceptions-rans")
    assert rans_row["template_codec"] == "none"
    assert rans_row["bitmap_codec"] == "rans"
    assert rans_row["values_codec"] == "rans"
    assert rans_row["decode_crc_mismatches"] == 0


def test_pack_all_zips_accepts_sparse_corpus_without_lane_zero(work: Path) -> None:
    root = work / "sparse_without_zero"
    root.mkdir()
    first_lane = 0x00A5
    last_lane = 0xFFFF
    write_full_good_zip(root, compression=ZIP_STORED, lane_id=first_lane)
    write_full_good_zip(root, compression=ZIP_STORED, lane_id=last_lane)
    spc3 = work / "sparse_without_zero.spc3"
    report = work / "sparse_without_zero_pack.json"

    run_command(
        [
            str(EXE),
            "--mode",
            "pack",
            "--root",
            str(root),
            "--all-zips",
            "--level",
            "0",
            "--no-predictor",
            "--no-entropy-probe",
            "--output",
            str(spc3),
            "--report",
            str(report),
        ]
    )

    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["ok"] is True
    assert data["lane_count"] == 2
    assert data["all_zips"] is True
    assert data["limit_zips"] == 0
    assert [lane["lane"] for lane in data["lanes"]] == [f"0x{first_lane:04X}", f"0x{last_lane:04X}"]


def test_full_content_pid_mismatch_lane_exits_nonzero(work: Path) -> None:
    root = work / "content_pid_mismatch"
    root.mkdir()
    report = work / "content_pid_mismatch_report.json"
    write_full_content_pid_mismatch_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--no-predictor",
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    totals = data["totals"]
    assert totals["audit_failed"] is True
    assert totals["records_processed"] == EXPECTED_RECORDS
    assert totals["content_pid_mismatches"] == 1
    assert totals["missing_entries"] == 0
    assert totals["rebuild_mismatches"] == 0
    assert totals["iv32_stream_bytes"] == 0


def test_full_bad_checksum_lane_exits_nonzero(work: Path) -> None:
    root = work / "bad_checksum_full"
    root.mkdir()
    report = work / "bad_checksum_full_report.json"
    write_full_bad_checksum_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--no-predictor",
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    totals = data["totals"]
    assert totals["audit_failed"] is True
    assert totals["records_processed"] == EXPECTED_RECORDS
    assert totals["checksum_failures"] == 1
    assert totals["rebuild_mismatches"] == 1
    assert "checksum mismatch" in data["lanes"][0]["errors"][0]


def test_full_template_mismatch_lane_exits_nonzero(work: Path) -> None:
    root = work / "template_mismatch_full"
    root.mkdir()
    report = work / "template_mismatch_full_report.json"
    write_full_template_mismatch_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--no-predictor",
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    totals = data["totals"]
    assert totals["audit_failed"] is True
    assert totals["records_processed"] == EXPECTED_RECORDS
    assert totals["checksum_failures"] == 0
    assert totals["template_mismatches"] == 1
    assert totals["rebuild_mismatches"] == 1


def test_bad_lane_exits_nonzero(work: Path) -> None:
    root = work / "bad"
    root.mkdir()
    predictor = work / "predictor_bad.json"
    report = work / "bad_report.json"
    write_predictor(predictor)
    write_bad_incomplete_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    totals = data["totals"]
    assert totals["audit_failed"] is True
    assert totals["records_processed"] == 1
    assert totals["missing_entries"] == EXPECTED_RECORDS - 1
    assert totals["raw_payload_bytes"] == RECORD_SIZE
    assert totals["iv32_stream_bytes"] == 0
    assert totals["predictor_exception_raw_bytes"] == 0
    assert totals["rebuild_mismatches"] == 0


def test_bad_duplicate_lane_exits_nonzero(work: Path) -> None:
    root = work / "bad_duplicate"
    root.mkdir()
    predictor = work / "predictor_bad_duplicate.json"
    report = work / "bad_duplicate_report.json"
    write_predictor(predictor)
    write_bad_duplicate_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert data["totals"]["duplicate_entries"] == 1
    assert data["totals"]["missing_entries"] == EXPECTED_RECORDS - 1


def test_bad_entry_lane_exits_nonzero(work: Path) -> None:
    root = work / "bad_entry_lane"
    root.mkdir()
    predictor = work / "predictor_bad_entry_lane.json"
    report = work / "bad_entry_lane_report.json"
    write_predictor(predictor)
    write_single_entry_manual_zip(root, name_pid=(LANE_ID + 1) & 0xFFFF)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "entry lane mismatch" in data["lanes"][0]["errors"][0]


def test_bad_unsupported_method_exits_nonzero(work: Path) -> None:
    root = work / "bad_unsupported_method"
    root.mkdir()
    predictor = work / "predictor_bad_unsupported_method.json"
    report = work / "bad_unsupported_method_report.json"
    write_predictor(predictor)
    write_single_entry_manual_zip(root, name_pid=LANE_ID, method=99)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "unsupported ZIP method" in data["lanes"][0]["errors"][0]


def test_bad_encrypted_flag_exits_nonzero(work: Path) -> None:
    root = work / "bad_encrypted_flag"
    root.mkdir()
    predictor = work / "predictor_bad_encrypted_flag.json"
    report = work / "bad_encrypted_flag_report.json"
    write_predictor(predictor)
    write_single_entry_manual_zip(root, name_pid=LANE_ID, flags=0x0001)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "encrypted ZIP entry is not supported" in data["lanes"][0]["errors"][0]


def test_bad_local_header_name_exits_nonzero(work: Path) -> None:
    root = work / "bad_local_name"
    root.mkdir()
    predictor = work / "predictor_bad_local_name.json"
    report = work / "bad_local_name_report.json"
    write_predictor(predictor)
    write_bad_local_header_name_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "local header name mismatch" in data["lanes"][0]["errors"][0]


def test_bad_trailing_deflate_exits_nonzero(work: Path) -> None:
    root = work / "bad_trailing_deflate"
    root.mkdir()
    predictor = work / "predictor_bad_trailing_deflate.json"
    report = work / "bad_trailing_deflate_report.json"
    write_predictor(predictor)
    write_bad_trailing_deflate_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "inflate failed" in data["lanes"][0]["errors"][0]


def test_bad_crc_exits_nonzero(work: Path) -> None:
    root = work / "bad_crc"
    root.mkdir()
    predictor = work / "predictor_bad_crc.json"
    report = work / "bad_crc_report.json"
    write_predictor(predictor)
    write_bad_crc_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "CRC32 mismatch" in data["lanes"][0]["errors"][0]


def test_bad_local_method_exits_nonzero(work: Path) -> None:
    root = work / "bad_local_method"
    root.mkdir()
    predictor = work / "predictor_bad_local_method.json"
    report = work / "bad_local_method_report.json"
    write_predictor(predictor)
    write_bad_local_method_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "local header method mismatch" in data["lanes"][0]["errors"][0]


def test_bad_local_flags_exits_nonzero(work: Path) -> None:
    root = work / "bad_local_flags"
    root.mkdir()
    predictor = work / "predictor_bad_local_flags.json"
    report = work / "bad_local_flags_report.json"
    write_predictor(predictor)
    write_bad_local_flags_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "local header flag mismatch" in data["lanes"][0]["errors"][0]


def test_bad_local_size_exits_nonzero(work: Path) -> None:
    root = work / "bad_local_size"
    root.mkdir()
    predictor = work / "predictor_bad_local_size.json"
    report = work / "bad_local_size_report.json"
    write_predictor(predictor)
    write_bad_local_size_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "local header metadata mismatch" in data["lanes"][0]["errors"][0]


def test_bad_data_descriptor_flag_exits_nonzero(work: Path) -> None:
    root = work / "bad_data_descriptor"
    root.mkdir()
    predictor = work / "predictor_bad_data_descriptor.json"
    report = work / "bad_data_descriptor_report.json"
    write_predictor(predictor)
    write_bad_data_descriptor_flag_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "data descriptor ZIP entry is not supported" in data["lanes"][0]["errors"][0]


def test_eocd_comment_zip_reaches_lane_audit(work: Path) -> None:
    root = work / "eocd_comment"
    root.mkdir()
    report = work / "eocd_comment_report.json"
    write_eocd_comment_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert data["lanes"][0]["entry_count"] == 1
    assert data["lanes"][0]["missing_entries"] == EXPECTED_RECORDS - 1


def test_bad_central_trailing_exits_nonzero(work: Path) -> None:
    root = work / "bad_central_trailing"
    root.mkdir()
    report = work / "bad_central_trailing_report.json"
    write_bad_central_trailing_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "central directory has trailing bytes" in data["lanes"][0]["errors"][0]


def test_bad_short_zip64_extra_exits_nonzero(work: Path) -> None:
    root = work / "bad_short_zip64_extra"
    root.mkdir()
    report = work / "bad_short_zip64_extra_report.json"
    write_bad_short_zip64_extra_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "short ZIP64 extra field for compressed size" in data["lanes"][0]["errors"][0]


def test_valid_local_zip64_size_reaches_lane_audit(work: Path) -> None:
    root = work / "valid_local_zip64_size"
    root.mkdir()
    report = work / "valid_local_zip64_size_report.json"
    write_valid_local_zip64_size_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert data["lanes"][0]["entry_count"] == 1
    assert data["lanes"][0]["missing_entries"] == EXPECTED_RECORDS - 1
    assert all("local header metadata mismatch" not in error for error in data["lanes"][0]["errors"])


def test_bad_central_truncated_extra_exits_nonzero(work: Path) -> None:
    root = work / "bad_central_truncated_extra"
    root.mkdir()
    report = work / "bad_central_truncated_extra_report.json"
    write_bad_central_truncated_extra_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "central entry extra field is truncated" in data["lanes"][0]["errors"][0]


def test_bad_local_truncated_extra_exits_nonzero(work: Path) -> None:
    root = work / "bad_local_truncated_extra"
    root.mkdir()
    report = work / "bad_local_truncated_extra_report.json"
    write_bad_local_truncated_extra_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-predictor",
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "local header extra field is truncated" in data["lanes"][0]["errors"][0]


def test_bad_multidisk_eocd_exits_nonzero(work: Path) -> None:
    root = work / "bad_multidisk_eocd"
    root.mkdir()
    predictor = work / "predictor_bad_multidisk_eocd.json"
    report = work / "bad_multidisk_eocd_report.json"
    write_predictor(predictor)
    write_bad_multidisk_eocd_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "multi-disk ZIP is not supported" in data["lanes"][0]["errors"][0]


def test_bad_zip64_locator_exits_nonzero(work: Path) -> None:
    root = work / "bad_zip64_locator"
    root.mkdir()
    predictor = work / "predictor_bad_zip64_locator.json"
    report = work / "bad_zip64_locator_report.json"
    write_predictor(predictor)
    write_bad_zip64_locator_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "ZIP64 EOCD outside ZIP" in data["lanes"][0]["errors"][0]


def test_bad_zip64_multidisk_locator_exits_nonzero(work: Path) -> None:
    root = work / "bad_zip64_multidisk_locator"
    root.mkdir()
    predictor = work / "predictor_bad_zip64_multidisk_locator.json"
    report = work / "bad_zip64_multidisk_locator_report.json"
    write_predictor(predictor)
    write_bad_zip64_multidisk_locator_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "multi-disk ZIP64 locator is not supported" in data["lanes"][0]["errors"][0]


def test_bad_zip64_eocd_size_exits_nonzero(work: Path) -> None:
    root = work / "bad_zip64_eocd_size"
    root.mkdir()
    predictor = work / "predictor_bad_zip64_eocd_size.json"
    report = work / "bad_zip64_eocd_size_report.json"
    write_predictor(predictor)
    write_bad_zip64_eocd_size_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "ZIP64 EOCD record outside ZIP" in data["lanes"][0]["errors"][0]


def test_bad_zip64_locator_gap_exits_nonzero(work: Path) -> None:
    root = work / "bad_zip64_locator_gap"
    root.mkdir()
    predictor = work / "predictor_bad_zip64_locator_gap.json"
    report = work / "bad_zip64_locator_gap_report.json"
    write_predictor(predictor)
    write_bad_zip64_locator_gap_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 2, result.stdout + result.stderr
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["totals"]["audit_failed"] is True
    assert "ZIP64 EOCD locator is not adjacent to record" in data["lanes"][0]["errors"][0]


def test_short_predictor_rejected(work: Path) -> None:
    root = work / "short_predictor"
    root.mkdir()
    predictor = work / "short_predictor.json"
    report = work / "short_predictor_report.json"
    write_short_predictor_with_later_strings(predictor)
    write_bad_incomplete_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 1
    assert "predictor comma missing" in result.stderr or "predictor string missing" in result.stderr
    assert not report.exists()


def test_extra_predictor_value_rejected(work: Path) -> None:
    root = work / "extra_predictor"
    root.mkdir()
    predictor = work / "extra_predictor.json"
    report = work / "extra_predictor_report.json"
    write_extra_value_predictor(predictor)
    write_bad_incomplete_zip(root)

    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(report),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 1
    assert "predictor array has extra values or missing close" in result.stderr
    assert not report.exists()


def test_malformed_predictor_rejected(work: Path) -> None:
    root = work / "malformed_predictor"
    root.mkdir()
    write_bad_incomplete_zip(root)

    predictor = work / "missing_colon_predictor.json"
    write_missing_colon_predictor(predictor)
    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(work / "missing_colon_report.json"),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 1
    assert "predictor key missing colon" in result.stderr

    predictor = work / "bad_hex_predictor.json"
    write_bad_hex_predictor(predictor)
    result = run_command(
        [
            str(EXE),
            "--root",
            str(root),
            "--predictor",
            str(predictor),
            "--limit-zips",
            "1",
            "--report",
            str(work / "bad_hex_report.json"),
            "--no-entropy-probe",
        ],
        check=False,
    )
    assert result.returncode == 1
    assert "predictor IV32 string has bad hex" in result.stderr


def test_negative_limit_argument_rejected() -> None:
    result = run_command([str(EXE), "--limit-zips", "-1", "--no-predictor"], check=False)
    assert result.returncode == 1
    assert "unsigned decimal integer" in result.stderr


def test_malformed_limit_arguments_rejected() -> None:
    for value in ("", " 1", "1x", "+1"):
        result = run_command([str(EXE), "--limit-zips", value, "--no-predictor"], check=False)
        assert result.returncode == 1
        assert "unsigned decimal integer" in result.stderr
    result = run_command([str(EXE), "--mode", "bench", "--bench-limits", "1,,2", "--no-predictor"], check=False)
    assert result.returncode == 1
    assert "unsigned decimal integer" in result.stderr


def test_mode_scoped_arguments_rejected() -> None:
    cases = [
        ([str(EXE), "--mode", "pack", "--bench-gpu", "--no-predictor"], "--bench options only apply to --mode bench"),
        ([str(EXE), "--mode", "audit", "--codec", "zstd", "--no-predictor"], "--codec and --codec-level only apply to --mode pack"),
        ([str(EXE), "--mode", "verify", "--external-predictor"], "--external-predictor only applies to --mode pack --level 3"),
        ([str(EXE), "--mode", "unpack", "--no-source-compare"], "--no-source-compare only applies to --mode verify"),
        ([str(EXE), "--mode", "pack", "--level", "0", "--codec-profile", "fast"], "--codec-profile only applies to pack levels 1..3"),
        ([str(EXE), "--mode", "pack", "--level", "0", "--codec", "none", "--codec-level", "9"], "--codec-level only applies to pack levels 1..3"),
    ]
    for args, expected_text in cases:
        result = run_command(args, check=False)
        assert result.returncode == 1
        assert expected_text in result.stderr


def test_report_tools_tolerate_partial_numeric_rows(work: Path) -> None:
    report = work / "partial_report.json"
    summary = work / "report_tools" / "summary.md"
    compare = work / "report_tools" / "compare.md"
    verify_cpu = work / "report_tools" / "cpu_verify_report.json"
    verify_gpu = work / "report_tools" / "gpu_verify_report.json"
    unpack_cpu = work / "report_tools" / "cpu_unpack_report.json"
    unpack_gpu = work / "report_tools" / "gpu_unpack_report.json"
    gpu_cache = work / "report_tools" / "gpu_cache_report.json"
    verify_summary = work / "report_tools" / "verify_summary.md"
    verify_compare = work / "report_tools" / "verify_compare.md"
    pack_report = work / "report_tools" / "pack_report.json"
    pack_summary_md = work / "report_tools" / "pack_summary.md"
    pack_compare_md = work / "report_tools" / "pack_compare.md"
    release_summary_md = work / "report_tools" / "release_summary.md"
    pack_fields_csv = work / "report_tools" / "pack_fields.csv"
    pack_lanes_csv = work / "report_tools" / "pack_lanes.csv"
    audit_report = work / "report_tools" / "audit_report.json"
    audit_summary_md = work / "report_tools" / "audit_summary.md"
    audit_lanes_csv = work / "report_tools" / "audit_lanes.csv"
    report.write_text(
        json.dumps(
            {
                "schema": "spc3_streaming_compression_oracle.v1",
                "streaming": True,
                "samples": [
                    {
                        "lane_count": 1,
                        "current_zip_bytes": None,
                        "exception_stats": {},
                        "spc3_levels": [
                            {
                                "level": 3,
                                "size_bytes": None,
                                "unpack_ms": None,
                                "decode_mib_s": None,
                                "decode_crc_mismatches": None,
                            }
                        ],
                        "gpu_offload": {
                            "status": "ok",
                            "backend": "cuda_driver_nvrtc",
                            "output_bytes": None,
                            "kernel_ms": "not-a-number",
                            "mismatched_lanes": None,
                        },
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    run_command([sys.executable, str(REPORT_TOOLS), "summary", str(report), "--output", str(summary)])
    summary_text = summary.read_text(encoding="utf-8")
    assert "| 1 | spc3_level | default | 3 | 0 | 0.000 | 0.000 | 0 |" in summary_text
    assert "| 1 | gpu_offload | cuda_driver_nvrtc | 3 | 0 | 0.000 | 0.000 | 0 | ok |" in summary_text

    run_command([sys.executable, str(REPORT_TOOLS), "compare", str(report), str(report), "--output", str(compare)])
    compare_text = compare.read_text(encoding="utf-8")
    assert "| 1 | spc3_level | default | 3 | 0 | 0 | 0 | 0.000 | 0.000 | 0.000 |" in compare_text
    assert "| 1 | gpu_offload | cuda_driver_nvrtc | 3 | 0 | 0 | 0 | 0.000 | 0.000 | 0.000 |" in compare_text

    verify_cpu.write_text(
        json.dumps(
            {
                "schema": "spc3_verify_report.v1",
                "mode": "verify",
                "ok": True,
                "level": 3,
                "lane_count": 4,
                "internal_crc_mismatches": 0,
                "source_compare_enabled": True,
                "source_compare_mismatches": 0,
                "gpu_rebuild": {
                    "status": "not_requested",
                    "requested": False,
                    "used": False,
                    "fallback_reason": "",
                    "download_mode": "none",
                    "runtime_cache_hit": False,
                    "runtime_failure_cached": False,
                    "runtime_initializations": 0,
                    "output_bytes": 20971520,
                    "value_count": 0,
                    "mismatched_lanes": 0,
                    "mismatched_bytes": 0,
                    "compile_ms": 0.0,
                    "kernel_ms": 0.0,
                    "host_crc_ms": 0.0,
                    "total_ms": 0.0,
                },
                "cpu_decode_profile": {
                    "used": True,
                    "crc_backend": "zlib_crc32",
                    "lane_count": 4,
                    "typed_lanes": 4,
                    "crc_bytes": 20971520,
                    "stream_decode_ms": 1.5,
                    "iv_expand_ms": 2.5,
                    "rebuild_encrypt_ms": 3.5,
                    "crc_ms": 4.5,
                    "total_ms": 12.0,
                },
                "total_ms": 20.0,
            }
        ),
        encoding="utf-8",
    )
    verify_gpu.write_text(
        json.dumps(
            {
                "schema": "spc3_verify_report.v1",
                "mode": "verify",
                "ok": True,
                "level": 3,
                "lane_count": 4,
                "internal_crc_mismatches": 0,
                "source_compare_enabled": True,
                "source_compare_mismatches": 0,
                "gpu_rebuild": {
                    "status": "ok",
                    "requested": True,
                    "used": True,
                    "fallback_reason": "",
                    "download_mode": "bulk",
                    "runtime_cache_hit": True,
                    "runtime_failure_cached": False,
                    "runtime_initializations": 1,
                    "output_bytes": 20971520,
                    "value_count": 4954,
                    "mismatched_lanes": 0,
                    "mismatched_bytes": 0,
                    "compile_ms": 173.25,
                    "kernel_ms": 1.25,
                    "host_crc_ms": 2.0,
                    "total_ms": 200.0,
                },
                "cpu_decode_profile": {
                    "used": False,
                    "crc_backend": "zlib_crc32",
                    "lane_count": 0,
                    "typed_lanes": 0,
                    "crc_bytes": 0,
                    "stream_decode_ms": 0.0,
                    "iv_expand_ms": 0.0,
                    "rebuild_encrypt_ms": 0.0,
                    "crc_ms": 0.0,
                    "total_ms": 0.0,
                },
                "total_ms": 210.0,
            }
        ),
        encoding="utf-8",
    )

    run_command([sys.executable, str(REPORT_TOOLS), "summary", str(verify_cpu), "--output", str(verify_summary)])
    verify_summary_text = verify_summary.read_text(encoding="utf-8")
    assert "| internal crc mismatches | 0 |" in verify_summary_text
    assert "| source compare mismatches | 0 |" in verify_summary_text
    assert "| gpu status | not_requested |" in verify_summary_text
    assert "| gpu download mode | none |" in verify_summary_text
    assert "| cpu crc backend | zlib_crc32 |" in verify_summary_text
    assert "| cpu crc bytes | 20971520 |" in verify_summary_text
    assert "| cpu crc ms | 4.5 |" in verify_summary_text

    run_command([sys.executable, str(REPORT_TOOLS), "compare", str(verify_cpu), str(verify_gpu), "--output", str(verify_compare)])
    verify_compare_text = verify_compare.read_text(encoding="utf-8")
    assert "| gpu used | false | true |  |" in verify_compare_text
    assert "| gpu download mode | none | bulk |  |" in verify_compare_text
    assert "| gpu runtime cache hit | false | true |  |" in verify_compare_text
    assert "| gpu value count | 0 | 4954 | 4954.000 |" in verify_compare_text
    assert "| gpu kernel ms | 0.0 | 1.25 | 1.250 |" in verify_compare_text
    assert "| gpu host crc ms | 0.0 | 2.0 | 2.000 |" in verify_compare_text
    assert "| source compare mismatches | 0 | 0 | 0.000 |" in verify_compare_text

    cpu_unpack_data = json.loads(verify_cpu.read_text(encoding="utf-8"))
    cpu_unpack_data.update({"schema": "spc3_unpack_report.v1", "mode": "unpack", "crc_mismatches": 0})
    unpack_cpu.write_text(json.dumps(cpu_unpack_data), encoding="utf-8")
    gpu_unpack_data = json.loads(verify_gpu.read_text(encoding="utf-8"))
    gpu_unpack_data.update({"schema": "spc3_unpack_report.v1", "mode": "unpack", "crc_mismatches": 0})
    unpack_gpu.write_text(json.dumps(gpu_unpack_data), encoding="utf-8")

    gpu_cache.write_text(
        json.dumps(
            {
                "schema": "spc3_streaming_compression_oracle.v1",
                "mode": "bench",
                "samples": [
                    {
                        "lane_count": 1,
                        "gpu_offload": {
                            "status": "ok",
                            "used": True,
                            "download_mode": "bulk",
                            "runtime_cache_hit": False,
                            "runtime_initializations": 1,
                            "compile_ms": 173.0,
                            "upload_ms": 1.0,
                            "kernel_ms": 0.5,
                            "download_ms": 2.0,
                            "total_ms": 200.0,
                            "mismatched_lanes": 0,
                            "mismatched_bytes": 0,
                        },
                    },
                    {
                        "lane_count": 4,
                        "gpu_offload": {
                            "status": "ok",
                            "used": True,
                            "download_mode": "bulk",
                            "runtime_cache_hit": True,
                            "runtime_initializations": 1,
                            "compile_ms": 0.0,
                            "upload_ms": 2.0,
                            "kernel_ms": 1.5,
                            "download_ms": 3.0,
                            "total_ms": 50.0,
                            "mismatched_lanes": 0,
                            "mismatched_bytes": 0,
                        },
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    pack_report.write_text(
        json.dumps(
            {
                "schema": "spc3_pack_report.v1",
                "mode": "pack",
                "ok": True,
                "version": 2,
                "level": 3,
                "lane_count": 1,
                "typed_level3": True,
                "codec": "typed-level3",
                "codec_profile": "fast",
                "output": "artifact|name.spc3\ncontinued",
                "spc3_size_bytes": 1234,
                "source_zip_bytes": 5678,
                "raw_payload_bytes": 4096,
                "roundtrip_mismatches": 0,
                "build_ms": 9.5,
                "lanes": [
                    {
                        "lane": "0x0001",
                        "codec": "typed-level3",
                        "codec_level": 0,
                        "stream_size": 321,
                        "uncompressed_model_size": 654,
                        "predictor_exceptions": 7,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_command([sys.executable, str(REPORT_TOOLS), "summary", str(pack_report), "--output", str(pack_summary_md)])
    pack_summary_text = pack_summary_md.read_text(encoding="utf-8")
    assert "artifact\\|name.spc3 continued" in pack_summary_text

    run_command([sys.executable, str(REPORT_TOOLS), "compare", str(pack_report), str(pack_report), "--output", str(pack_compare_md)])
    pack_compare_text = pack_compare_md.read_text(encoding="utf-8")
    assert "| output | artifact\\|name.spc3 continued | artifact\\|name.spc3 continued |  |" in pack_compare_text

    run_command([sys.executable, str(REPORT_TOOLS), "summary", str(pack_report), "--format", "csv", "--output", str(pack_fields_csv)])
    pack_fields_text = pack_fields_csv.read_text(encoding="utf-8")
    assert b"\r\n" not in pack_fields_csv.read_bytes()
    assert "field,value" in pack_fields_text
    assert "spc3 size bytes,1234" in pack_fields_text
    assert "lane table rows,1" in pack_fields_text

    run_command(
        [
            sys.executable,
            str(REPORT_TOOLS),
            "summary",
            str(pack_report),
            "--format",
            "csv",
            "--table",
            "lanes",
            "--output",
            str(pack_lanes_csv),
        ]
    )
    pack_lanes_text = pack_lanes_csv.read_text(encoding="utf-8")
    assert "lane,level,codec,codec_level" in pack_lanes_text
    assert "0x0001,3,typed-level3,0" in pack_lanes_text

    audit_report.write_text(
        json.dumps(
            {
                "schema": "spc3_phase3_cpu_prototype_report.v1",
                "config": {
                    "hotloop_backend": "x86_64_asm",
                    "predictor_loaded": True,
                    "limit_zips": 1,
                    "zips_found_for_run": 1,
                },
                "totals": {
                    "audit_failed": True,
                    "records_processed": 65535,
                    "lane_error_count": 1,
                    "duplicate_entries": 0,
                    "missing_entries": 1,
                    "checksum_failures": 1,
                    "content_pid_mismatches": 0,
                    "template_mismatches": 0,
                    "predictor_exceptions": 2,
                    "predictor_roundtrip_mismatches": 0,
                    "rebuild_mismatches": 1,
                    "zip_size_bytes": 123,
                    "raw_payload_bytes": 456,
                },
                "timings_ms": {
                    "inflate_ms": 1.25,
                    "decrypt_model_ms": 2.5,
                    "rebuild_ms": 3.75,
                    "entropy_probe_ms": 0.5,
                    "total_ms": 8.0,
                },
                "lanes": [
                    {
                        "lane": "0x0001",
                        "zip_size_bytes": 123,
                        "entry_count": 65535,
                        "audit_failed": True,
                        "predictor_exceptions": 2,
                        "checksum_failures": 1,
                        "template_mismatches": 0,
                        "rebuild_mismatches": 1,
                        "timings_ms": {"total_ms": 4.5},
                        "errors": ["bad|entry\ncontinued"],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    run_command([sys.executable, str(REPORT_TOOLS), "summary", str(audit_report), "--output", str(audit_summary_md)])
    audit_summary_text = audit_summary_md.read_text(encoding="utf-8")
    assert "| hotloop backend | x86_64_asm |" in audit_summary_text
    assert "| audit failed | true |" in audit_summary_text
    assert "| records processed | 65535 |" in audit_summary_text
    assert (
        "| 0x0001 | true | 65,535 | 123 | 2 | 1 | 0 | 1 | 4.500 | bad\\|entry continued |"
        in audit_summary_text
    )

    run_command(
        [
            sys.executable,
            str(REPORT_TOOLS),
            "summary",
            str(audit_report),
            "--format",
            "csv",
            "--table",
            "lanes",
            "--output",
            str(audit_lanes_csv),
        ]
    )
    audit_lanes_text = audit_lanes_csv.read_text(encoding="utf-8")
    assert "lane,audit_failed,entry_count,zip_size_bytes" in audit_lanes_text
    assert "0x0001,true,65535,123,2,1,0,1,4.5," in audit_lanes_text

    run_command(
        [
            sys.executable,
            str(REPORT_TOOLS),
            "release-summary",
            "--pack",
            str(pack_report),
            "--cpu-verify",
            str(verify_cpu),
            "--gpu-verify",
            str(verify_gpu),
            "--cpu-unpack",
            str(unpack_cpu),
            "--gpu-unpack",
            str(unpack_gpu),
            "--gpu-cache",
            str(gpu_cache),
            "--output",
            str(release_summary_md),
        ]
    )
    release_summary_text = release_summary_md.read_text(encoding="utf-8")
    assert "# SPC3 v0.2 Typed Level-3 Release Gate" in release_summary_text
    assert "| codec profile | fast |" in release_summary_text
    assert "| cpu verify | true | spc3_verify_report.v1 | not_requested | false | none | 0/0/0/0 | 20.000 | 12.000 |" in release_summary_text
    assert "| gpu verify | true | spc3_verify_report.v1 | ok | true | none | 0/0/0/0 | 210.000 | 200.000 |" in release_summary_text
    assert "| cpu verify | zlib_crc32 | 20,971,520 | 1.500 | 2.500 | 3.500 | 4.500 | 12.000 |" in release_summary_text
    assert "| gpu verify |  | ok | true | none | bulk | true | false | 1 | 4,954 | 173.250 |" in release_summary_text
    assert "| 4 | ok | true | bulk | true | 1 | 0.000 |" in release_summary_text
    assert "Keep targeted PK3 shuffle ASM active" in release_summary_text


def test_gui_report_helpers_show_release_evidence() -> None:
    gui = load_gui_module()
    report_summary_lines = getattr(gui, "report_summary_lines")
    comparison_lines = getattr(gui, "comparison_lines")

    pack_ok = {
        "schema": "spc3_pack_report.v1",
        "mode": "pack",
        "ok": True,
        "version": 2,
        "level": 3,
        "lane_count": 4,
        "codec": "typed-level3",
        "codec_profile": "fast",
        "typed_level3": True,
        "spc3_size_bytes": 1234,
        "source_zip_bytes": 5678,
        "raw_payload_bytes": 4096,
        "roundtrip_mismatches": 0,
        "build_ms": 9.5,
        "total_ms": 10.5,
    }
    pack_bad = dict(pack_ok)
    pack_bad["ok"] = False
    pack_bad["roundtrip_mismatches"] = 2
    pack_bad["build_ms"] = 13.0

    summary_text = "\n".join(report_summary_lines(pack_ok))
    assert "roundtrip mismatches: 0" in summary_text
    assert "build ms: 9.5" in summary_text
    assert "total ms: 10.5" in summary_text

    compare_text = "\n".join(comparison_lines(pack_ok, pack_bad, "pack-ok.json", "pack-bad.json"))
    assert "roundtrip mismatches: 0 | 2 delta=2.000" in compare_text
    assert "build ms: 9.5 | 13.0 delta=3.500" in compare_text

    verify_cpu = {
        "schema": "spc3_verify_report.v1",
        "mode": "verify",
        "ok": True,
        "level": 3,
        "lane_count": 4,
        "source_compare_enabled": True,
        "source_compare_mismatches": 0,
        "gpu_rebuild": {
            "status": "not_requested",
            "requested": False,
            "used": False,
            "fallback_reason": "",
            "download_mode": "none",
            "runtime_cache_hit": False,
            "runtime_failure_cached": False,
            "runtime_initializations": 0,
            "output_bytes": 20971520,
            "value_count": 0,
            "mismatched_lanes": 0,
            "mismatched_bytes": 0,
            "upload_ms": 0.0,
            "host_crc_ms": 0.0,
        },
        "cpu_decode_profile": {
            "used": True,
            "crc_backend": "zlib_crc32",
            "lane_count": 4,
            "typed_lanes": 4,
            "legacy_lanes": 0,
            "crc_bytes": 20971520,
            "stream_decode_ms": 1.5,
            "iv_expand_ms": 2.5,
            "rebuild_encrypt_ms": 3.5,
            "crc_ms": 4.5,
            "total_ms": 12.0,
        },
    }
    verify_gpu = dict(verify_cpu)
    verify_gpu["gpu_rebuild"] = {
        "status": "ok",
        "requested": True,
        "used": True,
        "fallback_reason": "",
        "download_mode": "bulk",
        "runtime_cache_hit": True,
        "runtime_failure_cached": False,
        "runtime_initializations": 1,
        "output_bytes": 20971520,
        "value_count": 4954,
        "mismatched_lanes": 0,
        "mismatched_bytes": 0,
        "upload_ms": 1.25,
        "host_crc_ms": 2.0,
    }
    verify_gpu["cpu_decode_profile"] = {
        "used": False,
        "crc_backend": "zlib_crc32",
        "lane_count": 0,
        "typed_lanes": 0,
        "legacy_lanes": 0,
        "crc_bytes": 0,
    }

    verify_summary_text = "\n".join(report_summary_lines(verify_cpu))
    assert "source compare enabled: true" in verify_summary_text
    assert "gpu: not_requested requested=false used=false" in verify_summary_text
    assert "gpu download mode: none" in verify_summary_text
    assert "cpu crc backend: zlib_crc32" in verify_summary_text
    assert "cpu decode ms: used=true lanes=4 typed=4 legacy=0" in verify_summary_text

    verify_compare_text = "\n".join(comparison_lines(verify_cpu, verify_gpu, "cpu.json", "gpu.json"))
    assert "gpu requested: false | true" in verify_compare_text
    assert "gpu download mode: none | bulk" in verify_compare_text
    assert "gpu runtime cache hit: false | true" in verify_compare_text
    assert "gpu upload ms: 0.0 | 1.25 delta=1.250" in verify_compare_text
    assert "gpu host crc ms: 0.0 | 2.0 delta=2.000" in verify_compare_text
    assert "cpu typed lanes: 4 | 0 delta=-4.000" in verify_compare_text


def main() -> None:
    run_command(["cmd", "/c", str(BUILD)])
    run_command([str(EXE), "--self-test"])
    test_server_mode_reuses_one_process()
    test_negative_limit_argument_rejected()
    test_malformed_limit_arguments_rejected()
    test_mode_scoped_arguments_rejected()
    with tempfile.TemporaryDirectory(prefix="spc3_prototype_tests_") as tmp:
        work = Path(tmp)
        test_full_good_lane(work)
        test_full_stored_lane(work)
        test_spc3_pack_unpack_verify_and_bench_modes(work)
        test_pack_all_zips_accepts_sparse_corpus_without_lane_zero(work)
        test_full_content_pid_mismatch_lane_exits_nonzero(work)
        test_full_bad_checksum_lane_exits_nonzero(work)
        test_full_template_mismatch_lane_exits_nonzero(work)
        test_bad_lane_exits_nonzero(work)
        test_bad_duplicate_lane_exits_nonzero(work)
        test_bad_entry_lane_exits_nonzero(work)
        test_bad_unsupported_method_exits_nonzero(work)
        test_bad_encrypted_flag_exits_nonzero(work)
        test_bad_local_header_name_exits_nonzero(work)
        test_bad_trailing_deflate_exits_nonzero(work)
        test_bad_crc_exits_nonzero(work)
        test_bad_local_method_exits_nonzero(work)
        test_bad_local_flags_exits_nonzero(work)
        test_bad_local_size_exits_nonzero(work)
        test_bad_data_descriptor_flag_exits_nonzero(work)
        test_eocd_comment_zip_reaches_lane_audit(work)
        test_bad_central_trailing_exits_nonzero(work)
        test_bad_short_zip64_extra_exits_nonzero(work)
        test_valid_local_zip64_size_reaches_lane_audit(work)
        test_bad_central_truncated_extra_exits_nonzero(work)
        test_bad_local_truncated_extra_exits_nonzero(work)
        test_bad_multidisk_eocd_exits_nonzero(work)
        test_bad_zip64_locator_exits_nonzero(work)
        test_bad_zip64_multidisk_locator_exits_nonzero(work)
        test_bad_zip64_eocd_size_exits_nonzero(work)
        test_bad_zip64_locator_gap_exits_nonzero(work)
        test_short_predictor_rejected(work)
        test_extra_predictor_value_rejected(work)
        test_malformed_predictor_rejected(work)
        test_report_tools_tolerate_partial_numeric_rows(work)
        test_gui_report_helpers_show_release_evidence()
    print("spc3 prototype regression tests ok")


if __name__ == "__main__":
    main()
