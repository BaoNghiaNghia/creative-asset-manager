#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="${CAM_DESKTOP_UPDATE_ROOT:-/var/www/creative-asset-manager-desktop-updates/windows}"
PUBLIC_URL="${CAM_DESKTOP_UPDATE_PUBLIC_URL:-https://creative-assets.ddns.net/desktop-updates/windows}"
SOURCE=""
ROLLBACK=false

usage() {
  cat <<'USAGE'
Usage:
  sudo scripts/publish-cam-desktop-update.sh --source /path/to/electron-builder-output
  sudo scripts/publish-cam-desktop-update.sh --rollback

Publishes a Windows NSIS electron-updater feed atomically. The source directory
must contain latest.yml, the referenced installer .exe, and its .blockmap.
USAGE
}

die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"; }

while (($#)); do
  case "$1" in
    --source) SOURCE="${2:?missing source directory}"; shift 2 ;;
    --rollback) ROLLBACK=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done

[[ $EUID -eq 0 ]] || die "Run as root so update artifacts can be activated safely."
for command in awk curl diff install ln mv readlink realpath rsync; do require "$command"; done

install -d -o root -g root -m 0755 "$ROOT/releases"

if $ROLLBACK; then
  [[ -L "$ROOT/previous" ]] || die "No previous desktop update release is recorded."
  target="$(readlink -f -- "$ROOT/previous")"
  [[ -f "$target/latest.yml" ]] || die "Previous desktop update release is invalid."
  ln -s -- "$target" "$ROOT/current.new"
  mv -Tf -- "$ROOT/current.new" "$ROOT/current"
  printf 'Desktop update rollback activated: %s\n' "$target"
  exit 0
fi

[[ -n "$SOURCE" ]] || die "--source is required."
SOURCE="$(realpath -- "$SOURCE")"
[[ -d "$SOURCE" ]] || die "Source directory does not exist: $SOURCE"
[[ -s "$SOURCE/latest.yml" ]] || die "latest.yml is missing."

VERSION="$(awk -F': *' '$1 == "version" { gsub(/[" ]/, "", $2); print $2; exit }' "$SOURCE/latest.yml")"
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die "latest.yml has an invalid version."

ARTIFACT="$(awk -F': *' '$1 == "path" { sub(/^[[:space:]]*/, "", $2); gsub(/^"|"$/, "", $2); print $2; exit }' "$SOURCE/latest.yml")"
[[ -n "$ARTIFACT" && "$ARTIFACT" == "$(basename -- "$ARTIFACT")" ]] || die "latest.yml has an unsafe installer path."
[[ "$ARTIFACT" == *.exe ]] || die "latest.yml does not point to an NSIS installer."
[[ -s "$SOURCE/$ARTIFACT" ]] || die "Referenced installer is missing: $ARTIFACT"
[[ -s "$SOURCE/$ARTIFACT.blockmap" ]] || die "Installer blockmap is missing: $ARTIFACT.blockmap"

TARGET="$ROOT/releases/$VERSION"
if [[ -e "$TARGET" ]]; then
  diff -q "$SOURCE/latest.yml" "$TARGET/latest.yml" >/dev/null || die "Existing release metadata differs for version $VERSION."
  diff -q "$SOURCE/$ARTIFACT" "$TARGET/$ARTIFACT" >/dev/null || die "Existing installer differs for version $VERSION."
else
  STAGE="$ROOT/releases/.${VERSION}.new.$$"
  install -d -o root -g root -m 0755 "$STAGE"
  rsync -a --chmod=D755,F644 -- "$SOURCE/$ARTIFACT" "$SOURCE/$ARTIFACT.blockmap" "$STAGE/"
  install -o root -g root -m 0644 "$SOURCE/latest.yml" "$STAGE/latest.yml"
  mv -T -- "$STAGE" "$TARGET"
fi

OLD=""
[[ ! -L "$ROOT/current" ]] || OLD="$(readlink -f -- "$ROOT/current")"
if [[ -n "$OLD" && "$OLD" != "$TARGET" ]]; then
  ln -s -- "$OLD" "$ROOT/previous.new"
  mv -Tf -- "$ROOT/previous.new" "$ROOT/previous"
fi
ln -s -- "$TARGET" "$ROOT/current.new"
mv -Tf -- "$ROOT/current.new" "$ROOT/current"

ARTIFACT_URL="${ARTIFACT// /%20}"
curl --fail --silent --show-error --max-time 20 "$PUBLIC_URL/latest.yml" >/dev/null
curl --fail --silent --show-error --location --max-time 30 --range 0-1023 "$PUBLIC_URL/$ARTIFACT_URL" >/dev/null
printf 'Desktop update %s activated: %s\n' "$VERSION" "$ARTIFACT"
