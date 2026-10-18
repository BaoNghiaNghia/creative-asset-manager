#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="$ROOT_DIR/infrastructure/docker/docker-compose.integration.yml"
ARTIFACT_DIR="$ROOT_DIR/artifacts/integration"
PROJECT_NAME="cam-integration-${USER:-local}-$$"
mkdir -p "$ARTIFACT_DIR"

cleanup() {
  local exit_code=$?
  if (( exit_code != 0 )); then
    docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" logs --no-color \
      >"$ARTIFACT_DIR/services.log" 2>&1 || true
  fi
  docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" down -v --remove-orphans \
    >/dev/null 2>&1 || true
  exit "$exit_code"
}
trap cleanup EXIT INT TERM

docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" up -d --wait --wait-timeout 150
POSTGRES_PORT="$(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" port postgres 5432 | awk -F: '{print $NF}')"
ELASTICSEARCH_PORT="$(docker compose -p "$PROJECT_NAME" -f "$COMPOSE_FILE" port elasticsearch 9200 | awk -F: '{print $NF}')"

export DATABASE_URL="postgresql+psycopg://cam_test:cam_test@127.0.0.1:${POSTGRES_PORT}/cam_integration"
export INTEGRATION_DATABASE_URL="$DATABASE_URL"
export ELASTICSEARCH_URL="http://127.0.0.1:${ELASTICSEARCH_PORT}"
export INTEGRATION_ELASTICSEARCH_URL="$ELASTICSEARCH_URL"

cd "$ROOT_DIR/apps/api"
PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
if [[ ! -x "$PYTHON_BIN" ]]; then PYTHON_BIN=python; fi

heads="$($PYTHON_BIN -m alembic heads | grep -c '(head)')"
test "$heads" -eq 1
$PYTHON_BIN -m alembic upgrade head 2>&1 | tee "$ARTIFACT_DIR/migration-upgrade.log"
# Only this isolated, freshly-created cam_integration DB may downgrade protected
# append-only ledgers in the migration regression test. Never export these
# opt-ins globally or permit them in a real production/staging database.
if [[ "${DATABASE_URL##*/}" != "cam_integration" || "${INTEGRATION_DATABASE_URL}" != "${DATABASE_URL}" ]]; then
  echo "Refusing destructive integration downgrade outside disposable cam_integration" >&2
  exit 1
fi
env CAM_ALLOW_SCAN_RESULT_DATA_DROP_FOR_TEST=1 \
    CAM_ALLOW_SCAN_REVIEW_DATA_DROP_FOR_TEST=1 \
    CAM_ALLOW_SCAN_RECEIPT_DATA_DROP_FOR_TEST=1 \
    CAM_ALLOW_RAG_DATA_DROP_FOR_TEST=1 \
    CAM_ALLOW_HANDOFF_HISTORY_DROP_FOR_TEST=1 \
    "$PYTHON_BIN" -m alembic downgrade 0012_ai_batch_processing 2>&1 | tee "$ARTIFACT_DIR/migration-downgrade.log"
$PYTHON_BIN -m alembic upgrade head 2>&1 | tee -a "$ARTIFACT_DIR/migration-upgrade.log"

timeout 15m "$PYTHON_BIN" -m unittest \
  tests.integration.test_postgresql \
  tests.integration.test_ai_operations_postgresql \
  tests.integration.test_inventory_postgresql \
  tests.integration.test_elasticsearch \
  tests.integration.test_pipeline_e2e -v 2>&1 | tee "$ARTIFACT_DIR/test.log"
