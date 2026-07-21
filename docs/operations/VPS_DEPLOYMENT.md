# VPS production deployment

This deployment is intentionally hybrid:

- native Nginx serves `apps/client/dist` and terminates TLS;
- the API and worker run as native systemd services;
- PostgreSQL runs natively and listens on `127.0.0.1:5432`;
- Docker Compose runs only Elasticsearch and publishes it on
  `127.0.0.1:9200`;
- only Nginx ports 80 and 443 are public.

The examples use `assets.example.com`, `/opt/creative-asset-manager`, the
`creative-assets` system account and the `creative_asset_manager` database.
Replace these values consistently before installation.

## Host preparation

Install Nginx, PostgreSQL, Python, Node.js, Docker Engine and the Docker Compose
plugin from trusted operating-system/vendor repositories. Create a locked
service account and the configuration directory:

```bash
sudo useradd --system --home /opt/creative-asset-manager --shell /usr/sbin/nologin creative-assets
sudo install -d -o creative-assets -g creative-assets /opt/creative-asset-manager
sudo install -d -o root -g creative-assets -m 0750 /etc/creative-asset-manager
```

Place the repository at `/opt/creative-asset-manager`. Build dependencies and
the static frontend as the service account:

```bash
cd /opt/creative-asset-manager/apps/api
python3 -m venv .venv
.venv/bin/python -m pip install --requirement requirements.txt
cd /opt/creative-asset-manager/apps/client
npm ci
npm run build
```

The build produces `apps/client/dist`, which Nginx serves directly.

## Native PostgreSQL

Configure PostgreSQL to listen only on loopback and restrict `pg_hba.conf` to
the application database/user. Exact file locations depend on the distribution.
The relevant settings are:

```text
listen_addresses = '127.0.0.1'
```

```text
host  creative_asset_manager  cam_app  127.0.0.1/32  scram-sha-256
```

Create the database/user with a generated password; never copy the placeholder
from the example environment. Keep the native PostgreSQL port blocked by the
VPS firewall. Run schema changes explicitly before starting the API:

```bash
cd /opt/creative-asset-manager/apps/api
DATABASE_URL='postgresql+psycopg://cam_app:ENCODED_PASSWORD@127.0.0.1:5432/creative_asset_manager' .venv/bin/python -m alembic upgrade head
DATABASE_URL='postgresql+psycopg://cam_app:ENCODED_PASSWORD@127.0.0.1:5432/creative_asset_manager' .venv/bin/python -m app.operations.tag_cli seed-system-tags
```

## Elasticsearch

The production Compose file contains Elasticsearch only. Its host binding is
explicitly loopback-only; do not change it to `9200:9200` or `0.0.0.0:9200`.
Tune `vm.max_map_count` and heap size for the VPS before startup:

```bash
sudo sysctl -w vm.max_map_count=262144
cd /opt/creative-asset-manager
ES_JAVA_OPTS='-Xms1g -Xmx1g' docker compose --file infrastructure/docker/docker-compose.prod.yml up --detach
```

Persist the sysctl through the operating system's normal configuration
mechanism. Elasticsearch application-layer security is disabled because this
single-node service is reachable only through loopback; firewall rules remain a
required second boundary.

## Environment and systemd

Copy the template and replace every placeholder. The populated file must not be
stored in Git:

```bash
sudo install -o root -g creative-assets -m 0640 deploy/production.env.example /etc/creative-asset-manager/production.env
sudoedit /etc/creative-asset-manager/production.env
sudo install -o root -g root -m 0644 deploy/systemd/creative-asset-manager-api.service /etc/systemd/system/
sudo install -o root -g root -m 0644 deploy/systemd/creative-asset-manager-worker.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now creative-asset-manager-api.service
sudo systemctl enable --now creative-asset-manager-worker.service
```

The API binds only `127.0.0.1:8000`. Uvicorn's own proxy-header processing is
disabled; the application middleware accepts forwarded headers only from
`127.0.0.1/32`. The worker health listener binds only `127.0.0.1:8081` and
processing remains inert while `PROCESSING_JOBS_ENABLED=false`.

## Nginx and TLS

Replace `assets.example.com` in both the Nginx file and environment. Provision
the TLS certificate before enabling the HTTPS server, then install the site:

```bash
sudo install -o root -g root -m 0644 infrastructure/nginx/creative-asset-manager.conf /etc/nginx/sites-available/creative-asset-manager.conf
sudo ln -s /etc/nginx/sites-available/creative-asset-manager.conf /etc/nginx/sites-enabled/creative-asset-manager.conf
sudo nginx -t
sudo systemctl reload nginx
```

The SPA fallback is handled by Nginx, `/assets/` receives immutable caching,
and `/api/`, `/live`, `/ready` and `/version` are proxied to the loopback API.

## Artifact validation

Run these checks from the repository before installation:

```bash
docker compose --file infrastructure/docker/docker-compose.prod.yml config --quiet
docker compose --file infrastructure/docker/docker-compose.prod.yml config --services
systemd-analyze verify deploy/systemd/creative-asset-manager-api.service deploy/systemd/creative-asset-manager-worker.service
```

`config --services` must output only `elasticsearch`. After installing the
repository, environment, domain and TLS certificate, validate the complete
host configuration:

```bash
sudo nginx -t
sudo systemctl restart creative-asset-manager-api creative-asset-manager-worker
sudo systemctl --no-pager --full status creative-asset-manager-api creative-asset-manager-worker
curl --fail --silent http://127.0.0.1:8000/live
curl --fail --silent http://127.0.0.1:8000/ready
curl --fail --silent http://127.0.0.1:8000/version
curl --fail --silent 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s'
ss -ltn
curl --fail --silent https://assets.example.com/live
```

Confirm `8000`, `8081`, `5432` and `9200` listen only on `127.0.0.1`; only Nginx
should listen publicly on 80/443. `/ready` returns HTTP 503 when PostgreSQL is
unavailable, and also when Elasticsearch is unavailable while a relevant
search feature is enabled.

Review logs without printing the environment file:

```bash
sudo journalctl -u creative-asset-manager-api -u creative-asset-manager-worker --since today
cd /opt/creative-asset-manager
docker compose --file infrastructure/docker/docker-compose.prod.yml logs --tail 200 elasticsearch
```

## Safe rollout and rollback

Keep all pipeline/search flags disabled for the first boot. Enable global flags
and tenant policies using the controlled-rollout runbook only after the health
checks pass. To roll back these runtime artifacts, stop the two services,
restore the previous unit/Nginx/environment files, run `systemctl daemon-reload`
and reload Nginx. `docker compose down` stops Elasticsearch without deleting its
named volume; do not add `--volumes` unless data deletion is explicitly intended.
Database migrations remain a separate release operation.
