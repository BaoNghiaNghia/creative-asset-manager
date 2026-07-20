# Asset Registry Data Model

## Authority and identity

PostgreSQL is the intended authoritative database. SQLite remains a local/test compatibility database during the staged rollout.

Source identity:

`tenant_id + external_source_id + external_asset_id`

Content identity:

`tenant_id + SHA-256 content_hash`

Names, paths, URLs, provider checksums, provider versions, and timestamps are mutable observations. Provider checksum/version may avoid an unnecessary download only after authoritative content hashing has already linked that source asset.

## Tables

### `external_sources`

One tenant-owned provider connection or external API source.

- Unique `(tenant_id, source_key)`.
- Stores source type, display name, and provider/source configuration metadata.
- Composite `(tenant_id, id)` uniqueness supports tenant-enforcing foreign keys.

### `source_assets`

One observed item in one external source.

- Unique `(tenant_id, external_source_id, external_asset_id)`.
- Stores filename, MIME type, size, source timestamps, provider checksum/version, and source metadata.
- Separate hashed-provider markers record which checksum/version was last confirmed by authoritative SHA-256.
- `deleted_at` implements source-side soft deletion.
- Soft deletion never deletes the canonical `assets` row.

### `assets`

Canonical tenant-scoped content identity.

- Unique `(tenant_id, content_hash)`.
- `content_hash` is SHA-256 of original file bytes.
- `analysis_image_hash` is optional and separate; it must never replace original content identity.
- Same hash is valid in different tenants.

### `asset_source_links`

Connects canonical content to a provider/source observation.

- Unique `(asset_id, source_asset_id)` prevents duplicate links.
- Composite tenant foreign keys prevent cross-tenant linking.
- Different source assets may link to the same canonical asset.

### `source_sync_cursors`

Stores named incremental synchronization cursors for one external source.

- Unique `(tenant_id, external_source_id, cursor_key)`.
- Cursor persistence exists in Step 03; polling/delta processing starts in Step 06.

## Deduplication flow

1. Resolve the tenant-owned `source_asset`.
2. Reuse an existing link only when a stored provider checksum/version proves the source observation is unchanged.
3. Otherwise stream original bytes through SHA-256 without buffering the whole file.
4. Look up `(tenant_id, content_hash)`.
5. Reuse or create the canonical asset.
6. Link the source asset idempotently.
7. On concurrent create conflict, recover from the database unique constraint, fetch the winner, and link the source.

Thumbnails are not hashed for original content identity. Managed content persistence is deferred to Step 08.

## Migration

Revision `0001_asset_registry` creates the five tables and required constraints/indexes. Its downgrade drops only these tables in dependency order. After registry flags are enabled, export or back up registry data before downgrade.

## Processing infrastructure

### `processing_jobs`

Durable tenant-scoped work with stable job identity.

- Unique `(tenant_id, idempotency_key)` prevents duplicate enqueue.
- Status is `pending`, `processing`, `retry`, `completed`, or `failed`.
- Claims store worker ownership and a renewable lease.
- Retries update the same row with bounded exponential backoff.
- Available, lease, and tenant/entity indexes support worker scans and operations.

### `outbox_events`

Transactional records for domain events that must be published later.

- Unique `(tenant_id, idempotency_key)` prevents duplicate events.
- The repository flushes without committing so a domain mutation and its outbox event share one transaction.
- Publication uses atomic claim, lease recovery, retry state, and idempotent completion.

Revision `0002_processing_jobs_outbox` creates both tables. Its downgrade drops only these Step 05 tables after consumers are stopped and pending records are exported.

## Managed storage

### asset_storage_objects

One row tracks one canonical asset stored by one managed provider.

- Unique tenant, asset, and storage provider enforces upload idempotency.
- Remote file identity is unique per storage provider.
- Status supports pending, uploading, stored, retry, and failed.
- Remote file ID, folder ID, web URL, attempts, backoff and errors are durable.

Revision 0003_managed_asset_storage creates this table. Database rollback does
not delete provider-side objects.

## Dynamic AI metadata

### metadata_profiles

Tenant-scoped, versioned analysis instructions.

- Unique tenant, profile name, and profile version.
- Stores prompt template, optional JSON Schema, search configuration, and active state.
- Schema and search configuration use JSONB on PostgreSQL.

### asset_ai_analyses

