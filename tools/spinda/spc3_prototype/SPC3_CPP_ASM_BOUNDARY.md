# SPC3 C++ And ASM Boundary

## Purpose

This note explains why most of SPC3 is C++ and why only selected hot loops are
written in assembly.

The compressor is not a single inner loop. It is a container format, a verifier,
a codec pipeline, a GPU coordination layer, and an operator tool. Only a small
part of that work is a good fit for hand-written assembly.

## Caveman TLDR

Most code stays C++.

ASM only measured byte-stable hot loops.

Current ASM target active: `spc3_shuffle48_asm`.

CRC bigger than rebuild in latest gate. Decide CRC first.

GPU solves batch rebuild. ASM solves CPU fallback hot loops.

Do not chase ASM percent. Chase correct speed.

## Current Split

The current Windows x86-64 build uses:

- C++ for the SPC3 container, CLI modes, validation, reports, codec integration,
  file handling, GPU orchestration, and the native verifier GUI.
- CUDA/NVRTC for optional typed level-3 rebuild/verify/unpack offload.
- x86-64 assembly for narrow Gen 3 PK3 hot loops where the inputs are stable,
  the output shape is fixed, and tests can compare exact bytes.
- Python for developer-only tests, reports, and non-shipping helper wrappers.

This split is deliberate. The byte format and verifier need readable control
flow. The assembly layer needs small contracts.

The CPU architecture target is Windows x86-64/AMD64. That includes AMD Ryzen,
Threadripper, and EPYC CPUs as well as Intel x86-64 CPUs. The current build
script uses `-march=native`, so a binary built on one machine can pick up CPU
instructions that an older AMD or Intel machine may not support. Use a baseline
x86-64 build when packaging for mixed machines:

```powershell
tools\spinda\spc3_prototype\build_spc3_prototype.bat baseline
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

## Why The Bulk Stays In C++

Most SPC3 code handles decisions, not arithmetic.

Examples:

- parse ZIP and SPC3 headers
- validate lane IDs, offsets, sizes, CRCs, hashes, codec IDs, and flags
- reject malformed streams with useful errors
- choose codec profiles
- call zlib, zstd, and liblzma safely
- manage CUDA loading and fallback reasons
- build JSON reports
- scan and consolidate existing `.spc3` shards
- create parent directories and handle operator file paths
- drive the native GUI process launcher

Assembly would make those parts harder to review without making them faster. It
would also raise the cost of every format change. That matters because SPC3 v0.2
typed level 3 is still the active development path.

## Where ASM Fits

Assembly belongs where all of these are true:

- the code is on a measured CPU hot path
- the operation is byte-stable and easy to compare against C++
- the loop runs over many lanes or large contiguous buffers
- the compiler or library is not already doing the same job well
- the speedup is large enough to justify the maintenance cost

Good candidates look like:

- the current 48-byte PK3 block shuffle helper, `spc3_shuffle48_asm`
- PK3 rebuild/encrypt loops
- IV expansion if CPU fallback remains hot
- XOR exception packing or unpacking if profile data points there
- CRC chunking only if a proven CRC path does not cover it

Poor candidates include:

- report generation
- parser state machines
- CLI argument handling
- file discovery
- codec container glue
- GPU setup and fallback logic
- GUI controls

## Current Profiling Read

The latest larger typed v0.2 level-3 gate showed that CRC time is larger than
PK3 rebuild/encrypt time on CPU fallback:

| Path | CRC ms | rebuild/encrypt ms |
| --- | ---: | ---: |
| verify | `268.717` | `137.223` |
| unpack | `275.299` | `143.118` |

SPC3 now has one deliberately small PK3 assembly target:
`spc3_shuffle48_asm` performs the fixed 48-byte block shuffle used by PK3
rebuild/encrypt. That unpauses ASM without moving parser, codec, report, or GPU
policy code out of C++.

CRC is still the larger measured slice. Before adding broader PK3
rebuild/encrypt assembly, decide the CRC strategy:

- remove duplicate CRC work where possible
- batch CRC work better
- check whether the current zlib CRC path is already the right backend
- test a hardware/intrinsics CRC path only if it preserves the required CRC32
  semantics

After that, profile again. If rebuild/encrypt is still a large CPU slice,
extend the existing targeted PK3 assembly boundary there.

## Why Not Rewrite Codecs In ASM

SPC3 already delegates entropy work to mature libraries:

- zlib for compatibility
- zstd for the recommended fast profile
- liblzma for the small profile

Rewriting entropy codecs in assembly is not a realistic path for this project.
The hard part is not one loop. It is format design, modeling, tables, tuning,
edge cases, and long-term verification. rANS/FSE experiments stay experimental
until a grouped-lane or table-reuse design beats zstd-9 on a real gate.

## GPU Versus ASM

GPU and ASM solve different problems.

GPU offload makes sense when many lanes can be rebuilt or verified together and
the runtime can amortize CUDA/NVRTC startup, transfers, and downloads. ASM makes
sense when CPU fallback remains hot after library and algorithm choices are
settled.

For one-shot CLI runs, GPU startup can dominate small batches. For streaming
bench, the native GUI hidden `--server` worker, and other long-running callers,
cached CUDA context/module state can make GPU offload more attractive. Assembly
does not fix GPU startup, transfer, or download cost.

The only GPU backend today is NVIDIA CUDA through the Windows driver API plus
NVRTC. AMD and Intel GPUs are not offload targets. On an AMD CPU system without
an NVIDIA CUDA GPU, `--gpu-rebuild` should report a CUDA fallback reason and use
CPU rebuild instead.

## Rule For Adding More ASM

Do not add more assembly just because a loop exists. The active exception is the
small PK3 shuffle helper, which has a fixed byte contract and keeps the C++
reference path.

Use this gate:

1. Run a typed v0.2 level-3 pack/verify/unpack profile on a real batch.
2. Confirm the CPU fallback hot section in `cpu_decode_profile` and
   `asm_recommendation`.
3. Confirm that library or C++ changes cannot remove the bottleneck cleanly.
4. Add one assembly routine with a narrow ABI.
5. Keep the C++ reference path.
6. Test malformed streams, fallback paths, and byte-for-byte output.
7. Keep the report fields that prove the new path was used.

## Practical Target Order

Current order:

1. Keep `spc3_shuffle48_asm` as the current targeted PK3 assembly helper.
2. CRC strategy.
3. Re-profile CPU typed decode.
4. Deeper PK3 rebuild/encrypt ASM only if it remains justified.
5. XOR exception packing only if it appears in profile data.
6. No entropy ASM unless the entropy design itself changes and wins on a real
   gate.

The goal is not to maximize assembly percentage. The goal is to keep SPC3 fast,
auditable, and hard to corrupt silently.
