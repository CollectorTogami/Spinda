# Python / Lua Scripts

## Status Bucket

- Current status: Active script inventory for generated and maintained Python/Lua helpers.
- Last verified date: 2026-05-30.
- Proven artifacts: listed script paths and source/package tests that import or exercise the maintained examples.
- Known gaps: New generated scripts can be missed if this inventory is not updated in the same task.
- Next action: Add or update entries here whenever a Python/Lua script is created, renamed, removed, or promoted.
- Evidence model: Claims must be labeled as `Proven`, `Observed once`, `Inferred`, `Planned`, or `Obsolete`; see DOCUMENTATION_EVIDENCE_POLICY.md.

This file lists the Python and Lua scripts generated in this workspace so far.

Root workspace:
- `<workspace-root>` for this mGBA-derived project tree.

Notes:
- Paths below are workspace-relative.
- This inventory covers the custom scripts created for this project and roadmap.
- It intentionally does not list older upstream Lua assets such as `res/scripts/pokemon.lua`.
- The current known FR/LG route-seed and CSV reference values are tracked in
  INITIAL_SEED_CSV_REFERENCE.md.
- The paired workflow decision trail is tracked in
  WORKFLOW_DECISION_LOG.md.
- Start broader doc navigation from index _markdown.md
  on the mGBA side. If using a paired RNG/CUDA project tree, use that
  project's own `DOCUMENTATION_INDEX.md`.

## Evidence Split

### Proven

- A script row is proven only as an inventory entry when the path exists.
- Behavior claims are proven only when paired source tests, deployment tests, or
  preserved run artifacts back the row.

### Observed once

- Script behavior seen in one emulator session must stay labeled as one-session
  evidence until repeated or tested.

### Inferred

- Descriptions that summarize intent from code structure are inferred unless
  tests or run artifacts are named.

### Planned

- Future full-corpus output shapes, planned route roles, and unrun script modes
  must stay in planned language.

### Obsolete

- Removed scripts, renamed scripts, or superseded workflows should be kept only
  as obsolete/historical notes or deleted from active tables.

## General Python Examples

| Name | Path | Description |
| --- | --- | --- |
| [_helpers.py](../doc/python-examples/_helpers.py) | [doc/python-examples/_helpers.py](../doc/python-examples/_helpers.py) | Shared loader/parser/output helpers used by the host-side Python example scripts. |
| [audio_demo.py](../doc/python-examples/audio_demo.py) | [doc/python-examples/audio_demo.py](../doc/python-examples/audio_demo.py) | Loads a ROM, runs frames, and reads stereo audio samples through the Python bindings. |
| [basic_core_demo.py](../doc/python-examples/basic_core_demo.py) | [doc/python-examples/basic_core_demo.py](../doc/python-examples/basic_core_demo.py) | Minimal host-side example that loads a ROM, resets it, taps a button, and advances frames. |
| [button_cycle_state_demo.py](../doc/python-examples/button_cycle_state_demo.py) | [doc/python-examples/button_cycle_state_demo.py](../doc/python-examples/button_cycle_state_demo.py) | Cycles common GBA inputs, then saves and reloads two file-backed savestates. |
| [embedded_debugger_script.py](../doc/python-examples/embedded_debugger_script.py) | [doc/python-examples/embedded_debugger_script.py](../doc/python-examples/embedded_debugger_script.py) | Example script for mGBA's embedded Python debugger environment. |
| [gba_sio_demo.py](../doc/python-examples/gba_sio_demo.py) | [doc/python-examples/gba_sio_demo.py](../doc/python-examples/gba_sio_demo.py) | Demonstrates a custom GBA SIO driver and safe cleanup of the attached device. |
| [gb_sio_demo.py](../doc/python-examples/gb_sio_demo.py) | [doc/python-examples/gb_sio_demo.py](../doc/python-examples/gb_sio_demo.py) | Demonstrates a custom GB serial/link driver and logs SB/SC writes. |
| [input_tape.py](../doc/python-examples/input_tape.py) | [doc/python-examples/input_tape.py](../doc/python-examples/input_tape.py) | Python helper for the shared `mgba-input-tape-v1` route-tape format. It records scripted exact key masks per frame, samples the visible Qt frontend's mapped GBA button mask once per emulated frame, or narrowly samples the Virtual Pad widget, replays tapes with stale-input clearing, exposes Lua-parity helper aliases such as `fromRuns`, `load`, `replay`, and `recordCurrentKeys`, rejects empty exchange tapes, normalizes saved masks to standard GBA bits, and stores no ROM/save/savestate anchor in the tape itself. |
| [logging_demo.py](../doc/python-examples/logging_demo.py) | [doc/python-examples/logging_demo.py](../doc/python-examples/logging_demo.py) | Installs a Python logger and runs frames while log messages flow through the bindings. |
| [memory_demo.py](../doc/python-examples/memory_demo.py) | [doc/python-examples/memory_demo.py](../doc/python-examples/memory_demo.py) | Demonstrates typed memory reads, a reversible write, and a small memory search. |
| [registers_demo.py](../doc/python-examples/registers_demo.py) | [doc/python-examples/registers_demo.py](../doc/python-examples/registers_demo.py) | Prints CPU register snapshots for GBA or GB cores and exercises safe setter writes. |
| [save_state_demo.py](../doc/python-examples/save_state_demo.py) | [doc/python-examples/save_state_demo.py](../doc/python-examples/save_state_demo.py) | Saves raw state bytes in memory, restores them, and can export the blob to disk. |
| [screenshot_demo.py](../doc/python-examples/screenshot_demo.py) | [doc/python-examples/screenshot_demo.py](../doc/python-examples/screenshot_demo.py) | Captures a rendered frame into an image buffer and writes it to PNG. |
| [thread_demo.py](../doc/python-examples/thread_demo.py) | [doc/python-examples/thread_demo.py](../doc/python-examples/thread_demo.py) | Runs a core through `mgba.thread.Thread`, pauses it, and inspects the live core safely. |
| [vfs_demo.py](../doc/python-examples/vfs_demo.py) | [doc/python-examples/vfs_demo.py](../doc/python-examples/vfs_demo.py) | Uses `mgba.vfs` to open a ROM, inspect bytes, and load the core through a VFile. |

