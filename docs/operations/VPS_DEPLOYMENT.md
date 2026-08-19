# VPS production deployment

This deployment is intentionally hybrid:

- native Nginx serves an immutable frontend release and terminates TLS;
- the API and worker run as native systemd services;
- PostgreSQL runs natively on `127.0.0.1:5432`;
- Docker Compose runs only Elasticsearch on `127.0.0.1:9200`;
- only Nginx ports 80 and 443 are public.

Examples use `assets.example.com`, `/opt/creative-asset-manager`,
`/var/www/creative-asset-manager`, the `creative-assets` service account and the
`creative_asset_manager` database. Replace them consistently.

## Release layout

Application and frontend releases are immutable after installation:

```text
/opt/creative-asset-manager/
  releases/<release-id>/
  current -> releases/<release-id>
  previous -> releases/<previous-id>
/var/www/creative-asset-manager/
  releases/<release-id>/
  current -> releases/<release-id>
  previous -> releases/<previous-id>
```

Systemd reads application code through `/opt/creative-asset-manager/current`.
Nginx reads static files through `/var/www/creative-asset-manager/current`. The
deployment CLI updates each symlink atomically and retains the preceding release
for rollback. Keep the source checkout outside these trees, for example
`/srv/creative-asset-manager-source`.

## Host preparation

Install Nginx, PostgreSQL, Python with `venv`, Node.js, npm, rsync, curl, Docker
Engine and the Docker Compose plugin from trusted repositories. Create the
nInstall ffmpeg and ffprobe from the distribution trusted package mechanism for the native worker.
locked service account and root-owned release/configuration directories:

```bash
sudo useradd --system --home /opt/creative-asset-manager --shell /usr/sbin/nologin creative-assets
sudo install -d -o root -g root -m 0755 /opt/creative-asset-manager
sudo install -d -o root -g root -m 0755 /var/www/creative-asset-manager
sudo install -d -o root -g creative-assets -m 0750 /etc/creative-asset-manager
```

Clone or unpack the release source at `/srv/creative-asset-manager-source`. Do
not put populated environment files in that checkout.

n## Video runtime preflight
Before any VIDEO activation verify command -v ffmpeg, command -v ffprobe, ffmpeg -version, and ffprobe -version.
Prepare the native StateDirectory child, never /tmp: sudo install -d -o creative-assets -g creative-assets -m 0750 /var/lib/creative-asset-manager/video-proxy
Then verify: sudo -u creative-assets test -w /var/lib/creative-asset-manager/video-proxy
VIDEO_PROXY_MAX_CHUNK_BYTES is 1,500,000,000 bytes plus a 67,108,864-byte working reserve; runtime requires 1,567,108,864 free bytes. This is not Docker volume preallocation.

## Native PostgreSQL

Configure PostgreSQL to listen only on loopback and restrict `pg_hba.conf` to
the application database/user. Exact paths depend on the distribution:

```text
listen_addresses = '127.0.0.1'
```

```text
host  creative_asset_manager  cam_app  127.0.0.1/32  scram-sha-256
```

Create the database/user with a generated password and keep port 5432 blocked by
the VPS firewall. Percent-encode reserved password characters in `DATABASE_URL`.
Database schema changes are performed only by the explicit `migrate` command.

## Environment

Install the template, replace every placeholder and retain mode `0640`. The
validator rejects replacement markers, non-production settings, unsafe file
ownership and group-write/world permissions. It reports setting names and error
classes only, never values.

```bash
cd /srv/creative-asset-manager-source
sudo install -o root -g creative-assets -m 0640 deploy/production.env.example /etc/creative-asset-manager/production.env
sudoedit /etc/creative-asset-manager/production.env
```

Do not run `source` on this file. Deployment commands parse it as data without
shell evaluation and suppress output from secret-bearing child commands.

## Elasticsearch

The production Compose file contains Elasticsearch only and explicitly binds
loopback. Never change it to `9200:9200` or `0.0.0.0:9200`.

