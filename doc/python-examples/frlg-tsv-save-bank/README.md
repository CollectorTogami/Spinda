# FR/LG TSV Save Bank

## Status Bucket

- Current status: Phase 4 save-bank stage is complete and verified for all `8192` TID0/TSV saves.
- Last verified date: 2026-05-06.
- Proven artifacts: `frlg_tsv_common.py`, `Build-FRLG-TSV-Save-Bank.py`, unit/source tests in `test_frlg_tsv_save_bank.py`, `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`, and `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`.
- Known gaps: Mass hatching and shiny/non-shiny ZIP subsets are separate planned work after the Phase 3 egg ZIP corpus is ready.
- Next action: Keep the verified bank and backup ZIP, then use the bank for mass hatching when Phase 3 inputs are ready.

## Purpose

This folder contains the FR/LG-only plan for creating one save per Trainer
Shiny Value, `0..8191`. These saves are meant for checking and hatching the
Spinda egg corpus after Phase 3 finishes.

Each save keeps a fixed TID and a different SID. The TSV formula is:

```text
TSV = (TID ^ SID) >> 3
```

A Spinda egg has a matching PSV:

```text
PSV = (PID_low ^ PID_high) >> 3
```

The save whose `TSV == PSV` should hatch that egg shiny. Saves with other TSVs
should hatch it non-shiny. The later mass-hatching stage will package these as
separate shiny and non-shiny ZIP subsets.

## Expected Emulator State

The live script expects mGBA to already be paused at the final input point
before SID generation. At that point:

- desired TID is already hit
- SID has not yet been committed
- no route input is being held
- `gRngValue` is stable enough for calibrated wait planning

The script captures this point once in the Qt in-memory scratch state. It then
restores that scratch state for every TSV branch.

## Output

Current output:

```text
<repo-root>\TSVs\
```

Files:

```text
TSV-0000-sid-00000.sav
TSV-0001-sid-00008.sav
...
TSV-8191-sid-65528.sav
_frlg_tsv_wait_plan.json
_frlg_tsv_save_bank_status.json
_sid_shiny_value_ledger_tid_0x0000.json
```

Save exports use a `.tmp` file followed by atomic replace.
The TSV and SID fields in filenames are decimal, not hex.

Verified as of 2026-05-06:

- `8192 / 8192` saves exist in `<repo-root>\TSVs`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`

## Dry Plan

Dry-plan mode writes the wait plan and status JSON without touching mGBA:

```powershell
python <repo-root>\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --dry-plan `
  --tid 0x1234 `
  --start-rng 0x89ABCDEF
```

## Live Shape

Live mode requires a post-SID input tape:

```powershell
<repo-root>\build-mingw64-python-qt\mGBA.exe --script `
  <repo-root>\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --post-sid-tape <repo-root>\routes\frlg-post-sid-to-save-point.json `
  --resume
```

The route tape begins after the final input has committed SID and continues to
the stable point where exporting the save is valid.

## Calibration

Two values must be proven by a small live run:

- `--sid-commit-offset`: LCRNG calls from final input acceptance to SID value.
- `--rng-advances-per-neutral-frame`: LCRNG calls burned by one neutral wait frame.

Defaults are `1` and `1` because that matches the current planned model. If the
first live proof disagrees, change the flags instead of changing saved outputs
by hand.

## Notes

- This is FR/LG-only.
- This script does not inject Pokemon.
- This script does not hatch eggs; hatching is the next roadmap stage.
- This script does not store ROM/save/savestate anchors in the route tape.
