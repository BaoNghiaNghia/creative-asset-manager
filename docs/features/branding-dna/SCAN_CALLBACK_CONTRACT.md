# BD-SCAN-CALLBACKS-01 — Authenticated, lease-fenced worker feedback

Implementation: 2026-10-18. **Backend/SDK contract only; dispatch remains disabled.**

## Current implemented flow

```
Tenant admin -> POST shop/snapshot/scan-tasks -> status=staged
Worker -> POST /workers/{agent}/lease -> { job:null, scan_enabled:false }
Future audited dispatcher (NOT implemented) -> scoped, live server-issued lease
  -> POST /workers/{agent}/scan-tasks/{task}/checkpoint (monotonic progress)
  -> POST /workers/{agent}/scan-tasks/{task}/failure (bounded retry or block)
```

The new callbacks are real, authenticated HTTP endpoints in
`apps/api/app/modules/branding_dna/scan_callback_router.py`. Their only
acceptable inputs are an opaque UUID lease identifier, bounded integer lease
generation and either a bounded integer progress sequence or a fixed safe
error classification. There are **no URLs, images, product facts, arbitrary
metadata, browser commands or instructions** in the contract.

The future agent-side request helper is
`apps/branding-dna-worker/src/scan_lease_protocol.mjs`. It validates HTTPS
CAM origin, exact UUIDs, field-only packets, response status and no-redirect
fetch. The live `heartbeat.mjs` and CAM-paired `agent_cam.mjs` deliberately
**do not import or call it**. No browser job is started by installing this code.

## API contract

All endpoints require a separately enrolled Branding DNA worker's
`Authorization: Bearer <worker token>`, not the user session or RRUGC Scout
pairing credential. Tenant is always derived from the authenticated worker.
`task_id`, `lease_id` are UUIDs; `lease_generation` is integer 1..1,000,000.

| Endpoint | Additional body | Success | Failure |
|---|---|---|---|
| `POST /api/v1/branding-dna/workers/{agent_id}/scan-tasks/{task_id}/checkpoint` | `checkpoint_seq` integer 0..1,000,000 | `accepted:true, scan_enabled:false, task_id, checkpoint_seq, lease_generation` | 401 invalid/revoked token, 409 stale/revoked/expired lease, 422 schema |
| `POST /api/v1/branding-dna/workers/{agent_id}/scan-tasks/{task_id}/failure` | `error_code` fixed enum | `accepted:true, scan_enabled:false, task_id, status, retry_after` | 401, 409 or 422 as above |

Allowed `error_code` values: `temporary_network_error`,
`rate_limited`, `source_unavailable`, `parser_changed`,
`manual_check_required`. `parser_changed` and
`manual_check_required` block automatically; temporary errors retry
within existing max attempts and capped backoff. This is **not** evidence
acknowledgement or proof of gallery completeness.

Only a task in **leased** state with the matching worker ID, tenant, lease ID,
lease generation and non-expired deadline is allowed to advance. A revoked
agent, a shop changed to public_observation or a stale lease fail closed.
Repeated checkpoint at exactly the current sequence returns the same ACK
without extending the deadline; decreased sequence fails 409.

**BD-SCAN-CALLBACKS-02A amendment:** After migration `0165_bdna_scan_failure_receipts`,
a repeated `failure` with the same tenant, task, lease generation, lease ID,
agent and error code returns the original terminal status and retry timestamp
with `replayed:true`, without mutating the current/new lease. A conflicting
receipt, revoked token or unrecognized/stale lease still fails closed.
The separate metadata-only SQLite `scan_failure_outbox.mjs` persists pending
failure callbacks until an exact server ACK (`replayed:boolean`) is received;
HTTP 401/409/network faults retain local state. It is not connected to the
live worker, does not upload evidence, and cannot activate a scan. See
[durable failure receipts](./SCAN_FAILURE_RECEIPTS.md).

## Risk and gate state

- **BD-SCAN-CALLBACKS-01 originally had no migration.** BD-SCAN-CALLBACKS-02A adds an append-only receipt table via migration `0165_bdna_scan_failure_receipts`. This must pass database backup/restore and additive schema gates before release.
- **No new dependency** (FastAPI/SQLAlchemy/Pydantic, Node built-ins).
- **No configuration, secret or token rotation** changes.
- **No public staged→pending transition** or worker job claim on a real route.
- `/workers/{agent_id}/lease` is still `job:null`, `scan_enabled:false`.
- No marketplace API/browser network traffic or media writes in new code.
- Cannot mark listing verified, claim rights, generate Brand DNA or
  perform source deletion.
- Future enabling requires per-platform legal/shop authorization, bounded
  allowlisted task envelope, authenticated result provenance and idempotent
  outbox receipts, dedicated Windows QA, tenant/policy kill switch and
  audited production backup/deploy gates.

## QA and rollback

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna -q
cd ../branding-dna-worker
npm test
```

Tests cover HTTP schema, worker/tenant mismatch, revoked credentials,
expired/fenced leases, decreasing/replayed checkpoint, manual blocks,
safe retry and worker refusal of unapproved payloads. These are mock-source
and test-only pending-task activation fixtures, **not** actual marketplace
pilot evidence.

Rollback executable code by reverting the callback router/service and worker helper; keep the additive `bc_scan_failure_receipts` table/data intact during production rollback. Automatic destructive downgrade is denied. If necessary disable the worker via revocation, and keep dispatch off.
