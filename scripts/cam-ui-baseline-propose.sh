#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
TASK="${CAM_UI_TASK:-}"
BASELINE_DIR="${CAM_UI_VISUAL_BASELINE_DIR:-$CLIENT/visual-baselines/explorer-viewer}"
FIXTURE="${CAM_UI_QA_FIXTURE:-$CLIENT/scripts/fixtures/explorer-viewer.json}"
PLAN="${CAM_UI_QA_PLAN:-$ROOT/docs/operations/ui-qa-explorer-viewer-plan.json}"
VIEWPORTS="${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}"
PROPOSALS_ROOT="$CLIENT/.ui-qa/baseline-proposals"
HOST="127.0.0.1"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ -n "$TASK" ]] || die "CAM_UI_TASK is required to create a baseline proposal."
[[ -f "$BASELINE_DIR/manifest.json" ]] || die "Tracked baseline manifest is missing."
if [[ -n "${CAM_UI_STAGING_PORT:-}" ]]; then PORT="$CAM_UI_STAGING_PORT"; else PORT="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
)"; fi
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"; SHORT_HEAD="$(git -C "$ROOT" rev-parse --short HEAD)"
PROPOSAL_ID="${CAM_UI_BASELINE_PROPOSAL_ID:-$STAMP-$SHORT_HEAD}"; PROPOSAL_DIR="$PROPOSALS_ROOT/$PROPOSAL_ID"; RUN_ROOT="$PROPOSAL_DIR/runs"; URL="http://$HOST:$PORT/"; SERVER_LOG="$PROPOSAL_DIR/dev-server.log"
[[ ! -e "$PROPOSAL_DIR" ]] || die "Proposal already exists: $PROPOSAL_DIR"; mkdir -p "$RUN_ROOT"
cleanup(){ if [[ -n "${SERVER_PID:-}" ]]; then kill "$SERVER_PID" >/dev/null 2>&1 || true; wait "$SERVER_PID" >/dev/null 2>&1 || true; fi; }; trap cleanup EXIT INT TERM
bash "$ROOT/scripts/cam-install-ui-browser.sh"
CHANGED="$({ git -C "$ROOT" diff --name-only HEAD -- 2>/dev/null || true; git -C "$ROOT" ls-files --others --exclude-standard; } | awk 'NF' | sort -u)"; export CAM_UI_CHANGED_FILES="$CHANGED"
cd "$CLIENT"; npm run dev -- --host "$HOST" --port "$PORT" --strictPort >"$SERVER_LOG" 2>&1 & SERVER_PID=$!
for _ in $(seq 1 80); do if curl --silent --fail --max-time 1 "$URL" >/dev/null; then break; fi; if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then cat "$SERVER_LOG" >&2 || true; die "Baseline proposal dev server exited before becoming ready."; fi; sleep 0.1; done
curl --silent --fail --max-time 2 "$URL" >/dev/null || die "Baseline proposal dev server did not become ready at $URL"
CAM_UI_QA_OUTPUT="$RUN_ROOT" CAM_UI_QA_MODE=baseline-proposal npm run ui:qa -- --mode baseline-proposal --url "$URL" --fixture "$FIXTURE" --plan "$PLAN" --viewports "$VIEWPORTS" --baseline-dir "$BASELINE_DIR"
RUN_DIR="$(python3 - "$RUN_ROOT" <<'PY'
from pathlib import Path
import sys
root=Path(sys.argv[1]); runs=[p for p in root.iterdir() if p.is_dir() and (p/'report.json').exists()]
if not runs: raise SystemExit('No UI QA run was produced.')
print(max(runs,key=lambda p:p.stat().st_mtime))
PY
)"
cd "$ROOT"; python3 scripts/ui_baseline_governance.py create --run-dir "$RUN_DIR" --baseline-dir "$BASELINE_DIR" --proposal-dir "$PROPOSAL_DIR" --task "$TASK" >/dev/null
printf '\nBaseline proposal created: %s\n' "$PROPOSAL_DIR"; cat "$PROPOSAL_DIR/proposal.md"; printf '\nTracked baselines were not modified.\n'
