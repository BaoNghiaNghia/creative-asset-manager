# External API architecture

## Authentication and authorization

External suppliers use high-entropy bearer API keys. PostgreSQL stores only a
SHA-256 fingerprint and a short non-secret prefix. Each credential belongs to
one tenant and one `external_api` source, so a credential cannot submit to or
read another source. API keys are provisioned through the repository/admin
boundary; Step 18 does not expose key-management endpoints.

## Asynchronous ingestion

`POST /api/v1/asset-ingestions` validates and persists one request and its
items, then creates durable `source_asset_download` jobs in the same database
transaction. The HTTP request never downloads, stores, hashes, analyzes, or
indexes asset bytes.

The caller supplies `Idempotency-Key`. Its scope is tenant plus external source.
A canonical JSON representation is SHA-256 hashed. The same key and hash returns
the existing ingestion; a different hash returns HTTP 409. A database unique
constraint is the final concurrency guard.

## Limits and status

Requests are limited to 1 MiB and 1,000 unique external asset IDs. API-key rate
limits use database-backed fixed one-minute windows. Status and item endpoints
are tenant/source scoped and never reveal the existence of another tenant's
records.
## HTTP contract

All endpoints require `Authorization: Bearer <api-key>`. The create endpoint
also requires a valid `Idempotency-Key` header.

```http
POST /api/v1/asset-ingestions
Content-Type: application/json
Authorization: Bearer <api-key>
Idempotency-Key: supplier-page-001
```

```json
{
  "source_id": "c34637f5-45b2-4a6e-8630-a6d721d8d417",
  "items": [
    {
      "external_asset_id": "supplier-cat-001",
      "download_url": "https://cdn.example.com/cat.jpg",
      "checksum": "sha256:abc",
      "filename": "cat.jpg",
      "modified_at": "2026-07-19T08:30:00+07:00"
    }
  ]
}
```

A newly accepted or idempotently reused request returns HTTP 202:

```json
{
  "ingestion_id": "ing_123",
  "status": "accepted",
  "received": 1
}
```

`GET /api/v1/asset-ingestions/{id}` returns aggregate queued, processing,
completed, and failed counts. `GET /api/v1/asset-ingestions/{id}/items` returns
ordered item status with `limit` and `offset` pagination. Inaccessible IDs return
HTTP 404; an idempotency-key body conflict returns HTTP 409; rate limiting
returns HTTP 429 with `Retry-After`.

## AI analysis orchestration API

`POST /api/v1/admin/asset-analyses/bulk` accepts unique tenant-owned asset
IDs, an allowlisted provider/model and an explicit single or batch mode. It
requires `Idempotency-Key`; the same canonical body reuses the durable request,
while a changed body returns HTTP 409. Payload bytes and item count are bounded
by centralized settings.

Bulk requests use partial acceptance. Invalid, cross-tenant, unavailable, or
budget-preflight-failed items are recorded without rolling back valid items.
Single mode creates one `asset_analyze` job per accepted analysis. Batch mode
creates one `ai_batch_prepare` job containing explicit compatible analysis IDs
and never creates `asset_analyze`. Explicit batches bypass the scheduler's
minimum-age coalescing delay. A one-item batch remains a batch and returns a
delayed-completion warning.

`GET /api/v1/admin/asset-analyses/requests/{id}` returns request, analysis,
batch, queue, running, completion and failure state plus per-item results.
Provider batch IDs are omitted unless a privileged administrator explicitly
requests them. `POST .../{id}/cancel` records actor and reason, stops queued
prepare/single work and uses the existing provider cancellation path for a
submitted batch. Cancellation never invokes analysis synchronously.
