# Phase 3 Command Center Guide

## Status Bucket

- Current status: Current guide for the low-overhead Flask command center.
- Last verified date: 2026-05-04.
- Proven artifacts:
  - `tools/spinda/phase3_command_center_web.py`
  - `tools/spinda/phase3_command_center.ps1`
  - `tools/spinda/phase3_command_center.cmd`
  - `tools/spinda/phase3_ledger_worker_client.py`
  - `tools/spinda/native_phase3_worker_pool.py`
- Known gaps: The command center reports production state; it does not prove
  PK3 semantic correctness.
- Next action: Update this guide whenever command-center controls, counters,
  endpoints, or refresh defaults change.

## Evidence Split

### Proven

- Default port is `235`.
- Wrapper launch starts the command center and independent watcher.
- The page shows completed lanes, exact Spinda count, worker slots, projected
  finish, output health, validation policy, watcher status, and multi-device
  coordination role.
- The page defaults to dark mode. The header toggle switches between dark and
  light mode and persists only in the browser.
- Hot status reads ZIP filenames and status JSON; it does not inspect each ZIP
  entry.
- Host CPU/RAM/disk sampling is cached separately from worker-status refresh.
- The command center can run as either `coordinator` or `subordinate`.
- Online coordination mode is off by default and must be enabled explicitly.
- Network role, online mode, primary endpoint, advertised endpoint, and
  heartbeat interval can be changed from the Flask UI.
- Command-center defaults are package-root relative. A copied helper package
  under `D:\Assisted-baking\Assisted-baking` reads and writes its own
  `Phase3SpindaBlocks` status files, not `<repo-root>`.
- The PowerShell wrapper also passes `--folder`, pool status, watcher status,
  ledger-client status, coordination settings, ledger, and cache paths
  explicitly. This keeps a moved helper package from accidentally reading a
  stale status file from another workspace.
- Primary and advertised endpoints support explicit `http` or `https`
  schemes. Host fields are sanitized so pasted values such as
  `https://example.com/` do not produce malformed URLs.
- The network settings panel exposes HTTP/HTTPS selectors for both the primary
  coordinator URL and this panel's advertised URL.
- The default lane range is the full endpoint-inclusive corpus:
  `0x0000-0xFFFF`. Operators can narrow the range for tests, but normal
  production should leave endpoints included because the `0x0000` and
  `0xFFFF` pickup states exist.
- The coordinator exposes a persistent lane ledger with claim, heartbeat,
  finish, fail, release, and reconcile endpoints.
- Coordinator-role local workers claim from the command center's own local
  panel URL, not from the editable `primary` URL used by subordinate machines.
  This prevents a coordinator from accidentally launching local workers against
  another scheduler after network-setting edits.
- In online mode, the command-center `Apply / launch workers` button launches
  `phase3_ledger_worker_client.py`, not the native pool directly. The ledger
  client claims from the coordinator first, then runs the native pool only for
  claimed lanes.
- The ledger client writes
  `Phase3SpindaBlocks\_phase3_ledger_worker_client_status.json` as a small
  local mirror of claimed/running lane ranges. Subordinate heartbeats include
  that mirror so the coordinator does not need each worker's individual
  `_0x####.phase3_status.json` files.
- The command center reads that ledger-client mirror from the active output
  folder. This matters for Assisted-baking and offsite helper folders because a
  stale `<repo-root>` mirror can make active/done reports look wrong.
- Ledger-client mirrors are trusted only while fresh. A stale
  `_phase3_ledger_worker_client_status.json` can still be displayed for
  diagnosis, but it no longer contributes active lane ranges or worker counts
  to subordinate heartbeats or the local command-center totals.
- Subordinate heartbeats now split worker-pool evidence from ledger-client
  evidence: `pool_active_lane_ranges` / `pool_running_workers` and
  `ledger_client_active_lane_ranges` / `ledger_client_running_workers`. This
  lets the coordinator ignore stale ledger-client mirrors without discarding
  real currently-running worker-pool lanes when the two ranges overlap.
- For legacy helpers that do not send split fields, the coordinator can infer
  active pool lanes from `active_lane_ranges - complete_lane_ranges` when the
  stale mirror overlaps lanes already reported complete. This preserves the
  second lane of an in-progress bundle while still dropping purely stale
  mirrors.
- The coordinator also sanitizes old-helper heartbeats. If an older helper
  still sends active lanes copied from a stale ledger-client mirror, the
  coordinator removes those stale ranges before importing active claims.
- The coordinator also reconciles stale active claims. If a helper reports a
  current ledger-client active set, old lanes owned by that same helper but no
  longer present in the active set are released back to pending. Stale
  remote-active heartbeat rows from dead panels are also released after the
  heartbeat freshness window.
