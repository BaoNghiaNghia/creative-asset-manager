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
SOURCE_SYNC_DAILY_FULL_SCAN_ENABLED=true
SOURCE_SYNC_DAILY_FULL_SCAN_HOUR=10
SOURCE_SYNC_DAILY_FULL_SCAN_TIMEZONE=Asia/Ho_Chi_Minh
SOURCE_SYNC_FULL_SCAN_PRIORITY=20
SOURCE_SYNC_INCREMENTAL_PRIORITY=5
```

The scheduler respects tenant `source_sync_enabled` and processing pauses, skips sources without an active OAuth connection, and uses a cursor for incremental sync. A source without a cursor receives one bounded high-priority full scan.

When enabled, the scheduler also creates exactly one high-priority full reconciliation per active source after **10:00 Asia/Ho_Chi_Minh** each day. The daily idempotency key is source/date scoped, so restarts and multiple workers cannot duplicate it. If the scheduler was unavailable at 10:00, it catches up later that same day; a paused tenant remains paused.

Sources explicitly marked with a meaningful source_metadata.decommissioned_at value are retained for historical integrity but are excluded from automatic synchronization. The scheduler does not rebind their OAuth connection or delete historical source assets, cursors, or runs.

Useful commands (run from the API environment):

```bash
python -m app.operations.source_sync_cli source-sync:list
python -m app.operations.source_sync_cli source-sync:enqueue --tenant-id TENANT --source-id SOURCE --dry-run
python -m app.operations.source_sync_cli source-sync:enqueue-all --tenant-id TENANT
```

Enqueue operations are protected by the processing job idempotency constraint, so multiple workers cannot create duplicate jobs for the same source and scheduler interval.

## Video asset identity repair

Google Drive binary videos expose a SHA-256 checksum in normal source discovery.
Source sync now uses that digest to create/reuse the tenant content-addressed
`Asset` and persist the `AssetSourceLink` before video analysis is enqueued.
Explorer uploads do the same while streaming, and video proxy preparation
computes the SHA-256 from the materialized source as a fallback when the
provider did not supply one. This keeps Asset Explorer and Public Review on the
same asset identity.

For historical videos that were analyzed before this invariant existed, run a
read-only preview first:

```bash
python -m app.operations.video_asset_link_repair --tenant-id TENANT --limit 100000
```

If the reported `repairable_sha256` count is expected, persist only those
checksum-backed links:

```bash
python -m app.operations.video_asset_link_repair --tenant-id TENANT --limit 100000 --execute
```

The repair never invents content hashes. Legacy videos whose persisted provider
checksum is not SHA-256 are skipped; their next video materialization or source
change will establish the link from actual source bytes.
