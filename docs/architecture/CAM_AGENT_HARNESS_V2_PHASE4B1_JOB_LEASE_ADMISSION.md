# CAM Agent Harness V2 — Phase 4B.1: ProcessingJob lease admission evidence

Date: 2026-11-11. Scope: source-of-truth read-only admission *preview* only.
This milestone does NOT enable actual multi-agent execution, an authenticated
Browser Extension heartbeat, provider execution, or automatic learning.

## Existing authority

Reuse `processing_jobs` with its existing claim / renewable lease machinery,
PostgreSQL `SKIP LOCKED`, worker ID, cancellation, attempt budget, and
`WORKER_HEARTBEAT_SECONDS`. Do not introduce an agent-owned queue,
parallel lease table, a standalone scheduling service or any new migration.

## API

`POST /api/v1/admin/agent-harness/{tenant_id}/agents/{agent_id}/{version}/jobs/{job_id}/lease-preview`

- Requires the existing Agent Harness READ flag; authenticated tenant scope;
  **both** `ai_operations.read` and `ai_provider.configure` permissions;
  a registered, pinned agent version; and explicit `job.read` in both the
  agent's immutable tool grants and request's required tools.
- Accepts the existing strict `AgentRoutePreviewRequest`; `job_id` must
  be a UUID, resolved by tenant. Missing `job.read` request or pinned agent
  grant returns 403 **before any job lookup**. Unknown and cross-tenant job
  IDs return the same 404 after authorization. Never return raw payload,
  prompts, credentials, lease worker ID, CLI profile path or other tenant jobs.
- Only known, exact RRUGC Stage 1 `rrugc_creative_brief_generate` and
  `rrugc_keyword_image_generate`, plus Stage 2 `rrugc_stage2_generate`,
  can produce an *observed* live lease. Stage 4 remains blocked until its
  concrete runtime handler is independently mapped, not just inferred from
  the stage label.
- Reject cancelled/queued/finished jobs, missing worker owner, invalid
  attempt budgets, missing/future/inconsistent timestamps, expired leases,
  and stale processing-row activity. The observation age window is clamped
  between 30 and 180 s (4x the configured worker heartbeat by default).
- No Codex account probe is made on an invalid lease, missing tool grant,
  emergency stop or wrong stage. For an active observed lease, invoke the
  existing route preview **only for diagnostics**. Its snapshot is ephemeral
  and does not reserve capacity; pinned executor priority remains unchanged.
- Return `dispatch_allowed=false`, `execution_enabled=false`, nested
  route advice or null, and explicit `worker_heartbeat_authenticated=false`
  / `executor_slot_reserved=false`. Recent `updated_at` activity is
  **not** a verified signed heartbeat or proof of a provider result.

## Operational safety

No new flag, migration, dependency, credentials, network call, queue
consumer, worker claim, provider bill, or database write. API is hidden until
the existing READ flag is enabled; learning and policy promotion remain off.

Security regressions must cover cross-tenant and nonexistent IDs, missing
job.read, absent/expired/stale lease, cancellation, stage mapping, malformed
UUID, emergency stop, and no Codex probe on denied requests. GitHub CI for
API and PostgreSQL 12/16 must pass. Release only merged `main` through
`scripts/cam-rebuild-backend.sh --no-migrate --keep-releases 2`, verify
API and workers, and preserve the previous immutable release. Rollback is
code-only; do not downgrade 0207 or delete any snapshot/QA evidence.

Prior owner-requested skipping of independent off-host restore rehearsal
does **not** prove production backup recovery. Do not extend that waiver
to destructive data operations.

## Follow-up: Phase 4B.2

Design authenticated, scoped heartbeats and acknowledged results for the
Windows Image Extension/Scout and other external executors, with signed
short-lived lease tokens tied to tenant/job/attempt/worker identity. The
real CAM worker retains exclusive claim/renew/release authority, and any
loss of heartbeat/lease fences off output writes. Then implement measured
capacity reservation and retry-safe multi-executor admission under a
separate disabled-by-default flag. Do not authorize job dispatch based on
this read-only preview alone.
