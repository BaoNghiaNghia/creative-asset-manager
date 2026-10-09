#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
if [[ $# -lt 2 || "${1:-}" != "--job-id" ]]; then
  echo "Usage: bash scripts/cam-recover-stage1-uploads.sh --job-id UUID [--apply]" >&2
  exit 2
fi
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ROOT/apps/api/.venv/bin/python" -m app.operations.recover_stage1_uploaded_outputs "$@"
