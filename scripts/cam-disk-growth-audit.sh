#!/usr/bin/env bash
# CAM VPS disk growth diagnostics. No user data, logs, media, model or release deletion.
set -Eeuo pipefail
export LC_ALL=C
umask 077
TOP=30
MIN_MIB=50
SAVE=0
STATE_DIR=/var/lib/creative-asset-manager/disk-growth-audit

usage() {
  cat <<'HELP'
Usage: bash scripts/cam-disk-growth-audit.sh [--save] [--top N] [--min-mib N] [--state-dir DIR]
Default: read-only audit across the root partition.
--save: store a small timestamped TSV snapshot and compare with the previous one.
No Docker prune, journal vacuum, user data deletion, or service restart.
HELP
}
while (($#)); do
  case "$1" in
    --save) SAVE=1; shift ;;
    --top|--min-mib|--state-dir)
      (($# >= 2)) || { echo "Missing value for $1" >&2; exit 2; }
      case "$1" in
        --top) TOP="$2" ;;
        --min-mib) MIN_MIB="$2" ;;
        --state-dir) STATE_DIR="$2" ;;
      esac
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done
[[ "$TOP" =~ ^[0-9]+$ ]] && ((TOP >= 1 && TOP <= 200)) || { echo "Invalid --top" >&2; exit 2; }
[[ "$MIN_MIB" =~ ^[0-9]+$ ]] && ((MIN_MIB >= 1 && MIN_MIB <= 10240)) || { echo "Invalid --min-mib" >&2; exit 2; }
if ((SAVE)); then [[ "$STATE_DIR" = /* ]] || { echo "State directory must be absolute" >&2; exit 2; }; fi

scratch="$(mktemp -d)"
trap 'rm -f -- "$scratch/snapshot.tsv" "$scratch/files.tsv" "$scratch/all-files.tsv" "$scratch/find-errors.txt"; rmdir -- "$scratch"' EXIT
snapshot="$scratch/snapshot.tsv"
files="$scratch/files.tsv"
: > "$snapshot"
fmt() { numfmt --to=iec --suffix=B "$1" 2>/dev/null || printf '%sB' "$1"; }
section() { printf '\n== %s ==\n' "$1"; }
echo "CAM disk audit | $(date -Is) | host $(hostname)"
section "Disk and inode usage"
df -hP /
df -iP /
used="$(df -B1 --output=used / | tail -1 | tr -d '[:space:]')"
printf 'df\t/\t%s\n' "$used" >> "$snapshot"

section "Tracked directory sizes (nested paths must not be added together)"
while IFS= read -r p; do
  [[ -d "$p" ]] || continue
  bytes="$(du -sxB1 -- "$p" 2>/dev/null | awk 'NR==1 {print $1}')" || true
  [[ "$bytes" =~ ^[0-9]+$ ]] || continue
  printf '%12s  %s\n' "$(fmt "$bytes")" "$p"
  printf 'dir\t%s\t%s\n' "$p" "$bytes" >> "$snapshot"
done <<'PATHS'
/var/lib/creative-asset-manager
/var/lib/creative-asset-manager/models
/var/lib/creative-asset-manager/codex
/var/lib/creative-asset-manager/codex-account-2
/var/lib/creative-asset-manager/visual-encoder-runtime
/var/lib/creative-asset-manager/error-logger
/var/lib/creative-asset-manager/database-backup
/opt/creative-asset-manager
/opt/creative-asset-manager/releases
/root/creative-asset-manager
/root/creative-asset-manager/.worktrees
/root/creative-asset-manager/apps
/root/creative-asset-manager/stage5-release-20261019
/var/lib/docker
/var/lib/docker/volumes
/var/lib/postgresql
/var/log
/var/log/journal
/var/log/creative-asset-manager
/var/cache
/var/cache/creative-asset-manager
/root/.cache
/root/.cache/ms-playwright
/root/.npm
/tmp
PATHS

section "Which subdirectories are large?"
for p in /opt/creative-asset-manager/releases /var/lib/creative-asset-manager /var/lib/docker /var/log /var/cache/creative-asset-manager /root/creative-asset-manager/.worktrees; do
  [[ -d "$p" ]] || continue
  printf -- '-- %s\n' "$p"
  du -xhd1 -- "$p" 2>/dev/null | sort -h | tail -12 || true
done

section "Largest files (entire root partition; file contents not read)"
find / -xdev -type f -size +"$MIN_MIB"M -printf '%s\t%T@\t%p\n' > "$scratch/all-files.tsv" 2> "$scratch/find-errors.txt" || true
sort -t $'\t' -k1,1nr "$scratch/all-files.tsv" > "$files"
if [[ -s "$scratch/find-errors.txt" ]]; then echo "Notice: some paths could not be scanned; run as root."; fi
printf 'Files exceeding %s MiB: %s\n' "$MIN_MIB" "$(wc -l < "$files")"
n=0
while IFS=$'\t' read -r bytes modified filepath; do
  [[ "$bytes" =~ ^[0-9]+$ ]] || continue
  n=$((n+1))
  ((n <= TOP)) || break
  when="$(date -d "@$(printf '%s' "$modified" | cut -d. -f1)" '+%F %T' 2>/dev/null || printf '?')"
  printf '%3d  %10s  %s  %s\n' "$n" "$(fmt "$bytes")" "$when" "$filepath"
done < "$files"
while IFS=$'\t' read -r bytes modified filepath; do
  [[ "$bytes" =~ ^[0-9]+$ ]] || continue
  printf 'file\t%s\t%s\n' "$filepath" "$bytes" >> "$snapshot"
done < "$files"

section "Docker allocation (live volumes are not reclaimable)"
if command -v docker >/dev/null 2>&1; then
  docker system df || true
  docker ps --size --format 'table {{.Names}}\t{{.Size}}' 2>/dev/null || true
fi
section "Systemd journal"
if command -v journalctl >/dev/null 2>&1; then journalctl --disk-usage || true; fi
section "Noisiest syslog writers (sample of 20000 lines; no messages disclosed)"
for logfile in /var/log/syslog /var/log/syslog.1; do
  [[ -f "$logfile" ]] || continue
  printf '%s: %s\n' "$logfile" "$(du -h -- "$logfile" 2>/dev/null | awk '{print $1}')"
  tail -n 20000 "$logfile" 2>/dev/null |
    awk 'NF>=5 { p=$5; sub(/\\[[0-9]+\\]:?$/, "", p); print p }' |
    sort | uniq -c | sort -nr | head -12 || true
done
section "Deleted but still open files"
if command -v lsof >/dev/null 2>&1; then
  lsof -nP +L1 2>/dev/null | head -25 || true
else
  echo "lsof not installed; cannot inspect deleted-but-open files."
fi
section "Release directories (most recent first)"
if [[ -d /opt/creative-asset-manager/releases ]]; then
  find /opt/creative-asset-manager/releases -mindepth 1 -maxdepth 1 -type d -printf '%TY-%Tm-%Td %TH:%TM  %f\n' 2>/dev/null | sort -r | head -20 || true
fi

if ((SAVE)); then
  section "Growth since previous saved audit (bytes, positive means growth)"
  mkdir -p -m 0700 -- "$STATE_DIR"
  previous="$(find "$STATE_DIR" -maxdepth 1 -type f -name 'cam-disk-*.tsv' -printf '%p\n' | sort | tail -1)"
  if [[ -s "$previous" ]]; then
    awk -F '\t' '
      FNR==NR { old[$1 SUBSEP $2]=$3; next }
      {
        key=$1 SUBSEP $2
        if ((key in old) && $3 != old[key]) {
          delta=$3-old[key]
          printf "%.0f\t%s\t%s\n",delta,$1,$2
        }
      }
    ' "$previous" "$snapshot" | sort -t $'\t' -k1,1nr | head -40 || true
    echo "Previous: $previous"
  else
    echo "First saved snapshot. Run again with --save to compare growth."
  fi
  stamp="$(date -u '+%Y%m%dT%H%M%SZ')"
  dest="$STATE_DIR/cam-disk-$stamp.tsv"
  if [[ -e "$dest" ]]; then dest="$STATE_DIR/cam-disk-$stamp-$$.tsv"; fi
  cp -- "$snapshot" "$dest"
  chmod 0600 -- "$dest"
  echo "Snapshot: $dest"
fi
echo "Audit complete. No user data or production releases deleted."
