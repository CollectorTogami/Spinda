# SPC3 Prototype TLDR

Caveman-full short doc. For exact audit trail, use [README.md](README.md).

## Wrap-Up Call

Use now.

Scope narrow:

- Phase 3 Spinda PK3 lanes
- `.spc3` pack
- verify
- inspect
- unpack
- consolidate prepacked shards
- native dark GUI

Main path now:

```text
v0.2 typed level 3 + --codec-profile fast
```

Codec law:

- `auto` = zlib-9 compat
- `fast` = zstd-9 normal new work
- `small` = LZMA2-9 size run
- rANS/FSE = experiment only

Low-level law:

- ASM active for measured x86-64 hot loops.
- `spc3_shuffle48_asm` active.
- Bigger PK3 ASM waits until CRC choice clear.
- GPU optional, NVIDIA CUDA only, CPU fallback required.
- GUI simple by design.

65536-lane estimate from 1024-lane typed gate:

| Thing | 1024 measured | 65536 estimated |
| --- | ---: | ---: |
| source ZIP | `11,707,706,577` | `749,293,220,928` |
| typed zstd-9 SPC3 | `17,743,980` | `1,135,614,720` |
| CPU typed unpack | `4.416 s` | `282.604 s` |

Estimate only. Full Phase 3 data decides final number.

Current wrap smoke:

```text
Phase3SpindaBlocks\_spc3_wrapup_v02_typed_fast_real4_release_summary.md
```

4 real lanes. Pack, CPU verify, GPU verify, CPU unpack, GPU unpack pass.
Mismatches `0`. CPU/GPU unpack hashes match. GPU cache second run:
`runtime_cache_hit=true`, `compile_ms=0.000`.

## What It Is

SPC3 prototype = C++ compression proof tool for Phase 3 Spinda PK3 lanes. Now
has real `.spc3` pack/unpack/verify/inspect/bench modes, frozen v0.1 layout,
v0.2 typed level-3 pack/unpack, x86-64 ASM hot loops, optional CUDA/NVRTC GPU
rebuild for typed unpack/verify, native zlib/zstd/LZMA2 stream-codec hooks,
and experimental byte-rANS typed substreams. Good enough for current narrow use.
Still keep format versioning honest.

Input: `0xLLLL.spinda80.zip` lane files.
Outputs: JSON reports, optional `.spc3`, optional `0xLLLL.spinda80.zip`
unpack output. Raw `0xLLLL.pk3raw` still possible. ZIP output holds encrypted
`0xUUUULLLL.pk3` records. No loose 65,536-file PK3 extraction unless another
tool opens lane ZIP.

## License

Folder source = repo default MPL-2.0 unless file says otherwise. Build links
zlib, zstd, and liblzma, so keep zlib/zstd/liblzma license text with any binary
package. It does not ship 7-Zip, PKHeX.Core, OpenCL, `py7zr`, or libarchive.
CUDA use is optional runtime driver/NVRTC loading for `--bench-gpu` and
`--gpu-rebuild`.
Reports and future `.spc3` files are artifacts, not permission to redistribute
game data.

## Why It Exists

Need know custom compression is real before building final format.

Tool answers:

- Can decrypt PK3, model data, rebuild encrypted PK3 byte-exact?
- Can PID upper half predict IV32 enough?
- Where time go: ZIP parse, inflate, PK3 crypto, rebuild, entropy probe?
- Is GPU worth it yet?

Current answer: PK3 model good. Assembly hot loops active, including IV32
exception expansion. Hardened `.spc3` container exists. v0.2 typed level `3`
packs/verifies/unpacks. GPU rebuild offload now works in real `unpack` and
`verify`, byte-matches CPU, and falls back to CPU for unsupported files. rANS
works on typed streams, but stays experimental because zstd-9 still wins the
balanced size/speed call.

Codec policy now explicit: `auto` stays compat/zlib-9. Use
`--codec-profile fast` for v0.2 typed zstd-9, or `--codec-profile small` for
LZMA2-9 archive runs.

