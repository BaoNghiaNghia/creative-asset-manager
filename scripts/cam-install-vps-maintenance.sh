#!/usr/bin/env bash
# Root-operated install for a bounded VPS maintenance CLI.
# Does not alter the current root-owned CodeLocal runtime or its host policy.
set -Eeuo pipefail
umask 077
usage() {
  cat <<'EOF'
Usage:
  bash scripts/cam-install-vps-maintenance.sh --check
  sudo bash scripts/cam-install-vps-maintenance.sh --install --operator DEDICATED_NON_ROOT_ACCOUNT

The operator must already exist, must not be root or creative-assets.
This does not authorize root shell, network changes or CodeLocal policy bypass.
EOF
}
action="--check"
if (($#)); then action="$1"; shift; fi
operator=""
while (($#)); do
  case "$1" in
    --operator)
      [[ $# -ge 2 ]] || { echo "Missing --operator value" >&2; exit 2; }
      operator="$2"; shift 2 ;;
    *) usage >&2; exit 2 ;;
  esac
done
case "$action" in
  --check)
    echo "Current CodeLocal process identity:"
    ps -C node -o user,pid,args 2>/dev/null | grep -E '(^USER|codelocal)' || true
    echo "Root installed broker:"
    if [[ -e /usr/local/sbin/cam-vps-maintenance ]]; then
      stat -c '%U:%G %a %n' /usr/local/sbin/cam-vps-maintenance
    else
      echo "not installed"
    fi
    echo "Sudoers policy:"
    if [[ -e /etc/sudoers.d/cam-vps-maintenance ]]; then
      stat -c '%U:%G %a %n' /etc/sudoers.d/cam-vps-maintenance
    else
      echo "not installed"
    fi
    exit 0 ;;
  --install) ;;
  *) usage >&2; exit 2 ;;
esac
[[ $EUID -eq 0 ]] || { echo "--install requires root" >&2; exit 2; }
[[ "$operator" =~ ^[a-z_][a-z0-9_-]{0,30}$ ]] ||
  { echo "Invalid operator identity" >&2; exit 2; }
id "$operator" >/dev/null 2>&1 ||
  { echo "Operator account does not exist" >&2; exit 2; }
[[ "$(id -u "$operator")" != 0 && "$operator" != "creative-assets" ]] ||
  { echo "Refusing root or application service identity" >&2; exit 2; }
command -v visudo >/dev/null || { echo "visudo required" >&2; exit 2; }
root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
source_file="$root/scripts/cam-vps-maintenance.py"
[[ -f "$source_file" && ! -L "$source_file" ]] || { echo "Reviewed CLI not found" >&2; exit 2; }
python3 -m py_compile "$source_file"
install -d -o root -g root -m 0755 /usr/local/sbin
install -d -o root -g root -m 0755 /etc/sudoers.d
target="/usr/local/sbin/cam-vps-maintenance"
if [[ -f "$target" && ! -L "$target" ]]; then
  install -d -o root -g root -m 0700 /var/backups/cam-vps-maintenance
  install -o root -g root -m 0700 "$target" \
    "/var/backups/cam-vps-maintenance/cli-$(date -u +%Y%m%dT%H%M%SZ)"
fi
install -o root -g root -m 0755 "$source_file" "$target"
policy_temp="$(mktemp)"
trap 'rm -f -- "$policy_temp"' EXIT
cat > "$policy_temp" <<EOF
# Precise actions only; do not add wildcard arguments.
Cmnd_Alias CAM_VPS_MAINT = /usr/local/sbin/cam-vps-maintenance status, /usr/local/sbin/cam-vps-maintenance network-audit, /usr/local/sbin/cam-vps-maintenance swap-repair, /usr/local/sbin/cam-vps-maintenance swap-rollback, /usr/local/sbin/cam-vps-maintenance stage1-retention-retry
Defaults!CAM_VPS_MAINT !setenv, use_pty, log_output
$operator ALL=(root) NOPASSWD: CAM_VPS_MAINT
EOF
chmod 0440 "$policy_temp"
visudo -cf "$policy_temp" >/dev/null
install -o root -g root -m 0440 "$policy_temp" /etc/sudoers.d/cam-vps-maintenance
visudo -cf /etc/sudoers.d/cam-vps-maintenance >/dev/null
# Retain a short, root-only audit history; the broker reopens the log each run.
if command -v logrotate >/dev/null 2>&1; then
  cat > /etc/logrotate.d/cam-vps-maintenance <<'ROTATE'
/var/log/cam-vps-maintenance/audit.jsonl {
  daily
  rotate 30
  missingok
  notifempty
  compress
  delaycompress
  create 0600 root root
}
ROTATE
  chown root:root /etc/logrotate.d/cam-vps-maintenance
  chmod 0644 /etc/logrotate.d/cam-vps-maintenance
fi
logger -t cam-vps-maintenance-install -- "Scoped maintenance CLI installed for non-root operator"
echo "Root-owned CLI and exact-argument sudoers policy installed."
echo "No CodeLocal service identity or network settings changed."
