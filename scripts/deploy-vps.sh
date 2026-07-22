#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
BRANCH=""
COMMIT=""
KEEP_RELEASES=5
HEALTH_ATTEMPTS=40
SWITCHED=false
PREVIOUS_FRONTEND=""

usage() {
  printf '%s\n' "Usage: $0 (--branch NAME | --commit SHA) [--project-root PATH] [--frontend-root PATH] [--env-file PATH]"
}

cleanup() {
  if [[ "$SWITCHED" == true && -n "$PREVIOUS_FRONTEND" && -d "$PREVIOUS_FRONTEND" ]]; then
    printf '%s\n' "Deployment failed after frontend switch; restoring previous frontend." >&2
    local restore_link="$FRONTEND_ROOT/current.restore.$$"
    sudo ln -s "$PREVIOUS_FRONTEND" "$restore_link"
    sudo mv -Tf "$restore_link" "$FRONTEND_ROOT/current"
    sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx || true
  fi
}
trap cleanup ERR

while (($#)); do
  case "$1" in
    --branch) BRANCH="${2:?missing branch}"; shift 2 ;;
    --commit) COMMIT="${2:?missing commit}"; shift 2 ;;
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    --keep-releases) KEEP_RELEASES="${2:?missing count}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
[[ "$(id -un)" == "desify" ]] || printf '%s\n' "WARNING: intended production user is desify." >&2
[[ -n "$BRANCH" || -n "$COMMIT" ]] || deploy_die "Specify --branch or --commit."
[[ -z "$BRANCH" || -z "$COMMIT" ]] || deploy_die "Choose only one Git reference."
[[ "$KEEP_RELEASES" =~ ^[1-9][0-9]*$ ]] || deploy_die "--keep-releases must be positive."
for command in git docker curl rsync sudo; do require_command "$command"; done
[[ -f "$ENV_FILE" ]] || deploy_die "Production env file is missing."
[[ -d "$REPO_ROOT/.git" ]] || deploy_die "Repository not found: $REPO_ROOT"

cd "$REPO_ROOT"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || deploy_die "Repository must be clean."
git fetch --prune origin
if [[ -n "$BRANCH" ]]; then
  if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git switch "$BRANCH"
  else
    git switch --track -c "$BRANCH" "origin/$BRANCH"
  fi
  git merge --ff-only "origin/$BRANCH"
else
  safe_release_id "$COMMIT"
  git cat-file -e "$COMMIT^{commit}" 2>/dev/null || git fetch origin "$COMMIT"
  git switch --detach "$COMMIT"
fi
RELEASE_COMMIT="$(git rev-parse --verify HEAD)"
safe_release_id "$RELEASE_COMMIT"

DIST="$REPO_ROOT/apps/client/dist"
verify_frontend_dist "$DIST"
scan_frontend_dist "$DIST"
printf 'Deploying commit %s with committed frontend artifact.\n' "$RELEASE_COMMIT"

COMPOSE_FILE="$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml"
export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="$RELEASE_COMMIT"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build api

printf '%s\n' "Validating production application configuration..."
"${COMPOSE[@]}" run --rm --no-deps api python -c \
  "from app.core.config import get_settings; s=get_settings(); assert s.APP_ENV == 'production'"

printf '%s\n' "Checking native PostgreSQL from an ephemeral API container..."
"${COMPOSE[@]}" run --rm --no-deps api python -c \
  "from app.core.database import validate_database_connection; validate_database_connection()"

printf '%s\n' "Running Alembic migration as a one-shot container..."
"${COMPOSE[@]}" --profile migration run --rm migration

"${COMPOSE[@]}" up -d elasticsearch
"${COMPOSE[@]}" up -d --no-build api worker

api_healthy=false
for ((attempt=1; attempt<=HEALTH_ATTEMPTS; attempt++)); do
  if curl --fail --silent --show-error --max-time 5 http://127.0.0.1:8000/ready >/dev/null; then
    api_healthy=true
    break
  fi
  sleep 2
done
[[ "$api_healthy" == true ]] || deploy_die "API readiness failed; frontend was not switched."

"${COMPOSE[@]}" exec -T worker python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=5).read()"

RELEASES_DIR="$FRONTEND_ROOT/releases"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_COMMIT"
sudo install -d -m 0755 "$FRONTEND_ROOT" "$RELEASES_DIR"
if [[ ! -d "$RELEASE_DIR" ]]; then
  sudo install -d -m 0755 "$RELEASE_DIR"
  sudo rsync -a --delete --chmod=D755,F644 "$DIST/" "$RELEASE_DIR/"
fi
verify_frontend_dist "$RELEASE_DIR"
if [[ -L "$FRONTEND_ROOT/current" ]]; then
  PREVIOUS_FRONTEND="$(readlink -f "$FRONTEND_ROOT/current")"
fi
sudo nginx -t
TEMP_LINK="$FRONTEND_ROOT/current.new.$$"
sudo ln -s "$RELEASE_DIR" "$TEMP_LINK"
sudo mv -Tf "$TEMP_LINK" "$FRONTEND_ROOT/current"
SWITCHED=true
sudo nginx -t
sudo systemctl reload nginx

PUBLIC_URL="$(read_env_value "$ENV_FILE" PUBLIC_APP_URL)"
[[ "$PUBLIC_URL" == https://* ]] || deploy_die "PUBLIC_APP_URL must use HTTPS."
for path in / /ai-operations /live /ready /version; do
  curl --fail --silent --show-error --max-time 15 "$PUBLIC_URL$path" >/dev/null
done

CURRENT_RELEASE="$(readlink -f "$FRONTEND_ROOT/current")"
mapfile -t OLD_RELEASES < <(find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' | sort -rn | tail -n "+$((KEEP_RELEASES + 1))" | cut -d' ' -f2-)
for old_release in "${OLD_RELEASES[@]}"; do
  resolved="$(readlink -f "$old_release")"
  [[ "$resolved" == "$RELEASES_DIR/"* ]] || deploy_die "Refusing to prune outside release root."
  [[ "$resolved" == "$CURRENT_RELEASE" ]] || sudo rm -rf -- "$resolved"
done

SWITCHED=false
printf 'Deployment complete: %s\n' "$RELEASE_COMMIT"
printf 'Rollback: sudo -u desify %s/rollback-vps.sh --commit PREVIOUS_COMMIT\n' "$SCRIPT_DIR"