```bash
sudo sysctl -w vm.max_map_count=262144
cd /srv/creative-asset-manager-source
ES_JAVA_OPTS='-Xms1g -Xmx1g' docker compose --file infrastructure/docker/docker-compose.prod.yml up --detach
```

Persist the sysctl using the operating system's standard mechanism. The local
single-node Elasticsearch has application-layer security disabled because it is
not reachable off-host; the host firewall remains a required second boundary.

## Deployment CLI

The CLI is `deploy/bin/cam-deploy`. It uses `set -Eeuo pipefail`, never enables
shell tracing, suppresses dependency/migration command output and prints only
bounded operational messages.

Prepare an immutable release from the source checkout. `install-release` runs
Python venv installation, pinned `requirements.txt`, `npm ci`, the frontend
production build and static frontend installation:

```bash
cd /srv/creative-asset-manager-source
RELEASE_ID="$(git rev-parse --short=12 HEAD)"
sudo deploy/bin/cam-deploy install-release "$PWD" "$RELEASE_ID"
sudo deploy/bin/cam-deploy check-config "$RELEASE_ID"
sudo deploy/bin/cam-deploy verify-alembic-head "$RELEASE_ID"
```

The individual preparation commands remain available when troubleshooting an
inactive staging release:

```bash
sudo deploy/bin/cam-deploy install-python RELEASE_ID
sudo deploy/bin/cam-deploy build-frontend RELEASE_ID
sudo deploy/bin/cam-deploy install-frontend-release RELEASE_ID
```

Run forward-only database operations before switching code:

```bash
sudo deploy/bin/cam-deploy migrate "$RELEASE_ID"
sudo deploy/bin/cam-deploy seed "$RELEASE_ID"
sudo deploy/bin/cam-deploy switch-release "$RELEASE_ID"
```

`migrate` verifies exactly one Alembic head and runs only `alembic upgrade head`.
`seed` invokes the idempotent system-tag command. `switch-release` does not
restart processes implicitly, allowing operators to control the maintenance
window.

## systemd and Nginx

Install the native units and Nginx site from the trusted source checkout after
the first release has been switched:

```bash
cd /srv/creative-asset-manager-source
sudo install -o root -g root -m 0644 deploy/systemd/creative-asset-manager-api.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/creative-asset-manager-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo install -o root -g root -m 0644 infrastructure/nginx/creative-asset-manager.conf /etc/nginx/sites-available/creative-asset-manager.conf
sudo ln -s /etc/nginx/sites-available/creative-asset-manager.conf /etc/nginx/sites-enabled/creative-asset-manager.conf
sudo nginx -t
sudo systemctl reload nginx
sudo systemctl enable creative-asset-manager-api.service creative-asset-manager-worker.service
sudo deploy/bin/cam-deploy restart-api
sudo deploy/bin/cam-deploy restart-worker
sudo deploy/bin/cam-deploy verify-api
sudo deploy/bin/cam-deploy verify-worker
```

The API binds `127.0.0.1:8000`; application middleware trusts proxy headers only
from Nginx loopback. Worker health binds `127.0.0.1:8081`. A worker with
`PROCESSING_JOBS_ENABLED=false` intentionally returns 503 from `/ready`, so the
worker verifier requires liveness/health and only requires readiness when the
flag is enabled.

Nginx terminates TLS, handles SPA fallback, caches immutable `/assets/`, and
proxies `/api/`, `/live`, `/ready` and `/version`.

## Validation

Validate artifacts before installation:

```bash
bash -n deploy/bin/cam-deploy
apps/api/.venv/bin/python -m unittest deploy.tests.test_production_env -v
docker compose --file infrastructure/docker/docker-compose.prod.yml config --quiet
docker compose --file infrastructure/docker/docker-compose.prod.yml config --services
systemd-analyze verify deploy/systemd/creative-asset-manager-api.service deploy/systemd/creative-asset-manager-worker.service
```

