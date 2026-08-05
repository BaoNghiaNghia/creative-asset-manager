# Periodic Google Drive source synchronization

The worker can periodically enqueue the existing `source_sync` processing job for active Google Drive sources. It is disabled by default.

Enable only after validating credentials and tenant policy:

```env
PROCESSING_JOBS_ENABLED=true
INCREMENTAL_SOURCE_SYNC_ENABLED=true
SOURCE_SYNC_SCHEDULER_ENABLED=true
SOURCE_SYNC_POLL_INTERVAL_SECONDS=60
SOURCE_SYNC_MAX_SOURCES_PER_TICK=100
SOURCE_SYNC_JOB_STALE_SECONDS=900
```

The scheduler respects tenant `source_sync_enabled` and processing pauses, skips sources without an active OAuth connection, and uses a cursor for incremental sync. A source without a cursor receives one bounded full scan.

Useful commands (run from the API environment):

```bash
python -m app.operations.source_sync_cli source-sync:list
python -m app.operations.source_sync_cli source-sync:enqueue --tenant-id TENANT --source-id SOURCE --dry-run
python -m app.operations.source_sync_cli source-sync:enqueue-all --tenant-id TENANT
```

Enqueue operations are protected by the processing job idempotency constraint, so multiple workers cannot create duplicate jobs for the same source and scheduler interval.
