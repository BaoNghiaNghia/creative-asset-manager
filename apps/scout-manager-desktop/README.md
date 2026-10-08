# RRUGC Scout Manager — Tauri v2 / React

This is the Windows native successor to PowerShell WinForms Scout Manager. **Do not remove** START_SCOUT_MANAGER.cmd or scripts/start_scout_manager.ps1 during migration.

## Install and run

Requires Node.js >=20, Rust stable (MSVC), Visual Studio C++ Build Tools, and Microsoft Edge WebView2.

From this folder:

    npm ci
    npm run tauri:dev
    npm run tauri:build

Tauri NSIS installer: src-tauri/target/release/bundle/nsis. Windows build was verified on BaoNghia; 3/3 Rust tests and the React/UI tests passed. Tauri 0.1.1 was smoke-tested with two isolated fake Scout PowerShell processes (both logged startup, the second Manager did not spawn duplicates, and exiting left zero orphan test processes). The unsigned 0.1.2 installer fixes PowerShell 5.1 startup from Windows extended paths and is stored at `D:\\Bot_Tool_Auto_Game\\scan_pinterest\\scout-manager-releases\\RRUGC_Scout_Manager_0.1.2_x64_Setup.exe`; it is excluded from Git. No signed binary auto-update is enabled yet. The installer does not embed scout.local.env or Chrome profiles.

The native app uses the checkout at CAM_SCOUT_REPO_ROOT (or the current ancestor checkout / D:\Bot_Tool_Auto_Game\scan_pinterest). It runs the existing scripts/start_scout_auto_update.ps1 -SkipUpdate with separate Review / Keyword Chrome profiles and log files. Tauri 0.1.2 shares the `Local\\CreativeAssetManager.RrugcScout.Manager` named Windows mutex with WinForms. Only the holder can start, stop, pair, or update Scouts; a second Manager shows a warning and disables its controls. Close the old WinForms Manager when ready to switch. It is preserved for rollback.

## Behavior and boundaries

- Same Scout Python runtime, API, pairing keys and histories; no API/database migration.
- Tauri Rust backend owns fixed, allowlisted commands (start, pause, restart, check update, save pairing and bounded log reads).
- No arbitrary command execution from the React webview. Dashboard/read-log IPC never returns saved Scout tokens; pairing input is submitted once over a privileged command and is not persisted by React.
- Child processes use Windows Job Objects with KILL_ON_JOB_CLOSE. Minimize/close hides the window into system tray **without** stopping Scouts; Quit and stop Scouts from the tray stops both.
- Local source update checks every 60s and performs git fetch + fast-forward merge **only on clean main at the exact Git worktree root**. Managed Scout script changes stop/restart affected modes. Git branch/dirty failures never reset files or delete profiles. Updates to the desktop binary require a new installer; signed binary auto-update is not yet enabled.
- Bounded restart attempts and exponential delay. After four failures Scout pauses, requiring operator intervention. Desktop does not bundle Playwright/Chrome/Python environments.
- Pairing writes to the existing ignored scout.local.env on the user's machine; token is never exposed by IPC status or logs.
- Rollback: quit the Tauri app using its tray menu; launch START_SCOUT_MANAGER.cmd. On a test checkout use `CAM_SCOUT_REPO_ROOT`, `CAM_SCOUT_LOG_ROOT`, and `CAM_SCOUT_DISABLE_UPDATES=1` to isolate mock processes and prevent Git fetch; never put test credentials in the live checkout. This preserves credentials, profiles and log history.

## Verification

    npm test
    npm run build
    cargo test --manifest-path src-tauri/Cargo.toml
    npm run tauri:build

For a checked Windows build and an installer copied into the ignored `scout-manager-releases` folder, run `powershell.exe -NoProfile -File scripts/build_scout_manager_tauri.ps1` from the repository root.

UI can also be previewed with npm run dev, but the browser preview intentionally has disabled process controls. Test the packaged application on Windows before replacing the old launcher by default.
