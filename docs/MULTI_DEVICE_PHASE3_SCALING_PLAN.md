# Multi-Device Phase 3 Scaling Plan

## Status Bucket

- Current status: Operational planning guide for running Phase 3 across more than one trusted machine.
- Last verified date: 2026-05-02.
- Proven artifacts: Current Phase 3 worker pool, command center, watcher,
  Assisted-baking package, Phase 2 pickup states, PID-named Phase 3 lane ZIP
  format, command-center coordinator/subordinate heartbeat mode, persistent
  coordinator lane ledger/claim API, command-center online launches through
  `phase3_ledger_worker_client.py`, trusted lane-range grand total counting,
  and minimum-size guards for final lane ZIPs.
- Known gaps: The coordinator-led assignment path has tests, but the first
  offsite workstation run still needs a small proof batch and merge test.
  Linux helper-node code exists but has not yet completed a live Linux lane.
- Next action: Reconcile existing finished ZIPs into the ledger, then assign a
  small claimed lane batch to the offsite workstation and validate returned
  ZIPs before expanding range size.

## Goal

Use more than one PC to process Phase 3 lanes without duplicate work.

One lane equals one lower PID half:

```text
0x0000 through 0xFFFF
```

Each finished lane produces:

```text
Phase3SpindaBlocks\0x####.spinda80.zip
```

No machine should intentionally process a lane already assigned to another
machine unless doing a controlled redundancy test.

## Current Command-Center Network Base

The Flask command center now has explicit roles:

```text
coordinator
subordinate
```

Online coordination is opt-in. A coordinator accepts subordinate heartbeats
and owns lane assignment. A subordinate may host local workers, but in online
mode those workers are launched through the ledger client, so every batch is
claimed from the coordinator before native mGBA work starts.

Coordinator example:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role coordinator -Online -AdvertiseHost 192.168.1.10 -AdvertisePort 235
```

Subordinate example:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role subordinate -Online -PrimaryHost 192.168.1.10 -PrimaryPort 235 -AdvertiseHost 192.168.1.21 -AdvertisePort 235
```

HTTPS coordinator example:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role subordinate -Online -PrimaryScheme https -PrimaryHost math.hyddwn.net -PrimaryPort 443 -AdvertiseHost 192.168.1.21 -AdvertisePort 235
```

The same panel also supports the persistent lane ledger. Helpers should use
the ledger worker client to claim and finish lanes. Status-only heartbeat is
only a fallback/visibility path for old already-running workers.

The same network fields are editable in the command-center browser UI under
`Multi-device coordination`. That panel writes
`Phase3SpindaBlocks\_phase3_command_center_network.json`, so a restarted panel
keeps the selected coordinator/subordinate settings.

Pathing rule: command-center and watcher defaults are relative to the package
root that contains `tools/spinda`, or to the explicit `--folder` argument.
Assisted helper folders must not read ledger-client mirrors from
`<repo-root>`; each helper uses its own
`Phase3SpindaBlocks\_phase3_ledger_worker_client_status.json`.
The command-center wrapper now passes the folder, pool status, watcher status,
ledger-client status, coordination settings, ledger, and cache paths
explicitly so helper packages keep all sidecars inside their own folder.

The command center stores scheme separately from host. It strips pasted host
slashes, so `https://host/` becomes `https://host:443` instead of malformed
`http://host/:443`.

The browser UI exposes HTTP/HTTPS selectors for both primary and advertised
URLs. Use `https` when a reverse proxy or public TLS endpoint fronts the
coordinator; use `http` for trusted LAN/VPN access.

## Core Rule

One lane has one owner at a time.

Ownership source must be outside worker memory, because workers can crash,
restart, or lose local status. Use a simple lane ledger.

Minimum ledger columns:

```text
lane_range | owner | status | assigned_at | started_at | finished_at | returned_at | verified_at | notes
```

Implemented coordinator ledger file:

```text
Phase3SpindaBlocks\_phase3_lane_ledger.json
```

Implemented API:

```text
GET  /api/ledger/status
POST /api/ledger/reconcile
POST /api/ledger/claim
POST /api/ledger/heartbeat
POST /api/ledger/finish
POST /api/ledger/fail
POST /api/ledger/release
```

Current ledger behavior:

- pending lanes are implicit
- normal claim ranges are endpoint-inclusive (`0x0000-0xFFFF`) because Phase 3
  now has validated pickup states for both endpoint lanes