## FR/LG Seed Scripts

| Name | Path | Description |
| --- | --- | --- |
| [analyze_button_combos.py](../doc/python-examples/frlg-seed-bruteforce/analyze_button_combos.py) | [doc/python-examples/frlg-seed-bruteforce/analyze_button_combos.py](../doc/python-examples/frlg-seed-bruteforce/analyze_button_combos.py) | Exhaustively tests valid final-frame button combinations from one checkpoint and summarizes which seeds they produce. |
| [frlg_seed_bruteforce.py](../doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce.py) | [doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce.py](../doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce.py) | Brute-forces FR/LG initial seeds from the title screen, now trying `A` and `Start` separately per delay in the same frame window. |
| [Seed-Bruteforcer.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Bruteforcer.py) | [doc/python-examples/frlg-seed-bruteforce/Seed-Bruteforcer.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Bruteforcer.py) | First-half-specific copy of the FR/LG brute-force workflow, now retargeted to seed `0xCD39` for the second-half route seed work, using the shared read-only save `<repo-root>\Artifacts\1 from egg.sav`, enabling Audio killswitch, no-render mode, and unbounded fast-forward by default, scanning widened title-skip lanes `0..31`, keeping non-hit branches file-light by only saving `1 from egg - replay-candidate` on the verified winning rerun, promoting that candidate to `1 from egg - replay-working`, exporting `1 from egg - replay-readonly` plus `1 from egg - replay-metadata.json`, and creating the locked baseline pair `1 from egg - locked-baseline` plus `1 from egg - locked-baseline-metadata.json` when no baseline exists yet. |
| [Seed-Replicator.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Replicator.py) | [doc/python-examples/frlg-seed-bruteforce/Seed-Replicator.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Replicator.py) | Metadata-driven replay script for the known-good first-half hit. It prefers matching locked metadata, falls back to matching replay metadata, and scans `<repo-root>\live-lanes\live-*` artifact folders such as `live-fbc7-lane16` and `live-cd39-lane21` when stale root metadata targets an older seed. Checkpoints, done states, and relative metadata paths resolve beside the selected metadata file, so replay can use the exact lane folder that produced the hit. It rejects unsafe checkpoint path names, loads but never writes the selected save except for intended replay savestate output, enables Audio killswitch, no-render mode, and unbounded fast-forward by default, skips losing brute-force branches, and records PRNG-orbit failsafe fields as secondary diagnostics without replacing Timer 1 as the authoritative 16-bit seed source. |
| [Seed-Sample-Replicator.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Sample-Replicator.py) | [doc/python-examples/frlg-seed-bruteforce/Seed-Sample-Replicator.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Sample-Replicator.py) | Read-only cross-save verifier for the known first-half initial-seed recipe. It samples existing `.sav` files from `<repo-root>\1sthalves\saves`, loads each as a temporary save, then uses the default `checkpoint` anchor to load the calibrated read-only pre-input checkpoint and press the known final button without brute-forcing. Optional `--anchor route` replays the exact recorded opening and pre-input route tapes from reset as an audit path. The script observes Timer 1 / `gRngValue`, hashes each source save and checkpoint before and after, and accepts `MGBA_SEED_SAMPLE_*` environment defaults for Qt `mGBA.exe --script` runs. |
| [Seed-Replicator-Pick-Save.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Replicator-Pick-Save.py) | [doc/python-examples/frlg-seed-bruteforce/Seed-Replicator-Pick-Save.py](../doc/python-examples/frlg-seed-bruteforce/Seed-Replicator-Pick-Save.py) | Save-picker variant of the fixed replay script. It opens the native Windows Explorer `.sav` picker, then calls the normal metadata-driven replay with that selected save loaded temporarily so the chosen file is not written back during seed replication. |
| [frlg_seed_bruteforce_embedded.py](../doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce_embedded.py) | [doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce_embedded.py](../doc/python-examples/frlg-seed-bruteforce/frlg_seed_bruteforce_embedded.py) | Embedded debugger-oriented variant of the FR/LG initial-seed brute-force workflow. |

