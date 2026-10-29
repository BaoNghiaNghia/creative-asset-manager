# Database Backup to Google Drive

**Status:** IMPLEMENTED / PRODUCTION TIMER ACTIVE, LATEST OBSERVED BACKUP FAILED
**Target database size:** < 10 GB
**Scheduler:** systemd
**Remote storage:** Google Drive managed storage
**Production rollout:** BLOCKED until dedicated identity, verified full backup and off-host restore

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

The deployed timer is `creative-asset-manager-db-backup.timer`; its paired
oneshot service is `creative-asset-manager-db-backup.service`. It must use
`/etc/creative-asset-manager/production.env`, the `creative-assets`
identity, and `/opt/creative-asset-manager/current/apps/api` as working
directory. The timer uses `Persistent=true` and `AccuracySec=1min`; the
service is `Type=oneshot`. Do not modify existing API/worker services merely
to schedule backup work.

No WAL/PITR or object-storage architecture is required for V1. Reassess backup
duration, staging disk, restore time, WAL/PITR, and object storage if growth
materially exceeds the target range.

## Dedicated PostgreSQL backup identity (October 29, 2026 remediation)

The production backup timer previously ran `pg_dump` as the app role from
`DATABASE_URL`. On 27 October its dump failed, and read-only PostgreSQL catalog
inspection found the app role could not read two protected
`cam_incident_20261024_*` recovery tables. Those tables must **not** be excluded
from backup, deleted, or made readable by the general app role just to clear
the error. The missing privileges are a plausible cause; the old error
message did not preserve sufficient sanitized stderr to prove causality.

Backup V1 now accepts optional `DATABASE_BACKUP_DATABASE_URL` for a **separate
PostgreSQL login**. Deliver it only in
`/etc/creative-asset-manager/database-backup.env` (root:root, mode 0600),
loaded by `creative-asset-manager-db-backup.service` after its standard
production environment. This file must not be loaded by the API or other
workers. The application continues using `DATABASE_URL` without changes.
The backup URL is rejected *before any dump* unless it uses the same
PostgreSQL database, host and port but a different username; URL query
overrides are forbidden. No password enters process arguments or normal
diagnostic output. An absent backup URL retains legacy behavior for existing
installations, but **must not be used as production remediation while these
incident tables remain inaccessible to the app role**. `verify-config`
reports only `POSTGRESQL_BACKUP_IDENTITY=DEDICATED` or
`APPLICATION_LEGACY`, and does not prove table read coverage.

Operator-controlled provisioning (never from a routine agent migration):

The idempotence guard in `scripts/cam_pg_backup_identity_operator.py`
requires a confirmed **local root** session and performs no changes by default.
On the VPS, from a clean, reviewed `main` checkout with the project's
Python virtualenv, first run:

```sh
apps/api/.venv/bin/python scripts/cam_pg_backup_identity_operator.py --status
```

Only after verifying the active production database, target hostname,
existing backup role and protected file ownership, use the deliberately
non-default `--apply` action:

```sh
apps/api/.venv/bin/python scripts/cam_pg_backup_identity_operator.py --apply
apps/api/.venv/bin/python scripts/cam_pg_backup_identity_operator.py --status
```

`--apply` generates a fresh random secret in process memory, creates the
`cam_backup` role inside one PostgreSQL transaction, grants only
database CONNECT, schema USAGE, table SELECT and **sequence SELECT**
(neither sequence USAGE nor UPDATE, which could advance sequences),
and records the backup-only URL in an exclusive root:root 0600 file.
It configures scoped default SELECT privileges for future tables/sequences
owned by `cam_app` or `postgres`. It refuses existing roles and files,
unexpected endpoints or unresolved DNS and never logs credentials.
Successful output must show **0 tables/sequences lacking SELECT and
0 tables/sequences with write-capable privileges**. A failed connection
check after role creation leaves the protected secret file in place for
controlled repair; do not delete the last usable credential.

This action does **not** deploy backend code, install/activate the new
systemd unit, restart services, change `cam_app`, execute a full dump,
upload anything to Drive, run migrations or prove recovery. The existing
timer still uses the old backend until a separately gated release.
If any gate fails, stop without invoking the timer; preserve evidence and
the rollback release.

1. Have an authorized PostgreSQL administrator provision a new
   **LOGIN, NOSUPERUSER, NOCREATEDB, NOCREATEROLE, NOREPLICATION** role
   solely for a backup of `creative_asset_manager`. Generate a fresh
   high-entropy secret using an approved secret delivery workflow, not a
   command line, Git file, terminal transcript or agent chat.
