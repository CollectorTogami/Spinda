# SPC3 Native GUI Guide

## Scope

`spc3_verifier_gui.exe` is the native C++/Win32 operator GUI for SPC3. It
launches `spc3_prototype.exe`, shows the exact command it ran, streams command
output into the console pane, writes JSON reports, and shows built-in report
summary/compare output.

The GUI is dark mode by default. It does not use Python, Tkinter, Qt, or a web
view.

Wrap-up status: this GUI is the shippable operator surface for the current
narrow SPC3 scope. It intentionally exposes the core workflow and a simple
report summary/compare view instead of expert GPU or benchmark controls.

Caveman-full quick guide:
[SPC3_NATIVE_GUI_CAVEMAN_TLDR.md](SPC3_NATIVE_GUI_CAVEMAN_TLDR.md).

Each run is sent to a hidden persistent `spc3_prototype.exe --server` worker.
That keeps in-process CUDA/NVRTC context and module cache state alive across
multiple GUI runs. Canceling a run, closing the GUI, or changing the compressor
path stops the worker and drops that cache. Direct CLI runs still start, run,
report, and exit normally.

When the GUI is run from this repo layout, the worker current directory is the
workspace root. When the GUI is moved into a standalone operator folder, the
worker current directory is the GUI folder, so relative paths stay inside the
package.

## What It Is For

Use the GUI for routine SPC3 work:

- verify an existing `.spc3` without writing lane payload files
- request GPU rebuild/verify when available
- pack typed v0.2 level-3 SPC3 files
- consolidate already-compressed `.spc3` shards
- inspect container metadata
- explicitly unpack selected lanes to `0xLLLL.spinda80.zip` files when needed

Keep detailed benchmarking, fuzzing, and release gates in the CLI. The GUI is a
thin operator surface over the same executable.

## Files

Main files:

| File | Purpose |
| --- | --- |
| `tools\spinda\spc3_gui_native\spc3_verifier_gui.exe` | Native dark-mode GUI. |
| `tools\spinda\spc3_gui_native\spc3_prototype.exe` | Local compressor copy used by the GUI after the GUI build script runs. |
| `tools\spinda\spc3_gui_native\spc3_verifier_gui_baseline.exe` | Portable x86-64 baseline GUI build for shared packages. |
| `tools\spinda\spc3_gui_native\spc3_prototype_baseline.exe` | Portable x86-64 baseline compressor copy used by the baseline GUI. |
| `tools\spinda\spc3_gui_native\gen3_moves.csv` | Gen 3 move names and base PP used by the unpack move dropdowns. |
| `tools\spinda\spc3_gui_native\gen3_held_items.csv` | Gen 3 held item names used by the unpack held-item dropdown. |
| `tools\spinda\spc3_gui_native\gen3_locations.csv` | Gen 3 met location names and origin-game filters used by the unpack met-location dropdown. |
| `tools\spinda\spc3_gui_native\spc3_verifier_gui.cpp` | GUI source. |
| `tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat` | GUI build script. |
| `tools\spinda\spc3_prototype\spc3_prototype.exe` | SPC3 compressor/verifier CLI that the GUI launches. |
| `tools\spinda\spc3_prototype\build_spc3_prototype.bat` | CLI build script. It statically links codec/runtime libraries. |

The GUI and compressor builds statically link the MinGW C++ runtime and codec
libraries. The GUI build script rebuilds the compressor and copies
`spc3_prototype.exe` into the GUI folder, so Explorer and GUI launches do not
depend on an MSYS2 shell `PATH` or local zlib/zstd/lzma DLLs.

## Build

From `<repo-root>`:

```powershell
cmd /c tools\spinda\spc3_prototype\build_spc3_prototype.bat
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
```

The first command builds the CLI and removes stale copied runtime DLLs from
older builds. The second command builds the dark-mode native GUI, then copies
the static compressor into `tools\spinda\spc3_gui_native`.

For a portable baseline build meant to move across mixed x86-64 machines:

```powershell
cmd /c tools\spinda\spc3_prototype\build_spc3_prototype.bat baseline
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

That writes `spc3_prototype_baseline.exe` and
`spc3_verifier_gui_baseline.exe`. The baseline GUI prefers the baseline
compressor beside it, while the normal GUI still prefers `spc3_prototype.exe`.

## CPU And GPU Support

The current release target is Windows x86-64, also called AMD64.

AMD CPUs are supported when they are x86-64 CPUs. That includes Ryzen,
Threadripper, and EPYC systems. The code does not require an Intel CPU. The
current build uses Windows x64 assembly hot loops and a MinGW64 C++ build.

The default build still uses `-march=native`. That is good for the machine
doing the build, but it can emit instructions that older AMD or Intel CPUs do
not support. For a package meant to move between machines, use the `baseline`
build variant instead of the native-tuned one.

GPU support is separate from CPU support:

- NVIDIA CUDA GPUs can be used for typed v0.2 level-3 verify/unpack rebuild.
- AMD GPUs are not a GPU-offload backend right now.
- Intel GPUs are not a GPU-offload backend right now.
- Systems with AMD CPUs and no NVIDIA CUDA GPU still run through the CPU path.
- If `Use GPU` is checked on a system without CUDA/NVRTC, the report should show
  a fallback reason and the run should continue on CPU.

## Launch

From PowerShell:

```powershell
.\tools\spinda\spc3_gui_native\spc3_verifier_gui.exe
```

You can also launch it from Explorer. The GUI looks for `spc3_prototype.exe`
beside itself first, so the normal Explorer path is the built
`tools\spinda\spc3_gui_native` folder. Default input, output, folder, predictor,
and report paths are absolute paths under the workspace root, so they still
resolve correctly when Explorer starts the GUI from its own folder.

If the GUI cannot find the compressor, set the `SPC3 exe` field to:

```text
<repo-root>\tools\spinda\spc3_prototype\spc3_prototype.exe
```

The `...` button next to `SPC3 exe` opens an executable picker.

## Default Screen

The GUI opens with the `Verify` radio button selected and disk-light defaults:

| Control | Default | Effect |
| --- | --- | --- |
| `Mode` | `Verify` | Validate an existing SPC3 file. |
| `Use GPU` | checked | Add `--gpu-rebuild`. The CLI falls back to CPU if CUDA is unavailable or unsupported. |
| `Internal only` | checked | Add `--no-source-compare`, so the run does not reload source ZIPs. |
| `Report JSON` | `_spc3_gui_verify_report.json` | Write a report file. |
| `Compare JSON` | empty | Optional second report for CPU/GPU or pack/verify/unpack comparison. |
| `Output ZIP dir` | hidden unless unpack mode is selected | No lane ZIP files are written in default verify mode. |

Default verify mode reads the input `.spc3`, rebuilds lane payloads in memory or
GPU memory, checks internal CRCs, and writes a JSON report.

## Path Pickers

Every path row has a `...` button.

| Row | Picker |
| --- | --- |
| `SPC3 exe` | Open-file picker for `*.exe`. |
| `Input .spc3` | Open-file picker for existing `*.spc3` files. |
| `Output .spc3` | Save-file picker for `*.spc3` output. |
| `Lane ZIP root` | Folder picker. |
| `SPC3 shard root` | Folder picker. |
| `Predictor JSON` | Open-file picker for `*.json`. |
| `Trainer index` | Open-file picker for the TSV trainer index `*.json`. |
| `Output ZIP dir` / `Unpack dir` | Folder picker for unpack output. |
| `Report JSON` | Save-file picker for `*.json` reports. |
| `Compare JSON` | Open-file picker for an existing `*.json` report to compare. |

The picker writes the selected path back into the field. You can still edit any
path by hand.

## Mode-Aware Layout

Mode is selected with radio buttons: `Verify`, `Pack`, `Consolidate`,
`Inspect`, and `Unpack`.

The GUI hides controls that do not apply to the selected mode. This keeps the
visible form close to the command that will run.

| Mode | Visible task controls |
| --- | --- |
| `Verify` | `Input .spc3`, `Predictor JSON`, `Report JSON`, `Compare JSON`, `Use GPU`, `Internal only`. |
| `Verify` with `Internal only` off | Adds `Lane ZIP root` for source comparison. |
| `Pack` | `Output .spc3`, `Lane ZIP root`, `Predictor JSON`, `Report JSON`, `Compare JSON`, lane count (`all` by default), `Level`, `Profile`, `Typed v0.2`, `External predictor`, `No entropy probe`. |
| `Consolidate` | `Output .spc3`, `SPC3 shard root`, `Report JSON`, `Compare JSON`. |
| `Inspect` | `Input .spc3`, `Report JSON`, `Compare JSON`. |
| `Unpack` | `Input .spc3`, `Predictor JSON`, `Output ZIP dir`, lane selector, PK3 state, `Trainer index` for hatched states, `Extra settings`, `Report JSON`, `Compare JSON`, `Use GPU`. |

`SPC3 exe`, `Summary`, `Run`, `Cancel`, and the console stay visible for every
mode.

## Mode Input/Output Summary

| Mode | Inputs | Outputs |
| --- | --- | --- |
| `Verify` | Existing `.spc3`; optional predictor; optional lane ZIP root when `Internal only` is off | JSON report only. No lane ZIP or raw payload files. |
| `Pack` | Lane ZIP root, lane count (`all` by default), level/profile, optional predictor | New `.spc3` and JSON report. Sparse corpuses are allowed. |
| `Consolidate` | Folder of existing `.spc3` shards | Combined `.spc3` and JSON report. |
| `Inspect` | Existing `.spc3` | Metadata JSON report only. |
| `Unpack` | Existing `.spc3`, optional predictor, lane selection, optional trainer index for hatched states | `0xLLLL.spinda80.zip` files in `Output ZIP dir` and JSON report. Each ZIP contains encrypted `0xUUUULLLL.pk3` records. |

## Controls

| Control | Used By | Meaning |
| --- | --- | --- |
| `Mode` | all modes | Radio buttons for `Verify`, `Pack`, `Consolidate`, `Inspect`, and `Unpack`. |
| `SPC3 exe` | all modes | Path to `spc3_prototype.exe`. |
| `Input .spc3` | verify, inspect, unpack | Existing SPC3 file to read. |
| `Output .spc3` | pack, consolidate | SPC3 file to create. |
| `Lane ZIP root` | pack, verify with source compare | Directory containing Phase 3 lane ZIPs. |
| `SPC3 shard root` | consolidate | Directory containing existing `.spc3` shard files. |
| `Predictor JSON` | pack, verify, unpack | Predictor path. Leave it set for current Phase 3 typed runs. Embedded predictors are used when available. |
| `Output ZIP dir` | unpack | Directory that receives `0xLLLL.spinda80.zip` files. Each ZIP contains encrypted `0xUUUULLLL.pk3` records. |
| `Lanes` | unpack | Select all lanes, one hex shared PID lower-half lane, or an inclusive hex lane range. |
| `PK3 state` | unpack | `Egg` preserves rebuilt PK3 bytes. `Hatched shiny` and `Hatched not shiny` rewrite each encrypted PK3 using TSV save data. |
| `Trainer index` | unpack hatched states | JSON index generated from the 8192 TSV save lanes. Hidden while `Egg` is selected. |
| `Extra settings` | unpack | Expands optional PK3 edit fields. `No change` entries are ignored. |
| `Report JSON` | all modes | JSON report path. Required. |
| `Compare JSON` | all modes | Optional existing report. When set, the GUI compares it against `Report JSON`. |
| `Limit` | pack | Maximum lane ZIPs to pack. |
| `Level` | pack | Dropdown for SPC3 level `0`, `1`, `2`, or `3`. Use `3` for the active typed path. |
| `Profile` | pack levels 1..3 | `fast`, `auto`, `compat`, or `small`. Hidden for level `0`, which is always raw. |
| `Typed v0.2` | pack level 3 | Adds `--typed-level3`. Keep enabled for the active main path. Hidden for levels `0..2`. |
| `Use GPU` | verify, unpack | Adds `--gpu-rebuild`. |
| `Internal only` | verify | Avoids source ZIP comparison and keeps disk writes light. |
| `External predictor` | pack level 3 | Stores the predictor outside the SPC3. Hidden for levels `0..2`. |
| `No entropy probe` | pack | Skips extra entropy probing. Default is on for faster operator runs. |
| `Summary` | all modes | Reads `Report JSON` and optional `Compare JSON` without running the compressor. |

## Codec Profiles

| Profile | CLI mapping | Use |
| --- | --- | --- |
| `fast` | `--codec-profile fast`, currently zstd-9 | Recommended active v0.2 typed level-3 path. |
| `auto` | omitted profile, compatibility zlib-9 | Maximum compatibility for now. |
| `compat` | zlib-9 | Explicit compatibility profile. |
| `small` | LZMA2-9 | Size-focused comparison path. Slower. |

Use `fast` for normal typed v0.2 level-3 work. Use `auto` or `compat` when you
need the current compatibility default. Use `small` only when size is more
important than runtime. Level `0` is raw and does not use a codec profile, so
the GUI hides the profile selector for that level.

## Mode: Verify

Verify checks an existing `.spc3`.

Disk-light verify:

1. Select `Verify`.
2. Set `Input .spc3`.
3. Leave `Internal only` checked.
4. Leave `Use GPU` checked if CUDA should be attempted.
5. Set `Report JSON`.
6. Click `Run`.

Equivalent CLI shape:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --input Phase3SpindaBlocks\input.spc3 --no-source-compare --gpu-rebuild --report Phase3SpindaBlocks\verify_report.json
```

