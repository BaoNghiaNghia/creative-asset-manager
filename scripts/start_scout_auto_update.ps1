param(
    [switch]$SkipUpdate,
    [switch]$KeywordMode
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$ConfigPath = Join-Path $RepoRoot "scout.local.env"
$ConfigExamplePath = Join-Path $RepoRoot "scout.local.env.example"
$RequirementsPath = Join-Path $RepoRoot "apps\rrugc_scout\requirements.txt"
$RequirementsStampPath = Join-Path $RepoRoot ".rrugc-scout-requirements.sha256"
$ScoutPath = Join-Path $RepoRoot "apps\rrugc_scout\scout.py"
$KeywordScoutPath = Join-Path $RepoRoot "apps\rrugc_scout\quote_keyword_volume.py"

function Write-Step([string]$Message) {
    Write-Host ""
    Write-Host ("==> " + $Message) -ForegroundColor Cyan
}

function Fail([string]$Message) {
    Write-Host ""
    Write-Host ("[ERROR] " + $Message) -ForegroundColor Red
    exit 1
}

function Read-LocalConfig([string]$Path) {
    $values = @{}
    foreach ($rawLine in Get-Content -LiteralPath $Path -Encoding UTF8) {
        $line = $rawLine.Trim()
        if (-not $line -or $line.StartsWith("#")) {
            continue
        }
        $separator = $line.IndexOf("=")
        if ($separator -lt 1) {
            continue
        }
        $name = $line.Substring(0, $separator).Trim()
        $value = $line.Substring($separator + 1).Trim()
        if (
            $value.Length -ge 2 -and
            (($value.StartsWith('"') -and $value.EndsWith('"')) -or
             ($value.StartsWith("'") -and $value.EndsWith("'")))
        ) {
            $value = $value.Substring(1, $value.Length - 2)
        }
        $values[$name] = $value
    }
    return $values
}

function Get-ConfigValue(
    [hashtable]$Config,
    [string]$Name,
    [string]$Default = ""
) {
    if ($Config.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace([string]$Config[$Name])) {
        return [string]$Config[$Name]
    }
    return $Default
}


function Set-LocalConfigValue(
    [string]$Path,
    [string]$Name,
    [string]$Value
) {
    $lines = @()
    if (Test-Path -LiteralPath $Path) {
        $lines = @(Get-Content -LiteralPath $Path -Encoding UTF8)
    }
    $pattern = "^\s*" + [regex]::Escape($Name) + "\s*="
    $updated = $false
    for ($index = 0; $index -lt $lines.Count; $index++) {
        if ($lines[$index] -match $pattern) {
            $lines[$index] = $Name + "=" + $Value
            $updated = $true
            break
        }
    }
    if (-not $updated) {
        $lines += ($Name + "=" + $Value)
    }
    Set-Content -LiteralPath $Path -Value $lines -Encoding UTF8
}

function Read-HiddenValue([string]$Prompt) {
    $secure = Read-Host $Prompt -AsSecureString
    $pointer = [IntPtr]::Zero
    try {
        $pointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
        return [Runtime.InteropServices.Marshal]::PtrToStringBSTR($pointer)
    }
    finally {
        if ($pointer -ne [IntPtr]::Zero) {
            [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($pointer)
        }
    }
}

function Complete-FirstRunPairing(
    [string]$Path,
    [hashtable]$Config
) {
    $agent = Get-ConfigValue $Config "RRUGC_AGENT_ID"
    $secret = Get-ConfigValue $Config "RRUGC_SCOUT_TOKEN"

    if (
        -not [string]::IsNullOrWhiteSpace($agent) -and
        -not [string]::IsNullOrWhiteSpace($secret)
    ) {
        return
    }

    Write-Step "Scout pairing"
    Write-Host "Copy Agent ID and token from Realistic Review UGC > Auto Scout > Setup & diagnostics." -ForegroundColor Yellow
    Write-Host ""

    if ([string]::IsNullOrWhiteSpace($agent)) {
        $agent = (Read-Host "Scout Agent ID").Trim()
        if ([string]::IsNullOrWhiteSpace($agent)) {
            Fail "Scout Agent ID cannot be empty."
        }
        Set-LocalConfigValue $Path "RRUGC_AGENT_ID" $agent
    }

    if ([string]::IsNullOrWhiteSpace($secret)) {
        $secret = Read-HiddenValue "Scout token (input hidden)"
        if ([string]::IsNullOrWhiteSpace($secret)) {
            Fail "Scout token cannot be empty."
        }
        Set-LocalConfigValue $Path "RRUGC_SCOUT_TOKEN" $secret
    }

    Write-Host ""
    Write-Host "Scout pairing saved locally. Continuing startup..." -ForegroundColor Green
}

function Invoke-Git([string[]]$Arguments) {
    $output = & git @Arguments 2>&1
    if ($LASTEXITCODE -ne 0) {
        throw ("git " + ($Arguments -join " ") + " failed: " + ($output -join [Environment]::NewLine))
    }
    return @($output)
}

function Ensure-ScoutBootstrap([string]$Path) {
    # A fast-forward can briefly leave the currently running launcher absent
    # from the working tree on Windows. The current PowerShell process keeps
    # running from memory, but a child -File launch will fail unless the file
    # is present again. Give Git/filesystem filters a moment to settle first.
    for ($attempt = 1; $attempt -le 10; $attempt++) {
        if (Test-Path -LiteralPath $Path -PathType Leaf) {
            return
        }
        Start-Sleep -Milliseconds 100
    }

    Write-Host "Scout updater is missing after source update. Restoring it from origin/main..." -ForegroundColor Yellow
    $parent = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $parent)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }

    $content = Invoke-Git @("show", "origin/main:scripts/start_scout_auto_update.ps1")
    if ($content.Count -eq 0) {
        throw "origin/main returned an empty Scout updater."
    }

    $temporaryPath = $Path + ".restore-" + [Guid]::NewGuid().ToString("N") + ".tmp"
    try {
        Set-Content -LiteralPath $temporaryPath -Value $content -Encoding UTF8
        if (-not (Test-Path -LiteralPath $temporaryPath -PathType Leaf)) {
            throw "Unable to stage the restored Scout updater."
        }
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    finally {
        if (Test-Path -LiteralPath $temporaryPath) {
            Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        }
    }

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw ("Scout updater is still missing after recovery: " + $Path)
    }
}

