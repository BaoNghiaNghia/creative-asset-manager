# Offline runtime verification. No install, login, network, or CAM calls.
# Refresh the current process PATH after user-scope WinGet installs.
$ErrorActionPreference = 'Stop'
$env:Path = ([Environment]::GetEnvironmentVariable('Path','Machine') + ';' +
             [Environment]::GetEnvironmentVariable('Path','User') + ';' + $env:Path)
$install = Get-ItemProperty -LiteralPath 'HKCU:\Software\Python\PythonCore\3.13\InstallPath' -ErrorAction SilentlyContinue
$python = $null
if ($install -and $install.ExecutablePath -and (Test-Path -LiteralPath $install.ExecutablePath -PathType Leaf)) {
    $python = $install.ExecutablePath
} else {
    $found = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($found) { $python = $found.Source }
}
if (-not $python) { throw 'Python runtime unavailable' }
$version = & $python --version
if ($LASTEXITCODE -ne 0 -or $version -notmatch '^Python 3\.(1[0-9]|[2-9][0-9])\.') { throw 'Python 3.10+ required' }
Write-Host $version
Push-Location -LiteralPath $PSScriptRoot
try {
    # Native MP4 pass-through tests use only Python standard library.
    & $python -m unittest discover -s . -p 'test_*.py' -v
    if ($LASTEXITCODE -ne 0) { throw 'Offline regression failed' }
    Write-Host 'OFFLINE_RUNTIME_READY; LIVE_RENDERER_NOT_VERIFIED'
} finally {
    Pop-Location
}