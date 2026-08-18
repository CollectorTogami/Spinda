# Syncthing Lane Result Transfer Guide

## Status Bucket

- Current status: Recommended file-transfer guide for moving finished Phase 3
  lane ZIPs from helper PCs to the coordinator.
- Last verified date: 2026-05-01.
- Proven artifacts: Phase 3 lane output naming convention
  `0x####.spinda80.zip`, command-center ledger workflow, and current assisted
  package folder shape.
- Known gaps: Syncthing itself was not installed or configured by this guide in
  the workspace.
- Next action: configure one worker with send-only result sync, confirm one ZIP
  arrives on coordinator, then reuse same pattern for other workers.

## Goal

Use Syncthing only as a finished-result conveyor belt.

Sync:

```text
0x####.spinda80.zip
```

Do not sync:

- ROMs
- `Phase2PickupStates`
- `_cache`
- status JSON
- temp files
- source tree
- portable Python
- command-center ledger files

## Folder Model

Worker PC local output:

```text
C:\SpindaWorker\Assisted-baking\Phase3SpindaBlocks
```

Coordinator receive folder:

```text
<repo-root>\incoming-worker-results\worker-pc-01
```

Use one receive folder per worker. This keeps attribution clear and prevents
two helper PCs from writing into the same folder at the same time.

## Install Syncthing

Install Syncthing on coordinator and worker from:

```text
https://syncthing.net/
```

Windows can run it as:

- tray app for simple manual setup
- background service for long unattended runs

Linux can run it as:

- user service
- system service
- terminal process for first proof

## Pair Devices

On each PC:

1. Open Syncthing web UI.
2. Copy device ID.
3. Add coordinator device on worker.
4. Add worker device on coordinator.
5. Approve pairing on both sides.

Use clear device names:

```text
coordinator-main
worker-pc-01
linux-helper-01
```

## Worker Folder Setup

On worker, add folder:

```text
Folder Label: spinda-results-worker-pc-01
Folder Path: C:\SpindaWorker\Assisted-baking\Phase3SpindaBlocks
Folder Type: Send Only
Shared With: coordinator-main
```

Set ignore patterns on worker:

```text
!0x*.spinda80.zip
*
```

Meaning:

- include final lane ZIPs
- ignore everything else

This avoids syncing private inputs and noisy runtime files.

## Coordinator Folder Setup

On coordinator, accept shared folder into:

```text
<repo-root>\incoming-worker-results\worker-pc-01
```

Set folder type:

```text
Receive Only
```

Do not point Syncthing directly at the main production
`<repo-root>\Phase3SpindaBlocks` folder. Receive into a staging folder first,
then validate/copy/merge intentionally.

## Linux Worker Paths

Example Linux worker output:

```text
/home/user/spinda-helper/Phase3SpindaBlocks
```

Worker ignore patterns are the same:

```text
!0x*.spinda80.zip
*
```

Coordinator can still receive into:

```text
<repo-root>\incoming-worker-results\linux-helper-01
```

## First Proof Transfer

Before syncing many lanes:

1. Worker generates one known lane such as `0x0001.spinda80.zip`.
2. Syncthing sends it to coordinator staging folder.
3. Coordinator verifies file name and size.
4. Coordinator runs ZIP validation.
5. Coordinator confirms it did not receive `_cache`, ROM, states, or JSON.
6. Worker continues broader range only after proof passes.

Example coordinator validation:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\incoming-worker-results\worker-pc-01 --allow-incomplete
```

For light validation while production is running:

```powershell
<repo-root>\.venv-mgba\bin\python.exe <repo-root>\tools\spinda\phase3_zip_validator.py --root <repo-root>\incoming-worker-results\worker-pc-01 --manifest-only --allow-incomplete
```

## Merge Policy

Recommended merge path:

1. Worker output arrives in staging folder.
2. Validate filenames and ZIP shape.
3. Copy only valid `0x####.spinda80.zip` into coordinator final folder.
4. Never overwrite an existing final ZIP unless hash/validation policy says so.
5. Keep worker copy until coordinator confirms receipt and validation.

Staging:

```text
<repo-root>\incoming-worker-results\worker-pc-01
```

Final:

```text
<repo-root>\Phase3SpindaBlocks
```

## Safety Rules

Use send-only on worker and receive-only on coordinator.

Avoid bidirectional sync for result folders. Bidirectional sync can propagate
accidental deletes or edits.

Do not sync the entire assisted package. It includes large and private files.

Do not sync live temp files. The ignore rules must exclude anything except
final `0x####.spinda80.zip`.

Do not use Syncthing as final validation. It proves transfer integrity, not
Pokemon data validity.

## Network Notes

Syncthing can work:

- on same LAN
- over VPN such as Hamachi, Tailscale, or ZeroTier
- over direct internet with proper firewall/NAT behavior
- through relays when direct connection is unavailable

For this project, VPN plus Syncthing is a good pairing:

- VPN gives private route
- Syncthing gives file transfer, resume, and hashing

## Bandwidth Planning

Each lane ZIP is much smaller than raw PK3 count, but still large enough that
many workers can generate steady traffic.

Practical rules:

- let Syncthing run continuously instead of bulk-copying at end
- keep worker local copy until coordinator validates
- throttle Syncthing if it interferes with command-center access
- avoid syncing across metered links unless planned

## What To Check In Syncthing UI

On worker:

- folder state should become `Up to Date`
- only final ZIP files should appear in folder item list
- no failed items

On coordinator:

- folder state should become `Up to Date`
- staging folder contains only `0x####.spinda80.zip`
- no override/revert warnings

If coordinator folder says local changes exist, do not click random override
buttons. Stop and inspect which side changed.

## Troubleshooting

No transfer:

- confirm devices are paired and connected
- confirm folder is shared with coordinator
- confirm ignore pattern is exactly:

```text
!0x*.spinda80.zip
*
```

- confirm result file name matches `0x####.spinda80.zip`
- confirm firewall/VPN path works

Wrong files syncing:

- pause folder
- fix ignore rules
- remove unwanted received files from staging
- resume after confirming only final ZIPs are included

Partial transfer:

- leave Syncthing running
- do not manually move temp transfer files
- verify final ZIP after Syncthing says up to date

Duplicate lane:

- compare coordinator ledger
- compare final folder
- validate both ZIPs before deciding which to keep

## Related Docs

- `WORKER_PC_CONFIGURATION_AND_SETUP.md`
- `MULTI_DEVICE_PHASE3_SCALING_PLAN.md`
- `PHASE3_COMMAND_CENTER_GUIDE.md`
- `PHASE3_FINAL_VALIDATION_PLAN.md`
- `PHASE3_RECOVERY_GUIDE.md`
