# SPC3 Prototype

## Status Bucket

- Current status: Working C++ prototype with x86-64 assembly hot loops, real
  `.spc3` pack/unpack/verify/inspect/bench modes, native zlib/zstd/LZMA2
  stream-codec hooks, experimental byte-rANS typed substreams, SPC3 v0.2
  typed level-3 pack/unpack, optional CUDA/NVRTC GPU rebuild-offload for
  typed unpack/verify, and Phase 3 PK3 custom-compression modeling.
- Last verified date: 2026-05-08.
- Proven artifacts: `spc3_prototype.cpp`, `spc3_hotloops_x86_64.S`,
  `build_spc3_prototype.bat`, and
  `Phase3SpindaBlocks\_spc3_prototype_report.json`,
  `Phase3SpindaBlocks\_spc3_streaming_zstd9_gate_report.json`, and
  `Phase3SpindaBlocks\_spc3_typed_level3_bench_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_pack_report.json`, and
  `Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`,
  `Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_verify_report.json`, and
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_unpack_report.json`, and
  `Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md`.
- Known gaps: SPC3 v0.1 is frozen as the first compatible layout. SPC3 v0.2
  typed level-3 files now work, but are still prototype format version `2`.
  rANS/FSE is implemented as an experimental typed-stream codec and is not the
  default because the 1024-lane gate did not beat typed zstd-9 enough to pay
  for the extra complexity. GPU offload works for typed level-3 unpack/verify,
  with CPU fallback, but is not yet a default codec/container path.
- Short version: [README-tldr.md](README-tldr.md) keeps the same facts in
  caveman-full form.
- Format spec: [SPC3_V0_1_FORMAT.md](SPC3_V0_1_FORMAT.md) documents the frozen
  header, lane table, stream layout, stable fields, and experimental fields.
- v0.2 design note:
  [SPC3_V0_2_TYPED_LEVEL3_PLAN.md](SPC3_V0_2_TYPED_LEVEL3_PLAN.md) documents
  the typed level-3 split, codec policy draft, rANS/FSE gate, and GUI boundary.
- C++/ASM boundary:
  [SPC3_CPP_ASM_BOUNDARY.md](SPC3_CPP_ASM_BOUNDARY.md) documents the C++, CUDA,
  and assembly split and the profiling gate for future ASM work.
- Next action: Use the v0.2 typed level `3` fast path for current work, keep
  GPU optional, keep targeted ASM active, and collect full Phase 3 evidence
  before any default-format change.

## Wrap-Up Decision

As of 2026-05-08, SPC3 is good enough to use for the current narrow scope:
Phase 3 Spinda PK3 lane compression, verification, inspection, unpack, and
pre-compressed shard consolidation. The active path is v0.2 typed level `3`
with `--codec-profile fast` for new work. Keep `auto` as zlib-9 for
compatibility, `fast` as zstd-9, and `small` as LZMA2-9.

The 65,536-lane full-corpus numbers are estimates until Phase 3 finishes.
Linear extrapolation from the 1024-lane typed zstd-9 gate gives roughly:

| Scope | Source ZIP bytes | Typed zstd-9 SPC3 bytes | CPU typed unpack |
| --- | ---: | ---: | ---: |
| 1024 lanes measured | `11,707,706,577` | `17,743,980` | `4,415.682` ms |
| 65,536 lanes estimated | `749,293,220,928` | `1,135,614,720` | `282.604` s |

Those estimates assume the remaining lanes look like the measured sample. They
are directionally useful, not a release guarantee.

Final current-binary smoke evidence:
`Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md`.
It packs 4 real lanes as v0.2 typed level `3` with `--codec-profile fast`, then
runs CPU verify, GPU verify, CPU unpack, GPU unpack, CPU/GPU unpack hash
comparison, and a repeated GPU cache smoke. All mismatch counters are `0`; the
second cache sample reports `runtime_cache_hit=true` and `compile_ms=0.000`.

Final low-level policy:

- ASM stays active for measured Windows x86-64 hot loops, including
  `spc3_shuffle48_asm`.
- Broader PK3 rebuild/encrypt ASM waits until CRC reduction, batching, or a
  proven same-polynomial CRC32 backend decision is made.
- GPU stays optional and NVIDIA CUDA-only. It is useful for large typed
  verify/unpack rebuild batches and remains safe-fallback for unsupported cases.
- The native GUI is intentionally simple: operator modes, codec/profile choice,
  disk-light verify, optional GPU, report summary/compare, and no deep GPU
  tuning.

## Purpose

`spc3_prototype.cpp` is the test bed for the Spinda custom-compression idea.
It reads Phase 3 lane ZIPs, decrypts PK3 records, checks the PID-upper-half to
IV32 predictor, rebuilds the encrypted PK3 records, and compares the result
byte for byte.

It is CPU-first, with optional GPU typed-rebuild paths. The hot loops work over
contiguous `65536 * 80` byte lane buffers, which gives SIMD, assembly, CUDA,
or other low-level work a plain memory layout instead of a pile of ZIP entry
objects.

## What This Program Is

SPC3 is the project name for the planned Spinda-focused compression format.
This program now has the first real container implementation, but it is still a
prototype. It is the tool we use to decide what the format should store, what it
can rebuild, and where the runtime goes. It answers these practical questions:

1. Can every PK3 in a Phase 3 lane ZIP be decrypted, modeled, rebuilt, and
   compared without losing a byte?
2. How much of the PK3 payload is predictable from lane/PID structure and the
   current IV32 predictor table?
3. Which stages cost CPU time: ZIP parsing, ZIP inflate, PK3 crypto, predictor
   modeling, rebuild proof, or entropy probing?
4. Is there enough non-ZIP work left to justify more assembly, SIMD, or CUDA
   work?
5. Can a `.spc3` file unpack and verify back to exactly the same ordered
   80-byte PK3 records as the source lane ZIPs?

The latest verified samples say the PK3 model is stable, the assembly hot
loops are active, v0.2 typed files round-trip, CUDA can rebuild typed level-3
lanes byte-exact in real unpack/verify modes, and experimental rANS/FSE
round-trips typed bitmap/XOR streams. ZIP inflate and entropy coding still
decide the default format; CUDA is useful for large typed rebuild batches but
stays optional because transfer/startup overhead dominates small runs.

## Licensing

The prototype source files in this folder follow the repository default
MPL-2.0 license unless a file says otherwise. That includes
`spc3_prototype.cpp`, `spc3_hotloops_x86_64.S`,
`build_spc3_prototype.bat`, `test_spc3_prototype.py`, and these README files.

Credit: Shawrkie created the streaming SPC3 prototype variant integrated into
`spc3_prototype.cpp`; keep this credit with source and binary packages that
include it.

The current build links zlib (`-lz`), zstd (`-lzstd`), and liblzma (`-llzma`).
zlib is still used for ZIP inflate, CRC32 checks, deflate probes, the default
SPC3 stream codec, and the embedded predictor stream. zstd is available as a
native decode-speed-focused SPC3 lane stream backend. liblzma is available as a
native LZMA2/XZ prototype backend for size comparisons, but it is not the
default.

zlib is already bundled in the project under `src/third-party/zlib/`; keep
`res/licenses/zlib.txt` with any source or binary package containing this
prototype. If binaries ship with the MSYS2 zstd or liblzma DLLs, include their
license text alongside the package as well. The prototype still does not vendor
or link 7-Zip, PKHeX.Core, OpenCL, `py7zr`, or libarchive. CUDA is optional:
`--bench-gpu` and `--gpu-rebuild` dynamically load the installed NVIDIA driver
and NVRTC DLLs at runtime instead of making the normal CPU build depend on CUDA
libraries.

Generated reports and any future `.spc3` files are artifacts. They do not grant
redistribution rights for ROMs, saves, savestates, PK3 files, or other game
data.

## Data Model

The prototype expects each Phase 3 lane ZIP to contain one full lane of boxed
PK3 records:

| Layer | Shape | Purpose |
| --- | --- | --- |
| Lane ZIP | `0xLLLL.spinda80.zip` | Existing production archive for one lower PID half. |
| PK3 entry | `0xUUUULLLL.pk3` | One encrypted 80-byte Gen 3 Pokemon record. |
| Lower half | `LLLL` | Lane ID, also low 16 bits of every PID in that lane. |
| Upper half | `UUUU` | Record index inside the lane and high 16 PID bits. |
| IV32 | 32-bit value in the decrypted growth/misc payload | Main modeled variable for compression. |
| Template bytes | Decrypted bytes that should be constant across the lane except modeled fields | Used to prove lane-level payload consistency. |
| Exception layer | Bitmap plus XOR values for predictor misses | Candidate compressed side stream for a future `.spc3` file. |

It treats the ZIP as untrusted input. Local headers, central headers, ZIP64
metadata, names, sizes, flags, and CRCs all have to agree before the PK3 model
gets to say anything useful.

## Pipeline

For each lane ZIP, it currently does this:

1. Read the ZIP file into RAM.
2. Locate EOCD, optional ZIP64 EOCD, locator, and central directory.
3. Validate single-volume ZIP metadata and reject unsupported data descriptors.
4. Parse central entries and cross-check local headers, names, methods, flags,
   CRC32 values, sizes, offsets, and ZIP64 extra fields.
5. Inflate each PK3 entry into a contiguous encrypted lane buffer.
6. Verify payload CRC32 and expected entry name/PID relationship.
7. Decrypt one PK3 record at a time using Gen 3 XOR and block shuffle logic.
8. Verify PK3 checksum and content PID.
9. Compare decrypted constant regions against one canonical lane template.
10. Read actual IV32, compare it against the predictor table, and record
    exception bitmap/XOR values.
11. Rebuild the encrypted PK3 from the canonical template, modeled IV32, and
    original PID/OTID data.
12. Compare rebuilt bytes against the original encrypted PK3.
13. Run in-memory entropy probes for modeled streams unless disabled.
14. Write one JSON report with counters, timings, compression estimates, and
    per-lane errors.

It never writes loose PK3 files.

## Build

From `<repo-root>`:

```powershell
tools\spinda\spc3_prototype\build_spc3_prototype.bat
```

The build script links the MinGW C++ runtime plus zlib, zstd, and liblzma
statically. It also removes stale copied runtime DLLs from older builds. The
resulting `spc3_prototype.exe` should only depend on Windows system DLLs.

CPU target: Windows x86-64/AMD64. AMD Ryzen, Threadripper, and EPYC CPUs are
valid targets; this is not Intel-only code. The default build uses
`-march=native`, so use a baseline x86-64 build for binaries that must run
across older or mixed AMD/Intel machines.

Baseline build:

```powershell
tools\spinda\spc3_prototype\build_spc3_prototype.bat baseline
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

