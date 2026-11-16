#!/usr/bin/env bash
# Controlled production entrypoint for the existing Etsy Picked YTrends backfill.
# Read-only unless --apply. No credentials, seller titles or source images are logged.
set -Eeuo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  printf '%s\n' 'ERROR: Production service operator required.' >&2
  exit 2
fi

readonly RELEASE="/opt/creative-asset-manager/current"
readonly PYTHON="$RELEASE/apps/api/.venv/bin/python"
readonly BACKFILL="$RELEASE/scripts/cam-ytrends-refresh-all-picked.py"
readonly ENV_RUNNER="$RELEASE/deploy/tools/production_env.py"
readonly ENV_FILE="/etc/creative-asset-manager/production.env"

if [[ ! -x "$PYTHON" || ! -f "$BACKFILL" || ! -f "$ENV_RUNNER" ]]; then
  printf '%s\n' 'ERROR: YTrends Picked backfill not available in active release.' >&2
  exit 2
fi

# Use the already-reviewed root-owned environment reader. It checks owner and
# permissions, never evaluates shell from the env file, and redacts secret
# values from any subprocess output. The backfill validates tenant/job state.
exec "$PYTHON" "$ENV_RUNNER" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  env PYTHONPATH="$RELEASE/apps/api" "$PYTHON" "$BACKFILL" "$@"
