#!/usr/bin/env bash
# Production-only, bounded entrypoint to the deployed ColorMatch V10 backfill.
# Dry run unless the caller explicitly passes --apply. No credentials are logged.
set -Eeuo pipefail

if [[ "$EUID" -ne 0 ]]; then
  printf '%s\n' 'ERROR: Production service operator required.' >&2
  exit 2
fi

readonly RELEASE="/opt/creative-asset-manager/current"
readonly PYTHON="$RELEASE/apps/api/.venv/bin/python"
readonly BACKFILL="$RELEASE/scripts/cam-etsy-colormatch-v2-backfill.py"
if [[ ! -x "$PYTHON" || ! -f "$BACKFILL" ]]; then
  printf '%s\n' 'ERROR: ColorMatch V10 is not present in the current backend release.' >&2
  exit 2
fi

# Fail closed if the active API still projects V1 metadata. This prevents
# backfill from silently making color swatches invisible to an older API.
if ! PYTHONPATH="$RELEASE/apps/api" "$PYTHON" -c \
  'from app.modules.etsy_spy.color_match_palette import ALGORITHM; assert ALGORITHM == "colormatch-sms-c1717-v10"'; then
  printf '%s\n' 'ERROR: Backend release is not ColorMatch V10.' >&2
  exit 2
fi

exec "$PYTHON" "$BACKFILL" "$@"
