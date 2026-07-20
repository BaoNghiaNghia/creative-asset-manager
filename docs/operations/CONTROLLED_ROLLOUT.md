# Controlled rollout runbook

## Purpose and hard safety boundaries

This runbook controls rollout of the unified asset pipeline. PostgreSQL remains
authoritative, Elasticsearch remains rebuildable, and Drive sidecars remain
exports. No command in this document changes a feature flag automatically.

All flags default to `false`. A deployment is not permission to enable them.
Every enablement requires an operator, recorded change ticket, named tenant or
isolated pilot environment, start/end time, owner, success metric, abort metric,
and tested rollback.

The current flags are process-wide. The current database worker claim does not
filter by tenant. Therefore a shared multi-tenant deployment does not yet offer
a hard tenant allowlist for every stage. Until tenant gates exist, use an
isolated pilot deployment whose producers and queued jobs contain only the
pilot tenant. Do not describe a global flag as tenant-scoped.

## Deployment checklist

### Before deployment

- [ ] Name the release owner, database owner, worker owner, search owner, and
      provider contact.
- [ ] Record the application commit, container image digest, migration head,
      previous Elasticsearch read/write targets, and active flag values.
- [ ] Confirm a recent PostgreSQL backup and perform a restore drill in a
      non-production environment.
- [ ] Confirm OAuth/provider secrets come from the deployment secret store and
      are not present in manifests, logs, job payload diagnostics, or tickets.
- [ ] Confirm Google Drive, Microsoft Graph, managed storage, AI, and
      Elasticsearch quotas and budgets.
- [ ] Confirm all required dashboards and alerts are visible to the release
      owner.
- [ ] Confirm no uncontrolled backfill, reconciliation, or reindex is active.
- [ ] Run backend tests, client tests/build, Alembic head check, and migration
      upgrade/downgrade tests.
- [ ] Verify all new flags are `false` in the candidate deployment.

### Deploy

1. Stop schema-changing jobs and operational rebuild commands.
2. Apply database migrations in the order below.
3. Deploy API and worker code with every new flag `false`.
4. Start API instances and validate health, authentication, legacy browsing,
   legacy search, tags, ratings, and media preview.
5. Start worker processes in disabled/no-claim mode.
6. Compare error rate, latency, connection-pool pressure, and database locks to
   the pre-deploy baseline.
7. Keep the release in flag-off observation before beginning pilot enablement.

### After deployment

- [ ] Confirm the migration head is exactly the expected revision.
- [ ] Confirm no worker claimed a unified-processing job.
- [ ] Confirm no new managed-storage file, AI call, v2 index, sidecar, or
      external ingestion was created unexpectedly.
- [ ] Record verification evidence and the rollback deadline.
- [ ] Retain the previous deployable image and Elasticsearch physical index
      through the rollback window.

## Database migration order

Apply one linear Alembic head in this order:

1. `0001_asset_registry`
2. `0002_processing_jobs_outbox`
3. `0003_managed_asset_storage`
4. `0004_dynamic_ai_metadata`
5. `0005_external_ingestions`
6. `0006_metadata_sidecars`
7. `0007_search_operations`

Use:

```bash
cd apps/api
alembic heads
alembic upgrade head
alembic current
```

There must be one head. Do not enable a feature before its migration is
present. Migrations are additive, but after authoritative production writes
begin, prefer a forward repair migration over destructive downgrade.

For rollback before any new writes, stop API/worker consumers and downgrade one
revision at a time using each migration's downgrade notes. After new writes,
disable the feature and preserve data; do not downgrade without a reviewed data
export and impact plan.

## Feature-flag enablement order

Enable only one stage per observation window:

1. `UNIFIED_ASSET_INGESTION_ENABLED` in shadow-write mode for the isolated
   pilot input only.
