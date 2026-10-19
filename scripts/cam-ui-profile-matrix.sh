#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"
HOST="127.0.0.1"
VIEWPORTS="${CAM_UI_VIEWPORTS:-desktop,tabletPortrait,mobile}"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

note() {
  printf '\n==> %s\n' "$*"
}

if [[ "${CAM_UI_VISUAL_UPDATE:-0}" == "1" ]]; then
  die "Direct baseline update is disabled. Use the governed proposal/acceptance workflow per profile."
fi

if [[ "${CAM_UI_QA_BROWSER_PREINSTALLED:-0}" == "1" ]] && [[ "${CAM_UI_QA_BROWSER:-chrome}" == "chromium" || "${CAM_UI_QA_BROWSER:-chrome}" == "webkit" || "${CAM_UI_QA_BROWSER:-chrome}" == "firefox" ]]; then
  note "Using the locked preinstalled Playwright ${CAM_UI_QA_BROWSER} browser (CI)"
else
  bash "$ROOT/scripts/cam-install-ui-browser.sh"
fi

CHANGED="${CAM_UI_CHANGED_FILES:-}"
if [[ -z "$CHANGED" ]]; then
  CHANGED="$({
    git -C "$ROOT" diff --name-only origin/main...HEAD 2>/dev/null || true
    git -C "$ROOT" diff --name-only
    git -C "$ROOT" diff --name-only --cached
    git -C "$ROOT" ls-files --others --exclude-standard
  } | awk 'NF' | sort -u)"
fi

PROFILES=()
SELECTION_MODE="explicit"
SELECTION_REASON="CAM_UI_QA_PROFILES"

if [[ -n "${CAM_UI_QA_PROFILES:-}" ]]; then
  IFS=',' read -r -a RAW_PROFILES <<< "$CAM_UI_QA_PROFILES"
  for profile in "${RAW_PROFILES[@]}"; do
    profile="$(printf '%s' "$profile" | xargs)"
    [[ -n "$profile" ]] && PROFILES+=("$profile")
  done
