# FR/LG ID Bots

New standalone bots for the ID stage. These do not replace or overwrite the
older initial-seed brute-force scripts.

## Roadmap Position

This folder owns the roadmap's TID0/TSV save-bank step. The save bank keeps
Trainer ID fixed at `0` and creates one `.sav` for each Trainer Shiny Value.

Proven as of 2026-05-06:

- `8192 / 8192` TSV saves exist in `<repo-root>\TSVs`
- standalone verifier result:
  - `checked=8192 ok=8192 failed=0 errors=0 invalid_names=0 in_progress=0`
- verifier report:
  - `<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json`
- backup ZIP:
  - `<repo-root>\Artifacts\TSV-save-backups\TSVs-save-backup-20260506-150751.zip`
- ledger:
  - `<repo-root>\TSVs\_sid_shiny_value_ledger_tid_0x0000.json`
- save naming:
  - `TSV-xxxx-sid-xxxxx.sav`
  - decimal TSV and decimal SID

The next roadmap step consumes these verified saves for mass hatching once the
Phase 3 egg ZIP corpus is ready: matching TSV saves hatch Spinda eggs shiny,
non-matching TSV saves hatch control outputs non-shiny, and the two result
groups are packaged as separate ZIP subsets. These ID bots create and verify
save contexts; they do not do the mass hatch.

## Trainer ID Bot

Script:

```text
Trainer-ID-Bruteforcer.py
```

Assumption:

- emulator is already paused at the last input before TID generation
- no physical button is held
- the final input is `A`
- pressing `A` causes `SeedRngAndSetTrainerId()`

Default target:

```text
TID = 0x0000
```

The bot enables Qt audio killswitch, no-render mode, and unbounded
fast-forward when those bridge hooks are available. It then captures the branch
state, restores it for each delay, waits neutral frames, runs one neutral
release frame by default, presses `A` for two frames by default, waits for
Timer 1 to stop, and reads the temporary TID mirror at `0x02020000`. FR/LG
checks a new-press edge here; use `--pre-press-neutral-frames 0` only from a
proven clean neutral branch. On hit it saves the state, pauses the visible core
when possible, and writes:

```text
<repo-root>\FRLGIDBots\TrainerID\tid-0x0000-hit.ss0
<repo-root>\FRLGIDBots\TrainerID\tid-0x0000-hit-metadata.json
```

These TID hit files are overwritten on every successful run. Use
`--success-state` only when you want a different state path; the metadata still
records the path that was written.

On success it prints a JSON summary in the mGBA scripting output and returns
normally. It does not raise `SystemExit`, because mGBA displays even
`SystemExit(0)` as `[ERROR]`. The summary and metadata include the Qt runtime
settings status and final pause status.

The scan uses a rolling no-input checkpoint. In Qt mode this stays in the
in-memory scratch slot; if scratch state is unavailable, it falls back to:

```text
<repo-root>\FRLGIDBots\TrainerID\_tid-bruteforce-rolling.ss0
```

That keeps the scan linear and avoids hot-loop disk I/O on the normal Qt path.
A miss reloads the rolling state, advances one neutral frame, and saves the
next candidate. It does not replay all prior delay frames from the original
anchor on every attempt.

Example:

```text
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-id-bots\Trainer-ID-Bruteforcer.py
```

## Secret ID Shiny-Value Bot

Script:

```text
Secret-ID-Shiny-Value-Bot.py
```

Setup helper:

```text
Secret-ID-Pause-After-Generation.py
```

Ready-state precalculator:

```text
Secret-ID-Ready-State-Precalculator.py
```

Assumption:

- TID `0x0000` already exists
- SID has not been generated yet
- emulator is paused at the last input before SID generation
- no physical button is held
- final input is `A`

The TID is read-only in this workflow. The bot verifies that the live mirror and
final SaveBlock2 IDs still use TID `0x0000`, but it does not write trainer ID
memory.

When launched from the trainer-ID hit state instead of the exact pre-SID
branch, the live bot tries to set itself up. It taps the final button forward
from the TID hit state, saves scratch before each tap, watches for SaveBlock2
to become TID `0x0000`, then restores the scratch state from immediately
before that tap. Only after this auto-setup does it read the branch `gRngValue`
and build the SID forecast.

Live Qt runs refuse to create a ledger until a branch proof passes. A fresh ROM
often shows `gRngValue == 0x00000000`; the bot may use auto-setup from a real
TID hit state, but otherwise it stays open, pauses, and waits instead of
writing a save or ledger from a bad branch. The zero-RNG guard runs before the
scratch probes so stale SaveBlock2 values such as `0x0000/0x0000` cannot be
mistaken for a real SID generation.

Before creating the ledger, live Qt runs probe the final input on scratch
state and restore the branch. A second probe waits 31 extra neutral frames. The
script proceeds only if both probes keep TID `0x0000` and the SID changes;
otherwise it checks whether the branch is RNG-frozen. If the same SID appears
after the 31-frame probe, the bot switches to deterministic TID-state schedule
mode. It reloads and pauses on the read-only TID hit state, records the loaded
state `gRngValue`, and writes the real repeated-A route-cycle schedule. That
schedule is not allowed to pretend the frozen branch can reach all 8192 TSVs:
it is built for the currently missing ledger rows, so already-complete TSVs do
not block resume. If any missing TSV is unreachable from this state, the bot
stops before route tape replay or save export and records `route_schedule_error`
in the ledger. A live SID must match the scheduled SID/TSV before any route tape
replay or save export happens.

