# General Leitmotif Finder

Small desktop tool for finding repeated musical motifs across a folder of audio
or video files.

Pick one or more reference time ranges, scan a corpus, and export the matches as
Markdown, CSV, and JSON. The matcher uses chroma features, so it looks for
melodic shape rather than identical waveforms. It can also check all 12 pitch
shifts when a motif may return in another key.

Reference sets can be saved as motif data JSON files and loaded again later.
This keeps source files, timestamps, labels, corpus paths, and scan settings in
one reusable preset.

## What It Is For

- Finding recurring themes across a soundtrack.
- Building timestamp maps for editing, research, or annotation.
- Auditioning candidate motif uses with built-in preview buttons.
- Producing CSV/Markdown reports that can be reviewed outside the app.
- Exporting a sequential MP3 of found motif samples for quick listening.

This is not an audio fingerprinting or copyright-identification tool. Treat the
scores as candidates and verify important hits by ear.

## Requirements

- Windows, macOS, or Linux with Python 3.11+.
- `numpy`.
- `ffmpeg` available in one of these places:
  1. `FFMPEG` environment variable, as either the executable path or the folder
     containing it. One matching pair of wrapping quotes is allowed.
  2. `ffmpeg.exe` beside the script or packaged EXE
  3. `ffmpeg.exe` one folder above the script, for source folders inside a
     bundled app folder
  4. `C:\Program Files\ShareX\ffmpeg.exe`
  5. `C:\msys64\mingw64\bin\ffmpeg.exe`
  6. system `PATH`

Install the Python dependency:

```bash
python -m pip install numpy
```

## Run From Source

```bash
python general_leitmotif_finder_gui.py
```

If you use the packaged build, run:

```text
dist\GeneralLeitmotifFinder.exe
```

`dist\GeneralLeitmotifFinderConsole.exe` is the same app with a console attached
for command-line self-tests and debugging.

## Basic Workflow

1. Pick a reference audio or video file.
2. Drag the waveform markers, or type start/end timestamps.
3. Click `Preview boxes` to hear the current range.
4. Click `Add reference`.
5. Repeat for each motif clip, then click `Save motif data...` if you want to
   reuse the set later.
6. Choose a full-song folder or individual corpus files.
7. Choose an output folder and report title.
8. Enable `Export sequence` if you want the detected samples concatenated into
   one MP3.
9. Click `Run scan`.
10. Open the Markdown, CSV, sequence MP3, or output folder when the scan
    finishes.

To resume a previous setup, click `Load motif data...`. The app reloads the
reference list and restores any saved corpus paths and scan settings.

## Controls

- `Threshold`: stricter values return fewer, cleaner hits. Start at `0.60`; try
  `0.75` or `0.80` for high-confidence editorial candidates.
- `Step`: scan stride in seconds. Lower values catch starts more precisely but
  scan more slowly.
- `NMS`: duplicate suppression window in seconds. Larger values collapse nearby
  repeats more aggressively.
- `Allow transposition`: checks all 12 pitch classes so key changes can match.
- `Recursive folders`: scans supported files inside subfolders.
- `Preview volume`: changes the app's sample-preview volume without changing
  the system volume. On Windows, the internal preview player also supports
  pause and resume.
- `Export sequence`: writes a single MP3 containing the found motif samples.
- `Minimize overlap`: merges overlapping hit windows from the same source file
  before building the MP3, so the same audio is not replayed twice.

## Output Files

Each run writes:

- `<title>.md`: human-readable report.
- `<title>.csv`: spreadsheet-friendly hit table.
- `<title>.json`: full structured data, including settings and errors.

When `Export sequence` is enabled, the app also writes:

- `<title> leitmotif sequence.mp3`: all exported hit samples in order.
- `<title> leitmotif sequence.csv`: the source and output timestamps for each
  rendered sequence segment.

If sequence export fails, the normal Markdown, CSV, and JSON reports are still
written and the export error is listed in the report.
Sequence export quotes concat-list paths with FFmpeg's apostrophe-safe form, so
files inside folders such as `Composer's Cuts` can still be rendered.
The exporter only renders inter-segment silence when there is more than one
clip and the configured pause is greater than zero.
Temporary FFmpeg targets are cleared before each render step, so stale files do
not get mistaken for successful output. Failed render steps also remove partial
targets before reporting the error.

Motif data presets are separate JSON files. They use the
`general-leitmotif-finder-motif-set-v1` schema and are meant for reloading
reference clips before a scan, not for storing scan results.
Saved scan settings are validated when the preset loads, so an invalid
threshold, step, or NMS value is reported before a scan starts.
The same validation is applied when writing a preset, which keeps broken motif
data files from being created by scripts or external callers.

Hit strengths:

- `anchor`: score >= `0.90`; strong enough for a foreground cue.
- `strong`: score >= `0.80`; good for transitions or obvious recurrence.
- `clear`: score >= `0.70`; useful under narration or notes.
- `echo`: score >= threshold; use as texture and verify by ear.

Report timestamps are rebuilt from sanitized numeric seconds before Markdown,
CSV, or JSON is written. This keeps imported or externally generated hit rows
from carrying stale display-time text into the final reports.

## Command-Line Self-Test

Use the console build or Python script:

```text
python general_leitmotif_finder_gui.py --self-test ^
  --reference "C:\music\song.mp3" ^
  --start 00:12.00 ^
  --end 00:18.00 ^
  --corpus "C:\music" ^
  --output "C:\music\leitmotif_results" ^
  --threshold 0.70 ^
  --step 0.50 ^
  --nms 5.00
```

The self-test uses the same scanner as the GUI and exits nonzero if any corpus
file errors. It validates the reference timestamps before looking up FFmpeg, so
bad command-line ranges are reported directly.

## How Matching Works

The scanner:

1. Decodes media through FFmpeg.
2. Converts audio into chroma features, one row per analysis frame.
3. Builds normalized vectors from each reference segment.
4. Optionally rotates the vectors through all 12 pitch classes.
5. Slides matching-length windows across each corpus file.
6. Scores cosine similarity and removes overlapping duplicate hits.

This favors melodic contour and harmony over exact timbre, mix, or mastering.
Decoded FFmpeg PCM is read as little-endian float32 and non-finite samples are
muted before peak normalization.

## Tests

From the repository root:

```bash
python -m unittest tools.spinda.general_leitmotif_finder.tests.test_general_leitmotif_finder
```

The tests cover timestamp parsing, preview rendering commands, audio feature
validation, duplicate suppression, output writing, sequence export edge cases,
GUI queue behavior, and a small synthetic motif scan.

## Publishing Notes

The source release should include:

- `general_leitmotif_finder_gui.py`
- `README.md`
- `GeneralLeitmotifFinder.spec`
- `GeneralLeitmotifFinderConsole.spec`
- `tests/test_general_leitmotif_finder.py`

Do not publish local corpora, generated reports, temporary previews, build
folders, packaged EXEs, or private project notes with the source tree.
