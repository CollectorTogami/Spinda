# Spinda CUDA runtime upgrade note

Status: Pending (as of 2026-05-11)

## Current constraint to remember

- `tools/spinda/spc3_prototype/spc3_prototype.cpp` currently loads NVRTC via hard-coded DLL names:
  - `nvcuda.dll`
  - `nvrtc64_120_0.dll`
- This means GPU paths (`--bench-gpu`, `--gpu-rebuild`) only work when that exact NVRTC DLL name is available at runtime (currently from CUDA 12.0-family layout).
- Removing the toolkit/runtime that provides `nvrtc64_120_0.dll` will force Spinda to fail back to CPU mode or fail those startup paths.

## Upgrade plan

1. Audit and make the loader version-tolerant in `spc3_prototype.cpp`.
   - Prefer probing for compatible NVRTC DLLs instead of only `nvrtc64_120_0.dll`.
   - Keep CPU fallback behavior unchanged.
2. Update startup diagnostics to log which DLL was selected and why.
3. Run a verification pass after code changes:
   - `spc3_prototype.exe --self-test`
   - `spc3_prototype.exe --mode bench --bench-gpu ...`
   - `spc3_prototype.exe --mode verify --input <file> --root <dir> --gpu-rebuild`
   - `SPC3_DISABLE_CUDA=1` smoke tests
4. Update docs for the minimum required CUDA runtime and supported versions.
5. Only after successful verification, remove old CUDA runtimes from disk/PATH.

## Why this note exists

- There is now a newer CUDA install present (`v13.2`), but the current code still depends on older NVRTC naming.
- Without this upgrade, deleting `v12.8` is risky if you still want Spinda GPU offload.