2. `CONTENT_DEDUP_ENABLED` for controlled pilot ingestion.
3. `INCREMENTAL_SOURCE_SYNC_ENABLED` for one external source.
4. `PROCESSING_JOBS_ENABLED` only when the pilot queue contains no other
   tenant and workers have the required handlers.
5. `EXTERNAL_ASSET_DOWNLOADER_ENABLED` after allowlist and security limits are
   verified.
6. `MANAGED_ASSET_STORAGE_ENABLED` after deterministic-folder and credential
   checks.
7. `DYNAMIC_AI_METADATA_ENABLED` and
   `AI_SINGLE_ANALYSIS_ENABLED` for manual pilot assets.
8. Run and approve the pilot evaluation before enabling
   `AI_BATCH_ANALYSIS_ENABLED`.
9. Enable `SEARCH_PROJECTION_ENABLED`; rebuild pilot projections with
   `--dry-run` first.
10. Create the Elasticsearch v2 shadow index with
    `ELASTICSEARCH_V2_ENABLED`, while production reads remain legacy.
11. Enable `SEARCH_QUERY_PARSER_V2_ENABLED` only in the isolated pilot after
    legacy/v2 comparison meets its gate.
12. Enable `AI_AUTO_ANALYZE_ENABLED` only for new pilot assets after cost and
    quality gates pass.
13. Run a controlled, throttled backfill.
14. Enable `EXTERNAL_INGESTION_API_ENABLED` for one credential/integration.
15. Enable `DRIVE_METADATA_SIDECAR_ENABLED` only after analysis is stable and
    Drive export permissions are validated.

`EXTERNAL_ASSET_DOWNLOADER_ENABLED` must remain off unless the hostname
allowlist, DNS protection, redirect validation, byte/pixel limits, and timeouts
are configured. Sidecars, Elasticsearch, and AI never become sources of truth.

## Pilot tenant runbook

### Pilot preparation

1. Use a dedicated non-production or isolated production deployment.
2. Select one tenant and one external source with a representative but bounded
   asset set.
3. Record expected item count, total bytes, file-type distribution, duplicate
   sample, metadata profile/version, projection version, and maximum AI budget.
4. Ensure no other tenant can enqueue work into the pilot database/queue.
5. Capture baseline legacy browse/search results and latency.
6. Set an observation window and explicit abort thresholds.

### Stage gates

At every stage:

- Enable one flag group only.
- Process a small canary set before the full pilot set.
- Reconcile source item, source asset, canonical asset, link, job, storage,
  analysis, projection, and index counts.
- Sample tenant isolation and authorization.
- Confirm retry is idempotent by replaying one completed operation.
- Confirm errors do not expose provider URLs, tokens, prompts, or credentials.
- Wait through the observation window before the next stage.

Suggested gates:

- Registry: 100% source identities unique; zero cross-tenant reads.
- Deduplication: expected same-byte samples converge; zero duplicate canonical
  assets for the same tenant/hash.
- Sync: cursor advances only after page commit; delete/restore/rename samples
  reconcile correctly.
- Storage: retries reuse remote identity; source access and storage credentials
  remain separate.
- AI: schema/safety validation passes; quality sample is approved; spend stays
  below the pilot cap.
- Search: indexed count matches eligible projections; strict/soft/phrase/OR
  fixtures pass; legacy/v2 relevance differences are reviewed.
- External API: idempotency conflict, rate limit, authorization, and async-only
  behavior are verified.

### Pilot exit

Record counts, failures, retries, costs, search comparison, operator actions,
and unresolved risks. Disable pilot-only flags unless the next rollout scope is
approved. A successful pilot is evidence, not automatic authorization for a
global rollout.

## General rollback procedure

1. Stop new producers for the affected stage by setting its highest-level flag
   to `false`.
2. Stop or drain affected workers as described below.
3. Cancel active search maintenance runs cooperatively.
4. Preserve database/job/provider error evidence before retry or cleanup.
5. Restore the previous API/worker image if behavior is not flag-isolated.
6. Reverse Elasticsearch aliases when search reads were switched.
7. Reconcile PostgreSQL authoritative records with external provider/storage
   state.
