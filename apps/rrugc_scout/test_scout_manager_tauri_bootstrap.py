from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_tauri_launcher_remains_opt_in_with_fallback():
    script = (ROOT / "START_SCOUT_TAURI.cmd").read_text(encoding="utf-8")
    assert "scout-manager-releases\\RRUGC_Scout_Manager_Latest_x64.exe" in script
    assert "START_SCOUT_MANAGER.cmd" in script
    assert 'if not defined CAM_SCOUT_REPO_ROOT set "CAM_SCOUT_REPO_ROOT=%~dp0"' in script
    assert 'start "" "%SCOUT_TAURI%"' in script
    assert "powershell.exe" not in script.split('start ""')[1]


def test_build_script_publishes_portable_and_installer_together():
    text = (ROOT / "scripts/build_scout_manager_tauri.ps1").read_text(encoding="utf-8")
    assert "RRUGC_Scout_Manager_Latest_x64.exe" in text
    assert 'Copy-Item -LiteralPath $builtExe -Destination $portableVersioned -Force' in text
    assert 'Copy-Item -LiteralPath $portableVersioned -Destination $portableLatest -Force' in text
    assert "Get-FileHash" in text
    assert "START_SCOUT_TAURI.cmd" in text
