#!/usr/bin/env bash
# CAM production cleanup; DRY-RUN is the default.
# ONLY purge obsolete immutable releases and rebuildable CAM pip cache.
# No changes to databases, Docker, Codex, models, backups, media or syslog.
set -Eeuo pipefail
export LC_ALL=C
MODE=dry-run
VACUUM=0
MIN_AGE=8
RELEASE_ROOT=/opt/creative-asset-manager/releases
CURRENT=/opt/creative-asset-manager/current
PREVIOUS=/opt/creative-asset-manager/previous
PIP_CACHE=/var/cache/creative-asset-manager/pip

usage() {
  cat <<'HELP'
CAM safe disk cleanup:
  sudo bash scripts/cam-safe-disk-cleanup.sh --dry-run
  sudo bash scripts/cam-safe-disk-cleanup.sh --apply
  sudo bash scripts/cam-safe-disk-cleanup.sh --apply --vacuum-journal
Options:
  --dry-run           report only (DEFAULT)
  --apply             delete verified obsolete releases, purge CAM pip cache
  --vacuum-journal    optionally trim archived systemd journal to 250 MB
  --min-age-hours N   protect releases younger than N hours (default 8)
NEVER removes current/previous releases, databases, Elasticsearch, Docker
volumes, Codex session state, models, swap, logs, browsers or user assets.
HELP
}
while (($#)); do
  case "$1" in
    --dry-run) MODE=dry-run; shift;;
    --apply) MODE=apply; shift;;
    --vacuum-journal) VACUUM=1; shift;;
    --min-age-hours) (($#>=2)) || exit 2; MIN_AGE="$2"; shift 2;;
    --help|-h) usage; exit 0;;
    *) echo "Unknown option: $1" >&2; exit 2;;
  esac
done
[[ "$MIN_AGE" =~ ^[0-9]+$ ]] && ((MIN_AGE>=1 && MIN_AGE<=720)) ||
  { echo "Invalid min-age-hours" >&2; exit 2; }
if [[ "$MODE" == apply && "$(id -u)" != 0 ]]; then
  echo "Apply requires sudo/root." >&2; exit 1
fi
human() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || echo "$1 bytes"; }
bytes() { du -sxB1 -- "$1" 2>/dev/null | awk 'NR==1 {print $1}' || true; }
release_ok() {
  local path="$1" name
  [[ "$path" == "$RELEASE_ROOT/"* ]] || return 1
  name="${path#"$RELEASE_ROOT/"}"
  [[ "$name" =~ ^[0-9a-f]{40}$ ]] && [[ "$path" == "$RELEASE_ROOT/$name" ]] &&
    [[ -d "$path" ]] && [[ ! -L "$path" ]]
}
echo "CAM cleanup - $(date -Is) - $MODE"
df -hP /
echo "== Protected release pointers =="
safe=1
[[ -L "$CURRENT" && -d "$RELEASE_ROOT" ]] || safe=0
active="$(readlink -f -- "$CURRENT" 2>/dev/null || true)"
rollback=""
had_previous=0
if ! release_ok "$active"; then safe=0; fi
if [[ -L "$PREVIOUS" ]]; then
  had_previous=1
  rollback="$(readlink -f -- "$PREVIOUS" 2>/dev/null || true)"
elif [[ -e "$PREVIOUS" ]]; then
  safe=0
  echo "SKIP: previous path exists but is not a symlink"