Verify with source comparison:

1. Select `Verify`.
2. Set `Input .spc3`.
3. Uncheck `Internal only`.
4. Set `Lane ZIP root`.
5. Set `Report JSON`.
6. Click `Run`.

Source comparison reloads the lane ZIPs and compares rebuilt payloads against
source material. It is heavier on disk than the default path.

## Mode: Pack

Pack creates a new `.spc3` from Phase 3 lane ZIPs.

Recommended typed v0.2 level-3 pack:

1. Select `Pack`.
2. Set `Lane ZIP root` to the lane ZIP directory.
3. Set `Output .spc3`.
4. Set `Limit`.
5. Set `Level` to `3`.
6. Leave `Typed v0.2` checked.
7. Set `Profile` to `fast`.
8. Set `Report JSON`.
9. Click `Run`.

Equivalent CLI shape:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --root Phase3SpindaBlocks --all-zips --level 3 --typed-level3 --codec-profile fast --output Phase3SpindaBlocks\packed.spc3 --report Phase3SpindaBlocks\pack_report.json
```

Pack mode does not use the `Use GPU` checkbox. Current codec work is CPU/library
backed. GPU rebuild is for verify/unpack payload reconstruction.

## Mode: Consolidate

Consolidate merges existing `.spc3` shard files without unpacking or
recompressing their lane payloads.

Use this when Phase 3 is incomplete but some lane batches are already packed.

Steps:

1. Put compatible `.spc3` shards in one directory.
2. Select `Consolidate`.
3. Set `SPC3 shard root` to that directory.
4. Set `Output .spc3`.
5. Set `Report JSON`.
6. Click `Run`.

Equivalent CLI shape:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode consolidate --consolidate-root Phase3SpindaBlocks\_spc3_shards --output Phase3SpindaBlocks\combined.spc3 --report Phase3SpindaBlocks\combined_consolidate_report.json
```

