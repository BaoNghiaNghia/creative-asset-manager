# AI pilot and budget operator runbook

## Preconditions

1. Apply Alembic migration `0011_ai_governance_pilot`.
2. Keep `AI_EMERGENCY_STOP_ENABLED=true` while configuring rates and limits.
3. Add a versioned provider/model rate through the platform-admin endpoint.
4. Configure and enable the tenant budget policy.
5. Verify the Step 26 tenant AI stage/provider policy and concurrency limits.
6. Enable `DYNAMIC_AI_METADATA_ENABLED`, `AI_SINGLE_ANALYSIS_ENABLED` and worker processing only for the pilot tenant.

## Create a pilot

```bash
cd apps/api
python -m app.operations.ai_pilot_cli ai:pilot-create \
  --tenant-id TENANT --profile-id PROFILE --maximum-items 25 \
  --sample-seed review-2026-07 --golden-query cat --force
```

Without `--force`, creation stops when the estimated maximum cost exceeds
`AI_PILOT_CONFIRMATION_THRESHOLD_MICROS`. Creation only persists the run and enqueues normal single-asset jobs.

## Observe and report

Use the authenticated metrics endpoint and worker health endpoints. Generate a durable-state report:

```bash
python -m app.operations.ai_pilot_cli ai:pilot-report \
  --tenant-id TENANT --run-id RUN --format json --output pilot.json
python -m app.operations.ai_pilot_cli ai:pilot-report \
  --tenant-id TENANT --run-id RUN --format csv --output pilot.csv
```

Reports exclude provider payloads, credentials and signed URLs.

## Budget block

A deferred denial leaves the job retryable and analysis `budget_blocked`. Increase/disable the tenant policy or wait for the UTC period reset; the next attempt uses a new idempotent operation key. A reject policy terminally fails the job. Usage already billed remains accounted.

Emergency stop:

1. Set `AI_EMERGENCY_STOP_ENABLED=true`.
2. Restart/drain workers according to `WORKER_RUNTIME.md`; reservation checks fail closed in the new process.
3. Inspect `budget_denied` audit events and budget metrics.
4. Change policy/rates if needed, then clear the emergency flag and resume workers.

## Cancel and resume

```bash
python -m app.operations.ai_pilot_cli ai:pilot-cancel --tenant-id TENANT --run-id RUN
python -m app.operations.ai_pilot_cli ai:pilot-resume --tenant-id TENANT --run-id RUN
```

Cancel preserves records and running jobs; it cancels only work not yet started. Resume creates fresh forced analysis attempts for cancelled, failed and budget-blocked items.

## Rollback

Enable the emergency stop, pause the tenant AI stage, drain workers, export required reports, revert Step 27 and downgrade `0011` to `0010`. The downgrade deletes governance usage, budgets and pilot history but retains assets, completed metadata and search projections.
