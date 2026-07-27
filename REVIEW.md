## Gemini multi-model failover

- Files changed: Gemini provider adapter, provider factory/configuration,
  analysis failure persistence, environment examples, and focused unit tests.
- Migrations added: none.
- Behavior introduced: image metadata analysis selects the configured Gemini
  model pool in priority order, enforces per-model local RPM/RPD and one
  in-flight request, retries a non-daily 429 once using Retry-After, then
  fails over on repeated 429/503. Daily quota exhaustion holds a model until
  the next America/Los_Angeles midnight. Permanent provider errors do not
  fail over. Success and terminal pool failure metadata records requested,
  actual and attempted models plus the failover reason.
- Tests: focused provider, service, handler, registry and config suite passed
  (54 tests); changed Python modules compile successfully.
- Feature flags: none changed or enabled.
- Known risk: rate/concurrency state is process-local; existing worker
  concurrency controls remain the cross-process guard.
- Rollback: revert this commit. No database rollback is required.

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

## Phase 14 review — Step 23

- Added a real `apps/worker/main.py` composition root that validates central
  settings, probes the database, initializes provider boundaries, builds the
  handler registry, exposes health HTTP, installs SIGTERM/SIGINT handlers, and
  exits non-zero on startup failure.
- Added a provider-neutral typed handler contract with immutable job/tenant
  identity, payload, shared dependencies, shutdown/cancellation signals,
  contextual logger, and explicit completed/retryable/non-retryable/cancelled
  outcomes.
- Explicitly registered `source_sync`, `source_asset_download`,
  `asset_store`, `asset_analyze`, `search_projection_build`,
  `asset_index`, `metadata_sidecar_export`, and `outbox_dispatch`.
  Pipeline stages not wired in Step 23 terminate with structured
  `unsupported_handler` rather than false success.
- Added independent-session lease heartbeat, ownership-loss cancellation, and a
  finalization guard that never completes work after lease loss or uncertain
  heartbeat.
- Added graceful draining: readiness becomes false and claims stop immediately;
  heartbeat continues through the grace period; cooperative cancellation
  releases work, while unresponsive work remains recoverable by lease expiry.
- Added `/live`, `/ready`, and `/health` with safe state only, plus JSON
  worker logs carrying worker/job/tenant/entity/attempt/lease/duration/outcome
  context without job payloads or startup credentials.
- Extended processing transitions with direct non-retryable failure and explicit
  release; no schema or migration was required.
- `PROCESSING_JOBS_ENABLED` remains false by default. No API route, API response,
  Gemini integration, or end-to-end ingestion pipeline changed.
- Step 23/config/processing targeted suite: 28 passed. Full API suite: 189 passed
  with `RuntimeWarning` treated as an error. Python compile, Alembic single-head,
  and diff whitespace checks passed.

### Step 23 risks and rollback

- The runtime is deliberately single-concurrency per process; scale-out uses
  additional worker processes and the existing atomic database claim.
- Current production composition has no complete pipeline handlers, so enabling
  the worker before later handler wiring will terminally fail queued known jobs
  as unsupported. Keep the flag false until the selected handlers are supplied.
- Heartbeat database uncertainty is treated conservatively as lost ownership.
  A handler that ignores cancellation may continue in its daemon thread until
  process exit, but the runtime cannot finalize it and another worker cannot
  recover it until lease expiry.
- PostgreSQL lease renewal, shutdown timing under orchestration, health-port
  binding, and database pool sizing still require staging validation.
- Roll back by setting `PROCESSING_JOBS_ENABLED=false`, sending SIGTERM, waiting
  through the drain/lease window, and reverting Step 23. No database downgrade,
  provider cleanup, Elasticsearch change, or API rollback is required.
- Next recommended step: wire one idempotent handler at a time behind its own
  existing feature flag, beginning with source synchronization or download,
  with live PostgreSQL lease tests before shared rollout.

## Phase 14 review — Step 24

- Added the provider-neutral pipeline: asset_analyze claim, managed image
  preparation, Gemini structured JSON, safety/profile validation, search
  projection, atomic analysis persistence and asset_index enqueue.
- Added the Gemini REST adapter at apps/api/app/providers/ai/gemini.py using the
  official generateContent API with inline image data and JSON response mode.
  It captures response ID, model version, finish reason and usage metadata while
  keeping credentials and image bytes out of logs.
- Added bounded image preparation at
  apps/api/app/modules/ai_metadata/analysis_image.py: streamed byte limit,
  Pillow decode, pixel/dimension checks, EXIF orientation, metadata-free JPEG,
  analysis-image SHA-256 and reliable temporary-file cleanup. Original
  content_hash remains unchanged.
- Migration 0008_ai_single_analysis adds stage, claimant/lease, projection
  checksum, provider request ID, usage/provider metadata, structured validation
  errors and retryability to asset_ai_analyses.
- Added authenticated tenant-scoped POST /api/v1/admin/asset-analyses. It
  returns 202, never calls Gemini synchronously, reuses normal analysis/job
  identities and creates history for explicit forced analyses.
- Registered the real asset_analyze handler in the Step 23 worker. Database
  uniqueness and an atomic analysis claim prevent duplicate normal Gemini calls.
- Central configuration now includes Gemini model/key/timeout, bounded image
  limits, validation attempts, raw-response retention, analysis lease and
  separate managed-storage credentials. Gemini credentials are required only
  when DYNAMIC_AI_METADATA_ENABLED and AI_SINGLE_ANALYSIS_ENABLED are both
  true; both flags remain false by default.
- Raw responses default to not persisted. Captured cost/audit data includes
  provider response ID, resolved model, usageMetadata token counters, finish
  reason and provider model version.
- Files changed: provider contracts and config; AI analysis
  model/repository/service/handler/router/schema/image preparer; Gemini and
  unconfigured AI adapters; Google Drive and unconfigured storage adapters;
  worker bootstrap; API composition; migration 0008; provider, image, service,
  API and config tests.
- Step 24 targeted suite: 19 passed. Full API suite: 203 passed with
  RuntimeWarning treated as an error. Python compile, Alembic head
  upgrade/downgrade, and diff whitespace checks passed.

### Step 24 risks and rollback

- The admin route currently treats an authenticated Google or Microsoft cloud
  account as its tenant owner because the project has no durable RBAC/admin role
  model. Explicit roles are required before shared production administration.
- Production analysis currently reads the separately configured Google managed
  storage object. SharePoint-managed storage and video-frame preparation are
  outside Step 24.
- Local safety and JSON Schema validation remain authoritative. Validation
  retries can consume provider tokens; pilot budgets and batch controls remain
  deferred.
- PostgreSQL claim/lease behavior and real managed-storage/Gemini timeouts
  require staging validation.
- Roll back by disabling AI_SINGLE_ANALYSIS_ENABLED and
  DYNAMIC_AI_METADATA_ENABLED, draining workers, reverting Step 24, then
  downgrading 0008_ai_single_analysis to 0007_search_operations. Existing
  completed metadata/projections and remote managed assets are retained.
- Next recommended step: run a cost-limited staging pilot before enabling batch
  or automatic analysis.

## Phase 15 review — Step 25

- Added a durable, tenant-scoped asset pipeline state machine with explicit
  stage failures, recovery transitions and stable correlation IDs.
- Added migration `0009_durable_asset_pipeline`, repository transition
  validation, and atomic state-plus-next-job chaining.
- Registered download, storage, analysis, projection, v2 index and independent
  sidecar handlers behind the existing feature flags.
- Integrated Step 24 completion with the same pipeline. Projection and indexing
  never call AI; persisted projection identity avoids unchanged index writes.
- Correlation IDs now appear in worker logs and chained payloads contain
  database references rather than signed URLs or credentials.
- All new and existing pipeline flags remain false by default.
- Targeted pipeline suite: 7 passed. Full API suite: 210 passed. Python compilation passed.

### Step 25 risks and rollback

- Google/SharePoint OAuth currently lives in browser sessions. A durable,
  encrypted worker credential resolver is required before source downloads can
  be enabled in a shared deployment. Pipeline provider stages fail closed when
  that production composition is absent.
- Real PostgreSQL concurrency and provider/Elasticsearch fault injection still
  require staging validation before rollout.
- Roll back by disabling unified ingestion and processing, draining workers,
  reverting Step 25 and downgrading `0009` to `0008`. Remote storage and all
  authoritative asset/analysis/search data are retained.
- Next recommended step: add encrypted refresh-token credential storage and a
  Google/SharePoint worker resolver before any tenant rollout.


## Phase 16 review — Step 26

- Files changed: processing policy models/repository/service/claim/auth/router/
  schemas; processing job model/repository/service/runtime/bootstrap; pipeline
  provider classification; central config and API composition; migration 0010;
  policy/auth/migration tests; data model and rollout documentation.
- Migration added: `0010_tenant_processing_policies` with a tested downgrade to
  `0009_durable_asset_pipeline`.
- Behavior: explicit tenant/stage rollout, generic provider pause, pre-lease SQL
  eligibility, database-backed tenant/category/provider concurrency, graceful
  pause/resume, tenant job counts and audited admin operations.
- Feature flags: no new pipeline feature is enabled. Existing global flags remain
  false and are strict emergency upper bounds. Cache TTL and admin allowlist are
  operational configuration, not enablement flags.
- Tests: 21 focused config/policy/auth/migration tests passed; the full API
  regression suite passed with 225 tests and RuntimeWarning treated as error.

### Step 26 risks and rollback

- Active counters depend on all job finalization paths using the processing
  repository. Manual database edits can create drift; operators should pause and
  reconcile before editing jobs directly.
- Worker global job-type bounds are composed at startup, so an emergency global
  disable requires worker restart; the admin effective-policy response reflects
  the disable immediately even while its configured policy is cached.
- Existing OAuth sessions are not a durable enterprise identity/RBAC store. The
  explicit administrator allowlist is the production-safe platform path until a
  durable account/role model is introduced.
- PostgreSQL `SKIP LOCKED` and concurrent counter reservations require staging
  load validation even though SQLite concurrency and migration tests pass.
- Roll back by globally disabling processing, pausing pilots, draining workers,
  reverting Step 26, and downgrading 0010 to 0009. Queued jobs are preserved.
- Next recommended step: staging-test one tenant with PostgreSQL and provider
  outages before implementing later roadmap steps. AI budgets/pilot evaluation
  remain deliberately out of scope.

## Phase 17 review — Step 27

- Files changed: central config/database/API composition; AI metadata
  model/repository/service/handler; AI governance models, repository, budget
  service, pilot evaluator, metrics, schemas and admin router; pilot CLI;
  migration 0011; governance/service/config tests; data model, step and operator
  documentation.
- Migration added: `0011_ai_governance_pilot`, including tested downgrade to
  `0010_tenant_processing_policies`.
- Behavior: idempotent provider-operation usage accounting, effective-dated
  provider/model rates, UTC daily/monthly/pilot budgets, atomic reservations,
  reconciliation of actual/billable failures, budget-blocked analysis state,
  deterministic asynchronous pilots, cancellation/resume, JSON/CSV reports and
  authenticated budget/cost/metrics operations.
- Feature flags: `AI_EMERGENCY_STOP_ENABLED` is new and defaults to false.
  Existing AI and worker rollout flags remain false; Step 27 enables no AI
  execution by itself.
- Security: usage/audit/report records omit prompts, images, credentials, signed
  URLs and raw provider payloads. Admin operations reuse Step 26 tenant/platform
  authorization.
- Tests: focused governance, concurrency, tenant isolation, pilot and analysis
  breaker tests pass without Gemini calls. Full API regression suite: 234 passed with RuntimeWarning treated as an error.
  Migration upgrade/downgrade, Python compile, Alembic single-head and diff checks passed.

### Step 27 risks and rollback

- Cost rates must be entered and reviewed by operators; a missing rate estimates
  zero, so production rollout must treat cost-rate configuration as a
  prerequisite.
- UTC is the only supported budget boundary in this step. Tenant-local billing
  periods require a later migration and DST-aware tests.
- Atomic account updates prevent trivial multi-worker overspend, but real
  PostgreSQL load and provider-reported cost variance still require staging
  validation. Conservative reservations should include adequate output/media
  headroom.
- The emergency flag follows existing settings lifecycle; restart/drain workers
  after changing it. Tenant policies cannot override it.
- Roll back by enabling the emergency stop, pausing tenant AI, draining workers,
  exporting reports, reverting Step 27 and downgrading 0011 to 0010. Assets,
  completed metadata and projections remain intact.
- Next recommended step: load-test one tenant on PostgreSQL with real rate
  configuration and simulated Gemini billing/timeouts before expanding pilot
  size. Steps 28–33 and AI batch processing remain out of scope.


## Phase 18 review  Step 28

- Files changed: provider contracts and TypeScript transport types; Gemini and
  unconfigured AI adapters; durable AI batch model/repository/service/handlers;
  shared single/batch result importer; governance reconciliation; processing
  job types, policy classification and bootstrap; central configuration;
  migration 0012; provider, service, configuration and migration tests; provider,
  data model, step and operator documentation.
- Migration added: `0012_ai_batch_processing`, including upgrade and tested
  downgrade to `0011_ai_governance_pilot`.
- Behavior: compatible-only tenant batch grouping, bounded disk-backed request
  preparation, budget reservation before provider submission, stable ambiguous
  submission recovery, provider-guided polling, streamed resumable out-of-order
  import, shared metadata validation/projection/index handoff, batch/item usage
  accounting, cancellation and selective retry.
- Feature flags: `AI_BATCH_ANALYSIS_ENABLED` remains false and gates all new
  worker types. `AI_BATCH_FALLBACK_TO_SINGLE_ENABLED` is new, defaults false,
  and independently gates explicit single-item fallback. Existing global,
  tenant, pause, provider and concurrency gates remain upper bounds.
- Security: temporary input is mode 0600 and always removed; queued payloads
  contain database IDs only; usage/errors omit credentials, signed URLs and
  raw provider requests.
- Tests: 246 full API tests passed with RuntimeWarning treated as an error after
  final safety refinements. Batch service covers grouping, budgets, duplicate
  submit, ambiguity, polling, out-of-order/duplicate/unknown/invalid/missing
  results, resume, cancellation and usage idempotency without real Gemini calls.
  Migration upgrade/downgrade and Gemini adapter contract tests passed.

### Step 28 risks and rollback

- Gemini inline Batch API requests/results are bounded to 20 MB, but still have
  a bounded in-memory representation inside the HTTP adapter. Staging must
  validate worker memory and provider limits before increasing configured caps.
- Provider batch creation has no native idempotency key. Stable display-name
  lookup reduces duplicate risk, but an unresolved network partition remains an
  ambiguous operator-visible state and must not be bypassed with a new key.
- Cost rates, tenant budgets and provider billing semantics must be configured
  and reviewed. Terminal billable failures use conservative reservation
  reconciliation until provider totals are available.
- Real PostgreSQL multi-worker claims, Gemini throttling/expiry, long-running
  cancellation and large result import require staging fault injection.
- Roll back by disabling batch analysis, pausing tenant AI, draining workers,
  reverting Step 28 and downgrading 0012 to 0011. Completed analyses, usage,
  budgets, metadata and projections are retained.
- Next recommended step: run a small budget-capped Gemini batch for one tenant,
  validate recovery and billing, then implement Step 29 separately.


## Phase 19 review  Step 29

- Files changed: tenant-scoped asset details schemas/router, Search v2 schemas/router,
  Elasticsearch configuration, explorer integration, bounded JSON viewer, details
  panel, search syntax/facet/debug controls, tests and architecture documentation.
- Migrations added: none. PostgreSQL remains authoritative and existing records are
  projected read-only.
- Behavior: operators can inspect identity/source/storage/analysis/projection/job and
  pipeline history, then enqueue authorized reanalysis, projection, indexing or retry
  work. Search v2 is selected only through effective global and tenant rollout gates.
- Tests: backend tenant isolation, authentication, URL redaction and smoke/config;
  frontend bounded metadata, search controls, status badges, TypeScript and production
  build.
- Feature flags: no new flag. Existing ELASTICSEARCH_V2_ENABLED,
  SEARCH_QUERY_PARSER_V2_ENABLED and tenant search_v2_enabled remain upper bounds.
