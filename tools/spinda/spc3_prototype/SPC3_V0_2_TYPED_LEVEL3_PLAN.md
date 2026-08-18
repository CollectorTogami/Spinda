# SPC3 v0.2 Typed Level 3 Plan

Status: active main prototype path for new level `3` work; `--typed-level3`
pack/unpack/verify/inspect, `--gpu-rebuild` typed rebuild,
`--bench-typed-level3` oracle, and experimental typed byte-rANS exist
Reason: SPC3 v0.1 level `3` works, but one fused model stream gives every
piece same codec. rANS/FSE needs typed exception streams before fair test.

## Caveman TLDR

Use v0.2 typed level `3` now.

Use `--codec-profile fast`.

`auto` stays zlib-9 compat. `fast` = zstd-9. `small` = LZMA2-9.

GPU optional. CPU verify truth.

ASM active only narrow hot loops now. Bigger ASM waits for CRC/profile proof.

rANS/FSE experiment only.

## Codec Policy Draft

1024-lane filtered native bench now exists. Current policy:

| Role | Codec | Reason |
| --- | --- | --- |
| preferred default candidate | zstd-9 | smaller and faster than zlib-9 on 1024-lane level-3 gate |
| compatibility / safe | zlib-9 | current v0.1 `auto` behavior, stable baseline, low dependency risk |
| smallest archive | LZMA2-9 | smaller, slower decode |
| experimental | rANS/FSE | works on typed bitmap/XOR streams, but 1024-lane gate is slower than zstd-9 for tiny size win |

Do not silently flip v0.1 `auto`. Change default only with an explicit release
note or v0.2 switch.

Current writer policy:

| CLI | Meaning |
| --- | --- |
| `--codec auto` | compatibility mode: zlib-9 for levels `1..3`, including typed v0.2 |
| `--codec-profile compat` | explicit zlib-9 |
| `--codec-profile fast` | recommended v0.2 typed candidate: zstd-9 |
| `--codec-profile small` | LZMA2-9 archive-oracle mode |

No GPU codec default. GPU rebuild offload exists behind `--bench-gpu` and real
typed unpack/verify behind `--gpu-rebuild`; GPU codec work remains a later
probe.

Policy freeze for this prototype stage:

- `auto` stays zlib-9 for compatibility.
- `fast` stays zstd-9 and is the recommended v0.2 typed level `3` candidate.
- `small` stays LZMA2-9 and is size-oriented evidence, not default behavior.
- v0.1 stays readable and compatibility-focused.
- v0.2 typed level `3` is the main path for new format work.

Wrap-up decision for current use:

- Ship and use v0.2 typed level `3` with `--codec-profile fast` as the active
  main path for Phase 3 Spinda PK3 lane work.
- Keep `auto` compatible instead of silently changing existing scripts.
- Keep GPU optional and report-driven; CPU verify remains the reference.
- Keep targeted assembly active, including `spc3_shuffle48_asm`, but do not
  rewrite broader PK3 rebuild/encrypt or entropy code until CRC/profile evidence
  justifies it.
- Treat the native Win32 GUI as the operator surface. The report view is simple
  on purpose.

65,536-lane planning estimate from the measured 1024-lane typed zstd-9 gate:

| Item | 1024 lanes measured | 65,536 lanes linear estimate |
| --- | ---: | ---: |
| source ZIP bytes | `11,707,706,577` | `749,293,220,928` |
| typed zstd-9 SPC3 bytes | `17,743,980` | `1,135,614,720` |
| CPU typed unpack time | `4,415.682 ms` | `282.604 s` |

This is an extrapolation, not a substitute for the full Phase 3 gate.

Current-binary wrap-up smoke:

- summary:
  `Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md`
- run: 4 real lanes, v0.2 typed level `3`, `--codec-profile fast`
- steps: pack, CPU verify, GPU verify, CPU unpack, GPU unpack, CPU/GPU unpack
  hash compare, repeated GPU cache smoke
