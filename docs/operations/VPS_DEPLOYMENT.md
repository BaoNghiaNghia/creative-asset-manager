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
locked service account and root-owned release/configuration directories:

```bash
sudo useradd --system --home /opt/creative-asset-manager --shell /usr/sbin/nologin creative-assets
sudo install -d -o root -g root -m 0755 /opt/creative-asset-manager
sudo install -d -o root -g root -m 0755 /var/www/creative-asset-manager
sudo install -d -o root -g creative-assets -m 0750 /etc/creative-asset-manager
```

Clone or unpack the release source at `/srv/creative-asset-manager-source`. Do
not put populated environment files in that checkout.

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
