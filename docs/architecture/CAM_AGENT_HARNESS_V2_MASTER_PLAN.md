# CAM Agent Harness V2 - Master Implementation Plan

Version: 1.1 | 2026-11-07 | Status: PHASE 0 STATIC AUDIT PARTIAL / PHASE 1 PURE CORE TESTED

## Vision and decision

CAM remains the system of record and production platform. PostgreSQL + existing processing queue are authoritative; retain idempotency, worker leases, outbox, asset lineage, Output History, Pick/Reject, QA and rollback. Introduce a compact agent registry, capability-aware executor interfaces, evidence-gated learning, and measured optimization. CodeLocal is a reference and optional engineering executor, not a replacement for CAM or a second authoritative database.

No direct integration of CodeLocal Cloud, Gas Town, or DeepSeek Harness into the CAM production core. CodeLocal public multi-agent activation is not assumed production-ready. Source-code reuse requires Apache-2.0 attribution and independent validation of copied components. Do not copy Go packages into Python.

## Guardrails

- New features roll out behind disabled-by-default flags; legacy jobs keep their pinned engine/contract. Do not delete modules while pending/retry jobs reference them.
- Every agent gets a versioned definition, capability allowlist, explicit tool scopes, bounded concurrency, retry limits, and cost ceiling. Agent definitions are not arbitrary executable prompts.
- Treat learning observations as untrusted; never promote a claim based solely on model text, a single Pick or QA guess. Reject/expire stale rules. Track source hash, cohort, sample size, outcome, policy version, confidence, reviewer, and evaluation evidence.
- Keep tenant/source/RBAC policy server-side. Never allow a learned recipe to broaden privileges or modify production credentials or data.
- Minimize API spending; deterministic code first, Codex CLI default, browser executor opt-in, no model/paid API for simple filtering, hashing, sync, retries or metrics.
- For all releases: merge to main, run tests, deploy through reviewed scripts, verify live health + smoke and maintain current+previous rollback. No changes to production jobs during evaluation without positive gates.

## Architecture

CAM UI -> FastAPI orchestrator -> existing Processing Queue & PostgreSQL
    -> Agent Registry (role, capabilities, policy, version)
    -> Capability Router (required features, preferred executor, fallback)
    -> Scout / Design / Image / UGC / Engineering on-demand workers
    -> Shared QA + Asset Store + Event-based feedback
    -> Evidence Gate -> Versioned Knowledge Rules -> eval -> canary -> promote
    -> Output History and metrics

Offline Windows Scout may retain SQLite local outbox/checkpoints, but backend PostgreSQL is authoritative for display and final ingestion. Do not create duplicate remote queues or rewrite the windows tool's existing resource gate.

## Implementation backlog - executable work packages

