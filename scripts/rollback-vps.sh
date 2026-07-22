#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
COMMIT=""

while (($#)); do
  case "$1" in
    --commit) COMMIT="${2:?missing commit}"; shift 2 ;;
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    -h|--help) printf '%s\n' "Usage: $0 [--commit SHA] [path overrides]"; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
for command in git docker curl sudo; do require_command "$command"; done
[[ -f "$ENV_FILE" ]] || deploy_die "Production env file is missing."
cd "$REPO_ROOT"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || deploy_die "Repository must be clean."

if [[ -z "$COMMIT" ]]; then
  CURRENT="$(readlink -f "$FRONTEND_ROOT/current")"
  mapfile -t RELEASES < <(find "$FRONTEND_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' | sort -rn | cut -d' ' -f2-)
  for candidate in "${RELEASES[@]}"; do
    if [[ "$FRONTEND_ROOT/releases/$candidate" != "$CURRENT" ]]; then COMMIT="$candidate"; break; fi
  done
fi
[[ -n "$COMMIT" ]] || deploy_die "No previous release is available."
safe_release_id "$COMMIT"
RELEASE_DIR="$FRONTEND_ROOT/releases/$COMMIT"
verify_frontend_dist "$RELEASE_DIR"
scan_frontend_dist "$RELEASE_DIR"

git fetch --prune origin
git cat-file -e "$COMMIT^{commit}" 2>/dev/null || git fetch origin "$COMMIT"
git switch --detach "$COMMIT"
[[ "$(git rev-parse HEAD)" == "$COMMIT" ]] || COMMIT="$(git rev-parse HEAD)"

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="$COMMIT"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build api
printf '%s\n' "WARNING: database migrations are not downgraded; verify schema compatibility."
"${COMPOSE[@]}" up -d --no-build api worker

healthy=false
for _ in {1..40}; do
  if curl --fail --silent --max-time 5 http://127.0.0.1:8000/ready >/dev/null; then healthy=true; break; fi
  sleep 2
done
[[ "$healthy" == true ]] || deploy_die "Rollback backend failed readiness; frontend was not switched."

sudo nginx -t
TEMP_LINK="$FRONTEND_ROOT/current.rollback.$$"
sudo ln -s "$RELEASE_DIR" "$TEMP_LINK"
sudo mv -Tf "$TEMP_LINK" "$FRONTEND_ROOT/current"
sudo nginx -t
sudo systemctl reload nginx

PUBLIC_URL="$(read_env_value "$ENV_FILE" PUBLIC_APP_URL)"
for path in / /ai-operations /live /ready /version; do
  curl --fail --silent --show-error --max-time 15 "$PUBLIC_URL$path" >/dev/null
done
printf 'Rollback complete at commit %s. PostgreSQL and Elasticsearch data were preserved.\n' "$COMMIT"
