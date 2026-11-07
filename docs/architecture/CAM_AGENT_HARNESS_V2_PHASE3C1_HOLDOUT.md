# CAM Agent Harness V2 — Phase 3C.1 Prospective Holdout Contract + Evaluation Readiness

Date: 2026-11-08
Scope: Backend-only, zero-AI, no schema migration, no experiment activation

## Purpose

Separate *human preference signals* from *verified design quality gain*. Pick/Reject
records can produce bounded candidate learning observations, but they cannot
prove a policy improves the resulting design. This slice introduces an offline
experiment/QA contract and an authenticated read-only readiness endpoint.

**No existing candidate is automatically promoted or used by Stage 1.**

## Prospective evaluation design (not yet activated)

`agent_harness/holdout.py` provides a pure deterministic keyword-level
assignment of a candidate-design trial into `control` (existing policy) and
`challenger` (proposed policy, default 20%). It hashes:
`tenant_id + cohort + trial_version + stable_keyword_id`.

Important boundaries:
- Assignment must be committed **before generating any outcomes** to prevent
  post-hoc cohort selection and label leakage. The current code does *not*
  register trials, freeze exposure manifests, schedule designs, or activate
  challenger policy. It is a planning kernel only.
- Each keyword's entire family of output variants remains in one arm. Never
  split per image, since a keyword's nine Stage 1 outputs are correlated.
- Trial version and tenant scope must be pinned and immutable in the future
  database-backed trial controller. A hash alone is not anti-tampering security.
- Never use the previous Pick/Reject votes that formed the candidate as
  prospective holdout outcomes. Need newly generated outputs, independent
  blinded QA, provenance and disjoint control/challenger cohorts.
- No new Codex CLI, ImageGen or browser extension calls are introduced.

## Objective QA gating (offline, untrusted until backed by telemetry)

The pure `assess_independent_holdout` function takes two QA arms
(`reviewed`, `accepted`, `independent_keyword_count`, `critical_defects`,
`cost_per_accepted_usd`) and eight independent provenance attestations:
pre-registration before generation, frozen assignments, verified source
lineage, disjoint keyword groups, disjoint evaluation assets, blinded and
independent QA, security passed and regression-free.

Safety bounds:
- At least 40 QA-reviewed results and 10 separate keyword groups per arm
  (policy cannot be loosened below 30/8).
- Challenger must have no critical defects, not regress cost beyond 10%
  (bounded policy allows 1.00–1.25 as an explicit override), and show a
  positive conservative improvement of at least 2 percentage points.
- Conservative improvement = lower 95% Wilson confidence bound on challenger
  QA acceptance − upper 95% Wilson bound on control. This is deliberately
  stricter than comparing raw Pick rates and is not a substitute for
  independent study design or correlated-data analysis.
- Any missing attestation blocks evaluation. Low samples are
  `needs_more_samples`; regressions are `no_verified_gain`; favorable
  independent evidence is at most `manual_review`.
- `promotion_allowed` is **always false**, including successful offline
  assessment. Only a separate audited owner-approval workflow may eventually
  change policies after re-checking telemetry and source lineage.

The function does not trust caller-provided booleans or numbers as factual
evidence. Future server code must derive them from immutable, tenant-owned,
independent QA/experiment records. This slice does not add such records.

## API: candidate evaluation-readiness, not validation

`GET /api/v1/admin/agent-harness/{tenant_id}/feedback/stage1/evaluation-readiness?cohort=music&limit=20`

- Requires `AGENT_HARNESS_READ_API_ENABLED` (false by default),
  `ai_operations.read` AND `ai_provider.configure`, and tenant scope.
- Limits to 30 candidate items from the existing Phase 3B.2 audit.
- Re-uses current Stage 1 evidence provenance audit; does not recompute
  from untrusted client payloads.
- Returns bounded reasons preventing promotion: source is stale, prospective
  trial not pre-registered, blinded independent QA unavailable, disjoint
  evaluation not verified, improvement not verified, owner approval absent.
- Does not disclose source keyword, image, reviewer, prompt, raw artwork hash,
  or source event ID. No write, replay, queue, AI provider or extra job occurs.
- `prospective_trial_registered`, `independent_qa_verified`,
  `automatic_promotion_enabled` and `agent_execution_enabled`
  remain hard-off; this endpoint is advisory.
- Emergency stop remains effective for any future execution. Read-only audit
  is intentionally available for diagnostics.

## Tests / canary / rollback

- Strong negative tests for unsafe parameters, forged/disabled attestation,
  small samples, cost regressions, critical QA defects, insufficient statistical
  gain, duplicate/outcome-laundering concerns and lack of automatic promotion.
- Targeted tests for authorization, tenant isolation, source revocation and
  lack of raw source disclosure. Existing Stage 1 and Agent Harness tests must
  pass. CI baseline has pre-existing unrelated Etsy/UI issues; classify them
  without skipping or weakening any security tests.
- Ship code through `main`, pinned commit, standard backend-only deployment
  with `--no-migrate`, health/version/worker verification. Keep all feature
  flags unchanged.
- Revert code via retained previous release without DB rollback.

## Next phase: 3C.2 / 3C.3

1. Immutable prospective study registration and control/challenger assignment
   persisted per tenant/keyword *before* the first production generation.
2. Independent blinded QA data pipeline and exact source/artifact provenance
   (no reused candidate/holdout samples), quality + spend telemetry, confidence
   and independent-keyword-level uncertainty metrics.
3. Read-only matched comparison review panel, plus signed/audited human approval
   with canary-specific rollback gate. Only after all gates can a learned rule
   influence new Stage 1 prompts. Existing queued/pinned jobs remain untouched.
