#!/usr/bin/env bash
# Install a single low-privilege, bounded Agent-Reach discovery timer.
# NEVER provisions/reuses Windows Scout secrets. Install after backend release.
set -Eeuo pipefail
[[ "${EUID}" -eq 0 ]] || { echo "Run via production release operator as root" >&2; exit 2; }
[[ -x /opt/creative-asset-manager/current/apps/api/.venv/bin/python ]] || {
  echo "Backend production release is not available" >&2; exit 2;
}
[[ -f /opt/creative-asset-manager/current/integrations/agent-reach/auto_discover.py ]] || {
  echo "Auto Discover script is not in the active backend release" >&2; exit 2;
}
if ! id -u cam-research >/dev/null 2>&1; then
  useradd --system --home-dir /var/lib/creative-asset-manager-research \
    --shell /usr/sbin/nologin cam-research
fi
install -d -o cam-research -g cam-research -m 0700 /var/lib/creative-asset-manager-research
# The directory is shared by the API's creative-assets account and the
# independent cam-research account. Preserve traverse permission for both;
# individual secrets remain protected by their own 0640 group ownership.
install -d -o root -g root -m 0755 /etc/creative-asset-manager
if [[ ! -e /etc/creative-asset-manager/research-auto-discover.env ]]; then
  (umask 027; : > /etc/creative-asset-manager/research-auto-discover.env)
fi
chown root:cam-research /etc/creative-asset-manager/research-auto-discover.env
chmod 0640 /etc/creative-asset-manager/research-auto-discover.env
cat > /etc/systemd/system/cam-research-auto-discover.service <<'UNIT'
[Unit]
Description=CAM Agent-Reach marketplace web search (VPS-only)
Wants=network-online.target
After=network-online.target

[Service]
Type=oneshot
User=cam-research
Group=cam-research
WorkingDirectory=/opt/creative-asset-manager/current
Environment=CAM_RESEARCH_AUTO_DISCOVER_ENABLED=1
Environment=PYTHONDONTWRITEBYTECODE=1
EnvironmentFile=-/etc/creative-asset-manager/research-auto-discover.env
ExecStart=/opt/creative-asset-manager/current/apps/api/.venv/bin/python /opt/creative-asset-manager/current/integrations/agent-reach/auto_discover.py once --database /var/lib/creative-asset-manager-research/research-inbox.sqlite3 --max-queries 2 --daily-limit 20
TimeoutStartSec=110
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/creative-asset-manager-research
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
LockPersonality=true
UMask=0077
UNIT
cat > /etc/systemd/system/cam-research-auto-discover.timer <<'UNIT'
[Unit]
Description=Hourly CAM Agent-Reach web discovery (independent of Windows)

[Timer]
OnBootSec=8min
OnUnitInactiveSec=1h
RandomizedDelaySec=4min
AccuracySec=1min
Persistent=true
Unit=cam-research-auto-discover.service

[Install]
WantedBy=timers.target
UNIT
systemctl daemon-reload
systemctl enable --now cam-research-auto-discover.timer
systemctl is-enabled cam-research-auto-discover.timer
echo "VPS Agent-Reach timer installed (hourly; independent of Windows)."
echo "Dedicated pairing is OPTIONAL for local public discovery but REQUIRED to sync results to CAM."
echo "Provision only CAM_RESEARCH_SCOUT_AGENT_ID and CAM_RESEARCH_SCOUT_TOKEN into root-owned group-readable /etc/creative-asset-manager/research-auto-discover.env."
