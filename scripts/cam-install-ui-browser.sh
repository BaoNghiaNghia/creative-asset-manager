#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
PLAYWRIGHT_VERSION="$(node -e 'const p=require(process.argv[1]); process.stdout.write(String(p.devDependencies.playwright || ""));' "$CLIENT/package.json")"

[[ -n "$PLAYWRIGHT_VERSION" ]] || {
  printf 'ERROR: Playwright version is missing from apps/client/package.json.\n' >&2
  exit 1
}

if command -v google-chrome >/dev/null 2>&1; then
  google-chrome --version
else
  printf 'Google Chrome is missing; installing the Playwright Chrome channel.\n'
  (cd "$CLIENT" && npm exec --yes --package="playwright@$PLAYWRIGHT_VERSION" -- playwright install chrome)
  google-chrome --version
fi

PLAYWRIGHT_CACHE="${PLAYWRIGHT_BROWSERS_PATH:-${HOME:-/root}/.cache/ms-playwright}"
FIREFOX_BIN=""
if [[ -d "$PLAYWRIGHT_CACHE" ]]; then
  FIREFOX_BIN="$(find "$PLAYWRIGHT_CACHE" -maxdepth 3 -type f -path '*/firefox/firefox' -perm -111 -print -quit)"
fi

if [[ -n "$FIREFOX_BIN" ]]; then
  printf 'Playwright Firefox is ready: %s\n' "$FIREFOX_BIN"
else
  printf 'Playwright Firefox is missing; installing the locked browser build for Playwright %s.\n' "$PLAYWRIGHT_VERSION"
  (cd "$CLIENT" && npm exec --yes --package="playwright@$PLAYWRIGHT_VERSION" -- playwright install firefox)
fi
