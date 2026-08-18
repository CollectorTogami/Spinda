# Spinda Large Corpus Idea Bank

## Status Bucket

- Current status: Planning and idea bank for unusual things to do with a large
  Phase 3 Spinda PK3/lane corpus after generation and validation.
- Last verified date: 2026-05-07.
- Proven artifacts: Current project docs describe the Phase 3 lane ZIP shape,
  PK3 validation path, Workbench PID/pattern search, hatch splitter, SPC3
  prototype, and ZIP-to-7z compaction tool.
- Known gaps: These are mostly proposals. They are not implemented unless a
  linked tool or doc later says so.
- Next action: Pick a small number of ideas that help validation,
  preservation, or user-facing exploration before spending time on novelty
  projects.

## Ground Rules

`Inferred`: The corpus is valuable because it is large, structured, and nearly
exhaustive across the target PID/lane space. That makes it useful for more than
storage. It can become a validation set, compression lab, search index, visual
atlas, and long-term preservation object.

Boundaries:

- Keep raw ROMs, saves, savestates, generated PK3 files, and generated lane
  ZIPs out of public source packages.
- Prefer metadata-only derived indexes for anything public-facing.
- Keep every destructive or rewriting idea behind dry-run and backup checks.
- Treat the CPU validator as the correctness reference unless a later doc
  promotes another verifier.

## High-Value Strange Ideas

| Idea | What It Does | Why It Is Interesting | Risk / Prerequisite |
| --- | --- | --- | --- |
| Corpus fingerprint ledger | Hash every lane ZIP, every PK3 entry, and every semantic record field into a Merkle-style manifest. | Gives proof of completeness and lets damaged/moved files be repaired or audited without rescanning everything. | Needs stable manifest format and chunked hashing so it can resume. |
| Metadata-only mirror | Export PID, lane, IV32, nature, gender, ability, PSV, spot traits, hashes, and validation status without PK3 payloads. | Allows sharing research/search data without shipping generated Pokemon files. | Must review data boundary before publishing. |
| Visual Spinda atlas | Render a tiny thumbnail for every PID or selected subsets, then build zoomable image tiles. | Turns the corpus into a browsable visual map instead of a folder of ZIPs. | Full atlas is huge; use pyramid tiles and lazy generation. |
| Pattern rarity index | Score every Spinda by visual traits: heart-like, diagonal, symmetric, face/eye overlaps, sparse, dense, clustered, edge-heavy. | Makes the Workbench searchable by human visual taste, not just PID. | Trait scoring needs manual review to avoid silly rankings. |
| Nearest-neighbor search | Given one Spinda pattern, find visually similar patterns across the corpus. | Useful for finding families of related-looking Spindas and alternate candidates. | Needs a compact feature vector per PID. |
| Corpus anomaly detector | Flag records whose hashes, checksums, decoded fields, template bytes, or IV predictor behavior look unlike neighbors. | Finds subtle corruption or generator bugs that normal pass/fail may miss. | Must distinguish real edge cases from bad data. |
| Compression oracle | For each lane and stream type, store measured size under ZIP, solid LZMA2, zstd, SPC3 levels, and GPU codecs. | Turns the corpus into a benchmark suite for compression decisions. | Needs repeatable commands and versioned codec settings. |
| Format fuzz seed bank | Use real lane headers, central directories, ZIP64 fields, PK3 records, and corrupt variants as fuzz seeds. | Hardens validators and future `.spc3` readers against weird archive inputs. | Must keep fuzz outputs separate from trusted corpus. |
| Record-level parity shards | Build parity/recovery blocks across lane files or SPC3 chunks. | Makes cold storage more resilient than plain backups. | Adds complexity; use only after primary backup strategy is stable. |
| Semantic diff engine | Compare two generated corpora by lane, PID, raw bytes, decrypted fields, and visual traits. | Proves whether a regeneration is identical, equivalent, or meaningfully different. | Needs stable normalized field extraction. |

