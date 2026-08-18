# Phase 3 Watcher Guide

## Status Bucket

- Current status: Current guide for the independent Phase 3 watcher.
- Last verified date: 2026-05-02.
- Proven artifacts:
  - `tools/spinda/phase3_independent_watcher.py`
  - `tools/spinda/phase3_command_center.ps1`
  - `tools/spinda/phase3_command_center_web.py`
- Known gaps: The watcher is a health monitor. It does not validate every PK3
  record.
- Next action: Update this guide whenever watcher checks, status fields, or
  thresholds change.

## Evidence Split

### Proven

- Wrapper launch starts watcher if missing.
- Watcher writes its own status JSON and event JSONL.
- Watcher is read-only toward workers and output ZIP contents.
- Watcher checks output filenames, worker-pool status JSON, command-center API
  status, host worker PIDs, and disk free space.
- Watcher does not decompress ZIPs or parse PK3 records.
- ZIP-stall checks use the active lane elapsed timer from worker-pool status.
  A freshly restarted worker pool no longer warns only because the newest
  completed ZIP came from an older run.

### Observed Once

- Watcher reported `ok` with matching reported/OS worker counts during active
  production.

### Inferred

- Watcher can detect many crash/stall cases faster than waiting for manual
  browser inspection.

### Planned

- Use watcher status as the command center's independent health signal.

### Obsolete

- Do not use the watcher as a worker restarter.
- Do not use watcher output as final data validation proof.

## What Watcher Does

The watcher builds an independent health snapshot from:

- final lane ZIP filenames and mtimes
- temporary ZIP files
- worker-pool status JSON
- command-center API status
- host process list
- disk free space

It emits:

```text
Phase3SpindaBlocks\_phase3_independent_watcher_status.json
Phase3SpindaBlocks\_phase3_independent_watcher_events.jsonl
```

## What Watcher Never Does

- no worker kill
- no worker restart
- no file delete
- no ZIP decompression
- no PK3 parsing
- no source save or savestate mutation

This is intentional. The watcher is a safety signal, not a control loop.

## Launch

Normal launch comes from:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

Manual one-shot:

```powershell
python .\tools\spinda\phase3_independent_watcher.py --once --print-summary
```

Manual continuous:

```powershell
python .\tools\spinda\phase3_independent_watcher.py --interval-seconds 300
```

## Low-Overhead Behavior

Default interval:

- watcher sample: `300` seconds
- host process check cache: `300` seconds
- command-center API check cache: `300` seconds

If the interval is lowered for debugging, process/API checks stay cached unless
their cache window expires. This prevents the watcher from competing with
Phase 3 workers.

## Main Status Values

- `ok`: no checks active.
- `warning`: attention needed, but production may still be running.
- `important`: likely broken or stalled production condition.
- `missing`: command center could not find watcher status file.

The command center displays these values in the watcher panel.

## Main Checks

Important check classes:

- worker-pool status missing or stale
- command-center API unreachable
- command-center count differs from watcher folder scan
- pool reports worker PID not found in OS process list
- OS worker process not present in pool status
- no running workers when production is expected to be active
- newest ZIP age exceeds stall threshold after the active lane has also run
  long enough to make a stall meaningful
- zero-size final ZIP
- suspicious tiny final ZIP
- bad ZIP-like filename
- stale temp ZIP
- disk free below threshold
- worker lane running longer than warning threshold

## How To Read A Warning

Use this order:

1. Read watcher `code`.
2. Check command-center worker slots.
3. Check newest ZIP age.
4. Check worker-pool status age.
5. Check OS worker count.
6. Only then decide whether to restart panel, stop workers, or use recovery
   steps.

## Safe Response

- Watcher missing: restart command center/watcher only.
- Command center unreachable but workers alive: restart command center/watcher.
- Pool status stale and no workers alive: follow recovery guide.
- ZIP output stalled but workers alive: inspect worker timers before killing.
- Disk warning: plan copy/cleanup after safe stop, not while writing active
  lane ZIPs.

## Related Docs

- [PHASE3_COMMAND_CENTER_GUIDE.md](PHASE3_COMMAND_CENTER_GUIDE.md)
- [PHASE3_RECOVERY_GUIDE.md](PHASE3_RECOVERY_GUIDE.md)
- [PHASE3_FINAL_VALIDATION_PLAN.md](PHASE3_FINAL_VALIDATION_PLAN.md)
