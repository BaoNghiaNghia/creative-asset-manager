# Step 27 — Pilot evaluator, AI cost accounting and budget breaker

Status: Implemented

## Runtime flow

1. The single-asset analysis worker prepares the bounded analysis image.
2. It resolves the provider/model cost version effective at the operation time.
3. PostgreSQL creates an idempotent reservation and atomically increments every applicable daily, monthly and pilot account.
4. A failed reservation marks the analysis `budget_blocked`; Gemini is not invoked.
5. A completed or billable failed provider operation reconciles the reservation and writes one idempotent usage record.
6. Pilot runs enqueue ordinary `asset_analyze` jobs and never invoke AI in the command process.

Amounts ending in `_micros` are integer millionths of the configured currency. Cost-rate values are currency units per provider unit. UTC is the supported accounting boundary. PostgreSQL is authoritative.

## Safety

`AI_EMERGENCY_STOP_ENABLED=true` is checked on every reservation and overrides every tenant policy. Tenant limits are protected by an atomic conditional account update; PostgreSQL row locking and unique operation keys prevent trivial concurrent overspend. Raw prompts, images, signed URLs, tokens and provider credentials are excluded from governance records and reports.

## Interfaces

- Admin budget: `GET/PATCH /api/v1/admin/ai-governance/{tenant_id}/budget`
- Platform cost rate: `POST /api/v1/admin/ai-governance/cost-rates`
- Bounded-label metrics: `GET /api/v1/admin/ai-governance/metrics`
- CLI: `python -m app.operations.ai_pilot_cli ai:pilot-create|ai:pilot-cancel|ai:pilot-resume|ai:pilot-report`

The pilot CLI supports explicit assets, source, path, date range, profile, maximum count and deterministic seed. Reports are JSON or CSV.
