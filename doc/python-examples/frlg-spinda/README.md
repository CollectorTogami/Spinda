# FR/LG Spinda Roadmap Scripts

## Status Bucket

- Current status: Active short overview for the local FR/LG Spinda roadmap scripts.
- Last verified date: 2026-05-06.
- Proven artifacts: scripts in this directory, paired script documentation, and Python tests that cover maintained helpers.
- Known gaps: Overview text does not prove full corpus completion; check manifests and run outputs.
- Next action: Update when script roles, required files, or pipeline completion state changes.
- Evidence model: Claims must be labeled as `Proven`, `Observed once`, `Inferred`, `Planned`, or `Obsolete`; see `DOCUMENTATION_EVIDENCE_POLICY.md`.

These are the first Python scripts built directly from the long-form Spinda
roadmap.

They are not the finished corpus generator yet. This folder is the starting
infrastructure for:

- first-half lane export
- lane manifests and resume metadata
- later second-half block generation
- TID0/TSV save-bank tracking for later shiny-hatch proof
- later mass hatching into separate shiny and non-shiny ZIP subsets

## Paper Trail

The current project now keeps the "why" in dedicated docs instead of leaving it
to inference.

Read these before changing route inputs or seed assumptions:

- `<repo-root>\AGENTS.md`
- `<repo-root>\markdown-files\index _markdown.md`
- `WORKFLOW_DECISION_LOG.md`
- `INITIAL_SEED_CSV_REFERENCE.md`
- `PHASE2_PICKUP_RESTART_RUNBOOK.md`
- `PHASE3_SPINDA_BLOCK_BUILDER.md`
- [FRLG_TSV_SAVE_BANK_PLAN.md](../../../docs/FRLG_TSV_SAVE_BANK_PLAN.md)
- [FR/LG TSV Save Bank README](../frlg-tsv-save-bank/README.md)
- `timer1_observations.md`
- Private CUDA route-model documentation index (not included in this clean tree)

These files spell out:

- why Timer 1 remains the authoritative initial-seed signal
- why the copied route CSV may not match the current loaded-state anchor
- why `initial seed` and `Spinda first half` must stay separate in docs/logs
- why the current consumer still expects `t-18`

## Evidence Split

### Proven

- Script files, route helpers, archive helpers, lint/audit tools, and source
  tests named in this folder exist in the local workspace.
- The first-half raw-CSV audit tooling separates organic lanes from endpoint
  exceptions.
- `Build-Phase3-Spinda-Block.py` exists and has source tests for
  `secondhalf.csv` parsing, pickup-lead timing, scratch-state sweeping, PID
  mismatch rejection, runtime-RNG scheduling, optimized CSV parsing, and
  PID-named `.pk3` ZIP output.
- The `0xFBC7` lane and `firsthalf-prng-FB91.csv` loaded-state workflow have
  preserved artifacts and tests referenced by the paired docs.
- The Phase 4 TID0/TSV save bank is complete and passed the standalone party
  slot verifier for all `8192` exported saves.

### Observed once

- Endpoint pickup saves for `0x0000` and `0xFFFF` are live artifacts from
  manual/emulator sessions, not organic lane generator output.
- The Phase 2 pickup-state folder was later observed as complete and valid for
  all `65536` states, including `0x0001.ss0`; rerun the validator before any
  destructive cleanup or Phase 3 bulk run.
- TID0/TSV production branch timing remains one-off operational evidence, even
  though the exported final save bank is now verifier-backed.

### Inferred

- Timing values such as `375` and `700` frames are planning anchors derived
  from current route modeling; PRNG checkpoints remain stronger proof.

### Planned

- Full Phase 3 live lane generation is now observed once through the Python
  block builder for `0x0001.ss0`; output ZIP validation and native comparison
  remain separate checks.
- Mass hatching against the TID0/TSV save-bank, plus separate shiny and
  non-shiny ZIP subsets, remains planned until hatch outputs and manifests
  exist.

### Obsolete

- Treating roadmap scaffold scripts as proof of completed corpus generation is
  obsolete.
