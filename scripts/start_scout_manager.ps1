param(
    [switch]$NoAutoStart,
    [switch]$ResumeAfterUpdate,
    [switch]$Preview
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$RunnerPath = Join-Path $RepoRoot "scripts\start_scout_auto_update.ps1"
$ConfigPath = Join-Path $RepoRoot "scout.local.env"
$StateRoot = Join-Path $env:LOCALAPPDATA "CreativeAssetManager\RrugcScoutManager"
$LogRoot = Join-Path $StateRoot "logs"
$UpdateIntervalSeconds = 60
$script:UpdateInProgress = $false
$script:RestartingForUpdate = $false
$script:Closing = $false
$script:LastUpdateCheck = [DateTime]::MinValue

New-Item -ItemType Directory -Path $LogRoot -Force | Out-Null

$nativeType = @"
using System;
using System.Runtime.InteropServices;

public static class RrugcScoutManagerNative {
    public const uint JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000;

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_BASIC_LIMIT_INFORMATION {
        public long PerProcessUserTimeLimit;
        public long PerJobUserTimeLimit;
        public uint LimitFlags;
        public UIntPtr MinimumWorkingSetSize;
        public UIntPtr MaximumWorkingSetSize;
        public uint ActiveProcessLimit;
        public UIntPtr Affinity;
        public uint PriorityClass;
        public uint SchedulingClass;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct IO_COUNTERS {
        public ulong ReadOperationCount;
        public ulong WriteOperationCount;
        public ulong OtherOperationCount;
        public ulong ReadTransferCount;
        public ulong WriteTransferCount;
        public ulong OtherTransferCount;
    }

    [StructLayout(LayoutKind.Sequential)]
    public struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION {
        public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
        public IO_COUNTERS IoInfo;
        public UIntPtr ProcessMemoryLimit;
        public UIntPtr JobMemoryLimit;
        public UIntPtr PeakProcessMemoryUsed;
        public UIntPtr PeakJobMemoryUsed;
    }

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)]
    public static extern IntPtr CreateJobObject(IntPtr lpJobAttributes, string lpName);

    [DllImport("kernel32.dll")]
    public static extern bool SetInformationJobObject(
        IntPtr hJob,
        int JobObjectInfoClass,
        IntPtr lpJobObjectInfo,
        uint cbJobObjectInfoLength);

    [DllImport("kernel32.dll")]
    public static extern bool AssignProcessToJobObject(IntPtr hJob, IntPtr hProcess);

    [DllImport("kernel32.dll")]
    public static extern bool CloseHandle(IntPtr hObject);
}
"@
Add-Type -TypeDefinition $nativeType -ErrorAction SilentlyContinue

function New-KillOnCloseJob {
    $handle = [RrugcScoutManagerNative]::CreateJobObject([IntPtr]::Zero, $null)
    if ($handle -eq [IntPtr]::Zero) {
        throw "Unable to create Scout Manager process job."
    }

    $info = New-Object RrugcScoutManagerNative+JOBOBJECT_EXTENDED_LIMIT_INFORMATION
    $info.BasicLimitInformation.LimitFlags = [RrugcScoutManagerNative]::JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    $length = [Runtime.InteropServices.Marshal]::SizeOf($info)
    $pointer = [Runtime.InteropServices.Marshal]::AllocHGlobal($length)
    try {
        [Runtime.InteropServices.Marshal]::StructureToPtr($info, $pointer, $false)
        if (-not [RrugcScoutManagerNative]::SetInformationJobObject($handle, 9, $pointer, [uint32]$length)) {
            throw "Unable to configure Scout Manager process job."
        }
    }
    finally {
        [Runtime.InteropServices.Marshal]::FreeHGlobal($pointer)
    }
    return $handle
}

$script:ScoutJob = New-KillOnCloseJob

