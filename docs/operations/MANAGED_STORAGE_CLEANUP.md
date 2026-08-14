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

Set the values in deploy/production.env.example; migrations never call Google
Drive or delete remote data.
