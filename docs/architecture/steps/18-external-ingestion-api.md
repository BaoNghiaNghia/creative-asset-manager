# Step 18 — Asynchronous external ingestion API

Step 18 adds authenticated create/status/item endpoints behind
`EXTERNAL_INGESTION_API_ENABLED`. Credentials are tenant/source scoped and are
stored only as SHA-256 fingerprints. Rate limits use atomic database counters.

Accepted requests persist `asset_ingestions` and `asset_ingestion_items` and
enqueue one durable `source_asset_download` job per item. Idempotency uses a
canonical validated request hash plus a tenant/source/key database constraint.

No request handler downloads an external URL, uploads managed storage, invokes
AI, or indexes Elasticsearch. Item workers and public credential-management
operations remain later operational work.
