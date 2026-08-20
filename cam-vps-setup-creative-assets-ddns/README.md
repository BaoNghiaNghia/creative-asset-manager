# Creative Asset Manager — New VPS quick setup

Architecture used by these helpers:

- Nginx: native
- API + worker: native systemd
- PostgreSQL: native loopback `127.0.0.1:5432`
- Elasticsearch: Docker Compose, start only `elasticsearch`
- source checkout: `/srv/creative-asset-manager-source`
- immutable releases under `/opt/creative-asset-manager`
- static frontend releases under `/var/www/creative-asset-manager`

## 1. Bootstrap

```bash
chmod +x *.sh
sudo ./00-host-bootstrap.sh
```

## 2. DNS

Domain production đã được cấu hình mặc định:

```text
https://creative-assets.ddns.net/
```

Hostname mà Nginx/Certbot sử dụng:

```text
creative-assets.ddns.net
```

Trước khi deploy, kiểm tra domain đang trỏ về đúng public IP của VPS:

```bash
getent ahosts creative-assets.ddns.net
curl -4 https://ifconfig.me
```

Nếu dùng IPv6/AAAA record, đảm bảo IPv6 cũng trỏ đúng VPS hoặc xóa AAAA sai trước khi cấp TLS.

## 3. First deploy

```bash
sudo -E env \
  CAM_EMAIL=you@example.com \
  ./01-first-deploy.sh
```

`CAM_DOMAIN` mặc định là `creative-assets.ddns.net`. Script cũng chấp nhận cả `creative-assets.ddns.net` lẫn `https://creative-assets.ddns.net/` nếu bạn override.

Optional custom DB password:


```bash
sudo -E env \
  CAM_EMAIL=you@example.com \
  CAM_DB_PASSWORD='url-safe-password' \
  ./01-first-deploy.sh
```

## 4. Configure Google / Gemini

```bash
sudoedit /etc/creative-asset-manager/production.env
```

For Google login / Drive, fill at least:

- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

For managed Google storage, when used:

- `GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN`
- `GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID`

Restart after env changes:

```bash
cd /srv/creative-asset-manager-source
sudo deploy/bin/cam-deploy restart-api
sudo deploy/bin/cam-deploy restart-worker
sudo deploy/bin/cam-deploy verify-api
sudo deploy/bin/cam-deploy verify-worker
```

## 5. Future update from main

```bash
sudo ./02-update-main.sh
```

## 6. Health

```bash
./03-health.sh
```

## Current-source networking note

The repository deployment runbook uses native API/worker + native PostgreSQL, while the current `production.env.example` contains Docker-oriented networking examples.

For the native deployment these helpers intentionally use:

```env
PROXY_TRUSTED_IPS=127.0.0.1/32
DATABASE_URL=postgresql+psycopg://cam_app:...@127.0.0.1:5432/creative_asset_manager
ELASTICSEARCH_URL=http://127.0.0.1:9200
```

The current Compose file also contains `api` and `worker` services. These helpers therefore run only:

```bash
docker compose ... up -d elasticsearch
```

Never use bare `docker compose up -d` for this native setup.

## Firewall

These helpers do not modify your firewall because the SSH port is unknown. Expose only:

- your SSH port
- TCP 80
- TCP 443

Keep 8000, 8081, 5432 and 9200 loopback-only.

## Rollback

```bash
cd /srv/creative-asset-manager-source
sudo deploy/bin/cam-deploy rollback-release
```

This never performs `alembic downgrade`.


## Domain-related production env

Sau first deploy, các giá trị chính phải là:

```env
PUBLIC_APP_URL=https://creative-assets.ddns.net
CORS_ALLOWED_ORIGINS=https://creative-assets.ddns.net
TRUSTED_HOSTS=creative-assets.ddns.net
GOOGLE_REDIRECT_URI=https://creative-assets.ddns.net/api/auth/google/callback
```

Google OAuth Console cũng phải có redirect URI chính xác:

```text
https://creative-assets.ddns.net/api/auth/google/callback
```
