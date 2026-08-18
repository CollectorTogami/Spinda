param(
  [string]$ShardDir = (Join-Path $PSScriptRoot "..\..\Helper-PC-Artifacts\v7-materialized-v2-shards-512"),
  [string]$ExtractDir = (Join-Path $PSScriptRoot "..\..\Helper-PC-Artifacts\v7-physical-extracted-zips"),
  [string]$NativeExe = (Join-Path $PSScriptRoot "spc3_prototype\spc3_prototype.exe"),
  [int]$ProgressEvery = 4,
  [switch]$Resume
)

$ErrorActionPreference = "Stop"

function Test-UnpackReportOk {
  param([string]$Path)
  if (-not (Test-Path -LiteralPath $Path)) {
    return $false
  }
  try {
    $report = Get-Content -Raw -LiteralPath $Path | ConvertFrom-Json
    return [bool]$report.ok -and [int]$report.crc_mismatches -eq 0
  } catch {
    return $false
  }
}

function Write-LaneStatusSidecars {
  param([object]$Report)
  foreach ($output in $Report.outputs) {
    if ($output.lane -notmatch '^0x([0-9A-Fa-f]{4})$') {
      throw "bad lane in unpack report: $($output.lane)"
    }
    $lane = $Matches[1].ToUpperInvariant()
    $zipPath = [string]$output.file
    $statusPath = Join-Path $ExtractDir ("_0x{0}.phase3_status.json" -f $lane)
    $status = [ordered]@{
      status = "complete"
      generated_records = 65536
      selected_targets = 65536
      zip_method = "store"
      output_zip_path = $zipPath
      source = "spc3_shard_unpack"
      source_spc3 = [string]$Report.input
      payload_crc32 = $output.payload_crc32
      output_payload_crc32 = $output.output_payload_crc32
      output_crc32 = $output.output_crc32
    }
    $json = $status | ConvertTo-Json -Depth 5
    [System.IO.File]::WriteAllText(
      $statusPath,
      $json + [System.Environment]::NewLine,
      [System.Text.UTF8Encoding]::new($false)
    )
  }
}

$resolvedShardDir = (Resolve-Path -LiteralPath $ShardDir).Path
$resolvedNative = (Resolve-Path -LiteralPath $NativeExe).Path
New-Item -ItemType Directory -Path $ExtractDir -Force | Out-Null
$resolvedExtractDir = (Resolve-Path -LiteralPath $ExtractDir).Path

$shards = Get-ChildItem -LiteralPath $resolvedShardDir -Filter "typed-v2-*.spc3" | Sort-Object Name
if ($shards.Count -eq 0) {
  throw "no shards found in $resolvedShardDir"
}

$started = Get-Date
$summary = [ordered]@{
  schema = "spc3_shard_unpack_summary.v1"
  started = $started.ToString("o")
  shard_dir = $resolvedShardDir
  extract_dir = $resolvedExtractDir
  native_exe = $resolvedNative
  shard_count = $shards.Count
  completed_shards = 0
  skipped_shards = 0
  lane_count = 0
  crc_mismatches = 0
  reports = @()
}

foreach ($shard in $shards) {
  $reportPath = Join-Path $resolvedExtractDir ("_spc3_unpack_{0}.json" -f $shard.BaseName)
  if ($Resume -and (Test-UnpackReportOk -Path $reportPath)) {
    $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    Write-LaneStatusSidecars -Report $report
    $summary.skipped_shards += 1
  } else {
    & $resolvedNative --mode unpack --input $shard.FullName --unpack-dir $resolvedExtractDir --unpack-format zip --report $reportPath
    if ($LASTEXITCODE -ne 0) {
      throw "native unpack failed for $($shard.Name) with exit code $LASTEXITCODE"
    }
    $report = Get-Content -Raw -LiteralPath $reportPath | ConvertFrom-Json
    if (-not [bool]$report.ok -or [int]$report.crc_mismatches -ne 0) {
      throw "unpack report failed for $($shard.Name): ok=$($report.ok) crc_mismatches=$($report.crc_mismatches)"
    }
    Write-LaneStatusSidecars -Report $report
    $summary.completed_shards += 1
  }

  $summary.lane_count += [int]$report.lane_count
  $summary.crc_mismatches += [int]$report.crc_mismatches
  $summary.reports += [ordered]@{
    shard = $shard.Name
    report = $reportPath
    lane_count = [int]$report.lane_count
    crc_mismatches = [int]$report.crc_mismatches
    total_ms = [double]$report.total_ms
  }

  $processed = $summary.completed_shards + $summary.skipped_shards
  if ($ProgressEvery -gt 0 -and (($processed % $ProgressEvery) -eq 0 -or $processed -eq $shards.Count)) {
    $elapsed = ((Get-Date) - $started).TotalSeconds
    Write-Host ("unpacked {0}/{1} shards, lanes={2}, elapsed={3:n1}s" -f $processed, $shards.Count, $summary.lane_count, $elapsed)
  }
}

$summary.finished = (Get-Date).ToString("o")
$summary.elapsed_seconds = ((Get-Date) - $started).TotalSeconds
$summaryPath = Join-Path $resolvedExtractDir "_spc3_shard_unpack_summary.json"
$summaryJson = $summary | ConvertTo-Json -Depth 8
[System.IO.File]::WriteAllText(
  $summaryPath,
  $summaryJson + [System.Environment]::NewLine,
  [System.Text.UTF8Encoding]::new($false)
)
Write-Host "SUMMARY $summaryPath"
Write-Host ("unpacked_lanes={0} crc_mismatches={1} completed_shards={2} skipped_shards={3}" -f $summary.lane_count, $summary.crc_mismatches, $summary.completed_shards, $summary.skipped_shards)
