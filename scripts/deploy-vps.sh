#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
BRANCH=""
COMMIT=""
ALLOW_USER=""
KEEP_RELEASES=5
HEALTH_ATTEMPTS=40
SWITCHED=false
PREVIOUS_FRONTEND=""
TEMP_LINK=""
PREVIOUS_BACKEND_IMAGE=""
PREVIOUS_BACKEND_RUNNING=false

usage() {
  printf '%s\n' "Usage: $0 (--branch NAME | --commit SHA) [--project-root PATH] [--frontend-root PATH] [--env-file PATH] [--allow-user USER]"
}

cleanup() {
  if [[ -n "$TEMP_LINK" && -L "$TEMP_LINK" ]]; then
    sudo rm -f -- "$TEMP_LINK"
  fi
  if [[ "$SWITCHED" == true && -n "$PREVIOUS_FRONTEND" && -d "$PREVIOUS_FRONTEND" ]]; then
    printf '%s\n' "Deployment failed after frontend switch; restoring previous frontend." >&2
    local restore_link="$FRONTEND_ROOT/current.restore.$$"
    sudo ln -s "$PREVIOUS_FRONTEND" "$restore_link"
    sudo mv -Tf "$restore_link" "$FRONTEND_ROOT/current"
    sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx || true
  fi
}
trap cleanup ERR
trap '[[ -n "$TEMP_LINK" && -L "$TEMP_LINK" ]] && sudo rm -f -- "$TEMP_LINK" || true' EXIT

capture_previous_backend() {
  local api_id worker_id container_id
  api_id="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q api 2>/dev/null || true)"
  worker_id="$(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" ps -q worker 2>/dev/null || true)"
  for container_id in "$api_id" "$worker_id"; do
    [[ -n "$container_id" ]] || continue
    if [[ "$(docker inspect -f '{{.State.Running}}' "$container_id")" == "true" ]]; then
      PREVIOUS_BACKEND_RUNNING=true
      PREVIOUS_BACKEND_IMAGE="$(docker inspect -f '{{.Config.Image}}' "$container_id")"
      break
    fi
  done
  printf 'Captured previous backend state: running=%s image=%s.\n' "$PREVIOUS_BACKEND_RUNNING" "$PREVIOUS_BACKEND_IMAGE"
}

recover_previous_backend() {
  local previous_repository previous_commit
  [[ "$PREVIOUS_BACKEND_RUNNING" == true ]] || return 0
  if [[ -z "$PREVIOUS_BACKEND_IMAGE" || "$PREVIOUS_BACKEND_IMAGE" == *@* || "$PREVIOUS_BACKEND_IMAGE" != *:* ]]; then
    printf 'Cannot safely recover the previous backend image reference.\n' >&2
    return 1
  fi
  docker image inspect "$PREVIOUS_BACKEND_IMAGE" >/dev/null
  previous_repository="${PREVIOUS_BACKEND_IMAGE%:*}"
  previous_commit="${PREVIOUS_BACKEND_IMAGE##*:}"
  export CAM_BACKEND_IMAGE="$previous_repository"
  export BUILD_COMMIT="$previous_commit"
  printf 'Migration failed; recovering prior backend image %s.\n' "$PREVIOUS_BACKEND_IMAGE" >&2
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" up -d --no-build api worker
  wait_for_api_release "http://127.0.0.1:8000" "$previous_commit" "$HEALTH_ATTEMPTS"
  docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE" exec -T worker python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=5).read()"
  printf 'Previous API and worker recovered successfully.\n' >&2
}

while (($#)); do
  case "$1" in
    --branch) BRANCH="${2:?missing branch}"; shift 2 ;;
    --commit) COMMIT="${2:?missing commit}"; shift 2 ;;
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    --allow-user) ALLOW_USER="${2:?missing user}"; shift 2 ;;
    --keep-releases) KEEP_RELEASES="${2:?missing count}"; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
require_deployment_user "desify" "$ALLOW_USER"
[[ -n "$BRANCH" || -n "$COMMIT" ]] || deploy_die "Specify --branch or --commit."
[[ -z "$BRANCH" || -z "$COMMIT" ]] || deploy_die "Choose only one Git reference."
[[ "$KEEP_RELEASES" =~ ^[1-9][0-9]*$ ]] || deploy_die "--keep-releases must be positive."
for command in git docker curl rsync sudo stat grep; do
  require_command "$command"
done
validate_production_env_file "$ENV_FILE"
[[ -d "$REPO_ROOT/.git" ]] || deploy_die "Repository not found: $REPO_ROOT"

