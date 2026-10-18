# BD-SCAN-CALLBACKS-02A — Durable failure receipts (dispatch OFF)

Implemented: 2026-10-18. **Source + test only, not a live marketplace crawler.**

## Dataflow and trust

```
Future authorized lease (NONE exposed by CAM today)
  -> future Windows worker records terminal failure locally in separate SQLite WAL
  -> bearer-scoped POST /workers/{agent}/scan-tasks/{task}/failure
  -> CAM transaction locks the tenant's task and stores its terminal failure
     + immutable per-lease ACK receipt in PostgreSQL
  <- accepted:true, status:retry|blocked, replayed:false|true
  -> local worker deletes only the exactly acknowledged outbox row
```

This protects against an HTTP 200 response lost between a committed CAM
transaction and a worker's receipt. Retransmission of the **exact** tenant,
task, generation, lease UUID, worker and code is an idempotent read of
`bc_scan_failure_receipts`, even if the same task has a newer lease.
Neither retransmission nor duplicate local enqueue changes attempts, backoff
or active lease. Conflicting error codes, UUIDs, worker IDs or other-tenant
attempts return 409. Revoked worker credentials return 401 before reading
the receipt.

## Durable persistence

Migration `0165_bdna_scan_failure_receipts` creates an additive, constrained
table with composite primary key `(tenant_id, task_id, lease_generation)`,
composite tenant/task foreign key referencing a newly unique indexed
`bc_scan_tasks(tenant_id, id)`, error enum enforced by service, terminal status,
server-produced retry timestamp, original worker and lease UUID. No images,
source URLs, body payload, tokens or product facts are stored. The same DB
transaction updates `bc_scan_tasks` and creates the receipt. PostgreSQL row
locking serializes terminal callbacks; a competing insert conflict returns
409 and is safe to retry. Receipts persist through new leases. Automatic
downgrade is deliberately blocked to avoid losing delivery proof.

The optional worker helper
`apps/branding-dna-worker/src/scan_failure_outbox.mjs` writes an agent-
namespaced local SQLite WAL (`synchronous=FULL`) with at most 500 metadata-
only failures, idempotent `task_id + lease_generation`, no arbitrary file
paths or URL fields, no auto-closing of failed records. It validates CAM's
strict returned status before deleting an ACKed row. A 401/409, network error,
unexpected body or missing replay indicator retains the event in SQLite.

## Critical safety boundaries

- CAM's worker `/lease` **still returns `job:null`, `scan_enabled:false`**.
- There is **no** production `staged -> pending` transition and no automatic
  call into `claim_next_task`.
- The outbox/helper are **not imported by** `heartbeat.mjs`,
  `agent_cam.mjs` or the live Windows Scout Manager UI.
- No marketplace source request, CAPTCHA handling, product/page data, image
  upload, source approval, ownership certification or automatic Brand DNA
  generation occurs in this feature.
- This covers **failure acknowledgements only**; verified source results,
  authorized-source provenance, shop-specific rights, document citations and
  complete data-result outbox are separate work packages.
- A replay is an ACK of a committed historical failure, not permission to
  continue scanning that shop. Initial new callbacks still require a live,
  correctly fenced lease and an enabled worker + authorized/owned shop.
- A `409` is never interpreted as proof of successful processing.

## QA, release and rollback

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna -q
.venv/bin/python -m alembic heads
cd ../branding-dna-worker
npm test
```

Mock tests cover retry-after equality, ACK loss/replay across a second lease,
conflicting errors and identities, revoked worker, tenant isolation and
deadline fencing; worker tests cover SQLite durability across restart, no
network/invalid ACK deletion, exact reply matching, unknown fields and symlink
rejection. Migration DDL is inspected under a disposable SQLite context and
the ordinary downgrade is tested to refuse data removal. This is not proof
of a PostgreSQL production migration, restored backup or Windows/browser QA.

**Deploy gate:** the new `0165` schema must first be migrated under a
reviewed database backup + restore check, additive-schema preflight and
release script; otherwise the new failure endpoint will fail on receipt
writes. Do not deploy a new API alone against an old schema. Current
dispatch must remain OFF even after migration. Source-only merge is safe;
production availability is not inferred from passing mocks.

**Rollback:** stop rollout and revert API/worker source to the last release,
retaining `bc_scan_failure_receipts` and all earlier tenant tasks.
Do not run destructive downgrade in production; revocation of affected worker
tokens is available if credentials are compromised. No new external packages,
credentials or environment secrets.
