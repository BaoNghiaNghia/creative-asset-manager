# Implementation Plan — Output of Step 00

## Purpose

This plan maps the audited MVP to the supplied target architecture. It schedules future work only; Step 00 does not implement application behavior, schema changes, infrastructure, or feature flags.

The plan preserves working Google Drive and SharePoint browsing while introducing PostgreSQL authority, durable asynchronous processing, dynamic metadata, and Elasticsearch behind explicit flags. Every stage should be small, independently reviewable, reversible, and covered by tests.

## Architectural transition

The safe transition is additive:

1. Define contracts and identifiers without changing current reads.
2. Establish PostgreSQL migrations and tenant/source/asset registries.
3. Add durable jobs/outbox and incremental source synchronization.
4. Download and hash content through isolated provider/storage adapters.
5. Persist dynamic AI metadata and build a stable versioned projection.
6. Introduce Elasticsearch v2 with dual-write, backfill, compare, and cutover.
7. Add AI, external ingestion, exports, and operations only after the foundation is measurable.

Directus and the current in-memory index should remain a legacy read path until the PostgreSQL/Elasticsearch path reaches parity. They must not become the long-term source of truth.

## Recommended module locations

Use the existing feature-module convention and add contracts/infrastructure gradually rather than rewriting all modules.

```text
apps/api/app/
  core/
    config.py                 # typed settings and feature flags
    auth/                     # app principal, tenant context, policies
  domain/
    assets/                   # identities, entities, invariants
    metadata/                 # dynamic document/profile concepts
    processing/               # job types, idempotency semantics
    search/                   # query AST and stable projection types
    providers/                # source/storage/AI protocols only
  modules/
    assets/                   # API schemas, service, repository, router
    sources/                  # connections, sync commands/status
    metadata/                 # persistence and metadata operations
    processing/               # job/admin API, outbox application service
    search/                   # query service and API
  providers/
    google/                   # Google source adapter
    microsoft/                # SharePoint source adapter
    storage/                  # local/S3/Azure adapters
    ai/                       # Gemini/OpenAI adapters
  infrastructure/
    persistence/              # SQLAlchemy implementations/UoW
    search/elasticsearch/     # client, mappings, bulk indexer, aliases
    queue/                     # durable queue implementation

apps/worker/
  main.py                     # worker process entrypoint
  handlers/                   # download/hash/store/analyze/project/index
  schedules/                  # periodic sync/reconciliation definitions

database/
  alembic.ini
  migrations/
    env.py
    versions/

packages/types/               # generated/shared API DTOs only when justified
```

Specific refactoring guidance:

- Keep FastAPI routers in `modules/*/router.py` and limit them to validation, principal/tenant resolution, application-service invocation, and response mapping.
- Put invariants and provider-neutral orchestration in domain/application services; do not import Google, Microsoft, storage, or AI clients there.
- Implement contracts with Python `Protocol` or abstract interfaces in `domain/providers`; concrete HTTP/SDK code stays under `providers`.
- Split the current `metadata/service.py`: legacy Directus indexing remains clearly named under a legacy adapter until retired; app-owned metadata moves to the new authoritative service/repository.
- Replace the frontend's single `useDriveExplorer.ts` incrementally with source-session, browse-cache, indexing-status, search, and asset-metadata hooks. Do this only alongside relevant API stages, not as an unrelated rewrite.

## Proposed authoritative data model

Exact columns belong to Step 03 design, but migrations should converge on these boundaries:

| Aggregate/table | Required identity or constraint |
| --- | --- |
| `tenants` | Stable tenant UUID/ULID |
| `users`, `tenant_memberships` | App principal plus unique tenant/user membership and role |
| `external_sources` | Tenant-scoped connection; unique provider-specific connection identity |
| `external_assets` | Unique `(tenant_id, external_source_id, external_asset_id)`; raw provider IDs stored separately from display path |
| `asset_contents` | Unique `(tenant_id, sha256)`; size/media/checksum state |
| `assets` | Tenant asset record linking current external record and optional content identity |
| `asset_metadata_documents` | Asset/profile/version plus dynamic PostgreSQL JSONB and validation state |
| `search_projections` | Asset plus projection/schema version and stable JSONB/typed projection payload |
| `processing_jobs` | Job type, idempotency key, state, attempts, lease, timestamps, error |
| `outbox_events` | Unique event identity, transactionally written, publication state |
| `tags`, `asset_tag_assignments`, ratings | Tenant-scoped and linked to canonical asset IDs, not provider item strings |

Do not use name, folder, URL, or modified timestamp as content identity. Keep source identity and content identity separate. AI metadata keys remain in JSONB; only profile/version, operational state, and necessary relationships become fixed columns.

