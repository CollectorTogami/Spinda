# Input Tapes Versus Movie Files

## Status Bucket

- Current status: Source-of-truth note for naming and scope differences between
  this fork's input tapes and emulator movie files.
- Last verified date: 2026-04-30.
- Proven artifacts:
  - `src/platform/qt/InputTapeView.cpp`
  - `doc/python-examples/input_tape.py`
  - `src/script/engines/input_tape.lua`
  - `src/platform/python/cinema/movie.py`
- Known gaps: This doc defines this fork's terminology. It is not a full
  survey of every emulator movie format.
- Next action: Update this note if the input tape format gains movie-like
  anchors such as ROM identity, savestate data, reset events, or rerecord
  branches.

## Evidence Split

### Proven

- `mgba-input-tape-v1` stores GBA button masks and frame counts.
- Input tapes do not store ROMs, saves, savestates, RTC state, emulator config,
  reset/power events, or rerecord history.
- Native Qt, Python, and Lua helpers share the same input tape model.
- Python cinema/movie tracing is a separate test/regression path.

### Observed Once

- Spinda route work used input tapes as short bridge segments after a known
  save/savestate/seed anchor was prepared.

### Inferred

- Calling input tapes "movies" can create false expectations about replay
  portability, full-session identity, and archival proof.

### Planned

- Keep the input tape format small unless a future workflow needs a true movie
  container.

### Obsolete

- Do not describe `mgba-input-tape-v1` as a full emulator movie file.

## Short Version

Input tape:

- small route helper
- button masks per emulated frame
- no game-state anchor
- caller must load correct ROM/save/state first
- used for deterministic local route segments

Movie file:

- full replay/session container in common emulator usage
- often includes or references sync metadata, rerecord state, reset/power
  events, emulator identity, core settings, or savestate anchors
- meant for broader playback or archival proof

## Input Tape Contract

An input tape answers one narrow question:

```text
Which GBA buttons should be pressed on each frame after the correct state is
already loaded?
```

It does not answer:

```text
Which game, save, savestate, settings, reset path, or emulator version produced
that state?
```

That anchor must come from surrounding metadata, runbook notes, test fixtures,
or operator setup.

## Movie File Boundary

In TAS and emulator terminology, "movie" usually implies a more complete replay
artifact than this fork's input tape. A movie may carry enough metadata to
prove or reproduce a full session from power-on or from a well-defined
savestate anchor.

This fork's input tapes deliberately do not do that. They are closer to
route-input snippets.

## Why The Distinction Matters

For Phase 1 and Phase 2 Spinda work:

- input tapes can bridge repeatable dialog/title/menu segments
- seed/save/state setup still needs separate proof
- replay success depends on correct anchor state
- matching a tape alone does not prove RNG equivalence

For final documentation:

- use "input tape" for `mgba-input-tape-v1`
- use "movie file" only for actual movie/cinema/replay formats
- do not imply input tapes are portable full-session proof

## Related Files

- input_tapes.md
- CUSTOM_FEATURES_MODULE.md
- [MGBA_CUSTOM_CHANGES_AND_FEATURES.md](MGBA_CUSTOM_CHANGES_AND_FEATURES.md)
- `src/platform/python/cinema/movie.py`