- Known risks: real Elasticsearch facet aggregation and high-cardinality tenant data
  require staging validation; v2 results whose source registry link was deleted are
  intentionally omitted; queued-job cancellation is terminal and audit event support
  remains a later operational enhancement.
- Rollback: disable global or tenant Search v2 to restore v1, then revert Step 29. No
  migration downgrade is required.
- Next recommended step: staging UX/accessibility validation with one enabled tenant
  and production-like metadata before implementing Step 30.


## Phase 20 review - Step 30

- Files changed: central auth persistence models/repository/encryption/service;
  Google and Microsoft OAuth/session/refresh integration; secure cookie policy;
  auth operator CLI; migration 0013; configuration, tests and security/runbook docs.
- Migration added: 0013_persistent_oauth_sessions, with tested upgrade and
  step-scoped downgrade to 0012.
- Behavior: PostgreSQL-shared sessions and one-time OAuth state survive process
  restart and work across replicas; AES-256-GCM encrypts tokens; DB leases
  serialize refresh; invalid grants require reconnect; key rotation and expired
  session/state cleanup are paginated operator operations.
- Tests: encryption/nonces/tamper/wrong-key, key rotation/dry-run/resume, shared
  session/restart/logout/expiry, state binding/expiry/replay, refresh rotation/
  lock/permanent revocation, cookie validation, secret-free persistence, tenant
  isolation and migration rollback. Full regression results are recorded in the
  completion report: 260 backend unit/integration/migration tests passed; 9
  frontend component tests passed; TypeScript and Vite production build passed.
- Feature flags: PERSISTENT_AUTH_ENABLED is new and defaults false. When false,
  persistent OAuth login fails closed; static developer provider tokens remain
  an explicit local fallback. Production enablement requires versioned keys and
  Secure cookies.
- Known risks: existing process-memory sessions cannot be migrated after a
  restart; users must reconnect. SQLite remains local/test-only for refresh
  contention; production rollout must validate PostgreSQL locking under load.
  Python strings cannot be reliably zeroized, so plaintext lifetime is minimized
  but not cryptographically erased from process memory.
- Rollback: disable login, restore the prior key set if needed, export secret-free
  audits, revert Step 30 and downgrade to 0012. Every user must reconnect.
- Next recommended step: enable for two staging replicas with one Google and one
  Microsoft account, test forced refresh/key rotation, then proceed to Step 31.


## Phase 21 review - Step 31

- Files changed: GitHub Actions CI, deterministic frontend lockfile, PostgreSQL
  and Elasticsearch integration tests, durable pipeline E2E fixtures, local
  Docker Compose runner, Elasticsearch alias compatibility, AI/pipeline index
  handoff, Make target, ignore rules, and CI operator documentation.
- Migrations added: none. CI upgrades an empty PostgreSQL 16.4 database through
  revision 0013, verifies a single head, downgrades to 0012, and upgrades to
  head again.
- Behavior introduced: pull requests now run frontend checks, API/worker/mock
  provider tests, real PostgreSQL tests, real Elasticsearch v2 tests, and a
  real-worker pipeline E2E job. All external source, storage, and Gemini
  providers are fakes. The durable pipeline suppresses the legacy standalone
  asset-index enqueue, preventing duplicate index jobs while preserving the
  standalone analysis default.
- Tests run: 271 backend tests passed (11 real-service modules skipped in the
  unit-only invocation); 11/11 PostgreSQL, Elasticsearch, migration, worker,
  and pipeline integration tests passed locally; 9 frontend component tests,
  TypeScript validation, and the Vite production build passed. Workflow YAML,
  Compose configuration, shell syntax, Python compilation, and git whitespace
  checks passed.
- Feature flags: none added or enabled. E2E settings and tenant policy are
  isolated to ephemeral test tenants; production defaults remain unchanged.
- Failure coverage: transient download retry, temporary Elasticsearch retry,
  duplicate bytes, invalid AI metadata, expired worker lease/restart, and
  tenant-disabled claiming. Persisted assets, links, storage, analyses,
  pipeline/job states, projections, index documents, and search results are
  asserted.
- Caching and security: npm and pip download caches only. PostgreSQL and
  Elasticsearch data, OAuth tokens, secrets, and provider responses are never
  cached or uploaded. Failure artifacts are bounded to test/migration logs and
  safe Elasticsearch diagnostics with seven-day retention.
- Known risks: GitHub-hosted runner capacity and Docker image download time can
  vary. The current locked frontend dependency graph reports three npm audit
  findings (one moderate, one high, one critical); dependency remediation is
  intentionally separate from Step 31. Long-duration and high-volume staging
  workloads remain outside this bounded CI suite.
- Rollback: revert the Step 31 commit to restore the previous two-job workflow.
  No database downgrade or feature-flag change is required. The Elasticsearch
  alias fix can be reverted independently only if the deployed Elasticsearch
  version supports the former request parameter.
- Next recommended step: observe several pull-request runs, tune timeouts only
  from measured data, and address the locked frontend audit findings before
  implementing Step 32 separately.


## Phase 22 review - Step 32

- Files changed: source reconciliation run/generation models, source sync
  repository/service, encrypted external-ingestion URL persistence, stable job
  payloads, shared URL redaction, retention cleanup model/service/handler/
  scheduler, worker registration/configuration, migration, tests and runbook.
- Migration added: `0014_reconciliation_retention`; upgrade adds sync run and
  cleanup state plus generation and encrypted URL columns. Step-scoped rollback
  to 0013 is tested and uses a non-sensitive URL tombstone where plaintext
  cannot be restored.
- Behavior introduced: full reconciliation is page-bounded and resumable;
  missing records are marked only after successful enumeration. Signed URLs are
  encrypted and resolved by tenant/item ID. Cleanup uses the existing durable
  queue, tenant claim gate, leases, cancellation, bounded pages, checkpoints,
  dry-run and count-only logging.
- Tests run/results: 287 backend unit/migration tests passed with 11
  real-service tests skipped in the unit invocation; all 11 PostgreSQL 16,
  Elasticsearch, migration and durable-pipeline integration tests passed.
  Python compilation and git whitespace validation passed.
- Feature flags: `RETENTION_CLEANUP_ENABLED` defaults false. Global processing
  and tenant pipeline policy remain upper bounds. External ingestion now
  requires a dedicated sensitive-URL encryption key ring.
- Known risks: migration 0014 tombstones legacy plaintext URLs, so queued
  legacy external ingestions need a fresh idempotency key. PostgreSQL
  production-scale reconciliation still needs staging soak validation.
  Temporary search/export operation items are deleted after retention;
  authoritative sidecar/storage references are preserved.
- Rollback: disable cleanup, pause/drain source jobs, revert Step 32 and
  downgrade to 0013. Run/checkpoint history is removed; assets and source
  identity remain.
- Next recommended step: deploy all flags false, migrate staging, run one
  multi-page Drive and SharePoint reconciliation with injected timeout/resume,
  then enable cleanup for one tenant. Do not begin Step 33 until retention
  counts and worker recovery are observed.
## Phase 23 review - Step 33

- Files changed: deterministic active-analysis model/service/admin endpoints,
  tenant shadow policy/comparator/reporting, Elasticsearch lifecycle verifier/
  cleanup controls, worker/rebuild selection, migration, tests and runbooks.
- Migration added: 0015_search_governance with tested step-scoped downgrade to
  0014. AI analysis history and physical Elasticsearch data are preserved.
- Behavior introduced: explicit tenant/profile/context analysis activation and
  rollback; jobs carry and validate the intended analysis; active-only rebuilds
  behind a flag; non-blocking sampled shadow calls with strict timeouts and
  bounded observations; verify-before-activate indices and alias-safe cleanup.
- Tests run/results: targeted config, migration, search operation and governance
  suites passed (20 tests); Python compilation passed. Full regression result is
  recorded below after final validation.
- Feature flags: DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED,
  SEARCH_SHADOW_COMPARISON_ENABLED and ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED
  all default false. Tenant shadow policy cannot override global disable.
- Known risks: shadow executors still require staged wiring for every legacy
  provider-specific search surface; the shared Elasticsearch alias remains a
  global resource and should only be promoted after full tenant coverage
  verification.
- Rollback: disable the three flags, stop admin lifecycle operations, switch
  aliases to a verified previous index if needed, revert Step 33, then downgrade
  to 0014. Export audits before downgrade if long-term retention is required.
- Next recommended step: migrate staging with flags false, select active
  analyses for one tenant, shadow v1/v2 at 5%, and promote only after report and
  fixture thresholds pass.

## CI repair - API, worker and provider unit tests

- First failing test: `MetadataDocumentValidatorTest.test_malicious_deep_json_is_rejected_without_recursion_escape`
  on Linux Python 3.12.13. The validator recursively deep-copied a decoded
  1,500-level document before its iterative depth check and raised
  `RecursionError`.
- Correction: validate structural limits before copying; valid documents are
  still deep-copied before being returned. A regression test verifies that an
  over-depth document is rejected without invoking `copy.deepcopy`.
- Failing test alone:
  `python -m unittest tests.modules.ai_metadata.test_validator.MetadataDocumentValidatorTest.test_malicious_deep_json_is_rejected_without_recursion_escape -v`
  - 1 test passed.
- Containing module:
  `python -m unittest tests.modules.ai_metadata.test_validator -v`
  - 9 tests passed.
- Exact CI command, run once after the correction in a clean Linux Python
  3.12.13 container:
  `python -m pip install -r requirements.txt && timeout 10m python -m unittest discover -s tests -v`
  - 293 tests passed, 11 integration tests skipped, exit code 0.


## Step 33R1 review - active-analysis remediation

- Files changed: active-analysis integrity constraints, activation/rollback
  service and admin request, active-only operation selection, projection/index
  worker ordering, focused tests, and architecture records.
- Migration added: `0016_active_analysis_integrity`; migration 0015 was not
  recreated or edited. Upgrade replaces the analysis-ID-only reference with a
  tenant/asset/profile/analysis composite foreign key. Downgrade restores the
  0015 reference and preserves pointers and audit rows.
- Behavior introduced: activation accepts only completed, projected analyses
  without validation errors; asset-row locking serializes activation; rollback
  follows the exact latest transition for the active pointer; audit history is
  append-only; rebuild/reindex selection uses the explicit pointer; an index
  job is created only after the projection transaction succeeds.
- Tests run/results:
  - `.venv/bin/python -m unittest tests.modules.search.test_active_analysis_repository -v`: 1 passed.
  - `.venv/bin/python -m unittest tests.modules.search.test_active_analysis_service -v`: 3 passed.
  - `.venv/bin/python -m unittest tests.modules.search.test_active_analysis_admin -v`: 2 passed.
  - `.venv/bin/python -m unittest tests.migrations.test_active_analysis_integrity_migration tests.migrations.test_search_governance_migration -v`: 2 passed.
  - The initial pytest invocation did not run because pytest is not installed
    in the local virtualenv; the repository's unittest runner was used.
- Feature flags: no new flags; `DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED`
  remains the default-disabled runtime gate.
- Known risks: PostgreSQL row-lock concurrency is enforced by design and the
  database unique constraint is the final guard; the focused local tests use
  SQLite and do not replace PostgreSQL integration coverage.
- Rollback: disable deterministic active analysis, drain projection/index jobs,
  deploy the prior code, and downgrade Alembic to
  `0015_search_governance`. Pointer and audit records remain.
- Next recommended step: run the existing PostgreSQL active-analysis
  concurrency scenario in CI before enabling the flag for a pilot tenant.


## Step 33R2 review - shadow-search remediation

- Files changed: shared shadow coordinator, comparator/repository/reporting,
  explorer regular and streaming search wiring, v2 search wiring, application
  shutdown, configuration, focused fake-provider tests, and search docs.
- Migrations added: none.
- Behavior introduced: explicit v1-to-v2 and v2-to-v1 surface directions;
  primary-independent shadow scheduling; deterministic sampling; strict bounded
  provider timeout; app-lifetime drain/cancel; stable error categories; standard
  documented overlap-at-K, top-1, zero-result, count-difference and latency
  observations; filtered percentile reports; bounded metrics without tenant or
  query labels.
- Tests run/results:
  `.venv/bin/python -m unittest tests.modules.search.test_shadow_search_r2 tests.modules.search.test_governance.SearchGovernanceTest.test_shadow_timeout_never_delays_primary_and_persists_bounded_data tests.modules.search.test_governance.SearchGovernanceTest.test_global_shadow_disable_is_an_upper_bound -v`
  passed 5 tests. Fake providers only; Elasticsearch was not started.
- Feature flags: no new flag. `SEARCH_SHADOW_COMPARISON_ENABLED` remains false
  by default and tenant policy cannot override it.
- Known risks: observation persistence is synchronous inside a detached task;
  a slow database cannot delay the primary response but may extend graceful
  shutdown until the configured bound.
- Rollback: disable shadow comparison, deploy the prior code, and remove the
  new shutdown setting. No database rollback is required.
- Next recommended step: enable one direction at 5% for a pilot tenant and
  validate report counts and p95 latency before expanding sampling.

## Step 33R3 review - Elasticsearch lifecycle remediation

- Files changed: lifecycle service and admin operations, stable Elasticsearch
  settings reader, lifecycle-state model/migration, focused lifecycle and real
  Elasticsearch tests, search/data-model docs, and the operator runbook.
- Migration added: `0017_search_lifecycle_states`; migrations 0015 and
  0016 were not recreated or edited. Upgrade adds durable `verified` and
  `activating` states. Downgrade restores the 0016 state constraint after those
  records have been reconciled.
- Behavior introduced: explicit allowed transitions and verified state;
  complete mapping/analyzer/projection/count/failure/golden/tenant validation;
  checkpointed atomic alias activation and exact previous rollback; database to
  cluster reconciliation; bounded, cancellable, resumable, age-gated cleanup
  with dry-run, explicit confirmation and a final alias recheck.
- Tests run/results:
  - `cd apps/api && .venv/bin/python -m unittest tests.modules.search.test_index_lifecycle_r3 tests.modules.search.test_governance.SearchGovernanceTest.test_verify_before_activate_and_alias_safe_cleanup tests.migrations.test_search_index_lifecycle_state_migration -v`: 7 passed.
  - `cd apps/api && INTEGRATION_ELASTICSEARCH_URL=http://127.0.0.1:9200 .venv/bin/python -m unittest tests.integration.test_elasticsearch -v` against pinned Elasticsearch 8.15.3: 1 passed.
  - The integration module first reported one expected skip with no configured
    endpoint; it was then run against the real temporary service above.
- Feature flags: no new flag and no global enablement. Existing
  `ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED` and `ELASTICSEARCH_V2_ENABLED` remain
  default-disabled gates.
- Known risks: aliases are global to an index prefix, so operators must include
  all expected tenants in verification. Cancellation is cooperative between
  bounded cleanup candidates; an in-flight Elasticsearch delete is not
  interruptible. Migration downgrade requires state reconciliation first.
- Rollback: disable lifecycle operations, reconcile read/write aliases to one
  verified index, ensure no record remains `verified` or `activating`, deploy
  the prior code, and downgrade Alembic to `0016_active_analysis_integrity`.
  The migration does not delete physical indices or change aliases.
- Next recommended step: deploy migration 0017 with flags false, verify one
  staging index using ranked golden fixtures and all pilot tenants, exercise an
  interrupted activation/reconcile drill, then keep the previous index through
  the documented retention window.

## Step 33 full regression validation

The first PostgreSQL attempt exposed two validation defects rather than product
features: Alembic revision `0017_search_index_lifecycle_states` exceeded the
standard 32-character version column, and the PostgreSQL integration test
hard-coded the prior 0014 head. The revision is now
`0017_search_lifecycle_states` and all revisions have a <=32-character
regression assertion. The PostgreSQL test resolves the single expected head
from Alembic. A repeated run on a previously used database demonstrated that
claim/lease integration tests require the clean service lifecycle used by CI;
the final recorded run used a fresh PostgreSQL container.

