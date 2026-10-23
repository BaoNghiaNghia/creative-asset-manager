#!/usr/bin/env bash
# Stage 1 Typography Concepts installer. Retired: legacy font-pool updater.
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
exec bash "$ROOT/scripts/cam-install-stage1-typography-skill.sh" "$@"
