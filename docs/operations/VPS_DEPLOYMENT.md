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
- Native systemd runs the API, exactly three image workers, the video worker, the visual worker, and the isolated visual encoder from `/opt/creative-asset-manager/current`. Image worker units 4 and 5 are retained for a reviewed future scale-up but are stopped and disabled by the default deployment profile.
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

The backend script runs disk cleanup before and after deployment, creates an immutable native release under `/opt/creative-asset-manager/releases/<commit>`, creates the API virtualenv, validates the root-owned environment without printing values, verifies one Alembic head, runs only `alembic upgrade head`, atomically switches `current`, and restarts native API/image/video services. Cleanup removes interrupted staging directories, expires old deployment logs, and prunes old standard or `SHA-suffix` releases while always preserving `current`, `previous`, the requested target, and any release still used by a running backend service. Configure retention with `--keep-releases`, `--keep-logs`, `CAM_BACKEND_RELEASE_KEEP`, and `CAM_BACKEND_DEPLOY_LOG_KEEP`; use `--no-cleanup` only for diagnostics. It never builds frontend files and never uses Docker for API or workers.

## One-time split-worker migration

The legacy all-role worker and optional image workers 4 and 5 must be inactive before the default three-image-worker profile is enabled:

```bash
sudo systemctl stop creative-asset-manager-worker.service
sudo systemctl disable creative-asset-manager-worker.service
sudo systemctl disable --now creative-asset-manager-image-worker-4.service
sudo systemctl disable --now creative-asset-manager-image-worker-5.service
sudo systemctl enable --now creative-asset-manager-image-worker.service
sudo systemctl enable --now creative-asset-manager-image-worker-2.service
sudo systemctl enable --now creative-asset-manager-image-worker-3.service
sudo systemctl enable --now creative-asset-manager-video-worker.service
```

The image worker has `WORKER_ROLE=image` and health port 8081. The video worker has `WORKER_ROLE=video` and health port 8082. Both use the same PostgreSQL processing queue and policy accounting.

## Verification

```bash
systemctl is-active creative-asset-manager-api.service
systemctl is-active creative-asset-manager-image-worker.service
systemctl is-active creative-asset-manager-image-worker-2.service
systemctl is-active creative-asset-manager-image-worker-3.service
systemctl is-active creative-asset-manager-image-worker-4.service
systemctl is-active creative-asset-manager-image-worker-5.service
systemctl is-active creative-asset-manager-video-worker.service
systemctl is-active creative-asset-manager-worker.service

sudo journalctl -u creative-asset-manager-image-worker.service -f
sudo journalctl -u creative-asset-manager-video-worker.service -f
curl --fail --silent http://127.0.0.1:9200/_cluster/health
```

Expected: API, image workers 1-3, video worker, visual worker, and visual encoder are active. Image workers 4-5 and the legacy all-role worker are inactive.

## Rollback

```bash
sudo scripts/deploy-cam-frontend.sh --rollback
sudo scripts/cam-rebuild-backend.sh --rollback
```

Backend rollback does not execute an Alembic downgrade. Schema compatibility with the previous application release remains required. Neither deployment script deletes processing jobs, PostgreSQL data, Elasticsearch data, or search aliases.
