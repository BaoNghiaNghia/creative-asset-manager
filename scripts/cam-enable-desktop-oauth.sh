#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f "$ENV_FILE" ]] || die "Production environment file is unavailable."

BACKUP="${ENV_FILE}.desktop-oauth-backup.$$"
cp -a -- "$ENV_FILE" "$BACKUP"
restore() {
  if [[ -f "$BACKUP" ]]; then
    cp -a -- "$BACKUP" "$ENV_FILE"
    rm -f -- "$BACKUP"
  fi
}
trap restore ERR INT TERM

python3 - "$ENV_FILE" <<'PY'
from pathlib import Path
import os
import stat
import sys

path = Path(sys.argv[1])
st = path.stat()
lines = path.read_text(encoding="utf-8").splitlines()
key = "DESKTOP_OAUTH_ENABLED"
updated = []
found = False
for line in lines:
    if line.startswith(f"{key}="):
        updated.append(f"{key}=true")
        found = True
    else:
        updated.append(line)
if not found:
    updated.append(f"{key}=true")
tmp = path.with_name(f".{path.name}.tmp.{os.getpid()}")
tmp.write_text("\n".join(updated) + "\n", encoding="utf-8")
os.chmod(tmp, stat.S_IMODE(st.st_mode))
os.chown(tmp, st.st_uid, st.st_gid)
os.replace(tmp, path)
PY

systemctl restart creative-asset-manager-api.service

for attempt in $(seq 1 30); do
  if curl --silent --fail --max-time 3 -H 'Host: creative-assets.ddns.net' http://127.0.0.1:8000/ready >/dev/null; then
    rm -f -- "$BACKUP"
    trap - ERR INT TERM
    printf 'DESKTOP_OAUTH_ENABLED=true\n'
    exit 0
  fi
  sleep 1
done

die "API did not become ready after enabling desktop OAuth."
