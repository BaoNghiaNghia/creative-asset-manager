# Architecture Review

## Current completed step

Step 20 — Resumable projection rebuild and Elasticsearch reindex operations.

## Review summary

- Repository-local architecture rules and ADR-001 through ADR-006 now exist.
- All 15 rollout flags are centralized in the API settings service.
- Every flag defaults to false; completed capabilities consume only their dedicated flag.
- Strict boolean validation runs at FastAPI startup.
- Configuration defaults, valid values, invalid values, and central cached access are covered by tests.
- Step 01 introduced no route, migration, worker, provider, database, or UI behavior.

## Feature flags

All flags remain disabled by default. Implemented capabilities stay inactive until their dedicated flag is explicitly enabled.

## Validation results

- API/configuration tests: 5 passed, including FastAPI startup and health smoke coverage.
- Python compile check for `app` and `tests`: passed.
- Client production build: passed (TypeScript and Vite).
- Step 01 diff check passed with no migration or worker changes.

## Step 02 review

- Source, storage, and AI provider-neutral contracts are defined without external SDK imports.
- Google Drive and SharePoint adapters wrap the existing clients.
- `ExplorerService` receives a source-provider factory and has no concrete cloud-provider import.
- Existing route declarations and response models are unchanged.
- Incremental changes, storage, and AI remain disabled skeletons.
- Fourteen API/contract/adapter tests pass with RuntimeWarning treated as an error.
- No migration or worker files changed.

## Phase 3 review — Steps 03 and 04

- Added the five tenant-scoped asset registry tables and Alembic revision `0001_asset_registry`.
- Database constraints enforce source identity, tenant content identity, tenant-safe links, and cursor identity.
- Repository operations cover source/asset upsert, content lookup/create, idempotent linking, soft deletion, and cursors.
- SHA-256 is calculated incrementally from original byte streams without whole-file buffering.
- Provider checksum/version optimization uses separately stored hash-confirmed markers.
- Concurrent transactions recover from the unique content conflict and converge on one asset.
- Same bytes deduplicate across filenames/providers within a tenant; different tenants remain isolated.
- `CONTENT_DEDUP_ENABLED` remains false and no explorer/search/worker route invokes the new service.
- Migration upgrade/downgrade, repository, hashing, behavior, and concurrency tests pass.
- No route or worker was added.

## Phase 4 review — Step 05

- Added `processing_jobs` and `outbox_events` with Alembic revision `0002_processing_jobs_outbox`.
- Job creation is tenant-idempotent and retries reuse the same stable job row.
- PostgreSQL claims with `FOR UPDATE SKIP LOCKED`; SQLite/test uses atomic conditional `UPDATE ... RETURNING`.
- Claims use renewable leases; expired leases recover while exhausted leases become terminal failures.
- Failures use bounded exponential backoff and respect `max_attempts`.
- Outbox writes share the caller transaction with domain mutations and publishing is idempotent.
- The worker loop is opt-in, sleeps on an empty queue, and no process is started by the application.
- `PROCESSING_JOBS_ENABLED` remains false; no API, ingestion, search, or UI behavior changed.
- Migration, concurrency, lease, retry, idempotency, transaction rollback, and idle polling tests pass.
- Full API suite: 42 passed with `RuntimeWarning` treated as an error; Python compile and client production build passed.

## Phase 5 review — Step 06

- Google Drive Changes and SharePoint drive Delta adapters now return provider-neutral change pages.
- Cursors are tenant/source scoped and committed only with the source assets and idempotent download jobs from the same page.
- Rename/folder move updates mutable metadata without scheduling content work; checksum/content-version overwrite schedules a new download version.
- Delete and unchanged restore are idempotent; reconciliation soft-deletes observations missing from a complete provider scan.
- `source_sync` is a supported durable job type, but no handler or scheduler is registered.
- `INCREMENTAL_SOURCE_SYNC_ENABLED` remains false; current explorer behavior is unchanged and no migration was added.

## Phase 6 review — Step 07

