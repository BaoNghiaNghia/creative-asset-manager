#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

[[ "${EUID}" -eq 0 ]] || { echo "Run as root: sudo $0"; exit 1; }

export DEBIAN_FRONTEND=noninteractive

echo "[1/8] Base packages"
apt-get update
apt-get install -y \
  ca-certificates curl gnupg git rsync jq openssl \
  python3 python3-venv python3-pip \
  nginx postgresql postgresql-contrib \
  ffmpeg certbot

echo "[2/8] Node.js 22 (system-wide)"
curl -fsSL https://deb.nodesource.com/setup_22.x -o /tmp/nodesource-setup.sh
bash /tmp/nodesource-setup.sh
rm -f /tmp/nodesource-setup.sh
apt-get install -y nodejs

echo "[3/8] Docker Engine + Compose plugin"
. /etc/os-release
case "${ID}" in
  ubuntu|debian) ;;
  *) echo "Unsupported distro for this helper: ${ID}. Install Docker Engine + Compose plugin manually."; exit 2 ;;
esac
install -m 0755 -d /etc/apt/keyrings
curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /etc/apt/keyrings/docker.asc
chmod a+r /etc/apt/keyrings/docker.asc
CODENAME="${UBUNTU_CODENAME:-${VERSION_CODENAME}}"
cat >/etc/apt/sources.list.d/docker.sources <<EOF
Types: deb
URIs: https://download.docker.com/linux/${ID}
Suites: ${CODENAME}
Components: stable
Architectures: $(dpkg --print-architecture)
Signed-By: /etc/apt/keyrings/docker.asc
EOF
apt-get update
apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin

echo "[4/8] Services"
systemctl enable --now postgresql
systemctl enable --now docker
systemctl enable --now nginx

echo "[5/8] Service account + directories"
if ! id creative-assets >/dev/null 2>&1; then
  useradd --system --home /opt/creative-asset-manager --shell /usr/sbin/nologin creative-assets
fi
install -d -o root -g root -m 0755 /opt/creative-asset-manager
install -d -o root -g root -m 0755 /var/www/creative-asset-manager
install -d -o root -g creative-assets -m 0750 /etc/creative-asset-manager
install -d -o root -g root -m 0755 /srv/creative-asset-manager-source
install -d -o creative-assets -g creative-assets -m 0750 /var/lib/creative-asset-manager
install -d -o creative-assets -g creative-assets -m 0750 /var/lib/creative-asset-manager/video-proxy
install -d -o creative-assets -g creative-assets -m 0750 /opt/creative-asset-manager/.npm
install -d -o root -g root -m 0755 /var/www/letsencrypt

echo "[6/8] Elasticsearch kernel setting"
cat >/etc/sysctl.d/99-creative-asset-manager.conf <<'EOF'
vm.max_map_count=262144
EOF
sysctl --system >/dev/null

echo "[7/8] Version checks"
python3 --version
node --version
npm --version
docker --version
docker compose version
psql --version
ffmpeg -version | head -n 1
ffprobe -version | head -n 1
nginx -v

NODE_MAJOR="$(node -p 'process.versions.node.split(".")[0]')"
[[ "${NODE_MAJOR}" -eq 22 ]] || { echo "ERROR: Node.js 22 required by the repository CI."; exit 3; }

echo "[8/8] Done"
echo "HOST_BOOTSTRAP=PASS"
echo "Next: run 01-first-deploy.sh with CAM_DOMAIN and CAM_EMAIL."
