#!/usr/bin/env bash
# Allow CAM's existing signed video Worker origin in img-src for Etsy images.
# Do not touch its video source, public R2 settings, or CDN feature flags.
set -Eeuo pipefail
umask 077

MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" ]] || {
  echo "Usage: $0 [--check|--apply]" >&2; exit 2;
}
[[ $EUID -eq 0 ]] || { echo "Run as root" >&2; exit 2; }
CONFIG="${CAM_NGINX_CONFIG:-/etc/nginx/sites-enabled/creative-asset-manager.conf}"
ENV_FILE="${CAM_ENV_FILE:-/etc/creative-asset-manager/production.env}"
BACKUP_DIR="${CAM_NGINX_BACKUP_DIR:-/root/cam-nginx-backups}"
[[ -f "$CONFIG" && -f "$ENV_FILE" ]] || { echo "Missing CAM site or env file" >&2; exit 2; }
TARGET="$(readlink -f -- "$CONFIG")"
case "$TARGET" in
  /etc/nginx/*) ;;
  *) echo "Refusing unapproved Nginx path" >&2; exit 2 ;;
esac
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ORIGIN="$(PYTHONPATH="$REPO_ROOT" python3 - "$ENV_FILE" <<'PY'
import sys
from pathlib import Path
from urllib.parse import urlsplit
from deploy.tools.production_env import parse_environment_file

settings = parse_environment_file(Path(sys.argv[1]))
url = settings.get("R2_VIDEO_MEDIA_BASE_URL", "").rstrip("/")
parsed = urlsplit(url)
if (parsed.scheme != "https" or not parsed.hostname or parsed.port or
        parsed.path or parsed.query or parsed.fragment or
        parsed.username or parsed.password):
    raise SystemExit("Invalid existing Worker origin")
print("https://" + parsed.hostname)
PY
)"

patch_or_check() {
  python3 - "$TARGET" "$ORIGIN" "$MODE" <<'PY'
from pathlib import Path
import re
import sys

path, origin, mode = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
content = path.read_text()
pattern = re.compile(r'^(\s*add_header\s+Content-Security-Policy\s+")([^"]+)("\s+always;\s*)$', re.M)
matches = list(pattern.finditer(content))
if len(matches) != 1:
    raise SystemExit("Refusing unexpected CSP configuration")
match = matches[0]
segments = match.group(2).split(";")
img = [i for i, v in enumerate(segments) if v.strip().startswith("img-src ")]
media = [v.strip().split()[1:] for v in segments if v.strip().startswith("media-src ")]
if len(img) != 1 or len(media) != 1:
    raise SystemExit("Refusing missing/duplicate img-src or media-src")
if origin not in media[0]:
    raise SystemExit("Origin is not approved for existing video; refusing img-src change")
parts = segments[img[0]].split()
if origin in parts:
    print("PASS: exact existing Worker origin is allowed in img-src")
elif mode == "--check":
    raise SystemExit("BLOCKED: Worker origin not in img-src")
else:
    segments[img[0]] = segments[img[0]].rstrip() + " " + origin
    updated = content[:match.start(2)] + ";".join(segments) + content[match.end(2):]
    path.write_text(updated)
    print("Updated img-src with the existing video Worker origin")
PY
}

if [[ "$MODE" == "--check" ]]; then
  patch_or_check
  exit 0
fi

mkdir -p -- "$BACKUP_DIR"
BACKUP="$(mktemp "$BACKUP_DIR/creative-asset-worker-csp.XXXXXXXX.bak")"
cp -a -- "$TARGET" "$BACKUP"
rollback() {
  local status=$?
  trap - ERR
  cp -a -- "$BACKUP" "$TARGET"
  nginx -t && systemctl reload nginx || true
  echo "CSP update failed (code=$status), restored previous live config" >&2
  exit "$status"
}
trap rollback ERR
patch_or_check
nginx -t
systemctl reload nginx
MODE="--check"
patch_or_check
trap - ERR
echo "CAM image CSP applied; live Nginx configuration validated and backup retained"