Compose service output must contain only `elasticsearch`. The direct systemd
check expects `/opt/creative-asset-manager/current` to exist; before initial
installation, validate with equivalent temporary executable/dependency stubs or
run it immediately after `switch-release`.

After startup:

```bash
sudo deploy/bin/cam-deploy verify-api
sudo deploy/bin/cam-deploy verify-worker
sudo deploy/bin/cam-deploy diagnostics
curl --fail --silent 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s' >/dev/null
ss -ltn
curl --fail --silent https://assets.example.com/live >/dev/null
```

Confirm 8000, 8081, 5432 and 9200 listen only on `127.0.0.1`. `diagnostics`
prints release IDs, service states, HTTP status codes and dependency availability
only. It never prints the environment, URLs, logs, payloads or provider errors.

## Rollback

Rollback switches both application and frontend to the recorded previous
release, gracefully restarts the worker, restarts the API and verifies health:

```bash
cd /srv/creative-asset-manager-source
sudo deploy/bin/cam-deploy rollback-release
```

Application rollback never executes `alembic downgrade`; PostgreSQL stays at its
current schema revision. Therefore migrations must remain backward compatible
with the retained application release. If that is not true, stop and follow a
reviewed database recovery procedure rather than using this command.

To switch only static frontend files, use:

```bash
sudo deploy/bin/cam-deploy switch-frontend-release RELEASE_ID
```

Keep pipeline/search flags disabled for the first boot and use the controlled
rollout runbook for tenant enablement. `docker compose down` stops Elasticsearch
without deleting its named volume; never add `--volumes` unless deletion is
explicitly approved.


### Validated baseline (2026-07-21)

The production artifacts were exercised locally with disposable PostgreSQL
16.4, Elasticsearch, Nginx and fake OAuth credentials. No production provider
was contacted.

| Check | Result | Validation |
| --- | --- | --- |
| Frontend production build | Passed | `npm ci --no-audit --no-fund && npm run build`; Vite transformed 46 modules. |
| Nginx syntax | Passed | `nginx -t` with `nginx:1.27.5-alpine`, the production site and a temporary certificate. |
| Production API configuration | Passed | `production_env.py check` accepted a mode-0600 production environment with PostgreSQL and loopback proxy settings. |
| Production SQLite rejection | Passed | The same validator rejected a production `sqlite` URL before application startup. |
| Empty PostgreSQL migration | Passed | Alembic upgraded an empty PostgreSQL 16.4 database to `0018_legacy_metadata_schema`; exactly one head was present. |
| API probes | Passed | Native Uvicorn returned `200` from `/live` and `ready` from `/ready`; PostgreSQL was available. Elasticsearch correctly reported `disabled` because all relevant Search v2 flags were false. |
| Worker lifecycle | Passed | The native worker exposed liveness/health and exited within the bounded shutdown window after `SIGTERM`. Its readiness remained false because processing jobs were intentionally disabled. |
| Elasticsearch Compose | Passed | The pinned production service became Docker-healthy and cluster yellow/green on `127.0.0.1:9200`; no wildcard binding was present. |
| OAuth and secure cookie | Passed | A request through Nginx redirected to Google with `https://assets.example.com/api/auth/google/callback`; the state cookie was `HttpOnly`, `Secure` and `SameSite=Lax`. Fake client credentials were used and authorization was not completed. |
| Nginx request behavior | Passed | A deep SPA route returned `index.html`, `/api` and `/live` reached the API, and hashed static assets returned public immutable caching headers with an expiry. |
| Feature-flag defaults | Passed | Focused settings tests confirmed every unified pipeline feature flag defaults to false; the production template keeps ingestion, AI processing and Search v2 rollout flags false. |

The focused production validation suite is reproducible with:

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python -m unittest \
  deploy.tests.test_vps_deployment \
  tests.test_config \
  tests.test_http_config \
  tests.test_production_health \
  tests.modules.auth_persistence.test_config \
  tests.modules.processing.test_runtime -v
