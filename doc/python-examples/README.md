# mGBA Python Examples

## Status Bucket

- Current status: Active example index for host-side and visible-Qt Python workflows.
- Last verified date: 2026-04-26.
- Proven artifacts: example scripts in this directory and pytest coverage under [src/platform/python/tests/examples](../../src/platform/python/tests/examples).
- Known gaps: Some examples require local ROM/save artifacts and cannot be proven by docs alone.
- Next action: Update this index whenever examples are added, removed, renamed, or their required artifacts change.
- Evidence model: Claims must be labeled as `Proven`, `Observed once`, `Inferred`, `Planned`, or `Obsolete`; see `DOCUMENTATION_EVIDENCE_POLICY.md`.

These examples are written for the Python-enabled mGBA build in this workspace.

## Related Docs

- `<repo-root>\AGENTS.md`
- `<repo-root>\markdown-files\index _markdown.md`
- `<repo-root>\markdown-files\PYTHON_SCRIPTING_CHEATSHEET.md`
- [<repo-root>\markdown-files\python_lua_scrips.md](../../docs/python_lua_scrips.md)
- [frlg-spinda/README.md](frlg-spinda/README.md)

Run them with the dedicated venv:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\doc\python-examples\basic_core_demo.py C:\path\to\game.gba
```

Use that MinGW-backed workspace interpreter for the maintained local bindings.
The in-tree `mgba._pylib` build is not meant to be imported from stock
`C:\Python312\python.exe`.

## Host-side examples

- `basic_core_demo.py`: [basic_core_demo.py](basic_core_demo.py), load a ROM, reset the core, print metadata, and tap a key
- `button_cycle_state_demo.py`: [button_cycle_state_demo.py](button_cycle_state_demo.py), cycle GBA buttons, save `state` and `state2` beside the ROM, then load both back
- `input_tape.py`: [input_tape.py](input_tape.py), record and replay the shared `mgba-input-tape-v1` route-tape format from Python, including visible Qt keyboard-mapped and Virtual Pad capture, Lua-parity helper aliases, stale-key clearing, and run-length compression for long routes
- `memory_demo.py`: [memory_demo.py](memory_demo.py), read, write, and search emulated memory
- `registers_demo.py`: [registers_demo.py](registers_demo.py), inspect CPU registers on GBA or GB
- `screenshot_demo.py`: [screenshot_demo.py](screenshot_demo.py), capture a PNG frame
- `audio_demo.py`: [audio_demo.py](audio_demo.py), collect stereo audio samples and optionally write raw PCM
- `save_state_demo.py`: [save_state_demo.py](save_state_demo.py), save raw state bytes, advance, then restore
- `logging_demo.py`: [logging_demo.py](logging_demo.py), install a custom Python logger for mGBA messages
- `vfs_demo.py`: [vfs_demo.py](vfs_demo.py), use `mgba.vfs` and load a ROM through a `VFile`
- `thread_demo.py`: [thread_demo.py](thread_demo.py), run the core in `mgba.thread.Thread` and inspect it safely
- `gba_sio_demo.py`: [gba_sio_demo.py](gba_sio_demo.py), attach a custom GBA SIO driver
- `gb_sio_demo.py`: [gb_sio_demo.py](gb_sio_demo.py), attach a custom GB link driver
- `frlg-seed-bruteforce\frlg_seed_bruteforce.py`: [frlg-seed-bruteforce/frlg_seed_bruteforce.py](frlg-seed-bruteforce/frlg_seed_bruteforce.py), boot `lg.gba`, create earlier `seed####titlebase.sav` plus untouched `seed####base.sav` and rolling `seed####test.sav`, widen search by varying neutral frames before first title `A`, advance rolling checkpoint one neutral frame per searched delay, rebuild it from untouched baseline only if it drifts, save `seed####done.sav` on match, then pause visible Qt GUI and show dark-mode success warning when run through Qt path in this workspace
- `frlg-seed-bruteforce\Seed-Bruteforcer.py`: [frlg-seed-bruteforce/Seed-Bruteforcer.py](frlg-seed-bruteforce/Seed-Bruteforcer.py), maintained title-seed workflow; it now defaults to the phase-two `0xCD39` target and scans `title_skip_start_delay=0..31`, with the verified hit stored under `<repo-root>\live-lanes\live-cd39-lane21`; it reuses `1 from egg.sav`, records and reuses the fixed pre-press input tapes, keeps the hot loop on the rolling no-input checkpoint, treats stopped Timer 1 as the seed source-of-truth, records `0x02020000` only as a secondary witness, saves replay candidate data only for the winning branch, exports read-only replay/locked-baseline artifacts, and can run from the dedicated venv or directly through the visible Qt scripting path
- `frlg-seed-bruteforce\Seed-Replicator.py`: [frlg-seed-bruteforce/Seed-Replicator.py](frlg-seed-bruteforce/Seed-Replicator.py), maintained metadata-driven replay companion for the first-half workflow; it replays the one known-good title hit from the read-only checkpoint and shared canonical save, with the final title button delivered as the same one-frame pulse used by the brute forcer
- `frlg-seed-bruteforce\Seed-Replicator-Pick-Save.py`: [frlg-seed-bruteforce/Seed-Replicator-Pick-Save.py](frlg-seed-bruteforce/Seed-Replicator-Pick-Save.py), Windows save-picker wrapper for the locked first-half replay; it asks for a `.sav` with the native Explorer dialog and then runs the normal metadata-driven replay using that save temporarily