- final ZIPs can be reconciled into `done`
- claims have leases
- stale leases become reclaimable
- expired leases appear as `expired_claims`, not active claim ranges, so stale
  helper rows do not make the scheduler/UI think dead work is still active
- devices heartbeat to extend lease
- devices finish lanes with required `zip_size` proof and `pk3_count=65536`;
  `zip_sha256` is optional
- duplicate finished lanes should be quarantined during merge if hashes differ
- subordinate command centers send their ledger summary and compact lane ranges
  in heartbeats
- subordinate command-center launches write
  `_phase3_ledger_worker_client_status.json` locally and include that live
  active-lane mirror in coordinator heartbeats
- current subordinate heartbeats also send worker-pool ranges separately from
  ledger-client ranges, so the coordinator can keep real running pool lanes
  while ignoring a stale ledger-client mirror
- for legacy helpers without split fields, the coordinator can infer active
  pool lanes from `active_lane_ranges - complete_lane_ranges` when completed
  lanes overlap the stale mirror, preserving the still-running lane in a bundle
- subordinate command centers ignore stale ledger-client mirrors for active
  lanes and worker counts, so a dead ledger client cannot keep renewing old
  lane reservations forever just because the web panel is still alive
- the coordinator sanitizes old helper heartbeats too: stale ledger-client
  ranges are removed before active lane imports, protecting the scheduler even
  while older helper packages are still running
- the coordinator reconciles active claims from helpers that do report a
  ledger-client sidecar: lanes owned by that helper but missing from the
  helper's current active set are released back to pending instead of blocking
  assignment until the long production lease expires
- coordinator-local claims are also synced to current worker-pool bundle
  ranges, so an old local ledger-client mirror cannot keep stale local lanes
  leased while a newer worker pool is actually running different bundles
- stale remote-active heartbeat imports from dead panels are released after the
  heartbeat freshness window; final `done` rows stay persistent evidence
- the coordinator grand total unions local ZIP lane ranges, coordinator ledger
  done ranges, subordinate `ledger.done_ranges`, and subordinate
  `health.complete_lane_ranges`, so overlapping reports do not inflate the
  total
- bare legacy done counts are diagnostic only because they cannot identify
  exact lanes or dedupe overlap
- subordinate active worker ranges are imported as leased `running` rows, so
  coordinator lane claims avoid lanes already being processed elsewhere
- stale remote worker counts are ignored after the heartbeat freshness window,
  while already-imported done lane ranges remain persistent ledger evidence
- coordinator-role local worker launches claim from the coordinator's own panel
  URL, while subordinate launches claim from the configured primary URL

Assisted worker client:

```text
tools\spinda\phase3_ledger_worker_client.py
```

Use it on a helper machine after setting coordinator URL and lane range. It
claims a batch, runs the native worker pool locally, heartbeats during the run,
then reports finish/fail for every claimed lane.

The client does not claim another batch until finish/fail reporting succeeds.
This avoids duplicate reassignment when a coordinator disappears briefly after
a helper already wrote ZIPs. If a claimed lane already has a valid local ZIP of
at least `1024` bytes, the client reports it instead of rerunning the lane.
The command-center heartbeat reads the same local ledger-client status file, so
active lane ranges and just-finished ZIPs reach the coordinator even before a
large batch fully exits.

Linux helper launcher:

```text
tools/spinda/build_phase3_cli_linux.sh
tools/spinda/run_phase3_ledger_helper.sh
```

Linux helper nodes run only the native Phase 3 CLI. They do not need Qt. See
[PHASE3_LINUX_HELPER_NODE.md](PHASE3_LINUX_HELPER_NODE.md) for exact build and
launch commands.

Recommended statuses:

```text
unassigned
assigned
running
returned
verified
rejected
reassigned
duplicate
```

## Package Shape For Helper Machine

Use `Assisted-baking` package as base.

Included:

- clean source/tools
- native Phase 3 CLI
- `secondhalf.csv`
- shared Phase 3 cache
- all Phase 2 pickup states
- start/collect scripts
- already-done lane list at package time

Not included:

- ROM
- completed local output ZIPs
- private account data

Helper supplies their own legal ROM:

```text
Assisted-baking\inputs\lg.gba
```

## Best First Offsite Test

Do not start with thousands of lanes.

Start with one small chunk:

```text
0x0200-0x020F
```

or if those already exist:

```text
next 16 unassigned lanes
```

Why:

- proves toolchain
- proves ROM path
- proves worker output
- proves return packaging
- proves merge/validation
- limits wasted time if offsite machine misconfigured

After proof:

```text
64 lanes per assignment
256 lanes per assignment
1024 lanes per assignment
```

Increase only after returned results validate cleanly.

## No-Duplicate Assignment Methods

### Method 1: Manual Ledger

Use a spreadsheet or Markdown table. Lowest tech, high reliability.

Example:

```text
0x0000-0x016D | owner-pc | verified | ...
0x016E-0x01FF | owner-pc | running  | ...
0x0200-0x02FF | offsite1 | assigned | ...
```

Rule:

- helper only runs assigned range
- owner machine avoids helper range
- returned ZIPs get validated before status changes to `verified`

### Method 2: Central Assignment File

Create one shared JSON file:

```json
{
  "0x0200-0x02FF": {
    "owner": "offsite1",
    "status": "assigned",
    "assigned_at": "2026-05-01T12:00:00-05:00"
  }
}
```

Use cloud drive, private Git repo, Syncthing, SMB share, or manual copy.

Risk:

- sync conflict can create bad assignments

Mitigation:

- only one person edits ledger
- helpers request ranges, owner grants ranges

### Method 3: Central Pull Queue

Best long-term model, not built yet.

One small service owns lane queue:

- helper asks for next range
- service marks range leased
- helper reports done
- service marks verified after returned ZIPs pass checks

Lease fields:

```text
owner
range
lease_started
lease_expires
heartbeat_time
```

This prevents duplicates even with many helpers.

Downside:

- needs new scheduler code
- more moving parts

### Method 4: Static Range Partition

Split by hex bands:

```text
Owner PC:    0x0000-0x7FFF
Offsite PC: 0x8000-0xFFFF
```

Simple. No central scheduler needed.

Downside:

- if one machine much faster, one side finishes early
- reassignment later requires care

Better static split:

```text
Owner PC:    even chunks
Offsite PC: odd chunks
```

Example chunk size `0x0100`:

```text
Owner:    0x0000-0x00FF, 0x0200-0x02FF, ...
Offsite: 0x0100-0x01FF, 0x0300-0x03FF, ...
```

This balances if lane time varies across ranges.

## Recommended Practical Plan

Use manual ledger first.

Use chunk size:

```text
0x0100 lanes = 256 lanes per assignment
```

Why:

- small enough to reassign after crash
- large enough to avoid constant coordination
- easy hex boundaries
- output count easy to verify

First offsite assignment:

```text
0x0200-0x02FF
```

if unassigned and not already complete.

Owner PC should exclude that range from its next worker launch.

## Worker Launch On Helper PC

Inside `Assisted-baking`:

```powershell
.\START_ASSISTED_WORKERS.cmd
```

Use assigned lane range when prompted:

```text
0x0200-0x02FF
```

Suggested worker count:

```text
start with physical cores / 2
```

Then tune:

- if CPU below 90%, add workers
- if CPU 100% and lanes/hour not improving, stop adding
- if memory low, reduce workers
- if disk queue high, reduce workers

## Owner PC Launch With Exclusions

Current worker pool does not understand exclusion ranges directly. Use one of
these instead:

### Option A: Run owner on separate lane ranges

Example:

```text
owner current: 0x0000-0x01FF
offsite:      0x0200-0x02FF
owner next:   0x0300-0xFFFF
```

### Option B: Let owner skip completed returned ZIPs later

Less ideal. It avoids final duplicate artifacts but still wastes time if both
machines process same lane before return.

### Option C: Generate explicit owner ranges

Use ledger to launch only unassigned ranges. Best with batch files or future
queue tool.

## Return Package From Helper

Helper should send back only:

```text
Phase3SpindaBlocks\0x####.spinda80.zip
```

Optional:

```text
_native_phase3_worker_pool_status.json
_phase3_independent_watcher_status.json
```

Do not send:

- ROM
- Phase 2 states
- `secondhalf.csv`
- cache files
- source tree

Helper can use:

```powershell
.\COLLECT_ASSISTED_RESULTS.ps1
```

## Merge Flow On Owner PC

1. Put returned ZIPs in staging folder:

```text
<repo-root>\incoming-assisted\offsite1\YYYY-MM-DD\
```

2. Run validation in staging, no extraction:

