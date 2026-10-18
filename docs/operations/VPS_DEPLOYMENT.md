# VPS production deployment

## Standing approval: automatically deploy after verified changes (2026-10-11)

The project owner has explicitly authorized **all routine Creative Asset Management production releases without asking for repeated approval**. CodeLocal workspace access is `full` and Live Project mode is configured. For each requested code/UI/fix rollout, the agent may run tests, commit, push `main`, deploy affected production components and verify live version/health/smoke. A request that explicitly says `do not deploy`, `staging only` or `proposal only` supersedes the default.

**No extra approval needed for normal release actions:**
- Frontend (including mobile/Stage UI): follow `scripts/deploy-cam-frontend.sh` with a clean checkout, committed build-info provenance and read-only post-deploy smoke.
- Backend/API/workers/Stage scheduler: follow `scripts/cam-rebuild-backend.sh` with the latest reviewed release, available disk/memory, test pass, and health checks. Inspect migration scope; do not execute destructive/unsafe migrations merely because deploy is preapproved.
- Desktop agent: use its actual supported build/update publisher; authorization is not a substitute for a connected Windows host or signing credentials.

**CodeLocal host-policy:** Its `full` mode does **not** bypass hard blocks on `sudo`, arbitrary host paths or privilege escalation. On the existing root-run VPS agent, the official project-root scripts have passed preflight when invoked via `bash scripts/deploy-cam-frontend.sh --commit COMMIT` and `bash scripts/cam-rebuild-backend.sh --commit COMMIT`, without adding `sudo` to the tool command. This is not a guarantee that a future call will be permitted; preflight/execute under the active host policy on each operation. If blocked, report the exact blocker and use only an operator-approved narrow broker, never a bypass.

Always verify `build-info.json`/backend revision, `/live`/`/ready`, essential workers and the corresponding read-only smoke after deployment; report which authenticated routes were or were not covered. Do not auto-accept visual baselines to conceal regressions. If deploy/health fails, stop further rollouts and follow the documented rollback, preserving job and user data. Standing approval excludes unrestricted root, production secrets/IAM/network/SSH modifications, bulk deletions, irreversible DB mutations, and changing the CodeLocal security boundary.


Production uses exactly two operator deployment entrypoints:

```bash
cd /srv/creative-asset-manager-source
git pull --ff-only
sudo scripts/deploy-cam-frontend.sh
sudo scripts/cam-rebuild-backend.sh
```

The frontend and backend workflows are independent. Do not deploy the API or workers with Docker.

## Topology

- Nginx serves `/var/www/creative-asset-manager/current`.
- Native systemd runs the API, exactly three image workers, one heavy-video worker, one video-delivery worker, the visual worker, and the isolated visual encoder from `/opt/creative-asset-manager/current`. Image worker units 4 and 5 are retained for a reviewed future scale-up but are stopped and disabled by the default deployment profile.
- PostgreSQL is native and loopback-only at `127.0.0.1:5432`.
- Docker Compose production runs Elasticsearch only at `127.0.0.1:9200`.
- Production settings remain root-owned at `/etc/creative-asset-manager/production.env`; never source that file.

## Frontend

```bash
sudo scripts/deploy-cam-frontend.sh
sudo scripts/deploy-cam-frontend.sh --commit SHA
sudo scripts/deploy-cam-frontend.sh --rollback
```

The frontend script builds and scans only generated `apps/client/dist`, installs an immutable release under `/var/www/creative-asset-manager/releases/<commit>`, atomically switches `current`, reloads Nginx, and restores the previous symlink if activation fails. It never restarts backend services.

### Read-only Production UI smoke

After an explicitly authorized frontend deploy, run the live Browser smoke separately with `make production-ui-smoke`, or opt in during that deploy with `CAM_PRODUCTION_UI_SMOKE_AFTER_DEPLOY=1`. The smoke never deploys anything and blocks every HTTP method except GET, HEAD, and OPTIONS. It verifies HTTPS, `/build-info.json` provenance, console/page/network health, and sequential desktop/tablet/mobile rendering for Asset Explorer, AI Operations, Review Board, Realistic Review UGC, Privacy, and Terms. On the root-operated VPS this smoke uses the Playwright Firefox build so the browser sandbox stays enabled; do not reintroduce Chromium `--no-sandbox` flags.

Production smoke has three coverage modes. `CAM_PRODUCTION_UI_MODE=auto` is the default: when a secure storage-state file exists it covers authenticated private routes; when the default storage-state file is absent it falls back to public Privacy/Terms coverage, reports the downgrade as `public-only`, and does not fail an otherwise healthy deploy. `strict` requires authenticated coverage and fails if storage state is unavailable. `public` deliberately runs only public routes. `CAM_PRODUCTION_UI_PUBLIC_ONLY=1` remains a compatibility alias for `public`.

