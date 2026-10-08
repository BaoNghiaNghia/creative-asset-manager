param(
  [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Project = Join-Path $RepoRoot "apps\scout-manager-desktop"
if (-not (Test-Path -LiteralPath (Join-Path $Project "src-tauri\Cargo.toml"))) {
  throw "Missing Tauri project: $Project"
}
if (-not (Get-Command cargo.exe -ErrorAction SilentlyContinue)) {
  throw "Rust MSVC toolchain is missing; install rustup and Visual Studio Build Tools."
}
if (-not (Get-Command npm.cmd -ErrorAction SilentlyContinue)) {
  throw "Node.js/npm is not available."
}

Push-Location $Project
$OriginalCi = $env:CI
try {
  # CodeLocal Windows sessions may set CI=1; Tauri expects true/false.
  $env:CI = "false"
  Write-Host "==> Installing locked dependencies" -ForegroundColor Cyan
  & npm.cmd ci --no-audit --no-fund
  if ($LASTEXITCODE -ne 0) { throw "npm ci failed" }
  if (-not $SkipTests) {
    Write-Host "==> Running desktop frontend tests" -ForegroundColor Cyan
    & npm.cmd test
    if ($LASTEXITCODE -ne 0) { throw "frontend tests failed" }
    Write-Host "==> Running Rust manager tests" -ForegroundColor Cyan
    & cargo.exe test --manifest-path (Join-Path $Project "src-tauri\Cargo.toml")
    if ($LASTEXITCODE -ne 0) { throw "Rust tests failed" }
  }
  Write-Host "==> Packaging Windows NSIS installer" -ForegroundColor Cyan
  & npm.cmd run tauri:build
  if ($LASTEXITCODE -ne 0) { throw "Tauri desktop build failed" }
  $bundleDir = Join-Path $Project "src-tauri\target\release\bundle\nsis"
  $version = (Get-Content -LiteralPath (Join-Path $Project "package.json") -Raw | ConvertFrom-Json).version
  $expected = Join-Path $bundleDir ("RRUGC Scout Manager_" + $version + "_x64-setup.exe")
  if (-not (Test-Path -LiteralPath $expected -PathType Leaf)) {
    throw ("Expected NSIS installer not found: " + $expected)
  }
  $releaseDir = Join-Path $RepoRoot "scout-manager-releases"
  New-Item -ItemType Directory -Force -Path $releaseDir | Out-Null
  $destination = Join-Path $releaseDir ("RRUGC_Scout_Manager_" + $version + "_x64_Setup.exe")
  Copy-Item -LiteralPath $expected -Destination $destination -Force
  # Portable distribution provides an explicit opt-in migration path that
  # does not need an installer or overwrite the WinForms fallback.
  $builtExe = Join-Path $Project "src-tauri\target\release\cam-scout-manager.exe"
  if (-not (Test-Path -LiteralPath $builtExe -PathType Leaf)) {
    throw ("Built Tauri executable is missing: " + $builtExe)
  }
  $portableVersioned = Join-Path $releaseDir ("RRUGC_Scout_Manager_" + $version + "_x64_Portable.exe")
  $portableLatest = Join-Path $releaseDir "RRUGC_Scout_Manager_Latest_x64.exe"
  Copy-Item -LiteralPath $builtExe -Destination $portableVersioned -Force
  Copy-Item -LiteralPath $portableVersioned -Destination $portableLatest -Force
  Write-Host "==> Installer and portable executable ready:" -ForegroundColor Green
  Get-Item -LiteralPath $destination, $portableVersioned, $portableLatest | Select-Object FullName, Length, LastWriteTime | Format-Table -AutoSize
  Get-FileHash -LiteralPath $destination, $portableLatest -Algorithm SHA256 | Select-Object Path, Hash | Format-List
  Write-Host "Launch the native UI with START_SCOUT_TAURI.cmd; fallback: START_SCOUT_MANAGER.cmd."
} finally {
  $env:CI = $OriginalCi
  Pop-Location
}