## Embedded example

- `embedded_debugger_script.py`: [embedded_debugger_script.py](embedded_debugger_script.py), example meant for mGBA's embedded Python debugger path, not for bare `python script.py`
- `frlg-seed-bruteforce\frlg_seed_bruteforce_embedded.py`: [frlg-seed-bruteforce/frlg_seed_bruteforce_embedded.py](frlg-seed-bruteforce/frlg_seed_bruteforce_embedded.py), legacy embedded debugger example kept as source/reference code; the maintained visible-window workflow in this workspace is the Qt GUI path instead

## Roadmap scaffolds

- [frlg-spinda/frlg_spinda_first_half_lane.py](frlg-spinda/frlg_spinda_first_half_lane.py): first-pass phase-1 lane exporter for the Spinda roadmap; it verifies the lower half from daycare RAM, walks to the daycare man, saves in-game, exports `0x####.sav` named after the actual live daycare lower half, and records lane metadata
- [frlg-spinda/frlg_spinda_first_half_batch.py](frlg-spinda/frlg_spinda_first_half_batch.py): phase-1 batch runner that defaults to the premade `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0` post-seed savestate, filters `firsthalf.csv` to that seed, keeps the loaded-state anchor's organic `gRngValue`, replays the two input tapes, checks `gRngValue` at `t-18`, searches nearby hit delays from a pre-hit checkpoint with raw/Qt-scratch/file-backed fallback, reuses pre-split hit-delay tape variants across the whole run, resolves bounded LCRNG/LCRNG(R) drift around CSV `t-0`, converts raw CSV halves to FR/LG's live daycare lower-half formula, collapses the wraparound raw-half collisions onto their real live results, verifies daycare RAM, caches invariant manifest hashes, and exports saves under `<repo-root>\1sthalves` using the live daycare half as the filename; explicit `--seed-mode csv-bruteforce` is still available for full CSV-driven seed searches
- [frlg-spinda/frlg_spinda_lane_workspace.py](frlg-spinda/frlg_spinda_lane_workspace.py): create or inspect canonical lane manifest/save/state/block paths without running the emulator
- [frlg-spinda/frlg_spinda_second_half_lane.py](frlg-spinda/frlg_spinda_second_half_lane.py): phase-2 sweep scaffold that loads one lane save/work state, replays upper-half target routes, and fills the raw block file
- [frlg-spinda/spinda_frlg_common.py](frlg-spinda/spinda_frlg_common.py): shared FR/LG helper layer for named memory reads, `.sav` export, PRNG-state route checkpoints, and manifest I/O
- [frlg-spinda/spinda_frlg_archive.py](frlg-spinda/spinda_frlg_archive.py): fixed-width lane-block, bitmap, and global-manifest helpers for the storage side of the roadmap
- [frlg-spinda/frlg_spinda_corpus_manifest.py](frlg-spinda/frlg_spinda_corpus_manifest.py): create or update the top-level global resume manifest for the whole corpus
- [frlg-spinda/frlg_spinda_recipe_lint.py](frlg-spinda/frlg_spinda_recipe_lint.py): lint first-half and second-half recipe JSON files before a long run so duplicate targets, missing routes, and obvious PID mistakes are caught early
- [frlg-spinda/frlg_spinda_workspace_audit.py](frlg-spinda/frlg_spinda_workspace_audit.py): audit an existing workspace offline by checking save/state hashes, block/bitmap readability, and manifest/block progress consistency
- [frlg-spinda/frlg_spinda_export.py](frlg-spinda/frlg_spinda_export.py): export one stored record as `.pk3`, one lane as loose `.pk3` files, one full lane ZIP, one upper-half range ZIP, or many lanes as a nested ZIP; single-record export reads only the needed 80-byte slice
- [frlg-spinda/first_half_recipe_template.json](frlg-spinda/first_half_recipe_template.json): template input file for the first-half lane exporter
- [frlg-spinda/second_half_recipe_template.json](frlg-spinda/second_half_recipe_template.json): template input file for the second-half sweep scaffold
- [frlg-spinda/SCRIPT_DOCUMENTATION.md](frlg-spinda/SCRIPT_DOCUMENTATION.md): detailed documentation for the roadmap Python scripts, file formats, manifests, and recipe templates
- [frlg-tsv-save-bank/Build-FRLG-TSV-Save-Bank.py](frlg-tsv-save-bank/Build-FRLG-TSV-Save-Bank.py): FR/LG-only save-bank builder for one save per Trainer Shiny Value; dry-plan mode writes the wait plan/status JSON without driving mGBA, and live mode branches from a pre-SID Qt scratch checkpoint, commits SID, verifies TSV, replays a post-SID input tape, and exports decimal `TSV-xxxx-sid-xxxxx.sav` files under `<repo-root>\TSVs`

