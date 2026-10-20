#!/usr/bin/env python3
"""Repair ONLY the dedicated PostgreSQL 16 Catinat rehearsal configuration.

pg_conftool passes numeric dotted listen_addresses as invalid bare syntax;
passing embedded single quotes results in triple-quoted config. This guarded
operator-only helper replaces the dedicated cluster's active setting with
exactly: listen_addresses = '127.0.0.1'
and enforces peer authentication on local Unix sockets.

Never targets production's PostgreSQL main cluster.
"""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile

CONF_DIR = Path("/etc/postgresql/16/catinat_rehearsal")
LISTEN = re.compile(r"^\s*listen_addresses\s*=", re.IGNORECASE)
LOCAL = re.compile(r"^(\s*local\s+\S+\s+\S+\s+)(\S+)(.*)$", re.IGNORECASE)


class RehearsalConfigError(RuntimeError):
    pass


def fix_postgresql_conf(text: str) -> str:
    lines = text.splitlines(keepends=True)
    positions = [i for i, line in enumerate(lines) if LISTEN.match(line)]
    if len(positions) > 1:
        raise RehearsalConfigError("Multiple active listen_addresses values; refused.")
    setting = "listen_addresses = '127.0.0.1'\n"
    if positions:
        lines[positions[0]] = setting
    else:
        lines.append(setting)
    return "".join(lines)


def fix_pg_hba_conf(text: str) -> str:
    """Disable accidental local trust from initdb; require operating-system peer."""
    lines = text.splitlines(keepends=True)
    matches = []
    for index, line in enumerate(lines):
        if line.lstrip().startswith("#") or not line.strip():
            continue
        match = LOCAL.match(line)
        if match is not None:
            auth = match.group(2).lower()
            if auth not in ("trust", "peer"):
                raise RehearsalConfigError("Unexpected local auth method in rehearsal cluster.")
            matches.append((index, match))
    if not matches:
        raise RehearsalConfigError("No local authentication rules found.")
    for index, match in matches:
        line_ending = "\n" if lines[index].endswith("\n") else ""
        lines[index] = match.group(1) + "peer" + match.group(3) + line_ending
    return "".join(lines)


def _replace_dedicated_file(name: str, transform) -> None:
    if name not in ("postgresql.conf", "pg_hba.conf"):
        raise RehearsalConfigError("Only the dedicated rehearsal cluster config may be edited.")
    path = CONF_DIR / name
    if path.is_symlink() or CONF_DIR.is_symlink():
        raise RehearsalConfigError("Rehearsal config path cannot be a symlink.")
    info = path.stat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != 0:
        raise RehearsalConfigError("Rehearsal config must be a root-owned regular file.")
    before = path.read_text(encoding="utf-8")
    after = transform(before)
    if after == before:
        return
    descriptor, tmp = tempfile.mkstemp(prefix=f".{name}.catinat-", dir=CONF_DIR)
    try:
        os.fchmod(descriptor, stat.S_IMODE(info.st_mode))
        os.fchown(descriptor, info.st_uid, info.st_gid)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(after)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def main() -> int:
    if os.geteuid() != 0:
        raise RehearsalConfigError("Authorized root SSH operator required.")
    if not CONF_DIR.is_dir():
        raise RehearsalConfigError("Dedicated rehearsal cluster config directory missing.")
    _replace_dedicated_file("postgresql.conf", fix_postgresql_conf)
    _replace_dedicated_file("pg_hba.conf", fix_pg_hba_conf)
    print("REHEARSAL_CONFIG=PASS (listen_addresses=127.0.0.1, local_auth=peer)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
