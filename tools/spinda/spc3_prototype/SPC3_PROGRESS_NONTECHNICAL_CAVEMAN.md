# SPC3 Compression Program Progress - Nontechnical Caveman

Generated: 2026-05-08
Audience: project owner, not code reader

## What This Is

SPC3 = custom compressor for huge Spinda PK3 file corpus.

Old way: many ZIP files, much repeated structure, lots of overhead.

New way: understand PK3 pattern, store only what needed, rebuild same bytes
later.

Goal: smaller files, exact rebuild, fast enough decode, simple native GUI.

## What Works Now

Program now can make real `.spc3` files.

It can:

- read lane ZIPs
- understand PK3 records
- rebuild records byte-for-byte
- pack into `.spc3`
- unpack back out
- verify every 80-byte PK3 record
- inspect file without unpacking
- consolidate pre-compressed compatible `.spc3` shards
- benchmark size and speed
- run big benchmark without building giant file in RAM
- run native dark-mode GUI

This is no longer only idea. It is working tool for current narrow use.

## Safety

Program strict.

Bad input fail clean:

- broken header
- wrong offsets
- cut-off stream
- wrong checksum
- wrong codec
- wrong predictor data
- extra bytes at end

No "close enough." Same bytes or fail.

## Format Frozen

Current file format now named SPC3 v0.1.

Means: current header/table/stream shape is written down and stable for now.

Stable:

- file starts with `SPC3`
- version number exists
- lane table shape known
- levels `0`, `1`, `2`, `3` known
- hashes stored
- codec info stored
- old files still read

Not final forever. Just first safe checkpoint.

## Compression Result

Big win.

256-lane sample:

| Thing | Size |
| --- | ---: |
| Current ZIPs | `2,926,925,218` bytes |
| SPC3 level 3 | `4,424,817` bytes |

Plain read: multi-gig ZIP pile became about 4.4 MB for this sample.

1024-lane streaming sample:

| Thing | Size |
| --- | ---: |
| Current ZIPs | `11,707,706,577` bytes |
| SPC3 level 3 | `18,285,512` bytes |

Plain read: about 11.7 GB ZIP pile became about 18.3 MB SPC3 estimate.

Why possible: data highly patterned. Program stores model + exceptions, not
same shape 65,536 times per lane.

## Speed Result

256-lane SPC3 level `3` unpack:

- `2385.3 ms`
- `536.6 MiB/s`

Plain read: decode speed good enough for current use. Keep measuring full
Phase 3 when data lands.

## Codec Result

Program can now test different compression engines inside `.spc3`.

64-lane level `3` result:

| Codec | Size | Speed note |
| --- | ---: | --- |
| zlib-9 | `1,085,803` | old default |
| zstd-9 | `1,069,893` | faster decode in sample |
| zstd-19 | `1,034,563` | smaller, slower than zstd-9 |
| LZMA2-9 | `985,995` | smallest, slower |

Plain read:

- zstd looks best next candidate
- LZMA2 good for smallest file
- zlib stays safe default now

1024-lane zstd check now done:

| Codec | Size | Speed |
| --- | ---: | ---: |
| zlib-9 | `18,285,512` | `1154.2 MiB/s` |
| zstd-9 | `17,895,663` | `1194.0 MiB/s` |

Plain read: zstd-9 smaller and faster. Make it preferred default candidate.
Keep zlib-9 as safe/compat mode until default switch is deliberate.

Codec choice now has names:

- `auto` = safe/compat zlib-9
- `--codec-profile fast` = recommended v0.2 typed zstd-9
- `--codec-profile small` = LZMA2-9 smallest-file mode

Typed level `3` split check now done through 64 lanes:

| Model | Size | Speed |
| --- | ---: | ---: |
| fused zstd-9 | `1,069,893` | `1035.0 MiB/s` |
| typed all-zstd-9 | `1,061,696` | `1114.5 MiB/s` |
| typed exceptions-LZMA2-9 | `967,103` | `941.7 MiB/s` |

1024-lane typed gate also done:

| Model | Size | Speed |
| --- | ---: | ---: |
| fused zstd-9 | `17,895,663` | `1142.1 MiB/s` |
| typed all-zstd-9 | `17,743,980` | `1159.5 MiB/s` |
| typed exceptions-LZMA2-9 | `15,490,371` | `928.8 MiB/s` |

