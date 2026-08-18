# SPC3 Compression Program Progress - Technical Caveman

Generated: 2026-05-08
Scope: SPC3 prototype in `tools/spinda/spc3_prototype`

## Current State

SPC3 now real container tool, not loose theory. CPU C++ prototype can:

- audit Phase 3 lane ZIPs
- pack `.spc3`
- unpack `.spc3`
- verify `.spc3` byte-for-byte
- inspect `.spc3` metadata without unpack
- consolidate compatible prepacked `.spc3` shards
- bench size, pack time, unpack time, verify time, MiB/s
- run typed level `3` streaming oracle: template, bitmap, XOR value substreams
- pack/verify/inspect/unpack real SPC3 v0.2 typed level `3`
- run CUDA/NVRTC typed level `3` rebuild offload with CPU byte compare
- run real `unpack`/`verify` GPU typed rebuild with CPU fallback
- run experimental byte-rANS codec on typed bitmap/XOR streams
- launch native Win32 disk-light GUI with report summary/compare

Hot loops use x86-64 assembly where useful now:

- PK3 checksum
- Gen 3 XOR path
- PK3 48-byte block shuffle
- decrypted template compare
- IV32 predictor + bitmap + XOR exception expansion

GPU no longer only theory. Bench path exists behind `--bench-gpu`, and real
decode path exists behind `--gpu-rebuild` / `--gpu`. It stays optional. Small
one-shot CLI batches can still pay CUDA startup/compile cost. Long-running
server/GUI paths cache CUDA context and module state.

## Frozen Format

SPC3 v0.1 format now frozen. Spec file:

`SPC3_V0_1_FORMAT.md`

v0.1 layout:

```text
80-byte header
optional zlib predictor stream
96-byte lane table entry * lane_count
contiguous lane streams
```

Stable v0.1 now:

- magic `SPC3`
- version `1`
- header size `80`
- table entry size `96`
- levels `0..3`
- contiguous stream layout
- source ZIP CRC/FNV hashes
- original/rebuilt payload CRCs
- strict no-gap/no-overlap/no-trailing-byte parser
- legacy `flags=0` decode support

Implemented v0.2 now:

- version `2` typed level `3`
- three substreams per lane: template, exception bitmap, XOR values
- strict typed substream parser
- typed pack/verify/inspect/unpack

Active optional paths now:

- zstd backend
- LZMA2 backend
- external predictor reference
- typed level 3 streaming oracle
- CUDA/NVRTC rebuild offload
- GUI

Experimental only:

- rANS/FSE

Important choice: no header/table widening. Codec metadata fits inside existing
entry `flags` word. Old files with `flags=0` still decode as legacy zlib-9 for
levels `1..3`.

## Levels

| Level | Model | Default Codec | Rebuild |
| ---: | --- | --- | --- |
| `0` | raw encrypted PK3 lane payload | none | copy out |
| `1` | full decrypted PK3 stream | zlib-9 | encrypt each record |
| `2` | one template + IV32 stream | zlib-9 | rebuild PID/IV/checksum/encrypt |
| `3` | one template + predictor bitmap/XOR exceptions | zlib-9 | predictor + exceptions, then rebuild |

Level `3` still best current format. It uses predictor table hard.

## Codec Backend

Internal enum now:

- `none`
- `zlib`
- `zstd`
- `lzma2`
- `rans` experimental typed-stream codec

CLI now:

```powershell
--codec auto|none|zlib|zstd|lzma2|rans
--codec-level N
--codec-profile compat|fast|small
--typed-level3
--typed-exceptions-only
--bench-native-codecs
--bench-typed-level3
--bench-gpu
--bench-rans-fse
--gpu-rebuild
```

Default `auto`:

- level `0` -> none
- levels `1..3` -> zlib-9

Codec profiles:

- `compat` -> zlib-9
- `fast` -> zstd-9, current recommended v0.2 typed candidate
- `small` -> LZMA2-9 archive-oracle mode

Native backends:

- zlib: stable default
- zstd: decode-speed candidate
- LZMA2: size candidate, slower decode
- rANS/FSE: experimental typed level 3 bitmap/XOR codec, not default

