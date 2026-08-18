param(
    [string]$ProjectRoot = "",
    [int]$Port = 235,
    [switch]$Json
)

# Read-only Phase 3 health check. This script does not launch, resize, stop,
# kill, decompress ZIP contents, or edit output files. It only reads the command
# center API plus the manifest-only ZIP validator.
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

$CommandCenterUrl = "http://127.0.0.1:$Port/api/status"
$PythonExe = Join-Path $ProjectRoot ".venv-mgba\bin\python.exe"
$Validator = Join-Path $ProjectRoot "tools\spinda\phase3_zip_validator.py"
$OutputDir = Join-Path $ProjectRoot "Phase3SpindaBlocks"

function ConvertTo-ShortJson {
    param([object]$Value)
    $Value | ConvertTo-Json -Depth 8
}

function Invoke-CommandCenterRead {
    try {
        $status = Invoke-RestMethod -Uri $CommandCenterUrl -TimeoutSec 8
        return [pscustomobject]@{
            ok = $true
            url = $CommandCenterUrl
            complete_lanes = $status.progress.complete_lanes
            target_lanes = $status.progress.target_lanes
            completed_spindas = $status.progress.completed_spindas
            running_workers = $status.workers.running_workers
            watcher_status = $status.watcher.status
            watcher_checks = $status.watcher.summary.check_count
            bad_zip_artifacts = $status.health.bad_zip_artifacts
            zero_size_zips = $status.health.zero_size_zips
            tmp_files = $status.health.tmp_files
            bad_names = $status.health.bad_names
            last_good_lane = $status.health.last_good_lane
            pool_status_age_seconds = $status.workers.pool_status_age_seconds
        }
    } catch {
        return [pscustomobject]@{
            ok = $false
            url = $CommandCenterUrl
            error = $_.Exception.Message
        }
    }
}

function Invoke-ManifestOnlyValidator {
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        return [pscustomobject]@{
            ok = $false
            exit_code = $null
            error = "Python exe missing: $PythonExe"
        }
    }
    if (-not (Test-Path -LiteralPath $Validator)) {
        return [pscustomobject]@{
            ok = $false
            exit_code = $null
            error = "Validator missing: $Validator"
        }
    }

    $arguments = @(
        $Validator,
        "--root", $OutputDir,
        "--manifest-only",
        "--allow-incomplete"
    )
    $completed = & $PythonExe @arguments 2>&1
    $exitCode = $LASTEXITCODE
    $text = ($completed | Out-String).Trim()
    return [pscustomobject]@{
        ok = ($exitCode -eq 0)
        exit_code = $exitCode
        raw = $text
    }
}

$commandCenter = Invoke-CommandCenterRead
$manifest = Invoke-ManifestOnlyValidator
$result = [pscustomobject]@{
    generated_at = (Get-Date).ToString("yyyy-MM-dd HH:mm:ss zzz")
    command_center = $commandCenter
    manifest_only_validation = $manifest
}

if ($Json) {
    ConvertTo-ShortJson $result
} else {
    Write-Host "Phase 3 low-risk check"
    Write-Host "Time: $($result.generated_at)"
    Write-Host ""
    if ($commandCenter.ok) {
        Write-Host "Command center: OK $($commandCenter.url)"
        Write-Host "Lanes: $($commandCenter.complete_lanes) / $($commandCenter.target_lanes)"
        Write-Host "Spindas: $($commandCenter.completed_spindas)"
        Write-Host "Workers: $($commandCenter.running_workers)"
        Write-Host "Watcher: $($commandCenter.watcher_status) checks=$($commandCenter.watcher_checks)"
        Write-Host "Health: bad_artifacts=$($commandCenter.bad_zip_artifacts) zero_size=$($commandCenter.zero_size_zips) tmp=$($commandCenter.tmp_files) bad_names=$($commandCenter.bad_names)"
        Write-Host "Last good lane: $($commandCenter.last_good_lane)"
    } else {
        Write-Host "Command center: FAIL $($commandCenter.error)"
    }
    Write-Host ""
    Write-Host "Manifest-only validator: $(if ($manifest.ok) { 'OK' } else { 'FAIL' }) exit=$($manifest.exit_code)"
    if ($manifest.raw) {
        Write-Host $manifest.raw
    }
}

if (-not $commandCenter.ok -or -not $manifest.ok) {
    exit 1
}
exit 0