## Build

```powershell
tools\spinda\spc3_prototype\build_spc3_prototype.bat
```

CPU target: Windows x86-64/AMD64. AMD Ryzen/Threadripper/EPYC are fine. Current
build uses `-march=native`; use a baseline x86-64 build for mixed machines.
GPU target: NVIDIA CUDA only. AMD/Intel GPUs fall back to CPU.

Baseline build:

```powershell
tools\spinda\spc3_prototype\build_spc3_prototype.bat baseline
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

## Run

```powershell
$env:PATH='C:\msys64\mingw64\bin;'+$env:PATH
.\tools\spinda\spc3_prototype\spc3_prototype.exe --root Phase3SpindaBlocks --limit-zips 20 --report Phase3SpindaBlocks\_spc3_prototype_report.json
```

Self-test:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --self-test
```

Python regression:

```powershell
<repo-root>\.venv-mgba\bin\python.exe tools\spinda\spc3_prototype\test_spc3_prototype.py
```

Native disk-light verifier GUI:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
.\tools\spinda\spc3_gui_native\spc3_verifier_gui.exe
```

GUI now has built-in report summary/compare for pack, verify, inspect, unpack,
consolidate, CPU reports, and GPU reports.

Full GUI guide:
[SPC3_NATIVE_GUI_GUIDE.md](../spc3_gui_native/SPC3_NATIVE_GUI_GUIDE.md).

## SPC3 Commands

## Mode I/O

| Mode | Input | Output |
| --- | --- | --- |
| `audit` | lane ZIP folder | JSON report only |
| `pack` | lane ZIP folder, level/profile, optional predictor | `.spc3` + pack report |
| `verify` | `.spc3`, optional source lane ZIP folder | verify report only |
| `unpack` | `.spc3`, lane pick | default `0xLLLL.spinda80.zip` output + report |
| `inspect` | `.spc3` | metadata report only |
| `consolidate` | folder of `.spc3` shards | combined `.spc3` + report |
| `bench` | lane ZIP folder | benchmark report |

Unpack ZIP contains encrypted `0xUUUULLLL.pk3` records. Raw old output needs
`--unpack-format raw`.

Pack:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --root Phase3SpindaBlocks --limit-zips 1 --level 3 --output Phase3SpindaBlocks\sample.spc3 --report Phase3SpindaBlocks\_spc3_pack_report.json
```

Verify:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --input Phase3SpindaBlocks\sample.spc3 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_verify_report.json
```

Unpack:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked --report Phase3SpindaBlocks\_spc3_unpack_report.json
```

Default unpack writes lane ZIPs:

```text
0xLLLL.spinda80.zip
```

Inside ZIP: stored encrypted `0xUUUULLLL.pk3` records.

Raw old shape:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_raw --unpack-format raw --report Phase3SpindaBlocks\_spc3_unpack_raw_report.json
```

Lane pick:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_one --lane 00A5 --report Phase3SpindaBlocks\_spc3_unpack_one_report.json
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode unpack --input Phase3SpindaBlocks\sample.spc3 --unpack-dir Phase3SpindaBlocks\_spc3_unpacked_range --lane-from 0001 --lane-to 00FF --report Phase3SpindaBlocks\_spc3_unpack_range_report.json
```

Inspect:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode inspect --input Phase3SpindaBlocks\sample.spc3 --report Phase3SpindaBlocks\_spc3_inspect_report.json
```

Bench:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --root Phase3SpindaBlocks --bench-limits 1,4,20,64 --report Phase3SpindaBlocks\_spc3_bench_report.json
```

Add `--bench-external` for 7z/zstd oracle runs over encrypted raw, decrypted
solid, template+IV32, and template+exception streams.

