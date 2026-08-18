# SPC3 Two-Stage Runtime Format

Generated: 2026-06-01

## Status Bucket

- Current status: Complete SPC3 v8 format note for the two-stage
  runtime predictor plus compact global stage bands, global template block, and
  adaptive upper-byte/mask-group residual package.
- Last verified date: 2026-06-01.
- Proven artifacts:
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.pack.json`,
  and `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json`.
- Known gaps: None for the Python v8 pack/verify/exact-reconstruction path.
  Native C++ decoder support is outside the closed compression exploration.
- Next action: None. v8 is the final verified SPC3 compression artifact for
  this phase.

This page documents the experimental two-stage SPC3 transform implemented in
`tools/spinda/spc3_two_stage_runtime_repack.py`,
`tools/spinda/spc3_v6_upper_repack.py`,
`tools/spinda/spc3_v7_global_stage_repack.py`, and
`tools/spinda/spc3_v8_compact_repack.py`.

The Python script packs and verifies this format against the original v2 typed
level-3 SPC3. Native C++ decoder support is optional follow-up infrastructure,
outside the compression scope.

## Current Artifact

| Item | Value |
| --- | ---: |
| source SPC3 | `Helper-PC-Artifacts/helper_full_corpus_65536.spc3` |
| v8 output | `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3` |
| size | `63,014,910` bytes |
| size, decimal | `0.063014910 GB` |
| size, binary | `0.058687208 GiB` |
| SHA256 | `6C70389496D893A1D40AE7D1DB28B1059E3DE2CA527AE33FFC5E96F7EF120E66` |
| final verify report | `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json` |
| verify status | `ok`, `0` mismatches |

## Size Comparison

| Format | Size | Savings vs original |
| --- | ---: | ---: |
| original v2 typed level-3 | `1,634,252,616` | `0` |
| v3 rule bitmap | `1,442,251,334` | `192,001,282` |
| v4 two-stage runtime, XOR residual | `710,668,843` | `923,583,773` |
| v5 two-stage runtime, stat-delta residual | `595,691,970` | `1,038,560,646` |
| v6 two-stage runtime, upper-byte/mask-group residual | `278,311,199` | `1,355,941,417` |
| v7 two-stage runtime, global-stage bands plus v6 residual | `103,403,124` | `1,530,849,492` |
| v8 compact global-stage/template package | `63,014,910` | `1,571,237,706` |

v5 saves `114,976,873` bytes over v4, or `16.178685%` of the v4 file.
v6 saves another `317,380,771` bytes over v5, or `53.279344%` of the v5
file.
v7 saves another `174,908,075` bytes over v6, or `62.846222%` of the v6
file.
v8 saves another `40,388,214` bytes over v7, or `39.058988%` of the v7
file.

## Predictor Model

The decoder keeps the old embedded predictor as stage 1.

```text
stage 1:
  predicted_iv32 = old_predictor[upper]

stage 2, only when stage 1 misses:
  try runtime RS/FRLG egg IV32 model from the fixed upper-half R0 state

explicit fallback:
  only store cells unmatched after stage 2
