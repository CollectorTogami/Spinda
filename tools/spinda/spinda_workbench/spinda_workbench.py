#!/usr/bin/env python3
"""Unified read-only Spinda project workbench.

The workbench is an operator dashboard for the post-Phase-3 project shape. It
does not launch workers, mutate saves, or open ZIP contents in the hot status
path. It scans file names and lightweight status files, shows readiness for the
next proof stages, and prints the exact commands for the heavier validators.
"""

from __future__ import annotations

import argparse
import heapq
import json
import math
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable

try:
    from flask import Flask, jsonify, request
except ImportError as exc:  # pragma: no cover - operator environment check.
    raise SystemExit(
        "Flask is required for Spinda Workbench. Install it with:\n"
        "python -m pip install Flask"
    ) from exc


# Keep the operator-facing path lexical. `resolve()` can follow the local
# vendor/junction backing path and make command previews point outside <repo-root>.
ROOT = Path(__file__).absolute().parents[3]
DEFAULT_PHASE3_DIR = ROOT / "Phase3SpindaBlocks"
DEFAULT_TSV_DIR = ROOT / "TSVs"
DEFAULT_HATCH_OUTPUT_DIR = ROOT / "HatchedSpindaZips"
DEFAULT_7Z_OUTPUT_DIR = ROOT / "Spinda7zArchives"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8780
DEFAULT_SAMPLE_LIMIT = 16
DEFAULT_TARGET_PHASE3_LANES = 0xFFFE
SPINDAS_PER_LANE = 0x10000
EXPECTED_TSVS = 8192
MIN_FINAL_ZIP_BYTES = 1024

ZIP_RE = re.compile(r"^0x(?P<lane>[0-9A-Fa-f]{4})\.spinda80\.zip$", re.IGNORECASE)
TMP_RE = re.compile(r"^0x[0-9A-Fa-f]{4}\.spinda80\.zip\..*\.tmp$", re.IGNORECASE)
TSV_SAVE_RE = re.compile(r"^TSV-(?P<tsv>\d{4})-sid-(?P<sid>\d{5})\.sav$", re.IGNORECASE)
HEX_PID_RE = re.compile(r"^(?:0x)?(?P<pid>[0-9A-Fa-f]{8})(?:\.pk3)?$", re.IGNORECASE)
DEFAULT_SUGGESTION_SCAN = 8192
DEFAULT_SUGGESTION_COUNT = 12
MAX_SUGGESTION_SCAN = 1_000_000
MAX_SUGGESTION_COUNT = 200
SPINDA_PAINTER_REFERENCE_URL = "https://spindapainter.neocities.org/"

NATURES = (
    "Hardy",
    "Lonely",
    "Brave",
    "Adamant",
    "Naughty",
    "Bold",
    "Docile",
    "Relaxed",
    "Impish",
    "Lax",
    "Timid",
    "Hasty",
    "Serious",
    "Jolly",
    "Naive",
    "Modest",
    "Mild",
    "Quiet",
    "Bashful",
    "Rash",
    "Calm",
    "Gentle",
    "Sassy",
    "Careful",
    "Quirky",
)

# Mini-preview coordinates from the original Spinda Painter layout. Each PID
# nibble moves the matching spot by 0..15 pixels from this base anchor.
SPOT_ANCHORS = (
    ("upper_left", 10, 13, 12, 12),
    ("upper_right", 34, 14, 13, 13),
    ("lower_left", 16, 31, 7, 9),
    ("lower_right", 28, 32, 8, 9),
)
PATTERN_SCORE_KEYS = {
    "centered": "centered_score",
    "balanced": "balance_score",
    "clustered": "cluster_score",
    "cursed": "cursed_score",
    "eye": "eye_cover_score",
    "eye_cover": "eye_cover_score",
    "funny": "funny_score",
    "heart": "heartish_score",
    "spread": "spread_score",
    "symmetry": "vertical_symmetry_score",
    "vertical_symmetry": "vertical_symmetry_score",
    "horizontal_symmetry": "horizontal_symmetry_score",
}
TRAIT_KEYS = (
    "balance_score",
    "centered_score",
    "cluster_score",
    "cursed_score",
    "eye_cover_score",
    "funny_score",
    "heartish_score",
    "horizontal_symmetry_score",
    "lower_face_cover_score",
    "spread_score",
    "vertical_symmetry_score",
)
# Tuple order is the JSON/report display contract. Append new scores at the end
# unless tests and saved UI expectations change.


def _sample_append(samples: dict[str, list[str]], key: str, value: str, limit: int) -> None:
    """Append bounded status samples for UI display."""

    if limit <= 0:
        return
    if len(samples.setdefault(key, [])) < limit:
        samples[key].append(value)


def _percent(done: int, total: int) -> float:
    """Return a stable percent for JSON/UI use."""

    if total <= 0:
        return 0.0
    return round((done / total) * 100.0, 4)


def _age_seconds(path: Path) -> float | None:
    """Return file age in seconds, or None when the file is absent."""

    try:
        return max(0.0, time.time() - path.stat().st_mtime)
    except OSError:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    """Read JSON object with a report-safe error payload."""

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception as error:  # noqa: BLE001 - dashboard reports the error.
        return {"_load_error": str(error)}
    return data if isinstance(data, dict) else {"_load_error": "top-level JSON is not an object"}


def lane_ids_to_ranges(lane_ids: Iterable[int]) -> list[str]:
    """Return compact lane ranges such as `0x0001-0x0004`."""

    lanes = sorted({int(lane) for lane in lane_ids if 0 <= int(lane) <= 0xFFFF})
    if not lanes:
        return []
    ranges: list[str] = []
    start = previous = lanes[0]
    for lane in lanes[1:]:
        if lane == previous + 1:
            previous = lane
            continue
        ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
        start = previous = lane
    ranges.append(f"0x{start:04X}" if start == previous else f"0x{start:04X}-0x{previous:04X}")
    return ranges


def discover_lan_ipv4_addresses() -> tuple[str, ...]:
    """Return likely LAN IPv4 addresses for URL hints."""

    addresses: set[str] = set()
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.connect(("8.8.8.8", 80))
            addresses.add(str(probe.getsockname()[0]))
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            addresses.add(str(info[4][0]))
    except OSError:
        pass
    return tuple(
        sorted(
            address
            for address in addresses
            if not address.startswith("127.") and not address.startswith("169.254.")
        )
    )


def workbench_urls(bind_host: str, port: int) -> tuple[str, ...]:
    """Return concrete browser URLs for the selected bind address."""

    host = bind_host.strip() or DEFAULT_HOST
    bracketed_host = f"[{host}]" if ":" in host and not host.startswith("[") else host
    if host in {"0.0.0.0", "::"}:
        urls = [f"http://127.0.0.1:{port}/"]
        if host == "::":
            urls.insert(0, f"http://[::1]:{port}/")
        urls.extend(f"http://{address}:{port}/" for address in discover_lan_ipv4_addresses())
        if len(urls) == 1:
            urls.append(f"http://<this-pc-ip>:{port}/")
        return tuple(dict.fromkeys(urls))
    return (f"http://{bracketed_host}:{port}/",)


@dataclass(frozen=True)
class WorkbenchConfig:
    """Paths and scan settings for the read-only workbench."""

    phase3_dir: Path = DEFAULT_PHASE3_DIR
    tsv_dir: Path = DEFAULT_TSV_DIR
    hatch_output_dir: Path = DEFAULT_HATCH_OUTPUT_DIR
    seven_zip_output_dir: Path = DEFAULT_7Z_OUTPUT_DIR
    target_phase3_lanes: int = DEFAULT_TARGET_PHASE3_LANES
    sample_limit: int = DEFAULT_SAMPLE_LIMIT
    display_url: str | None = None