$StartupMutex = $null
$StartupMutexHeld = $false

function Acquire-ScoutStartupLock {
    if ($script:StartupMutexHeld) {
        return
    }

    $mutexName = "Local\CreativeAssetManager.RrugcScout.Startup"
    $script:StartupMutex = New-Object System.Threading.Mutex($false, $mutexName)
    $acquired = $false

    try {
        $acquired = $script:StartupMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }

    if (-not $acquired) {
        Write-Host ""
        Write-Host "Another Scout launcher is finishing shared startup setup." -ForegroundColor Yellow
        Write-Host "Waiting for Git/update/environment lock; both Scouts will run together after setup." -ForegroundColor DarkGray
        try {
            $acquired = $script:StartupMutex.WaitOne([TimeSpan]::FromMinutes(5))
        }
        catch [System.Threading.AbandonedMutexException] {
            $acquired = $true
        }
    }

    if (-not $acquired) {
        $script:StartupMutex.Dispose()
        $script:StartupMutex = $null
        Fail "Timed out waiting for the shared Scout startup lock."
    }

    $script:StartupMutexHeld = $true
}

function Release-ScoutStartupLock {
    if ($script:StartupMutexHeld -and $null -ne $script:StartupMutex) {
        try {
            $script:StartupMutex.ReleaseMutex()
        }
        catch {
            # The OS releases abandoned named mutexes when a launcher exits.
        }
        $script:StartupMutexHeld = $false
    }
    if ($null -ne $script:StartupMutex) {
        $script:StartupMutex.Dispose()
        $script:StartupMutex = $null
    }
}

$RunnerMutex = $null
$RunnerMutexHeld = $false

function Acquire-ScoutRunnerLock([string]$Mode) {
    if ($script:RunnerMutexHeld) {
        return
    }

    $safeMode = if ($Mode -eq "Keyword") { "Keyword" } else { "Review" }
    $mutexName = "Local\CreativeAssetManager.RrugcScout.Runner." + $safeMode
    $script:RunnerMutex = New-Object System.Threading.Mutex($false, $mutexName)
    $acquired = $false
    try {
        $acquired = $script:RunnerMutex.WaitOne(0)
    }
    catch [System.Threading.AbandonedMutexException] {
        $acquired = $true
    }

    if (-not $acquired) {
        $script:RunnerMutex.Dispose()
        $script:RunnerMutex = $null
        Fail ($safeMode + " Scout is already running. Keep the existing " + $safeMode + " CMD open; Review and Keyword may run together, but duplicate instances of the same mode are blocked.")
    }

    $script:RunnerMutexHeld = $true
}