- Treating endpoint exceptions as normal organic first-half outputs is obsolete.

## Endpoint Coverage Status

As of 2026-04-26, all lower-half target values are represented when the
organic first-half lanes are counted with the two labeled endpoint exceptions:

- organic FR/LG live daycare lanes cover `0x0001..0xFFFE`
- raw CSV provenance still covers `0x0000..0xFFFF`
- `0x0000` is covered by the checksum-correct Day-Care Man RamScript bypass
  (`0xA3BB`) and verified pickup saves `<repo-root>\Artifacts\0x0 picked up.sav` plus
  `<repo-root>\Artifacts\picked up 2.sav`
- `0xFFFF` is covered by forced pending seed `0xFFFF`; the stock Day-Care Man
  path accepts it because it is nonzero, with `<repo-root>\Artifacts\working ffff.sav` as
  the current control/proof save

Do not treat endpoint exception files as organic daycare RNG hits. The flat
`<repo-root>\1sthalves` corpus still needs endpoint packaging cleanup if the
auditor is expected to report endpoint-complete pairs.

## TID0 / TSV Save-Bank Status

The later hatching proof uses a separate FR/LG save-bank with Trainer ID fixed
at `0`. Each save has a Secret ID that maps to one Trainer Shiny Value:

```text
TSV = (TID ^ SID) >> 3
```

Verified status as of 2026-05-06:

- saves folder:
  - `<repo-root>\TSVs`
- save naming:
  - `TSV-xxxx-sid-xxxxx.sav`
- numbering:
  - decimal TSV and decimal SID, not hex
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`
- verified complete:
  - `8192 / 8192`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`

The save whose `TSV` equals an egg's `PSV` should hatch that egg shiny. A
non-matching TSV save should hatch the same egg non-shiny. The planned mass
hatching stage will package those results as two separate ZIP subsets: shiny
and non-shiny.

Current hatch-splitter implementation:

- tool:
  - `<repo-root>\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj`
- tests:
  - `<repo-root>\tools\spinda\hatch_zip_splitter_tests\SpindaHatchZipSplitter.Tests.csproj`
- behavior:
  - streams Phase 3 egg ZIP entries without extracting loose PK3 files
  - parses TSV saves with PKHeX.Core by default
  - writes one shiny hatched ZIP and one not-shiny hatched ZIP
  - remains planned for production until the Phase 3 ZIP corpus is ready; the
    TSV save bank now exists and is verified

## Current Source Anchors

- [loaded-state anchor reload in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1640)
- [loaded-state frame-offset inference in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1774)
- [loaded-state `t-18` recovery in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1796)

## Files

- [spinda_frlg_common.py](spinda_frlg_common.py): shared FR/LG helpers for named RAM reads, lane path
  layout, save export, route execution, and manifest I/O
- [spinda_frlg_archive.py](spinda_frlg_archive.py): file-format helpers for raw lane blocks, presence
  bitmaps, and the top-level corpus manifest
- [frlg_spinda_first_half_lane.py](frlg_spinda_first_half_lane.py): phase-1 lane exporter that verifies the
  lower half from daycare RAM, then walks to the daycare man and exports
  `0x####.sav` named after the actual live daycare lower half
