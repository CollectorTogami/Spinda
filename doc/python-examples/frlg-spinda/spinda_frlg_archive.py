"""Archive helpers for the FR/LG Spinda roadmap corpus.

This module is deliberately file-oriented. It does not talk to the emulator.
Its job is to make the later long-running generation pipeline safer and easier
to resume by standardizing:

- the fixed-width `65536 * 80` lane block layout
- a compact 65536-bit presence bitmap for resume/export sanity checks
- a small global corpus manifest for stage and lane progress

The archive format stays intentionally boring:

- headerless raw block payloads
- separate JSON manifests
- separate bitmap sidecars
"""

from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Mapping

from spinda_frlg_common import (
    BOX_SLOT_SIZE,
    LANE_BLOCK_RECORDS,
    LANE_BLOCK_RECORD_SIZE,
    format_u16,
    read_json,
    sha1_bytes,
    sha1_file,
    write_bytes_atomic,
    write_json_atomic,
)


LANE_BITMAP_BYTES = LANE_BLOCK_RECORDS // 8
GLOBAL_MANIFEST_NAME = "global.json"


def parse_u16(value: int | str) -> int:
    """Parse one 16-bit lane or upper-half index."""

    if isinstance(value, int):
        parsed = value
    else:
        parsed = int(value, 0)
    if not 0 <= parsed <= 0xFFFF:
        raise ValueError(f"Value does not fit in 16 bits: {value!r}")
    return parsed


def lane_record_offset(upper_half: int) -> int:
    """Return the byte offset for one upper-half record inside a lane block."""

    upper_half = parse_u16(upper_half)
    return upper_half * LANE_BLOCK_RECORD_SIZE


def lane_bitmap_path(block_path: Path) -> Path:
    """Return the canonical bitmap sidecar path for one lane block."""

    return block_path.with_suffix(block_path.suffix + ".bitmap")


def pk3_filename(lane_id: int, upper_half: int) -> str:
    """Return a stable filename for one exported Spinda record."""

    return f"{format_u16(lane_id)}-{format_u16(upper_half)}.pk3"


@dataclass
class LaneBitmap:
    """Compact presence bitmap for one `65536`-record lane."""

    bits: bytearray = field(default_factory=lambda: bytearray(LANE_BITMAP_BYTES))

    def _index(self, upper_half: int) -> tuple[int, int]:
        upper_half = parse_u16(upper_half)
        return upper_half >> 3, upper_half & 7

    def is_present(self, upper_half: int) -> bool:
        """Return whether one upper-half slot has been written."""

        byte_index, bit_index = self._index(upper_half)
        return bool(self.bits[byte_index] & (1 << bit_index))

    def mark_present(self, upper_half: int) -> None:
        """Set one upper-half slot as present."""

        byte_index, bit_index = self._index(upper_half)
        self.bits[byte_index] |= 1 << bit_index

    def mark_absent(self, upper_half: int) -> None:
        """Clear one upper-half slot."""

        byte_index, bit_index = self._index(upper_half)
        self.bits[byte_index] &= ~(1 << bit_index)

    def count_present(self) -> int:
        """Return the number of set bits in the bitmap."""

        return sum(byte.bit_count() for byte in self.bits)

    def iter_present(self) -> Iterator[int]:
        """Yield each present upper-half index in ascending order."""

        for byte_index, value in enumerate(self.bits):
            if not value:
                continue
            base_upper_half = byte_index << 3
            for bit_index in range(8):
                if value & (1 << bit_index):
                    yield base_upper_half + bit_index

    def first_absent(self) -> int | None:
        """Return the first missing upper-half index, or `None` if full."""

        for byte_index, value in enumerate(self.bits):
            if value == 0xFF:
                continue
            base_upper_half = byte_index << 3
            for bit_index in range(8):
                if not value & (1 << bit_index):
                    return base_upper_half + bit_index
        return None

    def to_bytes(self) -> bytes:
        """Serialize the bitmap."""

        return bytes(self.bits)

    @classmethod
    def from_bytes(cls, data: bytes) -> "LaneBitmap":
        """Deserialize one bitmap payload."""

        if len(data) != LANE_BITMAP_BYTES:
            raise ValueError(
                f"Lane bitmap size must be exactly {LANE_BITMAP_BYTES} bytes, not {len(data)}."
            )
        return cls(bytearray(data))

    @classmethod
    def load(cls, path: Path) -> "LaneBitmap":
        """Load one bitmap from disk."""

        return cls.from_bytes(path.read_bytes())

    def save(self, path: Path) -> None:
        """Write the bitmap atomically."""

        write_bytes_atomic(path, self.to_bytes())


