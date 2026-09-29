param(
  [string]$OutputDirectory = "release-update"
)

$ErrorActionPreference = "Stop"
$package = Get-Content (Join-Path $PSScriptRoot "..\package.json") -Raw | ConvertFrom-Json
$version = $package.version

Push-Location (Join-Path $PSScriptRoot "..")
try {
  npm ci
  npm run build
  npx electron-builder --win nsis --publish never --config.win.signAndEditExecutable=false --config.directories.output=$OutputDirectory

  $latest = Join-Path $OutputDirectory "latest.yml"
  $installer = Join-Path $OutputDirectory "Creative Asset Manager Setup $version.exe"
  $blockmap = "$installer.blockmap"
  foreach ($path in @($latest, $installer, $blockmap)) {
    if (-not (Test-Path $path)) { throw "Expected update artifact was not generated: $path" }
  }

  Write-Host ""
  Write-Host "Windows update package ready:"
  Write-Host "  Version:  $version"
  Write-Host "  Feed:     $latest"
  Write-Host "  Setup:    $installer"
  Write-Host "  Blockmap: $blockmap"
} finally {
  Pop-Location
}
