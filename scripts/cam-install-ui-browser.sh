#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"

if command -v google-chrome >/dev/null 2>&1; then
  google-chrome --version
  exit 0
fi

printf 'Google Chrome is missing; installing the Playwright Chrome channel.\n'
cd "$ROOT/apps/client"
npx playwright install chrome
google-chrome --version
