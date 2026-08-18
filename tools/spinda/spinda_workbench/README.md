# Spinda Workbench

## Status Bucket

- Current status: Legacy Python/Flask reference for the read-only Spinda
  Workbench. The active optimized runtime is the native C++ port at
  [../spinda_workbench_native/README.md](../spinda_workbench_native/README.md).
- Last verified date: 2026-05-07.
- Proven artifacts: `tools/spinda/spinda_workbench/spinda_workbench.py`,
  `src/platform/python/tests/examples/test_spinda_workbench.py`, mirrored
  copies under `github-clean`, and focused local verification commands listed
  in [Audit And Verification](#audit-and-verification).
- Known gaps: Hot status scans validate filenames, settled sizes, status JSON,
  and TSV naming only. Deep ZIP entry proof, PKHeX legality proof, and hatch
  output proof still belong to the separate validators shown in command
  previews.
- Next action: Prefer the native C++ workbench for operator runs. Re-run focused
  tests after changing scan scope, command preview construction, painter math,
  pattern scoring, or API payloads in either implementation.
- Evidence model: Claims use `Proven`, `Observed once`, `Inferred`, `Planned`,
  and `Obsolete` per
  `DOCUMENTATION_EVIDENCE_POLICY.md`.

Unified read-only dashboard for the Spinda project after Phase 3. This Python
version is kept as a reference/fallback. The C++ port is now the optimized
operator path. Both versions do not start workers, export saves, mutate ZIPs, or
open every PK3 entry in the hot status path.

## Native C++ Port

Build and run the optimized native workbench:

```powershell
<repo-root>\tools\spinda\spinda_workbench_native\build_spinda_workbench_native.bat
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe
```

The native port moves the server, Phase 3 scan, TSV scan, PID locator, painter
math, pattern scoring, command previews, and JSON API to C++. See
[Spinda Workbench Native](../spinda_workbench_native/README.md).

## Evidence Split

### Proven

- Workbench scans Phase 3 ZIP filenames without opening PK3 entries.
- Workbench scans TSV save names and optional SID ledger JSON.
- PID locator maps `0xUUUULLLL` to lane ZIP `0xLLLL.spinda80.zip`, entry name,
  PSV, matching TSV, and TID0 SID range.
- Pattern suggestions use bounded direct PID scoring plus top-N heap retention.
- Command previews use PowerShell call-operator syntax plus single-quoted path
  arguments so custom directories with spaces or apostrophes remain
  copy/paste-safe.
- The Flask index serves the static HTML directly instead of passing it through
  template rendering.
- Default Phase 3 scope is `0x0001..0xFFFE`; endpoint or extra lane ZIPs are
  reported as out-of-scope artifacts unless the target is explicitly set to all
  `65,536` lanes.

### Observed once

- On 2026-05-06, the local private workspace had a complete TSV save bank and
  incomplete Phase 3 ZIP folder when checked through `build_snapshot()`.

### Inferred

- Tiny ZIP threshold is a dashboard hygiene check. It is not a substitute for
  deep ZIP validation.

### Planned

- Full hatch-splitter readiness remains blocked until Phase 3 ZIP production and
  validator passes are complete.

### Obsolete

- Treating `0x0000` or `0xFFFF` as normal default Phase 3 ZIP completion lanes is
  obsolete. Those endpoints are outside the organic FR/LG default lane scope.

## Run

```powershell
python <repo-root>\tools\spinda\spinda_workbench\spinda_workbench.py
```

Default URL:

```text
http://127.0.0.1:8780/
```

It binds to `0.0.0.0` by default and prints LAN URLs when it can detect them.

## What It Shows

- Phase 3 lane ZIP count from `Phase3SpindaBlocks`
- exact generated Spinda record count from lane count times `65,536`
- bad Phase 3 output names, temp ZIPs, zero-size ZIPs, tiny ZIPs, duplicates
- out-of-scope Phase 3 ZIPs such as endpoint lanes under the default target
- TSV save-bank progress from decimal `TSV-xxxx-sid-xxxxx.sav` filenames
- SID ledger done/error counts when `_sid_shiny_value_ledger_tid_0x0000.json`
  exists
- hatch-splitter readiness blockers
- PID locator: PID to lane ZIP, ZIP entry name, PSV, matching TSV, and SID
  range for TID `0`
- local Spinda Painter panel: PID-to-spot preview, original painter coordinate
  grid, nature/gender/ability slot, TID/SID shiny rarity, and visual labels
- bounded pattern automation for centered, balanced, eye-covering, symmetric,
  clustered, wide-spread, heart-ish, funny-face, and cursed-face candidate PIDs
- command previews for manifest validation, deep ZIP validation, PKHeX
  validation, TSV party verification, hatch splitting, and ZIP-to-7z GUI

## Safety

The workbench is read-only. It uses filename and status-file scans for live
panels. Heavy proof still belongs in the separate validators:

```powershell
python <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\Phase3SpindaBlocks --manifest-only
python <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\Phase3SpindaBlocks
dotnet run --project <repo-root>\tools\verify_tsv_party_slot\VerifyTsvPartySlot.csproj -- --save-dir <repo-root>\TSVs
```

The dashboard prints these commands but does not run them.

Command previews use the same Python interpreter that launched the workbench,
prefix quoted Python executables with PowerShell's `&` call operator, and
single-quote all path arguments. If you override folders with spaces or
apostrophes, the displayed commands should still be copy/paste-safe in
PowerShell.

IPv6 bind hosts are displayed with bracketed URL hostnames, such as
`http://[::1]:8780/`.

Phase 3 ZIP/tmp suffixes and PID `.pk3` suffixes are matched
case-insensitively. The canonical command output still uses lowercase
`0xLLLL.spinda80.zip` and `0xUUUULLLL.pk3` names.

## Target Lane Scope

Default Phase 3 target is `65,534` lanes, matching organic FR/LG lower-half
outputs `0x0001..0xFFFE`.

- `--target-phase3-lanes 4` checks lanes `0x0001..0x0004`.
- `--target-phase3-lanes 65534` checks lanes `0x0001..0xFFFE`.
- `--target-phase3-lanes 65536` checks the full raw 16-bit range
  `0x0000..0xFFFF`.

ZIPs outside the active target range are counted as `out_of_scope_zips` and are
included in `bad_artifacts`, so hatch readiness cannot silently pass with
endpoint or extra-lane files in the folder.

## PID Locator

For a PID `0xUUUULLLL`:

- lane ZIP is `Phase3SpindaBlocks\0xLLLL.spinda80.zip`
- entry name is `0xUUUULLLL.pk3`
- PSV is `(UUUU ^ LLLL) >> 3`
- with TID `0`, matching TSV is that PSV
- matching SID range is `(PSV << 3)` through `(PSV << 3) | 7`

The locator does not prove the entry exists inside the ZIP. It tells you where
the entry must live. Run the deep ZIP validator for proof.

## Painter And Pattern Search

The painter panel is a clean local companion to the original Spinda Painter
(`https://spindapainter.neocities.org/`). It mirrors the original spot-coordinate
model: the lowest PID nibble is spot 1 X, the next nibble is spot 1 Y, then the
same X/Y order repeats for spots 2, 3, and 4. The workbench does not vendor the
old site's image assets; it renders a small original SVG preview from the same
grid so the dashboard stays self-contained.

For shiny math, enter Trainer ID and Secret ID in the painter row. The default is
`0 / 0`. The archive mapping still assumes project TID `0` when it reports the
matching TSV and SID range.

Pattern search is a bounded top-N scan, not a corpus mutation. Pick a mode,
start PID, scan size, and result count. The app scores only that range and shows
the highest-ranked candidates:

- `centered`, `balanced`, `clustered`, and `spread` measure spot placement.
- `eye_cover`, `symmetry`, and `heart` look for common visual motifs.
- `funny` and `cursed` are heuristic blends for quick atlas curation.

These labels are taxonomy helpers, not game mechanics. The PSV/TSV and shiny
calculation fields remain the mechanical values.

## Pattern Search Performance

Pattern suggestions are deliberately lightweight:

- the scan uses only PID nibble math and four spot centers in the hot loop
- the center data is stored as one flat tuple so each scanned PID avoids nested
  coordinate objects
- the scanner calculates only the selected score mode instead of computing the
  full taxonomy for every PID
- the suggestion hot loop uses a direct PID scorer, so it does not allocate the
  center tuple at all while scanning; the tuple path is kept for rich reports
  and tested against the direct scorer
- it keeps a small top-N heap instead of sorting every PID in the scanned range;
  heap ordering uses unrounded scores and rounds only the final displayed values
- full JSON rows, ZIP-existence checks, labels, and SVG work are built only for
  the final winning candidates
- the API response includes elapsed time and approximate PID/s for the scan

This means the panel is comfortable for quick windows such as `8,192` or
`65,536` PIDs and still usable for larger exploratory windows. It remains a
local Flask tool, so million-PID scans can take several seconds and will occupy
one Python request thread while they run.

## Scoring Maintenance Notes

There are two score paths:

- rich reports use `spinda_spots()` plus `spinda_traits()` so the JSON output
  can include named spot rectangles, labels, and SVG preview data
- suggestion scans use the direct PID scorer inside `suggest_patterns()` to keep
  the per-PID work small

The unit tests compare both paths across several PIDs. If a score formula is
changed, update the shared helper or both score paths together and keep those
parity tests passing.

## API

The browser uses these read-only endpoints:

```text
GET /api/status
GET /api/commands
GET /api/pid/<pid>?tid=0&sid=0
GET /api/suggest/<mode>?start=0x00000000&scan_limit=8192&count=12&tid=0&sid=0
```

`/api/pid` accepts `0x12345678`, `12345678`, or `0x12345678.pk3`. The suggestion
endpoint accepts decimal or `0x` values for `start`, `tid`, and `sid`; empty
optional numeric query parameters fall back to their defaults. The request cap
is intentionally bounded so a browser click cannot accidentally try to rank the
whole four-billion-PID space in one Flask request.

## Options

```powershell
python <repo-root>\tools\spinda\spinda_workbench\spinda_workbench.py `
  --phase3-dir <repo-root>\Phase3SpindaBlocks `
  --tsv-dir <repo-root>\TSVs `
  --host 0.0.0.0 `
  --port 8780
```

Use `--target-phase3-lanes` if a partial proof run should be judged against a
smaller lane count. Numeric CLI options accept decimal or `0x` notation and are
range-checked before the server starts.

## Audit And Verification

Latest focused audit command set:

```powershell
<repo-root>\.venv-mgba\bin\python.exe -m py_compile tools\spinda\spinda_workbench\spinda_workbench.py src\platform\python\tests\examples\test_spinda_workbench.py github-clean\tools\spinda\spinda_workbench\spinda_workbench.py github-clean\src\platform\python\tests\examples\test_spinda_workbench.py
<repo-root>\.venv-mgba\bin\python.exe -m pytest -q src\platform\python\tests\examples\test_spinda_workbench.py
<repo-root>\.venv-mgba\bin\python.exe -m pytest -q github-clean\src\platform\python\tests\examples\test_spinda_workbench.py
<repo-root>\tools\spinda\spinda_workbench_native\build_spinda_workbench_native.bat
<repo-root>\tools\spinda\spinda_workbench_native\spinda_workbench_native.exe --self-test
<repo-root>\.venv-mgba\bin\python.exe -m pytest -q src\platform\python\tests\examples\test_spinda_workbench_native.py
<repo-root>\.venv-mgba\bin\python.exe tools\check_markdown_mirrors.py
```

Coverage points:

- Phase 3 good, bad, tiny, temp, duplicate, out-of-scope, and mixed-case
  ZIP/tmp lane scans.
- TSV valid, invalid, mismatched, duplicate, and ledger scans.
- PID locator, file-only ZIP existence, painter spot grid, SVG report payload,
  mixed-case `.pk3` input, score-path parity, unrounded suggestion heap
  ordering, pattern suggestions, Flask API payloads, empty optional query
  defaults, PowerShell-safe current-interpreter command previews, single-quote
  escaping, IPv6 URL formatting, bool-safe legacy ledger counts, CLI range
  validation, static HTML index serving, and zero-sample behavior.

