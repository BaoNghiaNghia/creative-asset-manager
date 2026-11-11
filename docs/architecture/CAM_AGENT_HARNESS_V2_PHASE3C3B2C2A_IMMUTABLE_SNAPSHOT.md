# CAM Agent Harness — Phase 3C.3B2C2A: verified immutable artwork snapshot

Date: 2026-11-10. Source previously stopped at 3C.3B2C1 (metadata lineage
and opt-in Drive-byte verification). This is an isolated Stage 1 QA evidence
capture primitive, **not an independent review or an A/B evaluation**.

## Why

`cam_agent_trial_qa_reviews.output_version_id` (migration 0205) has a
restrictive FK to `rrugc_image_output_versions.id`. Writing to it while
Stage 1 Regenerate deletes old output versions risks breaking production.
Phase 3C3B2C2A MUST NOT write that table or make it part of QA conclusions.

## Implemented, default OFF

- Additive schema revision `0207_cam_agent_trial_artwork_snapshots` follows
  `0206_etsy_spy_sms_status`. New table has **NO foreign key** to
  `rrugc_image_output_versions` and retains immutable historical output ID,
  tenant-scoped exposed trial FK, verified SHA-256, dimensions, original image
  job/Brief job, an independent stored-copy identity and an explicit
  `reserved -> verified` seal state. Upgrade creates **zero rows**.
- New flag `AGENT_HARNESS_TRIAL_ARTWORK_SNAPSHOT_ENABLED=false`. Also requires
  read/registration/byte-verify flags, no emergency stop, tenant + both
  `ai_operations.read` and `ai_provider.configure` permissions.
- `GET .../artwork/{output_version_id}/snapshot`: read-only historical copy
  status, even if the original output has since been Regenerated.
- `POST .../artwork/{output_version_id}/snapshot`:
  strict `{dry_run:true,reason:"..."}` by default; only explicit
  `dry_run:false` under all flags performs a copy.
- Trusted lineage is re-evaluated before and after Drive I/O. The original
  file is streamed with a 16-MiB cap, full SHA-256, PNG integrity and decoded
  dimension validation. Separate stable Drive asset ID is used for the QA
  copy, then the copied remote bytes are independently re-read and verified.
- Before network I/O a DB reservation is committed. A timeout/crash or
  mismatch remains `reserved`, **never auto-retries/overwrites**. Manual
  reconciliation is required. Verified exact replay does not duplicate upload.
- Sealing appends a tenant-scoped audit event in the same transaction.
  Neither keyword/arm/prompt nor provider credential is returned in the
  public status.
- No new worker queue, model call, paid API, learning promotion, reviewer
  token or silent backfill.

## Remaining blockers — NOT implemented here

- Independently authenticated reviewers, blind assignment and independent
  QA verdicts, with a new snapshot-bound QA ledger (NOT existing 0205 FK).
- Trusted provider cost capture linked to actual Stage 1 image job/attempt.
- Statistical experiment readiness, safety thresholds, drift and any
  policy promotion. All still blocked.

## Rollout/rollback requirements

**Do not deploy 0207 over a 0206 previous binary without a compatible bridge.**
A previous 0206 release normally refuses a DB at 0207.

1. First deploy an isolated **0206-compatible bridge release** containing the
   reviewed `_verified_snapshot_successor` check, while 0207 migration is
   absent and DB remains at 0206. Verify API/all workers healthy.
2. Make this bridge the retained `previous`. Only then deploy a release
   containing 0207 with migration and the new model/endpoints (flag OFF).
3. Test PostgreSQL 12 and 16 schema constraints, migration, startup and
   downgrade refusal. Confirm **zero snapshots** immediately after migration.
4. Verify all nine managed services, exact current/previous release,
   Stage 1 Regenerate/Output History, Etsy SMS statuses and no QA rows.
5. Do not enable snapshot writes or QA without a separately reviewed canary
   and independent reviewer/cost integration.
6. If DB contains a snapshot, migration downgrade refuses to erase it.
   Roll back **code-only** to the bridge while keeping the exact 0207 schema.
   Never delete trial evidence or source/output files to force a downgrade.

## Local verification

- `test_agent_trial_artwork_snapshot_migration.py` checks DDL, constraints,
  no output FK, empty upgrade, historical 0206 bridge and safe downgrade.
- `test_agent_trial_artwork_snapshot.py` checks verified copy integrity,
  idempotence, crash quarantine, Output History deletion survival, tenant
  scoping and default-OFF mutation.
- Run prior `test_agent_trial_artwork_lineage.py`, challenger, evidence and
  database startup tests before any production rollout.