- For coordinator-local workers, current worker-pool bundle ranges are also
  used as active evidence. If the local ledger-client mirror goes stale while
  a newer pool is running, old local claims outside the current worker bundles
  are released instead of blocking future assignment.
- The coordinator grand total counter unions local lane ranges with subordinate
  ledger `done_ranges`, subordinate health `complete_lane_ranges`, and the
  coordinator's persistent ledger `done_ranges`. This prevents double-counting
  when coordinator and subordinate both know about the same completed lane.
- Bare legacy done counts are shown as diagnostics only. They no longer add to
  the grand total because they cannot identify exact lanes or dedupe overlap.
- Coordinator worker totals show combined local plus registered subordinate
  workers, with local and remote counts shown separately. Stale remote worker
  counts are ignored after the heartbeat freshness window.
- Final lane ZIPs must be at least `1024` bytes before filename-only counters,
  ledger reconciliation, or the ledger client treat them as completed.
- `/api/ledger/finish` requires size proof and `pk3_count=65536`.
- Expired ledger leases are reported as `expired_claims`, not active/running
  lane ranges. They become claimable again instead of making the UI pretend
  stale work is still alive.
- The ledger worker client retries finish/fail reporting before claiming more
  lanes, so completed helper work does not sit leased until timeout after a
  temporary coordinator outage.
- Assisted-baking launchers now route command-center startup through the shared
  command-center wrapper, which starts the watcher too. The shared wrapper
  prefers `portable-python\python.exe` when present in a helper package.

## Contributor Credit

Shawrkie is credited for the dashboard and helper-side additions covered by
this guide. In practical terms, that means the operator-facing command center,
the helper-side ledger workflow, the worker launch wrappers, the coordinator /
subordinate status model, and the helper deployment behavior that let assisted
machines compute Phase 3 Spinda lanes while the dashboard tracks claims,
completion, stale work, health, and throughput.

### Observed Once

- Active worker slots displayed current lane, current timer, last lane, and
  last timer during a 12-worker production run.

### Inferred

- Refresh intervals below the defaults give little operator benefit and can add
  avoidable overhead.

### Planned

- Keep command-center UI as the normal way to launch, resize, stop, and kill
  worker pools.
- Run a small offsite proof batch after coordinator-led assignment is enabled
  and verify returned ZIPs before expanding helper worker count.

### Obsolete

- Do not rely on raw JSON panels as the primary UI.
- Do not use PKHeX validation as part of hot command-center polling.

## Launch

Preferred:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

Status:

```powershell
.\tools\spinda\phase3_command_center.cmd -Action Status
```

Restart only command center and watcher:

```powershell
.\tools\spinda\phase3_command_center.cmd -Action Restart
```

This should not kill active `mgba-spinda-phase3.exe` workers.

## Multi-Device Role

Default local mode:

```powershell
.\tools\spinda\phase3_command_center.cmd
```

Coordinator online mode:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role coordinator -Online -AdvertiseHost 192.168.1.10 -AdvertisePort 235
```

Subordinate online mode:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role subordinate -Online -PrimaryScheme http -PrimaryHost 192.168.1.10 -PrimaryPort 235 -AdvertiseHost 192.168.1.21 -AdvertisePort 235
```

HTTPS coordinator example:

```powershell
.\tools\spinda\phase3_command_center.cmd -Role subordinate -Online -PrimaryScheme https -PrimaryHost math.hyddwn.net -PrimaryPort 443 -AdvertiseHost 192.168.1.21 -AdvertisePort 235
```

Meaning:

- `coordinator`: primary panel. It owns lane assignment and accepts subordinate
  heartbeats.
- `subordinate`: secondary panel. In online mode, it launches the ledger client
  so the coordinator assigns every lane before local workers start.
- `-Online`: enables multi-device traffic.
- `-PrimaryScheme` / `-PrimaryHost` / `-PrimaryPort`: exact coordinator
  scheme/host/port a subordinate talks to.
- `-AdvertiseScheme` / `-AdvertiseHost` / `-AdvertisePort`: exact
  scheme/host/port other machines should use to reach this panel.

Current scope: online command-center launches use the coordinator ledger for
assignment. Offline command-center launches still use the direct local worker
pool path and are suitable for single-PC/manual fixed-range work only.

The same settings can be changed from the browser in the `Multi-device
coordination` panel. Press `Save network settings` after editing:

- role
- online mode
- primary scheme/IP/port
- advertise scheme/IP/port
- heartbeat seconds

The protocol selectors are explicit toggles:

- `Primary HTTP/HTTPS`: scheme this machine uses when talking to the
  coordinator.
- `Advertise HTTP/HTTPS`: scheme other machines should use when talking to
  this panel.

The UI writes:

```text
Phase3SpindaBlocks\_phase3_command_center_network.json
```

On the next command-center restart, the file is loaded as the active network
configuration unless CLI flags and the file are changed again.

Path rule:

- normal local workspace root: `<repo-root>`
- assisted Windows root: whatever folder contains `tools\spinda`
- Linux helper root: checkout/helper folder root

If `--folder` is set, related pool status, pool control, watcher status,
ledger-client status, and cache defaults follow that folder unless explicitly
overridden.

## Lane Ledger

The coordinator owns a persistent lane ledger:

```text
Phase3SpindaBlocks\_phase3_lane_ledger.json
```

Pending lanes are implicit. The file stores lanes that have had activity:

- `claimed`
- `running`
- `done`
- `failed`
- `released`
- `quarantined`
- `verified`

Claim flow:

1. Device asks coordinator for lanes.
2. Coordinator atomically writes claim records with lease expiry.
3. Device sends heartbeats while running.
4. Device marks lane `done` after output ZIP exists or is returned.
5. If device dies, lease expires and lane can be claimed again.

Claimed/running lanes have:

- `device_id`
- `worker_id`
- `claim_id`
- `claimed_at_unix`
- `heartbeat_at_unix`
- `lease_until_unix`
- `attempts`

Done lanes can store:

- `zip_path`
- `zip_size`
- `zip_sha256`
- `pk3_count`

For `/api/ledger/finish`, `zip_size` and `pk3_count=65536` are required.
`zip_sha256` remains optional. Folder reconciliation is looser because it is
only adopting already-present local ZIP filenames; it still rejects files under
`1024` bytes.

The command-center UI shows ledger counts and active claims. The button
`Reconcile finished ZIPs into ledger` scans valid final ZIP names and marks
them `done` in the ledger. This is useful when adopting an already-running
single-PC folder into the multi-device ledger. A valid final ZIP name is
`0x####.spinda80.zip` and the file must be at least `1024` bytes; tiny files
are treated as interrupted artifacts and stay claimable.

Standalone offline ledger merge:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\merge_phase3_json_ledgers.py
```

With no arguments, this opens Windows folder pickers for a source helper
`Phase3SpindaBlocks` folder and a destination `Phase3SpindaBlocks` folder. It
merges `_phase3_lane_ledger.json` records into the destination ledger, writes a
backup of the old destination ledger, and writes a merge report JSON. By
default it skips live `claimed` and `running` rows so stale helper claims do not
block lanes on the destination. Use `--include-active` only when the source
helper is still alive and you intentionally want to preserve active claims.

Command-line form:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\merge_phase3_json_ledgers.py --source D:\Helper\Phase3SpindaBlocks --destination <repo-root>\Phase3SpindaBlocks
```

For multi-device runs, a coordinator also keeps a trusted remote total in the
main progress panel:

- `Local ZIP lanes`: valid local `0x####.spinda80.zip` files on the
  coordinator.
- `Trusted ledger/remote lanes`: completed lanes known through the coordinator
  ledger or subordinate lane ranges, excluding local ZIP duplicates.
- `Total completed lanes`: range union of local ZIP lanes, coordinator ledger
  done ranges, and trusted subordinate lane ranges, capped at `65536`.

This does not copy ZIP files to the coordinator. It is an operator counter for
avoiding duplicate work and seeing global progress while completed ZIPs remain
on helper machines.

Modern heartbeats include compact lane ranges:

- `ledger.done_ranges`
- `health.complete_lane_ranges`
- `ledger.active_claim_ranges`
- `workers.active_lane_ranges`

On a coordinator, received remote done ranges are merged into the coordinator
ledger as `remote-ledger` rows unless local proof already exists. Health
complete ranges are accepted as a backup done-range source. Received active
worker ranges are merged as leased `running` rows so future claims do not
assign those lanes to another helper. Plain remote counts without ranges remain
visible, but they are diagnostic only.

Assisted machines can use:

```text
tools\spinda\phase3_ledger_worker_client.py
```

That client:

- claims a batch from the coordinator
- writes `_phase3_ledger_worker_client_status.json` with active lane ranges
- launches `native_phase3_worker_pool.py` for those claimed lanes
- heartbeats while the local worker pool runs
- marks lanes `done` when final ZIPs exist, are at least `1024` bytes, and are
  reported with `pk3_count=65536`
- marks missing outputs `failed` so they can be retried
- if reporting fails, retries the report before asking the coordinator for
  another batch
- if a claimed lane already has a valid local ZIP, skips rerunning it and
  reports the existing ZIP instead

Example:

```powershell
python .\tools\spinda\phase3_ledger_worker_client.py `
  --coordinator-url http://192.168.1.10:235 `
  --device-id offsite-workstation-1 `
  --workers 6 `
  --batch-size 24 `
  --lanes 0x8000-0xFFFF `
  -- `
  --rom C:\path\to\lg.gba
```

