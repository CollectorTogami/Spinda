param(
    [Parameter(Mandatory = $true)]
    [string]$RomPath,

    [string]$StatePath = (Join-Path $PSScriptRoot "..\..\..\tid 0 ready.ss0"),
    [string]$MgbaExe = (Join-Path $PSScriptRoot "..\..\..\build-mingw64-python-qt\mGBA.exe"),
    [string]$SecretIdScript = (Join-Path $PSScriptRoot "Secret-ID-Shiny-Value-Bot.py"),
    [string]$SecretIdEntryScript = (Join-Path $PSScriptRoot "Secret-ID-Shiny-Value-Bot-Entry.py"),
    [string]$TrackerScript = (Join-Path $PSScriptRoot "TSV-Save-Tracker-GUI.py"),
    [string]$PythonExe = "python",
    [string]$TrackerSaveDir = (Join-Path $PSScriptRoot "..\..\..\TSVs"),
    [string]$TrackerHost = "0.0.0.0",
    [int]$TrackerPort = 8765,
    [string]$TrackerLedger = "",
    [string[]]$TrackerExtraArgs = @(),
    [string[]]$BotExtraArgs = @(),
    [string]$BotArgsJson = "",
    [int]$WaitForSaveTsv = -1,
    [string]$WaitForSaveDir = "",
    [int]$WaitForSaveTimeoutSeconds = 900,
    [switch]$OpenBrowser,
    [switch]$NoBotDefaults
)

$ErrorActionPreference = "Stop"

function Format-Argument {
    param([string]$Value)

    if ($Value -notmatch '[\s"]') {
        return $Value
    }
    return '"' + ($Value -replace '"', '\"') + '"'
}

function Ensure-File {
    param([string]$Path, [string]$Label)

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "$Label not found: $Path"
    }
}

function Stop-ChildProcess {
    param([System.Diagnostics.Process]$Process, [string]$Label)

    if ($null -eq $Process) {
        return
    }
    if (-not $Process.HasExited) {
        try {
            $Process.Kill()
        } catch {
            Write-Host "Could not stop $Label process (PID $($Process.Id)): $($_.Exception.Message)"
        }
        try {
            $Process.WaitForExit(1500)
        } catch {
            # Best effort in shutdown.
        }
    }
}

function Wait-ForExpectedTsvSave {
    param(
        [System.Diagnostics.Process]$Process,
        [string]$SaveDir,
        [int]$Tsv,
        [datetime]$StartedAt,
        [int]$TimeoutSeconds
    )

    if ($Tsv -lt 0 -or $Tsv -gt 8191) {
        throw "WaitForSaveTsv must be in 0..8191, got $Tsv."
    }
    if (-not (Test-Path -LiteralPath $SaveDir -PathType Container)) {
        throw "WaitForSaveDir not found: $SaveDir"
    }

    $filter = "TSV-{0:D4}-sid-*.sav" -f $Tsv
    $deadline = (Get-Date).AddSeconds($TimeoutSeconds)
    Write-Host "Waiting for save: $SaveDir\$filter"
    while ((Get-Date) -lt $deadline) {
        if ($Process.HasExited) {
            break
        }
        $save = Get-ChildItem -LiteralPath $SaveDir -Filter $filter -File -ErrorAction SilentlyContinue |
            Where-Object { $_.LastWriteTime -ge $StartedAt.AddSeconds(-2) } |
            Sort-Object LastWriteTime -Descending |
            Select-Object -First 1
        if ($save) {
            Write-Host "Detected save: $($save.FullName)"
            return $save
        }
        Start-Sleep -Seconds 2
        try {
            $Process.Refresh()
        } catch {
            # Best effort while the GUI process exits.
        }
    }

    throw "Timed out waiting for padded TSV save matching $filter in $SaveDir."
}

$mgbaBuildDir = Split-Path -Parent $MgbaExe

Ensure-File -Path $MgbaExe -Label "mGBA executable"
Ensure-File -Path $SecretIdScript -Label "SID bot script"
Ensure-File -Path $SecretIdEntryScript -Label "SID bot entry script"
Ensure-File -Path $TrackerScript -Label "Tracker script"
Ensure-File -Path $RomPath -Label "ROM"
Ensure-File -Path $StatePath -Label "Ready-state .ss0"
if ($StatePath -match "tid-0x0000-hit\.ss0") {
    Write-Warning "StatePath is a hit-state checkpoint. Use the ready-state for all-TSV coverage."
}
if (-not (Test-Path -LiteralPath $TrackerSaveDir -PathType Container)) {
    throw "Tracker save directory not found: $TrackerSaveDir"
}
if ([string]::IsNullOrWhiteSpace($WaitForSaveDir)) {
    $WaitForSaveDir = $TrackerSaveDir
}
if (-not (Test-Path -LiteralPath $PythonExe -PathType Leaf)) {
    Write-Host "Configured Python executable missing: $PythonExe"
    $pythonFallback = Get-Command python -ErrorAction SilentlyContinue
    if ($null -eq $pythonFallback) {
        throw "Python not found; set -PythonExe to a valid interpreter."
    }
    $PythonExe = $pythonFallback.Source
    Write-Host "Using fallback Python interpreter: $PythonExe"
}

