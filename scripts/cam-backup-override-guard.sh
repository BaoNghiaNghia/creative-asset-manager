#!/usr/bin/env bash
# Strictly recognize a harmless, root-owned systemd backup override that
# resolves through CURRENT, rather than pinning an old immutable release.
# Any extra directive, symlink, non-root owner, or writable mode fails closed.
cam_backup_override_uses_current_only() {
  local file="${1:-}" uid mode expected actual
  [[ -n "$file" && -f "$file" && ! -L "$file" ]] || return 1
  uid="$(stat -c %u -- "$file" 2>/dev/null)" || return 1
  mode="$(stat -c %a -- "$file" 2>/dev/null)" || return 1
  [[ "$uid" == "0" && "$mode" =~ ^[0-7]{3,4}$ ]] || return 1
  (( (8#$mode & 022) == 0 )) || return 1
  expected="$(cat <<'CAM_CURRENT_BACKUP_UNIT'
[Service]
WorkingDirectory=/opt/creative-asset-manager/current/apps/api
ExecStart=
ExecStart=/opt/creative-asset-manager/current/apps/api/.venv/bin/python -m app.operations.database_backup_cli backup
CAM_CURRENT_BACKUP_UNIT
)"
  actual="$(cat -- "$file")" || return 1
  [[ "$actual" == "$expected" ]]
}
