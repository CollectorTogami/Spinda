# How to Replicate the Spinda/SPC3 Corpus

This guide describes the clean-source replication path for the Spinda/SPC3 project. The project as a whole is by Collector Togami. Shawrkie helped with the SPC3 compressor and decompressor/unpack work and contributed compute for corpus processing and verification.

The clean tree is intentionally source-first. It includes scripts, source code, docs, and public-safe input tapes. It does not include ROMs, saves, savestates, private worker folders, generated PK3 files, generated lane ZIPs, SPC3 corpora, DLLs, or EXEs.

## Starting Contract

The assumed starting point is not a fresh save. Start at Phase 1 from the known Four Island daycare setup:

- Game: Pokemon FireRed/LeafGreen-compatible FR/LG runtime.
- Location: Four Island daycare route setup used by the project input tapes.
- State: one player step before the daycare egg-generation check.
- Daycare counter: the second daycare mon step low byte is `0xFE`, meaning step `254`; the next valid step advances to `0xFF` and runs the egg-generation check.
- Parents: two Spinda are deposited in daycare and are from different trainers.
- Egg state: no pending daycare egg before the final triggering step.
- Private anchor: the project used private `1 from egg.sav` and `1 from egg.ss0` anchor files. They are not included because save data and savestates are not clean-source artifacts.

The exact tile coordinate is part of the private anchor state. If you recreate the state, verify it by checking the daycare fields and by replaying the included tapes without route drift. Do not treat frame count alone as proof; Four Island NPC activity can add PRNG noise.

## Included Input Tapes

The input tapes are under `inputs/tapes/`. They contain button masks only. They do not contain ROM data, save data, or savestate data.

- `tape seed to step 1.json`
- `tape seed to step 2.json`
- `hit 1st half walk to daycare man.json`
- `0x0000 special tape.json`
- `fbc7-lane16-reset-to-title-baseline.inputtape.json`
- `fbc7-lane16-title-baseline-to-checkpoint.inputtape.json`
- `cd39-lane21-reset-to-title-baseline.inputtape.json`
- `cd39-lane21-title-baseline-to-checkpoint.inputtape.json`

See `docs/INPUT_TAPES_VS_MOVIES.md` for the tape format and why these are not emulator movies.

## External Dependencies

Install or provide these yourself:

- A legally obtained FR/LG ROM. The project does not include ROMs.
- A recreated compatible save and savestate at the Phase 1 starting contract.
- MSYS2 MinGW64, CMake, Ninja, and a C/C++ compiler for Windows builds.
- Qt development packages if building the visible mGBA Qt runner.
- Python 3.12 for the Python automation scripts.
- Flask if using the command-center dashboards.
- zlib development headers for the native Phase 3 CLI and ZIP tooling.
- .NET 8 SDK only for optional PKHeX-based validators and hatch splitter tools.
- PKHeX.Core only for optional standalone semantic validation; it is not vendored here.
- 7-Zip or zstd only for optional external compression comparisons.
- CUDA route-search tooling or equivalent precomputed route schedules for regenerating large `firsthalf*.csv` and `secondhalf.csv` files.

Large generated CSV schedules are not included because they are generated artifacts and some exceed normal GitHub file-size limits.

## Build mGBA Qt

From the clean repository root:

```powershell
cmake -S . -B build-mingw64-python-qt -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_QT=ON -DBUILD_PYTHON=ON
cmake --build build-mingw64-python-qt --target mgba-qt
```

Run the visible emulator:

```powershell
.\build-mingw64-python-qt\mGBA.exe
```

The Python scripts can be run either from host Python with the mGBA Python module available, or from the Qt scripting window.

## Build the Phase 3 CLI

```powershell
.\tools\spinda\build_phase3_cli_lto.bat
```

Expected output:

```text
build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe
```

Linux helper builds use:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

## Phase 1: Generate First-Half Saves

Goal: generate one first-half save per live daycare lower PID half.

Main scripts:

- `doc/python-examples/frlg-seed-bruteforce/Seed-Bruteforcer.py`
- `doc/python-examples/frlg-seed-bruteforce/Seed-Replicator.py`
- `doc/python-examples/frlg-spinda/Egg-First-Half-Hitter.py`
- `doc/python-examples/frlg-spinda/frlg_spinda_first_half_batch.py`
- `doc/python-examples/frlg-spinda/spinda_frlg_common.py`

