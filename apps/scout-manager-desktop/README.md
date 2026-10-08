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

## Native 0.1.3 stability release

The Windows 0.1.3 release uses `CREATE_NO_WINDOW` for both Git commands and the PowerShell Scout launcher. Commit metadata is cached on startup and after successful fast-forward updates rather than spawning Git on every 2-second dashboard refresh. The React polling loop deduplicates concurrent requests. The tray Quit and **Quit and stop** footer action shut down Scout process trees on background threads, with bounded per-child waits and a 10-second fallback exit. Closing the window with X hides it to tray and does not interrupt automation. Test this via an isolated `CAM_SCOUT_REPO_ROOT` mock before switching from WinForms; do not terminate real Scout processes during validation.

## Native 0.1.7 AI backlog visibility

The authenticated Stage 0 summary also reports the number of queued RRUGC image-analysis jobs, age of the oldest pending job, and whether the shared-Gemini circuit breaker is active. This helps distinguish idle Scouts and provider backpressure from a loss of saved keywords; the UI never implies that a Running process guarantees new database rows. No Gemini quotas or safety thresholds are bypassed. Automatic Scout startup remains disabled until **Run automation** is pressed.

## Native 0.1.6 compact UI and live Stage 0 keyword health

Window dimensions and WebView typography/layout are scaled to 90% of 0.1.5. The Keyword Scout card shows authenticated tenant-scoped server totals, new keywords in the last 24 hours and 7 days, and the most recently created keyword's timestamp; refresh occurs every 30 seconds. A data-staleness warning appears when no keywords have been saved in 24 hours. The Rust backend sends the local Scout token directly to an HTTPS-only stats endpoint and never exposes it to React. This release also improves Gemini quote extraction fallback for generic schema-related HTTP 400 responses and keeps a single faulty Pin from forcing an entire Scout cycle into a 180-second pause. The keyword counts are database-confirmed, not inferred from processed Pin counts.

## Native 0.1.5 manual-start and responsive-window release

Launching the Manager now starts in **Idle**, even if `scout.local.env` is already paired. Saving pairing also leaves both Scouts stopped. **Run automation** is the sole global enable action: it launches Review and Keyword, enables bounded recovery and schedules automatic Git checks every 60 seconds. **Pause** stops both and disables automatic source updates. Update remains available as an explicit user action while Idle. Individual Scout Start/Restart buttons are disabled until automation has been enabled globally.

The window no longer invokes Git on startup for its commit label (the hash is read from `.git`), no background Git runs while Idle, tray mouse hover/move never refocuses the window, and resize events no longer trigger a synchronous minimize check. The native title bar can therefore be dragged normally. X hides the app into tray; Quit and stop exits on a background worker.

## Native 0.1.4 responsiveness and single-instance release

Starting with 0.1.4, the native Start / Pause / Restart and Git update commands run on blocking worker threads rather than the Windows WebView message thread. Launching a second Manager focuses the existing `RRUGC Scout Manager` window when the shared Windows mutex is held, avoiding duplicate WebViews, tray icons, and misleading start prompts. The runner mutexes and isolated Chrome profiles are unchanged. The existing 0.1.3 mitigations for hidden Git/PowerShell consoles, bounded shutdown, and background Quit remain in effect. Windows validation uses isolated fake Scouts only; do not interrupt live production Scouts during cutover.

## Opt-in launch and rollback

A new `START_SCOUT_TAURI.cmd` in the Windows Scout checkout launches `scout-manager-releases/RRUGC_Scout_Manager_Latest_x64.exe` (portable binary from the verified build). It explicitly sets `CAM_SCOUT_REPO_ROOT` to the checkout when absent. The packaged installer is optional and no administrator-level change is required for the portable app. The launcher does **not** automatically fall back to WinForms on errors, so the user can see what failed; run `START_SCOUT_MANAGER.cmd` directly to roll back.

Build from a full checkout, then copy the versioned + latest portable binaries and NSIS installer to the ignored `scout-manager-releases` directory for the target Windows machine. Do not run both WinForms and Tauri controllers simultaneously; they share the exclusive Windows Manager mutex.

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
