# Dependencies, Modifications, and Source Map

This is the public-release source/dependency map for the Spinda mGBA fork. Private workstation paths, cross-repository development notes, generated artifact locations, and stale historical links have been removed from this version.

`<repo-root>` means the root of the cloned repository. No source file listed here requires a specific drive letter, username, or checkout directory.

## Build and Runtime Dependencies

### Core mGBA / Phase 3 native path

The repository uses the upstream-style CMake build plus the custom `BUILD_SPINDA_PHASE3_CLI` target. Platform packages and example commands are documented in [`../RUN_GUIDE.md`](../RUN_GUIDE.md).

Important build entry points:

- [`../CMakeLists.txt`](../CMakeLists.txt)
- [`../tools/spinda/build_phase3_cli_linux.sh`](../tools/spinda/build_phase3_cli_linux.sh)
- `tools/spinda/build_phase3_cli_lto.bat` (Windows helper; path shown as plain text because GitHub does not need a special link for operation)

### Python

Optional repository Python-tool dependencies are declared in:

- [`../requirements-python-tools.txt`](../requirements-python-tools.txt)
- [`../requirements-dev.txt`](../requirements-dev.txt)

The `mgba` Python module imported by emulator scripts is generated from this source tree when the Python bindings are built; it is not an external PyPI dependency.

### PKHeX.Core

PKHeX.Core is an optional external GPL-3.0 dependency used by the PKHeX-backed Phase 3 validator and hatch splitter. It is not vendored and is supplied through the `PKHEX_CORE_DLL` MSBuild property. The relevant projects are:

- [`../tools/spinda/phase3_pkhex_validator/Phase3PkhexValidator.csproj`](../tools/spinda/phase3_pkhex_validator/Phase3PkhexValidator.csproj)
- [`../tools/spinda/hatch_zip_splitter/SpindaHatchZipSplitter.csproj`](../tools/spinda/hatch_zip_splitter/SpindaHatchZipSplitter.csproj)
- [`../tools/spinda/hatch_zip_splitter_tests/SpindaHatchZipSplitter.Tests.csproj`](../tools/spinda/hatch_zip_splitter_tests/SpindaHatchZipSplitter.Tests.csproj)

Each project fails explicitly when `PKHEX_CORE_DLL` is missing or points to a nonexistent file. See [`../RUN_GUIDE.md`](../RUN_GUIDE.md) and [`../LICENSES.md`](../LICENSES.md) for setup/licensing boundaries.

## High-Value Custom Source Areas

### Emulator / Qt automation

- [`../src/platform/qt/CoreController.cpp`](../src/platform/qt/CoreController.cpp) — custom frame stepping, input handling, and runtime automation hooks.
- [`../src/platform/qt/SpindaProjectView.cpp`](../src/platform/qt/SpindaProjectView.cpp) — native Spinda Project UI/Phase 3 integration.
- [`../src/platform/qt/scripting/ScriptingController.cpp`](../src/platform/qt/scripting/ScriptingController.cpp) — scripting-side batch stepping, scratch-state, and session control.
- [`../src/core/scripting.c`](../src/core/scripting.c) — core scripting additions used by the automation path.

### Python binding / examples

- [`../src/platform/python/`](../src/platform/python/) — custom Python binding/bootstrap changes.
- [`../doc/python-examples/`](../doc/python-examples/) — public-safe automation examples and FR/LG project scripts.

### Spinda operator / validation tools

- [`../tools/spinda/`](../tools/spinda/) — Phase 1/2/3 monitors, validators, command center, Workbench, hatch tooling, SPC3 tooling, and helper scripts.
- [`../tools/emulation_accuracy/`](../tools/emulation_accuracy/) — source/trace comparison harnesses used for emulator-accuracy checks.

## Current Public Documentation

- [`MGBA_CUSTOM_CHANGES_AND_FEATURES.md`](MGBA_CUSTOM_CHANGES_AND_FEATURES.md) — detailed custom-feature inventory.
- [`python_lua_scrips.md`](python_lua_scrips.md) — script/tool inventory.
- [`SPINDA_PROJECT_DOC_INDEX.md`](SPINDA_PROJECT_DOC_INDEX.md) — public documentation index.

## Publication Boundary

The clean repository intentionally excludes ROMs, saves, savestates, generated lane ZIPs, PK3/SPC3 corpora, worker userdata, local build directories, private cross-repository checkouts, and publication-paper directories. Runtime/user data paths should be passed through command-line options or use repository-relative defaults described in the run guide.
