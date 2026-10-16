# Installs the current, signed-by-checksum GitHub Scout Manager release into
# the EXISTING Scan Pinterest release folder. Never deletes old releases,
# scout.local.env or Pinterest/Branding DNA profiles.
param(
  [string]$DestinationDir = "D:\Bot_Tool_Auto_Game\scan_pinterest\scout-manager-releases",
  [string]$ReleaseVersion = ""
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$manifestPath = Join-Path $repoRoot "apps\scout-manager-desktop\package.json"
$version = if ($ReleaseVersion) { $ReleaseVersion } else {
  [string]((Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json).version)
}
if ($version -notmatch '^\d+\.\d+\.\d+$') { throw "Invalid Scout Manager release version." }
$tag = "scout-manager-v" + $version
$origin = "https://github.com/BaoNghiaNghia/creative-asset-manager/releases/download/" + $tag
$files = @(
  ("RRUGC_Scout_Manager_" + $version + "_x64_Setup.exe"),
  ("RRUGC_Scout_Manager_" + $version + "_x64_Portable.exe")
)
$hashFile = "RRUGC_Scout_Manager_" + $version + "_SHA256.txt"
$allNames = @($files) + @($hashFile)

New-Item -ItemType Directory -Path $DestinationDir -Force | Out-Null
$temp = Join-Path ([IO.Path]::GetTempPath()) ("scout-release-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $temp -Force | Out-Null
try {
  foreach ($name in $allNames) {
    $url = $origin + "/" + $name
    $path = Join-Path $temp $name
    Write-Host ("Downloading " + $name)
    Invoke-WebRequest -Uri $url -OutFile $path -UseBasicParsing -MaximumRedirection 7 -TimeoutSec 240
    if (-not (Test-Path -LiteralPath $path -PathType Leaf)) {
      throw ("Download failed: " + $name)
    }
  }

  $checksums = @{}
  foreach ($line in @(Get-Content -LiteralPath (Join-Path $temp $hashFile))) {
    if ($line -match '^([A-Fa-f0-9]{64})\s{2}(.+)$') {
      $checksums[$Matches[2]] = $Matches[1].ToUpperInvariant()
    }
  }
  if ($checksums.Count -ne $files.Count) {
    throw "Release checksum list is missing or unexpected. No installation performed."
  }
  foreach ($name in $files) {
    if (-not $checksums.ContainsKey($name)) {
      throw ("Missing SHA-256 checksum: " + $name)
    }
    $actual = (Get-FileHash -LiteralPath (Join-Path $temp $name) -Algorithm SHA256).Hash.ToUpperInvariant()
    if ($actual -ne $checksums[$name]) {
      throw ("SHA-256 mismatch. No installation performed: " + $name)
    }
  }

  # Only copy files after ALL checksums pass. No destructive cleanup.
  foreach ($name in $allNames) {
    $source = Join-Path $temp $name
    $dest = Join-Path $DestinationDir $name
    if (Test-Path -LiteralPath $dest) {
      if ($name -eq $hashFile) {
        $currentHash = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToUpperInvariant()
        $expectedHash = (Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($currentHash -ne $expectedHash) { throw ("Existing checksum manifest differs. Refusing overwrite: " + $dest) }
        Write-Host ("Existing checksum manifest verified: " + $name) -ForegroundColor Green
        continue
      }
      $existing = (Get-FileHash -LiteralPath $dest -Algorithm SHA256).Hash.ToUpperInvariant()
      if ($existing -ne $checksums[$name]) {
        throw ("Existing release differs. Refusing overwrite: " + $dest)
      }
      Write-Host ("Already installed and verified: " + $name) -ForegroundColor Green
      continue
    }
    $partial = $dest + ".incoming-" + [guid]::NewGuid().ToString("N")
    try {
      Copy-Item -LiteralPath $source -Destination $partial -ErrorAction Stop
      if ($name -ne $hashFile) {
        $copyHash = (Get-FileHash -LiteralPath $partial -Algorithm SHA256).Hash.ToUpperInvariant()
        if ($copyHash -ne $checksums[$name]) { throw ("Copy checksum mismatch: " + $name) }
      }
      Move-Item -LiteralPath $partial -Destination $dest -ErrorAction Stop
      Write-Host ("Installed: " + $dest) -ForegroundColor Green
    } finally {
      if (Test-Path -LiteralPath $partial) {
        Remove-Item -LiteralPath $partial -Force -ErrorAction SilentlyContinue
      }
    }
  }

  # Write the selected release only AFTER all files are installed and
  # checksums pass. The active marker is not a binary and may be replaced;
  # running EXEs, browser profiles and previous versioned files are untouched.
  $marker = Join-Path $DestinationDir "RRUGC_Scout_Manager_Active_Version.txt"
  $markerStage = $marker + ".incoming-" + [guid]::NewGuid().ToString("N")
  try {
    [IO.File]::WriteAllText($markerStage, ($version + [Environment]::NewLine), [Text.Encoding]::ASCII)
    # Avoid replacing the active marker when the same verified version is
    # already selected. This also makes repeated installs idempotent on PS 5.1.
    if (Test-Path -LiteralPath $marker) {
      $selected = (Get-Content -LiteralPath $marker -Raw -ErrorAction Stop).Trim()
      if ($selected -ne $version) {
        Copy-Item -LiteralPath $markerStage -Destination $marker -Force -ErrorAction Stop
      }
    } else {
      Move-Item -LiteralPath $markerStage -Destination $marker -ErrorAction Stop
    }
  } finally {
    if (Test-Path -LiteralPath $markerStage) { Remove-Item -LiteralPath $markerStage -Force -ErrorAction SilentlyContinue }
  }

  # Active-version marker is consulted first by START_SCOUT_TAURI.cmd;
  # old versions and currently running processes remain unaffected.
  Write-Host ""
  Write-Host ("Scout Manager " + $version + " release copied successfully.") -ForegroundColor Green
  Write-Host "Old versions and active Scout processes were preserved."
  Write-Host "Close/reopen Scout Manager via START_SCOUT_TAURI.cmd when convenient."
} finally {
  Remove-Item -LiteralPath $temp -Recurse -Force -ErrorAction SilentlyContinue
}
