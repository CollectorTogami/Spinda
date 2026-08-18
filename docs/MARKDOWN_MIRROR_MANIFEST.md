# Markdown Mirror Manifest

Current status: Active mirror list for Markdown files maintained in both the
main workspace and the sanitized `github-clean` package, with an automated
checker.

Last verified date: 2026-06-01.

Proven artifacts: hash comparison performed on the rows below on 2026-05-30,
plus `tools/check_markdown_mirrors.py`.

Known gaps: checker is not wired into CI because this is a private local fork
with source control disabled by project policy.

Next action: run the checker before clean-package refreshes and after edits to
any mirrored Markdown file.

## Policy

If a Markdown file appears in both the main workspace and `github-clean`, update
both copies in the same task unless this manifest marks the pair as
`intentional-divergent`.

For byte-identical mirrored files, copy changes both ways or edit both files so
their SHA-256 hashes match again.

For intentional-divergent files, keep the same topic current in both files, but
do not force byte identity.

## Mirror Table

| Main workspace path | Clean repo path | Sync direction | Expected hash state | Last verified | Main hash short | Clean hash short | Reason |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `README.md` | `github-clean/README.md` | topic-sync | intentional-divergent | 2026-04-30 | `AF74BF4E4DB0` | `8292D7D25F90` | Main README is upstream/general mGBA README; clean README is sanitized publication intro. Keep project/license/run-guide links conceptually current, not byte-identical. |
| `markdown-files/RUN_GUIDE.md` | `github-clean/RUN_GUIDE.md` | bidirectional-copy | hash-match | 2026-05-05 | `C79E82846741` | `C79E82846741` | Clean package run instructions should match main reference copy. |
| `markdown-files/LICENSES.md` | `github-clean/LICENSES.md` | bidirectional-copy | hash-match | 2026-05-05 | `B3F1760B6D23` | `B3F1760B6D23` | License summary must stay identical across main and clean package. |
| `markdown-files/CREDITS.md` | `github-clean/CREDITS.md` | bidirectional-copy | hash-match | 2026-05-04 | `4BE53DD76E84` | `4BE53DD76E84` | Credits and attribution must stay identical across main and clean package. |
| `markdown-files/MGBA_CUSTOM_CHANGES_AND_FEATURES.md` | `github-clean/docs/MGBA_CUSTOM_CHANGES_AND_FEATURES.md` | bidirectional-copy | hash-match | 2026-05-01 | `3D1D9485145A` | `3D1D9485145A` | Custom feature inventory must stay identical across main and clean package. |
| `markdown-files/INPUT_TAPES_VS_MOVIES.md` | `github-clean/docs/INPUT_TAPES_VS_MOVIES.md` | bidirectional-copy | hash-match | 2026-04-30 | `A99C0E4F31A7` | `A99C0E4F31A7` | Input tape versus movie terminology must stay identical across main and clean package. |
| `markdown-files/PHASE3_RUNBOOK.md` | `github-clean/docs/PHASE3_RUNBOOK.md` | bidirectional-copy | hash-match | 2026-05-01 | `E2EEB29873B1` | `E2EEB29873B1` | Phase 3 production runbook must stay identical across main and clean package. |
| `markdown-files/PHASE3_CONTROL_AND_AUX_SCRIPTS_CHEATSHEET.md` | `github-clean/docs/PHASE3_CONTROL_AND_AUX_SCRIPTS_CHEATSHEET.md` | bidirectional-copy | hash-match | 2026-05-01 | `A3EB1384F60B` | `A3EB1384F60B` | Command center, watcher, and aux script cheat sheet must stay identical across main and clean package. |
| `markdown-files/PHASE3_COMMAND_CENTER_GUIDE.md` | `github-clean/docs/PHASE3_COMMAND_CENTER_GUIDE.md` | bidirectional-copy | hash-match | 2026-05-04 | `8402DFFD3523` | `8402DFFD3523` | Command center guide must stay identical across main and clean package. |
| `markdown-files/MULTI_DEVICE_PHASE3_SCALING_PLAN.md` | `github-clean/docs/MULTI_DEVICE_PHASE3_SCALING_PLAN.md` | bidirectional-copy | hash-match | 2026-05-02 | `759BDACBD6FE` | `759BDACBD6FE` | Multi-device scaling guide must stay identical across main and clean package. |
| `markdown-files/WORKER_PC_CONFIGURATION_AND_SETUP.md` | `github-clean/docs/WORKER_PC_CONFIGURATION_AND_SETUP.md` | bidirectional-copy | hash-match | 2026-05-02 | `A4364E0C6C4F` | `A4364E0C6C4F` | Worker PC setup guide must stay identical across main and clean package. |
| `markdown-files/SYNCTHING_LANE_RESULT_TRANSFER_GUIDE.md` | `github-clean/docs/SYNCTHING_LANE_RESULT_TRANSFER_GUIDE.md` | bidirectional-copy | hash-match | 2026-05-01 | `AB0709E99D34` | `AB0709E99D34` | Syncthing lane-result transfer guide must stay identical across main and clean package. |
| `markdown-files/python_lua_scrips.md` | `github-clean/docs/python_lua_scrips.md` | bidirectional-copy | hash-match | 2026-05-05 | `BF2AE833D9E2` | `BF2AE833D9E2` | Script inventory must stay identical across main and clean package. |
| `markdown-files/dependencies-modifications-calls.md` | `github-clean/docs/dependencies-modifications-calls.md` | bidirectional-copy | hash-match | 2026-06-01 | `88C4C291816E` | `88C4C291816E` | Broad custom dependency/source-change inventory must stay identical across main and clean package. |
| `markdown-files/SPINDA_PROJECT_DOC_INDEX.md` | `github-clean/docs/SPINDA_PROJECT_DOC_INDEX.md` | topic-sync | intentional-divergent | 2026-05-06 | `F9FE5D78AA94` | `B77459864C62` | Clean index intentionally omits private-only rows that are not suitable for public clean export. |
| `markdown-files/PHASE3_WATCHER_GUIDE.md` | `github-clean/docs/PHASE3_WATCHER_GUIDE.md` | bidirectional-copy | hash-match | 2026-04-30 | `A92BF7A66C82` | `A92BF7A66C82` | Watcher guide must stay identical across main and clean package. |
| `markdown-files/PHASE3_RECOVERY_GUIDE.md` | `github-clean/docs/PHASE3_RECOVERY_GUIDE.md` | bidirectional-copy | hash-match | 2026-04-30 | `5F2E3C8956E0` | `5F2E3C8956E0` | Recovery guide must stay identical across main and clean package. |
| `markdown-files/PHASE3_FINAL_VALIDATION_PLAN.md` | `github-clean/docs/PHASE3_FINAL_VALIDATION_PLAN.md` | bidirectional-copy | hash-match | 2026-04-30 | `F9D826493757` | `F9D826493757` | Final validation plan must stay identical across main and clean package. |
| `markdown-files/PHASE3_LINUX_HELPER_NODE.md` | `github-clean/docs/PHASE3_LINUX_HELPER_NODE.md` | bidirectional-copy | hash-match | 2026-05-01 | `2283EB67DEA6` | `2283EB67DEA6` | Linux helper-node guide must stay identical across main and clean package. |
| `markdown-files/FRLG_TSV_SAVE_BANK_PLAN.md` | `github-clean/docs/FRLG_TSV_SAVE_BANK_PLAN.md` | bidirectional-copy | hash-match | 2026-05-06 | `8EF8AC9B4CF9` | `8EF8AC9B4CF9` | FR/LG TSV save-bank plan must stay identical across main and clean package. |
| `markdown-files/SPC3_PHASE3_GENERAL_FINDINGS.md` | `github-clean/docs/SPC3_PHASE3_GENERAL_FINDINGS.md` | bidirectional-copy | hash-match | 2026-06-01 | `78B93BCB4482` | `78B93BCB4482` | SPC3 predictor inconsistency and v8 compression findings must stay identical across main and clean package. |
| `markdown-files/SPC3_TWO_STAGE_RUNTIME_FORMAT.md` | `github-clean/docs/SPC3_TWO_STAGE_RUNTIME_FORMAT.md` | bidirectional-copy | hash-match | 2026-06-01 | `284499B854E1` | `284499B854E1` | SPC3 v8 two-stage runtime/global-stage/template/residual format note must stay identical across main and clean package. |
| `markdown-files/SPC3_COMPRESSION_EVOLUTION_2026-05-31.md` | `github-clean/docs/SPC3_COMPRESSION_EVOLUTION_2026-05-31.md` | bidirectional-copy | hash-match | 2026-06-01 | `51AA39A35F53` | `51AA39A35F53` | SPC3 compression-iteration size history must stay identical across main and clean package. |
| `markdown-files/SPC3_V8_COMPRESSION_PROCESS_CAVEMAN.md` | `github-clean/docs/SPC3_V8_COMPRESSION_PROCESS_CAVEMAN.md` | bidirectional-copy | hash-match | 2026-06-01 | `156C2DF6283A` | `156C2DF6283A` | Current v8 compression-process outline must stay identical across main and clean package. |
| `markdown-files/SPC3_GPU_OFFLOAD_SUMMARY.md` | `github-clean/docs/SPC3_GPU_OFFLOAD_SUMMARY.md` | bidirectional-copy | hash-match | 2026-05-07 | `96E2754DC4BF` | `96E2754DC4BF` | SPC3 GPU compression/decompression offload summary must stay identical across main and clean package. |
| `markdown-files/SPINDA_LARGE_CORPUS_IDEA_BANK.md` | `github-clean/docs/SPINDA_LARGE_CORPUS_IDEA_BANK.md` | bidirectional-copy | hash-match | 2026-05-07 | `1F58D2D0CF8E` | `1F58D2D0CF8E` | Large-corpus idea bank must stay identical across main and clean package. |
| `tools/spinda/hatch_zip_splitter/README.md` | `github-clean/tools/spinda/hatch_zip_splitter/README.md` | bidirectional-copy | hash-match | 2026-05-07 | `AC9DFC6F69F0` | `AC9DFC6F69F0` | Hatch ZIP splitter license and operator guide must stay identical across main and clean package. |
| `tools/spinda/zip_to_7z_gui/README.md` | `github-clean/tools/spinda/zip_to_7z_gui/README.md` | bidirectional-copy | hash-match | 2026-05-07 | `F35285CFCF04` | `F35285CFCF04` | ZIP-to-7z GUI license and operator guide must stay identical across main and clean package. |
| `tools/spinda/spinda_workbench_native/README.md` | `github-clean/tools/spinda/spinda_workbench_native/README.md` | bidirectional-copy | hash-match | 2026-05-07 | `0BD23B24EF5B` | `0BD23B24EF5B` | Native Workbench operator guide must stay identical across main and clean package. |
| `tools/spinda/spinda_workbench_native/README-tldr.md` | `github-clean/tools/spinda/spinda_workbench_native/README-tldr.md` | bidirectional-copy | hash-match | 2026-05-07 | `87A09BAAFB4F` | `87A09BAAFB4F` | Native Workbench caveman-full TLDR must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/README.md` | `github-clean/tools/spinda/spc3_prototype/README.md` | bidirectional-copy | hash-match | 2026-05-07 | `06E8FA6A443C` | `06E8FA6A443C` | SPC3 compression prototype operator guide must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/README-tldr.md` | `github-clean/tools/spinda/spc3_prototype/README-tldr.md` | bidirectional-copy | hash-match | 2026-05-07 | `3F9E3DE6E46B` | `3F9E3DE6E46B` | SPC3 compression prototype caveman-full TLDR must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/SPC3_V0_1_FORMAT.md` | `github-clean/tools/spinda/spc3_prototype/SPC3_V0_1_FORMAT.md` | bidirectional-copy | hash-match | 2026-05-07 | `F26C2CCDDC01` | `F26C2CCDDC01` | SPC3 v0.1/v0.2 format notes must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/SPC3_V0_2_TYPED_LEVEL3_PLAN.md` | `github-clean/tools/spinda/spc3_prototype/SPC3_V0_2_TYPED_LEVEL3_PLAN.md` | bidirectional-copy | hash-match | 2026-05-07 | `4D2C11A8D438` | `4D2C11A8D438` | SPC3 v0.2 typed level-3 plan must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/SPC3_CPP_ASM_BOUNDARY.md` | `github-clean/tools/spinda/spc3_prototype/SPC3_CPP_ASM_BOUNDARY.md` | bidirectional-copy | hash-match | 2026-05-08 | `5B25978FB7C8` | `5B25978FB7C8` | SPC3 C++/ASM boundary note must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/SPC3_PROGRESS_TECHNICAL_CAVEMAN.md` | `github-clean/tools/spinda/spc3_prototype/SPC3_PROGRESS_TECHNICAL_CAVEMAN.md` | bidirectional-copy | hash-match | 2026-05-07 | `56574DBA136C` | `56574DBA136C` | SPC3 technical progress note must stay identical across main and clean package. |
| `tools/spinda/spc3_prototype/SPC3_PROGRESS_NONTECHNICAL_CAVEMAN.md` | `github-clean/tools/spinda/spc3_prototype/SPC3_PROGRESS_NONTECHNICAL_CAVEMAN.md` | bidirectional-copy | hash-match | 2026-05-07 | `CA5BC33579BF` | `CA5BC33579BF` | SPC3 nontechnical progress note must stay identical across main and clean package. |
| `tools/spinda/spc3_gui/README.md` | `github-clean/tools/spinda/spc3_gui/README.md` | bidirectional-copy | hash-match | 2026-05-07 | `B17767F523F1` | `B17767F523F1` | SPC3 GUI wrapper operator guide must stay identical across main and clean package. |
| `tools/spinda/spc3_gui_native/README.md` | `github-clean/tools/spinda/spc3_gui_native/README.md` | bidirectional-copy | hash-match | 2026-05-08 | `827A1C1C2494` | `827A1C1C2494` | Native SPC3 verifier GUI operator guide must stay identical across main and clean package. |
| `tools/spinda/spc3_gui_native/SPC3_NATIVE_GUI_GUIDE.md` | `github-clean/tools/spinda/spc3_gui_native/SPC3_NATIVE_GUI_GUIDE.md` | bidirectional-copy | hash-match | 2026-05-08 | `AC52318A0CAD` | `AC52318A0CAD` | Full native SPC3 verifier GUI guide must stay identical across main and clean package. |
| `tools/spinda/spc3_gui_native/SPC3_NATIVE_GUI_CAVEMAN_TLDR.md` | `github-clean/tools/spinda/spc3_gui_native/SPC3_NATIVE_GUI_CAVEMAN_TLDR.md` | bidirectional-copy | hash-match | 2026-05-08 | `1F5567916E75` | `1F5567916E75` | Caveman TLDR native SPC3 verifier GUI guide must stay identical across main and clean package. |
| `markdown-files/MARKDOWN_MIRROR_MANIFEST.md` | `github-clean/docs/MARKDOWN_MIRROR_MANIFEST.md` | bidirectional-copy | hash-match | 2026-04-30 | `self-referential` | `self-referential` | Mirror policy should stay byte-identical; exact hash is checked by the command below because embedding its own hash would change the file. |

