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
    assert "$pidLabel = New-Object System.Windows.Forms.Label" in manager
    assert "Pid = $pidLabel" in manager


def test_manager_version_reads_runtime_client_version():
    manager = (REPO_ROOT / "scripts/start_scout_manager.ps1").read_text(
        encoding="utf-8"
    )
    runtime = (REPO_ROOT / "apps/rrugc_scout/scout.py").read_text(
        encoding="utf-8"
    )
    assert re.search(r'^CLIENT_VERSION\s*=\s*"rrugc-scout-v\d+"', runtime, re.M)
    assert "-Pattern '^CLIENT_VERSION\\s*=\\s*\"" in manager