## Search And Index Ideas

| Idea | Description | Useful Output |
| --- | --- | --- |
| PID locator index | Map every PID to lane ZIP, entry offset, PSV, TSV match options, and visual score. | Fast Workbench lookups without opening ZIPs. |
| Bloom-filter lane presence | Store compact membership filters per lane or shard. | Quick “might contain PID/trait” answers before disk reads. |
| SQLite/DuckDB catalog | Load all metadata rows into a queryable local database. | SQL queries for traits, sizes, validation status, and run provenance. |
| Columnar Parquet export | Export metadata-only columns for analytics tools. | Faster aggregate scans than JSON/CSV. |
| Spot-trait inverted index | Map trait tags to PID lists. | Instant searches like `dense+left-heavy+heart-ish`. |
| TSV/PSV pairing index | Map each PSV to all candidate eggs and matching TSV saves. | Mass-hatching planning, samples, and proof sets. |
| PID neighborhood graph | Link PIDs that differ by small visual or field distance. | Explore pattern families and local clusters. |
| Query snapshots | Save named query results as frozen manifests. | Reproducible “best 100 candidates” lists. |

## Visual And Human-Facing Ideas

| Idea | Description | Why It Helps |
| --- | --- | --- |
| Zoomable corpus wall | Tile thumbnails into a deep-zoom viewer by lane and upper-half. | Makes completeness and pattern distribution visible. |
| Trait heatmaps | Show where rare traits cluster across lane IDs or PID upper halves. | Helps see RNG/model structure. |
| Top-N galleries | Generate galleries for cutest, weirdest, most symmetric, densest, sparsest, or most face-like patterns. | Turns search results into reviewable pages. |
| Before/after hatching proof board | Show egg record, shiny hatch, and non-shiny control for selected examples. | Good final proof material. |
| Animated lane sweep | Render frames that sweep upper PID half inside one lane. | Visualizes how spots move as PID changes. |
| Similarity explorer | Click one Spinda and see nearest patterns plus why they match. | Useful for choosing final showcase candidates. |
| Corpus screensaver mode | Local-only display that cycles through validated patterns. | Low-risk, fun use of the corpus after validation. |
| “Impossible-looking” detector | Search for patterns that look manually designed: lines, corners, clusters, face marks, or logo-like shapes. | Highlights outliers that ordinary scoring may miss. |

## Validation And Forensics Ideas

| Idea | Description | Notes |
| --- | --- | --- |
| Completeness certificate | Emit a signed or hash-stamped report proving all expected lanes and entries exist. | Good final milestone artifact. |
| Multi-validator quorum | Require agreement from ZIP validator, PKHeX validator, SPC3 rebuild validator, and metadata indexer. | Reduces single-tool blind spots. |
| Lane health timeline | Record when each lane was generated, validated, transferred, backed up, compacted, and rechecked. | Helps diagnose drive or transfer problems. |
| Hash drift watch | Periodically rescan a rotating subset of files and compare against the ledger. | Early warning for storage damage. |
| Damaged ZIP triage | For corrupt ZIPs, report whether central directory, local header, deflate payload, CRC, or PK3 semantics failed. | Makes recovery targeted. |
| Salvage map | If a lane ZIP is partly readable, list recoverable entry ranges and exact missing PIDs. | Better than treating the whole lane as lost. |
| Canary corpus | Keep a tiny representative corpus with known good and known bad cases. | Fast regression tests for future tools. |
| Cross-drive audit | Compare same corpus across backup disks by hash and metadata, not just filenames. | Catches silent copy mistakes. |

## Compression And Storage Ideas

