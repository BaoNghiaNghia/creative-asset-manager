#!/usr/bin/env bash
# Safe, explicit production operation: import 4 authenticated Valucap fronts
# then reconcile only changed completed Products Stage 2 Slot 7 jobs.
set -Eeuo pipefail
export LC_ALL=C
if [[ "$(id -u)" != 0 ]]; then
  echo "ERROR: production maintenance requires the standard root deploy account." >&2
  exit 2
fi
MODE="${1:---preview}"
MAX_JOBS="${2:-3}"
if [[ "$MODE" != "--preview" && "$MODE" != "--apply" ]]; then
  echo "Usage: bash scripts/cam-slot7-production-sync.sh [--preview|--apply] [max-jobs 1..13] <40-character-deployed-commit>" >&2
  exit 2
fi
if [[ ! "$MAX_JOBS" =~ ^(1[0-3]|[1-9])$ ]]; then
  echo "ERROR: max-jobs must be between 1 and 13." >&2
  exit 2
fi
RELEASE="$(readlink -f /opt/creative-asset-manager/current)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
if [[ -z "$RELEASE" || ! -f "$RELEASE/.cam-release" ||
      ! -x "$RELEASE/apps/api/.venv/bin/python" ||
      ! -f "$RELEASE/scripts/cam_slot7_stock_sync.py" ||
      ! -f "$RELEASE/scripts/cam_slot7_stock_reconcile.py" ||
      ! -f "$RELEASE/deploy/tools/production_env.py" ]]; then
  echo "ERROR: expected complete immutable backend release is not active." >&2
  exit 2
fi
# Require the caller to specify the exact CI-approved deployed commit.
# This avoids a self-referential commit hash in a versioned script.
EXPECTED_SHA="${3:-}"
if [[ ! "$EXPECTED_SHA" =~ ^[a-f0-9]{40}$ ||
      "$(cat "$RELEASE/.cam-release")" != "$EXPECTED_SHA" ]]; then
  echo "ERROR: specify the exact approved backend commit as argument 3." >&2
  exit 2
fi
cd "$RELEASE"
if [[ "$MODE" == "--preview" ]]; then
  echo "=== Supplier front-stock preview (read-only) ==="
  STOCK_ARGS=()
else
  echo "=== Apply exactly 4 verified supplier Slot 7 fronts ==="
  STOCK_ARGS=(--apply)
fi
# The deployment already uses this security-checked parser. It creates a
# sanitized process environment without sourcing root-owned files as shell.
"$RELEASE/apps/api/.venv/bin/python" "$RELEASE/deploy/tools/production_env.py" \
  run-redacted --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  env PYTHONPATH="$RELEASE/apps/api" "$RELEASE/apps/api/.venv/bin/python" \
    "$RELEASE/scripts/cam_slot7_stock_sync.py" "${STOCK_ARGS[@]}"
echo "=== Safe production database reconciliation ==="
ARGS=()
if [[ "$MODE" == "--apply" ]]; then
  ARGS=(--apply --max-jobs "$MAX_JOBS")
fi
"$RELEASE/apps/api/.venv/bin/python" "$RELEASE/deploy/tools/production_env.py" \
  run-redacted --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  env PYTHONPATH="$RELEASE/apps/api" "$RELEASE/apps/api/.venv/bin/python" \
    "$RELEASE/scripts/cam_slot7_stock_reconcile.py" "${ARGS[@]}"
