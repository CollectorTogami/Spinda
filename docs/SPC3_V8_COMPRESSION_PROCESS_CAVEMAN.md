# SPC3 v8 Compression Process

Generated: 2026-06-01

Goal: turn `65,536` lane ZIP corpus into one exact SPC3 v8 corpus:

```text
Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3
```

Final verified size:

```text
63,014,910 bytes
```

## 1. Start With Lane ZIP Corpus

Input corpus:

```text
65,536 lane ZIPs
```

Meaning:

```text
one ZIP = one lower PID-half lane
one ZIP entry = one upper PID-half Spinda
65,536 lanes * 65,536 entries = 4,294,967,296 PK3 records
```

Each PK3 record is `80` bytes after normal Pokemon structure handling.

## 2. Read Each Spinda Record

Tool reads every lane, every upper PID half.

For each record:

```text
read encrypted PK3
decrypt PK3
read PID
read checksum
read 48-byte encrypted data body after decrypt
recover exact 80-byte PK3 shape
```

Need exact output later. Compression can model data, but verify must rebuild same record bytes.

## 3. Keep One Template Per Lane

Many bytes in lane record shape repeat.

Compressor keeps one `80` byte template per lane.

Template means:

```text
fixed record skeleton for that lane
later fill variable IV/PID/checksum bits back in
```

v8 does not store these templates inside each lane stream.

v8 writes all templates into one global template block:

```text
SPC3V8T1
```

Raw template bytes:

```text
5,242,880 bytes
```

Compressed global template section:

```text
162,079 bytes
```

## 4. Build Old Predictor View

For each upper PID half, there is old IV32 predictor value.

Compressor checks:

```text
does actual IV32 equal old_predictor[upper_half]?
```

If yes:

```text
no IV data stored for that cell
decoder can recreate it from predictor
```

If no:

```text
cell becomes old predictor miss
```

Old predictor miss bitmap exists conceptually for every lane:

```text
65,536 bits per lane
```

## 5. Replace Lane Bitmap With Rule Bitmap Plus Residual

Instead of storing full miss bitmap per lane, compressor uses learned rule table.

Rule key:

```text
lane low byte
lane mod 24
upper PID half
```

Rule predicts whether cell should miss old predictor.

Then compressor stores only difference:

```text
stage1_residual_bitmap = actual_miss_bitmap XOR rule_bitmap
```

This means decoder later does:

```text
actual_miss_bitmap = rule_bitmap XOR stage1_residual_bitmap
```

Rule table stored globally:

```text
SPC3RUL1
```

## 6. Try Runtime RS/FRLG IV Predictor On Misses

For old predictor misses only, compressor tries runtime RS/FRLG egg IV generator.

Input to runtime model:

```text
upper PID half
fixed R0 family
known parent IVs
known inheritance order model
small allowed offset classes
```

If runtime model gives actual IV32:

```text
cell no longer needs explicit IV value
```

If runtime model matches only with shifted class:

```text
store small shift class record
```

Shift record:

```text
u16 lane
u16 upper PID half
u8  class id
```

Shift records stored globally.

If runtime model still fails:

```text
cell remains explicit fallback
```

## 7. Store Explicit Fallback Bitmap

Compressor needs mark which old predictor misses still need explicit IV values.

It builds:

```text
explicit_full_bitmap
```

Meaning:

```text
1 = this upper PID half still needs explicit fallback IV32
0 = old predictor or runtime model can recreate IV32
```

This bitmap is also stored as global stage data, not per lane.

## 8. Split Stage Bitmaps Into Upper-Byte Bands

Stage data has two bitmap families:

```text
stage1 residual bits
explicit fallback bits
```

Each bitmap is split by upper PID high byte:

```text
256 bands for stage1 residual
256 bands for explicit fallback
512 total stage bands
```

Each band groups same upper-byte region across all lanes.

This makes patterns line up better before compression.

## 9. Choose Best Stage Band Shape

For each of 512 stage bands, v8 tries storage shapes:

```text
raw row-major bytes
byte-transposed bytes
bit index-list form
```

Then each candidate gets compressed.

Compressor keeps smallest one.

Full run choices:

```text
raw:            91 bands
byte-transpose: 421 bands
bit-index-list: 0 bands
```

Stage section written as:

```text
SPC3V8S1
```

Verified stage section size:

```text
33,287,611 bytes
```

## 10. Store Remaining Explicit IV Values

After predictor stages, remaining explicit cells:

```text
225,463,576 cells
```

For each explicit cell, compressor stores enough to rebuild actual IV32.

IV32 is six 5-bit stat fields:

```text
HP
Atk
Def
Spe
SpA
SpD
```

Compressor compares actual IV32 against baseline IV32.

Baseline normally:

```text
runtime RS/FRLG normal IV32 for that upper half
```

It stores:

```text
changed stat mask
changed 5-bit stat values
```

No need store unchanged stats.

## 11. Bucket Explicit IV Values By Upper Byte

Explicit residual values get split into 256 upper-byte bands.

For each band:

```text
group records by changed-stat mask
pack changed IV values as 5-bit stream
compress band
```

Residual section written as:

```text
SPC3V8R1
```

Verified residual section size:

```text
22,299,002 bytes
```

## 12. Test Optional Old-Predictor Residual Selector

v8 can choose, per upper-byte residual band:

```text
runtime baseline only
or
best of runtime baseline and old predictor baseline per cell
```

If old predictor baseline helps enough, store selector bitmap.

Full corpus result:

```text
runtime-only won every band
selector bitmap not used in final bands
```

Reason:

```text
old predictor helped some cells
but selector bitmap cost more than saved value bytes
```

So final residual remains runtime-baseline mask-group data.

## 13. Make Lane Table

SPC3 still needs lane metadata table.

One entry per lane:

```text
65,536 table entries
```

Each entry stores lane identity and source CRC/hash metadata.

v8 lane stream data:

```text
0 bytes per lane
```

Lane table points to empty data stream.

All useful data now global:

```text
predictor
rule table
stage section
residual section
template section
```

## 14. Write Final SPC3 v8 Container

File layout:

```text
SPC3 header
embedded predictor
runtime model metadata
SPC3V8G1 global header
SPC3RUL1 rule table
SPC3V8S1 stage section
SPC3V8R1 residual section
SPC3V8T1 template section
lane table
empty lane data area
```

Stream kind:

```text
10
```

SPC3 version:

```text
8
```

## 15. Verify Full Rebuild

Verifier reads v8 file.

For every lane and upper half:

```text
rebuild miss bitmap from rule + stage1 residual
apply old predictor hits
apply runtime predictor hits
apply shifted runtime classes
apply explicit residual IV values
rebuild 80-byte PK3 from template
compare against source corpus record
```

Required result:

```text
all 4,294,967,296 records match
0 mismatches
```

Current verified report:

```text
Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json
```

Status:

```text
ok
```

Mismatch count:

```text
0
```

## 16. Final Size

Current verified v8 corpus:

```text
63,014,910 bytes
```

SHA256:

```text
6C70389496D893A1D40AE7D1DB28B1059E3DE2CA527AE33FFC5E96F7EF120E66
```

Big remaining pieces:

```text
stage section:    33,287,611 bytes
residual section: 22,299,002 bytes
template section:    162,079 bytes
lane metadata:      fixed table cost
```

Meaning:

```text
file now mostly stores hard-to-model stage bits and remaining explicit IV facts
```
