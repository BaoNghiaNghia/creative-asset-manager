#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
PROFILE="${CAM_UI_QA_PROFILE:-explorer-viewer}"
PROPOSALS_ROOT="$CLIENT/.ui-qa/baseline-proposals"
VIEWPORTS="${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}"
ACCEPT="${CAM_UI_BASELINE_ACCEPT:-0}"
REASON="${CAM_UI_BASELINE_ACCEPT_REASON:-}"
PROPOSAL_REF="${CAM_UI_BASELINE_PROPOSAL:-}"
HOST="127.0.0.1"

die(){ printf 'ERROR: %s\n' "$*" >&2; exit 1; }
[[ "$ACCEPT" == "1" ]] || die "Explicit acceptance is required: set CAM_UI_BASELINE_ACCEPT=1 only after the user confirms the intended visual change."
[[ -n "$REASON" ]] || die "CAM_UI_BASELINE_ACCEPT_REASON is required."
[[ -n "$PROPOSAL_REF" ]] || die "CAM_UI_BASELINE_PROPOSAL is required."

mapfile -t PROFILE_VALUES < <(
  node "$CLIENT/scripts/ui-qa-profiles.mjs" --profile "$PROFILE" --format-lines
)
PROFILE_ROUTE="${PROFILE_VALUES[0]}"
PROFILE_FIXTURE="${PROFILE_VALUES[1]}"
PROFILE_PLAN="${PROFILE_VALUES[2]}"
PROFILE_BASELINE="${PROFILE_VALUES[3]}"

ROUTE_PATH="${CAM_UI_QA_PATH:-$PROFILE_ROUTE}"
FIXTURE="${CAM_UI_QA_FIXTURE:-$ROOT/$PROFILE_FIXTURE}"
PLAN="${CAM_UI_QA_PLAN:-$ROOT/$PROFILE_PLAN}"
BASELINE_DIR="${CAM_UI_VISUAL_BASELINE_DIR:-$ROOT/$PROFILE_BASELINE}"
[[ "$ROUTE_PATH" == /* ]] || ROUTE_PATH="/$ROUTE_PATH"

if [[ "$PROPOSAL_REF" == /* || "$PROPOSAL_REF" == ./* ]]; then
  PROPOSAL_DIR="$(realpath -- "$PROPOSAL_REF")"
else
  PROPOSAL_DIR="$PROPOSALS_ROOT/$PROPOSAL_REF"
fi
[[ -f "$PROPOSAL_DIR/proposal.json" ]] || die "Proposal not found: $PROPOSAL_DIR"

python3 "$ROOT/scripts/ui_baseline_governance.py" validate --proposal-dir "$PROPOSAL_DIR" >/dev/null

BACKUP_DIR="$CLIENT/.ui-qa/baseline-accept-backup-$$"
rm -rf "$BACKUP_DIR"
cp -a "$BASELINE_DIR" "$BACKUP_DIR"
APPLIED=0
SERVER_PID=""

finalize(){
  status=$?
  if [[ -n "$SERVER_PID" ]]; then
    # npm creates a child Vite server; stop its entire isolated process group.
    kill -TERM -- "-$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  if [[ "$APPLIED" == "1" && "$status" != "0" ]]; then
    rm -rf "$BASELINE_DIR"
    cp -a "$BACKUP_DIR" "$BASELINE_DIR"
    rm -f "$PROPOSAL_DIR/acceptance.json"
    printf 'Baseline acceptance verification failed; tracked baselines were restored.\n' >&2
  fi
  rm -rf "$BACKUP_DIR"
  trap - EXIT INT TERM
  exit "$status"
}
trap finalize EXIT
trap 'exit 130' INT TERM

python3 "$ROOT/scripts/ui_baseline_governance.py" apply   --proposal-dir "$PROPOSAL_DIR"   --reason "$REASON" >/dev/null
APPLIED=1

if [[ -n "${CAM_UI_STAGING_PORT:-}" ]]; then
  PORT="$CAM_UI_STAGING_PORT"
else
  PORT="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
)"
fi

ROOT_URL="http://$HOST:$PORT/"
URL="http://$HOST:$PORT$ROUTE_PATH"
SERVER_LOG="$PROPOSAL_DIR/accept-verify-server.log"
cd "$CLIENT"
setsid npm run dev -- --host "$HOST" --port "$PORT" --strictPort >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!
for _ in $(seq 1 80); do
  if curl --silent --fail --max-time 1 "$ROOT_URL" >/dev/null; then break; fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    cat "$SERVER_LOG" >&2 || true
    die "Baseline acceptance verification server exited before becoming ready."
  fi
  sleep 0.1
done
curl --silent --fail --max-time 2 "$ROOT_URL" >/dev/null ||
  die "Baseline acceptance verification server did not become ready at $ROOT_URL"

printf 'Baseline profile: %s (%s)\n' "$PROFILE" "$ROUTE_PATH"
CAM_UI_QA_MODE=baseline-accept   npm run ui:qa --     --mode baseline-accept     --url "$URL"     --fixture "$FIXTURE"     --plan "$PLAN"     --viewports "$VIEWPORTS"     --baseline-dir "$BASELINE_DIR"     --strict

kill "$SERVER_PID" >/dev/null 2>&1 || true
wait "$SERVER_PID" >/dev/null 2>&1 || true
SERVER_PID=""
rm -rf "$BACKUP_DIR"
trap - EXIT INT TERM

printf '\nBaseline proposal accepted and verified: %s\n' "$PROPOSAL_DIR"
printf 'Changed tracked baselines:\n'
git -C "$ROOT" diff --name-only -- "$PROFILE_BASELINE"
