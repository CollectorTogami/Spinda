# SPC3 v0.1 Format

SPC3 v0.1 is the frozen compatibility container for Phase 3 Spinda PK3 lanes.
It stores whole lane streams, one lane table entry per source ZIP, and enough
hashes to prove byte-for-byte rebuild of ordered 80-byte PK3 records.

The active development path is SPC3 v0.2 typed level `3`. Keep v0.1 readable
and deterministic, but do not add new stream-layout features to v0.1.

## Caveman TLDR

v0.1 readable forever.

Do not break old files.

v0.2 typed level `3` now main new path.

v0.1 = compatibility checkpoint, not active compression frontier.

Bad headers, bad offsets, bad CRCs, bad codec flags: fail clean.

## File Layout

All integers are little-endian.

```text
SPC3 file =
  80-byte header
  optional predictor stream
  lane table: lane_count * 96 bytes
  lane data streams: contiguous, table ordered
```

## Header

| Offset | Size | Field | Stable |
| ---: | ---: | --- | --- |
| `0x00` | 4 | Magic bytes `SPC3` | Yes |
| `0x04` | 4 | Format version, currently `1` | Yes |
| `0x08` | 4 | SPC3 level, `0..3` | Yes |
| `0x0C` | 4 | Lane count | Yes |
| `0x10` | 4 | Records per lane, currently `65536` | Yes |
| `0x14` | 4 | Record size, currently `80` | Yes |
| `0x18` | 4 | Container flags | Yes |
| `0x1C` | 4 | Header size, currently `80` | Yes |
| `0x20` | 8 | Predictor stream offset, currently `80` | Yes |
| `0x28` | 8 | Predictor stream size | Yes |
| `0x30` | 8 | Lane table offset | Yes |
| `0x38` | 8 | Lane table entry size, currently `96` | Yes |
| `0x40` | 8 | Lane data offset | Yes |
| `0x48` | 8 | Lane data size | Yes |

Container flag `0x00000001` means the predictor table is embedded. The embedded
predictor is only valid for level `3`. No other container flags are stable in
v0.1.

## Predictor Stream

When embedded, the predictor stream is zlib-compressed raw predictor data:

```text
65536 little-endian u32 IV32 predictor values
```

When the predictor is not embedded, `predictor_size` must be `0`. Level `3`
decode then requires the same external predictor JSON used at pack time.

## Lane Table Entry

Each lane table entry is exactly 96 bytes.

| Offset | Size | Field | Stable |
| ---: | ---: | --- | --- |
| `0x00` | 4 | Lane ID / low PID half | Yes |
| `0x04` | 4 | SPC3 level, must match header level | Yes |
| `0x08` | 4 | Stream kind, currently equal to level | Yes |
| `0x0C` | 4 | Entry flags: codec ID/settings | Yes |
| `0x10` | 8 | Source ZIP byte size | Yes |
| `0x18` | 8 | Source ZIP CRC32 stored in u64 | Yes |
| `0x20` | 8 | Source ZIP FNV-1a64 | Yes |
| `0x28` | 8 | Original ordered PK3 payload CRC32 stored in u64 | Yes |
| `0x30` | 8 | Rebuilt ordered PK3 payload CRC32 stored in u64 | Yes |
| `0x38` | 8 | Lane stream file offset | Yes |
| `0x40` | 8 | Lane stream stored byte size | Yes |
| `0x48` | 8 | Uncompressed model byte size | Yes |
| `0x50` | 8 | Predictor match count | Yes |
| `0x58` | 8 | Predictor exception count | Yes |

Entry flags pack codec metadata:

| Bits | Meaning |
| --- | --- |
| `0..7` | Codec ID |
| `8..15` | Codec level or preset |
| `16..23` | Codec settings byte, currently `0` |
| `24..31` | Stream flags, reserved and currently must be `0` |

Codec IDs:

| ID | Name | v0.1 Status |
| ---: | --- | --- |
| `0` | Legacy auto | Read-only compatibility for old prototype files with `flags=0` |
| `1` | none | Stable |
| `2` | zlib | Stable default for levels `1..3` |
| `3` | zstd | Experimental but readable/writable by current prototype |
| `4` | lzma2 | Experimental but readable/writable by current prototype |
| `5` | rANS/FSE | Reserved in v0.1; experimental only inside v0.2 typed substreams |

New v0.1 files write explicit codec flags. Readers must still accept old
`flags=0` entries as legacy: level `0` means `none`, and levels `1..3` mean
`zlib-9`.

