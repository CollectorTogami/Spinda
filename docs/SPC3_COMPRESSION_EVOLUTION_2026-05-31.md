# SPC3 Compression Evolution, v2 Baseline to v8

Generated: 2026-06-01

## Status Bucket

- Current status: Complete artifact-backed history of the full-corpus SPC3
  compression path from the original `1.6 GB` file to the final verified v8
  file.
- Last verified date: 2026-06-01.
- Proven artifacts:
  `Helper-PC-Artifacts/helper_full_corpus_65536.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.rule-lm24-lowbyte.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3`,
  their matching verify reports, and
  `Helper-PC-Artifacts/v7-physical-validation-provenance-20260531-104036`.
- Known gaps: None for compression. The exact in-game selector for the
  alternate IV RNG stream is not proven, but v8 models the observed corpus
  exactly and that selector is outside the compression scope.
- Next action: None for compression exploration. v8 is the final verified SPC3
  compression artifact for this phase.

## Evidence Split

### Proven

- The file sizes below come from local artifact file sizes in
  `Helper-PC-Artifacts`.
- v3, v4, v5, v6, v7, and v8 have verification reports with `status = ok` and
  `mismatch_count = 0`.
- The current v8 file is
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3`.
- v8 SHA256:
  `6C70389496D893A1D40AE7D1DB28B1059E3DE2CA527AE33FFC5E96F7EF120E66`.
- The physical validation pass materialized v7 into native-readable v2 shards,
  extracted all `65,536` lane ZIPs, and deep-validated all `4,294,967,296`
  PK3 entries with `bad=0` and `warnings=0`.

### Observed Once

- The physical extraction/validation pass was run once end to end on
  2026-05-31. Its extracted ZIP tree was later deleted to reclaim disk space,
  while the provenance reports were kept.

### Inferred

- The "why it got smaller" explanations below are inferred from the format
  shape plus report counters. The exact byte savings are proven by file sizes.

### Closed

- Native v8 decoding is optional infrastructure and is not part of the closed
  compression exploration.
- No third-stage predictor work remains scheduled for this phase. The
  remaining explicit residuals are accepted as the final exact v8 residual
  surface.

### Obsolete

- v3 through v7 are historical compression milestones. They remain useful for
  explaining the path, but v8 supersedes them as the smallest verified package.

## Size Timeline

Decimal `MB` uses 1,000,000 bytes. Binary `MiB` uses 1,048,576 bytes.

| Step | Artifact | Size bytes | MB | MiB | Saved vs previous | Saved vs v2 baseline |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| v2 baseline typed level-3 | `helper_full_corpus_65536.spc3` | `1,634,252,616` | `1,634.253` | `1,558.545` | `0` | `0` |
| v3 rule bitmap | `helper_full_corpus_65536.rule-lm24-lowbyte.spc3` | `1,442,251,334` | `1,442.251` | `1,375.438` | `192,001,282` | `192,001,282` |
| v4 two-stage runtime, XOR residual | `helper_full_corpus_65536.two-stage-runtime-rsfrlg.spc3` | `710,668,843` | `710.669` | `677.747` | `731,582,491` | `923,583,773` |
| v5 stat-delta residual | `helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3` | `595,691,970` | `595.692` | `568.096` | `114,976,873` | `1,038,560,646` |
| v6 upper-byte/mask-group residual | `helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3` | `278,311,199` | `278.311` | `265.418` | `317,380,771` | `1,355,941,417` |
| v7 global-stage bands plus v6 residual | `helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3` | `103,403,124` | `103.403` | `98.613` | `174,908,075` | `1,530,849,492` |
| v8 compact global-stage/template package | `helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3` | `63,014,910` | `63.015` | `60.096` | `40,388,214` | `1,571,237,706` |

Net result: v8 is `96.144114%` smaller than the original v2 baseline and is
about `25.93x` smaller by byte count.

## Source Corpus Scale

The underlying corpus is:

| Quantity | Value |
| --- | ---: |
| lower PID-half lanes | `65,536` |
| upper PID-half records per lane | `65,536` |
| total PK3 records | `4,294,967,296` |
| decrypted/encrypted record size | `80` bytes |
| raw 80-byte payload size | `343,597,383,680` bytes |

SPC3 never stores loose PK3 files in the compressed package. It stores enough
model data to reconstruct each `80` byte PK3 record.

## What Each Iteration Added

### v2 Baseline: Original Typed Level-3 SPC3

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.spc3`

Size: `1,634,252,616` bytes.

This was the starting full-corpus SPC3. It stored the corpus as typed level-3
lane streams with an embedded upper-half IV32 predictor. Conceptually, each
lane had:

