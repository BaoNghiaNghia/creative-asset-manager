#!/usr/bin/env bash
set -Eeuo pipefail
[[ "${EUID}" == 0 ]] || exit 2
ROOT=/opt/creative-asset-manager/current
[[ -f "$ROOT/apps/api/scripts/promote_agent_reach_keywords.py" ]] || exit 2
cat > /etc/systemd/system/cam-research-auto-approve.service <<'UNIT'
[Unit]
Description=CAM Agent-Reach high-confidence quote approval
After=network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
WorkingDirectory=/opt/creative-asset-manager/current
ExecStart=/bin/bash /opt/creative-asset-manager/current/scripts/cam-run-research-auto-approve.sh --apply
TimeoutStartSec=120
NoNewPrivileges=true
PrivateTmp=true
ProtectHome=true
ProtectSystem=strict
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
UMask=0077
UNIT
cat > /etc/systemd/system/cam-research-auto-approve.timer <<'UNIT'
[Unit]
Description=CAM automated quote screening and visual review
[Timer]
OnBootSec=5min
OnUnitInactiveSec=15min
RandomizedDelaySec=2min
Persistent=true
[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now cam-research-auto-approve.timer
systemctl is-enabled cam-research-auto-approve.timer