```powershell
python <repo-root>\tools\spinda\phase3_zip_validator.py `
  --root <repo-root>\incoming-assisted\offsite1\YYYY-MM-DD `
  --allow-incomplete
```

3. Check names against ledger.

Reject if:

- lane outside assigned range
- duplicate lane already verified
- bad file name
- zero-size ZIP
- tiny ZIP
- corrupt ZIP

4. Copy accepted ZIPs into main output:

```text
<repo-root>\Phase3SpindaBlocks\
```

5. Run manifest-only validation on main output:

```powershell
python <repo-root>\tools\spinda\phase3_zip_validator.py `
  --root <repo-root>\Phase3SpindaBlocks `
  --manifest-only `
  --allow-incomplete
```

6. Mark ledger rows `verified`.

## Duplicate Handling

If duplicate ZIP for same lane exists:

1. Do not overwrite automatically.
2. Move returned duplicate to quarantine:

```text
<repo-root>\incoming-assisted\quarantine\
```

3. Compare ZIP manifest and hash if needed.
4. Keep owner-verified file unless returned file is needed for forensic check.

If both are byte-identical:

- mark returned copy `duplicate`
- delete or archive later

If not byte-identical:

- deep validate both
- do not count lane complete until resolved

## Hashes To Pin

Before sending offsite package, record hashes:

```text
mgba-spinda-phase3.exe
secondhalf.csv
Phase3SpindaBlocks\_cache files
Phase2PickupStates count and sample hashes
```

At minimum:

```powershell
Get-FileHash .\build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe -Algorithm SHA256
Get-FileHash .\inputs\secondhalf.csv -Algorithm SHA256
Get-FileHash .\Phase2PickupStates\0x0000.ss0 -Algorithm SHA256
Get-FileHash .\Phase2PickupStates\0xFFFF.ss0 -Algorithm SHA256
```

Why:

- proves helper used same schedule
- catches stale package
- helps explain final reproducibility

## Security Notes

Offsite helper receives real Phase 2 states and schedule data. Treat package as
private until publication.

Use:

- encrypted archive
- private transfer
- checksum after download
- trusted machine only

Do not include ROM.

Do not include generated PK3/Spinda ZIPs unless sending result batch back.

## Failure Cases

### Helper crashes mid-range

Returned files still valid if individual ZIPs validate.

Ledger action:

- mark returned lanes `verified`
- mark missing lanes `reassigned`

### Helper runs wrong range

Do not merge outside assigned range unless owner explicitly accepts them.

Ledger action:

- mark as `duplicate` or `reassigned accepted`

### Helper uses old package

Detect with hashes.

Action:

- reject batch if executable or `secondhalf.csv` hash mismatch
- rebuild package from current owner machine

### Helper output has bad ZIPs

Action:

- quarantine whole returned batch
- validate per ZIP
- accept good lanes only
- reassign bad lanes

### Owner and helper both finish same lane

Action:

- keep first verified copy
- quarantine duplicate
- compare later if useful

## Scaling Beyond Two Machines

Once more than two machines exist, manual ranges get harder. Move toward:

1. central ledger file
2. chunk leases
3. helper identity
4. heartbeat timestamp
5. auto-expire stalled leases
6. result upload staging
7. owner-only final merge

Good chunk sizes:

```text
small proof: 16 lanes
normal helper: 256 lanes
fast trusted helper: 1024 lanes
```

Avoid giving one helper tens of thousands of lanes until reliability proven.

## Future Tool Ideas

### Lane Assignment Generator

Inputs:

- completed ZIP folder
- current ledger
- requested helper name
- chunk size

Output:

- next safe lane range
- updated ledger row
- command to run

### Incoming Batch Validator

Inputs:

- staging folder
- assigned range
- main output folder

Checks:

- names valid
- range valid
- no duplicates
- ZIPs open in RAM
- entry count valid if deep mode requested

Output:

- accepted list
- rejected list
- copy command

### Central Queue Flask Page

Add page to command center:

- machine name
- active assignment
- done returned
- verified count
- stale leases
- duplicate risk

This becomes useful if more than two helper machines join.

## Best Near-Term Choice

For one offsite workstation:

1. Use `Assisted-baking`.
2. Assign `256` lanes.
3. Keep owner PC off that range.
4. Validate returned ZIPs in staging.
5. Merge only after validation.
6. Increase helper range after first clean return.

This gives speedup without building full distributed scheduler yet.