Authenticated coverage uses a Playwright storage-state file outside the repository, normally `/etc/creative-asset-manager/production-ui-storage-state.json`, with mode `600` or stricter. Treat that file as a credential: never commit, print, copy into `.ui-qa`, or place it under the source checkout. If `CAM_PRODUCTION_UI_STORAGE_STATE` is explicitly set but missing, auto mode fails instead of silently ignoring that operator mistake.

### Recommended: no-login, headless Browser QA

For routine UI changes, neither a Google login nor noVNC is required. On the VPS, in the trusted repository:

```bash
make ui-qa-headless  # all 9 fixture-backed pages, three viewport sizes, strict functional/quality checks
```

Limit the run when investigating a specific page:

```bash
CAM_UI_QA_PROFILES=ai-operations CAM_UI_VIEWPORTS=desktop,mobile make ui-qa-headless
```

Run an **independent** visual audit with the saved, reviewed baselines:

```bash
make ui-qa-visual
```

The visual audit may fail if the UI has changed since its baselines were approved. Do not automatically accept/update baselines to conceal differences. The matrix summary reports `functionalStatus` and `visualStatus` separately to avoid false overall PASS results. All fixture-backed checks use a localhost Vite preview and mock data, not authenticated production. Continue using `CAM_PRODUCTION_UI_MODE=public make production-ui-smoke` for public production endpoints; authenticated production remains a distinct optional test requiring a protected session.

### CodeLocal Full Access and scoped VPS maintenance

The ongoing CodeLocal Full Access policy applies to the Creative Asset Management **workspace**, not unrestricted root access. The reviewed root-owned maintenance broker, exact-command sudoers policy, audit log, rollback, and one-time non-root operator setup are documented in [CodeLocal scoped VPS maintenance](VPS_CODELOCAL_MAINTENANCE.md). Do not assume the broker is installed just because Full Access is enabled; the existing CodeLocal host-policy and root-owned runtime require a separate operator-managed migration.

### VPS post-kernel-upgrade recovery

For the October 2026 systemd swap warning and network wait-online timeout, see [Post-reboot recovery](VPS_POST_REBOOT_RECOVERY.md). The documented repair is deliberately root-operated and does not change SSH/public network routes automatically.

### One-time QA login (trusted desktop)

For a lightweight, isolated VPS-side desktop (Xvfb + Openbox + noVNC + a private QA Chrome profile) and a conservative disk cleanup procedure, follow [VPS QA noVNC setup](VPS_QA_NOVNC.md). Keep VNC available only through a loopback SSH tunnel; the provider's serial console is not a noVNC desktop.

Use a **dedicated read-only QA account** that can visit AI Operations, Asset Explorer, Review Board, and the UGC stages. Capture from a trusted graphical desktop, not from the VPS. **Google may reject OAuth authentication in Playwright's automated Firefox.** Never disable browser security protections or try to automate the Google sign-in itself.

#### Ubuntu / Linux with Google Chrome (recommended for Google OAuth)

The command below uses a **separate QA Chrome profile**. It starts regular Chrome without Playwright/remote debugging. Sign into Creative Asset Management with Google, verify the AI Operations page is visible, then **close all Chrome windows for this QA profile**.

```bash
cd ~/creative-asset-manager
git pull --ff-only origin main
cd apps/client
# If not installed yet, install Google Chrome Stable using its official package.
google-chrome --user-data-dir="$HOME/.config/cam-qa-chrome-profile" \
  "https://creative-assets.ddns.net/ai-operations"
```

After Chrome exits, run the following in the same trusted Linux desktop terminal:

```bash
node scripts/production-ui-auth-capture-chrome.mjs \
  --url https://creative-assets.ddns.net \
  --profile-dir "$HOME/.config/cam-qa-chrome-profile" \
  --output "$HOME/cam-production-ui-session.json"
stat -c '%a %n' "$HOME/cam-production-ui-session.json"
```

The second command starts regular Chrome **only after** sign-in, attaches locally to a temporary loopback Chrome DevTools port, checks that the AI Operations page is visible, and saves only Creative Asset Management cookies and local storage. Google cookies and other websites' storage are explicitly discarded. An existing output is never overwritten. Chrome is not given `--no-sandbox` or stealth flags. If Chrome's executable has another name, add `--chrome /path/to/google-chrome`. Never point the capture tool at your everyday/default Chrome profile. Close the capture Chrome instance before deleting the dedicated QA profile when no longer needed; that profile itself contains login credentials.

#### Windows / alternative authentication

For sites that permit automated browser login without a Google security block, the original Firefox flow remains available from a graphical desktop:

