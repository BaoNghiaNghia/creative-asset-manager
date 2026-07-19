# Step 06 — Google Drive Changes and SharePoint Delta

## Scope

Incremental synchronization is implemented behind
`INCREMENTAL_SOURCE_SYNC_ENABLED=false`. Existing explorer browsing and API
responses are unchanged. No worker handler is registered or started.

## Provider cursors

- Google Drive initializes with `changes.getStartPageToken`, consumes
  `changes.list`, persists intermediate `nextPageToken`, and finishes on
  `newStartPageToken`.
- SharePoint consumes Microsoft Graph drive `root/delta`, persists each opaque
  `@odata.nextLink`, and finishes on `@odata.deltaLink`.
- SharePoint external source metadata must contain the Graph `drive_id`.
- Delta URLs are accepted only when they target `https://graph.microsoft.com`.

## Page transaction

For each fetched page, `source_assets`, idempotent
`source_asset_download` jobs, and the page cursor are flushed through one
SQLAlchemy session and committed together. Any persistence failure rolls back
the page and leaves its previous cursor intact.

Rename and folder moves update mutable source metadata only. A download job is
created only for a new non-folder item or when provider checksum/content version
changes. Delete soft-deletes the source observation. Restore clears deletion;
an unchanged restored version does not schedule another download.

## Reconciliation

Reconciliation requests a full provider listing/delta baseline, records all
seen external IDs, and soft-deletes active source observations absent from the
completed scan. Pages are still persisted atomically. A failed reconciliation
is restarted from the beginning so the missing-item sweep only runs after a
complete scan.

## Rollback

Set `INCREMENTAL_SOURCE_SYNC_ENABLED=false` and stop manually invoked sync
handlers. Step 06 adds no migration; existing cursors and source observations
may remain for a later retry.
