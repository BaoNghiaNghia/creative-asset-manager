#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

[[ "${EUID}" -eq 0 ]] || { echo "Run as root: sudo -E $0"; exit 1; }

: "${CAM_DOMAIN:?Set CAM_DOMAIN, e.g. assets.example.com}"
: "${CAM_EMAIL:?Set CAM_EMAIL for Let's Encrypt}"
REPO_URL="${CAM_REPO_URL:-https://github.com/BaoNghiaNghia/creative-asset-manager.git}"
SOURCE_DIR="${CAM_SOURCE_DIR:-/srv/creative-asset-manager-source}"
ENV_FILE="/etc/creative-asset-manager/production.env"
DB_NAME="${CAM_DB_NAME:-creative_asset_manager}"
DB_USER="${CAM_DB_USER:-cam_app}"

require() { command -v "$1" >/dev/null 2>&1 || { echo "Missing command: $1"; exit 2; }; }
for cmd in git python3 node npm docker psql pg_isready nginx certbot ffmpeg ffprobe openssl curl; do require "$cmd"; done
docker compose version >/dev/null

echo "[1/10] Checkout latest main"
if [[ -d "${SOURCE_DIR}/.git" ]]; then
  git -C "${SOURCE_DIR}" fetch origin main
  [[ -z "$(git -C "${SOURCE_DIR}" status --porcelain)" ]] || {
    echo "ERROR: source checkout is dirty: ${SOURCE_DIR}"; exit 3;
  }
  git -C "${SOURCE_DIR}" checkout main
  git -C "${SOURCE_DIR}" merge --ff-only origin/main
else
  rm -rf "${SOURCE_DIR}"
  git clone --branch main --single-branch "${REPO_URL}" "${SOURCE_DIR}"
fi
HEAD_SHA="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
RELEASE_ID="${HEAD_SHA:0:12}"
echo "HEAD=${HEAD_SHA}"
echo "RELEASE_ID=${RELEASE_ID}"

echo "[2/10] PostgreSQL"
DB_PASSWORD="${CAM_DB_PASSWORD:-$(openssl rand -hex 24)}"
sudo -u postgres psql -v ON_ERROR_STOP=1 --set=db_user="${DB_USER}" --set=db_name="${DB_NAME}" --set=db_pass="${DB_PASSWORD}" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN', :'db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'db_user') \gexec
SELECT format('ALTER ROLE %I PASSWORD %L', :'db_user', :'db_pass') \gexec
SELECT format('CREATE DATABASE %I OWNER %I', :'db_name', :'db_user')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = :'db_name') \gexec
SELECT format('ALTER DATABASE %I OWNER TO %I', :'db_name', :'db_user') \gexec
SQL

sudo -u postgres psql -v ON_ERROR_STOP=1 -c "ALTER SYSTEM SET listen_addresses = '127.0.0.1';" >/dev/null
HBA_FILE="$(sudo -u postgres psql -tAc 'show hba_file' | xargs)"
HBA_LINE="host ${DB_NAME} ${DB_USER} 127.0.0.1/32 scram-sha-256"
grep -Fqx "${HBA_LINE}" "${HBA_FILE}" || echo "${HBA_LINE}" >>"${HBA_FILE}"
systemctl restart postgresql
pg_isready -q -h 127.0.0.1 -p 5432

echo "[3/10] Production environment"
if [[ ! -f "${ENV_FILE}" ]]; then
  install -o root -g creative-assets -m 0640 \
    "${SOURCE_DIR}/deploy/production.env.example" "${ENV_FILE}"
fi

OAUTH_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
CREATIVE_KEY="$(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(32))
PY
)"
DB_URL="postgresql+psycopg://${DB_USER}:${DB_PASSWORD}@127.0.0.1:5432/${DB_NAME}"

python3 - "${ENV_FILE}" "${CAM_DOMAIN}" "${HEAD_SHA}" "${DB_URL}" "${OAUTH_KEY}" "${CREATIVE_KEY}" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
domain, head, db_url, oauth_key, creative_key = sys.argv[2:]
updates = {
    "APP_ENV": "production",
    "PUBLIC_APP_URL": f"https://{domain}",
    "CORS_ALLOWED_ORIGINS": f"https://{domain}",
    "TRUSTED_HOSTS": domain,
    "API_DOCS_ENABLED": "false",
    "BUILD_COMMIT": head,
    "PROXY_HEADERS_ENABLED": "true",
    "PROXY_TRUSTED_IPS": "127.0.0.1/32",
    "DATABASE_URL": db_url,
    "ELASTICSEARCH_URL": "http://127.0.0.1:9200",
    "AUTH_COOKIE_SECURE": "true",
    "OAUTH_ACTIVE_KEY_VERSION": "v1",
    "GOOGLE_REDIRECT_URI": f"https://{domain}/api/auth/google/callback",
    "VIDEO_TEMP_DIRECTORY": "/var/lib/creative-asset-manager/video-proxy",
}
lines = path.read_text().splitlines()
current = {}
for raw in lines:
    if raw and not raw.lstrip().startswith("#") and "=" in raw:
        k, v = raw.split("=", 1)
        current[k.strip()] = v.strip()