@dataclass(frozen=True)
class Phase3Summary:
    """Filename-only Phase 3 ZIP status."""

    folder: str
    target_lanes: int
    complete_lanes: int
    zip_files: int
    missing_lanes: int
    completed_spindas: int
    target_spindas: int
    progress_percent: float
    bad_names: int
    zero_size_zips: int
    tiny_zips: int
    tmp_files: int
    duplicate_lanes: int
    out_of_scope_zips: int
    bad_artifacts: int
    last_good_lane: str | None
    complete_lane_ranges: list[str]
    samples: dict[str, list[str]]

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class TsvSummary:
    """TSV save-bank status from names plus local ledger."""

    folder: str
    expected_saves: int
    complete_saves: int
    missing_saves: int
    progress_percent: float
    save_files: int
    invalid_files: int
    mismatched_files: int
    duplicate_tsvs: int
    duplicate_files: int
    ledger_path: str
    ledger_exists: bool
    ledger_done: int | None
    ledger_errors: int | None
    ledger_load_error: str | None
    recent_saves: list[dict[str, Any]]
    samples: dict[str, list[str]]

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class PidLocation:
    """Archive coordinates for one PID."""

    pid: str
    upper: str
    lower: str
    lane_zip: str
    entry_name: str
    expected_psv: int
    matching_tsv: int
    matching_sid_min: int
    matching_sid_max: int
    zip_exists: bool
    note: str

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass(frozen=True)
class SpindaSpot:
    """One original Spinda Painter spot rectangle."""

    name: str
    offset_x: int
    offset_y: int
    x: int
    y: int
    width: int
    height: int
    center_x: float
    center_y: float

    def to_json(self) -> dict[str, Any]:
        return self.__dict__.copy()


def _parse_pid_text(pid_text: str) -> int:
    """Parse an 8-hex-digit PID or `.pk3` entry name."""

    match = HEX_PID_RE.match(pid_text.strip())
    if not match:
        raise ValueError("PID must be 8 hex digits, with optional 0x prefix or .pk3 suffix")
    return int(match.group("pid"), 16)


def _parse_int_text(raw: str, *, minimum: int, maximum: int, name: str) -> int:
    """Parse decimal or `0x` integer text and enforce an inclusive range."""

    try:
        value = int(raw.strip(), 0)
    except ValueError as error:
        raise ValueError(f"{name} must be decimal or 0x-prefixed integer text") from error
    if not minimum <= value <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return value


def _query_int(name: str, default: int, *, minimum: int, maximum: int) -> int:
    """Parse an optional integer query value."""

    raw = request.args.get(name)
    if raw is None or raw.strip() == "":
        return default
    return _parse_int_text(raw, minimum=minimum, maximum=maximum, name=name)


def _locate_pid_int(pid: int, phase3_dir: Path) -> PidLocation:
    """Map a parsed PID to archive coordinates and TID0 TSV math."""

    upper = (pid >> 16) & 0xFFFF
    lower = pid & 0xFFFF
    psv = (upper ^ lower) >> 3
    zip_path = phase3_dir / f"0x{lower:04X}.spinda80.zip"
    zip_exists = zip_path.is_file()
    note = "ZIP file exists; deep validator proves entry later." if zip_exists else "ZIP file not present yet."
    return PidLocation(
        pid=f"0x{pid:08X}",
        upper=f"0x{upper:04X}",
        lower=f"0x{lower:04X}",
        lane_zip=str(zip_path),
        entry_name=f"0x{pid:08X}.pk3",
        expected_psv=psv,
        matching_tsv=psv,
        matching_sid_min=psv << 3,
        matching_sid_max=(psv << 3) | 7,
        zip_exists=zip_exists,
        note=note,
    )


def spinda_spots(pid: int) -> tuple[SpindaSpot, ...]:
    """Return spot rectangles from PID nibbles using Spinda Painter ordering.

    The original painter stores spot 1 X/Y in the lowest two PID nibbles, spot
    2 in the next two, and so on. This keeps the local atlas compatible with
    the original drag-grid behavior while avoiding any vendored image assets.
    """

    nibbles = _pid_nibbles_low_first(pid)
    spots: list[SpindaSpot] = []
    for index, (name, base_x, base_y, width, height) in enumerate(SPOT_ANCHORS):
        offset_x = nibbles[index * 2]
        offset_y = nibbles[(index * 2) + 1]
        x = base_x + offset_x
        y = base_y + offset_y
        spots.append(
            SpindaSpot(
                name=name,
                offset_x=offset_x,
                offset_y=offset_y,
                x=x,
                y=y,
                width=width,
                height=height,
                center_x=round(x + (width / 2), 3),
                center_y=round(y + (height / 2), 3),
            )
        )
    return tuple(spots)


def _pid_nibbles_low_first(pid: int) -> tuple[int, int, int, int, int, int, int, int]:
    """Return PID nibbles in original painter drag-grid order.

    This function is intentionally tiny and allocation-light because the pattern
    suggestion endpoint can call it hundreds of thousands of times per request.
    """

    return (
        pid & 0xF,
        (pid >> 4) & 0xF,
        (pid >> 8) & 0xF,
        (pid >> 12) & 0xF,
        (pid >> 16) & 0xF,
        (pid >> 20) & 0xF,
        (pid >> 24) & 0xF,
        (pid >> 28) & 0xF,
    )


def _spot_centers_from_pid(pid: int) -> tuple[float, ...]:
    """Return only spot centers, the hot data needed for search scoring.

    The rich `SpindaSpot` dataclass is useful for JSON reports and SVG output.
    Search scoring only needs four center points, so this path avoids per-PID
    dataclass construction during bounded scans. The tuple is flat
    `(x1, y1, x2, y2, x3, y3, x4, y4)` so the hot loop does not allocate four
    nested coordinate tuples for every PID.
    """

    nibbles = _pid_nibbles_low_first(pid)
    return (
        16.0 + nibbles[0],
        19.0 + nibbles[1],
        40.5 + nibbles[2],
        20.5 + nibbles[3],
        19.5 + nibbles[4],
        35.5 + nibbles[5],
        32.0 + nibbles[6],
        36.5 + nibbles[7],
    )


def _distance(a_x: float, a_y: float, b_x: float, b_y: float) -> float:
    """Return Euclidean distance in mini-preview pixels."""

    return math.hypot(a_x - b_x, a_y - b_y)


def _score_from_distance(distance: float, max_distance: float) -> float:
    """Convert a distance into a 0..100 score where closer is better."""

    if max_distance <= 0:
        return 0.0
    return round(max(0.0, 100.0 - ((distance / max_distance) * 100.0)), 3)


def _mirror_score(left_x: float, left_y: float, right_x: float, right_y: float, *, center_x: float = 26.0) -> float:
    """Score how well two spots mirror each other around the face centerline."""

    mirror_error_x = abs((left_x - center_x) + (right_x - center_x))
    y_error = abs(left_y - right_y)
    return _score_from_distance(math.hypot(mirror_error_x, y_error), 24.0)


def _horizontal_mirror_score(top_x: float, top_y: float, bottom_x: float, bottom_y: float, *, center_y: float = 30.0) -> float:
    """Score how well two spots mirror each other around the horizontal midline."""

    x_error = abs(top_x - bottom_x)
    mirror_error_y = abs((top_y - center_y) + (bottom_y - center_y))
    return _score_from_distance(math.hypot(x_error, mirror_error_y), 28.0)


def _average_center_distance_from_coords(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
) -> float:
    """Return average distance from all four spots to the face center."""

    return (
        _distance(x1, y1, 26.0, 30.0)
        + _distance(x2, y2, 26.0, 30.0)
        + _distance(x3, y3, 26.0, 30.0)
        + _distance(x4, y4, 26.0, 30.0)
    ) / 4.0


def _average_pair_distance_from_coords(
    x1: float,
    y1: float,
    x2: float,
    y2: float,
    x3: float,
    y3: float,
    x4: float,
    y4: float,
) -> float:
    """Return average distance across all six spot pairs."""

    pair_total = (
        _distance(x1, y1, x2, y2)
        + _distance(x1, y1, x3, y3)
        + _distance(x1, y1, x4, y4)
        + _distance(x2, y2, x3, y3)
        + _distance(x2, y2, x4, y4)
        + _distance(x3, y3, x4, y4)
    )
    return pair_total / 6.0


def spinda_stats(pid: int, *, tid: int = 0, sid: int = 0) -> dict[str, Any]:
    """Return original painter-style stats plus TID/SID shiny math."""

    upper = (pid >> 16) & 0xFFFF
    lower = pid & 0xFFFF
    shiny_value = (upper ^ lower ^ tid ^ sid) & 0xFFFF
    return {
        "pid_decimal": pid,
        "nature": NATURES[pid % len(NATURES)],
        "ability_slot": "First" if pid % 2 == 0 else "Second",
        "gender": "Male" if pid % 256 >= 127 else "Female",
        "tid": tid,
        "sid": sid,
        "rarity": shiny_value,
        "is_shiny": shiny_value < 8,
        "tid0_sid0_rarity": upper ^ lower,
        "tid0_sid0_is_shiny": (upper ^ lower) < 8,
    }


