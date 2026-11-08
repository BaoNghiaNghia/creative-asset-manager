# CAM Agent Harness V2 — Phase 3C.3B0: Evidence-Ledger Rollback Bridge

Date: 2026-11-09. Scope: **0204 backend-only bridge**. This release adds **no migration** and runs **no experiment**.

## Purpose and deployment order

The existing 0204 study/keyword reservation tables do not prove a policy was applied, nor that a reviewer independently accepted an artwork. A future 0205 migration will add two immutable, tenant-scoped ledger tables, with deliberate separation between assignments, **actual applied treatment** and **blinded QA evidence**. **Before** introducing revision 0205, the 0204 backend must learn how to read and safely start against exactly that newer schema during rollback.

Phase 3C.3B0 makes `validate_alembic_head` accept `0205_cam_agent_trial_evidence` **only when** the pre-existing 0203 and 0204 schemas and all expected new columns, PKs, tenant-study-keyword foreign keys, per-job/output uniqueness and required constraints match. Other revisions or degraded schemas continue to fail closed.

## Frozen planned 0205 contract

- `cam_agent_trial_exposures`: composite PK `tenant_id, study_id, keyword_id`; composite FK to reserved assignment; FK to processing job; UNIQUE `tenant_id,processing_job_id`; immutable applied arm, policy version/digest, worker identity and application timestamp.
- `cam_agent_trial_qa_reviews`: composite PK `tenant_id,review_id`; composite FK to verified exposure; FK to actual Stage 1 output version; UNIQUE `tenant_id,output_version_id`; immutable artwork SHA256, blinded review token digest, reviewer ID, accepted/rejected verdict, critical defect and review timestamp.
- A future service must verify job tenant/keyword lineage (the single-column job FK alone cannot enforce tenant ownership), server-authoritative applied policy, reviewer independence from generation/training, output version ownership and identical data on idempotent retries. Recording a table row alone **never** proves these claims.
- No manual raw QA upload, user-supplied policy, remote prompt, agent execution or client-derived cost metrics may populate these tables.
- Provider cost telemetry and confidence testing will be a **separate** verified source of truth. This schema does not claim cost telemetry or independent QA is already available.

## Guarantees

- No new `ProcessingJob`, AI request, Stage 1 policy, API endpoint, feature flag, database write, or migration.
- No current reservation or trial state changes. A `trial_shadow_admission` remains **non-exposure**.
- No change to generation, worker execution or automatic feedback/promotion.
- No external dependencies or new credentials. Production release uses `main`, `--no-migrate`, normal health checks and current+previous retention.

## Rollback / follow-up

1. Deploy this bridge while PostgreSQL remains at 0204; the earlier 0204 release remains rollback.
2. After verifying production HEAD and health, a separate reviewed 0205 additive migration may be deployed with its capture service still OFF.
3. On rollback after 0205, switch to **this** bridge; do not downgrade a nonempty evidence ledger. An earlier 0204 without this bridge is not an approved rollback target once the schema reaches 0205.
4. Test exact reviewed future tables and reject missing FK/unique/check constraints, absent tables, unknown revisions and multi-revision rollback.

Phase 3C.3B1 must implement and verify the migration. Only later, with a treatment-aware worker and independent QA review service, may actual `exposed` assignments and held-out outcomes be recorded.
