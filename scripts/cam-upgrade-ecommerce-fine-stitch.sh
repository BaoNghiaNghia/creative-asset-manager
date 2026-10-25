#!/usr/bin/env bash
# CAM Stage 2 fine-thread Skills 1-4: dry-run, --apply or --rollback.
# Versioned clones keep old hash-pinned jobs and uploaded ZIPs intact.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ROOT/apps/api/.venv/bin/python" -m app.operations.upgrade_ecommerce_fine_stitch_skills "$@"