def spinda_traits(spots: tuple[SpindaSpot, ...]) -> dict[str, float]:
    """Calculate heuristic pattern scores for atlas search and taxonomy.

    Scores are intentionally descriptive, not canonical game mechanics. They
    use the same spot grid as the original painter, then rank likely visual
    traits such as eye cover, clustering, symmetry, and face balance.
    """

    centers = (
        spots[0].center_x,
        spots[0].center_y,
        spots[1].center_x,
        spots[1].center_y,
        spots[2].center_x,
        spots[2].center_y,
        spots[3].center_x,
        spots[3].center_y,
    )
    return dict(zip(TRAIT_KEYS, _trait_tuple_from_centers(centers)))


def _unpack_centers(centers: tuple[float, ...]) -> tuple[float, float, float, float, float, float, float, float]:
    """Validate and unpack the flat four-spot center tuple."""

    if len(centers) != 8:
        raise ValueError("exactly four flat spot centers are required")
    return (
        centers[0],
        centers[1],
        centers[2],
        centers[3],
        centers[4],
        centers[5],
        centers[6],
        centers[7],
    )


def _centered_score(centers: tuple[float, ...]) -> float:
    """Score average closeness of all spots to the face center."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    average_center_distance = _average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4)
    return _score_from_distance(average_center_distance, 28.0)


def _balance_score(centers: tuple[float, ...]) -> float:
    """Score whether the spot centroid lands near the face center."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    centroid_x = (x1 + x2 + x3 + x4) / 4.0
    centroid_y = (y1 + y2 + y3 + y4) / 4.0
    return _score_from_distance(_distance(centroid_x, centroid_y, 26.0, 30.0), 18.0)


def _average_pair_distance(centers: tuple[float, ...]) -> float:
    """Return the six-pair average distance used by cluster/spread scores."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    return _average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4)


def _cluster_score(centers: tuple[float, ...]) -> float:
    """Score spots that cluster close together."""

    return _score_from_distance(_average_pair_distance(centers), 32.0)


def _spread_score(centers: tuple[float, ...]) -> float:
    """Score spots that spread across the face."""

    return round(min(100.0, (_average_pair_distance(centers) / 28.0) * 100.0), 3)


def _eye_cover_score(centers: tuple[float, ...]) -> float:
    """Score top spots that land near the eye area."""

    x1, y1, x2, y2, *_ = _unpack_centers(centers)
    return (
        _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
        + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
    ) / 2.0


def _lower_face_cover_score(centers: tuple[float, ...]) -> float:
    """Score lower spots that land near mouth/cheek space."""

    *_, x3, y3, x4, y4 = _unpack_centers(centers)
    return (
        _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
        + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
    ) / 2.0


def _vertical_symmetry_score(centers: tuple[float, ...]) -> float:
    """Score left/right mirror symmetry for top and bottom spot pairs."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    return (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0


def _horizontal_symmetry_score(centers: tuple[float, ...]) -> float:
    """Score top/bottom mirror symmetry for left and right spot pairs."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    return (
        _horizontal_mirror_score(x1, y1, x3, y3)
        + _horizontal_mirror_score(x2, y2, x4, y4)
    ) / 2.0


def _heartish_score(centers: tuple[float, ...]) -> float:
    """Blend symmetry, centeredness, and lower face cover for heart-like reads."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
    centered = _score_from_distance(_average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 28.0)
    lower_face_cover = (
        _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
        + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
    ) / 2.0
    return (vertical_symmetry + centered + lower_face_cover) / 3.0


def _funny_score(centers: tuple[float, ...]) -> float:
    """Blend eye cover, symmetry, clustering, and centeredness for funny reads."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    eye_cover = (
        _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
        + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
    ) / 2.0
    vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
    cluster = _score_from_distance(_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 32.0)
    centered = _score_from_distance(_average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 28.0)
    return ((eye_cover * 2.0) + vertical_symmetry + cluster + centered) / 5.0


def _cursed_score(centers: tuple[float, ...]) -> float:
    """Blend face cover, spread, and asymmetry for odd-looking reads."""

    x1, y1, x2, y2, x3, y3, x4, y4 = _unpack_centers(centers)
    eye_cover = (
        _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
        + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
    ) / 2.0
    lower_face_cover = (
        _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
        + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
    ) / 2.0
    spread = round(
        min(100.0, (_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4) / 28.0) * 100.0),
        3,
    )
    vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
    return (eye_cover + lower_face_cover + spread + (100.0 - vertical_symmetry)) / 4.0


def _pid_score(pid: int, score_key: str) -> float:
    """Return one score directly from a PID without center-tuple allocation.

    Suggestion scans call this once per candidate PID. It intentionally repeats
    the eight center-coordinate assignments in one local block so the hot loop
    avoids creating the flat center tuple used by richer report code.
    """

    x1 = 16.0 + (pid & 0xF)
    y1 = 19.0 + ((pid >> 4) & 0xF)
    x2 = 40.5 + ((pid >> 8) & 0xF)
    y2 = 20.5 + ((pid >> 12) & 0xF)
    x3 = 19.5 + ((pid >> 16) & 0xF)
    y3 = 35.5 + ((pid >> 20) & 0xF)
    x4 = 32.0 + ((pid >> 24) & 0xF)
    y4 = 36.5 + ((pid >> 28) & 0xF)

    if score_key == "centered_score":
        return _score_from_distance(_average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 28.0)
    if score_key == "balance_score":
        return _score_from_distance(_distance((x1 + x2 + x3 + x4) / 4.0, (y1 + y2 + y3 + y4) / 4.0, 26.0, 30.0), 18.0)
    if score_key == "cluster_score":
        return _score_from_distance(_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 32.0)
    if score_key == "spread_score":
        return min(100.0, (_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4) / 28.0) * 100.0)
    if score_key == "eye_cover_score":
        return (
            _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
            + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
        ) / 2.0
    if score_key == "lower_face_cover_score":
        return (
            _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
            + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
        ) / 2.0
    if score_key == "vertical_symmetry_score":
        return (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
    if score_key == "horizontal_symmetry_score":
        return (
            _horizontal_mirror_score(x1, y1, x3, y3)
            + _horizontal_mirror_score(x2, y2, x4, y4)
        ) / 2.0
    if score_key == "heartish_score":
        vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
        centered = _score_from_distance(_average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 28.0)
        lower_face_cover = (
            _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
            + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
        ) / 2.0
        return (vertical_symmetry + centered + lower_face_cover) / 3.0
    if score_key == "funny_score":
        eye_cover = (
            _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
            + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
        ) / 2.0
        vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
        cluster = _score_from_distance(_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 32.0)
        centered = _score_from_distance(_average_center_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4), 28.0)
        return ((eye_cover * 2.0) + vertical_symmetry + cluster + centered) / 5.0
    if score_key == "cursed_score":
        eye_cover = (
            _score_from_distance(_distance(x1, y1, 19.5, 24.0), 18.0)
            + _score_from_distance(_distance(x2, y2, 38.0, 24.5), 18.0)
        ) / 2.0
        lower_face_cover = (
            _score_from_distance(_distance(x3, y3, 23.5, 39.5), 18.0)
            + _score_from_distance(_distance(x4, y4, 31.5, 40.0), 18.0)
        ) / 2.0
        spread = min(100.0, (_average_pair_distance_from_coords(x1, y1, x2, y2, x3, y3, x4, y4) / 28.0) * 100.0)
        vertical_symmetry = (_mirror_score(x1, y1, x2, y2) + _mirror_score(x3, y3, x4, y4)) / 2.0
        return (eye_cover + lower_face_cover + spread + (100.0 - vertical_symmetry)) / 4.0
    raise ValueError(f"unknown score key: {score_key}")


SCORE_FUNCTIONS = {
    "balance_score": _balance_score,
    "centered_score": _centered_score,
    "cluster_score": _cluster_score,
    "cursed_score": _cursed_score,
    "eye_cover_score": _eye_cover_score,
    "funny_score": _funny_score,
    "heartish_score": _heartish_score,
    "horizontal_symmetry_score": _horizontal_symmetry_score,
    "lower_face_cover_score": _lower_face_cover_score,
    "spread_score": _spread_score,
    "vertical_symmetry_score": _vertical_symmetry_score,
}


def _trait_tuple_from_centers(centers: tuple[float, ...]) -> tuple[float, ...]:
    """Calculate all taxonomy scores from four spot centers.

    This is the shared core for both rich PID reports and high-volume pattern
    suggestions. Each score comes from the same mode-specific helper used by
    search, so the full report cannot drift from optimized scan results.
    """

    _unpack_centers(centers)
    return tuple(round(SCORE_FUNCTIONS[key](centers), 3) for key in TRAIT_KEYS)


def spinda_trait_score(pid: int, score_key: str) -> float:
    """Return one taxonomy score from the mode-specific search path."""

    return round(_pid_score(pid, score_key), 3)


def spinda_trait_labels(traits: dict[str, float]) -> list[str]:
    """Return human-readable taxonomy labels from score thresholds."""

    labels: list[str] = []
    if traits["centered_score"] >= 70:
        labels.append("centered")
    if traits["balance_score"] >= 80:
        labels.append("balanced")
    if traits["eye_cover_score"] >= 65:
        labels.append("eye-covering")
    if traits["cluster_score"] >= 70:
        labels.append("clustered")
    if traits["spread_score"] >= 72:
        labels.append("wide-spread")
    if traits["vertical_symmetry_score"] >= 75:
        labels.append("rare vertical symmetry")
    if traits["horizontal_symmetry_score"] >= 75:
        labels.append("rare horizontal symmetry")
    if traits["heartish_score"] >= 70:
        labels.append("heart-ish")
    if traits["funny_score"] >= 70:
        labels.append("funny-face candidate")
    if traits["cursed_score"] >= 70:
        labels.append("cursed-face candidate")
    return labels or ["plain"]


def render_spinda_svg(pid: int, spots: tuple[SpindaSpot, ...], *, shiny: bool = False) -> str:
    """Render a compact original SVG preview for the local painter panel."""

    spot_color = "#90a038" if shiny else "#de6b39"
    body_color = "#f1dfc5" if not shiny else "#efe8ba"
    spot_ellipses = "\n".join(
        (
            f'<ellipse cx="{spot.center_x:.2f}" cy="{spot.center_y:.2f}" '
            f'rx="{spot.width / 2:.2f}" ry="{spot.height / 2:.2f}" fill="{spot_color}" opacity="0.92" />'
        )
        for spot in spots
    )
    return f"""<svg class="spinda-svg" xmlns="http://www.w3.org/2000/svg" viewBox="0 0 52 59" role="img" aria-label="Spinda {pid:08X}">
  <rect width="52" height="59" rx="5" fill="#12161a"/>
  <ellipse cx="26" cy="54" rx="17" ry="4" fill="#000" opacity="0.28"/>
  <ellipse cx="12" cy="18" rx="7" ry="9" fill="{body_color}" stroke="#4b3a31" stroke-width="1.1"/>
  <ellipse cx="40" cy="18" rx="7" ry="9" fill="{body_color}" stroke="#4b3a31" stroke-width="1.1"/>
  <ellipse cx="26" cy="31" rx="21" ry="24" fill="{body_color}" stroke="#4b3a31" stroke-width="1.25"/>
  {spot_ellipses}
  <circle cx="20" cy="25" r="2.2" fill="#342820"/>
  <circle cx="36" cy="25" r="2.2" fill="#342820"/>
  <circle cx="19.4" cy="24.2" r="0.65" fill="#fff" opacity="0.85"/>
  <circle cx="35.4" cy="24.2" r="0.65" fill="#fff" opacity="0.85"/>
  <path d="M22 38 C25 41, 29 41, 32 38" fill="none" stroke="#342820" stroke-width="1.25" stroke-linecap="round"/>
  <text x="26" y="7" text-anchor="middle" fill="#a8b0b7" font-size="4.5" font-family="Consolas, monospace">{pid:08X}</text>
