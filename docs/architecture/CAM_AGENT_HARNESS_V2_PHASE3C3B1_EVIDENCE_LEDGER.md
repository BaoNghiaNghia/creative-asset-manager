# CAM Agent Harness V2 — Phase 3C.3B1: Inert Exposure and Independent QA Ledger

Date: 2026-11-09. Dependency: reviewed 0204→0205 bridge `610b3ddda` must already be the **production-current rollback-capable binary**.

## What this release changes

Migration `0205_cam_agent_trial_evidence` is additive and starts **with zero records**:

1. `cam_agent_trial_exposures`: one immutable intended worker-applied treatment per tenant/study/keyword. A real producer must prove that the control/challenger policy was actually selected and applied immediately before the provider call; `trial_shadow_admission` never qualifies. FK to registered keyword assignment, FK to ProcessingJob, UNIQUE tenant+processing job, pinned policy version and digest, worker identity and server applied time.
2. `cam_agent_trial_qa_reviews`: one blinded QA verdict per tenant/output. FK to exposure, FK to immutable Stage 1 output version, UNIQUE tenant+output, artwork hash, blinded token hash, reviewer identity, restricted verdict and defect flag, server review time.

No exposure writer, QA submitter, cost/usage attestation, candidate evaluation or promotion endpoint is provided in this release. No current code inserts these tables, and their presence **is not independent QA validation**.

## Important provenance gaps that the future service must close

- A single-column FK to `processing_jobs.id` or `rrugc_image_output_versions.id` does not by itself enforce tenant ownership. The future trusted writer MUST verify tenant, study, keyword, exact ProcessingJob lineage, source revision, and actual output ownership in one transaction; reject stale/other-tenant/past-output identities.
- The assignment arm has to match the real treatment applied. A reserved keyword, client claim, historical Pick/Reject, or shadow manifest never qualifies as a verified exposure.
- A reviewer MUST be independently authenticated, blinded to treatment, and disjoint from generation and candidate training reviewers. UI Pick/Reject is not independent QA.
- Artwork `artifact_sha256` must come from trusted output bytes; a client-supplied hash must never be accepted as verification.
- Cost data must be obtained separately from authenticated provider usage or a trusted local meter, not from form submissions, vague guesses or zero-cost assumptions.
- The pure Phase 3C.1 comparison cannot be fed guessed, unreviewed or user-supplied aggregate data. Production promotion remains disabled.

## Rollout

1. Confirm previous/current backend is `610b3ddda` bridge and database remains at 0204.
2. Run upgrade/downgrade/refusal tests and Agent Harness regression against disposable DB, including FKs, uniqueness and shadow-is-not-exposure.
3. Deploy committed `main` with normal script **with migrations enabled**; verify PostgreSQL is at 0205, all workers/API ready, and ledgers empty.
4. Keep all Agent Harness execution/trial admission and auto-promotion feature flags OFF.
5. Rollback application code to the retained **610b3ddda** bridge if necessary; **do not downgrade the database automatically**. Downgrade to 0204 rejects nonempty evidence tables.

## Dependency, cost and safety

No new dependency, credential, external API, provider request, user-facing UI, agent scheduler, AI policy or processing job type. No existing Stage 1/2 output is deleted, modified, or reclassified. Only two empty indexed ledger tables are created. Trial activation and independent QA are **not yet implemented**; the next phase must implement verified worker receipts and independent reviewer service under separate reviewed gates.
