# Database Backup Implementation Plan

**Status:** DB-BACKUP-1 AND DB-BACKUP-2 IMPLEMENTED / DB-BACKUP-3 AND DB-BACKUP-4 PLANNED

> Future Codex sessions implementing database backup **MUST** first read
> `AGENTS.md`, [the operations specification](../operations/DATABASE_BACKUP.md),
> and this plan. Do not rely on chat memory as the source of truth. Inspect
> current `main` before implementation because the repository may have evolved.

If implementation and these documents conflict, stop and reconcile the
difference explicitly instead of silently changing the documented requirements.

## Source-of-truth hierarchy

1. Current explicit user instruction
2. `AGENTS.md` repository instructions
3. [Database Backup to Google Drive](../operations/DATABASE_BACKUP.md)
4. This implementation plan
5. Current source and implementation details

The operations specification defines **what** must be achieved. This plan
describes **how** V1 is expected to be phased. Determine code signatures and
exact file locations from current source at implementation time.

## DB-BACKUP-1 -- Core backup service

**Status:** IMPLEMENTED. The local service and focused tests now cover disabled/default configuration, 15 GiB preflight, flock exclusion, custom-format dump, pg_restore verification, streamed SHA-256, and safe cleanup. DB-BACKUP-2 must retain the verified staging file until remote verification succeeds.

Implement configuration, 15 GiB staging preflight, a single-run OS lock,
custom-format `pg_dump`, `pg_restore --list` verification, SHA-256, and safe
local cleanup. Do not make Google Drive mutations in real-service tests in this
phase. Failures must not create a successful-looking backup or begin retention.

## DB-BACKUP-2 -- Google Drive upload and retention

**Status:** IMPLEMENTED. DB-BACKUP-2 reuses the managed Google credential refresh through a dedicated backup-folder adapter, streams resumable 16 MiB chunks, verifies remote metadata and size, and prunes only managed files after remote verification. It retains 21 days and six files with guarded deletion.

Never use tenant/user Source Drive OAuth. Retention starts only after the new backup has passed remote verification.

## DB-BACKUP-3 -- CLI and systemd

Implement `database_backup_cli` with `verify-config`, `backup`, `list`,
and `prune`. Add the oneshot service and persistent
`Tue,Fri *-*-* 22:00:00 Asia/Ho_Chi_Minh` timer, plus production environment
example entries. Use existing release/environment conventions. Do not deploy
automatically.

## DB-BACKUP-4 -- Regression and operations readiness

Run unit, mocked-Google, CLI, retention, systemd syntax/calendar, production
configuration, and restore-runbook validations. Verify existing managed Google
Drive asset behavior, Source Drive OAuth, and API/worker deployment services
remain unchanged. Do not perform a production deployment without a new explicit
user instruction.

## Expected future files

These are likely locations only. Inspect current source before deciding:

```text
apps/api/app/modules/database_backup/__init__.py
apps/api/app/modules/database_backup/service.py
apps/api/app/modules/database_backup/google_drive.py
apps/api/app/modules/database_backup/retention.py
apps/api/app/operations/database_backup_cli.py
deploy/systemd/creative-asset-manager-db-backup.service
deploy/systemd/creative-asset-manager-db-backup.timer
deploy/production.env.example
```

Use existing test conventions. V1 expects no database migration and no frontend
work.

## Mandatory future test plan

### Core

- database smaller than 10 GB is accepted;
- insufficient staging space rejects before `pg_dump`;
- failed `pg_dump`, empty dump, or failed `pg_restore --list` produces no
  upload;
- SHA-256 is generated;
- local cleanup follows successful remote verification; and
- the lock prevents a concurrent second backup.

### Google Drive

- resumable upload uses bounded chunks and never whole-file reads;
- managed credential refresh is mocked;
- upload failure, remote-verification failure, and size mismatch do not prune;
- every managed backup has the managed marker.

### Retention

- exactly six recent files delete none; seven recent files delete oldest;
- expired files are deleted, with age prune before count prune;
- unrelated files, wrong/missing `cam_kind`, and wrong-parent files are never
  deleted; and
- a prune failure never deletes the newly verified backup.

### CLI, systemd, and regression

- missing configuration fails closed; `verify-config` and `list` are
  read-only;
- `backup` follows documented workflow order;
- timer has Tuesday/Friday 22:00 Asia/Ho_Chi_Minh and `Persistent=true`; and
- managed-storage assets, Source Drive OAuth, and API/worker services remain
  unchanged.

## Implementation guardrails

- Begin from latest `main` on a feature branch and inspect managed-storage
  code before editing.
- Stream backup content through bounded chunks; never create a multi-GB RAM
  buffer.
- Do not add migration, frontend work, tenant credentials, or deployment unless
  a later explicit requirement authorizes it.
- Update this plan and the operations document when an intentional architecture
  decision changes.
