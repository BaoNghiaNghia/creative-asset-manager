#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/lib/deployment-common.sh"

REPO_ROOT="/home/desify/creative-asset-manager"
FRONTEND_ROOT="/var/www/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
COMMIT=""
ALLOW_USER=""
HEALTH_ATTEMPTS=40
SWITCHED=false
PREVIOUS_FRONTEND=""
TEMP_LINK=""

cleanup() {
  if [[ -n "$TEMP_LINK" && -L "$TEMP_LINK" ]]; then
    sudo rm -f -- "$TEMP_LINK"
  fi
  if [[ "$SWITCHED" == true && -n "$PREVIOUS_FRONTEND" && -d "$PREVIOUS_FRONTEND" ]]; then
    printf '%s\n' "Rollback smoke test failed; restoring the prior frontend symlink." >&2
    local restore_link="$FRONTEND_ROOT/current.restore.$$"
    sudo ln -s "$PREVIOUS_FRONTEND" "$restore_link"
    sudo mv -Tf "$restore_link" "$FRONTEND_ROOT/current"
    sudo nginx -t >/dev/null 2>&1 && sudo systemctl reload nginx || true
  fi
}
trap cleanup ERR
trap '[[ -n "$TEMP_LINK" && -L "$TEMP_LINK" ]] && sudo rm -f -- "$TEMP_LINK" || true' EXIT

while (($#)); do
  case "$1" in
    --commit) COMMIT="${2:?missing commit}"; shift 2 ;;
    --project-root) REPO_ROOT="${2:?missing project root}"; shift 2 ;;
    --frontend-root) FRONTEND_ROOT="${2:?missing frontend root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing env file}"; shift 2 ;;
    --allow-user) ALLOW_USER="${2:?missing user}"; shift 2 ;;
    -h|--help) printf '%s\n' "Usage: $0 [--commit SHA] [--allow-user USER] [path overrides]"; exit 0 ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
require_deployment_user "desify" "$ALLOW_USER"
for command in git docker curl sudo stat grep; do
  require_command "$command"
done
validate_production_env_file "$ENV_FILE"
[[ -d "$REPO_ROOT/.git" ]] || deploy_die "Repository not found: $REPO_ROOT"
cd "$REPO_ROOT"
[[ -z "$(git status --porcelain --untracked-files=normal)" ]] || deploy_die "Repository must be clean."

if [[ -z "$COMMIT" ]]; then
  CURRENT="$(readlink -f "$FRONTEND_ROOT/current")"
  mapfile -t RELEASES < <(
    find "$FRONTEND_ROOT/releases" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %f\n' |
      sort -rn |
      cut -d' ' -f2-
  )
  for candidate in "${RELEASES[@]}"; do
    if [[ "$FRONTEND_ROOT/releases/$candidate" != "$CURRENT" ]]; then
      COMMIT="$candidate"
      break
    fi
  done
fi
[[ -n "$COMMIT" ]] || deploy_die "No previous release is available."
safe_release_id "$COMMIT"

git fetch --prune origin
git cat-file -e "$COMMIT^{commit}" 2>/dev/null || git fetch origin "$COMMIT"
git switch --detach "$COMMIT"
COMMIT="$(git rev-parse --verify HEAD)"
safe_release_id "$COMMIT"
RELEASE_DIR="$FRONTEND_ROOT/releases/$COMMIT"
verify_frontend_dist "$RELEASE_DIR"
scan_frontend_dist "$RELEASE_DIR"

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"
export BUILD_COMMIT="$COMMIT"
COMPOSE=(docker compose --env-file "$ENV_FILE" -f "$REPO_ROOT/infrastructure/docker/docker-compose.prod.yml")
"${COMPOSE[@]}" config --quiet
"${COMPOSE[@]}" build api

printf '%s\n' "WARNING: PostgreSQL is never downgraded automatically."
printf '%s\n' "WARNING: confirm this application release is compatible with the current schema."
"${COMPOSE[@]}" up -d elasticsearch
"${COMPOSE[@]}" up -d --no-build api worker
wait_for_api_release "http://127.0.0.1:8000" "$COMMIT" "$HEALTH_ATTEMPTS"
"${COMPOSE[@]}" exec -T worker python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8081/live', timeout=5).read()"

if [[ -L "$FRONTEND_ROOT/current" ]]; then
  PREVIOUS_FRONTEND="$(readlink -f "$FRONTEND_ROOT/current")"
fi
sudo nginx -t
TEMP_LINK="$FRONTEND_ROOT/current.rollback.$$"
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
if ! version_matches_commit "$PUBLIC_URL/version" "$COMMIT"; then
  deploy_die "Public /version does not match the rollback commit."
fi

SWITCHED=false
printf 'Rollback complete at commit %s. PostgreSQL and Elasticsearch data were preserved.\n' "$COMMIT"