- one `80` byte template record;
- an exception bitmap for cells where the old predictor missed;
- XOR residual values for those misses.

Measured predictor scale:

| Quantity | Cells |
| --- | ---: |
| total corpus cells | `4,294,967,296` |
| old predictor hits | `3,632,325,008` |
| old predictor misses | `662,642,288` |
| old predictor hit rate | `84.571657%` |
| old predictor miss rate | `15.428343%` |

This file was already a huge reduction from raw lane ZIPs, but its main
remaining cost was the old-miss bitmap plus per-miss IV32 XOR values.

### v3: Rule-Predicted Miss Bitmap

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.rule-lm24-lowbyte.spc3`

Size: `1,442,251,334` bytes.

Added:

- a global rule table keyed by `lane low byte + lane % 24 + upper half`;
- reconstruction of the old-miss bitmap as:

```text
old_miss_bitmap = rule_bitmap[lane_lowbyte, lane_mod24] XOR residual_bitmap
```

Report counters:

| Quantity | Value |
| --- | ---: |
| actual old predictor misses | `662,642,288` |
| residual bitmap set bits after rule XOR | `152,121,859` |
| rule table groups | `768` |
| rule table raw size | `6,291,456` bytes |
| rule table compressed size | `743,428` bytes |

Why it helped:

The miss bitmap had structure by lane class and upper half. v3 did not solve
the IV mismatch itself, but it made the bitmap far cheaper by storing only the
rule errors instead of the entire old exception shape.

What did not change:

The IV residual values were still essentially the old per-miss XOR value stream,
so v3 only saved `192,001,282` bytes.

### v4: Two-Stage Runtime RS/FRLG Predictor

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg.spc3`

Size: `710,668,843` bytes.

Added:

- kept the old embedded predictor as stage 1;
- added a runtime RS/FRLG egg-IV generator as stage 2, used only for old
  predictor misses;
- added sparse shift-class records for non-normal runtime matches;
- stored explicit residual values only for cells stage 2 still could not
  explain.

The model uses start RNG `0x2B0C94C1`, base runtime positions
`[2, 3, 5, 6, 7, 8, 9, 10]`, normal class `0`, and extra-advance classes up to
`+2` around the IV/stat/parent inheritance call positions.

Coverage:

| Quantity | Cells |
| --- | ---: |
| old predictor misses entering stage 2 | `662,642,288` |
| normal runtime matches | `436,968,244` |
| shifted runtime matches | `210,468` |
| remaining explicit cells | `225,463,576` |
| combined predicted cells | `4,069,503,720` |
| combined predicted rate | `94.750517%` |

Why it helped:

This was the major semantic win. The compressor stopped storing IV32 values
for most old predictor misses because the decoder could compute them from the
runtime RS/FRLG model. The file dropped by `731,582,491` bytes from v3.

### v5: Stat-Delta Residual Encoding

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-statdelta.spc3`

Size: `595,691,970` bytes.

Added:

- changed the remaining explicit IV32 residual values from raw 32-bit XORs to
  IV-stat deltas;
- stored a changed-stat mask plus packed changed `5` bit IV stat values;
- retained the v4 two-stage predictor and v3 rule-residual bitmap.

Report counters:

| Quantity | Value |
| --- | ---: |
| remaining explicit cells | `225,463,576` |
| changed 5-bit stat values | `374,307,190` |
| high-bit mismatches | `0` |
| v4 compressed value stream | `452,558,537` bytes |
| v5 compressed value stream | `337,581,631` bytes |

Why it helped:

Most remaining explicit IV32 differences did not need a full 32-bit value. They
could be represented by which IV stats changed and the changed `5` bit stat
values. This saved `114,976,873` bytes from v4.

### v6: Global Upper-Byte / Mask-Group Residual Section

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-upper-maskgroup-v6.spc3`

Size: `278,311,199` bytes.

Added:

- moved the remaining explicit value data out of per-lane streams into a global
  residual section;
- split residual value data by the high byte of the upper PID half;
- grouped records by changed-stat mask inside each upper-byte band;
- kept per-lane template, stage-1 residual bitmap, stage-2 explicit bitmap, and
  shift records.

Report counters:

| Quantity | Value |
| --- | ---: |
| remaining explicit cells | `225,463,576` |
| global residual records | `225,463,576` |
| global residual raw bytes | `459,409,171` |
| global residual compressed stream | `22,287,710` bytes |
| full global residual section | `22,297,978` bytes |

Why it helped:

The residual values were much more compressible when grouped by upper-half high
byte and changed-stat mask. This removed another `317,380,771` bytes from v5.

