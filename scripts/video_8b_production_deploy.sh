#!/usr/bin/env bash
# Deploy VIDEO-8B capability on the production VPS. VIDEO remains disabled.
set -Eeuo pipefail
umask 027

TARGET_COMMIT="${VIDEO_8B_TARGET_COMMIT:?set VIDEO_8B_TARGET_COMMIT to the reviewed full commit}"
RELEASE_ID="${VIDEO_8B_RELEASE_ID:?set VIDEO_8B_RELEASE_ID to the reviewed immutable release id}"
VIDEO_INDEX_VERSION="${VIDEO_8B_VIDEO_INDEX_VERSION:-$RELEASE_ID}"
APP_ROOT="/opt/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
VIDEO_TEMP_DIRECTORY="/var/lib/creative-asset-manager/video-proxy"
MIN_FREE_BYTES=1567108864

SOURCE_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || true)"
[[ -n "$SOURCE_ROOT" ]] || { printf 'ERROR: run from the production source checkout.\n' >&2; exit 1; }
CAM_DEPLOY="$SOURCE_ROOT/deploy/bin/cam-deploy"
ENV_HELPER="$SOURCE_ROOT/deploy/tools/production_env.py"
PYTHON_BIN="${PYTHON_BIN:-python3}"
TEMP_FILES=()
SWITCHED=false

cleanup() {
  local path
  for path in "${TEMP_FILES[@]}"; do
    [[ -n "$path" ]] && rm -f -- "$path"
  done
}
finalize() {
  local status="$?"
  trap - EXIT
  cleanup
  if [[ "$status" -ne 0 && "$SWITCHED" == true ]]; then
    printf 'ROLLBACK_REQUIRED=YES\n' >&2
    if sudo "$CAM_DEPLOY" rollback-release; then
      printf 'ROLLBACK_RESULT=PASS\n' >&2
    else
      printf 'ROLLBACK_RESULT=FAIL\n' >&2
    fi
  fi
  exit "$status"
}
trap finalize EXIT

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

validate_inputs() {
  [[ "$TARGET_COMMIT" =~ ^[0-9A-Fa-f]{40}$ ]] || die 'VIDEO_8B_TARGET_COMMIT must be a 40-character Git SHA.'
  [[ "$RELEASE_ID" == "${TARGET_COMMIT:0:12}" ]] || die 'VIDEO_8B_RELEASE_ID must match the first 12 target SHA characters.'
  [[ "$VIDEO_INDEX_VERSION" =~ ^${RELEASE_ID}(-r[2-9][0-9]*)?$ ]] || die 'VIDEO index version must be the release ID or its deterministic -rN suffix.'
}

require_production_host() {
  systemctl status creative-asset-manager-api --no-pager >/dev/null 2>&1 ||
    die 'creative-asset-manager-api systemd service is unavailable; refusing non-production host.'
  systemctl status creative-asset-manager-worker --no-pager >/dev/null 2>&1 ||
    die 'creative-asset-manager-worker systemd service is unavailable; refusing non-production host.'
  [[ -d "$APP_ROOT" ]] || die "production release root is unavailable: $APP_ROOT"
  [[ -f "$ENV_FILE" ]] || die "production environment file is unavailable: $ENV_FILE"
}

require_source_checkout() {
  [[ -x "$CAM_DEPLOY" ]] || die 'cam-deploy is unavailable from this source checkout.'
  [[ -f "$ENV_HELPER" ]] || die 'production environment helper is unavailable from this source checkout.'
  [[ -z "$(git status --porcelain --untracked-files=normal)" ]] || die 'source checkout is dirty.'
  [[ "$(git rev-parse HEAD)" == "$TARGET_COMMIT" ]] ||
    die 'source checkout is not the reviewed VIDEO-8B commit.'
}

env_value() {
  local key="$1"
  "$PYTHON_BIN" - "$ENV_FILE" "$key" <<'PY'
from pathlib import Path
import sys
from deploy.tools.production_env import parse_environment_file
value = parse_environment_file(Path(sys.argv[1])).get(sys.argv[2])
if value is None:
    raise SystemExit(2)
print(value)
PY
}

require_env_value() {
  local key="$1" expected="$2" actual
  actual="$(env_value "$key")" || die "required production setting is absent: $key"
  [[ "$actual" == "$expected" ]] || die "required production setting is not safe: $key"
}

verify_video_flags_off() {
  require_env_value VIDEO_SEARCH_ENABLED false
  require_env_value VIDEO_ANALYSIS_ENABLED false
  require_env_value VIDEO_PROXY_ENABLED false
  require_env_value VIDEO_TEMP_DIRECTORY "$VIDEO_TEMP_DIRECTORY"
  require_env_value ELASTICSEARCH_URL http://127.0.0.1:9200
}

require_native_runtime() {
  command -v ffmpeg >/dev/null 2>&1 ||
    die 'ffmpeg is unavailable; install the trusted distribution package first.'
  command -v ffprobe >/dev/null 2>&1 ||
    die 'ffprobe is unavailable; install the trusted distribution package first.'
}

