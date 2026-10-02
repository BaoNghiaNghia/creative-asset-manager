#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
QA_ROOT="$CLIENT/.ui-qa"

MAX_ATTEMPTS="${CAM_UI_REPAIR_MAX_ATTEMPTS:-2}"
MAX_TARGETS="${CAM_UI_REPAIR_MAX_TARGETS:-4}"
PROFILE="${CAM_UI_QA_PROFILE:-explorer-viewer}"

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
HOST="127.0.0.1"

[[ "$ROUTE_PATH" == /* ]] || ROUTE_PATH="/$ROUTE_PATH"
if [[ "$FIXTURE" != /* ]]; then
  FIXTURE="$ROOT/$FIXTURE"
fi
if [[ "$PLAN" != /* ]]; then
  PLAN="$ROOT/$PLAN"
fi
if [[ "$BASELINE_DIR" != /* ]]; then
  BASELINE_DIR="$ROOT/$BASELINE_DIR"
fi

note() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

case "$MAX_ATTEMPTS" in
  ''|*[!0-9]*) die "CAM_UI_REPAIR_MAX_ATTEMPTS must be a positive integer" ;;
esac
(( MAX_ATTEMPTS >= 1 )) || die "CAM_UI_REPAIR_MAX_ATTEMPTS must be at least 1"

case "$MAX_TARGETS" in
  ''|*[!0-9]*) die "CAM_UI_REPAIR_MAX_TARGETS must be a positive integer" ;;
esac
(( MAX_TARGETS >= 1 )) || die "CAM_UI_REPAIR_MAX_TARGETS must be at least 1"

mkdir -p "$QA_ROOT"

ANALYSIS="${CAM_UI_REPAIR_ANALYSIS:-}"
if [[ -n "$ANALYSIS" && "$ANALYSIS" != /* ]]; then
  ANALYSIS="$ROOT/$ANALYSIS"
fi

if [[ -z "$ANALYSIS" ]]; then
  ANALYSIS="$(python3 - "$QA_ROOT" <<'PY'
import json
import os
import sys

root = sys.argv[1]
candidates = []
if os.path.isdir(root):
    for name in os.listdir(root):
        path = os.path.join(root, name, "visual-analysis.json")
        if not os.path.isfile(path):
            continue
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            continue
        if data.get("mode", "full") == "repair":
            continue
        candidates.append((os.path.getmtime(path), path, data))

if not candidates:
    raise SystemExit(0)

_, path, data = max(candidates, key=lambda item: item[0])
if data.get("status") == "failed" and int(data.get("issueCount", 0)) > 0:
    print(path)
PY
)"
fi

SESSION_ID="${CAM_UI_REPAIR_SESSION_ID:-}"
ANALYSIS_VIEWPORTS=""
ANALYSIS_STATES=""

if [[ -n "$ANALYSIS" ]]; then
  [[ -f "$ANALYSIS" ]] || die "Repair analysis not found: $ANALYSIS"
  mapfile -t TARGET_LINES < <(
    cd "$CLIENT"
    node --input-type=module - "$ANALYSIS" "$MAX_TARGETS" <<'JS'
import fs from "node:fs";
import { collectRepairTargets } from "./scripts/ui-qa-targeting.mjs";

const [analysisPath, maxTargets] = process.argv.slice(2);
const analysis = JSON.parse(fs.readFileSync(analysisPath, "utf8"));
const targets = collectRepairTargets(analysis, Number(maxTargets));
console.log(analysis.runId || "visual-repair");
console.log(targets.viewports.join(","));
console.log(targets.states.join(","));
JS
  )
  if [[ -z "$SESSION_ID" ]]; then
    SESSION_ID="${TARGET_LINES[0]:-visual-repair}"
  fi
  ANALYSIS_VIEWPORTS="${TARGET_LINES[1]:-}"
  ANALYSIS_STATES="${TARGET_LINES[2]:-}"
fi

VIEWPORTS="${CAM_UI_REPAIR_VIEWPORTS:-$ANALYSIS_VIEWPORTS}"
STATES="${CAM_UI_REPAIR_STATES:-$ANALYSIS_STATES}"

if [[ -z "$VIEWPORTS" || -z "$STATES" ]]; then
  die "No active failed full-gate analysis was found. Set CAM_UI_REPAIR_VIEWPORTS and CAM_UI_REPAIR_STATES for a targeted check, or run the full UI gate once when ready to finalize."
fi

if [[ -z "$SESSION_ID" ]]; then
  SESSION_ID="manual-$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || printf 'workspace')"
fi
SESSION_SAFE="$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9._-' '-')"
SESSION_DIR="$QA_ROOT/repair-sessions"
COUNT_FILE="$SESSION_DIR/$SESSION_SAFE.count"
mkdir -p "$SESSION_DIR"

if [[ "${CAM_UI_REPAIR_SESSION_RESET:-0}" == "1" ]]; then
  rm -f "$COUNT_FILE"
fi

ATTEMPTS=0
if [[ -f "$COUNT_FILE" ]]; then
  ATTEMPTS="$(cat "$COUNT_FILE" 2>/dev/null || printf '0')"
fi
case "$ATTEMPTS" in
  ''|*[!0-9]*) ATTEMPTS=0 ;;
esac

if (( ATTEMPTS >= MAX_ATTEMPTS )); then
  printf 'ERROR: Targeted repair budget exhausted for session %s (%s/%s). Stop automatic retries and inspect the remaining diff before running another full gate.\n'     "$SESSION_ID" "$ATTEMPTS" "$MAX_ATTEMPTS" >&2
  exit 3
fi

ATTEMPTS=$((ATTEMPTS + 1))
printf '%s\n' "$ATTEMPTS" > "$COUNT_FILE"

note "Targeted UI repair check $ATTEMPTS/$MAX_ATTEMPTS"
printf 'Session: %s\n' "$SESSION_ID"
printf 'Profile: %s (%s)\n' "$PROFILE" "$ROUTE_PATH"
printf 'Viewports: %s\n' "$VIEWPORTS"
printf 'States: %s\n' "$STATES"
if [[ -n "$ANALYSIS" ]]; then
  printf 'Source analysis: %s\n' "$ANALYSIS"
fi

note "Lightweight structural check"
cd "$ROOT"
git diff --check

if [[ ! -d "$CLIENT/node_modules" ]]; then
  note "Frontend dependency preflight"
  (cd "$CLIENT" && npm ci)
fi

CHANGED="$({
  git diff --name-only origin/main...HEAD 2>/dev/null || true
  git diff --name-only
  git diff --name-only --cached
  git ls-files --others --exclude-standard
} | awk 'NF' | sort -u)"
export CAM_UI_CHANGED_FILES="$CHANGED"

note "Smart targeted frontend tests"
CAM_UI_TESTS_OVERRIDE="${CAM_UI_REPAIR_TESTS:-}" \
  bash "$ROOT/scripts/cam-ui-run-smart-tests.sh"

if [[ "${CAM_UI_REPAIR_TYPECHECK:-0}" == "1" ]]; then
  note "Optional TypeScript check"
  (cd "$CLIENT" && npm run typecheck)
fi

if ! command -v google-chrome >/dev/null 2>&1 && ! command -v google-chrome-stable >/dev/null 2>&1; then
  note "Browser runtime preflight"
  bash "$ROOT/scripts/cam-install-ui-browser.sh"
fi

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

ROOT_URL="http://$HOST:$PORT/"
URL="http://$HOST:$PORT$ROUTE_PATH"
SERVER_LOG="$QA_ROOT/repair-dev-server.log"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT INT TERM

note "Fast Vite dev server"
cd "$CLIENT"
npm run dev -- --host "$HOST" --port "$PORT" --strictPort >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl --silent --fail --max-time 1 "$ROOT_URL" >/dev/null; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    cat "$SERVER_LOG" >&2 || true
    die "UI repair dev server exited before becoming ready."
  fi
  sleep 0.1
done

curl --silent --fail --max-time 2 "$ROOT_URL" >/dev/null || {
  cat "$SERVER_LOG" >&2 || true
  die "UI repair dev server did not become ready at $ROOT_URL"
}

note "Targeted Browser + visual regression QA"
set +e
CAM_UI_QA_MODE=repair npm run ui:qa --   --mode repair   --url "$URL"   --fixture "$FIXTURE"   --plan "$PLAN"   --viewports "$VIEWPORTS"   --states "$STATES"   --baseline-dir "$BASELINE_DIR"   --strict
STATUS=$?
set -e

if (( STATUS != 0 )); then
  printf '\nTargeted repair check failed. Read the newest repair visual-analysis.md, make one causally scoped fix, then retry within the %s-attempt budget. Do not rerun the full gate yet.\n' "$MAX_ATTEMPTS" >&2
  exit "$STATUS"
fi

note "Targeted repair check passed"
printf 'No production build or full frontend test suite was run in this repair iteration.\n'
printf 'Run the full UI gate once when the implementation is ready to finalize.\n'
