# Phase 3 Final Validation Plan

## Status Bucket

- Current status: Current final validation plan for completed Phase 3 lane ZIPs.
- Last verified date: 2026-04-30.
- Proven artifacts:
  - `tools/spinda/phase3_zip_validator.py`
  - `tools/spinda/phase3_pkhex_validator/`
  - `tools/spinda/phase3_command_center_web.py`
- Known gaps: Final validation cannot fully run until production has all
  expected lane ZIPs.
- Next action: Run staged validation after production completes or on selected
  completed batches.

## Evidence Split

### Proven

- Manifest-only validation can run without opening ZIP entries.
- Deep ZIP validation reads ZIP entries in RAM and does not extract PK3 files
  to disk.
- PKHeX.Core semantic validation is deferred until after production.
- Final expected lane count is `65,536`.
- Final expected Spinda count is `4,294,967,296`.

### Observed Once

- Early completed lanes passed prior spot validation.

### Inferred

- Deep ZIP validation plus PKHeX.Core validation gives stronger proof than hot
  command-center counters.

### Planned

- Run full manifest validation.
- Run full deep ZIP validation.
- Run PKHeX.Core semantic validation.
- Save final reports and hashes.

### Obsolete

- Do not validate by extracting every PK3 to disk.
- Do not use only filename counts as final proof.

## Validation Order

Use this order:

1. Manifest-only scan.
2. Deep ZIP structure scan.
3. Optional hash manifest.
4. PKHeX.Core semantic validation.
5. Final report.
6. Backup or publish artifact set according to project policy.

## Stage 1: Manifest-Only Scan

Run:

```powershell
python .\tools\spinda\phase3_zip_validator.py --root .\Phase3SpindaBlocks --manifest-only
```

Expected:

- `65,536` final lane ZIP names
- no missing lanes
- no zero-size ZIPs
- no bad names
- no stale temp ZIPs
- no duplicate weird files

If production is still active, add:

```powershell
--allow-incomplete
```

Do not use `--allow-incomplete` for final proof.

## Stage 2: Deep ZIP Structure Scan

Run:

```powershell
python .\tools\spinda\phase3_zip_validator.py --root .\Phase3SpindaBlocks
```

Expected per lane:

- exactly `65,536` entries
- entries are `.pk3`
- entry names are PID-based
- no extra files inside ZIP
- each PK3 entry is `80` bytes after in-RAM read
- ZIP CRC/decompression succeeds

This may take time. It should read entries in RAM and should not write PK3
files to disk.

## Stage 3: Hash Manifest

Planned output:

```text
Phase3SpindaBlocks\_phase3_final_hash_manifest.json
```

Record:

- lane ZIP filename
- size
- SHA-256
- validation timestamp
- validator version or source hash if available

This helps detect later disk or copy damage.

## Stage 4: PKHeX.Core Semantic Validation

Run only after raw ZIP checks pass:

```powershell
dotnet run --project .\tools\spinda\phase3_pkhex_validator\Phase3PkhexValidator.csproj -c Release -- .\Phase3SpindaBlocks
```

Purpose:

- parse generated PK3 records through PKHeX.Core
- catch semantic Pokemon-data issues that raw ZIP checks cannot see
- give final content-level confidence

This is deferred because it is expensive and not needed in the hot production
loop.

## Stage 5: Final Report

Final report should include:

- date/time
- source build identity
- Phase 3 CLI path and build type
- `secondhalf.csv` identity or hash
- Phase 2 state source identity
- final lane count
- final Spinda count
- manifest validation result
- deep ZIP validation result
- PKHeX.Core validation result if run
- skipped or regenerated lanes, if any
- known gaps

## Failure Policy

If one lane fails:

1. Preserve failing ZIP for evidence.
2. Record size, hash, validator error, and lane ID.
3. Regenerate only that lane from its Phase 2 pickup state.
4. Re-run deep validation on that lane.
5. Re-run final manifest validation.

If many lanes fail:

1. Stop.
2. Check whether validator logic is wrong.
3. Check disk/storage health.
4. Check whether output was produced by mixed old/new formats.
5. Do not mass-delete final ZIPs until cause is known.

## Pass Definition

Final pass requires:

- all `65,536` lane ZIPs present
- all ZIPs structurally valid
- all ZIPs contain only expected PK3 entries
- total PK3 count equals `4,294,967,296`
- no bad filenames or temp files
- no zero-size/tiny final ZIPs
- PKHeX.Core validation either passes or is explicitly documented as deferred

## Related Docs

- [PHASE3_RUNBOOK.md](PHASE3_RUNBOOK.md)
- [PHASE3_COMMAND_CENTER_GUIDE.md](PHASE3_COMMAND_CENTER_GUIDE.md)
- [PHASE3_WATCHER_GUIDE.md](PHASE3_WATCHER_GUIDE.md)
- [PHASE3_RECOVERY_GUIDE.md](PHASE3_RECOVERY_GUIDE.md)
