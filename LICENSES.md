# Licensing Summary

Short version: mGBA fork source stays under MPL-2.0, bundled third-party code
keeps its own notices, optional external tools keep their own licenses, and
private game data must not be shipped.

## Primary Project License

| Part | License | Required File |
| --- | --- | --- |
| Upstream mGBA source | Mozilla Public License 2.0 | `LICENSE` |
| Local mGBA fork changes | Mozilla Public License 2.0 unless a file says otherwise | `LICENSE` |
| Custom Qt features | Mozilla Public License 2.0 unless a file says otherwise | `LICENSE` |
| Virtual Pad BizHawk/EmuHawk reference boundary | Local implementation remains MPL-2.0; BizHawk team original frontend reference work is MIT | `res/licenses/bizhawk.txt` |
| Spinda native CLI, native Workbench, and SPC3 compression prototype | Mozilla Public License 2.0 unless a file says otherwise | `LICENSE` |
| Python helper scripts / Flask dashboards | Mozilla Public License 2.0 unless a file says otherwise | `LICENSE` |

If you distribute binaries, give source for MPL-covered changes as required
by the MPL-2.0.

## Bundled Third-Party Code

These components are included in this clean source tree and their full local
license/notice files are present under `res/licenses/`.

| Component | Local Source | License / Notice | Full Local Notice |
| --- | --- | --- | --- |
| `blip_buf` | `src/third-party/blip_buf/` | LGPL-2.1-or-later | `res/licenses/blip_buf.txt` |
| `discord-rpc` | `src/third-party/discord-rpc/` | MIT | `res/licenses/discord-rpc.txt` |
| `inih` | `src/third-party/inih/` | BSD-3-Clause / New BSD | `res/licenses/inih.txt` |
| `libpng` | `src/third-party/libpng/` | libpng license | `res/licenses/libpng.txt` |
| `lzma` | `src/third-party/lzma/` | Public domain notice | `res/licenses/lzma-sdk.txt` |
| `mingw-std-threads` | included with Discord RPC headers | BSD-2-Clause-style notice | `res/licenses/mingw-std-threads.txt` |
| `rapidjson` | included under Discord RPC headers | MIT plus bundled third-party notices | `res/licenses/rapidjson.txt` |
| `sqlite3` | `src/third-party/sqlite3/` | Public domain blessing/notice | `res/licenses/sqlite3.txt` |
| `zlib` | `src/third-party/zlib/` | zlib license | `res/licenses/zlib.txt` |

Keep all files in `res/licenses/` with any source or binary release.

## Compression-Specific Licensing Notes

The current compression-related tools are deliberately split by dependency
boundary:

| Area | What It Uses | License / Boundary |
| --- | --- | --- |
| Native Phase 3 ZIP writers (`SpindaProjectView.cpp` and `spinda-phase3-main.cpp`) | Local MPL-2.0 source plus zlib for ZIP deflate/CRC work | Keep `res/licenses/zlib.txt` with source and binary releases. |
| SPC3 CPU prototype (`tools/spinda/spc3_prototype/`) | Local C++/assembly/test source plus zlib for ZIP inflate/CRC and in-memory entropy probes | Source stays MPL-2.0 unless a file says otherwise. It does not vendor or link 7-Zip, zstd, liblzma, PKHeX.Core, CUDA, or OpenCL today. |
| Python ZIP/canonicalization helpers | Local MPL-2.0 source plus Python standard-library `zipfile`/`zlib` modules | If bundling Python, include Python runtime/library notices. |
| LZMA/LZMA2 planning | Bundled upstream LZMA SDK source exists in `src/third-party/lzma/`; the current SPC3 prototype does not use it | Keep `res/licenses/lzma-sdk.txt` if LZMA SDK source remains in the tree. Future direct use should be called out in this file. |
| ZIP-to-7z GUI (`tools/spinda/zip_to_7z_gui/`) | Python standard library plus an external user-installed 7-Zip command-line executable | 7-Zip is not vendored here. If a package includes 7-Zip binaries/DLLs, ship the matching 7-Zip license files and notices. |
| Hatch ZIP splitter (`tools/spinda/hatch_zip_splitter/`) | Standalone .NET tool that can reference local `PKHeX.Core` and can optionally use .NET `ZipArchive` compression | Treat any distributed hatch-splitter/PKHeX.Core build as its own package. PKHeX.Core is GPL-3.0 upstream and is not linked into mGBA, the Phase 3 generator, the Workbench, or the SPC3 prototype. |

Generated `.zip`, `.7z`, `.spc3`, `.pk3`, report, save, ROM, and savestate
artifacts are data/artifacts, not source license grants. This source license
summary does not grant permission to redistribute game data or generated
Pokemon data.

