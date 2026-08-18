# mGBA Custom Changes And Added Features

Current status: clean-source feature inventory for the public-safe fork.

Last verified date: 2026-04-30.

Proven artifacts: source paths listed below exist in this clean tree.

Known gaps: this file describes source features, not private production
artifacts. ROMs, saves, savestates, CSV schedules, PK3 files, lane ZIPs, and
private input tapes are intentionally not included.

Next action: keep this file updated when adding or removing Custom Features
menu actions, Phase 3 native runner behavior, worker controls, or public tools.

## High-Level Shape

This fork keeps upstream mGBA emulation code as the base and adds automation
features around the Qt frontend, Python bindings, and one standalone native
Phase 3 CLI target. The Spinda project changes are meant to drive deterministic
workflows, monitor long runs, and export generated data. They are not intended
as upstream mGBA features.

## Qt Custom Features Menu

Menu path: `Tools > Custom Features`.

Source:

- `src/platform/qt/Window.cpp`
- `src/platform/qt/CustomFeatures.cpp`
- `src/platform/qt/CustomFeatures.h`

Added menu actions:

- `Audio killswitch`: disables or restores audio output/sync paths for long
  automation runs.
- `No-render mode`: covers the render surface with a black overlay and reduces
  visible rendering burden during automated runs.
- `Input tapes...`: opens the input tape recorder/replayer tool.
- `Virtual Pad`: opens the live button-control pad.
- `Virtual Pad settings...`: configures always-on-top, autoboot, sticky buttons,
  timed presses, and analog-clear behavior.
- `Worker instances...`: launches isolated mGBA worker instances and exposes
  simple heartbeat/job-file coordination.
- `Savestate memory cache...`: configures in-RAM savestate cache limits.
- `Spinda project...`: opens the native Phase 3 lane builder dialog.

## Input Tapes

Source:

- `src/platform/qt/InputTapeView.cpp`
- `src/platform/qt/InputTapeView.h`
- `src/platform/qt/CoreController.cpp`
- `src/platform/qt/CoreController.h`

Purpose:

- record frame-by-frame button input
- replay recorded input routes
- support mapped Qt input or Virtual Pad-only recording
- keep repeatable route segments outside manual timing

This is used by project scripts to bridge known route sections while preserving
normal emulated frame execution.

Input tapes are not movie files. In this fork, `mgba-input-tape-v1` stores only
GBA button masks and frame counts. It does not store ROM identity, save data,
savestates, emulator settings, reset/power events, or rerecord history. Use
"movie file" only for actual movie/cinema/replay formats. See
`INPUT_TAPES_VS_MOVIES.md`.

## Virtual Pad

Source:

- `src/platform/qt/VirtualPad.cpp`
- `src/platform/qt/VirtualPad.h`
- `src/platform/python/qt.h`
- `src/platform/python/mgba/qt.py`

Purpose:

- expose a live GUI pad for GBA inputs
- support holds, autofire, timed presses, and scripted controls
- expose state to input tape capture
- give Python bindings for automation scripts

Attribution:

- The virtual game pad idea and reference code are based on BizHawk/EmuHawk's
  Virtual Pad.
- BizHawk team original frontend work is MIT-licensed; the upstream BizHawk
  repository also warns that its full tree contains mixed third-party material.
- This clean package keeps BizHawk as reference/attribution only and does not
  vendor BizHawk as a build dependency. Keep `res/licenses/bizhawk.txt` with
  source or binary releases containing this feature.

## Worker Instances

Source:

- `src/platform/qt/CustomFeatures.cpp`
- `src/platform/qt/CustomFeatures.h`
- `src/platform/qt/ConfigController.cpp`

Purpose:

- launch isolated mGBA processes from the Qt UI
- give each worker its own config/save/savestate folders
- write heartbeat files and claim simple job JSON files
- avoid workers trampling the main instance storage

Current production Phase 3 uses the separate headless worker pool more often,
but this Qt worker feature remains part of the fork.

## Savestate Memory Cache

Source:

- `src/platform/qt/Window.cpp`
- `src/platform/qt/CoreController.cpp`
- `src/platform/qt/CoreController.h`

Purpose:

- keep recent savestate data in RAM
- reduce disk reads during repeated restore-heavy automation
- expose max entries and max MiB settings through the Custom Features dialog

## Windows Dark Chrome Helper

Source:

- `src/platform/qt/WindowsDarkChrome.cpp`
- `src/platform/qt/WindowsDarkChrome.h`

Purpose:

- centralize Windows DWM dark-titlebar handling
- use immersive dark mode attributes `20` then `19`
- set caption/text/border colors
- force dark border color to black
- no-op on non-Windows or when mGBA light mode is active

Call sites include the main window, Custom Features dialogs, scripting message
boxes, Virtual Pad, Input Tapes, settings/dialog paths, and Spinda project
dialogs. This avoids rediscovering the same bright-border fix per window.

## Spinda Project Qt Runner

Source:

- `src/platform/qt/SpindaProjectView.cpp`
- `src/platform/qt/SpindaProjectView.h`
- `src/platform/qt/Window.cpp`

Purpose:

- run native Phase 3 lane generation from the Qt frontend
- load one Phase 2 pickup savestate
- read `secondhalf.csv`
- compute a pickup schedule
- emulate to target pickup frames
- extract one 80-byte PK3 record per target
- write one lane ZIP named `0x####.spinda80.zip`

