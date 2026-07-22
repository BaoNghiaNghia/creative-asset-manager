#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
REPOSITORY_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
# shellcheck source=scripts/lib/deployment-common.sh
source "$SCRIPT_DIR/lib/deployment-common.sh"

ALLOW_DIRTY=false
CREATE_COMMIT=false
PUSH=false
COMMIT_MESSAGE="build(frontend): production bundle"

cleanup() {
  unset BUILD_COMMIT
}
trap cleanup EXIT
trap 'status=$?; printf "Frontend release build failed (exit %s).\n" "$status" >&2' ERR

while (($#)); do
  case "$1" in
    --allow-dirty) ALLOW_DIRTY=true; shift ;;
    --commit) CREATE_COMMIT=true; shift ;;
    --push) PUSH=true; shift ;;
    --message) COMMIT_MESSAGE="${2:?missing commit message}"; shift 2 ;;
    -h|--help)
      printf '%s\n' "Usage: $0 [--allow-dirty] [--commit] [--push] [--message TEXT]"
      exit 0
      ;;
    *) deploy_die "Unknown option: $1" ;;
  esac
done

require_non_root
require_command git
require_command node
require_command npm
[[ "$PUSH" != true || "$CREATE_COMMIT" == true ]] \
  || deploy_die "--push requires --commit"
[[ "$(id -un)" == "baonghia" ]] \
  || printf 'WARNING: this local release script is intended for user baonghia.\n' >&2

cd "$REPOSITORY_ROOT"
if [[ "$ALLOW_DIRTY" != true ]] && [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
  deploy_die "Git working tree must be clean; commit source changes or pass --allow-dirty."
fi
[[ -f apps/client/package-lock.json ]] || deploy_die "Frontend package-lock.json is missing."

printf '%s\n' "Installing deterministic frontend dependencies..."
npm --prefix apps/client ci --no-audit --no-fund
printf '%s\n' "Running frontend tests..."
npm --prefix apps/client test
printf '%s\n' "Running frontend typecheck..."
npm --prefix apps/client run typecheck
printf '%s\n' "Building production frontend..."
BUILD_COMMIT="$(git rev-parse --verify HEAD)" npm --prefix apps/client run build

DIST="$REPOSITORY_ROOT/apps/client/dist"
scan_frontend_dist "$DIST"

FILE_COUNT="$(find "$DIST" -type f | wc -l | tr -d ' ')"
DIST_SIZE="$(du -sh "$DIST" | awk '{print $1}')"
printf 'Frontend dist verified: %s files, %s.\n' "$FILE_COUNT" "$DIST_SIZE"
git status --short -- apps/client/dist
git diff --stat -- apps/client/dist

if [[ "$CREATE_COMMIT" == true ]]; then
  git add -A -- apps/client/dist
  if git diff --cached --quiet -- apps/client/dist; then
    printf '%s\n' "Frontend dist is already current; no build commit created."
  else
    git commit -m "$COMMIT_MESSAGE" -- apps/client/dist
  fi
fi

if [[ "$PUSH" == true ]]; then
  BRANCH="$(git symbolic-ref --quiet --short HEAD)" \
    || deploy_die "Cannot push a detached HEAD."
  git push origin "$BRANCH"
fi
