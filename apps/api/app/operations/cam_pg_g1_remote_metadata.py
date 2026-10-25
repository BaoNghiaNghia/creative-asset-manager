"""Operator-only, read-only comparison of CAM's backup receipt with Drive metadata.

Runs under the existing redacted production environment wrapper from a
trusted root SSH session. Never downloads, deletes, prunes, dumps, restores,
or prints the managed file ID, checksum, or OAuth credentials.
"""
from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import stat
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND, build_database_backup_storage
from app.modules.database_backup.verified_receipt import RECEIPT_NAME, MAX_RECEIPT_BYTES, has_recent_verified_receipt
from app.operations.database_backup_cli import verify_configuration


class RemoteMetadataGateError(RuntimeError):
    """Safe failure type; error messages must not be logged."""


def _receipt(staging_directory: str) -> dict[str, Any]:
    if not has_recent_verified_receipt(staging_directory):
        raise RemoteMetadataGateError("Recent private receipt is unavailable.")
    directory = Path(staging_directory)
    target = directory / RECEIPT_NAME
    if directory.is_symlink() or target.is_symlink():
        raise RemoteMetadataGateError("Receipt path is unsafe.")
    try:
        fd = os.open(target, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        with os.fdopen(fd, "rb") as handle:
            metadata = os.fstat(handle.fileno())
            if (not stat.S_ISREG(metadata.st_mode) or
                    metadata.st_size > MAX_RECEIPT_BYTES or
                    metadata.st_mode & 0o077):
                raise RemoteMetadataGateError("Receipt is not owner private.")
            payload = json.loads(handle.read(MAX_RECEIPT_BYTES + 1))
        if (not isinstance(payload, dict) or
                not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256", ""))) or
                not re.fullmatch(r"[0-9a-f]{32}", str(payload.get("md5", ""))) or
                type(payload.get("size_bytes")) is not int or
                payload["size_bytes"] <= 0 or
                payload.get("remote_verified") is not True):
            raise RemoteMetadataGateError("Receipt checksum metadata is invalid.")
        if not has_recent_verified_receipt(staging_directory):
            raise RemoteMetadataGateError("Receipt changed during verification.")
        return payload
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RemoteMetadataGateError("Receipt verification unavailable.") from exc


def compare_receipt_to_backups(
    receipt: dict[str, Any], backups: list[Any], folder_id: str,
) -> dict[str, object]:
    matches = []
    for backup in backups:
        if (folder_id not in backup.parents or
                backup.app_properties.get("cam_kind") != DATABASE_BACKUP_KIND or
                backup.size_bytes != receipt["size_bytes"] or
                not backup.md5_checksum):
            continue
        sha = backup.app_properties.get("cam_sha256", "").lower()
        md5 = backup.md5_checksum.lower()
        if (hmac.compare_digest(sha, receipt["sha256"]) and
                hmac.compare_digest(md5, receipt["md5"])):
            matches.append(backup)
    if len(matches) != 1:
        raise RemoteMetadataGateError("No unique matching managed backup.")
    return {
        "G1_LOCAL_RECENT_RECEIPT": "PASS",
        "G1_DRIVE_CONTENT_MD5_METADATA_MATCH": "PASS",
        "G1_DRIVE_SIZE_AND_SHA_PROPERTY_MATCH": "PASS",
        "G1_STREAM_REDOWNLOAD": "NOT_RUN",
        "G1_ISOLATED_RESTORE": "NOT_RUN",
        "G1_RECOVERY_GATE": "BLOCKED",
        "MANAGED_BACKUP_COUNT": len(backups),
        "MATCHED_BACKUP_BYTES": receipt["size_bytes"],
        "DATABASE_MUTATIONS": "NONE",
    }


async def verify(settings: Any) -> dict[str, object]:
    verify_configuration(settings)
    receipt = _receipt(str(settings.DATABASE_BACKUP_STAGING_DIRECTORY))
    storage = build_database_backup_storage(settings)
    backups = await storage.list_managed_backups()
    return compare_receipt_to_backups(receipt, backups, storage.folder_id)


def main() -> int:
    try:
        result = asyncio.run(verify(get_settings()))
    except Exception as exc:
        # Do not propagate provider/credential errors to the terminal.
        print(json.dumps({
            "G1_RECOVERY_GATE": "BLOCKED",
            "REMOTE_METADATA_CHECK": "BLOCKED",
            "ERROR_TYPE": type(exc).__name__,
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