## Feature flags introduced in Step 01

The authoritative names are documented in `steps/01-foundation.md` and implemented by the central API settings service. They cover ingestion, deduplication, sync, jobs, downloading, storage, AI metadata, search, external ingestion, and sidecar export.

Every flag defaults to off and currently changes no runtime feature path. Future steps must add ownership, metrics, removal criteria, and independent rollback before enabling the corresponding flag. Secrets and provider configuration are not feature flags.

## Staged pull request order

### PR 00 — Audit documents

- Add `CURRENT_STATE.md` and this plan only.
- No application files, migrations, dependencies, or behavior changes.
- Resolve or explicitly accept the missing `AGENT.md` before PR 01.

### PR 01 — Architecture documents, typed configuration, and flags

- Commit/restore the authoritative agent/ADR/roadmap documents.
- Introduce a typed settings object and the flags above with all defaults off.
- Document configuration precedence and secret handling.
- Add unit tests for settings parsing and safe defaults.

### PR 02 — Provider contracts

- Define provider-neutral source contracts and asset/source DTOs.
- Adapt Google Drive and SharePoint clients behind those contracts without changing routes or responses.
- Add contract tests with HTTP fixtures for pagination, throttling, opaque raw IDs, and range streaming.
- Remove service imports of concrete clients through dependency injection/factory resolution.

### PR 03 — PostgreSQL migrations, tenancy, source registry, and asset registry

- Choose PostgreSQL as required non-test authority and add Alembic.
- Add tenant, principal/membership, external source, external asset, canonical asset, and tenant-scoped tag/rating migrations.
- Add database constraints for both source identity and tenant isolation.
- Keep legacy reads; dual-write only under `CAM_ASSET_REGISTRY_V2`.
- Supply upgrade, data-backfill, verification, and downgrade notes.

### PR 04 — Content hashing and tenant-scoped deduplication

- Introduce streaming SHA-256 calculation and `asset_contents` uniqueness on `(tenant_id, sha256)`.
- Record checksum algorithm/version and immutable size.
- Add concurrency tests proving duplicate ingestion converges without duplicate content rows.
- Do not yet force managed storage or AI processing.

### PR 05 — Durable processing jobs and transactional outbox

- Implement persisted job/outbox schemas and `apps/worker` entrypoint.
- Define idempotency keys, leases, retry/backoff, terminal failure, cancellation, and dead-letter operations.
- Move no user-visible flow until worker health and replay tests pass.

### PR 06 — Incremental provider synchronization

- Implement Google Drive Changes and Microsoft Graph delta adapters in the existing provider locations.
- Store cursors per external source and enqueue idempotent create/update/delete/move reconciliation.
- Retain periodic full reconciliation for cursor expiry and drift.

### PR 07 — Secure external downloader

- Add provider-neutral download contract, allowlists, redirect policy, size/time limits, MIME sniffing, range/resume policy, and audit data.
- Ensure credentials never enter job payload logs or client-visible URLs.
- Test malicious redirects, oversized content, timeouts, and provider rate limits.

### PR 08 — Managed asset storage

- Add local development and first production storage adapters.
- Store originals/renditions using tenant/content-derived keys while preserving authorization boundaries.
- Keep provider streaming as fallback behind `CAM_MANAGED_STORAGE_V2`.

### PR 09 — Dynamic metadata persistence

- Add versioned PostgreSQL JSONB metadata documents with optional profile validation.
- Preserve raw AI output separately from validation/processing status when needed.
- Migrate tags/ratings to canonical assets without treating them as arbitrary AI JSON.

### PR 10 — Metadata traverser and PR 11 — normalizer

- Implement deterministic traversal of arbitrary JSON values into typed intermediate terms.
- Normalize strings, numbers, paths, phrases, and facet candidates independently of AI providers.
- Add multilingual, punctuation, arrays/objects, numeric, null, and depth/size limit tests.

### PR 12 — Versioned search projection builder

- Build only `search_text`, `search_terms`, `normalized_terms`, `phrases`, `numbers`, `facets`, and `path_values`.
- Persist projection input version and builder version so rebuilds require no AI call.
- Never promote arbitrary JSON keys to Elasticsearch mappings.

### PR 13 — Elasticsearch v2 index

- Add explicit mappings/templates, versioned physical index names, aliases, bulk indexing, retry/error capture, and deletion handling.
- Dual-write projections while reads remain legacy.
- Add mapping snapshot, bulk partial-failure, rebuild, and tenant-isolation tests.

### PR 14 — Search query parser and controlled read cutover