Add `--bench-streaming` for big samples. It handles one lane at time, verifies
decode right away, computes exact SPC3 v0.1 size, avoids giant `.spc3` buffer.

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-streaming --root Phase3SpindaBlocks --bench-limits 1,4,20,64,256,1024 --report Phase3SpindaBlocks\_spc3_streaming_bench_report.json
```

Add `--bench-native-codecs` for real SPC3 codec runs:

- zlib-9
- zstd-3, zstd-9, zstd-19
- LZMA2-9

Native codec bench records size, pack ms, unpack ms, verify ms, decode MiB/s,
and CRC mismatches for levels `1`, `2`, and `3`.

Add `--bench-typed-level3` with `--bench-streaming` for v0.2 oracle. It splits
level `3` into template, exception bitmap, and XOR values.

Pack real v0.2 typed level `3`:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode pack --typed-level3 --codec-profile fast --root Phase3SpindaBlocks --limit-zips 4 --level 3 --output Phase3SpindaBlocks\typed-v2.spc3 --report Phase3SpindaBlocks\_spc3_typed_v2_pack_report.json
```

Run CUDA rebuild offload bench:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode bench --bench-gpu --bench-limits 4 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_gpu_smoke_4_report.json
```

Run real GPU typed rebuild:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode verify --gpu-rebuild --input Phase3SpindaBlocks\typed-v2.spc3 --root Phase3SpindaBlocks --report Phase3SpindaBlocks\_spc3_gpu_verify_report.json
```

Consolidate pre-compressed shards:

```powershell
.\tools\spinda\spc3_prototype\spc3_prototype.exe --mode consolidate --consolidate-root Phase3SpindaBlocks\_spc3_shards --output Phase3SpindaBlocks\combined.spc3 --report Phase3SpindaBlocks\combined_consolidate_report.json
```

This copies compressed lane streams into one `.spc3` without unpacking or
recompressing them, and rejects duplicate lanes or incompatible shard layouts.

Levels:

| Level | Meaning |
| ---: | --- |
| `0` | Raw ordered encrypted PK3 payload. |
| `1` | Default zlib-9 full decrypted PK3 stream. Optional zstd/LZMA2. |
| `2` | Default zlib-9 template plus IV32 stream. Optional zstd/LZMA2. |
| `3` | Default zlib-9 template plus predictor bitmap/XOR exceptions. Optional zstd/LZMA2. Predictor embedded by default. |

Format spec: [SPC3_V0_1_FORMAT.md](SPC3_V0_1_FORMAT.md). Stable now: v0.1
header, lane table, levels `0..3`, strict CRC/hash verification. Active main
path now: v0.2 typed level-3 streams, GPU rebuild bench/verify/unpack, native
C++ verifier GUI, and compressed-stream consolidate. Still staged: any GPU codec
default. Still experimental: byte-rANS typed streams.

v0.2 plan: [SPC3_V0_2_TYPED_LEVEL3_PLAN.md](SPC3_V0_2_TYPED_LEVEL3_PLAN.md).
Split template, bitmap, XOR values, predictor ref/embed. Needed before rANS/FSE.

C++/ASM boundary: [SPC3_CPP_ASM_BOUNDARY.md](SPC3_CPP_ASM_BOUNDARY.md).
Most SPC3 code stays C++; ASM is only for measured CPU hot loops with narrow
byte-stable contracts.

## Main Options

