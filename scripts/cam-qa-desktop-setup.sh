#!/usr/bin/env bash
# On-demand, local-only QA desktop. Run --install as root only after reviewing.
# Never touches CAM's API, workers, data, or active releases.
set -Eeuo pipefail
umask 077
SWAP=/swapfile-camqa
QA_USER=cam-qa
DISPLAY_NUM=:90
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
root_only() { [[ $EUID -eq 0 ]] || die "Run as root with sudo."; }
require() { command -v "$1" >/dev/null || die "Missing: $1"; }

status() {
  printf '=== Memory ===\n'; free -h
  printf '=== Swap ===\n'; swapon --show
  printf '=== Root disk ===\n'; df -h /
  printf '=== QA units ===\n'
  systemctl --no-pager --plain is-active cam-qa-desktop.service cam-qa-openbox.service cam-qa-vnc.service cam-qa-novnc.service 2>/dev/null || true
  printf '=== Local VNC sockets ===\n'
  ss -lnt '( sport = :5901 or sport = :6080 )' || true
}

install_swap() {
  local free_mb size
  if [[ -e "$SWAP" ]]; then
    [[ -f "$SWAP" && ! -L "$SWAP" ]] || die "$SWAP exists but is not a regular file."
    size="$(stat -c '%s' "$SWAP")"
    [[ "$size" == 2147483648 ]] || die "$SWAP exists with an unexpected size."
  else
    free_mb="$(df -Pm / | awk 'NR==2{print $4}')"
    [[ "$free_mb" =~ ^[0-9]+$ && "$free_mb" -ge 5120 ]] ||
      die "Need at least 5 GiB free before allocating a 2 GiB swapfile."
    fallocate -l 2G "$SWAP"
    chmod 600 "$SWAP"
  fi
  chmod 600 "$SWAP"
  if ! swapon --show=NAME --noheadings | grep -Fxq "$SWAP"; then
    mkswap "$SWAP" >/dev/null
    swapon "$SWAP"
  fi
  if ! grep -Eq '^/swapfile-camqa[[:space:]]+none[[:space:]]+swap' /etc/fstab; then
    printf '%s\n' '/swapfile-camqa none swap sw,nofail 0 0' >> /etc/fstab
  fi
  printf '2 GiB swap active and persistent.\n'
}

write_unit() {
  local target="/etc/systemd/system/$1"
  if [[ -e "$target" ]] && ! grep -Eq '^Description=CAM (isolated QA|QA )' "$target"; then
    die "Refusing to overwrite an unrelated service: $target"
  fi
  printf '%s\n' "$2" > "$target"
  chmod 644 "$target"
}

