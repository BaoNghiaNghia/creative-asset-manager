# Visual Search backfill

Visual Search uses **progressive indexing** for historical libraries. A large
library does not need to reach 100% historical coverage before SigLIP2 can stay
active in production. New and changed assets continue to enqueue the active
SigLIP2 schema immediately; historical coverage is continuous best-effort work.

The CLI only enqueues visual index jobs and defaults to dry-run:

    python -m app.operations.visual_search_backfill_cli --tenant-id TENANT_ID --schema-version visual_embedding_v2 --max-assets 10

Use `--execute` only after enabling the global backfill flag. Output includes
`checkpoint_asset_id`; resume with `--after-asset-id`. SIGINT/SIGTERM stop
after the current batch.

Both dry-run and executed backfill require Visual Search eligibility:
`VISUAL_SEARCH_ENABLED=true` and target-tenant membership in
`VISUAL_SEARCH_CANARY_TENANT_IDS`. An empty allowlist denies the work. Executed
backfill additionally requires `VISUAL_SEARCH_BACKFILL_ENABLED=true`; otherwise
the command is rejected before it can enqueue jobs. No backfill mode may enqueue
new visual work for an ineligible tenant.

## Production policy

Historical backfill must yield to user-facing and live-ingestion work:

- live/new/changed visual jobs keep priority `20`;
- recently active historical assets use priority `15`;
- archive historical assets use priority `5`;
- `VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS` caps tenant-scoped active SigLIP2
  work (pending + processing + retry) before another backfill slice can enqueue;
- `VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS` bounds one reconciliation slice;
- `VISUAL_SEARCH_BACKFILL_RECENT_DAYS` defines the recent-asset priority
  window.

Production runs `visual_index_sync` on the dedicated
`creative-asset-manager-visual-worker.service` (`WORKER_ROLE=visual`). Image
workers do not claim visual jobs. This gives historical indexing one bounded
consumer even when the Image AI queue contains an older high-priority backlog,
while the isolated encoder remains the single inference concurrency boundary.
The dedicated worker must be drained together with the encoder before alias
cutover or rollback.

Default production-safe values are:

    VISUAL_SEARCH_BACKFILL_MAX_QUEUED_JOBS=250
    VISUAL_SEARCH_BACKFILL_MAX_SLICE_ASSETS=100
    VISUAL_SEARCH_BACKFILL_RECENT_DAYS=30

When the queue cap is reached, backfill returns a throttled slice instead of
adding more work. The run remains resumable. This is expected behavior, not a
failure.

For large libraries, use release evidence mode `progressive_indexing`.
That mode still requires encoder, ANN relevance, load, regression, alias and
rollback evidence, but **historical backfill completion is not a release
prerequisite**. Coverage remains an operational metric.

## Embedding reuse and deduplication

Do not add a cross-tenant embedding cache. CAM already deduplicates Assets by
`(tenant_id, content_hash)`, visual jobs are idempotent by
asset/content/schema, and the isolated encoder has a bounded image embedding
cache keyed by request SHA-256 plus the pinned embedding descriptor. This
avoids duplicate inference within the supported security boundary without
leaking content existence across tenants.

Keep the strict SigLIP2 alias and its rollback index until the post-cutover
soak period is complete. Historical coverage can continue growing after that
without blocking normal Visual Search operation.
