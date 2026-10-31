#!/usr/bin/env bash
# Repair a stale isolated PostgreSQL backup runtime after its release was
# retired. This does NOT run a database backup or change any DB files.
#
# Run as root on the VPS with systemd-directory authorization:
#   bash scripts/ops-repair-db-backup-systemd-runtime.sh --apply
set -euo pipefail

unit="creative-asset-manager-db-backup.service"
timer="creative-asset-manager-db-backup.timer"
dropin="/etc/systemd/system/${unit}.d/90-cam-isolated-backup-runtime.conf"
retired="61e7da54cb280c0e3a1b5ee240b5f04425923cf7"
base="/opt/creative-asset-manager"
compat="${base}/releases/${retired}"
mode="${1:---check}"

if [[ "$mode" != "--check" && "$mode" != "--apply" ]]; then
  echo "Usage: $0 [--check|--apply]" >&2
  exit 2
fi

# Never manipulate an active backup. A one-shot may be creating a large dump.
if systemctl is-active --quiet "$unit"; then
  echo "ABORT: backup job is currently active." >&2
  exit 1
fi
test -x "${base}/current/apps/api/.venv/bin/python"
test -f "${base}/current/apps/api/app/operations/database_backup_cli.py"
systemctl cat "$unit" --no-pager | grep -Fq 'WorkingDirectory=./current/apps/api'

if [[ ! -f "$dropin" ]]; then
  echo "Backup service has no old runtime drop-in; nothing to repair."
  systemctl show "$unit" -p WorkingDirectory -p ExecStart
  exit 0
fi
if ! grep -Fq "$retired" "$dropin" || ! grep -Fq 'Temporary pinned runtime' "$dropin"; then
  echo "ABORT: backup drop-in differs from the inspected CAM version." >&2
  exit 1
fi

echo "Found stale backup runtime drop-in: $dropin"
echo "Current release Python exists; timer is kept unchanged."
if [[ "$mode" == "--check" ]]; then
  echo "DRY_RUN: use --apply to disable just the obsolete drop-in and reload systemd."
  exit 0
fi
if [[ $EUID -ne 0 ]]; then
  echo "ABORT: --apply requires root." >&2
  exit 1
fi
disabled="${dropin}.disabled"
if [[ -e "$disabled" ]]; then
  echo "ABORT: disabled config already exists; refusing overwrite." >&2
  exit 1
fi

mv -- "$dropin" "$disabled"
if ! systemctl daemon-reload; then
  mv -- "$disabled" "$dropin"
  systemctl daemon-reload || true
  echo "FAIL: daemon-reload; original drop-in restored." >&2
  exit 1
fi

actual="$(systemctl show "$unit" -p WorkingDirectory -p ExecStart)"
if [[ "$actual" == *"$retired"* || "$actual" != *"./current/apps/api"* ]]; then
  mv -- "$disabled" "$dropin"
  systemctl daemon-reload
  echo "FAIL: unexpected effective unit; original drop-in restored." >&2
  exit 1
fi

echo "BACKUP_SYSTEMD_RUNTIME_REPAIRED"
printf '%s\n' "$actual"
systemctl list-timers "$timer" --no-pager

# Clean the tiny compatibility symlink only when it is ours and no unit still
# references its retired release. Do not follow it or delete its target.
if [[ -L "$compat" && ! -e "$dropin" ]]; then
  resolved="$(readlink -f "$compat" || true)"
  current="$(readlink -f "${base}/current" || true)"
  if [[ -n "$resolved" && "$resolved" == "$current" ]]; then
    rm -- "$compat"
    echo "Removed obsolete runtime compatibility symlink."
  fi
fi

echo "No backup was run. PostgreSQL and Elasticsearch were not modified."
