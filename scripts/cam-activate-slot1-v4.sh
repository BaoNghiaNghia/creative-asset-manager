#!/usr/bin/env bash
# Switch only future Products Stage 2 Slot 1 jobs to the independently pinned v4 Skill.
# Previous v3 jobs and images remain unchanged. --rollback selects v3 again.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
ACTIVE_PYTHON="${CAM_ACTIVE_PYTHON:-/opt/creative-asset-manager/current/apps/api/.venv/bin/python}"
MODE="${1:---check}"
[[ "$MODE" == "--check" || "$MODE" == "--apply" || "$MODE" == "--rollback" ]] || {
  echo "Expected --check, --apply, or --rollback" >&2; exit 2;
}
[[ -x "$ACTIVE_PYTHON" ]] || { echo "Active backend unavailable" >&2; exit 1; }
if [[ "$MODE" != "--check" ]]; then
  [[ "$(git -C "$ROOT" branch --show-current)" == "main" ]] || {
    echo "Only main can change production assignments" >&2; exit 1;
  }
  [[ "$(git -C "$ROOT" rev-parse HEAD)" == "$(git -C "$ROOT" rev-parse origin/main)" ]] || {
    echo "main and origin/main must agree" >&2; exit 1;
  }
  [[ -z "$(git -C "$ROOT" status --porcelain --untracked-files=normal)" ]] || {
    echo "Release checkout is dirty" >&2; exit 1;
  }
fi
cd "$ROOT/apps/api"
ACTION=()
[[ "$MODE" == "--check" ]] || ACTION=("$MODE")
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ACTIVE_PYTHON" -m app.operations.activate_ecommerce_front_thread_v4 "${ACTION[@]}"