- [frlg_spinda_first_half_batch.py](frlg_spinda_first_half_batch.py): phase-1 batch runner for the
  first-half corpus; when opened from Qt it now defaults to the exact-seed
  loaded-state lane, using the premade post-seed route anchor
  `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0` plus `1 from egg - replay-metadata.json` so the run
  only targets the Spinda first halves that belong to that known-good initial
  seed. The wider full `firsthalf.csv` initial-seed brute-force path remains
  available as explicit `--seed-mode csv-bruteforce`, where targets are grouped
  by `initial_seed_16bit` so one initial-seed hit is reused for every
  compatible first-half target in that group. In both modes, the
  runner replays the two current input tapes starting from the organic PRNG
  state that the lane actually starts with, then checks
  `gRngValue` at `t-18`, searches nearby hit delays such as `17/18/19` from a
  pre-hit checkpoint, preferring host raw state, then Qt scratch state, then
  one file-backed scratch savestate, while reusing one pre-split set of nearby
  hit-delay tape variants across the run, resolves bounded LCRNG/LCRNG(R) drift
  around the CSV `t-0` state, converts CSV raw halves to the live FR/LG daycare
  lower-half formula `((Random() % 0xFFFE) + 1)`, de-duplicates the two FR/LG
  wraparound raw-half collisions onto their actual live results, tighten the
  loaded-state route model by learning the post-seed anchor's frame offset from
  the first `t-18` mismatch and reusing it instead of rewriting `gRngValue`
  back to the CSV seed, verify daycare RAM, immediately save a matching
  pre-daycare-man `.ss0` state after the lower half is hit, and then write
  saves under `<repo-root>\1sthalves`. Normal live-output mode de-duplicates to
  the practical live range `0x0001..0xFFFE`; raw-CSV mode preserves all
  `65536` CSV lower-half rolls while naming main
  `saves\0x####.sav` and `states\0x####.ss0` files by the
  converted live FR/LG daycare half. The two wraparound duplicate raw rows are
  kept under `_live_name_collisions` with `__raw0x####` suffixes.
  At startup, it now writes `_resume_status.json` after scanning existing
  artifacts. A target is skipped only when the `.sav`, pre-daycare-man `.ss0`,
  and manifest all exist and the manifest still matches the expected paths,
  target values, initial seed, and SHA-1 hashes. Crash-cut targets are retried
  on the next run instead of being treated as complete.
  The locked baseline checkpoint itself remains a title-screen calibration
  artifact for `Seed-Bruteforcer.py` and `Seed-Replicator.py`; the batch runner now
  rejects it as `--first-half-state` because it is not a valid post-seed route
  anchor.
  The currently copied route CSV references are tracked in
  `INITIAL_SEED_CSV_REFERENCE.md`.
  That matters operationally because the copied `firsthalf.csv` may describe a
  different single-seed route lane than the older `0x26CD` loaded-state anchor.
  Manifest hashes for unchanged inputs are cached per run/per seed group, and
  empty target selections exit before loading the emulator.
- [Egg-First-Half-Hitter.py](Egg-First-Half-Hitter.py): operator
  wrapper around `frlg_spinda_first_half_batch.py` for the current verified
  `0xFBC7` post-seed lane under `<repo-root>\live-lanes\live-fbc7-lane16`. It first
  preserves `1 from egg.ss0` as the read-only `1 from egg - clean-backup.ss0`
  if that backup is missing, then runs the loaded-state batch path against the
  matching `firsthalf.csv` rows. With no `--target-half`, it preserves all
  raw CSV first-half targets and now stores live-name
  `saves\0x####.sav` plus `states\0x####.ss0` outputs,
  moving only duplicate live-half collisions under
  `_live_name_collisions`; with `--target-half 0x####`, it targets
  exactly that live daycare half. It inherits the batch runner's
  organic-RNG anchor reload, `t-18` validation, pre-hit checkpoint reuse,
  nearby hit-delay retry, pre-daycare-man savestate capture, and bounded drift
  handling. It does not patch RAM to force the seed or `gRngValue`.
  For full loaded-state sweeps it now sorts targets by CSV `t-18` frame and
  advances one checkpointed post-setup runway forward, restoring a validated
  per-target `t-18` checkpoint after each export branch. That avoids replaying
  the setup tape and the full neutral wait from the anchor for every target
  while still recovering from drift through the preventative checkpoints.
- [frlg_spinda_lane_workspace.py](frlg_spinda_lane_workspace.py): create or inspect the canonical lane
  manifest/block/save paths without launching the emulator
- [frlg_spinda_second_half_lane.py](frlg_spinda_second_half_lane.py): phase-2 sweep scaffold for upper-half
  targets, block writes, and resume-aware manifest updates, including a
  stronger `next_upper_half` hint based on what is still actually missing