```

This preserves the old predictor. Runtime RS/FRLG is not a replacement for it.

Measured coverage:

| Quantity | Cells |
| --- | ---: |
| total corpus cells | `4,294,967,296` |
| old predictor hits | `3,632,325,008` |
| old predictor misses | `662,642,288` |
| runtime RS/FRLG matches among old misses | `437,178,712` |
| explicit residual after stage 2 | `225,463,576` |

The combined predictor covers `4,069,503,720` cells, or `94.750517%`.

## V8 Container Shape

v8 writes:

| Field | Value |
| --- | --- |
| SPC3 version | `8` |
| level | `3` |
| flags | embedded predictor, rule bitmap, two-stage runtime |
| stream kind | `10` |
| residual encoding | `selected-mask-group` container, with per-band runtime-only fallback |
| stage layout | `adaptive-bitmaps` |

Global streams after the embedded predictor:

1. `SPC3S2P1` model metadata stream.
2. `SPC3V8G1` v8 global header.
3. `SPC3RUL1` lane/mod24/lowbyte/upper rule table stream.
4. `SPC3V8S1` stage section with adaptive per-band transforms.
5. `SPC3V8R1` residual section with per-band runtime-vs-selected mode choice.
6. `SPC3V8T1` global template block.

v8 removes all per-lane stream bytes. Every lane table entry points to an empty
data stream; the stage bits, residual values, shifted class records, and
templates are global.

Verified v8 stream costs:

| Component | Bytes |
| --- | ---: |
| lane streams, all lanes | `0` |
| global template section | `162,079` |
| global template compressed stream | `162,043` |
| global stage section | `33,287,611` |
| stage-1 global band streams | `23,548,964` |
| stage-2 explicit global band streams | `9,065,572` |
| shift global stream | `652,543` |
| global residual section | `22,299,002` |

Stage-band transform choices:

| Transform | Bands |
| --- | ---: |
| raw | `91` |
| byte-transpose | `421` |
| bit-index-list | `0` |

The residual layer also tested the old-predictor/runtime selector per upper
byte band. For this full corpus, runtime-only mask-group won every band:
`225,463,576` records used runtime mode and `0` used selected mode. The forced
selected stream would have been `25,061,010` bytes versus `22,287,710` bytes
for runtime-only.

The full all-lane v8 verify report is
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json`;
it reports `status = ok` and `mismatch_count = 0`.

## V7 Container Shape

v7 writes:

| Field | Value |
| --- | --- |
| SPC3 version | `7` |
| level | `3` |
| flags | embedded predictor, rule bitmap, two-stage runtime |
| stream kind | `9` |
| residual encoding | `upper-mask-group` |
| stage layout | `split-bitmaps` |

Global streams after the embedded predictor:

1. `SPC3S2P1` model metadata stream.
2. `SPC3V7G1` v7 global header.
3. `SPC3RUL1` lane/mod24/lowbyte/upper rule table stream.
4. `SPC3V7S1` stage section with 256 upper-byte bands for stage-1 residual bits
   and 256 upper-byte bands for stage-2 explicit bits.
5. `SPC3V6R1` residual section with 256 upper-byte value bands.

Each lane has one typed substream:

| Kind | Meaning |
| ---: | --- |
| `1` | template record |

v7 reconstructs the old-miss bitmap the same way v6 does:

```text
old_miss_bitmap = rule_bitmap[lane_lowbyte, lane_mod24] XOR stage1_residual_bitmap
```

The difference is storage placement. v6 stored stage-1 residual, stage-2
explicit bits, and shift records per lane. v7 stores stage-1 residual bits and
stage-2 explicit bits as global streams split by upper PID high byte. Shifted
runtime class records are stored as one compressed global stream:

```text
u16 lane
u16 upper_pid_half
u8  class_id
```

Class `0` is normal runtime RS/FRLG and is implicit.

Verified v7 stream costs:

| Component | Bytes |
| --- | ---: |
| lane streams, all lanes | `7,340,032` |
| templates inside lane streams | `5,242,880` |
| global stage section | `66,498,907` |
| stage-1 global band streams | `55,378,660` |
| stage-2 explicit global band streams | `10,449,220` |
| shift global stream | `652,543` |
| global residual section | `22,297,978` |