The compressor rejects:

- duplicate lane IDs
- incompatible SPC3 versions, levels, or flags
- incompatible embedded predictors
- malformed shard files

The report uses `copy_mode=compressed_stream_copy_no_payload_decode` when it
successfully copies compressed streams.

## Mode: Inspect

Inspect reads metadata without decoding lane payloads.

Steps:

1. Select `Inspect`.
2. Set `Input .spc3`.
3. Set `Report JSON`.
4. Click `Run`.

Use inspect before handing an unknown `.spc3` to verify, unpack, or consolidate.

## Mode: Unpack

Unpack rebuilds selected lane payloads and writes `0xLLLL.spinda80.zip` files.
Each ZIP contains stored encrypted `0xUUUULLLL.pk3` entries, matching the Phase
3 lane ZIP shape. The GUI uses ZIP output intentionally; raw `.pk3raw` export
is CLI-only via `--unpack-format raw`.

Steps:

1. Select `Unpack`.
2. Set `Input .spc3`.
3. Set `Output ZIP dir`.
4. Choose `All lanes`, `One lane`, or `Range`.
5. For `One lane`, enter one hex lane such as `00A5`.
6. For `Range`, enter inclusive hex start and end lanes.
7. Choose `Egg`, `Hatched shiny`, or `Hatched not shiny`.
8. If a hatched state is selected, set `Trainer index` to `TSVs\_spinda_tsv_trainer_index_tid_0x0000.json` or another complete 8192-entry TSV index.
9. Leave `Use GPU` checked if CUDA should be attempted.
10. Set `Report JSON`.
11. Click `Run`.

