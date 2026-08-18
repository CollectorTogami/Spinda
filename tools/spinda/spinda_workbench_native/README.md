# Spinda Workbench Native

## Status Bucket

- Current status: Active native C++ port of the read-only Spinda Workbench.
- Last verified date: 2026-05-07.
- Proven artifacts: `spinda_workbench_native.cpp`,
  `build_spinda_workbench_native.bat`, `spinda_workbench_native.exe`, and
  `src/platform/python/tests/examples/test_spinda_workbench_native.py`.
- Known gaps: The legacy Flask workbench remains in-tree as a reference and
  fallback while operators move to this native executable. Deep ZIP entry proof,
  PKHeX legality proof, and hatch output proof still belong to the separate
  validators shown in command previews.
- Short version: [README-tldr.md](README-tldr.md) keeps the same facts in
  caveman-full form.
- Evidence model: Claims use `Proven`, `Observed once`, `Inferred`, `Planned`,
  and `Obsolete` per
  DOCUMENTATION_EVIDENCE_POLICY.md.

This is the standalone C++ server/CLI for the Spinda Workbench. It moves the
active Workbench runtime off Python/Flask so folder scans and PID scoring do
less work per request. The browser is still HTML/JavaScript, because it is a
browser UI, but the server, scans, PID locator, painter math, scoring, command
previews, and JSON API are native C++.

## What This Program Is

The native Workbench is a read-only panel for checking where the Spinda project
stands right now. It does not create lanes, hatch eggs, edit saves, delete
files, or start workers. It scans the project folders, shows readiness, maps a
PID to the lane/TSV relationship, draws the Spinda pattern, and prints command
previews for the heavier validators.

The old Python/Flask panel was useful, but it dragged more runtime baggage than
this job needs. The native port keeps the same practical surface and moves the
scan/scoring work into C++.

## Data It Reads

| Input | Default location | Used for |
| --- | --- | --- |
| Phase 3 lane ZIPs | `Phase3SpindaBlocks` | Lane completion, bad artifacts, PID-to-lane lookup. |
| TSV save bank | `TSVs` | Save-bank completion, TSV/SID validation, duplicate and mismatch reports. |
| SID ledger | `TSVs\_sid_shiny_value_ledger_tid_0x0000.json` | Optional done/error count for TSV production history. |
| Hatch output folder | `HatchedSpindaZips` | Readiness and command previews only. |
| Validator tools | Repo-local tools under `tools\spinda` | Command preview strings only. |

The Workbench does not modify these inputs. If a path is missing, or if a file
is passed where a folder was expected, the API reports that directly.

## Operator Workflow

Typical use:

1. Build the native executable.
2. Start it from the repo root or pass explicit `--root` / folder options.
3. Open the local URL.
4. Check Phase 3 lane count, TSV save count, and hatch readiness blockers.
5. Use PID Locator to inspect a candidate PID, lane ZIP path, PSV/TSV, and
   visual pattern.
6. Use Pattern Automation for small bounded PID searches.
7. Copy a command preview into a terminal only when you want to run the
   validator or hatch tool.

The panel refreshes `/api/status` every five seconds. Pattern searches are
bounded by `MAX_SUGGESTION_SCAN` and `MAX_SUGGESTION_COUNT` in source so a
browser click cannot accidentally kick off an unbounded scan.

## Build

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\build_spinda_workbench_native.bat
```

The build uses the local MSYS2 MinGW compiler:

```text
C:\msys64\mingw64\bin\g++.exe
```

The executable is statically linked against the GCC C++ runtime so it can run
from a normal PowerShell session without adding MinGW DLL folders to `PATH`.

## Run

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe
```

Default URL:

```text
http://127.0.0.1:8780/
```

It binds to `0.0.0.0` by default and stays read-only. No workers are launched.

## CLI JSON Modes

