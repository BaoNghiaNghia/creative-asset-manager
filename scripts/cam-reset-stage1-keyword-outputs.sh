#!/usr/bin/env bash
# Safely reset only the three screenshot-identified Stage 1 keyword jobs.
# Read-only by default. Pass --apply --expected-job-ids ... --audit-file ...
# after reviewing the dry-run's exact row identities.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ROOT/apps/api/.venv/bin/python" -m app.operations.reset_stage1_keyword_outputs "$@"
