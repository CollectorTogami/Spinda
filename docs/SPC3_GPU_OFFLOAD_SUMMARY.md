# SPC3 GPU Offload Summary

## Caveman TLDR

GPU works for typed v0.2 level `3` rebuild verify/unpack.

NVIDIA CUDA only.

CPU fallback required.

Use GPU for big typed batches, not tiny one-shot jobs.

GUI/server cache CUDA context/module. Second run skips compile.

No GPU codec default now.

## Status Bucket

- Current status: Planning summary plus implemented CUDA/NVRTC rebuild-offload
  for SPC3 typed level-3 decompression/rebuild in bench, `verify`, and
  `unpack`.
- Last verified date: 2026-05-08.
- Proven artifacts: `tools/spinda/spc3_prototype/README.md`,
  `tools/spinda/spc3_prototype/spc3_prototype.cpp`,
  `Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`,
  `Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_verify_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_unpack_report.json`,
  `Phase3SpindaBlocks\_spc3_typed_v2_real4_verify_report.json`,
  `markdown-files/SPC3_CPU_GPU_STRAIN_REPORT_2026-05-07.md`, and the NVIDIA
  nvCOMP/CUB references listed at the end of this file.
- Deliberate scope: The current SPC3 prototype uses CUDA driver/NVRTC rebuild
  offload. Other GPU APIs are not current targets; CPU fallback remains the
  portability path. GPU is an optional typed v0.2 verify/unpack accelerator, not
  a required container feature.
- Wrap-up decision: Use `--gpu-rebuild` for large typed v0.2 verify/unpack when
  CUDA is available. Long-running native GUI/server paths reuse CUDA
  context/module state and bulk output downloads; one-shot CLI runs stay safe and
  report fallback reasons when GPU is unavailable or unsupported.
- Next action: Decide CRC policy from CPU profile evidence before adding deeper
  assembly. Keep targeted PK3 shuffle ASM active and do not add GPU codec work
  unless a real gate beats typed zstd-9.

## Short Answer

`Proven`: Typed level-3 rebuild can be moved to the GPU and still byte-match
CPU output in both bench and real `verify`/`unpack` on a real Phase 3 sample.

`Proven`: Scaled 1024-lane GPU rebuild has `0` mismatches. Production-like
upload+kernel+download was `1,518.793` ms, or `1,714.546` ms with one-time
compile, versus `4,381.556` ms CPU level-3 unpack. The bench-only CPU compare
step is validation cost, not production GPU decode cost.

`Inferred`: More of the compression/decompression program can be moved to the
GPU, but not all of it should be.

`Wrap-up`: For the current data gap before full Phase 3, GPU rebuild is good
enough to ship as optional. It has byte-match evidence on real typed verify and
unpack paths, plus a 1024-lane scale gate with zero mismatches.

`Current-binary smoke`: `Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md`
packed 4 real lanes as v0.2 typed level `3` with `--codec-profile fast`, then
passed CPU verify, GPU verify, CPU unpack, GPU unpack, CPU/GPU unpack hash
comparison, and a repeated GPU cache smoke. All mismatch counters were `0`; the
second cache sample reported `runtime_cache_hit=true` and `compile_ms=0.000`.

Best GPU candidates:

- PK3 decrypt/encrypt/rebuild loops.
- IV32 predictor matching and exception bitmap/XOR generation.
- Template, PID, checksum, and rebuild validation across many records.
- CRC32 over large batches of lane payloads or output chunks.
- Exception-stream packing with prefix sums.
- GPU-friendly SPC3 codecs such as nvCOMP LZ4, GDeflate, Deflate, Zstd,
  Cascaded, or ANS.
- SPC3 decompression that rebuilds many PK3 records in parallel.

Keep on CPU for now:

- File discovery, file I/O, and final archive writes.
- ZIP central-directory/local-header parsing and metadata emission.
- High-ratio LZMA2/7z fallback.
- Current per-entry 80-byte ZIP deflate layout.
- mGBA emulation and Phase 3 generation.

The practical rule stays the same: do not add GPU work until a measured batch
shows the CPU path is dominated by rebuild/extraction/CRC/codec work after the
native `.spc3` container removes ZIP-entry overhead.