@dataclass
class LaneBlockBuffer:
    """In-memory lane block plus its presence bitmap."""

    data: bytearray = field(
        default_factory=lambda: bytearray(LANE_BLOCK_RECORDS * LANE_BLOCK_RECORD_SIZE)
    )
    bitmap: LaneBitmap = field(default_factory=LaneBitmap)

    def __post_init__(self) -> None:
        if len(self.data) != LANE_BLOCK_RECORDS * LANE_BLOCK_RECORD_SIZE:
            raise ValueError("Lane block buffers must be exactly one full lane in size.")

    def get_record(self, upper_half: int) -> bytes:
        """Return one raw 80-byte boxed record."""

        offset = lane_record_offset(upper_half)
        return bytes(self.data[offset : offset + LANE_BLOCK_RECORD_SIZE])

    def set_record(self, upper_half: int, record: bytes) -> None:
        """Store one boxed record into the lane buffer."""

        if len(record) != BOX_SLOT_SIZE:
            raise ValueError(f"Each boxed record must be {BOX_SLOT_SIZE} bytes.")
        # The upper half is used directly as the block index. That keeps lookup
        # and later export O(1) and avoids any separate table of contents for a
        # finished 65536-record lane.
        offset = lane_record_offset(upper_half)
        self.data[offset : offset + LANE_BLOCK_RECORD_SIZE] = record
        self.bitmap.mark_present(upper_half)

    def is_present(self, upper_half: int) -> bool:
        """Return whether one record has been written."""

        return self.bitmap.is_present(upper_half)

    def count_present(self) -> int:
        """Return how many upper halves are present."""

        return self.bitmap.count_present()

    def next_missing_upper_half(self) -> int | None:
        """Return the first missing upper-half index, or `None` if the lane is full."""

        return self.bitmap.first_absent()

    def save(self, block_path: Path, bitmap_path: Path | None = None) -> None:
        """Persist the raw lane block and its bitmap."""

        if bitmap_path is None:
            bitmap_path = lane_bitmap_path(block_path)
        # The payload and bitmap are written separately on purpose. The raw
        # block stays simple and streamable, while the bitmap gives resume and
        # export code a quick way to see which upper halves are already present.
        write_bytes_atomic(block_path, bytes(self.data))
        self.bitmap.save(bitmap_path)

    @classmethod
    def load(cls, block_path: Path, bitmap_path: Path | None = None) -> "LaneBlockBuffer":
        """Load a lane block and bitmap from disk."""

        if bitmap_path is None:
            bitmap_path = lane_bitmap_path(block_path)

        data = block_path.read_bytes()
        expected_size = LANE_BLOCK_RECORDS * LANE_BLOCK_RECORD_SIZE
        if len(data) != expected_size:
            raise ValueError(f"Lane block size must be {expected_size} bytes, not {len(data)}.")
        bitmap = LaneBitmap.load(bitmap_path)
        return cls(bytearray(data), bitmap)


@dataclass
class GlobalCorpusManifest:
    """Top-level resume metadata for the whole corpus."""

    manifest_path: Path
    workspace_root: Path
    stage: str = "phase1_first_half"
    current_lane_id: int | None = None
    next_lane_id: int = 0
    completed_lane_count: int = 0
    current_upper_half: int | None = None
    notes: str = ""
    schema_version: int = 1

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-friendly manifest payload."""

        return {
            "schema_version": self.schema_version,
            "workspace_root": str(self.workspace_root),
            "stage": self.stage,
            "current_lane_id": format_u16(self.current_lane_id),
            "next_lane_id": format_u16(self.next_lane_id),
            "completed_lane_count": self.completed_lane_count,
            "current_upper_half": format_u16(self.current_upper_half),
            "notes": self.notes,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any], manifest_path: Path) -> "GlobalCorpusManifest":
        """Rebuild the manifest from JSON data."""

        current_lane = data.get("current_lane_id")
        current_upper = data.get("current_upper_half")
        return cls(
            manifest_path=manifest_path,
            workspace_root=Path(str(data["workspace_root"])),
            stage=str(data.get("stage", "phase1_first_half")),
            current_lane_id=None if current_lane is None else parse_u16(current_lane),
            next_lane_id=parse_u16(data.get("next_lane_id", 0)),
            completed_lane_count=int(data.get("completed_lane_count", 0)),
            current_upper_half=None if current_upper is None else parse_u16(current_upper),
            notes=str(data.get("notes", "")),
            schema_version=int(data.get("schema_version", 1)),
        )

    @classmethod
    def load(cls, manifest_path: Path) -> "GlobalCorpusManifest":
        """Load one global manifest from disk."""

        manifest_path = manifest_path.expanduser().resolve()
        return cls.from_dict(read_json(manifest_path), manifest_path)

    def save(self) -> None:
        """Write the global manifest atomically."""

        write_json_atomic(self.manifest_path, self.to_dict())


def global_manifest_path(workspace_root: Path) -> Path:
    """Return the canonical global manifest path."""

    workspace_root = workspace_root.expanduser().resolve()
    return workspace_root / "manifests" / GLOBAL_MANIFEST_NAME


def init_global_manifest(workspace_root: Path, *, notes: str = "") -> GlobalCorpusManifest:
    """Create a fresh global manifest for the workspace."""

    workspace_root = workspace_root.expanduser().resolve()
    manifest = GlobalCorpusManifest(
        manifest_path=global_manifest_path(workspace_root),
        workspace_root=workspace_root,
        notes=notes,
    )
    manifest.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.save()
    return manifest


def lane_block_sha1(block_path: Path) -> str:
    """Return the SHA-1 of one raw lane block."""

    return sha1_file(block_path)


def lane_bitmap_sha1(bitmap_path: Path) -> str:
    """Return the SHA-1 of one lane bitmap."""

    return sha1_file(bitmap_path)


def lane_buffer_sha1(buffer: LaneBlockBuffer) -> str:
    """Return the SHA-1 of the in-memory raw block payload."""

    return sha1_bytes(buffer.data)
