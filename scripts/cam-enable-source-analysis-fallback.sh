#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
RUNNER="$ROOT/deploy/tools/production_env.py"
API_ROOT="$ROOT/apps/api"
PYTHON="$API_ROOT/.venv/bin/python"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f "$ENV_FILE" && -x "$RUNNER" && -x "$PYTHON" ]] || die "Production environment tooling is unavailable."

BACKUP="${ENV_FILE}.source-fallback-backup.$$"
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
key = "AI_ANALYSIS_SOURCE_FALLBACK_ENABLED"
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

"$PYTHON" "$RUNNER" check \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  --api-root "$API_ROOT" >/dev/null
"$PYTHON" "$RUNNER" flag-enabled \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  --name AI_ANALYSIS_SOURCE_FALLBACK_ENABLED

rm -f -- "$BACKUP"
trap - ERR INT TERM
printf 'AI_ANALYSIS_SOURCE_FALLBACK_ENABLED=true\n'
