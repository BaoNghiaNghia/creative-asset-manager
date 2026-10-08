#!/usr/bin/env bash
# Start resumable Stage 0 trademark screening refresh using production API env.
set -Eeuo pipefail

APP_ROOT="${CAM_APP_ROOT:-/opt/creative-asset-manager}"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
CURRENT="$(readlink -f "$APP_ROOT/current")"
[[ -n "$CURRENT" && -f "$CURRENT/apps/api/app/modules/realistic_review_ugc/trademark_backfill.py" ]] || {
  echo "Production release is unavailable" >&2
  exit 1
}
[[ -f "$ENV_FILE" ]] || { echo "Production environment is unavailable" >&2; exit 1; }
PYTHON="$CURRENT/apps/api/.venv/bin/python"
[[ -x "$PYTHON" ]] || { echo "Production Python environment unavailable" >&2; exit 1; }

# A fixed unit prevents accidental concurrent mass provider requests. Use
# --collect so future runs can resume safely after the previous run ends.
systemd-run --unit=cam-stage0-trademark-backfill --collect \
  --property="EnvironmentFile=$ENV_FILE" \
  --property="WorkingDirectory=$CURRENT/apps/api" \
  "$PYTHON" -m app.modules.realistic_review_ugc.trademark_backfill \
  --fetch --batch-size "${CAM_TM_BATCH_SIZE:-10}" --pause "${CAM_TM_PAUSE_SECONDS:-0.7}"
echo "Inspect progress: journalctl -u cam-stage0-trademark-backfill -n 50 --no-pager"
