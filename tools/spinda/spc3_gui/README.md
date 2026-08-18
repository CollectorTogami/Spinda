# SPC3 GUI

## Status Bucket

- Current status: Developer-only Tkinter wrapper for the verified SPC3
  prototype CLI, the Python umbrella compressor CLI, report summary, and
  two-report comparison. The shippable GUI path is the native C++ Win32
  verifier in `tools/spinda/spc3_gui_native`.
- Last verified date: 2026-06-01.
- Proven artifacts: `spc3_gui.py`, `spc3_compress.py`, `spc3_prototype.exe`,
  and SPC3 pack/verify/unpack reports.
- Known gaps: This Python GUI is intentionally thin and is not the shipping
  operator GUI. It does not replace CLI reports, does not expose benchmark
  matrices, and does not implement compression logic.
- Next action: Use it for manual pack/verify/inspect/unpack/compress smoke
  after CLI changes pass regression.

## Run

Developer-only Tkinter wrapper:

```powershell
python .\tools\spinda\spc3_gui\spc3_gui.py
```

Shipping native verifier GUI:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
.\tools\spinda\spc3_gui_native\spc3_verifier_gui.exe
```

The GUI starts with v0.2 typed level `3` and `--codec-profile fast`, which maps
to zstd-9. `auto` still means compatibility zlib-9.

## Test

Run the focused umbrella CLI/GUI regression harness:

```powershell
python -B .\tools\spinda\test_spc3_compress.py
```

The harness mocks expensive pack/verify calls and checks target dispatch,
v2 verify input selection, summary accounting, GUI command construction, and
the `all` target output-field state.

## Scope

Supported commands:

- pack
- verify
- unpack
- inspect
- compress

Controls:

- SPC3 executable path
- umbrella compressor CLI path
- root lane ZIP folder
- predictor JSON
- input `.spc3`
- output `.spc3`
- unpack directory
- report JSON
- compare report A/B JSON
- lane limit
- level
- codec profile: `auto`, `compat`, `fast`, `small`
- typed v0.2 toggle for pack
- use GPU toggle for verify/unpack
- internal-only verify toggle
- SPC3 compression target: `v2`, `v3`, `v4`, `v5`, `v6`, `v7`, `v8`, or `all`

`compress` mode invokes `tools/spinda/spc3_compress.py` with
`--mode pack-verify`. For a single target, the Output field chooses the new
SPC3 file. For target `all`, the umbrella CLI writes per-version outputs and
reports under its output directory and the GUI disables the single Output
field.

The report summary reads the JSON written by the CLI and shows status,
pack round-trip mismatches, source-compare state, codec/profile, GPU
status/fallback/timings, GPU cache/download state, host CRC timing, and CPU
decode profile timings/backend/byte counts when present. The compare button
loads two report JSON files and shows pack/verify/unpack/compress, CPU/GPU,
mismatch, size, and timing differences in the summary pane. For release
packaging, the CLI-side `spc3_report_tools.py release-summary` command creates
the concise pack/verify/unpack CPU/GPU evidence file; the GUI stays focused on
interactive inspection and pairwise report comparison.
The CLI creates missing parent folders for output and report paths.
Cancel requests termination of the active CLI process, disables the cancel
button immediately, and still loads any report JSON the CLI finished writing
before exit.

## Policy

The GUI stays a wrapper. Native v2 validation, packing, decoding, GPU
fallback, codec policy, and report generation stay in `spc3_prototype.exe`.
Version selection for v2 through v8 stays in `spc3_compress.py`; format-specific
logic remains in the individual repacker modules.

## License Audit

Program source:

- `spc3_gui.py` follows this repository's default MPL-2.0 source license.
- It uses only Python standard-library modules: `tkinter`, `pathlib`,
  `subprocess`, `threading`, `queue`, `json`, `os`, and `sys`.
- It does not grant redistribution rights for generated Pokemon files, ROMs,
  saves, or other game data.

Runtime dependency:

- The GUI invokes the local `spc3_prototype.exe` for native v2 operations and
  `spc3_compress.py` for v2-v8 umbrella compression selection. Codec and CUDA
  dependencies are the same optional runtime dependencies documented in the
  SPC3 prototype README.