$managerMutex = New-Object System.Threading.Mutex($false, "Local\CreativeAssetManager.RrugcScout.Manager")
$managerMutexHeld = $false
try {
    if (-not $Preview) {
        try {
            $managerMutexHeld = $managerMutex.WaitOne(0)
        }
        catch [System.Threading.AbandonedMutexException] {
            $managerMutexHeld = $true
        }
    }
    if (-not $Preview -and -not $managerMutexHeld) {
        [System.Windows.Forms.MessageBox]::Show(
            "Scout Manager is already running on this machine.",
            "RRUGC Scout Manager",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Information
        ) | Out-Null
        return
    }

    function Read-LocalConfig {
        $values = @{}
        if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
            return $values
        }
        foreach ($rawLine in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
            $line = $rawLine.Trim()
            if (-not $line -or $line.StartsWith("#")) {
                continue
            }
            $separator = $line.IndexOf("=")
            if ($separator -lt 1) {
                continue
            }
            $name = $line.Substring(0, $separator).Trim()
            $value = $line.Substring($separator + 1).Trim().Trim('"').Trim("'")
            $values[$name] = $value
        }
        return $values
    }

    function Set-LocalConfigValue([string]$Name, [string]$Value) {
        $lines = @()
        if (Test-Path -LiteralPath $ConfigPath -PathType Leaf) {
            $lines = @(Get-Content -LiteralPath $ConfigPath -Encoding UTF8)
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
        Set-Content -LiteralPath $ConfigPath -Value $lines -Encoding UTF8
    }

    function Test-PairingConfigured {
        $config = Read-LocalConfig
        return (
            $config.ContainsKey("RRUGC_AGENT_ID") -and
            -not [string]::IsNullOrWhiteSpace([string]$config["RRUGC_AGENT_ID"]) -and
            $config.ContainsKey("RRUGC_SCOUT_TOKEN") -and
            -not [string]::IsNullOrWhiteSpace([string]$config["RRUGC_SCOUT_TOKEN"])
        )
    }

    function Get-CommitShort {
        try {
            $value = (& git -C $RepoRoot rev-parse --short=8 HEAD 2>$null)
            if ($LASTEXITCODE -eq 0) {
                return $value.Trim()
            }
        }
        catch {}
        return "unknown"
    }

    function Get-ScoutVersion {
        $path = Join-Path $RepoRoot "apps\rrugc_scout\scout.py"
        if (-not (Test-Path -LiteralPath $path)) {
            return "unknown"
        }
        $match = Select-String -LiteralPath $path -Pattern '^CLIENT_VERSION\s*=\s*"([^"]+)"' | Select-Object -First 1
        if ($null -ne $match -and $match.Matches.Count -gt 0) {
            return $match.Matches[0].Groups[1].Value
        }
        return "unknown"
    }

    function Quote-Arg([string]$Value) {
        return '"' + $Value.Replace('"', '\"') + '"'
    }

    function Start-HiddenPowerShell([string[]]$Arguments, [string]$StdoutPath, [string]$StderrPath) {
        $argumentLine = ($Arguments | ForEach-Object { Quote-Arg $_ }) -join " "
        $startInfo = New-Object System.Diagnostics.ProcessStartInfo
        $startInfo.FileName = "powershell.exe"
        $startInfo.Arguments = $argumentLine
        $startInfo.WorkingDirectory = $RepoRoot
        $startInfo.UseShellExecute = $false
        $startInfo.CreateNoWindow = $true
        $startInfo.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        $startInfo.RedirectStandardOutput = $true
        $startInfo.RedirectStandardError = $true

        $process = New-Object System.Diagnostics.Process
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "Unable to start PowerShell Scout child."
        }

        if (-not [RrugcScoutManagerNative]::AssignProcessToJobObject($script:ScoutJob, $process.Handle)) {
            try { $process.Kill() } catch {}
            throw "Unable to attach Scout process to Manager job."
        }

        $stdoutWriter = [System.IO.StreamWriter]::new($StdoutPath, $true, [Text.UTF8Encoding]::new($false))
        $stderrWriter = [System.IO.StreamWriter]::new($StderrPath, $true, [Text.UTF8Encoding]::new($false))
        $stdoutWriter.AutoFlush = $true
        $stderrWriter.AutoFlush = $true

        Register-ObjectEvent -InputObject $process -EventName OutputDataReceived -MessageData $stdoutWriter -Action {
            if ($null -ne $EventArgs.Data) {
                $Event.MessageData.WriteLine($EventArgs.Data)
            }
        } | Out-Null
        Register-ObjectEvent -InputObject $process -EventName ErrorDataReceived -MessageData $stderrWriter -Action {
            if ($null -ne $EventArgs.Data) {
                $Event.MessageData.WriteLine($EventArgs.Data)
            }
        } | Out-Null
        Register-ObjectEvent -InputObject $process -EventName Exited -MessageData @($stdoutWriter, $stderrWriter) -Action {
            foreach ($writer in $Event.MessageData) {
                try { $writer.Flush(); $writer.Dispose() } catch {}
            }
        } | Out-Null
        $process.EnableRaisingEvents = $true
        $process.BeginOutputReadLine()
        $process.BeginErrorReadLine()
        return $process
    }

    $modes = @{
        Review = [ordered]@{
            Name = "Review Scout"
            KeywordMode = $false
            Process = $null
            Desired = $true
            RestartCount = 0
            NextRestartAt = [DateTime]::MinValue
            LastExitCode = $null
            StartedAt = $null
            Status = "Stopped"
            Out = Join-Path $LogRoot "review.stdout.log"
            Err = Join-Path $LogRoot "review.stderr.log"
        }
        Keyword = [ordered]@{
            Name = "Keyword Scout"
            KeywordMode = $true
            Process = $null
            Desired = $true
            RestartCount = 0
            NextRestartAt = [DateTime]::MinValue
            LastExitCode = $null
            StartedAt = $null
            Status = "Stopped"
            Out = Join-Path $LogRoot "keyword.stdout.log"
            Err = Join-Path $LogRoot "keyword.stderr.log"
        }
    }

    function Add-ManagerLog([string]$Message) {
        $line = "[" + (Get-Date -Format "yyyy-MM-dd HH:mm:ss") + "] " + $Message
        Add-Content -LiteralPath (Join-Path $LogRoot "manager.log") -Value $line -Encoding UTF8
    }

    function Get-LogTail([System.Collections.IDictionary]$Mode, [int]$Lines = 70) {
        $parts = @()
        if (Test-Path -LiteralPath $Mode.Out) {
            $parts += @(Get-Content -LiteralPath $Mode.Out -Tail $Lines -ErrorAction SilentlyContinue)
        }
        if (Test-Path -LiteralPath $Mode.Err) {
            $errors = @(Get-Content -LiteralPath $Mode.Err -Tail ([Math]::Min(25, $Lines)) -ErrorAction SilentlyContinue)
            if ($errors.Count -gt 0) {
                $parts += ""
                $parts += "[stderr]"
                $parts += $errors
            }
        }
        return ($parts | Select-Object -Last $Lines) -join [Environment]::NewLine
    }

    function Stop-ScoutMode([string]$ModeName, [switch]$KeepDesired) {
        $mode = $modes[$ModeName]
        if (-not $KeepDesired) {
            $mode.Desired = $false
        }
        $process = $mode.Process
        if ($null -eq $process) {
            $mode.Status = "Stopped"
            return
        }
        try {
            if (-not $process.HasExited) {
                Add-ManagerLog ("Stopping " + $mode.Name + " PID " + $process.Id)
                & taskkill.exe /PID $process.Id /T /F *> $null
                try { $process.WaitForExit(5000) | Out-Null } catch {}
            }
        }
        catch {
            try { $process.Kill() } catch {}
        }
        $mode.Process = $null
        $mode.Status = "Stopped"
    }

    function Start-ScoutMode([string]$ModeName) {
        $mode = $modes[$ModeName]
        if ($script:UpdateInProgress -or $script:Closing) {
            return
        }
        if ($null -ne $mode.Process) {
            try {
                if (-not $mode.Process.HasExited) {
                    $mode.Desired = $true
                    return
                }
            }
            catch {}
        }
        if (-not (Test-Path -LiteralPath $RunnerPath -PathType Leaf)) {
            $mode.Status = "Launcher missing"
            $mode.Desired = $false
            return
        }
        if (-not (Test-PairingConfigured)) {
            $mode.Status = "Pairing required"
            $mode.Desired = $false
            return
        }

        $arguments = @(
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-File", $RunnerPath,
            "-SkipUpdate"
        )
        if ($mode.KeywordMode) {
            $arguments += "-KeywordMode"
        }

        try {
            $mode.Process = Start-HiddenPowerShell $arguments $mode.Out $mode.Err
            $mode.Desired = $true
            $mode.Status = "Running"
            $mode.StartedAt = Get-Date
            $mode.NextRestartAt = [DateTime]::MinValue
            Add-ManagerLog ("Started " + $mode.Name + " PID " + $mode.Process.Id)
        }
        catch {
            $mode.Status = "Start failed"
            $mode.LastExitCode = -1
            $mode.RestartCount += 1
            $mode.NextRestartAt = (Get-Date).AddSeconds([Math]::Min(60, 5 * $mode.RestartCount))
            Add-ManagerLog ("Failed to start " + $mode.Name + ": " + $_.Exception.Message)
        }
    }

    function Restart-ScoutMode([string]$ModeName) {
        $modes[$ModeName].Desired = $true
        Stop-ScoutMode $ModeName -KeepDesired
        Start-Sleep -Milliseconds 250
        Start-ScoutMode $ModeName
    }

    function Start-AllScouts {
        $modes.Review.Desired = $true
        $modes.Keyword.Desired = $true
        Start-ScoutMode "Review"
        Start-ScoutMode "Keyword"
    }

    function Stop-AllScouts([switch]$KeepDesired) {
        Stop-ScoutMode "Review" -KeepDesired:$KeepDesired
        Stop-ScoutMode "Keyword" -KeepDesired:$KeepDesired
    }

    function Test-AuthenticationFailure([System.Collections.IDictionary]$Mode) {
        $tail = Get-LogTail $Mode 30
        return (
            $tail -match "credentials were rejected" -or
            $tail -match "RRUGC_AGENT_ID is missing" -or
            $tail -match "RRUGC_SCOUT_TOKEN is missing" -or
            $tail -match "401 Unauthorized"
        )
    }

    function Show-PairingDialog {
        $config = Read-LocalConfig
        $currentAgent = ""
        $hasCurrentToken = $false
        if ($config.ContainsKey("RRUGC_AGENT_ID")) {
            $currentAgent = [string]$config["RRUGC_AGENT_ID"]
        }
        if (
            $config.ContainsKey("RRUGC_SCOUT_TOKEN") -and
            -not [string]::IsNullOrWhiteSpace([string]$config["RRUGC_SCOUT_TOKEN"])
        ) {
            $hasCurrentToken = $true
        }

        $dialog = New-Object System.Windows.Forms.Form
        $dialog.Text = "Scout pairing"
        $dialog.StartPosition = "CenterParent"
        $dialog.FormBorderStyle = "FixedDialog"
        $dialog.MaximizeBox = $false
        $dialog.MinimizeBox = $false
        $dialog.ClientSize = New-Object System.Drawing.Size(520, 245)
        $dialog.Font = New-Object System.Drawing.Font("Segoe UI", 9)

        $help = New-Object System.Windows.Forms.Label
        $help.Text = "Paste the Agent ID and one-time token from Creative Asset Manager > Realistic Review UGC > Settings. The token is stored only in scout.local.env."
        $help.Location = New-Object System.Drawing.Point(18, 16)
        $help.Size = New-Object System.Drawing.Size(480, 46)
        $dialog.Controls.Add($help)

        $agentLabel = New-Object System.Windows.Forms.Label
        $agentLabel.Text = "Agent ID"
        $agentLabel.AutoSize = $true
        $agentLabel.Location = New-Object System.Drawing.Point(18, 75)
        $dialog.Controls.Add($agentLabel)

        $agentBox = New-Object System.Windows.Forms.TextBox
        $agentBox.Text = $currentAgent
        $agentBox.Location = New-Object System.Drawing.Point(18, 96)
        $agentBox.Size = New-Object System.Drawing.Size(480, 25)
        $dialog.Controls.Add($agentBox)

        $tokenLabel = New-Object System.Windows.Forms.Label
        $tokenLabel.Text = if ($hasCurrentToken) { "New token (leave blank to keep current token)" } else { "Token" }
        $tokenLabel.AutoSize = $true
        $tokenLabel.Location = New-Object System.Drawing.Point(18, 132)
        $dialog.Controls.Add($tokenLabel)

        $tokenBox = New-Object System.Windows.Forms.TextBox
        $tokenBox.UseSystemPasswordChar = $true
        $tokenBox.Location = New-Object System.Drawing.Point(18, 153)
        $tokenBox.Size = New-Object System.Drawing.Size(480, 25)
        $dialog.Controls.Add($tokenBox)

        $saveButton = New-Object System.Windows.Forms.Button
        $saveButton.Text = "Save & start"
        $saveButton.DialogResult = [System.Windows.Forms.DialogResult]::OK
        $saveButton.Location = New-Object System.Drawing.Point(392, 198)
        $saveButton.Size = New-Object System.Drawing.Size(106, 30)
        $dialog.AcceptButton = $saveButton
        $dialog.Controls.Add($saveButton)

        $cancelButton = New-Object System.Windows.Forms.Button
        $cancelButton.Text = "Cancel"
        $cancelButton.DialogResult = [System.Windows.Forms.DialogResult]::Cancel
        $cancelButton.Location = New-Object System.Drawing.Point(302, 198)
        $cancelButton.Size = New-Object System.Drawing.Size(82, 30)
        $dialog.CancelButton = $cancelButton
        $dialog.Controls.Add($cancelButton)

        if ($dialog.ShowDialog($form) -ne [System.Windows.Forms.DialogResult]::OK) {
            return
        }

        $newAgent = $agentBox.Text.Trim()
        $newToken = $tokenBox.Text.Trim()
        if ([string]::IsNullOrWhiteSpace($newAgent)) {
            [System.Windows.Forms.MessageBox]::Show(
                "Agent ID is required.",
                "Scout pairing",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Warning
            ) | Out-Null
            return
        }
        if (
            [string]::IsNullOrWhiteSpace($newToken) -and
            (-not $hasCurrentToken -or $newAgent -ne $currentAgent)
        ) {
            [System.Windows.Forms.MessageBox]::Show(
                "A new token is required when pairing a new Agent ID.",
                "Scout pairing",
                [System.Windows.Forms.MessageBoxButtons]::OK,
                [System.Windows.Forms.MessageBoxIcon]::Warning
            ) | Out-Null
            return
        }

        Set-LocalConfigValue "RRUGC_AGENT_ID" $newAgent
        if (-not [string]::IsNullOrWhiteSpace($newToken)) {
            Set-LocalConfigValue "RRUGC_SCOUT_TOKEN" $newToken
        }
        Add-ManagerLog "Scout pairing configuration updated locally."
        foreach ($modeName in @("Review", "Keyword")) {
            $modes[$modeName].RestartCount = 0
            $modes[$modeName].Desired = $true
        }
        Restart-ScoutMode "Review"
        Restart-ScoutMode "Keyword"
    }

    function Update-ProcessState([string]$ModeName) {
        $mode = $modes[$ModeName]
        $process = $mode.Process
        if ($null -ne $process) {
            $exited = $false
            try { $exited = $process.HasExited } catch { $exited = $true }
            if (-not $exited) {
                $mode.Status = "Running"
                return
            }

            try { $mode.LastExitCode = $process.ExitCode } catch { $mode.LastExitCode = -1 }
            if (
                $null -ne $mode.StartedAt -and
                ((Get-Date) - [DateTime]$mode.StartedAt).TotalSeconds -ge 300
            ) {
                $mode.RestartCount = 0
            }
            $mode.StartedAt = $null
            $mode.Process = $null
            Add-ManagerLog ($mode.Name + " exited with code " + $mode.LastExitCode)

            if (Test-AuthenticationFailure $mode) {
                $mode.Status = "Pairing required"
                $mode.Desired = $false
                return
            }
            if ($mode.LastExitCode -eq 70) {
                $mode.Status = "Fatal - attention required"
                $mode.Desired = $false
                return
            }

            if ($mode.Desired -and -not $script:UpdateInProgress) {
                $mode.RestartCount += 1
                if ($mode.RestartCount -gt 4) {
                    $mode.Status = "Paused after repeated exits"
                    $mode.Desired = $false
                    return
                }
                $delay = [Math]::Min(60, 5 * [Math]::Pow(2, $mode.RestartCount - 1))
                $mode.NextRestartAt = (Get-Date).AddSeconds($delay)
                $mode.Status = "Restarting in " + [int]$delay + "s"
            }
            else {
                $mode.Status = "Stopped"
            }
        }

        if (
            $mode.Desired -and
            $null -eq $mode.Process -and
            (Get-Date) -ge $mode.NextRestartAt -and
            -not $script:UpdateInProgress
        ) {
            Start-ScoutMode $ModeName
        }
    }

    function Invoke-Git([string[]]$Arguments) {
        $output = & git -C $RepoRoot @Arguments 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw ("git " + ($Arguments -join " ") + " failed: " + ($output -join [Environment]::NewLine))
        }
        return @($output)
    }

    function Start-UpdatedManager {
        $arguments = @(
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy", "Bypass",
            "-WindowStyle", "Hidden",
            "-File", $PSCommandPath,
            "-ResumeAfterUpdate"
        )
        $argumentLine = ($arguments | ForEach-Object { Quote-Arg $_ }) -join " "
        $info = New-Object System.Diagnostics.ProcessStartInfo
        $info.FileName = "powershell.exe"
        $info.Arguments = $argumentLine
        $info.WorkingDirectory = $RepoRoot
        $info.UseShellExecute = $true
        $info.WindowStyle = [System.Diagnostics.ProcessWindowStyle]::Hidden
        [System.Diagnostics.Process]::Start($info) | Out-Null
    }

    function Check-ForUpdates([switch]$Manual) {
        if ($script:UpdateInProgress -or $script:Closing) {
            return
        }
        $script:LastUpdateCheck = Get-Date
        try {
            if (-not (Get-Command git.exe -ErrorAction SilentlyContinue)) {
                $updateLabel.Text = "Auto-update: Git not found"
                return
            }

            $dirty = @(Invoke-Git @("status", "--porcelain", "--untracked-files=no"))
            if ($dirty.Count -gt 0) {
                $updateLabel.Text = "Auto-update paused: tracked local changes"
                if ($Manual) {
                    [System.Windows.Forms.MessageBox]::Show(
                        "Tracked local changes are present. Scout Manager will not overwrite them. Commit or revert the changes, then check again.",
                        "Scout update paused",
                        [System.Windows.Forms.MessageBoxButtons]::OK,
                        [System.Windows.Forms.MessageBoxIcon]::Warning
                    ) | Out-Null
                }
                return
            }

            $updateLabel.Text = "Auto-update: checking origin/main..."
            [System.Windows.Forms.Application]::DoEvents()
            Invoke-Git @("fetch", "--quiet", "origin", "+refs/heads/main:refs/remotes/origin/main") | Out-Null
            $localCommit = ((Invoke-Git @("rev-parse", "HEAD"))[0]).Trim()
            $remoteCommit = ((Invoke-Git @("rev-parse", "origin/main"))[0]).Trim()
            if ($localCommit -eq $remoteCommit) {
                $updateLabel.Text = "Auto-update: current (" + $localCommit.Substring(0, 8) + ")"
                if ($Manual) {
                    Add-ManagerLog "Manual update check: already current."
                }
                return
            }

            $changedPaths = @(Invoke-Git @("diff", "--name-only", "HEAD..origin/main"))
            $restartRequired = $false
            foreach ($changedPath in $changedPaths) {
                if (
                    $changedPath -match '^apps/rrugc_scout/' -or
                    $changedPath -match '^scripts/start_scout' -or
                    $changedPath -match '^START_SCOUT' -or
                    $changedPath -eq 'scout.local.env.example'
                ) {
                    $restartRequired = $true
                    break
                }
            }

            $script:UpdateInProgress = $true
            Add-ManagerLog ("Updating source " + $localCommit.Substring(0, 8) + " -> " + $remoteCommit.Substring(0, 8))

            if ($restartRequired) {
                $updateLabel.Text = "Scout update found. Stopping both Scouts..."
                Stop-AllScouts -KeepDesired
                [System.Windows.Forms.Application]::DoEvents()
            }
            else {
                $updateLabel.Text = "Repository update found. Scouts can keep running..."
            }

            $updateLabel.Text = "Applying update..."
            Invoke-Git @("merge", "--ff-only", "origin/main") | Out-Null

            if (-not $restartRequired) {
                $script:UpdateInProgress = $false
                $versionLabel.Text = "Scout " + (Get-ScoutVersion) + "  |  commit " + (Get-CommitShort)
                $updateLabel.Text = "Auto-update: current (" + (Get-CommitShort) + ")"
                Add-ManagerLog ("Updated unrelated repository files to " + (Get-CommitShort) + " without interrupting Scouts.")
                return
            }

            Add-ManagerLog ("Scout-managed files changed; updated to " + (Get-CommitShort) + " and restarting Manager + both Scouts.")
            $updateLabel.Text = "Updated. Restarting Scout Manager..."
            $script:RestartingForUpdate = $true
            try { $managerMutex.ReleaseMutex() } catch {}
            Start-UpdatedManager
            $form.Close()
        }
        catch {
            $script:UpdateInProgress = $false
            $updateLabel.Text = "Auto-update error - will retry"
            Add-ManagerLog ("Update error: " + $_.Exception.Message)
            if ($Manual) {
                [System.Windows.Forms.MessageBox]::Show(
                    $_.Exception.Message,
                    "Scout update failed",
                    [System.Windows.Forms.MessageBoxButtons]::OK,
                    [System.Windows.Forms.MessageBoxIcon]::Error
                ) | Out-Null
            }
        }
    }

    # Native WinForms dashboard: existing recovery and process controls remain
    # authoritative. Only the presentation and direct automation controls change.
    $ink = [System.Drawing.Color]::FromArgb(17, 24, 39)
    $muted = [System.Drawing.Color]::FromArgb(100, 116, 139)
    $primary = [System.Drawing.Color]::FromArgb(37, 99, 235)
    $green = [System.Drawing.Color]::FromArgb(22, 101, 52)
    $red = [System.Drawing.Color]::FromArgb(185, 28, 28)
    $surface = [System.Drawing.Color]::FromArgb(255, 255, 255)
    $border = [System.Drawing.Color]::FromArgb(220, 228, 238)

    function New-ScoutLabel([string]$Caption, [int]$Size, [System.Drawing.Color]$Color) {
        $control = New-Object System.Windows.Forms.Label
        $control.Text = $Caption
        $control.AutoSize = $true
        $control.Font = New-Object System.Drawing.Font("Segoe UI", $Size)
        $control.ForeColor = $Color
        return $control
    }

    function New-ScoutButton([string]$Caption, [int]$Width, [bool]$IsPrimary = $false) {
        $control = New-Object System.Windows.Forms.Button
        $control.Text = $Caption
        $control.Size = New-Object System.Drawing.Size($Width, 36)
        $control.FlatStyle = [System.Windows.Forms.FlatStyle]::Flat
        $control.FlatAppearance.BorderSize = 1
        $control.FlatAppearance.BorderColor = $border
        $control.BackColor = $surface
        $control.ForeColor = $ink
        $control.Cursor = [System.Windows.Forms.Cursors]::Hand
        $control.Margin = New-Object System.Windows.Forms.Padding(5, 0, 0, 0)
        if ($IsPrimary) {
            $control.BackColor = $primary
            $control.ForeColor = $surface
            $control.FlatAppearance.BorderColor = $primary
        }
        return $control
    }

    $form = New-Object System.Windows.Forms.Form
    $form.Text = "RRUGC Scout Manager"
    $form.StartPosition = "CenterScreen"
    $form.Size = New-Object System.Drawing.Size(1100, 750)
    $form.MinimumSize = New-Object System.Drawing.Size(940, 660)
    $form.AutoScaleMode = [System.Windows.Forms.AutoScaleMode]::Dpi
    $form.Font = New-Object System.Drawing.Font("Segoe UI", 9)
    $form.BackColor = [System.Drawing.Color]::FromArgb(247, 249, 252)

    $layout = New-Object System.Windows.Forms.TableLayoutPanel
    $layout.Dock = [System.Windows.Forms.DockStyle]::Fill
    $layout.ColumnCount = 1
    $layout.RowCount = 4
    $layout.Padding = New-Object System.Windows.Forms.Padding(18, 12, 18, 16)
    [void]$layout.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 116)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 85)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 234)))
    [void]$layout.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $form.Controls.Add($layout)

    # Header: global actions replace three controls per Scout.
    $header = New-Object System.Windows.Forms.Panel
    $header.Dock = [System.Windows.Forms.DockStyle]::Fill
    $header.Margin = New-Object System.Windows.Forms.Padding(0)
    $layout.Controls.Add($header, 0, 0)

    $title = New-ScoutLabel "RRUGC Scout Manager" 20 $ink
    $title.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 20)
    $title.Location = New-Object System.Drawing.Point(4, 5)
    $header.Controls.Add($title)

    $subtitle = New-ScoutLabel "Review + Keyword run automatically with independent profiles and crash recovery." 9 $muted
    $subtitle.Location = New-Object System.Drawing.Point(6, 45)
    $header.Controls.Add($subtitle)

    $versionLabel = New-ScoutLabel ("Version " + (Get-ScoutVersion) + "   |   commit " + (Get-CommitShort)) 8 $muted
    $versionLabel.Location = New-Object System.Drawing.Point(6, 69)
    $header.Controls.Add($versionLabel)

    $updateLabel = New-ScoutLabel ("Auto-update checks every " + $UpdateIntervalSeconds + "s") 8 $muted
    $updateLabel.Location = New-Object System.Drawing.Point(6, 91)
    $header.Controls.Add($updateLabel)

    $actions = New-Object System.Windows.Forms.FlowLayoutPanel
    $actions.AutoSize = $false
    $actions.WrapContents = $false
    $actions.FlowDirection = [System.Windows.Forms.FlowDirection]::RightToLeft
    $actions.Dock = [System.Windows.Forms.DockStyle]::Right
    $actions.Width = 486
    $actions.Height = 42
    $actions.Padding = New-Object System.Windows.Forms.Padding(0, 7, 0, 0)
    $header.Controls.Add($actions)

    $pairingButton = New-ScoutButton "Pairing" 78
    $pairingButton.Add_Click({ Show-PairingDialog })
    $actions.Controls.Add($pairingButton)

    $checkButton = New-ScoutButton "Update" 78
    $checkButton.Add_Click({ Check-ForUpdates -Manual })
    $actions.Controls.Add($checkButton)

    $stopAllButton = New-ScoutButton "Pause all" 92
    $stopAllButton.Add_Click({ Stop-AllScouts })
    $actions.Controls.Add($stopAllButton)

    $startAllButton = New-ScoutButton "Run automation" 133 $true
    $startAllButton.Add_Click({ Start-AllScouts })
    $actions.Controls.Add($startAllButton)

    # Honest system summary: state comes from each child process, never merely
    # from a colored badge or a successful process creation attempt.
    $overview = New-Object System.Windows.Forms.Panel
    $overview.Dock = [System.Windows.Forms.DockStyle]::Fill
    $overview.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 12)
    $overview.BackColor = [System.Drawing.Color]::FromArgb(239, 249, 246)
    $overview.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $layout.Controls.Add($overview, 0, 1)

    $summaryGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $summaryGrid.Dock = [System.Windows.Forms.DockStyle]::Fill
    $summaryGrid.ColumnCount = 4
    $summaryGrid.RowCount = 1
    $summaryGrid.Padding = New-Object System.Windows.Forms.Padding(11, 7, 11, 5)
    for ($index = 0; $index -lt 4; $index++) {
        [void]$summaryGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 25)))
    }
    $overview.Controls.Add($summaryGrid)

    $summaryValues = @{}
    foreach ($item in @(
        @{ Key = "Review"; Caption = "REVIEW SCOUT"; Value = "Starting..." },
        @{ Key = "Keyword"; Caption = "KEYWORD SCOUT"; Value = "Starting..." },
        @{ Key = "Update"; Caption = "AUTO UPDATE"; Value = "Every 60 seconds" },
        @{ Key = "Recovery"; Caption = "SYSTEM PROTECTION"; Value = "Restart & watchdog" }
    )) {
        $cell = New-Object System.Windows.Forms.Panel
        $cell.Dock = [System.Windows.Forms.DockStyle]::Fill
        $cell.Margin = New-Object System.Windows.Forms.Padding(3, 0, 3, 0)
        $cellTitle = New-ScoutLabel $item.Caption 8 $muted
        $cellTitle.Location = New-Object System.Drawing.Point(9, 3)
        $cell.Controls.Add($cellTitle)
        $cellValue = New-ScoutLabel $item.Value 10 $ink
        $cellValue.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
        $cellValue.Location = New-Object System.Drawing.Point(9, 25)
        $cell.Controls.Add($cellValue)
        $summaryValues[$item.Key] = $cellValue
        $summaryGrid.Controls.Add($cell, $summaryValues.Count - 1, 0)
    }

    # Cards: one direct recovery button and a compact secondary toggle per
    # Scout. The global controls remain the default automation workflow.
    $cardGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $cardGrid.Dock = [System.Windows.Forms.DockStyle]::Fill
    $cardGrid.Margin = New-Object System.Windows.Forms.Padding(0, 0, 0, 12)
    $cardGrid.ColumnCount = 2
    $cardGrid.RowCount = 1
    [void]$cardGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
    [void]$cardGrid.ColumnStyles.Add((New-Object System.Windows.Forms.ColumnStyle([System.Windows.Forms.SizeType]::Percent, 50)))
    $layout.Controls.Add($cardGrid, 0, 2)

    $cards = @{}
    foreach ($modeName in @("Review", "Keyword")) {
        $isReview = $modeName -eq "Review"
        $card = New-Object System.Windows.Forms.Panel
        $card.Dock = [System.Windows.Forms.DockStyle]::Fill
        $card.Margin = if ($isReview) { New-Object System.Windows.Forms.Padding(0, 0, 7, 0) } else { New-Object System.Windows.Forms.Padding(7, 0, 0, 0) }
        $card.BackColor = $surface
        $card.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
        $cardGrid.Controls.Add($card, $(if ($isReview) { 0 } else { 1 }), 0)

        $heading = New-ScoutLabel ($modeName + " Scout") 16 $ink
        $heading.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 16)
        $heading.Location = New-Object System.Drawing.Point(17, 16)
        $card.Controls.Add($heading)

        $description = if ($isReview) {
            "Scans photo references and review contexts"
        } else {
            "Discovers hat quotes and market keywords"
        }
        $sub = New-ScoutLabel $description 9 $muted
        $sub.Location = New-Object System.Drawing.Point(19, 53)
        $card.Controls.Add($sub)

        $state = New-Object System.Windows.Forms.Label
        $state.Text = "Starting..."
        $state.Size = New-Object System.Drawing.Size(134, 30)
        $state.TextAlign = [System.Drawing.ContentAlignment]::MiddleCenter
        $state.BackColor = [System.Drawing.Color]::FromArgb(241, 245, 249)
        $state.ForeColor = $muted
        $state.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 9)
        $state.Location = New-Object System.Drawing.Point(326, 18)
        $state.Anchor = "Top,Right"
        $card.Controls.Add($state)
        $card.Tag = $state
        $card.Add_Resize({
            if ($null -ne $this.Tag) {
                $this.Tag.Left = [Math]::Max(210, $this.ClientSize.Width - $this.Tag.Width - 16)
            }
        })

        $cardDivider = New-Object System.Windows.Forms.Panel
        $cardDivider.Height = 1
        $cardDivider.BackColor = $border
        $cardDivider.Dock = [System.Windows.Forms.DockStyle]::Top
        $cardDivider.Location = New-Object System.Drawing.Point(0, 82)
        # Use a fixed anchored line so it does not overlay the title.
        $cardDivider.Dock = [System.Windows.Forms.DockStyle]::None
        $cardDivider.Size = New-Object System.Drawing.Size(445, 1)
        $cardDivider.Location = New-Object System.Drawing.Point(18, 84)
        $cardDivider.Anchor = "Top,Left,Right"
        $card.Controls.Add($cardDivider)

        $pidLabel = New-ScoutLabel "PID -" 9 $ink
        $pidLabel.Location = New-Object System.Drawing.Point(19, 101)
        $card.Controls.Add($pidLabel)

        $attemptLabel = New-ScoutLabel "Automatic recovery enabled" 9 $muted
        $attemptLabel.Location = New-Object System.Drawing.Point(155, 101)
        $card.Controls.Add($attemptLabel)

        $feature = New-ScoutLabel "Auto restart    ·    Browser watchdog    ·    Separate Chrome profile" 8 $green
        $feature.Location = New-Object System.Drawing.Point(19, 130)
        $card.Controls.Add($feature)

        $restart = New-ScoutButton "Restart" 96
        $restart.Location = New-Object System.Drawing.Point(17, 166)
        $restart.Tag = $modeName
        $restart.Add_Click({ Restart-ScoutMode ([string]$this.Tag) })
        $card.Controls.Add($restart)

        $toggle = New-ScoutButton "Pause" 94
        $toggle.Location = New-Object System.Drawing.Point(120, 166)
        $toggle.Tag = $modeName
        $toggle.Add_Click({
            $which = [string]$this.Tag
            if ($modes[$which].Desired) {
                Stop-ScoutMode $which
            }
            else {
                $modes[$which].RestartCount = 0
                Start-ScoutMode $which
            }
        })
        $card.Controls.Add($toggle)

        $cards[$modeName] = @{
            Status = $state
            Pid = $pidLabel
            Recovery = $attemptLabel
            Toggle = $toggle
            Restart = $restart
        }
    }

    # Monitoring console: a dedicated selector and generous scannable output.
    $console = New-Object System.Windows.Forms.Panel
    $console.Dock = [System.Windows.Forms.DockStyle]::Fill
    $console.Margin = New-Object System.Windows.Forms.Padding(0)
    $console.BackColor = [System.Drawing.Color]::FromArgb(15, 23, 42)
    $console.BorderStyle = [System.Windows.Forms.BorderStyle]::FixedSingle
    $layout.Controls.Add($console, 0, 3)

    $consoleGrid = New-Object System.Windows.Forms.TableLayoutPanel
    $consoleGrid.Dock = [System.Windows.Forms.DockStyle]::Fill
    $consoleGrid.ColumnCount = 1
    $consoleGrid.RowCount = 2
    [void]$consoleGrid.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Absolute, 53)))
    [void]$consoleGrid.RowStyles.Add((New-Object System.Windows.Forms.RowStyle([System.Windows.Forms.SizeType]::Percent, 100)))
    $console.Controls.Add($consoleGrid)

    $consoleHeader = New-Object System.Windows.Forms.Panel
    $consoleHeader.Dock = [System.Windows.Forms.DockStyle]::Fill
    $consoleGrid.Controls.Add($consoleHeader, 0, 0)
    $consoleTitle = New-ScoutLabel "LIVE ACTIVITY" 10 $surface
    $consoleTitle.Font = New-Object System.Drawing.Font("Segoe UI Semibold", 10)
    $consoleTitle.Location = New-Object System.Drawing.Point(16, 9)
    $consoleHeader.Controls.Add($consoleTitle)
    $consoleHint = New-ScoutLabel "Latest output from the selected Scout" 8 ([System.Drawing.Color]::FromArgb(148, 163, 184))
    $consoleHint.Location = New-Object System.Drawing.Point(17, 30)
    $consoleHeader.Controls.Add($consoleHint)

    $logSelector = New-Object System.Windows.Forms.ComboBox
    $logSelector.DropDownStyle = "DropDownList"
    [void]$logSelector.Items.Add("Review Scout")
    [void]$logSelector.Items.Add("Keyword Scout")
    $logSelector.SelectedIndex = 0
    $logSelector.Size = New-Object System.Drawing.Size(180, 27)
    $logSelector.Location = New-Object System.Drawing.Point(810, 14)
    $logSelector.Anchor = "Top,Right"
    $consoleHeader.Controls.Add($logSelector)
    $consoleHeader.Tag = $logSelector
    $consoleHeader.Add_Resize({
        if ($null -ne $this.Tag) {
            $this.Tag.Left = [Math]::Max(270, $this.ClientSize.Width - $this.Tag.Width - 14)
        }
    })

    $logs = New-Object System.Windows.Forms.TextBox
    $logs.Multiline = $true
    $logs.ReadOnly = $true
    $logs.ScrollBars = "Vertical"
    $logs.WordWrap = $false
    $logs.BorderStyle = [System.Windows.Forms.BorderStyle]::None
    $logs.Font = New-Object System.Drawing.Font("Consolas", 9)
    $logs.Dock = [System.Windows.Forms.DockStyle]::Fill
    $logs.Margin = New-Object System.Windows.Forms.Padding(16, 0, 12, 12)
    $logs.BackColor = [System.Drawing.Color]::FromArgb(15, 23, 42)
    $logs.ForeColor = [System.Drawing.Color]::FromArgb(226, 232, 240)
    $consoleGrid.Controls.Add($logs, 0, 1)

    $timer = New-Object System.Windows.Forms.Timer
    $timer.Interval = 1000
    $timer.Add_Tick({
        foreach ($modeName in @("Review", "Keyword")) {
            Update-ProcessState $modeName
            $mode = $modes[$modeName]
            $cards[$modeName].Status.Text = $mode.Status
            $cards[$modeName].Toggle.Text = if ($mode.Desired) { "Pause" } else { "Start" }
            $cards[$modeName].Toggle.Enabled = -not $script:UpdateInProgress
            $cards[$modeName].Restart.Enabled = -not $script:UpdateInProgress
            $cards[$modeName].Recovery.Text = if ($mode.RestartCount -gt 0) {
                "Recovery attempts: " + $mode.RestartCount
            } else {
                "Auto restart enabled"
            }
            if ($mode.Status -eq "Running") {
                $cards[$modeName].Status.ForeColor = $green
                $cards[$modeName].Status.BackColor = [System.Drawing.Color]::FromArgb(228, 248, 235)
            }
            elseif ($mode.Status -match "required|Fatal|failed|Paused") {
                $cards[$modeName].Status.ForeColor = $red
                $cards[$modeName].Status.BackColor = [System.Drawing.Color]::FromArgb(254, 242, 242)
            }
            else {
                $cards[$modeName].Status.ForeColor = $muted
                $cards[$modeName].Status.BackColor = [System.Drawing.Color]::FromArgb(241, 245, 249)
            }
            $summaryValues[$modeName].Text = $mode.Status
            $summaryValues[$modeName].ForeColor = $cards[$modeName].Status.ForeColor
            if ($null -ne $mode.Process) {
                try {
                    $cards[$modeName].Pid.Text = "PID " + $mode.Process.Id
                }
                catch {
                    $cards[$modeName].Pid.Text = "PID -"
                }
            }
            else {
                $cards[$modeName].Pid.Text = "PID -"
            }
        }

        $checkButton.Enabled = -not $script:UpdateInProgress
        $startAllButton.Enabled = -not $script:UpdateInProgress
        $stopAllButton.Enabled = -not $script:UpdateInProgress
        $summaryValues["Update"].Text = if ($updateLabel.Text -match "error|paused|unavailable|not found") {
            "Needs attention"
        } elseif ($updateLabel.Text -match "current") {
            "Up to date"
        } elseif ($script:UpdateInProgress) {
            "Applying update"
        } else {
            "Checking every 60s"
        }
        $summaryValues["Update"].ForeColor = if ($summaryValues["Update"].Text -eq "Needs attention") {
            $red
        } else {
            $green
        }
        $summaryValues["Recovery"].Text = if (
            $modes.Review.Status -match "Fatal|failed" -or
            $modes.Keyword.Status -match "Fatal|failed"
        ) {
            "Attention required"
        } else {
            "Watchdog & recovery"
        }
        $summaryValues["Recovery"].ForeColor = if ($summaryValues["Recovery"].Text -eq "Attention required") {
            $red
        } else {
            $green
        }

        $selected = if ($logSelector.SelectedIndex -eq 1) { "Keyword" } else { "Review" }
        $tail = Get-LogTail $modes[$selected] 90
        if ($logs.Text -ne $tail) {
            $logs.Text = $tail
            $logs.SelectionStart = $logs.TextLength
            $logs.ScrollToCaret()
        }

        if (
            -not $Preview -and
            -not $script:UpdateInProgress -and
            ((Get-Date) - $script:LastUpdateCheck).TotalSeconds -ge $UpdateIntervalSeconds
        ) {
            Check-ForUpdates
        }
    })

    $form.Add_Shown({
        $timer.Start()
        if ($Preview) {
            $updateLabel.Text = "Preview mode - no Git or Scout processes started"
            return
        }
        $script:LastUpdateCheck = Get-Date
        Check-ForUpdates
        if (-not $script:RestartingForUpdate -and -not $NoAutoStart) {
            Start-AllScouts
        }
        if ($ResumeAfterUpdate) {
            Add-ManagerLog "Scout Manager resumed after source update."
        }
    })

    $form.Add_FormClosing({
        if ($Preview) {
            $script:Closing = $true
            $timer.Stop()
            return
        }
        if ($script:RestartingForUpdate) {
            $script:Closing = $true
            $timer.Stop()
            Stop-AllScouts -KeepDesired
            return
        }
        $choice = [System.Windows.Forms.MessageBox]::Show(
            "Close Scout Manager and stop both Review + Keyword Scout processes?",
            "Stop RRUGC Scouts",
            [System.Windows.Forms.MessageBoxButtons]::YesNo,
            [System.Windows.Forms.MessageBoxIcon]::Question
        )
        if ($choice -ne [System.Windows.Forms.DialogResult]::Yes) {
            $_.Cancel = $true
            return
        }
        $script:Closing = $true
        $timer.Stop()
        Stop-AllScouts
    })

    [System.Windows.Forms.Application]::EnableVisualStyles()
    [System.Windows.Forms.Application]::Run($form)
}
catch {
    try {
        [System.Windows.Forms.MessageBox]::Show(
            $_.Exception.Message,
            "RRUGC Scout Manager error",
            [System.Windows.Forms.MessageBoxButtons]::OK,
            [System.Windows.Forms.MessageBoxIcon]::Error
        ) | Out-Null
    }
    catch {}
}
finally {
    try { Stop-AllScouts } catch {}
    if ($script:ScoutJob -ne [IntPtr]::Zero) {
        [RrugcScoutManagerNative]::CloseHandle($script:ScoutJob) | Out-Null
        $script:ScoutJob = [IntPtr]::Zero
    }
    if ($managerMutexHeld) {
        try { $managerMutex.ReleaseMutex() } catch {}
    }
    $managerMutex.Dispose()
}