prepare_video_storage() {
  sudo install -d -o creative-assets -g creative-assets -m 0750 "$VIDEO_TEMP_DIRECTORY"
  sudo -u creative-assets test -w "$VIDEO_TEMP_DIRECTORY" ||
    die 'video proxy directory is not writable by creative-assets.'
  local available
  available="$(df -PB1 "$VIDEO_TEMP_DIRECTORY" | awk 'NR == 2 { print $4 }')"
  [[ "$available" =~ ^[0-9]+$ && "$available" -ge "$MIN_FREE_BYTES" ]] ||
    die 'video proxy filesystem has insufficient free space.'
}

require_elasticsearch() {
  command -v curl >/dev/null 2>&1 || die 'curl is unavailable.'
  local status
  status="$(curl --fail --silent --max-time 5 http://127.0.0.1:9200/_cluster/health?filter_path=status |
    "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("status", ""))')" ||
    die 'Elasticsearch is unavailable at loopback.'
  [[ "$status" == green || "$status" == yellow ]] ||
    die 'Elasticsearch health is not green or yellow.'
}

snapshot_image_aliases() {
  local prefix="$1"
  local path
  path="$(mktemp)"
  TEMP_FILES+=("$path")
  curl --fail --silent --max-time 5 'http://127.0.0.1:9200/_cat/aliases?format=json&h=alias,index' |
    "$PYTHON_BIN" -c 'import json,sys; rows=json.load(sys.stdin); print("\n".join(sorted("{} {}".format(row.get("alias", ""), row.get("index", "")) for row in rows if row.get("alias", "") not in {"${prefix}-video-v3-read", "${prefix}-video-v3-write"})))' >"$path" ||
    die 'unable to capture image alias snapshot.'
  printf '%s\n' "$path"
}

run_index_provisioning() {
  local release="$APP_ROOT/releases/$RELEASE_ID" prefix="$1" image_aliases_before="$2"
  local python="$release/apps/api/.venv/bin/python"
  [[ -x "$python" ]] || die 'new release Python environment is unavailable.'
  "$python" "$release/deploy/tools/production_env.py" run-quiet --env-file "$ENV_FILE" --expected-owner-uid 0 -- bash -c \
    "cd '$release/apps/api' && '$python' -m app.operations.video_search_index_cli --version '$VIDEO_INDEX_VERSION' --index-prefix '$prefix' --elasticsearch-url http://127.0.0.1:9200 --dry-run" ||
    die 'VIDEO index dry-run failed.'
  "$python" "$release/deploy/tools/production_env.py" run-quiet --env-file "$ENV_FILE" --expected-owner-uid 0 -- bash -c \
    "cd '$release/apps/api' && '$python' -m app.operations.video_search_index_cli --version '$VIDEO_INDEX_VERSION' --index-prefix '$prefix' --elasticsearch-url http://127.0.0.1:9200 --apply --confirmed" ||
    die 'VIDEO index provisioning failed.'
  local target="$prefix-video-v3-$VIDEO_INDEX_VERSION"
  curl --fail --silent --max-time 5 "http://127.0.0.1:9200/$target/_mapping" >/dev/null ||
    die 'VIDEO physical index mapping is unavailable after provisioning.'
  local count
  count="$(curl --fail --silent --max-time 5 "http://127.0.0.1:9200/$target/_count" | "$PYTHON_BIN" -c 'import json,sys; print(json.load(sys.stdin).get("count", -1))')" || die 'VIDEO index count check failed.'
  [[ "$count" == 0 ]] || die 'VIDEO index is not empty; release switch blocked.'
  local image_aliases_after
  image_aliases_after="$(snapshot_image_aliases "$prefix")"
  cmp --silent "$image_aliases_before" "$image_aliases_after" ||
    die 'IMAGE aliases changed while provisioning VIDEO index; release switch blocked.'
}

main() {
  cd "$SOURCE_ROOT"
  validate_inputs
  require_production_host
  require_source_checkout
  verify_video_flags_off
  require_native_runtime
  prepare_video_storage
  require_elasticsearch
  local prefix image_aliases_before
  prefix="$(env_value ELASTICSEARCH_INDEX_PREFIX)" || die 'ELASTICSEARCH_INDEX_PREFIX is absent.'
  [[ -n "$prefix" ]] || die 'ELASTICSEARCH_INDEX_PREFIX is empty.'
  image_aliases_before="$(snapshot_image_aliases "$prefix")"
  if [[ -e "$APP_ROOT/releases/$RELEASE_ID" ]]; then
    die "immutable release already exists; verify it independently before reuse."
  fi
  sudo "$CAM_DEPLOY" install-release "$SOURCE_ROOT" "$RELEASE_ID"
  sudo "$CAM_DEPLOY" check-config "$RELEASE_ID"
  sudo "$CAM_DEPLOY" verify-alembic-head "$RELEASE_ID"
  sudo "$CAM_DEPLOY" migrate "$RELEASE_ID"
  sudo "$CAM_DEPLOY" seed "$RELEASE_ID"
  run_index_provisioning "$prefix" "$image_aliases_before"
  verify_video_flags_off
  sudo "$CAM_DEPLOY" switch-release "$RELEASE_ID"
  SWITCHED=true
  sudo "$CAM_DEPLOY" restart-api
  sudo "$CAM_DEPLOY" restart-worker
  sudo "$CAM_DEPLOY" verify-api
  sudo "$CAM_DEPLOY" verify-worker
  sudo "$CAM_DEPLOY" diagnostics
  verify_video_flags_off
  printf 'VIDEO-8B capability deployment completed with VIDEO flags disabled.\n'
}
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  main "$@"
fi
