# mGBA Spinda Project — Clean Source Package

This repository is a cleaned source release of the private mGBA fork and companion tooling used for large-scale Pokémon FireRed/LeafGreen Spinda egg-generation research.

It contains source code, build files, operator tools, validators, and public-safe documentation. It intentionally excludes ROMs, saves, savestates, generated Spinda corpora, private worker data, local build products, virtual environments, and personal machine paths.

## Project Credit

Collector Togami is the originator, coordinator, and driving force behind the Spinda/SPC3 project. Shawrkie contributed to the SPC3 compressor/decompressor work and provided compute for corpus processing and verification. Additional upstream and third-party attribution is listed in [`CREDITS.md`](CREDITS.md).

## What Is Included

- A custom mGBA fork with Qt/native changes used for deterministic automation.
- Phase 1, Phase 2, and Phase 3 Spinda workflow scripts, with private inputs omitted.
- A standalone native Phase 3 generator path.
- Command-center and read-only monitoring/validation tools.
- FR/LG TSV save-bank tooling for the optional mass-hatching proof stage.
- SPC3 compression, verification, and analysis tooling.
- Build, replication, dependency, and operating documentation.

## What Is Not Included

You must provide your own legally obtained inputs where required:

- Game Boy Advance ROMs.
- Save files and savestates.
- Precomputed schedules such as `secondhalf.csv`.
- Phase 2 pickup states.
- Generated Phase 3 output ZIPs or SPC3 corpora.

Production/runtime directories such as `Phase3SpindaBlocks`, `Phase2PickupStates`, `1sthalves`, `Artifacts`, `userdata`, and `live-lanes` are intentionally absent.

## Start Here

- [`RUN_GUIDE.md`](RUN_GUIDE.md) — build requirements, Python/.NET dependencies, and operating commands.
- [`how-to-replicate.md`](how-to-replicate.md) — source-to-corpus replication and verification flow.
- [`docs/MGBA_CUSTOM_CHANGES_AND_FEATURES.md`](docs/MGBA_CUSTOM_CHANGES_AND_FEATURES.md) — inventory of custom mGBA changes.
- [`docs/SPC3_TWO_STAGE_RUNTIME_FORMAT.md`](docs/SPC3_TWO_STAGE_RUNTIME_FORMAT.md) — current experimental SPC3 v7 format.
- [`docs/FRLG_TSV_SAVE_BANK_PLAN.md`](docs/FRLG_TSV_SAVE_BANK_PLAN.md) — optional FR/LG TSV save-bank proof stage.
- [`LICENSES.md`](LICENSES.md) and [`CREDITS.md`](CREDITS.md) — licensing and attribution.

## Dependencies

The core mGBA build uses CMake and the platform dependencies described in `RUN_GUIDE.md`. Optional Python tooling dependencies are declared in [`requirements-python-tools.txt`](requirements-python-tools.txt); test tooling adds [`requirements-dev.txt`](requirements-dev.txt). The `mgba` Python package used by emulator scripts is the binding built from this source tree, not a PyPI package.

PKHeX.Core is **not vendored**. The optional PKHeX-backed validator and hatch splitter require a separately obtained compatible `PKHeX.Core.dll`; pass its path through the `PKHEX_CORE_DLL` MSBuild property as documented in `RUN_GUIDE.md`. This keeps the clean source tree free of hidden local checkout dependencies and keeps the licensing boundary explicit.

## Path and Data Policy

Repository tools use repository-relative defaults where practical and expose CLI/configuration overrides for external data. No public command should depend on a particular drive letter, Windows username, or private checkout path.

Before publishing or redistributing a rebuilt archive, verify that it contains no ROMs, saves, savestates, `.pk3` files, generated lane ZIPs/SPC3 corpora, personal home-directory paths, local build directories, worker userdata, caches, or virtual environments.

## SPC3 Result

The current experimental SPC3 v7 package is documented in `docs/SPC3_TWO_STAGE_RUNTIME_FORMAT.md`. The documented full 65,536-lane corpus result is `103,403,124` bytes with zero verification mismatches. The generated `.spc3` corpus itself is intentionally excluded from this source package.

## License

This is an mGBA-derived project under MPL-2.0, with bundled and external third-party components under their respective licenses. See [`LICENSES.md`](LICENSES.md) and [`CREDITS.md`](CREDITS.md).
