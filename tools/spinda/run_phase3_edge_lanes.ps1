<#
.SYNOPSIS
Runs Phase 3 only for the repaired endpoint lanes 0x0000 and 0xFFFF.

.DESCRIPTION
This is a final edge-lane launcher for after the normal Phase 3 run is done.
It starts one native CLI process per endpoint lane, so 0x0000 and 0xFFFF run
simultaneously while sharing the normal Phase3SpindaBlocks cache directory.

Examples:
  powershell -ExecutionPolicy Bypass -File tools\spinda\run_phase3_edge_lanes.ps1 -DryRun
  powershell -ExecutionPolicy Bypass -File tools\spinda\run_phase3_edge_lanes.ps1
  powershell -ExecutionPolicy Bypass -File tools\spinda\run_phase3_edge_lanes.ps1 -Overwrite
#>

[CmdletBinding()]
param(
    [string]$Root,
    [string]$Phase3CliExe,
    [string]$Rom,
    [string]$Phase2Dir,
    [string]$SecondHalfCsv,
    [string]$OutputDir,
    [string]$CacheDir,
    [ValidateSet("deflate", "store")]
    [string]$ZipMethod = "deflate",
    [int]$RuntimeScheduleMaxSteps = 4000000,
    [int]$MinPickupDetectFrame = 4,
    [int]$FastPickupCheckFirstFrame = 4,
    [int]$FastPickupCheckSecondFrame = 5,
    [int]$LearnPickupDelaySamples = 32,
    [int]$PollSeconds = 10,
    [int]$Limit = 0,
    [switch]$Overwrite,
    [switch]$DryRun,
    [switch]$SkipExisting,
    [switch]$SkipPhase2RuntimeCheck
)

Set-StrictMode -Version 3.0
$ErrorActionPreference = "Stop"

function Resolve-DefaultPath {
    param([string]$MaybePath, [string]$DefaultPath)
    if ([string]::IsNullOrWhiteSpace($MaybePath)) {
        return [System.IO.Path]::GetFullPath($DefaultPath)
    }
    return [System.IO.Path]::GetFullPath($MaybePath)
}

function Assert-File {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

function Assert-Directory {
    param([string]$Path, [string]$Label)
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        throw "$Label not found: $Path"
    }
}

function Format-Lane {
    param([int]$Lane)
    return ("0x{0:X4}" -f $Lane)
}

function Quote-Arg {
    param([string]$Value)
    if ($Value -match '[\s"]') {
        return '"' + ($Value -replace '"', '\"') + '"'
    }
    return $Value
}

function Get-StatusSummary {
    param([int]$Lane, [string]$OutputDir)

    $laneHex = Format-Lane $Lane
    $statusPath = Join-Path $OutputDir "_$laneHex.phase3_status.json"
    if (-not (Test-Path -LiteralPath $statusPath -PathType Leaf)) {
        return [pscustomobject]@{
            Lane = $laneHex
            Status = "missing-status"
            GeneratedRecords = $null
            Error = $null
            StatusPath = $statusPath
        }
    }

    $payload = Get-Content -LiteralPath $statusPath -Raw | ConvertFrom-Json
    $errorText = $null
    if ($payload.PSObject.Properties.Name -contains "error") {
        $errorText = $payload.error
    }
    return [pscustomobject]@{
        Lane = $laneHex
        Status = [string]$payload.status
        GeneratedRecords = $payload.generated_records
        Error = $errorText
        StatusPath = $statusPath
    }
}

