#!/usr/bin/env bash
set -Eeuo pipefail

CONFIG="\${CAM_NGINX_CONFIG:-/etc/nginx/sites-enabled/creative-asset-manager.conf}"
MARKER="location ^~ /desktop-updates/windows/"
BLOCK='    # Electron auto-update feed. Versioned installers and metadata are
    # published atomically under the current symlink by the desktop release script.
    location ^~ /desktop-updates/windows/ {
        alias /var/www/creative-asset-manager-desktop-updates/windows/current/;
        default_type application/octet-stream;
        add_header Cache-Control "no-store, no-cache, must-revalidate" always;
        add_header X-Content-Type-Options nosniff always;
    }

'

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f "$CONFIG" ]] || die "Nginx site config is missing: $CONFIG"

if grep -Fq "$MARKER" "$CONFIG"; then
  nginx -t
  printf 'Desktop update Nginx route already installed.\n'
  exit 0
fi

BACKUP="\${CONFIG}.desktop-update.bak"
cp -a -- "$CONFIG" "$BACKUP"
python3 - "$CONFIG" "$BLOCK" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
block = sys.argv[2]
text = path.read_text()
needle = "    location /assets/ {\n"
if needle not in text:
    raise SystemExit("Could not locate the assets location insertion point.")
path.write_text(text.replace(needle, block + needle, 1))
PY

if nginx -t && systemctl reload nginx; then
  rm -f -- "$BACKUP"
  printf 'Desktop update Nginx route installed.\n'
else
  cp -a -- "$BACKUP" "$CONFIG"
  rm -f -- "$BACKUP"
  nginx -t
  systemctl reload nginx
  die "Nginx update failed; previous config restored."
fi
