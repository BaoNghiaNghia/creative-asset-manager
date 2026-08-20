#!/usr/bin/env bash
set -Eeuo pipefail

DOMAIN_RAW="${CAM_DOMAIN:-https://creative-assets.ddns.net/}"
DOMAIN="${DOMAIN_RAW#http://}"
DOMAIN="${DOMAIN#https://}"
DOMAIN="${DOMAIN%%/*}"

echo "DOMAIN=${DOMAIN}"
echo

echo "=== DNS IPv4 ==="
getent ahostsv4 "${DOMAIN}" || true

echo
echo "=== DNS IPv6 ==="
getent ahostsv6 "${DOMAIN}" || true

echo
echo "=== This VPS public IPv4 ==="
curl -4 -fsS --max-time 10 https://ifconfig.me || true
echo

echo
echo "=== Port 80/443 local listeners ==="
ss -ltn | grep -E '(:80 |:443 )' || true

echo
echo "DOMAIN_PREFLIGHT_DONE=YES"
echo "Ensure DNS A/AAAA records point to this VPS before running first deploy."
