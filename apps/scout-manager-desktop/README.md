# RRUGC Scout Manager

## Branding DNA local config compatibility — Scout Manager v0.1.32 (2026-10-17)

Fixed a mismatch between the Node Browser Agent and Rust Scout Manager:
legacy `PilotShopConfig` used `deny_unknown_fields` and rejected valid
`interaction_mode: visible_click` and `maximum_delay_ms` config keys.
The new native reader accepts these optional, bounded keys and preserves them,
alongside profile/report/approval paths, when selecting a shop. Unknown fields
remain denied and local scan approval remains default-deny. The startup UI
now surfaces the underlying configuration error rather than a generic failure.
Regression test covers a real-format Amazon EMBROLY config and round-trip
preservation without activating the pilot.

## Branding DNA CAM Shop Library sync — Scout Manager v0.1.31 (2026-10-17)

The Browser Agent used to display only locally saved Windows shop records.
Now **CAM Shop Library** is loaded read-only from the current CAM tenant using
the Scout Manager's existing `RRUGC_AGENT_ID` / `RRUGC_SCOUT_TOKEN` pairing
over HTTPS. No separate browser-worker token is necessary for *listing shops*.
Select a remote Amazon/Etsy shop to create or reuse a local shop profile;
local authorization stays default-deny and browser scanning is not started.
The remote list is never silently replaced with the local list when pairing
or network access fails; a visible error and Refresh action are provided.
Shopify entries remain on the web; the current Browser Pilot supports only
Amazon/Etsy. Profile data, media, SQLite checkpoints and reports remain local
on Windows. No Amazon/Etsy marketplace request occurs during CAM shop sync.

## Branding DNA Visual UI pilot — worker v0.2.1 (2026-10-17)

On the existing Scout Manager's Branding DNA card, **Show Chromium browser**
uses `visible_click` mode for product pages, with semantic, observable
Playwright link clicks. The Node Browser Agent opens the initial shop and
returns to it for each subsequent product. No stealth/human impersonation,
CAPTCHA bypass, marketplace API, bulk scan, or auto-CAM import.
All profiles and local reports stay in Windows LOCALAPPDATA, not on CAM VPS.

This is source-level functionality pending Windows build/runtime QA; existing
installed Manager binaries do not gain it until updated. Etsy requires Etsy's
express written authorization. Amazon requires an identified Agent and
compliance with the applicable Agent Policy.

## Tải bản release mới vào thư mục Scan Pinterest

Máy Windows dùng sẵn thư mục:
`D:\Bot_Tool_Auto_Game\scan_pinterest\scout-manager-releases`.

Sau khi cập nhật checkout lên main và bản build Windows đạt, chạy
`UPDATE_SCOUT_MANAGER_RELEASE.cmd` ngay tại thư mục `scan_pinterest`.
Launcher gọi `scripts/install_scout_manager_release.ps1`, tải 2 file:
`RRUGC_Scout_Manager_0.1.30_x64_Setup.exe`,
`RRUGC_Scout_Manager_0.1.30_x64_Portable.exe`, và file SHA-256 của đúng
release `scout-manager-v0.1.30`. Kiểm tra toàn bộ SHA-256 trước khi cài,
không ghi đè binary khác hash, không xóa file release cũ, profile hay
`scout.local.env`. Launch Tauri bằng `START_SCOUT_TAURI.cmd` sau khi copy.

Các binary do workflow GitHub Actions Windows build và xuất bản qua GitHub
Releases **sau khi tất cả quality gates đạt**. Máy Windows offline sẽ không tự
nhận file; thao tác tải trong CMD phải chạy ở máy đó khi có kết nối.

## Manual Branding DNA handoff packet (Scout Manager v0.1.30)

After Pick/Reject review, operators may click **Prepare handoff** to generate
a *local-only* JSON packet for manually verifying the shortlisted products.
The packet is built from the current shop's unchanged reports and source-hash
bound Pick events, and contains only sanitized product keys, titles,
image-reference **counts**, report identifiers/hashes and operator notes.

Packet `schema_version=brand-dna-local-handoff/v1` is intentionally **NOT**
the CAM authorized-export import format. It explicitly sets
`camImportEligible=false`, `rightsVerified=false`,
`trainingUseApproved=false`, `evidenceCommitted=false` and
`scanEnabled=false`. It names four verification requirements: independent
source/export, platform permission, image/artwork usage rights, and original
media lineage. Nothing is uploaded to CAM, no rights are claimed and no
browser is started by preparing a handoff.

