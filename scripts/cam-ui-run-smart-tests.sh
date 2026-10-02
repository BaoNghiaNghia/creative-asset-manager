#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
CLIENT="$ROOT/apps/client"

note() {
  printf '\n==> %s\n' "$*"
}

CHANGED="${CAM_UI_CHANGED_FILES:-}"
if [[ -z "$CHANGED" ]]; then
  CHANGED="$({
    git -C "$ROOT" diff --name-only origin/main...HEAD 2>/dev/null || true
    git -C "$ROOT" diff --name-only
    git -C "$ROOT" diff --name-only --cached
    git -C "$ROOT" ls-files --others --exclude-standard
  } | awk 'NF' | sort -u)"
fi

if [[ -n "${CAM_UI_TESTS_OVERRIDE:-}" ]]; then
  note "Explicit targeted frontend tests"
  read -r -a TEST_ARGS <<< "$CAM_UI_TESTS_OVERRIDE"
  (cd "$CLIENT" && npm test -- "${TEST_ARGS[@]}")
  exit 0
fi

if [[ "${CAM_UI_SMART_TESTS:-1}" == "0" ]]; then
  note "Smart test selection disabled; running full frontend test suite"
  (cd "$CLIENT" && npm test)
  exit 0
fi

mapfile -t SELECTION < <(
  cd "$CLIENT"
  CAM_UI_CHANGED_FILES="$CHANGED"     node scripts/ui-smart-tests.mjs --format lines
)

MODE="${SELECTION[0]:-full}"
REASON="${SELECTION[1]:-selector-output-missing}"
TESTS=()
if (( ${#SELECTION[@]} > 2 )); then
  TESTS=("${SELECTION[@]:2}")
fi

printf 'Smart test mode: %s\n' "$MODE"
printf 'Reason: %s\n' "$REASON"

case "$MODE" in
  targeted)
    if (( ${#TESTS[@]} == 0 )); then
      printf 'ERROR: Smart test selector returned targeted mode without tests.\n' >&2
      exit 2
    fi
    printf 'Tests (%s):\n' "${#TESTS[@]}"
    printf '  %s\n' "${TESTS[@]}"
    (cd "$CLIENT" && npm test -- "${TESTS[@]}")
    ;;
  full)
    (cd "$CLIENT" && npm test)
    ;;
  none)
    printf 'No relevant Vitest files required for this change; Browser/typecheck/build coverage remains active in the surrounding workflow.\n'
    ;;
  *)
    printf 'ERROR: Unknown smart test mode: %s\n' "$MODE" >&2
    exit 2
    ;;
esac
