#!/usr/bin/env bash
# ThreadTransfer Lite V1: dry-run / apply / rollback only the future Slot 1 Skill.
# Existing output versions, jobs and the old v4 Skill remain immutable.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
ACTIVE_PYTHON="${CAM_ACTIVE_PYTHON:-/opt/creative-asset-manager/current/apps/api/.venv/bin/python}"
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" || "$MODE" == "--rollback" ]] || {
  echo "Expected --check, --apply, or --rollback" >&2; exit 2;
}
[[ -x "$ACTIVE_PYTHON" ]] || { echo "Active backend Python unavailable" >&2; exit 1; }
if [[ "$MODE" != "--check" ]]; then
  [[ "$(git -C "$ROOT" branch --show-current)" == "main" ]] || {
    echo "Only main can activate the production Slot 1 default" >&2; exit 1;
  }
  [[ "$(git -C "$ROOT" rev-parse HEAD)" == "$(git -C "$ROOT" rev-parse origin/main)" ]] || {
    echo "main must be synchronized with origin/main" >&2; exit 1;
  }
  [[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
    echo "main worktree must be clean" >&2; exit 1;
  }
fi
cd "$ROOT/apps/api"
action=()
[[ "$MODE" == "--check" ]] || action=("$MODE")
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ACTIVE_PYTHON" -m app.operations.activate_ecommerce_front_thread_lite_v1 "${action[@]}"
