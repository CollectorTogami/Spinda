from __future__ import annotations

import csv
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import wave

import numpy as np


MODULE_PATH = Path(__file__).resolve().parents[1] / "general_leitmotif_finder_gui.py"
SPEC = importlib.util.spec_from_file_location("general_leitmotif_finder_gui", MODULE_PATH)
assert SPEC and SPEC.loader
finder = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = finder
SPEC.loader.exec_module(finder)


def write_test_wav(path: Path) -> None:
    sample_rate = 22050
    total_seconds = 12.0
    audio = np.zeros(int(sample_rate * total_seconds), dtype=np.float32)
    motif = [(440.0, 0.5), (554.37, 0.5), (659.25, 0.5), (554.37, 0.5)] * 2

    def add_motif(start_seconds: float) -> None:
        cursor = int(start_seconds * sample_rate)
        phase = 0.0
        for freq, duration in motif:
            count = int(duration * sample_rate)
            t = np.arange(count, dtype=np.float32) / sample_rate
            note = 0.55 * np.sin(2 * math.pi * freq * t + phase)
            fade = min(128, count // 8)
            if fade:
                note[:fade] *= np.linspace(0, 1, fade, dtype=np.float32)
                note[-fade:] *= np.linspace(1, 0, fade, dtype=np.float32)
            audio[cursor : cursor + count] += note.astype(np.float32)
            cursor += count
            phase = float((phase + 2 * math.pi * freq * duration) % (2 * math.pi))

    add_motif(2.0)
    add_motif(8.0)
    pcm = np.clip(audio, -1.0, 1.0)
    pcm16 = (pcm * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm16.tobytes())


class TestGeneralLeitmotifFinder(unittest.TestCase):
    def test_parse_timestamp_accepts_common_forms(self) -> None:
        self.assertAlmostEqual(finder.parse_timestamp("12.5"), 12.5)
        self.assertAlmostEqual(finder.parse_timestamp("01:02.50"), 62.5)
        self.assertAlmostEqual(finder.parse_timestamp("01:02:03.25"), 3723.25)

    def test_parse_timestamp_rejects_negative_and_bad_ranges(self) -> None:
        for value in ("", "-1", "nan", "inf", "00:nan", "00:-01", "00:60.0", "01:60:00", "a:b"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    finder.parse_timestamp(value)

    def test_find_ffmpeg_checks_parent_bundle_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source"
            source.mkdir()
            ffmpeg = root / "ffmpeg.exe"
            ffmpeg.write_bytes(b"fake")
            original_is_file = finder.Path.is_file

            def fake_is_file(path: Path) -> bool:
                if str(path).lower() in {
                    str(source / "ffmpeg.exe").lower(),
                    str(root / "ffmpeg.exe").lower(),
                }:
                    return original_is_file(path)
                if str(path).lower().endswith("ffmpeg.exe"):
                    return False
                return original_is_file(path)

            with (
                mock.patch.dict(finder.os.environ, {"FFMPEG": ""}, clear=False),
                mock.patch.object(finder, "app_dir", return_value=source),
                mock.patch.object(finder, "resource_dir", return_value=source),
                mock.patch.object(finder.Path, "is_file", fake_is_file),
                mock.patch.object(finder.shutil, "which", return_value=None),
            ):
                found = finder.find_ffmpeg()

            self.assertEqual(Path(found), ffmpeg)

    def test_find_ffmpeg_accepts_quoted_env_folder(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bin_dir = Path(tmp) / "ffmpeg bin"
            bin_dir.mkdir()
            ffmpeg = bin_dir / "ffmpeg.exe"
            ffmpeg.write_bytes(b"fake")

            with mock.patch.dict(finder.os.environ, {"FFMPEG": f'"{bin_dir}"'}, clear=False):
                found = finder.find_ffmpeg()

            self.assertEqual(Path(found), ffmpeg)

    def test_ffmpeg_env_candidates_support_file_and_folder_values(self) -> None:
        candidates = finder.ffmpeg_env_candidates("'C:/tools/ffmpeg/bin'")

        self.assertEqual(candidates[0], Path("C:/tools/ffmpeg/bin"))
        self.assertEqual(candidates[1], Path("C:/tools/ffmpeg/bin/ffmpeg.exe"))
        self.assertEqual(candidates[2], Path("C:/tools/ffmpeg/bin/ffmpeg"))
        self.assertEqual(finder.ffmpeg_env_candidates("  "), [])

    def test_strip_wrapping_quotes_keeps_unmatched_path_quotes(self) -> None:
        self.assertEqual(finder.strip_wrapping_quotes('"C:/tools/ffmpeg/bin"'), "C:/tools/ffmpeg/bin")
        self.assertEqual(finder.strip_wrapping_quotes("'C:/tools/ffmpeg/bin'"), "C:/tools/ffmpeg/bin")
        self.assertEqual(finder.strip_wrapping_quotes("C:/tools/ffmpeg's/bin"), "C:/tools/ffmpeg's/bin")
        self.assertEqual(finder.strip_wrapping_quotes("C:/tools/bin'"), "C:/tools/bin'")

    def test_confidence_and_strength_boundaries(self) -> None:
        self.assertEqual(finder.confidence_from_score(0.0), 0)
        self.assertEqual(finder.confidence_from_score(0.60), 20)
        self.assertEqual(finder.confidence_from_score(1.0), 100)
        self.assertEqual(finder.strength_from_score(0.91), "anchor")
        self.assertEqual(finder.strength_from_score(0.85), "strong")
        self.assertEqual(finder.strength_from_score(0.75), "clear")
        self.assertEqual(finder.strength_from_score(0.65), "echo")

    def test_score_helpers_reject_non_finite_scores(self) -> None:
        for value in (math.nan, math.inf, -math.inf):
            with self.subTest(value=value):
                with self.assertRaisesRegex(ValueError, "score must be finite"):
                    finder.confidence_from_score(value)
                with self.assertRaisesRegex(ValueError, "score must be finite"):
                    finder.strength_from_score(value)

    def test_select_hits_suppresses_overlapping_windows(self) -> None:
        candidates = [
            {"start_seconds": 3.0, "end_seconds": 9.0, "score": 0.80},
            {"start_seconds": 5.0, "end_seconds": 11.0, "score": 0.90},
            {"start_seconds": 12.0, "end_seconds": 18.0, "score": 0.70},
        ]
        selected = finder.select_hits(candidates, nms_seconds=1.0)
        self.assertEqual([hit["start_seconds"] for hit in selected], [5.0, 12.0])

    def test_select_hits_matches_slow_reference_for_many_candidates(self) -> None:
        rng = np.random.default_rng(123)
        candidates: list[dict[str, object]] = []
        for index in range(400):
            start = float(rng.uniform(0.0, 240.0))
            length = float(rng.uniform(0.5, 8.0))
            candidates.append(
                {
                    "id": index,
                    "start_seconds": round(start, 3),
                    "end_seconds": round(start + length, 3),
                    "score": round(float(rng.uniform(0.6, 1.0)), 4),
                }
            )

        expected: list[dict[str, object]] = []
        for candidate in sorted(candidates, key=lambda item: float(item["score"]), reverse=True):
            start = float(candidate["start_seconds"])
            end = float(candidate["end_seconds"])
            if not any(
                abs(start - float(hit["start_seconds"])) < 2.0
                or max(start, float(hit["start_seconds"])) < min(end, float(hit["end_seconds"]))
                for hit in expected
            ):
                expected.append(candidate)
        expected.sort(key=lambda item: float(item["start_seconds"]))

        actual = finder.select_hits([dict(item) for item in candidates], nms_seconds=2.0)
        self.assertEqual([hit["id"] for hit in actual], [hit["id"] for hit in expected])

    def test_select_hits_does_not_mutate_candidate_order(self) -> None:
        candidates = [
            {"start_seconds": 10.0, "end_seconds": 12.0, "score": 0.70, "id": "first"},
            {"start_seconds": 20.0, "end_seconds": 22.0, "score": 0.90, "id": "second"},
        ]
        original = [dict(item) for item in candidates]
        finder.select_hits(candidates, nms_seconds=1.0)
        self.assertEqual(candidates, original)

    def test_select_hits_rejects_invalid_nms(self) -> None:
        candidates = [{"start_seconds": 0.0, "end_seconds": 1.0, "score": 0.9}]
        for nms in (-1.0, math.nan, math.inf):
            with self.subTest(nms=nms):
                with self.assertRaisesRegex(ValueError, "NMS"):
                    finder.select_hits(candidates, nms_seconds=nms)

    def test_select_hits_rejects_invalid_candidate_values(self) -> None:
        cases = [
            {"start_seconds": math.nan, "end_seconds": 1.0, "score": 0.9},
            {"start_seconds": 0.0, "end_seconds": 1.0, "score": math.inf},
            {"start_seconds": -1.0, "end_seconds": 1.0, "score": 0.9},
            {"start_seconds": 2.0, "end_seconds": 1.0, "score": 0.9},
        ]
        for candidate in cases:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValueError):
                    finder.select_hits([candidate], nms_seconds=1.0)

    def test_select_hits_handles_duplicate_zero_width_candidates(self) -> None:
        candidates = [
            {"start_seconds": 1.0, "end_seconds": 1.0, "score": 0.9, "id": "a"},
            {"start_seconds": 1.0, "end_seconds": 1.0, "score": 0.8, "id": "b"},
        ]
        selected = finder.select_hits(candidates, nms_seconds=0.0)
        self.assertEqual([hit["id"] for hit in selected], ["a", "b"])

    def test_smooth_feature_rows_preserves_shape_and_normalizes(self) -> None:
        features = np.eye(12, dtype=np.float32)[:6]
        smoothed = finder.smooth_feature_rows(features, width=5)
        self.assertEqual(smoothed.shape, features.shape)
        norms = np.linalg.norm(smoothed, axis=1)
        self.assertTrue(np.all(norms > 0.99))

    def test_validate_feature_matrix_rejects_bad_shapes_and_values(self) -> None:
        cases = [
            np.ones(12, dtype=np.float32),
            np.ones((3, 0), dtype=np.float32),
            np.array([[1.0, math.nan]], dtype=np.float32),
        ]
        for features in cases:
            with self.subTest(shape=features.shape):
                with self.assertRaises(ValueError):
                    finder.validate_feature_matrix(features)

    def test_write_outputs_escapes_markdown_and_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ref = finder.Reference(Path("C:/audio/ref|tick`name.wav"), 1.0, 3.0, "ref|\n`one")
            hits = [
                {
                    "file": "C:/audio/song.wav",
                    "name": "song|\nname.wav",
                    "start": "00:01.00",
                    "end": "00:03.00",
                    "start_seconds": 1.0,
                    "end_seconds": 3.0,
                    "score": 0.88,
                    "confidence": 92,
                    "strength": "strong",
                    "template_duration_seconds": 2.0,
                    "matched_template": "ref|\r`one",
                    "duration_seconds": 5.0,
                }
            ]
            md_path, csv_path = finder.write_outputs(
                out,
                "audit\nreport",
                [ref],
                [Path("C:/audio/song.wav")],
                hits,
                [{"file": "bad|file", "error": "line1\nline2\x00"}],
                {"threshold": 0.6, "bad\nkey": "bad`value"},
            )
            self.assertTrue(md_path.exists())
            self.assertTrue(csv_path.exists())
            text = md_path.read_text(encoding="utf-8")
            self.assertIn("# audit report", text)
            self.assertIn("ref\\| 'one", text)
            self.assertIn("song\\| name.wav", text)
            self.assertIn("line1 line2", text)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["confidence"], "92")
            self.assertEqual(rows[0]["name"], "song|\nname.wav")
            data = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertIn(f"Created: `{data['created']}`", text)

    def test_write_outputs_normalizes_malformed_hits_and_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            ref = finder.Reference(Path("C:/audio/ref.wav"), 0.0, 2.0, "ref")
            hits = [
                {
                    "file": "C:/audio/song.wav",
                    "score": math.nan,
                    "confidence": 999,
                    "end_seconds": -5,
                    "extra": Path("not-json-native"),
                }
            ]
            md_path, csv_path = finder.write_outputs(
                out,
                "malformed",
                [ref],
                [Path("C:/audio/song.wav")],
                hits,
                [{"file": Path("bad.wav")}],
                {"output": Path("C:/out")},
            )
            text = md_path.read_text(encoding="utf-8")
            self.assertIn("song.wav", text)
            self.assertIn("0.0000", text)
            self.assertIn("100", text)
            self.assertIn("bad.wav", text)
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["name"], "song.wav")
            self.assertEqual(rows[0]["score"], "0.0")
            self.assertEqual(rows[0]["confidence"], "100")
            data = json.loads(md_path.with_suffix(".json").read_text(encoding="utf-8"))
            self.assertEqual(data["settings"]["output"], "C:\\out")

    def test_normalize_hit_fills_missing_fields(self) -> None:
        hit = finder.normalize_hit({"file": "C:/audio/song.wav", "start_seconds": "2.5"})
        self.assertEqual(hit["name"], "song.wav")
        self.assertEqual(hit["start"], "00:02.50")
        self.assertEqual(hit["end"], "00:02.50")
        self.assertEqual(hit["strength"], "echo")

    def test_normalize_hit_clamps_negative_start_seconds(self) -> None:
        hit = finder.normalize_hit(
            {
                "file": "C:/audio/song.wav",
                "start_seconds": -4.0,
                "end_seconds": -1.0,
                "score": 0.75,
            }
        )

        self.assertEqual(hit["start_seconds"], 0.0)
        self.assertEqual(hit["end_seconds"], 0.0)
        self.assertEqual(hit["start"], "00:00.00")
        self.assertEqual(hit["end"], "00:00.00")

    def test_normalize_hit_rebuilds_display_times_from_sanitized_seconds(self) -> None:
        hit = finder.normalize_hit(
            {
                "file": "C:/audio/song.wav",
                "start": "bad start",
                "end": "bad end",
                "start_seconds": -3.0,
                "end_seconds": 1.25,
                "score": 0.7,
            }
        )

        self.assertEqual(hit["start_seconds"], 0.0)
        self.assertEqual(hit["end_seconds"], 1.25)
        self.assertEqual(hit["start"], "00:00.00")
        self.assertEqual(hit["end"], "00:01.25")

    def test_collect_corpus_filters_supported_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.mp3").write_bytes(b"")
            (root / "b.txt").write_bytes(b"")
            (root / "sub").mkdir()
            (root / "sub" / "c.wav").write_bytes(b"")
            non_recursive = finder.collect_corpus([root], recursive=False)
            recursive = finder.collect_corpus([root], recursive=True)
            self.assertEqual([p.name for p in non_recursive], ["a.mp3"])
            self.assertEqual([p.name for p in recursive], ["a.mp3", "c.wav"])

    def test_safe_filename_handles_reserved_and_long_names(self) -> None:
        self.assertEqual(finder.safe_filename("CON"), "CON_report")
        self.assertEqual(finder.safe_filename("CON.txt"), "CON_report.txt")
        self.assertEqual(finder.safe_filename("bad|name`."), "bad_name")
        self.assertLessEqual(len(finder.safe_filename("x" * 300)), 120)

    def test_parse_and_format_path_list_preserves_quoted_semicolons(self) -> None:
        first = Path("C:/audio/one;two.mp3").resolve()
        second = Path("C:/audio/plain.mp3").resolve()
        encoded = finder.format_path_list([first, second])
        self.assertIn('"', encoded)
        self.assertEqual(finder.parse_path_list(encoded), [first, second])
        self.assertEqual(finder.parse_path_list(f'"{first}"; {second}'), [first, second])

    def test_parse_path_list_rejects_unclosed_quote(self) -> None:
        with self.assertRaisesRegex(ValueError, "invalid path list"):
            finder.parse_path_list('"C:/audio/broken.mp3')

    def test_motif_set_round_trips_references_and_settings(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref_file = root / "theme.mp3"
            ref_file.write_bytes(b"placeholder")
            out = root / "out"
            motif_set = finder.MotifSet(
                references=(
                    finder.Reference(ref_file, 2.5, 8.75, "main theme"),
                    finder.Reference(ref_file, 30.0, 36.0, "variant"),
                ),
                corpus_paths=(root,),
                title="sample motifs",
                output_folder=out,
                threshold="0.72",
                step="0.25",
                nms="4",
                transpose=True,
                recursive=False,
            )
            path = root / "motifs.json"

            finder.save_motif_set(path, motif_set)
            loaded = finder.load_motif_set(path)

            self.assertEqual([ref.label for ref in loaded.references], ["main theme", "variant"])
            self.assertEqual([ref.start for ref in loaded.references], [2.5, 30.0])
            self.assertEqual([ref.end for ref in loaded.references], [8.75, 36.0])
            self.assertEqual(loaded.corpus_paths, (root.resolve(),))
            self.assertEqual(loaded.title, "sample motifs")
            self.assertEqual(loaded.output_folder, out.resolve())
            self.assertEqual(loaded.threshold, "0.72")
            self.assertEqual(loaded.step, "0.25")
            self.assertEqual(loaded.nms, "4")
            self.assertTrue(loaded.transpose)
            self.assertFalse(loaded.recursive)

    def test_motif_set_loads_text_timestamps_and_default_label(self) -> None:
        data = {
            "schema": finder.MOTIF_SET_SCHEMA,
            "references": [
                {
                    "file": "C:/audio/theme.mp3",
                    "start": "00:03.00",
                    "end": "00:09.50",
                }
            ],
            "settings": {"threshold": 0.75, "step": 0.25, "nms": 4, "transpose": False, "recursive": True},
        }

        loaded = finder.motif_set_from_data(data)

        self.assertEqual(len(loaded.references), 1)
        self.assertAlmostEqual(loaded.references[0].start, 3.0)
        self.assertAlmostEqual(loaded.references[0].end, 9.5)
        self.assertIn("theme.mp3", loaded.references[0].label)
        self.assertEqual(loaded.threshold, "0.75")
        self.assertEqual(loaded.step, "0.25")
        self.assertEqual(loaded.nms, "4")
        self.assertFalse(loaded.transpose)
        self.assertTrue(loaded.recursive)

    def test_motif_set_rejects_bad_schema_and_ranges(self) -> None:
        with self.assertRaisesRegex(ValueError, "unsupported motif data schema"):
            finder.motif_set_from_data({"references": []})
        with self.assertRaisesRegex(ValueError, "at most"):
            finder.motif_set_from_data(
                {
                    "schema": finder.MOTIF_SET_SCHEMA,
                    "references": [
                        {
                            "file": "C:/audio/theme.mp3",
                            "start_seconds": 0,
                            "end_seconds": finder.MAX_REFERENCE_SECONDS + 1,
                        }
                    ],
                }
            )
        with self.assertRaisesRegex(ValueError, "unsupported file type"):
            finder.motif_set_from_data(
                {
                    "schema": finder.MOTIF_SET_SCHEMA,
                    "references": [{"file": "C:/audio/theme.txt", "start": 0, "end": 2}],
                }
            )

    def test_motif_set_rejects_boolean_numeric_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "reference 1 start"):
            finder.motif_set_from_data(
                {
                    "schema": finder.MOTIF_SET_SCHEMA,
                    "references": [{"file": "C:/audio/theme.mp3", "start_seconds": True, "end": 2}],
                }
            )
        with self.assertRaisesRegex(ValueError, "threshold"):
            finder.motif_set_from_data(
                {
                    "schema": finder.MOTIF_SET_SCHEMA,
                    "references": [{"file": "C:/audio/theme.mp3", "start": 0, "end": 2}],
                    "settings": {"threshold": False},
                }
            )

    def test_motif_set_rejects_invalid_scan_setting_text_at_load_time(self) -> None:
        base = {
            "schema": finder.MOTIF_SET_SCHEMA,
            "references": [{"file": "C:/audio/theme.mp3", "start": 0, "end": 2}],
        }
        with self.assertRaisesRegex(ValueError, "threshold must be numeric"):
            finder.motif_set_from_data({**base, "settings": {"threshold": "loud"}})
        with self.assertRaisesRegex(ValueError, "threshold must be between 0 and 1"):
            finder.motif_set_from_data({**base, "settings": {"threshold": "1.5"}})
        with self.assertRaisesRegex(ValueError, "step must be positive"):
            finder.motif_set_from_data({**base, "settings": {"step": "0"}})
        with self.assertRaisesRegex(ValueError, "NMS must not be negative"):
            finder.motif_set_from_data({**base, "settings": {"nms": "-0.1"}})

    def test_saved_scan_settings_use_defaults_for_missing_fields(self) -> None:
        threshold, step, nms = finder.saved_scan_settings_as_numbers("0.75", None, "4")

        self.assertEqual((threshold, step, nms), (0.75, 0.5, 4.0))

    def test_save_motif_set_validates_before_writing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bad.json"
            motif_set = finder.MotifSet(
                references=(finder.Reference(Path("C:/audio/ref.mp3"), 0.0, 2.0, "ref"),),
                threshold="bad",
            )

            with self.assertRaisesRegex(ValueError, "threshold must be numeric"):
                finder.save_motif_set(path, motif_set)

            self.assertFalse(path.exists())

    def test_missing_reference_files_reports_missing_paths(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            present = root / "present.mp3"
            missing = root / "missing.mp3"
            present.write_bytes(b"placeholder")
            refs = [
                finder.Reference(present, 0.0, 2.0, "present"),
                finder.Reference(missing, 0.0, 2.0, "missing"),
            ]
            self.assertEqual(finder.missing_reference_files(refs), [missing])

    def test_validate_scan_settings_rejects_non_finite_and_bad_ranges(self) -> None:
        finder.validate_scan_settings(0.6, 0.5, 5.0)
        for values in ((math.nan, 0.5, 5.0), (1.1, 0.5, 5.0), (0.6, 0.0, 5.0), (0.6, 0.5, -1.0)):
            with self.subTest(values=values):
                with self.assertRaises(ValueError):
                    finder.validate_scan_settings(*values)

    def test_validate_reference_file_rejects_directories_and_unsupported_suffixes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_file = root / "notes.txt"
            text_file.write_text("not audio", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "reference file does not exist"):
                finder.validate_reference_file(root)
            with self.assertRaisesRegex(ValueError, "unsupported reference file type"):
                finder.validate_reference_file(text_file)

    def test_validate_media_file_uses_role_in_error_messages(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_file = root / "notes.txt"
            text_file.write_text("not audio", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "corpus file does not exist"):
                finder.validate_media_file(root / "missing.mp3", role="corpus")
            with self.assertRaisesRegex(ValueError, "unsupported corpus file type"):
                finder.validate_media_file(text_file, role="corpus")

    def test_validate_time_range_rejects_invalid_ranges(self) -> None:
        finder.validate_time_range(1.0, 3.0, label="sample", min_seconds=1.0, max_seconds=5.0)
        cases = [
            (math.nan, 2.0, "finite"),
            (-0.1, 2.0, "not be negative"),
            (2.0, 2.0, "after start"),
            (1.0, 1.5, "at least"),
            (1.0, 7.0, "at most"),
        ]
        for start, end, message in cases:
            with self.subTest(start=start, end=end):
                with self.assertRaisesRegex(ValueError, message):
                    finder.validate_time_range(
                        start,
                        end,
                        label="sample",
                        min_seconds=1.0,
                        max_seconds=5.0,
                    )

    def test_render_preview_wav_invokes_ffmpeg_for_selected_segment(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp3"
            source.write_bytes(b"fake")
            with mock.patch.object(finder.subprocess, "run") as run:
                def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                    Path(command[-1]).write_bytes(b"RIFF" + b"\0" * 128)
                    return SimpleNamespace(returncode=0, stderr=b"")

                run.side_effect = fake_run
                run.return_value = SimpleNamespace(returncode=0, stderr=b"")
                out = finder.render_preview_wav("ffmpeg.exe", source, 1.25, 4.75, root / "preview")
            self.assertEqual(out.suffix, ".wav")
            command = run.call_args.args[0]
            ss_indices = [index for index, item in enumerate(command) if item == "-ss"]
            self.assertEqual(len(ss_indices), 2)
            self.assertEqual(command[ss_indices[0] + 1], "0.000")
            self.assertEqual(command[ss_indices[1] + 1], "1.250")
            self.assertIn("-t", command)
            self.assertIn("3.500", command)
            self.assertIn("pcm_s16le", command)
            self.assertEqual(command[-1], str(out))

    def test_render_preview_wav_removes_empty_output(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp3"
            source.write_bytes(b"fake")
            with mock.patch.object(finder.subprocess, "run") as run:
                def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                    Path(command[-1]).write_bytes(b"RIFF")
                    return SimpleNamespace(returncode=0, stderr=b"")

                run.side_effect = fake_run
                with self.assertRaisesRegex(RuntimeError, "produced no playable WAV"):
                    finder.render_preview_wav("ffmpeg.exe", source, 1.0, 2.0, root / "preview")
            self.assertFalse(any((root / "preview").glob("*.wav")))

    def test_render_preview_wav_removes_partial_output_on_ffmpeg_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp3"
            source.write_bytes(b"fake")
            with mock.patch.object(finder.subprocess, "run") as run:
                def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                    Path(command[-1]).write_bytes(b"partial")
                    return SimpleNamespace(returncode=1, stderr=b"decode failed")

                run.side_effect = fake_run
                with self.assertRaisesRegex(RuntimeError, "decode failed"):
                    finder.render_preview_wav("ffmpeg.exe", source, 1.0, 2.0, root / "preview")
            self.assertFalse(any((root / "preview").glob("*.wav")))

    def test_cleanup_preview_files_keeps_current_and_newest_stale_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            files = [root / f"leitmotif_preview_{index}.wav" for index in range(5)]
            for index, path in enumerate(files):
                path.write_bytes(b"RIFF" + bytes([index]) * 64)
                os.utime(path, (1000 + index, 1000 + index))
            unrelated = root / "other.wav"
            unrelated.write_bytes(b"RIFF")

            finder.cleanup_preview_files(root, keep=files[0], max_files=3)

            self.assertEqual(
                {path.name for path in root.glob("*.wav")},
                {files[0].name, files[3].name, files[4].name, unrelated.name},
            )

    def test_build_preview_command_uses_fast_and_fine_seek(self) -> None:
        command = finder.build_preview_command(
            "ffmpeg.exe",
            Path("song.mp3"),
            80.99,
            86.99,
            Path("preview.wav"),
        )
        ss_indices = [index for index, item in enumerate(command) if item == "-ss"]
        self.assertEqual(command[ss_indices[0] + 1], "78.990")
        self.assertEqual(command[ss_indices[1] + 1], "2.000")
        self.assertEqual(command[command.index("-t") + 1], "6.000")

    def test_build_preview_command_applies_program_volume_filter(self) -> None:
        command = finder.build_preview_command(
            "ffmpeg.exe",
            Path("song.mp3"),
            1.0,
            4.0,
            Path("preview.wav"),
            volume_percent=45,
        )

        self.assertEqual(command[command.index("-filter:a") + 1], "volume=0.4500")

    def test_volume_helpers_clamp_slider_values(self) -> None:
        self.assertEqual(finder.clamp_volume_percent(-10), 0.0)
        self.assertEqual(finder.clamp_volume_percent(120), 100.0)
        self.assertEqual(finder.volume_percent_to_gain(25), 0.25)
        self.assertEqual(finder.volume_percent_to_mci(33.3), 333)
        self.assertEqual(finder.clamp_volume_percent(math.nan, default=80), 80.0)

    def test_render_preview_rejects_negative_start_before_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp3"
            source.write_bytes(b"fake")
            with mock.patch.object(finder.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "not be negative"):
                    finder.render_preview_wav("ffmpeg.exe", source, -1.0, 2.0, root / "preview")
            run.assert_not_called()

    def test_play_preview_wav_falls_back_to_system_player_when_winsound_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "preview.wav"
            wav_path.write_bytes(b"RIFF" + b"\0" * 128)
            fake_winsound = SimpleNamespace(
                SND_FILENAME=1,
                SND_ASYNC=2,
                PlaySound=mock.Mock(side_effect=RuntimeError("cannot play")),
            )
            with mock.patch.object(finder, "winsound", fake_winsound), mock.patch.object(finder, "open_path") as open_path:
                player = finder.play_preview_wav(wav_path)
            self.assertEqual(player, "system player")
            open_path.assert_called_once_with(wav_path)

    def test_play_preview_wav_falls_back_for_non_runtime_winsound_errors(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "preview.wav"
            wav_path.write_bytes(b"RIFF" + b"\0" * 128)
            fake_winsound = SimpleNamespace(
                SND_FILENAME=1,
                SND_ASYNC=2,
                PlaySound=mock.Mock(side_effect=OSError("device unavailable")),
            )
            with mock.patch.object(finder, "winsound", fake_winsound), mock.patch.object(finder, "open_path") as open_path:
                player = finder.play_preview_wav(wav_path)
            self.assertEqual(player, "system player")
            open_path.assert_called_once_with(wav_path)

    def test_preview_player_uses_mci_volume_pause_resume_and_stop(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "preview.wav"
            wav_path.write_bytes(b"RIFF" + b"\0" * 128)
            player = finder.PreviewPlayer()
            with (
                mock.patch.object(finder, "mci_available", return_value=True),
                mock.patch.object(finder, "mci_send", return_value="") as mci_send,
                mock.patch.object(finder, "play_preview_wav") as fallback,
            ):
                backend = player.play(wav_path, volume_percent=37)
                player.pause()
                player.resume()
                player.stop()

            commands = [call.args[0] for call in mci_send.call_args_list]
            self.assertEqual(backend, "mci")
            self.assertTrue(any(command.startswith("open ") for command in commands))
            self.assertTrue(any("volume to 370" in command for command in commands))
            self.assertTrue(any(command.startswith("pause ") for command in commands))
            self.assertTrue(any(command.startswith("play ") for command in commands))
            self.assertTrue(any(command.startswith("close ") for command in commands))
            fallback.assert_not_called()

    def test_sequence_segments_merge_overlapping_source_audio(self) -> None:
        hits = [
            {"file": "C:/audio/song.mp3", "start_seconds": 3.0, "end_seconds": 9.0, "score": 0.70},
            {"file": "C:/audio/song.mp3", "start_seconds": 5.0, "end_seconds": 11.0, "score": 0.90},
            {"file": "C:/audio/other.mp3", "start_seconds": 1.0, "end_seconds": 4.0, "score": 0.80},
        ]

        merged = finder.sequence_segments_from_hits(hits, minimize_overlap=True)
        repeated = finder.sequence_segments_from_hits(hits, minimize_overlap=False)

        self.assertEqual(len(merged), 2)
        self.assertEqual((merged[1].start, merged[1].end, merged[1].hit_count), (3.0, 11.0, 2))
        self.assertEqual(len(repeated), 3)

    def test_export_leitmotif_sequence_writes_mp3_and_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"fake")
            hits = [
                {"file": str(source), "start_seconds": 3.0, "end_seconds": 9.0, "score": 0.70},
                {"file": str(source), "start_seconds": 5.0, "end_seconds": 11.0, "score": 0.90},
            ]

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                Path(command[-1]).write_bytes(b"audio")
                return SimpleNamespace(returncode=0, stderr=b"")

            with mock.patch.object(finder.subprocess, "run", side_effect=fake_run) as run:
                sequence_path, manifest_path, count = finder.export_leitmotif_sequence(
                    "ffmpeg.exe",
                    root,
                    "export test",
                    hits,
                    minimize_overlap=True,
                )

            self.assertEqual(count, 1)
            self.assertTrue(sequence_path.exists())
            self.assertTrue(manifest_path.exists())
            self.assertEqual(run.call_count, 2)
            with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[0]["hit_count"], "2")
            self.assertEqual(rows[0]["source_start_seconds"], "3.0")
            self.assertEqual(rows[0]["source_end_seconds"], "11.0")

    def test_export_leitmotif_sequence_can_skip_inter_segment_silence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"fake")
            hits = [
                {"file": str(source), "start_seconds": 1.0, "end_seconds": 2.0, "score": 0.80},
                {"file": str(source), "start_seconds": 4.0, "end_seconds": 5.0, "score": 0.82},
            ]
            commands: list[list[str]] = []

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                commands.append(command)
                Path(command[-1]).write_bytes(b"audio")
                return SimpleNamespace(returncode=0, stderr=b"")

            with mock.patch.object(finder.subprocess, "run", side_effect=fake_run):
                _sequence_path, manifest_path, count = finder.export_leitmotif_sequence(
                    "ffmpeg.exe",
                    root,
                    "no silence",
                    hits,
                    minimize_overlap=True,
                    silence_seconds=0.0,
                )

            self.assertEqual(count, 2)
            self.assertFalse(any("anullsrc" in command for command in commands))
            with manifest_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows[1]["output_start_seconds"], "1.0")

    def test_export_leitmotif_sequence_rejects_invalid_silence(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"fake")
            hits = [{"file": str(source), "start_seconds": 1.0, "end_seconds": 2.0, "score": 0.80}]

            with mock.patch.object(finder.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "silence seconds"):
                    finder.export_leitmotif_sequence(
                        "ffmpeg.exe",
                        root,
                        "bad silence",
                        hits,
                        minimize_overlap=True,
                        silence_seconds=math.nan,
                    )

            run.assert_not_called()

    def test_ffmpeg_concat_line_escapes_single_quotes(self) -> None:
        line = finder.ffmpeg_concat_line(Path("C:/audio/it's here.mp3"))

        self.assertEqual(line, "file 'C:/audio/it'\\''s here.mp3'")

    def test_ffmpeg_concat_line_handles_apostrophe_paths_with_real_ffmpeg(self) -> None:
        ffmpeg = finder.find_ffmpeg()
        with tempfile.TemporaryDirectory(prefix="leitmotif_quote_it's_") as tmp:
            root = Path(tmp)
            source = root / "it's here.wav"
            with wave.open(str(source), "wb") as handle:
                handle.setnchannels(1)
                handle.setsampwidth(2)
                handle.setframerate(8000)
                frames = bytearray()
                for index in range(8000):
                    value = int(12000 * math.sin(2 * math.pi * 440 * index / 8000))
                    frames.extend(value.to_bytes(2, "little", signed=True))
                handle.writeframes(frames)
            concat_file = root / "concat.txt"
            concat_file.write_text(finder.ffmpeg_concat_line(source) + "\n", encoding="utf-8")
            output = root / "joined.wav"

            finder.run_ffmpeg_checked(
                [
                    ffmpeg,
                    "-nostdin",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-y",
                    "-f",
                    "concat",
                    "-safe",
                    "0",
                    "-i",
                    str(concat_file),
                    "-c",
                    "copy",
                    str(output),
                ],
                output=output,
                label="apostrophe concat regression",
            )

            self.assertGreater(output.stat().st_size, 44)

    def test_run_ffmpeg_checked_rejects_stale_output_not_rewritten(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "stale.mp3"
            output.write_bytes(b"old audio")
            result = SimpleNamespace(returncode=0, stderr=b"")

            with mock.patch.object(finder.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(RuntimeError, "produced no output"):
                    finder.run_ffmpeg_checked(["ffmpeg.exe", str(output)], output=output, label="stale")

            self.assertFalse(output.exists())

    def test_run_ffmpeg_checked_removes_partial_output_on_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "partial.mp3"

            def fake_run(_command: list[str], **_kwargs: object) -> SimpleNamespace:
                output.write_bytes(b"partial audio")
                return SimpleNamespace(returncode=1, stderr=b"encode failed")

            with mock.patch.object(finder.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "encode failed"):
                    finder.run_ffmpeg_checked(["ffmpeg.exe", str(output)], output=output, label="partial")

            self.assertFalse(output.exists())

    def test_export_leitmotif_sequence_uses_output_dir_for_temp_files(self) -> None:
        original_tempdir = tempfile.TemporaryDirectory
        calls: list[dict[str, object]] = []

        class RecordingTemporaryDirectory:
            def __init__(self, *args: object, **kwargs: object) -> None:
                calls.append(dict(kwargs))
                self._inner = original_tempdir(*args, **kwargs)

            def __enter__(self) -> str:
                return self._inner.__enter__()

            def __exit__(self, *args: object) -> object:
                return self._inner.__exit__(*args)

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"fake")
            hits = [{"file": str(source), "start_seconds": 0.0, "end_seconds": 1.0, "score": 0.80}]

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                Path(command[-1]).write_bytes(b"audio")
                return SimpleNamespace(returncode=0, stderr=b"")

            with (
                mock.patch.object(finder.tempfile, "TemporaryDirectory", RecordingTemporaryDirectory),
                mock.patch.object(finder.subprocess, "run", side_effect=fake_run),
            ):
                finder.export_leitmotif_sequence(
                    "ffmpeg.exe",
                    root,
                    "export test",
                    hits,
                    minimize_overlap=True,
                )

        self.assertEqual(calls[0]["dir"], root)

    def test_export_leitmotif_sequence_validates_sources_before_ffmpeg(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            missing = root / "missing.mp3"
            hits = [{"file": str(missing), "start_seconds": 0.0, "end_seconds": 1.0, "score": 0.80}]

            with mock.patch.object(finder.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "sequence source file does not exist"):
                    finder.export_leitmotif_sequence(
                        "ffmpeg.exe",
                        root,
                        "export test",
                        hits,
                        minimize_overlap=True,
                    )

            run.assert_not_called()

    def test_export_leitmotif_sequence_keeps_existing_mp3_on_concat_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "song.mp3"
            source.write_bytes(b"fake")
            existing = root / "export test leitmotif sequence.mp3"
            existing.write_bytes(b"old")
            hits = [{"file": str(source), "start_seconds": 0.0, "end_seconds": 1.0, "score": 0.80}]

            def fake_run(command: list[str], **_kwargs: object) -> SimpleNamespace:
                output = Path(command[-1])
                output.write_bytes(b"partial")
                if "-f" in command and command[command.index("-f") + 1] == "concat":
                    return SimpleNamespace(returncode=1, stderr=b"concat failed")
                return SimpleNamespace(returncode=0, stderr=b"")

            with mock.patch.object(finder.subprocess, "run", side_effect=fake_run):
                with self.assertRaisesRegex(RuntimeError, "concat failed"):
                    finder.export_leitmotif_sequence(
                        "ffmpeg.exe",
                        root,
                        "export test",
                        hits,
                        minimize_overlap=True,
                    )

            self.assertEqual(existing.read_bytes(), b"old")
            self.assertFalse((root / "export test leitmotif sequence.csv").exists())

    def test_decode_audio_rejects_non_positive_sample_rate(self) -> None:
        with self.assertRaisesRegex(ValueError, "sample rate must be positive"):
            finder.decode_audio(Path("song.mp3"), "ffmpeg.exe", 0)

    def test_decode_audio_rejects_malformed_float32_output(self) -> None:
        result = SimpleNamespace(returncode=0, stdout=b"abc", stderr=b"")
        with mock.patch.object(finder.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "malformed float32 audio"):
                finder.decode_audio(Path("song.mp3"), "ffmpeg.exe", 8000)

    def test_decode_audio_mutes_nonfinite_samples_before_normalizing(self) -> None:
        raw = np.array([math.nan, math.inf, -math.inf, 0.5], dtype="<f4").tobytes()
        result = SimpleNamespace(returncode=0, stdout=raw, stderr=b"")
        with mock.patch.object(finder.subprocess, "run", return_value=result):
            audio = finder.decode_audio(Path("song.mp3"), "ffmpeg.exe", 8000)

        np.testing.assert_allclose(audio, np.array([0.0, 0.0, 0.0, 1.0], dtype=np.float32))

    def test_make_pitch_map_rejects_invalid_or_unusable_parameters(self) -> None:
        for frame_size, sample_rate in ((0, 11025), (4096, 0), (8, 100)):
            with self.subTest(frame_size=frame_size, sample_rate=sample_rate):
                with self.assertRaises(ValueError):
                    finder.make_pitch_map(frame_size, sample_rate)

    def test_chroma_features_rejects_invalid_analysis_parameters(self) -> None:
        audio = np.ones(64, dtype=np.float32)
        valid_bins = np.array([1], dtype=np.int64)
        weights = np.ones((1, 12), dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "must be positive"):
            finder.chroma_features(audio, 0, 32, 8, valid_bins, weights)

    def test_chroma_features_rejects_invalid_pitch_inputs(self) -> None:
        audio = np.ones(64, dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "non-empty"):
            finder.chroma_features(audio, 8000, 32, 8, np.array([], dtype=np.int64), np.zeros((0, 12)))
        with self.assertRaisesRegex(ValueError, "integer"):
            finder.chroma_features(audio, 8000, 32, 8, np.array([1.2]), np.ones((1, 12)))
        with self.assertRaisesRegex(ValueError, "exceed"):
            finder.chroma_features(audio, 8000, 32, 8, np.array([99], dtype=np.int64), np.ones((1, 12)))
        with self.assertRaisesRegex(ValueError, "shape"):
            finder.chroma_features(audio, 8000, 32, 8, np.array([1], dtype=np.int64), np.ones((2, 12)))

    def test_chroma_features_sanitizes_nonfinite_audio(self) -> None:
        audio = np.sin(np.linspace(0, 4 * math.pi, 512, dtype=np.float32)).astype(np.float32)
        audio[10] = math.nan
        audio[20] = math.inf
        audio[30] = -math.inf
        valid_bins, weights = finder.make_pitch_map(64, 8000)

        features, times = finder.chroma_features(audio, 8000, 64, 16, valid_bins, weights)

        self.assertGreater(len(features), 0)
        self.assertEqual(len(features), len(times))
        self.assertTrue(np.all(np.isfinite(features)))

    def test_waveform_envelope_matches_chunk_min_max(self) -> None:
        view = object.__new__(finder.WaveformView)
        view.winfo_width = lambda: 900
        audio = np.linspace(-1.0, 1.0, 2701, dtype=np.float32)
        audio[5::17] *= -1.0
        envelope = view._build_envelope(audio)
        block = math.ceil(len(audio) / 900)
        expected = []
        for index in range(0, len(audio), block):
            chunk = audio[index : index + block]
            expected.append((float(np.min(chunk)), float(np.max(chunk))))
        self.assertEqual(envelope, expected)

    def test_build_template_groups_rejects_empty_reference_list(self) -> None:
        with self.assertRaisesRegex(ValueError, "add at least one reference sample"):
            finder.build_template_groups([], "ffmpeg.exe", transpose=False)

    def test_build_template_groups_rejects_invalid_reference_times_before_decode(self) -> None:
        ref = finder.Reference(Path("missing.wav"), -1.0, 2.0, "negative")
        with mock.patch.object(finder, "decode_audio") as decode_audio:
            with self.assertRaisesRegex(ValueError, "not be negative"):
                finder.build_template_groups([ref], "ffmpeg.exe", transpose=False)
        decode_audio.assert_not_called()

    def test_build_template_groups_rejects_missing_reference_file_before_decode(self) -> None:
        ref = finder.Reference(Path("missing.wav"), 0.0, 2.0, "missing")
        with mock.patch.object(finder, "decode_audio") as decode_audio:
            with self.assertRaisesRegex(ValueError, "reference file does not exist"):
                finder.build_template_groups([ref], "ffmpeg.exe", transpose=False)
        decode_audio.assert_not_called()

    def test_scan_file_rejects_empty_template_groups_before_decode(self) -> None:
        with mock.patch.object(finder, "decode_audio") as decode_audio:
            with self.assertRaisesRegex(ValueError, "add at least one reference template"):
                finder.scan_file(Path("song.mp3"), "ffmpeg.exe", [], 0.6, 0.5, 5.0)
        decode_audio.assert_not_called()

    def test_scan_file_rejects_unsupported_corpus_file_before_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "notes.txt"
            bad.write_text("not audio", encoding="utf-8")
            group = finder.TemplateGroup(
                duration_seconds=1.0,
                window_frames=3,
                templates=np.zeros((1, 36), dtype=np.float32),
                labels=("x",),
            )
            with mock.patch.object(finder, "decode_audio") as decode_audio:
                with self.assertRaisesRegex(ValueError, "unsupported corpus file type"):
                    finder.scan_file(bad, "ffmpeg.exe", [group], 0.6, 0.5, 5.0)
            decode_audio.assert_not_called()

    def test_scan_file_rejects_malformed_template_group_before_scoring(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "song.wav"
            wav_path.write_bytes(b"fake")
            bad_group = finder.TemplateGroup(
                duration_seconds=1.0,
                window_frames=3,
                templates=np.zeros((1, 12), dtype=np.float32),
                labels=("x",),
            )
            fake_features = np.ones((8, 12), dtype=np.float32)
            fake_times = np.arange(8, dtype=np.float32)
            with (
                mock.patch.object(finder, "decode_audio", return_value=np.ones(8192, dtype=np.float32)),
                mock.patch.object(finder, "chroma_features", return_value=(fake_features, fake_times)),
            ):
                with self.assertRaisesRegex(ValueError, "template width mismatch"):
                    finder.scan_file(wav_path, "ffmpeg.exe", [bad_group], 0.6, 0.5, 5.0)

    def test_atomic_writers_replace_outputs_and_remove_temps(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            text_path = root / "out.md"
            csv_path = root / "out.csv"
            finder.atomic_write_text(text_path, "first", encoding="utf-8")
            finder.atomic_write_text(text_path, "second", encoding="utf-8")
            finder.atomic_write_csv(csv_path, ["a", "b"], [{"a": 1, "b": 2, "extra": 3}])
            self.assertEqual(text_path.read_text(encoding="utf-8"), "second")
            with csv_path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
            self.assertEqual(rows, [{"a": "1", "b": "2"}])
            self.assertFalse(list(root.glob(".*.tmp")))

    def test_build_template_groups_caches_same_reference_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "motif.wav"
            write_test_wav(wav_path)
            refs = [
                finder.Reference(wav_path, 2.0, 6.0, "first"),
                finder.Reference(wav_path, 8.0, 12.0, "second"),
            ]
            with mock.patch.object(finder, "decode_audio", wraps=finder.decode_audio) as wrapped:
                groups = finder.build_template_groups(refs, finder.find_ffmpeg(), transpose=False)
            self.assertEqual(wrapped.call_count, 1)
            self.assertEqual(sum(group.templates.shape[0] for group in groups), 2)

    def test_build_template_groups_rejects_reference_outside_audio(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "motif.wav"
            write_test_wav(wav_path)
            ref = finder.Reference(wav_path, 20.0, 24.0, "outside")
            with self.assertRaisesRegex(ValueError, "outside audio duration"):
                finder.build_template_groups([ref], finder.find_ffmpeg(), transpose=False)

    def test_window_embeddings_match_segment_embedding(self) -> None:
        rng = np.random.default_rng(42)
        features = rng.normal(size=(24, 12)).astype(np.float32)
        features /= np.linalg.norm(features, axis=1, keepdims=True) + 1e-6
        matrix = finder.window_embeddings(features, first_start=2, count=5, step_frames=3, window_frames=4)
        expected = np.vstack(
            [finder.segment_embedding(features, start, 4) for start in (2, 5, 8, 11, 14)]
        )
        np.testing.assert_allclose(matrix, expected, atol=1e-6)

    def test_window_embeddings_reject_out_of_range_stride(self) -> None:
        features = np.ones((8, 12), dtype=np.float32)
        with self.assertRaises(ValueError):
            finder.window_embeddings(features, first_start=6, count=2, step_frames=1, window_frames=4)

    def test_window_embeddings_rejects_bad_params_even_for_zero_count(self) -> None:
        features = np.ones((8, 12), dtype=np.float32)
        for step_frames, window_frames in ((0, 1), (1, 0), (-1, 1), (1, -1)):
            with self.subTest(step_frames=step_frames, window_frames=window_frames):
                with self.assertRaisesRegex(ValueError, "positive"):
                    finder.window_embeddings(features, first_start=0, count=0, step_frames=step_frames, window_frames=window_frames)

    def test_segment_embedding_rejects_invalid_range(self) -> None:
        features = np.ones((8, 12), dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "range exceeds"):
            finder.segment_embedding(features, start=7, frames=2)

    def test_window_embeddings_rejects_non_2d_features(self) -> None:
        features = np.ones(8, dtype=np.float32)
        with self.assertRaisesRegex(ValueError, "2D"):
            finder.window_embeddings(features, first_start=0, count=0, step_frames=1, window_frames=1)

    def test_synthetic_reference_finds_repeated_motif(self) -> None:
        ffmpeg = finder.find_ffmpeg()
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "motif.wav"
            write_test_wav(wav_path)
            ref = finder.Reference(wav_path, 2.0, 6.0, "synthetic")
            groups = finder.build_template_groups([ref], ffmpeg, transpose=False)
            hits, duration = finder.scan_file(
                wav_path,
                ffmpeg,
                groups,
                threshold=0.60,
                step_seconds=0.25,
                nms_seconds=2.0,
            )
            self.assertGreater(duration, 11.9)
            starts = [float(hit["start_seconds"]) for hit in hits]
            self.assertTrue(any(1.5 <= start <= 2.5 for start in starts), starts)
            self.assertTrue(any(7.5 <= start <= 8.5 for start in starts), starts)

    def test_self_test_rejects_missing_reference_before_ffmpeg_decode(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            args = SimpleNamespace(
                reference=str(root / "missing.wav"),
                start="00:00.00",
                end="00:02.00",
                corpus=str(root),
                output=str(root / "out"),
                threshold=0.6,
                step=0.5,
                nms=5.0,
            )
        with self.assertRaisesRegex(RuntimeError, "reference file does not exist"):
            finder.run_self_test(args)

    def test_self_test_rejects_bad_time_range_before_ffmpeg_lookup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            ref = root / "ref.wav"
            ref.write_bytes(b"placeholder")
            corpus = root / "corpus"
            corpus.mkdir()
            (corpus / "song.wav").write_bytes(b"placeholder")
            args = SimpleNamespace(
                reference=str(ref),
                start="00:06.00",
                end="00:02.00",
                corpus=str(corpus),
                output=str(root / "out"),
                threshold=0.6,
                step=0.5,
                nms=5.0,
            )

            with mock.patch.object(finder, "find_ffmpeg") as find_ffmpeg:
                with self.assertRaisesRegex(ValueError, "self-test reference end must be after start"):
                    finder.run_self_test(args)

            find_ffmpeg.assert_not_called()

    def test_scan_worker_records_sequence_export_failure_without_losing_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = object.__new__(finder.LeitmotifFinderApp)
            app.ffmpeg = "ffmpeg.exe"
            app.log_queue = finder.queue.Queue()
            corpus_file = root / "song.mp3"
            captured: dict[str, object] = {}

            def fake_write_outputs(
                output_dir: Path,
                title: str,
                references: list[finder.Reference],
                corpus_files: list[Path],
                hits: list[dict[str, object]],
                errors: list[dict[str, object]],
                settings: dict[str, object],
            ) -> tuple[Path, Path]:
                captured["errors"] = errors
                captured["settings"] = settings
                captured["hits"] = hits
                return output_dir / f"{title}.md", output_dir / f"{title}.csv"

            group = finder.TemplateGroup(
                duration_seconds=2.0,
                window_frames=2,
                templates=np.ones((1, 12), dtype=np.float32),
                labels=("ref",),
            )
            hit = {
                "file": str(corpus_file),
                "name": corpus_file.name,
                "start_seconds": 0.0,
                "end_seconds": 2.0,
                "score": 0.8,
                "duration_seconds": 10.0,
            }
            patches = [
                mock.patch.object(finder, "collect_corpus", return_value=[corpus_file]),
                mock.patch.object(finder, "build_template_groups", return_value=[group]),
                mock.patch.object(finder, "scan_file", return_value=([hit], 10.0)),
                mock.patch.object(finder, "export_leitmotif_sequence", side_effect=RuntimeError("render failed")),
                mock.patch.object(finder, "write_outputs", side_effect=fake_write_outputs),
            ]
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                app._scan_worker(
                    0.6,
                    0.5,
                    5.0,
                    root,
                    "report",
                    [finder.Reference(corpus_file, 0.0, 2.0, "ref")],
                    [root],
                    True,
                    True,
                    True,
                    True,
                )

            events: list[object] = []
            while not app.log_queue.empty():
                events.append(app.log_queue.get())
            self.assertTrue(any(isinstance(event, tuple) and event[0] == "scan_outputs" for event in events))
            self.assertFalse(any(str(event).startswith("Fatal:") for event in events))
            errors = captured["errors"]
            settings = captured["settings"]
            self.assertIsInstance(errors, list)
            self.assertIn("sequence export failed", errors[-1]["error"])
            self.assertIn("sequence export failed", settings["sequence_output"])

    def test_log_queue_restores_run_button_without_worker_tk_call(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.after_calls: list[tuple[int, object]] = []

            def after(self, delay: int, callback: object) -> None:
                self.after_calls.append((delay, callback))

        class FakeButton:
            def __init__(self) -> None:
                self.config: dict[str, str] = {}

            def configure(self, **kwargs: str) -> None:
                self.config.update(kwargs)

        app = object.__new__(finder.LeitmotifFinderApp)
        app.root = FakeRoot()
        app.run_button = FakeButton()
        app.log_queue = finder.queue.Queue()
        app.closing = False
        logs: list[str] = []
        app._log = logs.append

        app.log_queue.put("hello")
        app.log_queue.put(("scan_done", None))
        app._drain_log_queue()

        self.assertEqual(logs, ["hello"])
        self.assertEqual(app.run_button.config["state"], "normal")
        self.assertEqual(len(app.root.after_calls), 1)

    def test_log_queue_records_scan_outputs_and_enables_buttons(self) -> None:
        class FakeRoot:
            def __init__(self) -> None:
                self.after_calls: list[tuple[int, object]] = []

            def after(self, delay: int, callback: object) -> None:
                self.after_calls.append((delay, callback))

        class FakeButton:
            def __init__(self) -> None:
                self.config: dict[str, str] = {"state": "disabled"}

            def configure(self, **kwargs: str) -> None:
                self.config.update(kwargs)

        app = object.__new__(finder.LeitmotifFinderApp)
        app.root = FakeRoot()
        app.run_button = FakeButton()
        app.output_buttons = [FakeButton(), FakeButton(), FakeButton(), FakeButton()]
        app.open_sequence_button = app.output_buttons[-1]
        app.log_queue = finder.queue.Queue()
        app.closing = False
        app._log = lambda _message: None

        md_path = Path("out.md")
        csv_path = Path("out.csv")
        sequence_path = Path("sequence.mp3")
        out_dir = Path("out")
        app.log_queue.put(("scan_outputs", md_path, csv_path, out_dir, sequence_path))
        app._drain_log_queue()

        self.assertEqual(app.last_md_path, md_path)
        self.assertEqual(app.last_csv_path, csv_path)
        self.assertEqual(app.last_sequence_path, sequence_path)
        self.assertEqual(app.last_output_dir, out_dir)
        self.assertTrue(all(button.config["state"] == "normal" for button in app.output_buttons))

    def test_log_queue_does_not_reschedule_when_closing(self) -> None:
        class FakeRoot:
            def after(self, _delay: int, _callback: object) -> None:
                raise AssertionError("after should not be called while closing")

        app = object.__new__(finder.LeitmotifFinderApp)
        app.root = FakeRoot()
        app.log_queue = finder.queue.Queue()
        app.closing = True
        app._drain_log_queue()

    def test_log_trims_old_lines(self) -> None:
        class FakeText:
            def __init__(self) -> None:
                self.lines: list[str] = []
                self.seen = False

            def insert(self, _index: str, text: str) -> None:
                self.lines.extend(text.splitlines())

            def index(self, _index: str) -> str:
                return f"{len(self.lines)}.0"

            def delete(self, _start: str, end: str) -> None:
                end_line = int(end.split(".", 1)[0])
                del self.lines[: max(0, end_line - 1)]

            def see(self, _index: str) -> None:
                self.seen = True

        app = object.__new__(finder.LeitmotifFinderApp)
        app.log_text = FakeText()
        old_limit = finder.MAX_LOG_LINES
        finder.MAX_LOG_LINES = 3
        try:
            for index in range(5):
                app._log(f"line {index}")
        finally:
            finder.MAX_LOG_LINES = old_limit

        self.assertEqual(app.log_text.lines, ["line 2", "line 3", "line 4"])
        self.assertTrue(app.log_text.seen)


if __name__ == "__main__":
    unittest.main()
