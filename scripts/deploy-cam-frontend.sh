#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
SOURCE_DIR="${CAM_SOURCE_DIR:-/srv/creative-asset-manager-source}"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
WEB_ROOT="${CAM_WEB_ROOT:-/var/www/creative-asset-manager}"
ALLOW_DIRTY=false
REF=""
ROLLBACK=false

usage() { cat <<'USAGE'
Usage: sudo scripts/deploy-cam-frontend.sh [--commit SHA|--branch NAME] [--rollback] [--allow-dirty]
Builds, validates and atomically activates an immutable native Nginx frontend release.
USAGE
}
die() { printf "ERROR: %s\n" "$*" >&2; exit 1; }
require() { command -v "$1" >/dev/null 2>&1 || die "Required command is unavailable: $1"; }
while (($#)); do
  case "$1" in
    --commit) REF="${2:?missing commit}"; shift 2 ;;
    --branch) REF="${2:?missing branch}"; shift 2 ;;
    --rollback) ROLLBACK=true; shift ;;
    --allow-dirty) ALLOW_DIRTY=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1" ;;
  esac
done
if [[ ! -d "$SOURCE_DIR/.git" ]]; then SOURCE_DIR="$CHECKOUT_ROOT"; fi
SOURCE_DIR="$(realpath -- "$SOURCE_DIR")"
for command in git node npm rsync nginx systemctl python3 curl; do require "$command"; done
[[ $EUID -eq 0 ]] || die "Run as root so release ownership and Nginx activation are safe."
[[ -f "$ENV_FILE" ]] || die "Production environment file is missing."
ENV_HELPER="$SOURCE_DIR/deploy/tools/production_env.py"
[[ -f "$ENV_HELPER" ]] || die "production_env.py is missing."
if $ROLLBACK; then
  [[ -L "$WEB_ROOT/previous" ]] || die "No previous frontend release is recorded."
  target="$(readlink -f -- "$WEB_ROOT/previous")"
  [[ -f "$target/index.html" ]] || die "Previous frontend release is invalid."
  ln -s -- "$target" "$WEB_ROOT/current.new"
  mv -Tf -- "$WEB_ROOT/current.new" "$WEB_ROOT/current"
  nginx -t && systemctl reload nginx
  printf "Frontend rollback activated.\n"
  exit 0
fi
[[ "$ALLOW_DIRTY" == true || -z "$(git -C "$SOURCE_DIR" status --porcelain --untracked-files=normal)" ]] || die "Refusing deployment from a dirty checkout."
COMMIT="$(git -C "$SOURCE_DIR" rev-parse --verify "${REF:-HEAD}^{commit}")"
RELEASE_ID="$COMMIT"
BUILD_ROOT="$SOURCE_DIR"
TEMP_BUILD="$(mktemp -d)"
cleanup() { [[ -z "$TEMP_BUILD" ]] || rm -rf -- "$TEMP_BUILD"; }
trap cleanup EXIT
git -C "$SOURCE_DIR" archive "$COMMIT" | tar -x -C "$TEMP_BUILD"
BUILD_ROOT="$TEMP_BUILD"
export BUILD_COMMIT="$COMMIT"
npm --prefix "$BUILD_ROOT/apps/client" ci --no-audit --no-fund
npm --prefix "$BUILD_ROOT/apps/client" test
npm --prefix "$BUILD_ROOT/apps/client" run typecheck
npm --prefix "$BUILD_ROOT/apps/client" run build
DIST="$BUILD_ROOT/apps/client/dist"
[[ -f "$DIST/index.html" && -f "$DIST/build-info.json" ]] || die "Frontend build is incomplete."
grep -Fq "$COMMIT" "$DIST/build-info.json" || die "build-info.json does not identify the deployed commit."
if find "$DIST" -type f \( -name "*.map" -o -name "*.map.gz" \) -print -quit | grep -q .; then die "Source maps are forbidden in production dist."; fi
if grep -R -E -I -n "https?://localhost(:[0-9]+)?|127\.0\.0\.1|DATABASE_URL|postgres(ql)?://|AIza[0-9A-Za-z_-]{20,}|sk-[A-Za-z0-9_-]{20,}|refresh_token|access_token|client_secret|BEGIN [A-Z ]*PRIVATE KEY" "$DIST" >/dev/null; then die "Generated dist contains a forbidden endpoint or secret marker."; fi
RELEASES="$WEB_ROOT/releases"
TARGET="$RELEASES/$RELEASE_ID"
install -d -o root -g root -m 0755 "$RELEASES"
if [[ -e "$TARGET" ]]; then
  diff -rq --exclude=.cam-frontend-release "$DIST" "$TARGET" >/dev/null || die "Existing frontend release differs from this commit."
else
  STAGE="$RELEASES/.${RELEASE_ID}.new.$$"
  install -d -o root -g root -m 0755 "$STAGE"
  rsync -a --delete --chmod=D755,F644 "$DIST/" "$STAGE/"
  printf "%s\n" "$COMMIT" > "$STAGE/.cam-frontend-release"
  mv -T "$STAGE" "$TARGET"
fi
python3 "$ENV_HELPER" check --env-file "$ENV_FILE" --expected-owner-uid 0 --api-root "$BUILD_ROOT/apps/api" >/dev/null
nginx -t
OLD=""
[[ ! -L "$WEB_ROOT/current" ]] || OLD="$(readlink -f -- "$WEB_ROOT/current")"
[[ -z "$OLD" || "$OLD" == "$TARGET" ]] || ln -s -- "$OLD" "$WEB_ROOT/previous.new"
[[ -z "$OLD" || "$OLD" == "$TARGET" ]] || mv -Tf -- "$WEB_ROOT/previous.new" "$WEB_ROOT/previous"
ln -s -- "$TARGET" "$WEB_ROOT/current.new"
mv -Tf -- "$WEB_ROOT/current.new" "$WEB_ROOT/current"
if ! nginx -t || ! systemctl reload nginx; then
  [[ -z "$OLD" ]] || { ln -s -- "$OLD" "$WEB_ROOT/current.rollback"; mv -Tf -- "$WEB_ROOT/current.rollback" "$WEB_ROOT/current"; nginx -t; systemctl reload nginx; }
  die "Frontend activation failed and previous release was restored."
fi
HOST="$(python3 -c "import sys; sys.path.insert(0, \"$SOURCE_DIR/deploy/tools\"); from production_env import parse_environment_file; print(parse_environment_file(__import__(\"pathlib\").Path(\"$ENV_FILE\"))[\"TRUSTED_HOSTS\"].split(\",\")[0].strip())")"
PUBLIC_URL="$(python3 -c "import sys; sys.path.insert(0, \"$SOURCE_DIR/deploy/tools\"); from production_env import parse_environment_file; print(parse_environment_file(__import__(\"pathlib\").Path(\"$ENV_FILE\"))[\"PUBLIC_APP_URL\"].rstrip(\"/\"))")"
for path in / /build-info.json /live /ready /version; do curl --fail --silent --show-error --max-time 15 "$PUBLIC_URL$path" >/dev/null; done
curl --fail --silent --show-error --max-time 10 -H "Host: $HOST" "http://127.0.0.1:8000/version" | grep -Fq "$COMMIT" || die "API version does not match the activated release commit."
printf "Frontend release %s activated.\n" "$RELEASE_ID"