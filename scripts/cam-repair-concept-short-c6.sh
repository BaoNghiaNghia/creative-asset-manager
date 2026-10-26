#!/usr/bin/env bash
# Repair the installed Concept Short C6 wrap with CAM's audited Skill replacement.
# Default: dry-run. --apply requires no active jobs; preserves historical outputs.
set -Eeuo pipefail
PROD="/opt/creative-asset-manager/current"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
cd "$PROD/apps/api"
python3 "$PROD/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  runuser -u creative-assets -- \
  "$PROD/apps/api/.venv/bin/python" -m app.operations.repair_concept_short_c6 "$@"