## Evidence Split

### Proven

- Current SPC3 prototype uses CPU C++ plus x86-64 assembly hot loops and
  optional CUDA driver/NVRTC rebuild-offload paths behind `--bench-gpu` and
  `--gpu-rebuild`.
- The 20-lane SPC3 profile recorded larger costs in ZIP inflate + CRC32 and
  zlib entropy probing than in PK3 decrypt/model/rebuild.
- `--bench-gpu` dynamically loads `nvcuda.dll` and `nvrtc64_120_0.dll`, compiles
  a CUDA kernel at runtime, uploads typed level-3 template/bitmap/XOR streams,
  rebuilds encrypted PK3 payloads on the GPU, downloads output, and byte
  compares against the CPU reference.
- `--gpu-rebuild` uses the same typed rebuild path for real `verify` and
  `unpack`, then falls back to CPU for unsupported SPC3 layouts, missing
  predictor data, CUDA load failures, or validation mismatches.
- The CUDA path now caches the driver context and NVRTC-compiled module inside
  one running process, and failed CUDA probes are cached for that process too.
  One-shot CLI behavior remains safe, while streaming bench, the native GUI
  hidden `--server` worker, and any other long-running caller can avoid
  repeated compile/startup.
- GPU rebuild uses one bulk output download for small/medium results and falls
  back to per-lane downloads only for very large output buffers.
- NVIDIA nvCOMP provides GPU lossless compression/decompression APIs and lists
  LZ4, Snappy, GDeflate, Deflate, Zstandard, Cascaded, Bitcomp, ANS, and CRC32
  support.
- nvCOMP has batched compression/decompression APIs intended for multiple
  chunks, and its docs recommend reasonably balanced chunks for performance.
- CUB provides device-wide segmented scan operations that fit packed exception
  stream construction.

### Observed Once

- The local SPC3 strain report saw `0%` GPU utilization during the older
  20-lane CPU-only prototype run. That was expected because that run predated
  `--bench-gpu`.
- `Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`: RTX 4070 Ti rebuilt
  `20,971,520` encrypted output bytes for 4 real lanes with `4,954` XOR values,
  `0.781` ms kernel time, and `0` mismatched lanes/bytes.
- `Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`: RTX 4070 Ti
  rebuilt `5,368,709,120` encrypted output bytes at 1024 lanes with
  `6,691,736` XOR values and `0` mismatches. Upload+kernel+download was
  `1,518.793` ms, or `1,714.546` ms including compile, versus `4,381.556` ms
  CPU level-3 unpack.
- `Phase3SpindaBlocks\_spc3_gpu_cache_smoke_1_4_report.json`: one-process
  `--bench-gpu` ran 1 lane then 4 lanes. The first sample compiled in
  `173.090` ms; the second sample had `compile_ms=0.000`, proving the cached
  context/module path.
- `Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_release_summary.md`:
  64 real lanes packed as v0.2 typed level `3` with `--codec-profile fast`.
  Pack size was `1,061,696` bytes from `730,288,857` source ZIP bytes and
  `335,544,320` raw PK3 bytes. CPU verify/unpack and GPU verify/unpack all
  reported zero mismatches. GPU verify/unpack used bulk download on an RTX 4070
  Ti, with `307,118` XOR values and no fallback. The one-process 1/64-lane
  cache smoke reported `compile_ms=0.000` and `runtime_cache_hit=true` on the
  second sample.
- `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_verify_report.json`: real
  4-lane typed v2 `.spc3` verified through `--gpu-rebuild` with `0`
  mismatches.
- `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_unpack_report.json`: real
  4-lane typed v2 `.spc3` unpacked through `--gpu-rebuild` with `0`
  mismatches.
- `Phase3SpindaBlocks\_spc3_fused_zstd9_real4_gpu_fallback_verify_report.json`:
  fused v0.1 zstd requested GPU and correctly fell back to CPU because the file
  is not v0.2 typed level `3`.
- `Phase3SpindaBlocks\_spc3_typed_v2_real4_verify_report.json`: real 4-lane
  typed v2 `.spc3` verified with internal and source-compare mismatches `0`.

### Inferred

- GPU helps most when data stays resident on the GPU across several stages:
  decrypt, model, exception packing, optional codec, rebuild, and CRC.
