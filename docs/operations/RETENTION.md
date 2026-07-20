# Retention and reconciliation operations

Step 32 adds generation-based full source reconciliation and durable retention
cleanup. PostgreSQL remains authoritative.

## Rollout

1. Apply migration `0014_reconciliation_retention`.
2. Deploy with `RETENTION_CLEANUP_ENABLED=false`.
3. Configure `SENSITIVE_URL_ENCRYPTION_KEYS` and its active version before
   enabling the external ingestion API. Keys use the same versioned,
   URL-safe-base64 32-byte format as OAuth encryption keys, but must be a
   separate key ring.
4. Validate one full reconciliation for a staging source. A failed, cancelled,
   timed-out or lease-lost enumeration must remain failed/incomplete and must
   not sweep missing records.
5. Enable `PROCESSING_JOBS_ENABLED` and `RETENTION_CLEANUP_ENABLED` for one
   tenant whose processing policy is enabled. The existing worker queue and
   tenant claim policy are used; no second scheduler is required.

## Generation reconciliation

One running full run per tenant/source is enforced by a partial unique index.
Overlapping requests coalesce onto that run. Each committed page advances the
checkpoint and stamps `source_assets.last_seen_generation`. A retry reopens
the failed run at its checkpoint. Only a successfully exhausted enumeration
performs the set-based missing-item sweep. Incremental discoveries during a
running full traversal inherit that generation; explicit incremental deletes
remain deleted.

Monitor run status, page/item/job counts and structured error code. Never
manually mark a run completed. To abandon a run, set it cancelled through the
operator path before starting a new generation.

## Sensitive URL persistence inventory

- Incoming request bodies exist only during validation and canonical hashing;
  persisted request history omits `download_url`.
- Ingestion item URLs are the sole unavoidable persistent copy and are stored
  as tenant/item-bound ciphertext with expiry/consumption markers.
- Source metadata is copied through recursive URL sanitization; stable paths
  may remain, while credentials, query strings and fragments do not.
- Processing jobs contain stable ingestion/source record IDs only.
- Durable job, pipeline, storage, AI, search and sidecar errors pass through
  query-string redaction.
- Worker structured logs redact URLs in messages and exception text.
- Policy/auth audit events never receive URL bodies or provider credentials.

## Retention policies

Safe defaults are configured centrally:

- ingestion URLs: 24 hours;
- completed ingestion request payloads: 30 days;
- raw AI responses: 7 days;
- completed job payloads: 30 days;
- failed/dead-letter details: 30 days;
- rate-limit buckets: 2 hours;
- published outbox events: 30 days;
- temporary search/export operation items: 7 days;
- completed/failed source sync runs: 30 days.

Expired sessions and OAuth states use their authoritative expiry timestamps.
OAuth state is platform-scoped; tenant cleanup cannot delete another tenant's
state. Asset rows, content hashes, source links, metadata documents, projections,
storage references and audit tables are never cleanup targets.

Cleanup is page-bounded, checkpointed and idempotent. Use dry-run first, a
tenant filter, selected record types, an optional age override and a conservative maximum row count.
Cancellation is checked between pages. Worker lease heartbeats and normal
expired-lease recovery cover long runs. Logs contain record types and counts,
never removed payloads or URLs.

## URL key rotation and incident response

New signed URLs are AES-256-GCM encrypted with tenant/item-bound associated
data. Processing jobs contain ingestion IDs only and resolve the URL just before
download. Retain old key versions until every URL using them is consumed,
expired and redacted.

For suspected URL/key exposure:

1. Disable `EXTERNAL_INGESTION_API_ENABLED`.
2. Pause affected tenant processing.
3. Rotate the active sensitive URL key.
4. Run ingestion URL cleanup for the affected tenant with an immediate cutoff.
5. Inspect count-only worker logs and audit records; do not export raw job or
   dead-letter payloads.

## Rollback

Disable retention cleanup and drain workers. Downgrade to revision 0013 only
after reconciliation jobs are stopped. The downgrade removes run/checkpoint
history and encrypted URL columns; redacted/encrypted-only legacy URL rows
receive the non-sensitive `https://redacted.invalid/` tombstone because a
schema migration does not have application encryption keys.