- Added an HTTPS-only, allowlisted, DNS-validating streamed image downloader.
- Every redirect repeats hostname and address validation and respects a bounded redirect count.
- Connect/read timeouts, byte, width, height and pixel limits are configurable.
- Requests are pinned to the validated public IP while retaining original Host/TLS SNI.
- SHA-256 is calculated while streaming; magic bytes and a full Pillow decode validate image content independently of Content-Type.
- Signed URL credentials/query/fragment are redacted and temporary files are removed on all exits.
- `EXTERNAL_ASSET_DOWNLOADER_ENABLED` remains false; no ingestion route or worker registration was added.
- Full API suite: 63 passed with `RuntimeWarning` treated as an error; Python compile passed.

## Phase 6 review — Step 08

- Added GoogleDriveAssetStorage with credentials and root folder independent from Source Drive.
- Deterministic content-hash filenames and Drive appProperties bind remote files to internal assets.
- Remote lookup plus the database uniqueness constraint makes retries idempotent.
- asset_storage_objects persists status, retries, errors, remote file/folder IDs and web URL.
- Metadata sidecars remain unimplemented and are owned by Step 19.
- MANAGED_ASSET_STORAGE_ENABLED remains false; no route or worker was registered.
- Added Alembic revision 0003_managed_asset_storage.

## Phase 7 review — Step 09

- Added tenant-versioned metadata_profiles with dynamic optional JSON Schema and search configuration.
- Added asset_ai_analyses as immutable history linked tenant-safely to canonical assets and profiles.
- metadata_json, optional raw_response_json, and versioned search_projection use PostgreSQL JSONB.
- Normal analysis is database-idempotent; forced analysis creates new history without overwriting completed results.
- No fixed asset-category schema, AI provider call, route, or worker was added.
- DYNAMIC_AI_METADATA_ENABLED remains false.
- Added Alembic revision 0004_dynamic_ai_metadata.

## Phase 7 review — Step 09B

- The metadata boundary requires an object root and valid finite JSON.
- Configurable byte, depth, node, array and string limits reject oversized or adversarial documents.
- Optional profile JSON Schema errors include stable codes and document paths.
- Validation deep-copies accepted data and never mutates or aliases caller input.
- jsonschema 4.26.0 is pinned.
- Targeted Step 09/09B suite: 17 passed with RuntimeWarning treated as an error.
- Full API suite: 89 passed; Python compile, Alembic single-head check, and client production build passed.
- The Step 05 outbox test fixture now uses a fixed available timestamp and no longer depends on wall-clock time.

## Phase 8 review — Step 10

- Added deterministic nested object/array traversal with index-free logical paths.
- Strings and finite numbers are extracted; booleans are opt-in and nulls are ignored.
- Work is bounded by depth, node, array item, and extracted-value limits.
- Global and profile path exclusions remove URLs, credentials, tokens, base64, vectors, embeddings, coordinates, bounding boxes, provider IDs, and debug payloads.

## Phase 8 review — Step 11

- Added NFKC Unicode normalization, case folding, punctuation normalization, and whitespace collapse.
- Meaningful short words are preserved; integer-like numbers, years, and useful phrases are retained.
- Terms are deduplicated deterministically and bounded without modifying source metadata.

## Phase 8 review — Step 12

- Added the stable seven-field SearchProjectionBuilder and flat/deep equivalence fixtures.
- Profile config controls text paths, facets, include-all behavior, booleans, exclusions, and query-only boosts.
- SearchProjectionService rebuilds only from stored metadata and persists projection/version separately.
- SEARCH_PROJECTION_ENABLED remains false; no route, worker, Elasticsearch mapping, or AI call was added.
- Targeted Steps 10–12 suite: 30 passed.
- Full API suite: 119 passed; Python compile, Alembic single-head, client build, and shared TypeScript contract check passed.

## Risks and follow-up