$ScriptRoot = $PSScriptRoot
if ([string]::IsNullOrWhiteSpace($ScriptRoot)) {
    if (-not [string]::IsNullOrWhiteSpace($PSCommandPath)) {
        $ScriptRoot = Split-Path -Parent $PSCommandPath
    }
    elseif ($MyInvocation.MyCommand.Path) {
        $ScriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
    }
    else {
        $ScriptRoot = (Get-Location).Path
    }
}
if ([string]::IsNullOrWhiteSpace($Root)) {
    $Root = (Resolve-Path (Join-Path $ScriptRoot "..\..")).Path
}
$Root = [System.IO.Path]::GetFullPath($Root)
$Phase3CliExe = Resolve-DefaultPath $Phase3CliExe (Join-Path $Root "build-mingw64-spinda-cli-lto\mgba-spinda-phase3.exe")
$Rom = Resolve-DefaultPath $Rom (Join-Path $Root "doc\python-examples\frlg-seed-bruteforce\lg.gba")
$Phase2Dir = Resolve-DefaultPath $Phase2Dir (Join-Path $Root "Phase2PickupStates")
$SecondHalfCsv = Resolve-DefaultPath $SecondHalfCsv (Join-Path $Root "build-mingw64-python-qt\secondhalf.csv")
$OutputDir = Resolve-DefaultPath $OutputDir (Join-Path $Root "Phase3SpindaBlocks")
$CacheDir = Resolve-DefaultPath $CacheDir (Join-Path $OutputDir "_cache")
$PythonExe = Join-Path $Root ".venv-mgba\bin\python.exe"
$Validator = Join-Path $Root "tools\spinda\phase2_pickup_state_validator.py"
$ExpectedStateSize = 397312
$ExpectedRng = "0x2B0C94C1"
$Lanes = @(0x0000, 0xFFFF)

Assert-File $Phase3CliExe "Phase 3 CLI executable"
Assert-File $Rom "ROM"
Assert-File $SecondHalfCsv "secondhalf.csv"
Assert-Directory $Phase2Dir "Phase 2 pickup state directory"
if (-not (Test-Path -LiteralPath $OutputDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $OutputDir | Out-Null
}
if (-not (Test-Path -LiteralPath $CacheDir -PathType Container)) {
    New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
}

foreach ($lane in $Lanes) {
    $laneHex = Format-Lane $lane
    $statePath = Join-Path $Phase2Dir "$laneHex.ss0"
    Assert-File $statePath "Phase 2 state $laneHex"
    $state = Get-Item -LiteralPath $statePath
    if ($state.Length -ne $ExpectedStateSize) {
        throw "Phase 2 state $laneHex has size $($state.Length), expected $ExpectedStateSize`: $statePath"
    }
}

if (-not $SkipPhase2RuntimeCheck) {
    Assert-File $PythonExe "mGBA Python"
    Assert-File $Validator "Phase 2 validator"
    Write-Host "Verifying repaired Phase 2 edge states against $ExpectedRng..."
    $validatorOutput = & $PythonExe $Validator $Phase2Dir `
        --verify-samples 2 `
        --sample-targets "0x0000,0xFFFF" `
        --expected-rng $ExpectedRng `
        --drift-window 8 `
        --json 2>&1
    if ($LASTEXITCODE -ne 0) {
        $text = ($validatorOutput | Out-String).Trim()
        throw "Phase 2 runtime validation failed with exit $LASTEXITCODE`n$text"
    }
    $validatorJson = ($validatorOutput | Out-String) | ConvertFrom-Json
    $badSamples = @($validatorJson.sample_verification | Where-Object {
        $_.status -ne "ok" -or [int]$_.drift -ne 0 -or [string]$_.observed_rng -ne $ExpectedRng
    })
    if ($badSamples.Count -gt 0) {
        $details = ($badSamples | ConvertTo-Json -Depth 5)
        throw "Phase 2 edge-state RNG validation did not pass:`n$details"
    }
}

$logRoot = Join-Path $OutputDir "_edge_lane_phase3_logs"
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$logDir = Join-Path $logRoot $stamp
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$runtimePaths = @(
    (Split-Path -Parent $Phase3CliExe),
    "C:\msys64\mingw64\bin",
    "C:\msys64\usr\bin",
    "C:\devkitPro\msys2\usr\bin"
) | Where-Object { Test-Path -LiteralPath $_ -PathType Container }
$oldPath = $env:PATH
$env:PATH = (($runtimePaths + @($oldPath)) -join [System.IO.Path]::PathSeparator)