Build now links:

```text
-lz -lzstd -llzma
```

## Test Coverage Added

Regression harness now checks:

- pack/unpack/verify/inspect levels `0..3`
- zstd level `3` pack + verify
- streaming bench size math against full-container bench
- external predictor level `3`
- bad magic
- bad table offset
- truncated stream
- wrong embedded predictor size
- level mismatch
- bad codec ID
- wrong codec ID against stream bytes
- trailing bytes
- wrong CRC
- header fuzz: bad version, bad record count, bad record size, bad header size
- native codec bench JSON shape
- typed level `3` bench JSON shape and raw substream byte counts
- typed v0.2 pack/verify/inspect/unpack and bad typed substream metadata
- ASM IV32 exception expansion
- CUDA/NVRTC GPU rebuild smoke when CUDA is available
- real typed v0.2 `verify --gpu-rebuild` and `unpack --gpu-rebuild` when CUDA
  is available
- CPU fallback when `--gpu-rebuild` is requested for unsupported SPC3 files
- environment-simulated CUDA disabled / forced GPU failure fallback
- CPU typed decode profile fields in verify/unpack reports
- typed rANS pack/verify and streaming rANS/FSE bench report shape

Failure must be clean nonzero exit. No crash accepted.

Fresh verification:

```text
build_spc3_prototype.bat -> exit 0
spc3_prototype.exe --self-test -> self-test ok
test_spc3_prototype.py -> spc3 prototype regression tests ok
check_markdown_mirrors.py -> all PASS
```

Current wrap-up smoke report:

`Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md`

4 real lanes. v0.2 typed level `3`, `--codec-profile fast`. CPU verify, GPU
verify, CPU unpack, GPU unpack all OK. Mismatch counters `0`. CPU/GPU unpack
hashes match. GPU cache smoke second call: `runtime_cache_hit=true`,
`compile_ms=0.000`.

## Bench Evidence

Native codec bench report:

`Phase3SpindaBlocks\_spc3_native_codec_bench_report.json`

64-lane level `3` result:

| Codec | Size bytes | Unpack ms | Decode MiB/s |
| --- | ---: | ---: | ---: |
| zlib-9 | `1,085,803` | `849.938` | `376.498` |
| zstd-3 | `1,084,877` | `772.018` | `414.498` |
| zstd-9 | `1,069,893` | `600.342` | `533.030` |
| zstd-19 | `1,034,563` | `641.533` | `498.805` |
| LZMA2-9 | `985,995` | `674.799` | `474.215` |

Read: zstd-9 best speed/size balance in this sample. LZMA2-9 smallest, but
decode slower. zstd-19 smaller than zstd-9 but slower.

Default zlib bench report:

`Phase3SpindaBlocks\_spc3_default_bench_256_report.json`

| Lanes | Current ZIP bytes | SPC3 L3 bytes | L3 unpack ms | L3 MiB/s |
| ---: | ---: | ---: | ---: | ---: |
| `1` | `11,415,137` | `228,836` | `9.0` | `558.0` |
| `4` | `45,701,445` | `245,877` | `36.6` | `546.5` |
| `20` | `228,261,594` | `491,722` | `176.5` | `566.5` |
| `64` | `730,288,857` | `1,085,803` | `567.8` | `563.6` |
| `256` | `2,926,925,218` | `4,424,817` | `2385.3` | `536.6` |

Read: SPC3 L3 crush current ZIP size. Decode speed still decent.

Streaming 1024-lane bench report:

`Phase3SpindaBlocks\_spc3_streaming_bench_1024_report.json`

| Lanes | Current ZIP bytes | SPC3 L3 bytes | L3 unpack ms | L3 MiB/s |
| ---: | ---: | ---: | ---: | ---: |
| `1024` | `11,707,706,577` | `18,285,512` | `4621.2` | `1107.9` |

All decode CRC mismatches `0`. Stderr empty. Working set near `23 MiB` during
polling. Streaming bench did job.

Targeted zstd-9 gate report:

`Phase3SpindaBlocks\_spc3_streaming_zstd9_gate_report.json`

