# AI Operations dashboard

## Scope and performance budget

PostgreSQL is authoritative for dashboard data. Interactive queries are tenant-scoped, default to seven days, and reject ranges over 90 days. CSV exports are capped at 10,000 rows and stream results in chunks; they are not an unbounded reporting API.

The initial production sizing target is up to 100,000 analysis attempts and 100,000 usage records per tenant in a rolling 90-day window. The acceptance threshold for each warm dashboard aggregation (`summary`, `daily`, `providers`, and `failures`) is 750 ms on a CI-class database host. Migration `0022_ai_operations_indexes` provides the tenant/time indexes used by these queries.

Run the repeatable benchmark against a migrated disposable PostgreSQL database:

```bash
cd apps/api
python -m app.operations.ai_operations_benchmark \
  --database-url "$AI_OPS_BENCHMARK_DATABASE_URL" \
  --allow-write --rows 100000 --repeats 3 --threshold-ms 750
```

The supplied database must be disposable: the command inserts and removes one uniquely named benchmark tenant. The database URL is never printed. Omitting `--database-url` uses an isolated temporary SQLite database for a local diagnostic only.

On 2026-07-22 the benchmark completed against migrated PostgreSQL 16.4 with 100,000 analyses and 100,000 usage rows:

| Query | Average | Maximum |
| --- | ---: | ---: |
| summary | 45.26 ms | 48.52 ms |
| daily | 138.90 ms | 147.50 ms |
| providers | 84.20 ms | 90.97 ms |
| failures | 9.47 ms | 9.84 ms |

The representative count plan chose a parallel sequential scan because every generated row belonged to the benchmark tenant and fell inside the selected range; even that worst-selectivity case remained well below 750 ms. An isolated SQLite diagnostic also remained below the threshold (maximum 393.91 ms), but it is not used for the production decision.

These measurements do not demonstrate a need for `ai_daily_metrics`; therefore AI-OPS-05 deliberately adds no rollup table, migration, scheduler, or backfill job. Re-run the PostgreSQL benchmark with production-like hardware and cardinality before rollout. Add a rollup only when repeated warm PostgreSQL measurements exceed the threshold, not merely because the table is large.

## Metric semantics

- `requested` is grouped by analysis `created_at`.
- terminal `completed` and `failed` counts are grouped by `completed_at` in UTC.
- retryable processing jobs are not terminal failures; only jobs in terminal `failed` state enter failure reports.
- `budget_blocked` remains separate from failed and from the success-rate denominator.
- success rate is `completed / (completed + terminal failed)`.
- costs come from idempotent provider-operation usage records and reconciled budget reservations. Batch summary fields are not added again, so batch and item cost cannot be counted twice.
- estimated, provider-reported, and reconciled costs remain separate labels.
- daily provider charts use server-side daily aggregates, not the bounded usage-table page.
- all time filters and day buckets are normalized to UTC.
- provider, model, mode, metadata profile, status, and source-provider filters use the same server filter object across summary, daily, provider, failure, jobs, usage, and export paths.

## CSV exports

Authenticated tenant operators can download:

- `/api/v1/admin/ai-operations/exports/daily.csv`
- `/api/v1/admin/ai-operations/exports/usage.csv`
- `/api/v1/admin/ai-operations/exports/failures.csv`
- `/api/v1/admin/ai-operations/exports/jobs.csv`

The standard dashboard filters are supported plus `row_limit` (default 5,000; maximum 10,000). Each request creates an append-only `ai_operations_export_requested` audit record before streaming begins. Exports use `private, no-store`, guard spreadsheet formula cells, and omit job payloads, raw error messages, provider request IDs, signed URLs, credentials, and raw provider responses.

## Validation and rollback

Run focused validation:

```bash
cd apps/api
python -m unittest -v tests.modules.ai_operations.test_api tests.modules.ai_operations.test_controls
## Running maintenance commands in production

Never source `/etc/creative-asset-manager/production.env` in a shell. Values such as
Java options contain spaces, while JSON settings contain quotes that a shell will
reinterpret. Use the release-owned environment helper so values are parsed without
executing shell syntax and command output is redacted against configured secrets.

For example, inspect quota-deferred Video AI jobs without changing them:

```bash
cd /opt/creative-asset-manager/current
./deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env \
  --expected-owner-uid 0 -- \
  ./apps/api/.venv/bin/python -m app.operations.processing_cli \
  video-quota:reconcile-deferred --tenant-id <tenant-id> --limit 1000
```

After confirming the dry-run count, append `--apply --yes` to the processing CLI
arguments. This only makes matching pending jobs eligible immediately; the Video
worker still performs its normal Free Tier quota reservation before calling Gemini.

python -m unittest -v tests.integration.test_ai_operations_postgresql

cd ../client
npm test
npm run typecheck
npm run build
```

The PostgreSQL test requires `INTEGRATION_DATABASE_URL`. Rollback is application-only: remove the export route/UI links and restore the prior aggregation implementation. No schema downgrade, data deletion, worker drain, or feature-flag change is required. Dashboard mutation authorization continues to use the existing tenant/platform admin controls; AI-OPS-05 does not enable controls for unauthorized users.

## Durable RBAC permission matrix

AUTH-08 replaces the legacy processing-admin dependency on AI Operations and related administration surfaces.

| Operation | Permission |
| --- | --- |
| Read dashboard/configuration | ai_operations.read |
| Run / force analysis | ai_analysis.run / ai_analysis.force |
| Retry / cancel jobs | ai_jobs.retry / ai_jobs.cancel |
| Configure providers and defaults | ai_provider.configure |
| Read / update budget | ai_budget.read / ai_budget.update |
| Pause/resume tenant or provider AI | ai_emergency_stop |
| Search read / rebuild / activation | search.read / search.rebuild / search.index.activate |

Physical index lifecycle, global runtime controls, cost-rate mutation and global process metrics require durable platform administration. Tenant roles cannot grant platform administration. Every tenant mutation uses the active tenant from the application principal (or validates an explicit target), and successful privileged mutations append a bounded audit record containing the durable user actor and tenant.

The dashboard stays read-only for principals with ai_operations.read but no mutation permissions. A 401 means sign-in is required; a 403 reports the safe missing permission. PROCESSING_POLICY_ADMIN_IDS is only a deprecated, default-disabled compatibility bridge controlled by AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED.