- Existing configuration outside the new flags still uses direct `os.getenv`; it should migrate incrementally when touched, not through a broad rewrite.
- The working tree contained pre-existing uncommitted metadata/tag changes during Steps 00 and 01; reviewers must separate those from foundation changes.
- PostgreSQL-specific `FOR UPDATE SKIP LOCKED` is implemented but still needs validation against the production PostgreSQL version and connection pool.
- SharePoint incremental sources require a concrete document-library `drive_id` in external source metadata.
- Reconciliation currently keeps the seen external IDs in memory; very large libraries should move this marker into PostgreSQL.
- Managed Drive storage requires a separately provisioned write-capable credential and root folder.
- PostgreSQL JSONB, partial-index and concurrent upload behavior still need live PostgreSQL/Google Drive staging validation.
- Concurrent storage execution relies on Step 05 job claiming; callers must not bypass the durable job ownership boundary.
- Sensitive-value heuristics intentionally favor exclusion and may omit unusually long base64-like human labels.
- Profile search configuration changes require a projection rebuild, but never another AI analysis.

## Rollback

Keep `PROCESSING_JOBS_ENABLED=false`, stop any manually started consumer, export pending/failed records if needed, then downgrade `0002_processing_jobs_outbox` to `0001_asset_registry`. This drops only the two Step 05 tables.

Steps 06 and 07 add no migration. Roll them back by keeping both flags false, removing direct callers, and removing the sync/downloader modules; remove Pillow if no other feature uses it.

For Step 08, keep MANAGED_ASSET_STORAGE_ENABLED false, stop storage consumers,
export remote IDs, then downgrade 0003 to 0002; remote Drive files remain. For
Step 09, disable metadata flags, export history, then downgrade 0004 to 0003.

Steps 10–12 add no migration. Roll back by keeping SEARCH_PROJECTION_ENABLED
false, removing callers and the traverser/normalizer/projection modules. Existing
metadata_json and stored analysis history remain authoritative and unchanged.

## Phase 9 review — Steps 13 and 14

- Added an HTTP-based Elasticsearch v2 infrastructure adapter with versioned
  physical indices and independent read/write aliases.
- The strict stable mapping uses flattened facets, nested path_values, and a
  lowercase/Unicode/punctuation analyzer; metadata_json is absent.
- Bounded bulk indexing uses asset_id as the document ID and idempotent
  doc-as-upsert semantics.
- Read/write aliases switch in one atomic API request and return their prior
  targets for explicit rollback.
- Added a provider-neutral parser for single terms, soft AND, comma strict AND,
  phrases, explicit OR, qualified terms, and qualified phrases.
- Query normalization reuses MetadataNormalizer. Malformed input falls back to
  safe plain-text fields; every query is tenant-filtered.
- ELASTICSEARCH_V2_ENABLED and SEARCH_QUERY_PARSER_V2_ENABLED remain false. No
  route, worker, API response, or current v1 search behavior changed.
- Added parser, builder, feature-gate, mapping, mocked HTTP integration, alias,
  bulk-upsert, and required result fixture coverage.
- Full API suite: 145 passed; Python compile, Alembic single-head, and client
  production build passed.

## Phase 9 risks and rollback

- The adapter still needs staging validation against the production
  Elasticsearch version, permissions, analyzer behavior, shard sizing, and
  realistic bulk payload sizes.
- No migration was added. Roll back by leaving both v2 flags false, switching
  both aliases to the previous physical index if a staging switch occurred,
  then removing the v2 adapter/parser. PostgreSQL projections and v1 search
  remain unchanged.

## Phase 11 review — Step 18

- Added authenticated `POST`, status, and paginated item endpoints under
  `/api/v1/asset-ingestions`, gated by `EXTERNAL_INGESTION_API_ENABLED`.
- High-entropy bearer API keys are stored only as SHA-256 fingerprints and are
  bound by database constraints to one tenant and one `external_api` source.
- Request bodies are bounded to 1 MiB and 1,000 unique items; external IDs,
  HTTPS URLs, filenames, checksums, and timezone-aware modified timestamps are
  validated before persistence.
- Canonical JSON hashing and the unique tenant/source/idempotency-key constraint
  make retries and concurrent duplicate requests converge on one ingestion.
- Same key plus a different canonical request returns HTTP 409.
- Accepted requests persist ingestion/item state and enqueue only durable
  `source_asset_download` jobs in the same transaction. No request-time
  download, storage, AI analysis, or Elasticsearch indexing occurs.
