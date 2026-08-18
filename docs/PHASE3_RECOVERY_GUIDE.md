# Phase 3 Recovery Guide

## Status Bucket

- Current status: Current recovery guide for Phase 3 production failures.
- Last verified date: 2026-04-30.
- Proven artifacts:
  - `tools/spinda/phase3_command_center.cmd`
  - `tools/spinda/native_phase3_worker_pool.py`
  - `tools/spinda/phase3_independent_watcher.py`
  - `tools/spinda/phase3_zip_validator.py`
- Known gaps: Recovery actions preserve or resume production. They do not
  replace final validation.
- Next action: Add new failure modes here when observed in production.

## Evidence Split

### Proven

- Final lane ZIPs are the hot-production resume boundary.
- Nonzero `0x####.spinda80.zip` files are skipped by filename during normal
  resume.
- Temporary ZIP names are not final output.
- Command center and watcher can be restarted without intentionally killing
  `mgba-spinda-phase3.exe` workers.

### Observed Once

- Antivirus false positives can kill or quarantine built executables.
- Command center and watcher restart did not stop already running worker EXEs.

### Inferred

- A computer crash can leave stale temp ZIPs or stale status JSON, while valid
  final ZIPs remain usable.

### Planned

- After full completion, final validation should decide whether any lane ZIP
  must be regenerated.

### Obsolete

- Do not manually edit status JSON to fake completion.
- Do not delete final ZIPs based only on command-center suspicion.

## First Rule

Do not delete final output while diagnosing.

Treat these as protected:

```text
Phase3SpindaBlocks\0x####.spinda80.zip
Phase2PickupStates\0x####.ss0
secondhalf.csv
```

## Fast Triage

1. Open command center.
2. Check watcher status.
3. Check worker count.
4. Check newest ZIP age.
5. Check pool status age.
6. Check zero-size, bad-name, and temp ZIP counters.
7. Decide which scenario below matches.

## Command Center Down, Workers Running

Symptoms:

- Browser page offline.
- OS still shows `mgba-spinda-phase3.exe` workers.

Action:

```powershell
.\tools\spinda\phase3_command_center.cmd -Action Restart
```

Expected:

- command center returns on port `235`
- watcher restarts
- workers keep running

## Watcher Missing, Workers Running

Symptoms:

- command center page online
- watcher panel says `missing`
- workers still active

Action:

```powershell
.\tools\spinda\phase3_command_center.cmd -Action Restart
```

This restarts command center and watcher only.

## Worker Pool Dead, Final ZIPs Present

Symptoms:

- no running workers
- completed lane count is still present
- pool status stale or missing

Action:

1. Restore any missing executable.
2. Start command center.
3. Use `Apply / launch workers`.
4. Confirm completed count does not reset.
5. Confirm existing final ZIPs are skipped.

## Antivirus Quarantined Executable

Symptoms:

- workers vanish
- executable missing or blocked
- final ZIPs remain

Action:

1. Restore executable from antivirus quarantine.
2. Add local exclusion if appropriate.
3. Start command center.
4. Relaunch workers.
5. Run manifest-only validation.

Do not regenerate completed lanes unless validation later proves bad output.

## Power Loss Or Reboot

Action:

1. Boot machine.
2. Confirm output drive mounted.
3. Start command center:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

4. Run manifest-only validation:

```powershell
python .\tools\spinda\phase3_zip_validator.py --root .\Phase3SpindaBlocks --manifest-only --allow-incomplete
```

5. Relaunch workers from command center.
6. Confirm resume skips existing final ZIP names.

## Stale Temp ZIPs

Symptoms:

- `0x####.spinda80.zip.*.tmp` files exist
- no worker for that same lane is running

Action:

- Safe to delete stale temp ZIPs only after confirming no worker owns that lane.
- Do not delete final `0x####.spinda80.zip`.

## Zero-Size Or Tiny Final ZIP

Symptoms:

- watcher or command center reports zero-size/tiny final ZIP

Action:

1. Stop workers that could write the affected lane.
2. Move suspicious final ZIP aside for evidence, or record hash/size.
3. Delete only the bad final ZIP after evidence is saved.
4. Relaunch worker for that lane.
5. Run deep validation on regenerated lane.

## Bad ZIP Name

Symptoms:

- bad ZIP-like filename reported

Action:

- If file is a temp or old artifact, archive or delete after workers are stopped.
- If file is intended output, rename only after proving exact intended lane and
  ensuring no correct final ZIP already exists.

## Disk Low

Symptoms:

- watcher disk warning or important

Action:

1. Stop launching new workers.
2. Let active workers finish or clean-stop them.
3. Move non-production artifacts first.
4. Do not move active final ZIPs while workers are writing.
5. Resume only after free space is back above threshold.

## Safe To Delete

Only after confirming no active writer:

- stale temp ZIPs
- old watcher event logs
- old command center stdout/stderr logs
- obsolete validation reports already copied elsewhere

## Do Not Touch During Recovery

- valid final lane ZIPs
- Phase 2 pickup states
- source ROM/save/savestate inputs
- `secondhalf.csv`
- active lane status sidecars
- worker-pool control JSON unless using the command center or intentionally
  changing desired worker count

## After Any Recovery

Run:

```powershell
python .\tools\spinda\phase3_zip_validator.py --root .\Phase3SpindaBlocks --manifest-only --allow-incomplete
```

Then verify:

- completed lane count sane
- no zero-size final ZIPs
- no unexplained temp files
- watcher status returns to `ok`
- workers begin new lanes

## Related Docs

- [PHASE3_RUNBOOK.md](PHASE3_RUNBOOK.md)
- [PHASE3_COMMAND_CENTER_GUIDE.md](PHASE3_COMMAND_CENTER_GUIDE.md)
- [PHASE3_WATCHER_GUIDE.md](PHASE3_WATCHER_GUIDE.md)
- [PHASE3_FINAL_VALIDATION_PLAN.md](PHASE3_FINAL_VALIDATION_PLAN.md)
