# CAM Agent Harness V2 - incremental deployment and rollback

Date: 2026-11-07 | Revision 2 | Owner: CAM backend

## Current source-of-truth

- Phase 1: pure Agent Registry, fail-closed routing, verified Experience Gate in main and production.
- Phase 2A: tenant-scoped ORM, repository and a READ-ONLY overview route in main and production. Both `AGENT_HARNESS_READ_API_ENABLED` and `AGENT_HARNESS_EXECUTION_ENABLED` are false by default. No new tables created yet and no agent dispatch.
- As of 2026-11-07 13:45 VN, main is at `16bc02dc5`, while verified public API was `77919d0ab`; source has advanced since original plan. Active schema revision is now `0202_etsy_spy_later`, NOT `0201_etsy_spy_parent_keywords`.
- Original held `0202_cam_agent_harness_store` migration is obsolete/conflicting. Do NOT cherry-pick or deploy that revision; a unique successor `0203_cam_agent_harness_store` must follow Etsy `0202`.

## Two-step safe database deployment

### A. Bridge release, no database migration

1. Add a **narrow audited** compatibility check in `app/core/database.py`. Expected head for bridge is `0202_etsy_spy_later`. As an exceptional rollback transition, it accepts exactly `0203_cam_agent_harness_store` only if the two agent tables, minimum required columns, and tenant+agent+version foreign key are verified by SQLAlchemy inspection.
2. All other ahead, behind, partial, unversioned and multiple-head revisions still fail closed. This does NOT waive authorization, execute migration, start workers, or enable any feature flags.
3. Validate prior `0202` and exact `0203` with isolated SQL tests, plus negative schema and revision tests, existing API startup and Etsy migration suite.
4. Merge bridge to `main`, deploy backend using the reviewed script with `--no-migrate`. Confirm production `/version`, `/ready`, active workers and previous rollback. Keep the bridge current before touching the database.

### B. Additive migration release

1. Rebase held migration contents onto new HEAD: revision `0203_cam_agent_harness_store`; `down_revision = 0202_etsy_spy_later`. No data backfill, updates to Etsy/UGC, or import of production user data.
2. The two tables are `cam_agent_definitions` and `cam_agent_experiences`, with composite tenant scoped primary keys, foreign key, constraints and indexes. No agent is enabled by default; all experience rows remain candidates until verified separately.
3. Test upgrade and downgrade only in disposable isolated DB; also regression test Etsy 0194 through latest HEAD. Verify the normal predecessor/release bridge works with `0203` schema before deploy.
4. Merge `0203` migration to `main`, then run the reviewed backend deploy script with migrations enabled; do NOT alter production DB directly via a shell or bypass deployment guards.
5. The bridge becomes `previous`. If the new backend fails, rollback to the bridge without downgrading schema: bridge accepts exactly the safe additive `0203`. Preserve candidate data.
6. Only after verifying production table existence and RBAC, consider setting `AGENT_HARNESS_READ_API_ENABLED` on in a separate reviewed canary. `AGENT_HARNESS_EXECUTION_ENABLED` stays false.

### C. Future active agent workflow (not covered by this release)

- Authenticate author + tenant, allow only reviewed versioned definitions, make worker/queue integration explicitly opt-in, enforce tool and runtime lease policies.
- Independently verify source event authenticity, assets, QA, holdout independence and source tenant before Experience promotion; pure confidence alone is insufficient.
- Add on-call rollback, per-agent budget and QA metrics and stage-by-stage promotion. Preserve GatorHats image jobs and current Scout jobs.

## Key invariant: rollback is not database downgrade

Rollback to a previous **schema-compatible** backend is preferred; never automatically downgrade production database if candidate records or other authoritative data might exist. A destructive downgrade requires separately reviewed data export/restore and permission.

## CI / health gates

Do not hide or weaken tests. Main branch's full CI baseline has unrelated existing UI/access/fixture failures; compare failing jobs with the immediately preceding commit and record the classification. The changed modules must pass targeted auth, configuration, Alembic, PostgreSQL integration (if available), frontend unchanged, and no additional security failures. Confirm live `/live`, `/ready`, `/version`, optional read API default-off, backend workers and `current/previous` after release.
