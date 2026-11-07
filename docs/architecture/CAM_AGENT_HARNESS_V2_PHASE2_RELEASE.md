# CAM Agent Harness V2 - Phase 2 release gates

Status: Phase 2A code-only candidate, Phase 2B schema held pending compatible rollback
Date: 2026-11-07

## Continuous deployment rule

Only deploy one independently reversible slice at a time. Source remains main-only for CAM production. Tests, commit, backend release health and the current + previous rollback target are mandatory. New agent features default off, and no model/job is started without later evidence and feature canary gates.

## Phase 2A: Safe dormant API scaffold (code-only, no database migration)

- Adds strongly typed Settings flags AGENT_HARNESS_READ_API_ENABLED=false and AGENT_HARNESS_EXECUTION_ENABLED=false.
- Adds ORM mapping, tenant-filtered repository and authenticated read-only API route `GET /api/v1/admin/agent-harness/{tenant_id}/overview` (default responds 404).
- New API accepts only existing ai_operations.read permission, restricts active tenant, exposes bounded agent metadata and candidate/promotion counts only.
- No create/update/dispatch/promotion endpoints. No worker integration, queue changes, new DB tables, migrations, new credentials, or model APIs.
- Tests create the isolated new tables in in-memory SQLite and prove cross-tenant separation, immutability, default-off API, denied permissions and idempotent candidates.
- Feature remains OFF in production even if users have admin permission. **Do not enable the read flag until Phase 2B tables exist and are verified**.

## Phase 2B: PostgreSQL schema and feature activation (held)

- Additive migration `0202_cam_agent_harness_store` creates dormant `cam_agent_definitions` and `cam_agent_experiences` with tenant-specific composite PKs, constraints, indexes, FK and no backfill or automatic learning.
- Migration tests must demonstrate upgrade, downgrade on empty isolated DB, and old Etsy migration regression.
- Critical rollback hazard: native CAM backend startup validates exact Alembic HEAD. A currently deployed 0201 release will reject schema HEAD 0202 on rollback. This means **do not deploy migration 0202 until a reviewed rollback-compatible release/procedure is in place**. The backend script does not downgrade the database.
- A migration rollback involving real experience records is a destructive data operation and requires separate reviewed export/restore, not an automatic script.
- Schema rollout is not equivalent to enabling agent dispatch; that remains a later canary.

## Phase 2C: Active tenant-scoped management (not yet)

- Add authenticated write API with explicit configure permission and audit provenance, or a trusted worker-only registration path.
- Promotion must verify actual event provenance, holdout set independence, QA lineage and tenant RBAC; the pure promotion function is not sufficient evidence.
- Add Executor Adapter lifecycle and job version pinning to existing processing queue under flag before any live task routing.

## CI baseline and gates

The globally red GitHub Actions CI baseline predates this feature (stale Skill Manager UI selector and other unrelated tests). Do not hide or skip failures. Require Agent Harness targeted tests, existing API smoke, migration tests where applicable, diff checks, full CI failure classification, production health and rollback verification. If a required release gate fails, hold production activation and document the reason.

## Feature flag and rollback

Disable AGENT_HARNESS_READ_API_ENABLED to hide read API. AGENT_HARNESS_EXECUTION_ENABLED is reserved and never read by workers in Phase 2A or 2B.
For Phase 2A, backend rollback returns to previous code release with the same existing Alembic HEAD. For Phase 2B, a separate schema-compatible rollback plan must be demonstrated before deploy.