The baseline compressor is written to
`tools\spinda\spc3_prototype\spc3_prototype_baseline.exe`. The baseline GUI
build writes `spc3_verifier_gui_baseline.exe` and bundles the baseline
compressor beside it as `spc3_prototype_baseline.exe`.

GPU target: NVIDIA CUDA only by design. Other GPU systems use CPU fallback
unless an NVIDIA CUDA GPU is also available.

Equivalent manual command:

```powershell
$env:PATH='C:\msys64\mingw64\bin;'+$env:PATH
C:\msys64\mingw64\bin\g++.exe -std=c++20 -O3 -march=native -Wall -Wextra -pedantic tools\spinda\spc3_prototype\spc3_prototype.cpp tools\spinda\spc3_prototype\spc3_hotloops_x86_64.S -o tools\spinda\spc3_prototype\spc3_prototype.exe -lz -lzstd -llzma
```

## Run

Default 20-lane sample:

```powershell
$env:PATH='C:\msys64\mingw64\bin;'+$env:PATH
.\tools\spinda\spc3_prototype\spc3_prototype.exe --root Phase3SpindaBlocks --limit-zips 20 --report Phase3SpindaBlocks\_spc3_prototype_report.json
```

Internal self-test:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --self-test
```

Regression harness:

```powershell
<repo-root>\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\test_spc3_prototype.py
```

Native disk-light verifier GUI:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
.\tools\spinda\spc3_gui_native\spc3_verifier_gui.exe
```

The shippable GUI is a native C++/Win32 wrapper around this CLI. It defaults to
a disk-light internal verifier path (`verify`, `--no-source-compare`, report
output, optional `--gpu-rebuild`) and also exposes pack, inspect, unpack, and
compressed-stream `consolidate` for existing `.spc3` shards. It also shows a
native report summary after runs and can compare the current report against a
second JSON report for CPU/GPU or pack/verify/unpack evidence checks. The older
Tkinter wrapper in `tools\spinda\spc3_gui` remains developer-only. Full guide:
[SPC3_NATIVE_GUI_GUIDE.md](../spc3_gui_native/SPC3_NATIVE_GUI_GUIDE.md).

The native GUI uses `spc3_prototype.exe --server` as a persistent hidden worker.
That preserves the in-process CUDA/NVRTC context/module cache across multiple
GUI runs until the GUI exits or the operator cancels a run. Normal direct CLI
invocations remain one-shot and safe.

Pack, unpack, and report paths create missing parent directories. The tool
still does not delete old unpack outputs; choose a fresh `--unpack-dir` when
you want a clean payload set.

Useful options:

| Option | Meaning |
| --- | --- |
| `--mode audit` | Default old audit/report path. |
| `--mode pack` | Read lane ZIPs and write one `.spc3` container. |
| `--mode unpack` | Read `.spc3` and write `0xLLLL.spinda80.zip` lane ZIPs by default; raw `0xLLLL.pk3raw` is still available. |
| `--mode verify` | Rebuild `.spc3` payloads and compare hashes, plus source ZIPs unless disabled. |
| `--mode inspect` | Read `.spc3` metadata without unpacking lane payloads. |
| `--mode consolidate` | Merge existing `.spc3` shards by copying compressed lane streams into one `.spc3`. |
| `--mode bench` | Build RAM-only compression oracle results for sample sizes and levels. |
| `--root PATH` | Directory containing `0xLLLL.spinda80.zip` lane files. |
| `--predictor PATH` | Predictor JSON, normally `_phase3_pid_second_half_iv_reference.json`. |
| `--report PATH` | One JSON audit report path. |
| `--input PATH` | Input `.spc3` for `unpack`, `verify`, or `inspect`. |
| `--output PATH` | Output `.spc3` for `pack`. |
| `--unpack-dir PATH` | Directory where `unpack` writes lane ZIP or raw payload output. |
| `--unpack-format zip\|raw` | `unpack` output format. Default `zip` writes `0xLLLL.spinda80.zip` files containing encrypted `0xUUUULLLL.pk3` records. `raw` writes ordered `0xLLLL.pk3raw` payloads. |
| `--pk3-state egg\|hatched-shiny\|hatched-not-shiny` | `unpack` ZIP corpus state. Default `egg` preserves the SPC3 payload. Hatched modes rewrite each encrypted PK3 with trainer data from `--trainer-index`. |
| `--trainer-index PATH` | JSON index of the 8192 TSV save lanes. Required for hatched unpack output. Current generated path: `TSVs\_spinda_tsv_trainer_index_tid_0x0000.json`. |
| `--set-nickname TEXT`, `--set-ot-name TEXT` | `unpack` PK3 edit layer. Gen 3 English strings, max 10 nickname chars and 7 OT chars. |
| `--set-moves A,B,C,D`, `--set-pp A,B,C,D`, `--set-pp-ups A,B,C,D` | `unpack` PK3 edit layer for move ids, current PP, and PP Up counts. |
| `--set-evs HP,ATK,DEF,SPA,SPD,SPE`, `--set-ivs HP,ATK,DEF,SPA,SPD,SPE` | `unpack` PK3 edit layer for EV bytes and IV values. |
| `--set-contest COOL,BEAUTY,CUTE,SMART,TOUGH,FEEL` | `unpack` PK3 edit layer for Gen 3 contest stats and feel/sheen. |
| `--set-held-item N`, `--set-experience N`, `--set-friendship N`, `--set-pokerus N` | `unpack` PK3 edit layer for common growth/status fields. |
| `--set-met-location N`, `--set-met-level N`, `--set-origin-game R\|S\|E\|FR\|LG\|N`, `--set-ball N`, `--set-ot-gender 0\|1`, `--set-language N`, `--set-ability-number 1\|2` | `unpack` PK3 edit layer for origin metadata and the Gen 3 ability bit. |
| `--lane-select all\|one\|range` | `unpack` lane selector. Default `all`. `--lane`, `--lane-from`, and `--lane-to` set this automatically. |
| `--lane HEX` | `unpack` one shared PID lower-half lane, `0..FFFF` or `0x0000..0xFFFF`. |
| `--lane-from HEX`, `--lane-to HEX` | `unpack` inclusive shared PID lower-half lane range. |
| `--consolidate-root PATH` | Directory of pre-compressed `.spc3` shards for `consolidate`. |
| `--level N` | SPC3 level `0..3` for `pack`. |
| `--codec NAME` | `auto`, `none`, `zlib`, `zstd`, `lzma2`, or experimental typed-stream `rans`; default `auto` writes `none` for level `0` and `zlib-9` for levels `1..3`. |
| `--codec-level N` | Pack levels `1..3` only: zlib level `1..9`, zstd level `1..22`, or LZMA2 preset `0..9`; explicit levels require `--codec`; rANS has no level. Level `0` is raw and does not accept codec levels. |
| `--codec-profile NAME` | Pack levels `1..3` shortcut: `compat` = zlib-9, `fast` = zstd-9, `small` = LZMA2-9; mutually exclusive with `--codec` and `--codec-level`. Level `0` is raw and does not accept profiles. |
| `--typed-level3` | Pack level `3` as SPC3 v0.2 typed template/bitmap/XOR substreams instead of v0.1 fused stream. |
| `--typed-exceptions-only` | With `--typed-level3`, leave template raw and apply `--codec` only to bitmap/XOR streams. |
| `--gpu-rebuild`, `--gpu` | For `unpack` or `verify`, try CUDA/NVRTC typed level-3 rebuild and fall back to CPU when unsupported. |
| `--external-predictor` | For level `3` pack, omit the embedded predictor and require `--predictor` at decode time. |
| `--limit-zips N\|all` | Number of sorted lane ZIPs to use. `all` or `0` means every valid lane ZIP found. Sparse corpuses are allowed. |
| `--all-zips` | Use every valid `0xLLLL.spinda80.zip` in `--root`; lane `0x0000` is not required. |
| `--bench-limits LIST` | Comma list for `bench`, default `1,4,20,64`. |
| `--bench-streaming` | Bench one lane model at a time, compute exact v0.1 container sizes, and avoid building giant `.spc3` byte buffers. |
| `--bench-typed-level3` | Streaming-only v0.2 oracle: split level-3 template, bitmap, and XOR values into separate substreams and round-trip them. |
| `--bench-gpu` | Streaming bench CUDA/NVRTC typed level-3 rebuild offload; CPU still parses ZIP and verifies bytes. |
| `--bench-rans-fse` | Include experimental rANS/FSE typed exception-stream rows; implies streaming typed level-3 bench. |
| `--bench-levels LIST` | Native codec SPC3 levels to test, default `1,2,3`; use `3` for targeted level-3 runs. |
| `--bench-codecs LIST` | Native codecs to test, like `zlib-9,zstd-9`; also enables native codec bench. |
| `--bench-native-codecs` | Compare native zlib-9, zstd-3, zstd-9, zstd-19, and LZMA2-9 across SPC3 levels `1..3`. |
| `--bench-external` | Let `bench` write temporary raw payload files and run external 7z/zstd comparisons when tools exist. |
| `--no-source-compare` | Let `verify` check internal `.spc3` hashes without source ZIP comparison. |
| `--no-predictor` | Skip predictor/exception modeling. |
| `--no-entropy-probe` | Skip in-memory zlib size probes. |