install_desktop() {
  root_only
  for tool in systemctl apt-get useradd id ss google-chrome-stable; do require "$tool"; done
  install_swap
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends \
    xvfb openbox x11vnc novnc websockify xauth
  for tool in Xvfb openbox x11vnc websockify xauth mcookie runuser; do require "$tool"; done
  [[ -f /usr/share/novnc/vnc.html ]] || die "noVNC UI is missing."
  if ! id "$QA_USER" >/dev/null 2>&1; then
    useradd --create-home --shell /bin/bash "$QA_USER"
    passwd -l "$QA_USER" >/dev/null || true
  fi
  install -d -m 0700 -o "$QA_USER" -g "$QA_USER" "/home/$QA_USER/.vnc"
  [[ ! -e /tmp/.X11-unix/X90 ]] || die "X display :90 is already in use."
  if [[ ! -f /home/cam-qa/.Xauthority ]]; then
    install -m 0600 -o cam-qa -g cam-qa /dev/null /home/cam-qa/.Xauthority
  fi
  runuser -u cam-qa -- xauth -f /home/cam-qa/.Xauthority add :90 . "$(mcookie)"
  [[ -z "$(ss -lnt '( sport = :5901 or sport = :6080 )' | tail -n +2)" ]] ||
    die "VNC ports 5901 or 6080 already in use."

  write_unit cam-qa-desktop.service "[Unit]
Description=CAM isolated QA Xvfb
After=network.target

[Service]
Type=simple
User=$QA_USER
Group=$QA_USER
Environment=HOME=/home/$QA_USER
ExecStart=/usr/bin/Xvfb $DISPLAY_NUM -screen 0 1366x768x24 -auth /home/$QA_USER/.Xauthority -nolisten tcp
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
UMask=0077"

  write_unit cam-qa-openbox.service "[Unit]
Description=CAM isolated QA Openbox
Requires=cam-qa-desktop.service
After=cam-qa-desktop.service

[Service]
Type=simple
User=$QA_USER
Group=$QA_USER
Environment=HOME=/home/$QA_USER
Environment=DISPLAY=$DISPLAY_NUM
Environment=XAUTHORITY=/home/$QA_USER/.Xauthority
ExecStart=/usr/bin/openbox --sm-disable
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
UMask=0077"

  write_unit cam-qa-vnc.service "[Unit]
Description=CAM QA VNC (loopback only)
Requires=cam-qa-desktop.service
After=cam-qa-desktop.service

[Service]
Type=simple
User=$QA_USER
Group=$QA_USER
Environment=HOME=/home/$QA_USER
Environment=DISPLAY=$DISPLAY_NUM
Environment=XAUTHORITY=/home/$QA_USER/.Xauthority
ExecStart=/usr/bin/x11vnc -display $DISPLAY_NUM -localhost -rfbport 5901 -rfbauth /home/$QA_USER/.vnc/passwd -forever -shared -noxdamage -noxrecord
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
UMask=0077"

  write_unit cam-qa-novnc.service "[Unit]
Description=CAM QA noVNC (loopback only)
Requires=cam-qa-vnc.service
After=cam-qa-vnc.service

[Service]
Type=simple
User=$QA_USER
Group=$QA_USER
Environment=HOME=/home/$QA_USER
ExecStart=/usr/bin/websockify --web=/usr/share/novnc 127.0.0.1:6080 127.0.0.1:5901
Restart=on-failure
RestartSec=4
NoNewPrivileges=true
UMask=0077"

  systemctl daemon-reload
  printf '\nInstalled but NOT started. Set password privately with:\n'
  printf '  sudo -u cam-qa x11vnc -storepasswd /home/cam-qa/.vnc/passwd\n'
  printf 'Then start: sudo bash scripts/cam-qa-desktop-setup.sh --start\n'
}

start_desktop() {
  root_only
  [[ -f /home/cam-qa/.vnc/passwd && ! -L /home/cam-qa/.vnc/passwd ]] ||
    die "Set a cam-qa VNC password first."
  [[ "$(stat -c '%U' /home/cam-qa/.vnc/passwd)" == cam-qa ]] ||
    die "The VNC password file must belong to cam-qa."
  chmod 600 /home/cam-qa/.vnc/passwd
  systemctl start cam-qa-desktop.service
  systemctl start cam-qa-openbox.service
  systemctl start cam-qa-vnc.service
  systemctl start cam-qa-novnc.service
  ss -lnt '( sport = :5901 or sport = :6080 )'
  printf 'Tunnel: ssh -N -L 127.0.0.1:6080:127.0.0.1:6080 USER@VPS\n'
  printf 'Then open http://127.0.0.1:6080/vnc.html\n'
}

stop_desktop() {
  root_only
  systemctl stop cam-qa-novnc.service cam-qa-vnc.service cam-qa-openbox.service cam-qa-desktop.service
  printf 'QA desktop stopped. Production services were not changed.\n'
}

case "${1:---status}" in
  --status|--plan) status ;;
  --install) install_desktop ;;
  --start) start_desktop ;;
  --stop) stop_desktop ;;
  *) die "Usage: $0 [--status|--install|--start|--stop]" ;;
esac
