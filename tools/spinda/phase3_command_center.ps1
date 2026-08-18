param(
    [ValidateSet("Start", "Stop", "Restart", "Status", "Open", "Killswitch")]
    [string]$Action = "Start",

    [string]$ProjectRoot = "",
    [string]$HostName = "0.0.0.0",
    [int]$Port = 235,
    [ValidateSet("coordinator", "subordinate")]
    [string]$Role = "coordinator",
    [switch]$Online,
    [ValidateSet("http", "https")]
    [string]$PrimaryScheme = "http",
    [string]$PrimaryHost = "127.0.0.1",
    [int]$PrimaryPort = 235,
    [ValidateSet("http", "https")]
    [string]$AdvertiseScheme = "http",
    [string]$AdvertiseHost = "",
    [int]$AdvertisePort = 0,
    [switch]$OpenBrowser
)

# If Windows blocks .ps1 files, launch this through:
#   phase3_command_center.cmd
# That wrapper calls powershell.exe with -ExecutionPolicy Bypass before this
# script is parsed.
$ErrorActionPreference = "Stop"

if ([string]::IsNullOrWhiteSpace($ProjectRoot)) {
    $ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

$ScriptPath = Join-Path $ProjectRoot "tools\spinda\phase3_command_center_web.py"
$WatcherScriptPath = Join-Path $ProjectRoot "tools\spinda\phase3_independent_watcher.py"
$VenvRoot = Join-Path $ProjectRoot ".venv-mgba"
$VenvPythonExe = Join-Path $VenvRoot "bin\python.exe"
$PortablePythonExe = Join-Path $ProjectRoot "portable-python\python.exe"
$DirectPythonExe = "C:\msys64\mingw64\bin\python3.12.exe"
$PythonExe = if (Test-Path -LiteralPath $PortablePythonExe) { $PortablePythonExe } elseif (Test-Path -LiteralPath $DirectPythonExe) { $DirectPythonExe } else { $VenvPythonExe }
$VenvLibDir = Join-Path $VenvRoot "lib"
$VenvSitePackages = $null
if (Test-Path -LiteralPath $VenvLibDir) {
    $VenvSitePackages = Get-ChildItem -LiteralPath $VenvLibDir -Directory -Filter "python*" |
        Sort-Object Name -Descending |
        Select-Object -First 1 |
        ForEach-Object { Join-Path $_.FullName "site-packages" }
}
$OutputDir = Join-Path $ProjectRoot "Phase3SpindaBlocks"
$PoolStatusPath = Join-Path $OutputDir "_native_phase3_worker_pool_status.json"
$PoolControlPath = Join-Path $OutputDir "_native_phase3_worker_pool_control.json"
$WatcherStatusPath = Join-Path $OutputDir "_phase3_independent_watcher_status.json"
$LedgerClientStatusPath = Join-Path $OutputDir "_phase3_ledger_worker_client_status.json"
$CoordinationSettingsPath = Join-Path $OutputDir "_phase3_command_center_network.json"
$LedgerPath = Join-Path $OutputDir "_phase3_lane_ledger.json"
$CacheDir = Join-Path $OutputDir "_cache"
$StdoutLog = Join-Path $OutputDir "_phase3_command_center_web.log"
$StderrLog = Join-Path $OutputDir "_phase3_command_center_web.err.log"
$WatcherStdoutLog = Join-Path $OutputDir "_phase3_independent_watcher.log"
$WatcherStderrLog = Join-Path $OutputDir "_phase3_independent_watcher.err.log"
$LocalUrl = "http://127.0.0.1:$Port"

function Assert-CommandCenterFiles {
    if (-not (Test-Path -LiteralPath $ProjectRoot)) {
        throw "Project root missing: $ProjectRoot"
    }
    if (-not (Test-Path -LiteralPath $ScriptPath)) {
        throw "Command center script missing: $ScriptPath"
    }
    if (-not (Test-Path -LiteralPath $WatcherScriptPath)) {
        throw "Independent watcher script missing: $WatcherScriptPath"
    }
    if (-not (Test-Path -LiteralPath $PythonExe)) {
        throw "Python exe missing: $PythonExe"
    }
    if (-not (Test-Path -LiteralPath $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir | Out-Null
    }
}

function Set-PythonRuntimeEnvironment {
    # The local venv's python.exe is a launcher that leaves both parent and
    # child Python rows visible to the watcher. Start the real MSYS Python
    # directly, then expose the venv site-packages so Flask is still available.
    if ($VenvSitePackages -and (Test-Path -LiteralPath $VenvSitePackages)) {
        $parts = @()
        if ($env:PYTHONPATH) {
            $parts += $env:PYTHONPATH -split ";"
        }
        if ($parts -notcontains $VenvSitePackages) {
            $parts = @($VenvSitePackages) + $parts
        }
        $env:PYTHONPATH = ($parts | Where-Object { $_ }) -join ";"
    }
    if (Test-Path -LiteralPath $VenvRoot) {
        $env:VIRTUAL_ENV = $VenvRoot
    }
}

function Get-CommandCenterProcesses {
    $needle = "tools\spinda\phase3_command_center_web.py"
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -and
            $_.CommandLine -like "*$needle*"
        }
}

function Get-WatcherProcesses {
    $needle = "tools\spinda\phase3_independent_watcher.py"
    Get-CimInstance Win32_Process |
        Where-Object {
            $_.Name -like "python*" -and
            $_.CommandLine -and
            $_.CommandLine -like "*$needle*"
        }
}

function Start-Watcher {
    Assert-CommandCenterFiles
    Set-PythonRuntimeEnvironment

    $existing = @(Get-WatcherProcesses)
    if ($existing.Count -gt 0) {
        Write-Host "Independent watcher already running:"
        $existing | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
        return
    }

    $watcherEvents = Join-Path $OutputDir "_phase3_independent_watcher_events.jsonl"
    $arguments = @(
        $WatcherScriptPath,
        "--folder", $OutputDir,
        "--pool-status", $PoolStatusPath,
        "--status-out", $WatcherStatusPath,
        "--events-out", $watcherEvents,
        "--command-center-url", "$LocalUrl/api/status",
        "--interval-seconds", "300",
        "--process-check-interval-seconds", "300",
        "--command-center-check-interval-seconds", "300"
    )

    $proc = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $WatcherStdoutLog `
        -RedirectStandardError $WatcherStderrLog `
        -PassThru

    Write-Host "Started independent watcher PID $($proc.Id)"
}

function Stop-Watcher {
    $procs = @(Get-WatcherProcesses)
    if ($procs.Count -eq 0) {
        Write-Host "No independent watcher process found."
        return
    }

    foreach ($proc in $procs) {
        Write-Host "Stopping independent watcher PID $($proc.ProcessId)"
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
            Write-Host "Independent watcher PID $($proc.ProcessId) already stopped."
        }
    }
}

