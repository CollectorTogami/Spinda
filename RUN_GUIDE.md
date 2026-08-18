# Run Guide

Use this guide to build and run the clean source package on another machine.
It assumes no private artifacts are present.

Documentation convention: `<repo-root>` means the root directory of this cloned repository. If you are already in that directory, Windows examples can generally use `.\` and POSIX examples can use `./` instead.

## Requirements

### Core mGBA / native tools

Windows/MSYS2:

- Windows 10/11.
- MSYS2 MinGW64 toolchain.
- CMake.
- Ninja.
- Qt 6 development packages when building `mgba-qt`.
- Python 3.12 when using the Python bindings or Python operator tools.

Example MSYS2 packages:

```powershell
pacman -S --needed mingw-w64-x86_64-toolchain mingw-w64-x86_64-cmake mingw-w64-x86_64-ninja mingw-w64-x86_64-qt6-base
```

Linux Phase 3 helper node:

- CMake.
- Ninja or Make.
- GCC/G++.
- zlib development headers.
- Python 3 when running Python-side helper tooling.

### Python tools

Create a virtual environment from the repository root, then install the optional tool dependencies:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-python-tools.txt
```

Linux/macOS equivalent:

```bash
python3 -m venv .venv
./.venv/bin/python -m pip install -r requirements-python-tools.txt
```

`requirements-python-tools.txt` declares Flask, NumPy, Pillow, psutil, matplotlib, and zstandard. `requirements-dev.txt` additionally installs pytest for the Python test suite.

The `mgba` Python module imported by emulator automation scripts is **not** installed from PyPI. Build the Python bindings from this repository (`-DBUILD_PYTHON=ON`) and run those scripts with the interpreter/environment that can import the resulting in-tree mGBA bindings.

### Optional PKHeX-backed .NET tools

PKHeX.Core is deliberately not vendored. Obtain/build a compatible `PKHeX.Core.dll` separately and pass it to MSBuild as `PKHEX_CORE_DLL`.

- `tools/spinda/phase3_pkhex_validator` targets .NET 8.
- `tools/spinda/hatch_zip_splitter` and its tests target .NET 10.

Example PowerShell:

```powershell
$pkhex = "C:\path\to\PKHeX.Core.dll"
dotnet build .\tools\spinda\phase3_pkhex_validator\Phase3PkhexValidator.csproj -c Release -p:PKHEX_CORE_DLL="$pkhex"
dotnet build .\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -p:PKHEX_CORE_DLL="$pkhex"
```

The project files fail with a clear error when `PKHEX_CORE_DLL` is missing or points to a nonexistent file. The referenced DLL is copied beside the built tool; redistribution remains subject to PKHeX's GPL-3.0 terms and the notices described in `LICENSES.md`/`CREDITS.md`.

## Build mGBA Qt

From this clean folder:

```powershell
cmake -S . -B build-mingw64-python-qt -G Ninja -DCMAKE_BUILD_TYPE=Release -DBUILD_QT=ON -DBUILD_PYTHON=ON
cmake --build build-mingw64-python-qt --target mgba-qt
```

Run:

```powershell
.\build-mingw64-python-qt\mGBA.exe
```

## Build Phase 3 CLI

The Phase 3 production path uses the standalone CLI target:

```powershell
.\tools\spinda\build_phase3_cli_lto.bat
```

Expected output:

```text
build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe
```

Linux helper-node CLI build:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

Expected output:

```text
build-linux-spinda-cli/mgba-spinda-phase3
```

## Required User Inputs For Spinda Runs

The clean repo does not include these. Create them yourself:

```text
ROM path
first-half source saves
first-half input/route artifacts
secondhalf.csv
pickup tape JSON
Phase2PickupStates\0x####.ss0
Phase3SpindaBlocks\
```

Input tapes are route snippets, not movie files. They store only per-frame GBA
button masks and must be paired with the correct ROM/save/savestate/setup
outside the tape. See `docs\INPUT_TAPES_VS_MOVIES.md`.

Example local layout:

```text
clean-root\
  inputs\
    lg.gba
    secondhalf.csv
  Phase2PickupStates\
    0x0001.ss0
  Phase3SpindaBlocks\
```

Use only legally obtained ROMs and saves.

## Run One Phase 3 Lane

## Phase 1: First-Half Setup

Goal: create first-half lane saves for lower PID halves. The clean package
contains the scripts, but not the ROM, saves, route tapes, or produced lane
artifacts.

Main scripts:

