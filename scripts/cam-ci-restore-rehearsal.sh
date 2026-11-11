#!/usr/bin/env bash
# CI-only synthetic restore: absolutely no production data or credentials.
set -euo pipefail
if [[ "${GITHUB_ACTIONS:-}" != "true" || -z "${RUNNER_TEMP:-}" ]]; then
  echo "Synthetic restore integration requires an isolated GitHub runner." >&2
  exit 2
fi
if [[ ! "${CAM_TEST_POSTGRES_IMAGE:-}" =~ ^postgres:(12[.]22|16[.]4)$ ]]; then
  echo "Unsupported CI PostgreSQL image." >&2
  exit 2
fi

work_dir="$(mktemp -d "$RUNNER_TEMP/cam-synthetic-restore.XXXXXXXX")"
target_container="cam-ci-restore-target"
source_db="cam_ci_restore_source"
target_db="cam_restore_rehearsal_ci202611"
bad_db="cam_restore_rehearsal_bad202611"
admin_pass="ci-fixture-postgres-only"
tester_pass="ci-fixture-restorer-only"
cleanup() {
  docker stop --time 5 "$target_container" >/dev/null 2>&1 || true
  rm -f -- "$work_dir/source.dump" "$work_dir/verified.json" "$work_dir/mismatch.json"
  rmdir -- "$work_dir" 2>/dev/null || true
}
trap cleanup EXIT

# A new synthetic source DB in the disposable GitHub CI PostgreSQL service.
PGPASSWORD=cam_test psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
  -U cam_test -d cam_integration -c "CREATE DATABASE $source_db;" >/dev/null
PGPASSWORD=cam_test psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 5432 \
  -U cam_test -d "$source_db" >/dev/null <<'SQL'
CREATE TABLE public.cam_agent_trial_exposures (id integer PRIMARY KEY, arm text NOT NULL);
CREATE TABLE public.cam_agent_trial_qa_reviews (id integer PRIMARY KEY, verdict text NOT NULL);
INSERT INTO public.cam_agent_trial_exposures VALUES (1, 'control'), (2, 'challenger');
INSERT INTO public.cam_agent_trial_qa_reviews VALUES (1, 'synthetic_pass');
SQL
PGPASSWORD=cam_test pg_dump -Fc --no-owner --no-privileges \
  -h 127.0.0.1 -p 5432 -U cam_test -d "$source_db" -f "$work_dir/source.dump"
chmod 600 "$work_dir/source.dump"
expected_sha="$(sha256sum "$work_dir/source.dump" | cut -d' ' -f1)"
expected_md5="$(md5sum "$work_dir/source.dump" | cut -d' ' -f1)"

# Independent ephemeral PostgreSQL, bound to runner loopback on port 55432.
docker run --rm -d --name "$target_container" --network host \
  -e POSTGRES_PASSWORD="$admin_pass" "$CAM_TEST_POSTGRES_IMAGE" \
  -c listen_addresses=127.0.0.1 -c port=55432 >/dev/null
for attempt in $(seq 1 45); do
  if PGPASSWORD="$admin_pass" pg_isready -h 127.0.0.1 -p 55432 -U postgres -d postgres >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
PGPASSWORD="$admin_pass" psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55432 \
  -U postgres -d postgres -c "CREATE ROLE cam_ci_restorer LOGIN PASSWORD '$tester_pass' NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;" >/dev/null
PGPASSWORD="$admin_pass" psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55432 \
  -U postgres -d postgres -c "CREATE DATABASE $target_db OWNER cam_ci_restorer;" >/dev/null
PGPASSWORD="$admin_pass" psql -X -w -v ON_ERROR_STOP=1 -h 127.0.0.1 -p 55432 \
  -U postgres -d postgres -c "CREATE DATABASE $bad_db OWNER cam_ci_restorer;" >/dev/null

PGPASSWORD="$tester_pass" python3 scripts/cam_db_restore_rehearsal.py \
  --dump "$work_dir/source.dump" --sha256 "$expected_sha" --md5 "$expected_md5" \
  --database "$target_db" --db-user cam_ci_restorer --db-port 55432 \
  --require-table cam_agent_trial_exposures --expect-table-count cam_agent_trial_exposures=2 \
  --require-table cam_agent_trial_qa_reviews --expect-table-count cam_agent_trial_qa_reviews=1 \
  --evidence "$work_dir/verified.json" --ack-isolated-target

python3 - "$work_dir/verified.json" <<'PY'
import json
from pathlib import Path
import sys
report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
assert report["RESTORE_REHEARSAL"] == "PASS"
assert report["EXPECTED_TABLE_COUNTS_MATCHED"] is True
assert report["REQUIRED_TABLE_COUNT"] == 2
assert report["EXPECTED_COUNTS_PROVENANCE"] == "OPERATOR_SUPPLIED_UNVERIFIED"
PY

# A wrong operator-supplied count must abort without evidence.
if PGPASSWORD="$tester_pass" python3 scripts/cam_db_restore_rehearsal.py \
    --dump "$work_dir/source.dump" --sha256 "$expected_sha" --md5 "$expected_md5" \
    --database "$bad_db" --db-user cam_ci_restorer --db-port 55432 \
    --require-table cam_agent_trial_exposures --expect-table-count cam_agent_trial_exposures=999 \
    --evidence "$work_dir/mismatch.json" --ack-isolated-target >/dev/null; then
  echo "Expected incorrect synthetic count to fail." >&2
  exit 1
fi
test ! -e "$work_dir/mismatch.json"
echo "Synthetic restore gate PASS; no production data accessed."
