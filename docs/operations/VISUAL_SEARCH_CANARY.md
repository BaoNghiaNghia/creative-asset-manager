# Visual Search canary runbook

## Current release-readiness status

**BLOCKED for production enablement.** The code remains safe to deploy with all
Visual Search flags off, but broad enablement is not approved until this
runbook's gates pass.

## Current VS-12 phase status

- **VS-12A — End-to-end canary tenant eligibility:** complete.
- **VS-12B — Pagination + committed visual query state:** complete.
- **VS-12C — Encoder package boundary cleanup:** next.
- **Production canary:** not yet authorized.
- **Broad rollout:** blocked.

## Required gates

- Tenant-isolation API and Elasticsearch integration tests pass.
- Existing Search V3 regression suite passes.
- SigLIP benchmark is recorded for the pinned revision.
- A human-labelled relevance set of 100--300 queries is evaluated:
  Recall@10, Precision@10, NDCG@10, empty-result rate.
- VPS measurements are recorded with encoder concurrency one:
  encoder RSS/CPU; by-asset, upload and crop p50/p95; KNN p50/p95;
  Elasticsearch memory/disk; a ten-asset backfill sample.
- Rollback is verified: set VISUAL_SEARCH_ENABLED=false and confirm Search V3
  remains healthy.

## First canary

1. Keep every VISUAL_SEARCH feature flag false.
2. Deploy the compatible backend/encoder/index release.
3. Run visual backfill dry-run for one allowlisted tenant and ten assets.
4. Review skipped/enqueued/checkpoint output. Do not execute yet.
5. Enable only the internal tenant allowlist and by-asset operation.
6. Observe diagnostics and worker RSS/CPU for at least one hour.
7. Add upload, then crop, then hybrid text only after each prior stage passes.

## Stop and rollback

Immediately set VISUAL_SEARCH_ENABLED=false when tenant isolation, Search V3,
encoder capacity, Elasticsearch health, or latency gates fail. Stop a running
backfill with SIGINT/SIGTERM; resume only from its emitted checkpoint. Do not
delete visual indexes during the rollback window.

## Evidence record

Record commit SHA, encoder descriptor, tenant allowlist (outside this document),
start/end time, metrics snapshot, process RSS/CPU, ES disk/memory, backfill
checkpoint, quality scores, and operator decision. Never record API keys, query
images, vectors, signed URLs, or cross-tenant asset details.

## End-to-end tenant eligibility

New Visual Search computation, public queries, and explicit backfill require
both `VISUAL_SEARCH_ENABLED=true` and membership in
`VISUAL_SEARCH_CANARY_TENANT_IDS`. An empty allowlist denies every tenant new
Visual Search work. After a tenant is removed from the canary, new query,
embedding, and backfill work remain denied, while retire/delete reconciliation
remains allowed when infrastructure is enabled so stale derived documents can be
removed. That cleanup must not generate a new embedding.

## Pagination and committed query state

Visual Search cursor offsets represent the next post-ranking candidate position.
They advance across selected candidates even if authoritative hydration omits a
stale result. Browser draft crop/text controls are separate from the last
successful committed visual query; Load More replays that committed crop, text,
and source scope without using unsubmitted edits.
