#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
ALLOW_USER=""
CONFIG_ONLY=false
PREFLIGHT=false

while (($#)); do
  case "$1" in
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    --allow-user) ALLOW_USER="${2:?missing user}"; shift 2 ;;
    --config-only) CONFIG_ONLY=true; shift ;;
    --preflight) PREFLIGHT=true; shift ;;
    -h|--help)
      printf '%s\n' "Usage: $0 [--config-only | --preflight] [--allow-user USER] [path overrides]"
      exit 0
      ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
require_deployment_user "desify" "$ALLOW_USER"
for command in docker curl stat grep git; do
  require_command "$command"
done
validate_production_env_file "$ENV_FILE"
[[ -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml" ]] || deploy_die "Compose file is missing."
verify_frontend_dist "$REPO_ROOT/apps/client/dist"
scan_frontend_dist "$REPO_ROOT/apps/client/dist"

if [[ "$CONFIG_ONLY" == true ]]; then
  printf '%s\n' "Production environment and committed frontend validation passed."
  exit 0
fi

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="$(git -C "$REPO_ROOT" rev-parse HEAD)"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" run --rm --no-deps api python -c "from app.core.config import get_settings; s=get_settings(); assert s.APP_ENV == 'production'"
"${COMPOSE[@]}" run --rm --no-deps api python -c "from app.core.database import validate_database_connection; validate_database_connection()"
if ! "${COMPOSE[@]}" run --rm --no-deps api python -m alembic current | grep -q '(head)'; then
  deploy_die "Database revision is not at Alembic head."
fi

if [[ "$PREFLIGHT" == true ]]; then
  printf '%s\n' "Production preflight passed."
  exit 0
fi

curl --fail --silent --show-error --max-time 10 'http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=5s' >/dev/null
wait_for_api_release "http://127.0.0.1:8000" "$BUILD_COMMIT" 5
"${COMPOSE[@]}" ps --status running --services | grep -qx api
"${COMPOSE[@]}" ps --status running --services | grep -qx worker
"${COMPOSE[@]}" ps --status running --services | grep -qx elasticsearch
verify_frontend_dist "$FRONTEND_ROOT/current"
scan_frontend_dist "$FRONTEND_ROOT/current"
sudo -n nginx -t
printf '%s\n' "Production validation passed; no secret values were printed."