- result: all OK, all mismatch counters `0`, CPU/GPU unpack hashes matched
- cache result: second GPU cache sample had `runtime_cache_hit=true` and
  `compile_ms=0.000`

Current typed-oracle evidence:

- report: `Phase3SpindaBlocks\_spc3_typed_level3_bench_report.json`
- 1024-lane report:
  `Phase3SpindaBlocks\_spc3_typed_level3_bench_1024_report.json`
- summaries:
  - `Phase3SpindaBlocks\_spc3_typed_level3_bench_summary.md`
  - `Phase3SpindaBlocks\_spc3_typed_level3_bench_summary.csv`
- 64-lane typed all-zstd-9: `1,061,696` bytes, `287.122` ms unpack,
  `1114.509` MiB/s
- 64-lane fused zstd-9: `1,069,893` bytes, `309.183` ms unpack,
  `1034.984` MiB/s
- 64-lane typed exceptions-LZMA2-9: `967,103` bytes, `339.827` ms unpack,
  `941.654` MiB/s
- 1024-lane typed all-zstd-9: `17,743,980` bytes, `4,415.682` ms unpack,
  `1159.504` MiB/s
- 1024-lane fused zstd-9: `17,895,663` bytes, `4,482.900` ms unpack,
  `1142.118` MiB/s
- 1024-lane typed exceptions-LZMA2-9: `15,490,371` bytes,
  `5,512.759` ms unpack, `928.755` MiB/s

Read: split layout has real evidence now. zstd-9 is still best default
candidate. LZMA2 typed is size oracle, not default.

Current implemented-format evidence:

- typed v2 pack report:
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_pack_report.json`
- typed v2 verify report:
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_verify_report.json`
- typed v2 inspect report:
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_inspect_report.json`
- real 4-lane typed v2 size: `245,784` bytes
- verify: `ok=true`, `internal_crc_mismatches=0`,
  `source_compare_mismatches=0`
- total predictor exceptions in the sample: `4,954`
- GPU offload proof:
  `Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`
- GPU offload result: RTX 4070 Ti, `20,971,520` output bytes,
  `4,954` XOR values, `0.781` ms kernel time, `0` mismatched lanes/bytes
- GPU scale proof:
  `Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`
- 1024-lane GPU scale: `5,368,709,120` output bytes,
  upload+kernel+download `1,518.793` ms, CPU level-3 unpack `4,381.556` ms,
  `0` mismatches
- real GPU verify/unpack reports:
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_verify_report.json` and
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_unpack_report.json`
- rANS/FSE gate:
  `Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_report.json`
- 1024-lane rANS/FSE result: typed rANS `17,711,423` bytes and `4,926.919` ms
  versus typed zstd-9 `17,743,980` bytes and `4,691.347` ms

## 64-Lane Release Gate

Gate run: v0.2 typed level `3`, `--codec-profile fast`, 64 real lane ZIPs.

| Step | Report | Result |
| --- | --- | --- |
| pack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_pack_report.json` | `ok=true`, size `1,061,696` bytes, source ZIP bytes `730,288,857`, raw payload bytes `335,544,320`, build `702.146` ms |
| CPU verify | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_verify_report.json` | `ok=true`, internal/source/GPU lane/GPU byte mismatches `0/0/0/0`, report `7257.418` ms, CPU profile `494.668` ms |
| GPU verify | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_verify_report.json` | `ok=true`, GPU used, fallback reason empty, bulk download, lane/byte mismatches `0/0`, report `7472.181` ms, GPU rebuild `824.236` ms |
| CPU unpack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_cpu_unpack_report.json` | `ok=true`, CRC/GPU lane/GPU byte mismatches `0/0/0`, report `716.307` ms, CPU profile `509.434` ms |
| GPU unpack | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_gpu_unpack_report.json` | `ok=true`, GPU used, fallback reason empty, bulk download, unpack/lane/byte mismatches `0/0/0`, report `1061.187` ms, GPU rebuild `867.183` ms |
| summary | `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_release_summary.md` | one-file release evidence with report paths, mismatch counters, CRC profile, GPU fallback/cache/download state, and timings |

