#!/usr/bin/env bash

if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf 'ERROR: Do not source this script.\n' >&2
  return 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
APP_ROOT="${CAM_APP_ROOT:-/opt/creative-asset-manager}"
CURRENT="$APP_ROOT/current"

die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

usage() {
  cat <<'USAGE'
Usage:
  scripts/cam-search-maintenance.sh flags
  scripts/cam-search-maintenance.sh test-search
  scripts/cam-search-maintenance.sh test-workspace-search
  scripts/cam-search-maintenance.sh search <search_cli arguments...>

Runs bounded Search V3 maintenance against the active immutable production
release. Production environment values are never evaluated by the shell and
command output is redacted against configured environment values.
USAGE
}

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -L "$CURRENT" ]] || die "Active backend release is unavailable."

RELEASE="$(readlink -f -- "$CURRENT")"
API_ROOT="$RELEASE/apps/api"
PYTHON="$API_ROOT/.venv/bin/python"
RUNNER="$RELEASE/deploy/tools/production_env.py"

[[ -x "$PYTHON" ]] || die "Active release Python runtime is unavailable."
[[ -x "$RUNNER" ]] || die "Production environment runner is unavailable."
[[ -f "$ENV_FILE" ]] || die "Production environment file is unavailable."

run_redacted() {
  "$RUNNER" run-redacted \
    --env-file "$ENV_FILE" \
    --expected-owner-uid 0 \
    -- "$@"
}

flag_state() {
  local name="$1"
  if "$RUNNER" flag-enabled \
      --env-file "$ENV_FILE" \
      --expected-owner-uid 0 \
      --name "$name"
  then
    printf '%s=true\n' "$name"
  else
    printf '%s=false\n' "$name"
  fi
}

case "${1:-}" in
  flags)
    flag_state SEARCH_PROJECTION_ENABLED
    flag_state SEARCH_V3_ENABLED
    flag_state ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED
    flag_state DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED
    ;;

  test-search)
    cd "$API_ROOT"
    run_redacted "$PYTHON" -m pytest -q \
      tests/modules/ai_metadata/test_projection.py \
      tests/modules/ai_metadata/test_projection_service.py \
      tests/modules/search/test_index_types.py \
      tests/modules/search/test_query_builder.py
    ;;

  test-workspace-search)
    cd "$CHECKOUT_ROOT/apps/api"
    run_redacted env PYTHONPATH="$CHECKOUT_ROOT/apps/api" \
      "$PYTHON" -m pytest -q \
      "$CHECKOUT_ROOT/apps/api/tests/modules/ai_metadata/test_projection.py" \
      "$CHECKOUT_ROOT/apps/api/tests/modules/ai_metadata/test_projection_service.py" \
      "$CHECKOUT_ROOT/apps/api/tests/modules/search/test_index_types.py" \
      "$CHECKOUT_ROOT/apps/api/tests/modules/search/test_query_builder.py" \
      "$CHECKOUT_ROOT/apps/api/tests/modules/search/test_active_analysis_repository.py" \
      "$CHECKOUT_ROOT/apps/api/tests/modules/search/test_operations_service.py"
    ;;

  search)
    shift
    (($#)) || die "search requires search_cli arguments."
    cd "$API_ROOT"
    run_redacted "$PYTHON" -m app.operations.search_cli "$@"
    ;;

  -h|--help|"")
    usage
    ;;

  *)
    die "Unknown command: $1"
    ;;
esac
