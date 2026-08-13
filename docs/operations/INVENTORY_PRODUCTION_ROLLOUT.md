# Inventory production rollout

## Safe defaults

All committed Inventory flags are off. Inventory is not activated by starting the Creative worker. The production topology has three independently restartable processes: worker (Creative), inventory-worker, and inventory-scheduler. Inventory uses inventory_jobs, Inventory health on port 8082, and independent tenant processing/AI controls.

## Controlled pilot

1. Upgrade the database to Alembic head.
2. Set INVENTORY_TENANT_ALLOWLIST to one pilot tenant ID in the deployment secret store; never commit tenant IDs, Drive IDs, OAuth tokens, or API keys.
3. Set INVENTORY_AUTOMATION_ENABLED=true and INVENTORY_WORKER_ENABLED=true. Enable Drive polling and the scheduler only after the binding, processing control, and pilot checks are verified.
4. For Inventory AI, set INVENTORY_AI_ENABLED=true only with the dedicated INVENTORY_AI_GEMINI_API_KEY; it is intentionally not inherited from GEMINI_API_KEY.
5. Start the inventory compose profile and verify worker live/ready endpoints, Inventory job metrics, and tenant-scoped logs.

## Shadow run

Set INVENTORY_SHADOW_MODE=true for the pilot. Discovery through validation remains observable, but ledger commits and Excel/Drive export/archive effects are blocked with a stable inventory_shadow_mode_*_blocked result. Treat this as test evidence, never as a completed production export.

## Rollback / kill switch

Disable Drive polling first, then INVENTORY_WORKER_ENABLED, scheduler, and INVENTORY_AUTOMATION_ENABLED. Set the tenant Inventory processing control to paused and the Inventory AI emergency stop if needed. This preserves Inventory audit/history rows and does not touch Creative controls, queues, workers, or Google sources. Restart only Inventory containers after corrective action.

## Limitations

The API/provider gateway remains intentionally disabled without a dedicated Inventory credential. No rollout action should reuse Creative AI credentials.