- [Build-Phase2-Pickup-States.py](Build-Phase2-Pickup-States.py): Phase 2 bridge that loads
  first-half `0x####.sav` files, reproduces the `secondhalf.csv` title seed,
  replays the seed-to-pre-pickup bridge tape, pads to a configurable baseline
  frame defaulting to `700`, validates `gRngValue`, and writes
  `<repo-root>\Phase2PickupStates\0x####.ss0`. On launches after the
  2026-04-26 crash-hardening patch, each savestate is first written as
  `0x####.ss0.tmp`, size-checked against the expected mGBA state size
  (`397312` bytes by default), then renamed into place; resume skips only
  final `.ss0` files with the expected size and rebuilds partial or bad-size
  finals. The ACE endpoint lane
  `0x0000.sav` automatically uses `<repo-root>\0x0000 special tape.json`
  instead of the normal bridge tape; no CLI flag is needed for that exception.
  For monitorable relaunches, `--no-unbounded-fast-forward` keeps Audio
  killswitch/no-render setup but disables the live fast-forward toggle. While
  the builder is active, use
  `<repo-root>\tools\spinda\phase2_pickup_runtime_control.py --human-check`
  instead of changing the mGBA Custom Features checkboxes directly; the builder
  applies that control file between save jobs, stops cleanly, and leaves mGBA
  open for inspection. Run `--performance` before relaunching to continue.
- [Build-Phase3-Spinda-Block.py](Build-Phase3-Spinda-Block.py): Phase 3 pilot
  lane builder for `0x0001.ss0` by default. It requires visible Qt mGBA,
  streams `secondhalf.csv` `t-0` pickup rows, presses `A` before the CSV
  pickup `Random()` state using default `--pickup-input-lead-frames 3`, sweeps
  targets from the loaded state's actual `gRngValue` by default
  (`--schedule-source runtime-rng`) while still using `secondhalf.csv` as the
  target/metadata authority. The older direct CSV frame schedule remains
  available as `--schedule-source csv-frame` for debugging; the visible
  `0x0001` pilot proved it does not match the current state. The script stores
  boxed 80-byte PK3 records and the completed ZIP byte stream in RAM, then
  atomically writes one ZIP containing only PID-named `.pk3` entries. Its CSV
  path uses positional `csv.reader`
  so non-`t-0` rows are skipped cheaply, and its extraction path reads only the
  80 boxed bytes it stores, dropping the final 20 party-only bytes. It caches
  parsed CSV targets and runtime pickup schedules under
  `Phase3SpindaBlocks\_cache`, writes status only at start/failure/final ZIP
  completion, and does not emit loose `.pk3` files outside the ZIP. Audio killswitch,
  no-render, and unbounded fast-forward now default on for this Phase 3 run
  type; use the `--disable-*` flags for monitoring.
- Native Qt Spinda Project: `Tools > Custom Features > Spinda project...`
  reimplements the Phase 3 lane builder in the Qt frontend. It uses direct C++
  `core->runFrame(core)` calls, in-RAM scratch savestates, direct party-slot
  memory reads, native target/schedule caches under
  `Phase3SpindaBlocks\_cache` by default, dynamic pickup detection to avoid the
  old fixed 24-frame post-pickup wait, PID-only checks during detection,
  learned pickup-delay mode after a stable sample window, timing buckets in
  final status, reused hot-loop buffers, and the same final PID-named `.pk3`
  ZIP layout. Native autorun can override the cache with
  `MGBA_SPINDA_NATIVE_PHASE3_CACHE_DIR` and can run without showing the main
  window or Spinda dialog with `MGBA_SPINDA_NATIVE_PHASE3_HEADLESS=1`. Full
  native `0x0001` output still needs a comparison against the Python Phase 3
  ZIP before treating it as replacement proof.
- [native_phase3_worker_pool.py](../../../tools/spinda/native_phase3_worker_pool.py):
  launches Phase 3 workers for one or more lanes. Production defaults to the
  standalone `mgba-spinda-phase3.exe` CLI runner, assigns isolated
  `MGBA_WORKER_INSTANCE` names, caps concurrent processes with `--workers`,
  skips existing lane ZIP filenames without opening them, and writes
  `_native_phase3_worker_pool_status.json`. `--runner qt` remains available for
  visual native-Qt inspection.