## Mode Input/Output Summary

| Mode | Required inputs | Main outputs | Notes |
| --- | --- | --- | --- |
| `audit` | `--root` lane ZIP directory; optional `--predictor`; optional `--limit-zips`/`--all-zips` | JSON report at `--report` | Reads valid `0xLLLL.spinda80.zip` files and writes evidence only. Sparse corpuses are allowed. No `.spc3` or payload export. |
| `pack` | `--root` lane ZIP directory, `--level`, `--output`, `--report`; optional `--predictor`, codec/profile, and `--typed-level3` | `.spc3` file at `--output`; pack JSON report | Active main path is `--level 3 --typed-level3 --codec-profile fast`. |
| `verify` | `--input` `.spc3`; `--report`; optional `--root` for source ZIP comparison; optional `--predictor` if the container needs an external predictor | Verify JSON report | Does not write lane payloads. Use `--no-source-compare` for disk-light internal verification. `--gpu-rebuild` may use CUDA for typed level-3 rebuild and reports fallback reason when CPU is used. |
| `unpack` | `--input` `.spc3`; `--unpack-dir`; `--report`; optional `--predictor`, lane selector, `--pk3-state`, `--trainer-index`, and `--gpu-rebuild` | Default: `0xLLLL.spinda80.zip` per selected lane; optional raw `0xLLLL.pk3raw`; unpack JSON report | ZIP output contains stored encrypted `0xUUUULLLL.pk3` records. `egg` preserves payload bytes; hatched modes rewrite encrypted PK3 records from the TSV trainer index. Select lanes with `--lane`, `--lane-from`/`--lane-to`, or `--lane-select all`. |
| `inspect` | `--input` `.spc3`; `--report` | Inspect JSON report | Reads headers, lane table, predictor metadata, and stream metadata without rebuilding payloads. |
| `consolidate` | `--consolidate-root` directory of `.spc3` shards; `--output`; `--report` | Combined `.spc3`; consolidate JSON report | Copies compatible compressed lane streams. It does not unpack or recompress payloads. |
| `bench` | `--root` lane ZIP directory; `--report`; optional bench limits/codecs/GPU/rANS flags | Benchmark JSON report; temporary model files only for external oracle mode | Streaming bench keeps memory lower for large samples. Benchmark output is evidence, not a release container. |

## SPC3 Container Modes

Pack one lane at level 3:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --root Phase3SpindaBlocks --limit-zips 1 --level 3 --output Phase3SpindaBlocks\sample.spc3 --report Phase3SpindaBlocks\_spc3_pack_report.json
```

Verify against source ZIPs:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --input Phase3SpindaBlocks\sample.spc3 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_verify_report.json
```

Unpack to production-shaped lane ZIPs:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked --report Phase3SpindaBlocks\_spc3_unpack_report.json
```

The default output is one `0xLLLL.spinda80.zip` per selected lane. Each ZIP
contains `65,536` stored `0xUUUULLLL.pk3` entries. The `.pk3` bytes are the
encrypted Gen 3 PK3 records rebuilt from the SPC3 model, matching the Phase 3
lane ZIP shape.

To unpack only selected lanes:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_one --lane 00A5 --report Phase3SpindaBlocks\_spc3_unpack_one_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_range --lane-from 0001 --lane-to 00FF --report Phase3SpindaBlocks\_spc3_unpack_range_report.json
```

Corpus-state export:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_egg_zips --pk3-state egg --report Phase3SpindaBlocks\_spc3_unpack_egg_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_hatched_shiny_zips --pk3-state hatched-shiny --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json --report Phase3SpindaBlocks\_spc3_unpack_hatched_shiny_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_hatched_not_shiny_zips --pk3-state hatched-not-shiny --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json --report Phase3SpindaBlocks\_spc3_unpack_hatched_not_shiny_report.json
```

`egg` is byte-preserving. `hatched-shiny` decrypts each rebuilt PK3, chooses the
trainer entry whose TSV matches that PID's PSV, clears egg state, writes trainer
TID/SID/name/gender/version/language, refreshes the Gen 3 checksum, and stores
the re-encrypted PK3 in the output ZIP. `hatched-not-shiny` uses the next TSV
`(PSV + 1) & 8191` to force a deterministic non-shiny result. ZIP entry names
stay PID-based, and the `.pk3` payloads remain encrypted.

Regenerate the TSV trainer index from the current save bank when needed:

```powershell
dotnet run --project tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- --save-dir TSVs --trainer-id 0 --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

The generated index is sufficient for the compressor hatch modes when it has
8192 entries, `complete=true`, and zero hard issues. The C++ unpack path checks
that every entry's computed TSV matches its TID/SID before any PK3 is rewritten.

Assorted output edits can be layered on the same export. The compressor applies
the hatch/shiny state first, then applies explicit `--set-*` edits, refreshes
the Gen 3 checksum, and writes encrypted `.pk3` records back into the lane ZIP.

Example edited shiny export:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_edited_shiny_zips --lane 0001 --pk3-state hatched-shiny --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json --set-nickname EDITSPIN --set-moves 33,45,0,0 --set-pp 35,30,0,0 --set-pp-ups 3,0,0,0 --set-evs 1,2,3,4,5,6 --set-ivs 31,30,29,28,27,26 --set-held-item 1 --set-friendship 200 --report Phase3SpindaBlocks\_spc3_edited_shiny_report.json
```

The edit layer intentionally does not change PID or species. PID controls lane
identity and shiny math; species remains Spinda for this corpus.

Raw payload export remains available when a tool needs one concatenated lane
buffer:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_raw --unpack-format raw --report Phase3SpindaBlocks\_spc3_unpack_raw_report.json
```

Inspect metadata without unpacking:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode inspect --input Phase3SpindaBlocks\sample.spc3 --report Phase3SpindaBlocks\_spc3_inspect_report.json
```

Consolidate pre-compressed shards without unpacking or recompressing payloads:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode consolidate --consolidate-root Phase3SpindaBlocks\_spc3_shards --output Phase3SpindaBlocks\combined.spc3 --report Phase3SpindaBlocks\combined_consolidate_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --input Phase3SpindaBlocks\combined.spc3 --no-source-compare --report Phase3SpindaBlocks\combined_verify_report.json
```

`consolidate` scans one directory for `.spc3` shards, rejects duplicate lanes
or incompatible shard layouts, copies existing compressed lane streams, rewrites
table offsets, and writes one combined `.spc3`. This is the intended path when
Phase 3 is incomplete but some lane batches have already been packed.

Run the compression oracle:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --root Phase3SpindaBlocks --bench-limits 1,4,20,64 --report Phase3SpindaBlocks\_spc3_bench_report.json
```

Run the lower-copy streaming oracle for larger samples:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-streaming --root Phase3SpindaBlocks --bench-limits 1,4,20,64,256,1024 --report Phase3SpindaBlocks\_spc3_streaming_bench_report.json
```

Add `--bench-external` when you want real 7z/LZMA2 and zstd oracle numbers.
That mode writes one temporary payload per model input, runs
`7z -t7z -m0=lzma2 -mx=9 -md=64m -ms=on -mmt=on` and `zstd -19 -T0` when the
tools are available, records output sizes/timings, then deletes the temporary
folder. The external inputs are encrypted raw payload, decrypted solid payload,
template plus IV32, and template plus predictor exception data.

Add `--bench-native-codecs` when you want SPC3 files built in RAM with native
container stream codecs. It compares zlib-9, zstd-3, zstd-9, zstd-19, and
LZMA2-9 across SPC3 levels `1`, `2`, and `3`, then records size, pack time,
unpack time, verify time, decode MiB/s, and CRC mismatch count. This is the
first real zstd/LZMA2 container path, not just an external raw-payload oracle.

Add `--bench-streaming` when sample count is large. It reads and models one
lane at a time, compresses/decompresses each lane stream immediately, compares
rebuilt bytes against the source payload, and computes exact SPC3 v0.1 file
sizes from `header + predictor + table + stream bytes`. It cannot combine with
`--bench-external` because external oracle mode intentionally builds
concatenated payload files.

