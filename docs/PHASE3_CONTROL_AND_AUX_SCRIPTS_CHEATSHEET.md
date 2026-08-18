# Phase 3 Control And Aux Scripts Cheatsheet

## Status Bucket

- Current status: Quick operator cheat sheet for redeploying the command
  center/watcher and running low-risk helper checks.
- Last verified date: 2026-04-30.
- Proven artifacts:
  - `<repo-root>\tools\spinda\phase3_command_center.cmd`
  - `<repo-root>\tools\spinda\phase3_command_center.ps1`
  - `<repo-root>\tools\spinda\phase3_command_center_web.py`
  - `<repo-root>\tools\spinda\phase3_independent_watcher.py`
  - `<repo-root>\tools\spinda\phase3_low_risk_check.ps1`
  - `<repo-root>\tools\spinda\phase3_zip_validator.py`
  - `<repo-root>\tools\spinda\build_phase3_cli_linux.sh`
  - `<repo-root>\tools\spinda\run_phase3_ledger_helper.sh`
  - `<repo-root>\tools\spinda\check_linux_helper_port.py`
- Known gaps: This sheet does not replace the full Phase 3 runbook,
  command-center guide, watcher guide, recovery guide, or final validation
  plan.
- Next action: Update this file whenever launch scripts, ports, or helper
  script names change.

## Evidence Split

### Proven

- The `.cmd` wrapper starts the command center and watcher.
- Restarting command center/watcher through the wrapper should not kill active
  `mgba-spinda-phase3.exe` workers.
- The low-risk check script reads command center status and runs manifest-only
  validation.
- Manifest-only validation does not open ZIP entries or extract PK3 files.

### Observed Once

- On 2026-04-30, the low-risk check reported command center `OK`, watcher `ok`,
  `12` workers, and no bad manifest artifacts while Phase 3 workers continued.

### Inferred

- These scripts are safe to run during normal production because they either
  read status or restart only monitoring processes, not active worker EXEs.

### Planned

- Keep this sheet as the first place to copy/paste routine operator commands.

### Obsolete

- Do not use ad hoc process-kill commands for normal command-center redeploy.
- Do not use deep ZIP validation as the routine "quick check" while workers are
  busy.

## Command Center And Watcher

### Start Both

Use when command center is not running, or watcher is missing:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd
```

What it does:

- starts Flask command center on port `235`
- starts independent watcher if missing
- does not start workers unless you use browser controls

Open:

```text
http://127.0.0.1:235
```

### Restart Monitoring Only

Use after command-center/watcher code or docs changed, or browser panel is
stale:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Restart
```

What it does:

- stops command center process
- stops watcher process
- starts command center again
- starts watcher again
- should not kill active `mgba-spinda-phase3.exe` worker processes

### Check Status

Use for quick command-center/watcher presence:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Status
```

What it reports:

- command center process
- watcher process
- API online/offline
- lane count
- Spinda count

### Open Browser

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Open
```

### Stop Command Center And Watcher

This stops monitoring processes, not worker production:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Stop
```

### Emergency Worker Killswitch

Use only when workers are stuck and clean stop fails:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Killswitch
```

This asks command center to kill known worker-pool and
`mgba-spinda-phase3.exe` PIDs.

## Direct Python Entrypoints

Normally use the `.cmd` wrapper. These are here for recovery/debugging.

### Command Center Direct

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_command_center_web.py --host 0.0.0.0 --port 235 --sample-interval 5 --zip-scan-interval 60 --host-resource-interval 15
```

### Watcher One-Shot

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_independent_watcher.py --once --print-summary
```

### Watcher Continuous

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_independent_watcher.py --folder <repo-root>\Phase3SpindaBlocks --pool-status <repo-root>\Phase3SpindaBlocks\_native_phase3_worker_pool_status.json --status-out <repo-root>\Phase3SpindaBlocks\_phase3_independent_watcher_status.json --events-out <repo-root>\Phase3SpindaBlocks\_phase3_independent_watcher_events.jsonl --command-center-url http://127.0.0.1:235/api/status --interval-seconds 300 --process-check-interval-seconds 300 --command-center-check-interval-seconds 300
```

### Merge Helper Ledger JSON

Use when a helper's Phase 3 output folder has been copied or handed back and
you want its `_phase3_lane_ledger.json` records folded into another folder:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\merge_phase3_json_ledgers.py
```

