# SPC3 Phase 3 Predictor Inconsistency Findings

Generated: 2026-06-01

## Status Bucket

- Current status: Complete findings page for the Phase 3 IV32 predictor
  inconsistency and the finished compression-facing v8 model.
- Last verified date: 2026-06-01.
- Proven artifacts:
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.verify.json`,
  `Helper-PC-Artifacts/helper_full_corpus_65536.two-stage-runtime-rsfrlg-compact-v8.spc3`,
  and the SHA256 listed below.
- Known gaps: None for the compression exploration. The exact in-game selector
  for the alternate subsequent RNG stream remains a research question, but it
  is outside the compression scope because v8 stores that residual surface
  exactly and verifies with `0` mismatches.
- Next action: No further compression exploration is scheduled. Keep v8 as the
  final verified SPC3 compression artifact for this phase.

This page is scoped to the closed compression question: how the Phase 3 IV32
predictor fails for a structured subset of Spinda eggs, which parts can be
modeled for compression, and why the final v8 file keeps the remaining cells as
explicit residuals.

Update, 2026-05-30: the predictor issue now has a compression-facing model.
The old predictor must stay as stage 1. A runtime RS/FRLG egg-IV generator works
well as a stage-2 predictor only for old predictor misses. A new experimental
SPC3 v5 stat-delta package verifies exactly at `595,691,970` bytes. Format
details are in `markdown-files/SPC3_TWO_STAGE_RUNTIME_FORMAT.md`.
The 2026-05-31 v6 package supersedes that size result while keeping the same
predictor model.

Compression-only update, 2026-05-30: `tools/spinda/spc3_residual_optimizer.py`
now evaluates six residual encodings without solving the vblank/state-selector
cause. The current audit run covers all `65,536` lanes and uses report schema
v2. The all-lane run changed the ranking: upper-byte splitting is the strongest
single value-stream replacement at `17,518,357` bytes, or `5.189369%` of the
current `337,581,631` byte v5 value stream. If used alone, the projected package
size would be about `275,628,696` bytes. Changed-mask grouping is still strong
at `31,788,176` bytes, or `9.416441%`, with a projected package size of
`289,898,515` bytes. These are projections, not verified replacement SPC3
packages.

V6 update, 2026-05-31: `tools/spinda/spc3_v6_upper_repack.py` implements the
upper-byte split as a real SPC3 v6 package and combines it with changed-mask
grouping. The full `65,536`-lane all-corpus pack verifies exactly at
`278,311,199` bytes with `status=ok` and `mismatch_count=0`. This is
`317,380,771` bytes smaller than v5.

V7 update, 2026-05-31: `tools/spinda/spc3_v7_global_stage_repack.py` keeps the
v6 residual value section but moves stage-1 residual bits and stage-2 explicit
bits out of per-lane streams into global upper-byte stage bands. The full
`65,536`-lane all-corpus pack verifies exactly at `103,403,124` bytes with
`status=ok` and `mismatch_count=0`. This is `174,908,075` bytes smaller than
v6 and `492,288,846` bytes smaller than v5.

V8 update, 2026-06-01: `tools/spinda/spc3_v8_compact_repack.py` adds adaptive
stage-band transforms, a global template block, and per-band residual mode
selection. The full `65,536`-lane all-corpus pack verifies exactly at
`63,014,910` bytes with `status=ok` and `mismatch_count=0`. This is
`40,388,214` bytes smaller than v7 and `1,571,237,706` bytes smaller than the
v2 baseline.

## Local Evidence

- `_tmp/spc3_exception_probe/bitmaps_raw.bin`
- `_tmp/spc3_exception_probe/spc3_miss_visual_counts_cache.npz`
- `_tmp/spc3_exception_probe/pass1_provenance_analysis.json`
- `_tmp/spc3_exception_probe/pass2_counts_shape_rng_analysis.json`
- `_tmp/spc3_exception_probe/pass3_interaction_correlation_analysis.json`
- `_tmp/spc3_exception_probe/high_lane_overlap_analysis.json`
- `Phase3SpindaBlocks/_phase3_pid_second_half_iv_reference.json`
- `Helper-PC-Artifacts/helper_full_corpus_65536.spc3`
- `markdown-files/FRLG_EGG_PICKUP_SECOND_HALF_IV_AUDIT.md`

The SPC3 file is the exact corpus source. The bitmap is the reduced view of the
problem: for each lower PID half lane and each upper PID half, one bit says
whether the predictor IV32 disagreed with the corpus IV32.

## Definition

A predictor inconsistency means:

```text
actual_iv32(lane, upper_half) != predictor_iv32(upper_half)
```

The current predictor table is keyed only by the upper PID half. It works when
the same upper-half draw is followed by the same effective RNG stream used by
the reference lane. It fails when the final IV32 came from a different
subsequent stream.

The inconsistency does not mean the PK3 is corrupt. The full corpus verifies as
valid. It means the single-upper-half predictor is incomplete.

## Scale

| Quantity | Value |
| --- | ---: |
| lower-half lanes | `65,536` |
| upper-half cells per lane | `65,536` |
| total cells | `4,294,967,296` |
| predictor hits | `3,632,325,008` |
| predictor misses | `662,642,288` |
| hit rate | `84.571657%` |
| miss rate | `15.428343%` |

The misses are too structured to treat as random noise.

## Compression Result From Current Knowledge

The practical compression model is:

```text
stage 1: old embedded predictor
stage 2: runtime RS/FRLG predictor for stage-1 misses
fallback: explicit residual only for cells stage 2 still cannot explain
```

This is not "replace the old predictor." Runtime RS/FRLG alone only explains
about half of all cells. Used as a second stage, it explains most old misses.

| Quantity | Cells |
| --- | ---: |
| total cells | `4,294,967,296` |
| old predictor hits | `3,632,325,008` |
| old predictor misses | `662,642,288` |
| old misses matched by runtime RS/FRLG | `437,178,712` |
| remaining explicit cells | `225,463,576` |
| combined predicted cells | `4,069,503,720` |
| combined predicted rate | `94.750517%` |
| remaining explicit rate | `5.249483%` |

Measured SPC3 sizes:

| Format | Size |
| --- | ---: |
| original v2 typed level-3 | `1,634,252,616` |
| v3 rule bitmap | `1,442,251,334` |
| v4 two-stage runtime, XOR residual | `710,668,843` |
| v5 two-stage runtime, stat-delta residual | `595,691,970` |
| v6 two-stage runtime, upper-byte/mask-group residual | `278,311,199` |
| v7 two-stage runtime, global-stage bands plus v6 residual | `103,403,124` |
| v8 compact global-stage/template package | `63,014,910` |

The v8 file verifies with `0` mismatches against the original corpus. SHA256:
`6C70389496D893A1D40AE7D1DB28B1059E3DE2CA527AE33FFC5E96F7EF120E66`.

The current largest component is still the globalized stage-1 residual band
stream, but v8's byte-transpose transform cuts it by more than half versus v7:

| Component | zstd bytes |
| --- | ---: |
| stage-1 global upper-byte bands | `23,548,964` |
| stage-2 explicit global upper-byte bands | `9,065,572` |
| shift class records | `652,543` |
| global upper-byte/mask-group residual section | `22,299,002` |
| global template section | `162,079` |

The v8 residual remains exact over the same `225,463,576` explicit cells. The
old-predictor residual selector was implemented as a per-band option, but the
audited full corpus chose runtime-only residual mode for all bands because the
selector bitmap cost outweighed the changed-value savings.

## Observed Shape

Lane-side miss counts:

| Statistic | Misses per lane |
| --- | ---: |
| min | `0` |
| p01 | `4,104` |
| p05 | `5,696` |
| mean | `10,111.119` |
| median | `10,225` |
| p95 | `14,066.25` |
| p99 | `15,366` |
| max | `43,317` |

Upper-half miss counts:

| Statistic | Misses per upper half |
| --- | ---: |
| min | `0` |
| p01 | `0` |
| p05 | `1` |
| mean | `10,111.119` |
| median | `171` |
| p95 | `56,608` |
| p99 | `63,372.65` |
| max | `65,535` |

The upper-half distribution is especially lopsided. Many upper halves almost
never miss, while a smaller band misses across a large share of lanes.

High-repeat upper-half bands:

| Band | Upper halves | Misses | Share of all misses |
| --- | ---: | ---: | ---: |
| top 18 near-universal uppers | `18` | `1,179,611` | `0.1780%` |
| other uppers with `>= 32768` misses | `9,807` | `485,642,915` | `73.2889%` |
| medium uppers, `256..32767` misses | `20,019` | `174,131,522` | `26.2784%` |
| rare uppers, `<= 255` misses | `35,692` | `1,688,240` | `0.2548%` |

Near-universal upper halves include `0x0EB5`, `0x12A8`, `0x16D7`, `0x1F0A`,
`0x239B`, `0x2FA5`, `0x843E`, and `0xE705`. Each misses `65,535` lanes.

The strongest measured lane feature is `lane % 24`, with R-squared about
`0.544` against lane miss count. Binary alignment also matters. High-miss lanes
often have low byte `0x00` or `0x80`, and many are multiples of `0x80`,
`0x100`, `0x800`, or `0x1000`.

Top high-miss lanes:

| Rank | Lane | Misses | Rate |
| ---: | --- | ---: | ---: |
| 1 | `0x0000` | `43,317` | `66.0965%` |
| 2 | `0x8000` | `24,355` | `37.1628%` |
| 3 | `0xA000` | `23,163` | `35.3439%` |
| 4 | `0x4000` | `23,035` | `35.1486%` |
| 5 | `0xB800` | `22,477` | `34.2972%` |
| 6 | `0xD000` | `22,193` | `33.8638%` |
| 7 | `0x7000` | `22,127` | `33.7631%` |
| 8 | `0xB000` | `21,936` | `33.4717%` |
| 9 | `0x5000` | `21,914` | `33.4381%` |
| 10 | `0x5800` | `21,629` | `33.0032%` |

`0x0000` is an edge lane and should not be used as the normal case.
`0x0001` is the reference lane and has zero misses in this predictor setup.

## Overlap Findings

Similar miss counts do not guarantee similar missed upper halves. Arithmetic
class matters.

Exact-overlap examples:

| Lane A | Lane B | Shared misses | Jaccard |
| --- | --- | ---: | ---: |
| `0x8380` | `0xB080` | `18,945` | `1.0000` |
| `0xB680` | `0xE380` | `18,496` | `1.0000` |

Strong but non-exact overlap:

| Lane A | Lane B | Shared misses | Jaccard |
| --- | --- | ---: | ---: |
| `0x9B80` | `0xE200` | `17,410` | `0.8515` |
| `0x6700` | `0xD600` | `16,464` | `0.8399` |

Near-equal-count counterexamples:

| Pair | Counts | Result |
| --- | --- | --- |
| `0xAC00` vs `0x1000` | `20,966` vs `20,962` | high overlap, both `mod 24 = 16` |
| `0xAC00` vs `0xE000` | `20,966` vs `20,965` | much lower overlap, different `mod 24` |
| `0xE000` vs `0x1000` | `20,965` vs `20,962` | low overlap, different `mod 24` |

This points away from a plain "some lanes are noisy" explanation. The missed
set itself has structure.

## Source-Backed Generation Model

The FR/LG pickup path is:

```text
R0: upper PID half
R1: base IV word for HP/Atk/Def
R2: base IV word for Spe/SpA/SpD
R3: inherited stat pick 1, modulo 6
R4: inherited stat pick 2, modulo 5
R5: inherited stat pick 3, modulo 4
R6: inherited parent pick 1, modulo 2
R7: inherited parent pick 2, modulo 2
R8: inherited parent pick 3, modulo 2
```

Source references:

- `daycare.c`: `SetInitialEggData()` draws the upper PID half with
  `Random() << 16`.
- `pokemon.c`: `CreateBoxMon()` draws the two base IV words.
- `daycare.c`: `InheritIVs()` draws the three inherited stat choices and the
  three parent choices.
- `main.c`: the VBlank handler calls `Random()`.

That creates a vulnerable boundary. If an extra `Random()` call occurs after
R0 but before one of the IV-related draws, the final PID upper half stays the
same, but the IV32 can change.

## Current Hypothesis

The original leading hypothesis was:

```text
Some predictor misses are caused by one or more different post-R0 RNG paths
after the upper-PID draw and before the IV/inheritance draws.
```

The current data refines this. A normal runtime RS/FRLG post-R0 path explains
`436,968,244` old predictor misses, and small shifted classes explain another
`210,468`. That is enough for a major compression win, but it does not prove
the exact game-state transition for every mismatch.

The remaining `225,463,576` cells are the exact residual surface chosen for the
finished v8 compressor. The bitmap proves structure and the source proves
plausible RNG boundaries, but this phase no longer needs to turn that structure
into another predictor. The compressor uses what is proven by corpus comparison
and stores the rest explicitly.

## Final Bottom Line

The inconsistency is real, deterministic, and heavily structured. The current
single-upper-half predictor is useful for compression, but it is not a complete
model of FR/LG egg IV generation.

The current best SPC3 format keeps the old predictor, adds runtime RS/FRLG as a
second-stage predictor for old misses, stores small shifted-class metadata, and
stores the remaining explicit IV32 residuals globally by upper PID high byte
with changed-mask grouping.

The compression exploration is complete at v8. There are no additional
compression passes, classifier tiers, or third-stage predictor tasks for this
project phase.