The full all-lane v7 verify report is
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.verify.json`;
it reports `status = ok` and `mismatch_count = 0`.

## V6 Container Shape

v6 writes:

| Field | Value |
| --- | --- |
| SPC3 version | `6` |
| level | `3` |
| flags | embedded predictor, rule bitmap, two-stage runtime |
| stream kind | `8` |
| residual encoding | `upper-mask-group` |

Global streams after the embedded predictor:

1. `SPC3S2P1` model metadata stream.
2. `SPC3V6G1` v6 global header.
3. `SPC3RUL1` lane/mod24/lowbyte/upper rule table stream.
4. `SPC3V6R1` residual section with 256 upper-byte bands.

Each lane has four typed substreams:

| Kind | Meaning |
| ---: | --- |
| `1` | template record |
| `2` | stage-1 residual old-miss bitmap |
| `4` | stage-2 explicit bitmap over old stage-1 misses |
| `5` | shifted runtime class records |

The remaining explicit IV32 data is no longer stored in each lane stream. It is
stored globally by the high byte of the upper PID half. Within each upper-byte
band, v6 stores a per-record changed-stat mask followed by packed 5-bit stat
values grouped by that mask.

v5 wrote:

| Field | Value |
| --- | --- |
| SPC3 version | `5` |
| level | `3` |
| flags | embedded predictor, rule bitmap, two-stage runtime |
| stream kind | `7` |
| residual encoding | `stat-delta` |

Global streams after the embedded predictor:

1. `SPC3S2P1` model metadata stream.
2. `SPC3RUL1` lane/mod24/lowbyte/upper rule table stream.

Each lane had five typed substreams:

| Kind | Meaning |
| ---: | --- |
| `1` | template record |
| `2` | stage-1 residual old-miss bitmap |
| `4` | stage-2 explicit bitmap over old stage-1 misses |
| `5` | shifted runtime class records |
| `3` | residual value stream |

The v5/v6 old-miss bitmap is not stored directly. It is reconstructed as:

```text
old_miss_bitmap = rule_bitmap[lane_lowbyte, lane_mod24] XOR stage1_residual_bitmap
```

The stage-2 explicit bitmap is ordered over the `1` bits of `old_miss_bitmap`.

In v5/v6, shift records are sparse records for non-normal runtime matches:

```text
u16 old_miss_ordinal
u8  class_id
```

Class `0` is normal runtime RS/FRLG and is implicit.

## Stat-Delta Residual

v4 stored remaining explicit cells as 32-bit XOR values:

```text
actual_iv32 XOR runtime_normal_iv32
```

v5 stores the same cells as changed IV stat fields relative to
`runtime_normal_iv32`.

For each lane residual value stream:

1. Six bitplanes mark changed fields for explicit residual records.
2. Then six packed 5-bit value streams store the actual values for changed
   fields, in stat order.

Stat order:

```text
HP, Atk, Def, Spe, SpA, SpD
```

The high bits are not stored. The packer validates that bits 30 and 31 match the
runtime-normal value for every residual record. Full-corpus result:
`stat_delta_high_bit_mismatches = 0`.

Full-corpus residual value stream:

| Encoding | Raw bytes | zstd bytes |
| --- | ---: | ---: |
| v4 XOR residual | `901,854,304` | `452,558,537` |
| v5 stat-delta residual | `403,383,850` | `337,581,631` |

Changed-field distribution for the `225,463,576` remaining explicit cells:

| Changed stat fields | Records |
| ---: | ---: |
| `0` | `0` |
| `1` | `107,669,794` |
| `2` | `100,184,445` |
| `3` | `10,830,246` |
| `4` | `1,750,903` |
| `5` | `3,394,972` |
| `6` | `1,633,216` |

## V6/V7 Upper-Byte Residual

v6 and v7 keep the same stage-1 and stage-2 model, but move the remaining
explicit cells into a single global residual section split into 256 bands by
`upper_pid_half >> 8`. The verified full-corpus packs use `upper-mask-group`:

```text
for each upper-byte band:
  store one 6-bit changed-stat mask per explicit record
  for each mask value 0..63:
    store the changed 5-bit IV stat values for records with that mask
```

This combines the strongest all-lane value-stream idea, upper-byte splitting,
with the strongest record-local layout, changed-mask grouping. The all-lane
optimizer projected `275,628,696` bytes for upper-byte splitting alone. The
actual v6 package verifies at `278,311,199` bytes, `2,682,503` bytes above that
projection because the implemented format carries the real v6 headers, lane
stream shape, and mask-group residual section. v7 then keeps the same residual
section and reduces the stage/container cost, verifying at `103,403,124` bytes.

Verified v6 residual stream:

| Quantity | Value |
| --- | ---: |
| remaining explicit records | `225,463,576` |
| global residual raw bytes | `459,409,171` |
| global residual zstd bytes | `22,287,710` |
| global residual section bytes | `22,297,978` |
| value-stream ratio vs v5 stat-delta stream | `6.602169%` |

The full all-lane v6 verify report is
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.verify.json`;
it reports `status = ok` and `mismatch_count = 0`.