Append-oriented analysis history for a canonical content version.

- Tenant-enforcing foreign keys link analyses to assets and profiles.
- Captures content hash, profile/version, prompt/pipeline versions, provider/model and state.
- metadata_json is the validated dynamic JSONB document.
- raw_response_json is separate and optional by configuration.
- search_projection is separately versioned JSONB.
- A partial unique index prevents duplicate non-forced runs for the same inputs.
- Forced analysis creates additional rows and preserves completed history.

Revision 0004_dynamic_ai_metadata creates both tables. Its downgrade removes
only Step 09 tables; legacy asset_metadata and tags are unaffected.

## External ingestion API

### external_api_credentials

Tenant/source-scoped credentials for an `external_api` source.

- Only a SHA-256 API-key fingerprint and short display prefix are stored.
- Composite tenant/source foreign keys prevent cross-tenant authorization.
- Credentials support revocation, activation, and a positive per-minute limit.

### external_api_rate_limits

Atomic fixed-window request counters keyed by credential and UTC minute.

- Unique credential/window identity is the concurrency boundary.
- PostgreSQL and SQLite use conflict-aware atomic increments.

### asset_ingestions

One accepted supplier request and its canonical body hash.

- Unique `(tenant_id, external_source_id, idempotency_key)` is the final
  idempotency and concurrency guard.
- Composite foreign keys guarantee the credential, source, and ingestion share
  the same tenant/source boundary.
- Aggregate status is derived from persisted item statuses.

### asset_ingestion_items

Per-item request data, durable processing status, and job/source-asset links.

- External asset IDs are unique within an ingestion.
- Position is unique within an ingestion for deterministic response ordering.
- Source-asset links are tenant-enforcing; source deletion is restricted while
  referenced by an ingestion audit record.

Revision `0005_external_ingestions` creates all four tables. Its downgrade
removes only Step 18 records; queued processing jobs can retain inert JSON
references and should be drained or archived before downgrade.

## Metadata sidecar exports

### metadata_sidecar_exports

One durable export state per analysis and storage provider.

- Unique `(analysis_id, storage_provider)` prevents duplicate logical exports.
- Document hash detects a changed PostgreSQL-derived sidecar document.
- Status, attempts, next retry, errors, remote file/folder identity, storage key,
  and web URL are independent from immutable completed analysis history.
- Tenant-safe asset foreign keys prevent cross-tenant asset association.

Revision `0006_metadata_sidecars` creates this table. Downgrade removes local
export state only and never deletes remote Google Drive JSON files.

## Search maintenance operations

### search_operation_runs

Tenant-scoped durable execution state for projection rebuild, reindex, or the
combined operation. It stores immutable selection intent, target versions,
physical index, dry-run/cancellation state, keyset cursor, progress metrics,
and terminal errors.

### search_operation_items

One per-analysis outcome within a run. Unique `(run_id, analysis_id)` makes
reruns idempotent and provides the durable failed-item set for `--only-failed`.
The composite tenant/run foreign key prevents cross-tenant item association.

Revision `0007_search_operations` creates both tables. Downgrade removes only
operational checkpoint/audit state and does not mutate projections or
Elasticsearch.

## Durable asset pipelines

### asset_pipelines

One tenant-scoped, observable state machine per source asset or external
ingestion item. Unique tenant/origin identity prevents a competing flow, while
unique tenant/correlation identity propagates one trace across all jobs.

The record stores source/asset/analysis references, content identity, projection
and indexed checksums/versions, current stage, stage-specific error details and
operator status data. It never stores signed URLs, credentials or file bytes.

Revision `0009_durable_asset_pipeline` creates this table. Its downgrade removes
only pipeline state; authoritative asset, metadata and processing records remain.


## Tenant processing rollout policies

### tenant_processing_policies

The authoritative tenant gate for the asynchronous pipeline. Every stage is
opt-in and defaults disabled. Global environment feature flags are emergency
upper bounds and cannot be overridden by these rows. Pause metadata is retained
for operations and running counters enforce database-backed tenant/category
concurrency across worker processes.

### tenant_provider_policies

Generic `(tenant_id, provider_key, provider_scope)` policy for source, storage,
AI and search providers. Missing rows inherit the enabled tenant stage; explicit
rows can disable/pause a provider and impose an active-job limit without adding
provider-specific columns.

