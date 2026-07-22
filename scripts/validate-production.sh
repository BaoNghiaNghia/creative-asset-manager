#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
PREFLIGHT=false

while (($#)); do
  case "$1" in
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    --preflight) PREFLIGHT=true; shift ;;
    -h|--help) printf '%s\n' "Usage: $0 [--preflight] [path overrides]"; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

for command in docker curl stat; do require_command "$command"; done
[[ -f "$ENV_FILE" ]] || deploy_die "Production env file is missing."
[[ -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml" ]] || deploy_die "Compose file is missing."
verify_frontend_dist "$REPO_ROOT/apps/client/dist"
scan_frontend_dist "$REPO_ROOT/apps/client/dist"

MODE="$(stat -c '%a' "$ENV_FILE")"
[[ "$MODE" == "600" || "$MODE" == "640" ]] || deploy_die "Production env must have mode 0600 or 0640."

APP_ENV="$(read_env_value "$ENV_FILE" APP_ENV)"
PUBLIC_URL="$(read_env_value "$ENV_FILE" PUBLIC_APP_URL)"
DATABASE_URL="$(read_env_value "$ENV_FILE" DATABASE_URL)"
COOKIE_SECURE="$(read_env_value "$ENV_FILE" AUTH_COOKIE_SECURE)"
PERSISTENT_AUTH="$(read_env_value "$ENV_FILE" PERSISTENT_AUTH_ENABLED)"
DEV_TENANT="$(read_env_value "$ENV_FILE" DEVELOPMENT_PERSONAL_TENANT_ENABLED)"
LEGACY_ADMIN="$(read_env_value "$ENV_FILE" AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED)"
[[ "$APP_ENV" == "production" ]] || deploy_die "APP_ENV must be production."
[[ "$PUBLIC_URL" == https://* ]] || deploy_die "PUBLIC_APP_URL must use HTTPS."
[[ "$DATABASE_URL" == postgresql+psycopg://*host.docker.internal:5432/* ]] || deploy_die "DATABASE_URL must use native PostgreSQL through host.docker.internal."
[[ "$DATABASE_URL" != *sqlite* ]] || deploy_die "SQLite is forbidden in production."
[[ "$COOKIE_SECURE" == "true" ]] || deploy_die "Secure cookies must be enabled."
[[ "$PERSISTENT_AUTH" == "true" ]] || deploy_die "Persistent RBAC authentication must be enabled."
[[ "$DEV_TENANT" == "false" ]] || deploy_die "Development tenant bootstrap must be disabled."
[[ "$LEGACY_ADMIN" == "false" ]] || deploy_die "Legacy admin allowlist compatibility must be disabled."

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --no-deps api python -c \
  "from app.core.config import get_settings; s=get_settings(); assert s.APP_ENV == 'production'"
"${COMPOSE[@]}" run --rm --no-deps api python -c \
  "from app.core.database import validate_database_connection; validate_database_connection()"
"${COMPOSE[@]}" run --rm --no-deps api python -m alembic current | grep -q '(head)' \
  || deploy_die "Database revision is not at Alembic head."

if [[ "$PREFLIGHT" == true ]]; then
  printf '%s\n' "Production preflight passed."
  exit 0
fi

curl --fail --silent --show-error --max-time 10 http://127.0.0.1:9200/_cluster/health >/dev/null
for path in live ready version; do
  curl --fail --silent --show-error --max-time 10 "http://127.0.0.1:8000/$path" >/dev/null
done
"${COMPOSE[@]}" ps --status running --services | grep -qx api
"${COMPOSE[@]}" ps --status running --services | grep -qx worker
"${COMPOSE[@]}" ps --status running --services | grep -qx elasticsearch
verify_frontend_dist "$FRONTEND_ROOT/current"
scan_frontend_dist "$FRONTEND_ROOT/current"
sudo -n nginx -t
printf '%s\n' "Production validation passed; no secret values were printed."
