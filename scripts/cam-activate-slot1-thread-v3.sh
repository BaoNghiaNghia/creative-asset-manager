#!/usr/bin/env bash
# Activate only Products Stage 2 Slot 1 AI Material v3, without regenerating jobs.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
ACTIVE_PYTHON="${CAM_ACTIVE_PYTHON:-/opt/creative-asset-manager/current/apps/api/.venv/bin/python}"
[[ -x "$ACTIVE_PYTHON" ]] || { echo "Active backend runtime is not available" >&2; exit 1; }
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ACTIVE_PYTHON" -m app.operations.activate_ecommerce_front_thread_v3 "$@"