| Codec | Level | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: | ---: |
| zlib-9 | `3` | `18,285,512` | `4435.803` | `1154.244` |
| zstd-9 | `3` | `17,895,663` | `4287.937` | `1194.047` |

Read: zstd-9 wins both size and speed at 1024 lanes. Policy: zstd-9 preferred
default candidate; zlib-9 compatibility/safe until auto flip deliberate.

Exception stats at 1024 lanes:

- predictor exceptions: `6,691,736`
- avg per lane: `6,534.898`
- min/max per lane: `0` / `17,225`
- bitmap density: `0.100`
- XOR zero values: `0`
- rANS/FSE table-init risk: `lower`

Typed level-3 oracle report:

`Phase3SpindaBlocks\_spc3_typed_level3_bench_report.json`

64-lane result:

| Model | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| fused zlib-9 | `1,085,803` | `315.142` | `1015.415` |
| fused zstd-9 | `1,069,893` | `309.183` | `1034.984` |
| typed all-zlib-9 | `1,078,999` | `293.710` | `1089.511` |
| typed all-zstd-9 | `1,061,696` | `287.122` | `1114.509` |
| typed all-LZMA2-9 | `970,171` | `335.705` | `953.219` |
| typed exceptions-LZMA2-9 | `967,103` | `339.827` | `941.654` |

1024-lane typed gate report:

`Phase3SpindaBlocks\_spc3_typed_level3_bench_1024_report.json`

| Model | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| fused zlib-9 | `18,285,512` | `4,518.689` | `1,133.072` |
| fused zstd-9 | `17,895,663` | `4,482.900` | `1,142.118` |
| typed all-zstd-9 | `17,743,980` | `4,415.682` | `1,159.504` |
| typed all-LZMA2-9 | `15,539,511` | `5,508.702` | `929.439` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5,512.759` | `928.755` |

Read: typed split pays at 1024 lanes too. zstd-9 typed wins speed+size over
fused zstd-9. LZMA2 typed smallest but slower.

Real typed v0.2 file proof:

`Phase3SpindaBlocks\_spc3_typed_v2_real4_pack_report.json`

| Proof | Value |
| --- | ---: |
| lanes | `4` |
| file bytes | `245,784` |
| typed exceptions | `4,954` |
| verify internal mismatches | `0` |
| verify source-compare mismatches | `0` |

CUDA/NVRTC GPU rebuild proof:

`Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json`

| Proof | Value |
| --- | ---: |
| device | `NVIDIA GeForce RTX 4070 Ti` |
| lanes | `4` |
| encrypted output bytes | `20,971,520` |
| XOR values | `4,954` |
| kernel ms | `0.781` |
| mismatched lanes | `0` |
| mismatched bytes | `0` |

GPU scale report:

`Phase3SpindaBlocks\_spc3_gpu_scale_20_64_1024_report.json`

| Lanes | Output bytes | Upload ms | Kernel ms | Download ms | CPU L3 unpack ms | Mismatches |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `20` | `104,857,600` | `4.297` | `3.700` | `22.188` | `85.149` | `0` |
| `64` | `335,544,320` | `11.867` | `15.708` | `73.689` | `266.465` | `0` |
| `1024` | `5,368,709,120` | `145.592` | `228.607` | `1,144.594` | `4,381.556` | `0` |

Read: 1024-lane production-like GPU cost is upload+kernel+download =
`1,518.793` ms, or `1,714.546` ms including one-time compile. Bench
`compare_ms=5,282.588` is CPU validation rebuild, not production GPU decode.
GPU rebuild is useful for big typed batches, but small jobs still pay startup.

Real GPU decode reports:

- `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_verify_report.json`
- `Phase3SpindaBlocks\_spc3_typed_v2_real4_gpu_unpack_report.json`
- `Phase3SpindaBlocks\_spc3_fused_zstd9_real4_gpu_fallback_verify_report.json`

Read: typed v2 verify/unpack used the RTX 4070 Ti and had `0` mismatches.
Fused v0.1 zstd requested GPU and correctly fell back to CPU.

rANS/FSE typed gate:

`Phase3SpindaBlocks\_spc3_typed_rans_fse_gate_20_64_1024_report.json`

| Model | Size bytes | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| typed all-zstd-9 | `17,743,980` | `4,691.347` | `1,091.371` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5,808.576` | `881.455` |
| typed exceptions-rANS | `17,711,423` | `4,926.919` | `1,039.189` |