| Group | Exact test command | Result | Count | Duration | Skips |
|---|---|---:|---:|---:|---|
| Active-analysis repository | `.venv/bin/python -m unittest tests.modules.search.test_active_analysis_repository -v` | passed | 1 | 0.34s | none |
| Active-analysis service/routes | `.venv/bin/python -m unittest tests.modules.search.test_active_analysis_service tests.modules.search.test_active_analysis_admin -v` | passed | 5 | 0.89s | none |
| Shadow execution | `.venv/bin/python -m unittest tests.modules.search.test_shadow_search_r2.ShadowSearchRemediationTest.test_primary_never_depends_on_shadow_or_policy_success tests.modules.search.test_shadow_search_r2.ShadowSearchRemediationTest.test_timeout_sampling_direction_and_shutdown_are_bounded -v` | passed | 2 | 0.29s | none |
| Shadow observations/reporting | `.venv/bin/python -m unittest tests.modules.search.test_shadow_search_r2.ShadowSearchRemediationTest.test_standard_overlap_counts_latencies_reports_and_tenant_isolation tests.modules.search.test_governance.SearchGovernanceTest.test_shadow_timeout_never_delays_primary_and_persists_bounded_data tests.modules.search.test_governance.SearchGovernanceTest.test_global_shadow_disable_is_an_upper_bound -v` | passed | 3 | 0.51s | none |
| Index lifecycle unit | `.venv/bin/python -m unittest tests.modules.search.test_index_lifecycle_r3 tests.modules.search.test_governance.SearchGovernanceTest.test_verify_before_activate_and_alias_safe_cleanup -v` | passed | 6 | 0.44s | none |
| Migration 0015 | `.venv/bin/python -m unittest tests.migrations.test_search_governance_migration -v` | passed | 1 | 2.13s | none |
| Migration fix regression | `.venv/bin/python -m unittest tests.migrations.test_search_governance_migration tests.migrations.test_search_index_lifecycle_state_migration -v` | passed | 2 | 3.75s | none |
| PostgreSQL 16.4 integration | `DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:5432/cam_integration INTEGRATION_DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:5432/cam_integration .venv/bin/python -m unittest tests.integration.test_postgresql -v` | passed | 4 | 0.40s | none |
| Elasticsearch 8.15.3 integration | `INTEGRATION_ELASTICSEARCH_URL=http://127.0.0.1:9200 ELASTICSEARCH_URL=http://127.0.0.1:9200 .venv/bin/python -m unittest tests.integration.test_elasticsearch -v` | passed | 1 | 2.37s | none |
| Exact Python 3.12 CI unit discovery | `python -m pip install -r apps/api/requirements.txt && cd apps/api && timeout 10m python -m unittest discover -s tests -v` | passed | 309 | 89.518s tests; 139.05s including clean image/dependencies | 11 expected: 1 Elasticsearch, 4 PostgreSQL, 6 pipeline because the unit job has no real-service URLs |
| Durable pipeline E2E | `DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:5432/cam_pipeline INTEGRATION_DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:5432/cam_pipeline ELASTICSEARCH_URL=http://127.0.0.1:9200 INTEGRATION_ELASTICSEARCH_URL=http://127.0.0.1:9200 .venv/bin/python -m unittest tests.integration.test_pipeline_e2e -v` | passed | 6 | 5.23s | none |

Final controls:

- `python -m alembic heads` reports exactly
  `0017_search_lifecycle_states (head)`.
- `DETERMINISTIC_ACTIVE_ANALYSIS_ENABLED`,
  `SEARCH_SHADOW_COMPARISON_ENABLED`,
  `ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED`, `SEARCH_PROJECTION_ENABLED`,
  `ELASTICSEARCH_V2_ENABLED`, and `SEARCH_QUERY_PARSER_V2_ENABLED` all resolve
  to false from default settings.
- Search v2 was enabled only inside isolated E2E `Settings`; it was not enabled
  globally or in repository defaults.
- No real Google Drive, SharePoint or Gemini credential was required. The E2E
  suite uses fake download/source resolution, fake managed storage and
  `FakeGemini` with a non-network fake key. PostgreSQL and Elasticsearch were
  disposable local services.
- Staging rollout and rollback commands are documented in
  `docs/operations/CONTROLLED_ROLLOUT.md`. Step 33 is validation-green locally;
  merge/deployment remains gated on the corresponding GitHub Actions run.


## Production HTTP and environment configuration review

- Files changed: `.env.example`, API settings/environment bootstrap, FastAPI
  middleware/app factory, Google and Microsoft OAuth return redirects, and
  focused configuration/HTTP tests. Frontend runtime code required no changes.
- Migrations added: none.
- Behavior introduced: configurable public app URL, exact CORS origins, trusted
  hosts, production-disabled API docs, fixed development-only dotenv loading,
  and OAuth redirects based on validated settings. Vite continues to proxy
  relative `/api` requests to localhost only in development.
- Tests run/results:
  - `.venv/bin/python -m unittest tests.test_config tests.test_environment tests.test_http_config tests.test_app_smoke -v`: 22 passed.
  - `.venv/bin/python -m unittest tests.modules.auth_persistence.test_config.AuthConfigurationTest.test_keys_required_and_production_cookie_is_secure -v`: 1 passed.
  - `.venv/bin/python -m unittest tests.modules.auth_persistence.test_config -v`: 2 passed.
  - `.venv/bin/python -m unittest discover -s tests -q`: 320 passed, 11 expected integration skips, 93.576s.
  - `npm run typecheck`: passed.
  - `npm test`: 9 passed in 2 files.
  - `npm run build`: passed (46 modules transformed).
- Feature flags: none added or enabled. `API_DOCS_ENABLED` is an HTTP setting,
  defaults true for development, and is required to be false in production.
- Known risks: reverse-proxy forwarded-host behavior must match the explicit
  `TRUSTED_HOSTS` deployment value; deployment configuration is intentionally
  outside this change.
- Rollback: revert this change and restore `CLIENT_URL` plus the previous
  localhost-only middleware configuration. No database rollback is required.
- Next recommended step: provide production environment values through the
  deployment secret/configuration system and exercise host/CORS behavior behind
  the actual reverse proxy before rollout.


## Production-safe database startup review

- Files changed: database settings/runtime lifecycle, FastAPI shutdown, Alembic
  environment, explicit tag operation, legacy metadata schema adoption migration,
  focused unit/migration/PostgreSQL tests, environment example, README and
  current-state documentation.
- Migration added: `0018_legacy_metadata_schema`. It adopts the pre-existing
  `tags`, `asset_metadata` and `asset_tag_assignments` tables into the
  Alembic chain, creates them on clean databases, and preserves existing tables
  and data on downgrade.
- Behavior introduced: production requires a non-SQLite `DATABASE_URL`, checks
  `SELECT 1`, requires exactly one current Alembic head, never mutates schema
  at API startup, and disposes the engine on shutdown. Development keeps the
  default SQLite database and upgrades it through Alembic. SQLAlchemy pool size,
  overflow, timeout, recycle and PostgreSQL connect timeout are configurable.
  Built-in tags are seeded only by
  `python -m app.operations.tag_cli seed-system-tags`.
- Tests run/results:
  - `.venv/bin/python -m unittest tests.test_database_startup tests.migrations.test_legacy_metadata_schema_migration -v`: 10 passed.
  - Production HTTP/configuration regression group: 22 passed.
  - PostgreSQL 16.4 empty-schema migration plus
    `tests.integration.test_postgresql -v`: 5 passed.
  - `.venv/bin/python -m unittest discover -s tests -q`: 330 passed,
    12 expected integration skips, 97.455s.
  - `.venv/bin/python -m unittest tests.test_app_smoke -v`: 2 passed after
    the final guaranteed-disposal refinement.
- Feature flags: none added or enabled.
- Known risks: production deployment must run Alembic before starting new API
  instances. Development auto-upgrade is intended for a single local process;
  shared environments must use an explicit migration release step. The 0018
  downgrade intentionally retains legacy metadata tables to avoid data loss.
- Rollback: stop new API instances, deploy the previous code, and downgrade the
  revision marker to `0017_search_lifecycle_states` if required; 0018 retains
  tag/metadata tables and their data. No automatic production DDL is performed.
- Next recommended step: run `python -m alembic upgrade head`, then
  `python -m app.operations.tag_cli seed-system-tags`, before starting the
  production API with validated pool settings.

## Production health and reverse-proxy support review

- Files changed: API health service and routes, HTTP/build settings, focused
  health and proxy tests, PostgreSQL readiness integration coverage,
  `.env.example`, README, current-state and security documentation.
- Migrations added: none.
- Behavior introduced: `/live` reports process liveness, `/ready` checks
  PostgreSQL and conditionally checks Elasticsearch when Search v2, search
  shadow comparison or index lifecycle is enabled, and `/version` exposes only
  validated build version/commit identifiers. Forwarded headers are disabled by
  default and can trust only explicitly configured IP addresses or CIDR ranges;
  wildcard and all-address networks are rejected.
- Tests run/results:
  - `.venv/bin/python -m unittest tests.test_production_health tests.test_http_config tests.test_app_smoke tests.test_config -v`: 28 passed.
  - `.venv/bin/python -m unittest discover -s tests -q`: 341 passed, 13 expected integration skips, 168.129s.
  - PostgreSQL 16.4 empty-schema Alembic upgrade plus
    `DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:55435/cam_integration INTEGRATION_DATABASE_URL=postgresql+psycopg://cam_test:cam_test@127.0.0.1:55435/cam_integration .venv/bin/python -m unittest tests.integration.test_postgresql -v`: 6 passed in 0.134s.
- Feature flags: none added or enabled. Elasticsearch readiness is required only
  when an existing relevant search feature flag is enabled.
- Known risks: the upstream ASGI server/load balancer must forward headers from
  an address included in `PROXY_TRUSTED_IPS`; `TRUSTED_HOSTS` remains an
  independent host-header boundary. Readiness performs live dependency calls,
  so orchestrator probe cadence should respect the configured timeout.
- Rollback: revert this change and remove the three probe routes/settings. No
  database rollback is required.
- Next recommended step: set `APP_VERSION` and `BUILD_COMMIT` in the release
  environment, configure the exact proxy subnet, and validate probes through
  the production ingress before rollout.

## VPS deployment artifacts review

- Files changed: native Nginx site, Elasticsearch-only production Compose file,
  native API/worker systemd units, production environment template and VPS
  validation/runbook documentation.
- Migrations added: none.
- Behavior introduced: none in application runtime. The artifacts bind API,
  worker health, native PostgreSQL and Dockerized Elasticsearch to loopback;
  Nginx alone serves the frontend and public HTTPS traffic.
- Feature flags: the environment template keeps the processing and search
  pipeline disabled. The global AI emergency stop is enabled defensively.
- Credentials: no real credentials are included; all credential fields are
  blank or explicit replacement placeholders.
- Rollback: restore prior systemd/Nginx/environment files and reload their
  managers. Stop Elasticsearch with Compose without deleting its named volume.
  No database rollback is required.
- Validation commands and host-level checks are documented in
  `docs/operations/VPS_DEPLOYMENT.md`.
- Validation results:
  - `docker compose --file infrastructure/docker/docker-compose.prod.yml config --quiet`: passed.
  - Compose service inventory: passed; only `elasticsearch` is present.
  - Production environment loaded through `Settings`: passed.
  - `systemd-analyze verify` with temporary dependency/path stubs: passed.
  - `nginx -t` using Nginx 1.27.5 and a temporary certificate: passed.

## Production deployment tooling review

- Files changed: secret-safe production environment helper, fail-fast deployment
  CLI, focused tooling tests, atomic-release Nginx/systemd paths and the VPS
  deployment runbook.
- Migrations added: none.
- Behavior introduced: explicit commands for configuration validation, Python
  installation, `npm ci` frontend builds, one-head verification, forward-only
  migration, idempotent seeding, immutable application/frontend installation,
  atomic switching, API/worker restarts, health verification, rollback and
  bounded diagnostics. No command runs a PostgreSQL downgrade.
- Release safety: application and frontend `current`/`previous` links are managed
  separately under root-owned directories; completed application releases become
  root-owned before activation. Worker restart uses systemd SIGTERM handling.
- Secret safety: the environment helper never evaluates shell syntax, validates
  owner/mode and placeholders, strips environment inheritance, suppresses child
  process output and reports only bounded setting/error identifiers. Diagnostics
  emit release IDs, service states, HTTP codes and dependency availability only.
- Tests and validation:
  - `apps/api/.venv/bin/python -m unittest deploy.tests.test_production_env -v`:
    5 passed in 0.226s.
  - `deploy/bin/cam-deploy verify-alembic-head /home/baonghia/creative-asset-manager`:
    passed; exactly one head.
  - `deploy/bin/cam-deploy diagnostics`: passed with bounded status-only output.
  - Bash syntax and Python compile checks: passed.
  - Production Compose validation: passed.
  - `systemd-analyze verify` with temporary dependency/path stubs: passed.
  - `nginx -t` with Nginx 1.27.5 and a temporary certificate: passed.
- Feature flags: none changed or enabled.
- Known risks: application migrations must remain backward compatible with the
  retained release because rollback intentionally leaves PostgreSQL at its
  current revision. Release switching is atomic per symlink, but application and
  frontend links are two ordered filesystem operations.
- Rollback: `cam-deploy rollback-release` switches to the recorded application
  and frontend release, restarts services and verifies health. It never invokes
  `alembic downgrade`.

## VPS production deployment validation review

- Files changed: focused deployment contract tests plus the VPS validation
  matrix/runbook. Application, database and deployment runtime code was not
  changed.
- Migrations added: none.
- Behavior introduced: none. This step validates the existing production
  artifacts and records the reproducible checks.
- Tests and actual results:
  - `PYTHONPATH=apps/api apps/api/.venv/bin/python -m unittest deploy.tests.test_vps_deployment tests.test_config tests.test_http_config tests.test_production_health tests.modules.auth_persistence.test_config tests.modules.processing.test_runtime -v`: 46 passed in 11.234s.
  - `npm ci --no-audit --no-fund && npm run build`: passed; Vite 5.4.14 transformed 46 modules and produced the static release.
  - Empty PostgreSQL 16.4 database `alembic upgrade head`: passed at `0018_legacy_metadata_schema`; exactly one head confirmed.
  - `tests.integration.test_postgresql -v`: 6 passed in 0.152s.
  - Native API: `/live` passed; `/ready` reported PostgreSQL available and Elasticsearch disabled because search flags were false.
  - Native worker: startup/health passed and `SIGTERM` produced a bounded graceful exit.
  - Production Elasticsearch Compose: Docker health passed, cluster reached yellow/green, and port 9200 was bound only to `127.0.0.1`.
  - `nginx -t`: passed with Nginx 1.27.5. Runtime checks passed for SPA fallback, `/api` and `/live` proxying, and immutable hashed-asset caching.
  - OAuth through Nginx: passed with a production callback URL and `HttpOnly`, `Secure`, `SameSite=Lax` state cookie; only fake credentials were used.
- Feature flags: no flag was changed or enabled. Unified ingestion, AI
  processing and Search v2 remain false by default and in the production
  template. `AI_EMERGENCY_STOP_ENABLED=true` remains the defensive global stop.
- Known risks: Nginx 1.27.5 emits a deprecation warning for the compatible
  `listen ... http2` syntax, although configuration validation succeeds. The
  PostgreSQL integration suite emits an existing unclosed-connection resource
  warning that did not fail the tests.
- Rollback: revert the documentation and focused deployment test commit. No
  service, data or schema rollback is required.
- Next recommended step: run the same matrix on the target VPS with its real
  hostname/certificate and production secrets supplied out of band, then use
  tenant-scoped rollout controls rather than enabling pipeline flags globally.

## AI-MULTI-01 review - provider registry

- Files changed: AI provider contracts/exports, provider registry, adapter identities, worker composition, single/batch handlers and services, focused tests, provider docs, roadmap and review.
- Migrations added: none.
- Behavior introduced: workers can register multiple provider-neutral AI adapters; single analysis resolves persisted `analysis.ai_provider`, batch operations resolve persisted `batch.provider`, missing adapters fail non-retryably with `ai_provider_unavailable`, and no cross-provider fallback occurs. Gemini remains the only production adapter and is registered only when configured.
- Tests and actual results:
  - `.venv/bin/python -m unittest tests.providers.test_ai_registry tests.domain.providers.test_contracts tests.providers.test_gemini_ai -v`: 12 passed in 0.018s.
  - `.venv/bin/python -m unittest tests.modules.ai_metadata.test_handler -v`: 2 passed in 0.054s.
  - `.venv/bin/python -m unittest tests.modules.ai_batch.test_handlers -v`: 2 passed in 0.054s.
  - `.venv/bin/python -m unittest tests.modules.processing.test_runtime.WorkerBootstrapTest -v`: 4 passed in 0.009s.
