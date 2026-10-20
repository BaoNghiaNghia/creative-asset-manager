#!/usr/bin/env bash
# Fix only CODEX_HOME/tmp directory ownership. No data deletion.
set -Eeuo pipefail
MODE=dry-run
case "${1:-}" in
  --apply) MODE=apply ;;
  --dry-run|"") ;;
  *) echo "Usage: $0 [--dry-run|--apply]" >&2; exit 2 ;;
esac
SERVICE_USER=creative-assets
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  echo "Service account not found" >&2; exit 1
fi
if [[ "$MODE" == apply && "$(id -u)" != 0 ]]; then
  echo "Apply requires root" >&2; exit 1
fi
echo "Codex tmp owner preflight: $MODE"
for home in /var/lib/creative-asset-manager/codex /var/lib/creative-asset-manager/codex-account-2; do
  [[ -d "$home" && ! -L "$home" ]] || { echo "SKIP unexpected home: $home"; continue; }
  tmp="$home/tmp"
  if [[ -L "$tmp" ]]; then
    echo "SKIP symlink tmp: $tmp" >&2
    continue
  fi
  if [[ -e "$tmp" && ! -d "$tmp" ]]; then
    echo "SKIP non-directory tmp: $tmp" >&2
    continue
  fi
  echo "PREVIOUS $(stat -c '%U:%G %a' "$tmp" 2>/dev/null || echo 'not created') $tmp"
  if [[ "$MODE" == apply ]]; then
    # install -d adjusts ONLY the exact directory, not its contents.
    install -d -m 0700 -o "$SERVICE_USER" -g "$SERVICE_USER" -- "$tmp"
    echo "UPDATED  $(stat -c '%U:%G %a' "$tmp") $tmp"
  else
    echo "WOULD set owner creative-assets:creative-assets and directory mode 0700."
  fi

  # Root-run Codex commands can leave stale arg0 child directories owned by
  # root. One unwritable child causes Codex's own stale-arg0 sweep to fail.
  # Repair ONLY stale, single-level codex-arg0* children, not all of HOME/tmp.
  arg0="$tmp/arg0"
  if [[ -d "$arg0" && ! -L "$arg0" ]]; then
    while IFS= read -r -d '' child; do
      basename="${child##*/}"
      [[ "$basename" =~ ^codex-arg0[A-Za-z0-9]{6,}$ ]] || continue
      [[ -d "$child" && ! -L "$child" ]] || continue
      if [[ "$(stat -c '%u' "$child")" == 0 ]]; then
        echo "STALE ROOT-OWNED arg0 child: $basename"
        if [[ "$MODE" == apply ]]; then
          chown --no-dereference "$SERVICE_USER:$SERVICE_USER" -- "$child"
          chmod 0700 -- "$child"
          echo "REPAIRED arg0 child owner: $basename"
        fi
      fi
      # Codex opens .lock as O_RDWR. A root-owned 0644 lock remains
      # unwritable even when the parent directory owner was fixed.
      lock="$child/.lock"
      if [[ -f "$lock" && ! -L "$lock" && "$(stat -c '%u' "$lock")" == 0 ]]; then
        echo "STALE ROOT-OWNED lock in $basename"
        if [[ "$MODE" == apply ]]; then
          chown --no-dereference "$SERVICE_USER:$SERVICE_USER" -- "$lock"
          chmod 0600 -- "$lock"
          echo "REPAIRED arg0 lock owner: $basename"
        fi
      fi
    done < <(find "$arg0" -mindepth 1 -maxdepth 1 -type d -name 'codex-arg0*' -mmin +120 -print0 2>/dev/null)
  fi
done
echo "No Codex media, sessions, auth, or file contents removed."
