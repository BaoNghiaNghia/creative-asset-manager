# Creative AI Managed Storage cleanup

Managed Storage contains temporary Google Drive copies used by Creative AI. It
does **not** contain the user's source Drive file. Cleanup addresses only the
remote ID stored in asset_storage_objects.remote_file_id, using the separate
Managed Storage Google account.

## Safe rollout

1. Deploy with MANAGED_STORAGE_AUTO_CLEANUP_ENABLED=false.
2. Call GET /api/v1/admin/ai-operations/managed-storage/cleanup/preview.
3. Run POST /api/v1/admin/ai-operations/managed-storage/cleanup with dry_run
   true and a limit of 100.
4. Review counts and run one confirmed, bounded non-dry batch.
5. Verify original source Drive files and AI metadata remain intact.
6. Enable automatic cleanup only after validation.

Completed analyses are retained for six hours by default; terminal,
non-retryable failures for 24 hours. Pending, running, retrying, and
budget-blocked analysis/batch/job activity is protected. A remote 404 is
treated as an already-removed staging binary and only removes its stale DB
reference. Authorization, rate-limit, network, and server errors retain the DB
row for a future retry.

The active Managed Storage location is identified by the folder ID configured
for the current environment. Take only the value after `/folders/` from the
active Google Drive URL; do not copy the whole URL and do not reuse an example
ID as a fixed root:

```dotenv
# https://drive.google.com/drive/u/0/folders/{folder_id}
GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID=<folder_id>
```

The example environment intentionally leaves this value blank. Automatic
cleanup is also disabled unless production explicitly sets:

```dotenv
MANAGED_STORAGE_AUTO_CLEANUP_ENABLED=true
```

Skipped active or not-yet-ready records are rotated behind unchecked records,
so a fixed set of old rows cannot starve an eligible backlog. Active analysis,
projection, and search-index jobs attached to either the asset or its pipeline
continue to protect the managed binary.

Set the values in deploy/production.env.example; migrations never call Google
Drive or delete remote data.


## Preventing and repairing self-ingestion

Google Source Sync ignores Drive items carrying CAM-owned appProperties:
a Managed Storage binary has cam_tenant_id, cam_asset_id, and
cam_content_hash; a metadata sidecar carries cam_sidecar. User files with
unrelated app properties continue to sync normally.

Before enabling cleanup on a deployment that previously ingested staging copies:

1. Keep automatic cleanup disabled.
2. Call GET /api/v1/admin/ai-operations/managed-storage/self-ingestion/preview.
3. Run POST /api/v1/admin/ai-operations/managed-storage/self-ingestion/repair
   with dry_run=true, then review every count.
4. Run a small confirmed batch with dry_run=false.
5. Verify every repaired asset retained another legitimate source link.
6. Only then use the Managed Storage cleanup preview and confirmed cleanup.

Repair only deletes the contaminated asset_source_links and an orphaned
source_assets row. It never deletes an Asset, analysis, source Drive file,
Managed Storage Drive file, or asset_storage_objects row. An asset whose
only source is a staging copy is reported as skipped_only_source.
