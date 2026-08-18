# Spinda Hatch ZIP Splitter

Standalone PKHeX.Core tool for the final mass-hatching proof stage. It reads
Phase 3 Spinda egg ZIPs and writes two output ZIPs:

- `spinda-hatched-shiny.zip`: each egg hatched with the TSV save whose TSV
  matches that egg's PSV
- `spinda-hatched-not-shiny.zip`: the same egg hatched with a non-matching TSV
  save as a control

The production default is Trainer ID `0` and the TSV save-bank naming contract:

```text
.\TSVs\TSV-xxxx-sid-xxxxx.sav
```

Both `xxxx` and `xxxxx` are decimal. The tool verifies
`TSV == (TID ^ SID) >> 3` before using a save. By default it also parses every
save with PKHeX.Core and copies the trainer name, gender, language, game
version, TID, and SID into each hatched PK3.

## Current Implementation Notes

- The hot TSV lookup uses a fixed `8192`-slot array instead of a dictionary.
- PID filename validation uses a small parser instead of Regex on each PK3
  entry.
- Report-only strings and conversion samples are built only when an issue or
  bounded sample is actually recorded.
- The report keeps full hard/soft issue counters even though only the first
  `512` issue samples are stored.
- In production mode, the first hard input issue stops the scan and deletes
  temporary outputs. Use `--skip-bad-records` only for forensic partial runs.

## Licensing

This splitter is a standalone tool, not part of the emulator runtime, Phase 3
generator, native Workbench, or SPC3 compression prototype. Its local project
source follows the repository default MPL-2.0 license unless a file says
otherwise.

The project references a user-supplied PKHeX.Core DLL through the `PKHEX_CORE_DLL` MSBuild property; no PKHeX checkout or binary is vendored. Upstream
PKHeX is GPL-3.0, so any distributed hatch-splitter binary or package that
includes or links PKHeX.Core must be handled as a separate GPL-3.0-compliant
tool package. Do not bundle that output into an mGBA release unless the package
also carries the required GPL text, source offer/source access, and notices for
that standalone tool.

The no-compression ZIP writer is local source. The optional `--compress` mode
uses .NET `System.IO.Compression.ZipArchive`; if a package bundles the .NET
runtime, include the applicable Microsoft/.NET runtime notices. Input/output
ZIPs and hatched PK3 records are generated data, not source-license grants.

## Build

```powershell
$pkhex = "C:\path\to\PKHeX.Core.dll"
dotnet build .\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -p:PKHEX_CORE_DLL="$pkhex"
```

## Planned Production Command

Do not run this until both the Phase 3 egg ZIP corpus and all `8192` TSV saves
are complete.

```powershell
dotnet run --project .\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -p:PKHEX_CORE_DLL="$pkhex" -- `
  --input-dir .\Phase3SpindaBlocks `
  --save-dir .\TSVs `
  --shiny-output .\HatchedSpindaZips\spinda-hatched-shiny.zip `
  --not-shiny-output .\HatchedSpindaZips\spinda-hatched-not-shiny.zip `
  --report .\HatchedSpindaZips\_spinda_hatch_zip_splitter_report.json `
  --overwrite
```

Default output ZIPs are stored with no compression for speed. The default
writer also streams local records immediately and spools central-directory
metadata to a temporary side file so corpus-scale runs do not keep one ZIP
entry object per Pokemon in RAM. Pass `--compress` only for small proof runs
where disk space is more important than CPU time.

## Safety

- The tool streams records directly from input ZIPs; it does not extract loose
  PK3 files.
- Output ZIPs are written to temporary files first and moved into place only
  after the run finishes.
- The default no-compression writer is the production path. `--compress` uses
  .NET `ZipArchive` and is meant for small checks because compressed ZIP
  writers retain more entry metadata.
- The issue sample list is bounded, but `hard_issue_count`,
  `soft_issue_count`, `total_issue_count`, and `issue_counts` are full-run
  counters.
- Missing TSV saves, bad PK3 checksums, wrong species, non-egg input records,
  or shiny-state mismatches are hard failures by default.
- `--skip-bad-records` exists for forensic partial runs, not production.
- `--trust-save-filenames` exists for synthetic unit tests and emergency
  planning only; production should parse real `.sav` files.

## Tests

The companion test project creates tiny synthetic PK3 egg ZIPs and filename-only
TSV contexts in a temporary directory. It does not touch production saves or
Phase 3 ZIPs. Coverage includes shiny/not-shiny split behavior, missing TSV
failure cleanup, and hard failures that occur after the bounded issue sample
list is already full.

```powershell
dotnet run --project .\tools\spinda\hatch_zip_splitter_tests\SpindaHatchZipSplitter.Tests.csproj -c Release -p:PKHEX_CORE_DLL="$pkhex"
```
