#!/usr/bin/env bash
# Read-only, non-secret disk audit. Run on the VPS with sufficient filesystem
# permissions; this script NEVER deletes assets, backups or DB files.
set -Eeuo pipefail
echo '== Disk and inode usage =='
df -hP
df -iP
echo '== Top-level data directories (same filesystem) =='
for dir in /opt/creative-asset-manager /var/lib/creative-asset-manager /var/log /var/cache /tmp /root/creative-asset-manager /root/.cache /root/.npm; do
  if [[ -d "$dir" ]]; then
    echo "-- $dir"
    du -xhd1 "$dir" 2>/dev/null | sort -h | tail -15 || true
  fi
done
echo '== Docker allocation (read only) =='
if command -v docker >/dev/null 2>&1; then
  docker system df || true
fi
echo '== systemd journal allocation =='
if command -v journalctl >/dev/null 2>&1; then
  journalctl --disk-usage || true
fi
echo '== Large files under CAM-managed paths (read only, <= 25 records each) =='
for dir in /opt/creative-asset-manager /var/lib/creative-asset-manager /root/creative-asset-manager; do
  if [[ -d "$dir" ]]; then
    find "$dir" -xdev -type f -size +250M -printf '%s %p\n' 2>/dev/null |
      sort -nr | head -25 || true
  fi
done
echo '== Finished: no files removed =='
