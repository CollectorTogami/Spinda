#!/usr/bin/env python3
"""Evaluate SPC3 v5 residual compression variants.

This script does not change the verified SPC3 v5 container. It streams the
remaining explicit IV32 cells after the old predictor and runtime RS/FRLG
second-stage predictor, then estimates replacement encodings for the two
largest v5 residual components:

* the stage-2 explicit bitmap
* the explicit IV32 stat-delta value stream

The goal is to test compression-only ideas without needing to explain the
remaining vblank/state-selector cause.
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import time
import zlib
from collections import Counter, OrderedDict, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Iterable

import numpy as np
import zstandard as zstd

import spc3_iv_offset_classifier as clf
import spc3_two_stage_runtime_repack as two_stage


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_INPUT = ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.spc3"
DEFAULT_REPORT = ROOT / "Helper-PC-Artifacts" / "spc3_residual_optimizer_report.json"
DEFAULT_BASELINE_REPORT = (
    ROOT / "Helper-PC-Artifacts" / "helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.verify.pack.json"
)

STAT_COUNT = 6
STAT_BIT_WEIGHTS = (1 << np.arange(STAT_COUNT, dtype=np.uint8)).astype(np.uint8)


@dataclass
class Bucket:
    """Append-only temporary byte bucket."""

    name: str
    path: Path
    raw_size: int = 0
    handle: BinaryIO | None = None
    initialized: bool = False

    def write(self, data: bytes) -> None:
        if not data:
            return
        if self.handle is None:
            mode = "ab" if self.initialized else "wb"
            self.handle = self.path.open(mode)
            self.initialized = True
        self.handle.write(data)
        self.raw_size += len(data)

    def close(self) -> None:
        if self.handle is not None:
            self.handle.close()
            self.handle = None


class BucketStore:
    """Owns temporary buckets and computes compressed sizes."""

    def __init__(self, root: Path, max_open: int = 384) -> None:
        self.root = root
        self.max_open = max(1, max_open)
        self.buckets: dict[str, Bucket] = {}
        self.open_order: OrderedDict[str, None] = OrderedDict()

    def get(self, name: str) -> Bucket:
        bucket = self.buckets.get(name)
        if bucket is None:
            safe_name = "".join(ch if ch.isalnum() or ch in "._-" else "_" for ch in name)
            bucket = Bucket(name=name, path=self.root / f"{safe_name}.bin")
            self.buckets[name] = bucket
        return bucket

    def write(self, name: str, data: bytes) -> None:
        bucket = self.get(name)
        bucket.write(data)
        if bucket.handle is not None:
            self.open_order.pop(name, None)
            self.open_order[name] = None
        while len(self.open_order) > self.max_open:
            old_name, _marker = self.open_order.popitem(last=False)
            self.buckets[old_name].close()

    def close_all(self) -> None:
        for bucket in self.buckets.values():
            bucket.close()
        self.open_order.clear()

    def raw_size(self, prefix: str | None = None) -> int:
        return sum(bucket.raw_size for name, bucket in self.buckets.items() if prefix is None or name.startswith(prefix))

    def compressed_size(
        self,
        *,
        zstd_level: int,
        prefix: str | None = None,
        dictionaries: dict[str, zstd.ZstdCompressionDict] | None = None,
    ) -> int:
        self.close_all()
        total = 0
        for name, bucket in self.buckets.items():
            if prefix is not None and not name.startswith(prefix):
                continue
            dict_data = dictionaries.get(name) if dictionaries else None
            total += min(bucket.raw_size, zstd_file_size(bucket.path, zstd_level=zstd_level, dict_data=dict_data))
        return total


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--baseline-report", type=Path, default=DEFAULT_BASELINE_REPORT)
    parser.add_argument("--predictor-json", type=Path, default=clf.DEFAULT_PREDICTOR_JSON)
    parser.add_argument("--start-rng", default="0x2B0C94C1")
    parser.add_argument("--runtime-max-steps", type=int, default=4_000_000)
    parser.add_argument("--base-model", choices=tuple(clf.BASE_MODEL_POSITIONS), default="rsfrlg")
    parser.add_argument("--max-extra", type=int, default=2)
    parser.add_argument("--sample-lanes", type=int, default=1024)
    parser.add_argument("--all-lanes", action="store_true", help="evaluate every lane instead of sampling")
    parser.add_argument("--sample-mode", choices=("first", "stride"), default="stride")
    parser.add_argument("--progress-every", type=int, default=512)
    parser.add_argument("--zstd-level", type=int, default=9)
    parser.add_argument("--top-classes", type=int, default=255)
    parser.add_argument("--dict-size", type=int, default=65536)
    parser.add_argument("--dict-sample-bytes", type=int, default=64 * 1024 * 1024)
    parser.add_argument("--scratch-dir", type=Path, default=None)
    parser.add_argument("--keep-scratch", action="store_true")
    return parser.parse_args()


def zstd_bytes_size(raw: bytes, *, zstd_level: int, dict_data: zstd.ZstdCompressionDict | None = None) -> int:
    if not raw:
        return 0
    compressor = zstd.ZstdCompressor(level=zstd_level, dict_data=dict_data)
    return len(compressor.compress(raw))


def zstd_file_size(path: Path, *, zstd_level: int, dict_data: zstd.ZstdCompressionDict | None = None) -> int:
    size = path.stat().st_size
    if size == 0:
        return 0
    compressor = zstd.ZstdCompressor(level=zstd_level, dict_data=dict_data)
    total = 0
    with path.open("rb") as source:
        with compressor.stream_reader(source) as reader:
            while True:
                chunk = reader.read(1024 * 1024)
                if not chunk:
                    break
                total += len(chunk)
    return total


def best_zstd_size(raw: bytes, *, zstd_level: int, dict_data: zstd.ZstdCompressionDict | None = None) -> int:
    if not raw:
        return 0
    return min(len(raw), zstd_bytes_size(raw, zstd_level=zstd_level, dict_data=dict_data))


def pack_u8(values: np.ndarray) -> bytes:
    return np.asarray(values, dtype=np.uint8).tobytes()


def pack_u16(values: np.ndarray) -> bytes:
    return np.asarray(values, dtype="<u2").tobytes()


def pack_u64(values: Iterable[int]) -> bytes:
    return np.fromiter(values, dtype="<u8").tobytes()


def changed_mask_values(actual_fields: np.ndarray, baseline_fields: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    changed = actual_fields != baseline_fields
    masks = (changed.T.astype(np.uint8) * STAT_BIT_WEIGHTS).sum(axis=1).astype(np.uint8)
    return changed, masks


def residual_keys(actual_fields: np.ndarray, changed: np.ndarray, masks: np.ndarray) -> np.ndarray:
    keys = masks.astype(np.uint64)
    for stat_index in range(STAT_COUNT):
        values = np.where(changed[stat_index], actual_fields[stat_index], 0).astype(np.uint64)
        keys |= values << np.uint64(6 + 5 * stat_index)
    return keys


def values_for_changed_mask(actual_fields: np.ndarray, changed: np.ndarray) -> np.ndarray:
    if actual_fields.shape[1] == 0:
        return np.empty(0, dtype=np.uint8)
    return actual_fields.T[changed.T].astype(np.uint8, copy=False)


def pack_record_mask_values(actual: np.ndarray, baseline: np.ndarray) -> tuple[bytes, bytes, dict[str, int]]:
    actual_fields = two_stage.iv32_stat_fields(actual)
    baseline_fields = two_stage.iv32_stat_fields(baseline)
    changed, masks = changed_mask_values(actual_fields, baseline_fields)
    values = values_for_changed_mask(actual_fields, changed)
    stats = {
        "records": int(len(actual)),
        "changed_values": int(changed.sum()),
        "mask_nonzero": int((masks != 0).sum()),
    }
    return masks.tobytes(), two_stage.pack_5bit_values(values), stats


def select_entries(
    entries: list[two_stage.base.LaneEntry],
    sample_lanes: int | None,
    sample_mode: str,
) -> list[two_stage.base.LaneEntry]:
    if sample_lanes is None or sample_lanes >= len(entries):
        return list(entries)
    if sample_lanes < 0:
        raise ValueError("--sample-lanes must be non-negative")
    if sample_lanes == 0:
        return []
    if sample_mode == "first":
        return entries[:sample_lanes]
    if sample_mode == "stride":
        indices = np.linspace(0, len(entries) - 1, sample_lanes, dtype=np.int64)
        unique_indices = list(dict.fromkeys(int(index) for index in indices.tolist()))
        return [entries[index] for index in unique_indices]
    raise ValueError(f"unsupported sample mode: {sample_mode}")


def explicit_lane_data(
    handle: BinaryIO,
    entry: two_stage.base.LaneEntry,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    _bitmap, uppers, actual = two_stage.source_actual_for_exceptions(handle, entry, predictor)
    if len(uppers) == 0:
        return uppers, actual, np.empty(0, dtype=np.int64), np.empty(0, dtype=np.uint32)
    class_ids = clf.classify_values(uppers, actual, candidate_table)
    explicit_indices = np.flatnonzero(class_ids < 0)
    explicit_uppers = uppers[explicit_indices]
    explicit_actual = actual[explicit_indices]
    baseline = candidate_table[0, explicit_uppers]
    return explicit_uppers, explicit_actual, explicit_indices, baseline


def stage2_bitmap_raw(old_exception_count: int, explicit_indices: np.ndarray) -> bytes:
    if old_exception_count == 0:
        return b""
    mask = np.zeros(old_exception_count, dtype=np.bool_)
    mask[explicit_indices] = True
    return np.packbits(mask.astype(np.uint8), bitorder="little").tobytes()


def rle_ranges(indices: np.ndarray) -> np.ndarray | None:
    if len(indices) == 0:
        return np.empty((0, 2), dtype=np.uint16)
    idx = np.asarray(indices, dtype=np.uint32)
    breaks = np.flatnonzero(np.diff(idx) != 1) + 1
    starts = np.concatenate(([0], breaks))
    ends = np.concatenate((breaks, [len(idx)]))
    run_starts = idx[starts]
    run_lengths = idx[ends - 1] - run_starts + 1
    if int(run_starts.max(initial=0)) > 0xFFFF or int(run_lengths.max(initial=0)) > 0xFFFF:
        return None
    return np.stack([run_starts, run_lengths], axis=1).astype("<u2")


def bitmap_choice_size(raw_bitmap: bytes, explicit_indices: np.ndarray, old_exception_count: int, zstd_level: int) -> dict[str, int | str]:
    choices: dict[str, int] = {}
    choices["bitmap_zstd_or_raw"] = best_zstd_size(raw_bitmap, zstd_level=zstd_level)
    choices["u16_indices_zstd_or_raw"] = best_zstd_size(pack_u16(explicit_indices), zstd_level=zstd_level)
    complement = np.setdiff1d(
        np.arange(old_exception_count, dtype=np.uint16),
        explicit_indices.astype(np.uint16, copy=False),
        assume_unique=True,
    )
    choices["u16_inverse_indices_zstd_or_raw"] = 1 + best_zstd_size(pack_u16(complement), zstd_level=zstd_level)
    ranges = rle_ranges(explicit_indices)
    if ranges is not None:
        choices["u16_ranges_zstd_or_raw"] = best_zstd_size(ranges.tobytes(), zstd_level=zstd_level)
    best_name = min(choices, key=choices.__getitem__)
    return {
        "best_kind": best_name,
        "best_size": choices[best_name],
        **choices,
    }


def nearest_baseline(
    uppers: np.ndarray,
    actual: np.ndarray,
    predictor: np.ndarray,
    candidate_table: np.ndarray,
) -> tuple[np.ndarray, np.ndarray, dict[str, int]]:
    if len(actual) == 0:
        return np.empty(0, dtype=np.uint8), np.empty(0, dtype=np.uint32), {}
    actual_fields = two_stage.iv32_stat_fields(actual)
    best_ids = np.zeros(len(actual), dtype=np.uint8)
    best_values = candidate_table[0, uppers].astype(np.uint32, copy=True)
    best_counts = (actual_fields != two_stage.iv32_stat_fields(best_values)).sum(axis=0).astype(np.uint8)

    for class_id in range(1, candidate_table.shape[0]):
        candidate_values = candidate_table[class_id, uppers]
        counts = (actual_fields != two_stage.iv32_stat_fields(candidate_values)).sum(axis=0).astype(np.uint8)
        improved = counts < best_counts
        if bool(improved.any()):
            best_counts[improved] = counts[improved]
            best_ids[improved] = class_id
            best_values[improved] = candidate_values[improved]

    predictor_id = candidate_table.shape[0]
    predictor_values = predictor[uppers]
    counts = (actual_fields != two_stage.iv32_stat_fields(predictor_values)).sum(axis=0).astype(np.uint8)
    improved = counts < best_counts
    if bool(improved.any()):
        best_counts[improved] = counts[improved]
        best_ids[improved] = predictor_id
        best_values[improved] = predictor_values[improved]

    stats = {f"nearest_records_changed_{count}": int((best_counts == count).sum()) for count in range(7)}
    for selector in np.unique(best_ids):
        stats[f"nearest_selector_{int(selector)}_records"] = int((best_ids == selector).sum())
    return best_ids, best_values, stats


def train_dictionary(samples: list[bytes], dict_size: int) -> zstd.ZstdCompressionDict | None:
    usable = [sample for sample in samples if sample]
    if len(usable) < 8 or sum(len(sample) for sample in usable) < dict_size:
        return None
    try:
        return zstd.train_dictionary(dict_size, usable)
    except zstd.ZstdError:
        return None


def first_pass(
    *,
    input_path: Path,
    predictor_json: Path,
    candidate_table: np.ndarray,
    sample_lanes: int | None,
    sample_mode: str,
    top_classes: int,
    dict_sample_bytes: int,
    progress_every: int,
) -> tuple[list[int], list[bytes], dict[int, list[bytes]], dict[str, int]]:
    key_counter: Counter[int] = Counter()
    dict_samples: list[bytes] = []
    mod24_samples: dict[int, list[bytes]] = defaultdict(list)
    sampled_bytes = 0
    mod24_sampled_bytes: dict[int, int] = defaultdict(int)
    totals = {
        "lanes": 0,
        "old_exceptions": 0,
        "explicit_records": 0,
        "class_counted_records": 0,
    }

    with input_path.open("rb") as handle:
        header = two_stage.base.parse_header(handle)
        entries = select_entries(two_stage.base.parse_lane_entries(handle, header), sample_lanes, sample_mode)
        predictor, _source = clf.load_predictor(handle, header, predictor_json)
        for index, entry in enumerate(entries, 1):
            uppers, actual, explicit_indices, baseline = explicit_lane_data(handle, entry, predictor, candidate_table)
            totals["lanes"] += 1
            totals["old_exceptions"] += int(entry.predictor_exceptions)
            totals["explicit_records"] += int(len(actual))
            if len(actual):
                raw, _stats = two_stage.pack_stat_delta_values(actual, baseline)
                if sampled_bytes < dict_sample_bytes:
                    dict_samples.append(raw)
                    sampled_bytes += len(raw)
                mod24 = entry.lane % 24
                if mod24_sampled_bytes[mod24] < max(1, dict_sample_bytes // 24):
                    mod24_samples[mod24].append(raw)
                    mod24_sampled_bytes[mod24] += len(raw)

                actual_fields = two_stage.iv32_stat_fields(actual)
                baseline_fields = two_stage.iv32_stat_fields(baseline)
                changed, masks = changed_mask_values(actual_fields, baseline_fields)
                keys = residual_keys(actual_fields, changed, masks)
                key_counter.update(int(key) for key in keys.tolist())
                totals["class_counted_records"] += int(len(keys))

            if progress_every and (index % progress_every == 0 or index == len(entries)):
                print(f"first pass: {index}/{len(entries)} lanes", flush=True)

    top_keys = [key for key, _count in key_counter.most_common(top_classes)]
    totals["unique_residual_keys_seen"] = len(key_counter)
    totals["top_class_records"] = sum(key_counter[key] for key in top_keys)
    return top_keys, dict_samples, dict(mod24_samples), totals


def evaluate(
    *,
    input_path: Path,
    report_path: Path,
    baseline_report: Path,
    predictor_json: Path,
    start_rng: int,
    runtime_max_steps: int,
    base_model: str,
    max_extra: int,
    sample_lanes: int | None,
    sample_mode: str,
    progress_every: int,
    zstd_level: int,
    top_classes: int,
    dict_size: int,
    dict_sample_bytes: int,
    scratch_dir: Path | None,
    keep_scratch: bool,
) -> dict[str, object]:
    started = time.perf_counter()
    baseline_data = json.loads(baseline_report.read_text(encoding="utf-8")) if baseline_report.is_file() else None
    baseline_full = {
        "size_bytes": baseline_data.get("size_bytes") if baseline_data else None,
        "stage2_bitmap_stream_bytes": (
            baseline_data.get("totals", {}).get("stage2_bitmap_stream_bytes") if baseline_data else None
        ),
        "value_stream_bytes": baseline_data.get("totals", {}).get("value_stream_bytes") if baseline_data else None,
        "stage2_explicit": baseline_data.get("stage2", {}).get("still_explicit") if baseline_data else None,
    }
    candidate_table, classes, model_meta = two_stage.build_candidate_model(
        start_rng=start_rng,
        runtime_max_steps=runtime_max_steps,
        base_model=base_model,
        max_extra=max_extra,
        residual_encoding=two_stage.RESIDUAL_ENCODING_STAT_DELTA,
    )

    scratch_context: tempfile.TemporaryDirectory[str] | None = None
    if scratch_dir is None:
        scratch_context = tempfile.TemporaryDirectory(prefix="spc3-residual-opt-")
        scratch_root = Path(scratch_context.name)
    else:
        scratch_root = scratch_dir
        scratch_root.mkdir(parents=True, exist_ok=True)

    try:
        top_keys, dict_samples, mod24_samples, first_totals = first_pass(
            input_path=input_path,
            predictor_json=predictor_json,
            candidate_table=candidate_table,
            sample_lanes=sample_lanes,
            sample_mode=sample_mode,
            top_classes=top_classes,
            dict_sample_bytes=dict_sample_bytes,
            progress_every=progress_every,
        )
        top_key_to_id = {key: index + 1 for index, key in enumerate(top_keys)}
        global_dict = train_dictionary(dict_samples, dict_size)
        mod24_dicts_raw = {mod24: train_dictionary(samples, dict_size) for mod24, samples in mod24_samples.items()}
        mod24_dicts = {mod24: dictionary for mod24, dictionary in mod24_dicts_raw.items() if dictionary is not None}

        buckets = BucketStore(scratch_root)
        totals: dict[str, int] = defaultdict(int)
        bitmap_choice_totals: dict[str, int] = defaultdict(int)
        nearest_stats: dict[str, int] = defaultdict(int)
        current_value_zstd_dict_global = 0
        current_value_zstd_dict_mod24 = 0

        with input_path.open("rb") as handle:
            header = two_stage.base.parse_header(handle)
            entries = select_entries(two_stage.base.parse_lane_entries(handle, header), sample_lanes, sample_mode)
            predictor, predictor_source = clf.load_predictor(handle, header, predictor_json)
            for index, entry in enumerate(entries, 1):
                old_bitmap, old_uppers, old_actual = two_stage.source_actual_for_exceptions(handle, entry, predictor)
                class_ids = clf.classify_values(old_uppers, old_actual, candidate_table) if len(old_uppers) else np.empty(0)
                explicit_indices = np.flatnonzero(class_ids < 0)
                uppers = old_uppers[explicit_indices]
                actual = old_actual[explicit_indices]
                baseline = candidate_table[0, uppers] if len(uppers) else np.empty(0, dtype=np.uint32)

                totals["lanes"] += 1
                totals["old_exceptions"] += int(len(old_uppers))
                totals["stage2_explicit"] += int(len(actual))
                totals["stage2_normal"] += int((class_ids == 0).sum()) if len(class_ids) else 0
                totals["stage2_shift"] += int((class_ids > 0).sum()) if len(class_ids) else 0

                explicit_bitmap = stage2_bitmap_raw(len(old_uppers), explicit_indices)
                current_bitmap_size = best_zstd_size(explicit_bitmap, zstd_level=zstd_level)
                totals["current_stage2_bitmap_size"] += current_bitmap_size
                bitmap_choice = bitmap_choice_size(explicit_bitmap, explicit_indices, len(old_uppers), zstd_level)
                bitmap_choice_totals["best_size"] += int(bitmap_choice["best_size"])
                bitmap_choice_totals[f"choice_{bitmap_choice['best_kind']}"] += 1
                for key, value in bitmap_choice.items():
                    if key.endswith("_or_raw"):
                        bitmap_choice_totals[key] += int(value)

                if len(actual) == 0:
                    if progress_every and (index % progress_every == 0 or index == len(entries)):
                        print(f"second pass: {index}/{len(entries)} lanes", flush=True)
                    continue

                current_raw, current_stats = two_stage.pack_stat_delta_values(actual, baseline)
                current_size = best_zstd_size(current_raw, zstd_level=zstd_level)
                totals["current_value_raw_size"] += len(current_raw)
                totals["current_value_stream_size"] += current_size
                totals["changed_values"] += current_stats["stat_delta_changed_values"]
                for count in range(7):
                    totals[f"records_changed_{count}"] += current_stats[f"stat_delta_records_changed_{count}"]
                for stat in range(6):
                    totals[f"stat_{stat}_changed_values"] += current_stats[f"stat_delta_stat_{stat}_changed_values"]

                if global_dict is not None:
                    current_value_zstd_dict_global += min(
                        len(current_raw),
                        zstd_bytes_size(current_raw, zstd_level=zstd_level, dict_data=global_dict),
                    )
                mod24_dict = mod24_dicts.get(entry.lane % 24)
                if mod24_dict is not None:
                    current_value_zstd_dict_mod24 += min(
                        len(current_raw),
                        zstd_bytes_size(current_raw, zstd_level=zstd_level, dict_data=mod24_dict),
                    )
                else:
                    current_value_zstd_dict_mod24 += current_size

                actual_fields = two_stage.iv32_stat_fields(actual)
                baseline_fields = two_stage.iv32_stat_fields(baseline)
                changed, masks = changed_mask_values(actual_fields, baseline_fields)
                values_flat = values_for_changed_mask(actual_fields, changed)

                # 1 + 4: split masks and changed stat values into global streams.
                for stat in range(6):
                    stat_mask = np.packbits(changed[stat].astype(np.uint8), bitorder="little").tobytes()
                    buckets.write(f"split_stat_mask_{stat}", stat_mask)
                    buckets.write(f"split_stat_values_{stat}", two_stage.pack_5bit_values(actual_fields[stat][changed[stat]]))

                # 1: split by lane classes and upper-byte bands.
                buckets.write(f"lane_mod24_{entry.lane % 24:02d}", current_raw)
                buckets.write(f"lane_group_{two_stage.base.lane_group(entry.lane):03d}", current_raw)
                for upper_band in np.unique((uppers >> 8).astype(np.uint8)):
                    band_mask = ((uppers >> 8).astype(np.uint8) == upper_band)
                    band_raw, _band_stats = two_stage.pack_stat_delta_values(actual[band_mask], baseline[band_mask])
                    buckets.write(f"upper_band_{int(upper_band):03d}", band_raw)

                # 4: one record-mask stream plus one value stream in record order.
                buckets.write("record_masks", masks.tobytes())
                buckets.write("record_values", two_stage.pack_5bit_values(values_flat))

                # 1 + 2: changed-mask grouping also exposes class-table literals.
                buckets.write("mask_group_masks", masks.tobytes())
                for mask_value in np.unique(masks):
                    mask_subset = masks == mask_value
                    grouped_values = values_for_changed_mask(actual_fields[:, mask_subset], changed[:, mask_subset])
                    buckets.write(f"mask_group_values_{int(mask_value):02d}", two_stage.pack_5bit_values(grouped_values))

                keys = residual_keys(actual_fields, changed, masks)
                class_ids_out = np.zeros(len(keys), dtype=np.uint8)
                literal_mask = np.ones(len(keys), dtype=np.bool_)
                for key, class_id in top_key_to_id.items():
                    matching = keys == key
                    if bool(matching.any()):
                        class_ids_out[matching] = class_id
                        literal_mask[matching] = False
                buckets.write("class_table_ids", class_ids_out.tobytes())
                if bool(literal_mask.any()):
                    literal_raw, _literal_stats = two_stage.pack_stat_delta_values(actual[literal_mask], baseline[literal_mask])
                    buckets.write("class_table_literals", literal_raw)
                totals["class_table_hits"] += int((class_ids_out != 0).sum())
                totals["class_table_literals"] += int(literal_mask.sum())

                # 6: nearest predictor selector among runtime candidates plus old predictor.
                selectors, selected_baseline, nstats = nearest_baseline(uppers, actual, predictor, candidate_table)
                buckets.write("nearest_selectors", selectors.tobytes())
                for key, value in nstats.items():
                    nearest_stats[key] += value
                for selector in np.unique(selectors):
                    selector_mask = selectors == selector
                    selector_raw, _selector_stats = two_stage.pack_stat_delta_values(
                        actual[selector_mask],
                        selected_baseline[selector_mask],
                    )
                    buckets.write(f"nearest_values_{int(selector):02d}", selector_raw)

                if progress_every and (index % progress_every == 0 or index == len(entries)):
                    print(f"second pass: {index}/{len(entries)} lanes", flush=True)

        class_table_raw = pack_u64(top_keys)
        class_table_size = best_zstd_size(class_table_raw, zstd_level=zstd_level)
        global_dict_size = len(global_dict.as_bytes()) if global_dict is not None else 0
        mod24_dict_size = sum(len(dictionary.as_bytes()) for dictionary in mod24_dicts.values())
        strategies = {
            "0_current_v5_value_component": {
                "raw_bytes": totals["current_value_raw_size"],
                "compressed_bytes": totals["current_value_stream_size"],
                "notes": "current per-lane stat-delta values; baseline for value-stream comparisons",
            },
            "1_split_stat_streams": {
                "raw_bytes": buckets.raw_size("split_stat_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="split_stat_"),
                "notes": "six global changed-stat masks plus six global 5-bit stat value streams",
            },
            "1_lane_mod24_split": {
                "raw_bytes": buckets.raw_size("lane_mod24_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="lane_mod24_"),
                "notes": "current stat-delta chunks grouped into 24 lane modulo classes",
            },
            "1_lane_mod24_lowbyte_split": {
                "raw_bytes": buckets.raw_size("lane_group_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="lane_group_"),
                "notes": "current stat-delta chunks grouped by lane low byte plus lane modulo 24",
            },
            "1_upper_byte_split": {
                "raw_bytes": buckets.raw_size("upper_band_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="upper_band_"),
                "notes": "current stat-delta values grouped by upper PID high byte",
            },
            "2_residual_class_table": {
                "raw_bytes": buckets.raw_size("class_table_") + len(class_table_raw),
                "compressed_bytes": (
                    buckets.compressed_size(zstd_level=zstd_level, prefix="class_table_") + class_table_size
                ),
                "class_table_bytes": class_table_size,
                "top_classes": len(top_keys),
                "class_hits": totals["class_table_hits"],
                "literals": totals["class_table_literals"],
            },
            "3_stage2_bitmap_current": {
                "raw_bytes": int(math.ceil(totals["old_exceptions"] / 8)),
                "compressed_bytes": totals["current_stage2_bitmap_size"],
                "notes": "current per-lane zstd-or-raw bitmap",
            },
            "3_stage2_bitmap_choice": {
                "compressed_bytes": bitmap_choice_totals["best_size"],
                "lane_choice_counts": {
                    key.removeprefix("choice_"): value for key, value in bitmap_choice_totals.items() if key.startswith("choice_")
                },
                "candidate_totals": {
                    key: value for key, value in bitmap_choice_totals.items() if key.endswith("_or_raw")
                },
            },
            "4_record_mask_value_split": {
                "raw_bytes": buckets.raw_size("record_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="record_"),
                "notes": "one byte changed-mask per explicit record plus record-order 5-bit values",
            },
            "4_changed_mask_group_split": {
                "raw_bytes": buckets.raw_size("mask_group_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="mask_group_"),
                "notes": "changed-mask stream plus separate value streams per changed-stat mask",
            },
            "5_zstd_dictionary_global": {
                "compressed_bytes": (
                    current_value_zstd_dict_global + global_dict_size if global_dict is not None else None
                ),
                "compressed_payload_bytes": current_value_zstd_dict_global if global_dict is not None else None,
                "dictionary_trained": global_dict is not None,
                "dict_size": global_dict_size,
            },
            "5_zstd_dictionary_lane_mod24": {
                "compressed_bytes": current_value_zstd_dict_mod24 + mod24_dict_size,
                "compressed_payload_bytes": current_value_zstd_dict_mod24,
                "dictionaries_trained": len(mod24_dicts),
                "dict_size_total": mod24_dict_size,
            },
            "6_nearest_baseline_selector": {
                "raw_bytes": buckets.raw_size("nearest_"),
                "compressed_bytes": buckets.compressed_size(zstd_level=zstd_level, prefix="nearest_"),
                "selector_count": candidate_table.shape[0] + 1,
                "selector_note": "runtime classes plus old embedded predictor as a nearest-delta baseline",
                "nearest_stats": dict(nearest_stats),
            },
        }

        current_value = strategies["0_current_v5_value_component"]["compressed_bytes"]
        current_bitmap = strategies["3_stage2_bitmap_current"]["compressed_bytes"]
        for strategy in strategies.values():
            compressed = strategy.get("compressed_bytes")
            if not isinstance(compressed, int):
                continue
            if strategy is strategies["3_stage2_bitmap_current"] or strategy is strategies["3_stage2_bitmap_choice"]:
                baseline = current_bitmap
                label = "bitmap"
                full_component = baseline_full["stage2_bitmap_stream_bytes"]
            else:
                baseline = current_value
                label = "value"
                full_component = baseline_full["value_stream_bytes"]
            if isinstance(baseline, int):
                if label == "value":
                    strategy["delta_vs_current_value_bytes"] = compressed - current_value
                    strategy["pct_vs_current_value"] = (100.0 * compressed / current_value) if current_value else 0.0
                strategy[f"delta_vs_current_{label}_bytes"] = compressed - baseline
                strategy[f"pct_vs_current_{label}"] = (100.0 * compressed / baseline) if baseline else 0.0
                if baseline and baseline_full["size_bytes"] is not None and full_component is not None:
                    ratio = compressed / baseline
                    projected_component = int(round(full_component * ratio))
                    projected_size = int(baseline_full["size_bytes"] - full_component + projected_component)
                    strategy["projected_full_package_bytes_if_used_alone"] = projected_size
                    strategy["projected_delta_full_package_bytes_if_used_alone"] = (
                        projected_size - int(baseline_full["size_bytes"])
                    )
                    strategy["projection_note"] = (
                        f"replaces only the current v5 {label} component; sample ratio is applied to the full component"
                    )

        report = {
            "schema": "spc3_residual_optimizer.v2",
            "generated": time.strftime("%Y-%m-%d %H:%M:%S"),
            "input": str(input_path),
            "sample_lanes": sample_lanes,
            "sample_mode": sample_mode,
            "zstd_level": zstd_level,
            "top_classes": top_classes,
            "dict_size": dict_size,
            "dict_sample_bytes": dict_sample_bytes,
            "predictor_source": predictor_source,
            "model": model_meta,
            "class_count": len(classes),
            "first_pass": first_totals,
            "totals": dict(totals),
            "strategies": strategies,
            "baseline_full_v5": baseline_full,
            "elapsed_seconds": time.perf_counter() - started,
            "scratch_dir": str(scratch_root) if keep_scratch else None,
        }
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"wrote {report_path}")
        return report
    finally:
        if "buckets" in locals():
            buckets.close_all()
        if scratch_context is not None and not keep_scratch:
            scratch_context.cleanup()


def main() -> int:
    args = parse_args()
    if not 1 <= args.zstd_level <= 22:
        raise SystemExit("--zstd-level must be in 1..22")
    if not 0 <= args.top_classes <= 255:
        raise SystemExit("--top-classes must be in 0..255 because class IDs are stored as u8")
    if args.sample_lanes is not None and args.sample_lanes < 0:
        raise SystemExit("--sample-lanes must be non-negative")
    sample_lanes = None if args.all_lanes else args.sample_lanes
    evaluate(
        input_path=args.input,
        report_path=args.report,
        baseline_report=args.baseline_report,
        predictor_json=args.predictor_json,
        start_rng=two_stage.parse_int(args.start_rng),
        runtime_max_steps=args.runtime_max_steps,
        base_model=args.base_model,
        max_extra=args.max_extra,
        sample_lanes=sample_lanes,
        sample_mode=args.sample_mode,
        progress_every=args.progress_every,
        zstd_level=args.zstd_level,
        top_classes=args.top_classes,
        dict_size=args.dict_size,
        dict_sample_bytes=args.dict_sample_bytes,
        scratch_dir=args.scratch_dir,
        keep_scratch=args.keep_scratch,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