## FR/LG TSV Save Bank Scripts

| Name | Path | Description |
| --- | --- | --- |
| [frlg_tsv_common.py](../doc/python-examples/frlg-tsv-save-bank/frlg_tsv_common.py) | [doc/python-examples/frlg-tsv-save-bank/frlg_tsv_common.py](../doc/python-examples/frlg-tsv-save-bank/frlg_tsv_common.py) | Pure-Python helper layer for the FR/LG TSV save bank. It implements TSV/PSV math, GBA LCRNG wait-plan construction, status JSON mutation, atomic JSON writes, and TID/SID memory reads without importing mGBA. |
| [Build-FRLG-TSV-Save-Bank.py](../doc/python-examples/frlg-tsv-save-bank/Build-FRLG-TSV-Save-Bank.py) | [doc/python-examples/frlg-tsv-save-bank/Build-FRLG-TSV-Save-Bank.py](../doc/python-examples/frlg-tsv-save-bank/Build-FRLG-TSV-Save-Bank.py) | Visible-Qt runner for one FR/LG save per Trainer Shiny Value. Dry-plan mode writes `_frlg_tsv_wait_plan.json` and `_frlg_tsv_save_bank_status.json`; live mode restores the pre-SID scratch checkpoint per TSV, waits neutral frames, presses the SID-commit input, verifies the final TID/SID/TSV, replays a post-SID input tape, and exports decimal `TSV-xxxx-sid-xxxxx.sav` files under `<repo-root>\TSVs`. |

## FR/LG Spinda Roadmap Scripts