```powershell
cd apps/client
npm ci
npx playwright install firefox
node scripts/production-ui-auth-capture.mjs --url https://creative-assets.ddns.net --output "$env:USERPROFILE\cam-production-ui-session.json"
```

If Google rejects the Playwright Firefox sign-in, use the manual Chrome flow above on Ubuntu instead.

#### Secure transfer and validation

Transfer the protected session file over an encrypted, trusted channel to the root-owned `/etc/creative-asset-manager/production-ui-storage-state.json` on the VPS, with permissions `600` and directory access restricted to the QA runner. Do not place either the app session file or QA Chrome profile in Git, chat, email, logs, public downloads, or a shared folder. Rotate/delete both after their QA purpose ends. Repeat the manual login if the app session expires.

Before treating authenticated QA as a release gate, run `CAM_PRODUCTION_UI_MODE=strict make production-ui-smoke` and verify the report says `Coverage: authenticated` with no skipped routes. **A public-only PASS is never equivalent to authenticated private-route coverage.**

```bash
# Default: authenticated when possible, safe public-only fallback otherwise.
CAM_PRODUCTION_UI_MODE=auto \
CAM_PRODUCTION_EXPECTED_COMMIT=$(git rev-parse HEAD) \
make production-ui-smoke

# Release gate: authenticated coverage is mandatory.
CAM_PRODUCTION_UI_MODE=strict \
CAM_PRODUCTION_UI_STORAGE_STATE=/etc/creative-asset-manager/production-ui-storage-state.json \
CAM_PRODUCTION_EXPECTED_COMMIT=$(git rev-parse HEAD) \
make production-ui-smoke

# Optional during an already-authorized frontend deploy. Auto is the default.
CAM_PRODUCTION_UI_SMOKE_AFTER_DEPLOY=1 \
CAM_PRODUCTION_UI_MODE=auto \
scripts/deploy-cam-frontend.sh
```

The plan may use read-only UI interactions such as tab clicks. Network interception still blocks every non-GET/HEAD/OPTIONS request, and any attempted mutation fails the smoke. This lets Production QA exercise states such as Realistic Review UGC Stage 3 and assert visibility, full-width layout, and horizontal overflow without granting write access. A smoke failure reports evidence under `apps/client/.ui-qa/production-smoke/` and returns non-zero. Successful runs prune that directory to the newest five smoke-run groups by default (`CAM_PRODUCTION_UI_KEEP_RUNS=1..20` overrides this). It does not roll back or mutate Production automatically; rollback remains an explicit operator decision based on the evidence.

## Backend

```bash
sudo scripts/cam-rebuild-backend.sh
sudo scripts/cam-rebuild-backend.sh --commit SHA
sudo scripts/cam-rebuild-backend.sh --rollback
```

The backend script creates an immutable native release under `/opt/creative-asset-manager/releases/<commit>`, creates the API virtualenv, validates the root-owned environment without printing values, verifies one Alembic head, runs only `alembic upgrade head`, atomically switches `current`, and restarts native API/image/video services. **Release/disk cleanup is disabled by default** pending an independently verified backup restore and operator inventory. The explicit `--cleanup` flag enables pre/post-deploy cleanup only after operator verification; `--no-cleanup` remains available. Cleanup protects `current`, `previous`, requested target and in-use service releases, and the default retention count is 4. Even `--keep-releases 1` never removes `previous`. Review `VPS_RELEASE_INVENTORY_READ_ONLY.md` and `VPS_DATABASE_RESTORE_REHEARSAL.md` first. The script never builds frontend files and never uses Docker for API/workers.

## One-time split-worker migration

The legacy all-role worker and optional image worker 5 must be inactive before the default four-image-worker profile is enabled:

```bash
sudo systemctl stop creative-asset-manager-worker.service
sudo systemctl disable creative-asset-manager-worker.service
sudo systemctl disable --now creative-asset-manager-image-worker-5.service
sudo systemctl enable --now creative-asset-manager-image-worker.service
sudo systemctl enable --now creative-asset-manager-image-worker-2.service
sudo systemctl enable --now creative-asset-manager-image-worker-3.service
sudo systemctl enable --now creative-asset-manager-image-worker-4.service
sudo systemctl enable --now creative-asset-manager-video-worker.service
sudo systemctl enable --now creative-asset-manager-video-delivery-worker.service
```