function Release-ScoutRunnerLock {
    if ($script:RunnerMutexHeld -and $null -ne $script:RunnerMutex) {
        try {
            $script:RunnerMutex.ReleaseMutex()
        }
        catch {
            # The OS also releases this mutex if the terminal is closed.
        }
        $script:RunnerMutexHeld = $false
    }
    if ($null -ne $script:RunnerMutex) {
        $script:RunnerMutex.Dispose()
        $script:RunnerMutex = $null
    }
}

Set-Location -LiteralPath $RepoRoot
Acquire-ScoutStartupLock

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    if (Test-Path -LiteralPath $ConfigExamplePath) {
        Copy-Item -LiteralPath $ConfigExamplePath -Destination $ConfigPath
    }
    else {
        Set-Content -LiteralPath $ConfigPath -Value @(
            "RRUGC_BASE_URL=https://creative-assets.ddns.net",
            "RRUGC_AGENT_ID=",
            "RRUGC_SCOUT_TOKEN=",
            "RRUGC_PROFILE_DIR=",
            "RRUGC_KEYWORD_PROFILE_DIR=",
            "RRUGC_PACE=careful",
            "RRUGC_DETAIL_CONCURRENCY=1"
        ) -Encoding UTF8
    }
}

if (-not $SkipUpdate) {
    Write-Step "Checking Creative Asset Manager updates"

    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail "Git is not installed or is not available in PATH."
    }

    $inside = (& git rev-parse --is-inside-work-tree 2>$null)
    if ($LASTEXITCODE -ne 0 -or $inside.Trim() -ne "true") {
        Fail ("Not a Git checkout: " + $RepoRoot)
    }

    $branch = (& git branch --show-current 2>$null).Trim()
    if ($LASTEXITCODE -ne 0 -or $branch -ne "main") {
        Fail ("Auto-update requires the local checkout to be on branch main. Current branch: " + $branch)
    }

    # The launcher itself is managed code, not local configuration. Older
    # releases could leave this file modified and permanently block updates.
    # Restore only this managed file from the current HEAD before checking for
    # real user/source edits; scout.local.env remains untouched.
    $managedLauncherPath = "scripts/start_scout_auto_update.ps1"
    $launcherDirty = @(& git status --porcelain --untracked-files=no -- $managedLauncherPath)
    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to inspect the managed Scout launcher."
    }
    if ($launcherDirty.Count -gt 0) {
        Write-Host "Repairing locally modified managed Scout launcher..." -ForegroundColor Yellow
        Invoke-Git @("restore", "--source=HEAD", "--staged", "--worktree", "--", $managedLauncherPath) | Out-Null
    }

    $dirty = @(& git status --porcelain --untracked-files=no)
    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to inspect the Git working tree."
    }
    if ($dirty.Count -gt 0) {
        Write-Host ""
        Write-Host "Tracked local changes were found. Auto-update was stopped to avoid overwriting your work:" -ForegroundColor Yellow
        $dirty | ForEach-Object { Write-Host ("  " + $_) -ForegroundColor Yellow }
        Write-Host ""
        Write-Host "Commit/stash those changes, then run START_SCOUT.bat again." -ForegroundColor Yellow
        exit 3
    }

    try {
        $before = ([string](Invoke-Git @("rev-parse", "HEAD") | Select-Object -First 1)).Trim()
        # Fetch directly into the remote-tracking ref. `git fetch origin main`
        # only refreshes FETCH_HEAD on some Git/Windows setups, which can leave
        # origin/main stale and make the launcher incorrectly print "Already up to date".
        Invoke-Git @(
            "fetch",
            "--quiet",
            "origin",
            "+refs/heads/main:refs/remotes/origin/main"
        ) | Out-Null
        $remote = ([string](Invoke-Git @("rev-parse", "origin/main") | Select-Object -First 1)).Trim()

        & git merge-base --is-ancestor HEAD origin/main 2>$null
        if ($LASTEXITCODE -ne 0) {
            Fail "Local branch has diverged from origin/main. Auto-update will not reset or overwrite it."
        }

        if ($before -ne $remote) {
            Write-Host ("Updating " + $before.Substring(0, 8) + " -> " + $remote.Substring(0, 8)) -ForegroundColor Green
            Invoke-Git @("merge", "--ff-only", "origin/main") | Out-Null
            Write-Host "Source update complete." -ForegroundColor Green

            # Reload the launcher from the updated checkout so changes to this
            # script take effect immediately. Re-resolve the tracked path after
            # Git updates the working tree; if a Windows filesystem/filter race
            # left it absent, recover the exact origin/main version first.
            $updatedBootstrap = Join-Path $RepoRoot "scripts\start_scout_auto_update.ps1"
            Ensure-ScoutBootstrap $updatedBootstrap

            # The updated child must acquire the same startup mutex itself.
            # Release before launching it or two simultaneous launchers could
            # deadlock here while waiting for the parent process to exit.
            Release-ScoutStartupLock
            if ($KeywordMode) {
                & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $updatedBootstrap -SkipUpdate -KeywordMode
            }
            else {
                & powershell.exe -NoLogo -NoProfile -ExecutionPolicy Bypass -File $updatedBootstrap -SkipUpdate
            }
            exit $LASTEXITCODE
        }

        Write-Host ("Already up to date: " + $before.Substring(0, 8)) -ForegroundColor Green
    }
    catch {
        Fail ("Auto-update failed. Scout was not started with a partial update. " + $_.Exception.Message)
    }
}