| Idea | Description | When To Try |
| --- | --- | --- |
| SPC3 tiered container | Store raw-pack, decrypt-solid, IV32-stream, and predictor-exception modes in one versioned format. | After CPU writer/reader is stable. |
| Mixed-codec lanes | Choose codec per stream or per lane based on measured ratio/speed. | After compression oracle data exists. |
| Deduplicated template store | Store common decrypted templates once across lane groups. | If real lanes prove enough template reuse. |
| Delta-to-reference lanes | Store each lane as a delta against a nearby or canonical lane. | If lane-to-lane similarity is strong. |
| Content-addressable archive | Store chunks by hash, then build manifests pointing to chunks. | Good for dedupe and repair, but more complex. |
| Erasure-coded cold pack | Add recovery shards over SPC3 chunks. | After the final corpus is stable. |
| Storage-class layout | Keep hot metadata on SSD, cold lane/SPC3 data on HDD, and parity/ledger on separate media. | Practical for daily use. |
| Progressive archive | Store thumbnails and metadata first, then lazy-fetch full PK3/lane data. | Useful for a GUI or public-safe demo. |

## Research And Benchmark Ideas

| Idea | Description | Output |
| --- | --- | --- |
| RNG structure maps | Visualize how IV32, PID halves, nature, and traits distribute across lanes. | Heatmaps and anomaly lists. |
| Compression benchmark corpus | Package metadata and synthetic-safe samples as a repeatable benchmark. | Useful for SPC3, zstd, LZMA2, GPU codec comparisons. |
| Validator performance lab | Time ZIP validation, PKHeX validation, SPC3 rebuild, and Workbench scans across sample sizes. | Helps choose production defaults. |
| GPU offload lab | Use selected lane batches to test decrypt/rebuild, CRC, exception packing, and nvCOMP codecs. | Decides whether GPU work is worth it. |
| Filesystem stress study | Compare NTFS/ReFS/exFAT/network share behavior for many archive files and large manifests. | Better storage recommendations. |
| Bit-field entropy atlas | Measure entropy per decrypted PK3 byte/field across the whole corpus. | Guides custom compression design. |
| Trait distribution paper trail | Record how often each visual trait appears and how scores were computed. | Makes visual rankings defensible. |
| Synthetic public corpus | Generate fake PK3-like records with the same shape but no game data. | Allows public benchmark/testing without private payloads. |

## Workbench And GUI Ideas

| Idea | Description | Implementation Sketch |
| --- | --- | --- |
| Corpus dashboard | Add total size, lane count, validation age, hash ledger age, and backup status to the Workbench. | Metadata-only scan and status JSON. |
| Visual search page | Query by traits, PID, PSV, nature, ability, and similarity. | Backed by SQLite/DuckDB or compact binary index. |
| Candidate review queue | Let the user star, tag, reject, and export interesting Spindas. | Local JSON list keyed by PID and corpus hash. |
| Proof-pack builder | Build a small local package with metadata, thumbnails, reports, and selected examples. | Must exclude private payloads unless explicitly local-only. |
| Restore assistant | Given a missing/corrupt lane, show which backup or parity shard can recover it. | Uses ledger plus backup inventory. |
| Compression explorer | Show estimated size under ZIP, 7z, SPC3, and other codecs for selected lanes. | Reads compression oracle reports. |
| Corpus map mode | Render lane grid colored by validation, size, trait density, or backup status. | Good for spotting holes. |
| Read-only public demo mode | Serve only metadata, thumbnails, and aggregate stats. | No PK3 payloads, saves, ROMs, or private paths. |

## Automation Ideas

