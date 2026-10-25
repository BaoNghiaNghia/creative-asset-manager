"""Operator-only verified CAM backup download onto a SEPARATE mounted volume.

Run only from trusted root SSH with the reviewed production_env.py run-redacted
wrapper. Never removes remote data, restores production, or provisions disks.
Fails closed if destination is on the production root device or low on space.
No managed file ID, digest, tenant identity, token or credential is printed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.operations.database_backup_cli import verify_configuration
from app.operations.backup_restore_download import (
    _private_directory,
    download_managed_backup,
)
from app.operations.cam_pg_g1_remote_metadata import (
    _receipt, compare_receipt_to_backups, RemoteMetadataGateError,
)
from app.modules.database_backup.google_drive import (
    build_database_backup_storage, DATABASE_BACKUP_KIND,
)

GIB = 1024**3
MIN_SEPARATE_VOLUME_FREE = 20 * GIB


class DownloadGateError(RuntimeError):
    """Non-sensitive failure state."""


def check_separate_volume(directory: Path, *, free_bytes: int | None = None) -> None:
    """Only an existing root-private directory on a different filesystem is valid."""
    if os.geteuid() != 0:
        raise DownloadGateError("root-only operator command")
    if not directory.is_absolute() or directory.is_symlink():
        raise DownloadGateError("private absolute directory required")
    # Reject symlinked ancestors, not merely a symlink at the final leaf.
    absolute = Path(os.path.abspath(directory))
    if absolute != directory or any(parent.is_symlink() for parent in (absolute, *absolute.parents)):
        raise DownloadGateError("symlinked destination is forbidden")
    try:
        info = directory.stat()
        root_info = Path("/").stat()
    except OSError as exc:
        raise DownloadGateError("destination metadata unavailable") from exc
    if info.st_dev == root_info.st_dev:
        raise DownloadGateError("target must be on a separate mounted filesystem")
    _private_directory(directory)  # Owner UID and mode checks; no directory creation.
    free = shutil.disk_usage(directory).free if free_bytes is None else free_bytes
    if free < MIN_SEPARATE_VOLUME_FREE:
        raise DownloadGateError("at least 20 GiB free required on separate volume")


def select_unique_receipt_backup(receipt: dict, backups: list[Any], folder_id: str) -> str:
    """Reuse the official strict metadata gate and return only an internal ID."""
    compare_receipt_to_backups(receipt, backups, folder_id)
    matched = [
        backup for backup in backups
        if folder_id in backup.parents
        and backup.app_properties.get("cam_kind") == DATABASE_BACKUP_KIND
        and backup.size_bytes == receipt["size_bytes"]
        and backup.md5_checksum is not None
        and backup.md5_checksum.lower() == receipt["md5"]
        and backup.app_properties.get("cam_sha256", "").lower() == receipt["sha256"]
    ]
    if len(matched) != 1:
        raise DownloadGateError("matching backup must be unique")
    return matched[0].remote_file_id


async def download_verified_backup(directory: Path) -> dict[str, object]:
    check_separate_volume(directory)
    settings = get_settings()
    verify_configuration(settings)
    receipt = _receipt(str(settings.DATABASE_BACKUP_STAGING_DIRECTORY))
    storage = build_database_backup_storage(settings)
    backups = await storage.list_managed_backups()
    backup_id = select_unique_receipt_backup(receipt, backups, storage.folder_id)
    downloaded = await download_managed_backup(
        backup_id=backup_id, directory=directory, storage=storage,
    )
    # The downloader validates SHA-256, Drive's content MD5, size and folder
    # *against newly streamed bytes*, independently of the receipt metadata.
    if (downloaded["sha256"] != receipt["sha256"] or
            downloaded["md5"] != receipt["md5"] or
            downloaded["size_bytes"] != receipt["size_bytes"]):
        raise DownloadGateError("independent stream differs from receipt")
    return {
        "G1_DRIVE_STREAM_VERIFIED": "PASS",
        "G1_DOWNLOAD_ON_SEPARATE_VOLUME": "PASS",
        "G1_ISOLATED_RESTORE": "NOT_RUN",
        "G1_RECOVERY_GATE": "BLOCKED",
        "MATCHED_BACKUP_BYTES": downloaded["size_bytes"],
        "PRIVATE_BACKUP_FILE": downloaded["file_path"],
        "PRODUCTION_MUTATIONS": "NONE",
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--private-directory", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = asyncio.run(download_verified_backup(args.private_directory))
    except Exception as exc:
        # Do not disclose provider exceptions, OAuth tokens, file IDs, digests.
        print(json.dumps({
            "G1_DRIVE_STREAM_VERIFIED": "BLOCKED",
            "ERROR_TYPE": type(exc).__name__,
            "PRODUCTION_MUTATIONS": "NONE",
        }, sort_keys=True))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
