# VPS production deployment

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
- Native systemd runs `creative-asset-manager-api.service`, `creative-asset-manager-image-worker.service`, and `creative-asset-manager-video-worker.service` from `/opt/creative-asset-manager/current`.
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

## Backend

```bash
sudo scripts/cam-rebuild-backend.sh
sudo scripts/cam-rebuild-backend.sh --commit SHA
sudo scripts/cam-rebuild-backend.sh --rollback
```

The backend script creates an immutable native release under `/opt/creative-asset-manager/releases/<commit>`, creates the API virtualenv, validates the root-owned environment without printing values, verifies one Alembic head, runs only `alembic upgrade head`, atomically switches `current`, and restarts native API/image/video services. It never builds frontend files and never uses Docker for API or workers.

## One-time split-worker migration

The legacy all-role worker must be inactive before both split workers are enabled:

```bash
sudo systemctl stop creative-asset-manager-worker.service
sudo systemctl disable creative-asset-manager-worker.service
sudo systemctl enable --now creative-asset-manager-image-worker.service
sudo systemctl enable --now creative-asset-manager-video-worker.service
```

The image worker has `WORKER_ROLE=image` and health port 8081. The video worker has `WORKER_ROLE=video` and health port 8082. Both use the same PostgreSQL processing queue and policy accounting.

## Verification

```bash
systemctl is-active creative-asset-manager-api.service
systemctl is-active creative-asset-manager-image-worker.service
systemctl is-active creative-asset-manager-video-worker.service
systemctl is-active creative-asset-manager-worker.service

sudo journalctl -u creative-asset-manager-image-worker.service -f
sudo journalctl -u creative-asset-manager-video-worker.service -f
curl --fail --silent http://127.0.0.1:9200/_cluster/health
```

Expected: API, image worker, and video worker are active; the legacy worker is inactive.

## Rollback

```bash
sudo scripts/deploy-cam-frontend.sh --rollback
sudo scripts/cam-rebuild-backend.sh --rollback
```

Backend rollback does not execute an Alembic downgrade. Schema compatibility with the previous application release remains required. Neither deployment script deletes processing jobs, PostgreSQL data, Elasticsearch data, or search aliases.
