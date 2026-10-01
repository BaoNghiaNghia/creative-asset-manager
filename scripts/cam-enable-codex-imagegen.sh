#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
RUNNER="$ROOT/deploy/tools/production_env.py"
API_ROOT="$ROOT/apps/api"
PYTHON="$API_ROOT/.venv/bin/python"
CODEX_RUNTIME="$ROOT/scripts/cam-codex-runtime.sh"

API_UNIT="creative-asset-manager-api.service"
IMAGE_UNITS=(
  "creative-asset-manager-image-worker.service"
  "creative-asset-manager-image-worker-2.service"
  "creative-asset-manager-image-worker-3.service"
  "creative-asset-manager-image-worker-4.service"
)
IMAGE_PORTS=(8081 8083 8084 8085)

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f "$ENV_FILE" ]] || die "Production environment file is unavailable."
[[ -x "$RUNNER" && -x "$PYTHON" && -x "$CODEX_RUNTIME" ]] \
  || die "Production activation tooling is unavailable."

# Never enable the provider unless the service-account auth and real built-in
# image generation smoke test both succeed first.
"$CODEX_RUNTIME" status >/dev/null \
  || die "Codex is not authenticated for the production service user."
"$CODEX_RUNTIME" smoke

BACKUP="${ENV_FILE}.codex-image-backup.$$"
cp -a -- "$ENV_FILE" "$BACKUP"

restart_runtime() {
  systemctl restart "$API_UNIT"
  for unit in "${IMAGE_UNITS[@]}"; do
    systemctl restart "$unit"
  done
}

restore() {
  local exit_code=$?
  trap - ERR INT TERM
  if [[ -f "$BACKUP" ]]; then
    cp -a -- "$BACKUP" "$ENV_FILE"
    restart_runtime || true
    rm -f -- "$BACKUP"
  fi
  exit "$exit_code"
}
trap restore ERR INT TERM

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os
import stat
import sys

path = Path(sys.argv[1])
st = path.stat()
updates = {
    "IMAGE_GENERATION_ENABLED": "true",
    "CODEX_IMAGE_GENERATION_ENABLED": "true",
    "RRUGC_IMAGE_GENERATION_PROVIDER": "codex",
    "CODEX_IMAGE_BINARY": "codex",
    "CODEX_IMAGE_HOME": "/var/lib/creative-asset-manager/codex",
    "CODEX_IMAGE_SKILL": "worker-hat-v1",
    "CODEX_IMAGE_TIMEOUT_SECONDS": "900",
}

lines = path.read_text(encoding="utf-8").splitlines()
seen: set[str] = set()
updated: list[str] = []
for line in lines:
    key, separator, _value = line.partition("=")
    if separator and key in updates:
        updated.append(f"{key}={updates[key]}")
        seen.add(key)
    else:
        updated.append(line)
for key, value in updates.items():
    if key not in seen:
        updated.append(f"{key}={value}")

tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
tmp.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.chmod(tmp, stat.S_IMODE(st.st_mode))
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, path)
PY

"$PYTHON" "$RUNNER" check \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  --api-root "$API_ROOT" >/dev/null

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import sys

required = {
    "IMAGE_GENERATION_ENABLED": "true",
    "CODEX_IMAGE_GENERATION_ENABLED": "true",
    "RRUGC_IMAGE_GENERATION_PROVIDER": "codex",
    "CODEX_IMAGE_HOME": "/var/lib/creative-asset-manager/codex",
    "CODEX_IMAGE_SKILL": "worker-hat-v1",
}
values = {}
for raw in Path(sys.argv[1]).read_text(encoding="utf-8").splitlines():
    key, separator, value = raw.partition("=")
    if separator:
        values[key] = value
for key, expected in required.items():
    if values.get(key) != expected:
        raise SystemExit(f"production setting mismatch: {key}")
PY

restart_runtime

for attempt in $(seq 1 30); do
  if curl --silent --fail --max-time 3 \
    -H 'Host: creative-assets.ddns.net' \
    http://127.0.0.1:8000/ready >/dev/null; then
    break
  fi
  [[ "$attempt" -lt 30 ]] || die "API did not become ready after Codex activation."
  sleep 1
done

for index in "${!IMAGE_PORTS[@]}"; do
  port="${IMAGE_PORTS[$index]}"
  unit="${IMAGE_UNITS[$index]}"
  for attempt in $(seq 1 30); do
    if curl --silent --fail --max-time 3 "http://127.0.0.1:$port/ready" >/dev/null; then
      break
    fi
    [[ "$attempt" -lt 30 ]] \
      || die "$unit did not become ready after Codex activation."
    sleep 1
  done
done

rm -f -- "$BACKUP"
trap - ERR INT TERM
printf 'IMAGE_GENERATION_ENABLED=true\n'
printf 'CODEX_IMAGE_GENERATION_ENABLED=true\n'
printf 'RRUGC_IMAGE_GENERATION_PROVIDER=codex\n'
