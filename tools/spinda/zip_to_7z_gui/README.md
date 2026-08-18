# ZIP to 7z LZMA GUI

## Status Bucket

- Current status: Manual post-project archive compaction tool.
- Last verified date: 2026-05-05.
- Proven artifacts: `zip_to_7z_gui.py` and
  `src/platform/python/tests/examples/test_zip_to_7z_gui.py`.
- Known gaps: No full production corpus has been converted yet. The tests cover
  planning, ZIP preflight, unsafe-path rejection, command construction,
  listfile handling, and docs, but do not run a large 7-Zip conversion.
- Next action: Run manually after final ZIP outputs are complete and backed up.

## Purpose

Use this GUI after the project is done to convert many `.zip` archives into
`.7z` archives using the 7z container and LZMA-family compression. It is meant
for cold storage and sharing prep, not live Phase 3 production.

The tool never deletes input ZIPs. It writes each archive as a temporary
`.tmp` file and moves it into place only after 7-Zip exits successfully.

## Run

```powershell
python .\tools\spinda\zip_to_7z_gui\zip_to_7z_gui.py
```

GUI fields:

- `Input ZIP folder`: folder containing `.zip` files.
- `Output 7z folder`: folder where matching `.7z` files are written.
- `7-Zip exe`: path to `7z.exe`, `7za.exe`, or `7zz`.
- `Recursive`: preserve subfolders from input under output.
- `Overwrite existing .7z`: replace existing converted archives.
- `Method`: `lzma2` by default, or strict `lzma`.

Output naming preserves layout:

```text
Input\lane\block.zip -> Output\lane\block.7z
```

## Conversion Model

For each source ZIP:

1. Validate that the ZIP central directory opens and has entries.
1. Reject unsafe member paths before extraction: empty names, absolute paths,
   Windows drive paths, parent-directory traversal, NUL bytes, and newline
   names.
1. Extract to a temporary folder beside the target `.7z`.
1. Write a UTF-8 7-Zip listfile of top-level extracted entries.
1. Run 7-Zip with `-t7z`, `-m0=lzma2` or `-m0=lzma`, `-mx=9`,
   `-md=64m`, `-ms=on`, and `-mmt=on`.
1. Atomically replace the final `.7z` only after success.

This is a real archive conversion, not a `.zip` file wrapped inside a `.7z`.
The progress console shows entry counts, unpacked size, output size, and the
final `.7z` size as a percentage of the original ZIP size.

## Error Handling And Performance Notes

- Job discovery resolves input and output roots once, then preserves relative
  paths under the output directory.
- If the output folder is inside the input folder, recursive scans skip that
  output subtree so a later run does not convert its own converted files.
- The listfile contains only top-level extracted entries, letting 7-Zip recurse
  through directories without materializing a line for every nested file.
- Failed or cancelled jobs remove their temporary `.tmp` archive and leave
  prior final `.7z` outputs untouched.
- The preflight rejects unsafe ZIP member paths before `7z x` can extract them.
- Bad ZIPs are reported in the console and the batch continues to the next ZIP.

## License Audit

Program source:

- `zip_to_7z_gui.py` follows this repository's default MPL-2.0 source license.
- It uses only Python standard-library modules: `tkinter`, `pathlib`,
  `subprocess`, `tempfile`, `zipfile`, `threading`, `queue`, `dataclasses`,
  `os`, `sys`, and `shutil`.
- If a package bundles Python, Python runtime/library license obligations apply
  under the Python Software Foundation License Version 2.

External runtime tool:

- The script invokes a user-installed 7-Zip CLI and does not vendor 7-Zip code.
- Official 7-Zip licensing says most 7-Zip code is GNU LGPL, some parts are
  BSD 3-clause or BSD 2-clause, and some parts have the unRAR license restriction.
- If a release package bundles `7z.exe`, `7za`, `7zz`, `7z.dll`, or p7zip
  equivalents, include the matching 7-Zip license files and satisfy that
  package's redistribution obligations.

Not used:

- No `py7zr`, libarchive, PKHeX.Core, Flask, Qt binding, .NET, or mGBA runtime
  dependency is used by this GUI.
- It does not grant redistribution rights for generated Pokemon files, ROMs,
  saves, or other game data.

Reference sources checked for this audit:

- <https://www.7-zip.org/>
- <https://www.7-zip.org/license.txt>
- <https://docs.python.org/3/license.html>