## Not Mirrored

These doc categories stay out of `github-clean` unless explicitly sanitized:

- private run reports
- live-lane evidence logs
- artifact inventories
- caveman rewrites, except explicitly mirrored public-safe SPC3 process docs
- local machine cleanup plans
- ROM/save/savestate/PK3/ZIP references
- stock upstream localized README files

## Automated Check Command

Run from the repository root:

```powershell
python tools\check_markdown_mirrors.py
```

Use JSON output for automation:

```powershell
python tools\check_markdown_mirrors.py --json
```

## Manual Check Command

If the checker is unavailable, run from the repository root:

```powershell
$pairs = @(
  @("README.md", "github-clean\README.md"),
  @("markdown-files\RUN_GUIDE.md", "github-clean\RUN_GUIDE.md"),
  @("markdown-files\LICENSES.md", "github-clean\LICENSES.md"),
  @("markdown-files\CREDITS.md", "github-clean\CREDITS.md"),
  @("markdown-files\MGBA_CUSTOM_CHANGES_AND_FEATURES.md", "github-clean\docs\MGBA_CUSTOM_CHANGES_AND_FEATURES.md"),
  @("markdown-files\INPUT_TAPES_VS_MOVIES.md", "github-clean\docs\INPUT_TAPES_VS_MOVIES.md"),
  @("markdown-files\PHASE3_RUNBOOK.md", "github-clean\docs\PHASE3_RUNBOOK.md"),
  @("markdown-files\PHASE3_CONTROL_AND_AUX_SCRIPTS_CHEATSHEET.md", "github-clean\docs\PHASE3_CONTROL_AND_AUX_SCRIPTS_CHEATSHEET.md"),
  @("markdown-files\PHASE3_COMMAND_CENTER_GUIDE.md", "github-clean\docs\PHASE3_COMMAND_CENTER_GUIDE.md"),
  @("markdown-files\MULTI_DEVICE_PHASE3_SCALING_PLAN.md", "github-clean\docs\MULTI_DEVICE_PHASE3_SCALING_PLAN.md"),
  @("markdown-files\WORKER_PC_CONFIGURATION_AND_SETUP.md", "github-clean\docs\WORKER_PC_CONFIGURATION_AND_SETUP.md"),
  @("markdown-files\SYNCTHING_LANE_RESULT_TRANSFER_GUIDE.md", "github-clean\docs\SYNCTHING_LANE_RESULT_TRANSFER_GUIDE.md"),
  @("markdown-files\python_lua_scrips.md", "github-clean\docs\python_lua_scrips.md"),
  @("markdown-files\dependencies-modifications-calls.md", "github-clean\docs\dependencies-modifications-calls.md"),
  @("markdown-files\SPINDA_PROJECT_DOC_INDEX.md", "github-clean\docs\SPINDA_PROJECT_DOC_INDEX.md"),
  @("markdown-files\PHASE3_WATCHER_GUIDE.md", "github-clean\docs\PHASE3_WATCHER_GUIDE.md"),
  @("markdown-files\PHASE3_RECOVERY_GUIDE.md", "github-clean\docs\PHASE3_RECOVERY_GUIDE.md"),
  @("markdown-files\PHASE3_FINAL_VALIDATION_PLAN.md", "github-clean\docs\PHASE3_FINAL_VALIDATION_PLAN.md"),
  @("markdown-files\PHASE3_LINUX_HELPER_NODE.md", "github-clean\docs\PHASE3_LINUX_HELPER_NODE.md"),
  @("markdown-files\FRLG_TSV_SAVE_BANK_PLAN.md", "github-clean\docs\FRLG_TSV_SAVE_BANK_PLAN.md"),
  @("markdown-files\SPC3_PHASE3_GENERAL_FINDINGS.md", "github-clean\docs\SPC3_PHASE3_GENERAL_FINDINGS.md"),
  @("markdown-files\SPC3_TWO_STAGE_RUNTIME_FORMAT.md", "github-clean\docs\SPC3_TWO_STAGE_RUNTIME_FORMAT.md"),
  @("markdown-files\SPC3_COMPRESSION_EVOLUTION_2026-05-31.md", "github-clean\docs\SPC3_COMPRESSION_EVOLUTION_2026-05-31.md"),
  @("markdown-files\SPC3_V8_COMPRESSION_PROCESS_CAVEMAN.md", "github-clean\docs\SPC3_V8_COMPRESSION_PROCESS_CAVEMAN.md"),
  @("markdown-files\SPC3_GPU_OFFLOAD_SUMMARY.md", "github-clean\docs\SPC3_GPU_OFFLOAD_SUMMARY.md"),
  @("markdown-files\SPINDA_LARGE_CORPUS_IDEA_BANK.md", "github-clean\docs\SPINDA_LARGE_CORPUS_IDEA_BANK.md"),
  @("tools\spinda\hatch_zip_splitter\README.md", "github-clean\tools\spinda\hatch_zip_splitter\README.md"),
  @("tools\spinda\zip_to_7z_gui\README.md", "github-clean\tools\spinda\zip_to_7z_gui\README.md"),
  @("tools\spinda\spinda_workbench_native\README.md", "github-clean\tools\spinda\spinda_workbench_native\README.md"),
  @("tools\spinda\spinda_workbench_native\README-tldr.md", "github-clean\tools\spinda\spinda_workbench_native\README-tldr.md"),
  @("tools\spinda\spc3_prototype\README.md", "github-clean\tools\spinda\spc3_prototype\README.md"),
  @("tools\spinda\spc3_prototype\README-tldr.md", "github-clean\tools\spinda\spc3_prototype\README-tldr.md"),
  @("tools\spinda\spc3_prototype\SPC3_V0_1_FORMAT.md", "github-clean\tools\spinda\spc3_prototype\SPC3_V0_1_FORMAT.md"),
  @("tools\spinda\spc3_prototype\SPC3_V0_2_TYPED_LEVEL3_PLAN.md", "github-clean\tools\spinda\spc3_prototype\SPC3_V0_2_TYPED_LEVEL3_PLAN.md"),
  @("tools\spinda\spc3_prototype\SPC3_CPP_ASM_BOUNDARY.md", "github-clean\tools\spinda\spc3_prototype\SPC3_CPP_ASM_BOUNDARY.md"),
  @("tools\spinda\spc3_prototype\SPC3_PROGRESS_TECHNICAL_CAVEMAN.md", "github-clean\tools\spinda\spc3_prototype\SPC3_PROGRESS_TECHNICAL_CAVEMAN.md"),
  @("tools\spinda\spc3_prototype\SPC3_PROGRESS_NONTECHNICAL_CAVEMAN.md", "github-clean\tools\spinda\spc3_prototype\SPC3_PROGRESS_NONTECHNICAL_CAVEMAN.md"),
  @("tools\spinda\spc3_gui\README.md", "github-clean\tools\spinda\spc3_gui\README.md"),
  @("tools\spinda\spc3_gui_native\README.md", "github-clean\tools\spinda\spc3_gui_native\README.md"),
  @("tools\spinda\spc3_gui_native\SPC3_NATIVE_GUI_GUIDE.md", "github-clean\tools\spinda\spc3_gui_native\SPC3_NATIVE_GUI_GUIDE.md"),
  @("tools\spinda\spc3_gui_native\SPC3_NATIVE_GUI_CAVEMAN_TLDR.md", "github-clean\tools\spinda\spc3_gui_native\SPC3_NATIVE_GUI_CAVEMAN_TLDR.md"),
  @("markdown-files\MARKDOWN_MIRROR_MANIFEST.md", "github-clean\docs\MARKDOWN_MIRROR_MANIFEST.md")
)
foreach ($pair in $pairs) {
  $main = Join-Path (Get-Location) $pair[0]
  $clean = Join-Path (Get-Location) $pair[1]
  $mainHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $main).Hash
  $cleanHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $clean).Hash
  [pscustomobject]@{
    Main = $pair[0]
    Clean = $pair[1]
    MainHash = $mainHash.Substring(0, 12)
    CleanHash = $cleanHash.Substring(0, 12)
    Match = $mainHash -eq $cleanHash
  }
}
```
