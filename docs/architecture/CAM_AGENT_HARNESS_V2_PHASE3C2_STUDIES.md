# CAM Agent Harness V2 — Phase 3C.2: Prospective Study Registry

Date: 2026-11-08
Rollout: Phase 3C.2A rollback bridge e130f82d4 already deployed as 0203.
Phase 3C.2B requires explicit 0204 schema migration; no live AI experiments yet.

## Problem addressed

The earlier Holdout Evaluation kernel was pure logic: it could calculate
theoretical control/challenger assignment but not prove when/how a study was
registered, freeze its candidate, or prevent a keyword being reused across
competing experiments. These controls are prerequisites for independently
measuring whether the Stage 1 self-learning recommendations improve real output.

## New persistence, exact revision 0204

- `cam_agent_trials`: composite tenant+study ID, candidate foreign key to
  tenant-owned immutable `cam_agent_experiences`, cohort, pinned agent version,
  trial version, challenger percentage (10–40%), frozen source digest, operator,
  server registration timestamp, and non-running status `registered`.
- `cam_agent_trial_assignments`: composite tenant+study+keyword ID,
  FK to study and source keyword, normalized keyword SHA256, deterministic
  `control` or `challenger` arm, `reserved` status, server timestamp, and actor.
- Database UNIQUE(`tenant_id,keyword_id`) blocks a single keyword from
  joining multiple studies (conservative: no reuse, including after cancellation,
  until a future reviewed archival lifecycle).
- No migration backfill. No existing Stage 1 output, Creative Brief, queue,
  selected design, expense data or production job is modified.

## Guarded API

The independent gate `AGENT_HARNESS_TRIAL_REGISTRATION_ENABLED` is **false**
by default; existing READ + MANAGEMENT flags must also be enabled. API is
inert while any gate is off. A production emergency stop denies new registration.

- `POST /api/v1/admin/agent-harness/{tenant_id}/trials/stage1`:
  Strictly validated `study_id`, `candidate_event_key`, cohort, trial version,
  challenger percentage (default 20%), reason, and `dry_run=true` by default.
  Requires `ai_provider.configure` and tenant scope. Must re-derive current
  feedback source identity (including undo/reject and asset lineage); cannot
  register an unknown/promoted/rejected/stale candidate. Permanent registration
  is immutable and idempotent. Writes same-transaction actor audit.
- `POST /api/v1/admin/agent-harness/{tenant_id}/trials/stage1/{study_id}/reservations`:
  Strict 1–20 canonical UUID keyword IDs, reason, dry-run default true.
  Only tenant-owned keyword records matching the study cohort; locks keyword
  records during assignment. Rejects any already-registered Creative Brief or
  Stage 1 generation job. Verifies candidate still current and frozen.
  Computes immutable keyword arm with the Phase 3C.1 deterministic hash,
  records only `reserved` (not `exposed`) and audits operator/aggregate count.
  Cross-study collisions fail closed, including concurrent insert races due
  to the DB unique constraint.
- `GET /api/v1/admin/agent-harness/{tenant_id}/trials/stage1/{study_id}`:
  Only read+configure permissions and tenant scope; returns aggregated arm
  counts with no source keyword text, prompt or artwork identifiers.

All registrations are *planning reservations*, not a verified prospective
experiment. Existing feedback that was used to discover the candidate is
explicitly NOT independent QA. The backend must later connect to Stage 0
pick/Stage 1 job dispatch to verify exposure timing and attach blind QA results.

## Safety defaults and performance

- No new provider calls, desktop/Windows tool changes, executor routes, model
  generations, Stage 1/2 alterations or automatic learning.
- No user-supplied arm, policy enabling, commands, URLs, new tool grants,
  prompts, secrets or arbitrary reviewer assertions accepted.
- Agent execution, challenger dispatch and automatic promotion stay hard-off.
- Requests are bounded: at most 20 keyword IDs, one tenant, one candidate
  source snapshot re-derivation, row locks/unique constraints and SQL indexes.
- Stable keywords are isolated by study ID + version + tenant + cohort.
- Non-replayable errors are redacted to fixed server codes; no raw keyword
  values or user vote content returned.
- All new mutations are in transactions with audited operator identity.

## Rollback strategy

1. Production must be at e130f82d4 bridge with Alembic 0203 before this
   migration is allowed. Bridge tests simulate 0203 booting against exact 0204
   and deny absent or weakened columns/FKs/UNIQUE.
2. Deploy from a clean `main` worktree using pinned commit with migrations
   enabled (do not pass `--no-migrate`), and validate PostgreSQL 0204 schema.
3. Check API version/live/ready, managed workers, and create NO actual studies
   until a later separate operator-controlled canary.
4. On backend rollback, keep PostgreSQL at 0204 and switch to the retained
   **e130f82d4** bridge. Its startup accepts structurally complete 0204.
   Do not blindly downgrade database or delete evidence.
5. Alembic downgrade to 0203 is deliberately destructive only if the new
   tables contain zero rows; it aborts when any study/assignment exists.
6. If Postgres migration fails before activation, stop. Restore verified
   database state by DBA review; no automatic data cleanup/overwriting.

## Validation

- Migration CREATE, constraints, structural bridge, single backward-compatible
  downgrade on empty scratch DB; nonempty downgrade refusal.
- Registration/permission/tenant/extra-field checks, idempotency, conflict,
  source revocation, historic job refusal, keyword matching, cross-study
  collisions and no raw source disclosure.
- Extended Agent Harness and API startup regression; no existing external API
  or new model usage.
- CI baseline may have preexisting unrelated Etsy/UI failures; classify them,
  do not weaken tests. Production migration jobs must pass separately.

## Future Phase 3C.3

Add server-side trial activation at *pre-generation* Stage 0/1 boundary,
fenced and durable exposure records, blinded QA, cost/reject telemetry, and
candidate evaluation on true disjoint holdout samples. Require reviewed human
promotion and reversible canary rollout only after objective improvement.