The script opens Windows folder pickers for source and destination folders.
It writes a destination ledger backup and a merge report. It skips live
`claimed`/`running` rows by default.

## Linux Helper Node

Build Phase 3 CLI on helper:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

Validate helper packaging from the source tree before copying or sending it:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\check_linux_helper_port.py --root <repo-root> --mode source --bash C:\msys64\usr\bin\bash.exe
```

This also checks Linux shell shebangs and LF-only line endings, so a
Windows-edited helper script does not fail later on the Linux box.

Run one proof lane from the coordinator ledger:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-proof-1 \
WORKERS=1 \
BATCH_SIZE=1 \
BUNDLE_SIZE=1 \
LANES=0x0001-0x0001 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

Run continuous helper production after proof validates:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-helper-1 \
WORKERS=6 \
BATCH_SIZE=24 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

## Low-Risk Helper Checks

### Combined Status Plus Manifest Check

This is the normal minor validation helper:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <repo-root>\tools\spinda\phase3_low_risk_check.ps1
```

What it does:

- reads command center API
- reports lanes, Spindas, workers, watcher status, and basic health counters
- runs manifest-only ZIP validation with `--allow-incomplete`
- does not stop workers
- does not resize workers
- does not open ZIP entries
- does not decompress PK3 files

JSON output:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <repo-root>\tools\spinda\phase3_low_risk_check.ps1 -Json
```

### Manifest-Only Validator

Use when you only want file-shape validation:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\Phase3SpindaBlocks --manifest-only --allow-incomplete
```

Checks:

- final ZIP filename shape
- completed lane count
- missing lane count
- bad ZIP-like names
- zero-size final ZIPs
- temp ZIP leftovers
- duplicate/weird artifacts

Does not:

- open ZIP entries
- read PK3 records
- extract to disk
- run PKHeX.Core

### Deep ZIP Validator

Use for batch/final validation, not routine hot checks:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\Phase3SpindaBlocks --allow-incomplete
```

This reads ZIP entries in RAM to force CRC/decompression validation. It still
does not extract PK3 files to disk.

## Important Files

Command center / watcher:

```text
<repo-root>\tools\spinda\phase3_command_center.cmd
<repo-root>\tools\spinda\phase3_command_center.ps1
<repo-root>\tools\spinda\phase3_command_center_web.py
<repo-root>\tools\spinda\phase3_independent_watcher.py
```

Aux checks:

```text
<repo-root>\tools\spinda\phase3_low_risk_check.ps1
<repo-root>\tools\spinda\phase3_zip_validator.py
<repo-root>\tools\spinda\check_linux_helper_port.py
```

Runtime status files:

```text
<repo-root>\Phase3SpindaBlocks\_native_phase3_worker_pool_status.json
<repo-root>\Phase3SpindaBlocks\_native_phase3_worker_pool_control.json
<repo-root>\Phase3SpindaBlocks\_phase3_independent_watcher_status.json
<repo-root>\Phase3SpindaBlocks\_phase3_independent_watcher_events.jsonl
```

Worker executable:

```text
<repo-root>\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe
```

## Normal Copy/Paste Routine

1. Check status and manifest:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <repo-root>\tools\spinda\phase3_low_risk_check.ps1
```

2. If command center or watcher missing, redeploy monitoring:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd -Action Restart
```

3. Recheck:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File <repo-root>\tools\spinda\phase3_low_risk_check.ps1
```

## Related Docs

- [PHASE3_RUNBOOK.md](PHASE3_RUNBOOK.md)
- [PHASE3_COMMAND_CENTER_GUIDE.md](PHASE3_COMMAND_CENTER_GUIDE.md)
- [PHASE3_WATCHER_GUIDE.md](PHASE3_WATCHER_GUIDE.md)
- [PHASE3_RECOVERY_GUIDE.md](PHASE3_RECOVERY_GUIDE.md)
- [PHASE3_FINAL_VALIDATION_PLAN.md](PHASE3_FINAL_VALIDATION_PLAN.md)