- Feature flags: none added, changed or enabled.
- Known risks: provider registration is process-local and static for one worker lifetime. OpenAI and request/API provider selection remain outside this step. Legacy null batch providers retain the existing Gemini compatibility normalization.
- Rollback: revert this change to restore the singleton worker AI dependency; no database or data rollback is required.
- Next recommended step: AI-MULTI-02 OpenAI single-analysis adapter against this registry.


## AI-MULTI-02 review - OpenAI single-image Responses API

- Files changed: OpenAI provider adapter, centralized OpenAI configuration,
  worker registry bootstrap, lease ownership guard, focused provider/config/
  bootstrap/service tests, dependency and environment templates, and provider
  architecture documentation.
- Migrations added: none.
- Behavior introduced: when explicitly enabled and configured, workers register
  an `openai` provider that uses the official asynchronous Responses API for
  bounded prepared images. Requests contain only the profile prompt and a Base64
  image data URL, use strict JSON Schema Structured Outputs when the profile is
  compatible, otherwise request one JSON object, and keep provider-side response
  storage disabled by default. Existing internal metadata safety/schema
  validation, projection building, PostgreSQL persistence, governance accounting,
  and persisted-provider routing remain authoritative. OpenAI batch operations
  fail non-retryably with `openai_batch_not_implemented`.
- Tests and actual results:
  - `.venv/bin/python -m unittest tests.providers.test_openai_ai -v`:
    11 passed in 0.039s.
  - `.venv/bin/python -m unittest tests.test_config -v`:
    18 passed in 0.074s.
  - `.venv/bin/python -m unittest tests.modules.processing.test_runtime.WorkerBootstrapTest -v`:
    6 passed in 0.060s.
  - `.venv/bin/python -m unittest tests.modules.ai_metadata.test_service tests.modules.ai_metadata.test_handler -v`:
    9 passed in 0.292s.
  - `.venv/bin/python -m unittest tests.providers.test_gemini_ai tests.providers.test_ai_registry tests.domain.providers.test_contracts -v`:
    12 passed in 0.012s.
  - Python compile and `git diff --check`: passed.
- Feature flags: `OPENAI_AI_ENABLED=false` by default.
  `OPENAI_STORE_RESPONSES=false` by default. Gemini configuration and behavior
  are unchanged.
- Known risks: OpenAI Batch API is deliberately unavailable until AI-MULTI-03.
  Provider-model choice remains configuration-driven because enqueue/request API
  selection is outside this step. Only profile schemas compatible with Responses
  strict Structured Outputs use schema mode; other profiles use JSON-object mode
  and still pass through the internal validator. Cost remains locally estimated
  unless a provider supplies an explicit reported cost.
- Rollback: revert this change and remove the OpenAI dependency/settings from
  deployment configuration. Leave `OPENAI_AI_ENABLED=false` for immediate
  runtime rollback. No database rollback is required.
- Next recommended step: AI-MULTI-03 OpenAI Batch API using the existing provider
  registry without changing single-analysis behavior.

## AI-MULTI-03 review - OpenAI Batch API

- Files changed: OpenAI adapter, provider batch submission contract, centralized
  OpenAI batch configuration, worker bootstrap, neutral batch JSONL preparation,
  environment templates, provider documentation, and focused provider/service/
  configuration/bootstrap regressions.
- Migrations added: none. Existing ai_batch_jobs, ai_batch_items, usage,
  reservations, analysis history, result importer, projection, and indexing
  records are reused.
- Behavior introduced: OpenAI advertises Batch API capability and executes only
  when OPENAI_BATCH_ENABLED=true. Provider-neutral rows are transformed
  incrementally into bounded /v1/responses JSONL, uploaded with purpose=batch,
  submitted for 24h completion, and reconciled by stable submission metadata
  after ambiguous transport outcomes. Temporary files use mode 0600 and are
  removed after upload. Polling maps every documented OpenAI batch state.
  Output and error JSONL files stream by custom_id through the existing
  validator/importer/governance/indexing pipeline; partial expired output is
  imported and only unresolved items remain retryable. Cancellation is
  idempotent. Provider usage is recorded without inventing provider cost.
- Tests and actual results:
  - cd apps/api && .venv/bin/python -m unittest
    tests.providers.test_openai_batch -v: 8 passed in 0.023s.
  - cd apps/api && .venv/bin/python -m unittest
    tests.modules.ai_batch.test_service -v: 7 passed in 0.520s.
  - cd apps/api && .venv/bin/python -m unittest
    tests.providers.test_openai_batch tests.providers.test_openai_ai
    tests.modules.ai_batch.test_service tests.modules.ai_batch.test_handlers
    tests.modules.processing.test_runtime.WorkerBootstrapTest tests.test_config
    tests.providers.test_gemini_ai -v: 60 passed in 0.708s.
  - Python compile and git diff --check: passed.
- Feature flags: OPENAI_BATCH_ENABLED=false by default in application,
  development template, and production template. Existing global
  AI_BATCH_ANALYSIS_ENABLED remains an additional upper bound. No AI feature was
  enabled automatically.
- Known risks: provider batch listing is bounded to the most recent 100 batches
  during automatic ambiguity reconciliation; an older unresolved submission may
  require the existing operator path. OpenAI output/error files are streamed,
  but one JSONL line is held in memory under the configured file-byte upper
  bound. Provider API limits can change and remain constrained by local limits.
- Rollback: set OPENAI_BATCH_ENABLED=false for immediate runtime rollback, then
  revert this change. Existing queued jobs remain preserved and no database
  downgrade is required.
- Next recommended step: AI-MULTI-04 provider capabilities and request API.

## AI-MULTI-04 review - request selection and capabilities

- Files changed: centralized AI provider registry factory, request/response
  schemas, tenant-aware provider selection service, analysis and capabilities
  routes, normal-analysis identity, configuration/environment templates,
  focused tests, and architecture documentation.
- Migration added: `0019_ai_provider_selection`. It adds provider and model to
  the partial unique identity for non-forced analyses. Downgrade restores the
  legacy index and may require provider/model variants to be removed or
  force-marked first.
- Behavior introduced: authenticated clients may select Gemini or OpenAI,
  single or batch processing, and an allowlisted model. Omitted provider and
  mode retain the documented temporary Gemini/single compatibility defaults.
  Selection is gated by global flags, configured registry adapters, credentials,
  capabilities, tenant policy, provider policy, and OpenAI batch enablement.
  The capabilities endpoint exposes only public, tenant-eligible choices.
- Tests and actual results:
  - `cd apps/api && .venv/bin/python -m unittest tests.test_config
    tests.modules.ai_metadata.test_api
    tests.modules.processing.test_runtime.WorkerBootstrapTest
    tests.modules.ai_metadata.test_service tests.modules.ai_metadata.test_handler
    tests.modules.ai_batch.test_service tests.modules.ai_batch.test_handlers
    tests.providers.test_ai_registry tests.providers.test_gemini_ai
    tests.providers.test_openai_ai tests.providers.test_openai_batch
    tests.migrations.test_ai_provider_selection_migration -v`:
    85 passed in 3.794s (4.93s wall time).
  - Python compile, `git diff --check`, and `alembic heads`: passed; exactly
    one head, `0019_ai_provider_selection`.
- Feature flags: none enabled. Existing AI and provider flags remain false by
  default, including `OPENAI_AI_ENABLED` and `OPENAI_BATCH_ENABLED`.
- Known risks: migration downgrade can conflict after multiple provider/model
  variants exist for one legacy identity. Compatibility defaults must be
  removed only through a future versioned API transition.
- Rollback: disable the existing AI flags for immediate runtime rollback, revert
  the application change, then downgrade to 0018 only after resolving any
  provider/model variants that collide under the legacy uniqueness key.

## AI-MULTI-05 review - single and batch enqueue orchestration

- Files changed: durable analysis-request models and migration, bulk analysis
  router/schemas/orchestration, processing cancellation support, explicit batch
  candidate handling, provider registry async cleanup, configuration templates,
  focused API/migration/config tests, API/data-model documentation, roadmap and
  review.
- Migration added: `0020_ai_analysis_requests`. It adds tenant-scoped durable
  request and item ledgers for canonical-body idempotency, partial acceptance,
  status aggregation and cancellation audit fields. Downgrade removes only
  these ledgers; analyses, provider batches and processing jobs remain intact.
- Behavior introduced: single mode enqueues only `asset_analyze`; batch mode
  enqueues one immediate `ai_batch_prepare` job with explicit analysis IDs and
  never enqueues `asset_analyze`. Provider/model/profile/version remain
  persisted per analysis, and existing batch compatibility grouping prevents
  provider/model mixing. The authenticated bulk API applies bounded payload and
  item limits, tenant isolation, model/provider policy, advisory budget
  preflight, canonical Idempotency-Key semantics, partial item acceptance,
  aggregate status and actor/reason cancellation.
- Tests and actual results:
  - `cd apps/api && .venv/bin/python -m unittest
    tests.modules.ai_metadata.test_bulk_api tests.modules.ai_metadata.test_api
    tests.modules.ai_metadata.test_service tests.modules.ai_metadata.test_handler
    tests.modules.ai_batch.test_service tests.modules.ai_batch.test_handlers
    tests.providers.test_ai_registry tests.providers.test_gemini_ai
    tests.providers.test_openai_batch tests.test_config
    tests.migrations.test_ai_provider_selection_migration
    tests.migrations.test_ai_analysis_requests_migration -v`: 77 passed in
    7.816s (9.93s wall time).
  - Python compile passed; `alembic heads` reports exactly one head,
    `0020_ai_analysis_requests`.
- Feature flags: none enabled or changed. All existing AI, OpenAI batch and
  processing flags remain false by default.
- Known risks: bulk budget preflight is intentionally advisory; the worker-side
  atomic reservation/circuit breaker remains authoritative. Submitted provider
  batch cancellation depends on the configured adapter; the durable request is
  still marked cancelled and provider cancellation remains retryable by an
  operator.
- Rollback: disable existing AI/processing flags, stop affected workers, revert
  the application change, and downgrade to `0019_ai_provider_selection` if the
  request ledger is no longer needed. No provider analysis or batch data is
  deleted.
- Next recommended step: AI-MULTI-06 frontend provider/mode/model selection UI.

## AI-MULTI-06 review - frontend provider and processing selection

- Files changed: typed metadata API client/contracts, provider-aware Analyze
  Metadata dialog, explorer bulk action, asset-detail analysis action/history,
  scoped responsive styles, focused Vitest coverage, and this review.
- Migrations added: none.
- Behavior introduced:
  - the UI loads tenant-filtered capabilities from
    `GET /api/v1/admin/ai/capabilities` and never hard-codes provider/model
    availability;
  - one asset defaults to Single and multiple assets default to Batch, while a
    valid explicit provider/model/mode selection is retained;
  - single selection uses the single analysis endpoint and multiple selection
    uses the idempotent bulk endpoint, always sending provider, model, mode,
    profile and force explicitly;
  - only indexed non-folder selections with internal asset IDs can be
    submitted, and disabled/empty/failure states explain why;
  - asynchronous progress exposes provider, model, mode, accepted, queued,
    running, completed, failed and budget-blocked counts; authorized detail
    operators may also retrieve provider batch state;
  - force analysis requires confirmation stating that history is preserved and
    names the selected provider/model;
  - analysis history presents provider/model, inferred persisted mode,
    profile/version, status, attempts, retry errors and authorized usage/cost.
- Tests and actual results:
  - `cd apps/client && npm test`: 4 files and 22 tests passed in 542ms.
  - `cd apps/client && npm run typecheck`: passed.
  - `cd apps/client && npm run build`: passed; Vite 5.4.14 transformed 51
    modules and produced the production bundle in 590ms.
  - frontend source scan found no `OPENAI_API_KEY` or `GEMINI_API_KEY`
    references.
- Feature flags: none added, changed or enabled. Provider visibility remains
  entirely controlled by server capabilities and existing tenant/global
  policies.
- Known risks: no dedicated public cost-estimate endpoint currently exists, so
  the frontend does not calculate provider pricing. It displays authoritative
  budget-preflight failures returned by the bulk API and persisted estimated or
  provider-reported costs in authorized analysis history.
- Rollback: revert this frontend-only change. No schema, worker, provider or
  queued-job rollback is required.
- Next recommended step: AI-MULTI-07 governance and production controls.


## AI-MULTI-07 review - multi-provider production governance

- Migration added: 0021_ai_multi_governance, with step-scoped downgrade.
- Behavior introduced: independent Gemini/OpenAI and single/batch tenant
  controls; database-backed pre-claim concurrency and runtime emergency stops;
  effective-dated mode-specific rates; provider budgets; fail-closed
  missing_cost_rate; audited privileged overrides; bounded metrics; forced
  analysis and cancellation audit events.
- Tests: focused governance, tenant claim, single analysis, analysis API, bulk
  orchestration, batch service/handler and migration groups: 57 tests passed
  in 25.080s. Compile check and git diff hygiene passed; Alembic reports
  exactly one head at 0021_ai_multi_governance.
- Global feature flags remain upper bounds and no AI feature was enabled.
- Rollback: set the global/runtime AI stop, drain workers, deploy the previous
  application, then downgrade to 0020_ai_analysis_requests only after workers
  no longer reference the new columns.

- Clean-environment backend regression: `cd apps/api && .venv/bin/python -m unittest discover -s tests -q`: 406 tests passed in 219.308s with 13 environment-dependent skips. The local `.env` was excluded for this run and restored afterward; no provider credentials were required.

## Step 21D review - file details and activity inspector

- Files changed: explorer details state, AssetGrid focus behavior, friendly AssetDetailsPanel, asset API types, responsive inspector styles and focused component tests.
- Migrations added: none.
- Behavior introduced: an accessible toolbar toggle opens or closes a Google Drive-style right inspector. It follows the focused file/folder while open, previews images and videos, shows type, size, location, provider, timestamps, tags, rating, processing status and provider link, and exposes an activity timeline. Existing metadata/history/jobs and operator actions remain available for registered internal assets.
- Tests and actual results: `cd apps/client && npm test` passed 5 files and 25 tests; `npm run typecheck` passed; `npm run build` passed with Vite 5.4.14 and 51 transformed modules.
- Feature flags: none added or changed. The inspector is a user-controlled UI toggle and does not alter provider, AI or Search v2 rollout.
- Known risks: automated in-app browser visual QA was unavailable because the desktop browser runtime could not initialize in the current sandbox. Responsive behavior is covered by CSS breakpoints and the production build, but should be smoke-tested once in the running signed-in explorer.
- Rollback: revert the frontend-only change. No database, API, worker or queued-job rollback is required.

## AI-OPS-01 review - tenant-scoped operations APIs

- Files changed: AI Operations filters, SQL aggregation repository, authenticated
  admin router, API registration, focused tests, reporting indexes and this
  completion record.
- Migration added: 0022_ai_operations_indexes. It adds tenant/date reporting
  indexes for analyses, AI batches and processing jobs. Downgrade removes only
  those indexes and does not alter authoritative data.
- Behavior introduced: six tenant-isolated read-only endpoints expose summary,
  UTC daily metrics, provider/model/mode breakdowns, stable failure codes,
  paginated AI jobs and paginated usage. Interactive ranges default to seven
  days and are capped at 90 days. Estimated, provider-reported and reconciled
  costs remain separately labelled. Job payloads, raw errors, signed URLs,
  provider request IDs and credentials are not returned.
- Success-rate denominator: completed plus terminal failed analyses; queued,
  running, cancelled and budget-blocked analyses do not affect the rate.