function Stop-CommandCenter {
    $procs = @(Get-CommandCenterProcesses)
    if ($procs.Count -eq 0) {
        Write-Host "No command center process found."
        return
    }

    foreach ($proc in $procs) {
        Write-Host "Stopping command center PID $($proc.ProcessId)"
        try {
            Stop-Process -Id $proc.ProcessId -Force -ErrorAction Stop
        } catch [Microsoft.PowerShell.Commands.ProcessCommandException] {
            Write-Host "Command center PID $($proc.ProcessId) already stopped."
        }
    }
}

function Start-CommandCenter {
    Assert-CommandCenterFiles
    Set-PythonRuntimeEnvironment

    $existing = @(Get-CommandCenterProcesses)
    if ($existing.Count -gt 0) {
        Write-Host "Command center already running:"
        $existing | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
        Start-Watcher
        return
    }

    $arguments = @(
        $ScriptPath,
        "--host", $HostName,
        "--port", $Port.ToString(),
        "--folder", $OutputDir,
        "--pool-status", $PoolStatusPath,
        "--pool-control", $PoolControlPath,
        "--watcher-status", $WatcherStatusPath,
        "--ledger-client-status", $LedgerClientStatusPath,
        "--coordination-settings", $CoordinationSettingsPath,
        "--ledger", $LedgerPath,
        "--cache-dir", $CacheDir,
        "--sample-interval", "5",
        "--zip-scan-interval", "60",
        "--host-resource-interval", "15",
        "--python-exe", $PythonExe,
        "--role", $Role,
        "--primary-scheme", $PrimaryScheme,
        "--primary-host", $PrimaryHost,
        "--primary-port", $PrimaryPort.ToString()
    )
    if ($Online) {
        $arguments += "--online"
    } else {
        $arguments += "--offline"
    }
    if ($AdvertiseHost) {
        $arguments += @("--advertise-scheme", $AdvertiseScheme)
        $arguments += @("--advertise-host", $AdvertiseHost)
    }
    if ($AdvertisePort -gt 0) {
        $arguments += @("--advertise-port", $AdvertisePort.ToString())
    }

    $proc = Start-Process `
        -FilePath $PythonExe `
        -ArgumentList $arguments `
        -WorkingDirectory $ProjectRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $StdoutLog `
        -RedirectStandardError $StderrLog `
        -PassThru

    Write-Host "Started command center PID $($proc.Id)"
    Wait-CommandCenter
    Start-Watcher
}

function Wait-CommandCenter {
    for ($attempt = 1; $attempt -le 20; $attempt++) {
        try {
            $status = Invoke-RestMethod -Uri "$LocalUrl/api/status" -TimeoutSec 2
            $done = $status.progress.complete_lanes
            $target = $status.progress.target_lanes
            $spindas = $status.progress.completed_spindas
            Write-Host "Command center ready: $LocalUrl"
            Write-Host "Lanes: $done / $target"
            Write-Host "Spindas: $spindas"
            return
        } catch {
            Start-Sleep -Milliseconds 500
        }
    }

    Write-Warning "Command center did not answer yet. Check logs:"
    Write-Warning $StdoutLog
    Write-Warning $StderrLog
}

function Show-CommandCenterStatus {
    $procs = @(Get-CommandCenterProcesses)
    if ($procs.Count -eq 0) {
        Write-Host "Command center process: not running"
    } else {
        Write-Host "Command center process:"
        $procs | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
    }

    $watchers = @(Get-WatcherProcesses)
    if ($watchers.Count -eq 0) {
        Write-Host "Independent watcher process: not running"
    } else {
        Write-Host "Independent watcher process:"
        $watchers | Select-Object ProcessId, CommandLine | Format-Table -AutoSize
    }

    try {
        $status = Invoke-RestMethod -Uri "$LocalUrl/api/status" -TimeoutSec 5
        Write-Host "API: online"
        Write-Host "URL: $LocalUrl"
        Write-Host "Lanes: $($status.progress.complete_lanes) / $($status.progress.target_lanes)"
        Write-Host "Spindas: $($status.progress.completed_spindas) / $($status.progress.target_spindas)"
        Write-Host "Workers: $($status.workers.running_workers)"
        Write-Host "Coordination: role=$($status.coordination.role) online=$($status.coordination.online) primary=$($status.coordination.primary_url)"
    } catch {
        Write-Host "API: offline"
    }
}

function Open-CommandCenter {
    Start-Process $LocalUrl
    Write-Host "Opened $LocalUrl"
}

function Invoke-CommandCenterKillswitch {
    try {
        $body = @{ confirm = $true } | ConvertTo-Json
        $result = Invoke-RestMethod `
            -Uri "$LocalUrl/api/control/killswitch" `
            -Method Post `
            -Body $body `
            -ContentType "application/json" `
            -TimeoutSec 10
        Write-Host "Killswitch sent."
        Write-Host "PID candidates: $($result.pid_candidates -join ', ')"
    } catch {
        throw "Killswitch failed. Is command center running at $LocalUrl ? $($_.Exception.Message)"
    }
}

switch ($Action) {
    "Start" {
        Start-CommandCenter
        if ($OpenBrowser) { Open-CommandCenter }
    }
    "Stop" {
        Stop-CommandCenter
        Stop-Watcher
    }
    "Restart" {
        Stop-CommandCenter
        Stop-Watcher
        Start-CommandCenter
        if ($OpenBrowser) { Open-CommandCenter }
    }
    "Status" {
        Show-CommandCenterStatus
    }
    "Open" {
        Open-CommandCenter
    }
    "Killswitch" {
        Invoke-CommandCenterKillswitch
    }
}
