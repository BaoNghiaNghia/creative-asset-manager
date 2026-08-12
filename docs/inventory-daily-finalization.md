# Inventory daily finalization

Phase 9 adds an Inventory-only, default-off business-day lifecycle. It does not use Creative workers or Creative processing controls.

Enable both INVENTORY_AUTOMATION_ENABLED and INVENTORY_DAILY_SCHEDULER_ENABLED to run the separate inventory-scheduler service. The scheduler evaluates enabled Inventory tenants in Asia/Ho_Chi_Minh at 16:30 (completeness), 16:50 (pre-close), and finalizes ready days at 17:00.

The persisted report identifies missing pages, unresolved reviews, uncommitted approved documents and active Inventory jobs. GET /api/inventory/daily-runs/{date} reads a tenant-scoped run. POST /api/inventory/daily-runs/{date}/finalize requires inventory.finalize. Forced finalization requires a reason and emits an append-only audit event. This phase creates no exports, Drive archive actions, reports delivery, or worker job types.
