# Step 08 — Google Drive managed storage

## Scope

This step adds managed Google Drive storage without changing source browsing or
enabling a worker. MANAGED_ASSET_STORAGE_ENABLED remains false.

## Boundary

GoogleDriveAssetStorage receives a dedicated managed-storage access token and
configured root folder ID. It never reads the current Google Drive source
session. Source permissions discover content; storage permissions write an
internal canonical asset.

## Deterministic identity and retry

- The internal tenant, asset ID, and content hash are validated before upload.
- The remote filename is the content hash plus normalized original suffix.
- Drive appProperties record tenant, internal asset ID, and content hash.
- Before upload, the adapter searches the root for the same tenant and asset.
- PostgreSQL records one storage object per tenant, asset, and provider.
- A retry reuses the DB record and re-discovers a remotely created file.

## State

asset_storage_objects status is pending, uploading, stored, retry, or failed.
Retryable provider failures use bounded backoff and terminal failures retain
structured error fields. Remote file ID, folder ID, web URL, attempt count and
timestamps are persisted.

Metadata sidecar export is deliberately deferred to Step 19.

## Rollback

Keep the flag false, stop asset_store consumers if manually started, export
remote identities if needed, then downgrade revision 0003 to 0002. Downgrade
does not delete files already uploaded to Google Drive.