Equivalent CLI shape:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\input.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_zips --unpack-format zip --lane-select all --gpu-rebuild --report Phase3SpindaBlocks\unpack_report.json
```

Use a fresh unpack directory when you need a clean payload set. The tool creates
missing parent directories, but it does not delete older unpacked files.

`Egg` is the byte-preserving corpus state. `Hatched shiny` picks the trainer
entry whose TSV matches each PK3 PID's PSV. `Hatched not shiny` picks the next
TSV, `(PSV + 1) & 8191`, so the result is deterministic and non-shiny. Both
hatched modes clear egg state, write trainer TID/SID/name/gender/version and
language, refresh the Gen 3 checksum, then store the re-encrypted PK3 in the
ZIP. The output remains encrypted `.pk3` inside a lane ZIP.

The default trainer index path is
`TSVs\_spinda_tsv_trainer_index_tid_0x0000.json`. To regenerate it from the
8192-save bank:

```powershell
dotnet run --project tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- --save-dir TSVs --trainer-id 0 --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

Click `Extra settings ...` in unpack mode to show optional PK3 edits. Free-text
fields remain for nickname and OT name. Other edit fields use dropdowns so the
GUI sends known-good values to the existing backend flags.

Move slots work like a front-end editor: each slot has a Gen 3 move dropdown, a
PP Ups dropdown, and a read-only PP field. The PP field updates automatically
from the selected move's base PP and PP Ups value. The GUI still calls the
existing backend contract by sending `--set-moves`, `--set-pp`, and
`--set-pp-ups`; no separate backend move picker logic is needed.

Spinda level is shown as `Level N - EXP` and sends `--set-experience` with the
fast-growth EXP value for that level. Held items are alphabetized. Ball choices
are limited to the twelve Gen 3 GBA balls: Master, Ultra, Great, Poke, Safari,
Net, Dive, Nest, Repeat, Timer, Luxury, and Premier. The language dropdown is
limited to Gen 3 GBA languages: Japanese, English, French, Italian, German, and
Spanish.

Origin game is alphabetized and repopulates the met-location dropdown so Ruby
and Sapphire, Emerald, and FireRed/LeafGreen locations stay separated. The
location catalog omits entries explicitly marked unused. Pokerus uses PKHeX-style
strain and days dropdowns and sends the packed Pokerus byte. Ability slot is
shown as `0` or `1`; slot `1` maps to the backend's second ability value.

IVs, EVs, and contest stats are individual dropdowns. Because the backend
accepts those fields as six-value CSVs, either set all six dropdowns in that row
or leave all six as `No change`. Partial six-stat rows are rejected before a run.

The full move, held-item, and location lists are loaded from `gen3_moves.csv`,
`gen3_held_items.csv`, and `gen3_locations.csv` beside the GUI executable. If a
catalog file is missing, the GUI falls back to a very small built-in list so the
window can still open, but operator packages should keep all three CSV files
next to the executable.

Available edits include nickname, OT name, moves, PP Ups, Spinda level/EXP,
held item, friendship, Pokerus, EVs, IVs, contest stats, met location, met
level, origin game, ball, OT gender, language, and ability slot. The compressor
applies these after the selected PK3 state, then recalculates checksum and
re-encrypts the PK3 before writing the lane ZIP.

## GPU Behavior

`Use GPU` requests GPU rebuild with `--gpu-rebuild`. The CLI report tells you
what actually happened.

Check the `gpu_rebuild` object in verify/unpack reports:

| Field | Meaning |
| --- | --- |
| `requested` | GUI asked for GPU. |
| `used` | GPU rebuild actually ran. |
| `status` | `ok`, `fallback`, or error status. |
| `fallback_reason` | Clear reason when GPU was requested but not used. |
| `device_name` | CUDA device when used. |
| `compile_ms`, `upload_ms`, `kernel_ms`, `download_ms` | GPU timing breakdown. |
| `host_crc_ms` | CPU CRC time after GPU rebuild output is available. |
| `mismatched_lanes`, `mismatched_bytes` | GPU compare counters. |

GPU unavailable is not automatically a failure. A verify/unpack run can still
pass through CPU fallback if the report has `ok=true` and mismatch counters are
zero.

