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
# Match CI: validate both audited branches and upgrade only on this
# disposable database. Protected merged histories must never be downgraded.
"$PYTHON_BIN" - <<'PY'
from alembic.config import Config
from alembic.script import ScriptDirectory
script = ScriptDirectory.from_config(Config("alembic.ini"))
heads = script.get_heads()
assert len(heads) == 1, f"Expected one migration head, got {heads}"
certification = script.get_revision("0174_catinat_catalog_cert")
assert certification.down_revision == "0173_catinat_branding_merge"
batch = script.get_revision("0175_catinat_batch_evidence")
assert batch.down_revision == "0174_catinat_catalog_cert"
waste = script.get_revision("0176_catinat_waste_witness")
assert waste.down_revision == "0175_catinat_batch_evidence"
# Allow future additive migrations while preserving the audited chain.
revisions = {revision.revision for revision in script.walk_revisions()}
assert {"0176_catinat_waste_witness", "0175_catinat_batch_evidence",
        "0174_catinat_catalog_cert",
        "0173_catinat_branding_merge"} <= revisions
merged = script.get_revision("0173_catinat_branding_merge")
assert set(merged.down_revision) == {
    "0172_catinat_transfers",
    "0170_branding_dna_browser_observation_reviews",
}
for ancestor in merged.down_revision:
    assert script.get_revision(ancestor).revision == ancestor
PY
"$PYTHON_BIN" -m alembic upgrade head 2>&1 | tee "$ARTIFACT_DIR/migration-upgrade.log"
EXPECTED_HEAD="$("$PYTHON_BIN" -m alembic heads | awk '{print $1}')"
test -n "$EXPECTED_HEAD"
"$PYTHON_BIN" -m alembic current | grep -F "$EXPECTED_HEAD"

timeout 15m "$PYTHON_BIN" -m unittest \
  tests.integration.test_postgresql \
  tests.integration.test_ai_operations_postgresql \
  tests.integration.test_inventory_postgresql \
  tests.integration.test_elasticsearch \
  tests.integration.test_pipeline_e2e -v 2>&1 | tee "$ARTIFACT_DIR/test.log"