Schedule file:

```text
<repo-root>\TSVs\_sid_tsv_earliest_schedule_tid_0x0000.json
```

Default tape:

```text
<repo-root>\SiD_RNG_After.json
```

The tape is replayed after each SID hit. It should carry the game to the first
starter so Spinda eggs can later be injected and hatched, then commit an
in-game save before it ends.

After the tape finishes, the SID bot exports only the battery save file
(`.sav`). It does not write a savestate for SID runs. mGBA exports active
cartridge save data, not unsaved WRAM progress, so the route tape should
include the game's save flow before export.

Before accepting an export, the bot parses the temp `.sav`, rewrites the
save-level TID/SID and party-slot-1 owner IDs to the observed hit, rebuilds the
Pokemon and Gen 3 sector checksums, then validates TID `0`, the observed SID,
and party slot 1 as a valid hatched Bulbasaur. This touches only the exported
file, not live trainer ID memory.

If a temp export fails that Bulbasaur proof, the bot waits extra neutral frames
and retries the battery export before giving up on that SID attempt. A
persistent export-only failure is written to the ledger as retryable, then the
live loop asks the LCRNG forecast for the next later SID hit that maps to the
same shiny value. By default it tries up to eight alternate hits before blocking
that row for the rest of the current process, so one bad cartridge-save
snapshot does not stop the full 8192-row run and does not require hand repair
unless every alternate also fails.

Use `Secret-ID-Pause-After-Generation.py` before recording that tape. Start
from the same pre-SID final-input branch, run the helper, and it will generate
whatever SID comes next, wait for the post-SID point, pause, and turn no-render
back off by default so the screen is visible for tape setup. It does not export
a save or write the shiny-value ledger. SID commit waits default to `360`
post-input frames because the root TID-0-ready state updates SaveBlock2 around
post-frame `272`; shorter waits can read stale `0x8E76/0x3C2F` IDs.

The bot reads the live pre-SID `gRngValue`, predicts SID results with the GBA
LCRNG, and branches once per missing shiny value. It does not brute-force every
emulator frame. For a true pre-SID branch, the forecast checks one-frame wait
positions in the LCRNG stream and writes a raw SID coverage proof:

```text
<repo-root>\TSVs\_sid_raw_lcrng_coverage_tid_0x0000.json
```

That proof scans in 1,000,000-frame chunks until every possible 16-bit SID has
appeared. From the current TID 0 branch math, the first chunk is enough: all
65,536 SIDs appear by wait frame `726440`, and all 8192 shiny values appear by
wait frame `65710`. The live save plan still targets one SID per missing shiny
value, choosing the earliest useful wait.

For the root ready savestate:

```text
<repo-root>\tid 0 ready.ss0
```

use `Secret-ID-Ready-State-Precalculator.py` to prove and precalculate the full
delay ledger without overwriting the savestate:

```text
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-id-bots\Secret-ID-Ready-State-Precalculator.py
```

That state has TID mirror `0x0000`, branch RNG `0xEAD1C0A3`, and live-proven
SID commit offset `273`. Its timing formula is:

```text
rng_advance = sid_commit_offset + max(wait_frames, 1)
```

Wait `0` and wait `1` both hit SID `0x7ED3`; the first neutral frame is the
release edge before the final `A` can be consumed. From wait `2` onward, each
extra neutral frame advances the LCRNG by one. The precalculator writes:

```text
<repo-root>\TSVs\_sid_ready_tid0_precalc_report.json
<repo-root>\TSVs\_sid_ready_tid0_all_sid_delays.json
<repo-root>\TSVs\_sid_ready_tid0_all_tsv_delays.json
```

Current proved coverage from that state is all `65,536` SIDs by wait `726412`
and all `8,192` TSVs by wait `65682`.

When driving this ready state with `Secret-ID-Shiny-Value-Bot.py`, use:

```text
--sid-commit-offset 273 --min-wait-frames 1
```

`--min-wait-frames 1` prevents the normal linear forecast from scheduling the
unusable fake wait `0` point at advance `273`; wait `1` is the first real
forecast point and maps to advance `274`.

For one-off validation runs through `run_secret_id_sid_bot_with_tracker.ps1`,
pass `-WaitForSaveTsv <0..8191>`. The launcher watches for the padded decimal
save name that the bot actually writes:

```text
TSV-xxxx-sid-yyyyy.sav
```

For example, TSV `860` is detected as `TSV-0860-sid-*.sav`, not
`TSV-860-sid-*.sav`. When the matching save appears, the launcher stops the
visible mGBA process so a single-iteration validation run does not sit open.