| Name | Path | Description |
| --- | --- | --- |
| [spinda_frlg_common.py](../doc/python-examples/frlg-spinda/spinda_frlg_common.py) | [doc/python-examples/frlg-spinda/spinda_frlg_common.py](../doc/python-examples/frlg-spinda/spinda_frlg_common.py) | Shared FR/LG roadmap helper layer for RAM reads, save/state handling, route playback, PID helpers, manifests, and runtime Qt integration. |
| [frlg_spinda_first_half_lane.py](../doc/python-examples/frlg-spinda/frlg_spinda_first_half_lane.py) | [doc/python-examples/frlg-spinda/frlg_spinda_first_half_lane.py](../doc/python-examples/frlg-spinda/frlg_spinda_first_half_lane.py) | Phase-1 scaffold that turns one calibrated base state into one archived lower-half lane save plus manifest/work-state data. |
| [frlg_spinda_first_half_batch.py](../doc/python-examples/frlg-spinda/frlg_spinda_first_half_batch.py) | [doc/python-examples/frlg-spinda/frlg_spinda_first_half_batch.py](../doc/python-examples/frlg-spinda/frlg_spinda_first_half_batch.py) | Phase-1 batch runner that now defaults to the exact-seed loaded-state lane, using the premade post-seed route anchor plus metadata so it targets the Spinda first halves for that known-good initial seed. It can collapse raw CSV collisions onto live daycare halves or preserve every raw CSV target under `<repo-root>\1sthalves`, using live-half names in main `saves` / `states` folders and `__raw0x####` suffixed collision files under `_live_name_collisions`. It uses a checkpointed post-setup runway for loaded-state full sweeps, restores per-target `t-18` checkpoints after export branches, saves a matching pre-daycare-man `.ss0` as soon as the lower half is hit, resolves bounded drift around CSV `t-0`, writes `_resume_status.json`, skips only save/state/manifest triads whose paths, target values, initial seed, and SHA-1 hashes still match, and exports matching `.sav` outputs without forcing RAM. |
| [Egg-First-Half-Hitter.py](../doc/python-examples/frlg-spinda/Egg-First-Half-Hitter.py) | [doc/python-examples/frlg-spinda/Egg-First-Half-Hitter.py](../doc/python-examples/frlg-spinda/Egg-First-Half-Hitter.py) | Operator wrapper for the current `0xFBC7` post-seed lane in `<repo-root>\live-lanes\live-fbc7-lane16`. It preserves `1 from egg.ss0` as a read-only `1 from egg - clean-backup.ss0` if missing, auto-generates/uses `firsthalf-prng-FB91.csv` from replay metadata so route checkpoints match the organic `gRngValue`, then delegates to the loaded-state first-half batch runner. With no `--target-half`, it preserves all `65536` raw CSV targets; future full-corpus output names main saves as `saves\0x####.sav` and states as `states\0x####.ss0` by live daycare half, with duplicate raw collisions under `_live_name_collisions`. With `--target-half`, it targets one live daycare half. It writes a running/finished status file and an error JSON traceback for visible-Qt failures. |
| [Prime-Second-Half-States.py](../doc/python-examples/frlg-spinda/Prime-Second-Half-States.py) | [doc/python-examples/frlg-spinda/Prime-Second-Half-States.py](../doc/python-examples/frlg-spinda/Prime-Second-Half-States.py) | Intermediate phase-2 priming runner. It loads each existing first-half `.sav` from `<repo-root>\1sthalves\saves`, reproduces the current phase-2 initial seed `0xCD39` through the route tapes from `live-cd39-lane21` without forcing RAM, learns a source-save-specific title delay from a rolling pre-input checkpoint when needed, replays `tape seed to step 2.json`, pads neutral input to exactly frame 740 from the observed seed frame, validates the live `gRngValue` against a learned or configured checkpoint, then writes matching `.ss0` states to `<repo-root>\1sthalves\priomed-2nd`. It uses compact `_prime_second_status.json` / JSONL error files and skips existing states for crash resume. |
| [frlg_spinda_lane_workspace.py](../doc/python-examples/frlg-spinda/frlg_spinda_lane_workspace.py) | [doc/python-examples/frlg-spinda/frlg_spinda_lane_workspace.py](../doc/python-examples/frlg-spinda/frlg_spinda_lane_workspace.py) | Offline helper for creating and inspecting canonical per-lane workspace paths and manifests. |
| [spinda_frlg_archive.py](../doc/python-examples/frlg-spinda/spinda_frlg_archive.py) | [doc/python-examples/frlg-spinda/spinda_frlg_archive.py](../doc/python-examples/frlg-spinda/spinda_frlg_archive.py) | Archive layer for fixed-width lane blocks, bitmaps, and the resumable global corpus manifest. |
| [frlg_spinda_corpus_manifest.py](../doc/python-examples/frlg-spinda/frlg_spinda_corpus_manifest.py) | [doc/python-examples/frlg-spinda/frlg_spinda_corpus_manifest.py](../doc/python-examples/frlg-spinda/frlg_spinda_corpus_manifest.py) | CLI for creating and updating the top-level corpus manifest without launching the emulator. |
| [frlg_spinda_recipe_lint.py](../doc/python-examples/frlg-spinda/frlg_spinda_recipe_lint.py) | [doc/python-examples/frlg-spinda/frlg_spinda_recipe_lint.py](../doc/python-examples/frlg-spinda/frlg_spinda_recipe_lint.py) | Offline linter for first-half and second-half JSON route recipes. |
| [frlg_spinda_workspace_audit.py](../doc/python-examples/frlg-spinda/frlg_spinda_workspace_audit.py) | [doc/python-examples/frlg-spinda/frlg_spinda_workspace_audit.py](../doc/python-examples/frlg-spinda/frlg_spinda_workspace_audit.py) | Offline auditor that checks save/state hashes, lane blocks, bitmaps, and manifest consistency. |
| [frlg_spinda_second_half_lane.py](../doc/python-examples/frlg-spinda/frlg_spinda_second_half_lane.py) | [doc/python-examples/frlg-spinda/frlg_spinda_second_half_lane.py](../doc/python-examples/frlg-spinda/frlg_spinda_second_half_lane.py) | Phase-2 scaffold that reloads one lane work state, sweeps upper halves, validates PIDs, and fills the raw lane block. |
| [Build-Phase2-Pickup-States.py](../doc/python-examples/frlg-spinda/Build-Phase2-Pickup-States.py) | [doc/python-examples/frlg-spinda/Build-Phase2-Pickup-States.py](../doc/python-examples/frlg-spinda/Build-Phase2-Pickup-States.py) | Standalone Phase 2 pickup-state builder that reproduces the `secondhalf.csv` seed, replays the seed-to-pre-pickup bridge tape, automatically uses `0x0000 special tape.json` for the ACE endpoint lane, pads to the configured baseline frame, validates `gRngValue`, and writes `Phase2PickupStates\0x####.ss0`. On launches after the 2026-04-26 hardening patch, states publish through `0x####.ss0.tmp` plus expected-size validation, resume skips only valid-size final `.ss0` files, and runtime feature controls are read from `_phase2_pickup_control.json` between save jobs so human-check toggles do not go through the live mGBA UI. |
| [phase2_pickup_runtime_control.py](../tools/spinda/phase2_pickup_runtime_control.py) | [tools/spinda/phase2_pickup_runtime_control.py](../tools/spinda/phase2_pickup_runtime_control.py) | Writes the Phase 2 pickup runtime-control JSON used by the active builder to disable or restore Audio killswitch, no-render, and fast-forward at safe points. `--human-check` stops the builder cleanly after applying visible/audio settings so mGBA returns to operator control; `--performance` restores high-speed settings for the next launch. |
| [frlg_spinda_export.py](../doc/python-examples/frlg-spinda/frlg_spinda_export.py) | [doc/python-examples/frlg-spinda/frlg_spinda_export.py](../doc/python-examples/frlg-spinda/frlg_spinda_export.py) | Offline exporter that writes one `.pk3`, one lane directory of loose `.pk3` files, one full lane ZIP, one upper-half range ZIP, or many lanes in a nested ZIP from the archive blocks. Single-record export reads only the bitmap and one 80-byte block slice. |
| [native_phase3_worker_pool.py](../tools/spinda/native_phase3_worker_pool.py) | [tools/spinda/native_phase3_worker_pool.py](../tools/spinda/native_phase3_worker_pool.py) | Phase 3 production launcher that keeps worker slots refilling from lane ranges. It defaults to the standalone CLI runner, uses platform-aware Windows/Linux CLI paths, passes shared cache, learned pickup-delay, and optional stored-ZIP settings, skips completed lane ZIPs by filename, and writes preview-limited pool status. |
| [phase3_ledger_worker_client.py](../tools/spinda/phase3_ledger_worker_client.py) | [tools/spinda/phase3_ledger_worker_client.py](../tools/spinda/phase3_ledger_worker_client.py) | Assisted-machine Phase 3 client that claims lane batches from the command-center ledger API, runs the native worker pool for those claimed lanes, sends lease heartbeats while workers run, then reports each lane as done or failed based on final ZIP presence. |
| [build_phase3_cli_linux.sh](../tools/spinda/build_phase3_cli_linux.sh) | [tools/spinda/build_phase3_cli_linux.sh](../tools/spinda/build_phase3_cli_linux.sh) | Linux helper-node build wrapper for the headless Phase 3 CLI target. It disables Qt, SDL, Python, scripting, and unrelated optional libraries, builds `mgba-spinda-phase3`, and supports native-CPU, LTO, and PGO environment switches. |
| [run_phase3_ledger_helper.sh](../tools/spinda/run_phase3_ledger_helper.sh) | [tools/spinda/run_phase3_ledger_helper.sh](../tools/spinda/run_phase3_ledger_helper.sh) | Linux helper-node launcher that validates ROM/CSV/Phase2 inputs, claims lanes from the coordinator ledger, runs the native CLI worker pool, heartbeats claims, and reports final ZIP presence back to the coordinator. |
| [check_linux_helper_port.py](../tools/spinda/check_linux_helper_port.py) | [tools/spinda/check_linux_helper_port.py](../tools/spinda/check_linux_helper_port.py) | Linux helper-node readiness validator for source, clean, and assisted trees. It checks required files, CLI-only build flags, helper command ownership, shell shebang/LF line endings, Bash syntax when available, clean-package artifact absence, and assisted-package input/state presence without launching mGBA or claiming lanes. |
| [test_phase3_linux_helper_port.py](../src/platform/python/tests/examples/test_phase3_linux_helper_port.py) | [src/platform/python/tests/examples/test_phase3_linux_helper_port.py](../src/platform/python/tests/examples/test_phase3_linux_helper_port.py) | Source/package tests for the Linux Phase 3 helper path, covering platform defaults, CLI-only build flags, ledger passthrough, docs proof-gate wording, clean-repo artifact absence, and Assisted-baking package readiness when run from that tree. |
| [phase3_independent_watcher.py](../tools/spinda/phase3_independent_watcher.py) | [tools/spinda/phase3_independent_watcher.py](../tools/spinda/phase3_independent_watcher.py) | Read-only Phase 3 watchdog that compares output-folder filenames, worker-pool status JSON, command-center API status, host process list, and disk free space. It filters process rows, caches process/API checks when run at short intervals, and writes `_phase3_independent_watcher_status.json` / `_phase3_independent_watcher_events.jsonl` for the command-center watcher panel. |
| [benchmark_phase3_worker_counts.py](../tools/spinda/benchmark_phase3_worker_counts.py) | [tools/spinda/benchmark_phase3_worker_counts.py](../tools/spinda/benchmark_phase3_worker_counts.py) | Phase 3 benchmark helper that runs the normal worker pool for selected worker counts and writes elapsed time, completed lanes, generated records, and lanes/hour to `_worker_count_benchmark_summary.json`. It refuses non-empty `workers_N` output folders unless `--reuse-output` is explicit, so timing runs do not accidentally measure resume skips, and it reads exact per-lane status files after each run instead of relying on preview-limited pool status. |
| [spinda_workbench.py](../tools/spinda/spinda_workbench/spinda_workbench.py) | [tools/spinda/spinda_workbench/spinda_workbench.py](../tools/spinda/spinda_workbench/spinda_workbench.py) | Unified read-only Flask workbench for post-Phase-3 operation. It scans Phase 3 lane ZIP filenames, TSV save-bank filenames, and the SID ledger, shows hatch-splitter blockers, maps a PID to its lane ZIP/entry/PSV/TSV, renders a local Spinda Painter preview using the original nibble coordinate grid, reports nature/gender/ability/TID-SID shiny stats, runs bounded pattern-taxonomy suggestions with a direct PID scorer, mode-specific scoring, and an unrounded top-N heap, reports scan timing/PID rate, rejects out-of-scope Phase 3 lane ZIPs for the active target range, accepts mixed-case ZIP/tmp/PK3 suffixes, defaults empty optional numeric API queries, validates bounded CLI numeric options, formats IPv6 URLs correctly, rejects bool-valued legacy ledger completion counts, serves static HTML without template rendering, quotes current-interpreter command-preview path arguments with PowerShell-safe single quotes and call-operator syntax, and prints validator/hatch/7z command previews without launching workers or mutating outputs. |
| [zip_to_7z_gui.py](../tools/spinda/zip_to_7z_gui/zip_to_7z_gui.py) | [tools/spinda/zip_to_7z_gui/zip_to_7z_gui.py](../tools/spinda/zip_to_7z_gui/zip_to_7z_gui.py) | Manual post-project Tkinter GUI that converts completed `.zip` result archives into matching `.7z` archives through a user-installed 7-Zip CLI, with input/output pickers, recursive layout preservation, overwrite control, LZMA/LZMA2 selection, progress bar, cancel support, and a small progress console. |

