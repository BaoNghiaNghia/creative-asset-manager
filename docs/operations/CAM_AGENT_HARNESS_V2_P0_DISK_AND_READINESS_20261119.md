# CAM Agent Harness V2: storage cleanup and P0 operational readiness (2026-11-19)

## Constraints respected

- Use the existing CAM PostgreSQL and Processing Queue. No new database,
  schema migration, backup, duplicate queue, model API or agent dispatch.
- Backend deployment uses `--no-migrate --keep-releases 2` and retains the
  current release plus one verified rollback. Frontend already retains two.
- Do not delete PostgreSQL, active Docker/Elasticsearch volume, model weights,
  Codex profile or credentials, generated media, historical job records,
  existing database backups or any dirty/unmerged worktree.
- No modification to existing backup-timer policy: this release simply does
  not create an additional backup.

## Cleanup evidence

Read-only audit: root volume 50 GB, initially 5.8 GB available (88% used).
The reviewed `cam-safe-disk-cleanup.sh --dry-run` found zero obsolete
backend releases, and identified the protected backend versions
`33f8790df` (current) and `74437a4b2` (previous).

Applied only safe, rebuildable cleanup:

1. CAM pip download cache: ~176 MB to ~4.8 MB, 443 cache files purged.
2. Archived systemd journal: one 57.5 MB historical archive was vacuumed.
3. npm package cache: `npm cache clean --force` after confirming no
   npm install/build command was active. Already installed node_modules
   remain untouched unless a separately proved old worktree is removed.
4. apt downloaded package cache and old archived journal by the existing
   `cam-qa-disk-audit.sh --clean-safe` operator workflow.
5. Seven detached, loopback-only Vite QA servers, each over 48 hours old,
   were stopped by a strict classifier. That scanner now recognizes both
   PNPM and `node_modules/.bin/vite`, still refusing production
   services, foreground Vite, active shells, network-facing servers, and
   young processes.
6. Two clean, fully merged, idle historical worktrees (2026-11-13 TipTap
   and 2026-11-14 Design Modal) were removed via non-force
   `git worktree remove`, approximately 509 MB combined. No dirty or
   unmerged worktree was removed.

After cleanup: about 7.0 GB free (~86% disk used). RAM available increased
from approximately 2.7 GB to 3.4 GB. These are observations, not guaranteed
free space under future workload. A production backend build may require
staging space and the existing deployment preflight must not be bypassed.

## Agent Harness V2 P0 read-only diagnostic

`GET /api/v1/admin/agent-harness/{tenant_id}/operational-readiness`

Authorization: existing tenant scope, both `ai_operations.read` and
`ai_provider.configure`, and `AGENT_HARNESS_READ_API_ENABLED` opt-in.

Output is sanitized and tenant-bound: open ProcessingJob aggregate counts,
local filesystem available MiB and pressure classification, feedback feature
flags, and blockers. No job/prompt/raw payload, secret, asset or remote URL.
No record/lease reservation, write, backup, database or provider call.
`dispatch_allowed=false` and `automatic_promotion_enabled=false`
remain true safety invariants even when the execution flag is configured.

The disk headroom floor is a non-authoritative advisory (at least 4 GiB
available and under 90% used), not permission to deploy or allocate a
browser slot. Real agent dispatch still requires a separately verified,
transactional capacity reservation bound to a ProcessingJob lease.

## Phase 4C incremental hardening: shared Image Agent claim fencing

Updated the *existing* `POST /api/v1/etsy-spy/image-agents/{agent_id}/claim`
without creating a second queue, database, schema table or stored reservation:

- PostgreSQL's stable tenant-scoped `pg_advisory_xact_lock(bigint)` now serializes
  the check of outstanding Image Agent leases, per-tenant daily claim quota,
  and assignment of a new ProcessingJob. The lock ends at transaction commit
  or rollback. SQLite remains an in-memory test-only exception.
- Do **not** use an EtsySpyAgent row lock for this purpose: other endpoints
  lock ProcessingJob first and update EtsySpyAgent second, and the reverse
  lock order risks a deadlock.
- More than one unresolved processing job owned by the same paired device
  produces `pause_reason=conflicting_processing_claims` and no new assignment.
  The operator must reconcile old duplicate claims before continuing.
- An expired owned job is reclaimed first with `FOR UPDATE SKIP LOCKED`,
  a new attempt and new per-attempt token. If it is no longer eligible,
  the device pauses instead of silently accepting a second job.
- The already-active lease replay contract, cancellation, tenant scope,
  agent credential revocation and immutable result receipts remain intact.
- The historical evidence verifier now imports its required UTC timedelta;
  timestamp tests normalize SQLite's stripped timezone, and expiring-lease
  cases obtain a new future deadline at execution rather than collection.
- Regression: Etsy Spy 76/76, Harness/heartbeat 85/85, mocked Drive 8/8,
  local cleanup/disk tests 5/5. No production data or provider calls
  were used for this verification.
- Real concurrent PostgreSQL load testing remains an explicit outstanding
  gate; compilation and SQLite fixtures do not prove the deployment's
  parallel throughput or eliminate all possible race conditions.

This strengthens the **existing** Image Agent queue. It does not enable new
Agent Harness auto-dispatch, independent browser-idle attestation or learning
promotion. All those flags remain independent and fail-closed.

## Next safe increment

Implement the Phase 4C executor slot reservation and acknowledgment protocol
only after validating one legitimate Image Agent claim and signed heartbeat.
Preserve the existing queue and reject any unverified idle self-report as
dispatch authority. For an activation that changes production data, separately
obtain evidence of independent QA and rollback compatibility. Do not use
unverified feedback to automatically promote rules.
