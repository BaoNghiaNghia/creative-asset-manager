"""Static contract checks for the Windows CMD / PowerShell Scout Manager bootstrap.

These guards run on Linux CI; a live Windows smoke remains necessary for cmd.exe.
"""
from pathlib import Path
import re


REPO_ROOT = Path(__file__).resolve().parents[2]


def test_scout_manager_repairs_missing_scripts_without_resetting_checkout():
    launcher = (REPO_ROOT / "START_SCOUT_MANAGER.cmd").read_text(encoding="utf-8")
    assert 'if exist "%SCOUT_MANAGER%" if exist "%SCOUT_RUNNER%" goto :START_MANAGER' in launcher
    assert 'call :RESTORE_MISSING "scripts/start_scout_manager.ps1"' in launcher
    assert 'call :RESTORE_MISSING "scripts/start_scout_auto_update.ps1"' in launcher
    assert 'git restore --source=HEAD -- "%~1"' in launcher
    assert "git sparse-checkout add scripts" in launcher
    assert "git diff --quiet" in launcher
    assert "git diff --cached --quiet" in launcher
    assert "git fetch origin +refs/heads/main:refs/remotes/origin/main" in launcher
    assert "git merge --ff-only origin/main" in launcher
    assert "git reset --hard" not in launcher
    assert "git clean -fd" not in launcher
    assert "scout.local.env" in launcher


def test_manager_does_not_assign_powershell_read_only_pid_variable():
    manager = (REPO_ROOT / "scripts/start_scout_manager.ps1").read_text(
        encoding="utf-8"
    )
    # PowerShell variable names are case-insensitive. $pid is the built-in
    # read-only process id and cannot be reused for a WinForms label.
    assert not re.search(r"(?im)^\\s*\\$pid\\s*=", manager)
    assert '$pidLabel = New-ScoutLabel "PID -" 9 $ink' in manager
    assert "Pid = $pidLabel" in manager


def test_scout_manager_dashboard_keeps_automation_controls_and_real_status():
    manager = (REPO_ROOT / "scripts/start_scout_manager.ps1").read_text(
        encoding="utf-8"
    )
    assert 'New-ScoutButton "Run automation"' in manager
    assert 'New-ScoutButton "Pause all"' in manager
    assert 'New-ScoutButton "Update"' in manager
    assert 'New-ScoutButton "Pairing"' in manager
    assert 'New-ScoutButton "Restart"' in manager
    assert 'Add_Click({ Start-AllScouts })' in manager
    assert 'Add_Click({ Stop-AllScouts })' in manager
    assert 'Restart-ScoutMode ([string]$this.Tag)' in manager
    assert 'Stop-ScoutMode $which' in manager
    assert 'Start-ScoutMode $which' in manager
    assert '$summaryValues[$modeName].Text = $mode.Status' in manager
    assert '$cards[$modeName].Pid.Text = "PID " + $mode.Process.Id' in manager
    assert '$summaryValues["Update"].Text' in manager
    assert '[switch]$Preview' in manager
    assert 'if ($Preview)' in manager
    assert '$script:UpdateInProgress' in manager
    assert 'Get-LogTail $modes[$selected]' in manager
    assert '$form.Add_FormClosing({' in manager
    assert 'Stop-AllScouts -KeepDesired' in manager
    # Stop/start are process controls, not UI-only decorative buttons.
    assert '$toggle.Add_Click({' in manager


def test_scout_manager_native_windows_parser_gate_exists():
    parser = (REPO_ROOT / "scripts/verify_scout_manager.ps1").read_text(
        encoding="utf-8"
    )
    assert "[System.Management.Automation.Language.Parser]::ParseFile(" in parser
    assert "$parseErrors.Count -gt 0" in parser
    assert "Scout Manager PowerShell syntax" in parser


def test_manager_version_reads_runtime_client_version():
    manager = (REPO_ROOT / "scripts/start_scout_manager.ps1").read_text(
        encoding="utf-8"
    )
    runtime = (REPO_ROOT / "apps/rrugc_scout/scout.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r'^CLIENT_VERSION\s*=\s*"rrugc-scout-v\d+"', runtime, re.M)
    assert "-Pattern '^CLIENT_VERSION\\s*=\\s*\"" in manager