Read: byte-rANS round-trips and byte-matches, but saves only `32,557` bytes
versus typed zstd-9 at 1024 lanes and decodes slower. Keep it experimental.

## Design Call

Do not make LZMA2 default yet. Size win exists, speed cost also exists.

Do not make rANS/FSE default. It works, but the measured win is too small and
slower than typed zstd-9.

Do not make GPU mandatory. The rebuild kernel works, real typed unpack/verify
works, and large batches can beat CPU rebuild after transfer, but small batches
pay CUDA startup/compile cost. Keep CPU fallback. CUDA context/NVRTC module now
caches inside one process, so repeated bench/GUI calls can amortize compile.

Do not split level `3` inside v0.1. Real typed file layout is v0.2 because
physical stream layout changes.

Best next codec candidate: zstd-9 for level `3`, zstd-3 or zstd-9 for fast
decode mode. Typed v0.2 layout now implemented and verified on real lanes.

## Wrap-Up Call

Use now for narrow Phase 3 Spinda PK3 lane work.

Main path:

- v0.2 typed level `3`
- `--codec-profile fast`
- CPU verify as reference
- optional GPU verify/unpack when CUDA exists
- native GUI for operator runs

Codec law:

- `auto` = zlib-9 compat
- `fast` = zstd-9 active typed v0.2 path
- `small` = LZMA2-9 smallest-file path
- rANS/FSE = experiment only

65,536-lane estimate from measured 1024-lane typed zstd-9 gate:

| Item | 1024 lanes measured | 65,536 lanes linear estimate |
| --- | ---: | ---: |
| source ZIP bytes | `11,707,706,577` | `749,293,220,928` |
| typed zstd-9 SPC3 bytes | `17,743,980` | `1,135,614,720` |
| CPU typed unpack time | `4,415.682 ms` | `282.604 s` |

Estimate is not Phase 3 proof. It is planning bound until full lane data lands.

## Known Limits

Old bench still RAM-heavy. It builds complete containers in memory and can
duplicate full lane streams.

New `--bench-streaming` path fixes this for default large samples. It keeps one
lane model and one compressed stream in RAM, computes exact v0.1 container size,
and verifies decoded bytes right away.

1024-lane default streaming bench and 1024-lane typed oracle now done. Full
native/typed codec matrix can still take long because LZMA2 compress cost is
high.

Typed stream experiment now exists at 1024 lanes, and real typed v0.2 file I/O
works. rANS/FSE now has fair input streams and a working prototype, but it did
not beat typed zstd-9 enough to become default.

rANS/FSE risk confirmed: exception bitmap/XOR streams can be encoded, but
decoder/table/cache overhead is not buying enough. Keep as experiment.

Need more assembly only where profiler points:

- PK3 rebuild/encrypt loop
- exception bitmap build
- XOR exception packing
- CRC/hash loops

Fresh CPU fallback profiles now come from `cpu_decode_profile` in `verify` and
`unpack` reports. Use `rebuild_encrypt_ms` versus `stream_decode_ms` before
writing more ASM.

20-lane v0.2 typed zstd-9 CPU profile:

| Slice | ms |
| --- | ---: |
| stream decode | `3.034` |
| IV expand | `5.210` |
| rebuild/encrypt | `44.783` |
| CRC | `85.732` |

Read: targeted PK3 shuffle ASM is active. CRC/hash is bigger than rebuild in
this gate; broader rebuild/encrypt ASM waits until CRC policy is settled.

Keep parser and file orchestration in C++.

## Operator Steps

1. Build baseline package for sharing.
2. Pack with v0.2 typed level `3` and `--codec-profile fast`.
3. Verify internal-only first.
4. Verify against source when source ZIPs available.
5. Use GPU for large typed verify/unpack when CUDA exists.
6. Keep JSON reports as release evidence.