In deterministic TID-state schedule mode, it precomputes the
actual route-cycle residues from the TID hit loadstate instead of accepting
whatever value happens to appear next. With the current repeated-A setup, adding
neutral wait before the tap loop only shifts the SID by
`(sid_commit_offset + wait_frames) % setup_cycle_frames`; this exposes the
reachable TSVs and the unreachable list in the schedule JSON. In this mode,
each row's `branch_rng` is the frozen branch used as the cycle origin, and
`rng_advance` is the effective route-cycle residue from that origin. The LCRNG
helper can skip ahead in `O(log n)` time and reuses a precomputed affine jump
for fixed neutral-frame strides.

Live SID runs also enable Qt audio killswitch, no-render mode, forced
fast-forward, and unbounded fast-forward ratio when the bridge exposes those
hooks. The final JSON summary includes `runtime_settings` so you can see which
toggles were applied or already enabled.

If live SID observation shows the commit offset differs from the configured
value, the bot infers the nearby offset and rebuilds the forecast for remaining
missing shiny values. If an observed SID lands on an already-complete shiny
value and no offset adjustment explains it, the run stops instead of looping.
When calibration changes the offset, the ledger row stores the observed
calibrated RNG advance and SID, not the stale pre-calibration forecast row.
If the SID branch also needs a release edge, pass
`--final-pre-neutral-frames N`; those frames are folded into the effective SID
commit offset used by the forecast and ledger.

Default output:

```text
<repo-root>\TSVs\
```

Save names use decimal TSV and SID values:

```text
TSV-0000-sid-00003.sav
TSV-8191-sid-65535.sav
```

Ledger:

```text
_sid_shiny_value_ledger_tid_0x0000.json
```

The ledger maps each shiny value to the actual SID hit for TID `0x0000`.
Live status breadcrumbs are appended beside it:

```text
_sid_live_status.log
```

Resume behavior:

- completed rows are trusted only if their exported save still exists
- missing save files are reset to retryable rows
- `--only-shiny-value` filters the forecast even when resuming a larger ledger
- `--only-shiny-value` also limits which observed shiny values can be accepted

## TSV Save Tracker GUI

Script:

```text
TSV-Save-Tracker-GUI.py
```

The GUI is a local Flask dashboard for the 8192 `.sav` files in:

```text
<repo-root>\TSVs\
```

It scans decimal save names like `TSV-0000-sid-00003.sav`, checks that each SID
maps to the filename TSV under TID `0`, reads the local ledger when present,
and shows done, missing, duplicate, mismatched, and ledger-missing rows.
The top panel updates itself from `/api/status`, showing live completion,
save/hour rate, ETA, finish time, recent saves, and whether the SID bot log is
still active. The row table refreshes from `/api/rows` without a full page
reload. Status polling reads only a bounded tail of `_sid_live_status.log`, so
long runs do not make the browser re-read a multi-hour log every few seconds.

This tracker is read-only and solo-compute. It binds to `0.0.0.0` by default so
other devices on the same LAN can open it with this PC's IP and port. It still
does not add multi-PC workers, queues, or coordination. The Flask server allows
multiple browser/API requests so an open browser tab cannot block status checks.

Launch:

```text
python <repo-root>\doc\python-examples\frlg-id-bots\TSV-Save-Tracker-GUI.py
```

Open:

```text
http://127.0.0.1:8765/
http://<this-pc-ip>:8765/
```

On this machine, the normal LAN URL is usually:

```text
http://10.0.0.66:8765/
```

If another device cannot open the page, Windows Firewall is likely blocking
inbound TCP `8765`. Allow Python on the active network or run the terminal as
Administrator and add an inbound TCP rule for port `8765`.

## TSV Party Slot Verifier

Standalone verifier:

```text
<repo-root>\tools\verify_tsv_party_slot\VerifyTsvPartySlot.csproj
```

This is separate from mGBA. It uses PKHeX.Core to inspect every existing
`TSV-xxxx-sid-xxxxx.sav` in `<repo-root>\TSVs\`. For TID `0`, it checks that
the filename numbers are in range, the filename SID maps to the filename TSV,
the save trainer TID/SID match the target, and party slot 1 is a valid hatched
Bulbasaur owned by TID `0` and the filename SID.

The verifier waits briefly before reading each save and confirms the file did
not change during the read. A save being written mid-run is reported as
`in_progress` instead of a false parse failure.

Run:

```text
dotnet run --project <repo-root>\tools\verify_tsv_party_slot\VerifyTsvPartySlot.csproj -- --save-dir <repo-root>\TSVs
```

Default report:

```text
<repo-root>\TSVs\_party_slot1_bulbasaur_verification.json
```

## Dry Plan

To build only ledger and forecast JSON:

```text
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\doc\python-examples\frlg-id-bots\Secret-ID-Shiny-Value-Bot.py --dry-plan --start-rng 0x12345678
```

For a small live proof:

```text
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-id-bots\Secret-ID-Shiny-Value-Bot.py --only-shiny-value 0x0000-0x0003 --limit 1 --overwrite
```

To pause after SID generation for recording `SiD_RNG_After.json`:

```text
<repo-root>\build-mingw64-python-qt\mGBA.exe --script <repo-root>\doc\python-examples\frlg-id-bots\Secret-ID-Pause-After-Generation.py
```
