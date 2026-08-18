# SPC3 Native GUI Caveman TLDR

## What This Is

Native C++ dark GUI for SPC3.

No Python GUI. No Tkinter. No web app.

It runs `spc3_prototype.exe`, shows command, writes JSON report.

GUI keeps hidden worker:

```text
spc3_prototype.exe --server
```

GPU cache lives across GUI runs.

Exit GUI, cancel run, or change exe = worker dies, cache gone.

Cancel during startup still stops run.

Repo GUI worker starts in workspace root.

Moved package worker starts in GUI folder.

Plain CLI still one-shot.

## Wrap-Up Use

Use GUI now.

Good for:

- pack
- verify
- inspect
- unpack
- consolidate pre-compressed lanes
- CPU/GPU report compare

GUI simple on purpose.

No expert GPU knobs.

Use `Use GPU` only when CUDA box and big typed verify/unpack.

CPU fallback OK if report says why.

## Build

Run from `<repo-root>`:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
```

Portable build:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat baseline
```

This builds:

- `spc3_verifier_gui.exe`
- `spc3_prototype.exe`
- `spc3_verifier_gui_baseline.exe` with `baseline`
- `spc3_prototype_baseline.exe` with `baseline`

Both land in:

```text
tools\spinda\spc3_gui_native
```

Current build should need no extra MinGW/zlib/zstd/lzma DLLs.

## Launch

Run:

```powershell
.\tools\spinda\spc3_gui_native\spc3_verifier_gui.exe
```

Explorer launch OK too.

Normal folder has:

```text
spc3_verifier_gui.exe
spc3_prototype.exe
```

## CPU / GPU

CPU target: Windows x86-64 / AMD64.

AMD Ryzen, Threadripper, EPYC = OK.

Intel x86-64 = OK.

ARM64 = not current target.

GPU target: NVIDIA CUDA only.

AMD GPU = CPU fallback.

Intel GPU = CPU fallback.

If `Use GPU` checked and no CUDA, report should say fallback reason.

## Important Build Caveat

Build uses `-march=native`.

Good for build machine.

Risk for older CPU.

For mixed machines, make baseline x86-64 build.

Now script can do this.

Use `baseline`.

## Mode Pick

Mode is radio button:

- `Verify`
- `Pack`
- `Consolidate`
- `Inspect`
- `Unpack`

GUI hides stuff not used by chosen mode.

## Mode I/O

| Mode | Input | Output |
| --- | --- | --- |
| `Verify` | `.spc3` | JSON report only |
| `Pack` | lane ZIP folder | `.spc3` + report |
| `Consolidate` | `.spc3` shard folder | combined `.spc3` + report |
| `Inspect` | `.spc3` | metadata report |
| `Unpack` | `.spc3` + lane pick | `0xLLLL.spinda80.zip` + report |

Unpack ZIP has encrypted `0xUUUULLLL.pk3` inside.

Unpack has PK3 state:

- `Egg`: keep egg
- `Hatched shiny`: hatch, use matching TSV trainer
- `Hatched not shiny`: hatch, use nonmatching TSV trainer

Hatched modes need:

