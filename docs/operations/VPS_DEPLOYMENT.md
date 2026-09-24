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
sudo systemctl enable --now creative-asset-manager-video-delivery-worker.service
```

The image worker has `WORKER_ROLE=image` and health port 8081. The heavy-video worker keeps the historical service name `creative-asset-manager-video-worker.service`, uses `WORKER_ROLE=video-heavy`, and listens on health port 8082. It claims only `video_analyze` and `video_generate`. The delivery worker uses `WORKER_ROLE=video-delivery`, health port 8088, and claims `video_search_index`, `video_cache_fill`, and `video_playback_prepare`. This prevents long Gemini/FFmpeg/generation work from blocking review playback and CDN cache jobs. All workers use the same PostgreSQL processing queue and policy accounting.

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

Expected: API, image workers 1-3, heavy-video worker, video-delivery worker, visual worker, and visual encoder are active. Image workers 4-5 and the legacy all-role worker are inactive.

## Rollback

```bash
sudo scripts/deploy-cam-frontend.sh --rollback
sudo scripts/cam-rebuild-backend.sh --rollback
```

Backend rollback does not execute an Alembic downgrade. Schema compatibility with the previous application release remains required. Neither deployment script deletes processing jobs, PostgreSQL data, Elasticsearch data, or search aliases.
