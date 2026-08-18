# Phase 3 Runbook

## Status Bucket

- Current status: Current operator runbook for Phase 3 bulk Spinda generation.
- Last verified date: 2026-04-30.
- Proven artifacts:
  - `tools/spinda/native_phase3_worker_pool.py`
  - `tools/spinda/phase3_command_center_web.py`
  - `tools/spinda/phase3_independent_watcher.py`
  - `tools/spinda/phase3_zip_validator.py`
  - `tools/spinda/phase3_command_center.cmd`
  - `tools/spinda/build_phase3_cli_linux.sh`
  - `tools/spinda/run_phase3_ledger_helper.sh`
  - `src/platform/test/spinda-phase3-main.cpp`
- Known gaps: This doc covers operation and resume behavior. It does
  not prove that every generated PK3 is semantically valid.
- Next action: Keep this runbook synchronized with the command center, watcher,
  recovery guide, and final validation plan.

## Evidence Split

### Proven

- Production path uses the native CLI runner, not the Qt frontend, for bulk
  generation.
- Worker pool refills worker slots from lane ranges and can skip existing lane
  ZIPs by filename.
- One complete lane produces one final ZIP named `0x####.spinda80.zip`.
- Final ZIPs contain PID-named `.pk3` files only.
- Hot progress checks avoid opening ZIP entries.
- Command center and watcher are support processes; they do not replace the
  native worker output path.

### Observed Once

- A 12-worker production run was observed with the command center, watcher, and
  Phase 3 CLI workers active at the same time.
- The watcher reported matching worker counts between worker-pool status and
  OS process list during that run.

### Inferred

- Filename-only resume is sufficient for hot production continuity, but final
  validation must later prove the ZIP contents.
- More command-center polling than the defaults can steal small but needless
  CPU time from workers.

### Planned

- Run final raw ZIP validation after all lanes complete.
- Run optional PKHeX.Core semantic validation after raw validation passes.
- Build a final manifest or backup record after validation.

### Obsolete

- Do not write one loose PK3 file per Spinda during production.
- Do not decompress PK3 files to disk during validation.
- Do not use Qt production mode for bulk unless doing visual inspection.

## Paths

Use these as placeholders:

```text
<project-root>
<project-root>\Phase2PickupStates
<project-root>\Phase3SpindaBlocks
<project-root>\Phase3SpindaBlocks\_cache
<project-root>\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe
<project-root>/build-linux-spinda-cli/mgba-spinda-phase3
```

Private ROMs, saves, savestates, CSV schedules, and generated ZIPs do not
belong in the clean repository.

## Normal Production Shape

- Runner: `mgba-spinda-phase3.exe`
- Launcher: `tools/spinda/native_phase3_worker_pool.py`
- Dashboard: `tools/spinda/phase3_command_center_web.py`
- Watcher: `tools/spinda/phase3_independent_watcher.py`
- Output: one ZIP per lane in `Phase3SpindaBlocks`
- Cache: shared cache files in `Phase3SpindaBlocks\_cache`
- Resume: skip existing nonzero final ZIPs by filename during hot production
- Final proof: separate validation after generation

Linux helper nodes use the same native CLI runner through
`tools/spinda/run_phase3_ledger_helper.sh`. They should claim lanes from the
coordinator ledger and do not need Qt. See
[PHASE3_LINUX_HELPER_NODE.md](PHASE3_LINUX_HELPER_NODE.md).

## Start Command Center

Preferred Windows wrapper:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

This starts the Flask command center and the independent watcher. It does not
start workers unless you use the browser controls.

Default page:

```text
http://127.0.0.1:235
```

## Start Or Resize Workers

Use the command center:

1. Open the page.
2. Set desired worker count.
3. Confirm lane range.
4. Press `Apply / launch workers`.
5. Confirm worker count, watcher status, and lane counter.

Default worker-pool production settings:

- CLI runner
- bundle size `2`
- deflate ZIP output
- shared schedule/cache folder
- filename-only skip of completed lane ZIPs
- preview-limited status JSON

## Operator Loop

During production, check:

- completed lanes increases over time
- exact Spinda counter equals completed lanes times `65,536`
- worker count matches intended count
- watcher status is `ok`
- zero-size ZIP count is `0`
- bad-name count is `0`
- stale temp files stay `0` or explainable
- disk free remains above warning threshold
- no worker stall warnings remain unexplained

## Safe During Active Run

- Viewing the command center page
- Restarting command center and watcher
- Running manifest-only ZIP validation
- Reading status JSON files
- Reading final ZIP file names
- Taking notes in documentation

## Avoid During Active Run

- Deleting or moving final lane ZIPs
- Editing worker-pool status JSON
- Deleting active lane status sidecars
- Running deep validation across the whole output set if disk/CPU is already
  saturated
- Decompressing PK3 files to disk
- Launching duplicate worker pools over the same lanes without understanding
  resume behavior

## Stop

Clean stop from browser:

- Use `Stop workers`.

Emergency stop:

```powershell
.\tools\spinda\phase3_command_center.cmd -Action Killswitch
```

Use killswitch only when workers do not respond to clean stop.

## Related Docs

- [PHASE3_COMMAND_CENTER_GUIDE.md](PHASE3_COMMAND_CENTER_GUIDE.md)
- [PHASE3_WATCHER_GUIDE.md](PHASE3_WATCHER_GUIDE.md)
- [PHASE3_RECOVERY_GUIDE.md](PHASE3_RECOVERY_GUIDE.md)
- [PHASE3_FINAL_VALIDATION_PLAN.md](PHASE3_FINAL_VALIDATION_PLAN.md)
