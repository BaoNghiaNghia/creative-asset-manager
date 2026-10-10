#!/usr/bin/env bash
# Update the separately-installed Stage 1 Codex runtime skill, safely.
# Dry-run by default; --apply requires zero queued/running Stage 1 jobs.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
ARGS=()
case "${1:-}" in
  "") ;;
  --apply) ARGS=(--apply) ;;
  *) printf 'Usage: bash scripts/cam-stage1-skill-update.sh [--apply]\n' >&2; exit 2 ;;
esac
if [[ $# -gt 1 ]]; then
  printf 'Only one argument is supported.\n' >&2
  exit 2
fi
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ROOT/apps/api/.venv/bin/python" -m app.operations.update_stage1_six_skill "${ARGS[@]}"