2. Restrict `CONNECT` to the target database. Grant only `USAGE` on
   required schemas and `SELECT` on **every** table, including the two
   incident tables, plus necessary sequence read privileges. Do not grant
   `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `CREATE`, `pg_write_all_data`
   or broad administrative memberships. Configure narrowly scoped
   default privileges per table owner for future migrations.
3. Install a **separate** root:root 0600 backup-only environment file from
   `deploy/database-backup.env.example`, with the **same endpoint/database**
   as `DATABASE_URL` but a different username. Do not put the backup secret
   into shared `production.env`, a shell command or any committed file. Never
   paste its populated value into documentation. Do not change the app's role
   or current backend configuration until release and backup repair pass the
   deployment gates.
4. With a dedicated connection, perform a read-only catalog privilege audit
   of all application tables and sequences (zero missing read grants),
   then `verify-config`. Under the dedicated backup identity, a safe
   table-coverage query (do **not** run as superuser for this check) is:

   ```sql
   SELECT COUNT(*) AS tables_without_select
   FROM pg_class c
   JOIN pg_namespace n ON n.oid = c.relnamespace
   WHERE n.nspname NOT LIKE 'pg_%'
     AND n.nspname <> 'information_schema'
     AND c.relkind IN ('r', 'p', 'm')
     AND NOT has_table_privilege(current_user, c.oid, 'SELECT');
   ```

   Run an equivalent `has_sequence_privilege(..., 'SELECT')` audit for
   `relkind='S'`. Both counts must be zero. This is only an access check;
   a **real** `pg_dump -Fc` and `pg_restore --list` must still pass.
5. Confirm independent Google Drive checksum/size verification and download,
   then restore the *complete data* into an isolated off-host PostgreSQL test
   environment, validate table/row counts including both incident tables,
   and rehearse additive migrations `0188` / `0189` with old-release
   compatibility. Only then clear the G0.1 migration/rollback block.

This is an opt-in backend code path, **not** authorization to create a role,
modify production secrets, start a new backup job, grant SQL privileges, or
activate Typography Engine V2. All such actions require a separate guarded
operator workflow; never retry a failed full dump under an unverified identity
while the production disk and swap are under pressure.

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
normal lifecycle: zero completed local files before work, one active staging
file during work, and zero completed local files after a verified upload. If
Drive upload or remote verification fails, keep the locally verified dump in
staging for an operator-controlled retry or reconciliation. Never silently
delete that only known-good copy.

Before starting, require at least **1.5 GiB** (`1610612736` bytes) free in
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
- SHA-256 and MD5 are calculated in one bounded streaming pass. SHA-256 is
  retained for safe operational/audit logging; MD5 is compared with the
  checksum independently calculated by Google Drive.

Use an OS-level single-host lock such as `flock`. If another backup is active,
report `BACKUP_ALREADY_RUNNING=YES` and `NEW_BACKUP_STARTED=NO`; never run
concurrent `pg_dump` jobs.

Never log `DATABASE_URL`, database passwords, Google access/refresh tokens,
or `GOOGLE_CLIENT_SECRET`.

## Google Drive managed storage

Backups must use the application's existing managed Google Drive
credential/storage infrastructure, not tenant/user Source Drive OAuth. The
Managed Storage refresh token is validated against the configured active folder,
then encrypted and persisted through the Platform Admin Providers UI. Runtime
and backup code resolve that persisted credential and do not fall back to
access or refresh tokens from the environment.

The non-secret folder selection and OAuth client configuration remain:

- `GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID`
- `GOOGLE_CLIENT_ID`
- `GOOGLE_CLIENT_SECRET`

A logged-in user's Drive connection is never a production backup identity.
Never copy a Source Drive token into the Managed Storage credential form.

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
it. After upload HTTP success, verify the remote file ID exists, remote size
matches local size, and Drive's content-derived `md5Checksum` exactly matches
the locally streamed MD5. A missing Drive checksum fails closed. Also require
the remote `cam_sha256` appProperty to match local SHA-256; this binds metadata
but is not a substitute for Drive's content-derived checksum. Record only safe
metadata (created time, size, checksums, application/build commit if available,
safe Alembic revision). When Drive appProperties are used, mark managed files
with:

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
the local staging backup after retention completes or fails. Before remote
verification succeeds, only incomplete/unverified partial dumps may be
cleaned. A locally verified dump must survive Drive upload, network, quota, and
remote-verification failures.

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
DATABASE_BACKUP_MIN_FREE_BYTES=1610612736
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
| Drive upload or remote verification fails | `RETENTION=NO`; retain locally verified dump |
| Pruning fails after a verified upload | Preserve new backup; report failure |

Configuration missing, insufficient space, missing `pg_dump`/`pg_restore`,
dump failure, unavailable Drive credentials, upload failure, and remote
verification failure all fail closed. No failure path may delete an existing
managed backup before a new remote copy has been verified.

## Verified remote-copy receipt (2026-10-23 implementation)

After the complete backup workflow succeeds (PostgreSQL dump and local
verification, Drive upload, remote independent MD5/size metadata verification,
retention and local cleanup), the CLI atomically writes a private 0600 receipt
in the existing backup staging directory. The receipt is lightweight metadata
for the separately gated production retention worker, not the backup itself.
It includes no access tokens, connection URLs, personal data or Drive file IDs.
The receipt does **not** replace an isolated PostgreSQL restore test, and an
older backup log is not silently trusted as a new receipt. Invalid/missing
receipts must prevent production retention from scheduling or executing.
The backup service is authorized to write only under its pre-existing staging
path; no additional permissions or secret storage are required.

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
BACKUP_MD5=
DUMP_VERIFY=
DRIVE_UPLOAD=
REMOTE_VERIFY=
REMOTE_FILE_ID=
REMOTE_MD5_CHECKSUM=
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
MIN_FREE_STAGING_GIB=1.5
FULL_BACKUP_IN_MEMORY=NO
GOOGLE_DRIVE_RESUMABLE_UPLOAD=YES
TENANT_USER_DRIVE_OAUTH_FOR_BACKUP=NO
DEDICATED_BACKUP_FOLDER_ID=YES
MAX_MANAGED_REMOTE_FILES=6
MAX_REMOTE_AGE_DAYS=21
PRUNE_ONLY_AFTER_NEW_BACKUP_VERIFIED=YES
DELETE_NON_MANAGED_FILES=NO
DELETE_LOCAL_AFTER_SUCCESS=YES
LOCAL_VERIFIED_RETAINED_ON_REMOTE_FAILURE=YES
REMOTE_CONTENT_CHECKSUM_VERIFIED=YES
CONCURRENT_BACKUPS=NO
NORMAL_PRODUCTION_DOWNTIME=NO
DATABASE_MIGRATION_REQUIRED=NO
FRONTEND_REQUIRED=NO
AUTO_PRODUCTION_DEPLOY=NO
```
