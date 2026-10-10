#!/usr/bin/env bash
# Conservative VPS disk inspection and opt-in cleanup.
# Never prunes Docker, active/previous app releases, PostgreSQL, Elasticsearch,
# user assets, Google Drive, Cloudflare R2, skill outputs, or visual baselines.
set -Eeuo pipefail
mode="${1:---audit}"
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
show_path() {
  if [[ -e "$1" ]]; then
    du -sh -- "$1" 2>/dev/null || true
  fi
}
audit() {
  printf '== Disk and memory ==\n'; df -h /; free -h
  printf '\n== Safe cache/log candidates ==\n'
  show_path /var/cache/apt/archives
  show_path /var/log/journal
  show_path /root/.cache
  show_path /home/cam-qa/.cache
  journalctl --disk-usage 2>/dev/null || true
  printf '\n== Critical directories (INVENTORY ONLY) ==\n'
  show_path /opt/creative-asset-manager/releases
  show_path /var/www/creative-asset-manager/releases
  show_path /var/lib/docker
  show_path /srv/creative-asset-manager-source
  show_path /root/creative-asset-manager
  printf '\n== Container usage (INVENTORY ONLY) ==\n'
  if command -v docker >/dev/null 2>&1; then docker system df 2>/dev/null || true; fi
  printf '\nNo production releases, asset folders or Docker volumes were deleted.\n'
}
safe_cleanup() {
  [[ $EUID -eq 0 ]] || die "Run safe cleanup as root."
  printf '== Before ==\n'; df -h /
  printf '\n== Safe cleanup: downloaded .deb package cache ==\n'
  apt-get clean
  printf '\n== Safe cleanup: archived systemd journal older than 14 days ==\n'
  journalctl --vacuum-time=14d
  printf '\n== After ==\n'; df -h /
  printf 'Only apt package cache and old archived journals were cleaned.\n'
  printf 'Unapproved app releases, Docker images/volumes, and creative assets were preserved.\n'
}
case "$mode" in
  --audit|--dry-run) audit ;;
  --clean-safe) safe_cleanup ;;
  *) die "Usage: $0 [--audit|--clean-safe]" ;;
esac
