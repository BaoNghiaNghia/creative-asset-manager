# Visual Search backfill

This tool only enqueues visual index jobs and defaults to dry-run.

    python -m app.operations.visual_search_backfill_cli --tenant-id TENANT_ID --schema-version visual_embedding_v1 --max-assets 10

Use `--execute` only after enabling the global backfill flag. Output includes
`checkpoint_asset_id`; resume with `--after-asset-id`. SIGINT/SIGTERM stop after
the current batch.

Both dry-run and executed backfill require Visual Search eligibility:
`VISUAL_SEARCH_ENABLED=true` and target-tenant membership in
`VISUAL_SEARCH_CANARY_TENANT_IDS`. An empty allowlist denies the work. Executed
backfill additionally requires `VISUAL_SEARCH_BACKFILL_ENABLED=true`; otherwise
the command is rejected before it can enqueue jobs. No backfill mode may enqueue
new visual work for an ineligible tenant.
