# CAM Agent Harness V2 - Phase 0 Read-Only Architecture Audit

Date: 2026-11-07
Scope: checked-out CAM source and CodeLocal workspace metadata only.
Status: static baseline PARTIAL; production SQL metrics and release identity NOT VERIFIED.

## Source and safety baseline

- Explicit CodeLocal workspace: creativeasset /srv/creative-asset-manager-source.
- Root checkout is on historical fix/rrugc-codex-quota-economy-20261017 and is DIRTY. Do not mutate or deploy root checkout.
- Nested worktree .cam-creative-brief-v1 is main tracking origin/main; on 2026-11-07 it was at commit 621bb07fee156a080266635964111dbc047aa0d6. Other work is concurrently modifying Etsy Spy and UI. Stage/commit only Agent Harness-related paths.
- Native backend uses FastAPI/Python, PostgreSQL + one central Processing Queue. New Agent Registry must be an optional abstraction ABOVE this job system, never a second queue.
- This audit did not read secrets, query production DB, inspect systemd current symlink, mutate jobs or change Windows Scout.

## Existing contracts confirmed in source

| Subsystem | Sources | Reuse decision / risk |
| --- | --- | --- |
| Queue and job identities | apps/api/app/modules/processing/model.py, service.py, repository.py | Already has tenant-bound idempotency key, job status, priority, lease and retry. Reuse; no queue rewrite. |
| Worker roles and job types | apps/api/app/modules/processing/worker_roles.py and registry.py | Explicit allowlist for image, video, visual. Agent capability must route to an existing job type only after compatibility gate. |
| Stage 1 learning | realistic_review_ugc/stage1_design_learning.py, typography_feedback_learning.py, keyword_cohort_learning.py | Preserve Pick/Reject and existing per-tenant metadata. General Experience Engine should read validated signals, not replace feedback storage. |
| Stage 2 image | realistic_review_ugc/ecommerce_pipeline.py ~1730-2510; slot2_fixed_uv | Multiple modes remain present, including fixed UV, raster placement, direct angle edits and legacy transfers. Count active job payload modes before any deletion. Preserve design master PNG when needed for fixed UV. |
| Windows Scout load | apps/rrugc_scout/network_load.py | Existing cross-process lock, burst gate, image readiness and CPU adaptive pauses. Verify four live agents share same resolved lock file path before changing logic. |
| Etsy Spy offline sync | modules/etsy_spy/service.py | Preserve batch idempotency, receipt checksum, cursor and reconciliation. Avoid second authoritative listing store. |
| CodeLocal reuse | github.com/codelocal-cloud/CodeLocal (public) | Borrow tested design concepts: Registry, DAG, Experience Promotion, capability routing. Public team fan-out is not assumed shipping production. |

## Baseline gaps to resolve before production activation

1. Identify exact `/opt/creative-asset-manager/current` and `/var/www/creative-asset-manager/current` commits via an explicitly reviewed read-only diagnostic; do not bypass CodeLocal workspace restrictions.
2. Record pending/retry processing_jobs grouped by job_type, engine version and stage. Inventory pinned fields. Do not read secrets or user content.
3. Measure Stage2 p50/p95, QA accept, engine selection, retry rate and cost per accepted output with a fixed observation window and tenant scope.
4. Record Scout four-agent page/navigation/image times and network-slot contention; confirm the actual Windows deployment root and shared lock. Measure unrelated job timeouts.
5. Verify Etsy Spy local receipt/cursor reconciles with backend, including reconnect and repeated batch.
6. Find active production worker paths and legacy dependencies before deleting any Stage2/Scout code.

## Phase 1 deliverable boundary

Initial agent_harness/registry.py and experience.py are PURE modules, not imported by processing workers, routers or job payload handlers. Nothing is automatically registered, dispatched, promoted or learned. Existing jobs, queue schema, auth and API behavior remain unchanged.

Registry acceptance:
- Explicit agent version and exact tool allowlist.
- Only registered and healthy/available executors satisfy required capabilities.
- Fallback is pre-dispatch only; does not replay live mutations.
- Config limits and identifiers fail closed; policy sets are frozen.

Experience acceptance:
- Exact tenant+agent+version+scope filtering.
- Independent verification and no regression required for promotion.
- Distinct sample/event IDs, holdout samples, confidence >=0.8.
- A pure policy decision never becomes authoritative until DB adapter independently validates event lineage, authorization and evidence references.

## Next work package

CAM-000 (verify production release and metric baseline) and CAM-004 (schema/persistence with negative tenant/RBAC tests); then install an opt-in Agent Harness entrypoint behind a default-off feature flag, leaving legacy worker handling unchanged. Integrate Stage1 and Stage2 only after golden fixtures, QA checks and feature canary gates pass.