| Option | Meaning |
| --- | --- |
| `--root PATH` | Lane ZIP folder. |
| `--predictor PATH` | IV32 predictor JSON. |
| `--report PATH` | JSON report output. |
| `--mode MODE` | `audit`, `pack`, `unpack`, `verify`, `inspect`, or `bench`. |
| `--input PATH` | Input `.spc3` for unpack/verify/inspect. |
| `--output PATH` | Output `.spc3` for pack. |
| `--unpack-dir PATH` | Folder for unpack output. Default output = `0xLLLL.spinda80.zip`. |
| `--unpack-format zip\|raw` | Default `zip`; `raw` writes `0xLLLL.pk3raw`. |
| `--pk3-state egg\|hatched-shiny\|hatched-not-shiny` | Unpack corpus state. Default `egg`. |
| `--trainer-index PATH` | 8192 TSV save index JSON. Needed for hatched unpack. |
| `--set-moves A,B,C,D` | Set four move ids on unpack output. |
| `--set-pp A,B,C,D` | Set four current PP bytes. |
| `--set-pp-ups A,B,C,D` | Set four PP Up counts, each `0..3`. |
| `--set-evs HP,ATK,DEF,SPA,SPD,SPE` | Set EV bytes. |
| `--set-ivs HP,ATK,DEF,SPA,SPD,SPE` | Set IV values `0..31`. |
| `--set-nickname TEXT`, `--set-ot-name TEXT` | Set Gen 3 English strings. |
| `--set-held-item N`, `--set-experience N`, `--set-friendship N`, `--set-pokerus N` | Set common growth/status bytes. |
| `--set-met-location N`, `--set-met-level N`, `--set-origin-game G`, `--set-ball N`, `--set-ot-gender G`, `--set-language N`, `--set-ability-number N` | Set origin metadata. |
| `--lane-select all\|one\|range` | Unpack lane pick. `--lane` / range flags set it. |
| `--lane HEX` | One lower PID half lane, hex. |
| `--lane-from HEX`, `--lane-to HEX` | Inclusive lane range, hex. |
| `--consolidate-root PATH` | Folder of existing `.spc3` shards for consolidate. |
| `--level N` | SPC3 level `0..3`. |
| `--codec NAME` | `auto`, `none`, `zlib`, `zstd`, `lzma2`, or experimental typed-stream `rans`. |
| `--codec-level N` | Pack levels `1..3` only: zlib `1..9`, zstd `1..22`, LZMA2 `0..9`; rANS has no level. |
| `--codec-profile NAME` | Pack levels `1..3` only: `compat` = zlib-9, `fast` = zstd-9, `small` = LZMA2-9. |
| `--typed-level3` | Pack SPC3 v0.2 typed template/bitmap/XOR streams. |
| `--typed-exceptions-only` | With typed level `3`, leave template raw and codec bitmap/XOR. |
| `--gpu-rebuild`, `--gpu` | For unpack/verify, try CUDA typed rebuild and fall back to CPU. |
| `--external-predictor` | Level `3` pack: omit embedded predictor; decode needs same `--predictor`. |
| `--limit-zips N|all` | Sorted lane count. `all`/`0` means every found ZIP. Sparse OK. |
| `--all-zips` | Use every valid lane ZIP in root. No `0x0000` needed. |
| `--bench-limits LIST` | Bench sample sizes, default `1,4,20,64`. |
| `--bench-streaming` | Big-sample bench with one lane in RAM. |
| `--bench-typed-level3` | Streaming-only typed level-3 v0.2 oracle. |
| `--bench-gpu` | CUDA/NVRTC typed level-3 rebuild offload bench. |
| `--bench-rans-fse` | Experimental rANS/FSE typed-stream bench. |
| `--bench-levels LIST` | Native codec levels, use `3` for level-3-only. |
| `--bench-codecs LIST` | Native codec filter, example `zlib-9,zstd-9`. |
| `--bench-native-codecs` | Compare native zlib/zstd/LZMA2 SPC3 files. |
| `--bench-external` | Run temporary 7z/zstd comparisons too. |
| `--no-source-compare` | Verify internal hashes only. |
| `--no-predictor` | Skip IV32 predictor model. |
| `--no-entropy-probe` | Skip zlib size probes. |
| `--self-test` | Internal parser/PK3 tests. |

## Pipeline

One lane:

1. Read ZIP bytes to RAM.
2. Parse EOCD / ZIP64 / central directory.
3. Check local headers match central headers.
4. Reject bad ZIP flags, bad sizes, bad CRC, bad ZIP64, duplicates.
5. Inflate all `65536` PK3 records to RAM.
6. Decrypt each 80-byte Gen 3 PK3.
7. Check checksum and content PID.
8. Compare constant decrypted bytes to one lane template.
9. Model IV32: predictor hit or exception bitmap + XOR.
10. Rebuild encrypted PK3.
11. Compare rebuilt bytes against original.
12. Probe compressed stream sizes in RAM.
13. Write JSON report.