| Idea | Description | Guardrail |
| --- | --- | --- |
| Nightly metadata refresh | Rebuild only lightweight indexes and dashboards. | Never rewrite lane ZIPs during refresh. |
| Rotating deep audit | Deep-validate a subset every day/week until the whole corpus is covered again. | Store audit seed and lane list. |
| Backup-before-transform gate | Require fresh backup proof before canonicalization, compaction, or SPC3 conversion. | Hard fail if backup ledger is stale. |
| Duplicate detector | Find duplicate lane files, duplicate PK3 entries, and duplicate metadata rows. | Report-only by default. |
| Space forecast | Predict storage needs for raw ZIP, 7z, SPC3, indexes, thumbnails, parity, and backups. | Update after each benchmark batch. |
| Auto-quarantine | Move newly detected corrupt outputs into a quarantine folder only after explicit operator approval. | Avoid surprise destructive behavior. |
| Rebuild manifest | For every derived file, record source hashes and command line. | Makes derived artifacts reproducible. |
| “Do not touch” lock file | Block tools from modifying the trusted corpus root unless a specific unlock flag exists. | Protects validated data. |

## Public-Safe Derivatives

`Inferred`: The most useful public-safe outputs are probably not the PK3 files.
They are derived facts.

Possible public-safe artifacts after review:

- Corpus shape: lane count, expected entry count, validation status model.
- Tooling docs and source code.
- Synthetic corpus with fake records.
- Aggregate statistics, such as trait distributions and compression ratios.
- Thumbnails only if the project owner is comfortable with generated Pokemon
  visuals being shown.
- Metadata rows that exclude raw PK3 bytes and private save/ROM paths.
- Compression benchmark results without redistributing payloads.

Keep private unless explicitly approved:

- ROMs, saves, savestates, raw PK3 files, generated lane ZIPs, full SPC3
  payload containers, private CSV schedules, and machine-local paths.

## Unusual Showcase Ideas

| Idea | Description |
| --- | --- |
| Corpus museum | A local static site telling the generation story with counters, maps, thumbnails, and validation proof. |
| “Spinda constellations” | Group patterns by visual similarity and render them like clusters. |
| Pattern roulette | Local tool that returns a random validated Spinda matching loose human preferences. |
| Rarity badges | Assign badges like `edge-heavy`, `near-symmetric`, `face-like`, `dense-center`, or `lonely-spot`. |
| Lane postcards | Generate one image per lane summarizing its most interesting patterns and stats. |
| Compression race board | Track codecs by ratio, encode speed, decode speed, RAM, and correctness failures. |
| Corpus soundtrack triggers | Map trait clusters or lane ranges to simple tones for a novelty visualization. |
| Time-lapse of production | Replay lane completion order, worker activity, transfer, validation, and backup states. |

## First Ten Worth Building

`Planned`: Best order if this becomes real work:

1. Metadata-only corpus catalog.
2. Corpus fingerprint ledger.
3. Workbench read-only dashboard for corpus health.
4. Trait scoring and inverted trait index.
5. Thumbnail generator with cache.
6. Visual search / top-N review queue.
7. Compression oracle for ZIP, 7z/LZMA2, zstd, and SPC3 candidates.
8. Completeness certificate with reproducible hashes.
9. Semantic diff engine for regenerated or restored lanes.
10. Public-safe synthetic benchmark corpus.

This order favors preservation and validation first, then exploration, then
novelty.

## Bad Idea Bin

These are tempting but should be avoided unless the constraints change:

- Uploading the full generated PK3 corpus publicly.
- Rewriting trusted lane ZIPs as part of an exploratory tool.
- Building a GPU-only or proprietary-only archive format.
- Making a visual search tool that depends on loose PK3 extraction.
- Treating one compression run as final proof without decoding and byte-compare
  validation.
- Storing indexes without source hashes, making them impossible to trust later.
- Mixing synthetic, corrupt, and trusted real corpus samples in one folder.
- Letting a GUI mutate the trusted corpus without a dry-run report and backup
  proof.

## Decision Rule

`Planned`: Any corpus idea should pass at least one of these tests before it
gets implementation time:

- It improves preservation or recovery.
- It improves validation confidence.
- It makes the corpus easier to search or understand.
- It produces public-safe derived evidence.
- It directly informs SPC3 compression, GPU offload, or storage decisions.
- It creates a clear operator workflow that does not risk trusted data.
