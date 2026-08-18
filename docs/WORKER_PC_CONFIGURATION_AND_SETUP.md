# Worker PC Configuration And Setup

## Status Bucket

- Current status: Operator guide for adding Windows or Linux worker PCs to
  Phase 3 production through the command-center ledger.
- Last verified date: 2026-05-02.
- Proven artifacts:
  - `tools/spinda/phase3_command_center_web.py`
  - `tools/spinda/phase3_ledger_worker_client.py`
  - `tools/spinda/native_phase3_worker_pool.py`
  - `tools/spinda/build_phase3_cli_linux.sh`
  - `tools/spinda/run_phase3_ledger_helper.sh`
  - `tools/spinda/check_linux_helper_port.py`
  - `START_LEDGER_ASSISTED_WORKERS.cmd` in the private assisted package
- Known gaps: first large Linux helper deployment still needs one real proof
  lane on the Linux workstation before broad assignment.
- Next action: set up helper, run one proof lane, verify returned ZIP, then
  widen worker count and lane range.

## Goal

A worker PC should do one thing: claim lanes from the coordinator, produce
`0x####.spinda80.zip` files, and avoid duplicate lane work.

The coordinator PC owns:

- command center on port `235`
- ledger and claim state
- final merge/validation
- worker count decisions

Worker PCs own:

- local CPU work
- local output ZIPs
- heartbeats while lanes run
- returning finished ZIPs to coordinator

All helper-local status files should live under that helper's own
`Phase3SpindaBlocks` folder. If a helper command center shows
`<repo-root>\Phase3SpindaBlocks\_phase3_ledger_worker_client_status.json` while
the helper root is somewhere else, its active lane reports are using the wrong
mirror and the command center package needs the current pathing fix.

## Hardware Guidance

CPU matters most. GPU matters little for Phase 3 because the bottleneck is
accurate emulated frame advancement and state/PK3 extraction.

Recommended worker:

- modern 6-core or better CPU
- 16 GB RAM minimum
- 32 GB RAM preferred for many workers
- SSD with at least 80 GB free
- wired Ethernet preferred
- stable cooling
- sleep/hibernation disabled
- antivirus exclusions for the helper folder and mGBA executable if false
  positives occur

Do not run more workers than cooling and system responsiveness allow. For a new
machine, start with `WORKERS=1`, then `2`, then `4`, then higher only after real
lane timings look stable.

## Required Inputs

Every worker needs:

```text
inputs/lg.gba
inputs/secondhalf.csv
Phase2PickupStates/0x0000.ss0 ... 0xFFFF.ss0
Phase3SpindaBlocks/
tools/spinda/
src/
build artifacts or a way to build them
```

Clean public source does not include ROMs, save states, or generated PK3 ZIPs.
The private assisted package can include those files when the owner chooses to
send them.

## Coordinator Setup

On the coordinator PC, start command center and watcher:

```powershell
<repo-root>\tools\spinda\phase3_command_center.cmd
```

Open:

```text
http://127.0.0.1:235
```

For other PCs, use the coordinator LAN IP:

```text
http://192.168.1.10:235
```

If the coordinator is exposed through HTTPS, use the HTTPS URL and normal TLS
port, for example:

```text
https://math.hyddwn.net
```

Allow inbound TCP port `235` through the coordinator firewall. Keep the
coordinator on a trusted LAN or VPN; do not expose the command center directly
to the public internet.

## Windows Worker Setup

Copy or extract the private helper package, for example:

```text
C:\SpindaWorker\Assisted-baking
```

Before running work, verify the package:

```powershell
cd C:\SpindaWorker\Assisted-baking
portable-python\python.exe tools\spinda\check_linux_helper_port.py --root . --mode assisted --skip-phase2-count
```

This validator is named for Linux helper readiness, but in assisted mode it
also verifies shared helper-package requirements such as scripts, ROM/CSV
presence, manifest, and optional Phase 2 state count.

For full Phase 2 state count:

```powershell
portable-python\python.exe tools\spinda\check_linux_helper_port.py --root . --mode assisted
```

Edit `START_LEDGER_ASSISTED_WORKERS.cmd` or set environment variables before
launch:

```powershell
set COORDINATOR_URL=http://192.168.1.10:235
set DEVICE_ID=worker-pc-01
set WORKERS=4
set BATCH_SIZE=16
set LANES=0x0000-0xFFFF
START_LEDGER_ASSISTED_WORKERS.cmd
```

The launcher no longer supplies a fake placeholder coordinator URL. If
`COORDINATOR_URL` is not set and the prompt is left blank, it exits instead of
silently trying to contact `OWNER-IP`.

For an HTTPS coordinator:

```powershell
set COORDINATOR_URL=https://math.hyddwn.net
```

Expected behavior:

- worker claims lane batch from coordinator
- command-center wrapper passes every status/ledger/cache path inside the
  helper package's own `Phase3SpindaBlocks` folder
- worker writes `_phase3_ledger_worker_client_status.json` with active
  coordinator-owned lane ranges
- command-center heartbeats trust that ledger-client status only while it is
  fresh; if the client stops updating it, active lane ranges and worker counts
  are dropped from heartbeat totals
- current helper heartbeats report worker-pool ranges separately from
  ledger-client ranges, so a stale ledger-client mirror does not hide real
  pool lanes that are still running
- older helpers without split fields are partly protected too: if completed
  lane ranges overlap their stale active range, the coordinator infers the
  remaining lanes as the active bundle lanes
- current coordinators also sanitize stale ledger-client ranges from old helper
  heartbeats, but helpers should still be updated and restarted so their local
  UI stops showing stale mirrors
- current coordinators release old active claims when the helper's current
  ledger-client active set no longer includes them, and release stale
  remote-active heartbeat rows from dead panels after the freshness window
- coordinator-local claims are also checked against current worker-pool bundle
  ranges, so a stale local ledger-client mirror cannot keep old local lanes
  reserved during a newer run
- local CLI workers start
- command center shows active claims/worker status
- finished lanes produce local `Phase3SpindaBlocks\0x####.spinda80.zip`
- client reports done lanes back to coordinator only after the final ZIP is at
  least `1024` bytes and can be reported with `pk3_count=65536`
- if reporting fails, client keeps retrying before claiming more lanes
- the helper command-center launcher uses the shared command-center wrapper,
  so starting the panel also starts the independent watcher
- coordinator grand total uses local ZIP ranges, coordinator ledger done
  ranges, and reported subordinate lane ranges, so global progress can rise
  before ZIP files are manually consolidated without double-counting
  overlapping lanes

Path check:

- command-center `control.ledger_client_status_path` should point inside this
  worker package
- `ledger_client.path` should point to the same worker package
- `health.folder` should point to this worker package's `Phase3SpindaBlocks`
- none of those should point at `<repo-root>` unless this worker really runs
  from `<repo-root>`

## Linux Worker Setup

Install build basics:

```bash
sudo apt install build-essential cmake ninja-build python3 zlib1g-dev
```

Package names vary by distro. Need compiler, CMake, optional Ninja, Python 3,
and zlib development headers.

Place helper folder:

```text
~/spinda-helper/
  inputs/lg.gba
  inputs/secondhalf.csv
  Phase2PickupStates/
  Phase3SpindaBlocks/
  tools/
  src/
  CMakeLists.txt
```

Check shell/package shape:

```bash
python3 tools/spinda/check_linux_helper_port.py --root . --mode assisted --skip-phase2-count
```

Build headless CLI:

```bash
bash tools/spinda/build_phase3_cli_linux.sh
```

Expected output:

```text
build-linux-spinda-cli/mgba-spinda-phase3
```

