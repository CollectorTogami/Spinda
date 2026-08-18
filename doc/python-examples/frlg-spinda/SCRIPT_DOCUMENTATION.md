# FR/LG Spinda Script Documentation

## Status Bucket

- Current status: Active detailed script/file-format reference for local FR/LG Spinda helpers.
- Last verified date: 2026-05-05.
- Proven artifacts: listed scripts, recipe templates, manifest formats, audit tools, and source tests.
- Known gaps: Scaffold and plan sections are not proof of full pipeline completion.
- Next action: Update this file with every script interface, artifact format, manifest, or pipeline status change.
- Evidence model: Claims must be labeled as `Proven`, `Observed once`, `Inferred`, `Planned`, or `Obsolete`; see `DOCUMENTATION_EVIDENCE_POLICY.md`.

This file documents the Python roadmap scripts that currently exist in
[<repo-root>\doc\python-examples\frlg-spinda](../frlg-spinda).

The goal of this fileation is simple:

- explain what each file does
- explain what files it reads and writes
- explain how the pieces fit together
- make the future emulator-bound work easier once the real save files and route
  tables are ready

This is documentation for the **current scaffold**, not a claim that the whole
Spinda pipeline is already finished.

## Decision Trail

The current documentation no longer assumes the reader already knows why the
project made certain workflow choices.

Use these paired references while reading this file:

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

Those files record the reasons behind the decisions that this script reference
depends on:

- Timer 1 is still the authoritative initial-seed source
- `gRngValue` and LCRNG/LCRNG(R) inference are secondary checks
- the copied route CSV and the loaded-state anchor are not implicitly the same
  lane
- `t-18` is still the current first-half route-validation checkpoint

## Evidence Split

### Proven

- Listed scripts, JSON templates, manifest formats, archive helpers, and audit
  tools exist in the workspace.
- Source tests cover maintained helper behavior and raw-CSV audit behavior.
- Runtime Qt entrypoints exist for the maintained emulator-facing scripts.
- `Build-Phase3-Spinda-Block.py` exists with source tests for the `0x0001`
  pilot path: `secondhalf.csv` parsing, pickup A-press lead timing,
  in-memory scratch-state sweeping, runtime-RNG scheduling, PID mismatch
  rejection, and PID-named `.pk3` ZIP output.
- A separate `mgba-spinda-phase3.exe` CLI LTO entrypoint exists for headless
  Phase 3 proof runs without Qt or Python in the hot path.
- The Phase 4 TID0/TSV save bank is complete and verifier-backed for all
  `8192` exported saves.

### Observed once

- Current endpoint proof saves show that `0x0000` and `0xFFFF` can be obtained
  through their labeled exception paths.
- The Phase 2 pickup-state folder was later observed as complete and valid for
  all `65536` states, including `0x0001.ss0`; rerun the validator before any
  destructive cleanup or Phase 3 bulk run.
- TID0/TSV production branch timing remains one-off operational evidence, even
  though the exported final save bank is now verifier-backed.

### Inferred

- Conservative route timing anchors are route-planning estimates, not final
  correctness checks.
- CUDA-side model accuracy is treated as owner-confirmed context here; this doc
  still requires emulator-side artifacts before claiming pipeline completion.

### Planned

- Full Phase 3 live lane generation remains planned until the new visible-Qt
  block builder runs against `0x0001.ss0` and the output ZIP validates clean.
- The mass hatching proof layer remains planned until shiny and non-shiny hatch
  outputs exist as separate ZIP subsets with manifests.

### Obsolete

- Any text that implies scaffold presence equals completed corpus generation is
  obsolete.
- Any text that treats raw endpoint exceptions as organic Day-Care RNG lanes is
  obsolete.

## Current Source Anchors