if not current.get("OAUTH_TOKEN_ENCRYPTION_KEYS"):
    updates["OAUTH_TOKEN_ENCRYPTION_KEYS"] = f"v1:{oauth_key}"
if not current.get("CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY"):
    updates["CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY"] = creative_key

seen = set()
out = []
for raw in lines:
    if raw and not raw.lstrip().startswith("#") and "=" in raw:
        k = raw.split("=", 1)[0].strip()
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(raw)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
path.write_text("\n".join(out) + "\n")
PY
chown root:creative-assets "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

echo "[4/10] Elasticsearch only"
docker compose \
  --env-file "${ENV_FILE}" \
  --file "${SOURCE_DIR}/infrastructure/docker/docker-compose.prod.yml" \
  up -d elasticsearch

for _ in $(seq 1 40); do
  if curl --fail --silent 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=3s' >/dev/null; then
    break
  fi
  sleep 2
done
curl --fail --silent 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s' >/dev/null

echo "[5/10] Immutable release"
cd "${SOURCE_DIR}"
deploy/bin/cam-deploy install-release "${SOURCE_DIR}" "${RELEASE_ID}"
deploy/bin/cam-deploy check-config "${RELEASE_ID}"
deploy/bin/cam-deploy verify-alembic-head "${RELEASE_ID}"
deploy/bin/cam-deploy migrate "${RELEASE_ID}"
deploy/bin/cam-deploy seed "${RELEASE_ID}"
deploy/bin/cam-deploy switch-release "${RELEASE_ID}"

echo "[6/10] systemd units"
install -o root -g root -m 0644 \
  "${SOURCE_DIR}/deploy/systemd/creative-asset-manager-api.service" \
  /etc/systemd/system/creative-asset-manager-api.service
install -o root -g root -m 0644 \
  "${SOURCE_DIR}/deploy/systemd/creative-asset-manager-worker.service" \
  /etc/systemd/system/creative-asset-manager-worker.service
systemctl daemon-reload
systemctl enable creative-asset-manager-api.service creative-asset-manager-worker.service

echo "[7/10] Temporary HTTP site for ACME"
install -d -o root -g root -m 0755 /var/www/letsencrypt
cat >/etc/nginx/sites-available/creative-asset-manager-bootstrap.conf <<EOF
server {
    listen 80;
    listen [::]:80;
    server_name ${CAM_DOMAIN};
    location ^~ /.well-known/acme-challenge/ {
        root /var/www/letsencrypt;
    }
    location / {
        return 200 "Creative Asset Manager bootstrap\n";
        add_header Content-Type text/plain;
    }
}
EOF
ln -sfn /etc/nginx/sites-available/creative-asset-manager-bootstrap.conf \
  /etc/nginx/sites-enabled/creative-asset-manager-bootstrap.conf
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl reload nginx

echo "[8/10] TLS certificate"
certbot certonly --webroot \
  -w /var/www/letsencrypt \
  -d "${CAM_DOMAIN}" \
  --email "${CAM_EMAIL}" \
  --agree-tos \
  --non-interactive \
  --keep-until-expiring

echo "[9/10] Production Nginx site"
sed "s/assets\.example\.com/${CAM_DOMAIN//\//\\/}/g" \
  "${SOURCE_DIR}/infrastructure/nginx/creative-asset-manager.conf" \
  >/etc/nginx/sites-available/creative-asset-manager.conf
ln -sfn /etc/nginx/sites-available/creative-asset-manager.conf \
  /etc/nginx/sites-enabled/creative-asset-manager.conf
rm -f /etc/nginx/sites-enabled/creative-asset-manager-bootstrap.conf
nginx -t
systemctl reload nginx

echo "[10/10] Start + verify"
deploy/bin/cam-deploy restart-api
deploy/bin/cam-deploy restart-worker
deploy/bin/cam-deploy verify-api
deploy/bin/cam-deploy verify-worker
deploy/bin/cam-deploy diagnostics
curl --fail --silent "https://${CAM_DOMAIN}/live" >/dev/null

echo
echo "FIRST_DEPLOY=PASS"
echo "MAIN_HEAD=${HEAD_SHA}"
echo "RELEASE_ID=${RELEASE_ID}"
echo "ENV_FILE=${ENV_FILE}"
echo
echo "Next: sudoedit ${ENV_FILE}"
echo "Fill GOOGLE_CLIENT_ID / GOOGLE_CLIENT_SECRET and managed-storage / Gemini credentials if used."
echo "Generated DB/OAuth secrets were written to ${ENV_FILE} and were not printed."
