from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]




def test_cap_stack_mode_has_isolated_profile_launcher_native_control_and_server_lane():
    runtime = (ROOT / "apps/rrugc_scout/scout.py").read_text(encoding="utf-8")
    launcher = (ROOT / "scripts/start_scout_auto_update.ps1").read_text(encoding="utf-8")
    manager = (ROOT / "apps/scout-manager-desktop/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    standalone = (ROOT / "START_SCOUT_CAP_STACK.cmd").read_text(encoding="utf-8")
    assert 'class CapStackScoutClient(AutoScoutClient):' in runtime
    assert 'parser.add_argument("--cap-stack-mode"' in runtime
    assert '"cap_stack" if getattr(args, "cap_stack_mode", False)' in runtime
    assert "$capStackProfileDir" in launcher
    assert 'pinterest-profile-cap-stack' in launcher
    assert "-CapStackMode" in launcher
    assert 'for m in ["review", "keyword", "cap_stack"]' in manager
    assert 'process.arg("-CapStackMode")' in manager
    assert 'Scout::new("cap_stack")' in manager
    assert '-CapStackMode' in standalone

def test_tauri_launcher_remains_opt_in_with_fallback():
    script = (ROOT / "START_SCOUT_TAURI.cmd").read_text(encoding="utf-8")
    assert "scout-manager-releases\\RRUGC_Scout_Manager_Latest_x64.exe" in script
    assert "START_SCOUT_MANAGER.cmd" in script
    assert "RRUGC_Scout_Manager_*_x64_Portable.exe" in script
    assert "goto :portable_resolved" in script
    assert 'if not defined CAM_SCOUT_REPO_ROOT set "CAM_SCOUT_REPO_ROOT=%~dp0"' in script
    assert 'start "" "%SCOUT_TAURI%"' in script
    assert "powershell.exe" not in script.split('start ""')[1]


def test_native_commands_do_not_spawn_flashing_consoles_or_block_ui_on_exit():
    native = (ROOT / "apps/scout-manager-desktop/src-tauri/src/lib.rs").read_text(
        encoding="utf-8"
    )
    react = (ROOT / "apps/scout-manager-desktop/src/App.tsx").read_text(
        encoding="utf-8"
    )
    assert "hide_console(&mut process);" in native
    assert "process.creation_flags(0x08000000)" in native
    dashboard = native.split("fn dashboard(&self) -> Dashboard {", 1)[1].split(
        "fn check_update", 1
    )[0]
    assert "execute_git(" not in dashboard
    assert "self.cached_commit.lock()" in dashboard
    assert "thread::spawn(move || {" in native.split("fn request_shutdown(", 1)[1]
    assert "timeout_app.exit(0);" in native
    assert 'fn quit_manager(app: tauri::AppHandle)' in native
    assert 'Quit and stop' in react
    assert "refreshInFlight.current" in react


def test_tauri_long_running_native_operations_run_off_webview_thread():
    source = (ROOT / "apps/scout-manager-desktop/src-tauri/src/lib.rs").read_text(
        encoding="utf-8"
    )
    for command in ("control_scout", "control_all", "check_update"):
        assert f"async fn {command}(" in source
    assert source.count("tauri::async_runtime::spawn_blocking(move ||") >= 3
    assert "winjob::focus_existing_manager()" in source
    assert "FindWindowW(" in source
    assert "ShowWindow(window, SW_RESTORE)" in source


def test_tauri_manual_start_and_no_idle_git_or_tray_refocus():
    native = (ROOT / "apps/scout-manager-desktop/src-tauri/src/lib.rs").read_text(
        encoding="utf-8"
    )
    react = (ROOT / "apps/scout-manager-desktop/src/App.tsx").read_text(
        encoding="utf-8"
    )
    assert 'automation_enabled: AtomicBool::new(false)' in native
    assert 'let commit = read_local_commit(&repo);' in native
    assert 'fn read_local_commit(repo: &Path)' in native
    assert "if updating.automation_enabled.load(Ordering::SeqCst)" in native
    assert 'controller.control_all("start")' not in native.split(".setup(|app|", 1)[1]
    assert 'matches!(event, tauri::tray::TrayIconEvent::DoubleClick { .. })' in native
    assert 'WindowEvent::Resized(_)' not in native
    assert "automationEnabled: boolean" in react
    assert "Pairing saved. Press Run automation to start." in react
    assert 'await act("control_all", { command: "start" })' not in react
    assert "automationEnabled={state.automationEnabled}" in react


def test_manager_and_scout_versions_are_distinct_on_dashboard():
    src = (ROOT / "apps/scout-manager-desktop/src/App.tsx").read_text(encoding="utf-8")
    native = (ROOT / "apps/scout-manager-desktop/src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert 'manager_version: env!("CARGO_PKG_VERSION").to_string()' in native
    assert 'Manager v{state.managerVersion}' in src
    assert 'Scout version</span>' in src
    assert 'Run automation' in src
    package = (ROOT / "apps/scout-manager-desktop/package.json").read_text(encoding="utf-8")
    cargo = (ROOT / "apps/scout-manager-desktop/src-tauri/Cargo.toml").read_text(encoding="utf-8")
    tauri = (ROOT / "apps/scout-manager-desktop/src-tauri/tauri.conf.json").read_text(encoding="utf-8")
    assert '"version": "0.1.20"' in package
    assert 'version = "0.1.20"' in cargo
    assert '"version": "0.1.20"' in tauri


def test_build_script_publishes_portable_and_installer_together():
    text = (ROOT / "scripts/build_scout_manager_tauri.ps1").read_text(encoding="utf-8")
    assert "RRUGC_Scout_Manager_Latest_x64.exe" in text
    assert 'Copy-Item -LiteralPath $builtExe -Destination $portableVersioned -Force' in text
    assert 'Copy-Item -LiteralPath $portableVersioned -Destination $portableLatest -Force' in text
    assert "Get-FileHash" in text
    assert "START_SCOUT_TAURI.cmd" in text
    assert "existing Scouts were not interrupted" in text
    assert "try {" in text