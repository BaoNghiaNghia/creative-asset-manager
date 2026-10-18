# BD-SCAN-CALLBACKS-02B.2c-2B.1 — Server quarantined result receipt (internal only)

Implemented on the Branding DNA draft feature branch on 2026-10-18.
**There is no HTTP POST result ACK route and no marketplace scan
activation.** Existing `/workers/{agent_id}/lease` still always returns
`job:null,scan_enabled:false`. A real Windows agent cannot invoke the
quarantined result service.

## Internal storage and replay

`apps/api/app/modules/branding_dna/scan_result_receipt_service.py`
offers **only an internal function** `persist_quarantined_future_result`
for controlled tests and a possible future authorized adapter. It is NOT
included in any CAM FastAPI router and must not be exposed as a public
endpoint before a separate, independently verified marketplace-specific
authorization/signed job-envelope mechanism exists.

The service requires:
- an existing tenant-fenced task with matching shop, snapshot,
  `verify_listing_evidence` kind, **live server-owned lease ID**,
  positive lease generation and enabled matched worker identity;
- matching listing key from the original snapshot and nonexpired capture
  timestamp, original shop access declaration, exact client payload
  SHA256 of the bounded canonical Node worker JSON manifest;
- a manifest with 1–5 pages and 1–20 contiguous gallery entries
  containing SHA256 hashes, roles and `rights_state:unknown`. No raw
  media, URLs, tokens, freeform browser output or result confidence.

The service serializes by locked task row before reading existing
receipts. One transaction commits:
1. An immutable `bc_scan_result_receipts` row whose status is always
   `quarantined_unverified`, including original snapshot content SHA256,
   tenant/shop/task/agent/lease/generation, client receipt ID, canonical
   metadata JSON, manifest SHA256 and client payload SHA256.
2. Changing the leased task to `blocked` with
   `manual_check_required` and no active lease/retry timer, for
   **manual provenance review**. The original listing snapshot is
   **never** marked verified or overwritten.

The ACK packet uses the exact future Node result-delivery schema:
`accepted:true, persisted:true, receipt_issued:true`, persisted
`receipt_id`, original client receipt/digest/IDs/lease generation,
`status:quarantined_unverified`, and
`provenance_verified:false`, `marketplace_authorization_verified:false`,
`scan_enabled:false`.

Duplicate identical submissions for the same tenant/task/generation
return the original receipt with `replayed:true` even after the task
becomes blocked, so a future worker can recover from a lost HTTP ACK.
Conflicting client IDs, digests, lease/generation/worker or source
identity fail closed. Disabled worker tokens cannot replay an ACK.
An existing terminal failure receipt for the same generation blocks
result persistence.

**Critical distinction:** this ACK proves only CAM durably stored
unverified metadata; it is NOT a platform signature, source-accuracy
attestation, proof of shop authorization or a grant to reuse media.

## Migration / protection

Alembic migration `0167_bdna_scan_result_receipts` is additive:
- Composite FK `(tenant_id,task_id)` → scan task.
- Composite FK `(tenant_id,shop_id,snapshot_id)` → original snapshot.
- Unique `(tenant_id,task_id,lease_generation)` and
  `(tenant_id,client_receipt_id)` for idempotency and conflict detection.
- Check constraints for generation, page/gallery bounds and mandatory
  `quarantined_unverified` status; index per tenant/shop/received time.

**Production downgrade is denied.** Dropping immutable ACK receipts
requires the dedicated test-only env flag and disposable test DB name.
GitHub CI (Postgres 12.22 / 16.4) was updated to perform a disposable
0167 rollback before existing guarded 0166/0165 rollback and re-upgrade.
This migration has **not** been run on production and must not be run
without a verified backup, restore rehearsal and explicit release gate.

## Tests and remaining gates

Local tests include lease expiration/fencing, tenant mismatch,
revoked worker, failed-terminal conflict, duplicate ACK replay,
manifest-integrity mismatch, quarantined-only receipt status, no
snapshot mutation and disposable SQL FK/uniqueness/downgrade checks.

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_result_receipts.py -q
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_result_receipt_migration.py -q
.venv/bin/python -m alembic heads
```

Before a real browser run, independently verify Etsy/Amazon shop-specific
automation permission, per-shop media rights, signed bounded envelopes,
agent/shop binding, kill switch, Windows end-to-end browser validation,
CI green across required jobs and production database backup/restore.
Do not connect this function to any worker route or treat a user
`access_mode:authorized` selection as proof of provider approval.

Rollback: revert code only; retain the append-only receipt table and
rows. No existing Scout, Inventory or CAM production jobs are touched
by this feature branch.