Use `--once` for one claimed batch. Omit it for continuous claiming.

Linux helper nodes use the same ledger API but skip Qt entirely:

```bash
bash tools/spinda/build_phase3_cli_linux.sh

COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-helper-1 \
WORKERS=6 \
BATCH_SIZE=24 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

The Linux launcher validates local ROM, `secondhalf.csv`, and
`Phase2PickupStates`, then passes Linux-native paths to the same ledger worker
client. See [PHASE3_LINUX_HELPER_NODE.md](PHASE3_LINUX_HELPER_NODE.md).

## Low-Overhead Defaults

- worker/status JSON refresh: `5` seconds
- output-folder ZIP scan: `60` seconds
- CPU/RAM/disk sample: `15` seconds
- browser SSE interval: `3` seconds, but shared server cache prevents each
  browser tab from doing its own full scan

These are meant to keep operator status useful while giving CPU time to worker
processes.

## Main Counters

- `Total completed lanes`: local valid ZIP lane count on subordinate panels;
  on coordinator panels, the union of local ZIP lanes, coordinator ledger done
  lanes, and trusted subordinate lane ranges.
- `Exact Spindas generated`: completed lanes times `65,536`.
- `Since panel boot`: lanes completed since this Flask process started.
- `Since pool boot`: lanes completed by the current worker-pool status.
- `Projected finish`: estimate based on recent completed worker jobs.
- `ETA`: estimate based on current command-center boot session.

Use projected finish as the more stable estimate after the pool has recent job
history.

## Worker Controls

Controls:

- `Apply / launch workers`: writes desired worker count and starts a managed
  pool if needed.
- `Stop workers`: asks the pool to stop.
- `Force kill running workers`: sends a stronger stop through known PIDs.
- `Killswitch: stop all workers`: emergency stop for known worker-pool and
  CLI worker PIDs.

Use clean stop first. Use killswitch only when workers fail to respond.

## Watcher Panel

The watcher panel shows:

- watcher status: `ok`, `warning`, `important`, or `missing`
- status age
- check count
- reported worker count versus OS worker count
- recent watcher warnings

If watcher is `missing`, launch through the wrapper or start
`phase3_independent_watcher.py` manually.

## Validation Policy Panel

The panel intentionally splits validation:

- raw ZIP manifest checks: safe during production
- deep ZIP checks: batch or final work
- PKHeX.Core semantic validation: final audit after all lanes complete

This keeps hot production from spending time on expensive per-entry validation.

## API

Main status:

```text
GET /api/status
```

Worker summary:

```text
GET /api/workers
```

SSE stream:

```text
GET /events?interval=3
```

Worker controls:

```text
POST /api/control/workers
POST /api/control/stop
POST /api/control/killswitch
GET  /api/control/state
```

Coordination:

```text
GET  /api/coordination/state
POST /api/coordination/register
POST /api/coordination/heartbeat
POST /api/coordination/settings
```

The heartbeat/register endpoints accept traffic only when this panel is a
`coordinator` and online mode is enabled.

Ledger:

```text
GET  /api/ledger/status
POST /api/ledger/reconcile
POST /api/ledger/claim
POST /api/ledger/heartbeat
POST /api/ledger/finish
POST /api/ledger/fail
POST /api/ledger/release
```

Minimal claim:

```json
{
  "device_id": "offsite-workstation-1",
  "worker_id": "pool-a",
  "count": 12,
  "lanes": "0x8000-0xFFFF",
  "lease_seconds": 21600
}
```

Minimal finish:

```json
{
  "device_id": "offsite-workstation-1",
  "lane": "0x8000",
  "zip_size": 123456,
  "zip_sha256": "optional",
  "pk3_count": 65536
}
```

## Troubleshooting

- Page offline: run wrapper with `-Action Status`, then `-Action Restart`.
- Watcher missing: restart wrapper or launch watcher manually.
- Workers shown stale: check worker-pool status age and OS worker processes.
- Completed lane count not moving: check watcher newest-ZIP age and worker
  stall warnings.
- Bad names, zero-size ZIPs, or tiny ZIPs: stop new work for affected lanes,
  then follow recovery guide.

## Related Docs

- [PHASE3_RUNBOOK.md](PHASE3_RUNBOOK.md)
- [PHASE3_WATCHER_GUIDE.md](PHASE3_WATCHER_GUIDE.md)
- [PHASE3_RECOVERY_GUIDE.md](PHASE3_RECOVERY_GUIDE.md)
- [PHASE3_FINAL_VALIDATION_PLAN.md](PHASE3_FINAL_VALIDATION_PLAN.md)
