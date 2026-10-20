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

## Operator shortcut — guarded independent restore (recommended)

For the verified 20 Oct 2026 backup, the checked-in one-shot **SSH operator** script handles the isolated PG16 cluster setup, separate role/password, restore, and *source* Alembic revision check. It never connects to PostgreSQL production (5432), never loads production credentials or production.env, requires an empty named cluster and unused isolated port, and fails before DB modifications if backup SHA-256 or MD5 disagree.

Review `scripts/cam_catinat_restore_isolated.sh` first, then execute this on the VPS by the authorized root operator:

```bash
cd /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020
bash scripts/cam_catinat_restore_isolated.sh \
  --dump /root/catinat-db-rehearsal-20261020/cam-db-20261020-164908+0700.dump \
  --sha256 dfdd83eb724b4d4f0e9527aa4297201f49120328f07f4be5e3b22df32a364ae2 \
  --md5 22aa75ff85f073bcf135dc57fa4dbac5
```

Expected output: `RESTORE_PREFLIGHT=PASS`, a JSON `RESTORE_REHEARSAL=PASS` from `cam_db_restore_rehearsal.py`, and `RESTORE_SOURCE_REVISION=PASS` with restored Alembic 0168. Output `FAIL` or `BLOCKED` must **not** be reinterpreted as success. Do not retry after partial cluster creation without reviewing isolation and state. This script does not upgrade a database; it only restores the backup to a new isolated cluster.

### Recovery for 20 October cluster startup failure (PostgreSQL `listen_addresses` syntax)

The first script version set `listen_addresses = 127.0.0.1` without PostgreSQL string quotes, causing a syntax error at line 60, so `initdb` completed but `pg_ctlcluster` failed **before role creation, database creation or restore**. Trying to pass pre-quoted input to `pg_conftool` produced triple quotes as well. The updated script uses a dedicated, strictly scoped configuration repair helper to write exactly `listen_addresses = '127.0.0.1'`, also hardens local socket authentication from `trust` to `peer`, and accepts a tightly checked one-time resume flag. **Only use this mode after confirming `pg_lsclusters` reports exactly `16 catinat_rehearsal 55432 down postgres` and `16 main 5432 online postgres`.** The script will reject an existing initialized role/database, a running cluster or production-port conflict.

```bash
cd /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020
bash scripts/cam_catinat_restore_isolated.sh \
  --dump /root/catinat-db-rehearsal-20261020/cam-db-20261020-164908+0700.dump \
  --sha256 dfdd83eb724b4d4f0e9527aa4297201f49120328f07f4be5e3b22df32a364ae2 \
  --md5 22aa75ff85f073bcf135dc57fa4dbac5 \
  --resume-existing-cluster
```

Do **not** delete either PostgreSQL cluster or repeat the initial `pg_createcluster` command. No production upgrade is included in this recovery.


If a manual configuration is preferred, follow the original steps 2 and 3 below instead, **not in addition** to the shortcut.

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

### Safe PostgreSQL clone migration command (only AFTER restore PASS)

The guarded script `scripts/cam_catinat_migrate_restored_clone.py` reads the private restore evidence and `rehearsal.pgpass`, verifies the exact database identity + non-superuser account + Alembic source `0168` + source table counts, applies migration **only** to fixed local port 55432, and requires revision `0172`, six new Catinat tables and unchanged row counts afterwards.

Do not run this if `RESTORE_REHEARSAL=PASS` and `RESTORE_SOURCE_REVISION=PASS` have not both been confirmed, or if the new cluster is missing. The script does not have any option to change host, port, database or username.

```bash
cd /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020
python3 scripts/cam_catinat_migrate_restored_clone.py \
  --ack-isolated-target \
  --source-evidence /root/catinat-db-rehearsal-20261020/restore-proof.json \
  --pgpassfile /root/catinat-db-rehearsal-20261020/rehearsal.pgpass \
  --result /root/catinat-db-rehearsal-20261020/migration-proof.json \
  --checkout /root/creative-asset-manager/.worktrees/inventory-catinat-prod-candidate-20261020 \
  --python /root/creative-asset-manager/apps/api/.venv/bin/python
```

Expect `ISOLATED_MIGRATION=PASS`, `SOURCE_REVISION=0168_branding_dna_outline_versions`, `TARGET_REVISION=0172_catinat_transfers`, `CREATED_CATINAT_TABLES=6`, `PRODUCTION_MIGRATION=NOT_RUN`. Stop on `BLOCKED` rather than rerunning against partially migrated clone.

## Release activation conditions

Only after steps 1–4 PASS: (1) verify release candidate matches reviewed commit and latest main, (2) execute the official production deploy with a short-lived lock, (3) apply migration together with matching API release, (4) verify `/live`, `/ready`, `alembic current`, UI and read-only Inventory endpoints, (5) keep current+previous release and a tested restore route. **No automatic Google Sheets write, inventory carry-forward, silent catalog merge or unconfirmed shift close is authorized.**
