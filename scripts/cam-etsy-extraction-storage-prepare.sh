#!/usr/bin/env bash
# Prepare isolated, persistent Etsy extraction PNG storage for a one-job pilot.
# No credential handling, mass queue activation or automatic data deletion.
set -euo pipefail
mode="--check"
if [[ "$#" -gt 0 ]]; then mode="$1"; fi
if [[ "$mode" != "--check" && "$mode" != "--apply" ]]; then
  echo "Usage: cam-etsy-extraction-storage-prepare.sh [--check|--apply]" >&2
  exit 2
fi
env_file="/etc/creative-asset-manager/production.env"
storage_root="/var/lib/creative-asset-manager/etsy-design-extraction"
service="creative-asset-manager-api.service"
quota_bytes=2147483648
min_headroom_bytes=3221225472
if [[ ! -r "$env_file" ]]; then
  echo "Required production environment file is not readable" >&2
  exit 1
fi
run_user="$(systemctl show "$service" -p User --value)"
run_group="$(systemctl show "$service" -p Group --value)"
if [[ -z "$run_user" || -z "$run_group" || "$run_user" == "root" ]]; then
  echo "Unexpected API service identity" >&2
  exit 1
fi
if grep -Eq '^CAM_ETSY_EXTRACTION_STORAGE_ROOT=' "$env_file"; then
  if ! grep -Fxq "CAM_ETSY_EXTRACTION_STORAGE_ROOT=$storage_root" "$env_file"; then
    echo "Existing storage root differs from managed path; require manual review" >&2
    exit 1
  fi
  configured="yes"
else
  configured="no"
fi
if [[ "$mode" == "--check" ]]; then
  echo "mode=check"
  echo "storage_root_configured=$configured"
  if [[ -d "$storage_root" ]]; then echo "storage_directory=exists"
  else echo "storage_directory=missing"; fi
  echo "pilot_cap=1 (recommended; not modified in check mode)"
  echo "extraction_worker=unchanged"
  exit 0
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "Storage apply requires root privileges" >&2
  exit 1
fi
install -d -o "$run_user" -g "$run_group" -m 0700 "$storage_root"
if [[ "$configured" == "no" ]]; then
  printf '\nCAM_ETSY_EXTRACTION_STORAGE_ROOT=%s\n' "$storage_root" >> "$env_file"
fi
if ! grep -Eq '^CAM_ETSY_EXTRACTION_DAILY_CAP=' "$env_file"; then
  printf 'CAM_ETSY_EXTRACTION_DAILY_CAP=1\n' >> "$env_file"
fi
if ! grep -Eq '^CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES=' "$env_file"; then
  printf 'CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES=%s\n' "$quota_bytes" >> "$env_file"
fi
if ! grep -Eq '^CAM_ETSY_EXTRACTION_MIN_FREE_BYTES=' "$env_file"; then
  printf 'CAM_ETSY_EXTRACTION_MIN_FREE_BYTES=%s\n' "$min_headroom_bytes" >> "$env_file"
fi
echo "status=provisioned"
echo "storage_mode=0700"
echo "storage_owner=$run_user"
echo "default_quota_bytes=$quota_bytes"
echo "minimum_free_bytes=$min_headroom_bytes"
echo "extraction_worker=unchanged"
echo "NOTE: API reload required to load environment; extraction is not enabled."
