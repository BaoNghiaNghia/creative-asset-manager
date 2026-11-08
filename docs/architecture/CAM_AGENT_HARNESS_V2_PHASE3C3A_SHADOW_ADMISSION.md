# CAM Agent Harness V2 — Phase 3C.3A: Shadow Admission

Date: 2026-11-08. Status: code candidate in isolated main-based worktree, not deployed.

## Scope

Adds an opt-in prospective admission check at the first text-only Stage 1 Creative Brief enqueue, including automatic Stage 0 Pick and explicit manual Generate. The existing Phase 3C.2 registration and deterministic keyword arm are revalidated under row locks. The original keyword normalized digest, pinned candidate source identity, cohort, study status, tenant scope and assignment arm must remain unchanged; pre-existing Creative Brief/image generation attempts are disqualified.

A successful first enqueue attaches a fixed, privacy-limited `trial_shadow_admission` manifest to the **existing** ProcessingJob payload, and appends an actor-attributed audit record in the same transaction. The assignment remains `reserved` (never `exposed`). Retries and background backfills do not create duplicate admission for an existing Creative Brief. No new provider calls, new job type, database table or migration.

## Safety

- `AGENT_HARNESS_TRIAL_ADMISSION_ENABLED=false` by default; additionally requires existing READ, MANAGEMENT and REGISTRATION gates and no emergency stop.
- Even if enabled, the admission is **shadow-only**. The worker uses its existing prompt regardless of control/challenger assignment. An admission record cannot count as an actual treatment exposure or independent QA outcome.
- The API never lets a caller submit an arm, QA result, experience promotion, or treatment prompt.
- Fail-closed conflicts return HTTP 409 on Stage 0 Pick/explicit Generate, with transaction rollback.
- Does not upgrade historical/re-generated Stage 1 jobs into an experiment. Existing in-flight jobs remain unaffected.
- No deployment until integration tests run in CAM's official Python environment and the existing production migration 0204 is verified.

## Verification gates before a future deploy

1. Run the dedicated `test_agent_trial_shadow_admission.py` suite and the existing `test_agent_holdout_registration.py` and Creative Brief regression suite.
2. Run complete API security tests, lint/typing, migration 0204 validation on disposable PostgreSQL and review the diff.
3. Deploy clean main commit only with the new flag OFF. Verify health, worker/queue metrics, rollback and no new audit/exposure records.
4. Separate deliberate operator canary with a fresh study, no pre-existing Brief and no generated images; confirm audit and manifest without treatment.
5. Phase 3C.3B must implement immutable *actual* exposure at treatment-aware Stage 1 worker invocation, independent blinded QA with asset and reviewer provenance, cost metrics, and the signed-owner gated evaluation. Shadow admission alone is NOT an A/B test.

## Rollback

With feature flag OFF, previous behavior is unchanged and queued jobs are not modified. Rollback uses previous compatible release; no destructive database downgrade. Trial reservations remain immutable.
