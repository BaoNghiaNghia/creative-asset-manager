# CAM Agent Harness — Phase 3C.3B2B: reviewed challenger treatment

Date: 2026-11-09. Baseline production commit: `c94143947`. No database migration; uses existing `0205_cam_agent_trial_evidence`.

## Purpose

A Phase 3C.3A shadow-only assignment is NOT an exposure. Previously the Creative Brief worker always generated the baseline prompt, even if the prospective study assigned the keyword to the challenger arm. This change introduces a **different, fixed, reviewed challenger policy** with an exact prompt receipt under an independent default-OFF feature flag. It does not implement independent image QA or any automatic promotion.

## Safety model

- `AGENT_HARNESS_TRIAL_CHALLENGER_ENABLED` defaults **OFF** and only takes effect with all existing READ/MANAGEMENT/TRIAL_REGISTRATION/TRIAL_ADMISSION/CONTROL_CAPTURE prerequisites enabled; emergency stop OFF; generic Agent Harness EXECUTION must remain OFF. Production environment flags are not changed by the release.
- Only a job with a committed, unchanged `trial_shadow_admission` payload and a true prospective `challenger` assignment enters this path. Control and ordinary Stage 1 jobs continue the existing prompt.
- The worker verifies the claimed processing lease, tenant, keyword (including digest), stored job payload, cohort, pre-registered study/version, deterministic arm assignment, candidate source digest and current human-feedback snapshot *before* calling Codex.
- Candidate `rule_key` is restricted to fixed `stage1:pick:{mode}:{variant}` and `stage1:reject:{reason}` enums. The reviewed `directive_for_rule` mapping contains only server-authored typography guidance. No arbitrary Experience JSON, raw votes, user instructions or client-provided prompt can become challenger policy text.
- `trial-144-ch1` is the treatment policy version, pinned against the baseline schema `meaning-first-144-v3`. Both arms generate the same 1/4/4 typed Creative Brief schema; the challenger adds one semantically distinct, bounded style directive to the existing `learning_context`, in the same *single* Codex request. No extra API call or paid provider fallback is added.
- After a successful Codex request, in the same transaction as the completed Creative Brief, the trusted worker checks that the treatment is still enabled and re-verifies assignment, source, candidate rule and immutable plan, then writes one unique exposure receipt (arm, policy version, SHA-256 of the exact prompt text, processing job ID and worker identity) and an audit event. Invalid/stale source fails closed; failure or timeout creates no exposure; retrying an identical receipt is idempotent.
- No raw prompt or source keyword is persisted into the exposure ledger or its audit. A control's historical shadow result never becomes a challenger receipt.
- The read-only evidence status endpoint remains an *audit*, not a trial evaluator. It still reports evaluation blocked, independent QA unverified and promotion disabled even after challenger receipts exist.

## Provenance and statistical limitations

Creative Brief provenance proves **text policy execution**, not that a Stage 1 output artwork is traceable to that treatment. The image worker uses a different processing job and output versions. Before any real independent QA/evaluation, implement an immutable link from the output version's Stage 1 job to its proven Creative Brief treatment, verify bytes at trusted storage and blinded reviewer independence, plus authenticated provider cost. The `0205` QA ledger currently has no trusted write path. Do not use human Pick/Reject votes as independent QA or estimate gain from unverified rows.

## Verification and rollout

1. Run strict policy enum tests, shadow admission/registration tests, Control/Challenger worker success/failure/replay/tamper tests, and broad Creative Brief/Agent Harness regressions with `PYTHONPATH` set to the isolated worktree.
2. Run Python compile and `git diff --check`. No dependency, new model migration, new key or UI change.
3. Merge/cherry-pick to `main`, push, deploy backend `--no-migrate`. Verify PostgreSQL remains at 0205, healthy API plus Image/Video/Visual workers, new flag **OFF**, and both current+previous releases PostgreSQL-0205-compatible. Never flip the flag as part of deployment.
4. Continue with a separately reviewed **3C.3B2C** end-to-end output lineage plus blinded independent QA/cost service before any A/B decision or automatic learning.