- Tests and actual results:
  - cd apps/api && .venv/bin/python -m unittest tests.modules.ai_operations.test_api
    tests.migrations.test_ai_operations_indexes_migration
    tests.integration.test_ai_operations_postgresql
    tests.modules.processing_policy.test_auth
    tests.modules.ai_governance.test_budget_pilot
    tests.modules.ai_metadata.test_repository
    tests.modules.ai_batch.test_service
    tests.modules.processing.test_repository -v: 38 tests passed in 22.416s;
    one PostgreSQL-only percentile test skipped because
    INTEGRATION_DATABASE_URL was not configured.
  - cd apps/api && .venv/bin/alembic heads: exactly one head,
    0022_ai_operations_indexes.
- Feature flags: none added or enabled. These are authenticated reporting APIs
  over existing PostgreSQL state and do not enable AI processing.
- Known risks: current analysis rows infer single/batch mode from the persisted
  pipeline version; durable usage and batch records retain explicit mode. The
  PostgreSQL percentile path is covered by an environment-gated integration
  test and should run in CI with the standard PostgreSQL service.
- Rollback: remove the API router/application code and downgrade Alembic to
  0021_ai_multi_governance. No AI analysis, usage, job or batch records are
  deleted.
## AI-OPS-02 review - safe AI Operations controls

- Files changed: authenticated AI Operations control router/schemas/service, existing tenant/provider policy and processing job models/repositories/runtime, focused API/claim tests, migration, data-model notes and roadmap.
- Migration added: 0023_ai_operations_controls. It adds default AI provider/model to the existing tenant policy and durable cancellation-request metadata to existing processing jobs; downgrade removes only these fields and the cancellation eligibility index.
- Behavior introduced: tenant AI pause/resume, provider pause/resume, validated defaults and model allowlists, single/batch and durable concurrency controls, tenant daily/monthly budgets, idempotent failed-job retry, queued cancellation, running cancellation requests and provider-batch cancellation requests. Every effective mutation uses the existing append-only policy audit with tenant, actor, reason, before/after values and timestamp. No API accepts or returns provider API keys.
- Tests and actual results:
  - focused AI Operations, processing policy, governance and runtime group: 46 passed in 34.24s.
  - all migration unit tests: 21 passed in 55.75s.
  - processing repository, AI batch regression and control eligibility: 20 passed in 15.21s.
  - exact new control, claim, running-cancel and migration regression: 8 passed in 0.562s.
  - Python compile passed; Alembic reports exactly one head, 0023_ai_operations_controls.
- Feature flags: none added, enabled or changed. Global flags and runtime emergency stops remain upper bounds; tenant resume cannot override them.
- Known risks: provider-batch cancellation is durable and handled through the existing batch cancellation checkpoint; completion latency is bounded by the worker heartbeat/poll interval. PostgreSQL migration execution remains covered by the standard CI integration job rather than this local SQLite-focused run.
- Rollback: apply the global AI emergency stop, drain workers, deploy the prior application, then downgrade to 0022_ai_operations_indexes. Queued jobs and policy/audit history remain; outstanding cancellation-request metadata and stored dashboard defaults are removed.
- Next recommended step: add the AI Operations frontend controls against these authenticated endpoints.

## AI-OPS-04 review - Providers and Configuration tabs

- Files changed: AI Operations configuration read/write API and schemas,
  existing tenant/provider policy model and repository, provider p95 reporting,
  frontend API/types, provider cards, configuration forms, responsive styles,
  focused backend/frontend tests, data-model and roadmap documentation.
- Migration added: `0024_ai_operations_configuration`. It extends the existing
  tenant processing policy with default mode/profile, auto-analyze preference,
  daily item limit, retry count and timeout. It creates no duplicate policy
  table. Downgrade removes only these six fields and two checks.
- Behavior introduced: authenticated tenant admins can view public provider
  capabilities/connection state, today's requests/success/p95/cost, stable last
  error, pause/resume providers, manage tenant defaults, server-allowlisted
  models, single/batch controls, concurrency and budgets. Destructive actions
  require confirmation and an audit reason. Platform-global values are visibly
  read-only to tenant admins; only platform admins see the runtime emergency
  control. API keys, provider headers and credential values are never returned.
- Tests and actual results:
  - `cd apps/api && .venv/bin/python -m unittest -v tests.modules.ai_operations.test_controls tests.modules.ai_operations.test_api tests.migrations.test_ai_operations_configuration_migration`: 12 tests passed in 0.787s.
  - `cd apps/client && npm test -- app/ai-operations/ProvidersConfiguration.test.tsx`: 6 tests passed in 0.740s.
  - `cd apps/client && npm test`: 31 tests across 6 files passed in 0.671s.
  - `cd apps/client && npm run typecheck`: passed.
  - `cd apps/client && npm run build`: passed; Vite 5.4.14 transformed 60 modules.
- Feature flags: none added or enabled. Global AI/provider flags and runtime
  emergency controls remain upper bounds; OpenAI remains disabled by default.
- Known risks: provider p95 uses PostgreSQL percentile aggregation in
  production and a maximum-latency fallback for SQLite-only unit tests. The
  migration should still run through the standard PostgreSQL CI migration job.
- Rollback: activate the global or tenant AI pause, drain workers, deploy the
  previous frontend/API, then downgrade Alembic to
  `0023_ai_operations_controls`. Analyses, jobs, usage, budgets, provider policy
  and append-only audit history are preserved.
- Next recommended step: finish and independently validate AI-OPS-03 dashboard
  interaction tests before AI-OPS-05 performance/export validation.
## AI-OPS-05 review - performance, bounded exports and validation

- Files changed: AI Operations daily/query repositories, authenticated export router/helper, frontend export links and chart mapping, benchmark command, PostgreSQL/local CI coverage, focused tests, operations runbook, roadmap and this review. Existing migration `0024_ai_operations_configuration` was corrected to Alembic batch mode so the documented SQLite development path can upgrade and downgrade; the production PostgreSQL schema and constraints are unchanged.
- Migrations added: none. No `ai_daily_metrics` table, rollup worker, scheduler or backfill was added because direct aggregation met the documented threshold. Rollback requires no schema downgrade.
- Performance result: migrated PostgreSQL 16.4 with 100,000 analyses plus 100,000 usage records over 90 days, three warm repetitions, 750 ms threshold. Maximums were summary 48.52 ms, daily 147.50 ms, providers 90.97 ms and failures 9.84 ms. All passed. The repeatable command is `python -m app.operations.ai_operations_benchmark`; supplied database writes require `--allow-write` and use an isolated benchmark tenant that is cleaned afterward.
- Behavior introduced: tenant-authorized streaming CSV exports for daily metrics, usage, failures and jobs; 5,000 default/10,000 maximum rows; standard 90-day/filter validation; append-only export audit; no-store response headers; CSV formula neutralization; no payloads, raw errors, request IDs, signed URLs or credentials. Daily provider costs now come from the full server aggregate rather than the first usage page. Requested counts use `created_at`; terminal completion/failure counts use UTC `completed_at`; retry jobs and budget-blocked analyses remain non-terminal/separate; batch summary costs are never added to item usage costs.
- Tests and actual results:
  - backend dashboard/control: 14 passed across the final focused modules; the dashboard module includes 7 passing tests covering cross-endpoint filter agreement.
  - frontend: 7 files / 33 tests passed; typecheck passed; Vite production build passed with 60 modules.
  - worker/job/policy/governance regression: 43 passed in 45.370s.
  - migration 0024 focused unit: 1 passed; SQLite empty upgrade, downgrade to 0023 and re-upgrade passed.
  - clean CI discovery command: 425 tests passed in 128.083s, 14 service-dependent tests skipped in the clean unit environment.
  - Docker integration command `PYTHON_BIN=.venv/bin/python scripts/test-integration.sh`: PostgreSQL 16.4 migration upgrade/downgrade/re-upgrade, PostgreSQL repositories and dashboard percentile, Elasticsearch 8.15.3 and durable pipeline all passed, 14 tests in 4.936s.
  - shell syntax, Python compile, one Alembic head and application import were covered; the single head is `0024_ai_operations_configuration`.
- CI corrections discovered during validation: the committed 0024 constraint operations now use portable batch operations; the fake Gemini pipeline fixture explicitly allowlists its fake model and seeds a zero-cost fake rate, preserving production fail-closed model/cost validation. CI and the local integration script now include the PostgreSQL AI Operations percentile test.
- Feature flags: none added, enabled or changed. Dashboard reads/exports do not enable AI. Existing dashboard mutations remain protected by tenant/platform admin authorization; OpenAI, ingestion, AI automation and Search v2 remain default-disabled according to existing configuration.
- Known risks: the current measured sizing target is 100,000 attempts/usage rows per tenant per rolling 90 days. Re-benchmark on target VPS hardware and realistic tenant selectivity before rollout; add rollups only after repeated PostgreSQL measurements breach 750 ms. CSV exports are intentionally bounded and are not a bulk warehouse export. A pre-existing psycopg resource warning appeared after the passing durable pipeline suite and should be investigated separately; it did not leave the Docker test stack running.
- Rollback: deploy the previous API/frontend and remove the export links/routes. No PostgreSQL downgrade is needed. If reverting the 0024 portability correction, production PostgreSQL behavior remains equivalent, but local SQLite startup regression returns. Audit records already written remain append-only.
- Next recommended step: deploy reads/exports to one tenant, observe PostgreSQL query latency and export volume, then revisit rollups only if the 750 ms threshold is repeatedly exceeded.

## AI-OPS-03 review - AI Operations navigation and dashboard UI

- Files changed: AI Operations dashboard rendering, processing asset links, cost presentation, responsive styles, focused dashboard/presentation tests, roadmap and this review.
- Migrations added: none.
- Behavior introduced: the existing normal `/ai-operations` application route and sidebar navigation were verified; Overview, Processing and Cost & Usage expose tenant-scoped KPIs, accessible chart/table equivalents, URL-backed filters, pagination, bounded server CSV exports, loading/partial-error/empty/unauthorized states and responsive layouts. Providers and Configuration continue to use the completed AI-OPS-04 implementation. Processing rows now resolve the real asset ID from usage instead of linking an analysis ID. Estimated, provider-reported and reconciled period totals are labelled separately.
- Tests and actual results:
  - `cd apps/client && npm test -- app/ai-operations/AiOperationsPage.test.tsx app/ai-operations/presentation.test.ts`: 2 files / 11 tests passed in 1.08s.
  - `cd apps/client && npm test`: 8 files / 42 tests passed in 1.05s.
  - `cd apps/client && npm run typecheck`: passed.
  - `cd apps/client && npm run build`: passed; Vite 5.4.14 transformed 60 modules.
- Feature flags: none added, enabled or changed. The page consumes authenticated read APIs and does not enable AI, ingestion or Search v2.
- Known risks: the processing table can only link an asset when the bounded usage response contains the job-to-asset association or when the job entity is explicitly an asset; otherwise it deliberately shows `Unavailable` rather than navigating to the wrong record. Provider/model filter options remain bounded to known dashboard data and existing server capabilities/configuration views.
- Rollback: revert the frontend and documentation changes. No migration, API, worker or queued-job rollback is required.
- Next recommended step: smoke-test the responsive dashboard with a production-like tenant data set and operator session during staged rollout.

## AI-OPS-CI-FIX review - migration and durable pipeline regressions

- Initial reproduction, before modifications:
  - Clean Python 3.12.13 snapshot at `422a351`, exact CI discovery command `timeout 10m python -m unittest discover -s tests -v`: first failure was `migrations.test_active_analysis_integrity_migration.ActiveAnalysisIntegrityMigrationTest.test_upgrade_and_step_scoped_downgrade`; migration `0024_ai_operations_configuration.upgrade()` raised `NotImplementedError: No support for ALTER of constraints in SQLite dialect` from `op.create_check_constraint`. Snapshot result: 423 tests, 1 failure, 21 errors, 14 skips in 114.703s.
  - Clean `422a351` pipeline with PostgreSQL 16.4, Elasticsearch 8.15.3 and Python 3.12.13, exact module command `timeout 15m python -m unittest tests.integration.test_pipeline_e2e -v`: first failure was `test_disabled_tenant_is_not_claimed_then_can_resume`; `Settings(...)` raised `ValidationError: GEMINI_MODEL must be in GEMINI_ALLOWED_MODELS`. Result: 6 tests, 6 errors in 0.010s.
- Root cause: two independent regressions. Migration 0024 used direct constraint alteration unsupported by the SQLite development/unit-test path; its nullable/server defaults were valid and PostgreSQL migration succeeded. The durable pipeline fake Gemini fixture was stale after model allowlist and fail-closed cost-rate validation. New processing-policy ORM fields, configuration API policy creation and constructor signatures were not the cause.
- Existing targeted runtime fixes retained: migration 0024 uses Alembic batch operations for portable constraints; the fake pipeline allowlists `fake-gemini-v1` and persists an explicit zero-cost fake rate. No production default or feature flag changed.
- Regression coverage added:
  - migration 0024 now performs an actual SQLite 0023-to-0024-to-0023 round trip, proving existing policy preservation, server defaults, constraints and rollback.
  - durable pipeline fake provider settings/rate are built by tested helpers, proving allowlist and explicit cost configuration without real credentials.
- Validation in required order:
  - migration regression alone: 1 passed in 2.530s.
  - pipeline fixture regression alone: 1 passed in 0.017s.
  - migration containing module: 2 passed in 2.285s.
  - pipeline containing module without services: 1 passed, 6 service-dependent skips in 0.018s.
  - exact clean Python 3.12 CI discovery command: 428 passed, 14 service-dependent skips in 122.431s.
  - pipeline E2E with PostgreSQL 16.4 and Elasticsearch 8.15.3: 7 passed in 3.941s.
  - PostgreSQL empty upgrade, downgrade to `0012_ai_batch_processing`, re-upgrade and repository modules: one Alembic head (`0024_ai_operations_configuration`), 7 passed in 0.359s. A pre-existing psycopg resource warning was emitted after the passing suite.
  - frontend tests: skipped because no frontend file changed.
- Migrations added: none. Migration 0024 behavior is unchanged from the already-corrected HEAD; this step adds explicit regression coverage only.
- Feature flags: none added or enabled. AI, ingestion and Search v2 defaults remain unchanged.
- Rollback: revert the two test refactors and documentation entry. No database or runtime rollback is required.

## AI-OPS-03-COMPLETE review - dashboard interactions and operational controls

- Files changed: existing AI Operations page, request coordinator, AI Operations API client, Providers display, responsive dashboard styles, focused page/provider/refresh tests, roadmap and this review.
- Migrations added: none.
- Behavior introduced: the existing `/ai-operations` page now offers bounded auto-refresh choices Off/15s/30s/60s (default Off), persists that choice with the existing filter/tab URL state, pauses refresh while the document is hidden, aborts superseded/unmounted requests and rejects stale responses. Processing rows expose only backend-eligible retry/cancel actions, require an audited reason and confirmation, distinguish queued cancellation from running cancellation requests, and refresh after acceptance. Budget-blocked remains a distinct KPI; cost labels keep estimated, provider-reported and reconciled values separate. Provider cards now label the maximum grouped percentile as `Highest grouped p95 latency` instead of incorrectly presenting it as a provider-wide p95.
- Accessibility: the tabs use tablist/tab/tabpanel semantics with arrow, Home and End keyboard navigation; refresh/error feedback uses live regions; chart SVGs retain text/table alternatives; statuses include text and accessible labels; job action buttons identify the target job.
- Tests and actual results:
  - `cd apps/client && npm test -- app/ai-operations/AiOperationsPage.test.tsx app/ai-operations/requestCoordinator.test.ts`: 2 files / 20 tests passed in 605ms.
  - `cd apps/client && npm test -- app/ai-operations/ProvidersConfiguration.test.tsx`: 1 file / 6 tests passed in 407ms.
  - `cd apps/client && npm test`: 9 files / 54 tests passed in 671ms.
  - `cd apps/client && npm run typecheck`: first run found four test-only mock typing errors; after the targeted cast matching the existing test convention, the unchanged command passed.
  - `cd apps/client && npm run build`: passed; Vite 5.4.14 transformed 61 modules.
