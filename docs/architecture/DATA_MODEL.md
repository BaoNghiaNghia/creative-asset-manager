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