These roadmap scripts are not included in the generic smoke-test host example set because they expect user-gived FR/LG route recipes, save files, and savestates.

## Notes

- Stock upstream mGBA 0.10.5 Qt scripting is Lua-only.
- Custom addition in this workspace: the Qt GUI build can load `.py` files through `Tools > Scripting...` and `mGBA.exe --script FILE`.
- Custom addition in this workspace: the emulator-facing Python examples can now be loaded from `Tools > Scripting...` at runtime and then load the ROM/save/state they need themselves, instead of only working as startup scripts.
- Custom fix in this workspace: Python script-load callbacks borrow the C/C++ script file handle instead of closing it from Python, which keeps repeated Qt startup-script runs from crashing during file teardown.
- Custom addition in this workspace: visible-Qt scripts can batch fixed waits with `core.run_frames(count)` / `mgba.qt.run_frames(count)` for better performance during long automation loops.
- Custom addition in this workspace: visible-Qt scripts can also use `run_frames_with_keys(...)` and `pulse_keys(...)` when a fixed button mask is held for a fixed wait.
- Custom addition in this workspace: Lua/core scripting now exposes `runFramesWithKeys(...)` and `pulseKeys(...)` for the same fixed-key waits.
- Custom addition in this workspace: visible-Qt scripts can open and drive the native Virtual Pad through `mgba.qt.open_virtual_pad()` and `mgba.qt.virtual_pad_*` helpers, including timed frame presses and `virtual_pad_key_mask()` capture for input tapes.
- Custom addition in this workspace: visible-Qt scripts can sample `mgba.qt.controller_key_mask()` to record the next-frame GBA button mask after keyboard mappings such as `X -> A` have already been resolved.
- Custom addition in this workspace: host-side `Core` and visible-Qt `current_core()` now expose `get_keys()` so Python input-tape helpers can mirror Lua `emu:getKeys()` behavior for current-key recording.
- Custom addition in this workspace: Qt startup scripts get normal Python script globals, including `__name__ == "__main__"` and a usable `__file__`.
- The generic FR/LG visible Qt launcher is `frlg-seed-bruteforce/run_frlg_seed_bruteforce_visible.ps1`, and it launches `frlg_seed_bruteforce.py`.
- The maintained first-half workflow instead runs [frlg-seed-bruteforce/Seed-Bruteforcer.py](frlg-seed-bruteforce/Seed-Bruteforcer.py) or [frlg-seed-bruteforce/Seed-Replicator.py](frlg-seed-bruteforce/Seed-Replicator.py) directly through `Tools > Scripting...` or `mGBA.exe --script FILE`.
- The broad Python API in these examples is the host-side `import mgba` workflow.
- The maintained local dev build is `<repo-root>\build-mingw64-python-qt`; other old build directories are disposable workspace artifacts, not the canonical target.
- The maintained local dev build stores retained config, saves, and savestates under `<repo-root>\userdata` so rebuilding the binary does not wipe progress.
- The older SDL debugger path still exists in source for reference and unit tests, but it is not the maintained local build target in this workspace.
- Each example has its own module docstring with a short explanation and command-line usage.

## VS Code Note

- These are normal relative Markdown links on purpose.
- In VS Code, clicking them from the editor or Markdown preview should open the referenced file inside the workspace.


