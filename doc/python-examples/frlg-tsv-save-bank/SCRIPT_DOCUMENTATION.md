# FR/LG TSV Save Bank Script Documentation

## Status Bucket

- Current status: Phase 4 save-bank implementation has completed all `8192` TID0/TSV save files and passed standalone verification.
- Last verified date: 2026-05-06.
- Proven artifacts: Source files and unit tests for planner, status, memory reads, and atomic export helpers, plus `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json` and `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`.
- Known gaps: Mass hatching and separate shiny/non-shiny ZIP subsets are not implemented here and still need a production run after the Phase 3 egg ZIP corpus is ready.
- Next action: Preserve the verified save bank, use the backup ZIP as rollback, and hand the bank to the mass-hatching stage when Phase 3 inputs are ready.

## Script

`Build-FRLG-TSV-Save-Bank.py` builds a bank of FR/LG saves with one save per
Trainer Shiny Value.

The script has two layers:

- `frlg_tsv_common.py`: pure Python math, status JSON, and memory-read helpers.
- `Build-FRLG-TSV-Save-Bank.py`: visible-Qt mGBA runner.

## Workflow

1. Operator pauses mGBA at the final input before SID generation.
2. Script reads TID and `gRngValue`, unless both were supplied by CLI.
3. Script calculates one neutral wait for every TSV.
4. Script captures the branch point in the Qt scratch state.
5. For each TSV, script restores scratch, waits neutral frames, presses final input, verifies TID/SID/TSV, replays the post-SID route tape, and exports a save.
6. Status JSON records true/false completion for every TSV.

Current save naming contract:

- folder:
  - `<repo-root>\TSVs`
- save files:
  - `TSV-xxxx-sid-xxxxx.sav`
- filename values:
  - decimal TSV and decimal SID, not hex
- TID:
  - fixed at `0`

Verified as of 2026-05-06:

- complete saves:
  - `8192 / 8192`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`

## Status JSON

`_frlg_tsv_save_bank_status.json` stores:

- fixed TID
- starting `gRngValue`
- SID commit offset
- neutral-frame RNG advance rate
- one row per TSV
- final TID/SID
- save path
- save SHA-1
- error text if a branch fails

## Safety Rules

- Source branch state lives in RAM through Qt scratch state.
- Save export writes `*.sav.tmp` then renames to final `*.sav`.
- `--resume` skips only TSV rows already marked done with an existing save.
- The post-SID route is an input tape, not a movie file.
- The tape is anchor-free and does not store private ROM/save paths.

## Mass Hatching Consumer

The save bank is consumed by the next roadmap stage:

1. calculate each egg's PSV:
   - `PSV = (PID_low ^ PID_high) >> 3`
2. hatch with the matching TSV save to prove shiny output
3. hatch with a non-matching TSV save to prove non-shiny output
4. package hatch outputs into separate shiny and non-shiny ZIP subsets

This script does not inject Pokemon or hatch eggs. It only creates the verified
save contexts used by that later hatching pass.

## Live Command Shape

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script `
  <repo-root>\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --post-sid-tape <repo-root>\routes\frlg-post-sid-to-save-point.json `
  --resume
```

## Dry Command Shape

```powershell
python <repo-root>\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --dry-plan `
  --tid 0x1234 `
  --start-rng 0x89ABCDEF
```

## Evidence Labels

- Proven: TSV math, acceptable SID set, wait-plan coverage, status mutation, memory read helpers under unit tests, and full-bank verification for all `8192` exported saves.
- Observed once: live route timing and individual emulator branch behavior from production remain historical operational evidence; the final bank itself is now verifier-backed.
- Planned: production mass hatching ZIP subsets.
