"""Operator-only: download one managed production DB backup for an isolated restore.

The operator must invoke this with protected production settings (using the
production_env.py wrapper). This does NOT restore or modify any database,
alter remote storage, or create directories. It never logs credentials or rows.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import os
from pathlib import Path
import re
import shutil
import stat

import httpx

from app.core.config import get_settings
from app.modules.database_backup.google_drive import DATABASE_BACKUP_KIND
from app.operations.database_backup_cli import verify_configuration
from app.modules.database_backup.google_drive import build_database_backup_storage

_FILE_ID = re.compile(r"[A-Za-z0-9_-]{10,160}\Z")
_FILENAME = re.compile(r"cam-db-\d{8}-\d{6}[+-]\d{4}\.dump\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_MD5 = re.compile(r"[0-9a-f]{32}\Z")
_MIN_FREE_REMAINDER = 1024 * 1024 * 1024
_MAX_BACKUP_BYTES = 20 * 1024**3
_CHUNK = 1024 * 1024


class DownloadBlocked(RuntimeError):
    """Safe, secret-free error for the operator terminal."""


def _private_directory(directory: Path) -> Path:
    if not directory.is_absolute() or directory.is_symlink():
        raise DownloadBlocked("A real absolute private directory is required.")
    try:
        info = directory.stat()
    except OSError as exc:
        raise DownloadBlocked("Private output directory does not exist.") from exc
    if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid():
        raise DownloadBlocked("Private directory must belong to the invoking operator.")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise DownloadBlocked("Private directory must not allow group/other access.")
    return directory.resolve(strict=True)


def _verified_source(remote, *, expected_folder: str) -> tuple[str, int, str, str]:
    filename = str(remote.name or "")
    size = remote.size_bytes
    md5 = str(remote.md5_checksum or "").lower()
    sha = str(remote.app_properties.get("cam_sha256", "")).lower()
    if not _FILENAME.fullmatch(filename):
        raise DownloadBlocked("Remote backup has an unexpected filename.")
    if not isinstance(size, int) or not 0 < size <= _MAX_BACKUP_BYTES:
        raise DownloadBlocked("Remote backup has an invalid or excessive size.")
    if expected_folder not in remote.parents or remote.app_properties.get("cam_kind") != DATABASE_BACKUP_KIND:
        raise DownloadBlocked("Remote file is not a managed database backup.")
    if not _MD5.fullmatch(md5) or not _SHA256.fullmatch(sha):
        raise DownloadBlocked("Backup remote checksum metadata is incomplete.")
    return filename, size, sha, md5


async def download_managed_backup(
    *, backup_id: str, directory: Path, storage,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict:
    if not _FILE_ID.fullmatch(str(backup_id)):
        raise DownloadBlocked("Invalid managed backup file ID.")
    output_dir = _private_directory(directory)
    backups = await storage.list_managed_backups()
    matching = [entry for entry in backups if entry.remote_file_id == backup_id]
    if len(matching) != 1:
        raise DownloadBlocked("Backup not found uniquely in managed Google Drive folder.")
    filename, size, sha256, md5_expected = _verified_source(
        matching[0], expected_folder=storage.folder_id,
    )
    if shutil.disk_usage(output_dir).free < size + _MIN_FREE_REMAINDER:
        raise DownloadBlocked("Insufficient free disk space to download the backup safely.")
    path = output_dir / filename
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC

    descriptor = None
    created = False
    try:
        descriptor = os.open(path, flags, 0o600)
        created = True
        os.fchmod(descriptor, 0o600)
        token = await storage._get_access_token()
        sha = hashlib.sha256()
        md5 = hashlib.md5(usedforsecurity=False)
        length = 0
        async with httpx.AsyncClient(
            transport=transport,
            timeout=httpx.Timeout(120, connect=15, read=120),
            headers={"Authorization": f"Bearer {token}"},
        ) as client:
            async with client.stream(
                "GET",
                f"https://www.googleapis.com/drive/v3/files/{backup_id}",
                params={"alt": "media", "supportsAllDrives": "true"},
            ) as response:
                if response.status_code != 200:
                    raise DownloadBlocked("Google Drive raw backup download failed.")
                with os.fdopen(descriptor, "wb", closefd=True) as stream:
                    descriptor = None
                    async for block in response.aiter_bytes(_CHUNK):
                        length += len(block)
                        if length > size:
                            raise DownloadBlocked("Downloaded backup exceeded declared size.")
                        stream.write(block)
                        sha.update(block)
                        md5.update(block)
                    stream.flush()
                    os.fsync(stream.fileno())
        if length != size or not hmac.compare_digest(sha.hexdigest(), sha256) or not hmac.compare_digest(md5.hexdigest(), md5_expected):
            raise DownloadBlocked("Downloaded backup size/hash does not match managed remote metadata.")
        return {
            "DOWNLOAD_VERIFIED": "PASS",
            "file_path": str(path),
            "size_bytes": length,
            "sha256": sha256,
            "md5": md5_expected,
            "RESTORE_REHEARSAL": "NOT_RUN",
        }
    except (httpx.HTTPError, OSError) as exc:
        raise DownloadBlocked("Backup download failed before local integrity verification.") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        # Discard any incomplete/corrupt file. Never remove a preexisting path:
        # O_EXCL must have created this one for us to own its lifecycle.
        if created and path.exists() and (
            "length" not in locals() or length != size
            or "sha" not in locals()
            or sha.hexdigest() != sha256
            or md5.hexdigest() != md5_expected
        ):
            path.unlink()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-id", required=True)
    parser.add_argument("--private-directory", type=Path, required=True)
    args = parser.parse_args()
    try:
        settings = get_settings()
        verify_configuration(settings)
        storage = build_database_backup_storage(settings)
        result = asyncio.run(download_managed_backup(
            backup_id=args.backup_id,
            directory=args.private_directory,
            storage=storage,
        ))
    except (DownloadBlocked, ValueError, RuntimeError) as exc:
        # Never emit provider response details, credentials or file contents.
        print(f"DOWNLOAD_VERIFIED=FAIL ({type(exc).__name__})")
        return 2
    print("DOWNLOAD_VERIFIED=PASS")
    print(f"FILE={result['file_path']}")
    print(f"SIZE_BYTES={result['size_bytes']}")
    print(f"SHA256={result['sha256']}")
    print(f"MD5={result['md5']}")
    print("RESTORE_REHEARSAL=NOT_RUN")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