$config = Read-LocalConfig $ConfigPath
Complete-FirstRunPairing $ConfigPath $config
$config = Read-LocalConfig $ConfigPath

$baseUrl = Get-ConfigValue $config "RRUGC_BASE_URL" "https://creative-assets.ddns.net"
$agentId = Get-ConfigValue $config "RRUGC_AGENT_ID"
$token = Get-ConfigValue $config "RRUGC_SCOUT_TOKEN"
$profileDir = Get-ConfigValue $config "RRUGC_PROFILE_DIR" (Join-Path $RepoRoot "pinterest-profile")
$keywordProfileDir = Get-ConfigValue $config "RRUGC_KEYWORD_PROFILE_DIR" (Join-Path $RepoRoot "pinterest-profile-keyword")
$profileDir = [IO.Path]::GetFullPath($profileDir).TrimEnd([char[]]"\/")
$keywordProfileDir = [IO.Path]::GetFullPath($keywordProfileDir).TrimEnd([char[]]"\/")
$pathComparer = [StringComparer]::OrdinalIgnoreCase
$profileSeparator = [IO.Path]::DirectorySeparatorChar
if (
    $pathComparer.Equals($profileDir, $keywordProfileDir) -or
    $keywordProfileDir.StartsWith($profileDir + $profileSeparator, [StringComparison]::OrdinalIgnoreCase) -or
    $profileDir.StartsWith($keywordProfileDir + $profileSeparator, [StringComparison]::OrdinalIgnoreCase)
) {
    Fail (
        "Review Scout and Keyword Scout must use two independent Chrome profile folders. " +
        "Current Review profile: " + $profileDir + "; Keyword profile: " + $keywordProfileDir
    )
}
$machineLabel = Get-ConfigValue $config "RRUGC_MACHINE_LABEL"
$pace = Get-ConfigValue $config "RRUGC_PACE" "careful"
$detailConcurrency = "1"
Set-LocalConfigValue $ConfigPath "RRUGC_DETAIL_CONCURRENCY" "1"
$chromeExecutable = Get-ConfigValue $config "RRUGC_CHROME_EXECUTABLE"
$configuredPython = Get-ConfigValue $config "RRUGC_PYTHON"

if ([string]::IsNullOrWhiteSpace($agentId)) {
    Fail ("RRUGC_AGENT_ID is missing in " + $ConfigPath)
}
if ([string]::IsNullOrWhiteSpace($token)) {
    Fail ("RRUGC_SCOUT_TOKEN is missing in " + $ConfigPath)
}
if ($pace -notin @("careful", "balanced")) {
    Fail "RRUGC_PACE must be careful or balanced."
}
$detailValue = 0
if (-not [int]::TryParse($detailConcurrency, [ref]$detailValue)) {
    Fail "Unable to configure low-footprint detail mode."
}

