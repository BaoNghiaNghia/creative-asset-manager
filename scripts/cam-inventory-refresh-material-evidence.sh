#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf 'ERROR: Do not source this script.\n' >&2
  return 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
APP_ROOT="${CAM_APP_ROOT:-/opt/creative-asset-manager}"
CURRENT="$APP_ROOT/current"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -L "$CURRENT" ]] || die "Active backend release is unavailable."

RELEASE="$(readlink -f -- "$CURRENT")"
API_ROOT="$RELEASE/apps/api"
PYTHON="$API_ROOT/.venv/bin/python"
RUNNER="$RELEASE/deploy/tools/production_env.py"
COMMAND="$RELEASE/scripts/inventory-refresh-material-evidence.py"

[[ -x "$PYTHON" ]] || die "Active release Python runtime is unavailable."
[[ -x "$RUNNER" ]] || die "Production environment runner is unavailable."
[[ -f "$COMMAND" ]] || die "Inventory material evidence command is unavailable."
[[ -f "$ENV_FILE" ]] || die "Production environment file is unavailable."

cd "$API_ROOT"
"$RUNNER" run-redacted \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  -- "$PYTHON" "$COMMAND"