$botArgs = @("--sid-commit-offset", "273", "--min-wait-frames", "1", "--overwrite")
if ($NoBotDefaults) {
    $botArgs = @()
}
if ([string]::IsNullOrWhiteSpace($BotArgsJson)) {
    $BotArgsJson = [Environment]::GetEnvironmentVariable("SECRET_ID_BOT_ARGS_JSON")
}
if (-not [string]::IsNullOrWhiteSpace($BotArgsJson)) {
    try {
        $jsonArgs = ConvertFrom-Json -InputObject $BotArgsJson -ErrorAction Stop
    } catch {
        throw "Failed to parse -BotArgsJson as JSON array: $($_.Exception.Message)"
    }
    if ($null -eq $jsonArgs) {
        throw "BotArgsJson parsed to $null."
    }
    $jsonArgList = @()
    foreach ($item in @($jsonArgs)) {
        if ($item -isnot [string]) {
            throw "BotArgsJson must be a JSON array of strings."
        }
        $jsonArgList += $item
    }
    $botArgs += $jsonArgList
}
$botArgs += $BotExtraArgs

$trackerArgs = @(
    $TrackerScript,
    "--save-dir", $TrackerSaveDir,
    "--host", $TrackerHost,
    "--port", "$TrackerPort"
)
if ($TrackerLedger) {
    $trackerArgs += @("--ledger", $TrackerLedger)
}
if ($OpenBrowser) {
    $trackerArgs += "--open-browser"
}
$trackerArgs += $TrackerExtraArgs

$env:SECRET_ID_BOT_ARGS_JSON = ConvertTo-Json -InputObject $botArgs -Compress
$env:SECRET_ID_INITIAL_STATE_PATH = $StatePath

$mgbaArgs = @(
    "-C", "fpsTarget=0",
    "--script", $SecretIdEntryScript,
    $RomPath
)

$prettyTracker = ($trackerArgs | ForEach-Object { Format-Argument $_ }) -join " "
$prettyBot = ($mgbaArgs | ForEach-Object { Format-Argument $_ }) -join " "

$trackerUrl = if ($TrackerHost -eq "0.0.0.0") { "http://127.0.0.1:$TrackerPort/" } else { "http://$TrackerHost`:$TrackerPort/" }

Write-Host "Starting tracker:"
Write-Host "  $PythonExe $prettyTracker"
$trackerProc = Start-Process -FilePath $PythonExe -ArgumentList $trackerArgs -PassThru -WindowStyle Hidden
$saveDetected = $false

try {
    Write-Host "Tracker PID: $($trackerProc.Id)"
    Write-Host "Dashboard URL: $trackerUrl"
    Write-Host "Starting secret-ID bot:"
    Write-Host "  $MgbaExe $prettyBot"

    $mgbaProc = Start-Process -FilePath $MgbaExe -ArgumentList $mgbaArgs -PassThru -WorkingDirectory $mgbaBuildDir
    Write-Host "mGBA PID: $($mgbaProc.Id)"
    Write-Host "Press Ctrl+C to stop both processes."

    if ($WaitForSaveTsv -ge 0) {
        $launchTime = Get-Date
        $null = Wait-ForExpectedTsvSave `
            -Process $mgbaProc `
            -SaveDir $WaitForSaveDir `
            -Tsv $WaitForSaveTsv `
            -StartedAt $launchTime `
            -TimeoutSeconds $WaitForSaveTimeoutSeconds
        $saveDetected = $true
        Stop-ChildProcess -Process $mgbaProc -Label "mGBA"
    } else {
        $null = $mgbaProc.WaitForExit()
    }
} finally {
    Stop-ChildProcess -Process $trackerProc -Label "tracker"
    Stop-ChildProcess -Process $mgbaProc -Label "mGBA"
    Remove-Item Env:\SECRET_ID_BOT_ARGS_JSON -ErrorAction SilentlyContinue
    Remove-Item Env:\SECRET_ID_INITIAL_STATE_PATH -ErrorAction SilentlyContinue
}

if (-not $saveDetected -and $mgbaProc.ExitCode -ne 0) {
    Write-Host "mGBA exited with code $($mgbaProc.ExitCode)"
    exit $mgbaProc.ExitCode
}
