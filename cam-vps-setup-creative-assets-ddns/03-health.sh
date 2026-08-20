#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE_DIR="${CAM_SOURCE_DIR:-/srv/creative-asset-manager-source}"
DOMAIN_RAW="${CAM_DOMAIN:-https://creative-assets.ddns.net/}"
DOMAIN="${DOMAIN_RAW#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"

echo "=== Creative Asset Manager health ==="
echo "timestamp=$(date -Is)"
echo

if [[ -x "${SOURCE_DIR}/deploy/bin/cam-deploy" ]]; then
  sudo "${SOURCE_DIR}/deploy/bin/cam-deploy" diagnostics || true
else
  echo "cam-deploy unavailable"
fi

echo
echo "=== systemd ==="
systemctl --no-pager --full status creative-asset-manager-api.service 2>/dev/null | sed -n '1,8p' || true
systemctl --no-pager --full status creative-asset-manager-worker.service 2>/dev/null | sed -n '1,8p' || true

echo
echo "=== local endpoints ==="
for url in \
  http://127.0.0.1:8000/live \
  http://127.0.0.1:8000/ready \
  http://127.0.0.1:8081/live \
  http://127.0.0.1:8081/health \
  http://127.0.0.1:9200/_cluster/health
do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 5 "$url" || true)"
  printf '%-55s %s\n' "$url" "$code"
done

echo
echo "=== PostgreSQL ==="
pg_isready -h 127.0.0.1 -p 5432 || true

echo
echo "=== listening TCP sockets ==="
ss -ltn | grep -E '(:80 |:443 |:8000 |:8081 |:5432 |:9200 )' || true

if [[ -n "${DOMAIN}" ]]; then
  echo
  echo "=== public ==="
  curl -fsS -o /dev/null -w "https://${DOMAIN}/live -> %{http_code}\n" "https://${DOMAIN}/live" || true
  echo | openssl s_client -servername "${DOMAIN}" -connect "${DOMAIN}:443" 2>/dev/null \
    | openssl x509 -noout -subject -issuer -dates || true
fi