Run one proof lane:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-proof-1 \
WORKERS=1 \
BATCH_SIZE=1 \
BUNDLE_SIZE=1 \
LANES=0x0001-0x0001 \
bash tools/spinda/run_phase3_ledger_helper.sh
```

Only widen after proof ZIP validates.

Broader run:

```bash
COORDINATOR_URL=http://192.168.1.10:235 \
DEVICE_ID=linux-helper-1 \
WORKERS=6 \
BATCH_SIZE=24 \
BUNDLE_SIZE=2 \
LANES=0x0000-0xFFFF \
bash tools/spinda/run_phase3_ledger_helper.sh
```

## Proof Lane Checklist

For the first worker on any new PC:

1. Start with one known lane such as `0x0001`.
2. Confirm coordinator shows lane claimed by correct `DEVICE_ID`.
3. Wait for `Phase3SpindaBlocks/0x0001.spinda80.zip`.
4. Confirm coordinator marks lane done.
5. Copy ZIP back to coordinator.
6. Run manifest/deep ZIP validation on coordinator.
7. Compare output with known-good lane if available.
8. Increase worker count only after proof passes.

## No-Duplicate Rule

Use the ledger path for multi-device work. Do not hand-edit lane ranges on many
PCs unless the coordinator ledger is offline and a human keeps a separate
allocation sheet.

Ledger behavior:

- worker requests lanes
- coordinator grants lease
- worker heartbeats
- worker reports done/fail
- other workers skip lanes already done or actively leased
- command-center heartbeats also report active lane ranges; coordinators import
  those as leased `running` rows for duplicate-work protection
- command-center heartbeats report completed lane ranges; coordinators import
  those as done rows and ignore bare legacy counts for total math
- online command-center launch buttons use the ledger client, not direct local
  lane picking, so the coordinator remains the only allocator

If a worker dies, leases can expire and lanes can be reclaimed later.

## Safe Return Process

Worker output to return:

```text
Phase3SpindaBlocks/0x####.spinda80.zip
```

Do not return:

- ROMs unless owner explicitly requests
- `Phase2PickupStates`
- `_cache`
- temporary status JSON unless debugging a failure
- partial `.tmp` files

Before deleting worker outputs, confirm coordinator has copied and validated
them.

## Troubleshooting

Command center cannot see worker:

- confirm `COORDINATOR_URL`
- confirm URL scheme is correct: `http://` for LAN/VPN, `https://` for TLS or
  reverse-proxy coordinator endpoints
- confirm firewall allows port `235`
- confirm both machines are on same LAN/VPN
- open the matching `/api/status` URL from worker browser or `curl`, for
  example `http://coordinator-ip:235/api/status` or
  `https://coordinator-name/api/status`

Worker claims no lanes:

- confirm ledger mode is enabled in command center
- confirm `LANES` range is valid
- confirm lanes are not already done or leased
- restart only the ledger worker client, not finished ZIPs

CLI missing:

- Windows: confirm `build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe`
- Linux: run `bash tools/spinda/build_phase3_cli_linux.sh`

Linux script fails immediately:

- run `python3 tools/spinda/check_linux_helper_port.py --root . --mode assisted`
- verify LF line endings
- verify executable bit when needed: `chmod +x tools/spinda/*.sh`

Antivirus quarantine:

- restore the executable only if hash/source is trusted
- add exclusion for worker folder
- rerun package validation

Overheating or unstable timing:

- reduce `WORKERS`
- increase cooling
- avoid other heavy apps
- watch lane duration in command center

## Shutdown And Resume

Preferred pause:

1. Let current lanes finish if possible.
2. Stop worker client.
3. Leave finished ZIPs in place.
4. Restart later with same `COORDINATOR_URL`, `DEVICE_ID`, and output folder.

Safe to delete:

- stale temp files
- failed partial output after confirming no valid final ZIP exists
- local logs copied elsewhere

Do not delete:

- valid `0x####.spinda80.zip`
- source ROM/CSV/state inputs
- coordinator ledger files unless backed up

## Security

Treat helper packages as private if they include ROMs, savestates, or generated
PK3 ZIPs.

Use:

- trusted helper operators
- private transfer method
- checksums for package and returned ZIPs
- no public internet exposure for command center
- device-specific `DEVICE_ID`

## Related Docs

- `PHASE3_COMMAND_CENTER_GUIDE.md`
- `MULTI_DEVICE_PHASE3_SCALING_PLAN.md`
- `PHASE3_LINUX_HELPER_NODE.md`
- `PHASE3_RECOVERY_GUIDE.md`
- `PHASE3_FINAL_VALIDATION_PLAN.md`
- `PHASE3_CONTROL_AND_AUX_SCRIPTS_CHEATSHEET.md`