A separate JSON file is created without overwriting prior files at
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\handoffs\<shop-hash>`.
The UI displays its location. Empty selections, stale source revisions,
blocked pilots and invalid observations cannot produce a handoff.
Native tests verify shop isolation, no-rights defaults and stale refusal.
Full production ingestion still requires an independent rights-verified,
tenant-scoped approval process.

## Branding DNA local product review (Scout Manager v0.1.30)

Each local DOM pilot result now exposes **Pick / Reject / Reset** per valid
observed product, plus an optional one-line operator note (up to 200 chars).
Pick means **shortlisted for human verification only**, not proof of listing
accuracy, copyright permission, ownership, training eligibility or Brand DNA
approval. A blocked or partially parsed product cannot be picked.

The review is kept in a separate per-shop folder under
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\reviews`; it never
changes the immutable pilot JSON report. Decisions are bound to the exact
normalized shop, report filename, SHA-256 of report bytes and product
index/listing key. If a report is replaced, a saved shortlist is not shown
on the new revision and cannot be reused silently. A report from another
shop is rejected. Reviews are local-only and are **never sent to CAM**;
the tenant-scoped rights-verification and evidence-ingest workflow remains
a separate deployment gate.

## Branding DNA Shop Library (Scout Manager v0.1.30)

The existing Scout Manager now retains up to **50 manually configured
Amazon/Etsy shop pilots** in a local per-user shop library. Operators can
switch between saved shops without retyping their URLs. Switching changes
only the active local pilot config: it **does not launch Chromium, fetch
marketplaces, upload anything to CAM, or enable job dispatch**.