if (-not (Test-Path -LiteralPath $profileDir)) {
    New-Item -ItemType Directory -Path $profileDir -Force | Out-Null
}
if (-not (Test-Path -LiteralPath $keywordProfileDir)) {
    New-Item -ItemType Directory -Path $keywordProfileDir -Force | Out-Null
}

$python = ""
if (-not [string]::IsNullOrWhiteSpace($configuredPython)) {
    if (Test-Path -LiteralPath $configuredPython) {
        $python = (Resolve-Path -LiteralPath $configuredPython).Path
    }
    else {
        $configuredCommand = Get-Command $configuredPython -ErrorAction SilentlyContinue
        if ($configuredCommand) {
            $python = $configuredCommand.Source
        }
        else {
            Fail ("RRUGC_PYTHON was configured but could not be found: " + $configuredPython)
        }
    }
}
else {
    $venvPython = Join-Path $RepoRoot ".venv-rrugc\Scripts\python.exe"
    if (Test-Path -LiteralPath $venvPython) {
        $python = $venvPython
    }
}

if ([string]::IsNullOrWhiteSpace($python) -or -not (Test-Path -LiteralPath $python)) {
    Write-Step "Creating local Scout Python environment"
    $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
    $systemPython = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($pyLauncher) {
        & $pyLauncher.Source -3 -m venv (Join-Path $RepoRoot ".venv-rrugc")
    }
    elseif ($systemPython) {
        & $systemPython.Source -m venv (Join-Path $RepoRoot ".venv-rrugc")
    }
    else {
        Fail "Python 3 was not found. Install Python 3 and run START_SCOUT.bat again."
    }
    if ($LASTEXITCODE -ne 0) {
        Fail "Unable to create .venv-rrugc."
    }
    $python = Join-Path $RepoRoot ".venv-rrugc\Scripts\python.exe"
}

if (-not (Test-Path -LiteralPath $python)) {
    Fail ("Python executable was not found: " + $python)
}

if (-not (Test-Path -LiteralPath $RequirementsPath)) {
    Fail ("Scout requirements file is missing: " + $RequirementsPath)
}

$requirementsHash = (Get-FileHash -LiteralPath $RequirementsPath -Algorithm SHA256).Hash
$installedHash = ""
if (Test-Path -LiteralPath $RequirementsStampPath) {
    $installedHash = (Get-Content -LiteralPath $RequirementsStampPath -Raw).Trim()
}

if ($requirementsHash -ne $installedHash) {
    Write-Step "Updating Scout Python dependencies"
    & $python -m pip install --disable-pip-version-check -r $RequirementsPath
    if ($LASTEXITCODE -ne 0) {
        Fail "Dependency installation failed. Scout was not started."
    }
    Set-Content -LiteralPath $RequirementsStampPath -Value $requirementsHash -Encoding ASCII
}
else {
    Write-Host "Dependencies are up to date." -ForegroundColor Green
}

if (-not (Test-Path -LiteralPath $ScoutPath)) {
    Fail ("Scout entry point is missing: " + $ScoutPath)
}

$head = (& git rev-parse --short=8 HEAD 2>$null)
if ($LASTEXITCODE -ne 0) {
    $head = "unknown"
}

# Shared mutable setup is complete. From this point onward Review Scout and
# Keyword Scout are independent. A mode-specific runner mutex prevents only
# accidental duplicate starts of the same Scout.
Release-ScoutStartupLock
$runMode = if ($KeywordMode) { "Keyword" } else { "Review" }
Acquire-ScoutRunnerLock $runMode