</svg>"""


def pid_painter_report(pid_text: str, phase3_dir: Path, *, tid: int = 0, sid: int = 0) -> dict[str, Any]:
    """Return one merged PID locator, painter, stats, and taxonomy report."""

    pid = _parse_pid_text(pid_text)
    location = _locate_pid_int(pid, phase3_dir).to_json()
    spots = spinda_spots(pid)
    stats = spinda_stats(pid, tid=tid, sid=sid)
    traits = spinda_traits(spots)
    labels = spinda_trait_labels(traits)
    location["painter"] = {
        "source_reference": SPINDA_PAINTER_REFERENCE_URL,
        "coordinate_model": "original-painter-nibble-grid",
        "spots": [spot.to_json() for spot in spots],
        "stats": stats,
        "traits": traits,
        "labels": labels,
        "svg": render_spinda_svg(pid, spots, shiny=bool(stats["is_shiny"])),
    }
    return location


def suggest_patterns(
    mode: str,
    *,
    start_pid: int = 0,
    scan_limit: int = DEFAULT_SUGGESTION_SCAN,
    count: int = DEFAULT_SUGGESTION_COUNT,
    phase3_dir: Path,
    tid: int = 0,
    sid: int = 0,
) -> dict[str, Any]:
    """Return top scoring PIDs for one bounded pattern-taxonomy scan.

    The scan is O(scan_limit log count). It keeps only a small heap of winners
    and uses center-point math in the inner loop, so a million-PID review does
    not allocate a million report dictionaries or SVG previews.
    """

    clean_mode = mode.strip().lower().replace("-", "_")
    score_key = PATTERN_SCORE_KEYS.get(clean_mode)
    if score_key is None:
        raise ValueError(f"mode must be one of: {', '.join(sorted(PATTERN_SCORE_KEYS))}")
    scan_limit = max(1, min(scan_limit, MAX_SUGGESTION_SCAN))
    count = max(1, min(count, MAX_SUGGESTION_COUNT, scan_limit))
    started = time.perf_counter()
    heap: list[tuple[float, int, int]] = []
    for offset in range(scan_limit):
        pid = (start_pid + offset) & 0xFFFFFFFF
        score = _pid_score(pid, score_key)
        # Higher score wins; for exact score ties, earlier offset wins.
        item = (score, -offset, pid)
        if len(heap) < count:
            heapq.heappush(heap, item)
        elif item > heap[0]:
            heapq.heapreplace(heap, item)
    elapsed_seconds = max(0.000001, time.perf_counter() - started)

    results: list[dict[str, Any]] = []
    for score, negative_offset, pid in sorted(heap, reverse=True):
        location = _locate_pid_int(pid, phase3_dir)
        spots = spinda_spots(pid)
        traits = spinda_traits(spots)
        stats = spinda_stats(pid, tid=tid, sid=sid)
        results.append(
            {
                "pid": location.pid,
                "offset": -negative_offset,
                "score": round(score, 3),
                "lane_zip": location.lane_zip,
                "entry_name": location.entry_name,
                "matching_tsv": location.matching_tsv,
                "zip_exists": location.zip_exists,
                "rarity": stats["rarity"],
                "is_shiny": stats["is_shiny"],
                "labels": spinda_trait_labels(traits),
                "traits": traits,
            }
        )
    return {
        "mode": clean_mode,
        "score_key": score_key,
        "start_pid": f"0x{start_pid:08X}",
        "scan_limit": scan_limit,
        "count": count,
        "elapsed_seconds": round(elapsed_seconds, 6),
        "pids_per_second": round(scan_limit / elapsed_seconds, 2),
        "results": results,
    }


def _normalize_target_phase3_lanes(target_lanes: int) -> int:
    """Clamp target lane count to the possible 16-bit lower-half space."""

    return max(0, min(int(target_lanes), 0x10000))


def _target_phase3_lane_bounds(target_lanes: int) -> tuple[int, int]:
    """Return inclusive lane bounds for a target lane count.

    The default production target is the organic FR/LG lower-half range
    `0x0001..0xFFFE`. A full `65536` target explicitly includes both endpoint
    lanes for archive audits that want all raw 16-bit names.
    """

    if target_lanes >= 0x10000:
        return 0x0000, 0xFFFF
    if target_lanes <= 0:
        return 0x0001, 0x0000
    return 0x0001, min(target_lanes, 0xFFFE)


def scan_phase3(config: WorkbenchConfig) -> Phase3Summary:
    """Scan Phase 3 output by filename and settled size only."""

    target_lane_count = _normalize_target_phase3_lanes(config.target_phase3_lanes)
    lane_min, lane_max = _target_phase3_lane_bounds(target_lane_count)
    seen = bytearray(0x10000)
    complete_lanes: list[int] = []
    zip_files = 0
    bad_names = 0
    zero_size_zips = 0
    tiny_zips = 0
    tmp_files = 0
    duplicate_lanes = 0
    out_of_scope_zips = 0
    last_good_lane: int | None = None
    samples: dict[str, list[str]] = {}

    try:
        with os.scandir(config.phase3_dir) as entries:
            for entry in entries:
                if not entry.is_file():
                    continue
                name = entry.name
                lower_name = name.lower()
                match = ZIP_RE.match(name)
                if match:
                    zip_files += 1
                    lane = int(match.group("lane"), 16)
                    if not lane_min <= lane <= lane_max:
                        out_of_scope_zips += 1
                        _sample_append(
                            samples,
                            "out_of_scope_zips",
                            f"{name} (target range 0x{lane_min:04X}-0x{lane_max:04X})",
                            config.sample_limit,
                        )
                        continue
                    try:
                        size = entry.stat().st_size
                    except OSError:
                        size = 0
                    if size <= 0:
                        zero_size_zips += 1
                        _sample_append(samples, "zero_size_zips", name, config.sample_limit)
                    elif size < MIN_FINAL_ZIP_BYTES:
                        tiny_zips += 1
                        _sample_append(samples, "tiny_zips", f"{name} ({size} bytes)", config.sample_limit)
                    elif seen[lane]:
                        duplicate_lanes += 1
                        _sample_append(samples, "duplicate_lanes", name, config.sample_limit)
                    else:
                        seen[lane] = 1
                        complete_lanes.append(lane)
                        last_good_lane = lane if last_good_lane is None else max(last_good_lane, lane)
                    continue
                if lower_name.endswith(".spinda80.zip") or ".spinda80.zip." in lower_name:
                    if TMP_RE.match(name):
                        tmp_files += 1
                        _sample_append(samples, "tmp_files", name, config.sample_limit)
                    else:
                        bad_names += 1
                        _sample_append(samples, "bad_names", name, config.sample_limit)
    except FileNotFoundError:
        _sample_append(samples, "folder_errors", f"missing folder: {config.phase3_dir}", config.sample_limit)
    except OSError as error:
        _sample_append(samples, "folder_errors", str(error), config.sample_limit)

    complete_count = len(complete_lanes)
    bad_artifacts = bad_names + zero_size_zips + tiny_zips + tmp_files + duplicate_lanes + out_of_scope_zips
    return Phase3Summary(
        folder=str(config.phase3_dir),
        target_lanes=target_lane_count,
        complete_lanes=complete_count,
        zip_files=zip_files,
        missing_lanes=max(0, target_lane_count - complete_count),
        completed_spindas=complete_count * SPINDAS_PER_LANE,
        target_spindas=target_lane_count * SPINDAS_PER_LANE,
        progress_percent=_percent(complete_count, target_lane_count),
        bad_names=bad_names,
        zero_size_zips=zero_size_zips,
        tiny_zips=tiny_zips,
        tmp_files=tmp_files,
        duplicate_lanes=duplicate_lanes,
        out_of_scope_zips=out_of_scope_zips,
        bad_artifacts=bad_artifacts,
        last_good_lane=f"0x{last_good_lane:04X}" if last_good_lane is not None else None,
        complete_lane_ranges=lane_ids_to_ranges(complete_lanes),
        samples=samples,
    )


def _ledger_summary(ledger_path: Path) -> tuple[int | None, int | None, str | None]:
    """Return done/error counts from the SID ledger if present."""

    data = _read_json(ledger_path)
    if not data:
        return None, None, None
    if "_load_error" in data:
        return None, None, str(data["_load_error"])
    entries = data.get("entries")
    if not isinstance(entries, list):
        done = data.get("complete_shiny_values")
        return done if isinstance(done, int) and not isinstance(done, bool) else None, None, None
    done_count = 0
    error_count = 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        if entry.get("done") is True:
            done_count += 1
        if entry.get("error") or entry.get("route_schedule_error"):
            error_count += 1
    return done_count, error_count, None


def scan_tsv(config: WorkbenchConfig) -> TsvSummary:
    """Scan decimal TSV save names and the optional SID ledger."""

    by_tsv: dict[int, list[Path]] = {}
    invalid_files = 0
    mismatched_files = 0
    save_files = 0
    recent: list[tuple[float, Path, int | None, int | None]] = []
    samples: dict[str, list[str]] = {}

    try:
        with os.scandir(config.tsv_dir) as entries:
            for entry in entries:
                if not entry.is_file() or not entry.name.lower().endswith(".sav"):
                    continue
                save_files += 1
                match = TSV_SAVE_RE.match(entry.name)
                if not match:
                    invalid_files += 1
                    _sample_append(samples, "invalid_files", entry.name, config.sample_limit)
                    continue
                tsv = int(match.group("tsv"))
                sid = int(match.group("sid"))
                if not (0 <= tsv < EXPECTED_TSVS and 0 <= sid <= 0xFFFF):
                    invalid_files += 1
                    _sample_append(samples, "out_of_range_files", entry.name, config.sample_limit)
                    continue
                if sid >> 3 != tsv:
                    mismatched_files += 1
                    _sample_append(samples, "mismatched_files", entry.name, config.sample_limit)
                    continue
                path = config.tsv_dir / entry.name
                by_tsv.setdefault(tsv, []).append(path)
                try:
                    recent.append((entry.stat().st_mtime, path, tsv, sid))
                except OSError:
                    recent.append((0.0, path, tsv, sid))
    except FileNotFoundError:
        _sample_append(samples, "folder_errors", f"missing folder: {config.tsv_dir}", config.sample_limit)
    except OSError as error:
        _sample_append(samples, "folder_errors", str(error), config.sample_limit)

    duplicates = {tsv: paths for tsv, paths in by_tsv.items() if len(paths) > 1}
    for tsv, paths in sorted(duplicates.items())[: config.sample_limit]:
        _sample_append(samples, "duplicate_tsvs", f"TSV {tsv:04d}: {len(paths)} saves", config.sample_limit)

    recent_saves = [
        {
            "name": path.name,
            "path": str(path),
            "tsv": tsv,
            "sid": sid,
            "mtime_unix": mtime,
        }
        for mtime, path, tsv, sid in sorted(recent, key=lambda item: (item[0], item[1].name), reverse=True)[:12]
    ]
    ledger_path = config.tsv_dir / "_sid_shiny_value_ledger_tid_0x0000.json"
    ledger_done, ledger_errors, ledger_load_error = _ledger_summary(ledger_path)
    complete_saves = len(by_tsv)
    duplicate_files = sum(len(paths) - 1 for paths in duplicates.values())
    return TsvSummary(
        folder=str(config.tsv_dir),
        expected_saves=EXPECTED_TSVS,
        complete_saves=complete_saves,
        missing_saves=max(0, EXPECTED_TSVS - complete_saves),
        progress_percent=_percent(complete_saves, EXPECTED_TSVS),
        save_files=save_files,
        invalid_files=invalid_files,
        mismatched_files=mismatched_files,
        duplicate_tsvs=len(duplicates),
        duplicate_files=duplicate_files,
        ledger_path=str(ledger_path),
        ledger_exists=ledger_path.exists(),
        ledger_done=ledger_done,
        ledger_errors=ledger_errors,
        ledger_load_error=ledger_load_error,
        recent_saves=recent_saves,
        samples=samples,
    )


def locate_pid(pid_text: str, phase3_dir: Path) -> PidLocation:
    """Map a PID to lane ZIP, entry name, and matching TSV for TID 0."""

    return _locate_pid_int(_parse_pid_text(pid_text), phase3_dir)


def _quote_cli_arg(value: Path | str) -> str:
    """Quote a command-preview argument for PowerShell."""

    text = str(value)
    return "'" + text.replace("'", "''") + "'"


def command_previews(config: WorkbenchConfig) -> dict[str, str]:
    """Return operator commands without executing them."""

    python_exe = _quote_cli_arg(sys.executable or "python")
    python_command = f"& {python_exe}"
    workbench_script = _quote_cli_arg(ROOT / "tools" / "spinda" / "spinda_workbench" / "spinda_workbench.py")
    phase3_dir = _quote_cli_arg(config.phase3_dir)
    tsv_dir = _quote_cli_arg(config.tsv_dir)
    hatch_output_dir = config.hatch_output_dir
    return {
        "workbench": (
            f"{python_command} {workbench_script} "
            f"--phase3-dir {phase3_dir} --tsv-dir {tsv_dir}"
        ),
        "phase3_manifest": (
            f"{python_command} {_quote_cli_arg(ROOT / 'tools' / 'spinda' / 'phase3_zip_validator.py')} "
            f"--root {phase3_dir} --manifest-only"
        ),
        "phase3_deep_zip": (
            f"{python_command} {_quote_cli_arg(ROOT / 'tools' / 'spinda' / 'phase3_zip_validator.py')} "
            f"--root {phase3_dir}"
        ),
        "phase3_pkhex": (
            f"dotnet run --project {_quote_cli_arg(ROOT / 'tools' / 'spinda' / 'phase3_pkhex_validator' / 'Phase3PkhexValidator.csproj')} -- "
            f"--input-dir {phase3_dir}"
        ),
        "tsv_party": (
            f"dotnet run --project {_quote_cli_arg(ROOT / 'tools' / 'verify_tsv_party_slot' / 'VerifyTsvPartySlot.csproj')} -- "
            f"--save-dir {tsv_dir}"
        ),
        "hatch_splitter": (
            f"dotnet run --project {_quote_cli_arg(ROOT / 'tools' / 'spinda' / 'hatch_zip_splitter' / 'SpindaHatchZipSplitter.csproj')} -c Release -- "
            f"--input-dir {phase3_dir} --save-dir {tsv_dir} "
            f"--shiny-output {_quote_cli_arg(hatch_output_dir / 'spinda-hatched-shiny.zip')} "
            f"--not-shiny-output {_quote_cli_arg(hatch_output_dir / 'spinda-hatched-not-shiny.zip')} "
            f"--report {_quote_cli_arg(hatch_output_dir / '_spinda_hatch_zip_splitter_report.json')} --overwrite"
        ),
        "zip_to_7z_gui": (
            f"{python_command} {_quote_cli_arg(ROOT / 'tools' / 'spinda' / 'zip_to_7z_gui' / 'zip_to_7z_gui.py')}"
        ),
    }


def tool_readiness(config: WorkbenchConfig) -> dict[str, Any]:
    """Return existence checks for helper tools and reports."""

    paths = {
        "phase3_zip_validator": ROOT / "tools" / "spinda" / "phase3_zip_validator.py",
        "phase3_pkhex_validator": ROOT / "tools" / "spinda" / "phase3_pkhex_validator" / "Phase3PkhexValidator.csproj",
        "tsv_party_verifier": ROOT / "tools" / "verify_tsv_party_slot" / "VerifyTsvPartySlot.csproj",
        "hatch_splitter": ROOT / "tools" / "spinda" / "hatch_zip_splitter" / "SpindaHatchZipSplitter.csproj",
        "zip_to_7z_gui": ROOT / "tools" / "spinda" / "zip_to_7z_gui" / "zip_to_7z_gui.py",
        "hatch_report": config.hatch_output_dir / "_spinda_hatch_zip_splitter_report.json",
    }
    return {
        name: {
            "path": str(path),
            "exists": path.exists(),
            "age_seconds": _age_seconds(path),
        }
        for name, path in paths.items()
    }


def build_snapshot(config: WorkbenchConfig) -> dict[str, Any]:
    """Build one dashboard snapshot."""

    phase3 = scan_phase3(config)
    tsv = scan_tsv(config)
    blockers: list[str] = []
    if phase3.complete_lanes < phase3.target_lanes:
        blockers.append("Phase 3 lane ZIPs incomplete")
    if phase3.bad_artifacts:
        blockers.append("Phase 3 output folder has bad artifacts")
    if tsv.complete_saves < EXPECTED_TSVS:
        blockers.append("TSV save bank incomplete")
    if tsv.invalid_files or tsv.mismatched_files or tsv.duplicate_tsvs:
        blockers.append("TSV save folder has naming/mapping issues")
    if tsv.ledger_load_error:
        blockers.append("SID ledger cannot be read")

    return {
        "generated_at_unix": time.time(),
        "phase3": phase3.to_json(),
        "tsv": tsv.to_json(),
        "tools": tool_readiness(config),
        "commands": command_previews(config),
        "readiness": {
            "ready_for_hatch_splitter": not blockers,
            "blocked_by": blockers,
        },
        "server": {
            "display_url": config.display_url,
            "read_only": True,
            "root": str(ROOT),
        },
    }


HTML = r"""
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Spinda Workbench</title>
  <style>
    :root {
      color-scheme: dark;
      --bg: #101214;
      --panel: #171b1f;
      --panel2: #1f252b;
      --line: #303840;
      --text: #edf1f3;
      --muted: #a8b0b7;
      --green: #38c172;
      --amber: #e0a83a;
      --red: #e15a5a;
      --blue: #4ea1d3;
    }
    * { box-sizing: border-box; }
    body {
      margin: 0;
      background: var(--bg);
      color: var(--text);
      font: 14px/1.45 "Segoe UI", system-ui, sans-serif;
    }
    header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 16px;
      padding: 18px 22px;
      border-bottom: 1px solid var(--line);
      background: #15191d;
      position: sticky;
      top: 0;
      z-index: 1;
    }
    h1 { font-size: 20px; margin: 0; font-weight: 650; }
    main { padding: 18px 22px 32px; max-width: 1500px; margin: 0 auto; }
    .grid { display: grid; grid-template-columns: repeat(4, minmax(180px, 1fr)); gap: 12px; }
    .wide { grid-column: span 2; }
    .full { grid-column: 1 / -1; }
    .panel {
      background: var(--panel);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 14px;
      min-width: 0;
    }
    .panel h2 { margin: 0 0 10px; font-size: 14px; color: var(--muted); font-weight: 650; }
    .metric { font-size: 30px; font-weight: 700; letter-spacing: 0; }
    .sub { color: var(--muted); overflow-wrap: anywhere; }
    .ok { color: var(--green); }
    .warn { color: var(--amber); }
    .bad { color: var(--red); }
    .info { color: var(--blue); }
    progress { width: 100%; height: 12px; accent-color: var(--green); }
    table { width: 100%; border-collapse: collapse; }
    td, th { border-bottom: 1px solid var(--line); padding: 7px 6px; text-align: left; vertical-align: top; }
    th { color: var(--muted); font-weight: 600; }
    code, pre {
      font-family: "Cascadia Mono", Consolas, monospace;
      font-size: 12px;
    }
    pre {
      margin: 8px 0 0;
      background: var(--panel2);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 10px;
      overflow-x: auto;
      white-space: pre-wrap;
    }
    input, button, select {
      color: var(--text);
      background: var(--panel2);
      border: 1px solid var(--line);
      border-radius: 6px;
      padding: 8px 10px;
      font: inherit;
    }
    input.short { width: 88px; }
    button { cursor: pointer; }
    button:hover { border-color: var(--blue); }
    button:disabled { cursor: wait; opacity: 0.65; }
    button.link {
      border: 0;
      padding: 0;
      background: transparent;
      color: var(--blue);
      font-family: "Cascadia Mono", Consolas, monospace;
    }
    .row { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
    .pill {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 999px;
      border: 1px solid var(--line);
      color: var(--muted);
      margin: 2px 4px 2px 0;
    }
    .painter-wrap {
      display: grid;
      grid-template-columns: minmax(140px, 190px) 1fr;
      gap: 12px;
      align-items: start;
      margin-top: 12px;
    }
    .preview-box {
      min-height: 210px;
      display: grid;
      place-items: center;
      background: var(--panel2);
      border: 1px solid var(--line);
      border-radius: 8px;
      padding: 12px;
    }
    .spinda-svg {
      width: 100%;
      max-width: 170px;
      height: auto;
      image-rendering: pixelated;
    }
    .score-grid {
      display: grid;
      grid-template-columns: repeat(2, minmax(140px, 1fr));
      gap: 6px 12px;
      margin-top: 8px;
    }
    .score-grid div {
      border-bottom: 1px solid var(--line);
      color: var(--muted);
      padding: 3px 0;
    }
    @media (max-width: 900px) {
      .grid { grid-template-columns: 1fr; }
      .wide { grid-column: span 1; }
      header { align-items: flex-start; flex-direction: column; }
      .painter-wrap { grid-template-columns: 1fr; }
    }
  </style>
</head>
<body>
  <header>
    <h1>Spinda Workbench</h1>
    <div class="sub">Read-only panel. Last update: <span id="updated">never</span></div>
  </header>
  <main>
    <section class="grid">
      <div class="panel">
        <h2>Phase 3 Lanes</h2>
        <div class="metric" id="phase3-count">0 / 0</div>
        <progress id="phase3-progress" value="0" max="100"></progress>
        <div class="sub" id="phase3-sub"></div>
      </div>
      <div class="panel">
        <h2>Spinda Records</h2>
        <div class="metric" id="spinda-count">0</div>
        <div class="sub" id="spinda-sub"></div>
      </div>
      <div class="panel">
        <h2>TSV Saves</h2>
        <div class="metric" id="tsv-count">0 / 8192</div>
        <progress id="tsv-progress" value="0" max="100"></progress>
        <div class="sub" id="tsv-sub"></div>
      </div>
      <div class="panel">
        <h2>Hatch Readiness</h2>
        <div class="metric" id="hatch-ready">check</div>
        <div class="sub" id="hatch-blockers"></div>
      </div>

      <div class="panel wide">
        <h2>Health</h2>
        <table><tbody id="health-table"></tbody></table>
      </div>
      <div class="panel wide">
        <h2>Spinda Painter / PID Locator</h2>
        <div class="row">
          <input id="pid-input" placeholder="0x12345678" aria-label="PID">
          <input id="tid-input" class="short" placeholder="TID" value="0" aria-label="Trainer ID">
          <input id="sid-input" class="short" placeholder="SID" value="0" aria-label="Secret ID">
          <button id="pid-button" type="button">Locate</button>
        </div>
        <div class="painter-wrap">
          <div class="preview-box" id="spinda-preview">No PID loaded.</div>
          <div>
            <table><tbody id="pid-details"></tbody></table>
            <div id="pid-labels"></div>
            <div class="score-grid" id="pid-scores"></div>
          </div>
        </div>
        <pre id="pid-output">Enter a PID to map archive position, original painter spots, stats, shiny math, and visual labels.</pre>
      </div>
      <div class="panel wide">
        <h2>Pattern Automation</h2>
        <div class="row">
          <select id="suggest-mode" aria-label="Pattern mode">
            <option value="funny">funny</option>
            <option value="eye_cover">eye cover</option>
            <option value="symmetry">symmetry</option>
            <option value="balanced">balanced</option>
            <option value="centered">centered</option>
            <option value="heart">heart-ish</option>
            <option value="cursed">cursed</option>
            <option value="spread">spread</option>
            <option value="clustered">clustered</option>
          </select>
          <input id="suggest-start" placeholder="0x00000000" value="0x00000000" aria-label="Start PID">
          <input id="suggest-scan" class="short" placeholder="scan" value="8192" aria-label="Scan limit">
          <input id="suggest-count" class="short" placeholder="count" value="12" aria-label="Result count">
          <button id="suggest-button" type="button">Suggest</button>
        </div>
        <div class="sub" id="suggest-status">No scan yet.</div>
        <table>
          <thead><tr><th>PID</th><th>Score</th><th>Offset</th><th>TSV</th><th>Rarity</th><th>Labels</th></tr></thead>
          <tbody id="suggestions-table"><tr><td colspan="6" class="sub">No scan yet.</td></tr></tbody>
        </table>
      </div>

      <div class="panel full">
        <h2>Command Preview</h2>
        <table><tbody id="commands-table"></tbody></table>
      </div>
      <div class="panel full">
        <h2>Samples</h2>
        <pre id="samples-output">No samples yet.</pre>
      </div>
    </section>
  </main>
  <script>
    const fmt = new Intl.NumberFormat();
    const byId = (id) => document.getElementById(id);
    function setText(id, value) { byId(id).textContent = value; }
    function percent(value) { return `${Number(value || 0).toFixed(2)}%`; }
    function esc(value) {
      return String(value ?? "").replace(/[&<>"']/g, (char) => ({
        "&": "&amp;",
        "<": "&lt;",
        ">": "&gt;",
        '"': "&quot;",
        "'": "&#39;",
      })[char]);
    }
    function pills(values) {
      return (values || []).map((value) => `<span class="pill">${esc(value)}</span>`).join("");
    }
    function sampleText(samples) {
      const lines = [];
      for (const [section, values] of Object.entries(samples || {})) {
        if (!values.length) continue;
        lines.push(`${section}:`);
        for (const value of values) lines.push(`  ${value}`);
      }
      return lines.length ? lines.join("\n") : "No warning samples.";
    }
    function renderStatus(data) {
      const phase3 = data.phase3 || {};
      const tsv = data.tsv || {};
      setText("updated", new Date((data.generated_at_unix || 0) * 1000).toLocaleTimeString());
      setText("phase3-count", `${fmt.format(phase3.complete_lanes || 0)} / ${fmt.format(phase3.target_lanes || 0)}`);
      byId("phase3-progress").value = phase3.progress_percent || 0;
      setText("phase3-sub", `${percent(phase3.progress_percent)} complete. Last lane ${phase3.last_good_lane || "none"}.`);
      setText("spinda-count", fmt.format(phase3.completed_spindas || 0));
      setText("spinda-sub", `Target ${fmt.format(phase3.target_spindas || 0)} records.`);
      setText("tsv-count", `${fmt.format(tsv.complete_saves || 0)} / ${fmt.format(tsv.expected_saves || 8192)}`);
      byId("tsv-progress").value = tsv.progress_percent || 0;
      setText("tsv-sub", `${percent(tsv.progress_percent)} complete. Ledger done ${tsv.ledger_done ?? "n/a"}.`);
      const ready = (data.readiness || {}).ready_for_hatch_splitter;
      const blockers = (data.readiness || {}).blocked_by || [];
      setText("hatch-ready", ready ? "ready" : "blocked");
      byId("hatch-ready").className = `metric ${ready ? "ok" : "warn"}`;
      setText("hatch-blockers", blockers.length ? blockers.join("; ") : "No blockers from read-only scan.");
      const healthRows = [
        ["Phase3 bad artifacts", phase3.bad_artifacts || 0],
        ["Bad ZIP names", phase3.bad_names || 0],
        ["Zero-size ZIPs", phase3.zero_size_zips || 0],
        ["Tiny ZIPs", phase3.tiny_zips || 0],
        ["Temp ZIPs", phase3.tmp_files || 0],
        ["Out-of-scope ZIPs", phase3.out_of_scope_zips || 0],
        ["TSV invalid files", tsv.invalid_files || 0],
        ["TSV mismatched files", tsv.mismatched_files || 0],
        ["TSV duplicate rows", tsv.duplicate_tsvs || 0],
        ["Ledger errors", tsv.ledger_errors ?? "n/a"],
      ];
      byId("health-table").innerHTML = healthRows.map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
      const commands = data.commands || {};
      byId("commands-table").innerHTML = Object.entries(commands)
        .map(([name, command]) => `<tr><th>${esc(name)}</th><td><code>${esc(command)}</code></td></tr>`).join("");
      setText("samples-output", [
        "Phase 3",
        sampleText(phase3.samples || {}),
        "",
        "TSV",
        sampleText(tsv.samples || {}),
      ].join("\n"));
    }
    async function refresh() {
      try {
        const response = await fetch("/api/status", { cache: "no-store" });
        renderStatus(await response.json());
      } catch (error) {
        setText("updated", `offline: ${error}`);
      }
    }
    async function locatePid() {
      const raw = byId("pid-input").value.trim();
      if (!raw) return;
      const params = new URLSearchParams({
        tid: byId("tid-input").value.trim() || "0",
        sid: byId("sid-input").value.trim() || "0",
      });
      const response = await fetch(`/api/pid/${encodeURIComponent(raw)}?${params}`, { cache: "no-store" });
      const data = await response.json();
      renderPid(data);
      byId("pid-output").textContent = JSON.stringify(data, null, 2);
    }
    function renderPid(data) {
      if (data.error) {
        setText("spinda-preview", data.error);
        byId("pid-details").innerHTML = "";
        byId("pid-labels").innerHTML = "";
        byId("pid-scores").innerHTML = "";
        return;
      }
      const painter = data.painter || {};
      const stats = painter.stats || {};
      const traits = painter.traits || {};
      byId("spinda-preview").innerHTML = painter.svg || "No preview.";
      const sidRange = `${data.matching_sid_min} - ${data.matching_sid_max}`;
      const detailRows = [
        ["PID", data.pid],
        ["Entry", data.entry_name],
        ["Lane ZIP", data.lane_zip],
        ["PSV / TSV", `${data.expected_psv} / ${data.matching_tsv}`],
        ["SID range for TID 0", sidRange],
        ["Nature", stats.nature],
        ["Gender", stats.gender],
        ["Ability slot", stats.ability_slot],
        ["Rarity", stats.rarity],
        ["Shiny for entered TID/SID", stats.is_shiny ? "yes" : "no"],
        ["TID0/SID0 rarity", stats.tid0_sid0_rarity],
      ];
      byId("pid-details").innerHTML = detailRows
        .map(([k, v]) => `<tr><th>${esc(k)}</th><td>${esc(v)}</td></tr>`).join("");
      byId("pid-labels").innerHTML = pills(painter.labels || []);
      byId("pid-scores").innerHTML = Object.entries(traits)
        .map(([k, v]) => `<div>${esc(k.replaceAll("_", " "))}: <strong>${esc(Number(v).toFixed(1))}</strong></div>`)
        .join("");
    }
    async function suggestPatterns() {
      const button = byId("suggest-button");
      const params = new URLSearchParams({
        start: byId("suggest-start").value.trim() || "0x00000000",
        scan_limit: byId("suggest-scan").value.trim() || "8192",
        count: byId("suggest-count").value.trim() || "12",
        tid: byId("tid-input").value.trim() || "0",
        sid: byId("sid-input").value.trim() || "0",
      });
      const mode = byId("suggest-mode").value;
      button.disabled = true;
      setText("suggest-status", "Scanning...");
      try {
        const response = await fetch(`/api/suggest/${encodeURIComponent(mode)}?${params}`, { cache: "no-store" });
        const data = await response.json();
        if (data.error) {
          byId("suggestions-table").innerHTML = `<tr><td colspan="6" class="bad">${esc(data.error)}</td></tr>`;
          setText("suggest-status", "Scan failed.");
          return;
        }
        setText(
          "suggest-status",
          `${fmt.format(data.scan_limit)} PIDs scanned in ${Number(data.elapsed_seconds || 0).toFixed(3)}s; ${fmt.format(data.pids_per_second || 0)} PID/s.`
        );
        byId("suggestions-table").innerHTML = (data.results || []).map((row) => `
          <tr>
            <td><button class="link pid-link" type="button" data-pid="${esc(row.pid)}">${esc(row.pid)}</button></td>
            <td>${esc(Number(row.score).toFixed(3))}</td>
            <td>${esc(row.offset)}</td>
            <td>${esc(row.matching_tsv)}</td>
            <td>${esc(row.rarity)}${row.is_shiny ? " shiny" : ""}</td>
            <td>${pills(row.labels)}</td>
          </tr>
        `).join("") || `<tr><td colspan="6" class="sub">No matches.</td></tr>`;
      } catch (error) {
        byId("suggestions-table").innerHTML = `<tr><td colspan="6" class="bad">${esc(error)}</td></tr>`;
        setText("suggest-status", "Scan failed.");
      } finally {
        button.disabled = false;
      }
    }
    byId("pid-button").addEventListener("click", locatePid);
    byId("pid-input").addEventListener("keydown", (event) => {
      if (event.key === "Enter") locatePid();
    });
    byId("suggest-button").addEventListener("click", suggestPatterns);
    byId("suggestions-table").addEventListener("click", (event) => {
      const target = event.target.closest(".pid-link");
      if (!target) return;
      byId("pid-input").value = target.dataset.pid || "";
      locatePid();
    });
    refresh();
    setInterval(refresh, 5000);
  </script>