## Verification Commands

Compile check:

```powershell
python -m py_compile tools\spinda\spc3_two_stage_runtime_repack.py
python -m py_compile tools\spinda\spc3_v6_upper_repack.py
python -m py_compile tools\spinda\spc3_v7_global_stage_repack.py
```

Full v7 pack and verify:

```powershell
python tools\spinda\spc3_v7_global_stage_repack.py `
  --mode pack-verify `
  --progress-every 4096 `
  --output Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3 `
  --report Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.verify.json
```

Full v6 pack and verify:

```powershell
python tools\spinda\spc3_v6_upper_repack.py `
  --mode pack-verify `
  --progress-every 4096 `
  --value-layout upper-mask-group `
  --output Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3 `
  --report Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.verify.json
```

Previous v5 pack and verify:

```powershell
python tools\spinda\spc3_two_stage_runtime_repack.py `
  --mode pack-verify `
  --progress-every 4096 `
  --output Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3 `
  --report Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.verify.json
```

Backward v4 XOR verification after the stat-delta patch:

```powershell
python tools\spinda\spc3_two_stage_runtime_repack.py `
  --mode verify `
  --input Helper-PC-Artifacts\helper_full_corpus_65536.spc3 `
  --output Helper-PC-Artifacts\helper_full_corpus_65536.two-stage-runtime-rsfrlg.spc3 `
  --report _tmp\spc3_exception_probe\two_stage_runtime_rsfrlg_old_v4_reverify_after_patch.json `
  --progress-every 16384
```

## Closed Optimization Audit

After v7, the largest costs were global stage-1 bands and explicit IV32
residual values:

| Component | zstd bytes |
| --- | ---: |
| stage-1 global upper-byte bands | `55,378,660` |
| global upper-byte/mask-group residual section | `22,297,978` |
| stage-2 explicit global upper-byte bands | `10,449,220` |
| templates | `5,242,880` |
| shift records | `652,543` |

v8 completed the compression-only audit by adding adaptive stage-band
transforms, a global template block, zero-byte lane streams, and per-band
residual mode selection. The project accepts the remaining explicit cells as
the final exact residual surface.

## Residual Optimizer Pass

`tools/spinda/spc3_residual_optimizer.py` evaluates the six compression-only
ideas for the remaining explicit cells without needing the vblank/state-selector
cause. It keeps the verified v5 package unchanged and estimates replacement
encodings for the current value stream and stage-2 explicit bitmap.

Audit update, 2026-05-30: report schema v2 now records full-package projection
fields directly, avoids stale scratch-bucket appends, streams bucket compression
instead of reading each bucket into memory, bounds open bucket handles for
full-corpus safety, and rejects unencodable 16-bit RLE range candidates instead
of letting them price as zero bytes.

Full-corpus all-lane run:

| Quantity | Value |
| --- | ---: |
| lanes | `65,536` |
| old predictor misses | `662,642,288` |
| runtime normal matches | `436,968,244` |
| runtime shift matches | `210,468` |
| explicit residual | `225,463,576` |
| current value stream | `337,581,631` bytes |
| current stage-2 bitmap | `78,338,269` bytes |
| optimizer report | `Helper-PC-Artifacts/spc3_residual_optimizer_all_lanes_v2.json` |
| independent v5 verifier report | `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.all-lanes-rerun-verify.json` |

Full-corpus component results:

| Idea | Component bytes | Ratio vs current component | Full-file projection if used alone |
| --- | ---: | ---: | ---: |
| split stat streams | `92,608,369` | `27.432882%` | `350,718,708` bytes |
| lane mod24 split | `89,680,140` | `26.565468%` | `347,790,479` bytes |
| lane mod24 + low-byte split | `207,585,247` | `61.491867%` | `465,695,586` bytes |
| upper-byte split | `17,518,357` | `5.189369%` | `275,628,696` bytes |
| residual class table | `113,452,884` | `33.607541%` | `371,563,223` bytes |
| record mask/value split | `66,988,002` | `19.843497%` | `325,098,341` bytes |
| changed-mask group split | `31,788,176` | `9.416441%` | `289,898,515` bytes |
| zstd dictionary, global | `302,695,996` | `89.666015%` | `560,806,335` bytes |
| zstd dictionary, lane mod24 | `299,783,408` | `88.803235%` | `557,893,747` bytes |
| nearest-baseline selector | `87,798,051` | `26.007947%` | `345,908,390` bytes |
| per-lane bitmap representation choice | `78,338,244` | `99.999968%` | `595,691,945` bytes |

These are not cumulative. Most value-stream ideas are alternate encodings for
the same explicit records. The strongest single full-corpus component result is
upper-byte splitting. Changed-mask grouping remains the strongest record-local
restructure; v6 combines it with upper-byte splitting in the actual package.
The bitmap representation choice is real but tiny on the full corpus.

## V7 Pack Audit

`tools/spinda/spc3_v7_global_stage_repack.py` implements the verified v7
package. It keeps the v6 value residual section and moves stage state into
global upper-byte bands. A 256-lane dry run verified at `2,016,892` bytes. A
second dry run with wider `--max-extra 4` classes verified at `2,019,070` bytes,
so the full run kept the default `--max-extra 2` model.

The all-lane run used `upper-mask-group` values plus the `split-bitmaps` stage
layout. Pack time was `386.4` seconds; verify time was `537.1` seconds. Final
artifacts:

| Artifact | Value |
| --- | --- |
| v7 SPC3 | `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3` |
| v7 SPC3 SHA256 | `8F8653B8965CE1493796E61AB31BF79377C1D8627BF850CE64908D36769E619A` |
| pack report SHA256 | `22EB0257E907429870320B915C647763CEB020B983ECBCA7F04D5D7DE375DFCE` |
| verify report SHA256 | `6313F96F242C007B3162FBE5D6FA8AD839FC8846B03AF74CB73D676750D4C173` |
| verify result | `status=ok`, `mismatch_count=0` |

The audit found and fixed a metadata-report typing issue before the full run.
The verified transform reduces v6 lane streams from `248,747,026` bytes to
`7,340,032` bytes and reduces stage bitmap storage from `234,491,585` bytes to
`65,827,880` bytes.

## V6 Pack Audit

`tools/spinda/spc3_v6_upper_repack.py` implements the verified v6 package. A
256-lane dry run compared the three implemented residual layouts:

| Layout | 256-lane package size |
| --- | ---: |
| `upper-statdelta` | `2,764,988` bytes |
| `upper-record-mask` | `2,569,410` bytes |
| `upper-mask-group` | `2,496,088` bytes |

The all-lane run used `upper-mask-group`. Pack time was `408.5` seconds; verify
time was `418.7` seconds. Final artifacts:

| Artifact | Value |
| --- | --- |
| v6 SPC3 | `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3` |
| v6 SPC3 SHA256 | `38DADBAF4CCFA3CFF02CFDC231B69C2C0748C50690D339061008FD05BEB57678` |
| pack report SHA256 | `D615AF1BA72FF33CFCCE00F3FBE4542BDF1B69C3F4CF3EF26FDC324836563D60` |
| verify report SHA256 | `7A0C4BEC83751CB7BE2015DE7278CEE12505CC6FB0E72297571372931D234FCC` |
| verify result | `status=ok`, `mismatch_count=0` |

The audit found and fixed a packer performance issue before the full run: bucket
files were being reopened too often during upper-byte writes. The fixed script
uses a bounded handle cache large enough for all actual/baseline band files and
keeps verification baseline writes separate from decoded residual writes.
