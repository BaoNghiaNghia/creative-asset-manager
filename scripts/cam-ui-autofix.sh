#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
QA_ROOT="$CLIENT/.ui-qa"
SESSION_DIR="$QA_ROOT/autofix-sessions"
PLAN_FILE="$QA_ROOT/autofix-plan.json"
TASK="${CAM_UI_TASK:-}"
MAX_REPAIRS="${CAM_UI_AUTOFIX_MAX_REPAIRS:-2}"

note() {
  printf '\n==> %s\n' "$*"
}

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

case "$MAX_REPAIRS" in
  ''|*[!0-9]*) die "CAM_UI_AUTOFIX_MAX_REPAIRS must be a positive integer" ;;
esac
(( MAX_REPAIRS >= 1 && MAX_REPAIRS <= 2 )) ||
  die "CAM_UI_AUTOFIX_MAX_REPAIRS must be 1 or 2; automatic UI repair is intentionally bounded."

mkdir -p "$QA_ROOT" "$SESSION_DIR"

CHANGED="$({
  git -C "$ROOT" diff --name-only origin/main...HEAD 2>/dev/null || true
  git -C "$ROOT" diff --name-only
  git -C "$ROOT" diff --name-only --cached
  git -C "$ROOT" ls-files --others --exclude-standard
} | awk 'NF' | sort -u)"
export CAM_UI_CHANGED_FILES="$CHANGED"

BASE_COMMIT="$(git -C "$ROOT" rev-parse HEAD)"
cd "$CLIENT"
CAM_UI_TASK="$TASK" CAM_UI_CHANGED_FILES="$CHANGED" CAM_UI_BASE_COMMIT="$BASE_COMMIT"   node scripts/ui-autofix-plan.mjs > "$PLAN_FILE"
cd "$ROOT"

mapfile -t PLAN_LINES < <(
  python3 - "$PLAN_FILE" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as handle:
    plan = json.load(handle)
print(plan.get("sessionId", "ui-autofix"))
print(plan.get("scope", "unknown"))
print(plan.get("profile") or "")
print("1" if plan.get("visualProfileSupported") else "0")
print(",".join(plan.get("viewports") or []))
print(",".join(plan.get("states") or []))
print(plan.get("reason", ""))
PY
)

SESSION_ID="${CAM_UI_AUTOFIX_SESSION:-${PLAN_LINES[0]:-ui-autofix}}"
SCOPE="${PLAN_LINES[1]:-unknown}"
PROFILE="${PLAN_LINES[2]:-}"
VISUAL_SUPPORTED="${PLAN_LINES[3]:-0}"
VIEWPORTS="${PLAN_LINES[4]:-desktop}"
STATES="${PLAN_LINES[5]:-default}"
PLAN_REASON="${PLAN_LINES[6]:-}"
SESSION_SAFE="$(printf '%s' "$SESSION_ID" | tr -c 'A-Za-z0-9._-' '-')"
FINAL_MARKER="$SESSION_DIR/$SESSION_SAFE.final-attempted"
PASS_MARKER="$SESSION_DIR/$SESSION_SAFE.passed"

if [[ "${CAM_UI_AUTOFIX_RESET:-0}" == "1" ]]; then
  rm -f "$FINAL_MARKER" "$PASS_MARKER"
fi

note "UI Auto-Fix plan"
printf 'Session: %s\n' "$SESSION_ID"
printf 'Scope: %s\n' "$SCOPE"
printf 'Profile: %s\n' "${PROFILE:-none}"
printf 'Changed files: %s\n' "$(printf '%s\n' "$CHANGED" | awk 'NF' | wc -l)"
printf 'Visual profile: %s\n' "$PLAN_REASON"
printf 'Plan: %s\n' "$PLAN_FILE"

if [[ "${CAM_UI_AUTOFIX_PLAN_ONLY:-0}" == "1" ]]; then
  exit 0
fi

TARGETED_VERIFIED=0
if [[ "$VISUAL_SUPPORTED" == "1" ]]; then
  note "Bounded targeted repair verification"
  set +e
  CAM_UI_QA_PROFILE="$PROFILE" \
  CAM_UI_REPAIR_SESSION_ID="$SESSION_ID" \
  CAM_UI_REPAIR_SESSION_RESET="${CAM_UI_AUTOFIX_RESET:-0}" \
  CAM_UI_REPAIR_MAX_ATTEMPTS="$MAX_REPAIRS" \
  CAM_UI_REPAIR_VIEWPORTS="$VIEWPORTS" \
  CAM_UI_REPAIR_STATES="$STATES" \
    bash "$ROOT/scripts/cam-ui-repair-check.sh"
  REPAIR_STATUS=$?
  set -e

  if (( REPAIR_STATUS != 0 )); then
    printf '\nAuto-Fix targeted verification did not pass (exit %s). Read the newest repair visual-analysis.md, make one causally scoped edit, and rerun this same Auto-Fix session. The repair budget is capped at %s; do not run the full gate yet. If the mismatch is an intentional user-approved redesign, create a governed baseline proposal instead of rewriting baselines directly; acceptance still requires a separate explicit user confirmation.\n' \
      "$REPAIR_STATUS" "$MAX_REPAIRS" >&2
    exit "$REPAIR_STATUS"
  fi
  TARGETED_VERIFIED=1
else
  note "No fixture-backed visual profile for this UI scope"
  printf 'Skipping Asset Explorer targeted visual QA because it would be false coverage for %s. Smart Test Selection will run once inside the final gate together with typecheck/build and the project-wide authenticated smoke.\n' "$SCOPE"
fi

if [[ -f "$PASS_MARKER" ]]; then
  note "Auto-Fix session already finalized successfully"
  exit 0
fi

if [[ -f "$FINAL_MARKER" ]]; then
  printf 'ERROR: The final full gate was already attempted for Auto-Fix session %s. Do not loop the full gate. Inspect the previous failure and only restart after a causally scoped repair with CAM_UI_AUTOFIX_RESET=1.\n' "$SESSION_ID" >&2
  exit 4
fi

note "One final full UI gate"
date -u +"%Y-%m-%dT%H:%M:%SZ" > "$FINAL_MARKER"

set +e
CAM_UI_FORCE=1 \
CAM_UI_SMART_TESTS=1 \
CAM_UI_QA_PROFILE="${PROFILE:-explorer-viewer}" \
CAM_UI_SKIP_FRONTEND_TESTS="$TARGETED_VERIFIED" \
  bash "$ROOT/scripts/cam-ui-gate.sh"
FINAL_STATUS=$?
set -e

if (( FINAL_STATUS != 0 )); then
  printf '\nFinal UI gate failed (exit %s). This Auto-Fix session will not rerun the full gate automatically. Use the generated diagnostics to make a targeted repair, then explicitly reset this session before one new final attempt.\n' "$FINAL_STATUS" >&2
  exit "$FINAL_STATUS"
fi

date -u +"%Y-%m-%dT%H:%M:%SZ" > "$PASS_MARKER"
note "UI Auto-Fix verification complete"
printf 'Targeted verification passed and the final full gate ran once successfully.\n'
printf 'Production was not deployed.\n'
