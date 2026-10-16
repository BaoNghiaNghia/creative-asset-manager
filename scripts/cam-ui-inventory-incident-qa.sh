#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
HOST="127.0.0.1"
PORT="${CAM_UI_STAGING_PORT:-$(python3 - <<'PY'
import socket
with socket.socket() as sock:
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1])
PY
)}"
BASE="http://$HOST:$PORT"
OUTPUT="$CLIENT/.ui-qa"
LOG="$OUTPUT/inventory-incident-preview.log"
mkdir -p "$OUTPUT"
cleanup() {
  if [[ -n "${PID:-}" ]]; then
    kill -TERM -- "-$PID" >/dev/null 2>&1 || true
    wait "$PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM
cd "$CLIENT"
setsid npm run dev -- --host "$HOST" --port "$PORT" --strictPort >"$LOG" 2>&1 &
PID=$!
for _ in $(seq 1 80); do
  if curl --fail --silent --max-time 1 "$BASE/" >/dev/null; then break; fi
  if ! kill -0 "$PID" >/dev/null 2>&1; then cat "$LOG" >&2; exit 1; fi
  sleep .1
done
curl --fail --silent --max-time 3 "$BASE/" >/dev/null
CAM_UI_QA_KEEP=8 npm run ui:qa -- \
  --mode inventory-incident \
  --url "$BASE${CAM_UI_QA_ROUTE:-/inventory/pipeline}" \
  --fixture "$ROOT/apps/client/scripts/fixtures/inventory-daily.json" \
  --plan "$ROOT/${CAM_UI_QA_PLAN:-docs/operations/ui-qa-inventory-incident-plan.json}" \
  --viewports "${CAM_UI_VIEWPORTS:-desktop,compact,tabletLandscape,tabletPortrait,mobile,mobileSmall}" \
  --strict