- Database fixed-window counters enforce per-credential rate limits atomically.
- Added Alembic revision `0005_external_ingestions`; upgrade and step-scoped
  downgrade are covered by migration tests.
- `EXTERNAL_INGESTION_API_ENABLED` remains false by default.
- Targeted Step 18 suite: 13 passed. Full API suite: 158 passed with
  `RuntimeWarning` treated as an error; Python compile, Alembic single-head,
  migration rollback, and client production build passed.

## Phase 11 risks and rollback

- Credential provisioning/rotation is currently an admin/repository operation;
  a dedicated admin UI or secret-manager integration is still required.
- Signed download URLs must remain in PostgreSQL/job payloads until workers use
  them; logs and validation errors do not echo those values, but production
  database encryption, retention, and access policies remain operational duties.
- Fixed-window rate-limit rows require periodic retention cleanup at scale.
- PostgreSQL `ON CONFLICT ... RETURNING` rate limiting and high-concurrency
  ingestion still need staging validation against the production database and
  connection pool.
- The Step 18 API enqueues existing job types only; with processing workers
  disabled, accepted items intentionally remain queued.
- Roll back by keeping `EXTERNAL_INGESTION_API_ENABLED=false`, stopping external
  callers, exporting audit/status records if needed, and downgrading
  `0005_external_ingestions` to `0004_dynamic_ai_metadata`. Existing processing
  jobs may retain inert JSON references to removed ingestion item IDs.
## Phase 12 review — Step 19

- Added deterministic `cam-metadata-sidecar-v1` documents built only from
  PostgreSQL assets, source links, completed analysis identity, sanitized
  `metadata_json`, and search projection version.
- Raw AI responses, source metadata, credentials, auth values, secret-like keys,
  and signed URLs are excluded or redacted.
- Google Drive lookup uses tenant, asset, and analysis appProperties. A retry
  updates the same remote JSON file instead of creating another sidecar.
- Durable `metadata_sidecar_exports` state tracks document hash, remote identity,
  attempts, retry backoff, and terminal failure independently of analysis state.
- Provider I/O starts only after completed analysis and export-attempt state are
  committed; a sidecar failure cannot roll back completed metadata.
- `DRIVE_METADATA_SIDECAR_ENABLED` remains false by default and no worker starts
  automatically.
- Added Alembic revision `0006_metadata_sidecars` with step-scoped downgrade.
- Targeted Step 19 suite: 9 passed; full API suite: 163 passed with
  `RuntimeWarning` treated as an error. Python compile and Alembic single-head
  checks passed.

### Step 19 risks and rollback

- Google Drive appProperties lookup provides retry idempotency, while concurrent
  remote creation still relies on the database/job lease preventing two owners.
- Existing duplicate remote sidecars are treated as a non-retryable integrity
  failure and require operator cleanup.
- Disable `DRIVE_METADATA_SIDECAR_ENABLED`, stop sidecar consumers, export audit
  records if needed, then downgrade `0006_metadata_sidecars` to
  `0005_external_ingestions`. Remote JSON exports are intentionally retained.

## Phase 12 review — Step 20

- Added tenant-scoped `search_operation_runs` and `search_operation_items` for
  durable filters, keyset cursor, bounded page size, current progress metrics,
  cancellation requests, per-analysis failures, and only-failed resume.
- Added `search:rebuild-projections`, `search:reindex-assets`,
  `search:rebuild-and-reindex`, and cooperative `search:cancel` CLI operations.
- Filters support metadata profile, current projection version, up to 1,000
  explicit asset IDs, only missing projections, only failed items, and dry-run.
- Projection rebuild uses stored metadata_json/profile configuration only and
  contains no AI provider call.
- Elasticsearch reindex creates a versioned physical index, writes bounded
  batches directly to it, and atomically switches read/write aliases only after
  every selected item succeeds. Failed/cancelled runs retain the physical index
  without switching aliases.
- Completed runs are idempotent no-ops; failed runs reuse their target index and
  item identities when resumed.
