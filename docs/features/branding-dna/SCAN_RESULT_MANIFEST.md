# BD-SCAN-CALLBACKS-02B.2c-1 — Metadata result manifest preflight

Implemented 2026-10-18. **Read-only; no result ingestion, receipt, media download or
marketplace scanner dispatch.** This prepares a narrowly typed handoff between a
future authorized browser adapter and CAM without inventing source trust.

## Data contract

`POST /api/v1/branding-dna/shops/{shop_id}/scan-results/preview`
requires `ai_provider.configure`; the tenant comes only from the authenticated
principal.

```json
{
  "task_id": "12345678-1111-4111-8111-123456789012",
  "manifest": {
    "listing_key": "Listing_001",
    "observed_revision": "revision-2",
    "captured_at": "2026-10-18T04:00:00Z",
    "page_count": 1,
    "images": [{
      "sequence_index": 0,
      "asset_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "source_url_sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
      "role": "hero",
      "rights_state": "unknown"
    }]
  }
}
```

The server resolves the **staged** `verify_listing_evidence` task and its
original imported snapshot, scoped by tenant/shop/task/snapshot. The candidate
listing key must exactly match the snapshot listing key. The observed revision
may differ so future agents can *report* changes, but this endpoint does not
write a new revision or mark an imported snapshot verified.

**Bounds and validation**
- Exactly 1–5 pages and 1–20 images, contiguous 0-based source order;
  hashes must be exactly 64 lowercase hex characters.
- No raw URLs, media bytes, credentials, source markup, freeform parser output,
  inference claims, verified rights, executable commands or arbitrary fields.
- Capture time requires explicit timezone, within 30 days before now or
  5 minutes after; normalize to UTC before canonical hashing. JSON RFC3339
  timestamps are tested through an actual HTTP request.
- Roles: hero/detail/lifestyle/ugc/information/variation/unknown. All rights
  states remain `unknown`; no page or image can self-assert provenance.
- Existing task must be staged. Cancelled/leased/completed and wrong-tenant,
  wrong-shop or missing-snapshot attempts fail closed.

The preview returns a deterministic SHA256 digest bound to the tenant,
shop, task, original snapshot ID, original snapshot content hash, and canonical
candidate metadata. **This only identifies bytes, not that a platform
produced the information.** It does not sign a message or prove any grant.

Response includes `manifest_sha256`, gallery/page counts and static bounds,
`evidence_state:"imported_unverified"`, `rights_state:"unknown"`,
`provenance_verified:false`, `marketplace_authorization_verified:false`,
`persisted:false`, `receipt_issued:false`, `job:null`,
`scan_enabled:false`, `dispatchable:false` and explicit unmet blockers.
It never echoes hashes or submitted listing content.

## Worker-side preparation

`apps/branding-dna-worker/src/scan_result_manifest.mjs` validates and
normalizes the same metadata-only envelope, refuses extra raw URL/byte/secret
fields, preserves source image ordering and rejects forged rights/statuses.
It is **not imported** from the live paired Scout Manager, heartbeat,
`agent_cam.mjs`, or browser pilot. It makes no HTTP requests.

## Release status and next work

- No schema migration, token, dependency, environment variable, live job
  delivery, result outbox/ACK, store navigation or production deploy here.
- Before any real scan: independently verify shop-specific marketplace grant
  and policy, media usage rights, signed and revocable server job envelope,
  execution-time URL/robots policy, worker and tenant kill switches.
- Later result ingest must have a durable, transactionally issued ACK,
  immutable source/revision provenance, exact lease fencing, safe replay,
  worker SQLite outbox and reviewer approval. The preview digest must **never**
  be treated as a receipt or an authorization.
- Full CI and Windows Browser QA need independent passing evidence; production
  database backup/restore and canary remain mandatory. Source-only rollback
  can remove this preview route and isolated worker helper without data loss.

## Tests

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_result_contract.py -q
.venv/bin/python -m pytest tests/modules/branding_dna -q
cd ../branding-dna-worker
npm test
```

Tests cover deterministic identity-bound digest, changed gallery hashes,
strict metadata-only limits, timezone normalization, invalid/expired/future
timestamps, tenant/shop/task isolation, rights-smuggling, lifecycle safety,
and live HTTP JSON schema handling. No actual Etsy/Amazon traffic.