```text
Trainer index = TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

GUI hides trainer index when `Egg`.

Make index:

```powershell
dotnet run --project tools\spinda\hatch_zip_splitter\SpindaHatchZipSplitter.csproj -c Release -- --save-dir TSVs --trainer-id 0 --trainer-index TSVs\_spinda_tsv_trainer_index_tid_0x0000.json
```

Need edit Spinda too?

Unpack mode click:

```text
Extra settings ...
```

Can set:

- nickname
- OT name
- moves
- PP Ups
- PP auto-made from move + PP Ups
- EVs
- IVs
- held item
- EXP
- friendship
- Pokerus
- contest stats
- met place
- met level
- game
- ball
- OT gender
- language
- ability slot

Most stuff dropdown now.

Catalog files:

```text
gen3_moves.csv
gen3_held_items.csv
gen3_locations.csv
```

Keep CSVs beside GUI exe.

Pick move.

Pick PP Ups `0..3`.

GUI fills PP.

GUI sends:

```text
--set-moves
--set-pp
--set-pp-ups
```

Level dropdown = Spinda fast EXP.

Sorted:

- moves by name
- held items by name
- Gen 3 balls by name
- origin games by name
- met places by name

Ball list:

- Master
- Ultra
- Great
- Poke
- Safari
- Net
- Dive
- Nest
- Repeat
- Timer
- Luxury
- Premier

No Dusk.

No Heal.

No Quick.

Language list:

- Japanese
- English
- French
- Italian
- German
- Spanish

Pick origin game.

Met place list changes for that game.

Pokerus:

- strain `0..15`
- days `0..4`

IVs:

- six dropdowns
- `0..31`

EVs / contest:

- six dropdowns
- `0..255`

Ability:

- slot `0`
- slot `1`

No change = no edit sent.

For IV/EV/contest row:

- all six set, or
- all six No change

Partial row = GUI refuses run.

Tool hatch first.

Then edits.

Then checksum.

Then encrypt.

Pack level hides stuff too:

- level `0`: no `Profile`, raw only
- level `1` or `2`: `Profile` yes, no `Typed v0.2`, no `External predictor`
- level `3`: all main pack controls visible

## Default Mode

Default = `Verify`.

Default does disk-light check:

- reads `.spc3`
- rebuilds in RAM or GPU memory
- checks internal CRC
- writes report JSON
- writes no lane payload files

Keep `Internal only` checked for disk-light verify.

## Buttons

Each path row has `...`.

Use `...` to pick file or folder.

File rows open file/save dialog.

Folder rows open folder picker.

Still can type path by hand.

`Summary` button read report.

`Compare JSON` path lets two reports fight side by side:

- CPU vs GPU
- pack vs verify
- verify vs unpack

## Verify

Use when checking existing `.spc3`.

Visible fields:

- `Input .spc3`
- `Predictor JSON`
- `Report JSON`
- `Use GPU`
- `Internal only`

If `Internal only` unchecked, `Lane ZIP root` appears.

Use normal:

1. Select `Verify`.
2. Pick `Input .spc3`.
3. Keep `Internal only` checked.
4. Keep or clear `Use GPU`.
5. Pick `Report JSON`.
6. Click `Run`.

Good report:

- `ok=true`
- `internal_crc_mismatches=0`
- if GPU asked but not used, `fallback_reason` clear

## Pack

Use when making new `.spc3` from lane ZIPs.

Visible fields:

- `Output .spc3`
- `Lane ZIP root`
- `Predictor JSON`
- `Report JSON`
- `Lanes` (`all` default; sparse OK)
- `Level`
- `Profile`
- `Typed v0.2`
- `External predictor`
- `No entropy probe`

Some fields show only when level needs them.

Recommended:

- `Level`: `3`
- `Typed v0.2`: checked
- `Profile`: `fast`
- `No entropy probe`: checked

`fast` = zstd-9 path.

`auto` / `compat` = zlib-9 compatibility.

`small` = LZMA2-9 size path.

## Consolidate

Use when have existing `.spc3` shards.

It copies compressed lane streams.

It does not unpack payload.

It does not recompress payload.

Visible fields:

- `Output .spc3`
- `SPC3 shard root`
- `Report JSON`

Rejects:

- duplicate lane
- mixed version
- mixed level
- mixed flags
- predictor mismatch
- bad shard

Good report has:

```text
copy_mode=compressed_stream_copy_no_payload_decode
```

## Inspect

Use before trusting unknown `.spc3`.

Visible fields:

- `Input .spc3`
- `Report JSON`

Reads metadata.

Does not decode lane payload.

## Unpack

Use when need lane ZIP output.

Visible fields:

- `Input .spc3`
- `Predictor JSON`
- `Output ZIP dir`
- `Lanes`
- `PK3 state`
- `Trainer index` when hatched
- `Extra settings ...`
- `Report JSON`
- `Use GPU`

Writes:

```text
0xLLLL.spinda80.zip
```

Inside ZIP:

```text
0xUUUULLLL.pk3
```

PK3 bytes stay encrypted.

`Egg` keeps original rebuilt corpus.

`Hatched shiny` makes PID shiny for chosen trainer.

`Hatched not shiny` makes PID not shiny.

Lane pick:

- `All lanes`
- `One lane`: hex lower PID half, example `00A5`
- `Range`: hex from/to, inclusive

Use fresh unpack dir when you need clean output.

## GPU Read

Check report object:

```text
gpu_rebuild
```

Important fields:

- `requested`
- `used`
- `status`
- `fallback_reason`
- `device_name`
- `mismatched_lanes`
- `mismatched_bytes`

GPU unavailable is OK if CPU fallback passes.

Bad:

- `ok=false`
- mismatch counters nonzero
- GPU requested but no reason shown

## Missing DLL Error

Run:

```powershell
cmd /c tools\spinda\spc3_gui_native\build_spc3_verifier_gui.bat
```

Then launch from:

```text
tools\spinda\spc3_gui_native
```

Current folder should have only:

```text
spc3_verifier_gui.exe
spc3_prototype.exe
```

If old `.dll` files remain there, rebuild should remove stale ones.

## Fast Rule

Need check file? `Verify`.

Need make file? `Pack`.

Need merge prepacked shards? `Consolidate`.

Need look at metadata? `Inspect`.

Need lane ZIP output? `Unpack`.

Need smallest disk writes? `Verify` + `Internal only`.