## Unpack Corpus State

Default:

```text
--pk3-state egg
```

Means: keep egg bytes.

Hatched shiny:

```text
--pk3-state hatched-shiny --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

Means: pick trainer TSV = PID shiny value.

Hatched not shiny:

```text
--pk3-state hatched-not-shiny --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

Means: pick trainer TSV = next nonmatching TSV.

Make trainer index:

```powershell
dotnet run --project tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- --save-dir TSVs --trainer-id 0 --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

All three write ZIP lane files.

Inside ZIP: encrypted `.pk3`.

No loose decrypted PK3.

Need edit output too:

```text
--set-moves 33,45,0,0 --set-pp 35,30,0,0 --set-evs 1,2,3,4,5,6
```

Order:

1. rebuild PK3
2. hatch/shiny state
3. apply `--set-*`
4. checksum
5. re-encrypt
6. write ZIP

PID stays same.

Species stays Spinda.

## How To Read Report

Trust report only if:

- `ok=true`
- `hotloop_backend=x86_64_asm`
- checksum failures = `0`
- content PID mismatches = `0`
- template mismatches = `0`
- predictor round-trip mismatches = `0`
- encrypted rebuild mismatches = `0`

If `ok=false`, read first lane `errors` first. Timing/size numbers not useful
until failure understood.

## RAM Rule

Hot file handling stays in RAM:

- full ZIP bytes
- encrypted lane buffer
- one decrypted scratch PK3
- one canonical template PK3
- IV32 stream
- exception bitmap/XOR stream
- one rebuilt scratch PK3

Only JSON report written.

## Safety

Audit/bench/inspect read and write reports. Pack writes `.spc3`. Unpack writes
lane ZIPs by default. Raw `.pk3raw` only with `--unpack-format raw`. No delete.
No lane mutation. Bad lane returns exit code `2`.

SPC3 parser now rejects bad magic, bad offsets, truncation, wrong predictor
size, level mismatch, trailing bytes, and wrong CRCs.

## Compression Model

Future `.spc3` should store less data:

- lane lower PID half from filename
- upper PID half from record index
- template bytes once
- IV32 predictor exceptions, not every IV32 raw
- remaining variable streams only when rebuild need them
- no ZIP central-dir overhead

Reader rebuilds encrypted PK3 from model, then verifies.

## Optimization Rule

Use report timings. Do not guess.

| Hot thing | Next move |
| --- | --- |
| ZIP inflate hot | Use `.spc3` container to remove ZIP entry overhead. |
| entropy probe hot | Compare zlib, zstd, LZMA2, then maybe rANS/FSE. |
| decrypt/model hot | Add SIMD/assembly. |
| rebuild hot | Batch/vectorize shuffle/checksum/encrypt. |
| GPU transfer hot | Keep GPU optional until full transfer+kernel+download wins. |

## Next Work

1. Keep zstd-9 as preferred default candidate.
2. Keep rANS/FSE experimental; it works, but did not beat zstd-9 enough.
3. Keep `--gpu-rebuild` optional with CPU fallback; use it for large typed v2 batches.
4. ASM is re-enabled narrowly with PK3 shuffle ASM; use profiles before deeper
   CRC or PK3 rebuild ASM.
5. Keep the native verifier GUI thin: operator controls only, no GPU tuning.

## First Real Oracle

Expanded oracle observed once on 2026-05-07:

| Lanes | ZIP | 7z/LZMA2 | zstd | SPC3 L0 | SPC3 L1 | SPC3 L2 | SPC3 L3 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| `1` | `11,415,137` | `749,956` | `926,554` | `5,243,056` | `726,183` | `228,801` | `228,836` |
| `4` | `45,701,445` | `2,301,530` | `4,577,290` | `20,971,984` | `2,904,478` | `914,949` | `245,877` |
| `20` | `228,261,594` | `10,235,488` | `21,303,292` | `104,859,600` | `14,522,414` | `4,574,830` | `491,722` |
| `64` | `730,288,857` | `33,172,326` | `65,900,696` | `335,550,544` | `46,469,036` | `14,639,132` | `1,085,803` |

Level `3` decode speed:

| Lanes | Unpack ms | MiB/s |
| ---: | ---: | ---: |
| `1` | `7.045` | `709.764` |
| `4` | `41.582` | `480.982` |
| `20` | `185.049` | `540.398` |
| `64` | `554.815` | `576.769` |

External template-exception oracle tiny: `193,448` bytes with 7z and `229,884`
bytes with zstd at `64` lanes. That is not a full `.spc3`; it has no headers,
hashes, lane table, or embedded predictor.

Level `3` sample pack/verify/unpack passed against real lane `0x0001`.
External-predictor level `3` sample for `0x0001` is `289` bytes and verifies
when given the predictor JSON.

Native level `3` codec check through 64 lanes:

| Codec | Size | Unpack ms |
| --- | ---: | ---: |
| zlib-9 | `1,085,803` | `849.9` |
| zstd-3 | `1,084,877` | `772.0` |
| zstd-9 | `1,069,893` | `600.3` |
| zstd-19 | `1,034,563` | `641.5` |
| LZMA2-9 | `985,995` | `674.8` |

Default zlib bench reached 256 lanes: level `3` = `4,424,817` bytes,
`2385.3` ms unpack, `536.6` MiB/s. 1024 lanes exist. Use `--bench-streaming`
for next run so giant buffer does not happen.

1024-lane streaming bench now complete:

| Lanes | ZIP | SPC3 L3 | L3 unpack ms | MiB/s |
| ---: | ---: | ---: | ---: | ---: |
| `1024` | `11,707,706,577` | `18,285,512` | `4621.2` | `1107.9` |

All decode CRC mismatches `0`. Stderr empty. Memory stayed near `23 MiB` while
polling. Good low-copy proof.

1024-lane zstd-9 gate now complete:

| Codec | SPC3 L3 | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| zlib-9 | `18,285,512` | `4435.8` | `1154.2` |
| zstd-9 | `17,895,663` | `4287.9` | `1194.0` |

Policy now:

- zstd-9 = preferred default candidate
- zlib-9 = compatibility/safe, current v0.1 auto
- LZMA2-9 = smallest archive mode
- rANS/FSE = experimental after typed streams

Exception stats at 1024 lanes: `6,691,736` exceptions, avg `6,534.9` per lane,
bitmap density `0.100`, rANS/FSE table-init risk `lower`.

Typed level-3 oracle now done through 64 lanes:

| Model | Size | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| fused zstd-9 | `1,069,893` | `309.2` | `1035.0` |
| typed all-zstd-9 | `1,061,696` | `287.1` | `1114.5` |
| typed exceptions-LZMA2-9 | `967,103` | `339.8` | `941.7` |

Typed split pays on 64 lanes.

1024-lane typed gate now done:

| Model | Size | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| fused zstd-9 | `17,895,663` | `4482.9` | `1142.1` |
| typed all-zstd-9 | `17,743,980` | `4415.7` | `1159.5` |
| typed exceptions-LZMA2-9 | `15,490,371` | `5512.8` | `928.8` |

Typed split wins at 1024 lanes too. Draft v0.2 typed layout next. rANS/FSE
was tested on bitmap/XOR streams and stays experimental.

1024-lane rANS/FSE gate:

| Model | Size | Unpack ms | MiB/s |
| --- | ---: | ---: | ---: |
| typed all-zstd-9 | `17,743,980` | `4691.3` | `1091.4` |
| typed exceptions-rANS | `17,711,423` | `4926.9` | `1039.2` |

Plain read: rANS saves only `32,557` bytes, about `0.18%`, and decodes slower.
Do not make it default.

v0.2 typed file proof now exists:

| Sample | Result |
| --- | --- |
| real 4-lane typed v2 pack | `245,784` bytes |
| verify | `ok=true`, internal/source mismatches `0` |
| typed exceptions | `4,954` |

GPU offload proof now exists:

| Sample | Device | Output | XOR values | Kernel ms | Mismatches |
| --- | --- | ---: | ---: | ---: | ---: |
| real 4 lanes | RTX 4070 Ti | `20,971,520` bytes | `4,954` | `0.781` | `0` |

GPU scale now done:

| Lanes | Output bytes | Upload+kernel+download ms | CPU L3 unpack ms | Mismatches |
| ---: | ---: | ---: | ---: | ---: |
| `1024` | `5,368,709,120` | `1518.793` | `4381.556` | `0` |

Real `verify --gpu-rebuild` and `unpack --gpu-rebuild` on the 4-lane typed v2
file both passed with `0` mismatches. Non-typed/fused files fall back to CPU.
Long-running process paths cache the CUDA context/NVRTC module after first use.
The native GUI uses a hidden `spc3_prototype.exe --server` worker, so repeated
GUI runs can reuse that cache until the GUI exits, the operator cancels, or the
compressor path changes. Normal CLI runs stay one-shot and safe. Failed CUDA
probes are cached for that process too, and small/medium GPU rebuild outputs
now download with one bulk copy instead of one copy per lane.

64-lane release gate now done with v0.2 typed level `3` + `--codec-profile
fast`:

| Step | Result |
| --- | --- |
| pack | `ok=true`, size `1,061,696` bytes from `730,288,857` source ZIP bytes, build `702.146` ms |
| CPU verify | `ok=true`, internal/source/GPU lane/GPU byte mismatches `0/0/0/0`, report `7257.418` ms |
| GPU verify | `ok=true`, GPU used, fallback reason empty, bulk download, lane/byte mismatches `0/0`, report `7472.181` ms |
| CPU unpack | `ok=true`, CRC/GPU lane/GPU byte mismatches `0/0/0`, report `716.307` ms |
| GPU unpack | `ok=true`, GPU used, bulk download, CRC/lane/byte mismatches `0/0/0`, report `1061.187` ms |

CPU and GPU unpack directories both had 64 `.pk3raw` files, `335,544,320`
bytes total, and matching per-file SHA-256 hashes. That gate used raw output.
Current unpack default = `.spinda80.zip`.

Release evidence file:
`Phase3SpindaBlocks\_spc3_v02_typed_fast_real64_current_release_summary.md`.

Latest audit hardening: CUDA device buffers now free on mid-run GPU failures,
NVRTC program cleanup is exception-safe, SPC3 writer size math uses checked
overflow paths, report/output parent folders are created automatically, and the
regression suite covers nested output/report paths.

Second audit hardening: empty numeric CLI args and malformed bench-limit lists
are rejected, direct GPU bench skip/disabled reports now include fallback
reasons, and GPU staging/output buffer sizes use checked host-side arithmetic.

Third audit hardening: real `verify`/`unpack --gpu-rebuild` CPU fallback keeps
the detailed GPU reason, CRC32 chunking is safe in the byte helper itself, and
external 7z/zstd bench temp directories are unique and cleaned by RAII.

Fourth audit hardening: report summary/compare helpers now handle partial
benchmark rows with missing numeric values, and GUI cancel requests are shown
immediately while repeat cancel clicks are disabled until the CLI exits.

Fifth audit hardening: partial GPU report rows are coerced before MiB/s math,
report compare sorting tolerates mixed JSON value types, and wrong-mode CLI
flags now fail instead of being silently ignored.

Sixth audit hardening: `spc3_report_tools.py` now summarizes and compares real
pack/verify/unpack/inspect reports, including codec profile, mismatch counters,
GPU fallback status/timings, GPU cache/download state, host CRC timing, and CPU
decode-profile timings. CPU-vs-GPU release evidence compares no longer produce
empty benchmark tables.

Seventh audit hardening: real-report CSV summaries now default to release
evidence fields, and `summary --table lanes` exports the detailed lane table
when needed.

Eighth audit hardening: the GUI report summary/compare view now shows pack
round-trip mismatches, source-compare state, GPU requested/upload timing, and
CPU lane counts. Report-helper CSV output now uses stable LF line endings for
cleaner evidence diffs.

Ninth audit hardening: report-helper markdown tables now escape `|` and fold
embedded newlines inside report values so paths and GPU fallback text cannot
break evidence columns.

Tenth audit hardening: report-helper summaries now understand the original
lane-audit report schema instead of showing blank inspect columns. Audit lane
tables show failed flag, entry count, ZIP size, mismatch counters, total time,
and first error.

Eleventh audit hardening: GPU reports now distinguish not-requested, fallback,
cache-hit, cached-failure, bulk-download, and per-lane-download paths. CPU
decode profiles now report `crc_backend=zlib_crc32` and the CRC byte count.
Regression coverage checks cache reuse, disabled/forced GPU fallbacks, report
helpers, and GUI report comparison output.

Twelfth audit hardening: persistent `--server` runs reset stream formatting for
each command, so one GUI run cannot leak `fixed`/precision/base flags into the
next. Native GUI cancel is tracked during startup, and the simple summary now
shows path/config plus GPU output byte/value fields.

Fresh audit GPU proof:

| Gate | Result |
| --- | --- |
| cache smoke 1 then 4 lanes | first compile `185.464` ms, second `0.000` ms, mismatches `0` |
| real64 cache smoke 1 then 64 lanes | first compile `202.579` ms, second `0.000` ms, second `runtime_cache_hit=true`, mismatches `0/0` |
| real 20-lane typed v2 GPU verify | `ok=true`, GPU used, internal/source/lane/byte mismatches `0` |
| disabled GPU bench | status `cuda_disabled_by_environment`, reason `SPC3_DISABLE_CUDA is set` |

`unpack` and `verify` reports now include `cpu_decode_profile`, split into
stream decode, IV expansion, rebuild/encrypt, CRC, and total CPU decode time.
Use that before adding deeper ASM.

20-lane CPU profile says CRC is bigger than rebuild right now:

| Slice | ms |
| --- | ---: |
| stream decode | `3.034` |
| IV expand | `5.210` |
| rebuild/encrypt | `44.783` |
| CRC | `85.732` |

Read: targeted PK3 shuffle ASM is active now through `spc3_shuffle48_asm`.
Rebuild/encrypt is still the next PK3-specific target, but CRC is larger in
this gate. Keep `zlib_crc32` as the measured backend for now and use the new
CRC byte/timing fields before deciding whether CRC batching, hardware CRC, or
deeper PK3 rebuild/encrypt assembly should come next.

64-lane CPU profile says the same:

| Path | stream | IV | rebuild/encrypt | CRC | total |
| --- | ---: | ---: | ---: | ---: | ---: |
| CPU verify | `9.119` | `17.699` | `137.223` | `268.717` | `494.668` |
| CPU unpack | `9.357` | `18.017` | `143.118` | `275.299` | `509.434` |

Read: v0.2 typed level `3` + `fast` is the active main path. v0.1 stays
readable/compat. `auto=zlib-9`, `fast=zstd-9`, `small=LZMA2-9`. Leave
rANS/FSE experimental. Before deeper PK3 ASM, use the CPU profile CRC backend,
byte count, and timing fields to decide CRC policy deliberately.

Clean package checklist: keep source/docs/license notes, remove generated
caches, leave `.spc3`/`.spinda80.zip`/`.pk3raw`/private reports out, and do
not ship `spc3_prototype.exe` unless package intentionally includes binaries
and dependency licenses.