- [frlg_spinda_corpus_manifest.py](frlg_spinda_corpus_manifest.py): create or update the top-level global
  manifest for resume state
- [frlg_spinda_recipe_lint.py](frlg_spinda_recipe_lint.py): offline recipe checker for first-half and
  second-half JSON route files
- [frlg_spinda_workspace_audit.py](frlg_spinda_workspace_audit.py): offline workspace checker for save/state
  hashes, canonical lane paths, block/bitmap readability, and manifest
  consistency
- [<repo-root>\tools\spinda\first_half_raw_csv_audit.py](../../../tools/spinda/first_half_raw_csv_audit.py):
  read-only checker for the live `1sthalves` first-half output folder; counts
  `.sav` / `.ss0` pairs, missing sides, absent targets, duplicate entries, bad
  names, and bad settled sizes. It streams directory entries into compact
  target sets so live scans stay low-overhead as the folder grows, and
  auto-detects both flat output and the current split `saves` / `states`
  folders.
- [<repo-root>\tools\spinda\first_half_raw_csv_monitor.py](../../../tools/spinda/first_half_raw_csv_monitor.py):
  read-only progress/ETA watcher for the same live `1sthalves` folder
- [<repo-root>\tools\spinda\first_half_progress_web.py](../../../tools/spinda/first_half_progress_web.py):
  Flask status dashboard with `/api/status` JSON and `/events` SSE updates,
  modeled after the copied pokebot web UI pattern but limited to read-only
  filesystem progress data. It shares one throttled scan cache between API and
  SSE clients, defaulting to one scan per second through `--sample-interval`.
  It binds to `0.0.0.0` by default and displays the detected local LAN IPv4 URL
  instead of `localhost`.
- [<repo-root>\tools\spinda\phase2_pickup_progress_web.py](../../../tools/spinda/phase2_pickup_progress_web.py):
  Phase 2 pickup-state dashboard for `<repo-root>\Phase2PickupStates`,
  with `/api/status`, `/events`, and a visible `complete / 65536` counter. It
  treats `0x####.ss0.tmp` files as unsettled writes instead of completed
  states.
- [<repo-root>\tools\spinda\phase2_pickup_state_validator.py](../../../tools/spinda/phase2_pickup_state_validator.py):
  read-only Phase 2 pickup-state validator for missing states, bad-size final
  `.ss0` files, stale `.ss0.tmp` files, bad names, duplicate-looking state
  variants, and optional post-run sample `gRngValue` checks in a separate
  host-side core.
- [<repo-root>\tools\spinda\fix_first_half_raw_csv_names.py](../../../tools/spinda/fix_first_half_raw_csv_names.py):
  host-side venv tool that renames completed raw-CSV first-half `.sav` and
  `.ss0` artifacts to live FR/LG daycare-half names through a temporary-name
  phase. Duplicate raw targets are preserved under `_live_name_collisions`.
- [frlg_spinda_export.py](frlg_spinda_export.py): export one `.pk3`, one lane directory of
  loose `.pk3` files, one full lane ZIP, one upper-half range ZIP, or one nested
  ZIP archive from the raw block files, while rejecting empty nested exports and
  invalid ranges before writing output
- [first_half_recipe_template.json](first_half_recipe_template.json): template for the recipe file that
  [frlg_spinda_first_half_lane.py](frlg_spinda_first_half_lane.py) expects
- [second_half_recipe_template.json](second_half_recipe_template.json): template for the phase-2 upper-half
  sweep recipe
- [SCRIPT_DOCUMENTATION.md](SCRIPT_DOCUMENTATION.md): detailed documentation for every roadmap Python
  file, recipe, manifest, and archive format in this folder
- [Spinda Hatch ZIP Splitter](../../../tools/spinda/hatch_zip_splitter/README.md): standalone
  PKHeX.Core tool for the planned shiny/not-shiny hatched ZIP proof stage

## Why PRNG checkpoints matter here

