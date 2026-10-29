#!/usr/bin/env python3
"""Opt-in, API-only Typography V2 Ideas/Style Lab canary on one known tenant.

Default dry-run. Does not modify PostgreSQL, run backups, enable ImageGen,
enable G3 workers, launch Scout or restart non-API services.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time

SOURCE = Path(__file__).resolve().parents[1]
APP = Path("/opt/creative-asset-manager")
API = "creative-asset-manager-api.service"
DB = "creative_asset_manager"
TENANT_SLUG = "creative-assets"
VERSION = "0189_typography_v2_analysis_queue"
CONFIG_ROOT = Path("/etc/creative-asset-manager")
CANARY_ENV = CONFIG_ROOT / "typography-v2-api-canary.env"
DROPIN_DIR = Path("/etc/systemd/system/creative-asset-manager-api.service.d")
DROPIN = DROPIN_DIR / "99-typography-v2-api-canary.conf"
MARKER = "# CAM Typography V2 API-only canary v1"
VALID_TENANT = re.compile(r"[A-Za-z0-9_-]{1,255}\Z")


class GateError(RuntimeError):
    pass


def run(*args: str, timeout: int = 25) -> str:
    try:
        result = subprocess.run(
            args, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, timeout=timeout, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError("Canary preflight command was unavailable.") from exc
    if result.returncode:
        raise GateError("Canary preflight command failed.")
    return result.stdout.strip()


def source_gate() -> None:
    if run("git", "-C", str(SOURCE), "rev-parse", "--show-toplevel") != str(SOURCE):
        raise GateError("Must run from an exact Git checkout.")
    if run("git", "-C", str(SOURCE), "status", "--porcelain", "--untracked-files=normal"):
        raise GateError("Source checkout is dirty.")
    if run("git", "-C", str(SOURCE), "rev-parse", "HEAD") != run(
        "git", "-C", str(SOURCE), "rev-parse", "origin/main"
    ):
        raise GateError("Use the latest merged main source.")


def db_gate() -> str:
    current = run(
        "runuser", "-u", "postgres", "--", "psql",
        "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-d", DB, "-c", "SELECT version_num FROM alembic_version;",
    )
    if current != VERSION:
        raise GateError("Production PostgreSQL must be at the Typography 0189 schema.")
    tenant = run(
        "runuser", "-u", "postgres", "--", "psql",
        "-X", "-A", "-t", "-v", "ON_ERROR_STOP=1",
        "-d", DB, "-c",
        "SELECT id FROM tenants WHERE slug='creative-assets' AND status='active';",
    ).splitlines()
    if len(tenant) != 1 or not VALID_TENANT.fullmatch(tenant[0]):
        raise GateError("Expected exactly one active target tenant.")
    return tenant[0]


def production_gate() -> None:
    if not (APP / "current").is_symlink():
        raise GateError("No active CAM production release pointer.")
    active = (APP / "current").resolve()
    if active.parent != APP / "releases":
        raise GateError("Unknown backend release pointer.")
    if not (active / "apps/api/app/modules/realistic_review_ugc/typography_router.py").is_file():
        raise GateError("Current API does not contain the Typography V2 canary router.")
    if run("systemctl", "show", API, "-p", "ActiveState", "--value") != "active":
        raise GateError("CAM API must already be healthy.")
    if DROPIN.is_symlink() or DROPIN_DIR.is_symlink() or CANARY_ENV.is_symlink():
        raise GateError("Canary configuration has an unsafe symlink.")


def _write_exclusive(path: Path, data: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".cam-typography-canary-", dir=path.parent)
    temp = Path(temporary)
    try:
        with os.fdopen(fd, "w", encoding="ascii") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temp, mode)
        # Atomic create-if-absent. Never replace an administrator's file.
        os.link(temp, path, follow_symlinks=False)
    finally:
        temp.unlink(missing_ok=True)


def config_for(tenant: str) -> str:
    if not VALID_TENANT.fullmatch(tenant):
        raise GateError("Invalid canary tenant.")
    return (
        "# CAM Typography V2 API-only canary v1\n"
        "TYPOGRAPHY_V2_ENABLED=true\n"
        f"TYPOGRAPHY_V2_CANARY_TENANT_IDS={tenant}\n"
        "TYPOGRAPHY_V2_PILOT_WORKER_ENABLED=false\n"
        "TYPOGRAPHY_V2_IMAGEGEN_PILOT_ENABLED=false\n"
        "TYPOGRAPHY_V2_PILOT_ROLLBACK_VERIFIED=false\n"
    )


def dropin_text() -> str:
    return (
        MARKER + "\n"
        "[Service]\n"
        "EnvironmentFile=" + str(CANARY_ENV) + "\n"
    )


def _reload_and_restart() -> None:
    run("systemctl", "daemon-reload", timeout=35)
    run("systemctl", "restart", API, timeout=180)
    if run("systemctl", "show", API, "-p", "ActiveState", "--value") != "active":
        raise GateError("CAM API did not return to active state.")

    # Validate real readiness, not just a systemd process state. Print neither
    # the production environment nor the tenant ID.
    sys.path.insert(0, str(SOURCE / "deploy" / "tools"))
    from production_env import parse_environment_file
    host = parse_environment_file(CONFIG_ROOT / "production.env").get(
        "TRUSTED_HOSTS", ""
    ).split(",")[0].strip()
    if not host or any(character.isspace() for character in host):
        raise GateError("Production trusted host is unavailable.")
    for _ in range(20):
        try:
            run(
                "curl", "--fail", "--silent", "--show-error",
                "--max-time", "5", "-H", "Host: " + host,
                "http://127.0.0.1:8000/ready", timeout=9,
            )
            return
        except GateError:
            time.sleep(2)
    raise GateError("CAM API readiness did not recover.")


def activate(tenant: str) -> None:
    if CANARY_ENV.exists() or DROPIN.exists():
        raise GateError("Canary files already exist; refusing overwrite.")
    created_env = False
    created_dropin = False
    try:
        _write_exclusive(CANARY_ENV, config_for(tenant), 0o600)
        created_env = True
        _write_exclusive(DROPIN, dropin_text(), 0o644)
        created_dropin = True
        _reload_and_restart()
        installed = run("systemctl", "show", API, "-p", "EnvironmentFiles", "--value")
        if str(CANARY_ENV) not in installed:
            raise GateError("Systemd did not load the canary EnvironmentFile.")
    except Exception:
        if created_dropin and DROPIN.is_file():
            DROPIN.unlink()
        if created_env and CANARY_ENV.is_file():
            CANARY_ENV.unlink()
        if created_env or created_dropin:
            try:
                _reload_and_restart()
            except Exception:
                pass
        raise


def disable(tenant: str) -> None:
    if DROPIN.is_symlink() or CANARY_ENV.is_symlink():
        raise GateError("Refusing to remove a symbolic link.")
    if not DROPIN.is_file() or DROPIN.read_text(encoding="ascii") != dropin_text():
        raise GateError("Canary service override differs from our owned configuration.")
    if not CANARY_ENV.is_file() or CANARY_ENV.read_text(encoding="ascii") != config_for(tenant):
        raise GateError("Canary environment differs from the approved tenant configuration.")
    old_override = DROPIN.read_bytes()
    old_env = CANARY_ENV.read_bytes()
    DROPIN.unlink()
    CANARY_ENV.unlink()
    try:
        _reload_and_restart()
    except Exception:
        # Restore only our original, non-secret canary records; do not modify DB.
        try:
            _write_exclusive(CANARY_ENV, old_env.decode("ascii"), 0o600)
            _write_exclusive(DROPIN, old_override.decode("ascii"), 0o644)
            _reload_and_restart()
        except Exception:
            pass
        raise


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="Make a scoped production systemd change")
    parser.add_argument("--disable", action="store_true", help="Only remove our own canary files")
    args = parser.parse_args(argv)
    try:
        if os.geteuid() != 0:
            raise GateError("Root operator required.")
        source_gate()
        production_gate()
        tenant = db_gate()
        if args.disable:
            if args.apply:
                disable(tenant)
            else:
                if not DROPIN.is_file() or not DROPIN.read_text(encoding="ascii").startswith(MARKER + "\n"):
                    raise GateError("No owned canary to disable.")
            print("CANARY_DISABLED=YES" if args.apply else "CANARY_DISABLE_DRY_RUN=PASS")
        else:
            if CANARY_ENV.exists() or DROPIN.exists():
                raise GateError("Canary configuration already exists.")
            if args.apply:
                activate(tenant)
            print("CANARY_ENABLED=YES" if args.apply else "CANARY_DRY_RUN=PASS")
        print("TENANT_UNIQUELY_IDENTIFIED=YES")
        print("IMAGEGEN_PILOT_ENABLED=NO")
        print("SCOUT_ENABLED_BY_THIS_TOOL=NO")
        print("DATABASE_MUTATIONS=NONE")
        print("BACKUP_CREATED=NO")
        return 0
    except (GateError, OSError, UnicodeError, ValueError) as exc:
        # Never print the tenant ID, sensitive environment or command stderr.
        description = str(exc) if isinstance(exc, GateError) else "Canary operator filesystem error."
        print("CANARY_FAILED=" + description, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