- Parse keyword, soft AND, comma strict AND, quoted phrase, explicit OR, and qualified field/facet forms into an AST.
- Compile the AST against the stable projection only.
- Compare legacy and v2 results, expose metrics/admin diagnostics, then enable `CAM_ELASTICSEARCH_READ_V2` by tenant.

### PRs 15–19 — AI and external APIs

- Single-asset analysis through an AI provider contract.
- Pilot evaluation before batch enablement.
- Idempotent batch processing through durable jobs.
- Authenticated tenant-scoped external ingestion API.
- Drive sidecar export as an output only, never an authority.

### PRs 20–22 — Rebuild, UI/admin, and rollout

- Projection rebuild/reindex tooling with checkpoints and alias swap.
- Admin operations for source status, job retry/cancel, failures, and index health.
- Controlled tenant rollout, SLOs, dashboards, runbooks, rollback drills, and legacy Directus retirement.

## Migration and rollback strategy

### General rules

- Use expand → backfill → verify → switch reads → contract. Never combine destructive contraction with the first read cutover.
- Every externally visible command/job receives a durable idempotency key and a database uniqueness constraint.
- Backfills are resumable, tenant-scoped, observable, and safe to replay.
- PostgreSQL transactions write domain state and outbox events together; workers never rely on best-effort dual writes.
- Elasticsearch documents are always rebuildable from PostgreSQL projections.

### Legacy metadata transition

1. Freeze and document the schemas of both current `asset_metadata` stores.
2. Add new canonical tables with different, unambiguous names.
3. Establish a mapping from legacy `(provider, account_id, item_id)` to `(tenant_id, external_source_id, external_asset_id)`.
4. Import tags/ratings with reconciliation counts; optionally import useful Directus browse fields as source observations, not AI truth.
5. Dual-write current mutations behind flags.
6. Compare record counts and sampled reads before switching.
7. Keep legacy data read-only for a defined rollback window; delete only in a later contraction PR.

Rollback for additive stages is flag-off plus worker pause. Database downgrades should remove only objects that are proven unused; after authoritative writes begin, prefer a forward repair migration over destructive downgrade. Elasticsearch rollback is alias/read-flag reversal. Provider and storage adapter rollback returns resolution to the previous adapter without changing domain records.

## Required test layers

- Domain unit tests for identity, tenant scope, state transitions, idempotency, metadata traversal/normalization, projection versions, and query parsing.
- Repository integration tests against PostgreSQL, including constraints, transaction/outbox atomicity, migrations up/down, and concurrent deduplication.
- Provider contract tests with recorded/synthetic HTTP responses; no live cloud account required in CI.
- Worker integration tests for retry, lease expiry, replay, cancellation, and poison jobs.
- Elasticsearch tests for exact mappings, tenant filters, query semantics, bulk partial failures, alias cutover, and rebuild parity.
- API authorization tests for cross-tenant denial and source ownership.
- Frontend component/e2e tests for source selection, progress/failure states, search semantics, and flag fallback.
- Performance tests for large folder trees, large files, batch projection, and reindex throughput.

## Operational requirements before cutover

- Structured logs with request, tenant, external source, asset, job, attempt, and provider correlation IDs.
- Metrics for source sync lag, queue depth/age, retry/failure rates, download bytes/duration, hash/dedup outcomes, AI latency/cost, projection lag, bulk-index errors, and search latency/result counts.
- Traces across API → database/outbox → worker → provider/storage/AI/Elasticsearch.
- Readiness checks for PostgreSQL and required runtime dependencies; liveness remains dependency-light.
- Runbooks for OAuth/token failures, provider throttling, cursor expiry, stuck leases, replay, storage mismatch, and Elasticsearch reindex/alias rollback.

## Blockers to resolve before Step 01

1. Restore or formally create `docs/architecture/AGENT.md` so repository-local instructions are authoritative.
2. Decide whether the current dirty SQLAlchemy tag/rating work is the accepted baseline.
3. Define the initial tenant model and mapping of existing provider account IDs.
4. Decide Directus's future role and whether its metadata will be imported or rebuilt.
5. Select PostgreSQL migration tooling/conventions (Alembic is recommended).
6. Select durable queue runtime, managed storage provider, and Elasticsearch/OpenSearch deployment/version.
7. Decide whether OAuth connections belong to individual users or tenants/service principals.
8. Resolve npm-versus-pnpm ownership before adding more workspace packages.

## Step 00 completion boundary

Step 00 is complete when these two audit documents are reviewed. No roadmap implementation checkbox beyond `00 Codebase audit` should be marked complete, and no application behavior should change as part of this step.