Plain read: splitting template/bitmap/XOR streams helps at 64 and 1024 lanes.
zstd-9 split looks best balanced. LZMA2 split smallest, slower.

## GPU Status

GPU first real step now exists, and it moved from bench-only into real verify
and unpack for typed SPC3 files.

`--bench-gpu` dynamically loads NVIDIA CUDA driver/NVRTC, compiles a rebuild
kernel, uploads typed level `3` streams, rebuilds encrypted PK3 bytes on GPU,
then CPU compares every byte. `--gpu-rebuild` does same rebuild during real
`verify` or `unpack` and falls back to CPU when file/GPU not right.

4-lane real smoke:

| Item | Value |
| --- | ---: |
| GPU | `NVIDIA GeForce RTX 4070 Ti` |
| output bytes | `20,971,520` |
| XOR values | `4,954` |
| kernel | `0.781 ms` |
| mismatched lanes/bytes | `0 / 0` |

Plain read: GPU rebuild offload works. It is not default because small runs
still pay startup/compile cost.

1024-lane GPU scale:

| Item | Value |
| --- | ---: |
| output bytes | `5,368,709,120` |
| upload + kernel + download | `1518.793 ms` |
| same CPU level-3 unpack | `4381.556 ms` |
| mismatches | `0` |

Plain read: big typed batches can use GPU well. Small jobs still pay startup
and compile overhead, so GPU stays optional.

Real 4-lane GPU `verify` and `unpack` both passed with `0` mismatches. A
non-typed file requested with GPU correctly fell back to CPU.

GPU startup now caches inside one running process. First call still pays setup;
later calls in same bench/GUI process reuse context and compiled module.

## rANS/FSE Status

rANS/FSE custom codec now works as experiment.

1024-lane gate:

| Model | Size | Speed |
| --- | ---: | ---: |
| typed zstd-9 | `17,743,980` | `1091.4 MiB/s` |
| typed rANS | `17,711,423` | `1039.2 MiB/s` |

Plain read: rANS saved only `32,557` bytes, about `0.18%`, and ran slower.
Keep zstd-9 as best balanced choice.

## What Still Missing

No blocker for current narrow use.

Still known:

- full 65,536-lane Phase 3 data not here yet
- `auto` stays zlib-9 compat on purpose
- GPU optional, NVIDIA CUDA only
- rANS/FSE experiment only
- broader ASM waits for CRC/profile proof

Custom exception codec risk was measured. It works, but size win too tiny and
speed worse than typed zstd-9.

## Current Judgment

Compression program made major jump.

Before: research prototype and reports.

Now: real `.spc3` container, exact verify, codec choices, bad-file tests, format
spec, v0.2 typed file path, targeted ASM, GPU rebuild offload, native GUI, and
prepacked lane consolidation.

Bench now uses less RAM when `--bench-streaming` used. 1024-lane default run
finished clean. zstd-9 beat zlib-9. Typed level `3` split beat fused zstd-9 on
64 and 1024 lanes. GPU proof scaled to 1024 lanes and real GPU verify/unpack
works. rANS/FSE was tested and rejected as default.

## Wrap-Up Call

Use now.

Main path:

- v0.2 typed level `3`
- `--codec-profile fast`
- CPU verify reference
- GPU verify/unpack only when useful
- native GUI for normal operator use

65,536-lane estimate from 1024-lane measured gate:

| Item | 1024 lanes measured | 65,536 lanes estimate |
| --- | ---: | ---: |
| source ZIP bytes | `11,707,706,577` | `749,293,220,928` |
| typed zstd-9 SPC3 bytes | `17,743,980` | `1,135,614,720` |
| CPU typed unpack time | `4.416 s` | `282.604 s` |

Estimate only. Need full Phase 3 gate later.

Current wrap smoke:

```text
Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md
```

4 real lanes passed pack, CPU verify, GPU verify, CPU unpack, GPU unpack.
Mismatches `0`. CPU/GPU unpack hashes same. GPU cache second call no compile.

## Simple Use Steps

1. Pack: level `3`, typed, fast profile.
2. Verify: internal-only first.
3. Verify against source if source ZIPs available.
4. Unpack only when bytes needed on disk.
5. Use GPU for big typed verify/unpack.
6. Keep JSON reports.

Latest profile says CRC checking bigger than rebuild/encrypt in 20-lane typed
gate. Targeted shuffle ASM active. More ASM waits for proof.
