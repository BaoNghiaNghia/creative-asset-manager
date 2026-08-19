# Database Backup to Google Drive

**Status:** DESIGN APPROVED / NOT YET IMPLEMENTED
**Target database size:** < 10 GB
**Scheduler:** systemd
**Remote storage:** Google Drive managed storage
**Production rollout:** NOT STARTED

This is the authoritative functional and operational specification for V1. See
[the implementation plan](../plans/DATABASE_BACKUP_IMPLEMENTATION.md) for the
future delivery phases.

## Scope and schedule

V1 performs a PostgreSQL logical backup without intentionally stopping API,
worker, or PostgreSQL. It is optimized for a database below 10 GB. Systemd owns
scheduling; an application polling loop must not schedule backups.

| Requirement | V1 value |
| --- | --- |
| Schedule | Tuesday 22:00 and Friday 22:00 |
| Time zone | `Asia/Ho_Chi_Minh` |
| Calendar | `Tue,Fri *-*-* 22:00:00 Asia/Ho_Chi_Minh` |
| Missed run | `Persistent=true` |

The future timer is `creative-asset-manager-db-backup.timer`; its paired
oneshot service is `creative-asset-manager-db-backup.service`. It must use
`/etc/creative-asset-manager/production.env`, the `creative-assets`
identity, and `/opt/creative-asset-manager/current/apps/api` as working
directory. The timer uses `Persistent=true` and `AccuracySec=1min`; the
service is `Type=oneshot`. Do not modify existing API/worker services merely
to schedule backup work.

No WAL/PITR or object-storage architecture is required for V1. Reassess backup
duration, staging disk, restore time, WAL/PITR, and object storage if growth
materially exceeds the target range.

## Dump and staging contract

Use PostgreSQL custom logical dumps:

```sh
pg_dump --format=custom --no-owner --no-privileges
# equivalent: pg_dump -Fc
```

Use `--no-owner` and `--no-privileges` where compatible with restore
requirements. Name files `cam-db-YYYYMMDD-HHMMSS+0700.dump`. Never put
passwords in filenames or command logs.

The persistent staging directory is:

```text
/var/lib/creative-asset-manager/database-backup
```

Do not use `/tmp`. Use directory mode 0750, file mode 0600, and existing
`creative-assets` deployment user/group where permissions allow. Expected
lifecycle: zero completed local files before work, one active staging file
during work, and zero completed local files after a verified upload.

Before starting, require at least **15 GiB** (`16106127360` bytes) free in
staging. A later implementation may compare database size too, but this hard
floor remains. If it fails, `BACKUP_STARTED=NO`; do not risk filling the VPS.

## Workflow, verification, and concurrency

The future `backup` command owns this exact order:

```text
preflight -> lock -> pg_dump -> local verification -> checksum
-> Drive resumable upload -> remote verification -> retention -> local cleanup
```

Before upload require all of:

- successful `pg_dump` exit status;
- dump exists and size is greater than zero;
- `pg_restore --list <backup-file>` succeeds; and
- SHA-256 is calculated for safe operational/audit logging.

Use an OS-level single-host lock such as `flock`. If another backup is active,
report `BACKUP_ALREADY_RUNNING=YES` and `NEW_BACKUP_STARTED=NO`; never run
concurrent `pg_dump` jobs.

Never log `DATABASE_URL`, database passwords, Google access/refresh tokens,
or `GOOGLE_CLIENT_SECRET`.

## Google Drive managed storage

Backups must use the application's existing managed Google Drive
credential/storage infrastructure, not tenant/user Source Drive OAuth. Current
relevant settings are:

- `GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN`
- `GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN`
- `GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

Future code should reuse/refactor existing managed-storage credential refresh
behavior. A logged-in user's Drive connection is never a production backup
identity.

Add this future non-secret production configuration:

```dotenv
DATABASE_BACKUP_DRIVE_FOLDER_ID=
```

It is the authoritative dedicated Google Drive folder ID. Do not look up a
folder by name each run. A logical layout can be `Creative Asset
Manager/Database Backups`, but the human-readable name is not required.

The dump must not be loaded fully into RAM. Forbidden V1 behavior includes
`read_bytes()`, whole-file `read()`, or passing a multi-GB dump as one Python
bytes object. Required flow is:

```text
local file -> bounded chunk reads -> Google Drive resumable upload
```

Start with 16 MiB chunks; 32 MiB is acceptable only if code and tests justify
it. After upload HTTP success, verify remote file ID exists and remote size
matches local size. Record only safe metadata (created time, size, SHA-256,
application/build commit if available, safe Alembic revision). When Drive
appProperties are used, mark managed files with:

```text
cam_kind=database_backup_v1
```

Do not use a JSON sidecar: the folder is limited to six managed backup files.

## Retention and safe deletion

Retention runs **only after** a new dump is created, locally verified,
uploaded, and remotely verified.

1. Delete managed backups older than 21 days.
2. Sort remaining managed backups newest to oldest; keep the newest six and
   delete position seven and older until `backup_count <= 6`.

The rules are independent. Four backups with one 25 days old loses that expired
file. Seven recent backups loses the oldest one.

Deletion must satisfy both guards:

```text
parent folder == DATABASE_BACKUP_DRIVE_FOLDER_ID
AND cam_kind == database_backup_v1
```

Ignore all other folder files. No wildcard or arbitrary-file cleanup is
permitted.

If `pg_dump`, local verification, credentials, Drive upload, or remote
verification fails, do not prune prior backups. If upload succeeds but pruning
fails, keep the valid new backup, report failure, and never delete the new
backup to compensate.

After dump, local verification, upload, and remote verification pass, remove
the local staging backup. On failure, clean partial local files when safe. The
Drive copy is the retained copy.

## CLI and configuration contract

Intended future CLI:

```sh
python -m app.operations.database_backup_cli verify-config
python -m app.operations.database_backup_cli backup
python -m app.operations.database_backup_cli list
python -m app.operations.database_backup_cli prune
```

`backup` performs the complete workflow. `prune` is explicit maintenance,
but a normal scheduled backup must not require a separate operator call.

Future settings:

```dotenv
DATABASE_BACKUP_ENABLED=false
DATABASE_BACKUP_DRIVE_FOLDER_ID=
DATABASE_BACKUP_RETENTION_DAYS=21
DATABASE_BACKUP_MAX_FILES=6
DATABASE_BACKUP_MIN_FREE_BYTES=16106127360
DATABASE_BACKUP_STAGING_DIRECTORY=/var/lib/creative-asset-manager/database-backup
```

Do not duplicate Google credentials into `DATABASE_BACKUP_*` settings.
Client-side at-rest encryption is recommended future hardening but is not
required for V1 without a later explicit instruction.

## Failure behavior

| Failure | Required result |
| --- | --- |
| Staging preflight fails | `BACKUP_STARTED=NO` |
| `pg_dump` or local verification fails | `UPLOAD=NO`; `RETENTION=NO` |
| Drive upload or remote verification fails | `RETENTION=NO` |
| Pruning fails after a verified upload | Preserve new backup; report failure |

Configuration missing, insufficient space, missing `pg_dump`/`pg_restore`,
dump failure, unavailable Drive credentials, upload failure, and remote
verification failure all fail closed. No failure path may delete an existing
managed backup before a new remote copy has been verified.

## Restore and observability

Every dump must support `pg_restore --list`. Never improvise a restore into
live production. Preferred recovery is:

```text
download -> verify expected file/checksum -> pg_restore --list
-> restore into temporary/test PostgreSQL -> validate schema/data
-> separately reviewed production recovery procedure
```

A backup strategy is incomplete until restore has been tested; no restore is
performed by this design task.

Safe result/log fields are:

```text
DATABASE_SIZE_BYTES=
STAGING_FREE_BYTES=
STORAGE_PREFLIGHT=
PG_DUMP=
BACKUP_FILE_SIZE_BYTES=
BACKUP_SHA256=
DUMP_VERIFY=
DRIVE_UPLOAD=
REMOTE_VERIFY=
REMOTE_FILE_ID=
REMOTE_BACKUP_COUNT_BEFORE_PRUNE=
AGE_PRUNED=
COUNT_PRUNED=
REMOTE_BACKUP_COUNT=
LOCAL_CLEANUP=
DATABASE_BACKUP=
```

## Non-negotiable invariants

```text
DATABASE_TARGET_SIZE_LT_10GB=YES
SCHEDULE_TUESDAY_22=YES
SCHEDULE_FRIDAY_22=YES
TIMEZONE_ASIA_HO_CHI_MINH=YES
PG_DUMP_CUSTOM_FORMAT=YES
LOCAL_STAGING=YES
MIN_FREE_STAGING_GIB=15
FULL_BACKUP_IN_MEMORY=NO
GOOGLE_DRIVE_RESUMABLE_UPLOAD=YES
TENANT_USER_DRIVE_OAUTH_FOR_BACKUP=NO
DEDICATED_BACKUP_FOLDER_ID=YES
MAX_MANAGED_REMOTE_FILES=6
MAX_REMOTE_AGE_DAYS=21
PRUNE_ONLY_AFTER_NEW_BACKUP_VERIFIED=YES
DELETE_NON_MANAGED_FILES=NO
DELETE_LOCAL_AFTER_SUCCESS=YES
CONCURRENT_BACKUPS=NO
NORMAL_PRODUCTION_DOWNTIME=NO
DATABASE_MIGRATION_REQUIRED=NO
FRONTEND_REQUIRED=NO
AUTO_PRODUCTION_DEPLOY=NO
```
