# CAM Agent Harness V2 Phase 4C.2B — opt-in executor session fence

Date: 2026-11-19. Backend-only, disabled by default. This is a safety
foundation for later browser-slot admission, **not** an independently
verified browser slot or permission to dispatch jobs.

## Existing authorities and scope

Use the existing paired Etsy Image Agent bearer, per-attempt lease token,
PostgreSQL `processing_jobs` row, transaction advisory lock and
`FOR UPDATE SKIP LOCKED`. No new tables, migrations, databases,
queue, provider calls, cron workers or backup artifacts.

The opt-in `CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED=1` is effective
only for new `orchestrator_v1` claims; default is `0`.
A device must send a canonical lowercase UUIDv4 header
`X-CAM-Executor-Session` at `POST .../claim`. This UUID must be generated
from the paired extension's single-flight browser journal, not per HTTP call.
If absent/malformed while enabled, the new claim is rejected with 422 before
claiming any job.

On successful claim the server stores only a SHA-256 binding over tenant,
paired device ID, job ID, attempt and session UUID, under
`processing_jobs.payload_json._cam_harness_executor_session_v1`.
The UUID itself and digest are never returned. A second tab on the same
paired bearer cannot retrieve an active bound job under a different session:
the claim returns `task=null`, `pause_reason=executor_session_conflict`.
The same session can repeat `claim` idempotently. Server verifies the same
binding after the per-attempt lease token on `check-job`, `result` and
`harness-heartbeat`; wrong/missing session returns 409 and cannot renew
a lease or submit an output.

An expired claim may be re-fenced only through the existing atomic
transactional reclaim, with incremented attempt and a different HMAC lease
token; the previous session and attempt lose authority. A malformed stored
binding fails closed. Existing unbound historical jobs continue to work
under their old token/ownership rules. Disabling the feature stops new
bindings but **does not bypass** a binding already stored on an active job.

## Current rollout gate

The active Windows Image Extension is independently evolving and has
uncommitted changes, so no client code is modified or restarted by this
backend release. Keep `CAM_AGENT_HARNESS_EXECUTOR_SESSION_FENCE_ENABLED=0`
in production until the existing device journals supply stable UUIDs and
a one-tab canary proves retry/resume across relays. Probe reports a
capability/ready boolean only, not a free slot.

Neither this header nor the server-created digest proves independent
browser state, provider readiness, idle capacity or result quality.
`verified_free_slots=0`, `dispatch_allowed=false` and
`automatic_promotion_enabled=false` remain unchanged. A later separate
transactional slot-reservation + browser challenge must pass before
admission can be enabled.

## Security and rollback

- Tenant/agent/attempt/job binding fails closed; existing bearer, lease token,
  job status and expiry checks remain mandatory.
- Run negative tests for bad UUIDs, different tab, revoked/cross-tenant
  owner, stale lease and attempts, heartbeat and result paths.
- No schema/dependency/secret changes; no migration or extra backup during
  production deploy (`--no-migrate --keep-releases 2`).
- Rollout rollback: keep the opt-in flag OFF. If ever activated, first drain
  fenced jobs or continue sending the same client session header, then
  disable new fenced claims. Reverting backend code while fenced leases
  are active would remove the additional safety check and needs an
  operator-reviewed drain; no database downgrade is needed.
- This phase must not stop Chrome or the active Windows relay.
