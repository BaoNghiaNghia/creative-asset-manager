# Search projection rebuild and Elasticsearch reindex runbook

## Safety model

PostgreSQL metadata_json is authoritative. These commands never invoke an AI
provider. Every run is tenant-scoped and stores its filters, keyset cursor,
progress counters, per-analysis status, errors, target physical index, and
cancellation request in PostgreSQL.

Before a mutating run, enable only the needed rollout flags:

```dotenv
SEARCH_PROJECTION_ENABLED=true
ELASTICSEARCH_V2_ENABLED=true
```

Start with `--dry-run`. Dry-run records selection/progress but does not update
PostgreSQL projections, create an Elasticsearch index, index documents, or
switch aliases.

## Commands

Run from `apps/api` with its virtual environment active:

```bash
python -m app.operations.search_cli search:rebuild-projections \
  --tenant-id tenant-a \
  --target-projection-version search-projection-v2 \
  --page-size 100 \
  --dry-run
```

```bash
python -m app.operations.search_cli search:reindex-assets \
  --tenant-id tenant-a \
  --current-projection-version search-projection-v2 \
  --index-version 20260720-001 \
  --elasticsearch-url https://elasticsearch.example.com
```

```bash
python -m app.operations.search_cli search:rebuild-and-reindex \
  --tenant-id tenant-a \
  --metadata-profile default \
  --target-projection-version search-projection-v2 \
  --index-version 20260720-002 \
  --elasticsearch-url https://elasticsearch.example.com
```

Available selection controls:

- `--metadata-profile PROFILE`
- `--current-projection-version VERSION`
- repeatable `--asset-id ASSET_ID` (maximum 1,000)
- `--only-missing`
- `--page-size 1..500`
- `--dry-run`

The final JSON line contains run ID, status, cursor, physical target index, and
scanned/processed/succeeded/failed/skipped metrics. Database counters update
after every bounded page.

## Resume failed work

Keep the returned run ID. A failed reindex retains its physical target index and
does not switch aliases. Retry only failed items on the same run:

```bash
python -m app.operations.search_cli search:rebuild-and-reindex \
  --tenant-id tenant-a \
  --run-id RUN_ID \
  --only-failed \
  --elasticsearch-url https://elasticsearch.example.com
```

Completed runs are idempotent no-ops. Resuming a run reuses its filters,
checkpoint, item identities, and physical target index.

## Cancellation

Request cooperative cancellation:

```bash
python -m app.operations.search_cli search:cancel \
  --tenant-id tenant-a \
  --run-id RUN_ID
```

Cancellation is observed between pages. A cancelled or failed reindex never
switches aliases. The partially populated versioned physical index is retained
for diagnosis or a later resume.

## Alias rollback

Alias switching is atomic and happens only after all selected items succeed.
If post-switch validation fails, use the Elasticsearch v2 adapter rollback
operation to switch both read and write aliases to the previous physical index
recorded in the command's `alias_switch` JSON and the run's
`alias_switch_json` database field. Never delete the previous index until
validation and the rollback window are complete.

## Database rollback

Stop operational commands, cancel active runs, and export audit records if
needed. Downgrade `0007_search_operations` to `0006_metadata_sidecars`.
Downgrade removes checkpoints only; it does not revert PostgreSQL projections,
delete Elasticsearch indices, or change aliases.

## Step 33R3 lifecycle promotion and cleanup

Keep `ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED=false` until an authorized rollout.
For each physical index: register/build it, run lifecycle verification with an
expected projection version, count/tolerance, failure threshold, ranked golden
fixtures and every tenant expected in the index, then inspect the stored
verification evidence. Only a `verified` record may be activated.

Activation atomically moves both aliases and durably checkpoints `activating`.
If a process stops after the Elasticsearch switch, call the authenticated
`POST /api/v1/admin/search/indices/reconcile?index_prefix=...` operation. It
requires one identical read and write target, promotes that database record,
demotes the old active record to previous, and retires older previous records.
Use `POST /api/v1/admin/search/indices/{previous_record_id}/rollback` to restore
the protected previous index, then verify both aliases and database state.

Always run cleanup with `dry_run=true` first. Destructive cleanup requires a
positive retention age and `confirmed=true`, is limited to 100 records per
call, and checkpoints each candidate as `deletion_pending`. Cancellation stops
between candidates; rerunning resumes checkpoints. Cleanup never selects active
or previous database records and re-reads read/write aliases immediately before
each delete. If database and cluster aliases disagree, reconcile before cleanup.

Rollback migration note: stop lifecycle operations, reconcile all `activating`
records and either activate or re-verify all `verified` records before
downgrading Alembic from 0017 to 0016. The downgrade changes only the PostgreSQL
state constraint; it does not switch aliases or delete indices.