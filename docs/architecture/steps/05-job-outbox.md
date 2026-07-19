# Step 05 — Processing jobs and transactional outbox

## Scope

Step 05 adds durable database infrastructure only. It does not start a worker,
change ingestion, expose a route, or publish an event. `PROCESSING_JOBS_ENABLED`
remains false.

## Processing jobs

`processing_jobs` stores one durable unit of work. The initial job types are:

- `source_asset_download`
- `asset_store`
- `asset_analyze`
- `search_projection_build`
- `asset_index`
- `metadata_sidecar_export`

The unique `(tenant_id, idempotency_key)` constraint is the final guard against
duplicate enqueue. A retry updates the existing row and never creates another
job, so handlers can use the stable job ID/idempotency key for their own
side-effect idempotency.

## Claiming and leases

PostgreSQL claims the next due job using `FOR UPDATE SKIP LOCKED`. SQLite, used
for local development and tests, uses one conditional `UPDATE ... RETURNING`
statement. Both paths order work by priority and availability.

Claiming increments `attempt_count` and sets `claimed_by`, `claimed_at`, and
`lease_expires_at`. A different worker may claim the same row after the lease
expires. Workers should renew leases for long-running handlers. An expired
final attempt becomes terminal `failed` rather than being claimed again.

Handler failures transition the same row to `retry` with bounded exponential
backoff, or to terminal `failed` when `max_attempts` is reached. Empty queues
sleep for a configured interval; the infrastructure has no hot polling loop.

## Transactional outbox

`outbox_events` is written through the same SQLAlchemy session as the domain
mutation. Repository methods flush but do not commit, so the caller owns the
transaction and both changes commit or roll back together. Event creation is
idempotent through unique `(tenant_id, idempotency_key)`.

Outbox publication also uses a lease and atomic claim. Marking an event
`published` is idempotent. A future opt-in publisher may dispatch these rows;
Step 05 starts none.

## Rollback

Keep `PROCESSING_JOBS_ENABLED=false`, stop any manually started consumer,
export pending/failed records if required, and downgrade revision
`0002_processing_jobs_outbox` to `0001_asset_registry`. Only
`processing_jobs` and `outbox_events` are dropped.
