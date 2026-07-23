#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH='' cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/deployment-common.sh
source "$SCRIPT_DIR/lib/deployment-common.sh"

ENV_FILE=""
KEEP_SERVICES=false
NGINX_CONFIG=""
ARTIFACT_DIR=""
COMPOSE=()
NGINX_TMP=""

usage() {
  printf '%s\n' "Usage: $0 --env-file FILE [--project-root DIR] [--keep-services]"
}

while (($#)); do
  case "$1" in
    --env-file) ENV_FILE="${2:?missing environment file}"; shift 2 ;;
    --project-root) PROJECT_ROOT="${2:?missing project root}"; shift 2 ;;
    --keep-services) KEEP_SERVICES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

[[ -n "$ENV_FILE" ]] || deploy_die "--env-file is required."
PROJECT_ROOT="$(CDPATH='' cd -- "$PROJECT_ROOT" && pwd)"
NGINX_CONFIG="$PROJECT_ROOT/infrastructure/nginx/creative-asset-manager.conf"
ARTIFACT_DIR="$PROJECT_ROOT/artifacts/production-gate"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  local status=$?
  if [[ -n "$NGINX_TMP" ]]; then
    rm -rf "$NGINX_TMP"
  fi
  if (("${#COMPOSE[@]}" > 0)); then
    "${COMPOSE[@]}" ps >"$ARTIFACT_DIR/compose-ps.txt" 2>&1 || true
    if [[ "$KEEP_SERVICES" != true ]]; then
      "${COMPOSE[@]}" down --volumes --remove-orphans >/dev/null 2>&1 || true
    fi
  fi
  if ((status != 0)); then
    printf 'Production release gate failed (exit %s). Production is not ready.\n' "$status" >&2
  fi
}
trap cleanup EXIT
trap 'exit 130' INT TERM

require_non_root
for command in curl docker git grep nginx openssl sed stat; do
  require_command "$command"
done

if docker compose version >/dev/null 2>&1; then
  COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$PROJECT_ROOT/infrastructure/docker/docker-compose.prod.yml")
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose --env-file "$ENV_FILE" -f "$PROJECT_ROOT/infrastructure/docker/docker-compose.prod.yml")
else
  deploy_die "Docker Compose v2 is required."
fi

validate_production_env_file "$ENV_FILE"
verify_frontend_dist "$PROJECT_ROOT/apps/client/dist"
scan_frontend_dist "$PROJECT_ROOT/apps/client/dist" >/dev/null
GATE_HOST="$(read_env_value "$ENV_FILE" TRUSTED_HOSTS)"
GATE_HOST="${GATE_HOST%%,*}"

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="${BUILD_COMMIT:-$(git -C "$PROJECT_ROOT" rev-parse HEAD)}"
export CAM_BACKEND_IMAGE="${CAM_BACKEND_IMAGE:-cam-production-gate}"

printf '%s\n' "Validating Docker Compose topology..."
"${COMPOSE[@]}" config --quiet
SERVICES="$("${COMPOSE[@]}" --profile migration config --services)"
for required in api worker migrate elasticsearch; do
  printf '%s\n' "$SERVICES" | grep -qx "$required" \
    || deploy_die "Compose service is missing: $required"
done
if printf '%s\n' "$SERVICES" | grep -Eq '^(postgres|postgresql|nginx|frontend)$'; then
  deploy_die "Production Compose must not contain PostgreSQL, Nginx or frontend services."
fi

IMAGE_REF="$CAM_BACKEND_IMAGE:$BUILD_COMMIT"
printf 'Building immutable backend image %s...\n' "$IMAGE_REF"
docker build \
  --file "$PROJECT_ROOT/infrastructure/docker/backend.Dockerfile" \
  --tag "$IMAGE_REF" \
  "$PROJECT_ROOT"

IMAGE_USER="$(docker image inspect --format '{{.Config.User}}' "$IMAGE_REF")"
[[ "$IMAGE_USER" == "10001:10001" ]] \
  || deploy_die "Backend image must run as 10001:10001."

if docker history --no-trunc "$IMAGE_REF" | grep -Eq \
  '(postgresql[+]psycopg://[^[:space:]]+:[^[:space:]]+@|sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY)'; then
  deploy_die "Secret-like material was found in backend image history."
fi

printf '%s\n' "Checking backend imports and Alembic command..."
"${COMPOSE[@]}" run --rm --no-deps api printenv APP_ENV PUBLIC_APP_URL CORS_ALLOWED_ORIGINS TRUSTED_HOSTS API_DOCS_ENABLED AUTH_COOKIE_SECURE
"${COMPOSE[@]}" run --rm --no-deps api \
  python -c "from app.main import app; assert app.title == 'Creative Asset Manager API'"
"${COMPOSE[@]}" run --rm --no-deps api \
  python -c "from app.core.config import get_settings; s=get_settings(); assert s.APP_ENV == 'production'; assert s.PERSISTENT_AUTH_ENABLED; assert not s.DEVELOPMENT_PERSONAL_TENANT_ENABLED; assert not s.AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED; assert not s.DATABASE_URL.startswith('sqlite'); assert '@host.docker.internal:5432/' in s.DATABASE_URL"
