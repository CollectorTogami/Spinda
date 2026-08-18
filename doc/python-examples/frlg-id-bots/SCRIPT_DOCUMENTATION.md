# FR/LG ID Bot Script Documentation

## Roadmap Position

These scripts feed the roadmap's TID0/TSV save-bank step. The bank is used
later to hatch Spinda eggs as proof outputs:

- matching `TSV == PSV` save:
  - shiny hatch proof
- non-matching TSV save:
  - non-shiny control hatch

Proven as of 2026-05-06:

- save folder:
  - `<repo-root>\TSVs`
- complete saves:
  - `8192 / 8192`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`
- filename convention:
  - `TSV-xxxx-sid-xxxxx.sav`
  - decimal TSV and decimal SID

The mass-hatching ZIP stage is not part of these bots. It is a later consumer
that should emit separate shiny and non-shiny ZIP subsets after the Phase 3 egg
ZIP corpus is ready.

## Shared Math

`frlg_id_bot_common.py` owns emulator-independent math and JSON helpers. Its
`lcrng_jump_for_steps()` helper composes the RNG transform and skips ahead in
`O(log n)` time. Forecasts reuse a precomputed stride jump, which keeps large
forecasts and calibration probes cheap.

Important formulas:

```text
next_rng = (rng * 0x41C64E6D + 0x6073) & 0xFFFFFFFF
SID = next_rng >> 16
shiny_value = (TID ^ SID) >> 3
```

For this workflow:

```text
TID = 0
shiny_value = SID >> 3
```

Each shiny value has eight possible SIDs because the low three SID bits do not
affect `(TID ^ SID) >> 3`.

## Trainer-ID Brute Force

`Trainer-ID-Bruteforcer.py` starts from the final `A` input before
`SeedRngAndSetTrainerId()`.

Loop shape:

1. Restore branch.
2. Clear held input to neutral.
3. Wait `delay_frames` neutral frames.
4. Run `pre_press_neutral_frames` neutral release frames.
5. Press `A`.
6. Wait until Timer 1 stops.
7. Read `0x02020000`.
8. If TID equals target, save hit state and metadata.

Default target is `0x0000`.
Default input edge settings are:

```text
--pre-press-neutral-frames 1
--press-frames 2
```

FR/LG menu input is edge-triggered. The neutral release frame prevents the
failure mode where the game still remembers the previous `A` and the scripted
final `A` does not count.

The bot entrypoint calls `main()` directly instead of raising `SystemExit`.
mGBA's embedded script runner displays `SystemExit(0)` as `[ERROR]`, even
though the hit and saved state are valid.

The bot is intentionally brute-force because Timer 1 low is the target and the
input point is the timer-sensitive point.

Before the scan starts, the bot asks the visible Qt core to enable audio
killswitch, no-render mode, forced fast-forward, and fast-forward ratio `-1.0`
for unbounded speed. Missing hooks are recorded as `unavailable`; available
hooks are enabled once and captured in the hit metadata.

The implementation uses the Qt scratch slot as a rolling no-input checkpoint
when available, and falls back to `_tid-bruteforce-rolling.ss0` only when
scratch state is unavailable. This avoids the naive `0 + 1 + 2 + ... + n`
delay replay cost and avoids hot-loop disk I/O on the normal Qt path. Each miss
costs one candidate branch plus one neutral frame to create the next candidate.

After a hit, the bot overwrites the selected `.ss0`, pauses the visible core
when that Qt hook exists, overwrites metadata, prints the JSON result, and
returns normally.

## Secret-ID Shiny-Value Bot

`Secret-ID-Shiny-Value-Bot.py` starts after TID `0x0000` is already hit and
before `InitPlayerTrainerId()` consumes the SID `Random()`.

`Secret-ID-Pause-After-Generation.py` uses the same final-input assumption but
does not target any shiny value. It exists only to generate whichever SID comes
next, pause at the post-SID anchor, and make recording `SiD_RNG_After.json`
practical.

Loop shape:

1. Capture pre-SID branch.
2. Read live branch `gRngValue`.
3. Build an LCRNG forecast for missing shiny values.
4. Restore branch.
5. Clear held input to neutral.
6. Wait the predicted neutral frame count.
7. Optionally run `final_pre_neutral_frames` release frames.
8. Press final `A`.
9. Read SaveBlock2 final TID/SID.
10. Infer nearby SID commit offset if observed SID differs.
11. Accept any still-missing actual shiny value.
12. Replay `SiD_RNG_After.json`.
13. Verify the post-tape live TID/SID still match the hit.
14. Export the battery `.sav` to a temp path.
15. Patch save-level TID/SID and party-slot-1 owner IDs to the observed hit,
    then rebuild Pokemon and Gen 3 sector checksums.
16. Parse the temp save and require matching TID/SID plus party-slot-1
    Bulbasaur. If that exported SRAM proof fails, wait extra neutral frames and
    retry the temp export before treating the SID attempt as bad.
17. Atomically replace the final save path.
18. Mark the ledger row complete.

This is not emulator brute force. The emulator is driven once per desired
shiny value, with wait frames predicted from the LCRNG.

TID `0x0000` is read-only. The SID bot verifies the pre-SID mirror and final
SaveBlock2 TID but does not write trainer ID memory. The post-tape output is
only a battery `.sav`; SID runs intentionally do not create savestates.

mGBA's battery export clones active cartridge save data. It does not serialize
unsaved WRAM progress. Therefore `SiD_RNG_After.json` should include an
in-game save before the export point. As a second guard, the bot patches IDs in
the temp export from the observed live hit and validates the result before the
ledger row can become complete. This edits only the exported file; live trainer
ID memory remains read-only.

Persistent export validation failures are considered recoverable only at the
save-export layer. The row keeps its ledger error and stays retryable. The live
loop then asks the LCRNG forecast for the next later SID hit for that same shiny
value and queues it at the front of the forecast. By default, a process will try
up to eight alternate SID hits for the same TSV before blocking that row for the
rest of the current process. Prediction, TID/SID, route-schedule, and live-ID
mismatches remain fatal because those point to a wrong branch or tape, not a
transient battery export.

The trainer-ID hit state can be earlier than the real pre-SID final input.
When the normal probe fails, live mode tries one setup pass from that TID state:
it taps the final button, saves scratch before each tap, waits briefly after
each tap, and watches SaveBlock2. When a tap writes TID `0x0000`, it restores
the scratch state from immediately before that tap. That restored point becomes
the branch used for the LCRNG forecast.

Live Qt runs refuse to create the ledger until a branch proof passes. A fresh
ROM often leaves `gRngValue == 0x00000000`; a real TID hit state may be auto-set
up from there, but an unproven branch stays paused and does not write a bogus
`TSV-0000-sid-00000.sav`. This check happens before scratch probing, because a
fresh or stale SaveBlock2 can already read as TID/SID `0x0000/0x0000` even
though SID generation has not happened.

Live Qt runs also probe the final input on scratch state before ledger
creation. The probe commits SID, reads final SaveBlock2 TID/SID, then restores
the branch. A second probe waits 31 extra neutral frames before the final input.
The branch is accepted only when both probes keep TID `0x0000` and the SID
changes. Otherwise the script remains alive and waits for the operator to load
the correct pre-SID state.

For a true pre-SID branch, the bot now writes a raw one-frame LCRNG coverage
proof before driving saves:

```text
<repo-root>\TSVs\_sid_raw_lcrng_coverage_tid_0x0000.json
```

The coverage scan walks the mathematical LCRNG one waited frame at a time in
1,000,000-frame chunks. If all possible 16-bit SIDs are not seen in the first
chunk, it advances another chunk and repeats until coverage is complete or an
explicit `--max-advances` cap stops it. For the current TID `0x0000` branch
math (`branch_rng=0x721CBDD8`, `sid_commit_offset=14`, stride `1`), all 65,536
SIDs are present by wait frame `726440`; all 8192 shiny values are present by
wait frame `65710`.

### Ready-State Precalculator

`Secret-ID-Ready-State-Precalculator.py` is the stable tool for the root
savestate:

```text
<repo-root>\tid 0 ready.ss0
```

It loads that savestate read-only, clears held input, probes several waits,
and verifies every probe commits final TID `0x0000`. It then infers the SID
commit offset from live SIDs and writes deterministic delay tables for future
SID-bank runs.

Observed branch values:

```text
gRngValue = 0xEAD1C0A3
initial TID mirror = 0x0000
sid_commit_offset = 273
```

The ready state has one input-edge quirk:

```text
rng_advance = sid_commit_offset + max(wait_frames, 1)
```

Wait `0` and wait `1` both land on the same LCRNG state because the first
neutral frame is consumed as the release edge before the final `A` press can be
accepted. The script documents that formula inline and tests it directly. After
wait `1`, each additional neutral wait advances the LCRNG by one.
For the main SID save bot, this is represented with:

```text
--sid-commit-offset 273 --min-wait-frames 1
```

The minimum wait keeps the forecast from selecting the fake linear wait `0`
state at advance `273`. The first scheduled point becomes wait `1`, which
matches the live ready-state behavior at advance `274`.

Outputs:

```text
<repo-root>\TSVs\_sid_ready_tid0_precalc_report.json
<repo-root>\TSVs\_sid_ready_tid0_all_sid_delays.json
<repo-root>\TSVs\_sid_ready_tid0_all_tsv_delays.json
```

Current proved coverage:

```text
all SIDs: 65,536 / 65,536 by wait 726412
all TSVs: 8,192 / 8,192 by wait 65682
```

The delay-table scan is linear over the LCRNG stream. It uses a direct one-step
LCRNG update in the hot loop and avoids re-running `O(log n)` jump math per
wait. The root savestate is never saved or overwritten.

The prepared TID hit state can produce an RNG-frozen exact pre-SID branch. In
that case, delaying at the final input cannot change SID. The live bot detects
this and switches to deterministic TID-state schedule mode. It reloads and
pauses the read-only TID state, records the loaded-state `gRngValue`, then uses
the proven pre-SID branch RNG and LCRNG commit offset to write:

```text
<repo-root>\TSVs\_sid_tsv_earliest_schedule_tid_0x0000.json
```

That schedule contains the real repeated-A route-cycle rows. This matters
because the TID hit state does not give a free-running wait-to-any-SID branch:
extra neutral wait before the setup taps only shifts the SID by
`(sid_commit_offset + wait_frames) % setup_cycle_frames`. With the current
defaults, that cycle is 20 frames, so most TSVs are unreachable from this
specific state. The schedule JSON records `reachable_target_shiny_values`,
`unreachable_target_shiny_values`, and `unreachable_shiny_values`. In this
mode, row `branch_rng` is the frozen branch used as the cycle origin and row
`rng_advance` is the effective route-cycle residue from that origin. The
schedule is built against currently missing ledger rows, not rows already
complete on resume. If any missing TSV is unreachable, the run stops before
replaying the tape or exporting a save, and writes `route_schedule_error` plus a
short unreachable sample into the ledger. If a reachable row is used and the
live probe or final SID commit does not match the scheduled SID/TSV, the run
also stops before export.

Before a live SID run drives the emulator, it asks the visible Qt core to enable
audio killswitch, no-render mode, forced fast-forward, and fast-forward ratio
`-1.0` for unbounded speed. Missing hooks are recorded as `unavailable`;
available hooks are enabled once and reported in the final summary as
`runtime_settings`.

The pause helper uses the same runtime toggles while it advances through SID
generation. After pausing, it disables no-render by default so the post-SID
screen is visible for tape setup. Pass `--keep-no-render-on` only when you want
to leave the black no-render overlay active. Both the pause helper and the SID
save bot default to a `360`-frame post-commit wait. The ready-state timing
probe showed SaveBlock2 updating at post-frame `272`, so the old `180`-frame
wait could read stale IDs from the loaded save.

`run_secret_id_sid_bot_with_tracker.ps1` can now manage one-off validation
runs with `-WaitForSaveTsv <0..8191>`. That watcher uses the same padded
decimal filename contract as the bot and tracker:

```text
TSV-xxxx-sid-yyyyy.sav
```

This matters for values below 1000. TSV `860` is `TSV-0860-sid-*.sav`; an
unpadded watcher such as `TSV-860-sid-*.sav` will miss a valid save and can
leave mGBA running after the bot has already succeeded. After the padded save
is detected, the launcher stops the mGBA process and leaves the tracker cleanup
path unchanged.

Normal pre-SID mode builds a forecast for the current missing set, then reuses
it across ordinary successful exports. Deterministic TID-state mode instead
builds the route-cycle schedule up front and treats that schedule as the
contract for the run, including explicit proof when the current state cannot
cover the requested TSV set.

## Ledger Contract

Default ledger:

```text
<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json
```

Format:

```json
{
  "format": "frlg-sid-shiny-value-ledger-v1",
  "target_tid": "0x0000",
  "complete_shiny_values": 1,
  "entries": [
    {
      "shiny_value": "0x0000",
      "done": true,
      "tid": "0x0000",
      "sid": "0x0003",
      "save_path": "<repo-root>\\TSVs\\TSV-0000-sid-00003.sav",
      "save_sha1": "...",
      "wait_frames": 123,
      "rng_advance": 124
    }
  ]
}
```

The reference key is `shiny_value`; `sid` records the actual SID hit relative
to TID `0x0000`.

Live Qt runs also append setup/preflight/save breadcrumbs to:

```text
<repo-root>\TSVs\_sid_live_status.log
```

## Calibration Flags

Defaults:

```text
--sid-commit-offset 1
--rng-advances-per-neutral-frame 1
--calibration-search-radius 240
--final-pre-neutral-frames 0
```

When `--final-pre-neutral-frames` is nonzero, those fixed neutral frames are
folded into the effective branch-relative SID commit offset before forecasting.

If the first live proof observes a different SID, the SID bot searches nearby
RNG advances, updates the runtime offset, writes it into the ledger, and
rebuilds the forecast for remaining shiny values.

Ledger rows are built from observed live SID data after calibration. The
`rng_advance`, `predicted_sid`, and `predicted_rng` fields therefore describe
the actual exported save, even if the branch was selected by an older forecast
row before the offset adjustment.

Use `--strict-prediction` only when validating a frozen route. Without it, the
bot can accept an actual still-missing shiny value and keep going.

If the observed SID maps to an already-complete shiny value and calibration
cannot explain the mismatch, the bot raises an error. That prevents an
unchanged bad forecast from replaying the same losing branch forever.

## Resume Safety

On `--resume`, the SID bot validates:

- ledger format
- ledger TID against requested TID
- save file existence for each completed row
- save file TID/SID and party-slot-1 Bulbasaur proof for each completed row

Completed rows whose save files have only stale TID/SID data are repaired in
place and keep their rows complete with a fresh SHA-1. Missing saves or saves
that cannot be repaired are reset to retryable rows and kept in the ledger with
their old SID/path data as audit breadcrumbs.

`--only-shiny-value` applies to resumed ledgers too. This allows a small proof
or repair run against one shiny-value range without rebuilding the full ledger.
The same selection gates observed live SIDs, so a repair run cannot silently
fill an unrelated missing row outside the requested range.

## TSV Save Tracker GUI

`TSV-Save-Tracker-GUI.py` is a read-only Flask dashboard for the local TSV save
bank. It scans:

```text
<repo-root>\TSVs\
```

Expected filename format:

```text
TSV-0000-sid-00003.sav
TSV-8191-sid-65535.sav
```

The tracker always builds an 8192-row view. A row is complete when a valid
local save exists for that TSV or when the ledger points to an existing save.
It flags duplicate saves for one TSV, ledger rows whose save path is missing,
old/unrecognized `.sav` names, and files where `SID >> 3` does not equal the
filename TSV.

The browser page has a live progress panel modeled after the Phase 3 command
center style: JavaScript polls `/api/status` every two seconds for completion,
rate, ETA, finish time, recent saves, and SID live-log activity, then polls
`/api/rows` every five seconds to refresh the visible table. The live-log read
uses only a bounded tail of `_sid_live_status.log`; this keeps refresh cheap
even after many hours of SID bot messages.

The tracker is intentionally solo-compute and read-only:

- default host is `0.0.0.0`, so same-LAN devices can browse to this PC's IP
- Flask may handle multiple browser/API requests
- no worker queue, remote host registry, or multi-PC status protocol exists
- routes only read local files

Useful endpoints:

```text
http://127.0.0.1:8765/
http://<this-pc-ip>:8765/
http://127.0.0.1:8765/api/status
http://127.0.0.1:8765/api/rows
http://127.0.0.1:8765/api/tsv/0
```

On this machine the likely LAN endpoint is:

```text
http://10.0.0.66:8765/
```

If another device cannot open the page, Windows Firewall is likely blocking
inbound TCP `8765`. Allow Python on the active network or run the terminal as
Administrator and add an inbound TCP rule for port `8765`.

## TSV Party Slot Verifier

`tools/verify_tsv_party_slot/VerifyTsvPartySlot.csproj` is a standalone .NET
checker that uses PKHeX.Core and never touches mGBA. It verifies the existing
TSV save bank in:

```text
<repo-root>\TSVs\
```

For every stable `TSV-xxxx-sid-xxxxx.sav`, it checks:

- filename SID maps to filename TSV using `(TID ^ SID) >> 3` with TID `0`
- filename TSV and SID are within Gen 3 ranges: `0..8191` and `0..65535`
- save-level TID is `0`
- save-level SID equals the filename SID
- party slot 1 exists
- party slot 1 is Bulbasaur, is not still an egg, and has a valid checksum
- party slot 1 TID/SID equals TID `0` plus the filename SID

To avoid false failures while the SID bot is exporting a save, the verifier
checks file size and write time, waits briefly, then checks them again after
reading. A changing file is reported as `in_progress` and skipped unless
`--strict-in-progress` is passed.

Run:

```text
dotnet run --project <repo-root>\tools\verify_tsv_party_slot\VerifyTsvPartySlot.csproj -- --save-dir <repo-root>\TSVs
```

Report:

```text
<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json
```
