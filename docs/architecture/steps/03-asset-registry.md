# Step 03 — Asset Registry

## Objective

Add tenant-scoped source and canonical asset registry persistence without switching current explorer, ingestion, metadata, or search flows.

## Deliverables

- `external_sources`, `source_assets`, `assets`, `asset_source_links`, and `source_sync_cursors` ORM models.
- Alembic revision `0001_asset_registry` with upgrade and downgrade.
- Required uniqueness and tenant-enforcing composite foreign keys.
- Domain record types and `AssetRegistryRepository`.
- Migration and repository integration tests.

## Repository operations

- `upsert_external_source`
- `upsert_source_asset`
- `find_asset_by_content_hash`
- `create_asset`
- `link_source_asset`
- `mark_source_asset_deleted`
- `save_sync_cursor`

Python uses snake_case names; they correspond to the roadmap operations `upsertExternalSource`, `upsertSourceAsset`, `findAssetByContentHash`, `createAsset`, `linkSourceAsset`, `markSourceAssetDeleted`, and `saveSyncCursor`.

## Constraints

- No new route or worker.
- Existing browse/search behavior remains unchanged.
- Unified ingestion is not wired and its feature flag remains false.
- Source soft deletion does not delete canonical content.
