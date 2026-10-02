#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"

bash "$ROOT/scripts/cam-install-ui-browser.sh"

FIXTURE="${CAM_UI_QA_FIXTURE:-$CLIENT/scripts/fixtures/explorer-viewer.json}"
PLAN="${CAM_UI_QA_PLAN:-$ROOT/docs/operations/ui-qa-explorer-viewer-plan.json}"
VIEWPORTS="${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}"
BASELINE_DIR="${CAM_UI_VISUAL_BASELINE_DIR:-$CLIENT/visual-baselines/explorer-viewer}"
HOST="127.0.0.1"

if [[ -n "${CAM_UI_STAGING_PORT:-}" ]]; then
  PORT="$CAM_UI_STAGING_PORT"
else
  PORT="$(python3 - <<'PY'
import socket
sock = socket.socket()
sock.bind(("127.0.0.1", 0))
print(sock.getsockname()[1])
sock.close()
PY
)"
fi

URL="http://$HOST:$PORT/"
LOG_DIR="$CLIENT/.ui-qa"
mkdir -p "$LOG_DIR"
SERVER_LOG="$LOG_DIR/staging-preview.log"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

cd "$CLIENT"
npm run preview -- --host "$HOST" --port "$PORT" --strictPort >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl --silent --fail --max-time 1 "$URL" >/dev/null; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    cat "$SERVER_LOG" >&2 || true
    echo "ERROR: UI staging preview exited before becoming ready." >&2
    exit 1
  fi
  sleep 0.1
done

curl --silent --fail --max-time 2 "$URL" >/dev/null || {
  cat "$SERVER_LOG" >&2 || true
  echo "ERROR: UI staging preview did not become ready at $URL" >&2
  exit 1
}

QA_ARGS=(
  --url "$URL"
  --fixture "$FIXTURE"
  --plan "$PLAN"
  --viewports "$VIEWPORTS"
)

if [[ "${CAM_UI_VISUAL_UPDATE:-0}" == "1" ]]; then
  echo "ERROR: Direct baseline update is disabled. Use make ui-visual-propose, review the proposal, then explicitly run make ui-visual-accept." >&2
  exit 2
fi

if [[ "${CAM_UI_VISUAL_SKIP:-0}" != "1" ]]; then
  QA_ARGS+=(--baseline-dir "$BASELINE_DIR")
fi

if [[ "${CAM_UI_QA_STRICT:-1}" == "1" ]]; then
  QA_ARGS+=(--strict)
fi

npm run ui:qa -- "${QA_ARGS[@]}"
