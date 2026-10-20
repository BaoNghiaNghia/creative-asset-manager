# Catinat Inventory Q47 — production backup restore gate (operator runbook)

**Production migration is BLOCKED until a verified isolated PostgreSQL restore, source identity and rollout checks pass.** As of 20 Oct 2026 the source database revision was independently confirmed by the operator as `0168_branding_dna_outline_versions`. Production release is not yet on Inventory `0172`.

## Verified backup

A managed PostgreSQL backup ran successfully on 20 Oct 2026 at 16:49 Asia/Ho_Chi_Minh, completed 16:50. The service logged `PG_DUMP=PASS`, `DUMP_VERIFY=PASS`, `DRIVE_UPLOAD=PASS`, `REMOTE_VERIFY=PASS`, `LOCAL_CLEANUP=PASS`; backup size was 287,692,571 bytes. This is **not** proof of an independent restore.

## Operator step 1 — download into a private directory

**Run in an authorized root SSH terminal**, not through the CodeLocal workspace sandbox. Never print the production environment, Google OAuth tokens, downloaded dump, or row-level data.

The code below is from the isolated review branch `release/inventory-catinat-q47-20261020`; review the diff before using it with production credentials.

```bash
umask 077
install -d -m 700 /root/catinat-db-rehearsal-20261020
cd /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020/apps/api
python3 /opt/creative-asset-manager/current/deploy/tools/production_env.py run-redacted \
  --env-file /etc/creative-asset-manager/production.env \
  --expected-owner-uid 0 -- \
  /root/creative-asset-manager/apps/api/.venv/bin/python \
  -m app.operations.backup_restore_download \
  --backup-id REPLACE_WITH_VERIFIED_MANAGED_BACKUP_ID \
  --private-directory /root/catinat-db-rehearsal-20261020
```

The downloader queries the managed Google Drive folder, permits only recognized `cam_kind=database_backup_v1` dumps, and requires complete Google MD5 plus stored SHA-256 metadata. It streams to a new mode-0600 file only, hashes the actual downloaded bytes, refuses overwrites, fails on mismatch and deletes partial files. No local or remote database operation is performed. The result prints a local file path and both verified digests.

## Operator step 2 — isolated PostgreSQL 16

Provision a **separate PostgreSQL 16 cluster** on loopback, dedicated port `55432`, with a separate data directory and a dedicated non-superuser `cam_rehearsal_user`. Do not share data directories or use production 5432, and do not attach production volumes to a test container. Ensure free disk and memory are sufficient for the *expanded* backup, not just its compressed size. Create the empty database `cam_restore_rehearsal_oct2026` owned by the rehearsal user. Use a password entered interactively (not placed in shell history). Record cluster identity and production-source snapshot/table counts privately.

Do not run a command against the test target until its listener and process ownership are independently verified.

## Operator step 3 — restore verified dump

The repository already contains a guarded restore verifier:

```bash
cd /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020
python3 scripts/cam_db_restore_rehearsal.py \
  --dump /root/catinat-db-rehearsal-20261020/REPLACE_WITH_DOWNLOADED_FILE.dump \
  --sha256 REPLACE_WITH_VERIFIED_DOWNLOADED_SHA256 \
  --md5 REPLACE_WITH_VERIFIED_DOWNLOADED_MD5 \
  --database cam_restore_rehearsal_oct2026 \
  --db-user cam_rehearsal_user --db-port 55432 \
  --evidence /root/catinat-db-rehearsal-20261020/restore-proof.json \
  --ack-isolated-target
```

Supply isolated role password through a protected `PGPASSFILE` with mode 0600, **never a command argument**. The existing restore verifier refuses nonempty databases, superusers, target port 5432, checksum mismatches, and overwrite of evidence.

**The helper's PASS is necessary but not sufficient**: independently compare production database identity, source Alembic revision `0168`, representative row counts, integrity and approved backup provenance. Record the remote file ID, actual checksum, restored table count and private operator sign-off.

## Operator step 4 — test the migration against the RESTORED test DB

With isolated database credentials (not production), point `DATABASE_URL` at `127.0.0.1:55432/cam_restore_rehearsal_oct2026` and the `release/inventory-catinat-q47-20261020` API source. Verify `alembic current` is `0168`, then `alembic upgrade head` and `alembic current` returns `0172_catinat_transfers`. Repeat relevant inventory integration tests. Preserve evidence; do not claim PASS based solely on the previously completed SQLite smoke.

## Release activation conditions

Only after steps 1–4 PASS: (1) verify release candidate matches reviewed commit and latest main, (2) execute the official production deploy with a short-lived lock, (3) apply migration together with matching API release, (4) verify `/live`, `/ready`, `alembic current`, UI and read-only Inventory endpoints, (5) keep current+previous release and a tested restore route. **No automatic Google Sheets write, inventory carry-forward, silent catalog merge or unconfirmed shift close is authorized.**