</body>
</html>
"""


def create_app(config: WorkbenchConfig) -> Flask:
    """Create Flask app for tests and operator launch."""

    app = Flask(__name__)

    @app.get("/")
    def index() -> str:
        return HTML

    @app.get("/api/status")
    def api_status() -> Any:
        return jsonify(build_snapshot(config))

    @app.get("/api/commands")
    def api_commands() -> Any:
        return jsonify(command_previews(config))

    @app.get("/api/pid/<pid_text>")
    def api_pid(pid_text: str) -> Any:
        try:
            tid = _query_int("tid", 0, minimum=0, maximum=0xFFFF)
            sid = _query_int("sid", 0, minimum=0, maximum=0xFFFF)
            return jsonify(pid_painter_report(pid_text, config.phase3_dir, tid=tid, sid=sid))
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

    @app.get("/api/suggest/<mode>")
    def api_suggest(mode: str) -> Any:
        try:
            start_pid = _query_int(
                "start",
                0,
                minimum=0,
                maximum=0xFFFFFFFF,
            )
            scan_limit = _query_int(
                "scan_limit",
                DEFAULT_SUGGESTION_SCAN,
                minimum=1,
                maximum=MAX_SUGGESTION_SCAN,
            )
            count = _query_int(
                "count",
                DEFAULT_SUGGESTION_COUNT,
                minimum=1,
                maximum=MAX_SUGGESTION_COUNT,
            )
            tid = _query_int("tid", 0, minimum=0, maximum=0xFFFF)
            sid = _query_int("sid", 0, minimum=0, maximum=0xFFFF)
            return jsonify(
                suggest_patterns(
                    mode,
                    start_pid=start_pid,
                    scan_limit=scan_limit,
                    count=count,
                    phase3_dir=config.phase3_dir,
                    tid=tid,
                    sid=sid,
                )
            )
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

    return app


def _bounded_int_arg(label: str, minimum: int, maximum: int) -> Callable[[str], int]:
    """Return an argparse validator for bounded integer options."""

    def parse(raw: str) -> int:
        try:
            value = int(raw, 0)
        except ValueError as error:
            raise argparse.ArgumentTypeError(f"{label} must be an integer") from error
        if not minimum <= value <= maximum:
            raise argparse.ArgumentTypeError(f"{label} must be between {minimum} and {maximum}")
        return value

    return parse


_target_phase3_lanes_arg = _bounded_int_arg("target-phase3-lanes", 0, 0x10000)
_sample_limit_arg = _bounded_int_arg("sample-limit", 0, 10_000)
_port_arg = _bounded_int_arg("port", 1, 65_535)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse CLI options."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--phase3-dir", type=Path, default=DEFAULT_PHASE3_DIR)
    parser.add_argument("--tsv-dir", type=Path, default=DEFAULT_TSV_DIR)
    parser.add_argument("--hatch-output-dir", type=Path, default=DEFAULT_HATCH_OUTPUT_DIR)
    parser.add_argument("--seven-zip-output-dir", type=Path, default=DEFAULT_7Z_OUTPUT_DIR)
    parser.add_argument("--target-phase3-lanes", type=_target_phase3_lanes_arg, default=DEFAULT_TARGET_PHASE3_LANES)
    parser.add_argument("--sample-limit", type=_sample_limit_arg, default=DEFAULT_SAMPLE_LIMIT)
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=_port_arg, default=DEFAULT_PORT)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    """Run the workbench server."""

    args = parse_args(argv)
    urls = workbench_urls(args.host, args.port)
    display_url = urls[0]
    config = WorkbenchConfig(
        phase3_dir=args.phase3_dir,
        tsv_dir=args.tsv_dir,
        hatch_output_dir=args.hatch_output_dir,
        seven_zip_output_dir=args.seven_zip_output_dir,
        target_phase3_lanes=args.target_phase3_lanes,
        sample_limit=args.sample_limit,
        display_url=display_url,
    )
    print("Spinda Workbench URLs:")
    for url in urls:
        print(f"  {url}")
    print("Read-only mode. No workers launched.")
    create_app(config).run(host=args.host, port=args.port, threaded=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
