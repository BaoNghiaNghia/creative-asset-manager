#!/usr/bin/env bash
set -Eeuo pipefail

SOURCE="${CAM_CURSOR_SOURCE:-/srv/creative-asset-manager-source}"
TARGET="${CAM_CURSOR_TARGET:-/srv/creative-asset-manager-cursor}"
USER_NAME="${CAM_CURSOR_USER:-cursoragent}"
HOME_DIR="${CAM_CURSOR_HOME:-/var/lib/cursoragent}"
SERVICE_NAME="${CAM_CURSOR_SERVICE:-cam-cursor-my-machine.service}"
UNIT_TEMPLATE="${CAM_CURSOR_UNIT_TEMPLATE:-deploy/systemd/cam-cursor-my-machine.service.example}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Run as root"
[[ -d "$SOURCE/.git" ]] || die "Source checkout not found: $SOURCE"
[[ -f "$UNIT_TEMPLATE" ]] || die "Unit template not found: $UNIT_TEMPLATE"

for cmd in git curl install systemctl useradd sudo; do
  command -v "$cmd" >/dev/null 2>&1 || die "Missing required command: $cmd"
done

if ! id "$USER_NAME" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "$HOME_DIR" --shell /bin/bash "$USER_NAME"
fi

install -d -o "$USER_NAME" -g "$USER_NAME" -m 0750 "$HOME_DIR"

if [[ ! -d "$TARGET/.git" ]]; then
  [[ ! -e "$TARGET" ]] || die "Target exists but is not a Git checkout: $TARGET"
  git clone --no-hardlinks "$SOURCE" "$TARGET"
fi

REMOTE_URL="$(git -C "$SOURCE" remote get-url origin 2>/dev/null || true)"
if [[ -n "$REMOTE_URL" ]]; then
  git -C "$TARGET" remote set-url origin "$REMOTE_URL"
fi

chown -R "$USER_NAME:$USER_NAME" "$TARGET"

if [[ ! -x "$HOME_DIR/.local/bin/agent" ]]; then
  tmp_installer="$(mktemp)"
  trap 'rm -f "$tmp_installer"' EXIT
  curl https://cursor.com/install -fsS -o "$tmp_installer"
  chmod 0755 "$tmp_installer"
  sudo -u "$USER_NAME" -H bash "$tmp_installer"
fi

install -o root -g root -m 0644 "$UNIT_TEMPLATE" "/etc/systemd/system/$SERVICE_NAME"
systemctl daemon-reload

cat <<EOF

Cursor CLI and the isolated staging checkout are prepared.

One-time authentication is intentionally NOT automated.
Run:

  sudo -u $USER_NAME -H $HOME_DIR/.local/bin/agent login
  sudo -u $USER_NAME -H $HOME_DIR/.local/bin/agent worker debug

After debug confirms authentication/routing:

  systemctl enable --now $SERVICE_NAME
  systemctl status $SERVICE_NAME --no-pager

Worker name: cam-vps-staging
Worker checkout: $TARGET

Production deploy remains outside this service and must stay a CodeLocal/operator action.
EOF