8. Resume the previous read path and validate user workflows.
9. Open an incident record before any cleanup or replay.

Do not delete canonical assets to undo duplicate links. Do not overwrite
metadata history to undo an analysis. Do not treat deleting a sidecar or
Elasticsearch index as a database rollback.

## Elasticsearch alias rollback

Before cutover, record:

- target physical index;
- previous read alias targets;
- previous write alias targets;
- the completed search operation run ID;
- the run's `alias_switch_json`.

If validation fails:

1. Stop reindex commands and v2 write producers.
2. Keep the failed/new physical index for diagnosis.
3. Use the v2 adapter's `rollback_aliases(previous_index)`, which atomically
   points both read and write aliases to the previous physical index.
4. Verify both aliases resolve only to the intended previous index and that the
   write alias has exactly one write target.
5. Run tenant-scoped keyword, strict AND, phrase, OR, facet, and authorization
   smoke tests.
6. Keep `ELASTICSEARCH_V2_ENABLED=false` or restore the legacy read path until
   the incident is resolved.

Never delete the previous physical index before the rollback window closes.
If aliases cannot be changed atomically, keep v2 reads disabled and escalate to
the search owner rather than partially editing aliases.

## Worker shutdown and drain

The current worker observes its enabled setting before each claim but does not
provide a durable drain endpoint.

1. Disable upstream producers and schedulers first.
2. Prevent each worker instance from starting another claim through deployment
   configuration or orchestrator scale-down.
3. Allow already claimed handlers to finish within their lease and platform
   shutdown grace period.
4. Watch jobs in `processing` and their `lease_expires_at`.
5. After the maximum lease/grace interval, terminate remaining instances.
6. Do not manually mark in-flight jobs pending. Expired leases are recoverable
   by the repository claim logic.
7. Before restart, inspect terminal failures, handler availability, queue age,
   and idempotency keys.
8. Restart one worker, observe recovery, then scale gradually.

A hard kill is allowed for credential exposure, uncontrolled spend, destructive
provider behavior, or tenant isolation failure. Recovery still relies on
leases and idempotent handlers.

## AI budget emergency stop

If spend, request rate, or token/image volume breaches the approved threshold:

1. Set `AI_AUTO_ANALYZE_ENABLED=false`.
2. Set `AI_BATCH_ANALYSIS_ENABLED=false`.
3. Set `AI_SINGLE_ANALYSIS_ENABLED=false`.
4. Stop AI worker claims; if handlers are not separately routable, drain or stop
   all processing workers.
5. Revoke or quota-limit the AI provider credential only when flag/worker stop
   is insufficient.
6. Preserve queued jobs and analysis history; do not mark unexecuted jobs
   completed.
7. Record provider usage, affected tenant/assets, last successful analysis, and
   outstanding queue depth.
8. Require a new budget approval and small manual canary before restart.

Disabling `DYNAMIC_AI_METADATA_ENABLED` additionally blocks metadata writes but
is not a substitute for stopping provider calls.

## External provider outage

For Google Drive, Microsoft Graph, managed storage, AI, or Elasticsearch:

1. Identify the failing provider and distinguish authentication, authorization,
   quota/throttle, network, and data errors.
2. Disable only producers for the affected provider/stage where possible.
3. Preserve source/delta cursors; never advance a cursor for an uncommitted
   page.
4. Allow retryable jobs to use persisted exponential backoff. Avoid manual
   replay storms.
5. Pause reconciliation and backfill traffic before user-triggered traffic.
6. Do not rotate/revoke credentials unless the cause requires it.
7. Keep legacy browsing/search active when its dependency is healthy.
8. After recovery, run a canary request, resume one worker, and monitor retry
   convergence.
9. Run source reconciliation if the outage exceeded cursor/provider retention
   or missed-change guarantees.