The first GPU run in a fresh GUI session can still pay CUDA/NVRTC startup cost.
Later GUI runs can reuse the hidden worker's runtime cache. Report fields such
as `runtime_cache_hit`, `runtime_initializations`, and
`runtime_failure_cached` show whether a later run reused cache state.

## Reports

Every GUI run shows the command line in the console, writes a JSON report, then
prints a native summary of that report. If `Compare JSON` points to another
report, the GUI also prints a field-by-field comparison.

Common checks:

| Mode | Report fields to check |
| --- | --- |
| verify | `ok`, `lane_count`, `internal_crc_mismatches`, `source_compare_mismatches`, `gpu_rebuild`. |
| pack | `ok`, `level`, `typed_level3`, codec/profile fields, compressed size. |
| consolidate | `ok`, `input_spc3_count`, `lane_count`, `copy_mode`, `spc3_size_bytes`. |
| inspect | schema/version/level/lane metadata. |
| unpack | `ok`, output count/path, `pk3_state`, `trainer_index`, `pk3_edits_enabled`, `crc_mismatches`, `gpu_rebuild`. |

The built-in summary covers path and config fields, codec/profile fields,
mismatch counters, GPU fallback/cache/download timings, GPU output byte/value
counts, CPU decode profile slices, and the `asm_recommendation` gate. For
release evidence, keep the JSON report and any Markdown/CSV summary generated
by the CLI report tools.

Use `Summary` when you only want to inspect existing reports:

1. Set `Report JSON`.
2. Optionally set `Compare JSON` to a CPU/GPU counterpart or another mode's
   report.
3. Click `Summary`.

## Troubleshooting

### Missing DLL Error

Run both build scripts from `<repo-root>`:

```powershell
cmd /c tools\spinda\spc3_prototype\build_spc3_prototype.bat
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
```

The GUI should not need MinGW runtime DLLs beside it. The compressor has its
codec/runtime libraries statically linked into `spc3_prototype.exe`; the build
script removes stale copied DLLs from older builds. A current GUI folder should
only need `spc3_verifier_gui.exe` and `spc3_prototype.exe` for normal launch.

### GUI Opens But Run Fails

Check the first line in the console pane. It shows the exact command. Confirm:

- `SPC3 exe` points to `spc3_prototype.exe`
- paths with spaces are quoted in the shown command
- `Report JSON` is set
- mode-specific required fields are not blank

### GPU Requested But Not Used

Open the report and check `gpu_rebuild.fallback_reason`. Common causes are:

- CUDA driver unavailable
- NVRTC unavailable
- unsupported stream shape
- GPU runtime initialization failure
- malformed typed stream

The fallback reason is part of the report so there should be no hidden
"requested but not used" case.

Clicking `Cancel` terminates the hidden worker, so the next GPU run starts with
a fresh CUDA/NVRTC cache. Cancel is tracked from the moment `Run` is clicked,
so a request made during worker startup is honored before the command is sent.

### Consolidate Fails

Consolidate is strict. Check for:

- two shards containing the same lane
- mixed SPC3 versions
- mixed levels or flags
- predictor mismatch
- a shard accidentally copied from a different experiment

Run inspect on the shards when the reason is not obvious.

### Verify Fails

Check:

- `internal_crc_mismatches`
- `source_compare_mismatches`
- GPU mismatch counters
- whether `Internal only` was checked
- whether the source ZIP root matches the `.spc3` being verified

An internal CRC mismatch means the rebuilt payload does not match the stored
payload CRC. A source compare mismatch means the rebuilt payload did not match
the original lane ZIP material.

## Packaging

For an operator package, run the GUI build and include the generated
executables from `tools\spinda\spc3_gui_native`:

- `spc3_verifier_gui.exe`
- `spc3_prototype.exe`
- `gen3_moves.csv`
- `gen3_held_items.csv`
- `gen3_locations.csv`
- `spc3_verifier_gui_baseline.exe` when sharing across mixed x86-64 machines
- `spc3_prototype_baseline.exe` when sharing across mixed x86-64 machines
- license notes for project source, zlib, zstd, liblzma, and optional CUDA/NVRTC

For `github-clean`, include source, build scripts, docs, and license notes. Do
not include generated `.exe`, `.dll`, `.spc3`, `.spinda80.zip`, `.pk3raw`,
cache, or private report artifacts unless a release package explicitly names
them.
