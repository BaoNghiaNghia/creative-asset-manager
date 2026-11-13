#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
[[ "$EUID" -eq 0 ]] || { echo "Root permissions required to install a systemd watchdog." >&2; exit 1; }
install -d -o root -g root -m 0755 /usr/local/libexec
install -o root -g root -m 0755 "$ROOT/scripts/cam-api-liveness-watchdog.py" /usr/local/libexec/cam-api-liveness-watchdog.py
for unit in cam-api-liveness-watchdog.service cam-api-liveness-watchdog.timer; do
  install -o root -g root -m 0644 "$ROOT/deploy/systemd/$unit" "/etc/systemd/system/$unit"
done
systemctl daemon-reload
systemctl enable --now cam-api-liveness-watchdog.timer
systemctl start cam-api-liveness-watchdog.service
systemctl is-active --quiet cam-api-liveness-watchdog.timer
echo "CAM API watchdog installed: API-only, two failed local checks, 15-minute restart cooldown."
