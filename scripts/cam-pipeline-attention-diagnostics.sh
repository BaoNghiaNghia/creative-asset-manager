#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
RUNNER="$ROOT/deploy/tools/production_env.py"
PYTHON="$ROOT/apps/api/.venv/bin/python"
COMMAND="$ROOT/scripts/pipeline-attention-diagnostics.py"

[[ $EUID -eq 0 ]] || { echo "ERROR: Run as root." >&2; exit 1; }
[[ -x "$RUNNER" && -x "$PYTHON" && -f "$COMMAND" && -f "$ENV_FILE" ]] || {
  echo "ERROR: Pipeline diagnostics runtime is unavailable." >&2
  exit 1
}

cd "$ROOT/apps/api"
"$RUNNER" run-redacted \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  -- "$PYTHON" "$COMMAND"
