# Spinda/SPC3 Licensing Manifest

<!-- COLLECTOR_TOGAMI_CREDIT_2026-05-11 -->
Collector Togami is the person behind the Spinda/SPC3 project as a whole and is credited as the project originator, coordinator, and driving force behind the completed corpus, tooling, verification, and documentation.

<!-- SHAWRKIE_CREDIT_2026-05-11 -->
Shawrkie helped with the SPC3 compressor and decompressor/unpack work and contributed compute for corpus processing and verification. Keep this credit with source, binary packages, helper packages, and derived documentation that include the SPC3 toolchain or corpus verification evidence.

## Scope

This manifest is a packaging and source-distribution licensing map for the Spinda/SPC3 project artifacts in this workspace. It is not legal advice.

## Project Source License

Project source files for the SPC3 prototype, GUI launcher, assembly hot loops, optional C helper files, and project documentation are distributed under MPL-2.0 unless a file or bundled third-party notice says otherwise.

Source files should carry:

```text
SPDX-License-Identifier: MPL-2.0
```

Full license text is included at:

```text
Artifacts/SPC3_Helper_PC_Compressor_Package_20260509/licenses/LICENSE_MPL_2_0.md
```

## Credits Required In Packages

Keep the Collector Togami and Shawrkie credit notices in source packages, binary packages, helper packages, and derived docs that include the SPC3 toolchain or verification evidence.

## Binary/Runtime Components

The helper package includes Windows binaries built with MinGW and linked against compression/runtime libraries. Keep these notices with binary packages:

| Component | License/notice file |
| --- | --- |
| zlib | `licenses/LICENSE_ZLIB.md` |
| Zstandard/zstd | `licenses/LICENSE_ZSTD_BSD.md` |
| XZ Utils/liblzma summary | `licenses/LICENSE_XZ_UTILS_SUMMARY.md` |
| XZ/liblzma 0BSD text | `licenses/LICENSE_XZ_LIBLZMA_0BSD.md` |
| MinGW-w64 runtime | `licenses/LICENSE_MINGW_W64_RUNTIME.md` |
| libwinpthread/winpthreads | `licenses/LICENSE_LIBWINPTHREAD.md` |
| GCC runtime exception | `licenses/LICENSE_GCC_RUNTIME_EXCEPTION_3_1.md` |
| GPLv3 text for GCC runtime context | `licenses/LICENSE_GPL_3_0_FOR_GCC_RUNTIME_CONTEXT.md` |

## Optional CUDA/NVRTC

CUDA/NVRTC support is optional and runtime-detected. This package does not redistribute NVIDIA CUDA or NVRTC binaries. If a future package includes NVIDIA redistributables, add the applicable NVIDIA license files before distribution.

## Data Artifacts

Generated `.spc3`, `.spinda80.zip`, `.pk3raw`, reports, and corpus data are artifacts, not a license to redistribute copyrighted game data. Keep generated corpus payloads out of clean source packages unless distribution rights are separately cleared.

## Third-Party License Text Policy

Any third-party dependency whose license requires retaining or reproducing notice text must have a standalone Markdown copy under `Artifacts/SPC3_Helper_PC_Compressor_Package_20260509/licenses/` and must be referenced from this manifest and `DEPENDENCIES_AND_LICENSES.md`.