- [loaded-state anchor reload in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1640)
- [loaded-state frame-offset inference in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1774)
- [loaded-state `t-18` recovery in `frlg_spinda_first_half_batch.py`](frlg_spinda_first_half_batch.py#L1796)

## Big Picture

The roadmap scripts are split into three layers:

1. emulator-facing helpers and runners
2. file-format and resume-state helpers
3. export tools

The current design assumes the following long-term flow:

1. create one lower-half lane save
2. reuse that lane save to sweep upper-half targets
3. keep the per-lane PK3 records in RAM during generation
4. build the final ZIP byte stream in RAM
5. write one ZIP per lower-half lane containing only PID-named `.pk3` entries
6. use the TID0/TSV save-bank to hatch proof outputs as shiny and non-shiny
7. package hatch proof outputs into separate shiny and non-shiny ZIP subsets

That split matters because it keeps the expensive emulator work separate from
the cheap archive/export work. Older raw block helpers still exist for legacy
workspace experiments, but Phase 3 production ZIPs are now explicit `.pk3`
archives.

## Quick Links

- [spinda_frlg_common.py](spinda_frlg_common.py)
- [spinda_frlg_archive.py](spinda_frlg_archive.py)
- [frlg_spinda_first_half_lane.py](frlg_spinda_first_half_lane.py)
- [frlg_spinda_first_half_batch.py](frlg_spinda_first_half_batch.py)
- [Egg-First-Half-Hitter.py](Egg-First-Half-Hitter.py)
- [Build-Phase2-Pickup-States.py](Build-Phase2-Pickup-States.py)
- [Build-Phase3-Spinda-Block.py](Build-Phase3-Spinda-Block.py)
- [frlg_spinda_lane_workspace.py](frlg_spinda_lane_workspace.py)
- [frlg_spinda_second_half_lane.py](frlg_spinda_second_half_lane.py)
- [frlg_spinda_corpus_manifest.py](frlg_spinda_corpus_manifest.py)
- [frlg_spinda_recipe_lint.py](frlg_spinda_recipe_lint.py)
- [frlg_spinda_workspace_audit.py](frlg_spinda_workspace_audit.py)
- [frlg_spinda_export.py](frlg_spinda_export.py)
- [first_half_recipe_template.json](first_half_recipe_template.json)
- [second_half_recipe_template.json](second_half_recipe_template.json)
- [<repo-root>\tools\spinda\first_half_raw_csv_audit.py](../../../tools/spinda/first_half_raw_csv_audit.py)
- [<repo-root>\tools\spinda\first_half_raw_csv_monitor.py](../../../tools/spinda/first_half_raw_csv_monitor.py)
- [<repo-root>\tools\spinda\first_half_progress_web.py](../../../tools/spinda/first_half_progress_web.py)
- [<repo-root>\tools\spinda\phase2_pickup_progress_web.py](../../../tools/spinda/phase2_pickup_progress_web.py)
- [<repo-root>\tools\spinda\phase2_pickup_state_validator.py](../../../tools/spinda/phase2_pickup_state_validator.py)
- [<repo-root>\tools\spinda\fix_first_half_raw_csv_names.py](../../../tools/spinda/fix_first_half_raw_csv_names.py)
- `<repo-root>\markdown-files\FIRST_HALF_RAW_CSV_AUDIT_AND_SECOND_HALF_PLAN.md`
- `<repo-root>\markdown-files\PHASE3_SPINDA_BLOCK_BUILDER.md`
- [<repo-root>\markdown-files\FRLG_TSV_SAVE_BANK_PLAN.md](../../../docs/FRLG_TSV_SAVE_BANK_PLAN.md)
- [<repo-root>\doc\python-examples\frlg-tsv-save-bank\README.md](../frlg-tsv-save-bank/README.md)

## Conservative timing anchors

The scaffold now carries two conservative timing assumptions for route planning:

- about `375` frames from seed generation to the lower PID half being set
- about `700` frames from seed generation to receiving the egg itself

These are intentionally conservative estimates for Four Island. NPC movement in
that area can perturb the route slightly, so these values are best treated as
planning anchors for GPU search and recipe drafting, not as final correctness
checks. The scripts still prefer PRNG-state checkpoints wherever the route is
known to be noisy.

Project status note:

- the CUDA route model accuracy has now been independently confirmed by the
  project owner
- remaining route risk is emulator-bound validation against the real save,
  savestate, and PRNG checkpoints

Endpoint status note:

- organic first-half scripts still produce only live daycare lower halves
  `0x0001..0xFFFE`
- raw CSV provenance still spans all `0x0000..0xFFFF` target halves
- `0x0000` and `0xFFFF` are now covered as labeled ACE endpoint exceptions,
  not organic daycare RNG hits
- `0x0000` works by installing the checksum-correct Day-Care Man `RamScript`
  (`0xA3BB`) and calling `GiveEggFromDaycare` after the "Take good care of it"
  message
- `0xFFFF` works through the stock Day-Care Man path after forcing pending
  daycare seed `0xFFFF`, because the pending check only rejects zero
- endpoint archive cleanup is still separate from the raw-CSV folder audit

## Runtime Execution Modes

The emulator-facing scripts now support two local execution styles:

1. host-side execution from `<repo-root>\.venv-mgba\bin\python.exe`
2. runtime execution from `Tools > Scripting...` in the visible Qt GUI

In host-side mode, a script creates its own core with `mgba.core.load_path(...)`.

In Qt runtime mode, the same script can be loaded mid-session and then use the
live Qt bridge to:

- load the requested ROM into the visible window
- load the requested save or savestate into that same visible core
- export the current save from that visible core

That is the main change from the earlier scaffold stage: the emulator-facing
roadmap scripts are no longer limited to startup-script use.

## Canonical Workspace Layout

The scripts use one workspace root that contains four main subdirectories:

```text
workspace/
  saves/
    0x0001.sav
    0x0002.sav
    ...
  states/
    0x0001.state
    0x0002.state
    ...
  blocks/
    0x0000.bin
    0x0000.bin.bitmap
    0x0001.bin
    0x0001.bin.bitmap
    ...
  manifests/
    global.json
    0x0000.json
    0x0001.json
    ...
```

Meaning:

- `saves/0x####.sav`
  - one exported lower-half archive save per lane
- `states/0x####.state`
  - one reusable work savestate for the upper-half sweep of that lane
- `blocks/0x####.bin`
  - one raw `65536 * 80` payload per lane
- `blocks/0x####.bin.bitmap`
  - one compact bitmap that says which upper-half slots are already present
- `manifests/0x####.json`
  - one lane manifest that describes the state of that lane
- `manifests/global.json`
  - one top-level manifest for the whole corpus run

## Offline Safety Tools

Two newer scripts exist specifically to reduce operator mistakes before or
between emulator-backed runs:

1. recipe linting
2. workspace auditing
3. raw-CSV first-half output auditing
4. raw-CSV first-half ETA monitoring
5. raw-CSV first-half Flask dashboard

They do not prove a route is correct, but they do catch common structural
mistakes that would otherwise waste time:

- duplicate second-half targets
- empty route segments that were meant to be filled later
- obvious PID mismatches in the second-half recipe
- stale or mismatched save/state hashes
- block/bitmap counts that no longer match the lane manifest
- incomplete live `1sthalves` `.sav` / `.ss0` pairs
- bad raw-CSV filenames or settled file sizes

The raw-CSV auditor and monitor live under `<repo-root>\tools\spinda`. They only
list and stat files. They do not open the emulator, write manifests, or repair
the active output folder. The auditor streams directory entries into compact
target sets instead of retaining one Python object per file, which keeps
repeated scans cheap during the live `65536`-pair run.

The name-fix tool in the same folder is intentionally different: it mutates the
completed first-half corpus after the live run is done. It renames raw CSV
`Random()` half filenames to live FR/LG daycare-half filenames by first moving
each source to a temporary name, then moving each temp to its final destination.
That temp phase prevents cyclic rename overwrite during the `0x0000 -> 0x0001`
style shift. Duplicate raw targets are preserved under `_live_name_collisions`.

The Flask dashboard follows the copied pokebot web UI shape at a smaller scope:
serve one browser page, expose `/api/status` JSON, and push live updates through
`/events` as Server Sent Events. It samples the same read-only audit data and
the hitter status JSON. JSON and SSE requests share an in-process scan cache;
`--sample-interval` defaults to `1.0` second and bounds filesystem overhead
when multiple browser clients are open.

## Important File Formats

## 1. Lane save

Example:

- `saves/0x1234.sav`

Meaning:

- one FR/LG save file where the daycare state is already locked to lower-half
  `0x1234`

How it is produced:

- the first-half lane script loads a canonical save and canonical base
  savestate
- it runs the lower-half route
- it verifies the daycare lower half from RAM
- it walks to the daycare man
- it performs an in-game save
- it exports the live save data and writes the `.sav`

## 2. Lane work state

Example:

- `states/0x1234.state`

Meaning:

- one savestate used as the canonical reload point for sweeping upper halves for
  lane `0x1234`

This is not the archive object. It is the runtime convenience object.

## 3. Lane block

Example:

- `blocks/0x1234.bin`

Meaning:

- one headerless payload with exactly `65536 * 80 = 5,242,880` bytes

Index math:

- upper-half `0x0000` lives at bytes `0..79`
- upper-half `0x0001` lives at bytes `80..159`
- ...
- upper-half `N` lives at `N * 80`

Stored object:

- the canonical 80-byte boxed Gen 3 Pokemon record

## 4. Lane bitmap

Example:

- `blocks/0x1234.bin.bitmap`

Meaning:

- a compact `65536`-bit presence map
- one bit per upper-half slot

Purpose:

- fast resume logic
- fast export sanity checks
- avoid assuming a zero-filled 80-byte block is necessarily "missing"

## 5. Lane manifest

Example:

- `manifests/0x1234.json`

Meaning:

- metadata for one lane

Current fields include:

- lane id
- canonical save/state/block paths
- observed lower half
- observed PRNG checkpoints around the noisy walk/save segment
- next upper half
- completed upper-half count
- completion flag
- route-step telemetry

## 6. Global manifest

Example:

- `manifests/global.json`

Meaning:

- the current corpus stage and broad resume state

Current fields include:

- stage
- current lane
- current upper half
- next lane
- completed-lane count
- notes

## Recipe Files

Two recipe templates exist:

- [first_half_recipe_template.json](first_half_recipe_template.json)
- [second_half_recipe_template.json](second_half_recipe_template.json)

These templates are intentionally placeholders. They document the expected
shape of the route input files before the real route schedules are plugged in.

## PRNG-State Route Design

One important design choice is already locked:

- the walk from the daycare building to the daycare man is not validated purely
  by frame count

Why:

- that area can contain slight RNG noise from NPC activity

So the route system supports two kinds of steps:

1. fixed-frame steps
2. PRNG-state steps

A PRNG-state step says:

- hold these inputs
- advance until `gRngValue` equals a known checkpoint
- fail if that does not happen inside `max_frames`

That lets the route definition say "stop when the real game state is right"
instead of "hope that N frames always means the same thing."

## Script-by-Script Documentation

## `spinda_frlg_common.py`

This is the shared foundation layer.

What it does:

- defines FR/LG addresses used by the roadmap scripts
- defines route data structures
- defines recipe and manifest data structures
- gives named readers for:
  - current PRNG state
  - SaveBlock1 pointer
  - daycare lower half
  - daycare step counter
  - party slot bytes
  - boxed 80-byte record bytes
- gives savestate helpers
- gives live `.sav` cloning from the core
- gives runtime-safe save export through the visible Qt bridge
- gives route execution helpers with PRNG checkpoints

Important classes:

- `RouteStep`
  - one route segment
  - either `frames` or `wait_for_rng`
- `RouteStepResult`
  - telemetry for one executed route step
- `FirstHalfRecipe`
  - the recipe model for one lower-half lane export
- `LanePaths`
  - canonical file locations for one lane
- `LaneWorkspaceManifest`
  - persistent lane metadata

Important helpers:

- `load_gba_core(...)`
  - load a ROM and optionally a temporary save
  - in Qt runtime mode, this now reloads the requested ROM into the visible
    window instead of creating a second hidden emulator core
- `clone_save_data(...)`
  - export the live save RAM as raw `.sav` bytes
- `export_save_file(...)`
  - write the live save to disk in either host-side or Qt runtime mode
- `read_daycare_lower_half(...)`
  - read the lower-half personality value from daycare RAM
- `run_route(...)`
  - execute a sequence of `RouteStep` values
  - fixed-frame segments now batch through the native Qt bridge when that fast
    path is available
- `wait_for_rng_state(...)`
  - the core helper for noisy segments that must be keyed to PRNG state

Why this file matters:

- it keeps the FR/LG memory knowledge in one place
- it keeps route execution and path logic consistent across phase 1 and phase 2

## `frlg_spinda_first_half_lane.py`

This is the phase-1 lane exporter.

What it is for:

- create one lower-half archive save

Expected inputs:

- a first-half recipe JSON file
- a ROM path
- optionally a base save file
- a base savestate

Big-picture flow:

1. resolve canonical lane paths
2. load the ROM and base save
3. load the canonical pre-step savestate
4. run the pre-generation route
5. verify the lower half from daycare RAM
6. run the post-generation route
7. run the save sequence
8. export the live save data
9. write `0x####.sav`
10. optionally write a lane work state
11. write the lane manifest

Important detail:

- it records the observed PRNG before the walk, after the walk, and after the
  save

That is important because the noisy segment has to be checked by PRNG state,
not only by timing.

Runtime usage:

- this script now exposes `run_recipe_file(...)`
- in the Qt scripting window, it can be loaded mid-session and then pointed at
  a real recipe from the prompt
- if `MGBA_SPINDA_FIRST_HALF_RECIPE` is set, or
  `first_half_recipe.json` exists beside the script, it can auto-run there too

## `Egg-First-Half-Hitter.py`

This is the current operator entrypoint for hitting first personality-value
halves from the `0xFBC7` post-seed lane.

What it is for:

- use `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0` as the current post-seed
  route anchor
- use the copied `firsthalf.csv` rows as the timing/PRNG anchor for the target
  live daycare lower half
- run the full raw CSV first-half set without replaying the full title-screen
  seed search for every attempt

Expected inputs:

- `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0`
- `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.sav`
- `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg - replay-metadata.json`
- `<repo-root>\build-mingw64-python-qt\firsthalf.csv`
- `tape seed to step 1.json`
- `hit 1st half walk to daycare man.json`

Safety behavior:

- creates `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg - clean-backup.ss0` if that
  backup does not exist yet
- marks the clean backup read-only and never overwrites an existing backup
- reports SHA-1 values for both the active anchor and the preserved backup
- warns if the active anchor later differs from the preserved backup

Execution behavior:

- delegates to `frlg_spinda_first_half_batch.py` in `loaded-state` mode
- keeps the savestate's organic `gRngValue`; it does not patch RAM to force a
  seed or PRNG state
- when replay metadata has a `prng_discerned_seed` that differs from the Timer
  1 `target_seed`, auto-generates/uses an organic PRNG-origin CSV such as
  `firsthalf-prng-FB91.csv` so loaded-state checkpoints match the savestate
- reuses the batch runner's post-seed anchor reloads, `t-18` validation,
  pre-hit checkpoint, nearby hit-delay variants, and bounded drift checks
- for full sweeps, uses the optimized loaded-state runway: replay setup once,
  checkpoint the post-setup route, process targets in CSV `t-18` order, and
  restore a validated per-target `t-18` checkpoint after each export branch
- with no `--target-half`, scans the current CSV lane and preserves all raw
  CSV lower-half targets
- with `--target-half 0x####`, tries exactly that live daycare lower half
- writes raw-CSV full-sweep outputs as `saves\0x####.sav` plus
  matching `states\0x####.ss0` states, where `0x####` is the live
  FR/LG daycare half already stored inside the file
- preserves the two duplicate raw CSV wrap collisions under
  `_live_name_collisions` with `__raw0x####` suffixes
- saves each `.ss0` immediately after the target daycare lower half is present
  in RAM and before replaying the remaining daycare-man walk/save tape
- writes `_egg_first_half_hitter_status.json` beside the first-half outputs,
  first as `run_status: running` and then as `run_status: finished`
- clears stale `_egg_first_half_hitter_error.json` at the start of a new run
- if Qt raises an exception, writes `_egg_first_half_hitter_error.json` with
  the exception type and traceback before mGBA shows its generic error dialog
- when launched through Qt, optional CLI-style overrides can be supplied with
  `MGBA_EGG_FIRST_HALF_HITTER_ARGS`, which is useful for smoke runs such as
  `--limit 1` without editing the script
- on Windows, that environment hook preserves normal `C:\...` paths instead
  of treating backslashes as POSIX escapes

The script is intentionally small. All emulator-route mechanics remain in the
batch runner so the operator wrapper and the bulk path keep the same drift,
checkpoint, and pre-daycare-man savestate behavior.

Current `0xFBC7` lane note:

- Timer 1 remains the title-hit source-of-truth: `0xFBC7`
- replay metadata records `rng_at_seed=0x94FCE3E3`
- that organic PRNG state corresponds to `prng_discerned_seed=0xFB91` plus two
  LCRNG steps
- therefore the wrapper defaults to `firsthalf-prng-FB91.csv` for loaded-state
  first-half exports instead of forcing RAM onto the copied `0xFBC7` CSV orbit
- the completed full raw-CSV run stores live-half `0x####.sav` under
  `<repo-root>\1sthalves\saves` and matching `0x####.ss0` states
  under `<repo-root>\1sthalves\states`
- duplicate raw collisions live under
  `<repo-root>\1sthalves\_live_name_collisions`
- the active executable tree is `<repo-root>\build-mingw64-python-qt`; do not
  rebuild or delete it while that run is active

## Raw-CSV Audit And ETA Tools

Use the point-in-time auditor:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\first_half_raw_csv_audit.py
```

Use the ETA monitor:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\first_half_raw_csv_monitor.py --watch
```

Use the web dashboard:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\first_half_progress_web.py
```

Then open:

```text
http://<local-LAN-IP>:233/
```

Both tools default to `<repo-root>\1sthalves`. They are safe to run
while mGBA is producing files because they only list directory entries and read
file metadata. Very recent files are classified as `unsettled` for size checks
so a file being written is not treated as corrupted. The auditor auto-detects
both the old flat layout and the current split layout:

- `<repo-root>\1sthalves\saves\0x####.sav`
- `<repo-root>\1sthalves\states\0x####.ss0`

The raw-CSV auditor is endpoint-aware. It reports organic live lanes
`0x0001..0xFFFE` separately from the labeled ACE endpoint exceptions
`0x0000` and `0xFFFF`. Endpoint files belong under:

```text
<repo-root>\1sthalves\_endpoint_exceptions\saves
<repo-root>\1sthalves\_endpoint_exceptions\states
```

It also reports complete save/state pairs, missing sides, bad target naming,
bad settled sizes, duplicate targets, and optional manifest SHA-1 mismatches
with `--check-hashes`. This keeps the known endpoint exception gap separate
from real organic-lane holes.

The web dashboard uses the same safe data path. It does not expose controls that
can mutate mGBA or the first-half corpus. Its default one-second shared scan
cache is only for UI overhead control; the hitter's save/state files remain the
source-of-truth. The server binds to `0.0.0.0` by default and displays the
detected LAN IPv4 URL in stdout, `/api/status`, SSE payloads, and the dashboard
Run facts panel. Use `--display-host <ip-address>` when Windows has multiple
active adapters and the automatic choice is not the address you want to show.
The default port is `233`, matching the project's `2^33` Spinda-plus-shiny
mnemonic.

The second-half handoff plan is tracked in
`<repo-root>\markdown-files\FIRST_HALF_RAW_CSV_AUDIT_AND_SECOND_HALF_PLAN.md`.
The producer-side `secondhalf.csv` was regenerated from the 2026-04-25 CUDA
pickup-formula fix. Its `target_half_16bit` is the pickup upper PID half,
`t-0` is the pickup `Random()` state, and expected IV output uses `A` for male
parent, `B` for female parent, and numeric IVs for untouched RNG base values.
Current SHA-256:
`81A8CC4DDA4117268B06CE082F98717EBE8A5586E0B50567A8FD92DC050A1AD9`.

## `Build-Phase2-Pickup-States.py`

This is a standalone Phase 2 bridge script.

What it is for:

- load completed first-half `0x####.sav` lane saves
- stream `secondhalf.csv` and require one Phase 2 initial seed
- require replay metadata to match that seed
- replay the known title route to reproduce the initial seed
- run the seed-to-pre-pickup bridge tape through the last input before pickup
- automatically use `<repo-root>\0x0000 special tape.json` only for the ACE
  endpoint lane `0x0000.sav`; no CLI flag is needed for normal runs
- pad neutral frames to a configurable baseline, default `700`
- validate the final `gRngValue` against a learned or supplied baseline state
- write `<repo-root>\Phase2PickupStates\0x####.ss0`
- by default, enable live Audio killswitch, no-render, and unbounded
  fast-forward through the Qt bridge
- allow monitoring-friendly bounded/manual speed with
  `--no-unbounded-fast-forward` or
  `MGBA_PHASE2_PICKUP_UNBOUNDED_FAST_FORWARD=0`; this disables the live
  fast-forward toggle while still applying Audio killswitch and no-render

Crash/restart behavior for launches after the 2026-04-26 hardening patch:

- each savestate is written first as `0x####.ss0.tmp`
- the temp file is checked against `--expected-state-size`, default `397312`
  bytes
- only a good-size temp file is renamed into the final `0x####.ss0` path
- resume skips only existing final `.ss0` files with the expected byte size
- partial or bad-size final `.ss0` files are rebuilt on the next run
- stale `0x####.ss0.tmp` files are not treated as completed states

Default command shape:

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py
```

Monitoring-friendly relaunch, with live fast-forward disabled by the script:

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py --no-unbounded-fast-forward <repo-root>\doc\python-examples\frlg-seed-bruteforce\lg.gba
```

Runtime human-check control, applied by the builder between save jobs. This
disables Audio killswitch, no-render, and fast-forward, then stops the builder
cleanly so the mGBA UI is returned to the operator:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase2_pickup_runtime_control.py --human-check
```

Runtime performance control for the next builder launch or active safe point.
This restores the high-speed feature settings and clears the stop request:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase2_pickup_runtime_control.py --performance
```

The control file is `<repo-root>\Phase2PickupStates\_phase2_pickup_control.json`
by default. Use it instead of changing mGBA Custom Features checkboxes while the
builder is active. After a human-check stop, relaunch the builder with the
normal restart command to continue; resume skips valid-size completed states.

Dry-run example for the first lane:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py --dry-run --start-hex 0x0001 --end-hex 0x0001 --expected-save-count 1 --require-expected-save-count
```

Evidence note:

- Source tests cover parsing, path selection, bridge-tape padding, RNG drift
  checks, temporary save loading, crash-safe temp-state publishing, valid-size
  resume skips, runtime feature-control polling, and source-save non-mutation.
- A real emulator-generated `Phase2PickupStates\0x0001.ss0` was observed in
  the active 2026-04-26 run; this file does not claim the full folder is
  complete until the final audit proves it.

## Phase 2 Pickup Dashboard

Use the Phase 2 pickup-state dashboard:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase2_pickup_progress_web.py
```

Then open:

```text
http://<local-LAN-IP>:234/
```

It is read-only. It reports `complete / 65536`, status JSON fields from
`_phase2_pickup_status.json`, recent JSONL errors, bad names, bad sizes, and
unsettled state files in `<repo-root>\Phase2PickupStates`. It treats
`0x####.ss0.tmp` as an unsettled write, not as a bad filename and not as a
complete state.

## Phase 2 Pickup State Validator

Use the Phase 2 pickup-state validator for offline recovery checks:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase2_pickup_state_validator.py
```

What it reports:

- complete states and missing states
- bad-size final `0x####.ss0` files
- current or stale `0x####.ss0.tmp` files
- bad names and duplicate-looking state variants such as copied `.ss0` files
- optional sample-load `gRngValue` verification against the learned baseline
  RNG, using a separate host-side core only when `--verify-samples` or
  `--sample-targets` is passed

Operational note:

- The active 2026-04-26 builder and dashboard must not be stopped just to run
  the validator. The default scan is read-only and does not attach to the live
  Qt process.
- Restart commands and safe-delete guidance live in
  `PHASE2_PICKUP_RESTART_RUNBOOK.md`.

## `Build-Phase3-Spinda-Block.py`

Current status:

- Proven: source script, tests, and one full `0x0001` Python-runner ZIP exist.
- Observed once: the `0x0001.ss0` Phase 2 input state was validated in the
  completed Phase 2 folder audit.
- Planned: output archive validation and native-output comparison still need
  separate checks.
- Observed once: native headless autorun and native worker-pool headless mode
  completed `0x0002` smoke runs with `2` generated records after the final
  rebuild, and both manifests recorded the shared cache path.
- Observed once: standalone CLI LTO `mgba-spinda-phase3.exe` completed a
  `0x0001 --limit 2` proof and wrote PID-named 80-byte `.pk3` ZIP entries.
- Planned: tune native worker count before broad Phase 3 production runs.

What it is for:

- load `<repo-root>\Phase2PickupStates\0x0001.ss0` by default
- require visible Qt mGBA for real generation; it does not run headless
- enable visible Qt Audio killswitch, no-render mode, and unbounded
  fast-forward by default for performance runs; use
  `--disable-audio-killswitch`, `--disable-no-render`, or
  `--disable-fast-forward` for monitoring runs
- keep `--enable-audio-killswitch`, `--enable-no-render`,
  `--enable-fast-forward`, and `--fast-forward-ratio` accepted; non-positive
  fast-forward ratio means unbounded
- stream the precomputed
  `<repo-root>\build-mingw64-python-qt\secondhalf.csv`
- use only `t-0` rows because CUDA/workbench math defines `t-0` as the pickup
  `Random()` state, not the visible A-press frame
- press `A` before that `t-0` state using
  `--pickup-input-lead-frames`, default `3`; this accounts for the observed
  2-4 frame delay between the final input and the egg pickup RNG call
- treat the current validated `0x0001.ss0` Phase 2 state as baseline frame
  `700` plus RNG drift `+1`, using default `--baseline-rng-drift-frames 1`;
  waits are calculated from effective start frame `701`
- account for the current `secondhalf.csv` seed delay of `750`: the earliest
  `t-0` pickup row is frame `751`, so with A lead `3` the first A-input frame
  is `748`, and the first neutral wait from effective frame `701` is `47`
- by default, sort targets from the loaded state's actual `gRngValue`
  sequence using `--schedule-source runtime-rng`; direct CSV-frame scheduling
  remains available as `--schedule-source csv-frame` for debugging, but the
  first visible `0x0001` pilot disproved it for the current Phase 2 state
- sweep forward instead of seeking each target from the original state
- use Qt scratch-state save/load around each pickup so the script can extract
  the egg, restore to the pre-pickup frame, then continue to the next target
- read the party-slot record, require PID `0x<upper><lane_lower>`, drop the
  final 20 party-only bytes, and keep the 80-byte boxed PK3 record in RAM
- parse the large `secondhalf.csv` with positional `csv.reader` and skip
  non-`t-0` rows before parsing target/frame/RNG fields
- cache parsed `secondhalf.csv` `t-0` targets in
  `<repo-root>\Phase3SpindaBlocks\_cache`
- cache the runtime pickup schedule in the same `_cache` folder, keyed by the
  parsed-target cache, live starting `gRngValue`, schedule mode, pickup lead,
  effective start frame, and max-step window
- read only the 80 stored boxed bytes from the party slot, not all 100 bytes of
  the party record
- write status only at start, failure, and final ZIP completion; the
  `--progress-every` argument is kept for old command compatibility but does
  not write mid-run progress anymore
- write one ZIP archive atomically at the end:
  `<repo-root>\Phase3SpindaBlocks\0x0001.spinda80.zip`
- final ZIP archives contain only PID-named `.pk3` files; no manifest,
  bitmap, block file, or other non-`.pk3` entry is written inside the archive
- Native Qt autorun is available through
  `MGBA_SPINDA_NATIVE_PHASE3_AUTORUN=1`, using the Qt frontend core but
  bypassing the Python scripting layer for the hot frame loop. Add
  `MGBA_SPINDA_NATIVE_PHASE3_HEADLESS=1` to suppress the main window and Spinda
  dialog for unattended native runs.
- Bulk multi-lane runs can be launched with
  `tools/spinda/native_phase3_worker_pool.py`; production now defaults to the
  standalone CLI runner, starts one `mgba-spinda-phase3.exe` process per active
  worker slot, sets isolated `MGBA_WORKER_INSTANCE` names, skips existing lane
  ZIPs by filename without opening their central directories, and watches the
  per-lane status JSON files. `--runner qt` remains available for visual
  inspection and Qt-native cache/bundle experiments.
- A separate CLI LTO runner can be built with
  `tools/spinda/build_phase3_cli_lto.bat`. It produces
  `build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe`, links a static/LTO
  libmgba build, and disables Qt, SDL, and Python. Direct PowerShell launches
  need `C:\msys64\mingw64\bin` and `C:\msys64\usr\bin` on `PATH`.

ZIP contents:

- `0x00000001.pk3`
- `0x00010001.pk3`
- ...
- `0xFFFF0001.pk3`

Each entry is exactly `80` bytes and is named from the PID stored in that
record. Status JSON beside the ZIP carries run metadata.

Default command shape:

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-spinda\Build-Phase3-Spinda-Block.py
```

Native autorun command shape:

```powershell
$env:MGBA_SPINDA_NATIVE_PHASE3_AUTORUN='1'
$env:MGBA_SPINDA_NATIVE_PHASE3_HEADLESS='1'
$env:MGBA_SPINDA_NATIVE_PHASE3_EXIT_ON_COMPLETE='1'
$env:MGBA_SPINDA_NATIVE_PHASE3_LANE_ID='0x0002'
$env:MGBA_SPINDA_NATIVE_PHASE3_PHASE2_STATE='<repo-root>\Phase2PickupStates\0x0002.ss0'
$env:MGBA_SPINDA_NATIVE_PHASE3_CACHE_DIR='<repo-root>\Phase3SpindaBlocks\_cache'
$env:MGBA_SPINDA_NATIVE_PHASE3_OVERWRITE='1'
<repo-root>\build-mingw64-python-qt\mGBA.exe <repo-root>\doc\python-examples\frlg-seed-bruteforce\lg.gba
```

Native worker-pool command shape:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\native_phase3_worker_pool.py --lanes 0x0002-0x0005 --workers 2 --runner cli --overwrite
```

Standalone CLI LTO command shape:

```powershell
<repo-root>\tools\spinda\build_phase3_cli_lto.bat
$env:PATH='C:\msys64\mingw64\bin;C:\msys64\usr\bin;' + $env:PATH
<repo-root>\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe --rom <repo-root>\doc\python-examples\frlg-seed-bruteforce\lg.gba --lane 0x0001 --phase2-state <repo-root>\Phase2PickupStates\0x0001.ss0 --secondhalf-csv <repo-root>\build-mingw64-python-qt\secondhalf.csv --output-dir <repo-root>\Phase3SpindaBlocks\_proof_cli_lto_20260430 --limit 2 --overwrite
```

Bulk remaining batch command:

```bat
<repo-root>\tools\spinda\run_phase3_remaining_workers.bat 6
```

The batch command asks for a worker count when no argument is supplied. It
passes the normal lane range `0x0001-0xFFFE` to the worker pool, uses the
standalone CLI runner by default, and skips lanes whose final ZIP filename
already exists. Endpoint lanes are separate follow-up work.
The filename-only skip path checks only for `0x####.spinda80.zip` with nonzero
size, so restarts do not open thousands of 65,536-entry ZIPs. Deep ZIP content
validation remains a separate auditor step. The pool is a refilling queue: when
one mGBA worker finishes a lane or lane bundle, the next missing lane bundle is
launched until all requested missing lanes are done or failed. Full-range
production uses lazy job creation and preview-limited status output so it does
not allocate or rewrite every remaining lane job before launching workers.

Useful pilot flags:

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-spinda\Build-Phase3-Spinda-Block.py --limit 16 --overwrite
```

Evidence note:

- Source tests pass for the core file-format and control-flow behavior.
- Live timing may still require changing `--pickup-input-lead-frames` to `2`
  or `4`, or changing `--baseline-rng-drift-frames` if a different Phase 2
  state audits at a different drift. A PID mismatch error includes the expected
  PID, observed PID, CSV `t-0` frame, adjusted input frame, and current
  lead-frame value so the calibration is explicit.

## Native Qt Spinda Project

Current status:

- Proven: native Qt source, standalone CLI source, source tests, `mgba-qt`
  rebuild, and CLI LTO build exist.
- Observed once: `0x0002` native headless autorun and `0x0002` native
  worker-pool headless smoke runs completed at `LIMIT=2`.
- Observed once: bundled native worker-pool smoke run
  `0x0001..0x0002 --bundle-size 2 --limit 2` completed headless, wrote two
  PID-named 80-byte `.pk3` entries for each lane, and exited with code `0`.
- Observed once: LTO build completed but produced a startup pseudo-relocation
  failure in `mGBA.exe`; safe non-LTO Release was restored.
- Observed once: separate static/LTO CLI build completed and `0x0001 --limit
  2` produced `0x21D10001.pk3` and `0x35D80001.pk3`, both 80 bytes.
- Observed once: reviewed CLI LTO build completed after quiet-logger and
  hot-loop buffer fixes; `0x0001 --limit 2` completed in about `3.79s` with the
  same two 80-byte entries.
- Observed once: not yet run through a full `0x0001` lane from the dialog or
  headless autorun path.
- Planned: compare native output ZIP against the Python Phase 3 output after
  first full native run.

What it is for:

- expose Phase 3 from `Tools > Custom Features > Spinda project...`
- use the visible Qt GBA core directly instead of the Python scripting layer
- keep the same Phase 3 storage contract: one `0x####.spinda80.zip` file
  whose contents are only `0x<PID>.pk3` entries
- keep extracted boxed PK3 records and the completed ZIP byte stream in RAM
  until the final disk write
- use native target and runtime-schedule caches under
  `<repo-root>\Phase3SpindaBlocks\_cache` by default, with
  `MGBA_SPINDA_NATIVE_PHASE3_CACHE_DIR` for a reusable shared cache
- write status only at start, failure, and final completion

Native execution model:

- load a validated Phase 2 `.ss0`
- read the loaded `gRngValue`
- build or load the runtime-RNG pickup schedule
- interrupt the visible `CoreController`
- call `core->runFrame(core)` directly for neutral waits and final `A` input
- save one in-RAM scratch savestate at each pre-pickup frame
- by default, check party slot 2 once per frame after `A` until the expected
  PID appears, avoiding the old fixed 24-frame post-pickup wait
- native detection reads only the 4 PID bytes during the wait loop, then reads
  the full 80-byte boxed PK3 once the PID matches
- native learned-delay mode samples early dynamic pickups; if every sample uses
  the same wait count, later pickups use one fixed-delay PID check and fall
  back to dynamic scan on mismatch
- native fast-check mode checks likely pickup frames `4` and `5` before the
  fallback scan, while still emulating every frame
- keep a fixed post-pickup wait fallback for calibration
- reuse hot-loop scratch/record buffers to reduce per-target allocation churn
- restore scratch after extracting each egg, then continue along the same
  timeline
- read party slot 2 directly from GBA memory and validate
  `PID == 0x<upper><lane>`
- build one compressed ZIP in RAM, then write it at the end
- record pickup detection min/max/average wait frames in final status when
  dynamic detection is enabled
- record learned-delay stats and timing buckets for frame advance, scratch
  save, pickup wait/detect, scratch restore, PK3 reads, ZIP build/write, and
  hashing
- support no-dialog/headless native autorun through
  `MGBA_SPINDA_NATIVE_PHASE3_HEADLESS=1`
- support sequential lane bundles through `MGBA_SPINDA_NATIVE_PHASE3_LANE_IDS`
  when the worker pool is launched with `--bundle-size`
- support a separate non-Qt CLI entrypoint through `mgba-spinda-phase3.exe`
  for LTO benchmarking and headless proof runs
- use that CLI entrypoint as the default worker-pool production runner; Qt
  worker mode is now an explicit inspection path
- quiet mGBA core diagnostic logging by default in the CLI path; use
  `--verbose-core-logs` only for short debugging runs
- audit the runtime pickup schedule by actual input frame before extraction in
  both CLI and native Qt paths, with sorting only when cached/imported schedule
  data is detected out of order
- optionally prove CLI neutral-wait equivalence with
  `--neutral-wait-proof-frames`; the optimized path still runs every core frame
  and only reduces redundant key-state calls while comparing RNG/party runtime
  witnesses against the one-frame-at-a-time reference
- stop CLI runtime-schedule construction early for `--limit` proof runs
- reuse CLI ZIP/PK3 scratch buffers inside hot loops
- reject signed numeric CLI/CSV fields before they can wrap through C unsigned
  parsing
- parse CLI fields and CSV `sweep_index` / `frame_from_initial_seed` through
  the same unsigned parser; no `strtol` path remains in the CLI runner
- enforce the CLI `secondhalf.csv` initial-seed contract; the default expected
  seed is `0xCD39`, with `--expected-initial-seed` and
  `--no-expected-initial-seed-check` reserved for deliberate experiments
- fail early if the CLI output directory cannot be created, and include the
  Windows error code when atomic ZIP publish fails
- on POSIX CLI builds, publish temp ZIPs by renaming directly over the final
  path instead of unlinking the previous final ZIP first
- on POSIX native Qt builds, use the same direct-rename publish rule so a failed
  replace does not delete the previous final ZIP
- enforce no-overwrite at final publish as well as at pre-run validation; this
  prevents a stale worker from replacing a ZIP that appeared during generation
- avoid replacing a completed lane status with a stale CLI failure when the
  final publish correctly refuses to overwrite an existing ZIP
- name native Qt and CLI temp ZIPs as process-local `.pid<PID>.tmp` files, so
  duplicate same-lane workers do not share one temp archive; stale temp files
  are safe to delete only after Phase 3 workers are stopped
- reject CLI `--pickup-hold 0`; at least one held frame is required for the
  emulated `A` input to exist

Important limit:

- this is still an emulator-driven daycare pickup path. It skips Python call
  overhead, but it does not bypass FR/LG game logic or directly synthesize
  Spinda records.
- the CLI entrypoint currently does not reuse the Qt-native shared target and
  runtime-schedule cache, so full-lane timing may include extra startup parsing
  work until cache support is added there.

## `frlg_spinda_first_half_batch.py`

This is the phase-1 batch runner for producing the full set of first-half save
files.

What it is for:

- generate the unique live lower-half save set, `0x0001.sav` through
  `0xFFFE.sav`, or preserve all raw CSV targets under the flat
  `<repo-root>\1sthalves` output root
- place those saves and matching pre-daycare-man `.ss0` states in
  `<repo-root>\1sthalves`
- keep per-save manifests under `<repo-root>\1sthalves\_manifests`

Expected inputs:

- `firsthalf.csv`
- `1 from egg.sav`
- `Seed-Bruteforcer.py`
- `tape seed to step 1.json`
- `hit 1st half walk to daycare man.json`
- the LeafGreen ROM used by the first-half proof of concept
- `1 from egg.ss0`, the premade post-seed savestate used by the explicit loaded-state
  workflow

Big-picture flow:

1. stream `firsthalf.csv` and load the `t-18` and `t-0` rows for each raw egg-half roll
2. by default, group the full CSV target space by `initial_seed_16bit`
3. brute-force each needed initial seed once per group
4. replay `tape seed to step 1.json`
5. wait the CSV-derived neutral frames until `t-18`
6. check live RAM `gRngValue` against the CSV `t-18` PRNG
7. capture a pre-hit checkpoint and try nearby hit delays around the
   nominal `18` frames
8. compare live RAM `gRngValue` with CSV `t-0` using bounded forward and
    reverse LCRNG drift checks
9. convert the CSV raw half into FR/LG's stored daycare lower half with
    `((Random() % 0xFFFE) + 1)`
10. verify the daycare lower-half RAM value
11. save a matching `0x####.ss0` state before the daycare-man walk/save suffix
12. replay the rest of the lower-half/walk/save tape
13. export the live save as `1sthalves/0x####.sav`, where `####` is the actual
    daycare lower half stored by FR/LG

Design choice now made explicit:

- this script can consume route CSVs and loaded-state anchors, but it does not
  assume they describe the same lane unless that has been verified
- that rule exists because the current copied `firsthalf.csv` seed and the
  older loaded-state calibration seed are different artifacts

Seed modes:

- default `loaded-state`: reads the predetermined locked baseline initial seed
  from `1 from egg - replay-metadata.json`, loads the premade post-seed
  route anchor `<repo-root>\live-lanes\live-fbc7-lane16\1 from egg.ss0`, filters the CSV to that one initial seed,
  and keeps the anchor's organic `gRngValue` after every anchor load
- explicit `csv-bruteforce`: uses every CSV row's `initial_seed_16bit`, loads
  the unique live first-half targets from the current `firsthalf.csv`, and
  brute-forces each needed initial seed once per group
- with the current locked baseline metadata, `loaded-state` mode still reports
  initial seed `0x26CD`
- whether that lane has compatible rows now depends on the copied
  `firsthalf.csv`; the current copied route CSV references are tracked in
  `INITIAL_SEED_CSV_REFERENCE.md`
- explicit `csv-bruteforce` mode still plans whatever raw target space exists
  in the current `firsthalf.csv` and exports the de-duplicated live results
- the locked baseline checkpoint itself is a title-screen calibration artifact
  from `Seed-Bruteforcer.py`, not a post-seed route anchor; the batch runner now
  rejects it as `--first-half-state` so the failure surfaces immediately

Terminology note:

- `initial seed` means the 16-bit title-screen seed generated before the route
  starts
- `Spinda first half` means the lower 16-bit half produced later by the daycare
  RNG path
- these are both 16-bit numbers, so the batch script now names them
  separately in logs, manifests, and dry-run output

CSV-half note:

- the CSV `target_half_16bit` field is the raw 16-bit `Random()` output used by
  the lower-half roll
- FR/LG stores the pending daycare lower half as `((Random() % 0xFFFE) + 1)`,
  so the live daycare value is usually one greater than the raw CSV half
- because of that formula, raw CSV halves `0x0000` and `0xFFFE` both map to
  live `0x0001`, and raw `0x0001` and `0xFFFF` both map to live `0x0002`
- normal live-output mode collapses those two raw-half collisions onto the
  actual live daycare results so it exports one save per actual reachable live
  lower half
- raw-CSV output mode preserves those collision rows as separate suffixed
  outputs under `_live_name_collisions`
- the practical main save-name range is therefore `0x0001` through `0xFFFE`;
  raw-CSV mode still processes all `65536` `0x0000..0xFFFF` CSV targets by
  keeping collision duplicates separately
- lower-half target coverage is now complete only when the labeled
  `0x0000`/`0xFFFF` ACE endpoint exceptions are added to those organic outputs
- the batch runner now records both the raw CSV half and the observed live
  daycare lower half in its manifests
- manifests keep the older `lower_half` field for compatibility, but also
  write the clearer alias `spinda_half_live`

Resume behavior:

- startup now scans the requested target artifacts before loading or driving
  mGBA
- a target is treated as complete only when its `.sav`, matching
  pre-daycare-man `.ss0`, and manifest all exist
- the manifest must point at the expected paths, match the current raw CSV
  half, live Spinda half, initial seed, output-key mode, and recorded SHA-1
  values for both files
- `_resume_status.json` records the scan result, including complete existing
  targets, pending targets, and reason counts such as `missing-manifest` or
  `save-sha1-mismatch`
- if mGBA crashes after writing only a `.ss0`, only a `.sav`, or a stale
  manifest, the next run treats that target as pending and regenerates the
  target instead of silently skipping it
- if every requested target is complete, the script exits before loading the
  emulator helper, so a restart does not replay already-finished work

Recovery behavior:

- if the expected `t-18` PRNG is not present after the planned wait, the script
  reloads the current post-seed anchor, replays the setup tape, and scans
  forward frame-by-frame for the documented `t-18` PRNG
- in loaded-state mode, recovery reloads the same organic post-seed RNG state
  from the savestate before scanning; it does not rewrite RAM back to the CSV
  seed
- the default scan window is `240` frames
- if recovery succeeds, the manifest records that recovery was needed
- if recovery fails, the script stops before exporting a bad save
- in loaded-state mode, the first mismatch now also attempts to infer the
  anchor's frame offset from bounded LCRNG drift, reruns the setup tape with
  the corrected wait, and reuses that calibrated offset for later targets in
  the same lane before falling back to a broad scan

Hit-drift behavior:

- for first-half batch routes, the CUDA completion-history CSV records `t-0`
  as the successful compatibility-roll state
- the live frame may also consume the target `Random()` call and later noisy
  calls before Python observes `gRngValue` again
- after replaying the 18-frame hit prefix, the script calculates signed drift
  from CSV `t-0` with LCRNG and LCRNG(R)
- positive drift is accepted only if the requested target half's `Random()`
  call is inside the observed forward drift window
- negative drift means the route stopped before `t-0` and is treated as an
  error because the target event has not been proven
- the manifest records the observed post-hit RNG, signed drift, and target-call
  offset
- after the lower half is observed in daycare RAM, the batch saves a matching
  `.ss0` state before it replays the remaining daycare-man walk/save suffix

Hit-delay behavior:

- the noisy daycare step route does not always land the egg-generation event at
  exactly the same rendered delay
- the batch runner now captures one pre-hit checkpoint after reaching the
  validated pre-hit PRNG checkpoint
- checkpoint capture prefers host raw-state support, then visible-Qt scratch
  state, then one file-backed scratch savestate as a final fallback
- nearby hit-delay prefix/suffix tape variants are pre-split once per run and
  then reused across targets instead of being rebuilt for every lane
- from that checkpoint it tries nearby prefix lengths such as `17`, `18`, and
  `19` frames before committing to the rest of the walk/save tape
- this keeps the retry path fast and avoids reloading the larger anchor or
  rerunning the entire title-to-daycare segment for every nearby delay guess

Performance behavior:

- loaded-state full sweeps sort pending targets by `t-18` frame and run one
  checkpointed post-setup route runway forward instead of reloading the
  post-seed anchor, replaying the setup tape, and waiting from zero for every
  target
- before each target branch, the optimized runway captures a validated `t-18`
  checkpoint; after the `.ss0`/`.sav` branch finishes, the script restores that
  checkpoint and advances only the frame delta to the next target
- if the runway observes PRNG drift at `t-18`, it restores the durable
  post-setup checkpoint, recalibrates the loaded-state frame offset, and only
  continues after the expected CSV `t-18` PRNG is proven
- that recovery bound covers the planned post-setup wait plus the local
  `--t-minus-recovery-window`, so an organic loaded-state offset larger than
  the small local slop window can still be calibrated
- explicit `csv-bruteforce` mode groups targets by `initial_seed_16bit`, so the
  same CSV seed is brute-forced once and reused for every compatible target
- default `csv-bruteforce` seed searches use the rolling pre-input checkpoint
  from `Seed-Bruteforcer.py` rather than replaying from the title baseline for every
  delay
- when a non-target title branch does expose a seed, the batch records the
  exact observed `seed`, `seed_frame`, and `rng_at_seed`, then can enqueue an
  auto-adjusted delay candidate before the normal linear `delay + 1` fallback
- that auto-adjustment primarily uses the observed Timer-1 seed delta across
  neighboring delays for the same button; it also tries a very small bounded
  LCRNG/LCRNG(R) hint from the seeded PRNG state, but that is only a secondary
  hint because Timer 1, not LCRNG, still chooses the initial seed
- when those auto-adjust candidates are queued, the stronger Timer-1 estimate
  now keeps its intended priority instead of being accidentally reversed by the
  deque prepend path
- if one title-input branch times out without exposing a seed after the helper
  has already rebuilt that exact delay checkpoint once, the batch records that
  branch as a miss and continues to the next branch or delay instead of
  aborting the whole CSV seed group
- the same is now true for the helper's "checkpoint drifted out of the legal
  pre-seed title window" failures, including the `RUN/state=1` guard: those
  are recorded as branch misses and the batch continues searching
- loaded-state mode performs no title-screen initial-seed search; it loads the
  premade post-seed route anchor, keeps the savestate's organic `gRngValue`,
  and uses the CSV only for the matching Spinda first-half rows
- loaded-state mode now learns the post-seed anchor's frame offset from the
  first verified `t-18` mismatch and reuses that offset on later targets so the
  route lands closer to CSV timing without repeated wide scans
- manifest hashes for invariant inputs, including the setup tape, hit tape, and
  base save, are computed once per batch run
- the current post-seed anchor hash is computed once per seed group, because
  that file is overwritten when the next CSV seed is hit
- `_batch_status.json` keeps the older `last_seed` / `last_lower_half` fields
  for compatibility, but now also writes `last_initial_seed` and
  `last_spinda_half_live` so operator tooling can tell the two 16-bit concepts
  apart without guessing from context
- `_batch_status.json` also records `last_pre_daycare_man_state`, matching the
  `.ss0` emitted before the daycare-man suffix
- if the selected CSV/range/options produce no targets, the script exits before
  loading the emulator helper or touching the visible Qt core
- the script's `__main__` wrapper only raises `SystemExit` for nonzero exit
  codes, so successful Qt runtime runs do not end with a false error line

Input tape note:

- the current `tape seed to step 1.json` header says `292` frames, but its run
  list currently sums to `238` frames
- the batch runner trusts the run list because that is what replay actually
  executes
- it fixes that mismatch only in memory for the current run; it does not
  rewrite the JSON file on disk, so the stale header remains visible for later
  audits
- the dry-run output reports this mismatch so the operator can see the exact
  frame count used for CSV delay math

## `frlg_spinda_lane_workspace.py`

This is a pure metadata utility.

What it is for:

- create the canonical lane manifest and directory structure
- optionally preallocate a lane block file
- print current lane status

What it does not do:

- it does not launch the emulator
- it does not generate routes

This is useful because later long-running automation needs the file layout to
be predictable even before a lane is fully generated.

## `spinda_frlg_archive.py`

This is the storage layer.

What it does:

- defines the raw lane-block model
- defines the presence bitmap model
- defines the global corpus manifest

Important pieces:

- `LaneBitmap`
  - `65536` bits
  - mark present/absent
  - iterate present indices
  - load/save to disk
- `LaneBlockBuffer`
  - one in-memory `65536 * 80` payload
  - set/get records
  - save/load alongside a bitmap
  - compute the next missing upper-half slot for resume hints
- `GlobalCorpusManifest`
  - one file that describes the current big-picture state of the corpus run

Why this file matters:

- it makes the archive format explicit before the heavy run begins
- it keeps the resume and export logic independent from the emulator

## `frlg_spinda_corpus_manifest.py`

This is the top-level resume manifest CLI.

What it is for:

- create the global manifest
- inspect it
- update the current lane and upper-half position

This is intentionally simple. It is meant to support long-running resumable
automation later, not to do any game logic itself.

## `frlg_spinda_recipe_lint.py`

This is the offline recipe checker.

What it is for:

- validate first-half and second-half recipe JSON files before a live run

What it checks today:

- missing ROM/save/state files referenced by the recipe
- empty route sections
- duplicate route labels
- duplicate second-half target ids
- missing `expected_rng_before_route` values in second-half targets
- second-half `expected_pid` values that do not match the lane id plus target
  upper half

What it does **not** do:

- it does not boot the game
- it does not prove any route is frame-correct
- it does not prove a PRNG checkpoint is truly reachable

Why it matters:

- it gives the roadmap one cheap preflight check before a long batch run

## `frlg_spinda_workspace_audit.py`

This is the offline workspace auditor.

What it is for:

- inspect an existing workspace root and decide whether the archive state is
  coherent enough to resume from

What it checks today:

- global manifest existence and basic lane reference sanity
- lane save and work-state presence
- recorded save/state SHA-1 values when the lane manifest includes them
- whether the lane manifest still points at the canonical save/state/block
  paths for that lane id
- lane block and bitmap readability
- whether the bitmap count matches `completed_upper_halves`
- whether a lane marked complete really contains all `65536` upper halves

Why it matters:

- the roadmap assumes long-running interrupted jobs
- this script is the offline answer to "can I trust these files before I
  resume - "

## TID0 / TSV Save Bank and Mass Hatching

This is the hatching proof layer that sits beside the Phase 3 Spinda corpus.
It is not a replacement for lane blocks or PID-named `.pk3` archives.

Save-bank contract:

- Trainer ID is fixed at `0`
- each save has a Secret ID that maps to one Trainer Shiny Value
- formula:
  - `TSV = (TID ^ SID) >> 3`
- output folder:
  - `<repo-root>\TSVs`
- save naming:
  - `TSV-xxxx-sid-xxxxx.sav`
- TSV and SID in filenames are decimal, not hex
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`

Verified status as of 2026-05-06:

- complete saves:
  - `8192 / 8192`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`

Mass hatching plan:

1. calculate each egg's Pokemon Shiny Value:
   - `PSV = (PID_low ^ PID_high) >> 3`
2. hatch the egg through the matching `TSV == PSV` save as shiny proof
3. hatch the same egg or selected control through a non-matching TSV save as
   non-shiny proof
4. verify species, PID, TID, SID, TSV/PSV relation, and shiny flag after hatch
5. package shiny proof outputs in one ZIP subset
6. package non-shiny proof outputs in a separate ZIP subset

Implemented tool for that planned stage:

- `<repo-root>\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj`
- test harness:
  - `<repo-root>\tools\spinda\hatch_zip_splitter_tests\SpindaHatchZipSplitter.Tests.csproj`

What the tool does:

- reads Phase 3 ZIP entries in memory without extracting loose PK3 files
- parses `TSV-xxxx-sid-xxxxx.sav` files with PKHeX.Core by default
- copies trainer data from the selected save context into the hatched PK3
- uses `TSV == PSV` for the shiny output ZIP
- uses a non-matching TSV for the not-shiny output ZIP
- writes `_spinda_hatch_manifest.json` inside each derived ZIP
- writes a top-level JSON report with PSV/use histograms and sampled entries
- uses a custom no-compression ZIP writer by default so corpus-scale output
  spools central-directory metadata to disk instead of retaining all entries in
  RAM

Production remains planned until the Phase 3 egg ZIP corpus is ready. The TSV
save bank is complete; hatch-splitter proof is still limited to synthetic unit
tests that construct tiny PK3 egg ZIPs in a temporary folder.

Each hatch report records samples with PID, PSV, TID, SID, TSV save filename
context, species, shiny/non-shiny result, and issue counts. Keep the hatching
proof ZIPs separate from the canonical corpus outputs so archive audits can
tell raw generation products from derived proof products.

## `frlg_spinda_export.py`

This is the archive export layer.

What it is for:

- export one stored record as `.pk3`
- export one lane as loose `.pk3` files in a directory
- export one lane as a ZIP of `.pk3` files
- export one inclusive upper-half range as a ZIP of `.pk3` files
- export many lanes as one nested ZIP

Important behavior:

- it reads the raw lane block
- it respects the bitmap when deciding what is actually present
- it does not require the emulator
- single-record `.pk3` export reads only the bitmap plus one 80-byte block
  slice instead of loading the whole lane into memory
- it now refuses an empty nested export instead of silently writing a useless
  outer ZIP
- single-record `.pk3` exports are written atomically
- directory exports also write individual `.pk3` files atomically
- `range-zip` and `lane-dir` can limit exports with inclusive upper-half
  `start` / `end` bounds
- invalid `start > end` ranges fail before creating output directories or ZIPs

Current assumption:

- the stored 80-byte record is already the canonical object, so exporting a
  `.pk3` means writing those exact 80 bytes

If the project later decides that a transformed export format is needed, this
is the place where that conversion layer would live.

## `frlg_spinda_second_half_lane.py`

This is the phase-2 sweep scaffold.

What it is for:

- load one lower-half lane
- create or reuse a canonical pre-pickup work state
- sweep upper-half targets
- validate PIDs
- write boxed records into the lane block
- update lane progress

Big-picture flow:

1. load the lane manifest
2. make sure the work state exists
3. load or create the lane block buffer
4. for each target:
   - reload the work state
   - optionally verify the expected PRNG before the route
   - run the target route
   - extract party slot 2
   - compute the observed PID
   - compare to the expected PID
   - write the 80-byte record into the correct slot
   - periodically flush block and manifest progress
5. save the final block and updated manifest

Resume behavior:

- `next_upper_half` is no longer just "last target plus one"
- it is now a best-effort resume hint:
  - first missing target from the current recipe, if one exists
  - otherwise the first missing slot in the whole lane block

What is still placeholder here:

- the regenerated real pickup route schedules
- the exact pre-pickup route
- the exact PRNG checkpoints

So this file is the execution shell, not the finished target runner yet.

Runtime usage:

- this script now also exposes `run_recipe_file(...)`
- in the Qt scripting window, it can be loaded after the operator has a live
  GUI session open, and it will reload the requested ROM/save/work-state into
  that visible core itself
- if `MGBA_SPINDA_SECOND_HALF_RECIPE` is set, or
  `second_half_recipe.json` exists beside the script, it can auto-run in that
  Qt runtime path

## Recipe Templates

## `first_half_recipe_template.json`

Purpose:

- documents the expected shape of a lower-half recipe

Current fields:

- `rom_path`
- `base_save_path`
- `base_state_path`
- `workspace_root`
- `target_lower_half`
- `create_lane_work_state`
- `notes`
- `pre_generation_route`
- `post_generation_route`
- `save_sequence`

## `second_half_recipe_template.json`

Purpose:

- documents the expected shape of an upper-half sweep recipe

Current fields:

- `rom_path`
- `workspace_root`
- `lane_id`
- `lane_save_path`
- `flush_every`
- `notes`
- `create_work_state_route`
- `targets`

Each target can include:

- `upper_half`
- `expected_rng_before_route`
- `route`
- optionally `expected_pid`

## Current Test Coverage

Focused pytest files exist for:

- common helper logic
- first-half lane export logic
- lane workspace setup
- archive block and bitmap behavior
- export behavior, including `.pk3`, full-lane ZIP, lane-directory, range-ZIP,
  and nested-ZIP paths
- global manifest behavior
- second-half sweep scaffolding

These tests started as theory-only coverage while the scripts were scaffolding.
They have since been executed in the local workspace as part of the broader
Qt/Python example verification passes. In the current documentation/code pass,
the full `test_frlg_spinda_*.py` set reports `38 passed`.

## What Is Still Missing

The remaining missing work is not basic file or script plumbing. It is mostly
truth-of-route work:

- the real gived save and savestate
- the real first-half route
- the real noisy walk-to-man PRNG checkpoints
- the real second-half pickup route tables
- real emulator validation that the saved/loaded state matches the intended PID
- mass hatching proof workflow for shiny and non-shiny outputs
- separate shiny and non-shiny hatch ZIP subset generation

That means the project now has a real offline codebase for the roadmap, and the
next phase is mostly about validating authentic FR/LG behavior in the emulator
instead of building more blank scaffolding.