| ID | Priority | Deliverable | Entry dependency | Definition of done |
| --- | --- | --- | --- | --- |
| CAM-000 | P0 | Inventory exact production current/main release, jobs, active engine routes and previous rollback | None | Baseline version, changes and queues recorded; no live mutation |
| CAM-001 | P0 | Map Stage 2 runtime paths by slot and task version; pending/retry references | CAM-000 | Dependency matrix + proof of invocation from logs and code |
| CAM-002 | P0 | Baselines: Stage2 p50/p95, retries, tokens, cost/accepted image; Scout p95 latency/CPU/network; Etsy sync lag | CAM-000 | Reproducible metrics windows and thresholds |
| CAM-003 | P0 | Agent Registry V1, schema contract and pure unit tests | CAM-000 | Safe registration, capability constraints, version pinning, deterministic routing |
| CAM-004 | P0 | Agent definition persistence and API with tenant/RBAC enforcement | CAM-003 | PostgreSQL migration + negative authorization tests; flag off |
| CAM-005 | P0 | Shared runtime contract for Codex CLI and browser extension | CAM-003 | Capability probe, start/poll/cancel/resume, artifact and error normalization |
| CAM-006 | P0 | Scout resource gate audit and upgrades | CAM-002 | Four concurrent agents share limits without starving other tasks |
| CAM-007 | P0 | Etsy SQLite <-> backend sync reconciliation | CAM-002 | Cursors, checksums, visible lag and recovery diagnostics |
| CAM-008 | P0 | Stage2 input contract (stock hat, saved reference, design master, placement, engine version) | CAM-001 | Explicit versioned contract; golden regression fixture |
| CAM-009 | P0 | Stage2 single supported new-job path per view; QA and mask checks | CAM-008 | Front, 3/4, side improve or match baseline; rollback |
| CAM-010 | P0 | Durable step checkpoints with lease fencing | CAM-004 | Crash/retry cannot duplicate externally visible output |
| CAM-011 | P1 | Event-based feedback capture, idempotent observations | CAM-004 | Pick/Reject, QA, corrections, cost and asset lineage preserved |
| CAM-012 | P1 | Shared verified experience store | CAM-011 | Candidate->validated->promoted/rejected; provenance and TTL |
| CAM-013 | P1 | Context compiler and prompt versioning | CAM-012 | Small deterministic task context; mandatory rules retained |
| CAM-014 | P1 | Typography learning from Pick/Reject across keyword similarity | CAM-011 | 1/4/4 retained; guarded champion/challenger evaluation |
| CAM-015 | P1 | Scout opportunity score & resume keyword-tree/suffix | CAM-011 | Resumes productive branches, not initial seed repeatedly |
| CAM-016 | P1 | Reviews/TikTokShop shared Scout/UGC engine configurations | CAM-006 | Source-specific adapters preserved and no user regression |
| CAM-017 | P1 | Routing optimization with verified outcome and cost | CAM-005, CAM-012 | No unsupported executor selected; quality gates hold |
| CAM-018 | P1 | Agent Control Center (responsive) | CAM-004 | Role, health, jobs, errors, learning versions, pause/retry |
| CAM-019 | P2 | Engineering maintenance agent through CodeLocal | CAM-005, CAM-010 | Sandboxed/mediated tools, independent tests, no autonomous prod privilege |
| CAM-020 | P2 | Learning policy canary/auto-promotion with rollback | CAM-012, CAM-014 | Holdout eval, sample bounds, drift and reversible policies |
| CAM-021 | P2 | Remove proven-unused branches, flags and old settings | CAM-001, CAM-009 | Zero dependency references, historical job compatibility, rollback |

## Phase sequence

PHASE 0: Inspect read-only baseline and dependency map (CAM-000 to 002).
PHASE 1: Pure Agent Registry and explicit policy/contract first; no job cutover (CAM-003).
PHASE 2: Versioned database + runtime integration under flag; negative tests (CAM-004/005/010).
PHASE 3: Feedback collection, Experience promotion gate, bounded contexts (CAM-011/012/013).
PHASE 4: Product learn/QA and resource optimizers, gated per-cohort rollouts (CAM-006 through 017).
PHASE 5: Minimal UI and controllable engineering improvement agent (CAM-018/019).
PHASE 6: Compatibility audit, legacy cleanup (CAM-020/021).

## KPI contract

- Stage2 median time per keyword target >=30% improvement vs measured baseline; p95 should not regress.
- QA acceptance and source-image preservation: no regression vs golden test set; strict text/layout preservation for Slot 1-3.
- Zero observed duplicate accepted outputs per logical idempotency key and zero lost jobs in fault-injection trials.
- Four Scout agents must not materially increase unrelated job p95/timeout; measure lock wait, page load and CPU separately.
- Etsy Diagnostics reconciliation must detect and recover gaps, preserving offline durability.
- Per accepted output cost is tracked and should decrease; learning must not increase rejected outputs.
- Rule promotion requires explicit objective evidence and evaluation, not just self-reported confidence.

## Deployment and rollback

For every phase: preserve current/previous release only; capture commit and feature flag state; migration downgrade plan; deploy only after unit/integration/security checks. Stage2 new engine canary 1% -> 10% -> 50% -> 100% only after golden QA and real telemetry at each gate; percentages illustrative, not enabled today. Disable flags before reverting code if runtime incompatibility found. Never delete production data or rewrite existing jobs during rollback.

## Initial implementation record (2026-11-07)

- Verified local source via CodeLocal on creativeasset. The available main worktree at .cam-creative-brief-v1 is commit 646f4695; workspace root is a different historical branch and contains multiple nested worktrees.
- Source inspection confirms existing processing service/repository lease/idempotency/outbox model and separate Scout network_load.py shared locking in other checkout.
- CAM-003 pure no-side-effect Python agent registry and an evidence promotion gate are implemented in apps/api/app/modules/agent_harness. 14/14 stdlib unit tests passed on Python 3.12. No API routes, migrations, flags, worker registrations or production behavior are changed in this slice.
- Static dependency findings and outstanding live verification are documented in docs/architecture/CAM_AGENT_HARNESS_V2_PHASE0_AUDIT.md.
- Production current commit and live metrics are NOT claimed validated in this document. Finish CAM-000/001/002 before runtime cutover.