```text
doc\python-examples\frlg-spinda\Egg-First-Half-Hitter.py
doc\python-examples\frlg-spinda\frlg_spinda_first_half_batch.py
doc\python-examples\frlg-seed-bruteforce\Seed-Bruteforcer.py
doc\python-examples\frlg-seed-bruteforce\Seed-Replicator.py
```

Typical operator inputs:

```text
ROM
starting save or savestate
input tape / route metadata
target initial seed metadata
output folder for 0x####.sav first-half lane saves
```

Example folder shape:

```text
work\
  inputs\
    lg.gba
    starting-save.sav
    route-tape.json
    replay-metadata.json
  1sthalves\
    saves\
```

Example command shape:

```powershell
python .\doc\python-examples\frlg-spinda\Egg-First-Half-Hitter.py `
  --rom .\inputs\lg.gba `
  --output-root .\1sthalves
```

Phase 1 output expected by later phases:

```text
1sthalves\saves\0x0001.sav
1sthalves\saves\0x0002.sav
...
```

Exact options depend on the route/tape and seed-replication artifacts you
create. Do not commit those artifacts to the clean repository.

## Phase 2: Pickup-State Builder

Goal: load each Phase 1 `0x####.sav`, replicate the second-half initial seed,
run the daycare pickup bridge tape to the final pre-pickup dialog point, pad to
the baseline frame, then write one Phase 2 pickup savestate per lane.

Main script:

```text
doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py
```

Typical inputs:

```text
ROM
1sthalves\saves\0x####.sav
secondhalf.csv
pickup bridge tape JSON
replay metadata JSON
```

Example folder shape:

```text
work\
  inputs\
    lg.gba
    secondhalf.csv
    tape-seed-to-step-2.json
    replay-metadata.json
  1sthalves\
    saves\
      0x0001.sav
  Phase2PickupStates\
```

Example command shape:

```powershell
python .\doc\python-examples\frlg-spinda\Build-Phase2-Pickup-States.py `
  --rom .\inputs\lg.gba `
  --save-dir .\1sthalves\saves `
  --output-dir .\Phase2PickupStates `
  --second-half-csv .\inputs\secondhalf.csv `
  --pickup-tape ".\inputs\tape-seed-to-step-2.json" `
  --metadata .\inputs\replay-metadata.json `
  --baseline-frame 700
```

Phase 2 output expected by Phase 3:

```text
Phase2PickupStates\0x0001.ss0
Phase2PickupStates\0x0002.ss0
...
```

Endpoint note:

- `0x0000` and `0xFFFF` need valid Phase 2 pickup states too.
- Phase 3 treats them like normal lanes after the `.ss0` exists.
- If your Phase 1/2 endpoint route uses special ACE or custom tapes, keep
  those private artifacts outside the clean repo.

Phase 2 progress dashboard:

```powershell
python .\tools\spinda\phase2_pickup_progress_web.py --folder .\Phase2PickupStates --port 234
```

Open:

```text
http://127.0.0.1:234
```

## Linux Phase 3 Helper Node

Linux helpers are for Phase 3 CLI production only. They do not need Qt.

Prepare:

```text
inputs/lg.gba
inputs/secondhalf.csv
Phase2PickupStates/0x####.ss0
Phase3SpindaBlocks/
```

Build:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

Source/package readiness check from the machine preparing the helper:

```powershell
.\.venv\Scripts\python.exe .\tools\spinda\check_linux_helper_port.py --root . --mode source --bash C:\msys64\usr\bin\bash.exe
```

Run one proof claim from the coordinator:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-proof-1 \
WORKERS=1 \
BATCH_SIZE=1 \
BUNDLE_SIZE=1 \
LANES=0x0001-0x0001 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

Run broader helper work after the proof ZIP validates:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-helper-1 \
WORKERS=6 \
BATCH_SIZE=24 \
LANES=0x0000-0xFFFF \
bash tools/spinda/run_phase3_ledger_helper.sh
```

Details: `PHASE3_LINUX_HELPER_NODE.md` in the main documentation folder, or
`docs/PHASE3_LINUX_HELPER_NODE.md` in the clean package.

Phase 2 validator:

```powershell
python .\tools\spinda\phase2_pickup_state_validator.py --folder .\Phase2PickupStates
```

## Phase 3: Spinda PK3 ZIP Generation

Example:

```powershell
.\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe `
  --rom .\inputs\lg.gba `
  --secondhalf-csv .\inputs\secondhalf.csv `
  --lane 0x0001 `
  --phase2-state .\Phase2PickupStates\0x0001.ss0 `
  --output-dir .\Phase3SpindaBlocks `
  --cache-dir .\Phase3SpindaBlocks\_cache `
  --overwrite
