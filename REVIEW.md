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