## SPC3 Analysis And Repack Scripts

| Name | Path | Description |
| --- | --- | --- |
| [spc3_iv_offset_classifier.py](../tools/spinda/spc3_iv_offset_classifier.py) | [tools/spinda/spc3_iv_offset_classifier.py](../tools/spinda/spc3_iv_offset_classifier.py) | Read-only SPC3 IV32 classifier that compares predictor misses against runtime FR/LG egg-IV models, nearby offsets, and parent-inheritance assumptions to measure which explicit cells can become modeled compression cases. |
| [spc3_residual_optimizer.py](../tools/spinda/spc3_residual_optimizer.py) | [tools/spinda/spc3_residual_optimizer.py](../tools/spinda/spc3_residual_optimizer.py) | Experimental residual-compression evaluator for the remaining SPC3 v5 explicit IV32 cells. It tests changed-stat stream splitting, residual class tables, per-lane bitmap representation choice, mask/value splitting, zstd dictionaries, and nearest-baseline selectors without changing the verified v5 package. Report schema v2 includes full-package projections, `--all-lanes`, streamed bucket compression, and bounded bucket handles for larger runs. |
| [spc3_compress.py](../tools/spinda/spc3_compress.py) | [tools/spinda/spc3_compress.py](../tools/spinda/spc3_compress.py) | Umbrella SPC3 compressor/auditor CLI. It lets the operator choose `v2`, `v3`, `v4`, `v5`, `v6`, `v7`, `v8`, or `all` from one command, delegates to the native v2 tool or the Python repackers, supports pack/verify/pack-verify/audit, and writes per-target JSON reports plus an all-target summary. |
| [spc3_rule_bitmap_repack.py](../tools/spinda/spc3_rule_bitmap_repack.py) | [tools/spinda/spc3_rule_bitmap_repack.py](../tools/spinda/spc3_rule_bitmap_repack.py) | Experimental SPC3 rule-bitmap repacker that preserves the embedded predictor and stores old predictor misses through lane/mod24/lowbyte/upper bitmap structure before later two-stage experiments. |
| [spc3_two_stage_runtime_repack.py](../tools/spinda/spc3_two_stage_runtime_repack.py) | [tools/spinda/spc3_two_stage_runtime_repack.py](../tools/spinda/spc3_two_stage_runtime_repack.py) | Experimental SPC3 v5 pack/verify tool that keeps the old IV32 predictor as stage 1, applies a runtime RS/FRLG egg-IV generator to stage-1 misses, and stores the remaining explicit cells as changed IV stat fields. |
| [spc3_v6_upper_repack.py](../tools/spinda/spc3_v6_upper_repack.py) | [tools/spinda/spc3_v6_upper_repack.py](../tools/spinda/spc3_v6_upper_repack.py) | Experimental SPC3 v6 pack/verify tool that keeps the v5 two-stage runtime predictor, moves remaining explicit IV32 cells into 256 global upper-byte residual bands, and stores those bands with changed-mask-grouped 5-bit stat values. The verified full corpus is `278,311,199` bytes with zero mismatches. |
| [spc3_v7_global_stage_repack.py](../tools/spinda/spc3_v7_global_stage_repack.py) | [tools/spinda/spc3_v7_global_stage_repack.py](../tools/spinda/spc3_v7_global_stage_repack.py) | Experimental SPC3 v7 pack/verify tool that keeps the v6 residual value section, stores stage-1 and stage-2 bitmaps as global upper-byte stage bands, keeps only template substreams per lane, and verifies the full corpus at `103,403,124` bytes with zero mismatches. |
| [spc3_v8_compact_repack.py](../tools/spinda/spc3_v8_compact_repack.py) | [tools/spinda/spc3_v8_compact_repack.py](../tools/spinda/spc3_v8_compact_repack.py) | Experimental SPC3 v8 pack/verify tool that adds adaptive stage-band transforms, a global template section, zero-byte lane streams, and per-band residual mode selection. The verified full corpus is `63,014,910` bytes with zero mismatches. |
| [test_spc3_compress.py](../tools/spinda/test_spc3_compress.py) | [tools/spinda/test_spc3_compress.py](../tools/spinda/test_spc3_compress.py) | Focused regression harness for the umbrella SPC3 CLI and developer GUI wrapper. It mocks expensive corpus operations and checks default output routing, native v2 verify input selection, report summary accounting, all-target report naming, GUI command construction, GUI target-state behavior, and documentation coverage. |
| [test_spc3_v8_compact.py](../tools/spinda/test_spc3_v8_compact.py) | [tools/spinda/test_spc3_v8_compact.py](../tools/spinda/test_spc3_v8_compact.py) | Focused regression harness for v8 stage transforms, residual selector decoding, and global template section round trips. |