else
  mapfile -t SELECTION < <(
    cd "$CLIENT"
    CAM_UI_CHANGED_FILES="$CHANGED" node scripts/ui-qa-profile-selection.mjs --format-lines
  )
  SELECTION_MODE="${SELECTION[0]:-default}"
  SELECTION_REASON="${SELECTION[1]:-selector-output-missing}"
  if (( ${#SELECTION[@]} > 2 )); then
    PROFILES=("${SELECTION[@]:2}")
  fi
fi

(( ${#PROFILES[@]} > 0 )) || PROFILES=("explorer-viewer")

for profile in "${PROFILES[@]}"; do
  node "$CLIENT/scripts/ui-qa-profiles.mjs" --profile "$profile" --format-lines >/dev/null
done

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
LOG_DIR="$CLIENT/.ui-qa"
mkdir -p "$LOG_DIR"
SERVER_LOG="$LOG_DIR/profile-matrix-preview.log"

cleanup() {
  if [[ -n "${SERVER_PID:-}" ]]; then
    # npm creates a child Vite server; stop its entire isolated process group.
    kill -TERM -- "-$SERVER_PID" >/dev/null 2>&1 || true
    wait "$SERVER_PID" >/dev/null 2>&1 || true
  fi
  # All profile reports and screenshots have been consumed by matrix-summary
  # before EXIT. Do not retain transient browser artifacts on the VPS.
  # Preserve governed golden baselines and proposals: only timestamp-named
  # completed run folders inside apps/client/.ui-qa are eligible.
  # GitHub Actions has post-matrix visual-review and artifact-upload steps.
  # Keep captures on its ephemeral runner until those steps consume them.
  # Local/VPS executions purge immediately, unless explicitly preserved.
  if [[ "${CAM_UI_QA_PRESERVE_RUNS:-0}" != "1" && "${GITHUB_ACTIONS:-false}" != "true" ]]; then
    node "$CLIENT/scripts/ui-qa-purge-runs.mjs" "$LOG_DIR" >&2 || true
    rm -f -- "$SERVER_LOG"
  fi
}
trap cleanup EXIT

cd "$CLIENT"
setsid npm run preview -- --host "$HOST" --port "$PORT" --strictPort >"$SERVER_LOG" 2>&1 &
SERVER_PID=$!

for _ in $(seq 1 80); do
  if curl --silent --fail --max-time 1 "$ROOT_URL" >/dev/null; then
    break
  fi
  if ! kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    cat "$SERVER_LOG" >&2 || true
    die "UI profile matrix preview exited before becoming ready."
  fi
  sleep 0.1
done

curl --silent --fail --max-time 2 "$ROOT_URL" >/dev/null || {
  cat "$SERVER_LOG" >&2 || true
  die "UI profile matrix preview did not become ready at $ROOT_URL"
}

note "Fixture-backed UI profile matrix"
printf 'Selection mode: %s\n' "$SELECTION_MODE"
printf 'Reason: %s\n' "$SELECTION_REASON"
printf 'Profiles (%s):\n' "${#PROFILES[@]}"
printf '  %s\n' "${PROFILES[@]}"

FAILURES=()
PASSED=0
MATRIX_STARTED_AT="$(date +%s)"
KEEP_RUNS="${CAM_UI_QA_KEEP:-$(( ${#PROFILES[@]} + 5 ))}"

for profile in "${PROFILES[@]}"; do
  mapfile -t PROFILE_VALUES < <(
    node "$CLIENT/scripts/ui-qa-profiles.mjs" --profile "$profile" --format-lines
  )
  PROFILE_ROUTE="${PROFILE_VALUES[0]}"
  PROFILE_FIXTURE="${PROFILE_VALUES[1]}"
  PROFILE_PLAN="${PROFILE_VALUES[2]}"
  PROFILE_BASELINE="${PROFILE_VALUES[3]}"

  [[ "$PROFILE_ROUTE" == /* ]] || PROFILE_ROUTE="/$PROFILE_ROUTE"
  URL="http://$HOST:$PORT$PROFILE_ROUTE"

  note "UI QA profile: $profile"
  QA_ARGS=(
    --url "$URL"
    --fixture "$ROOT/$PROFILE_FIXTURE"
    --plan "$ROOT/$PROFILE_PLAN"
    --viewports "$VIEWPORTS"
  )

  if [[ "${CAM_UI_VISUAL_SKIP:-0}" != "1" ]]; then
    QA_ARGS+=(--baseline-dir "$ROOT/$PROFILE_BASELINE")
  fi
  if [[ "${CAM_UI_QA_STRICT:-1}" == "1" ]]; then
    QA_ARGS+=(--strict)
  fi

  set +e
  CAM_UI_QA_KEEP="$KEEP_RUNS" npm run ui:qa -- "${QA_ARGS[@]}"
  status=$?
  set -e

  if (( status == 0 )); then
    PASSED=$((PASSED + 1))
  else
    FAILURES+=("$profile:$status")
  fi
done

# Impeccable augments, but never replaces, the Playwright functional matrix.
# No npm downloads from the QA process; CLI is an explicitly pinned installation.
if [[ "${CAM_IMPECCABLE_MODE:-advisory}" != "off" ]]; then
  note "Impeccable Design Quality (mode=${CAM_IMPECCABLE_MODE:-advisory})"
  if ! node "$CLIENT/scripts/ui-qa-impeccable.mjs" \
    --mode "${CAM_IMPECCABLE_MODE:-advisory}" \
    --output "$LOG_DIR/impeccable-report.json"; then
    if [[ "${CAM_IMPECCABLE_MODE:-advisory}" == "enforce" ]]; then
      FAILURES+=("impeccable-design:2")
    else
      printf 'WARN: Impeccable advisory scan was not completed; inspect report.\\n' >&2
    fi
  fi
fi

MATRIX_PROFILE_CSV="$(IFS=,; printf '%s' "${PROFILES[*]}")"
if ! node "$CLIENT/scripts/ui-qa-matrix-summary.mjs" \
  --profiles "$MATRIX_PROFILE_CSV" \
  --viewports "$VIEWPORTS" \
  --since "$MATRIX_STARTED_AT" \
  --output "$LOG_DIR/matrix-summary.json"; then
  FAILURES+=("matrix-summary:2")
fi

# New Research Inbox checks are functional-only until their visuals have an
# explicitly reviewed baseline. Run sequentially in the existing loopback
# browser preview; do not contaminate the legacy profile's visual report.
if [[ "${CAM_UI_QA_RESEARCH_INBOX:-1}" == "1" ]]; then
  for profile in "${PROFILES[@]}"; do
    if [[ "$profile" == "realistic-review-ugc" ]]; then
      note "Research Inbox interactive QA (no visual baseline auto-acceptance)"
      if ! CAM_UI_QA_KEEP="$KEEP_RUNS" npm run ui:qa -- \
        --url "${ROOT_URL}realistic-review-ugc" \
        --fixture "$ROOT/apps/client/scripts/fixtures/realistic-review-ugc.json" \
        --plan "$ROOT/docs/operations/ui-qa-research-inbox-plan.json" \
        --viewports "$VIEWPORTS" --strict; then
        FAILURES+=("research-inbox:2")
      fi
      break
    fi
  done
fi

printf '\nUI profile matrix summary: %s/%s passed.\n' "$PASSED" "${#PROFILES[@]}"
if (( ${#FAILURES[@]} > 0 )); then
  printf 'Failures:\n' >&2
  printf '  %s\n' "${FAILURES[@]}" >&2
  exit 2
fi

printf 'All selected fixture-backed profiles passed.\n'