```

It passed 46 tests. PostgreSQL-specific repository/startup coverage additionally
passed six tests in `tests.integration.test_postgresql`. Keep Elasticsearch
readiness disabled until the associated search feature is deliberately enabled;
the independent Compose health check still proves that the service is available.


## Multi-provider AI production governance

Provider selection is governed by three fail-closed layers:

1. Global feature flags and AI_EMERGENCY_STOP_ENABLED are upper bounds.
2. Tenant/provider policy controls provider enablement, single/batch mode,
   allowlisted models, distributed concurrency, pause state and provider budgets.
3. A cost rate effective for provider, model and processing mode is required
   before provider invocation.

Runtime emergency controls do not require a worker restart:

- PUT /api/v1/admin/ai-governance/runtime-controls/global
- PUT /api/v1/admin/ai-governance/runtime-controls/gemini
- PUT /api/v1/admin/ai-governance/runtime-controls/openai

Use a platform-admin credential and body
{"stopped":true,"reason":"incident reference"}. Resume with stopped=false.
The worker checks durable controls before claim and immediately before provider
invocation. Static environment stops remain an additional emergency upper bound.

Configure tenant AI policies through
PATCH /api/v1/admin/processing-policies/{tenant_id}/providers/ai/{provider}.
Supported governance fields include processing_enabled, single_enabled,
batch_enabled, emergency_stop, single/batch concurrency limits,
daily/monthly budget limits, currency and allowed_models_json.

Cost rates are created through POST /api/v1/admin/ai-governance/cost-rates
with provider, model, processing_mode (single, batch, or any), effective date,
unit prices and currency. Missing rates produce missing_cost_rate and no
provider call. A platform administrator may grant a specific analysis exception
at POST /api/v1/admin/ai-governance/{tenant_id}/budget-overrides; the reason and
actor are audited, and unknown cost remains NULL rather than being reported as
zero.

Emergency rollback:

1. Stop the affected runtime control.
2. Allow currently running calls to drain; queued jobs remain durable.
3. Correct policy/rates, inspect bounded metrics and audit events.
4. Resume the runtime control. Never delete queued jobs to resume processing.

## AUTH-09 production access migration

Before deployment, production configuration must contain:

```dotenv
AUTH_SELF_SIGNUP_ENABLED=false
AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=false
PROCESSING_POLICY_ADMIN_IDS=
AUTH_COOKIE_SECURE=true
```

The API rejects a production startup that enables the deprecated processing
administrator allowlist. Apply Alembic migrations first, complete an approved
OAuth login or pre-provisioned identity, and use the commands documented in
`docs/operations/AUTH_PERSISTENCE.md`:

1. Run `bootstrap-access --dry-run` for the exact provider subject and tenant.
2. Run the same command with `--confirm` and an audit/ticket reason.
3. Grant platform administration only through the separate explicit command
   when platform-wide access is required.
4. Run `backfill-legacy-auth --dry-run --max-pages 1`, resolve every reported
   tenant/user conflict, then run confirmed pages resumably.
5. Verify persistent sessions have application user and active tenant IDs,
   tenant roles are durable, disabled users cannot reuse sessions, and audit
   events contain no tokens.
6. Verify an ordinary viewer receives 403 for AI Operations while the
   tenant_admin can access the tenant-scoped dashboard.

Never infer an administrator from email domain, Google Drive ownership,
Microsoft tenant claims or OAuth scopes. Never paste access/refresh tokens into
the bootstrap commands.

Rollback deploys the previous application release without downgrading
PostgreSQL. Disable self-signup and compatibility bypasses, revoke incorrect
durable assignments, and preserve identity/membership/audit history. The
temporary allowlist must remain disabled in production throughout rollback.

The compatibility migration deadline is 2026-08-31. Remove
`PROCESSING_POLICY_ADMIN_IDS` only after the bounded backfill has no unresolved
records and two production releases have emitted no compatibility authorization
events.