## Read-Only Spinda Corpus Tools

| Name | Path | Description |
| --- | --- | --- |
| [first_half_raw_csv_audit.py](../tools/spinda/first_half_raw_csv_audit.py) | [tools/spinda/first_half_raw_csv_audit.py](../tools/spinda/first_half_raw_csv_audit.py) | Read-only auditor for `<repo-root>\1sthalves`; counts `.sav` / `.ss0` pairs, missing target sides, absent target IDs, duplicate target entries, bad names, and bad settled sizes without touching mGBA or output files. It auto-detects flat output and the current split `saves` / `states` folders. |
| [first_half_raw_csv_monitor.py](../tools/spinda/first_half_raw_csv_monitor.py) | [tools/spinda/first_half_raw_csv_monitor.py](../tools/spinda/first_half_raw_csv_monitor.py) | Read-only ETA monitor for the live first-half raw-CSV output folder; samples complete-pair growth and prints rate, ETA, finish time, and structural warning counts. |
| [first_half_progress_web.py](../tools/spinda/first_half_progress_web.py) | [tools/spinda/first_half_progress_web.py](../tools/spinda/first_half_progress_web.py) | Flask dashboard for the same live first-half raw-CSV output folder. It serves `/api/status` JSON and `/events` Server Sent Events, using the pokebot-style browser update pattern while remaining read-only. API and SSE clients share one throttled filesystem-scan cache, controlled by `--sample-interval`, so extra browser tabs do not multiply directory scans. It binds to `0.0.0.0` by default and displays the detected local LAN IPv4 URL instead of `localhost`. |
| [phase2_pickup_progress_web.py](../tools/spinda/phase2_pickup_progress_web.py) | [tools/spinda/phase2_pickup_progress_web.py](../tools/spinda/phase2_pickup_progress_web.py) | Read-only Flask dashboard for `Phase2PickupStates`, serving `/api/status` JSON and `/events` SSE with a visible `complete / 65536` progress counter, bad-size reporting, and `0x####.ss0.tmp` unsettled-write reporting. |
| [phase2_pickup_state_validator.py](../tools/spinda/phase2_pickup_state_validator.py) | [tools/spinda/phase2_pickup_state_validator.py](../tools/spinda/phase2_pickup_state_validator.py) | Read-only Phase 2 pickup-state validator for `Phase2PickupStates`; reports missing states, bad-size final `.ss0` files, `.ss0.tmp` files, stale temp files, bad names, duplicate-looking state variants, and optional post-run sample `gRngValue` checks in a separate host-side core. |

