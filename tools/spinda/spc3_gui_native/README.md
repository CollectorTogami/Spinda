# SPC3 Native Verifier GUI

## Purpose

`spc3_verifier_gui.exe` is a native Win32 C++ launcher for
`spc3_prototype.exe`. It is intended as the disk-light operator path for SPC3:
verify an existing `.spc3` file, optionally use GPU rebuild, and write/summarize
a JSON report without exporting lane payload files.

Full operator guide: [SPC3_NATIVE_GUI_GUIDE.md](SPC3_NATIVE_GUI_GUIDE.md).
Short caveman TLDR:
[SPC3_NATIVE_GUI_CAVEMAN_TLDR.md](SPC3_NATIVE_GUI_CAVEMAN_TLDR.md).

## Build

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
```

Portable x86-64 baseline build:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

The GUI uses only Win32 APIs and the C++ standard library. It is dark mode by
default. It is not a Python GUI and has no Tkinter dependency. The build links
the MinGW C++ runtime statically so the GUI itself does not require
`libstdc++-6.dll`, `libgcc_s_seh-1.dll`, or `libwinpthread-1.dll` beside the
executable.

Runs are sent to a hidden persistent `spc3_prototype.exe --server` worker. That
keeps CUDA/NVRTC context and module cache state alive across multiple GUI runs,
which reduces repeated GPU startup overhead in normal operator use. The GUI
terminates the worker when the operator cancels, closes the GUI, or changes the
compressor path. Inside the repo the worker runs from the workspace root; in a
moved operator folder it runs from the GUI folder so relative paths stay local
to the package. Cancel requests are tracked during worker startup as well as
during active runs. Direct CLI launches remain one-shot.

The GUI build also rebuilds `spc3_prototype.exe` and copies the compressor into
`tools\spinda\spc3_gui_native`. Both shipped executables are statically linked
against MinGW and codec libraries, so that folder should not need non-system
DLLs. The baseline GUI build copies `spc3_prototype_baseline.exe` beside
`spc3_verifier_gui_baseline.exe`, and the baseline launcher prefers that
baseline compressor by default. The default SPC3 paths are absolute workspace
paths so they do not depend on Explorer's working directory.

CPU support is Windows x86-64/AMD64. AMD Ryzen, Threadripper, and EPYC CPUs are
valid targets. The default build is native-tuned for the build machine; the
`baseline` build avoids `-march=native` for shared packages. GPU offload is
NVIDIA CUDA only; AMD GPU systems use CPU fallback unless they also have a
CUDA-capable NVIDIA GPU.

## Default Mode

The GUI starts in `verify` mode with these disk-light defaults:

- `Internal only` enabled, which passes `--no-source-compare`
- `Use GPU` enabled, which passes `--gpu-rebuild`
- report JSON enabled
- no unpack directory writes

This means the default run rebuilds in RAM/GPU memory, checks internal CRCs,
and writes only the report.

Each path field has a `...` picker button. File rows use open/save file
dialogs, folder rows use a folder picker, and `Level` is a dropdown for SPC3
levels `0..3`.

`Report JSON` is summarized in the console after each run. Set `Compare JSON`
and click `Summary` to compare an existing CPU/GPU or pack/verify/unpack report
pair without running the compressor again. The summary includes the core path,
config, mismatch, GPU fallback/cache, GPU output-size, CPU profile, and ASM
recommendation fields.

Mode selection uses radio buttons. The GUI hides fields that do not apply to the
selected mode; for example, `Consolidate` shows shard/output/report fields, and
`Verify` only shows `Lane ZIP root` when source comparison is enabled. In
`Pack`, `Profile` is hidden for level `0`, while `Typed v0.2` and
`External predictor` are shown only for level `3`.

## Supported Modes

- `verify`: disk-light validation of an existing `.spc3`
- `pack`: build a v0.2 typed level-3 `.spc3` from available Phase 3 lane ZIPs
- `consolidate`: merge existing `.spc3` shards by copying compressed lane
  streams into one `.spc3`
- `inspect`: read metadata without decoding lane payloads
- `unpack`: explicit export path that writes `0xLLLL.spinda80.zip` files with
  encrypted `.pk3` records inside

Unpack mode includes a `PK3 state` radio group. `Egg` preserves the rebuilt
payload, while `Hatched shiny` and `Hatched not shiny` use the TSV trainer index
JSON to rewrite and re-encrypt each PK3 before it is stored in the output ZIP.
The trainer index field is hidden unless a hatched state is selected.

The unpack-only `Extra settings ...` button expands optional PK3 edit fields.
Move slots use Gen 3 move dropdowns loaded from `gen3_moves.csv`; PP is
read-only and is calculated from the selected move's base PP and PP Ups value.
Held items and met locations use `gen3_held_items.csv` and
`gen3_locations.csv`. Ball and language dropdowns are limited to values
attainable in the Gen 3 GBA games. Other numeric PK3 fields use bounded
dropdowns: Spinda level maps to the correct fast-growth EXP value, Pokerus is
split into strain and days, and each IV, EV, and contest stat is selected
separately. The GUI still passes the existing backend flags such as
`--set-moves`, `--set-pp`, `--set-pp-ups`, `--set-evs`, and `--set-ivs`. `No
change` means no edit is sent.

## Consolidating Pre-Compressed Lanes

Use `consolidate` mode when Phase 3 is incomplete but some lane batches are
already packed as `.spc3` shards. The C++ compressor scans `SPC3 shard root`
for `.spc3` files, rejects duplicate lanes or incompatible shard layouts, and
writes a single output `.spc3` without unpacking or recompressing lane payloads.

The CLI equivalent is:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode consolidate --consolidate-root Phase3SpindaBlocks --output Phase3SpindaBlocks\combined.spc3 --report Phase3SpindaBlocks\combined_consolidate_report.json
```

## Packaging Notes

Ship the contents of `tools\spinda\spc3_gui_native` after running the GUI build.
That folder contains the GUI, the Gen 3 catalog CSVs, and a local copy of
`spc3_prototype.exe`. Keep `gen3_moves.csv`, `gen3_held_items.csv`, and
`gen3_locations.csv` beside the GUI for complete dropdown lists; without them,
the GUI falls back to tiny built-in lists. The folder should not contain
MinGW/zlib/zstd/lzma DLLs after the static build. If the compressor is moved
elsewhere, keep the GUI's `SPC3 exe` field pointed at the moved compressor. Do
not include generated `.spc3`, `.spinda80.zip`, `.pk3raw`, or private report
artifacts in clean source packages.