- Feature flags: none added, enabled or changed. Auto-refresh is off by default. The dashboard does not enable AI, ingestion or Search v2.
- Security: no provider keys, OAuth tokens, signed URLs, raw job payloads or stack traces were added to the UI or action request bodies. Job errors remain stable codes and all mutations go through authenticated existing admin endpoints.
- Known risks: provider-level p95 cannot be mathematically reconstructed from provider/model/mode percentiles. The UI deliberately presents the available value as the highest grouped p95 until a true provider-wide backend aggregate is added. Action eligibility is mirrored for affordance only; the backend remains authoritative and returns conflicts for races.
- Rollback: revert the frontend, tests and documentation changes. No migration, worker, queue or data rollback is required.
- Next recommended step: staged operator smoke testing of retry/cancel races and hidden-tab refresh behavior with production-like latency; do not add rollups unless measured aggregation thresholds justify AI-OPS-05 work.

## AUTH-02 review - durable tenant membership

- Files changed: canonical tenant/membership ORM models and service, persistent
  session active-tenant pointer and validation, Google/Microsoft development
  bootstrap integration, processing-policy compatibility resolver, explicit
  tenant bootstrap CLI, configuration examples, focused tests and architecture/
  operations documentation. AUTH-01 user/identity changes remain the required
  additive prerequisite in the same worktree.
- Migration added: `0026_tenant_memberships`, chained from
  `0025_application_users`. It creates `tenants`, `tenant_memberships`, required
  foreign keys/uniqueness/indexes and nullable `auth_sessions.active_tenant_id`.
  Downgrade removes only AUTH-02 data/pointer and preserves users, identities,
  encrypted OAuth connections and legacy provider session fields.
- Behavior introduced: effective tenant access requires an active user, active
  tenant and active membership; membership removal is status-preserving;
  explicit tenant selection validates ownership and records a secret-free audit
  event; single-membership sessions may resolve deterministically; actor ID is
  no longer used as tenant ID by the processing-policy compatibility helper.
  Legacy sessions without `user_id` retain a documented compatibility path.
- Bootstrap: automatic personal development tenancy remains default-disabled
  and is rejected in production. `python -m app.operations.auth_cli
  bootstrap-tenant` is confirmation-gated, supports dry-run, is idempotent and
  assigns no administrator role.
- Tests and actual results:
  - focused API/auth/membership/bootstrap/processing modules using the API
    virtual environment: 28 tests passed in 0.449s.
  - migration `0026` SQLite upgrade and step-scoped downgrade: 1 test passed in
    3.20s.
  - Python compilation and `git diff --check`: passed.
  - Alembic heads: exactly one, `0026_tenant_memberships`.
  - PostgreSQL concurrent-membership integration test: present but skipped
    locally because `INTEGRATION_DATABASE_URL` was not configured; CI with
    PostgreSQL must execute it.
- Feature flags: `DEVELOPMENT_PERSONAL_TENANT_ENABLED=false` is the only AUTH-02
  rollout setting; no existing runtime feature was enabled. Production rejects
  this development-only setting when true.
- Known risks: durable roles and permissions are intentionally absent until
  AUTH-03. Routes not yet migrated continue using compatibility authorization,
  while the processing-policy helper now resolves membership tenancy. Real
  PostgreSQL concurrent creation remains pending service-backed CI execution.
- Rollback: stop tenant-aware application traffic, deploy the prior code, then
  downgrade Alembic to `0025_application_users`. Active-tenant selections and
  memberships are removed; legacy provider-scoped sessions/connections remain.
- Next recommended step: run the PostgreSQL integration job, then implement
  AUTH-03 tenant-scoped roles and permissions without changing ordinary routes.

## AUTH-03 review - tenant-scoped roles and permissions

- Files changed: durable permission/role/assignment models, tenant authorization
  service, canonical role seed definitions, explicit seed CLI, Alembic metadata,
  PostgreSQL concurrency coverage, focused unit/migration tests, security/data
  model/operations documentation, roadmap and this review.
- Migration added: `0027_tenant_rbac`, chained from
  `0026_tenant_memberships`. It creates `permissions`, per-tenant `roles`,
  `role_permissions` and `membership_roles`, plus the composite membership key
  required for tenant-compatible assignment foreign keys. Downgrade removes
  only AUTH-03 catalog/assignment data and preserves users, tenants,
  memberships, sessions and OAuth connections.
- Behavior introduced: stable machine-readable permissions; protected viewer,
  operator, tenant_admin and billing_admin roles instantiated per tenant;
  unioned effective permissions across roles; active user/tenant/membership
  enforcement; tenant-safe idempotent assign/remove operations; custom roles;
  protected-role deletion guard; secret-free assignment/role audit events.
  Platform administration is not a role or permission in this tenant RBAC.
- Seed operation: `python -m app.operations.auth_cli seed-rbac --tenant <id>
  --reason <reason> --dry-run|--confirm` is explicit, idempotent and reconciles
  canonical system permissions without assigning any membership.
- Tests and actual results:
  - focused AUTH-03 plus AUTH-01/02 compatibility modules: 39 tests passed in
    0.822s.
  - AUTH-02/03 migration upgrade and step-scoped downgrade: 2 tests passed in
    4.64s.
  - Python compilation and `git diff --check`: passed.
  - Alembic heads: exactly one, `0027_tenant_rbac`.
  - PostgreSQL concurrent membership and role assignment tests: both present
    but skipped locally because `INTEGRATION_DATABASE_URL` was not configured;
    PostgreSQL CI must execute them.
- Feature flags: none added or enabled. Existing routes were intentionally not
  migrated and continue using compatibility authorization until AUTH-04.
- Known risks: real PostgreSQL composite-FK/concurrent assignment execution is
  pending service-backed CI. Role management APIs and final-admin protections
  are intentionally deferred; direct database writes must not bypass the
  service and composite constraints.
- Rollback: stop RBAC-dependent code, deploy the previous release and downgrade
  Alembic to `0026_tenant_memberships`. Role assignments/catalog data are
  removed; application identities and membership history remain.
- Next recommended step: execute PostgreSQL integration CI, then implement
  AUTH-04 central principal and FastAPI permission dependencies without yet
  migrating every protected route.

## AUTH-04 review - central application authorization

- Files changed: central `CurrentPrincipal`/FastAPI permission dependencies,
  safe identity router, durable platform-administrator model/service,
  processing-admin compatibility guard, configuration examples, focused tests,
  migration and security/roadmap documentation.
- Migration added: `0028_central_authorization`, chained from
  `0027_tenant_rbac`. It creates `platform_admin_assignments` with one durable
  assignment per user and explicit active/revoked state. Downgrade removes only
  platform assignments and preserves users, identities, tenants, memberships,
  tenant roles, sessions and OAuth connections.
- Behavior introduced: new protected routes can require an authenticated
  application principal plus one, any or all tenant permissions; platform
  privilege is separate from tenant roles; tenant-scope mismatches have a stable
  error; `/api/v1/auth/identity` exposes only safe identity, tenant, role and
  permission data. Existing admin routes were deliberately not migrated.
- Tests and actual results:
  - `.venv/bin/python -m pytest -q tests/modules/authorization/test_principal.py -x`: 12 passed in 1.16s.
  - `.venv/bin/python -m pytest -q tests/modules/authorization tests/modules/auth_persistence/test_tenant_membership.py tests/modules/processing_policy -x`: 44 passed in 17.09s.
  - `.venv/bin/python -m pytest -q tests/migrations/test_central_authorization_migration.py -x`: 1 passed in 2.60s; upgrade and step-scoped downgrade passed.
- Feature flag: `AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=false` gates
  the deprecated `PROCESSING_POLICY_ADMIN_IDS` bridge. No application feature,
  pipeline, AI or Search v2 flag was enabled.
- Known risks: existing AI Operations, processing-policy and search admin routes
  still use the compatibility dependency until the dedicated route migration.
  Durable platform-admin bootstrap tooling is intentionally deferred.
- Rollback: stop code paths using `CurrentPrincipal`, deploy the previous
  release, then downgrade Alembic to `0027_tenant_rbac`. The identity endpoint
  and central dependencies disappear; tenant RBAC and persistent sessions stay
  intact. Keep the deprecated allowlist flag false unless a bounded migration
  rollback explicitly requires it.
- Next recommended step: integrate OAuth/session creation with the central
  application principal, then migrate protected routes permission-by-permission.

## AUTH-05 review - OAuth application login and tenant sessions

- Files changed: fail-closed admission configuration, shared OAuth application
  login service, Google/Microsoft callback integration, persistent-session
  rotation and legacy cutoff, authenticated active-tenant endpoint,
  configuration examples, focused tests, security/runbook and roadmap docs.
- Migrations added: none. AUTH-05 reuses `users`, `user_identities`, `tenants`,
  `tenant_memberships`, `oauth_connections`, `auth_sessions` and
  `auth_audit_events` from AUTH-01 through AUTH-04. Alembic has exactly one
  head: `0028_central_authorization`.
- Behavior introduced: OAuth identities resolve only by provider plus subject;
  first login follows explicit self-signup/domain/default-tenant policy;
  application sessions persist durable user and active tenant IDs while cloud
  credentials remain separately encrypted; tenant switching validates active
  membership, rotates the session, revokes the old cookie and audits the
  change. Legacy actor-only sessions are rejected outside an explicit bounded
  compatibility deadline. No role is assigned by login.
- Tests and actual results:
  - `apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/auth_persistence apps/api/tests/modules/authorization -x`:
    64 passed in 2.80s.
  - `apps/api/.venv/bin/python -m compileall -q apps/api/app`: passed.
  - `cd apps/api && .venv/bin/python -m alembic heads`: one head,
    `0028_central_authorization`.
  - `git diff --check`: passed; only repository line-ending conversion notices
    were emitted by Git on this Windows/WSL checkout.
- Feature/configuration controls: `AUTH_SELF_SIGNUP_ENABLED=false` by default;
  optional `AUTH_DEFAULT_TENANT_ID`, `AUTH_ALLOWED_EMAIL_DOMAINS` and
  `AUTH_LEGACY_ACTOR_SESSION_COMPAT_UNTIL`. Production self-signup requires an
  explicit default tenant. Admission domains never imply administration.
- Known risks: the rolling-deployment compatibility deadline must be applied
  consistently across replicas. Existing actor-only sessions are revoked when
  no active compatibility window is configured. Tenant/role administration UI
  remains outside AUTH-05.
- Rollback: disable self-signup, deploy AUTH-04 code and revoke any sessions
  admitted under an incorrect policy. No database downgrade is required;
  durable identities, memberships, sessions and audit history remain intact.
- Next recommended step: implement AUTH-06 tenant membership/role APIs using
  the central permission dependencies; do not restore email-only linking or
  automatic administrator grants.

## AUTH-06 review - tenant membership and role administration APIs

- Files changed: tenant access administration domain service and FastAPI
  router, route registration, optional bounded audit reasons in the existing
  RBAC service, focused service/API tests, API/security documentation, roadmap
  and this review.
- Migrations added: none. AUTH-06 reuses `users`, `tenant_memberships`,
  `permissions`, `roles`, `role_permissions`, `membership_roles` and
  `auth_audit_events`. Alembic remains at one head,
  `0028_central_authorization`.
- Behavior introduced: tenant-scoped paginated member/role listing; safe
  invitation of an existing unambiguous application user without pretending
  email was delivered; activate/suspend/restore/remove transitions; role
  assignment/removal; permission listing; custom-role create/update/delete;
  stable domain error codes and bounded secret-free responses.
- Authorization and safety: reads require `tenant_members.read`, membership
  mutations require `tenant_members.manage`, and role mutations require
  `tenant_roles.manage`. Tenant scope remains explicit in every query. Tenant
  locking serializes final-admin checks; only a durable platform admin using an
  explicit override may remove the final active tenant administrator. Actors
  cannot grant permissions they do not hold, protected system roles cannot be
  changed/deleted, and platform administration cannot be tenant-granted.
- Tests and actual results:
  - `apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/authorization/test_admin_service.py apps/api/tests/modules/authorization/test_admin_router.py -x`:
    11 passed in 0.97s.
  - `apps/api/.venv/bin/python -m pytest -q apps/api/tests/modules/authorization apps/api/tests/modules/auth_persistence -x`:
    75 passed in 2.46s.
  - `apps/api/.venv/bin/python -m compileall -q apps/api/app`: passed.
  - `git diff --check`: passed; Git emitted line-ending conversion notices
    only for this Windows/WSL checkout.
  - `cd apps/api && .venv/bin/python -m alembic heads`: one head,
    `0028_central_authorization`.
- Feature flags: none added or enabled. Durable RBAC remains authoritative.
- Known risks: invitation delivery is intentionally absent; invite-by-email
  only resolves an existing unique application user and otherwise requires an
  operator to use a user ID. PostgreSQL enforces the production tenant lock;
  SQLite tests cannot model every concurrent transaction interleaving.
- Rollback: remove the AUTH-06 router registration and deploy the previous
  code. No schema downgrade is needed; membership/role/audit history remains.
- Next recommended step: implement AUTH-07 Access Management frontend against
  these APIs without treating hidden UI actions as authorization.

## AUTH-07 review - Access Management frontend

- Files changed: manual application route/navigation, tenant access API client
  and safe response types, responsive Access Management page, focused frontend
  tests, styles, roadmap and this review.
- Migrations added: none. AUTH-07 consumes the AUTH-05 identity/tenant-switch
  endpoints and AUTH-06 membership/role administration APIs.
- Behavior introduced: `/settings/access` with keyboard-accessible Members,
  Roles and My access tabs; tenant-scoped member filtering/pagination;
  invitation recording, membership transitions and role assignment/removal;
  protected/custom role presentation and custom-role editing; effective-role
  and permission summaries; active-tenant switching through session rotation.
- Authorization and safety: mutation controls are hidden without their exact
  durable permissions, while the backend remains authoritative. Dangerous
  suspend/remove/role-removal actions require confirmation and a bounded
  reason. Stable unauthenticated, permission-denied, no-tenant,
  stale-membership, final-admin and network states are surfaced. Platform
  administration is excluded from tenant role choices and no credentials,
  OAuth tokens, API keys or session IDs are represented by the client types.
- Tests and actual results:
  - `cd apps/client && npm test -- app/access-management/AccessManagementPage.test.tsx`:
    12 passed in 0.66s.
  - `cd apps/client && npm test`: 66 passed across 10 files in 0.76s.
  - `cd apps/client && npm run typecheck`: passed in 2.4s.
  - `cd apps/client && npm run build`: passed; Vite transformed 66 modules and
    produced the production bundle in 0.57s.
  - production bundle secret-identifier scan: passed; no API-key, OAuth-token,
    client-secret or session-ID identifier was found.
  - `git diff --check`: passed.
- Feature flags: none added or enabled. Access is determined by the persistent
  application session, active tenant membership and durable RBAC permissions.
- Known risks: invitation email delivery remains intentionally absent; the UI
  states that invitations are only recorded. The current frontend test
  convention uses SSR/static interaction and mocked fetch contracts rather
  than a browser DOM runner, so backend authorization remains the security
  boundary.
- Rollback: remove the `/settings/access` route/navigation and deploy the
  previous frontend bundle. No database or API rollback is required.
- Next recommended step: AUTH-08 can migrate AI Operations/admin surfaces to
  the same durable permissions without reusing UI visibility as authorization.

## AUTH-08 review - durable RBAC for AI Operations

