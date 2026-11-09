# CAM Agent Harness V2 — Phase 3C.3B2A: Trusted baseline receipts

Date: 2026-11-09. Dependency: production PostgreSQL at additive evidence schema `0205_cam_agent_trial_evidence`, first deployed and recovered via `6db036aa6`, with safe current/previous releases.

## Motivation

The previous Stage 1 `trial_shadow_admission` records only a prospective assignment, and both control and challenger still use the identical baseline Creative Brief. Shadow admissions must not be presented as real treatment exposure, and an unreviewed challenger must never be added to the feedback learning loop.

## What this slice adds

- **Internal worker-only baseline receipt**: after Codex returns a successfully validated Creative Brief, the Stage 1 worker can optionally compute SHA-256 of the precise `build_prompt(keyword, learning_context)` arguments and pass it to the evidence recorder within the same transaction as the completed Brief. The raw prompt and model response are not logged in the evidence ledger.
- **Server-side revalidation**: persisted job state, claimed worker lease, tenant, keyword and SHA-256, reserved study/assignment, frozen deterministic arm, recorded candidate source digest, initial shadow manifest, completed Brief revision and matching job lineage. Lock relevant rows; immutable receipt and same-transaction audit; exact retry is idempotent, conflicting retry fails closed.
- **Control only**: any prospective keyword assigned `challenger` remains reserved because its actual policy has NOT been applied. No new provider request, scheduling, change to the baseline prompt, image generation, or Candidate promotion.
- **Read-only admin evidence status**: `GET /api/v1/admin/agent-harness/{tenant_id}/trials/stage1/{study_id}/evidence` requires `ai_operations.read` and `ai_provider.configure`, validates tenant access and read feature flag, returns **counts only**, never keywords or prompt text. Evaluation remains blocked.
- **New independent default-OFF gate**: `AGENT_HARNESS_TRIAL_CONTROL_CAPTURE_ENABLED`. Effective only if prior READ/MANAGEMENT/TRIAL_REGISTRATION/TRIAL_ADMISSION gates are all on and AI emergency stop is off. Existing `AGENT_HARNESS_EXECUTION_ENABLED` must remain OFF. No new dependency or secret.

## Non-goals and safety rules

- A successful baseline Creative Brief is a verified **control treatment receipt**, not proof of image quality or of an independent reviewer.
- The QA ledger `cam_agent_trial_qa_reviews` has **no** submission endpoint or trusted reviewer implementation; counts alone are never quality evidence. No backfill from Pick/Reject or from historic jobs.
- Challenger execution is deliberately unavailable. Do not label a shadow-only challenger job as exposure or change assignment to exposed. Even if control count grows, A/B evaluation stays **blocked** pending distinct challenger prompt application, reviewer identity/blinding, output file checksum verification and provider usage/cost telemetry.
- A direct database insert can never be trusted as a verified treatment without worker provenance. Preserve the existing unique keys/foreign keys and authority boundaries; no automatic learning/promotion.

## Deployment and rollback

1. Run the new worker provenance tests, old trial admission/registration tests, and Creative Brief regression tests. Verify Python compilation and Git diff checks.
2. Push one reviewed commit to `main`. Deploy backend with `--no-migrate`, verifying API and all Image/Video/Visual worker health, exact 0205 schema, and flags still OFF.
3. Check production release current and rollback. Never select a release that lacks PostgreSQL CHECK normalization compatibility.
4. Once release is healthy, clean old releases/worktrees, keeping only current+previous. Do not modify production environment flags automatically.
5. Next: reviewed **challenger-specific prompt policy mapping**, bound to the pre-registered candidate and applied inside the real worker, followed by authenticated blind QA on saved Stage 1 output bytes and trusted cost metrics.

## Verification boundaries

The capture service trusts only the worker execution path; it receives no client or admin-supplied hash. It never emits an exposure until after provider success, and preserves the shadow manifest separately from the new evidence row. The read-only API advertises `evaluation_status: blocked` regardless of ledger counts.