## Reference Material Attribution

Reference material below is not vendored as a build dependency, but it affected
custom feature design or code and must stay credited.

| Reference | Use | License Handling |
| --- | --- | --- |
| BizHawk / EmuHawk Virtual Pad | Idea and reference code for this fork's native Qt Virtual Pad | BizHawk team original frontend work is MIT-licensed. The upstream BizHawk repository also warns about mixed third-party material in the full tree, so do not vendor the whole BizHawk repository without a separate license review. Keep `res/licenses/bizhawk.txt` with source or binary releases containing the Virtual Pad-derived feature. |

## External Dependencies Not Vendored Here

These dependencies are not copied into the clean repo. Their licenses apply
when you install them locally or ship them in a binary package.

| Dependency | Use | License Handling |
| --- | --- | --- |
| Qt | `mgba-qt` frontend | Use the license matching your Qt distribution/modules: commercial, LGPL, or GPL. If shipping Qt DLLs, include Qt notices and satisfy Qt obligations. |
| Python | Python bindings and scripts | Python Software Foundation License. Include runtime/package notices if bundling Python. |
| Flask / Pallets stack | Flask dashboards | Flask is BSD-3-Clause. Include installed package license metadata if bundling dependencies. |
| .NET SDK/runtime | Optional PKHeX validators and mass-hatching splitter | The .NET runtime repository is MIT-licensed; official packages can carry additional Microsoft/runtime notices. Include the applicable notices if bundling runtime files. |
| PKHeX.Core | Optional final semantic PK3 validator and standalone mass-hatching splitter | PKHeX upstream is GPL-3.0. PKHeX.Core is not linked into mGBA, not used by Phase 3 production workers, and not vendored here. If you distribute PKHeX.Core or binaries linked against it, include GPL-3.0 text and comply with that tool's license obligations. |
| 7-Zip command-line executable | Optional manual ZIP-to-7z LZMA/LZMA2 compaction GUI | The GUI uses only Python standard library code and invokes user-installed `7z`, `7za`, or `7zz`; 7-Zip is not vendored here. Official 7-Zip licensing states most code is GNU LGPL, some parts are BSD 3-clause or BSD 2-clause, and some parts have an unRAR license restriction. If bundling 7-Zip binaries or DLLs, include the matching 7-Zip license files and satisfy those redistribution terms. |

## Optional PKHeX Validator Boundary

`tools/spinda/phase3_pkhex_validator/` and
`tools/spinda/hatch_zip_splitter/` are standalone .NET tools. They are not
linked into mGBA, not used by hot Phase 3 production workers, and not required
to build or run the emulator or Phase 3 generator.

Do not bundle `PKHeX.Core.dll` into an mGBA release unless the release package
also handles GPL-3.0 obligations for that validator or hatch-splitter
distribution.

## Optional ZIP-to-7z GUI Boundary

`tools/spinda/zip_to_7z_gui/` is a standalone Python/Tkinter utility for manual
post-project archive compaction. It has no third-party Python package
dependency and does not vendor 7-Zip. Its source follows the repository default
MPL-2.0 license unless a file says otherwise. If you run it with a local Python
install, Python's PSF License applies to that runtime. If you distribute a
package that includes 7-Zip binaries, include the 7-Zip license material and
handle LGPL/BSD/unRAR-restriction notice obligations for that bundle.

## Data Not Covered By Source Licenses

This clean package intentionally excludes:

- ROMs
- save files
- savestates
- private input tapes
- generated `.pk3` files
- generated Spinda ZIPs
- private CSV schedules
- private live-lane folders
- local userdata

Those files are user-supplied or generated artifacts. Do not assume the
MPL-2.0 source license grants permission to redistribute game data or generated
Pokemon data.

## Practical Release Rule

For a source release, ship at least:

- `LICENSE`
- `LICENSES.md`
- `CREDITS.md`
- `res/licenses/`

For a binary release, also add one package-specific notice listing exact Qt,
Python, .NET, NuGet, and other runtime files included in that binary package.

## License References Checked

Checked on 2026-05-07 against upstream sources:

- zlib license: <https://zlib.net/zlib_license.html>
- LZMA SDK license: <https://www.7-zip.org/sdk.html>
- 7-Zip license: <https://www.7-zip.org/license.txt>
- Python license: <https://docs.python.org/3/license.html>
- .NET runtime license: <https://github.com/dotnet/runtime/blob/main/LICENSE.TXT>
- PKHeX license: <https://github.com/kwsch/PKHeX/blob/master/LICENSE>