Output shape:

- ZIP contains only `0x########.pk3` entries.
- Entry name is PID-based.
- Each PK3 entry is 80 bytes.
- Full lane contains 65,536 PK3 entries.
- Records are held in RAM before final ZIP write.
- Final output uses temp path then rename to avoid half-written final files.

Speed/solidness features:

- parsed `secondhalf.csv` cache
- runtime schedule cache
- chronological schedule validation
- PID-only pickup detection before full PK3 read
- learned pickup-delay mode with fallback scan
- configurable pickup lead/hold/detect windows
- timing buckets for frame advance, scratch save, pickup detect, restore,
  PK3 read, ZIP build/write, and hashing
- deflate level `1` for lower CPU cost
- optional ZIP store mode for benchmarks
- optional neutral-wait proof frames
- audio killswitch, no-render, and fast-forward run toggles

## Phase 3 Headless CLI

Source:

- `src/platform/test/spinda-phase3-main.cpp`
- `src/platform/test/CMakeLists.txt`

Build target:

- `${BINARY_NAME}-spinda-phase3`
- `mgba-spinda-phase3.exe` on Windows
- `mgba-spinda-phase3` on Linux helper nodes

Purpose:

- run Phase 3 without Qt frontend overhead
- process one lane with `--lane`
- process multiple lanes sequentially in one process with `--lanes`
- load Phase 2 states from `--phase2-state` or `--phase2-dir`
- reuse target/schedule binary cache files
- write PID-named PK3 ZIPs
- run on Linux helper nodes without Qt when built through
  `tools/spinda/build_phase3_cli_linux.sh`

Important options:

- `--rom`
- `--lane`
- `--lanes`
- `--phase2-state`
- `--phase2-dir`
- `--secondhalf-csv`
- `--output-dir`
- `--cache-dir`
- `--overwrite`
- `--pickup-lead`
- `--pickup-hold`
- `--no-fast-pickup-checks`
- `--no-learn-pickup-delay`
- `--neutral-wait-proof-frames`
- `--zip-store`

## Python And Flask Tools

Source:

- `tools/spinda/native_phase3_worker_pool.py`
- `tools/spinda/phase3_command_center_web.py`
- `tools/spinda/phase3_independent_watcher.py`
- `tools/spinda/phase3_zip_validator.py`
- `tools/spinda/phase2_pickup_progress_web.py`
- `tools/spinda/phase2_pickup_state_validator.py`
- `tools/spinda/first_half_progress_web.py`
- `tools/spinda/first_half_raw_csv_audit.py`
- `tools/spinda/canonicalize_phase3_zips.py`
- `tools/spinda/phase3_pkhex_validator/`

Purpose:

- launch and monitor CLI worker pools
- allow browser command-center control of worker count and killswitch
- count completed lanes and total Spindas
- avoid opening ZIP internals during hot production status checks
- show independent watcher status inside the command center
- throttle command-center ZIP scans, JSON parsing, and host resource samples so
  worker emulation gets priority
- let the watcher compare output files, worker-pool JSON, command-center API
  status, OS worker PIDs, and disk free space without controlling workers
- validate Phase 2 pickup states
- validate Phase 3 ZIP structure in RAM
- optionally run final PKHeX.Core semantic validation later

These tools are repo support code, not emulator-core changes.

## Python Binding Additions

Source:

- `src/platform/python/qt.h`
- `src/platform/python/mgba/qt.py`

Purpose:

- expose Custom Features controls to Python scripts
- mirror Virtual Pad controls
- allow scripts to toggle audio killswitch/no-render paths
- support frame-accurate scripted input and monitoring

## Build And Packaging Changes

Source:

- `src/platform/qt/CMakeLists.txt`
- `src/platform/test/CMakeLists.txt`
- `tools/spinda/build_phase3_cli_lto.bat`

Purpose:

- build new Qt source files
- build standalone Phase 3 CLI target
- support separate LTO/native-CPU/PGO experiments for CLI performance

The clean repo includes source and build scripts only. It does not include built
executables.

## Emulation Accuracy Boundary

The project features are automation and extraction layers around mGBA. The
intended RNG boundary is:

- do not patch game RAM to create Phase 3 outputs
- do run normal emulated frames for pickup timing
- do validate expected initial seed and baseline `gRngValue`
- do compare output ZIP/PK3 structure after generation

Custom UI features such as no-render, audio killswitch, command-center control,
and dark chrome should not alter GBA RNG state. Any future optimization that
skips, batches, or shortcuts emulated frames must be proven against the current
exact-output method before production use.

## Removed Or Not Included In Clean Package

Not included in this clean tree:

- ROMs
- saves
- savestates
- generated PK3 files
- generated Spinda ZIPs
- private CSV schedules
- private live-lane folders
- personal userdata
- local build outputs

Features previously explored but removed from current Spinda Workbench scope:

- traverse section
- Method 1 IV section
- XD anti-shiny section
- deleted CSV-reader and old worker-instance experiments not used by current
  source path

## License Note

This fork remains mGBA-derived source under MPL-2.0 unless a file says
otherwise. The Virtual Pad carries a BizHawk/EmuHawk reference notice because
its idea and reference code are based on BizHawk's MIT-licensed
frontend work. Bundled third-party notices stay in `res/licenses/`. See
`LICENSES.md` for concise release guidance.
