#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="${CAM_SOURCE_DIR:-$CHECKOUT_ROOT}"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
APP_ROOT="${CAM_APP_ROOT:-/opt/creative-asset-manager}"
REF=""
ROLLBACK=false
NO_MIGRATE=false
NO_RESTART=false
ALLOW_DIRTY=false

usage() { cat <<'USAGE'
Usage: sudo scripts/cam-rebuild-backend.sh [--commit SHA] [--source-dir PATH] [--no-migrate] [--no-restart] [--rollback] [--allow-dirty]
Creates and activates an immutable native systemd backend release. Docker is never used for API or workers.
USAGE
}
die() { printf "ERROR: %s\n" "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"; }
while (($#)); do
  case "$1" in
    --commit) REF="${2:?missing commit}"; shift 2 ;;
    --source-dir) SOURCE_DIR="${2:?missing source directory}"; shift 2 ;;
    --no-migrate) NO_MIGRATE=true; shift ;;
    --no-restart) NO_RESTART=true; shift ;;
    --rollback) ROLLBACK=true; shift ;;
    --allow-dirty) ALLOW_DIRTY=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
[[ ! -d "$SOURCE_DIR/.git" ]] && SOURCE_DIR="$CHECKOUT_ROOT"
SOURCE_DIR="$(realpath -- "$SOURCE_DIR")"
for command in git rsync python3 systemctl curl; do require "$command"; done
[[ $EUID -eq 0 ]] || die "Run as root to install releases and native systemd units."
[[ -f "$ENV_FILE" ]] || die "Production environment file is missing."
[[ -f "$SOURCE_DIR/deploy/tools/production_env.py" ]] || die "production_env.py is missing."
RELEASES="$APP_ROOT/releases"
CURRENT="$APP_ROOT/current"
PREVIOUS="$APP_ROOT/previous"
activate_release() {
  local target="$1" old=""
  [[ -d "$target/apps/api" && -x "$target/apps/api/.venv/bin/python" ]] || die "Release is invalid."
  [[ ! -L "$CURRENT" ]] || old="$(readlink -f -- "$CURRENT")"
  [[ -z "$old" || "$old" == "$target" ]] || { ln -s -- "$old" "$PREVIOUS.new"; mv -Tf -- "$PREVIOUS.new" "$PREVIOUS"; }
  ln -s -- "$target" "$CURRENT.new"; mv -Tf -- "$CURRENT.new" "$CURRENT"
}
trusted_host() {
  python3 -c "import sys; sys.path.insert(0, \"$SOURCE_DIR/deploy/tools\"); from production_env import parse_environment_file; print(parse_environment_file(__import__(\"pathlib\").Path(\"$ENV_FILE\"))[\"TRUSTED_HOSTS\"].split(\",\")[0].strip())"
}
wait_for_endpoint() {
  local label="$1"; shift
  local attempt
  for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 5 "$@" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  die "$label did not become healthy within 30 seconds."
}
verify_services() {
  local host; host="$(trusted_host)"
  for service in creative-asset-manager-api.service creative-asset-manager-image-worker.service creative-asset-manager-video-worker.service; do systemctl is-active --quiet "$service" || die "$service is not active."; done
  systemctl is-active --quiet creative-asset-manager-worker.service && die "Legacy all-role worker must be inactive." || true
  for endpoint in live ready version; do wait_for_endpoint "API $endpoint" -H "Host: $host" "http://127.0.0.1:8000/$endpoint"; done
  for port in 8081 8082; do for endpoint in live health ready; do wait_for_endpoint "worker $port $endpoint" "http://127.0.0.1:$port/$endpoint"; done; done
}
restart_services() {
  systemctl restart creative-asset-manager-api.service
  systemctl restart creative-asset-manager-image-worker.service
  systemctl restart creative-asset-manager-video-worker.service
  verify_services
}
if $ROLLBACK; then
  [[ -L "$PREVIOUS" ]] || die "No previous backend release is recorded."
  activate_release "$(readlink -f -- "$PREVIOUS")"
  $NO_RESTART || restart_services
  printf "Backend rollback activated. PostgreSQL was not downgraded.\n"
  exit 0
fi
[[ "$ALLOW_DIRTY" == true || -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=normal)" ]] || die "Refusing deployment from a dirty checkout."
COMMIT="$(git -C "$SOURCE_DIR" rev-parse --verify "${REF:-HEAD}^{commit}")"
[[ "$COMMIT" == "$(git -C "$SOURCE_DIR" rev-parse HEAD)" ]] || die "--commit must be checked out in --source-dir; do not mutate the source checkout automatically."
TARGET="$RELEASES/$COMMIT"
install -d -o root -g root -m 0755 "$RELEASES"
if [[ ! -e "$TARGET" ]]; then
  STAGE="$RELEASES/.${COMMIT}.new.$$"
  install -d -o creative-assets -g creative-assets -m 0750 "$STAGE"
  rsync -a --delete --chown=creative-assets:creative-assets --exclude=.git --exclude=.env --exclude=.env.local --exclude=node_modules --exclude=.venv --exclude=__pycache__ --exclude=.pytest_cache --exclude="*.pyc" "$SOURCE_DIR/" "$STAGE/"
  runuser -u creative-assets -- python3 -m venv "$STAGE/apps/api/.venv"
  runuser -u creative-assets -- "$STAGE/apps/api/.venv/bin/python" -m pip install --disable-pip-version-check --no-input --no-cache-dir --requirement "$STAGE/apps/api/requirements.txt" >/dev/null
  printf "%s\n" "$COMMIT" > "$STAGE/.cam-release"
  mv -T "$STAGE" "$TARGET"
fi
PYTHON="$TARGET/apps/api/.venv/bin/python"
[[ -x "$PYTHON" ]] || die "Native Python virtual environment is missing."
"$PYTHON" "$TARGET/deploy/tools/production_env.py" check --env-file "$ENV_FILE" --expected-owner-uid 0 --api-root "$TARGET/apps/api" >/dev/null
HEADS="$("$PYTHON" -m alembic -c "$TARGET/apps/api/alembic.ini" heads | wc -l)"
[[ "$HEADS" -eq 1 ]] || die "Exactly one Alembic head is required."
if ! $NO_MIGRATE; then "$TARGET/deploy/tools/production_env.py" run-quiet --env-file "$ENV_FILE" --expected-owner-uid 0 -- "$PYTHON" -m alembic -c "$TARGET/apps/api/alembic.ini" upgrade head >/dev/null; fi
for unit in creative-asset-manager-api.service creative-asset-manager-image-worker.service creative-asset-manager-video-worker.service; do install -o root -g root -m 0644 "$TARGET/deploy/systemd/$unit" "/etc/systemd/system/$unit"; done
systemctl daemon-reload
systemctl stop creative-asset-manager-worker.service || true
systemctl disable creative-asset-manager-worker.service || true
systemctl enable creative-asset-manager-api.service creative-asset-manager-image-worker.service creative-asset-manager-video-worker.service
activate_release "$TARGET"
if ! $NO_RESTART; then restart_services; fi
printf "Backend release %s activated. Docker API and workers were not used.\n" "$COMMIT"