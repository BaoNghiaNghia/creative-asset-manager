param(
    [string]$RepositoryRoot = (Split-Path -Parent $PSScriptRoot)
)

$ErrorActionPreference = "Stop"
$PSNativeCommandUseErrorActionPreference = $false
if ([Environment]::OSVersion.Platform -ne [PlatformID]::Win32NT) {
    throw "Scout launcher regression requires Windows."
}

$fixture = Join-Path ([IO.Path]::GetTempPath()) ("cam-scout-launcher-" + [Guid]::NewGuid().ToString("N"))
$priorResolve = [Environment]::GetEnvironmentVariable("CAM_SCOUT_MANAGER_RESOLVE_ONLY", "Process")
$priorRepo = [Environment]::GetEnvironmentVariable("CAM_SCOUT_REPO_ROOT", "Process")
$entered = $false
try {
    $releases = Join-Path $fixture "scout-manager-releases"
    New-Item -ItemType Directory -Path $releases -Force | Out-Null
    Copy-Item -LiteralPath (Join-Path $RepositoryRoot "START_SCOUT_TAURI.cmd") -Destination $fixture
    [IO.File]::WriteAllText((Join-Path $releases "RRUGC_Scout_Manager_Active_Version.txt"), ("0.0.123" + [char]10))
    # This is not executable. Every case must stay in the no-launch resolver.
    [IO.File]::WriteAllText(
        (Join-Path $releases "RRUGC_Scout_Manager_0.0.123_x64_Portable.exe"),
        "Resolve-only fixture; never execute."
    )
    $env:CAM_SCOUT_MANAGER_RESOLVE_ONLY = "1"
    $env:CAM_SCOUT_REPO_ROOT = $fixture
    Push-Location -LiteralPath $fixture
    $entered = $true
    $cases = @(
        @{ Arguments = ''; Code = 0; Action = 'idle' },
        @{ Arguments = '--start-automation'; Code = 0; Action = 'start-automation' },
        @{ Arguments = '"--start-automation"'; Code = 0; Action = 'start-automation' },
        @{ Arguments = '--START-AUTOMATION'; Code = 2 },
        @{ Arguments = '--start-automation=true'; Code = 2 },
        @{ Arguments = '--start-automation extra'; Code = 2 },
        @{ Arguments = '--start-automation --start-automation'; Code = 2 },
        @{ Arguments = '--start-automation ""'; Code = 2 },
        @{ Arguments = '""'; Code = 2 },
        @{ Arguments = '"unknown & echo CMD_ARGUMENT_WAS_EXECUTED>unexpected.txt"'; Code = 2 },
        @{ Arguments = '"unknown | exit 0"'; Code = 2 },
        @{ Arguments = 'operator-secret-must-not-be-printed'; Code = 2 }
    )
    foreach ($case in $cases) {
        $output = (& cmd.exe /D /C ("START_SCOUT_TAURI.cmd " + $case.Arguments) 2>&1) -join [char]10
        $code = $LASTEXITCODE
        if ($code -ne $case.Code) {
            throw ("Unexpected launcher exit code: expected " + $case.Code + ", got " + $code)
        }
        if ($case.Code -eq 0) {
            if ($output -notlike "*RRUGC_Scout_Manager_0.0.123_x64_Portable.exe*" -or
                $output -notlike ("*Startup action: " + $case.Action + "*")) {
                throw "Launcher did not resolve the expected literal startup action."
            }
        } elseif (-not $output.Contains("[ERROR] Usage: START_SCOUT_TAURI.cmd")) {
            throw "Invalid arguments did not receive the fixed usage diagnostic."
        }
        if ($output -like "*operator-secret-must-not-be-printed*" -or
            (Test-Path -LiteralPath (Join-Path $fixture "unexpected.txt"))) {
            throw "Launcher reflected or executed an invalid argument."
        }
    }
    Write-Output ("Scout startup launcher regression: " + $cases.Count + " cases passed; no GUI or Scout launched.")
} finally {
    if ($entered) { Pop-Location }
    [Environment]::SetEnvironmentVariable("CAM_SCOUT_MANAGER_RESOLVE_ONLY", $priorResolve, "Process")
    [Environment]::SetEnvironmentVariable("CAM_SCOUT_REPO_ROOT", $priorRepo, "Process")
    if (Test-Path -LiteralPath $fixture) {
        Remove-Item -LiteralPath $fixture -Recurse -Force
    }
}
