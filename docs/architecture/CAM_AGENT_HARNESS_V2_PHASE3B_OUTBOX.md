# CAM Agent Harness V2 — Phase 3B.1 Durable Stage 1 Feedback Outbox

## Intent and boundaries

This is the first automatic, opt-in, **evidence-only** pipeline for Stage 1 Pick/Reject/Undo. It does not spawn agents, call Codex CLI, use paid APIs, change image-generation jobs, update the Creative Brief, or promote memories. Existing RRUGC Stage 1 decisions are authoritative.

## Vote-capture flow

- Two existing endpoints are instrumented: POST /keyword-images/jobs/{job_id}/versions/{version}/design-feedback and POST /keyword-images/jobs/{job_id}/versions/{version}/products-stage2.
- Only when AGENT_HARNESS_FEEDBACK_SYNC_ENABLED=true and AGENT_HARNESS_FEEDBACK_AUTO_CAPTURE_ENABLED=true (both default false), and no AI_EMERGENCY_STOP, a *changed* decision/reason creates one `agent.stage1-feedback.changed.v1` event in existing `outbox_events` in the SAME transaction as the user vote and any Stage 2 selection change.
- Repeated identical clicks do not emit an event. Undo/Neutral emits a source-change event, but does not count as positive/rejected evidence.
- Outbox event idempotency uses tenant+output version ID+server timestamp+normalized decision/reason hash. Payload contains only a fixed cohort ID and timestamp, no keyword, prompt, raw user text, image, password, access token or provider response.
- Unsupported/unclassified cohorts do not produce events. Current human voting and Stage 2/UGC behavior otherwise remain unchanged.

## Consumption and stale invalidation

- Existing operational worker lifecycle starts AgentFeedbackOutboxScheduler only when AGENT_HARNESS_FEEDBACK_AUTO_PROCESS_ENABLED=true. Scheduler additionally requires PROCESSING_JOBS_ENABLED, FEEDBACK_SYNC_ENABLED, AUTO_CAPTURE_ENABLED and no AI_EMERGENCY_STOP. It runs every 120 seconds (config constant), max 24 feedback events and 3 tenant/cohort groups per tick.
- Uses only event type `agent.stage1-feedback.changed.v1`, not the generic outbox claim routine; other CAM event types remain untouched. PostgreSQL FOR UPDATE SKIP LOCKED protects against multiple worker processes draining the same outbox event.
- Recalculates current tenant-only Stage 1 votes using the verified-source Phase 3A snapshot builder (180 days/800 rows; 10 output versions across 3 keyword phrases; SHA256 uniqueness; 4 votes/keyword, 40/rule). It never treats the outbox payload as evidence.
- Requires an already registered, disabled `gatorhats.feedback-intelligence` agent version with exact `feedback.read` tool permission and `feedback_analyze` capability. Never self-seeds privileged agent definitions.
- Prior candidate/promoted snapshots in that cohort are compared with the latest evidence. Changed/withdrawn evidence marks old snapshots `rejected` with `source_status=stale_due_to_feedback_change`, `promotion_allowed=false`; only new, never-before-seen snapshot keys are saved as `candidate`. Rejected snapshots are never resurrected. Up to 250 recent existing snapshots are compared per scope per tick; older snapshots are not eligible for promotion without a separately designed source recheck.
- New candidates have confidence 0.25, no independent holdout/verification refs and cannot be promoted by this pipeline. The system does not expose any automatic execution or promotion endpoint.
- Unknown tenant/cohort events and unconfigured-agent events become failed metadata records, not an unbounded retry loop. After registering the agent, the existing authenticated Phase 3A explicit sync can safely backfill current votes.
- A transient database failure rolls back the processing transaction so events remain pending for the next tick. No feature introduces remote API calls or extra processing jobs.
- Admin observability: `GET /api/v1/admin/agent-harness/{tenant_id}/feedback/stage1/outbox-status` requires existing READ flag, BOTH `ai_operations.read` and `ai_provider.configure`, exact tenant authorization. Returns only counts for pending/processing/published/failed and flag/execution status, never raw payloads, actor IDs or keywords.

## Canary / rollout

1. Deploy code from `main` pinned commit with `--no-migrate`; leave all new flags false. Verify `/version`, `/ready`, worker health and retained prior release.
2. Register a disabled feedback-only agent version via the existing audited Agent Catalog, after administrative tenant/role verification. Do NOT enable agent execution.
3. Enable FEEDBACK_SYNC + AUTO_CAPTURE only in a restricted canary, verify actual Pick, Reject, Undo produce tenant-only outbox records and duplicate clicks do not.
4. Enable AUTO_PROCESS separately; verify low event latency, no unexpected backlog, no provider calls, no forced model requests and candidate-only snapshot creation. Inspect stale invalidation; stop if errors or high DB utilization.
5. Emergency rollback: turn AUTO_PROCESS and AUTO_CAPTURE false, then optionally switch to previous backend release. No Alembic downgrade or data deletion; event ledger/candidates remain for audit.

## Follow-up for Phase 3B.2

Independent QA/holdout runner, candidate review UI, owner-approved promotion with provenance revalidation at promotion time, strict revocation of already applied rules, and a dedicated observability panel. Until then, all experience data remains inert.