Image workers 1-4 use `WORKER_ROLE=image` on health ports 8081, 8083, 8084, and 8085. The fourth image worker lets the production queue use the tenant's third storage slot while one image worker continues image-AI work; worker 5 remains reserve capacity. The heavy-video worker keeps the historical service name `creative-asset-manager-video-worker.service`, uses `WORKER_ROLE=video-heavy`, and listens on health port 8082. It claims only `video_analyze` and `video_generate`. The delivery worker uses `WORKER_ROLE=video-delivery`, health port 8088, and claims `video_search_index`, `video_cache_fill`, and `video_playback_prepare`. This prevents long Gemini/FFmpeg/generation work from blocking review playback and CDN cache jobs. All workers use the same PostgreSQL processing queue and policy accounting.

## Disk preflight

Backend deploys abort before building a new immutable release when free disk is below the larger of:

- `CAM_BACKEND_MIN_FREE_MIB` (default `2048` MiB), or
- the current/source release estimate multiplied by `CAM_BACKEND_RELEASE_HEADROOM_PERCENT` (default `125`%).

The preflight runs after normal old-release/log cleanup. Lower these gates only after reviewing the actual release size and keeping enough space for the active and rollback releases.

## Low-memory release protection

Fresh backend release builds can temporarily add Python/pip/validation memory on top of the running production footprint. Before a fresh build, the deployment script checks Linux `MemAvailable`. When it is below `CAM_BACKEND_MIN_AVAILABLE_MEMORY_MIB` (default `3072` MiB), the script drains the dedicated Visual Search worker first and then stops the isolated visual encoder. Other API/image/video services remain available. If the deployment fails, the script restores only the visual services that had been active before the drain.

The drain is skipped for an already-built immutable release and for `--no-restart`. During normal restart, the visual encoder is restarted and must pass `/ready` before the visual worker is restarted. This avoids turning healthy queued visual work into transient failures while the model is still loading. Do not lower the memory threshold on a no-swap host without measuring the full API/worker/Elasticsearch/PostgreSQL footprint during a fresh release build.

## Verification

```bash
systemctl is-active creative-asset-manager-api.service
systemctl is-active creative-asset-manager-image-worker.service
systemctl is-active creative-asset-manager-image-worker-2.service
systemctl is-active creative-asset-manager-image-worker-3.service
systemctl is-active creative-asset-manager-image-worker-4.service
systemctl is-active creative-asset-manager-image-worker-5.service
systemctl is-active creative-asset-manager-video-worker.service
systemctl is-active creative-asset-manager-video-delivery-worker.service
systemctl is-active creative-asset-manager-worker.service

sudo journalctl -u creative-asset-manager-image-worker.service -f
sudo journalctl -u creative-asset-manager-video-worker.service -f
sudo journalctl -u creative-asset-manager-video-delivery-worker.service -f
curl --fail --silent http://127.0.0.1:9200/_cluster/health
```

Expected: API, image workers 1-4, heavy-video worker, video-delivery worker, visual worker, and visual encoder are active. Image worker 5 and the legacy all-role worker are inactive.

## Rollback

```bash
sudo scripts/deploy-cam-frontend.sh --rollback
sudo scripts/cam-rebuild-backend.sh --rollback
```

Backend rollback does not execute an Alembic downgrade. Schema compatibility with the previous application release remains required. Neither deployment script deletes processing jobs, PostgreSQL data, Elasticsearch data, or search aliases.

### Browser QA automatic cleanup after successful deploy

The production frontend deployment performs a **non-blocking, narrowly scoped**
Browser QA cleanup **only after Nginx activation and public health checks pass**.
It runs `python3 scripts/cam-clean-browser-qa.py`. It does **not** clean
R2/Google Drive product assets, Stage 1 images, Codex final output staging,
visual-baselines, database backups, Docker volumes, or Playwright browser
binaries. If cleanup fails, the deployment remains healthy and a warning is logged.

| QA evidence | Retention |
| --- | --- |
| Ordinary `apps/client/.ui-qa/<run-id>` | Keep newest 3 and at least 3 days |
| Production smoke | Keep newest 5; preserve failed runs for at least 14 days |
| Accepted baseline proposals | Keep newest 2; prune proposals older than 7 days **only after verifying the approved image copies exist** in `visual-baselines/` |
| Unapproved/incomplete proposals | Never automatically remove |
| Failed QA and any run in progress | Failed run evidence retained 14 days; no run modified within last 2 hours |
| Autofix/repair sessions, other unknown folders | Never automatically remove |

Review without deletion: `python3 scripts/cam-clean-browser-qa.py --dry-run`.
Manual approved cleanup: `python3 scripts/cam-clean-browser-qa.py`.
Temporarily disable on deploy with `CAM_BROWSER_QA_CLEANUP_AFTER_DEPLOY=0`.
The cleaner uses a lock, refuses symlinked QA roots, and only removes
known timestamp-named directories directly inside the allowlisted QA paths.
The cleanup is limited to Browser QA: if disk usage stays high, investigate
the separate model cache, release storage, staging files, Docker and logs.
