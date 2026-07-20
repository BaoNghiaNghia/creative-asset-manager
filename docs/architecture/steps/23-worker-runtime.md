# Step 23 — Worker runtime and graceful draining

## Scope

Step 23 turns the existing database job broker into an executable opt-in worker
without wiring the later end-to-end ingestion or AI pipeline.

## Boundaries

- `processing_jobs` and its lease remain authoritative.
- The handler contract and registry are provider-neutral.
- All known job types are explicit registry entries.
- Unwired operations return terminal `unsupported_handler`; they never fake
  completion.
- `PROCESSING_JOBS_ENABLED` remains false by default.

## Runtime

The single-concurrency runtime claims atomically through
`ProcessingRepository`, executes a typed handler, renews the lease from an
independent session, and maps explicit handler outcomes to existing transitions.
A loss or uncertainty of lease ownership cancels cooperative work and prevents
completion.

SIGTERM/SIGINT transitions readiness to false and stops claims. The active job
keeps its heartbeat during the drain grace period. After timeout, cooperative
work is released; unresponsive work is left processing and recoverable after
lease expiry.

Health HTTP exposes `/live`, `/ready`, and `/health`. JSON logs contain
worker/job/tenant/entity/attempt/lease/outcome context but never payloads or
credentials.

## Migration and rollback

No migration is added. Roll back by setting `PROCESSING_JOBS_ENABLED=false`,
sending SIGTERM, waiting for active work or leases to settle, and reverting Step
23. Pending and retry jobs remain in PostgreSQL and the API is unchanged.
