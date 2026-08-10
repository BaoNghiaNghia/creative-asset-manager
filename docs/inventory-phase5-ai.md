# Phase 5 — Separate Inventory AI

Inventory document extraction is an isolated worker lane:

`inventory_document_prepare` → prepared `inventory_document_pages` → `inventory_document_analyze` → `inventory_ai_analyses`.

The only extraction profile is `inventory-stock-sheet` version `v1`; its prompt and schema versions are persisted with every analysis. Phase 5 stores raw provider output separately from validated `extracted_json`. It intentionally does not normalize item names, make approval decisions, create transactions, or register Phase 6 handlers.

`inventory_ai_controls` is tenant-scoped and independent of Creative AI governance. It provides enabled/emergency-stop, provider/model allowlists, minimum start interval, maximum concurrent analysis jobs, per-run limit, and daily/monthly budget configuration. The Inventory job claimer enforces the enabled/emergency-stop/concurrency boundary only for `inventory_document_analyze`; Creative pause, workers, usage, budgets, and provider state are never consulted.

All Inventory AI settings default off. The default gateway is unconfigured, so no provider request can be made without an explicit Inventory-only gateway and enabled tenant control. Credentials are not persisted or logged.

Rollback: downgrade Alembic revision `0036_inventory_ai` to `0035_inventory_document_prep`. This removes `inventory_ai_controls` and Phase 5-only analysis columns; do this only when Phase 5 analysis history is intentionally retired.