- A GPU path is unlikely to help if every 80-byte record is copied to/from the
  GPU around a single tiny operation.
- GPU Deflate may help with existing ZIP/Deflate data only if entries are
  batched hard enough. The current ZIP layout has many tiny independent
  streams, so launch and transfer overhead are the main risk.
- GDeflate is better treated as a future SPC3 stream format choice than as a
  drop-in replacement for existing ZIP files. It is Deflate-like, but the
  bitstream layout is GPU-oriented.
- High-ratio entropy coding should stay CPU-side unless benchmarks show that a
  lower-ratio GPU codec is acceptable.

### Planned

- Keep the in-process CUDA module/context cache and cached-failure path visible
  in reports.
- Keep the native GUI worker path on `spc3_prototype.exe --server` so operator
  runs can reuse the CUDA/NVRTC cache until GUI exit, cancel, or compressor-path
  change.
- Keep bulk download versus per-lane download mode visible in reports.
- Add GPU codec probes only after typed rebuild and CPU codec policy are stable.
- Preserve CPU output as the reference for every GPU path.

### Obsolete

- None currently recorded.

## Ranked Offload Candidates

| Rank | Candidate | Compression Side | Decompression Side | Fit | Notes |
| ---: | --- | --- | --- | --- | --- |
| 1 | IV32 predictor match and exception generation | Compare `actual_iv32` against predictor, write mismatch flag and XOR | Rebuild IV32 from predictor plus exceptions | Excellent | One record per thread. Low branching. Natural `65536`-record lane batch. |
| 2 | PK3 decrypt/encrypt/rebuild proof | Decrypt records, verify checksum/PID/template, rebuild encrypted bytes | Generate encrypted PK3 records from model streams | Good | Current CPU time is not dominant yet, but the operation is regular and already uses contiguous buffers. |
| 3 | Template and content validation | Compare constant decrypted spans, validate PID/checksum/species fields | Validate rebuilt output before writing | Good | Use per-record status bytes plus reduction to lane-level counters. |
| 4 | CRC32 batches | CRC inflated PK3 payloads and final rebuilt chunks | CRC rebuilt chunks before ZIP/SPC3 export | Good | nvCOMP has GPU CRC32 support. Best if many chunks are processed per launch. |
| 5 | Exception packing | Convert mismatch flags into compact bitmap and XOR stream offsets | Expand bitmap/XOR stream into per-record IV32 | Good | Use CUB-style scan/prefix-sum. Keep output buffers preallocated. |
| 6 | SPC3 stream codecs | Compress typed streams with GPU-friendly codecs | Decompress typed streams directly to device buffers | Medium to good | Test nvCOMP Zstd, GDeflate, LZ4, Cascaded, and ANS against CPU zlib/LZMA2/rANS. |
| 7 | Existing ZIP Deflate inflate | Inflate current lane ZIP entries | Not usually relevant after SPC3 exists | Medium | Only worth testing through batched nvCOMP Deflate. Tiny 80-byte entries are the risk. |
| 8 | Solid encrypted/decrypted stream compression | Compress concatenated PK3 stream | Decompress to lane buffers | Medium | Simpler than field modeling but likely worse ratio than SPC3 level 3. |
| 9 | ZIP output assembly | Build local headers, central directory, ZIP64 records | Parse archive metadata | Poor | Branchy metadata work. Keep CPU-side. |
| 10 | File I/O | Read/write archives | Read/write archives | Poor | Storage, OS cache, and filesystem calls remain CPU/OS work. |

## Codec Choices To Benchmark