Add `--bench-typed-level3` with `--bench-streaming` when you want the v0.2
layout oracle. It keeps v0.1 pack/unpack unchanged, then estimates a typed
level-3 layout by compressing these per-lane substreams separately:

- 80-byte decrypted template
- 8192-byte exception bitmap
- u32 XOR exception values

The report writes `typed_level3_matrix` rows for raw, all-codec, and
exceptions-only policies. Every row is decoded and rebuilt back to the source
payload before it counts.

Filter native streaming runs when you only need a decision gate:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-streaming --bench-levels 3 --bench-codecs zlib-9,zstd-9 --root Phase3SpindaBlocks --bench-limits 1,4,20,64,256,1024 --report Phase3SpindaBlocks\_spc3_streaming_zstd9_gate_report.json
```

Run the typed level-3 oracle like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-streaming --bench-typed-level3 --bench-levels 3 --bench-codecs zlib-9,zstd-9,lzma2-9 --root Phase3SpindaBlocks --bench-limits 1,4,20,64 --report Phase3SpindaBlocks\_spc3_typed_level3_bench_report.json
```

Pack a real SPC3 v0.2 typed level-3 file like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --typed-level3 --codec-profile fast --root Phase3SpindaBlocks --limit-zips 4 --level 3 --output Phase3SpindaBlocks\typed-v2.spc3 --report Phase3SpindaBlocks\_spc3_typed_v2_pack_report.json
```

`--codec auto` remains the compatibility policy: zlib-9 for levels `1..3`,
including typed v0.2. Use `--codec-profile fast` for the recommended v0.2
typed zstd-9 path, or `--codec-profile small` for LZMA2-9 archive-oracle
runs.

Run the CUDA/NVRTC GPU rebuild-offload proof like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-gpu --bench-limits 4 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json
```

Use CUDA/NVRTC for real typed v0.2 unpack/verify like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --gpu-rebuild --input Phase3SpindaBlocks\typed-v2.spc3 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_gpu_verify_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --gpu-rebuild --input Phase3SpindaBlocks\typed-v2.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_gpu_unpacked --report Phase3SpindaBlocks\_spc3_gpu_unpack_report.json
```

`--gpu-rebuild` falls back to CPU when the file is not SPC3 v0.2 typed level
`3`, the predictor is unavailable, CUDA/NVRTC cannot load, or GPU validation
fails.

Run the experimental rANS/FSE typed-stream gate like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-streaming --bench-typed-level3 --bench-rans-fse --bench-levels 3 --bench-codecs zstd-9,lzma2-9 --root Phase3SpindaBlocks --bench-limits 20,64,1024 --report Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_report.json
```