$scoutExit = 1
$env:RRUGC_SCOUT_TOKEN = $token
try {
    if ($KeywordMode) {
        if (-not (Test-Path -LiteralPath $KeywordScoutPath)) {
            Fail ("Keyword Scout entry point is missing: " + $KeywordScoutPath)
        }
        try {
            $Host.UI.RawUI.WindowTitle = "Realistic Review UGC - Keyword Scout"
        }
        catch {
            # Window title is best-effort only.
        }

        Write-Step "Starting Stage 0 Keyword Scout"
        Write-Host ("Source commit       : " + $head) -ForegroundColor Green
        Write-Host ("Agent ID            : " + $agentId) -ForegroundColor Green
        Write-Host ("Creative Asset URL  : " + $baseUrl) -ForegroundColor Green
        Write-Host "Pinterest query      : Saying Trucker hat" -ForegroundColor Green
        Write-Host ("Pinterest profile   : " + $keywordProfileDir) -ForegroundColor Green
        Write-Host "Browser isolation   : dedicated Keyword Chrome profile/process" -ForegroundColor Green
        Write-Host "Concurrent mode     : SAFE with Review Scout" -ForegroundColor Green
        Write-Host "Duplicate Keyword   : blocked by runner lock" -ForegroundColor Green
        Write-Host "Related per Pin     : 60" -ForegroundColor Green
        Write-Host "Quote priority      : clear product + worn lifestyle quote styles" -ForegroundColor Green
        Write-Host "Deep detail         : 150 Pins; style depth 3; market LOW + >=1000/mo depth 4" -ForegroundColor Green
        Write-Host ("Pace                : " + $pace) -ForegroundColor Green
        Write-Host "Mode                : autonomous Pinterest clear-quote priority -> AEBrowse volume" -ForegroundColor Green
        Write-Host "Loop                : continuous until this terminal is closed" -ForegroundColor Green
        Write-Host "Review Scout state  : separate browser + profile + history + log" -ForegroundColor Green
        Write-Host "Stage 1 claim lane  : not used" -ForegroundColor Green
        Write-Host "Token               : loaded from scout.local.env (hidden)" -ForegroundColor Green
        Write-Host ""

        $keywordArguments = @(
            $KeywordScoutPath,
            "--auto-pinterest",
            "--base-url", $baseUrl,
            "--agent-id", $agentId,
            "--profile-dir", $keywordProfileDir,
            "--seed-query", "Saying Trucker hat",
            "--related-per-pin", "60",
            "--deep-dive-related-per-pin", "150",
            "--deep-dive-min-search-volume", "1000",
            "--deep-dive-style-max-depth", "3",
            "--deep-dive-market-max-depth", "4",
            "--pace", $pace
        )
        if (-not [string]::IsNullOrWhiteSpace($chromeExecutable)) {
            $keywordArguments += @("--chrome-executable", $chromeExecutable)
        }

        & $python @keywordArguments
        $scoutExit = $LASTEXITCODE
    }
    else {
        try {
            $Host.UI.RawUI.WindowTitle = "Realistic Review UGC - Review Scout"
        }
        catch {
            # Window title is best-effort only.
        }

        Write-Step "Starting Pinterest Review Scout"
        Write-Host ("Source commit       : " + $head) -ForegroundColor Green
        Write-Host "Scout mode          : persistent no-repeat discovery + source-plan context + login-gated self-heal + bounded Scout restart + learned ref seeds (v35)" -ForegroundColor Green
        Write-Host ("Agent ID            : " + $agentId) -ForegroundColor Green
        Write-Host ("Pinterest profile   : " + $profileDir) -ForegroundColor Green
        Write-Host "Browser isolation   : dedicated Review Chrome profile/process" -ForegroundColor Green
        Write-Host "Concurrent mode     : SAFE with Keyword Scout" -ForegroundColor Green
        Write-Host "Duplicate Review    : blocked by runner lock" -ForegroundColor Green
        Write-Host ("Pace                : " + $pace) -ForegroundColor Green
        Write-Host "Detail mode         : single reusable tab" -ForegroundColor Green
        Write-Host "Token               : loaded from scout.local.env (hidden)" -ForegroundColor Green
        Write-Host ""

        $arguments = @(
            $ScoutPath,
            "--base-url", $baseUrl,
            "--agent-id", $agentId,
            "--profile-dir", $profileDir,
            "--pace", $pace,
            "--detail-concurrency", [string]$detailValue
        )
        if (-not [string]::IsNullOrWhiteSpace($machineLabel)) {
            $arguments += @("--machine-label", $machineLabel)
        }
        if (-not [string]::IsNullOrWhiteSpace($chromeExecutable)) {
            $arguments += @("--chrome-executable", $chromeExecutable)
        }

        & $python @arguments
        $scoutExit = $LASTEXITCODE
    }
}
finally {
    Remove-Item Env:RRUGC_SCOUT_TOKEN -ErrorAction SilentlyContinue
    Release-ScoutRunnerLock
}

exit $scoutExit