### processing_policy_audits

Append-only policy-change audit containing actor, tenant, action, reason, before
and after JSON documents, provider identity where applicable, and timestamp.
Credentials and authentication tokens are never copied into audit documents.

### processing_jobs policy fields

`provider_key`, `provider_scope`, and `concurrency_accounted` allow the atomic
claim transaction to pre-filter provider eligibility and reserve/release durable
concurrency capacity. Revision `0010_tenant_processing_policies` creates this
state. Before downgrade, globally disable and drain workers. The downgrade
removes policy/audit data and claim metadata but preserves queued jobs.

## Step 27 AI governance and pilot evaluation

Migration `0011_ai_governance_pilot` adds:

- `ai_cost_rates`: immutable provider/model/effective-date rates.
- `tenant_ai_budget_policies`: tenant daily, monthly and pilot limits with UTC
  period boundaries and defer/reject behavior.
- `ai_budget_accounts`: PostgreSQL-authoritative actual and reserved integer
  micro-cost totals by tenant and period.
- `ai_budget_reservations`: idempotent provider-operation reservations.
- `ai_usage_records`: one idempotent, secret-free usage record per provider
  operation/attempt.
- `ai_budget_events`: warning, denial and operator audit events.
- `ai_pilot_runs` and `ai_pilot_items`: durable selection, enqueue,
  cancellation/resume and reporting state.

All tenant-owned pilot relationships use tenant-qualified lookup/constraints.
`asset_ai_analyses.status` additionally permits `budget_blocked`. Cost rates
are currency units per provider unit; all persisted totals ending in
`_micros` are 64-bit integer millionths. Downgrade to `0010` removes only
Step 27 governance/pilot data and restores the previous analysis status check.


## Step 28 durable AI batch processing

### ai_batch_jobs

One tenant-scoped orchestration record per logical provider submission. A unique
tenant/submission key protects retry idempotency and a unique provider/batch ID
protects recovered external submissions. The record stores compatibility
identity, provider state, bounded input checksum/size, polling and import
attempts, resumable result cursor, aggregate counts, cancellation, cost/usage,
errors and timestamps.

### ai_batch_items

One stable custom item ID per analysis within a batch. Tenant-qualified batch and
asset foreign keys prevent cross-tenant association. Unique tenant/analysis
membership prevents an analysis from joining multiple batches. Each item stores
attempt/result state, provider item identity, budget reservation/operation,
usage/cost, retry classification and timestamps.

Revision `0012_ai_batch_processing` creates both tables. Downgrade removes only
batch orchestration records; assets, analysis history, metadata, projections,
usage and budget accounting remain authoritative.


## Step 30 persistent OAuth and sessions

### oauth_connections

Tenant/provider/account-unique connection identity with AES-GCM encrypted access
and refresh tokens, expiry/scopes/type, encryption key version, bounded provider
metadata, connection status, refresh errors and a leased refresh claim.

### auth_sessions

Shared server-side sessions keyed by a SHA-256 digest of the opaque cookie ID.
Each row is tenant/provider/connection scoped, fixed-expiry and revocable. User
session JSON is bounded and never contains provider tokens.

### oauth_transactions

Hashed OAuth state with encrypted PKCE verifier, provider/browser binding,
redirect intent, short expiry and atomic one-time consumption.

### auth_audit_events

Secret-free events for connection creation/reconnect/refresh, reconnect-required,
key rotation and session revocation. Revision 0013 adds all four tables. Its
downgrade removes persistent auth state only after login is disabled and users
are informed that reconnection will be required.


## Step 32 reconciliation generations and retention

Revision `0014_reconciliation_retention` adds `source_sync_runs`, with one
tenant/source generation, durable page checkpoint, counts, terminal timestamps
and structured errors. `source_assets.last_seen_generation` and
`last_seen_at` make the final missing-item sweep set-based and bounded by
provider page size. A partial unique active-run index prevents two running full
generations for one source.

`asset_ingestion_items` gains versioned encrypted URL ciphertext, expiry,
consumption and redaction timestamps. Plaintext is nullable and new requests do
not persist plaintext URLs. `retention_cleanup_runs` stores tenant/policy
scope, record types, dry-run state, cursor, count metrics, cancellation and
checkpoint version. Cleanup preserves assets, content hashes, source identity,
metadata, projections and audits.
