# P2D restored-clone migration — operator handoff

Production release 8a7b9c0f7 is discovery-only; full P2D candidate is based on
933918ca6. Keep PREPARED_DISABLED until G1, central schema, legacy drain, trusted
QA/storage, resource arbitration, and canary gates pass.

## Current preflight

- Managed backup receipt PASS. Independent remote re-download and restore NOT_VERIFIED.
- Production disk approximately 3.9 GiB free; same-volume restore BLOCKED.
- Windows CAM worker D: reports approximately 1.7 TB free, but no working Docker,
  PostgreSQL restore tools or configured WSL distribution were verified. It is not
  yet an approved isolated restore host. Do not install or transfer production
  data to that worker as an automatic fallback.
- Use an approved independent Linux PostgreSQL 16 environment with at least
  20 GiB free; private encrypted storage and restricted local access. Do not
  use the live VPS data volume or production credentials on a worker.
- Production Discovery smoke: three authenticated GET endpoints return 200,
  19-file Skill manifest matches, central schema/claim/renderer false.

## Operator procedure (trusted console only)

Follow DATABASE_BACKUP.md and the guarded managed-backup download workflow.
Independently verify the latest managed remote dump identity, MD5 and SHA256.
Collect independent source-revision and protected-table counts from the same
backup snapshot, not from the current changing production database. If the
snapshot counts are not available, stop: they must not be manufactured from the
restored rows or copied from a later live snapshot.

Provision a dedicated empty PostgreSQL 16 database
`cam_restore_rehearsal_p2d_20261126` on loopback port 55432, with a dedicated
non-superuser owner `cam_rehearsal_user`. Use a separate data directory and
listener. Verify process ownership and instance identity; never attach the
production PostgreSQL data directory. Use an operator-owned mode 0700 private
directory, mode 0600 dump and one-entry rehearsal.pgpass (exact
127.0.0.1:55432:cam_restore_rehearsal_p2d_20261126:cam_rehearsal_user target).
No password or dump contents in terminal output or chat.

Use existing scripts/cam_db_restore_rehearsal.py from the reviewed checkout with
--ack-isolated-target, actual verified --dump, --sha256, --md5, the fixed
--database, --db-user, --db-port 55432 and --evidence <private-dir>/restore-proof.json.
For each of the ten tables below provide --require-table TABLE and
--expect-table-count TABLE=COUNT, where COUNT is independently recorded for
that same backup snapshot. Supply PGPASSFILE from the private directory.

Protected tables: tenants, processing_jobs, etsy_spy_picked,
etsy_spy_listing_images, etsy_spy_listing_image_deletions, cam_muse_jobs,
cam_muse_commands, inventory_items, inventory_locations, inventory_transactions.

Create operator-owned source-snapshot.json mode 0600 with actual
backup_sha256, backup_md5, source_revision (0216_etsy_listing_idea_draft),
and row_counts mapping exactly those ten names to actual independent counts.
The source snapshot is an operator evidence input, not a generated fixture.
If the restore output is not PASS, or source revision differs, do not proceed.

From the reviewed P2D checkout, using the matching isolated Python environment:

    python scripts/image_orchestrator/p2d_migrate_restored_clone.py \
      --private-directory /absolute/private/operator/directory \
      --ack-isolated-target --ack-independent-source-snapshot

This checks the restore receipt, source snapshot, exact local target and PG16
identity, source revision and protected counts; runs 0216 -> 0219 on the same
connection inside a transaction; checks exactly ten new empty tables, prior
column/PK signatures and protected count parity. It writes a new private
p2d-migration-proof.json, refuses overwrite, and never declares independent
backup provenance or G1 verified. Do not run again on a partially migrated clone.
Operator must verify the actual backup provenance and complete release gates.

## Next CAM steps after actual G1 clearance

1. Review candidate commit against production Discovery (8a7b9c0f7), run official
   release preflight and retain rollback. Deploy the complete disabled P2D
   candidate with additive migrations 0217-0219 only after verified restore.
2. Verify API readiness, Alembic head, legacy coverage and existing video health.
   Still no claim or renderer. Source adapters are deliberately unbound.
3. Establish real legacy Skill admission fence and drain; reconcile exact source
   SHA, role identity, saved/deleted receipt and unknown attempts. Do not treat
   a read-only daily audit as drain evidence or a source approval.
4. Bind actual trusted QA, original-byte, storage and scheduler/resource
   adapters. Verify live Muse identity and resource participation. Keep limits,
   cooldowns, STOP, lease and idempotency controls intact.
5. Canary one eligible C1717 role only; verify native PNG, source/placement,
   QA receipt, Drive and CAM receipt before any further grant. No upscaling
   to bypass the native publication gate. Do not alter video tasks.
6. Enable image claim only after the full canary and independent gates pass.
   Until then status remains PREPARED_DISABLED.

This handoff creates no production mutation, task, renderer call, backup transfer,
restore proof, or G1 approval. Unit input validation tests are not restore evidence.
