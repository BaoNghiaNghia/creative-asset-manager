# Step 04 — Content Hashing and Deduplication

## Objective

Calculate authoritative SHA-256 identity while streaming original bytes and converge concurrent tenant-scoped ingestion on one canonical asset.

## Flow

`source item → provider checksum/version optimization → original download stream → streaming SHA-256 → tenant/hash lookup → reuse or create asset → idempotent source link`

## Rules

- Never load the complete source file into memory for hashing.
- Provider checksum/version is only an optimization after authoritative content identity exists.
- Never hash a thumbnail as original identity.
- Same bytes and tenant reuse an asset regardless of filename or provider.
- Same filename with different bytes creates different assets.
- Same hash in different tenants creates separate tenant assets.
- Database unique `(tenant_id, content_hash)` is the final concurrency guard.
- A unique conflict is recovered by fetching the winning asset and continuing the link.
- Duplicate source-to-asset links remain idempotent.

## Feature flag

The application service requires an explicit enabled value. No current runtime composition calls it, and `CONTENT_DEDUP_ENABLED` remains false by default.

## Deferred work

- Secure external download policy: Step 07.
- Managed original storage: Step 08.
- `analysis_image_hash` generation: later image-analysis work.