Typical first-half command shape:

```powershell
$env:PATH = "$PWD\build-mingw64-python-qt;$env:PATH"
$env:PYTHONPATH = "$PWD\build-mingw64-python-qt;$PWD"
python doc\python-examples\frlg-spinda\Egg-First-Half-Hitter.py `
  --rom C:\path\to\your\lg.gba `
  --base-save C:\work\anchor\1-from-egg.sav `
  --first-half-state C:\work\anchor\1-from-egg.ss0 `
  --csv C:\work\routes\firsthalf-prng-FB91.csv `
  --setup-tape .\inputs\tapes\tape seed to step 1.json `
  --hit-tape '.\inputs\tapes\hit 1st half walk to daycare man.json' `
  --output-dir C:\work\1sthalves `
  --live-output
```

For a shakedown target, add one lower half:

```powershell
python doc\python-examples\frlg-spinda\Egg-First-Half-Hitter.py --target-half 0x1234 --limit 1 --dry-run
```

Verification for Phase 1:

```powershell
python tools\spinda\first_half_raw_csv_audit.py C:\work\1sthalves --json
```

A valid Phase 1 result has live-half keyed `.sav` and pre-daycare-man `.ss0` outputs, matching manifests, and RAM-verified daycare lower halves. The walk to the daycare man must be checked by PRNG checkpoints, not by frame count alone.

## Phase 2: Generate Pickup Baseline States

Goal: convert first-half saves into pre-pickup states keyed by lower PID half.

Main scripts:

- `doc/python-examples/frlg-spinda/Build-Phase2-Pickup-States.py`
- `doc/python-examples/frlg-spinda/Prime-Second-Half-States.py`
- `tools/spinda/phase2_pickup_state_validator.py`
- `tools/spinda/phase2_pickup_progress_web.py`

Command shape:

```powershell
python doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py `
  --save-dir C:\work\1sthalves\saves `
  --output-dir C:\work\Phase2PickupStates `
  --secondhalf-csv C:\work\routes\secondhalf.csv `
  --pickup-tape '.\inputs\tapes\hit 1st half walk to daycare man.json' `
  --zero-pickup-tape '.\inputs\tapes\0x0000 special tape.json' `
  --reset-tape '.\inputs\tapes\cd39-lane21-reset-to-title-baseline.inputtape.json' `
  --pre-input-tape '.\inputs\tapes\cd39-lane21-title-baseline-to-checkpoint.inputtape.json'
```

Verify Phase 2 without writing to the running emulator:

```powershell
python tools\spinda\phase2_pickup_state_validator.py C:\work\Phase2PickupStates `
  --target-states 65536 `
  --expected-state-size 397312 `
  --json `
  --strict-health `
  --require-complete
```

Optional runtime sampling opens savestates read-only through a separate host-side mGBA core:

```powershell
python tools\spinda\phase2_pickup_state_validator.py C:\work\Phase2PickupStates `
  --rom C:\path\to\your\lg.gba `
  --verify-samples 64 `
  --json `
  --strict-health
```

## Phase 3: Generate Egg Lane ZIPs

Goal: for each lower PID half, generate all upper PID half eggs and write one `0xLLLL.spinda80.zip` lane.

One lane:

```powershell
.\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe `
  --rom C:\path\to\your\lg.gba `
  --lane 0x0001 `
  --phase2-state C:\work\Phase2PickupStates\0x0001.ss0 `
  --secondhalf-csv C:\work\routes\secondhalf.csv `
  --output-dir C:\work\Phase3SpindaBlocks `
  --expected-initial-seed 0xCD39 `
  --expected-rng 0x2B0C94C1
```

Lane range:

```powershell
.\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe `
  --rom C:\path\to\your\lg.gba `
  --lanes 0x0001-0x00FF `
  --phase2-dir C:\work\Phase2PickupStates `
  --secondhalf-csv C:\work\routes\secondhalf.csv `
  --output-dir C:\work\Phase3SpindaBlocks
```

Parallel local workers:

```powershell
python tools\spinda\native_phase3_worker_pool.py `
  --rom C:\path\to\your\lg.gba `
  --phase2-dir C:\work\Phase2PickupStates `
  --secondhalf-csv C:\work\routes\secondhalf.csv `
  --output-dir C:\work\Phase3SpindaBlocks
```

The command-center and helper-node scripts are under `tools/spinda/`:

- `phase3_command_center.ps1`
- `phase3_command_center_web.py`
- `phase3_ledger_worker_client.py`
- `run_phase3_ledger_helper.sh`
- `run_phase3_remaining_workers.bat`

## Verify Phase 3 ZIP Output

Manifest and content validation:

```powershell
python tools\spinda\phase3_zip_validator.py `
  --root C:\work\Phase3SpindaBlocks `
  --target-lanes 65536 `
  --report C:\work\reports\phase3_zip_validator.json
```

This opens every lane ZIP, forces ZIP CRC/decompression validation, checks lane coverage, and checks that each PK3 entry's first four bytes match the PID encoded by its filename.

## Build and Verify SPC3

Build the SPC3 prototype from source:

```powershell
.\tools\spinda\spc3_prototype\build_spc3_prototype.bat
```

Pack a corpus:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe `
  --mode pack `
  --root C:\work\Phase3SpindaBlocks `
  --output C:\work\Artifacts\spinda-eggs.spc3 `
  --level 3 `
  --typed-level3 `
  --all-zips `
  --threads 0 `
  --report C:\work\reports\spc3_pack_report.json
```

Audit against the source ZIP corpus and predictor JSON if you generated one:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe `
  --mode audit `
  --root C:\work\Phase3SpindaBlocks `
  --input C:\work\Artifacts\spinda-eggs.spc3 `
  --predictor C:\work\Artifacts\_phase3_pid_second_half_iv_reference.json `
  --report C:\work\reports\spc3_full_lane_audit.json `
  --threads 0 `
  --all-zips
```

Unpack and revalidate:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe `
  --mode unpack `
  --input C:\work\Artifacts\spinda-eggs.spc3 `
  --unpack-dir C:\work\spc3_unpacked `
  --unpack-format zip `
  --pk3-state egg

python tools\spinda\phase3_zip_validator.py `
  --root C:\work\spc3_unpacked\egg `
  --target-lanes 65536 `
  --report C:\work\reports\spc3_unpacked_zip_validator.json
```

A full verification run should cover:

- 65,536 lane ZIPs present.
- Every lane has 65,536 PK3 records.
- ZIP central-directory shape, CRC, and decompression are valid.
- PK3 PID bytes match filenames.
- SPC3 internal hashes rebuild correctly.
- SPC3 unpacked ZIP payloads match the source lane payloads.
- Predictor roundtrip has zero mismatches if predictor audit is enabled.
- Any transient I/O failure lane is rerun from Phase 3 and byte-compared against the decompressed SPC3 lane and original ZIP lane.

## Clean Publication Audit

Before publishing this tree, run this from the clean repository root:

```powershell
$badExt = '.gba','.gb','.gbc','.sav','.ss0','.ss1','.state','.dll','.exe','.spc3','.pk3','.pk3raw','.zip','.7z'
Get-ChildItem -Recurse -File | Where-Object {
  $badExt -contains $_.Extension.ToLowerInvariant() -or
  $_.Name.EndsWith('.spinda80.zip') -or
  $_.Length -gt 95000000
} | Select-Object FullName,Length
```

Expected result: no rows.

Also check for private absolute paths before publishing:

```powershell
rg -n "<your-private-user-path>|merged\\esp32s3|_vendor\\mgba|lg\.gba|\.sav|\.ss0|\.dll|\.exe" .
```

Some documentation may mention example Windows paths. Do not publish any real ROM, save, savestate, DLL, EXE, generated ZIP, PK3, or SPC3 artifact.

## Final Project Verification Status

The completed private corpus was verified by composite evidence: full lane generation, ZIP validation, SPC3 rebuild checks, predictor-roundtrip checks, decompressed-SPC3 payload checks, and targeted Phase 3 reruns for transient I/O lanes. The clean repo gives the source path to reproduce that result, but it intentionally excludes the private corpus and private game-data inputs.