For signed URL expiry, create a fresh authorized source download request; never
log or persist a refreshed URL outside the protected job boundary.

## Backfill throttling guide

Use tenant-scoped, resumable batches and begin with the smallest practical
scope:

- one pilot tenant;
- one metadata profile/version;
- explicit asset IDs for the canary;
- `--dry-run`;
- page size 10, increasing only after stable observation;
- one worker replica or one operational command;
- no concurrent full reconciliation.

Control throughput with worker replica count, command page size, provider quota,
and scheduled pause windows. The current search operation page limit is 500,
but that is a safety ceiling, not a recommended starting size.

Pause the backfill when any threshold is breached:

- database CPU, lock wait, connection utilization, or replica lag;
- oldest queue age or retry/failure rate;
- provider 429/5xx rate;
- download bandwidth or temporary disk;
- storage errors or duplicate remote writes;
- AI spend/rate/quality threshold;
- Elasticsearch bulk rejection, refresh pressure, shard growth, or latency;
- API p95/p99 latency or user-visible error budget.

To pause search rebuild/reindex, issue `search:cancel` with the run ID. To
resume failures, reuse the run and `--only-failed`; do not create overlapping
runs for the same tenant/profile/index target.

## Required evidence and open control gaps

Archive for each rollout:

- change ticket and approvers;
- flag before/after values;
- image and migration versions;
- tenant/source scope;
- job and error metrics;
- provider/AI cost;
- search index and alias identities;
- reconciliation results;
- rollback test result.

The following controls remain required before shared multi-tenant rollout:

- hard tenant allowlists for feature evaluation;
- tenant-filtered worker claiming;
- worker drain/health admin controls;
- AI spend counters and automated circuit breaker;
- search comparison dashboards;
- provider-specific producer pause controls.

Until those exist, use isolation and manual operational approval.


## Tenant policy rollout (Step 26)

1. Apply migration `0010_tenant_processing_policies` while all global pipeline
   flags remain false.
2. Set `PROCESSING_POLICY_ADMIN_IDS` to the platform administrator account IDs.
3. Use `/api/v1/admin/processing-policies/{tenant_id}` to configure exactly one
   pilot tenant. Set `pipeline_enabled` and only the required stage booleans.
4. Configure provider rows before rollout when provider-specific concurrency or
   pause control is required (`google_drive/source`, `sharepoint/source`,
   `google_drive/storage`, `gemini/ai`, `elasticsearch/search`).
5. Enable global flags last. Effective eligibility is global AND tenant AND not
   paused. Workers calculate the globally permitted job types at startup and the
   SQL claim filters tenant/provider policy before acquiring a lease.
6. Observe the tenant job-count endpoint and active counters. Increase limits
   gradually. PostgreSQL counters are transactionally reserved with the lease.

Emergency stop: disable the relevant global feature flag and restart workers.
This bypasses the short policy cache because global bounds are evaluated on every
effective-policy read and worker job-type bounds are rebuilt at startup. For a
single tenant/provider, call pause; queued jobs remain intact and running jobs
finish normally. Resume makes them claimable again.

Rollback: disable global processing, pause all pilot tenants, allow graceful
drain through the lease window, revert Step 26 code, then downgrade `0010` to
`0009_durable_asset_pipeline`. Export audits first if retention is required.

## Step 33 shadow and index rollback

Deploy all Step 33 flags false. Enable deterministic analysis only after a
valid completed analysis is selected per profile. Start shadowing with v1
primary, v2 shadow, low deterministic sampling and a short timeout.

For promotion, build/reindex, run verification, inspect persisted evidence,
then explicitly activate. Keep at least one verified previous version. Rollback
switches read/write aliases to that version before any cleanup.

Emergency procedure: globally disable shadow comparison or index lifecycle.
Never delete cluster indices manually. Run cleanup dry-run, explicitly confirm,
and verify aliases; cleanup also rechecks aliases immediately before deletion.
