# BD-SCAN-CALLBACKS-02B.2c-2A — Durable result outbox + strict future ACK

Implemented on 2026-10-18 on feature PR #24. **This is a worker-side
component, not an active scan result ingestion pipeline.** The server has
**no** `POST /workers/{agent_id}/scan-tasks/{task_id}/result` route, so a
running CAM instance must return 404 and the SQLite event stays queued.
Do not mistake the existing `/scan-results/preview` for a receipt: that
API returns `persisted:false,receipt_issued:false` by design.

## Scope

`apps/branding-dna-worker/src/scan_result_outbox.mjs` stores only:
- validated UUIDs: task, lease, shop, original snapshot, client receipt;
- exact integer lease generation;
- canonical JSON manifest containing bounded listing key, observed revision,
  captured-at timestamp, page count and **hashes/roles only** for 1–20 ordered
  gallery records, all media rights `unknown`;
- SHA256 of the canonical local payload, used to detect corruption and
  match a future server ACK, **not** a source authenticity signature.

There are **no** raw marketplace URLs, image bytes, browser HTML, arbitrary
metadata, secrets or token values in the database. Worker manifest validation
rejects forged rights and unknown fields. The SQLite database uses WAL mode,
`synchronous=FULL`, immediate write transactions, per-agent storage,
bounded 200 pending events, and a payload size ceiling of 8192 bytes.
Duplicate `(task_id,lease_generation)` with exactly the same immutable
payload returns a replay indicator without creating a second row; a changed
lease/shop/snapshot/content conflicts, never silently overwrites.
The queue is not pruned on process restart. A full queue raises an error
instead of deleting older entries.

Default storage:
- Windows: `%LOCALAPPDATA%/CreativeAssetManager/BrandingDNA/<agent_id>/scan-result-outbox.sqlite`
- Linux: `$XDG_STATE_HOME/CreativeAssetManager/BrandingDNA/<agent_id>/scan-result-outbox.sqlite`,
  falling back to `~/.local/state` when XDG_STATE_HOME is missing.

The future-only helper `flushScanResultsOnce` attempts a POST to:
`/api/v1/branding-dna/workers/<agent_id>/scan-tasks/<task_id>/result`
with bearer identity, JSON body, 15-second timeout, no redirects, and a
single batch capped at 10. **It is deliberately not imported from the
running heartbeat, configured pilot, or Scout Manager Windows entrypoint.**
No production result POST was attempted during tests.

## Deletion requirements (future ACK)

An event is deleted in a transaction **only after** a hypothetical server
response has all of the following exact fields and values:
- `accepted:true, persisted:true, receipt_issued:true` and a UUID
  `receipt_id`;
- same client receipt ID, exact payload SHA256, task, lease, generation,
  shop and snapshot;
- `status:"quarantined_unverified", provenance_verified:false,
  marketplace_authorization_verified:false,scan_enabled:false`;
- `replayed` must be a real boolean, not a string or number.

An absent/malformed/partial ACK, server 404/409/401/403/5xx, network error,
or local data corruption never removes a pending record. No result can
automatically complete a task or promote Brand DNA data. This ACK is only
a planned protocol **and not cryptographic evidence of source authenticity**.
Until the backend route exists and independently verifies a genuine signed,
fenced lease and marketplace rights, there will be no real success ACKs.

## Safety gates before a server result route

1. Provider-granted shop-level permission verified independently, with
   revocation and current policy/robots constraints. An internal RAG
   `human_review_current` or shop `access_mode` is insufficient.
2. Server-minted, tamper-evident bounded job envelope and live `/lease`
   authorization, exact worker/shop/tenant binding, generation fencing and
   execution-time policy/kill-switch check. No arbitrary URLs.
3. Additive server receipt schema with transactionally durable idempotent ACK
   and **quarantined_unverified** immutable result record, cross-tenant
   foreign keys, replay tests and explicit backup/restore gate.
4. Import/media rights review, independent Windows Browser QA, CI, staging
   canary and reversible code deploy. Never destructively downgrade audit
   history on production.

## Tests

```bash
cd apps/branding-dna-worker
node --test tests/scan_result_outbox.test.mjs
npm test
```

The tests simulate network transport with an injected requester (no
marketplace/browser activity). They cover forced local restart, exact replay,
conflicting lease/content, capacity, on-disk corruption, symlink checks,
wrong agent config, forged ACK, digest-only preview response, lost ACK,
401/403/404/409/503, data retention and exact ACK deletion. SQLite `node:sqlite`
is experimental on current Node 22 and should be verified on the actual
Windows build/runtime before any pilot.

No migration, frontend change, new third-party package, or production
service change belongs to this iteration. Source-only rollback is safe as
long as existing outbox files are preserved until legitimately reconciled.
