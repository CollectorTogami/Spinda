# Emulation Accuracy Harnesses

## Status Bucket

- Current status: Manual harness reference for stock-vs-custom source and runtime accuracy checks.
- Last verified date: 2026-04-26.
- Proven artifacts: scripts in [tools/emulation_accuracy](../emulation_accuracy), maintained local build outputs, and comparison commands listed below.
- Known gaps: Harnesses are not continuously run; results are only proof when the command output is captured for the current build.
- Next action: Run and record harness output before using this doc to claim emulator-accuracy parity.
- Evidence model: Claims must be labeled as `Proven`, `Observed once`, `Inferred`, `Planned`, or `Obsolete`; see `DOCUMENTATION_EVIDENCE_POLICY.md`.

This folder contains manual harnesses for comparing the custom `<repo-root>`
fork against an original-source mGBA reference tree or build.

These harnesses are intentionally **not** part of normal `pytest`, CMake, or
GUI startup behavior. They only do work when explicitly launched.

## Purpose

Goal: separate two kinds of evidence:

1. source-diff evidence
2. runtime-trace evidence

The current audit already established a strong source-diff conclusion:

- `src/gba`, `src/arm`, and `include/mgba` appear unchanged versus the upstream
  `0.10.5` reference tree
- the real risk surface is the control path around scripting, savestates,
  input timing, and Qt/Python orchestration

These harnesses exist to make that claim reproducible and to enable later
runtime stock-vs-custom trace comparison when requested.

## Files

| File | Purpose |
| --- | --- |
| [source_tree_accuracy_harness.py](source_tree_accuracy_harness.py) | Compares accuracy-relevant subtrees in the custom fork against the upstream source tree and writes a JSON report. |
| [capture_trace.py](capture_trace.py) | Host-side trace capture harness for one Python-enabled mGBA build. Loads a ROM, optional save/state, optional input tape, and records frame/memory samples. |
| [compare_traces.py](compare_traces.py) | Compares two trace captures and reports the first divergence. |
| `<repo-root>\tools\run_emulation_accuracy_harnesses.ps1` | PowerShell wrapper for source diff, trace capture, and dual-build comparison runs. |

## What These Harnesses Can Prove

### 1. Source Tree Accuracy

`source_tree_accuracy_harness.py` can prove whether the source trees differ in:

- `src/gba`
- `src/arm`
- `include/mgba`
- `src/core`
- `src/platform/qt`
- `src/platform/python`

This is useful for checking whether the hardware-emulation core still matches
upstream or whether a later local edit touched accuracy-sensitive areas.

### 2. Runtime Trace Accuracy

`capture_trace.py` plus `compare_traces.py` can compare two actual emulator
builds for one exact scenario.

That is the stronger runtime check. It lets us compare:

- frame counters
- Timer 1 values
- `KEYINPUT`
- `gRngValue`
- other configured memory addresses

under the same ROM/save/state/tape path.

## Current Operational Rule

These harnesses are staged and deployed, but they are **not** considered part
of the default test path. Run them only when a direct stock-vs-custom accuracy
check is wanted.

## Typical Manual Commands

### Source diff harness

```powershell
<repo-root>\.venv-mgba\bin\python.exe `
  <repo-root>\tools\emulation_accuracy\source_tree_accuracy_harness.py
```

### One-build trace capture

Use the workspace MinGW interpreter when you are capturing the maintained local
build:

```powershell
<repo-root>\.venv-mgba\bin\python.exe `
  <repo-root>\tools\emulation_accuracy\capture_trace.py `
  C:\path\to\game.gba `
  --save C:\path\to\game.sav `
  --state C:\path\to\checkpoint.state `
  --profile frlg_title_seed `
  --frames 240 `
  --output-json C:\temp\trace.json
```

Only use a different interpreter path when you are deliberately capturing a
different Python-enabled mGBA build for stock-vs-custom comparison.

### Compare two captures

```powershell
<repo-root>\.venv-mgba\bin\python.exe `
  <repo-root>\tools\emulation_accuracy\compare_traces.py `
  C:\temp\custom-trace.json `
  C:\temp\stock-trace.json
```

### Wrapper script

```powershell
<repo-root>\tools\run_emulation_accuracy_harnesses.ps1 -Mode source-diff
```

The wrapper defaults to `<repo-root>\.venv-mgba\bin\python.exe` for its own
host-side harness execution. In `dual-trace` mode, `-CustomPythonExe` and
`-StockPythonExe` must be passed explicitly because they refer to two distinct
builds on purpose.

## Limits

- The trace harness assumes a Python-enabled mGBA build for the build being
  captured.
- If the stock reference build has not been built with Python bindings, the
  runtime harness cannot capture it until that build exists.
- A matching source tree does not prove matching runtime behavior.
- A runtime trace only proves one scenario, not universal parity.

## Related Docs

- `<repo-root>\markdown-files\SPINDA_RNG_EMULATION_AUDIT_2026-04-19.md`
- `<repo-root>\markdown-files\index _markdown.md`
- Private CUDA route-model documentation index (not included in this clean tree)