- Files changed: central permission helpers; AI Operations, AI metadata/batch, processing policy, AI governance, search governance/search and asset-details routes; permission-aware AI Operations/navigation UI; focused tests; security/operations documentation; roadmap and this review.
- Migrations added: none. AUTH-08 reuses users, memberships, roles, permissions, platform-admin assignments and existing append-only audit tables. Alembic has one head: 0028_central_authorization.
- Behavior introduced: AI Operations reads require ai_operations.read; run/force, retry/cancel, provider configuration, budget read/update and emergency stop use their dedicated permissions. Explicit tenant targets are validated against CurrentPrincipal.active_tenant_id, audit actors use the durable user_id, and platform-global runtime, index lifecycle, cost-rate and metrics controls require durable platform administration. Provider pause/resume is intentionally separate from ordinary provider configuration.
- Frontend behavior: AI Operations navigation is shown with ai_operations.read rather than tenant-admin role; signed-in permission failures show a safe 403 state; read-only users keep dashboards while mutation controls are hidden by their exact permissions. Backend authorization remains authoritative.
- Audit behavior: forced single analysis and shadow-policy mutation now append bounded actor/tenant audit records; existing policy, provider, budget, retry/cancel and search activation services retain their append-only audit paths.
- Tests and actual results:
  - cd apps/api && .venv/bin/python -m pytest -q tests/modules/ai_metadata/test_api.py::AssetAnalysisAdminApiTest::test_capabilities_response_is_public_and_tenant_scoped: 1 passed in 1.16s.
  - cd apps/api && .venv/bin/python -m pytest -q tests/modules/search/test_governance.py::SearchGovernanceTest::test_shadow_policy_route_uses_rbac_actor_and_appends_audit: 1 passed in 0.54s.
  - cd apps/api && .venv/bin/python -m pytest -q tests/modules/authorization tests/modules/ai_operations tests/modules/ai_metadata tests/modules/ai_governance tests/modules/processing_policy tests/modules/search tests/modules/asset_details --maxfail=1: 202 passed in 34.39s.
  - cd apps/client && npm test: 68 passed across 10 files in 1.09s.
  - cd apps/client && npm run typecheck: passed.
  - cd apps/client && npm run build: passed; Vite transformed 66 modules and produced the bundle in 0.79s.
  - frontend production bundle credential-name scan: passed.
  - apps/api/.venv/bin/python -m alembic heads: one head, 0028_central_authorization.
- Feature flags: no flag enabled. AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED remains false in settings and both environment examples. PROCESSING_POLICY_ADMIN_IDS is no longer used by normal migrated route authorization.
- Known risks: frontend permission visibility depends on the safe identity endpoint and may briefly hide navigation while loading; direct API enforcement is unaffected. Routes outside the explicitly migrated AUTH-08 scope may still use compatibility authorization until their own migration.
- Rollback: deploy the previous application/UI revision. No database downgrade is required. Keep the compatibility flag disabled unless an explicitly approved emergency migration requires the deprecated adapter.
- Next recommended step: AUTH-09 bootstrap/backfill and final validation; do not infer administration from provider identity or Drive ownership.

## AUTH-09 review - migration tooling and final validation

- Files changed: identity-based migration operations, auth CLI commands,
  production compatibility validation, focused regression tests, local and VPS
  operator documentation, roadmap and this review.
- Migrations added: none. AUTH-09 reuses the AUTH-01 through AUTH-08 schema.
  Alembic has one head: `0028_central_authorization`.
- Behavior introduced:
  - `bootstrap-access` resolves an existing Google/Microsoft identity by
    provider subject, creates/selects one tenant, restores/creates active
    membership, seeds protected RBAC definitions and idempotently assigns
    `tenant_admin`.
  - `grant-platform-admin` is a separate explicit confirmed action.
  - `backfill-legacy-auth` scans OAuth connections in bounded resumable pages,
    creates subject-keyed users/identities without email linking, creates no
    admin privilege, fills nullable session user/tenant references and reports
    bounded non-secret unresolved records.
  - production configuration rejects both the legacy allowlist flag and
    non-empty `PROCESSING_POLICY_ADMIN_IDS`.
- Security: commands never accept or print OAuth credentials. Same-email
  Google and Microsoft identities remain distinct. Drive ownership, domains,
  scopes and provider account types never imply administration. Privilege
  mutations and backfill pages append bounded audit events.

Validation was run in the requested order:

1. `cd apps/api && .venv/bin/python -m unittest tests.modules.auth_persistence.test_identity -v`:
   7 passed in 0.063s.
2. `cd apps/api && .venv/bin/python -m unittest tests.modules.auth_persistence.test_tenant_membership tests.modules.auth_persistence.test_tenant_bootstrap -v`:
   8 passed in 0.075s.
3. `cd apps/api && .venv/bin/python -m unittest tests.modules.authorization.test_service tests.modules.authorization.test_seed_cli tests.operations.test_auth_migration -v`:
   13 passed in 0.479s.
4. `cd apps/api && .venv/bin/python -m unittest tests.modules.authorization.test_principal -v`:
   12 passed in 0.288s.
5. `cd apps/api && .venv/bin/python -m pytest -q tests/modules/auth_persistence/test_login.py tests/modules/auth_persistence/test_api.py -k google`:
   3 passed, 9 deselected in 0.45s.
6. The same OAuth modules with `-k microsoft`: 3 passed, 9 deselected in
   0.42s.
7. `cd apps/api && .venv/bin/python -m unittest tests.modules.authorization.test_admin_service tests.modules.authorization.test_admin_router -v`:
   11 passed in 0.469s.
8. `cd apps/api && .venv/bin/python -m pytest -q tests/modules/ai_operations tests/modules/processing_policy/test_auth.py --maxfail=1`:
   21 passed in 2.26s.
9. `cd apps/client && npm test -- app/access-management/AccessManagementPage.test.tsx`:
   12 passed in 0.612s.
10. AI Operations page/provider frontend authorization tests: 23 passed in
    0.639s.
11. AUTH migration upgrade/downgrade modules: 4 passed in 9.954s.
12. PostgreSQL 16.4 integration with an empty database upgraded to head:
    9 passed in 0.207s. A psycopg ResourceWarning about one test connection
    being garbage-collected open was emitted after success.
13. The first local API discovery run loaded developer `.env` values and
    failed with 1 failure/5 errors because encryption keys and insecure-cookie
    values contaminated isolated configuration fixtures. No code was changed
    to mask it. The clean-CI equivalent temporarily excluded that local file:
    `timeout 10m .venv/bin/python -m unittest discover -s tests -v`:
    final post-fix run passed 506 tests with 16 integration skips in 151.400s.
    A focused regression first exposed SQLite savepoint behavior that could
    escape a dry-run rollback; after the targeted dialect-safe transaction fix,
    the failing backfill test passed alone (1 in 0.041s) and its module passed
    (2 in 0.099s).
14. Durable pipeline against PostgreSQL 16.4 and Elasticsearch 8.15.3:
    7 passed in 2.606s; all providers were fakes.
15. `cd apps/client && npm run typecheck && npm run build`: passed; Vite
    transformed 66 modules and built in 0.622s.
16. `cd apps/client && npm test`: 68 passed across 10 files in 0.675s.
17. Elasticsearch 8.15.3 integration mapping/alias/query/cleanup fixture:
    1 passed in 0.362s.
18. Shell syntax, Python compileall, one-head assertion, fail-closed default
    settings, frontend credential-name scan and `git diff --check`: passed.

- CI parity: the local API environment provides Python 3.10.12; GitHub Actions
  remains pinned to Python 3.12 and Node 22. The exact CI discovery command,
  PostgreSQL, Elasticsearch, pipeline and frontend job components all passed
  locally without production provider credentials.
- Feature/configuration controls: self-signup and the deprecated compatibility
  adapter remain false by default. Production rejects the adapter and any
  legacy admin ID list. No ingestion, AI or Search v2 flag was enabled.
- Deprecation: production disablement is immediate; compatibility migration
  deadline is 2026-08-31. Remove the legacy ID setting after an empty unresolved
  report and two releases without compatibility authorization events.
- Rollback: deploy the previous application release without downgrading
  PostgreSQL; disable self-signup/compatibility, revoke incorrect durable
  assignments, preserve identity/membership/audit history, then rerun the
  idempotent command against the corrected identity and tenant.
- Known risk: local Python differs from CI Python; remote Actions status must be
  confirmed after pushing this commit. AUTH-09 must not be considered remotely
  green until that workflow completes.

## ADMIN-SETUP-SCRIPT review - safe first-administrator bootstrap

- Files changed: `scripts/setup-admin.sh`, its operator example, focused shell
  and AUTH-09 CLI tests, safe identity-listing/preflight/final-verification CLI
  helpers, roadmap and this review.
- Migrations added: none. Authentication models, RBAC rules and application
  authorization behavior are unchanged; Alembic remains at
  `0028_central_authorization`.
- Behavior introduced:
  - user `baonghia` defaults to local setup and user `desify` defaults to
    production; unknown users must select the environment explicitly;
  - local and VPS project/environment/virtualenv paths follow the documented
    defaults and support explicit overrides;
  - fail-closed preflight validates database reachability, exact Alembic head,
    persistent RBAC authentication and disabled production compatibility
    bypasses;
  - identity selection lists only provider, masked email, shortened subject and
    user status, while the authoritative provider subject remains internal;
  - tenant administration always runs a mandatory dry-run and explicit
    confirmation; durable platform administration is a separate optional grant
    with a second confirmation;
  - final verification requires active tenant membership, `tenant_admin`,
    `ai_operations.read` and `tenant_members.manage`, and reports the actual
    durable platform-admin state.
- Security: the script uses `set -Eeuo pipefail`, quotes shell variables,
  installs cleanup/error traps, never accepts or prints tokens/API keys, never
  infers privilege from email/domain, and never changes
  `PROCESSING_POLICY_ADMIN_IDS` or enables compatibility authorization.
- Tests and actual results:
  - first shell test run exposed one fake-runner pattern that confused
    `bootstrap-access` with `verify-bootstrap-access`; the pattern was
    narrowed without changing runtime code;
  - `cd apps/api && .venv/bin/python -m unittest tests.operations.test_auth_cli tests.operations.test_auth_migration tests.operations.test_setup_admin_script -v`:
    12 passed in 1.604s;
  - `bash -n scripts/setup-admin.sh`: passed;
  - Python `py_compile` for the changed CLI and tests: passed.
- Feature flags: none added or enabled. Production continues to reject the
  legacy processing-admin allowlist.
- Known risks: the operator-controlled environment file is sourced by the
  script, matching existing deployment conventions; its ownership and write
  permissions remain an operational security boundary. Identity listing is
  bounded to 500 records.
- Rollback: revert this isolated commit. No database downgrade or authorization
  data mutation is required; any administrator assignment already applied is
  durable and must be explicitly revoked through existing audited services if
  it was not intended.
## DEPLOY-COMMITTED-FRONTEND review

- Files changed: committed Vite build configuration and artifact, safe build
  marker and local release builder, non-root API/worker Dockerfiles, production
  Compose, native Nginx configuration, deploy/rollback/validation scripts,
  production env template, focused deployment tests and operator documentation.
- Migrations added: none. Production schema changes remain explicit one-shot
  Alembic commands and API startup does not auto-migrate.
- Behavior introduced:
  - local user `baonghia` can deterministically test, typecheck, build, scan,
    commit and optionally push `apps/client/dist`;
  - production user `desify` deploys the committed bundle without Node.js;
  - native PostgreSQL is reached from containers through
    `host.docker.internal`, while Compose contains only API, worker and
    Elasticsearch plus an opt-in migration service;
  - backend readiness is required before the frontend symlink is switched;
    frontend releases are atomic and bounded, and rollback never downgrades the
    database or removes PostgreSQL/Elasticsearch data.
- Tests and actual results:
  - `npm ci --no-audit --no-fund && npm test && npm run typecheck && npm run build`:
    68 frontend tests passed; typecheck passed; Vite transformed 66 modules and
    wrote the production bundle and safe marker;
  - `python -m unittest deploy.tests.test_committed_frontend_deployment deploy.tests.test_vps_deployment -v`:
    19 focused deployment/environment tests passed in 0.365s;
  - Docker Compose v2.33.1 production config validation: passed;
  - API and worker Docker image builds using pinned Python 3.12.8 slim:
    passed. The local Docker installation emitted only a legacy-builder/buildx
    availability warning;
  - Bash syntax for all new scripts and `git diff --check`: passed;
  - ShellCheck and native Nginx were unavailable locally; equivalent shell
    syntax and focused static Nginx proxy/SPA/cache tests passed.
- Feature flags: no application feature flag was added or enabled. All existing
  ingestion, AI and Search v2 defaults remain unchanged.
- Security: no frontend source map or secret-like value is committed; API and
  Elasticsearch publish only to loopback; worker is unexposed; production env
  contains placeholders only and legacy/local authorization bypasses remain
  disabled.
- Known risks: native PostgreSQL connectivity, real TLS certificates and
  `nginx -t` require the VPS environment and were intentionally not accessed.
  The fixed Docker bridge subnet must not conflict with an existing VPS network.
- Rollback: run `scripts/rollback-vps.sh --commit PREVIOUS_COMMIT`. It restores
  matching backend/frontend code after health checks, preserves data, and warns
  that schema compatibility must be reviewed because Alembic is not downgraded.

## ADMIN-SETUP local environment fallback fix

- Root cause: local configuration already lives at `apps/api/.env`, while the
  setup wrapper only searched for root `.env.local` and root `.env`.
- Behavior introduced: local automatic resolution now checks root
  `.env.local`, root `.env`, then `apps/api/.env`; explicit `--env-file`
  and all production behavior remain unchanged.
- Security: no environment value is printed or copied, and a missing file still
  fails before database access with the searched locations listed.
- Tests: `cd apps/api && .venv/bin/python -m unittest
  tests.operations.test_setup_admin_script -v`: 9 passed in 1.461s.
  `bash -n scripts/setup-admin.sh`: passed.
- Migration/rollback: no migration or authorization change. Revert this commit
  to restore the previous root-only local lookup.

## Local Google OAuth first-login fix

- Root cause: OAuth token exchange succeeded, but local application admission
  remained fail-closed because both local signup flags were false.
- Local-only action: the ignored apps/api/.env now enables the two development
  settings. Production defaults remain false and production still rejects this bootstrap.
- Behavior: callback admission failures now display specific safe messages;
  .env.example documents the explicit local first-login sequence.
- Tests: 12 backend auth tests and 2 focused frontend tests passed; frontend
  typecheck and production build passed.
- Migrations: none. No committed feature-flag default was enabled.
- Operational step: restart make api, then sign in again.
- Rollback: set both local flags false and revert this UI/message change.

## PROD-DOCKER-01 review

- Files changed: one consolidated backend Dockerfile, production Compose,
  production resource-limit template, deployment migration invocation and
  focused deployment tests. The duplicate API/worker Dockerfiles were removed.
- Migrations added: none. The one-shot `migrate` service runs the existing
  Alembic head and exits; API startup does not run migrations.
- Behavior introduced:
  - API, worker and migrate use the same commit-tagged immutable image;
  - Python is pinned to 3.12.8 slim Bookworm and runtime processes use UID/GID
    10001 with a read-only filesystem, dropped capabilities and SIGTERM/init
    forwarding;
  - native PostgreSQL remains reachable only through
    `host.docker.internal`, while backend Elasticsearch uses
    `http://elasticsearch:9200`;
  - API and Elasticsearch publish only to loopback; PostgreSQL, Nginx and the
    committed frontend remain native/outside Compose;
  - CPU and memory bounds are explicit environment-backed values so operators
    can tune measured VPS limits without editing Compose.
- Tests and actual results:
  - focused deployment tests: 14 passed in 0.056s;
  - Docker Compose configuration rendered successfully with exactly
    `api`, `worker`, `migrate` and `elasticsearch`;
  - the backend image built successfully from the pinned base;
  - container UID/GID verification, API import, worker import and Alembic
    `0028_central_authorization (head)` checks passed;
  - image runtime-context and history scans found no frontend, local database,
    test tree, environment file or secret-bearing build instruction;
  - deployment shell syntax and `git diff --check` passed.
- Feature flags: none added or enabled. AI, ingestion and Search v2 production
  defaults remain unchanged.
- Known risks: the resource defaults are starting bounds and must be measured
  against the real VPS workload. The image build downloads pinned Python
  dependencies unless the Docker cache is warm.
- Rollback: revert this isolated commit and rebuild the prior backend images.
  Do not downgrade PostgreSQL; data and the Elasticsearch volume are unchanged.
- Next recommended step: validate the image against the protected VPS
  environment and native PostgreSQL during the normal deployment preflight;
  this step intentionally performs no deployment.


## PROD-FE-02 committed frontend release

- Files changed: committed frontend release metadata and artifacts, deterministic
  build-info generation, release scanning and focused deployment tests, CI
  parity validation, Nginx build-info cache handling and operator documentation.