| Codec / API | Use Case | Expected Project Fit |
| --- | --- | --- |
| CPU zlib Deflate | Current ZIP compatibility and reference checks | Keep as compatibility baseline. |
| CPU LZMA2 / 7z | Maximum generic compression benchmark | Keep as high-ratio fallback; not first GPU target. |
| nvCOMP Deflate | Batched compatibility test for current Deflate streams | Try only after batching whole lanes or many lanes. |
| nvCOMP GDeflate | GPU-oriented SPC3 stream option | Strong decompression candidate for a new container, not a ZIP drop-in. |
| nvCOMP Zstd | Speed/ratio compromise for SPC3 streams | Worth testing against CPU zstd/LZMA2 once streams are defined. |
| nvCOMP LZ4 / Snappy | Very fast lower-ratio streams | Useful for temporary caches, previews, or fast validation artifacts. |
| nvCOMP Cascaded | Structured numeric/tabular streams | Good fit for IV32, flags, small integers, run lengths, and offsets. |
| nvCOMP ANS | Entropy stream experiment | Candidate for exception and small-symbol streams. Benchmark before trusting. |
| nvCOMP Bitcomp | Numeric/scientific compression experiment | Lower priority. Review license/redistribution terms before depending on it. |

## Proposed GPU Memory Layout

`Planned`: A useful GPU prototype should avoid one-record transfers. Use a
batch layout like this:

```text
device_encrypted_pk3    [lane_count][65536][80]
device_decrypted_pk3    optional scratch, or one-record-per-thread registers
device_iv32             [lane_count][65536]
device_exception_flags  [lane_count][65536]
device_exception_xor    [lane_count][65536] upper-bound scratch
device_status           [lane_count][65536] bitfield of validation failures
device_lane_counters    [lane_count] reduced mismatch/error counts
device_stream_offsets   prefix-sum offsets for packed exception streams
```

For compression, the CPU should read the source files and parse container
metadata, then upload contiguous lane payloads. The GPU should run model,
validation, exception packing, and optional codec steps before copying compact
SPC3 streams back to host memory.

For decompression, the CPU should parse the SPC3 header and stream table, then
upload compressed stream chunks or point nvCOMP at device-readable buffers. The
GPU should decode streams, rebuild IV32 values, rebuild encrypted PK3 records,
and compute validation counters before the CPU writes output.

## Compression Pipeline With Optional GPU

`Planned`:

1. CPU reads input lane archives or `.spc3` files into RAM.
2. CPU validates cheap container metadata and rejects unsupported shapes.
3. CPU uploads large contiguous payload batches, not individual PK3 records.
4. GPU decrypts PK3 records or decodes SPC3 typed streams.
5. GPU validates PID/checksum/template rules and writes per-record status.
6. GPU compares IV32 against predictor and writes exception flags/XOR values.
7. GPU packs exception streams using prefix sums.
8. GPU optionally compresses typed streams with nvCOMP codecs.
9. CPU receives compact streams and writes the final `.spc3` container.
10. CPU report compares all GPU counters with CPU reference counters.

## Decompression Pipeline With Optional GPU

`Planned`:

1. CPU parses the `.spc3` header, stream table, version, lane map, and codec
   IDs.
2. CPU uploads compressed stream chunks or maps them into device-readable
   buffers.
3. GPU decompresses typed streams with nvCOMP or custom kernels.
4. GPU expands exception bitmaps and reconstructs IV32.
5. GPU rebuilds decrypted PK3 templates into encrypted 80-byte PK3 records.
6. GPU computes CRC32 or validation hashes for rebuilt records.
7. CPU downloads final PK3 buffers in bulk for small/medium output, per lane
   for very large output, or downloads compact verification results.
8. CPU writes ZIP, loose PK3, or report output depending on command mode.

## What Not To Offload Yet

- Do not GPU-rewrite the emulator or Phase 3 generation. That bottleneck is
  stateful emulation, not a broad compression kernel.
- Do not keep the current ZIP-per-80-byte-entry format and expect GPU Deflate
  to fix everything. That layout creates too many tiny streams.
- Do not move ZIP metadata parsing to GPU. It is small, branchy, and
  security-sensitive.
- Do not replace LZMA2/7z with GPU codecs until corpus tests prove the size
  tradeoff is acceptable.
- Do not make GPU required for decompression. SPC3 should remain readable on
  CPU-only machines.

## Benchmark Gate

`Measured for rebuild`: GPU work should be accepted only if a report shows one
of these:

| Gate | Required Evidence |
| --- | --- |
| Model/rebuild offload | Proven at 1024 typed lanes: transfer+kernel+download beats CPU level-3 unpack and still matches every rebuilt byte. |
| CRC offload | Batched GPU CRC beats CPU CRC on representative lane batches. |
| Codec offload | GPU codec improves wall time at an acceptable size ratio versus CPU zlib/LZMA2/rANS. |
| Decompression offload | GPU decompression plus rebuild beats CPU decompression plus rebuild for full-lane or multi-lane batches. |
| UI/GUI toggle | CLI path is stable first; GUI only exposes a proven backend. |

