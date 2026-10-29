#!/usr/bin/env python3
"""Temporarily pin only the DB backup service to an existing merged backend release.

Use when the production API remains at Alembic 0187 while the isolated backup
identity is only understood by a newer (already built) backend release. No
database migration, API restart, worker restart, pg_dump or Google Drive write
is performed by this operator.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile

APP_ROOT = Path("/opt/creative-asset-manager")
PRODUCTION_ENV = Path("/etc/creative-asset-manager/production.env")
BACKUP_ENV = Path("/etc/creative-asset-manager/database-backup.env")
SERVICE = "creative-asset-manager-db-backup.service"
DROPIN_ROOT = Path("/etc/systemd/system") / (SERVICE + ".d")
DROPIN = DROPIN_ROOT / "90-cam-isolated-backup-runtime.conf"
OWNER_MARKER = "# CAM isolated backup runtime v1"
SHA = re.compile(r"[0-9a-f]{40}\Z")
SOURCE = Path(__file__).resolve().parents[1]


class BackupRuntimeGateError(RuntimeError):
    """Never include environment values, passwords or provider credentials."""


def command(*args: str, timeout: int = 35, capture: bool = True) -> str:
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, timeout=timeout, text=True, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise BackupRuntimeGateError("Backup runtime system operation unavailable.") from exc
    if result.returncode:
        raise BackupRuntimeGateError("Backup runtime system operation failed.")
    return result.stdout.strip() if capture else ""


def git_authority() -> str:
    if not (SOURCE / ".git").exists():
        raise BackupRuntimeGateError("A complete Git checkout is required.")
    if command("git", "-C", str(SOURCE), "rev-parse", "--show-toplevel") != str(SOURCE):
        raise BackupRuntimeGateError("Git checkout root mismatch.")
    if command("git", "-C", str(SOURCE), "status", "--porcelain", "--untracked-files=normal"):
        raise BackupRuntimeGateError("Deployment checkout must be clean.")
    head = command("git", "-C", str(SOURCE), "rev-parse", "HEAD")
    remote = command("git", "-C", str(SOURCE), "rev-parse", "origin/main")
    if head != remote:
        raise BackupRuntimeGateError("Deployment checkout must match origin/main.")
    return head


def release_path(commit: str, head: str) -> Path:
    if not SHA.fullmatch(commit):
        raise BackupRuntimeGateError("Backup release requires an exact SHA40.")
    command("git", "-C", str(SOURCE), "cat-file", "-e", commit + "^{commit}")
    command("git", "-C", str(SOURCE), "merge-base", "--is-ancestor", commit, head)
    target = APP_ROOT / "releases" / commit
    if target.is_symlink() or not target.is_dir() or target.resolve() != target:
        raise BackupRuntimeGateError("Immutable backup release is missing or redirected.")
    if ((target / ".cam-release").is_symlink() or
            not (target / ".cam-release-ready").is_file() or
            (target / ".cam-release").read_text(encoding="ascii").strip() != commit):
        raise BackupRuntimeGateError("Backup release integrity markers are invalid.")
    python = target / "apps/api/.venv/bin/python"
    if not python.is_file() and not python.is_symlink():
        raise BackupRuntimeGateError("Backup runtime Python is unavailable.")
    if not os.access(python, os.X_OK):
        raise BackupRuntimeGateError("Backup runtime Python is not executable.")
    service = target / "apps/api/app/modules/database_backup/service.py"
    cli = target / "apps/api/app/operations/database_backup_cli.py"
    try:
        if "DATABASE_BACKUP_DATABASE_URL" not in service.read_text() or "verify-config" not in cli.read_text():
            raise BackupRuntimeGateError("Existing release has no dedicated backup identity support.")
    except OSError as exc:
        raise BackupRuntimeGateError("Backup runtime files are unavailable.") from exc
    return target


def preflight_unit() -> None:
    current = APP_ROOT / "current"
    if not current.is_symlink() or not current.resolve().is_dir():
        raise BackupRuntimeGateError("Live backend release pointer is missing.")
    if not BACKUP_ENV.is_file() or BACKUP_ENV.is_symlink():
        raise BackupRuntimeGateError("Protected backup credential file is missing.")
    metadata = BACKUP_ENV.stat()
    if metadata.st_uid != 0 or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise BackupRuntimeGateError("Backup credential file must be root-owned 0600.")
    environment = command("systemctl", "show", SERVICE, "--property=EnvironmentFiles", "--value")
    if str(PRODUCTION_ENV) not in environment or str(BACKUP_ENV) not in environment:
        raise BackupRuntimeGateError("Backup service must load both protected env files.")
    user = command("systemctl", "show", SERVICE, "--property=User", "--value")
    if user != "creative-assets":
        raise BackupRuntimeGateError("Backup service must use creative-assets account.")
    if DROPIN.is_symlink() or DROPIN_ROOT.is_symlink():
        raise BackupRuntimeGateError("Untrusted backup override path.")
    if DROPIN_ROOT.exists():
        if not DROPIN_ROOT.is_dir():
            raise BackupRuntimeGateError("Backup override directory is not a directory.")
        if any(path.suffix == ".conf" and path != DROPIN for path in DROPIN_ROOT.iterdir()):
            raise BackupRuntimeGateError("Unrecognized backup service drop-in exists.")


def unit_override(target: Path) -> str:
    return (
        OWNER_MARKER + "\n"
        + "# Temporary pinned runtime; restore after the backend is migrated.\n"
        + "[Service]\n"
        + "WorkingDirectory=" + str(target / "apps/api") + "\n"
        + "ExecStart=\n"
        + "ExecStart=" + str(target / "apps/api/.venv/bin/python")
        + " -m app.operations.database_backup_cli backup\n"
    )


def config_verify(target: Path) -> None:
    # The source code runs only verify-config under systemd's protected
    # EnvironmentFile handling. No secrets or connection strings in argv.
    command(
        "systemd-run", "--wait", "--collect", "--quiet",
        "--unit=cam-db-backup-preflight",
        "--property=Type=oneshot",
        "--property=User=creative-assets",
        "--property=Group=creative-assets",
        "--property=WorkingDirectory=" + str(target / "apps/api"),
        "--property=EnvironmentFile=" + str(PRODUCTION_ENV),
        "--property=EnvironmentFile=" + str(BACKUP_ENV),
        "--property=NoNewPrivileges=yes",
        "--property=PrivateTmp=yes",
        "--property=PrivateDevices=yes",
        "--property=ProtectSystem=strict",
        "--property=ProtectHome=yes",
        "--",
        str(target / "apps/api/.venv/bin/python"),
        "-m", "app.operations.database_backup_cli", "verify-config",
        timeout=120, capture=False,
    )


def apply(target: Path) -> None:
    if os.geteuid() != 0:
        raise BackupRuntimeGateError("Activation requires root.")
    if DROPIN.exists() or DROPIN.is_symlink():
        raise BackupRuntimeGateError("An isolated backup override already exists.")
    execution = command("systemctl", "show", SERVICE, "--property=ExecStart", "--value")
    if str(APP_ROOT / "current/apps/api/.venv/bin/python") not in execution:
        raise BackupRuntimeGateError("Backup service already uses a nonstandard runtime.")
    config_verify(target)
    DROPIN_ROOT.mkdir(parents=True, mode=0o755, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=".backup-runtime-", dir=DROPIN_ROOT)
    temp = Path(temp_name)
    created = False
    try:
        with os.fdopen(fd, "w", encoding="ascii") as output:
            output.write(unit_override(target))
            output.flush()
            os.fsync(output.fileno())
        os.chmod(temp, 0o644)
        # Hard link provides atomic create-if-absent; never overwrite another
        # operator's drop-in, even if one appears during the preflight.
        os.link(temp, DROPIN, follow_symlinks=False)
        created = True
        command("systemctl", "daemon-reload", capture=False)
        execution = command("systemctl", "show", SERVICE, "--property=ExecStart", "--value")
        if str(target / "apps/api/.venv/bin/python") not in execution:
            raise BackupRuntimeGateError("New backup service did not use the pinned runtime.")
    except Exception:
        if created:
            DROPIN.unlink(missing_ok=True)
            try:
                command("systemctl", "daemon-reload", capture=False)
            except BackupRuntimeGateError:
                pass
        raise
    finally:
        temp.unlink(missing_ok=True)


def restore_current(*, do_apply: bool) -> None:
    if DROPIN.is_symlink() or not DROPIN.is_file():
        raise BackupRuntimeGateError("No owned backup override exists.")
    if not DROPIN.read_text(encoding="ascii").startswith(OWNER_MARKER + "\n"):
        raise BackupRuntimeGateError("Unrecognized backup override; refusing deletion.")
    current = (APP_ROOT / "current").resolve()
    service = current / "apps/api/app/modules/database_backup/service.py"
    if not current.is_dir() or "DATABASE_BACKUP_DATABASE_URL" not in service.read_text():
        raise BackupRuntimeGateError("Active backend cannot safely run dedicated backups.")
    if do_apply:
        if os.geteuid() != 0:
            raise BackupRuntimeGateError("Restoring backup unit requires root.")
        config_verify(current)
        previous_content = DROPIN.read_bytes()
        DROPIN.unlink()
        try:
            command("systemctl", "daemon-reload", capture=False)
            execution = command("systemctl", "show", SERVICE, "--property=ExecStart", "--value")
            if str(APP_ROOT / "current/apps/api/.venv/bin/python") not in execution:
                raise BackupRuntimeGateError("Current backup runtime was not restored.")
        except Exception:
            # Fail closed: preserve the previously pinned known-good runtime
            # instead of leaving an unverified backup service configuration.
            try:
                fd = os.open(DROPIN, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
                with os.fdopen(fd, "wb") as output:
                    output.write(previous_content)
                command("systemctl", "daemon-reload", capture=False)
            except Exception:
                pass
            raise
        print("BACKUP_RUNTIME_RESTORED_CURRENT=YES")
    else:
        print("BACKUP_RUNTIME_RESTORE_DRY_RUN=YES")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Isolate production database backup runtime")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--commit", help="SHA40 of an existing built main-ancestor release")
    group.add_argument("--restore-current", action="store_true")
    parser.add_argument("--apply", action="store_true", help="Explicit live systemd unit change")
    args = parser.parse_args(argv)
    try:
        head = git_authority()
        preflight_unit()
        if args.restore_current:
            restore_current(do_apply=args.apply)
            return 0
        target = release_path(args.commit, head)
        if DROPIN.exists() or DROPIN.is_symlink():
            raise BackupRuntimeGateError("Backup runtime override already present.")
        if args.apply:
            apply(target)
            print("BACKUP_RUNTIME_ISOLATED=YES")
        else:
            print("BACKUP_RUNTIME_DRY_RUN=YES")
        print("BACKUP_RUNTIME_RELEASE=" + args.commit)
        print("API_AND_WORKERS_UNCHANGED=YES")
        print("DATABASE_MIGRATIONS_RUN=NO")
        print("FULL_DUMP_STARTED=NO")
        return 0
    except (BackupRuntimeGateError, OSError, UnicodeError, ValueError) as exc:
        # Deliberately never include lower-level exceptions that may carry
        # environment contents or confidential credentials.
        print(
            "BACKUP_RUNTIME_FAILED=" +
            (str(exc) if isinstance(exc, BackupRuntimeGateError) else "Untrusted runtime state."),
            file=sys.stderr,
        )
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