try {
    $jobs = @()
    foreach ($lane in $Lanes) {
        $laneHex = Format-Lane $lane
        $zipPath = Join-Path $OutputDir "$laneHex.spinda80.zip"
        if ($SkipExisting -and -not $Overwrite -and (Test-Path -LiteralPath $zipPath -PathType Leaf)) {
            Write-Host "Skipping $laneHex because final ZIP already exists: $zipPath"
            continue
        }

        $statePath = Join-Path $Phase2Dir "$laneHex.ss0"
        $stdoutPath = Join-Path $logDir "$laneHex.stdout.log"
        $stderrPath = Join-Path $logDir "$laneHex.stderr.log"
        $commandPath = Join-Path $logDir "$laneHex.command.txt"

        $args = @(
            "--rom", $Rom,
            "--lane", $laneHex,
            "--phase2-state", $statePath,
            "--secondhalf-csv", $SecondHalfCsv,
            "--output-dir", $OutputDir,
            "--cache-dir", $CacheDir,
            "--expected-rng", $ExpectedRng,
            "--runtime-schedule-max-steps", [string]$RuntimeScheduleMaxSteps,
            "--min-pickup-detect-frame", [string]$MinPickupDetectFrame,
            "--fast-pickup-check-first-frame", [string]$FastPickupCheckFirstFrame,
            "--fast-pickup-check-second-frame", [string]$FastPickupCheckSecondFrame,
            "--learn-pickup-delay-samples", [string]$LearnPickupDelaySamples
        )
        if ($Limit -gt 0) {
            $args += @("--limit", [string]$Limit)
        }
        if ($Overwrite) {
            $args += "--overwrite"
        }
        if ($ZipMethod -eq "store") {
            $args += "--zip-store"
        }

        $argLine = ($args | ForEach-Object { Quote-Arg ([string]$_) }) -join " "
        "$Phase3CliExe $argLine" | Set-Content -LiteralPath $commandPath -Encoding UTF8

        if ($DryRun) {
            Write-Host "DRY RUN $laneHex`: $Phase3CliExe $argLine"
            continue
        }

        Remove-Item -LiteralPath $stdoutPath, $stderrPath -ErrorAction SilentlyContinue
        $process = Start-Process `
            -FilePath $Phase3CliExe `
            -ArgumentList $argLine `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath `
            -WindowStyle Hidden `
            -PassThru
        $jobs += [pscustomobject]@{
            Lane = $laneHex
            Process = $process
            Stdout = $stdoutPath
            Stderr = $stderrPath
            ZipPath = $zipPath
        }
        Write-Host "Started $laneHex as PID $($process.Id)"
    }

    if ($DryRun) {
        Write-Host "Dry run complete. Logs would be written under: $logDir"
        exit 0
    }

    if ($jobs.Count -eq 0) {
        Write-Host "No edge-lane jobs were launched."
        exit 0
    }

    do {
        Start-Sleep -Seconds $PollSeconds
        $running = @($jobs | Where-Object { -not $_.Process.HasExited })
        $status = $jobs | ForEach-Object {
            [pscustomobject]@{
                Lane = $_.Lane
                PID = $_.Process.Id
                Running = -not $_.Process.HasExited
                ExitCode = if ($_.Process.HasExited) { $_.Process.ExitCode } else { $null }
            }
        }
        $status | Format-Table -AutoSize
    } while ($running.Count -gt 0)

    $summaries = foreach ($job in $jobs) {
        $job.Process.Refresh()
        $laneValue = [Convert]::ToInt32($job.Lane.Substring(2), 16)
        $zip = Get-Item -LiteralPath $job.ZipPath -ErrorAction SilentlyContinue
        $sidecar = Get-StatusSummary -Lane $laneValue -OutputDir $OutputDir
        [pscustomobject]@{
            Lane = $job.Lane
            ExitCode = $job.Process.ExitCode
            ZipBytes = if ($zip) { $zip.Length } else { $null }
            Status = $sidecar.Status
            GeneratedRecords = $sidecar.GeneratedRecords
            Error = $sidecar.Error
            StatusPath = $sidecar.StatusPath
            Stdout = $job.Stdout
            Stderr = $job.Stderr
        }
    }

    $summaries | Format-List
    $summaryPath = Join-Path $logDir "edge-lane-summary.json"
    $summaries | ConvertTo-Json -Depth 5 | Set-Content -LiteralPath $summaryPath -Encoding UTF8
    Write-Host "Summary: $summaryPath"

    $failures = @($summaries | Where-Object {
        $_.ExitCode -ne 0 -or $_.Status -ne "complete" -or $_.GeneratedRecords -ne 65536 -or -not $_.ZipBytes
    })
    if ($failures.Count -gt 0) {
        Write-Error "One or more edge-lane Phase 3 jobs failed. See $logDir"
        exit 1
    }
}
finally {
    $env:PATH = $oldPath
}
