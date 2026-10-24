#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
[[ $EUID -eq 0 ]] || exit 1
cd "$ROOT/apps/api"
"$ROOT/deploy/tools/production_env.py" run-redacted --env-file "$ENV_FILE" --expected-owner-uid 0 -- "$ROOT/apps/api/.venv/bin/python" "$ROOT/scripts/pipeline-completion-audit.py"
