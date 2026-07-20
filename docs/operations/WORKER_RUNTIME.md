# Worker runtime operations

Step 23 adds a real, opt-in database worker. PostgreSQL/SQLite processing jobs
remain the broker; no external queue is introduced.

## Start

Install the API dependencies first, then run from the repository root:

```bash
apps/api/.venv/bin/python apps/worker/main.py
```

The worker probes the database before becoming ready, initializes the source,
storage, and AI provider boundaries, builds the typed registry, starts health
HTTP, and then enters the claim loop. Production handlers are registered only
when their complete dependencies and idempotency boundaries exist. The current
Step 23 composition intentionally registers explicit `unsupported_handler`
handlers for pipeline stages that are not yet wired; these fail terminally and
never report false success.

Processing remains disabled unless:

```bash
PROCESSING_JOBS_ENABLED=true apps/api/.venv/bin/python apps/worker/main.py
```

## Configuration

| Variable | Default | Meaning |
| --- | --- | --- |
| `PROCESSING_JOBS_ENABLED` | `false` | Allows this process to claim jobs |
| `WORKER_ID` | hostname and PID | Lease owner identity |
| `WORKER_LEASE_SECONDS` | `60` | Job ownership lease |
| `WORKER_HEARTBEAT_SECONDS` | `15` | Lease renewal interval; must be shorter than lease |
| `WORKER_IDLE_POLL_SECONDS` | `2` | Empty/error poll delay |
| `WORKER_DRAIN_TIMEOUT_SECONDS` | `30` | Grace period for active work |
| `WORKER_HEALTH_HOST` | `127.0.0.1` | Health listener |
| `WORKER_HEALTH_PORT` | `8081` | Health port |
| `WORKER_LOG_LEVEL` | `INFO` | Structured JSON log level |

Configuration validation fails startup with a non-zero exit. Startup logs expose
only safe operational values and registered job types; payloads and credentials
are excluded.

## Health

- `GET /live`: process liveness.
- `GET /ready`: readiness to claim. Returns 503 before startup, while disabled,
  when database access is unavailable, or while draining.
- `GET /health`: liveness/readiness, drain state, worker ID, active count, last
  poll, and last successful claim. It never contains credentials or job payloads.

## Lease and outcomes

Each handler receives immutable job/tenant identity, payload, provider/repository
dependencies, shutdown and cancellation events, and contextual logging. It must
return completed, retryable failure, non-retryable failure, or cancelled.

A heartbeat uses an independent database session. Any ownership loss or uncertain
heartbeat prevents completion. Retryable failures use the existing bounded
backoff. Non-retryable/unsupported handlers transition directly to terminal
failed. Cooperative cancellation explicitly releases the job to retry.

## Shutdown and recovery

SIGTERM and SIGINT stop new claims and immediately make readiness false.
Heartbeat continues while the active job has the configured drain grace period.
At timeout the cancellation event is set. A cooperative handler returns
cancelled and the job is released; an unresponsive handler is abandoned without
completion and becomes claimable after its lease expires. Database pools and
registered client closers are then closed.

Operators should send SIGTERM, wait at least
`WORKER_DRAIN_TIMEOUT_SECONDS + WORKER_LEASE_SECONDS`, verify active jobs are
zero or leases are expiring, and only then force-stop the container.
