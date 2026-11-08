# CAM Agent Harness 0205 production compatibility incident

Date: 2026-11-09 (Asia/Ho_Chi_Minh). Scope: backend startup safety gate only.

## What happened

The additive Phase 3C.3B1 `0205_cam_agent_trial_evidence` migration was applied on production after the reviewed 0204→0205 compatibility bridge `610b3ddda` was deployed. The API failed startup with `DatabaseStartupError: Agent Harness evidence schema incomplete at revision 0205` in release `7c056ffe2`. Backend release validation caught the unhealthy API and the deploy exited nonzero.

Root cause: the introspection validator incorrectly required SQLite-style SQL `arm IN (...)` and `verdict IN (...)` strings. PostgreSQL rewrites these CHECK expressions into equivalents using `= ANY (ARRAY[...])` and can normalize boolean logic for the critical-defect constraint. An intact PostgreSQL schema was incorrectly classified as invalid. Since the preceding 0204 bridge used the same validator, switching directly to the previous release did **not** constitute a safe rollback.

## Recovery

1. Preserved PostgreSQL 0205 and all records; did not drop tables, disable migrations, modify permissions, or weaken data ownership rules.
2. Added narrow syntax-normalizing constraint checks, preserving named checks, allowed values, foreign keys, unique constraints and tenant/study/keyword lineage.
3. Added regression tests that simulate PostgreSQL-normalized SQL while still rejecting a named but semantically permissive CHECK.
4. Committed and pushed hotfix `6db036aa6` to `main`; deployed with `--no-migrate --no-cleanup`.
5. Production database compatibility, API live/ready/version, and all managed Image/Video/Visual worker health checks passed. No trial activation, QA recording, policy promotion or additional provider usage occurred.

## Rollback precautions

A release created before this normalization fix must **not** be used as the rollback target for a database at 0205. Keep the first healthy hotfix release, `6db036aa6`, as `previous` by deploying a separate compatibility-identical documentation update from main and checking its health. Only then remove the broken `7c056ffe2` release through the reviewed retention workflow.

Never automatically downgrade 0205, especially after recording any evidence; the 0205 downgrade intentionally aborts when ledgers contain rows. Future releases that change schema validators require both a disposable PostgreSQL integration test and a verified rollback release **before** migration.

## Follow-up

- Add a PostgreSQL-backed migration smoke gate (alongside SQLite) in CI to verify real dialect normalization and refuse an incompatible production release before activation.
- Improve pre-activation rollback pointer validation so a release that cannot start against the new database head is not treated as a usable rollback.
- Exposure/QA ledgers remain inert until a treatment-aware worker and independently authenticated blinded reviewer pipeline are reviewed separately.
