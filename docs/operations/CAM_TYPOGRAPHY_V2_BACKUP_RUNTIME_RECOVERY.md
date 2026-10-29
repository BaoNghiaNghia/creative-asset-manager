# CAM Typography V2 — Isolated PostgreSQL backup runtime recovery

Date: 2026-10-29. Source-only until authorized production activation.

## Purpose

Production API is healthy on backend d4573f7e with PostgreSQL schema 0187.
A newer already-built backend 61e7da54c understands the dedicated cam_backup
identity but cannot run as the live API until migration 0188/0189.
The scheduled backup service currently invokes the old API runtime and last
failed, even though its systemd unit loads the dedicated credential.

This narrowly scoped operator isolates ONLY the PostgreSQL backup service.
It reuses an immutable merged backend release with no new Python environment.
There is no change to API, workers, frontend, live/previous symlinks, database
schema or tenant assets.

## Operator procedure

Use a clean, latest origin/main checkout with an existing immutable release.
Run the dry-run first:

    sudo python3 scripts/cam_isolate_db_backup_runtime.py --commit 61e7da54cb280c0e3a1b5ee240b5f04425923cf7

To apply after inspecting the release and backup target:

    sudo python3 scripts/cam_isolate_db_backup_runtime.py --commit 61e7da54cb280c0e3a1b5ee240b5f04425923cf7 --apply

Apply performs a read-only verify-config under a transient systemd oneshot,
with secrets loaded via the protected systemd EnvironmentFile mechanism.
It fails without changing backup.service if configuration is invalid.
On success it pins only the backup service WorkingDirectory/ExecStart to the
existing release, reloads systemd and verifies the new command. It does NOT
run pg_dump, upload, restart the API/workers, alter PostgreSQL, or prune files.

Verify separately:

    systemctl show creative-asset-manager-db-backup.service -p ExecStart -p EnvironmentFiles -p User
    systemctl show creative-asset-manager-api.service -p ActiveState -p SubState
    systemctl show creative-asset-manager-db-backup.timer -p ActiveState -p NextElapseUSecRealtime

Run one real backup only with enough free disk, the correct managed Drive
destination and a scoped operator session. Record pg_dump success, local dump
inspection, full remote size/MD5/SHA validation and remote backup ID. Remove
local copies only after remote verification. Test an independent full-data
restore OFF the production PostgreSQL server before migrating 0188/0189.

Once the active backend has been safely migrated and itself supports cam_backup,
restore the original systemd backup unit:

    sudo python3 scripts/cam_isolate_db_backup_runtime.py --restore-current
    sudo python3 scripts/cam_isolate_db_backup_runtime.py --restore-current --apply

The restore operation verifies the active backend's backup configuration before
removing its own exact drop-in. It refuses unknown overrides. Standard backend
release cleanup and safe disk cleanup now protect the backup-pinned immutable
release, even after CURRENT/PREVIOUS pointers change. Do not delete that
runtime until the override has been removed.

## Gates still outstanding

This repair does not prove successful off-host backup or restore. Keep
Typography V2, ImageGen bulk generation, Scout and new migrations OFF until
full backup+restore, migration rehearsal, rollback and resource headroom pass.
Preserve Stage 1 V1 outputs, GatorHats assets and existing worker data.