```

Expected output:

```text
Phase3SpindaBlocks\0x0001.spinda80.zip
```

The ZIP should contain only PID-named 80-byte `.pk3` entries.

## FR/LG TSV Save Bank

This post-corpus support stage creates one FR/LG save for each Trainer Shiny
Value. The clean package includes the script and docs, but not the ROM, route
tape, or any produced saves. The later mass-hatching stage uses these saves to
produce separate shiny and non-shiny ZIP subsets.

Main script:

```text
doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py
```

Dry-plan command shape:

```powershell
python .\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --dry-plan `
  --tid 0x1234 `
  --start-rng 0x89ABCDEF `
  --output-dir .\TSVs
```

Live command shape:

```powershell
.\build-mingw64-python-qt\mGBA.exe --script `
  .\doc\python-examples\frlg-tsv-save-bank\Build-FRLG-TSV-Save-Bank.py `
  --post-sid-tape .\inputs\frlg-post-sid-to-save-point.json `
  --output-dir .\TSVs `
  --resume
```

The live script expects the emulator to already be paused at the final input
before SID generation with the desired TID already hit. It branches from that
point in the Qt scratch state, verifies TID/SID/TSV after SID commit, replays a
post-SID input tape, and exports decimal `TSV-xxxx-sid-xxxxx.sav` files.

Detailed docs:

- main workspace: `markdown-files\FRLG_TSV_SAVE_BANK_PLAN.md`
- clean package: `docs\FRLG_TSV_SAVE_BANK_PLAN.md`
- script folder: `doc\python-examples\frlg-tsv-save-bank\README.md`

## Mass Hatch ZIP Splitter

Planned after both inputs are complete:

- Phase 3 egg ZIPs in `Phase3SpindaBlocks`
- all `8192` TID0/TSV saves in `TSVs`

Standalone PKHeX.Core tool:

```text
tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj
```

Production command shape:

```powershell
dotnet run --project .\tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- `
  --input-dir .\Phase3SpindaBlocks `
  --save-dir .\TSVs `
  --shiny-output .\HatchedSpindaZips\spinda-hatched-shiny.zip `
  --not-shiny-output .\HatchedSpindaZips\spinda-hatched-not-shiny.zip `
  --report .\HatchedSpindaZips\_spinda_hatch_zip_splitter_report.json `
  --overwrite
```

The tool streams input `.pk3` entries, parses TSV saves through PKHeX.Core,
hatches each Spinda once with `TSV == PSV` and once with a non-matching TSV,
and writes only the two derived ZIPs plus a JSON report. Default ZIP output is
stored without compression for speed and uses a custom streaming writer that
spools ZIP central-directory metadata to a temporary side file instead of RAM.
Use `--compress` only for small proof runs where disk space is the bottleneck.
The hot path uses fixed-array TSV lookup, a non-Regex PID filename parser, and
lazy report/sample string creation. Reports keep full hard/soft issue counters
while storing only bounded issue samples. In production mode, any hard issue
stops the scan and removes temp outputs before final ZIPs are moved into place.

Synthetic unit tests:

```powershell
dotnet run --project .\tools\spinda\hatch_zip_splitter_tests\SpindaHatchZipSplitter.Tests.csproj -c Release
```

## Manual ZIP to 7z Compaction

After final outputs are complete and backed up, use the manual GUI to convert
large `.zip` result folders into `.7z` archives with LZMA-family compression:

```powershell
python .\tools\spinda\zip_to_7z_gui\zip_to_7z_gui.py
```

Requirements:

- Python with Tkinter.
- A user-installed 7-Zip CLI: `7z.exe`, `7za.exe`, or `7zz`.

The GUI lets you pick input and output folders, choose recursive scanning,
choose overwrite behavior, and choose `lzma2` or strict `lzma`. It preserves
relative folder layout:

```text
Input\lane\block.zip -> Output\lane\block.7z
```

The tool performs real conversion by extracting each ZIP to a temporary folder,
then writing a new `.7z` archive. It does not wrap the ZIP file inside a `.7z`,
and it never deletes input ZIPs. Before extraction it rejects unsafe ZIP member
paths such as parent-directory traversal, absolute paths, Windows drive paths,
NUL bytes, and newlines. Failed or cancelled jobs remove their temporary output
archive and leave existing final `.7z` files untouched.

Source/license audit:

- `tools\spinda\zip_to_7z_gui\zip_to_7z_gui.py` uses only Python standard
  library modules.
- 7-Zip is an external runtime tool, not vendored source. See
  `tools\spinda\zip_to_7z_gui\README.md` and `LICENSES.md` before bundling any
  7-Zip binary.

## Run Worker Pool

Example two workers over a small range:

```powershell
python .\tools\spinda\native_phase3_worker_pool.py `
  --lanes 0x0001-0x0004 `
  --workers 2 `
  --runner cli `
  --phase3-cli-exe .\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe `
  --rom .\inputs\lg.gba `
  --secondhalf-csv .\inputs\secondhalf.csv `
  --phase2-dir .\Phase2PickupStates `
  --output-dir .\Phase3SpindaBlocks `
  --cache-dir .\Phase3SpindaBlocks\_cache `
  --bundle-size 2 `
  --skip-existing-by-name `
  --overwrite
```

## Run Command Center

Install Flask if needed:

```powershell
python -m pip install Flask
```

Start:

Preferred Windows wrapper:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

The wrapper starts both the Flask command center and the independent watcher.
It does not start, stop, or resize workers unless you use the browser controls.

Direct Python launch:

```powershell
python .\tools\spinda\phase3_command_center_web.py `
  --folder .\Phase3SpindaBlocks `
  --pool-status .\Phase3SpindaBlocks\_native_phase3_worker_pool_status.json `
  --pool-control .\Phase3SpindaBlocks\_native_phase3_worker_pool_control.json `
  --host 0.0.0.0 `
  --port 235 `
  --sample-interval 5 `
  --zip-scan-interval 60 `
  --host-resource-interval 15
```

Open:

```text
http://127.0.0.1:235
```

The command center shows completed lanes, worker status, projected finish,
artifact health, disk/RAM/CPU, watcher status, and validation policy. Hot
status avoids ZIP entry reads; the folder scan, host resource sample, and
watcher process/API checks are throttled so worker CPU gets priority.

Detailed Phase 3 docs:

- main workspace: `markdown-files\PHASE3_RUNBOOK.md`
- clean package: `docs\PHASE3_RUNBOOK.md`
- command center guide: `PHASE3_COMMAND_CENTER_GUIDE.md`
- watcher guide: `PHASE3_WATCHER_GUIDE.md`
- recovery guide: `PHASE3_RECOVERY_GUIDE.md`
- final validation plan: `PHASE3_FINAL_VALIDATION_PLAN.md`

Manual watcher launch if not using the wrapper:

```powershell
python .\tools\spinda\phase3_independent_watcher.py `
  --folder .\Phase3SpindaBlocks `
  --pool-status .\Phase3SpindaBlocks\_native_phase3_worker_pool_status.json `
  --interval-seconds 300
```

## Validate Output Without Extracting PK3 Files To Disk

Manifest-only check during active production:

```powershell
python .\tools\spinda\phase3_zip_validator.py `
  --root .\Phase3SpindaBlocks `
  --manifest-only `
  --allow-incomplete
```

Deep ZIP check:

```powershell
python .\tools\spinda\phase3_zip_validator.py `
  --root .\Phase3SpindaBlocks `
  --allow-incomplete
```

This reads ZIP entries in RAM and writes only a JSON report.

## Optional Final PKHeX Validation

Run only after all lanes are complete:

```powershell
dotnet run --project .\tools\spinda\phase3_pkhex_validator\Phase3PkhexValidator.csproj -c Release -- .\Phase3SpindaBlocks
```

This uses `PKHeX.Core` from NuGet in the clean package.

## License Check Before Publishing

Keep these files in any source release:

```text
LICENSE
LICENSES.md
CREDITS.md
res/licenses/
```

Do not ship ROMs, saves, savestates, generated PK3 files, generated lane ZIPs,
private CSV schedules, or private input tapes. For binary releases, add notices
for exact Qt/Python/NuGet packages included with that binary.

The Virtual Pad feature credits BizHawk/EmuHawk because the virtual game pad
idea and reference code are based on theirs. Keep `res\licenses\bizhawk.txt` with source
or binary releases that include the feature.

For source feature inventory, keep
`docs/MGBA_CUSTOM_CHANGES_AND_FEATURES.md` with this clean package.

## Clean-Tree Audit Before Publishing

Run from the clean folder:

```powershell
rg -i "C:\\Users\\$env:USERNAME|Users/$env:USERNAME|AppData\\Local\\Temp" .
rg --files . | rg -i "(\.gba$|\.gb$|\.gbc$|\.sav$|\.ss[0-9]$|\.state$|\.pk3$|\.spinda80\.zip$|\.zip$|\.7z$|\.rar$|\.csv$|\.log$|Phase3SpindaBlocks|Phase2PickupStates|1sthalves|Artifacts|userdata|live-lanes|_cache|build-mingw64)"
```

Both commands should return no project-private artifacts. Also search your own
local username if you generated files outside this clean package.
