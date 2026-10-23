#!/usr/bin/env bash
# Install the validated GatorHats Typography Concepts Stage 1 Skill in CAM.
# Dry run by default. With --apply, only Stage 1 aliases are updated; old
# installed Skills, Drive assets and versions are never deleted by the installer.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
cd "$ROOT/apps/api"
python3 "$ROOT/deploy/tools/production_env.py" run-redacted \
  --env-file "$ENV_FILE" --expected-owner-uid 0 -- \
  "$ROOT/apps/api/.venv/bin/python" -m app.operations.install_stage1_typography_skill "$@"
