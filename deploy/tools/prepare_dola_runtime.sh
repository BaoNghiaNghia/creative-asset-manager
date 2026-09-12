#!/usr/bin/env bash
# Prepare only the isolated Dola gateway runtime; never touches CAM API/worker venvs.
set -euo pipefail

MODE=check
ROOT=/
ENV_FILE=/etc/dola-render-gateway/production.env
SERVICE_USER=dola-render-gateway
RELEASE_ROOT=/opt/dola-render-gateway
STATE_ROOT=/var/lib/dola-render-gateway
RUNTIME_ROOT=/var/lib/dola-render-gateway/current-runtime

usage() {
  cat <<'EOF'
Usage: prepare_dola_runtime.sh [--check|--dry-run|--prepare] [--root PATH] [--env-file PATH]
  --check   Validate paths and safe configuration without requiring a secret.
  --dry-run Print the isolated preparation actions without changing anything.
  --prepare Create only the Dola venv/state/Xvfb runtime (requires root).
EOF
}

die() { printf '%s\n' "dola runtime: $*" >&2; exit 1; }
rooted() { printf '%s%s\n' "$ROOT" "$1"; }
env_value() {
  local key=$1 path=$2
  [[ -r "$path" ]] || return 0
  awk -F= -v wanted="$key" '$1 == wanted { value=$0; sub(/^[^=]*=/, "", value) } END { print value }' "$path"
}
is_under() { [[ "$1" == "$2" || "$1" == "$2/"* ]]; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --check) MODE=check ;;
    --dry-run) MODE=dry-run ;;
    --prepare) MODE=prepare ;;
    --root) ROOT=$2; shift ;;
    --env-file) ENV_FILE=$2; shift ;;
    -h|--help) usage; exit 0 ;;
    *) usage >&2; die "unknown argument: $1" ;;
  esac
  shift
done

[[ "$ROOT" = /* ]] || die "--root must be absolute"
if [[ "$ROOT" != / ]]; then ENV_FILE="$ROOT$ENV_FILE"; fi
CURRENT=$(rooted "$RELEASE_ROOT/current")
SOURCE="$CURRENT/apps/dola_render_gateway"
REQ_LOCK="$CURRENT/deploy/dola-render-gateway.requirements.lock"
STATE=$(rooted "$STATE_ROOT")
RUNTIME=$(rooted "$RUNTIME_ROOT")

[[ -d "$SOURCE" ]] || die "release source is missing: $SOURCE"
[[ -f "$REQ_LOCK" ]] || die "pinned requirements lock is missing: $REQ_LOCK"
[[ -f "$SOURCE/cam_runtime/app.py" ]] || die "gateway entrypoint is missing"
[[ -d "$SOURCE/upstream/extensions/dola30" ]] || die "vendored extension is missing"
if is_under "$STATE" "$CURRENT" || is_under "$RUNTIME" "$CURRENT"; then
  die "persistent Dola state/runtime must not be under the release checkout"
fi

HOST=$(env_value DOLA_GATEWAY_HOST "$ENV_FILE")
PORT=$(env_value DOLA_GATEWAY_PORT "$ENV_FILE")
CONCURRENCY=$(env_value DOLA_MAX_CONCURRENCY "$ENV_FILE")
if [[ -z "$HOST" ]]; then HOST=127.0.0.1; fi
if [[ -z "$PORT" ]]; then PORT=8100; fi
if [[ -z "$CONCURRENCY" ]]; then CONCURRENCY=1; fi
[[ "$HOST" = 127.0.0.1 || "$HOST" = ::1 || "$HOST" = localhost ]] || die "gateway host must be loopback"
[[ "$PORT" = 8100 ]] || die "gateway port must be 8100"
[[ "$CONCURRENCY" = 1 ]] || die "DOLA_MAX_CONCURRENCY must remain 1 for the approved runtime"
[[ "$MODE" != prepare || "$(id -u)" = 0 ]] || die "--prepare requires root"
[[ "$MODE" != prepare || "$ROOT" = / ]] || die "--prepare may not use a fake root"

if [[ "$MODE" = check ]]; then
  printf '%s\n' "dola runtime check passed: isolated release, state, loopback and concurrency are safe"
  exit 0
fi
if [[ "$MODE" = dry-run ]]; then
  printf '%s\n' "would create $STATE and $RUNTIME owned by $SERVICE_USER"
  printf '%s\n' "would create Dola-only venv at $RUNTIME and install $REQ_LOCK"
  printf '%s\n' "would install the Patchright Chromium revision into $RUNTIME/browser-cache"
  printf '%s\n' "would arrange Xvfb :99 only under the dedicated systemd service"
  exit 0
fi

install -d -o "$SERVICE_USER" -g "$SERVICE_USER" -m 0750 \
  "$STATE/db" "$STATE/accounts" "$STATE/profiles" "$STATE/downloads" "$STATE/artifacts" "$RUNTIME"
python3 -m venv "$RUNTIME"
"$RUNTIME/bin/pip" install --disable-pip-version-check --no-deps -r "$REQ_LOCK"
PATCHRIGHT_BROWSERS_PATH="$RUNTIME/browser-cache" "$RUNTIME/bin/patchright" install chromium
"$RUNTIME/bin/python" -m pip freeze | sha256sum | awk '{print $1}' > "$RUNTIME/requirements.sha256"
chown -R "$SERVICE_USER:$SERVICE_USER" "$STATE" "$RUNTIME"
printf '%s\n' "prepared isolated Dola runtime; feature flags remain off"