The native executable can also return the API payloads directly:

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --status-json
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --pid 0x12345678 --tid 0 --sid 0
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --suggest funny --start 0x00000000 --scan-limit 8192 --count 12
```

Useful path and scan options:

```powershell
--root <repo-root>
--phase3-dir <repo-root>\Phase3SpindaBlocks
--tsv-dir <repo-root>\TSVs
--hatch-output-dir <repo-root>\HatchedSpindaZips
--target-phase3-lanes 65534
--sample-limit 16
--host 0.0.0.0
--port 8780
```

## Ported Behavior

- Phase 3 lane ZIP scan from filenames and settled sizes.
- Bad ZIP names, temp ZIPs, zero-size ZIPs, tiny ZIPs, duplicate lanes, and
  out-of-scope lanes.
- TSV save-bank scan from decimal `TSV-xxxx-sid-xxxxx.sav` filenames.
- Optional SID ledger done/error summary.
- PID locator from `0xUUUULLLL` to lane ZIP, ZIP entry name, PSV, matching TSV,
  and TID0 SID range.
- Local Spinda Painter spot grid, SVG preview, nature/gender/ability, shiny
  math, visual taxonomy scores, and labels.
- Bounded pattern search with allocation-light PID scoring and top-N retention.
- Read-only HTTP endpoints:

```text
GET /
GET /api/status
GET /api/commands
GET /api/pid/<pid>?tid=0&sid=0
GET /api/suggest/<mode>?start=0x00000000&scan_limit=8192&count=12&tid=0&sid=0
GET /favicon.ico
HEAD /api/status
```

## API Contract

The server is small on purpose. It accepts `GET` and `HEAD`; unsupported
methods return JSON `405`.

| Route | Body type | Notes |
| --- | --- | --- |
| `/` | HTML | Browser dashboard with embedded JavaScript and CSS. |
| `/api/status` | JSON | Full scan snapshot, readiness, samples, command previews, and server metadata. |
| `/api/commands` | JSON | Command preview strings without scan payloads. |
| `/api/pid/<pid>` | JSON | PID report. `pid` accepts eight hex digits, optional `0x`, optional `.pk3`. |
| `/api/suggest/<mode>` | JSON | Pattern suggestion search for a bounded PID window. |
| `/favicon.ico` | empty | Returns `204 No Content` to keep browser probes quiet. |

Query parsing mirrors Flask/Werkzeug in the places that affect the legacy
contract: duplicate keys keep the first value, query `+` decodes to space, and
path `+` remains a literal plus. Malformed percent escapes return HTTP `400`.

## Status JSON Guide

Useful `/api/status` sections:

| Field | Meaning |
| --- | --- |
| `server` | Runtime label, version-ish metadata, root path, and generated timestamp. |
| `phase3` | Lane ZIP counts, progress, artifact counts, lane ranges, last good lane, and samples. |
| `tsv` | Save-bank counts, duplicate/mismatch/invalid counts, recent saves, and ledger summary. |
| `readiness` | Boolean hatch-readiness result plus blockers. |
| `commands` | PowerShell-safe command previews for follow-up tools. |

Samples are capped by `--sample-limit` so one messy folder does not swamp the
browser or API response. Use `--sample-limit 0` when counters are enough.

## PID And Pattern Search

PID locator derives:

- upper and lower PID halves
- expected lane ZIP path
- expected ZIP entry name
- expected PSV and matching TSV
- TID0 SID range
- local Spinda Painter SVG
- nature, gender, ability slot, shiny status, rarity, and pattern labels

Pattern search scores candidate PIDs by visual traits such as centered, eye
cover, symmetry, heart-ish, cursed, clustered, and spread. It keeps only the
current top-N rows in memory. It does not scan the whole 32-bit PID space; that
would need separate batch tooling.

## Optimization Notes

- Phase 3 scan uses a fixed 65,536-byte lane bitmap instead of a dynamic set.
- Phase 3 ZIP/tmp names, PID text, and TSV save names use direct ASCII parsers
  instead of `std::regex` in hot paths.
- Numeric CLI/query values use a direct bounded parser: decimal stays decimal
  even with leading zeroes, while hex requires an explicit `0x`/`0X` prefix.
- SID ledger JSON is parsed once with the local minimal JSON parser so falsey
  values such as `""`, `0`, `false`, and `null` do not inflate error counts.
- Pattern search scores directly from PID nibbles inside the hot loop.
- Suggestion results use a fixed-size worst-first heap and keep only the
  requested top-N rows instead of sorting the whole scan window.
- JSON rows, ZIP existence checks, labels, traits, and SVG work are created only
  for final winners.
- Recent TSV save names are captured once during scan before sorting, avoiding
  repeated `filesystem::path::filename()` work in the comparator.
- Tool readiness now reports `age_seconds` for existing helper files.
- Duplicate query keys keep the first value, matching Flask/Werkzeug
  `request.args.get()`.
- URL path decoding keeps literal `+` characters distinct from query-space
  decoding.
- Malformed URL percent escapes return HTTP `400` instead of falling through as
  literal route text.
- `HEAD` requests return headers without a body, and malformed request lines
  return HTTP `400`.
- `POST` and other unsupported methods return a JSON `405` that names the
  supported `GET`/`HEAD` methods.
- Browser-side JSON fetch handling is centralized so HTTP failures, API error
  payloads, and non-JSON responses surface cleanly in the panel.
- `/favicon.ico` returns `204 No Content` to avoid browser probe noise.
- The server is a small dependency-free socket handler instead of Flask.

## Safety Boundaries

- Read-only by design: the server has no delete, move, edit, hatch, or worker
  launch endpoint.
- Command previews are plain strings. The workbench does not execute them.
- Numeric query and CLI values are bounded before use.
- Bad request lines, bad PIDs, bad numeric values, malformed percent escapes,
  and unsupported methods return explicit HTTP errors.
- The native server handles one client per detached thread. Keep it on localhost
  or a trusted LAN. It is an operator tool, not an internet service.
- Deep ZIP integrity, PKHeX legality, and hatch proof are delegated to the
  validator tools. The Workbench is for readiness and command previews, not
  final proof.

## Troubleshooting

| Symptom | Likely cause | Action |
| --- | --- | --- |
| Browser cannot connect | Server not running or port already occupied | Start the executable with `--port N` or check `Get-NetTCPConnection`. |
| Phase 3 folder error | Path missing or points at a file | Pass `--phase3-dir` or fix the folder path. |
| TSV count wrong | Save names do not match `TSV-xxxx-sid-xxxxx.sav` or SID/TSV mismatch | Check `tsv.samples` and duplicate/mismatch counters. |
| PID lookup says ZIP missing | Lane has not been produced or path option points elsewhere | Verify the `lane_zip` path from the PID payload. |
| Pattern search returns HTTP 400 | Bad `start`, `scan_limit`, `count`, `tid`, or `sid` query | Use decimal or explicit `0x` hex and stay inside documented bounds. |
| Panel shows API error | Server returned JSON error or a non-JSON response | Check route, PID text, and query values. |

## Audit Notes

2026-05-07 native audit pass:

- Replaced hot filename/PID TSV regular expressions with direct bounded parsers.
- Replaced loose SID ledger regex counting with a small JSON parser and Python
  truthiness-compatible ledger counting.
- Replaced pattern-suggestion `std::set` retention with a worst-first heap.
- Added PowerShell-safe command previews that prefer the repo-local Python venv
  when present.
- Added real tool `age_seconds` reporting instead of placeholder `null` values.
- Added tests for case-insensitive scan names, endpoint lane scope, zero sample
  limits, ledger falsey values, legacy ledger counts, malformed ledger payloads,
  suggestion wraparound/count clamping, command quoting, tool ages, bad PID
  errors, HTTP 400 errors, and Python-reference trait parity.

2026-05-07 second native audit pass:

- Added explicit `not a directory` folder-error reporting when scan path options
  point at files instead of folders.
- Matched Flask's first-value query behavior for duplicate query parameters.
- Fixed URL path decoding so `+` remains a literal plus outside query strings.
- Added `HEAD` support and clearer HTTP `400` handling for malformed request
  lines.
- Added regressions for file-valued scan roots, duplicate query keys, literal
  plus PID paths, `HEAD /api/status`, and malformed raw HTTP request lines.

2026-05-07 third native audit pass:

- Centralized browser API fetch handling in `fetchJson(...)` and added a small
  inline comment to keep future UI calls on the same error path.
- Added explicit `/favicon.ico` `204 No Content` handling so normal browser
  probes do not look like missing API routes.
- Updated unsupported-method errors from `GET only` to `GET or HEAD only`.
- Added regressions for negative/out-of-range numeric CLI options, HTTP
  `scan_limit=0`, unsupported `POST`, favicon probes, and the browser
  `fetchJson(...)` helper.

2026-05-07 fourth native audit pass:

- Replaced `std::stoull(..., base 0)` numeric option parsing with a direct
  bounded parser so `010` is decimal ten, not octal eight.
- Tightened URL decoding so malformed percent escapes return HTTP `400`.
- Fixed `--target-phase3-lanes 65535` so lane `0xFFFF` is in scope; before this
  pass the target count and accepted lane range disagreed.
- Removed a now-unused C runtime include from the native source.
- Added regressions for leading-zero decimal parsing, uppercase hex prefixes,
  trailing numeric garbage, malformed percent-escaped query text, and the
  `65535`/`0xFFFF` Phase 3 boundary.

## Legacy Python Status

`tools/spinda/spinda_workbench/spinda_workbench.py` remains available as a
legacy reference and fallback. New operator runs should use the native C++
workbench. The old Python tests are still useful for guarding the reference
contract; the native tests guard the C++ implementation.

## Audit And Verification

Focused commands:

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\build_spinda_workbench_native.bat
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --self-test
<repo-root>\.venv-mgba\bin\python.exe -m pytest -q src\platform\python\tests\examples\test_spinda_workbench_native.py
C:\msys64\mingw64\bin\g++.exe -std=c++20 -O2 -Wall -Wextra -Wpedantic -Wconversion -static -static-libgcc -static-libstdc++ tools\spinda\spinda_workbench_native\spinda_workbench_native.cpp -o %TEMP%\spinda_workbench_native_audit.exe -lws2_32
<repo-root>\.venv-mgba\bin\python.exe tools\check_markdown_mirrors.py
```

Coverage points:

- Native build and built-in self-test.
- Phase 3 scan contract, mixed-case names, endpoint scope, zero-sample mode, and
  the `65535` target-lane boundary.
- TSV scan, ledger summary, falsey ledger values, legacy ledger counts, and
  malformed ledger payloads.
- PID report, painter spot math, SVG payload, bad PID rejection, and Python
  reference score parity.
- Pattern suggestion ordering, wraparound, count clamping, and top-N behavior.
- Command quoting, repo-venv Python command preference, and tool age reporting.
- Real native HTTP server responses for `/`, `/api/status`, `/api/pid`, and
  HTTP 400 error payloads.
- HTTP edge cases: `HEAD /api/status`, malformed request lines, duplicate query
  keys, literal `+` in route paths, unsupported `POST`, invalid numeric query
  ranges, malformed percent escapes, and favicon probes.
- CLI edge cases: bad PID text, leading-zero decimal input, uppercase hex
  prefixes, trailing numeric garbage, and negative/out-of-range numeric options.