Each saved shop preserves the exact previous config, including its dedicated
profile, local report folder, bounded preview size and separate approval
document path. Existing custom/legacy configurations are added to the
library on first subsequent save or switch. Records live outside Git under
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\shop-library`;
none of the shop files or approvals are embedded in a release. Files are
bounded, ID-bound and validated before selection; corrupt or mismatched
records are skipped/refused. No deletion or automatic scan of saved shops.
Closing/reopening Scout Manager preserves the library selection via the
existing `pilot.local.json` active configuration. Changing shop while a
pilot is running is blocked.

Shop URL and platform authorization are checked independently at launch by
the Node agent; a saved shop does **not imply marketplace permission**.

## Local Branding DNA pilot results (Scout Manager v0.1.30)

The Branding DNA card now includes a **read-only local pilot result viewer**.
It lists up to eight recent reports for the currently configured shop, with
the observed timestamp, preview status, blocker reason, product title/ID,
image-reference counts and parser errors. Reports are selected by a SHA-256
digest of the exact normalized shop URL and platform. Other shops' reports
are never displayed, including on old installations that used a shared
report directory.

New shop configurations store reports in distinct per-shop directories
under `%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA\reports`.
Re-editing an existing shop preserves its previous report path. Blocked
pilots (needs_operator) now save a shop-hashed, locally identifiable report;
the UI does not expose raw image URLs or transfer any report to CAM.
These are partial unverified DOM observations, **not confirmed brand DNA**.

## Branding DNA shop configuration in the Tauri UI (v0.1.30)

The Branding DNA card now includes a native in-app shop editor. Operators set
**Platform (Amazon/Etsy), exact storefront URL, 1–5 preview product pages and
visible/headless Chromium**, then click **Save shop**. The Rust backend validates
the marketplace host/path before writing a machine-local, Git-ignored JSON
config. Each distinct shop gets a stable separate Chromium profile and a
default-deny approval-document path under
`%LOCALAPPDATA%\CreativeAssetManager\BrandingDNA`; reports stay local.
Existing approval/profile/report paths are retained when editing settings
for the same shop.

**Edit approval** opens a default-deny document in Notepad. It does not mark
any rights reviewed or authorize scanning. Etsy requires actual written
authorization and Amazon an applicable Agent Policy/rights review. Run pilot
is unavailable until the document exists; Node additionally validates its
contents/age at launch. These are self-reported operator approvals, **not
independently verified permission**. The UI never starts the pilot on save,
never sends marketplace data to any third-party scraping API, and never
shares Pinterest profiles. Advanced JSON remains for existing custom setups.

Native Windows Rust regression tests cover storefront URL restrictions,
per-shop profile separation, stable re-edits and preservation of approvals.
A real permissioned storefront test on the specific Windows machine remains
outstanding.

## Integrated Branding DNA Browser Agent — Manager v0.1.30

**One checkout/tool:** `D:\Bot_Tool_Auto_Game\scan_pinterest` hosts Review,
Keyword, Cap Stack and **Branding DNA · Browser Agent** in the same Tauri Scout
Manager. No separate Branding DNA ZIP installation is needed.
Build/launch via `scripts/build_scout_manager_tauri.ps1` and
`START_SCOUT_TAURI.cmd`. The Windows CI workflow builds an installer and
portable EXE, and runs Browser Agent package tests.

Branding DNA has its own Install runtime, Edit shop config, Check,
Run pilot, Stop and Live logs. It calls the checked-in
`apps/branding-dna-worker` and uses separate Chromium profiles and reports.
It never shares Pinterest profiles, Scout pair tokens or Gemini processing.

**Important:** Global `Run automation` and automatic recovery still affect
Pinterest modes only. Branding DNA does **not auto-launch, auto-retry or claim
production jobs**. Explicit pilot start requires an exact shop configuration
and actual platform approval. `Quit and stop` terminates its Windows Job
Object; source updates wait while a pilot or installer is running.
`START_SCOUT_BRANDING_DNA.cmd` is a compatibility launcher in the same
repository for legacy Manager users.

For first use, press Install runtime (Node 22.13+ needed), then Edit shop
config, Check, and Run pilot only if authorized. The local config is
Git-ignored and the platform authorization file must stay outside the repo.
See `docs/features/branding-dna/BROWSER_AGENT_PILOT.md`.
Real Windows browser QA remains pending.

 — Tauri v2 / React

This is the Windows native successor to PowerShell WinForms Scout Manager. **Do not remove** START_SCOUT_MANAGER.cmd or scripts/start_scout_manager.ps1 during migration.

## Install and run

Requires Node.js >=20, Rust stable (MSVC), Visual Studio C++ Build Tools, and Microsoft Edge WebView2.

From this folder:

    npm ci
    npm run tauri:dev
    npm run tauri:build

Tauri NSIS installer: src-tauri/target/release/bundle/nsis. Windows build was verified on BaoNghia; 3/3 Rust tests and the React/UI tests passed. Tauri 0.1.1 was smoke-tested with two isolated fake Scout PowerShell processes (both logged startup, the second Manager did not spawn duplicates, and exiting left zero orphan test processes). The unsigned 0.1.2 installer fixes PowerShell 5.1 startup from Windows extended paths and is stored at `D:\\Bot_Tool_Auto_Game\\scan_pinterest\\scout-manager-releases\\RRUGC_Scout_Manager_0.1.2_x64_Setup.exe`; it is excluded from Git. No signed binary auto-update is enabled yet. The installer does not embed scout.local.env or Chrome profiles.

The native app uses the checkout at CAM_SCOUT_REPO_ROOT (or the current ancestor checkout / D:\Bot_Tool_Auto_Game\scan_pinterest). It runs the existing scripts/start_scout_auto_update.ps1 -SkipUpdate with separate Review / Keyword Chrome profiles and log files. Tauri 0.1.2 shares the `Local\\CreativeAssetManager.RrugcScout.Manager` named Windows mutex with WinForms. Only the holder can start, stop, pair, or update Scouts; a second Manager shows a warning and disables its controls. Close the old WinForms Manager when ready to switch. It is preserved for rollback.

## Native 0.1.20 / Scout v55 — Cap Stack lane (Reviews Stage 1)

The Windows Scout Manager now presents a third, individually controllable
**Cap Stack Scout** card and log channel. The new runner uses the same
authenticated Scout Agent pairing token, but a **third Chrome profile**
(`pinterest-profile-cap-stack`), independent Pin-history file and log spool.
It never claims or modifies a Review campaign or Keyword Scout query lease.

The Stage 1 Cap Stack queue lives on CAM backend, defaults to **paused**, and
requires both the global **Run automation** (for the local process) and the
Stage 1 **Enable Scout** toggle (for the server lease). Repeated starts,
automatic restarts and global Pause follow the existing native Manager
single-instance and bounded restart rules. A 45-minute exclusive lease
prevents multiple Cap Stack processes from scouting the same shared lane.
No credentials are copied to React or logged.

Initial search is exactly `Cap Stack`, with a few related stacked-cap
phrases. Accepted Pins are stored only in the tenant-wide Cap Stack Library
(not in a source-plan, embroidery group or Review Scout campaign). The
✓/×/AI choices are learned through a distinct query-yield ranking;
human-corrected cap counts are recorded separately from machine guesses.
Gemini vision uses CAM's shared RRUGC provider pool to classify cap stacks
and count visible caps. Only real photos of overlapping stacks with 2–6 caps
are marked ready; counts above six are flagged out of range. New rows
remain pending until an actual model analysis completes.

The Windows operator may also use `START_SCOUT_CAP_STACK.cmd` directly after
updating the checkout; this is an alternative to the third Tauri card, not
a fourth concurrent instance. The script enforces independent profile paths
and a separate process lock. Do not run both control mechanisms for the same
mode. Legacy WinForms does not yet expose a Cap Stack control.

**Deployment order:** migrate/release the CAM backend and Stage 1 UI first;
then install Manager 0.1.20 or update Scout scripts on Windows. The old native
binary does not dynamically gain a third card from a Git fetch.
Versioned portable executables are preferred on ordinary relaunch by
`START_SCOUT_TAURI.cmd`; building a newer version will not overwrite a running
`Latest` binary or terminate active Scout children. Keep the
existing Review/Keyword processes unchanged while rolling out this lane.

## Native 0.1.17 / Scout v54 — shared Gemini fairness and Pinterest watchdog

This Windows x64 build packages the current Scout Manager with Scout v54 source compatibility. The API now targets 50/50 Gemini start opportunities between Review and Keyword during heavy Review backlog (12-second lanes, independent credential/project quota controls). The allocation is server-side: rebuilding the Manager is for consistent versioning, distribution, diagnostics and updating Scout processes, not to apply the quota policy.

Scout v54 also limits a stuck Pinterest scroll to 25 seconds and asks the existing supervisor to recycle the browser context. Reuse the existing `scout.local.env`, separate Chrome profiles, history and agent pairing. Restart the individual Scout through its Manager control to load updated Python code; replacing the Manager executable alone does not restart running Scouts.

## Native 0.1.16 / Scout v52 — non-starving Keyword fair-share hotfix

The v51 queue-first regression paused Keyword discovery whenever even one Review AI job remained pending, leaving valid keyword queries unleased indefinitely. Scout v52 treats the Review backlog as a signal to **reduce** root/related Pin work, not as a reason to suspend Pinterest discovery. The API enforces a tenant-wide, database-atomic start slot of one Keyword quote extraction per 60 seconds while Review jobs remain pending, across all paired Scout machines. The reservation is committed before provider I/O; the existing Gemini model/project credential limiter remains authoritative and responds to real quota exhaustion. Client diagnostics failures degrade to this protected extraction endpoint; authorization failures still fail closed.

No database migration, new dependency, credential re-pairing, browser profile change, or history reset is required. Preserve each machine's `scout.local.env` and `pinterest-profile*` directories. Rollback: use the previous installer / portable binary, and revert the API code commit through the normal deployment pipeline; the existing rate-limit state row expires naturally after 60 seconds. Deploy the API gate before restarting/updating Windows Scouts. Verify server `keyword_quote_backpressure`, Keyword query leases, actual quote extraction and new saved-keyword counters; do not treat `Running` alone as proof of progress.

## Native 0.1.3 stability release

The Windows 0.1.3 release uses `CREATE_NO_WINDOW` for both Git commands and the PowerShell Scout launcher. Commit metadata is cached on startup and after successful fast-forward updates rather than spawning Git on every 2-second dashboard refresh. The React polling loop deduplicates concurrent requests. The tray Quit and **Quit and stop** footer action shut down Scout process trees on background threads, with bounded per-child waits and a 10-second fallback exit. Closing the window with X hides it to tray and does not interrupt automation. Test this via an isolated `CAM_SCOUT_REPO_ROOT` mock before switching from WinForms; do not terminate real Scout processes during validation.

## Native 0.1.9 explicit installed Manager version

The header prominently shows **Manager v0.1.9**, with the exact version embedded in the installed Tauri binary (`CARGO_PKG_VERSION`) rather than inferred from the checkout. Each Scout card separately labels its own **Scout version** (`rrugc-scout-v46`, when updated), and the live-log footer shows both versions. The native UI continues to launch only after the user presses **Run automation**; the manual-start and shared-Gemini protections are unchanged. The release is built as an NSIS installer and portable executable through `.github/workflows/scout-manager-windows.yml`.

## Native 0.1.8 Keyword fair-share and Gemini key pool resilience

When Stage 1 Review image-analysis backlog is high, Keyword Scout no longer stops entirely. A tenant-scoped database gate admits at most one Quote Scout extraction per 60 seconds, then Gemini still enforces its provider/model/project quota. The quote extractor prefers active backup Gemini credentials under pressure and fails over to the primary key if a backup is temporarily unavailable. The desktop exposes queue count, backup-key configuration count, and remaining fair-share wait time. Scout client version v46 reads the keyword-specific gate from diagnostics; older clients retain the conservative global backpressure behavior. Manual Run automation remains required; no credentials or browser history are modified.

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