### v7: Global Stage Bands

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.spc3`

Size: `103,403,124` bytes.

Added:

- kept the v6 global upper-byte/mask-group residual section;
- moved stage-1 residual bits out of per-lane streams into global upper-byte
  stage bands;
- moved stage-2 explicit bits out of per-lane streams into global upper-byte
  stage bands;
- moved shifted runtime class records into one compressed global stream;
- reduced each lane stream to the template substream only.

Verified v7 stream costs:

| Component | Bytes |
| --- | ---: |
| all lane streams | `7,340,032` |
| templates inside lane streams | `5,242,880` |
| global stage section | `66,498,907` |
| stage-1 global band streams | `55,378,660` |
| stage-2 explicit global band streams | `10,449,220` |
| shift global stream | `652,543` |
| global residual section | `22,297,978` |

Why it helped:

v6 had already made the explicit values cheap, so the remaining large cost was
stage state stored in many lane-local streams. v7 made that state global and
upper-byte-banded, then compressed the bands together. That saved another
`174,908,075` bytes from v6.

### v8: Compact Global Stage and Template Package

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3`

Size: `63,014,910` bytes.

Added:

- adaptive stage-band transforms, choosing raw or byte-transposed storage per
  upper-byte band before zstd;
- a global `SPC3V8T1` template block instead of `65,536` per-lane template
  streams;
- zero-byte per-lane streams, leaving lane table entries as metadata only;
- a `SPC3V8R1` residual section that can choose runtime-only or
  old-predictor-selected residual mode per upper-byte band;
- an umbrella CLI/GUI target for `v8`.

Verified v8 stream costs:

| Component | Bytes |
| --- | ---: |
| all lane streams | `0` |
| global template section | `162,079` |
| global stage section | `33,287,611` |
| stage-1 global band streams | `23,548,964` |
| stage-2 explicit global band streams | `9,065,572` |
| shift global stream | `652,543` |
| global residual section | `22,299,002` |

Why it helped:

The v7 stage bands were row-major by lane. v8 tries a byte-transposed view for
each band, and `421` of `512` bands compressed smaller that way. The template
block also dropped from `5,242,880` lane-local bytes to a `162,079` byte global
section. The residual mode selector was implemented and audited, but
runtime-only won all residual bands in the verified full-corpus run, so v8
keeps the same compressed residual value stream size as v7.

## Validation-Only Materialized v2 Bridge

Artifact:
`Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-global-stage-v7.materialized-v2.spc3`

Size: `3,205,494,483` bytes.

This is not a compression milestone. It is intentionally larger than both the
v7 file and the original v2 baseline.

Why it exists:

- The current native SPC3 extractor understands older typed level-3 v2 streams,
  but not v7 global-stage streams.
- A Python bridge materialized v7 back into native-readable typed-v2 lane
  streams.
- The bridge used uncompressed substreams for compatibility, so the file grew
  to about `3.2 GB`.

Materialization report:

| Quantity | Value |
| --- | ---: |
| output size | `3,205,494,483` bytes |
| lane stream bytes | `3,198,974,400` |
| template bytes | `5,242,880` |
| bitmap bytes | `536,870,912` |
| value bytes | `2,650,569,152` |
| payload CRC mismatches | `0` |

The physical validation pass then split this materialized file into `128`
native-readable shards, extracted all lane ZIPs, and deep-validated:

| Quantity | Value |
| --- | ---: |
| lane ZIPs extracted | `65,536` |
| status sidecars | `65,536` |
| PK3 entries read | `4,294,967,296` |
| central directory entries cross-checked | `4,294,967,296` |
| bad ZIPs | `0` |
| warnings | `0` |

The extracted ZIP tree and temporary shards were deleted after validation. The
kept provenance folder is:

```text
Helper-PC-Artifacts/v7-physical-validation-provenance-20260531-104036
```

## Short Version

The compression path was:

```text
v2: old predictor + per-lane miss bitmap + per-miss XOR values
v3: add lane/mod24/lowbyte/upper rule table for miss bitmap residuals
v4: add runtime RS/FRLG stage-2 predictor, store values only for remaining misses
v5: encode remaining IV32 values as changed IV stat masks + 5-bit stat values
v6: move remaining values into global upper-byte/mask-group residual bands
v7: move stage bitmaps/classes into global upper-byte stage bands
v8: add adaptive stage transforms, global templates, and zero-byte lane streams
```

The main size drops were not from a stronger generic compressor. They came from
turning repeated game-structure into decoder-side rules, then storing only the
parts those rules could not explain.

Compression exploration is closed at v8: `63,014,910` bytes, SHA256
`6C70389496D893A1D40AE7D1DB28B1059E3DE2CA527AE33FFC5E96F7EF120E66`, with
`status=ok` and `mismatch_count=0`.
