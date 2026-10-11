#!/usr/bin/env bash
# Configure only CAM's isolated Agent-Reach HOME; never touch user/global yt-dlp.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
HOME_DIR="$ROOT/integrations/agent-reach/runtime-home"
CONFIG_DIR="$HOME_DIR/.config/yt-dlp"
CONFIG="$CONFIG_DIR/config"

if ! command -v node >/dev/null 2>&1; then
  echo "Agent-Reach YouTube remains unavailable: Node.js runtime not installed." >&2
  exit 0
fi
# Refuse to follow symlinks from our isolated runtime into another location.
for path in "$HOME_DIR" "$HOME_DIR/.config" "$CONFIG_DIR" "$CONFIG"; do
  if [[ -L "$path" ]]; then
    echo "Refusing symlink in isolated yt-dlp configuration: $path" >&2
    exit 2
  fi
done
mkdir -p -m 0700 "$CONFIG_DIR"
if [[ ! -e "$CONFIG" ]]; then
  (umask 077; printf '%s\n' '--js-runtimes node' > "$CONFIG")
elif ! grep -Eq '^--js-runtimes[[:space:]]+node([[:space:]]|$)' "$CONFIG"; then
  printf '%s\n' '--js-runtimes node' >> "$CONFIG"
fi
chmod 0600 "$CONFIG"
echo "CAM Agent-Reach isolated YouTube JS runtime configured."