Completed rebuild benchmark sizes:

- `4` lanes to match small worker bundles.
- `20` lanes to compare with current strain report.
- `64` lanes to test mid-size batches.
- `1024` lanes to test large batches.

## Correctness Tests

Every GPU path needs these tests before production use:

- CPU/GPU round-trip equality for generated synthetic lanes.
- CPU/GPU equality for real lane samples.
- Corrupt ZIP/SPC3 inputs still fail closed.
- Predictor-hit and predictor-exception lanes match CPU counters.
- Content PID mismatch, bad checksum, and template drift are detected.
- GPU OOM falls back to CPU or fails without partial output.
- Non-NVIDIA or no-GPU systems still run CPU mode.
- Reports include backend, device name, driver/runtime version, batch size,
  transfer time, kernel time, codec time, and validation counters.

## Dependency And License Notes

`Inferred`: A GPU prototype should keep nvCOMP/CUDA optional. Do not make the
clean source package depend on NVIDIA runtime files unless the project license
docs are updated again.

If nvCOMP is bundled or linked in a distributed build, review NVIDIA's SDK
license and update `LICENSES.md`, binary notices, and build instructions. If
the implementation uses only locally installed CUDA/nvCOMP as an optional
developer dependency, document the expected installation and keep CPU-only
builds supported.

## Recommendation

`Implemented`: `--bench-gpu` is the measured GPU rebuild prototype, and
`--gpu-rebuild` is the real typed v0.2 verify/unpack path. CPU still owns file
parsing, unsupported-layout fallback, and final output/reporting. CUDA/NVRTC
uploads typed level-3 template/bitmap/XOR streams, rebuilds encrypted PK3
payloads on the GPU, downloads the result, and byte-compares when validation is
requested. Reports now expose fallback reason, download mode, runtime cache
hit, cached runtime failure, runtime initialization count, host CRC timing, and
mismatch counters.

Next GPU work should stay narrow:

1. Keep `--gpu-rebuild` optional and fallback-safe; use it for large typed v0.2
   batches and keep every fallback reason explicit.
2. Use the report fields to compare GUI-worker cached behavior versus one-shot
   GPU behavior; one-shot CLI runs still load, run, report, and exit cleanly.
3. Decide CRC strategy from CPU profile evidence before adding deeper ASM; the
   fresh 64-lane CPU verify/unpack profiles still show CRC above
   rebuild/encrypt, while narrow PK3 shuffle ASM is already active.
4. Add `gpu_pack_exceptions` only if compression-side profiling points there.
5. Add `gpu_codec_probe` only after rebuild and exception packing measurements;
   nvCOMP candidates are Cascaded, Zstd, GDeflate, and LZ4.

Large runs did beat the CPU rebuild path after transfer time, but small runs
still pay startup/compile overhead. Keep GPU as a verified optional accelerator,
not a required codec/container feature.

## References Checked

- NVIDIA nvCOMP overview: <https://docs.nvidia.com/cuda/nvcomp/index.html>
- NVIDIA nvCOMP batched C quick start:
  <https://docs.nvidia.com/cuda/nvcomp/samples/lowlevel_c_quickstart.html>
- NVIDIA nvCOMP GDeflate notes:
  <https://docs.nvidia.com/cuda/nvcomp/gdeflate.html>
- NVIDIA nvCOMP CRC32 notes:
  <https://docs.nvidia.com/cuda/nvcomp/crc32.html>
- NVIDIA CUB segmented scan:
  <https://nvidia.github.io/cccl/unstable/cub/api/structcub_1_1DeviceSegmentedScan.html>
- NVIDIA Blackwell decompression engine note:
  <https://developer.nvidia.com/blog/speeding-up-data-decompression-with-nvcomp-and-the-nvidia-blackwell-decompression-engine>
- NVIDIA nvCOMP SDK license:
  <https://docs.nvidia.com/cuda/nvcomp/license.html>
