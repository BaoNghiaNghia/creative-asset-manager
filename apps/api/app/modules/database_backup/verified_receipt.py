"""Local non-secret receipt for a newly uploaded and remote-verified DB backup.

This receipt is evidence of an uploaded copy, NOT of a successful restore.
Missing, old, malformed or symlinked receipts fail closed for production
retention. The backup service and CAM worker share the deployment service UID.
"""
from __future__ import annotations

import json
import os
import re
import stat
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

RECEIPT_NAME = ".latest-verified-remote-backup.json"
MAX_RECEIPT_BYTES = 1024
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MD5 = re.compile(r"^[0-9a-f]{32}$")


def write_verified_receipt(
    staging_directory: str, *, sha256: str, local_md5: str,
    remote_md5: str, size_bytes: int, completed_at: datetime | None = None,
) -> None:
    """Write only after the workflow has independently verified remote bytes."""
    if (not isinstance(sha256, str) or not isinstance(local_md5, str)
            or not isinstance(remote_md5, str) or type(size_bytes) is not int
            or _SHA256.fullmatch(sha256) is None or _MD5.fullmatch(local_md5) is None
            or remote_md5 != local_md5 or size_bytes <= 0):
        raise ValueError("Verified backup metadata is invalid")
    directory = Path(staging_directory)
    if not directory.is_absolute() or directory.is_symlink() or not directory.is_dir():
        raise ValueError("Backup staging directory is not a safe existing directory")
    when = completed_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("Backup receipt timestamp must contain a timezone")
    receipt = {
        "version": 1,
        "completed_at": when.astimezone(timezone.utc).isoformat(),
        "sha256": sha256,
        "md5": local_md5,
        "size_bytes": size_bytes,
        "remote_verified": True,
    }
    encoded = (json.dumps(receipt, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")
    if len(encoded) > MAX_RECEIPT_BYTES:
        raise ValueError("Backup receipt exceeds size limit")
    target = directory / RECEIPT_NAME
    if target.is_symlink():
        raise ValueError("Existing backup receipt must not be a symbolic link")
    fd, temporary = tempfile.mkstemp(prefix=".backup-receipt.", dir=directory)
    try:
        with os.fdopen(fd, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(encoded)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, target)
    finally:
        Path(temporary).unlink(missing_ok=True)


def has_recent_verified_receipt(
    staging_directory: str, *, now: datetime | None = None, max_age_hours: int = 108,
) -> bool:
    """Conservatively validate owner-private receipt data without remote I/O."""
    if max_age_hours <= 0:
        return False
    directory = Path(staging_directory)
    target = directory / RECEIPT_NAME
    if not directory.is_absolute() or directory.is_symlink() or target.is_symlink():
        return False
    try:
        descriptor = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(descriptor, "rb") as stream:
            metadata = os.fstat(stream.fileno())
            if (not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_size <= 0 or metadata.st_size > MAX_RECEIPT_BYTES
                    or metadata.st_mode & 0o077):
                return False
            payload = json.loads(stream.read(MAX_RECEIPT_BYTES + 1))
        if (type(payload) is not dict or payload.get("version") != 1
                or payload.get("remote_verified") is not True
                or type(payload.get("size_bytes")) is not int
                or payload["size_bytes"] <= 0
                or not isinstance(payload.get("sha256"), str)
                or not _SHA256.fullmatch(payload["sha256"])
                or not isinstance(payload.get("md5"), str)
                or not _MD5.fullmatch(payload["md5"])):
            return False
        timestamp = datetime.fromisoformat(payload["completed_at"])
        if timestamp.tzinfo is None:
            return False
        reference = now or datetime.now(timezone.utc)
        if reference.tzinfo is None:
            return False
        age = reference.astimezone(timezone.utc) - timestamp.astimezone(timezone.utc)
        return timedelta(0) <= age <= timedelta(hours=max_age_hours)
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError):
        return False
