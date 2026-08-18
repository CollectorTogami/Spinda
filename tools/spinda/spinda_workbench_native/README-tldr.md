# Spinda Workbench Native TLDR

Caveman-full short doc. For exact audit trail, use [README.md](README.md).

## What It Is

Native Workbench = read-only C++ operator panel for Spinda project.

It scans folders, shows status, maps PID to lane/TSV, draws Spinda preview,
scores patterns, shows command previews. It does not hatch, edit, delete, move,
or start workers.

Legacy Python/Flask Workbench still exists as fallback/reference. Native C++
one is normal path now.

## Build

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\build_spinda_workbench_native.bat
```

## Run

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe
```

Default URL:

```text
http://127.0.0.1:8780/
```

If port busy:

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --host 127.0.0.1 --port 8781
```

## Reads

| Input | Default | Use |
| --- | --- | --- |
| Phase 3 ZIPs | `Phase3SpindaBlocks` | lane count, bad artifacts, PID lane lookup |
| TSV saves | `TSVs` | save-bank count, duplicates, SID/TSV mismatch |
| SID ledger | `TSVs\_sid_shiny_value_ledger_tid_0x0000.json` | optional done/error count |
| hatch folder | `HatchedSpindaZips` | readiness + command previews |
| validators | `tools\spinda` | preview command strings only |

Read-only. Missing folder = reported issue. File where folder expected =
`not a directory`.

## Main UI

- Phase 3 lanes: complete lanes, missing lanes, bad ZIP/temp/tiny/zero files.
- Spinda records: total expected/completed records.
- TSV saves: complete saves, duplicates, invalid names, mismatches, ledger.
- Hatch readiness: blocker list.
- PID Locator: PID -> lane ZIP, entry name, PSV, TSV, SID range, SVG preview.
- Pattern Automation: bounded scan for visual patterns.
- Command Preview: commands to run manually outside Workbench.
- Samples: capped examples of bad artifacts.

## API

| Route | Meaning |
| --- | --- |
| `GET /` | dashboard HTML |
| `GET /api/status` | full scan JSON |
| `GET /api/commands` | command preview JSON |
| `GET /api/pid/<pid>` | PID report |
| `GET /api/suggest/<mode>` | bounded pattern search |
| `GET /favicon.ico` | `204 No Content` |
| `HEAD /api/status` | headers only |

Bad input -> JSON HTTP error. Unsupported method -> `405`.

## CLI JSON

```powershell
.\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --status-json
.\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --pid 0x12345678 --tid 0 --sid 0
.\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --suggest funny --start 0x00000000 --scan-limit 8192 --count 12
```

## Useful Options

| Option | Meaning |
| --- | --- |
| `--root PATH` | repo root |
| `--phase3-dir PATH` | lane ZIP folder |
| `--tsv-dir PATH` | TSV save folder |
| `--hatch-output-dir PATH` | hatch output folder |
| `--target-phase3-lanes N` | target lane count |
| `--sample-limit N` | max sample strings per issue |
| `--host HOST` | bind host |
| `--port N` | bind port |

## Input Rules

- Decimal numbers stay decimal, even `010`.
- Hex numbers need `0x` or `0X`.
- Query duplicate keys keep first value.
- Query `+` means space.
- Path `+` stays plus.
- Bad percent escapes return HTTP `400`.
- `--target-phase3-lanes 65535` includes lane `0xFFFF`.

## Safety

- No delete endpoint.
- No write endpoint.
- No hatch endpoint.
- No worker launch endpoint.
- Command previews are text only.
- Keep server on localhost/trusted LAN.
- Deep ZIP proof, PKHeX legality, hatch output proof happen in separate tools.

## Verify

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --self-test
<repo-root>\.venv-mgba\bin\python.exe -m pytest -q src\platform\python\tests\examples\test_spinda_workbench_native.py
```

## Common Trouble

| Symptom | Fix |
| --- | --- |
| cannot connect | start exe, change `--port`, check port owner |
| bad Phase 3 count | check `phase3.samples` and folder path |
| bad TSV count | check save names and SID/TSV mismatch samples |
| PID says ZIP missing | lane not produced or wrong `--phase3-dir` |
| pattern search 400 | fix `start`, `scan_limit`, `count`, `tid`, `sid` |
| panel API error | read JSON error text; route/query/PID likely bad |