"${COMPOSE[@]}" run --rm --no-deps -e PYTHONPYCACHEPREFIX=/tmp/pycache api \
  python -c "from pathlib import Path; import compileall; assert compileall.compile_dir(Path('/app/apps/worker'), quiet=1)"
[[ "$("${COMPOSE[@]}" run --rm --no-deps api python -m alembic heads | grep -c '(head)')" -eq 1 ]] \
  || deploy_die "Alembic must have exactly one head."

printf '%s\n' "Running migration service against native PostgreSQL through host.docker.internal..."
"${COMPOSE[@]}" --profile migration run --rm migrate
"${COMPOSE[@]}" run --rm --no-deps api \
  python -c "from app.core.database import validate_database_connection; validate_database_connection()"
"${COMPOSE[@]}" run --rm --no-deps api python -m alembic current | grep -q '(head)' \
  || deploy_die "Migrated database is not at Alembic head."
"${COMPOSE[@]}" run --rm --no-deps api \
  python -c "from sqlalchemy import inspect; from app.core.database import engine; required={'users','user_identities','tenants','tenant_memberships','permissions','roles','role_permissions','membership_roles','platform_admin_assignments'}; missing=required-set(inspect(engine).get_table_names()); assert not missing, sorted(missing)"

printf '%s\n' "Starting Elasticsearch, API and worker..."
"${COMPOSE[@]}" up -d elasticsearch
"${COMPOSE[@]}" up -d --no-build api worker

api_ready=false
for _attempt in $(seq 1 60); do
  if curl --fail --silent --max-time 5 --header "Host: $GATE_HOST" http://127.0.0.1:8000/live >/dev/null &&
    curl --fail --silent --max-time 5 --header "Host: $GATE_HOST" http://127.0.0.1:8000/ready >/dev/null &&
    curl --fail --silent --max-time 5 --header "Host: $GATE_HOST" http://127.0.0.1:8000/version | grep -Fq "$BUILD_COMMIT"; then
    api_ready=true
    break
  fi
  sleep 2
done
[[ "$api_ready" == true ]] || deploy_die "API live/ready/version checks did not pass."

wait_for_worker() {
  local _attempt
  for _attempt in $(seq 1 45); do
    if "${COMPOSE[@]}" exec -T worker python -c \
      "import urllib.request; response=urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=5); raise SystemExit(0 if response.status == 200 else 1)" \
      >/dev/null 2>&1; then
      return 0
    fi
    sleep 2
  done
  return 1
}

wait_for_worker || deploy_die "Worker did not become live."
printf '%s\n' "Verifying graceful worker SIGTERM..."
"${COMPOSE[@]}" stop -t 45 worker
WORKER_ID="$("${COMPOSE[@]}" ps -a -q worker)"
[[ -n "$WORKER_ID" ]] || deploy_die "Worker container is missing after graceful stop."
[[ "$(docker inspect --format '{{.State.Running}}' "$WORKER_ID")" == "false" ]] \
  || deploy_die "Worker did not stop after SIGTERM."
"${COMPOSE[@]}" up -d --no-build worker
wait_for_worker || deploy_die "Worker did not recover after graceful restart."

printf '%s\n' "Validating native Nginx syntax with committed static assets..."
NGINX_TMP="$(mktemp -d)"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 \
  -subj /CN=assets.example.com \
  -keyout "$NGINX_TMP/privkey.pem" \
  -out "$NGINX_TMP/fullchain.pem" >/dev/null 2>&1
sed \
  -e '/listen \[::\]/d' \
  -e 's/listen 80;/listen 18080;/' \
  -e 's/listen 443 ssl http2;/listen 18443 ssl http2;/' \
  -e "s#ssl_certificate /etc/letsencrypt/live/assets.example.com/fullchain.pem;#ssl_certificate $NGINX_TMP/fullchain.pem;#" \
  -e "s#ssl_certificate_key /etc/letsencrypt/live/assets.example.com/privkey.pem;#ssl_certificate_key $NGINX_TMP/privkey.pem;#" \
  -e "s#root /var/www/creative-asset-manager/current;#root $PROJECT_ROOT/apps/client/dist;#" \
  "$NGINX_CONFIG" >"$NGINX_TMP/site.conf"
cat >"$NGINX_TMP/nginx.conf" <<EOF
pid $NGINX_TMP/nginx.pid;
error_log stderr notice;
events {}
http {
    include /etc/nginx/mime.types;
    access_log off;
    include $NGINX_TMP/site.conf;
}
EOF
nginx -t -p "$NGINX_TMP/" -c "$NGINX_TMP/nginx.conf" \
  2>&1 | tee "$ARTIFACT_DIR/nginx-test.log"
rm -rf "$NGINX_TMP"
NGINX_TMP=""

# The literal Nginx variables must not be expanded by this shell.
# shellcheck disable=SC2016
grep -q 'try_files \$uri \$uri/ /index.html;' "$NGINX_CONFIG" \
  || deploy_die "Nginx SPA fallback is missing."
grep -q 'location /api/' "$NGINX_CONFIG" \
  || deploy_die "Nginx API proxy is missing."

printf '%s\n' "Production release gate passed. No production credentials were used or printed."