## Mutating Spinda Corpus Maintenance Tools

| Name | Path | Description |
| --- | --- | --- |
| [fix_first_half_raw_csv_names.py](../tools/spinda/fix_first_half_raw_csv_names.py) | [tools/spinda/fix_first_half_raw_csv_names.py](../tools/spinda/fix_first_half_raw_csv_names.py) | Host-side venv tool that renames completed raw-CSV first-half `.sav` / `.ss0` artifacts from raw `Random()` half names to live FR/LG daycare half names using a source -> temp -> final two-phase rename. Duplicate raw targets that convert to the same live half are preserved under `_live_name_collisions` by default. |

## Live Runtime Monitor Scripts

| Name | Path | Description |
| --- | --- | --- |
| [frlg-daycare-egg-status.py](../res/scripts/frlg-daycare-egg-status.py) | [res/scripts/frlg-daycare-egg-status.py](../res/scripts/frlg-daycare-egg-status.py) | Python runtime monitor for the Qt scripting window that shows daycare egg state, lower half, and steps to the next egg check every frame. |
| [frlg-daycare-egg-status.lua](../res/scripts/frlg-daycare-egg-status.lua) | [res/scripts/frlg-daycare-egg-status.lua](../res/scripts/frlg-daycare-egg-status.lua) | Lua runtime monitor that mirrors the Python daycare status panel for live FR/LG calibration in the scripting window. |
| [frlg-grng-state.lua](../res/scripts/frlg-grng-state.lua) | [res/scripts/frlg-grng-state.lua](../res/scripts/frlg-grng-state.lua) | Lua runtime monitor for the Qt scripting window that reads FR/LG `gRngValue` at `0x03005000`, reverse-searches the nearby LCRNG/LCRNG(R) orbit for the 16-bit seed state behind the current value, shows the current 32-bit value plus seed and frame-distance estimate in a script buffer, and writes periodic console log lines. The displayed frame distance is the signed LCRNG advance distance from the seed state to live `gRngValue`; noisy gameplay can make emulator frames and RNG calls diverge. `MGBA_GRNG_LOG_EVERY` controls console log cadence, `MGBA_GRNG_DISCERN_WINDOW` controls reverse-search range, `MGBA_GRNG_REVERSE_STEPS_PER_FRAME` controls chunk size, and optional `MGBA_GRNG_STATUS_MARKER` mirrors the buffer text for deployment tests. |