else
  # Deploys with KEEP_RELEASES=1 deliberately remove the PREVIOUS symlink.
  # Preserve the most recent older *valid* immutable release as an offline
  # fallback without pretending the backend rollback command is configured.
  latest=-1
  for candidate in "$RELEASE_ROOT"/*; do
    release_ok "$candidate" || continue
    [[ "$candidate" != "$active" ]] || continue
    [[ -x "$candidate/apps/api/.venv/bin/python" ]] || continue
    timestamp="$(stat -c %Y -- "$candidate" 2>/dev/null || true)"
    [[ "$timestamp" =~ ^[0-9]+$ ]] || continue
    if ((timestamp>latest)); then
      latest="$timestamp"
      rollback="$candidate"
    fi
  done
  echo "No PREVIOUS symlink: choosing newest older valid release as offline fallback."
fi
if ! release_ok "$rollback" || [[ "$active" == "$rollback" ]]; then safe=0; fi
# A temporary dedicated backup service can pin an immutable backend release
# not referenced by CURRENT/PREVIOUS. Never remove a pinned backup runtime.
BACKUP_OVERRIDE=/etc/systemd/system/creative-asset-manager-db-backup.service.d/90-cam-isolated-backup-runtime.conf
pinned_backup=""
if [[ -e "$BACKUP_OVERRIDE" || -L "$BACKUP_OVERRIDE" ]]; then
  if [[ -L "$BACKUP_OVERRIDE" || ! -f "$BACKUP_OVERRIDE" ]] ||
     ! grep -Fxq "# CAM isolated backup runtime v1" "$BACKUP_OVERRIDE"; then
    safe=0
    echo "SKIP: untrusted isolated backup runtime override"
  else
    for candidate in "$RELEASE_ROOT"/*; do
      release_ok "$candidate" || continue
      if grep -Fxq "ExecStart=$candidate/apps/api/.venv/bin/python -m app.operations.database_backup_cli backup" "$BACKUP_OVERRIDE"; then
        pinned_backup="$candidate"
        break
      fi
    done
    if [[ -z "$pinned_backup" ]]; then
      safe=0
      echo "SKIP: isolated backup runtime cannot be validated"
    fi
  fi
fi
echo "CURRENT : ${active:-unresolved}"
echo "FALLBACK: ${rollback:-unresolved} (previous symlink exists: $had_previous)"
[[ -z "$pinned_backup" ]] || echo "BACKUP RUNTIME: $pinned_backup (protected)"
echo "== Verified obsolete immutable releases =="
potential=0
count=0
now="$(date +%s)"
if ((safe)); then
  for target in "$RELEASE_ROOT"/*; do
    [[ -d "$target" ]] || continue
    if ! release_ok "$target"; then echo "SKIP unknown path: $target"; continue; fi
    size="$(bytes "$target")"
    [[ "$size" =~ ^[0-9]+$ ]] || { echo "SKIP unmeasurable: $target"; continue; }
    if [[ "$target" == "$active" || "$target" == "$rollback" || "$target" == "$pinned_backup" ]]; then
      echo "PROTECT $(human "$size") $target"; continue
    fi
    ts="$(stat -c %Y -- "$target" 2>/dev/null || true)"
    if [[ ! "$ts" =~ ^[0-9]+$ ]] || ((now-ts<MIN_AGE*3600)); then
      echo "SKIP recent: $target"; continue
    fi
    if ! command -v pgrep >/dev/null 2>&1 ||
       pgrep -af -- "$target" >/dev/null 2>&1; then
      echo "SKIP in-use or cannot check processes: $target"; continue
    fi
    potential=$((potential+size))
    count=$((count+1))
    echo "CANDIDATE $(human "$size") $target"
    if [[ "$MODE" == apply ]]; then
      a="$(readlink -f -- "$CURRENT" || true)"
      b="$rollback"
      if ((had_previous)); then
        b="$(readlink -f -- "$PREVIOUS" || true)"
      elif [[ -e "$PREVIOUS" || -L "$PREVIOUS" ]]; then
        b="previous_pointer_changed"
      fi
      if [[ "$a" != "$active" || "$b" != "$rollback" ||
            "$target" == "$a" || "$target" == "$b" || "$target" == "$pinned_backup" ]] ||
         ! release_ok "$target" ||
         pgrep -af -- "$target" >/dev/null 2>&1; then
        echo "SKIP changed release state: $target"
        continue
      fi
      rm -rf --one-file-system -- "$target"
      echo "REMOVED $target"
    fi
  done
else
  echo "SKIP ALL RELEASES: current and previous must be distinct valid release links."
fi
echo "Obsolete releases: $count; estimated savings $(human "$potential")"
echo "== Rebuildable pip cache =="
if [[ -d "$PIP_CACHE" && ! -L "$PIP_CACHE" ]]; then
  cache_bytes="$(bytes "$PIP_CACHE")"
  [[ "$cache_bytes" =~ ^[0-9]+$ ]] || cache_bytes=0
  echo "Pip cache: $(human "$cache_bytes") ($PIP_CACHE)"
  if [[ "$MODE" == apply ]]; then
    # The OS Python may not have pip installed. Deploys use the release venv,
    # and this cache is owned by the creative-assets service account.
    pip_python="$active/apps/api/.venv/bin/python"
    if [[ -x "$pip_python" ]] &&
       "$pip_python" -m pip --version >/dev/null 2>&1 &&
       ! pgrep -af 'python.* -m pip|[/ ]pip(3)? (install|download|wheel)' >/dev/null 2>&1; then
      runuser -u creative-assets -- env PIP_CACHE_DIR="$PIP_CACHE" \
        "$pip_python" -m pip cache purge ||
        echo "WARNING: pip cache purge unsuccessful"
    else
      echo "SKIP: active pip process or production virtualenv pip unavailable"
    fi
  fi
else
  echo "No regular CAM pip cache found"
fi
echo "== Optional archived journal vacuum =="
if ((VACUUM)); then
  if [[ "$MODE" == apply ]]; then
    journalctl --vacuum-size=250M || echo "WARNING: journal vacuum failed"
  else
    echo "DRY-RUN: would vacuum archived journal files to 250M"
  fi
else
  echo "No journal changes. Use --vacuum-journal to opt in."
fi
echo "== Never automatically removed =="
echo "Codex accounts, model weights, PostgreSQL, Elasticsearch, Docker volumes,"
echo "swap, syslog, current/previous releases, backups, browser state, user media."
if [[ "$MODE" == dry-run ]]; then
  echo "DRY-RUN DONE. Nothing deleted."
else
  echo "APPLY DONE. Check API health and remaining disk space."
fi
df -hP /
