# FR/LG TSV Save Bank Plan

## Status Bucket

- Current status: Phase 4 TID0/TSV save-bank production is complete and verified for all `8192` TSV save files.
- Last verified date: 2026-05-06.
- Proven artifacts: `doc/python-examples/frlg-tsv-save-bank/frlg_tsv_common.py`, `Build-FRLG-TSV-Save-Bank.py`, `test_frlg_tsv_save_bank.py`, `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`, and `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`.
- Known gaps: Mass hatching has not been run on production Phase 3 inputs; the splitter still has synthetic unit-test proof only until it is run against the verified save bank and the Phase 3 egg ZIP corpus.
- Next action: Keep the verified save bank read-only, keep the backup ZIP, and run the PKHeX.Core hatch ZIP splitter only when the Phase 3 egg ZIP corpus is ready for the derived shiny/non-shiny proof pass.

## Goal

Create and preserve `8192` FR/LG save files, one for each Trainer Shiny Value:

```text
TSV = (TID ^ SID) >> 3
```

The purpose is to check and hatch the Spinda egg corpus after Phase 3. For a
Spinda PID:

```text
PSV = (PID_low ^ PID_high) >> 3
```

The save whose `TSV == PSV` hatches that egg shiny. Other TSV saves hatch the
same egg non-shiny. The hatching stage packages these as two separate ZIP
files: one shiny-hatched subset and one not-shiny-hatched subset.

Planned mass-hatching tool:

```text
tools/spinda/hatch_zip_splitter/SpindaHatchZipSplitter.csproj
```

That tool uses PKHeX.Core, streams Phase 3 egg ZIP entries, parses the TSV save
bank once, writes output ZIPs through temporary files first, and refuses
production by default unless all `8192` TSV saves are present. Its default
no-compression ZIP writer spools central-directory metadata to disk so the
derived proof ZIPs can be produced without retaining one entry object per
Pokemon in RAM.

## Exact Behavior Wanted

Start from a paused FR/LG emulator state where:

- the desired TID already exists
- SID has not yet been generated
- the game is waiting for the final input that commits SID
- no button is held

From that one point:

1. Read TID and `gRngValue`.
2. Precalculate wait frames for all `8192` TSV values.
3. Save the branch point in the Qt in-memory scratch state.
4. For each TSV, reload scratch, wait neutral frames, press the final input, and let SID generate.
5. Verify the final TID/SID creates the desired TSV.
6. Replay a post-SID input tape to the stable save/export point.
7. Export `TSV-xxxx-sid-xxxxx.sav`.
8. Mark that TSV true in `_frlg_tsv_save_bank_status.json`.

## Why FR/LG

This side project stays on FR/LG because the main Spinda work is already based
on FR/LG route control, input tapes, save export behavior, and legality checks.
Keeping the save bank in the same game family avoids introducing another route
system for the hatching proof.

## Script Location

```text
doc/python-examples/frlg-tsv-save-bank/
```

Files:

- `frlg_tsv_common.py`
- `Build-FRLG-TSV-Save-Bank.py`
- `README.md`
- `SCRIPT_DOCUMENTATION.md`

## Output

Current output folder:

```text
<repo-root>\TSVs\
```

Expected files:

```text
TSV-0000-sid-00000.sav
TSV-0001-sid-00008.sav
...
TSV-8191-sid-65528.sav
_frlg_tsv_wait_plan.json
_frlg_tsv_save_bank_status.json
_sid_shiny_value_ledger_tid_0x0000.json
```

The filename TSV and SID fields are decimal, not hex.

## Verified Completion

Proven as of 2026-05-06:

- complete saves:
  - `8192 / 8192`
- standalone verifier:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- physical save files:
  - `8192` files matching `TSV-*.sav`
- source save bytes:
  - `1,073,741,824`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`
- backup ZIP entries:
  - `8192` `.sav` entries
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`

The standalone verifier read the physical saves and confirmed filename shape,
filename SID-to-TSV mapping, save TID/SID, and party-slot-1 hatched Bulbasaur
ownership/checksum for every stable save. Treat this as Phase 4 completion
evidence for the save-bank stage, not as proof that the later mass-hatching
stage has run.

## Calibration Values

The script exposes two calibration flags:

```text
--sid-commit-offset
--rng-advances-per-neutral-frame
```

Defaults are planned values, not live proof:

```text
--sid-commit-offset 1
--rng-advances-per-neutral-frame 1
```

These values are historical bring-up controls for regenerating or repairing the
bank. They are no longer blockers for Phase 4 completion because the finished
bank has been verified from exported save data.

## Status And Resume

The status JSON lists all TSVs with true/false completion. A branch is complete
only when:

- the final TID/SID was verified or intentionally trusted during bring-up
- the TSV matches the target row
- the save was exported
- the save SHA-1 was recorded

`--resume` skips only rows marked done whose save file still exists.

## Boundaries

- This is FR/LG-only.
- This does not inject Pokemon.
- This save-bank script does not hatch eggs; hatching is handled by
  `tools/spinda/hatch_zip_splitter` after the save bank is complete.
- This does not store ROM, save, or savestate anchors inside input tapes.
- This uses input tapes, not movie files.

## Mass-Hatching Handoff

When the Phase 3 egg ZIP corpus is ready, run the hatch splitter against the
verified TSV save bank:

```powershell
dotnet run --project <repo-root>\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- `
  --input-dir <repo-root>\Phase3SpindaBlocks `
  --save-dir <repo-root>\TSVs `
  --shiny-output <repo-root>\HatchedSpindaZips\spinda-hatched-shiny.zip `
  --not-shiny-output <repo-root>\HatchedSpindaZips\spinda-hatched-not-shiny.zip `
  --report <repo-root>\HatchedSpindaZips\_spinda_hatch_zip_splitter_report.json `
  --overwrite
```

The splitter keeps the canonical Phase 3 egg ZIPs read-only. Its two output
ZIPs are derived proof products, not replacements for the raw corpus.
The production path uses the splitter's stored ZIP writer, fixed-array TSV
lookup, non-Regex PID filename parsing, lazy report/sample string creation,
full hard/soft issue counters, and fail-fast cleanup for hard input problems
before final output ZIPs are moved into place.