## Runtime API Helpers

These are not standalone scripts, but they are script-facing additions that
matter when using the visible Qt GUI from Python:

| API | Location | Description |
| --- | --- | --- |
| `mgba.qt.open_virtual_pad()` and `mgba.qt.open_virtual_pad_settings()` | [src/platform/python/mgba/qt.py](../src/platform/python/mgba/qt.py) | Open the native Qt Virtual Pad and its settings UI from a visible Qt script. |
| `mgba.qt.virtual_pad_hold(...)`, `virtual_pad_autofire(...)`, `virtual_pad_press_for_frames(...)`, and `virtual_pad_clear()` | [src/platform/python/mgba/qt.py](../src/platform/python/mgba/qt.py) | Drive the native Virtual Pad from Python, including frame-counted presses that release and pause at the final frame. |
| `mgba.qt.virtual_pad_key_mask()` | [src/platform/python/mgba/qt.py](../src/platform/python/mgba/qt.py) | Samples the visible Qt Virtual Pad's held, timed, and autofire-visible keys as one GBA bitmask for input-tape capture. |
| `mgba.qt.controller_key_mask()` | [src/platform/python/mgba/qt.py](../src/platform/python/mgba/qt.py) | Samples the visible Qt controller's next-frame GBA button mask after keyboard bindings, Virtual Pad state, and scripted held keys have been resolved. |
| `runFramesWithKeys(keys, count)` and `pulseKeys(keys, count)` | [src/core/scripting.c](../src/core/scripting.c) | Generic scripting helpers for Lua/core scripts that need fixed key-mask waits without stepping one frame at a time through the script boundary. |
| `inputTape` | [src/script/engines/input_tape.lua](../src/script/engines/input_tape.lua) | Built-in Lua helper for creating, loading, saving, replaying, and recording the same anchor-agnostic `mgba-input-tape-v1` route tapes used by the native Qt Input Tapes tool and the Python `input_tape.py` helper. |

## VS Code Note

- The links above use normal relative Markdown paths.
- In VS Code, clicking them from the editor or preview should open the target
  file inside VS Code.