The CPU and GPU unpack directories each contained 64 `.pk3raw` files totaling
`335,544,320` bytes, and per-file SHA-256 hashes matched.

GPU verify rebuilt `335,544,320` bytes from `307,118` XOR exception values.
One-shot GPU verify reported `download_mode=bulk`, compile `186.553` ms,
upload `9.540` ms, kernel `12.182` ms, download `52.431` ms, host CRC
`274.563` ms, and GPU rebuild total `824.236` ms. GPU unpack reported
`download_mode=bulk`, compile `208.963` ms, upload `10.734` ms, kernel
`11.918` ms, download `52.575` ms, host CRC `274.915` ms, and GPU rebuild
total `867.183` ms.

Current reports add `download_mode`, `runtime_cache_hit`,
`runtime_failure_cached`, `runtime_initializations`, and `host_crc_ms` so
one-shot, cached, unavailable-CUDA, and failed-GPU paths are visible. GPU
rebuild downloads small/medium output with one bulk copy and keeps per-lane
downloads for very large output buffers.

CPU typed decode profile from the same gate:

| Path | stream decode ms | IV expand ms | rebuild/encrypt ms | CRC ms | total ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU verify | `9.119` | `17.699` | `137.223` | `268.717` | `494.668` |
| CPU unpack | `9.357` | `18.017` | `143.118` | `275.299` | `509.434` |

Decision: ASM is unpaused only for a narrow PK3 shuffle helper,
`spc3_shuffle48_asm`, whose byte contract is fixed and covered by the existing
round-trip checks. CRC remains the larger CPU slice, and reports now record
`crc_backend=zlib_crc32` plus the CRC byte count. The next low-level pass should
first decide whether CRC work can be reduced, batched, or replaced with a
proven CRC32 implementation before adding broader PK3 rebuild/encrypt assembly.

## v0.1 Limitation

v0.1 level `3` uncompressed model:

```text
80-byte template
8192-byte exception bitmap
u32 XOR exception values
```

Then one codec handles whole blob.

Problem: template, bitmap, and XOR values have different entropy shape. One
codec may be wrong for at least one piece.

## v0.2 Split

Implemented level `3` typed streams:

| Stream | Contents | Likely Codec |
| --- | --- | --- |
| template | one 80-byte decrypted template per lane or template group | none/zstd |
| exception bitmap | 65536-bit predictor miss bitmap | rANS/FSE probe, zstd, or raw |
| XOR exception values | u32 XOR deltas for bitmap hits | rANS/FSE probe, zstd, or raw |
| predictor | embedded predictor table or external predictor reference/hash | zlib/zstd or reference |

Typed readers apply the same level-3 size limits as v0.1: the template is
exactly 80 bytes, the bitmap is exactly 8192 bytes, and the XOR value stream
cannot exceed `65536 * 4` bytes. Impossible raw sizes are rejected during table
parse before decode allocation.

Reader rebuild:

1. Load predictor table or verify external predictor reference.
2. Load template.
3. For each PID upper half, take predictor IV32.
4. If bitmap bit set, XOR with next exception value.
5. Write PID, IV32, checksum.
6. Encrypt PK3.
7. Verify payload CRC.

## Container Impact

This is not hidden inside v0.1. The prototype uses a versioned change.

Current route:

- file version is `2`
- header stays 80 bytes
- lane table stays 96 bytes
- lane `stream_kind` is `typed_level3`
- each lane stream starts with three 32-byte typed substream table entries

Do not reuse v0.1 `stream_kind=3` with silent substream layout change.

## rANS/FSE Gate

rANS/FSE risk real and now measured:

- decoder table init cost per lane
- cache pressure from tables
- tiny exception streams may not amortize setup
- low exception count lanes may be bigger/slower despite better entropy coding

Gate completed:

1. 1024-lane streaming bench done.
2. Per-lane exception count recorded.
3. Bitmap density recorded.
4. XOR value distribution recorded enough for byte codec gate.
5. Completed 1024-lane typed level `3` zstd/LZMA2 baseline used.
6. Byte-rANS implemented and round-tripped.
7. Result did not beat typed zstd-9 enough to freeze as default.

Current 1024-lane exception stats:

| Metric | Value |
| --- | ---: |
| predictor exceptions | `6,691,736` |
| average exceptions per lane | `6,534.898` |
| min / max exceptions per lane | `0` / `17,225` |
| bitmap density | `0.100` |
| exception bitmap bytes | `8,388,608` |
| exception XOR value bytes | `26,766,944` |
| XOR zero values | `0` |
| table-init risk | `lower` |

Measured rANS/FSE result:

| Model | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| typed all-zstd-9 | `17,743,980` | `4,691.347` | `1,091.371` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5,808.576` | `881.455` |
| typed exceptions-rANS | `17,711,423` | `4,926.919` | `1,039.189` |

Current rule: v0.2 typed stream layout is justified. rANS/FSE stays
experimental because it saved only `32,557` bytes versus typed zstd-9 at 1024
lanes and decoded slower. Revisit only if grouped lanes or table reuse changes
the timing.

## Hot Loop Policy

Current assembly/SIMD status:

- Gen 3 XOR hot loop: x86-64 assembly
- PK3 checksum: x86-64 assembly
- decrypted template match: x86-64 assembly
- IV32 predictor+bitmap+XOR expansion: x86-64 assembly
- PK3 48-byte block shuffle: x86-64 assembly

Possible later assembly only after profile says hot:

- CRC/hash policy or acceleration, because the 20-lane and 64-lane typed
  zstd-9 gates measured CRC above rebuild/encrypt. Current reports expose the
  `zlib_crc32` backend and byte count for that decision.
- PK3 rebuild/encrypt, still the next PK3-specific CPU fallback target, only
  after CRC policy is settled.
- exception bitmap creation
- XOR exception packing

Keep parser, container layout, report generation, GPU transfer orchestration,
and file orchestration in C++.

## GUI Policy

The shippable GUI path is the native C++/Win32 verifier wrapper in
`tools/spinda/spc3_gui_native`. It stays behind the CLI and exposes operator
controls:

- pack
- verify
- inspect
- unpack
- consolidate existing pre-compressed `.spc3` shards
- codec/profile choice
- optional GPU rebuild for verify/unpack
- report summary view
- report comparison view for pack/verify/unpack and CPU/GPU reports

No complex GPU tuning controls. The only GPU control is "use GPU when
available"; fallback reasons must remain visible in the report summary.
The GUI launches `spc3_prototype.exe --server` as a hidden persistent worker, so
CUDA context/module cache state survives repeated GUI runs. Canceling a run,
closing the GUI, or changing the compressor path terminates that worker and
drops the cache. Direct CLI runs remain one-shot.
The Python/Tkinter wrapper remains a developer-only convenience and is not the
shipping GUI.

## Clean Package Checklist

Before refreshing `github-clean`:

1. Include source files: `spc3_prototype.cpp`, `spc3_hotloops_x86_64.S`,
   `build_spc3_prototype.bat`, `spc3_report_tools.py`,
   `test_spc3_prototype.py`, `spc3_gui.py`, and the native verifier GUI source
   and build script.
2. Include mirrored docs and specs: README, TLDR, v0.1/v0.2 notes, progress
   notes, C++/ASM boundary note, GUI README, and mirror manifest.
3. Include license notes for MPL-2.0 project source, zlib, zstd, liblzma, and
   optional runtime CUDA/NVRTC loading.
4. Exclude generated caches such as `__pycache__`.
5. Exclude generated lane data, `.spc3` files, unpacked `.pk3raw` outputs, and
   private report artifacts unless a release explicitly names them.
6. Exclude generated `.exe` files from `github-clean` unless that package is
   intentionally shipping binaries with dependency and license files.
7. Run `tools\check_markdown_mirrors.py` before treating the clean package as
   current.
