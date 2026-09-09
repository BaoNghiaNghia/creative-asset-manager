# Visual Search backfill

This tool only enqueues visual index jobs and defaults to dry-run.

    python -m app.operations.visual_search_backfill_cli --tenant-id TENANT_ID --schema-version visual_embedding_v1 --max-assets 10

Use --execute only after enabling the backfill flag. Output includes checkpoint_asset_id; resume with --after-asset-id. SIGINT/SIGTERM stop after the current batch.