Pack with a non-default codec like this:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --root Phase3SpindaBlocks --limit-zips 1 --level 3 --codec zstd --codec-level 3 --output Phase3SpindaBlocks\sample-zstd.spc3 --report Phase3SpindaBlocks\_spc3_pack_zstd_report.json
```

Level `3` embeds the predictor by default so a `.spc3` file is self-contained.
For small packs where predictor overhead dominates, add `--external-predictor`.
The resulting file is smaller, but `verify` and `unpack` must be given the same
predictor JSON path.

`unpack` writes one `0xLLLL.spinda80.zip` file per lane by default. The ZIP
entries are stored, not deflated, because SPC3 already did the compression work.
The entry payloads are encrypted 80-byte PK3 records named by full PID, so the
result can replace or compare against Phase 3 lane ZIPs. `--unpack-format raw`
writes one `0xLLLL.pk3raw` file per lane instead, as a simple upper-half-ordered
concatenation of those same encrypted records.

Current SPC3 levels:

| Level | Stored Model | Default Codec | Rebuild Rule |
| ---: | --- | --- | --- |
| `0` | Raw ordered encrypted PK3 payload | None | Copy ordered payload back out. |
| `1` | Full ordered decrypted PK3 payload | zlib level 9 | Encrypt each decrypted record. |
| `2` | One decrypted template plus IV32 stream | zlib level 9 | Rebuild PID, IV32, checksum, then encrypt. |
| `3` | One decrypted template plus predictor exception bitmap/XOR stream | zlib level 9, embedded predictor table by default | Rebuild IV32 from predictor plus exceptions, then encrypt. |

SPC3 v0.2 typed level `3` is enabled explicitly with `--typed-level3`. It
writes format version `2`, keeps the v0.1 80-byte header and 96-byte lane
table, sets lane `stream_kind` to `typed_level3`, and stores a 3-entry
substream table at the start of each lane stream:

| Substream | Contents | Default explicit codec |
| --- | --- | --- |
| `template` | 80-byte decrypted template | chosen codec/profile; `auto` = zlib-9, `fast` = zstd-9 |
| `exception_bitmap` | 8192-byte predictor miss bitmap | chosen codec/profile; `auto` = zlib-9, `fast` = zstd-9 |
| `xor_values` | u32 XOR values for bitmap hits | chosen codec/profile; `auto` = zlib-9, `fast` = zstd-9 |

Levels `1..3` can also use native zstd or LZMA2 when explicitly requested.
`--codec-profile fast` maps to zstd-9 and is the current preferred v0.2 typed
candidate. `--codec-profile compat` maps to zlib-9 and matches `auto`.
`--codec-profile small` maps to LZMA2-9, a size oracle converted to a native
backend, but it should not become default unless its smaller output beats level
`3` by enough to justify slower decode and a larger dependency. rANS/FSE now
works as an experimental byte-level typed substream codec for the bitmap/XOR
exception streams, but the 1024-lane gate keeps it non-default.

The container stores source ZIP size, ZIP CRC32, ZIP FNV-1a64, original payload
CRC32, rebuilt payload CRC32, per-lane codec ID/settings, stream sizes, and
predictor counters in the lane table. `pack` refuses lanes that fail validation,
rebuilds from the just-written stream in RAM, and errors if rebuilt bytes differ
from the source ordered PK3 payload.

`verify` is intentionally strict. By default it rebuilds every lane from
`.spc3`, checks internal CRCs, reloads the source lane ZIPs, and compares every
80-byte PK3 record byte-for-byte. Use `--no-source-compare` only when the source
ZIPs are unavailable and internal container integrity is enough for the task.

## Report Fields

The JSON report is the artifact worth keeping. The useful top-level fields are:

| Field | Meaning |
| --- | --- |
| `ok` | `true` only when every audited lane passes structural and semantic checks. |
| `exit_code` | Process status reflected in the report. Structural/model failures use `2`. |
| `hotloop_backend` | Active low-level backend, usually `x86_64_asm` on this Windows build. |
| `lanes_processed` | Count of lane ZIPs opened and audited. |
| `records_processed` | Count of PK3 entries processed after structural checks. |
| `totals` | Cross-lane counters for checksum, template, predictor, and rebuild results. |
| `timings_ms` | Stage totals used to decide optimization order. |
| `size_estimates` | Current in-memory compression estimates. These are not final `.spc3` sizes. |
| `lanes` | Per-lane counters, timings, errors, and compression estimates. |

Additional mode reports:

| Report | Schema | Meaning |
| --- | --- | --- |
| Pack | `spc3_pack_report.v1` | Output size, level, source ZIP hashes, payload hashes, stream sizes, predictor counts, and pack round-trip status. |
| Unpack | `spc3_unpack_report.v1` | Output format, selected lanes, output files, rebuilt payload CRCs, output CRCs, and internal CRC mismatch count. |
| Verify | `spc3_verify_report.v1` | Internal hash checks plus source ZIP byte-compare mismatch count. |
| Inspect | `spc3_inspect_report.v1` | Header offsets, table entries, predictor status, stream sizes, hashes, and ratios without unpacking. |
| Consolidate | `spc3_consolidate_report.v1` | Input shard list, compressed-copy mode, merged lane count, output size, and copied lane stream metadata. |
| Bench | `spc3_compression_oracle.v1` | Current ZIP size, raw payload size, SPC3 level `0..3` sizes, native zlib/zstd/LZMA2 matrix when requested, pack/unpack/verify timings, decode MiB/s, and external LZMA2/zstd model comparisons. |
| Streaming bench | `spc3_streaming_compression_oracle.v1` | Same size/speed focus as bench, but one lane at a time and no full-container byte buffer. Includes `typed_level3_matrix` when `--bench-typed-level3` is enabled, `exceptions-rans` rows when `--bench-rans-fse` is enabled, and `gpu_offload` when `--bench-gpu` is enabled. |
| GPU rebuild | `gpu_rebuild` object in unpack/verify reports | `--gpu-rebuild` status, fallback reason, device, transfer/kernel/download timings, download mode, CUDA runtime cache state, host CRC timing, and mismatch counters. |
| CPU decode profile | `cpu_decode_profile` object in unpack/verify reports | CPU fallback/not-requested timings split into stream decode, IV expansion, rebuild/encrypt, CRC, total time, CRC backend, and CRC byte count. |
| ASM recommendation | `asm_recommendation` object in unpack/verify reports | Records `targeted_asm_unpaused_profile_guided`, the implemented PK3 shuffle ASM target, the current largest CPU slice, and the next low-level action. |

Small report helper:

```powershell
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py summary Phase3SpindaBlocks\_spc3_streaming_bench_1024_report.json --format markdown --output Phase3SpindaBlocks\_spc3_streaming_bench_1024_summary.md
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py summary Phase3SpindaBlocks\_spc3_streaming_bench_1024_report.json --format csv --output Phase3SpindaBlocks\_spc3_streaming_bench_1024_summary.csv
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py compare Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_verify_report.json Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_verify_report.json --format markdown --output Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_verify_cpu_gpu_compare.md
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py summary Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_report.json --format csv --output Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_fields.csv
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py summary Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_report.json --format csv --table lanes --output Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_lanes.csv
.\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\spc3_report_tools.py release-summary --pack Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_report.json --cpu-verify Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_verify_report.json --gpu-verify Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_verify_report.json --cpu-unpack Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_unpack_report.json --gpu-unpack Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_unpack_report.json --gpu-cache Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_cache_report.json --output Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_release_summary.md
```

The helper handles benchmark/oracle tables, the original lane-audit report,
and real pack, inspect, verify, and unpack reports. Non-benchmark summaries
show top-level evidence fields such as codec profile, mismatch counters, GPU
fallback status, GPU timings, and CPU decode-profile slices. Lane-audit
summaries show hotloop/config/totals/timings plus per-lane audit failure,
entry count, mismatch counters, total time, and first error. Non-benchmark
compares use the same evidence fields so CPU vs GPU verify/unpack reports do
not collapse to an empty table. The native GUI uses the same high-signal field
set for its built-in summary and compare view. CSV summaries use benchmark rows
for benchmark reports and evidence fields for real reports by default; pass `--table lanes`
when you need the per-lane stream or audit table instead. Markdown table cells
escape pipes and fold embedded newlines, and CSV output uses stable LF line
endings so generated evidence diffs cleanly.

Rule of thumb: if `ok=false`, read the first failing lane's `errors` list before
you look at sizes or timings. Structural failures stop later modeling for that
lane on purpose; otherwise a broken ZIP would leak garbage into the compression
numbers.

## Compression Theory In Current Prototype

A future `.spc3` file should not store every encrypted PK3 as a separate ZIP
entry. The prototype is checking which parts can be rebuilt from smaller
streams:

- PID lower half comes from the lane filename.
- PID upper half comes from record order.
- Gen 3 encrypted bytes can be regenerated if the decrypted template, PID/OTID,
  checksum, block order, and modeled variable fields are known.
- IV32 is often predicted by the current PID-upper-half table.
- Predictor misses can be stored as a compact exception bitmap plus XOR values.
- ZIP central-directory overhead should disappear in a native container.

That is why this program cares about byte-for-byte rebuilds as much as size
estimates. If the reader can rebuild something deterministically, the writer
should not spend bytes storing it.

## Safety Boundaries

The prototype is cautious because bad compression evidence is worse than no
evidence:

- Audit and bench read production lane ZIPs and write reports only. Pack writes
  the selected `.spc3`; unpack writes lane ZIPs by default or ordered `.pk3raw`
  payloads when `--unpack-format raw` is selected.
- Consolidate reads existing `.spc3` shards and writes one `.spc3` plus one
  report. It does not unpack lane payloads and does not recompress lane streams.
- It rejects multi-disk ZIPs, data descriptors, inconsistent local/central
  headers, invalid ZIP64 metadata, CRC mismatches, duplicate entries, bad entry
  names, short lanes, content PID mismatches, bad PK3 checksums, and template
  drift.
- It rejects malformed `.spc3` headers, bad table offsets, truncated data
  sections, wrong embedded predictor sizes, level mismatches, trailing bytes,
  and internal CRC mismatches.
- It performs file-size checks before allocating ZIP buffers.
- It keeps all PK3 reconstruction in RAM.
- It returns non-zero on audited-lane failures so automation cannot quietly
  treat a corrupt sample as useful data.

## Optimization Guidance

Do not optimize this blind. Use the report timings:

| If report shows... | Then do this |
| --- | --- |
| ZIP inflate dominates | Finish native `.spc3` container before GPU work. |
| Entropy probe dominates | Compare zlib levels, zstd, LZMA2, or custom stream packing on CPU first. |
| Decrypt/model dominates | Expand assembly/SIMD over larger batches. |
| Rebuild dominates | Consider batch rebuild kernels or vectorized block shuffle/checksum. |
| Transfer/setup overhead dominates in GPU prototype | Cache the CUDA runtime for long-running paths, use bulk downloads for small/medium output, and keep GPU optional until end-to-end reports win. |
| CRC dominates CPU typed decode | Reduce duplicate CRC work, batch it better, or test a proven CRC32 backend before adding more PK3 rebuild/encrypt assembly. |

GPU is still on the table, but only after the final container removes the
per-entry ZIP overhead. Right now the evidence says GPU rebuild is useful for
large typed v0.2 batches, while CPU fallback work should start with CRC policy
rather than more PK3-specific assembly.

## File Handling

The tool avoids loose PK3 writes. For each lane it keeps these in RAM:

- full ZIP file bytes
- inflated encrypted PK3 buffer
- one decrypted PK3 scratch record
- one canonical decrypted PK3 template record
- IV32 stream
- predictor exception bitmap and XOR values
- one rebuilt encrypted PK3 scratch record for byte comparison

Only the JSON report is written.

## Verified Sample

`Observed once`: The 2026-05-07 20-lane run processed lanes `0x0001..0x0014`:

- records processed: `1,310,720`
- checksum failures: `0`
- content PID mismatches: `0`
- template mismatches: `0`
- predictor round-trip mismatches: `0`
- encrypted PK3 rebuild mismatches: `0`
- predictor matches: `1,216,780`
- predictor exceptions: `93,940`

Measured hotspot totals:

| Stage | Time ms |
| --- | ---: |
| ZIP inflate + CRC32 | `1218.856` |
| zlib entropy probe | `819.984` |
| ZIP central parse | `141.993` |
| ZIP read | `97.941` |
| decrypt/model | `50.933` |
| rebuild/encrypt/compare | `47.195` |

`Observed once`: The expanded `.spc3` compression oracle run on 2026-05-07
processed lane samples from `0x0001..0x0040` with `--bench-external` and wrote
`Phase3SpindaBlocks\_spc3_bench_report.json`. GPU codecs were not run. External
7z/zstd numbers are oracle inputs, not container files, so the
template-exception rows do not include `.spc3` headers, lane tables, or an
embedded predictor.

| Lanes | Current ZIP bytes | 7z/LZMA2 bytes | zstd bytes | SPC3 level 0 | SPC3 level 1 | SPC3 level 2 | SPC3 level 3 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `1` | `11,415,137` | `749,956` | `926,554` | `5,243,056` | `726,183` | `228,801` | `228,836` |
| `4` | `45,701,445` | `2,301,530` | `4,577,290` | `20,971,984` | `2,904,478` | `914,949` | `245,877` |
| `20` | `228,261,594` | `10,235,488` | `21,303,292` | `104,859,600` | `14,522,414` | `4,574,830` | `491,722` |
| `64` | `730,288,857` | `33,172,326` | `65,900,696` | `335,550,544` | `46,469,036` | `14,639,132` | `1,085,803` |

Decode-speed and model-oracle highlights from the same report:

| Lanes | SPC3 level 3 unpack ms | SPC3 level 3 decode MiB/s | Template-exception 7z bytes | Template-exception zstd bytes |
| ---: | ---: | ---: | ---: | ---: |
| `1` | `7.045` | `709.764` | `237` | `88` |
| `4` | `41.582` | `480.982` | `12,627` | `13,790` |
| `20` | `185.049` | `540.398` | `74,953` | `88,795` |
| `64` | `554.815` | `576.769` | `193,448` | `229,884` |

Interpretation: SPC3 level `3` is still the best self-contained container result
because it stores hashes, per-lane metadata, and the predictor. The external
template-exception oracle shows the exception stream itself is tiny, which is a
good reason to test a specialized exception codec later.

The same pass packed `0x0001` at level `3` into
`Phase3SpindaBlocks\_spc3_sample_level3.spc3` (`228,836` bytes), verified it
against the source lane ZIP with zero mismatches, and unpacked it to
`Phase3SpindaBlocks\_spc3_unpacked_sample\0x0001.pk3raw` with zero internal CRC
mismatches. Current unpack defaults to `.spinda80.zip`; use
`--unpack-format raw` to reproduce that older raw-output evidence shape.

The external-predictor level `3` sample for lane `0x0001` produced
`Phase3SpindaBlocks\_spc3_sample_level3_external_predictor.spc3` (`289` bytes).
It inspected cleanly and verified against the source ZIP when given the
predictor JSON.

`Observed once`: The native codec bench on 2026-05-07 processed lanes
`0x0001..0x0040` with `--bench-native-codecs` and wrote
`Phase3SpindaBlocks\_spc3_native_codec_bench_report.json`. For level `3`, the
64-lane result was:

| Codec | Size bytes | Unpack ms | Decode MiB/s |
| --- | ---: | ---: | ---: |
| zlib-9 | `1,085,803` | `849.938` | `376.498` |
| zstd-3 | `1,084,877` | `772.018` | `414.498` |
| zstd-9 | `1,069,893` | `600.342` | `533.030` |
| zstd-19 | `1,034,563` | `641.533` | `498.805` |
| LZMA2-9 | `985,995` | `674.799` | `474.215` |

Interpretation: zstd is the best decode-speed candidate so far. LZMA2 is
smaller, but the slower decode means it should stay non-default unless a larger
corpus says size matters more. rANS/FSE should target the level-3 exception
pieces only after the stream is split in a future format version.

rANS/FSE has a specific risk: per-lane decoder tables and cache churn can erase
small-stream byte wins. If exception counts stay low, table setup can cost more
than the saved bytes. The 1024-lane streaming bench is the gate before any
rANS/FSE implementation decision.

`Observed once`: The default zlib SPC3 bench on 2026-05-07 processed through
256 lanes and wrote `Phase3SpindaBlocks\_spc3_default_bench_256_report.json`.

| Lanes | Current ZIP bytes | SPC3 level 0 | SPC3 level 1 | SPC3 level 2 | SPC3 level 3 | L3 unpack ms | L3 MiB/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `1` | `11,415,137` | `5,243,056` | `726,183` | `228,801` | `228,836` | `9.0` | `558.0` |
| `4` | `45,701,445` | `20,971,984` | `2,904,478` | `914,949` | `245,877` | `36.6` | `546.5` |
| `20` | `228,261,594` | `104,859,600` | `14,522,414` | `4,574,830` | `491,722` | `176.5` | `566.5` |
| `64` | `730,288,857` | `335,550,544` | `46,469,036` | `14,639,132` | `1,085,803` | `567.8` | `563.6` |
| `256` | `2,926,925,218` | `1,342,201,936` | `185,872,909` | `58,558,089` | `4,424,817` | `2385.3` | `536.6` |

The corpus has more than 1024 lanes, but the current RAM-only bench duplicates
full lane streams while building containers. A 1024-lane default bench should be
run only after a streaming or lower-copy bench path is added, or on a machine
where a 15+ GiB transient working set is acceptable.

That lower-copy path now exists as `--bench-streaming`. Use it for 1024-lane
default bench first, then decide whether a full native codec matrix is worth
the longer runtime.

`Observed once`: The streaming bench completed on 2026-05-07 through 1024
lanes and wrote `Phase3SpindaBlocks\_spc3_streaming_bench_1024_report.json`.
All decode CRC mismatch counters were zero, and the redirected stderr log was
empty. The process working set stayed near 23 MiB during polling, confirming the
lower-copy bench path.

| Lanes | Current ZIP bytes | SPC3 level 0 | SPC3 level 1 | SPC3 level 2 | SPC3 level 3 | L3 unpack ms | L3 MiB/s |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `1` | `11,415,137` | `5,243,056` | `726,183` | `228,801` | `228,836` | `7.2` | `693.8` |
| `4` | `45,701,445` | `20,971,984` | `2,904,478` | `914,949` | `245,877` | `19.3` | `1033.7` |
| `20` | `228,261,594` | `104,859,600` | `14,522,414` | `4,574,830` | `491,722` | `94.7` | `1055.5` |
| `64` | `730,288,857` | `335,550,544` | `46,469,036` | `14,639,132` | `1,085,803` | `292.7` | `1093.4` |
| `256` | `2,926,925,218` | `1,342,201,936` | `185,872,909` | `58,558,089` | `4,424,817` | `1159.9` | `1103.5` |
| `1024` | `11,707,706,577` | `5,368,807,504` | `743,480,413` | `234,229,427` | `18,285,512` | `4621.2` | `1107.9` |

Interpretation: level `3` continues to scale well. The 1024-lane SPC3 level `3`
file estimate is about 18.3 MB versus 11.7 GB of current ZIPs, with level `3`
decode staying above 1 GiB/s in the streaming bench.

`Observed once`: The targeted 1024-lane native streaming gate completed with
`--bench-levels 3 --bench-codecs zlib-9,zstd-9` and wrote
`Phase3SpindaBlocks\_spc3_streaming_zstd9_gate_report.json`. It produced only
two native rows per sample, proving the filter works. All native decode CRC
mismatch counters were zero, and stderr was empty.

| Codec | Level | Size bytes | Unpack ms | Decode MiB/s |
| --- | ---: | ---: | ---: | ---: |
| zlib-9 | `3` | `18,285,512` | `4435.803` | `1154.244` |
| zstd-9 | `3` | `17,895,663` | `4287.937` | `1194.047` |

Codec policy from current evidence:

| Role | Codec | Status |
| --- | --- | --- |
| preferred default candidate | zstd-9 | Smaller and faster than zlib-9 at 1024 lanes; use `--codec-profile fast` for v0.2 typed level `3` until any auto-default flip is made on purpose. |
| compatibility / safe | zlib-9 | Current v0.1 `auto` behavior and baseline. |
| smallest archive | LZMA2-9 | Still non-default because decode is slower. |
| experimental | rANS/FSE | Only after typed level-3 streams and table/cache-cost proof. |

1024-lane exception stats from the same report:

| Metric | Value |
| --- | ---: |
| predictor exceptions | `6,691,736` |
| average exceptions per lane | `6,534.898` |
| min / max exceptions per lane | `0` / `17,225` |
| bitmap density | `0.100` |
| exception bitmap bytes | `8,388,608` |
| exception XOR value bytes | `26,766,944` |
| XOR zero values | `0` |
| rANS/FSE table-init risk | `lower` |

Interpretation: exception streams are not ultra-sparse at 1024 lanes. rANS/FSE
is still not free, but the table-init risk is lower than feared and deserves a
typed-stream experiment.

`Observed once`: The first typed level-3 streaming oracle completed on
2026-05-07 through 64 lanes and wrote
`Phase3SpindaBlocks\_spc3_typed_level3_bench_report.json`, with summaries at
`Phase3SpindaBlocks\_spc3_typed_level3_bench_summary.md` and
`Phase3SpindaBlocks\_spc3_typed_level3_bench_summary.csv`. All typed decode
CRC mismatch counters were zero.

64-lane level `3` result:

| Model | Size bytes | Unpack ms | Decode MiB/s |
| --- | ---: | ---: | ---: |
| v0.1 fused zlib-9 | `1,085,803` | `315.142` | `1015.415` |
| native fused zstd-9 | `1,069,893` | `309.183` | `1034.984` |
| typed all-zlib-9 | `1,078,999` | `293.710` | `1089.511` |
| typed all-zstd-9 | `1,061,696` | `287.122` | `1114.509` |
| typed all-LZMA2-9 | `970,171` | `335.705` | `953.219` |
| typed exceptions-LZMA2-9 | `967,103` | `339.827` | `941.654` |

`Observed once`: The 1024-lane typed level-3 gate completed on 2026-05-07 and
wrote `Phase3SpindaBlocks\_spc3_typed_level3_bench_1024_report.json`, with
summaries at `Phase3SpindaBlocks\_spc3_typed_level3_bench_1024_summary.md`
and `Phase3SpindaBlocks\_spc3_typed_level3_bench_1024_summary.csv`. stderr was
empty and all decode CRC mismatch counters were zero.

1024-lane level `3` result:

| Model | Size bytes | Unpack ms | Decode MiB/s |
| --- | ---: | ---: | ---: |
| v0.1 fused zlib-9 | `18,285,512` | `4,518.689` | `1,133.072` |
| fused zstd-9 | `17,895,663` | `4,482.900` | `1,142.118` |
| fused LZMA2-9 | `16,243,279` | `5,431.993` | `942.564` |
| typed all-zlib-9 | `18,135,635` | `4,540.414` | `1,127.651` |
| typed all-zstd-9 | `17,743,980` | `4,415.682` | `1,159.504` |
| typed all-LZMA2-9 | `15,539,511` | `5,508.702` | `929.439` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5,512.759` | `928.755` |

