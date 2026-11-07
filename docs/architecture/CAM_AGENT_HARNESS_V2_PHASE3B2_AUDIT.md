# CAM Agent Harness V2 — Phase 3B.2 Evidence Audit

Date: 2026-11-08 | Status: backend-only, read-only, production flag default OFF

## Scope

Provide an authenticated and bounded operator audit of stored Stage 1 learning
candidates, without requiring any agent to run and without accepting historical
AI confidence as proof of quality.

This slice intentionally does **not** implement independent holdout QA,
statistical causal inference, automatic policy promotion or execution. It is
a preflight visibility and source-provenance gate only.

## Endpoint

GET /api/v1/admin/agent-harness/{tenant_id}/feedback/stage1/evidence-audit?cohort=music&limit=20

- Existing `AGENT_HARNESS_READ_API_ENABLED` remains false by default.
- Requires both `ai_operations.read` and `ai_provider.configure` AND exact tenant authorization.
- Cohort is one strict enum from music, western, pets, healthcare, coffee, family, outdoors.
- Limit 1..30, default 20. Returns `has_more` only; no offset/streaming of unlimited rows.
- Returns aggregate status counts and per-candidate opaque event key, whitelisted
  rule key, agent version, count, confidence, current source state and reason.
- Returns **no** source keyword, reviewer, user ID, image hash, raw event IDs,
  original asset identifier, prompt or personally identifying information.
- Always reports `automatic_promotion_enabled=false`, `agent_execution_enabled=false`,
  `holdout_verified=false` and `independent_qa_verified=false`.
- Read-only even under emergency stop, to allow operators to diagnose disabled workflows.

## Verification contract

For each of at most 30 stored Stage 1 candidate/rejected/promoted records:

1. Verify tenant, feedback-only agent ID and explicit cohort before loading the record.
2. Re-derive the current source from tenant-owned RRUGC Stage 1 votes using
   Phase 3A's 180-day, 800-output, QA, artwork-hash and per-keyword bounds.
3. Compare full candidate identity: deterministic event key, rule, scope,
   sample count, confidence, ordered source event IDs, artwork hashes and empty
   verification references.
4. Mark missing/modified/un-done source evidence `stale_source`. Mark historical
   unknown source kind `unsupported_source`, unexpected verification references
   `unexpected_verification`, existing revoked candidate `rejected`, or
   surprising pre-promoted record `promoted_requires_review`.
5. The service does not change the stored status, auto-resurrect a rejected
   candidate, issue approval or trigger any worker. The Stage 3B outbox remains
   responsible for durable invalidation under its separate canary flags.
6. Cache recomputed snapshots per agent version within one request; at most three
   versions get rechecked. Older versions are explicitly `not_checked`.

`source_current=true` proves only that current *metadata-based* evidence matches
its earlier snapshot. It does not independently verify asset bytes, run a holdout,
establish causality, or warrant a production learning policy.

## Rollout and rollback

- Release from pinned `main` commit using approved backend deploy script
  `--no-migrate`. No database schema, user data, source queue, Stage 1 voting
  workflow, API costs, provider credentials, CLI, extension, or frontend changes.
- Keep the read API default OFF. Enabling an authenticated operator read canary
  is a separate controlled configuration operation.
- Validate targeted tests, existing API startup/auth regressions, live
  `/version`, `/live`, `/ready`, all managed workers and current/previous.
- Rollback uses the retained backend release; no DB downgrade or data deletion.
- Before any live agent feedback promotion, build independent QA/holdout
  evaluations, audited operator approval, and robust post-promotion revocation.

## Tests

- Current source parity, absent/undone vote, unknown source record,
  unexpected verification reference, promoted anomaly, no mutation,
  strict request enum/limits, tenant scoping, both permissions, anonymous denial,
  empty result and zero raw evidence leakage.
- Targeted Phase 3A/3B regression tests must also pass.
- Main CI may have unrelated preexisting Etsy Spy fixture/navbar failures;
  classify them without disabling or weakening security gates.
