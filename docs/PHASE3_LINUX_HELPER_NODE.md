# Phase 3 Linux Helper Node

## Status Bucket

- Current status: Source-side Linux helper-node path is implemented for Phase 3
  CLI workers. It does not port Qt.
- Last verified date: 2026-05-01.
- Proven artifacts:
  - `tools/spinda/build_phase3_cli_linux.sh`
  - `tools/spinda/run_phase3_ledger_helper.sh`
  - `tools/spinda/check_linux_helper_port.py`
  - `tools/spinda/native_phase3_worker_pool.py`
  - `tools/spinda/phase3_ledger_worker_client.py`
  - `src/platform/python/tests/examples/test_phase3_linux_helper_port.py`
  - targeted source tests for platform-aware defaults, helper command shape,
    headless-only build flags, LF shell script shape, documentation
    registration, package layout, and validator behavior
- Known gaps: No live Linux workstation lane has been run yet in this workspace.
  First helper must prove one lane before large assignments.
- Next action: Build the Linux CLI on the helper, run one claimed lane, return
  the ZIP, and compare PK3-level output before widening lane range.

## Goal

Use a Linux machine as a Phase 3 worker only. The Windows machine can remain the
coordinator and browser command center.

Linux helper does not need:

- Qt
- dark-mode UI changes
- scripting window
- Windows command-center wrapper
- portable Windows Python

Linux helper does need:

- source tree
- Linux CMake/compiler toolchain
- Python 3
- ROM supplied by operator
- `secondhalf.csv`
- `Phase2PickupStates`
- coordinator URL

## Folder Shape

Recommended helper folder:

```text
spinda-helper/
  inputs/
    lg.gba
    secondhalf.csv
  Phase2PickupStates/
    0x0000.ss0
    ...
    0xFFFF.ss0
  Phase3SpindaBlocks/
  tools/
  src/
  include/
  CMakeLists.txt
```

Generated output stays local until returned:

```text
Phase3SpindaBlocks/0x####.spinda80.zip
```

## Build CLI

Install normal Linux build basics. Package names vary by distro, but typical
requirements are:

```text
cmake
ninja-build
gcc
g++
zlib development package
python3
```

Build:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

Expected binary:

```text
build-linux-spinda-cli/mgba-spinda-phase3
```

Optional native CPU build for a dedicated helper:

```bash
PHASE3_CLI_NATIVE_CPU=1 bash tools/spinda/build_phase3_cli_linux.sh
```

Optional no-LTO fallback if the distro toolchain rejects LTO:

```bash
PHASE3_CLI_LTO=0 bash tools/spinda/build_phase3_cli_linux.sh
```

## Run Helper

Set the coordinator URL to the Windows command center:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-helper-1 \
WORKERS=6 \
BATCH_SIZE=24 \
LANES=0x0000-0xFFFF \
bash tools/spinda/run_phase3_ledger_helper.sh
```

The helper script:

- validates local inputs exist
- claims lanes from coordinator ledger
- launches `native_phase3_worker_pool.py`
- passes Linux CLI path explicitly
- heartbeats while workers run
- reports finish/fail by final ZIP filename
- keeps claiming more batches unless stopped

Extra worker-pool arguments can go after the script:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
bash tools/spinda/run_phase3_ledger_helper.sh --zip-method deflate
```

## First Proof Run

Do not start with thousands of lanes.

Recommended first proof:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-proof-1 \
WORKERS=1 \
BATCH_SIZE=1 \
BUNDLE_SIZE=1 \
LANES=0x0001-0x0001 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

Proof checks:

- coordinator marks lane claimed
- helper creates `Phase3SpindaBlocks/0x0001.spinda80.zip`
- coordinator marks lane done
- ZIP has `65536` PID-named `.pk3` entries
- every `.pk3` is 80 bytes
- all PIDs end in lane `0x0001`

If byte-for-byte ZIP hash differs from Windows but PK3 entries match, prefer the
PK3-level proof. Compression library differences can change ZIP container bytes.

## Source-Side Verification

Before sending a Linux helper package, run these from the main project root:

```powershell
<repo-root>\.venv-mgba\bin\python.exe -m pytest `
  <repo-root>\src\platform\python\tests\examples\test_phase3_linux_helper_port.py `
  <repo-root>\src\platform\python\tests\examples\test_native_phase3_worker_pool.py `
  <repo-root>\src\platform\python\tests\examples\test_phase3_ledger_worker_client.py -q
```

Run the package/source validator:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\check_linux_helper_port.py --root <repo-root> --mode source --bash C:\msys64\usr\bin\bash.exe
```

Script syntax check from Windows/MSYS:

```powershell
C:\msys64\usr\bin\bash.exe -n /c/mgba-py/tools/spinda/build_phase3_cli_linux.sh
C:\msys64\usr\bin\bash.exe -n /c/mgba-py/tools/spinda/run_phase3_ledger_helper.sh
```

Dry-run the worker-pool command shape without launching mGBA:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\native_phase3_worker_pool.py `
  --lanes 0x0100 `
  --allow-missing-inputs `
  --dry-run `
  --runner cli `
  --phase3-cli-exe C:\tmp\build-linux-spinda-cli\mgba-spinda-phase3 `
  --rom C:\tmp\inputs\lg.gba `
  --phase2-dir C:\tmp\Phase2PickupStates `
  --secondhalf-csv C:\tmp\inputs\secondhalf.csv `
  --output-dir C:\tmp\Phase3SpindaBlocks `
  --cache-dir C:\tmp\Phase3SpindaBlocks\_cache
```

Expected result:

- command uses `mgba-spinda-phase3`, not Qt
- command includes `--runner cli`
- command includes ROM, CSV, Phase 2 state, output, and cache paths
- no worker process is launched during dry-run

Current source tests also check:

- Linux defaults do not append `.exe`
- Linux runtime path handling does not inject MSYS/DevkitPro paths
- Linux build script keeps Qt, SDL, Python embedding, scripting, and GB core
  disabled
- Linux launcher passes `--output-dir` once through the ledger client
- Linux shell scripts keep `#!/usr/bin/env bash` shebangs and LF line endings
- Linux validator rejects private artifacts in clean-package mode
- Linux validator skips the Windows WSL shim when Bash exists but no WSL distro
  is installed
- clean repo keeps Linux helper scripts but no private artifacts
- Assisted-baking keeps Linux helper scripts plus personal inputs

## Assisted-Baking Package

If using the private assisted package, these Linux helper files must be present
there too:

```text
Assisted-baking/tools/spinda/build_phase3_cli_linux.sh
Assisted-baking/tools/spinda/run_phase3_ledger_helper.sh
Assisted-baking/tools/spinda/check_linux_helper_port.py
Assisted-baking/docs/PHASE3_LINUX_HELPER_NODE.md
```

That package already includes personal inputs by owner request. A Linux helper
can build its own native binary from the bundled source tree and use the same
`inputs/`, `Phase2PickupStates/`, `Phase3SpindaBlocks/`, and shared cache
folders.

## Ledger Safety

Coordinator ledger prevents intentional duplicate work:

```text
Phase3SpindaBlocks/_phase3_lane_ledger.json
```

Helper workers should use the ledger client, not a manually fixed lane range,
unless doing a controlled offline batch.

If helper crashes:

- coordinator lease expires
- lane becomes claimable again
- stale helper temp ZIPs can be deleted locally
- finished `0x####.spinda80.zip` files should be returned or reconciled before
  rerunning those lanes

## Evidence Split

### Proven

- Linux helper scripts are present.
- Worker pool has platform-aware CLI executable defaults.
- Worker pool passes all Phase 3 CLI options through normal `--runner cli`.
- Ledger client forwards Linux-specific CLI/ROM/CSV/Phase2 paths through to the
  worker pool.
- Source tests verify Linux helper scripts stay CLI-only and registered in the
  mirrored documentation set.
- Dedicated Linux helper test file verifies clean-package and Assisted-baking
  layouts when run from those trees.
- Shell syntax checks pass under MSYS Bash on the Windows host.

### Observed Once

- None yet for a live Linux lane.

### Inferred

- mGBA core and Phase 3 CLI should build on Linux because upstream mGBA supports
  Linux and the Phase 3 CLI already has POSIX file/process branches.

### Planned

- First helper node should run one proof lane before production scaling.

### Obsolete

- Qt porting is not required for helper nodes.
