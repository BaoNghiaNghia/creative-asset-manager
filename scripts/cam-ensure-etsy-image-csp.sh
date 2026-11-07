#!/usr/bin/env bash
# CAM Etsy Spy: allow direct browser-rendered Etsy CDN images without proxying
# or downloading image files to the CAM server.
set -Eeuo pipefail

CONFIG="${CAM_NGINX_CONFIG:-/etc/nginx/sites-enabled/creative-asset-manager.conf}"
BACKUP_DIR="${CAM_NGINX_BACKUP_DIR:-/root/cam-nginx-backups}"

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ $EUID -eq 0 ]] || die "Run as root."
[[ -f "$CONFIG" ]] || die "CAM nginx config is missing."
TARGET="$(readlink -f -- "$CONFIG")"
case "$TARGET" in /etc/nginx/*) ;; *) die "Refusing to modify an unexpected path." ;; esac

# All validation operates on exactly the active site policy. Do not replace the
# whole nginx vhost with the repository template: it may have live-only routes.
if [[ "${1:-}" == "--check" ]]; then
  python3 - "$TARGET" <<'PY'
from pathlib import Path
import re
import sys

text = Path(sys.argv[1]).read_text()
matches = re.findall(r'^\s*add_header\s+Content-Security-Policy\s+"([^"]+)"\s+always;\s*$', text, re.M)
if len(matches) != 1:
    raise SystemExit("Expected exactly one active CSP header in CAM site config.")
parts = [part.strip().split() for part in matches[0].split(";")]
sources = next((part[1:] for part in parts if part and part[0] == "img-src"), None)
if not sources:
    raise SystemExit("Missing img-src directive in CAM CSP.")
required = {"https://i.etsystatic.com", "https://img.etsystatic.com"}
missing = sorted(required - set(sources))
if missing:
    raise SystemExit("Missing approved Etsy origins: " + ", ".join(missing))
print("PASS: CAM Nginx CSP allows both Etsy image CDN hosts.")
PY
  exit 0
fi
[[ "${1:-}" == "" || "${1:-}" == "--apply" ]] || die "Usage: $0 [--check|--apply]"

mkdir -p -- "$BACKUP_DIR"
BACKUP="$BACKUP_DIR/cam-etsy-csp-previous.conf"
cp -a -- "$TARGET" "$BACKUP"

rollback() {
  local status=$?
  trap - ERR
  cp -a -- "$BACKUP" "$TARGET"
  nginx -t && systemctl reload nginx || true
  die "Etsy CSP update failed (exit=$status); original config restored from backup."
}
trap rollback ERR

python3 - "$TARGET" <<'PY'
from pathlib import Path
import re
import sys

path = Path(sys.argv[1])
text = path.read_text()
pattern = re.compile(r'^(\s*add_header\s+Content-Security-Policy\s+")([^"]+)("\s+always;\s*)$', re.M)
matches = list(pattern.finditer(text))
if len(matches) != 1:
    raise SystemExit("Expected exactly one CAM CSP header; refusing speculative changes.")
match = matches[0]
policy = match.group(2)
directives = policy.split(";")
image_directives = [i for i, directive in enumerate(directives)
                    if directive.strip().startswith("img-src ")]
if len(image_directives) != 1:
    raise SystemExit("Expected exactly one img-src directive.")
i = image_directives[0]
items = directives[i].split()
required = ["https://i.etsystatic.com", "https://img.etsystatic.com"]
missing = [origin for origin in required if origin not in items]
if missing:
    directives[i] = directives[i].rstrip() + " " + " ".join(missing)
    new_policy = ";".join(directives)
    path.write_text(text[:match.start(2)] + new_policy + text[match.end(2):])
    print("Patched only img-src with:", ", ".join(missing))
else:
    print("Both Etsy CDN origins already allowed. No file change.")
PY
nginx -t
systemctl reload nginx
bash "$0" --check
trap - ERR
echo "CAM Etsy image CSP applied; backup retained at $BACKUP"