cd "$REPO_ROOT"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || deploy_die "Repository must be clean."
git fetch --prune origin
if [[ -n "$BRANCH" ]]; then
  if ! git check-ref-format --branch "$BRANCH" >/dev/null 2>&1; then
    deploy_die "Invalid branch name."
  fi
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
capture_previous_backend

printf 'Building immutable backend image tagged %s.\n' "$RELEASE_COMMIT"
"${COMPOSE[@]}" build api

printf '%s\n' "Validating production application configuration..."
"${COMPOSE[@]}" run --rm --no-deps api python -c "from app.core.config import get_settings; s=get_settings(); assert s.APP_ENV == 'production'"

printf '%s\n' "Checking native PostgreSQL from an ephemeral backend container..."
"${COMPOSE[@]}" run --rm --no-deps api python -c "from app.core.database import validate_database_connection; validate_database_connection()"

printf '%s\n' "Stopping currently running API and worker before migration..."
if [[ "$PREVIOUS_BACKEND_RUNNING" == true ]]; then
  "${COMPOSE[@]}" stop api worker
fi

printf '%s\n' "Running the forward-only Alembic migration service..."
if "${COMPOSE[@]}" --profile migration run --rm migrate; then
  :
else
  migration_exit=$?
  printf 'Migration failed with exit %s; attempting backend recovery.\n' "$migration_exit" >&2
  if ! recover_previous_backend; then
    printf 'WARNING: migration failure recovery did not complete; inspect Docker service state immediately.\n' >&2
  fi
  exit "$migration_exit"
fi

"${COMPOSE[@]}" up -d elasticsearch
"${COMPOSE[@]}" up -d --no-build api worker
wait_for_api_release "http://127.0.0.1:8000" "$RELEASE_COMMIT" "$HEALTH_ATTEMPTS"
"${COMPOSE[@]}" exec -T worker python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=5).read()"

RELEASES_DIR="$FRONTEND_ROOT/releases"
RELEASE_DIR="$RELEASES_DIR/$RELEASE_COMMIT"
sudo install -d -m 0755 "$FRONTEND_ROOT" "$RELEASES_DIR"
if [[ ! -d "$RELEASE_DIR" ]]; then
  sudo install -d -m 0755 "$RELEASE_DIR"
  sudo rsync -a --delete --chmod=D755,F644 "$DIST/" "$RELEASE_DIR/"
elif ! sudo diff -qr "$DIST" "$RELEASE_DIR" >/dev/null; then
  deploy_die "Existing frontend release differs from committed dist."
fi
verify_frontend_dist "$RELEASE_DIR"
scan_frontend_dist "$RELEASE_DIR"

if [[ -L "$FRONTEND_ROOT/current" ]]; then
  PREVIOUS_FRONTEND="$(readlink -f "$FRONTEND_ROOT/current")"
fi
sudo nginx -t
TEMP_LINK="$FRONTEND_ROOT/current.new.$$"
sudo ln -s "$RELEASE_DIR" "$TEMP_LINK"
sudo mv -Tf "$TEMP_LINK" "$FRONTEND_ROOT/current"
TEMP_LINK=""
SWITCHED=true
sudo nginx -t
sudo systemctl reload nginx

PUBLIC_URL="$(read_env_value "$ENV_FILE" PUBLIC_APP_URL)"
for path in / /ai-operations /settings/access /live /ready; do
  curl --fail --silent --show-error --max-time 15 "$PUBLIC_URL$path" >/dev/null
done
if ! version_matches_commit "$PUBLIC_URL/version" "$RELEASE_COMMIT"; then
  deploy_die "Public /version does not match the deployed commit."
fi

CURRENT_RELEASE="$(readlink -f "$FRONTEND_ROOT/current")"
mapfile -t OLD_RELEASES < <(
  find "$RELEASES_DIR" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' |
    sort -rn |
    tail -n "+$((KEEP_RELEASES + 1))" |
    cut -d' ' -f2-
)
for old_release in "${OLD_RELEASES[@]}"; do
  resolved="$(readlink -f "$old_release")"
  [[ "$resolved" == "$RELEASES_DIR/"* ]] || deploy_die "Refusing to prune outside release root."
  [[ "$resolved" == "$CURRENT_RELEASE" ]] || sudo rm -rf -- "$resolved"
done

SWITCHED=false
printf 'Deployment complete: %s\n' "$RELEASE_COMMIT"
printf 'Rollback: sudo -u desify %s/rollback-vps.sh --commit PREVIOUS_COMMIT\n' "$SCRIPT_DIR"
