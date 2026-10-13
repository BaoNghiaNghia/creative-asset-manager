#!/usr/bin/env bash
# Controlled scheduled enrichment for Stage 0 Etsy/Amazon source previews.
set -Eeuo pipefail
[[ "${EUID}" == 0 ]] || { echo "Requires deploy root" >&2; exit 2; }
ROOT=/opt/creative-asset-manager/current
[[ -f "$ROOT/apps/api/scripts/enrich_marketplace_images.py" ]] || { echo "Deploy backend first" >&2; exit 2; }
cat > /etc/systemd/system/cam-marketplace-images.service <<'UNIT'
[Unit]
Description=CAM Stage 0 approved marketplace source-image enrichment
After=network-online.target
Wants=network-online.target

[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/creative-asset-manager/current
ExecStart=/bin/bash /opt/creative-asset-manager/current/scripts/cam-run-marketplace-images.sh --apply --limit 4
TimeoutStartSec=180
Environment=PYTHONDONTWRITEBYTECODE=1
Nice=10
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0077
UNIT
cat > /etc/systemd/system/cam-marketplace-images.timer <<'UNIT'
[Unit]
Description=Periodically enrich Stage 0 Etsy/Amazon source images

[Timer]
OnBootSec=8min
OnUnitInactiveSec=3h
RandomizedDelaySec=8min
Persistent=true

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now cam-marketplace-images.timer
systemctl is-enabled cam-marketplace-images.timer
