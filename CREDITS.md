# Credits And Attributions

Current status: clean-source attribution inventory.

Last verified date: 2026-05-07.

Proven artifacts: source folders and license files listed below exist in this
clean tree.

Known gaps: private production artifacts, ROMs, saves, savestates, generated
PK3 files, generated lane ZIPs, private CSV schedules, and private input tapes
are intentionally not included and not credited here as distributable source.

Next action: update this file whenever bundled third-party code, optional
validators, or major custom features are added or removed.

## Upstream Project

- mGBA: credited to Jeffrey Pfau and upstream mGBA contributors.
- License: Mozilla Public License 2.0.
- Local file: `LICENSE`.
- Scope: emulator core, Qt frontend base, platform code, build system, and
  original documentation.

## Local Fork / Spinda Project Changes

- Local custom fork changes: Spinda project maintainers.
- License: MPL-2.0 unless a file says otherwise.
- Scope:
  - Qt Custom Features menu
  - Audio killswitch
  - No-render mode
  - Input Tapes UI
  - Virtual Pad UI and Python bridge, based on the BizHawk/EmuHawk Virtual Pad
    idea and reference code
  - Worker instance helpers
  - Savestate memory cache controls
  - Windows dark chrome helper
  - Spinda Project Qt runner
  - Phase 3 headless CLI
  - Flask command center and Spinda validation tools
  - Native Spinda Workbench
  - SPC3 compression prototype and assembly hot loops
  - ZIP/canonicalization and optional ZIP-to-7z helper tools

### Contributor Credits

- Shawrkie: credited for the Spinda Phase 3 dashboard and helper-side
  additions that help compute Spindas outside the main emulator UI. Summary:
  - Flask command-center dashboard for live Phase 3 progress, worker controls,
    SSE/API status, ledger counts, health warnings, ETA/throughput panels, and
    local/LAN operator visibility.
  - Helper-side lane orchestration around the command-center ledger: claim,
    heartbeat, finish/fail, stale-claim cleanup, subordinate/coordinator roles,
    and active-lane reporting that avoids duplicate work.
  - Worker launch and helper wrappers that connect the native Phase 3 worker
    pool to the dashboard, including Windows command-center launch scripts,
    helper client status files, and Linux helper-node support scripts.
  - Helper-only deployment packaging notes for assisted machines, including
    local read-only helper UI, coordinator configuration, native worker
    auto-discovery, and operator-visible scaling/status behavior.

See `docs/MGBA_CUSTOM_CHANGES_AND_FEATURES.md` for feature detail.

## Reference Credits

These projects informed custom features but are not vendored as build
dependencies in this clean source package.

| Project | Credited Author / Project | License / Notice | Scope |
| --- | --- | --- | --- |
| BizHawk / EmuHawk Virtual Pad | BizHawk team / TAS Emulators | MIT for BizHawk team original frontend work; upstream repository warns about mixed third-party material in the full tree. See `res/licenses/bizhawk.txt`. | Virtual game pad idea and reference code for this fork's native Qt Virtual Pad. |

## Bundled Third-Party Source

These components are included in `src/third-party/` and retain their own
licenses. Keep the matching files in `res/licenses/` with source and binary
releases.

| Component | Credited Author / Project | License / Notice | Local Notice |
| --- | --- | --- | --- |
| `blip_buf` | Shay Green | LGPL-2.1-or-later | `res/licenses/blip_buf.txt` |
| `discord-rpc` | Discord, Inc. | MIT | `res/licenses/discord-rpc.txt` |
| `inih` | Ben Hoyt | BSD-3-Clause / New BSD | `res/licenses/inih.txt` |
| `libpng` | PNG Reference Library Authors, Cosmin Truta, Glenn Randers-Pehrson, Andreas Dilger, Guy Eric Schalnat / Group 42, Inc. | libpng license | `res/licenses/libpng.txt` |
| `lzma` | Igor Pavlov / LZMA SDK | Public domain notice | `res/licenses/lzma-sdk.txt` |
| `mingw-std-threads` | Mega Limited | BSD-2-Clause-style notice | `res/licenses/mingw-std-threads.txt` |
| `rapidjson` | THL A29 Limited, Tencent, Milo Yip | MIT, with bundled third-party notices including BSD and JSON License text | `res/licenses/rapidjson.txt` |
| `sqlite3` | SQLite authors | Public domain blessing/notice | `res/licenses/sqlite3.txt` |
| `zlib` | Jean-loup Gailly and Mark Adler | zlib license | `res/licenses/zlib.txt` |

## External Build And Runtime Dependencies

These are not vendored in this clean source package. Credit and include their
license texts if you ship them in a binary package.

| Dependency | Use | License Family |
| --- | --- | --- |
| Qt | Qt frontend build/runtime | Commercial or open-source GPL/LGPL, depending on chosen Qt distribution/modules |
| Python | Python bindings and scripts | Python Software Foundation License |
| Flask / Pallets stack | Command center dashboards | BSD-3-Clause for Flask; preserve package metadata for dependencies |
| .NET SDK/runtime | Optional PKHeX validators and mass-hatching splitter build/run | MIT for the .NET runtime source; preserve Microsoft/runtime notices for bundled packages |
| PKHeX.Core | Optional final semantic PK3 validator and standalone mass-hatching splitter | GPL-3.0 in upstream PKHeX repository; not vendored here |
| 7-Zip command-line executable | Optional manual ZIP-to-7z LZMA/LZMA2 compaction GUI | Not vendored here; 7-Zip uses GNU LGPL for most code, BSD notices for some code, and an unRAR license restriction for some code |

## Data And Trademark Boundary

This source package does not include Nintendo, Game Freak, Creatures, or Pokemon
Company ROMs, save data, art assets, or generated Pokemon data. Pokemon names
and related terms are used only to describe interoperability/research targets.

Do not ship private game data with this source tree.