- `SEARCH_PROJECTION_ENABLED` and `ELASTICSEARCH_V2_ENABLED` remain false by
  default. Dry-run performs no projection, index, or alias mutation.
- Added Alembic revision `0007_search_operations` with step-scoped downgrade and
  an operator runbook at `docs/operations/SEARCH_REBUILD.md`.
- New Step 20 tests: 9; full API suite: 172 passed with `RuntimeWarning`
  treated as an error. Python compile, database startup smoke, Alembic
  single-head/migration rollback, and client production build passed.

### Step 20 risks and rollback

- Reindex selection can contain multiple completed analysis histories for one
  asset when operators omit a metadata-profile filter; production runbooks
  should select the intended profile/version explicitly.
- PostgreSQL keyset pagination, cancellation latency, large-tenant throughput,
  Elasticsearch permissions, shard sizing, and physical-index cleanup require
  staging validation.
- Stop operational commands, request cancellation, preserve run audit data if
  needed, then downgrade `0007_search_operations` to
  `0006_metadata_sidecars`. Downgrade does not revert stored projections, delete
  physical Elasticsearch indices, or change aliases.

## Phase 13 review — Step 21A

- Added tenant/provider-scoped processing status projections for `discovered`,
  `stored`, `analyzing`, `metadata_ready`, `indexed`, `duplicate`, and
  `failed`.
- Statuses are derived read-only from PostgreSQL registry/source links, managed
  storage, latest analysis, latest relevant processing jobs, and completed
  reindex operations. No parallel status source or migration was added.
- Existing metadata query, rating, and tag responses now preserve the current
  processing status without adding a route or changing their request shape.
- Added a compact accessible `AssetStatusBadge` to the existing asset grid;
  unrelated explorer views and interactions were not redesigned.
- Added Vitest as the client component-test convention and a reusable
  `npm test` command.
- New API/service tests: 3 passed. New component cases: 7 passed. Full API
  suite: 175 passed with `RuntimeWarning` treated as an error. Client
  production build passed.
- No feature flags were enabled or added.

### Step 21A risks and rollback

- Status derivation currently resolves UI items by tenant, provider source type,
  and external asset ID. Tenants with multiple sources of the same provider and
  colliding external IDs need source-key context in a future API revision.
- One primary badge intentionally uses lifecycle precedence; an indexed
  duplicate displays `indexed`, while full provenance remains a Step 21B
  asset-details concern.
- The batched query is bounded by the existing 500-item metadata request but
  requires production query-plan and latency validation on large tenants.
- Roll back by reverting the Step 21A commit. No database downgrade, worker
  action, remote cleanup, or feature-flag change is required.

## Phase 13 review — Step 22

- Added a controlled rollout runbook covering the deployment checklist,
  migration order, flag enablement order, isolated pilot procedure, general
  rollback, Elasticsearch alias rollback, worker drain, AI budget emergency
  stop, provider outage response, and backfill throttling.
- The runbook preserves PostgreSQL authority, Elasticsearch rebuildability, and
  non-authoritative sidecars. It contains no automatic flag mutation.
- Documented the current hard control gap: feature flags and worker claims are
  not tenant-gated. Shared multi-tenant pilot rollout is blocked; use an
  isolated deployment containing only the pilot tenant until tenant allowlists
  and tenant-filtered job claiming are implemented.
- No application behavior, dependency, API route, worker, or database migration
  was added in Step 22.
- Configuration regression suite: 4 passed and confirms every feature flag
  still defaults to false. Step 21A's full API suite (175), component suite (7),
  and client build were already green before the documentation-only step.

### Step 22 risks and rollback

- Tenant gates, tenant-filtered worker claims, a durable drain endpoint,
  automated AI budget circuit breaker, search comparison dashboards, and
  provider-specific pause controls remain required before shared production
  rollout.
- Operational thresholds and owners must be filled with environment-specific
  values before use; the runbook deliberately does not invent production SLOs
  or credentials.
- Roll back Step 22 by reverting its documentation commit. No database,
  provider, search alias, worker, or feature-flag rollback is required.
