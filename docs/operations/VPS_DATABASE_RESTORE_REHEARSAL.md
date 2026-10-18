# VPS PostgreSQL restore rehearsal — isolated, fail-closed

**State (18 October 2026):** The production backup timer is active and last returned success on 16 October. There is **no independently observed restore PASS for the production backup**. This document and the helper below do not change that fact.

This is an **operator-controlled** P0 readiness check, not a deployment, not a production restore and not evidence that the backup on Google Drive was actually downloaded or verified.

## Access and protected data

- Run from an **authorized trusted operator terminal**, not by reaching through the CodeLocal workspace sandbox into protected /etc, /opt, /var/lib paths. Do not copy production credentials, backups, environment files or Google OAuth tokens into Git, a chat, .ui-qa, or the web root.
- The selected backup must be independently identified as a managed database backup in the configured Google Drive folder, with matching cam_kind, remote file ID, created time, size, remote **content-derived MD5** and managed metadata SHA-256. An authorized `database_backup_cli list` now returns `content_md5`, `sha256_metadata` and `integrity_metadata` (`COMPLETE_NOT_DOWNLOADED` or `INCOMPLETE`) after checking the managed folder and file kind. **These fields are remote listing metadata only; neither downloaded content nor restore has been verified.** Independently verify the actual Drive object, checksum and source database identity before the restore. Service success alone does not prove this.
- Download into an operator-owned private directory, mode 0700; the .dump file must be mode 0600. Its SHA-256 and MD5 must match the independently recorded expected values.
- A **separate PostgreSQL instance/cluster**, not the live PostgreSQL service on port 5432, must listen exclusively at 127.0.0.1 on a dedicated port such as **55432**. Provision separate storage and adequate free space. A disposable local container is an option when operator-approved, but never mount production PostgreSQL data or Docker volumes into it.
- Create an existing **empty database** named cam_restore_rehearsal_<six_or_more_lowercase_chars_or_digits> and a dedicated **non-superuser** role with access only to that database. The helper never creates or drops a database.
- Supply the PostgreSQL rehearsal password with protected PGPASSFILE or PGPASSWORD in the operator environment. Never use or print the production DATABASE_URL and never supply secrets as CLI arguments.
- Record the independent cluster container/VM identity, process/service ownership and PostgreSQL major version privately. The helper checks loopback address, non-production port, distinctive database name, non-superuser role and database emptiness. Those guards **do not independently prove cluster isolation**. Verify it before invoking.

## Rehearsal command (operator only)

After the above checks, from the repository checkout, run the bundled utility using explicit, already-verified expected digests:

    python3 scripts/cam_db_restore_rehearsal.py \
      --dump /SECURE/PRIVATE/verified.dump \
      --sha256 EXPECTED_64_HEX_DIGEST \
      --md5 EXPECTED_32_HEX_DRIVE_CONTENT_MD5 \
      --database cam_restore_rehearsal_oct2026 \
      --db-user cam_rehearsal_user --db-port 55432 \
      --evidence /SECURE/PRIVATE/rehearsal-20261018.json \
      --ack-isolated-target

The example paths and digest placeholders must be replaced with operator-owned paths/values; this is **not** a ready-to-run production command.

The utility is deliberately fail-closed:

1. Rejects dump symlinks, non-regular/empty/world-readable files, nonexistent or overwritten evidence and incorrect digests **before connecting** to PostgreSQL.
2. Uses psql to assert a loopback connection on the specified dedicated port, exact database identity, zero existing user tables/objects and a non-superuser PostgreSQL role. Explicitly refuses port **5432**.
3. Validates pg_restore --list; restores with --single-transaction, --exit-on-error, --no-owner, --no-privileges, and restrictions on subscriptions/tablespaces/publications/security labels.
4. Confirms that user tables exist afterwards. It never issues DROP DATABASE or CREATE DATABASE, and does not remove the test database afterwards.
5. Writes **new**, mode-0600 evidence (metadata only, no rows or credentials) if requested. Does not overwrite an existing file.

**Important:** A helper RESTORE_REHEARSAL=PASS establishes only checksum consistency against operator-provided digests plus a successful isolated restore command and table-presence smoke. Before removing the production release gate, the operator must also verify the dump belongs to the intended production database, compare Alembic revision, representative table counts and application integrity against an independently captured source baseline, and record the actual remote backup file ID and checksums privately. A mismatch or any missing step remains **BLOCKED**.

## Required release evidence

| Gate | Required proof | State as of 18 October |
| --- | --- | --- |
| Scheduled backup | service result, timestamp, enabled timer | Observed PASS |
| Remote immutable artifact | Drive folder, managed kind, ID, size, remote MD5, metadata SHA-256 | Not checked |
| Isolated restore infrastructure | separate cluster/process/port, non-superuser, clean database | Not checked |
| Restore | command exit, checksum validation, table existence | Not run on production backup |
| Application semantics | Alembic revision + representative counts + private operator sign-off | Not checked |
| Release activation | current/previous symlink inventory, CI, rollback route | Not approved by this document |

Do not start backend migrations, bulk release cleanup or blind Docker pruning if P0 fails. Keep the most recent known-good backup and the live/previous production releases.

## Testing in the code workspace

    python3 -m unittest scripts/test_cam_db_restore_rehearsal.py -v

These tests mock PostgreSQL tools and exercise safety guards. They **are not** a real database restore and cannot be used as restore evidence.
