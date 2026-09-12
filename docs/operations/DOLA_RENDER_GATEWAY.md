# Dola Render Gateway operation runbook

## Scope and authorization

DG-07 prepares repository artifacts only. It does not authorize VPS access, account provisioning, service installation, feature enablement, canary tenants, or provider requests. DG-10 is a read-only VPS audit; DG-11 is separately authorized installation with CAM generation still off; DG-12 is separately authorized canary.

The gateway provenance is recorded in apps/dola_render_gateway/UPSTREAM.md. Its upstream license and service-terms review remains a release gate.

## Architecture and isolation

CAM API/workers remain under /opt/creative-asset-manager. Dola has a separate source release, venv, user, systemd unit, SQLite execution state and Patchright/Chromium runtime. CAM PostgreSQL is business authority; Dola SQLite is recovery/execution state only.

| Item | Location |
| --- | --- |
| source releases | /opt/dola-render-gateway/releases/<release-sha> |
| active source | /opt/dola-render-gateway/current |
| Dola-only runtime/venv | /var/lib/dola-render-gateway/current-runtime |
| persistent state | /var/lib/dola-render-gateway |
| execution/quota DBs | /var/lib/dola-render-gateway/db/tasks.db, pool_usage.db |
| profiles/accounts | /var/lib/dola-render-gateway/profiles, /accounts |
| downloads/artifacts | /var/lib/dola-render-gateway/downloads, /artifacts |

Create dola-render-gateway:dola-render-gateway; never use root, www-data, or a CAM identity. Persistent directories must be owned by that account, mode 0750 or stricter. State, profiles and SQLite DBs must never be inside a release checkout.

## Later installation (DG-11 only)

After an authorized readiness audit install Python 3, venv support, Xvfb and Chromium dependencies. Copy the committed unit and example to /etc/dola-render-gateway/production.env, root-owned 0640. Generate DOLA_INTERNAL_API_KEY out-of-band. The matching CAM-side value belongs only in /etc/creative-asset-manager/video-worker.env, root-owned 0640; never expose it to frontend, browser, Nginx, unrelated CAM services or logs.

Run deploy/tools/prepare_dola_runtime.sh --check, then --dry-run; DG-11 alone may use --prepare. It creates the Dola-only venv from deploy/dola-render-gateway.requirements.lock and installs Patchright Chromium in current-runtime/browser-cache. Record requirements.sha256, browser/Patchright revision and active release SHA before upgrades. It never changes the CAM API/worker venv.

No Nginx Dola proxy/dashboard, /videos, VNC or noVNC route is permitted. Bind only 127.0.0.1:8100; 0.0.0.0:8100 or :::8100 are failures.

## Xvfb and browser

The service requires deterministic DISPLAY=:99; DG-11 starts Xvfb before activating the gateway and treats display failure as fatal. Verify with pgrep -a Xvfb, systemctl status, service environment and a controlled Chromium launch. Do not run Chromium as root and never use --no-sandbox. Temporary visual provisioning, if unavoidable, is localhost-only VNC/noVNC through SSH, with the temporary display service removed afterward.

## Account provisioning

The actual entrypoint is apps/dola_render_gateway/upstream/add_account.py. An authorized operator runs it as the dedicated user with only an account identifier on argv:

sudo -u dola-render-gateway /var/lib/dola-render-gateway/current-runtime/bin/python /opt/dola-render-gateway/current/apps/dola_render_gateway/upstream/add_account.py account-01

Credentials/TOTP are entered interactively. Never put passwords, cookies, TOTP data or personal Windows Chrome profiles in argv, repository, logs or unencrypted backups. Profiles are secrets.

## Health and default-off checks

After authorized installation verify the process user, ss -ltnp loopback listener, /health/live, /health/ready, wrong-bearer rejection and correct-bearer acceptance. Verify CAM API and video-worker health independently. Before any later canary, retain VIDEO_GENERATION_ENABLED=false, DOLA_RENDER_GATEWAY_ENABLED=false, and VIDEO_GENERATION_CANARY_TENANT_IDS=.

## Backup, upgrade and rollback

Stop the gateway before making a consistent SQLite/profile backup; do not copy an actively written Chromium profile. Back up tasks.db, pool_usage.db and restricted/encrypted account profiles. Before upgrades record release SHA, runtime lock/browser revision and backup location.

Rollback: disable CAM generation if enabled; stop gateway; repoint current and runtime pointer if required to known-good releases; restore compatible state only when necessary; start and verify live, ready, bearer and loopback. Do not Alembic-downgrade CAM for Dola rollback.

For upstream updates, re-audit source/license/terms, record old/new upstream SHAs and diff, regenerate/review lock, test in isolation and retain rollback release.

## Incidents

- Gateway unavailable/browser crash: keep flags off; inspect journal/Xvfb and restart only after cause review.
- Invalid profile: remove from rotation and reprovision interactively; never copy cookies.
- Duplicate provider task: severe—disable VIDEO_GENERATION_ENABLED, preserve CAM/Dola state, do not mass-delete, investigate idempotency.
- Memory pressure: stop/limit gateway, preserve state, inspect Chromium and host capacity.
- SQLite issue: stop before consistency recovery; restore only compatible backups.
- Auth failure: verify secret-file permissions and matching keys without printing values.
- Public bind: stop gateway immediately and restore loopback configuration; firewall-only controls are insufficient.