Current CLI policy keeps `--codec auto` identical to that compatibility rule:
level `0` uses `none`, and levels `1..3` use zlib-9. `--codec-profile` and
`--codec-level` are writer shortcuts/settings for pack levels `1..3`, not
format fields. Level `0` is raw and rejects codec profiles and codec levels:

| Profile | Concrete codec |
| --- | --- |
| `compat` | zlib-9 |
| `fast` | zstd-9 |
| `small` | LZMA2-9 |

## Lane Streams

The lane data section is contiguous. Each entry stream must start exactly where
the previous one ended. Gaps, overlaps, truncation, and trailing bytes are
errors.

| Level | Uncompressed model | Default codec |
| ---: | --- | --- |
| `0` | Raw ordered encrypted PK3 payload, `65536 * 80` bytes | none |
| `1` | Full ordered decrypted PK3 payload, `65536 * 80` bytes | zlib-9 |
| `2` | One 80-byte decrypted template plus `65536` IV32 values | zlib-9 |
| `3` | One 80-byte decrypted template plus 8192-byte exception bitmap plus u32 XOR exception values | zlib-9 |

Level `1` rebuild encrypts every decrypted record.

Level `2` rebuilds PID, IV32, checksum, and encryption from the template plus
raw IV32 stream.

Level `3` rebuilds IV32 from the predictor table, flips bitmap-marked entries
with the XOR exception values, then rebuilds checksum and encryption.

Level `3` model sizes are bounded. The minimum is `80 + 8192` bytes. The
maximum is `80 + 8192 + 65536 * 4` bytes, because a lane can have at most one
u32 XOR exception per PID upper half. Readers reject level-3 model sizes beyond
that range before attempting stream decode.

## Stable

- Magic, version field, header size, table entry size, lane count, and records
  per lane validation.
- Levels `0..3` and their current uncompressed model meanings.
- Contiguous predictor/table/data layout.
- Per-entry hashes, stream offsets, stream sizes, and strict CRC verification.
- Codec metadata packing in the table entry flags field.
- Legacy `flags=0` decoding for old prototype files.

## Experimental

- zstd and LZMA2 backend selection. They are implemented for measurement, but
  zlib-9 remains default.
- External predictor references for small level-3 packs.
- SPC3 v0.2 typed level-3 streams: template stream, exception bitmap stream,
  exception XOR stream, and predictor reference/embed stream.
- rANS/FSE coding for v0.2 typed bitmap/XOR exception streams.
- Expanded assembly, SIMD, GPU batch rebuild/CRC/exception packing beyond the
  current targeted hot loops. The current Win32 GUI is the shippable operator
  surface for the narrow SPC3 scope and follows the v0.2 active-path policy.

rANS/FSE is gated on corpus measurement. The 1024-lane typed gate showed it
round-trips, but it saved only about `0.18%` versus typed zstd-9 and decoded
slower, so it remains experimental.

## v0.2 Typed Level 3

The current prototype can also write format version `2` when `--typed-level3`
is used. Version `2` keeps the v0.1 80-byte header and 96-byte lane table, but
sets `stream_kind=4` for typed level `3`.

Each typed lane stream starts with three 32-byte substream table entries:

| Substream | Contents | Codec policy |
| --- | --- | --- |
| template | 80-byte decrypted template | `auto`/`compat` zlib-9, `fast` zstd-9 |
| exception bitmap | 8192-byte predictor miss bitmap | `auto`/`compat` zlib-9, `fast` zstd-9, experimental rANS |
| XOR values | u32 XOR values for bitmap hits | `auto`/`compat` zlib-9, `fast` zstd-9, experimental rANS |

Version `2` is the active development path and the main format path for new
typed level `3` work. v0.1 stays readable for compatibility; v0.2 typed zstd-9
via `--codec-profile fast` is the recommended balanced candidate. `auto`
remains zlib-9 for compatibility until a future release deliberately changes
that policy.

## Compatibility Note

A v0.1 reader must reject unknown container flags, unknown codec IDs, reserved
stream flags, bad offsets, bad sizes, wrong CRCs, unsupported levels, and
trailing bytes cleanly.

Any future change that widens the header, widens the lane table, changes
`stream_kind`, or splits level `3` into multiple physical streams must either
bump `version` or use a new table entry size that old readers reject. The
current typed-stream and rANS/FSE work should be treated as a v0.2 design until
benchmarks prove it is worth freezing.