The walk from the daycare building to the daycare man is not something we want
to validate purely with frame counts. There can be slight RNG noise in that
area from NPC activity, so the first-half lane recipe supports route steps that
wait for or validate the live FR/LG PRNG state.

That means the route file can say:

- hold a movement input for a fixed number of frames when that part is stable
- or keep advancing until `gRngValue` reaches a known checkpoint when the area
  is noisy

## Conservative timing anchors

The current scaffold also carries two conservative planning estimates:

- about `375` frames from seed generation to the first half of the egg PID
- about `700` frames from seed generation to receiving the egg itself

These numbers are intentionally conservative because Four Island NPC movement
can add slight RNG noise. They are useful for route search and recipe planning,
but the actual correctness checks in this scaffold still prefer PRNG-state
checkpoints over raw frame counts wherever the area is noisy.

## Status

This folder now has real scaffolding for both phases, but the actual route
content is still placeholder data. The remaining hard work is supplying and
validating authentic FR/LG schedules in the emulator.

The CUDA route model is now treated as independently accuracy-confirmed by the
project owner. The remaining uncertainty in this folder is route execution
against real saves/savestates, not whether the CUDA model itself should be
considered speculative.

The emulator-facing scripts in this folder now support both of the local Python
execution paths:

- host-side execution through `<repo-root>\.venv-mgba\bin\python.exe`
- runtime Qt execution from `Tools > Scripting...`

In the Qt path, the scripts can load the ROM and related save/state files into
the visible window themselves. They do not have to be launched only at boot.

The newest offline utilities are there to reduce risk before that stage:

- lint the recipe JSON before a run
- audit the workspace after an interruption
- keep the resume metadata and lane artifacts consistent without booting mGBA

## Current Operational Decisions

The current workflow should be read with these explicit assumptions:

- the active full first-half run uses `Egg-First-Half-Hitter.py` from
  `<repo-root>\build-mingw64-python-qt\mGBA.exe`
- future completed runs store live-half keyed pairs directly under
  `<repo-root>\1sthalves\saves` and
  `<repo-root>\1sthalves\states`
- duplicate raw collisions are preserved under
  `<repo-root>\1sthalves\_live_name_collisions`
- the active anchor is `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0`
- `firsthalf.csv` is the current route-CSV bridge file; it is not the same
  thing as the older `frlg_routes.csv` summary output from the CUDA repo
- the current copied `firsthalf.csv` can describe a different seed lane than
  the loaded-state anchor under `<repo-root>`
- loaded-state mode keeps the anchor's organic `gRngValue`; it does not patch
  emulator RAM back to the CSV seed
- `secondhalf.csv` targets daycare pickup upper PID halves. After the
  2026-04-25 CUDA fix, `t-0` is the pickup `Random()` state; IV output labels
  `A` as male parent, `B` as female parent, and numeric IVs as RNG base values.
  Current regenerated copy has SHA-256
  `81A8CC4DDA4117268B06CE082F98717EBE8A5586E0B50567A8FD92DC050A1AD9`.
- the current `0xFBC7` title replay has organic `rng_at_seed=0x94FCE3E3`,
  which matches PRNG-origin seed `0xFB91` plus two LCRNG steps, so the hitter
  auto-generates/uses `firsthalf-prng-FB91.csv` for loaded-state exports
- do not rebuild or delete `<repo-root>\build-mingw64-python-qt` while that live
  run is active
- loaded-state drift recovery now sizes its search from the planned post-setup
  route span plus the local recovery window, then lands on a validated
  preventative `t-18` checkpoint before any daycare-man input suffix
- the first-half hitter writes a `running` status before emulator work and an
  error JSON with a traceback if the visible Qt runner raises before export
- `1 from egg - locked-baseline` is a title-screen calibration artifact, not a valid
  post-seed route anchor for the batch runner
- the current route consumer still expects at least `t-18`, which is why
  `T-minus 18` is the current minimum practical compatibility floor

## VS Code Note

- These are normal relative Markdown links on purpose.
- In VS Code, clicking them from the editor or Markdown preview should open the referenced file inside the workspace.