- Migrations added: none. Backend runtime and domain behavior are unchanged.
- Behavior introduced:
  - `apps/client/dist` remains the only tracked distribution directory and is
    normalized to LF for byte-for-byte CI comparison; source maps remain ignored;
  - production API calls remain relative `/api` URLs;
  - the existing non-root release script runs lockfile-based `npm ci`, frontend
    tests, typecheck, production build, artifact verification, safe
    `build-info.json` generation, secret/local-URL scanning and size reporting;
  - commit and push remain explicit options, and no push occurs without
    `--push`;
  - CI rebuilds with the committed build commit/timestamp and fails if the
    regenerated distribution differs or contains untracked files.
- Tests and actual results:
  - `./scripts/build-frontend-release.sh --allow-dirty`: 70 frontend tests
    passed across 11 files; typecheck passed; Vite transformed 66 modules;
    artifact verification and security scan passed;
  - `apps/api/.venv/bin/python -m unittest deploy.tests.test_committed_frontend_deployment deploy.tests.test_vps_deployment -v`:
    20 focused tests passed;
  - deterministic rebuild SHA-256 comparison: passed;
  - Bash syntax, CI YAML parsing and `git diff --check`: passed.
- Feature flags: none added or enabled.
- Security: the committed release is rejected if it contains localhost,
  loopback, database URLs, Gemini/OpenAI keys, Google/Microsoft secrets, private
  keys, OAuth/access/refresh tokens or source maps. `build-info.json` contains
  only commit, UTC timestamp and frontend version.
- Known risks: byte-for-byte output depends on the pinned lockfile and CI Node
  runtime; CI is the authoritative parity check.
- Rollback: revert this isolated commit and restore the previous committed
  distribution/build marker. No database rollback is involved.
- Next recommended step: let the frontend CI job rebuild and verify the committed
  artifacts before deployment.


## PROD-VPS-03 hybrid VPS deployment

- Files changed: hardened native Nginx site, shared production validation
  helpers, Docker-backed deploy/rollback/validation scripts, focused deployment
  tests and the VPS production runbook.
- Migrations added: none. The deployment runs only the existing forward
  `alembic upgrade head` migration service.
- Behavior introduced:
  - Nginx serves the committed `apps/client/dist` release through the atomic
    `/var/www/creative-asset-manager/current` symlink, supports SPA deep links,
    proxies API/health endpoints only to loopback and applies URI-specific cache
    policy without dropping inherited security headers;
  - deployment refuses root and unauthorized users, accepts only an explicit
    user override, updates Git by fast-forward or an explicit commit, validates
    the production environment without printing values, builds the commit-tagged
    backend image, validates native PostgreSQL, migrates, starts services and
    requires live/ready/version-matched API and worker liveness before switching
    the frontend;
  - public smoke tests cover the home route, AI Operations, Access Management
    and health/version endpoints; five frontend releases are retained;
  - rollback starts the backend image matching the retained frontend commit,
    verifies health/version before an atomic switch, restores the prior symlink
    on smoke-test failure and never downgrades PostgreSQL.
- Tests and actual results:
  - `python -m unittest deploy.tests.test_prod_vps_03 deploy.tests.test_committed_frontend_deployment deploy.tests.test_vps_deployment -v`:
    25 focused tests passed;
  - Bash syntax for all deployment scripts: passed;
  - `validate-production.sh --config-only` with an explicit local user override and a mode-0600 non-secret environment: passed;
  - Docker Compose production configuration: passed with the production env
    path explicitly overridden to the non-secret template;
  - Nginx 1.27.5 syntax validation with a temporary certificate and committed
    frontend mount: passed;
  - `git diff --check`: passed.
- Feature flags: none added or enabled. Existing ingestion, AI and Search v2
  defaults remain unchanged.
- Security: client-supplied forwarded address/protocol values are replaced by
  Nginx-derived values; dotfiles and environment/key artifacts are denied;
  security headers, bounded proxy timeouts and no-cache entry documents are
  configured. Scripts never echo the production environment.
- Known risks: real TLS paths, sudo policy, native PostgreSQL `pg_hba.conf`,
  firewall rules and public DNS can only be verified on the VPS. Older backend
  releases must remain compatible with the current forward-only schema.
- Rollback: run `sudo -u desify ./scripts/rollback-vps.sh --commit COMMIT`.
  This changes application/frontend versions while preserving PostgreSQL and
  Elasticsearch data; it never invokes Alembic downgrade.
- Next recommended step: run `validate-production.sh --preflight` on the VPS
  during a reviewed maintenance window before the first deployment.


## PROD-GATE-04 production release gate

- CI regression investigated first: GitHub Actions workflow run #150, job
  `API, worker and provider unit tests`, failed in
  `WorkerRuntimeTest.test_heartbeat_extends_the_lease`. The assertion compared
  a lease timestamp observed inside the handler with wall-clock time captured
  only after `run_once()` returned; the 41 ms teardown delay made the timing
  assertion flaky. Migration 0024 and provider configuration defaults were not
  involved. The regression test now compares the lease with the timestamp
  captured at the same observation point.
- Files changed: production release-gate workflow, fail-closed gate runner,
  release checklist, focused gate/regression tests, compatible Nginx HTTP/2
  syntax, and roadmap/review documentation.
- Migrations added: none. Domain and application runtime behavior are unchanged.
- Behavior introduced:
  - the release gate is downstream of every existing frontend, API/worker,
    PostgreSQL, Elasticsearch and durable-pipeline CI group and fails when any
    prerequisite is not successful;
  - one immutable non-root backend image is built and validated; production
    Compose topology, native-PostgreSQL connectivity, migration service,
    Alembic head, persistent auth/RBAC schema, fail-closed configuration,
    API health/version, worker SIGTERM/restart and Nginx syntax are checked;
  - committed frontend artifacts are verified and scanned without echoing a
    rejected secret-like match; image-history scanning is quiet;
  - the gate uses generated test-only values and never requires production
    credentials or live Google, Microsoft, Gemini or OpenAI calls.
- Tests and actual results:
  - failing test alone: `cd apps/api && .venv/bin/python -m unittest tests.modules.processing.test_runtime.WorkerRuntimeTest.test_heartbeat_extends_the_lease -v`: 1 passed in 1.482 s;
  - containing module: `cd apps/api && .venv/bin/python -m unittest tests.modules.processing.test_runtime -v`: 16 passed in 13.400 s;
  - exact clean Python 3.12 CI discovery command: `python -m unittest discover -s tests -v`: 516 tests passed, 16 skipped, in 166.621 s;
  - focused gate plus regression: `cd apps/api && .venv/bin/python -m unittest tests.test_production_release_gate tests.modules.processing.test_runtime.WorkerRuntimeTest.test_heartbeat_extends_the_lease -v`: 4 passed in 1.483 s;
  - Actionlint 1.7.7 workflow syntax: passed;
  - ShellCheck 0.10.0 (with the known dynamic-source SC1091 excluded) and Bash syntax: passed;
  - Nginx 1.24-alpine syntax with a temporary certificate and committed dist: passed;
  - positive production configuration validation: passed; loopback PostgreSQL negative case was rejected as required;
  - `git diff --check`: passed.
- Feature flags: none enabled. Pipeline, ingestion, AI and Search v2 remain
  false in the gate environment; the global AI emergency stop remains active.
- Security: production SQLite, loopback container database URLs, development
  personal tenants and legacy processing-admin compatibility are rejected.
  API keys, OAuth credentials and signed URLs are not included in artifacts.
- Known risk/status: the local host has legacy Docker Compose 2.0.1, which
  cannot parse the current production Compose schema/default resource values.
  The complete container gate therefore remains pending on the current GitHub
  runner. PROD-GATE-04 and production readiness stay unchecked until the new
  commit's remote `Production release gate` is green.
- Rollback: revert this isolated commit. No database downgrade is required or
  performed. Application rollback remains forward-schema compatible and never
  downgrades PostgreSQL automatically.
- Release procedure: follow
  `docs/operations/PRODUCTION_RELEASE_CHECKLIST.md`; deploy only the exact SHA
  with a green, non-skipped gate.

## Secure OAuth JIT provisioning

- Files changed: shared application-login/identity services, auth settings,
  focused auth and PostgreSQL concurrency tests, environment examples, security
  and operator documentation.
- Migrations added: none. Existing provider-subject, tenant membership and
  membership-role uniqueness constraints remain the final concurrency guards.
- Behavior introduced:
  - approved Google and Microsoft first logins atomically create one
    application user/identity, active default-tenant membership, configured
    least-privilege role and bounded audit records;
  - `AUTH_SELF_SIGNUP_DEFAULT_ROLE` defaults to `viewer`; missing/inactive
    roles fail closed, and `tenant_admin`/`platform_admin` are rejected;
  - repeated and concurrent provider-subject logins reuse the existing user,
    membership and role assignment without duplicate provisioning audits;
  - admission-domain policy is shared by Google and Microsoft.
- Tests and actual results:
  - `python -m unittest discover -s tests/modules/auth_persistence -v`:
    46 passed in 0.758 s;
  - `python -m unittest discover -s tests/modules/authorization -v`:
    34 passed in 1.313 s;
  - PostgreSQL concurrent-login test: discovered locally but skipped because
    `INTEGRATION_DATABASE_URL` is not configured; CI executes it with PostgreSQL;
  - full `python -m unittest discover -s tests -v`: 522 tests ran in
    161.654 s, with 17 integration skips and 7 pre-existing environment-leak
    failures in auth/HTTP configuration tests. JIT/auth tests passed.
- Feature flags: self-signup remains false by default; no administrator or
  platform privilege is enabled.
- Known risk: the target tenant must exist, be active and have the configured
  role seeded before production self-signup is enabled.
- Rollback: revert this isolated change and remove
  `AUTH_SELF_SIGNUP_DEFAULT_ROLE` from deployment configuration. No database
  downgrade is required; existing users, memberships, roles and audit history
  remain authoritative.


## Search V3 lifecycle and retrieval (2026-07-26)

- Added a default-disabled `SEARCH_V3_ENABLED` flag and versioned Elasticsearch v3 aliases/mapping for visible text and type-ahead fields.
- Completed AI analysis now enqueues the durable projection job before indexing; direct reindex/rebuild actions retain direct analysis identity.
- Worker bootstrap configures the Elasticsearch index provider when Elasticsearch is configured.
- Search query clauses add visible-text phrase priority, prefix/type-ahead and fuzzy matching while retaining mandatory tenant filtering.
- Verified: focused backend search/projection/bootstrap tests (28), frontend typecheck, frontend tests (77), and production frontend build.

## Search V3 coverage in AI Operations

- Added a tenant-scoped, database-only coverage summary to AI Operations. It reports completed, current-projection, database-indexed, missing/stale, backlog, failed and latest Elasticsearch-verification discrepancy counts without scanning Elasticsearch on dashboard refresh.
- `POST /api/v1/admin/ai-operations/coverage/audit` requires `search.rebuild`, records a bounded audit event and only verifies Elasticsearch when explicitly requested.
- `POST /api/v1/admin/ai-operations/coverage/repair` requires `search.rebuild` plus explicit confirmation; it reuses the existing idempotent repair service, creates only projection/index jobs, and never calls AI.
- The Overview now includes an accessible Search Coverage card with audited timestamp, discrepancy warning, queued/running repair progress and a minimum 10-second repair-refresh interval.
- Tests: backend coverage/API focused suite 23 passed; frontend AI Operations page tests 18 passed; TypeScript typecheck and production build passed.
- No migration, feature-flag, AI/provider, or repair-rule change was made.


## Search V3 completed metadata retrieval (2026-07-27)

- Fixed the Search V3 document schema mismatch that prevented worker indexing: source ID, visible text and type-ahead text are now persisted in the Elasticsearch document.
- The shared document builder now safely traverses dynamic metadata, preserves short visible-text tokens such as `BSN` and `RN`, and creates normalized `search_text`/type-ahead values without mutating the original AI metadata.
- Both worker indexing and maintenance reindexing use the shared builder. A one-asset bulk upsert requests Elasticsearch `refresh=wait_for`, so a completed single-asset index is searchable before the job returns.
- Existing durable handoff and retry behavior remains: completed analysis queues projection build, projection queues index, and failed index jobs can be retried without rerunning AI. Existing `search:repair-coverage` remains the tenant-scoped backfill command.
- Tests: `python -m unittest tests.modules.search.test_index_types tests.infrastructure.search.test_elasticsearch_v2 tests.modules.ai_metadata.test_projection tests.modules.search.test_operations_service tests.modules.search.test_active_analysis_service tests.modules.pipeline.test_state_and_repository` — 33 passed. `python -m unittest tests.integration.test_elasticsearch` — 2 skipped because Elasticsearch is not configured locally. `python -m unittest tests.integration.test_pipeline_e2e` — 1 passed, 6 skipped because integration services are not configured locally.
- No migration or feature-flag change. Rollback: revert this isolated commit; Elasticsearch documents can be rebuilt with `python -m app.operations.search_cli search:repair-coverage --tenant-id <tenant-id> --apply --repair-projections --repair-indexes --verify-elasticsearch`.

- Additional validation: frontend `npm test` — 79 passed; `npm run typecheck` and `npm run build` passed. Full backend discovery ran 612 tests in 179.252 s but remains red on 5 failures and 4 errors outside this change (ambient development/production config and pre-existing AI batch/status expectations); all Search V3-focused tests passed.

## Backend unittest environment isolation (2026-07-27)

- Added a test-only bootstrap that is activated only while `unittest` is the
  executing command. It clears application configuration inherited from the
  shell, repository dotenv files and deployment environments, then forces
  `APP_ENV=test`, `ENVIRONMENT=test` and `TESTING=true` before application
  settings or `app.main` are imported.
- `load_development_environment()` now refuses dotenv loading when `TESTING`
  is true. Normal development and production loading behavior is unchanged.
  The bootstrap resets cached settings and the application-specific environment
  before and after each `unittest.TestCase`.
- Focused verification: `python -m unittest discover -s tests -p
  test_environment.py -v` — 5 passed; `python -m unittest discover -s tests
  -p test_http_config.py -v` — 4 passed. The previous production HTTP errors
  caused by leaked `DEVELOPMENT_PERSONAL_TENANT_ENABLED` are fixed.
- Full verification: `python -m unittest discover -s tests -v` — 614 tests
  ran in 183.974 s; 18 integration tests skipped cleanly because
  `INTEGRATION_DATABASE_URL` and/or `ELASTICSEARCH_URL` were not explicitly
  configured. No real provider credentials or endpoints were used.
- Remaining legitimate regressions, unrelated to environment leakage:
  - `modules.ai_batch.test_service.AiBatchServiceTest.test_submit_poll_out_of_order_partial_import_and_usage_idempotency`: expected two index jobs, received zero.
  - `modules.assets.test_processing_status.AssetProcessingStatusTest.test_status_projection_covers_lifecycle_and_precedence`: expected `metadata_ready`, received `search_pending`.
  These preserve the current full-suite red state and require separate
  pipeline/status behavior fixes; this change does not hide or skip them.
- No migration or feature flag changes. Rollback: revert the isolated test
  bootstrap commit.

## File activity and dashboard control UX (2026-07-27)

- The file inspector now renders a readable processing timeline: source import,
  managed storage, AI analysis, search-projection and search-index events each
  have a plain-language outcome, category and state indicator. Failed jobs show
  a safe human-readable error and point operators to the retry action.
- AI Operations now groups Auto-refresh, update status and the back-to-assets
  action into one consistent, responsive header control area.
- Tests: `npm test` — 80 passed; `npm run typecheck` passed; `npm run build`
  passed. No API, authorization or background-processing behavior changed.

## AI Operations dashboard response mapping regression (2026-07-27)

- Fixed the frontend dashboard response mapping: summary, today, month, daily, provider, failure, job and usage responses now remain aligned with their request order. The old one-offset mapping assigned the daily response to month, causing a runtime error when the Overview read estimated_cost_micros.
- Overview cost cards now also safely tolerate an incomplete cost object while a partial dashboard response is loading.
- Regression coverage verifies all nine dashboard responses map to the correct fields.
- Tests: focused AI Operations test (20 passed); full frontend suite (81 passed); npm run typecheck and production build passed.
- No backend API, migration, feature flag or authorization behavior changed. Rollback: revert this isolated frontend commit.