Interpretation: splitting level `3` is now measured at 1024 lanes. Typed
zstd-9 beats fused zstd-9 on both size and decode speed, so v0.2 typed streams
are worth drafting. LZMA2 remains the smallest result, but slower decode keeps
it in archive-oracle territory. Any exception-stream codec must be judged
against typed zstd-9 and typed LZMA2, not against v0.1 zlib.

`Observed once`: The experimental rANS/FSE typed gate completed on 2026-05-07
through 1024 lanes and wrote
`Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_report.json`, with
summaries at
`Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_summary.md` and
`.csv`. All decode CRC mismatch counters were zero.

1024-lane rANS/FSE gate result:

| Model | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| typed all-zstd-9 | `17,743,980` | `4,691.347` | `1,091.371` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5,808.576` | `881.455` |
| typed exceptions-rANS | `17,711,423` | `4,926.919` | `1,039.189` |

Interpretation: byte-rANS works and byte-matches, but it is only `32,557`
bytes smaller than typed all-zstd-9 at 1024 lanes, about `0.18%`, while
decoding slower. Keep rANS/FSE experimental; do not make it default.

## Audit Fixes From 2026-05-07

`Proven`: The audit pass fixed these issues:

- Reused one raw-deflate zlib stream per lane instead of calling
  `inflateInit2` / `inflateEnd` for every 80-byte ZIP entry.
- Added an identity-shuffle fast path for Gen 3 PK3 block selector `0`.
- Made malformed lanes return process exit code `2` after writing the JSON
  report.
- Stopped decrypt/model/rebuild when structural ZIP checks fail, preventing
  zero-filled missing records from polluting compression stats.
- Added `--self-test` for parser and PK3 crypto coverage.
- Added `test_spc3_prototype.py`, which creates a full synthetic ZIP64 lane and
  an intentionally incomplete lane in a temporary directory.
- Hardened ZIP64 extra parsing so fields are decoded by the ZIP64-required
  flags instead of fragile positional guesses.
- Cross-checks central-directory names against local-header names before
  trusting compressed payload offsets.
- Rejects deflate streams that finish before consuming all declared compressed
  bytes.
- Uses one 80-byte rebuilt-record scratch buffer instead of allocating a full
  rebuilt lane buffer.
- Rejects signed or malformed `--limit-zips` values instead of relying on
  unsigned conversion behavior.
- Converts file sizes through an explicit checked helper before allocating ZIP
  byte buffers.
- Compares constant decrypted-template regions with three `memcmp` spans
  instead of a branch-heavy byte loop.
- Regression tests now cover predictor exceptions, complete stored ZIP lanes,
  duplicate central entries, local-header name tampering, trailing deflate
  bytes, and malformed numeric CLI values.
- Fourth audit pass added payload CRC32 verification, local-vs-central method,
  flag, CRC, and size checks, corrupt ZIP64 locator coverage, and a central
  entry-count ceiling. The 20-lane timing is higher because `inflate_ms` now
  includes CRC32 over every 80-byte PK3 payload.
- Fifth audit pass added `spc3_hotloops_x86_64.S` for Gen 3 XOR, checksum, and
  decrypted-template comparisons. The JSON report now records
  `hotloop_backend`, and regression coverage asserts the Windows x86-64 build
  is using `x86_64_asm`.
- Sixth audit pass tightened ZIP trust boundaries: EOCD and ZIP64 metadata must
  be single-volume, ZIP data descriptors are rejected, local-header flags and
  sizes must match central-directory metadata, and declared ZIP64 EOCD record
  size must fit inside the file. It also fixed report accounting so
  no-predictor runs do not claim predictor exception bytes, failed structural
  lanes do not claim IV32 streams, and entropy probes compress the full
  exception layer (`bitmap + XOR values`). Regression tests cover each case.
- Seventh audit pass bounded predictor JSON parsing to the predictor array,
  requires the expected key-colon-array shape, rejects extra/short/bad-hex
  predictor tables, and requires the ZIP64 EOCD locator to be adjacent to the
  ZIP64 EOCD record. It also added full-lane tests for content PID mismatches
  and bad decrypted PK3 checksums with valid ZIP CRCs. The exception entropy
  probe now streams `bitmap` and XOR values through one zlib session instead of
  allocating a temporary concatenated buffer.
- Eighth audit pass hardened EOCD comment matching to use subtraction instead
  of additive tail arithmetic, and added regression coverage for non-empty EOCD
  comments, central-directory trailing bytes, and short ZIP64 extra fields.
- Ninth audit pass changed rebuild proof to use one canonical lane template
  instead of copying per-record decrypted constant bytes, so template drift now
  also appears as a rebuild mismatch. It removes the full decrypted lane buffer
  from the RAM model and adds full-lane template-drift coverage. The same pass
  validates central and local ZIP extra-field subfield lengths even when ZIP64
  values are not needed, and accepts valid local-header ZIP64 size placeholders
  by decoding their ZIP64 extra field before comparing against central
  metadata.
- Tenth audit pass added malformed `.spc3` regression tests, `inspect` mode,
  external-predictor level `3` packing, stricter SPC3 offset/trailing-byte
  validation, decode-speed reporting, and external oracle inputs for encrypted,
  decrypted, template+IV32, and template+exception streams.
- Eleventh audit pass froze SPC3 v0.1 as the current 80-byte header plus
  96-byte lane-table layout, added explicit per-entry codec metadata, kept
  zlib-9 as the default, added native zstd and LZMA2/XZ backends, expanded the
  bench report with a native codec matrix, and added malformed codec/fuzz
  regression cases. Typed level-3 substreams and rANS/FSE remain experimental
  because enabling them would require a versioned stream-layout change.
- Twelfth audit pass added `--bench-streaming`, a lower-copy oracle path that
  processes one lane model at a time, verifies every decoded lane immediately,
  and computes exact v0.1 container sizes without materializing giant `.spc3`
  byte buffers. Regression coverage checks streaming size math against the
  full-container bench on a synthetic lane.
- Thirteenth audit pass added `--bench-typed-level3`, an experimental v0.2
  oracle that compresses level-3 template, exception bitmap, and XOR exception
  values as separate substreams, then decodes and rebuilds every lane for proof.
  Regression coverage checks typed report shape and raw substream size on a
  synthetic lane.
- Fourteenth audit pass made SPC3 v0.2 typed level `3` a real pack/inspect/
  verify/unpack format behind `--typed-level3`, added x86-64 assembly for full
  IV32 predictor+bitmap+XOR expansion, and added `--bench-gpu`, which
  dynamically loads CUDA driver/NVRTC, compiles a typed level-3 rebuild kernel,
  rebuilds encrypted PK3 payloads on the GPU, and byte-compares the result
  against the CPU reference. Regression coverage now checks v0.2 typed files,
  malformed typed substream tables, ASM IV expansion, and CUDA offload on
  machines with an NVIDIA GPU.
- Fifteenth audit pass promoted CUDA/NVRTC typed rebuild from bench proof into
  real `unpack`/`verify` behind `--gpu-rebuild`/`--gpu`, with CPU fallback for
  unsupported files or CUDA failures. It also added an experimental byte-rANS
  typed-stream codec, `--bench-rans-fse`, rANS pack/verify regression coverage,
  and 20/64/1024-lane gates for GPU scale and rANS/FSE codec policy.
- Sixteenth audit pass hardened CUDA failure cleanup so device buffers are
  freed on mid-run failures, made NVRTC program cleanup exception-safe, added
  overflow-checked SPC3 container size arithmetic, lowered the generated PTX
  target to a broader `compute_52`, and made output/report writers create
  missing parent directories. Regression coverage now includes nested
  output/report paths.
- Seventeenth audit pass tightened CLI numeric parsing coverage for empty
  values and malformed bench-limit lists, added explicit fallback reasons to
  direct GPU bench skipped/disabled paths, and added checked host-staging size
  arithmetic for GPU template, bitmap, prefix, value, and output buffers.
- Eighteenth audit pass preserved detailed GPU skip/failure reasons when real
  `verify`/`unpack --gpu-rebuild` falls back to CPU, moved CRC32 chunking into
  the lower-level byte helper so all callers are size-safe, and made external
  7z/zstd benchmark temp directories unique with RAII cleanup.
- Nineteenth audit pass made `spc3_report_tools.py` tolerate partial benchmark
  reports with missing numeric fields in markdown summaries and report
  compares, added regression coverage for that failure shape, and made GUI
  cancel requests visible while disabling repeat cancel clicks until the CLI
  exits.
- Twentieth audit pass extended report-tool coercion to GPU offload rows before
  derived MiB/s math, made report compare ordering tolerant of mixed JSON value
  types, and rejected bench-only, pack-codec-only, external-predictor, and
  source-compare flags when they are supplied with modes that ignore them.
- Twenty-first audit pass made `spc3_report_tools.py` summarize and compare
  real pack/inspect/verify/unpack reports, including mismatch counters, codec
  profile, GPU fallback status, GPU timings, and CPU decode-profile slices.
  This closes the empty CPU-vs-GPU compare output that could hide release-gate
  evidence outside benchmark/oracle schemas.
- Twenty-second audit pass made real-report CSV summaries export release
  evidence fields by default and added `summary --table lanes` for the detailed
  per-lane table. This prevents `.spc3` size, codec profile, timings, and
  mismatch counters from disappearing when operators request CSV evidence.
- Twenty-third audit pass aligned the minimal GUI report summary/compare view
  with the CLI evidence fields for pack round-trip mismatches, source-compare
  state, GPU requested/upload timings, and CPU lane counts. It also made report
  helper CSV output use stable LF line endings for cleaner evidence diffs.
- Twenty-fourth audit pass hardened markdown evidence tables so paths, fallback
  reasons, and other report values containing `|` or embedded newlines cannot
  split table columns or hide the actual evidence.
- Twenty-fifth audit pass taught `spc3_report_tools.py` the original
  `spc3_phase3_cpu_prototype_report.v1` lane-audit schema, replacing blank
  inspect-style lane rows with audit failure, entry count, mismatch counters,
  timing, and first-error evidence.
- Twenty-sixth audit pass cached CUDA initialization failures per process,
  reported CUDA cache/download/host-CRC metadata, switched small/medium GPU
  rebuild output downloads to one bulk copy, exposed CPU CRC backend/byte
  counts, and added regression coverage for cache reuse, fallback clarity,
  report summaries, and GUI report comparisons.
- Twenty-seventh audit pass hardened the persistent `--server` worker by
  resetting standard stream formatting around each command, preventing one
  long-running command from leaking `fixed`, precision, fill, or base flags
  into the next GUI run. It also split native GUI run state from worker-command
  state so early cancel requests are honored during startup, and aligned the
  GUI report summary with path/config/GPU output fields from the report helper.

## GPU / Assembly Decision

`Proven`: Current Windows x86-64 build uses assembly hot loops for Gen 3 XOR,
checksum, decrypted-template comparisons, and IV32 exception expansion.

`Observed once`: CUDA/NVRTC GPU offload rebuilt 4 real lanes (`20,971,520`
encrypted output bytes, `4,954` XOR exception values) on an NVIDIA GeForce RTX
4070 Ti with `mismatched_lanes=0` and `mismatched_bytes=0` in
`Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`.

`Observed once`: Real typed v0.2 `verify --gpu-rebuild` and
`unpack --gpu-rebuild` both rebuilt the 4-lane sample on the RTX 4070 Ti with
`0` mismatched lanes/bytes. A fused v0.1 zstd file requested with
`--gpu-rebuild` correctly fell back to CPU with reason `SPC3 file is not v0.2
level 3`.

`Observed once`: The CUDA cache smoke
`Phase3SpindaBlocks\_spc3_gpu_cache_smoke_1_4_report.json` ran `--bench-gpu`
for 1 then 4 lanes in one process. The first sample compiled in `173.090` ms;
the second reported `compile_ms=0.000`, proving the context/module cache is
reused inside long-running process paths.

`Observed once`: The latest audit cache smoke
`Phase3SpindaBlocks\_spc3_gpu_cache_audit5_1_4_report.json` repeated the same
1/4-lane gate after report/GUI hardening. The first sample compiled in
`185.464` ms; the second reported `compile_ms=0.000`, with `0` mismatched
lanes.

`Observed once`: The latest audit 20-lane typed v0.2 GPU verify report
`Phase3SpindaBlocks\_spc3_typed_v2_profile_fast_real20_gpu_verify_audit5_report.json`
reported `ok=true`, GPU status `ok`, GPU used `true`, and `0` internal,
source, lane, and byte mismatches.

`Observed once`: The disabled-GPU bench audit report
`Phase3SpindaBlocks\_spc3_gpu_disabled_audit_report.json` reported status
`cuda_disabled_by_environment`, used `false`, and fallback reason
`SPC3_DISABLE_CUDA is set`, proving direct bench reports no longer leave
disabled fallback reasons blank.

`Verified in regression`: Real typed v0.2 `verify --gpu-rebuild` fallback now
preserves the detailed environment reason for disabled and forced GPU failure
paths instead of replacing it with only the short GPU status string.

`Observed once`: The scaled GPU bench wrote
`Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`. At 1024 lanes,
the GPU rebuilt `5,368,709,120` output bytes with `6,691,736` XOR values and
`0` mismatches. Upload+kernel+download was `1,518.793` ms, or `1,714.546` ms
including one-time compile, versus `4,381.556` ms CPU level-3 unpack. The
bench-only `compare_ms=5,282.588` is CPU validation rebuild time, not
production GPU decode cost.

GPU decision from current evidence:

1. Keep `--gpu-rebuild` optional and safe-fallback for all files.
2. Use GPU rebuild as a real accelerator candidate for large typed v0.2
   batches; avoid making it mandatory because small batches pay CUDA
   startup/compile overhead.
3. The process now caches the CUDA context/NVRTC module after the first GPU
   rebuild call, so streaming bench, the native GUI worker, and any
   long-running in-process caller can amortize startup while one-shot CLI runs
   remain safe. Failed CUDA probes are also cached for that process, and GPU
   reports expose cache-hit/failure-cache state.
4. GPU rebuild uses one bulk output download for small/medium outputs and keeps
   the per-lane path for very large output buffers.
5. Do not expect assembly to fix GPU transfer cost. Targeted PK3 shuffle ASM is
   active for the CPU path, but CRC is currently larger than PK3
   rebuild/encrypt in the typed decode profile, so deeper assembly work should
   still start from measured report data.
6. Keep high-ratio entropy coding CPU-side unless a GPU codec is benchmarked
   against typed zstd-9/LZMA2 with acceptable size loss.

`Observed once`: The 20-lane v0.2 typed zstd-9 gate
`Phase3SpindaBlocks\_spc3_typed_v2_profile_fast_real20_cpu_verify_report.json`
showed CPU decode profile `stream_decode_ms=3.034`, `iv_expand_ms=5.210`,
`rebuild_encrypt_ms=44.783`, and `crc_ms=85.732`. That means the first
re-enabled assembly target should stay narrow. SPC3 now uses a targeted
`spc3_shuffle48_asm` helper for the PK3 block shuffle, while CRC/hash policy
remains the larger next low-level decision.

`Observed once`: The 64-lane v0.2 typed zstd-9 release gate wrote
`Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current.spc3` and the report
bundle below:

| Step | Report | Evidence |
| --- | --- | --- |
| pack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_report.json` | `ok=true`, size `1,061,696` bytes, source ZIP bytes `730,288,857`, raw payload bytes `335,544,320`, build `702.146` ms |
| CPU verify | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_verify_report.json` | `ok=true`, internal/source/gpu lane/gpu byte mismatches `0/0/0/0`, report `7257.418` ms, CPU decode profile `494.668` ms |
| GPU verify | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_verify_report.json` | `ok=true`, GPU used, fallback reason empty, lane/byte mismatches `0/0`, report `7472.181` ms, GPU rebuild `824.236` ms |
| CPU unpack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_unpack_report.json` | `ok=true`, unpack/GPU lane/GPU byte mismatches `0/0/0`, report `716.307` ms, CPU decode profile `509.434` ms |
| GPU unpack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_unpack_report.json` | `ok=true`, GPU used, fallback reason empty, unpack/lane/byte mismatches `0/0/0`, report `1061.187` ms, GPU rebuild `867.183` ms |
| summary | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_release_summary.md` | one-file release evidence with sizes, timings, CRC profile, GPU fallback/cache/download state, mismatch counters, and report paths |

CPU and GPU unpack output directories each contained 64 `.pk3raw` files
totaling `335,544,320` bytes because that gate used raw output, and per-file
SHA-256 hashes matched. GPU verify
rebuilt `335,544,320` bytes from `307,118` XOR exception values with
`download_mode=bulk`, `compile_ms=186.553`, `kernel_ms=12.182`,
`download_ms=52.431`, and `host_crc_ms=274.563`. GPU unpack reported
`download_mode=bulk`, `compile_ms=208.963`, `kernel_ms=11.918`,
`download_ms=52.575`, and `host_crc_ms=274.915`.

Long-running cache smoke
`Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_cache_report.json`
ran 1 lane then 64 lanes in one process. The first sample had
`runtime_cache_hit=false` and `compile_ms=202.579`; the second had
`runtime_cache_hit=true`, `runtime_initializations=1`, `compile_ms=0.000`,
bulk download, and `0/0` GPU mismatches.

64-lane CPU decode profile:

| Path | stream decode ms | IV expand ms | rebuild/encrypt ms | CRC ms | total ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU verify | `9.119` | `17.699` | `137.223` | `268.717` | `494.668` |
| CPU unpack | `9.357` | `18.017` | `143.118` | `275.299` | `509.434` |

Read: targeted PK3 shuffle ASM is re-enabled, but broad PK3 rebuild/encrypt ASM
is still gated by profile data. CRC remains the larger measured CPU slice. Keep
`zlib_crc32` as the current backend, record `crc_bytes` in reports, and decide
whether to reduce duplicate CRC work, batch it better, or use a proven CRC32
implementation before adding more assembly.

## Roadmap To Final `.spc3`

Planned implementation order after the first hardened container:

1. Treat v0.2 typed level `3` plus `--codec-profile fast` as the active main
   path for new format work, while keeping v0.1 readable for compatibility.
2. Keep `auto` as zlib-9 for compatibility, `fast` as zstd-9, and `small` as
   LZMA2-9 until a release deliberately changes that policy.
3. Keep rANS/FSE experimental. It works on typed exception streams, but the
   1024-lane gate made it only `0.18%` smaller than typed zstd-9 while slower.
4. Store canonical template bytes once per compatible group.
5. Store any remaining non-template variable fields as typed streams if real
   corpus data proves they exist.
6. Re-run hotspot measurement without ZIP per-entry overhead.
7. Use the `cpu_decode_profile` and `asm_recommendation` report timings to
   decide whether deeper ASM should target CRC, PK3 rebuild/encrypt, or another
   measured slice.

## Clean Package Checklist

Before refreshing `github-clean`, confirm:

1. Source files are present: `spc3_prototype.cpp`,
   `spc3_hotloops_x86_64.S`, `build_spc3_prototype.bat`,
   `spc3_report_tools.py`, `test_spc3_prototype.py`, and the GUI wrapper.
2. Mirrored docs/specs are byte-identical where required by
   `MARKDOWN_MIRROR_MANIFEST.md`.
3. License notes cover MPL-2.0 project source, zlib, zstd, liblzma, and
   optional CUDA/NVRTC runtime loading.
4. Generated caches such as `__pycache__` are absent.
5. Generated lane data, `.spc3` artifacts, unpacked `.spinda80.zip` or
   `.pk3raw` outputs, and private reports are excluded unless a release
   intentionally names them.
6. `spc3_prototype.exe` is excluded unless the clean package is intentionally
   shipping binaries with dependency and license files.
7. `tools\check_markdown_mirrors.py` passes from the repository root.

## Operator Checklist

Before using a report as evidence, do this:

1. Build with the assembly source included.
2. Run `--self-test`.
3. Run `test_spc3_prototype.py`.
4. Run a representative lane sample with a report path outside temp storage.
5. Confirm `ok=true`, `hotloop_backend=x86_64_asm`, and all mismatch counters
   are zero.
6. Read `timings_ms` before making any optimization claim.
7. Keep the report with the exact source revision or audit note that produced
   it.
8. For container work, run `pack`, `verify`, and `unpack` on the same sample and
   compare `verify` source mismatches plus `unpack` CRC mismatches against zero.
9. Run `inspect` on any `.spc3` handed to a GUI or benchmark script so bad
   offsets, missing predictors, and ratio surprises show up early.
