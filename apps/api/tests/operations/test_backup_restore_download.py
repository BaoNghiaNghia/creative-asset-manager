"""No network/production secrets: mock Drive's raw streaming response."""
import asyncio
from dataclasses import dataclass
import hashlib
import os

import httpx
import pytest

from app.operations.backup_restore_download import DownloadBlocked, download_managed_backup


@dataclass
class Backup:
    remote_file_id: str
    name: str
    size_bytes: int
    parents: tuple[str, ...]
    app_properties: dict
    md5_checksum: str


class Storage:
    folder_id = "managed-private-folder"
    def __init__(self, record):
        self.record = record
    async def list_managed_backups(self):
        return [self.record]
    async def _get_access_token(self):
        return "TEST_TOKEN_NOT_REAL"


@pytest.fixture
def valid(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    raw = b"PGDMP\0backup-placeholder-not-real" * 40
    sha = hashlib.sha256(raw).hexdigest()
    md5 = hashlib.md5(raw, usedforsecurity=False).hexdigest()
    record = Backup("1i4i3JNo7EwOxtkP1mmKZ847P2Otx4IG4",
                    "cam-db-20261020-164908+0700.dump",
                    len(raw), ("managed-private-folder",),
                    {"cam_kind": "database_backup_v1", "cam_sha256": sha}, md5)
    return private, raw, record


def run(private, record, payload: bytes, *, status=200):
    def responder(request):
        assert request.url.params["alt"] == "media"
        assert request.headers["authorization"] == "Bearer TEST_TOKEN_NOT_REAL"
        return httpx.Response(status, content=payload)
    return asyncio.run(download_managed_backup(
        backup_id=record.remote_file_id, directory=private,
        storage=Storage(record), transport=httpx.MockTransport(responder),
    ))


def test_download_has_private_permissions_and_matches_both_checksums(valid):
    private, raw, record = valid
    result = run(private, record, raw)
    file = private / record.name
    assert result["DOWNLOAD_VERIFIED"] == "PASS"
    assert result["RESTORE_REHEARSAL"] == "NOT_RUN"
    assert file.read_bytes() == raw
    assert (file.stat().st_mode & 0o777) == 0o600


def test_same_file_does_not_overwrite_or_redownload(valid):
    private, raw, record = valid
    run(private, record, raw)
    with pytest.raises(DownloadBlocked):
        run(private, record, raw)
    assert (private / record.name).read_bytes() == raw


@pytest.mark.parametrize("payload", [b"", b"corrupt" * 100, b"x" * 5000])
def test_partial_or_changed_backup_is_destroyed(valid, payload):
    private, _, record = valid
    with pytest.raises(DownloadBlocked):
        run(private, record, payload)
    assert not list(private.iterdir())


def test_failed_http_download_cleans_private_partial(valid):
    private, raw, record = valid
    with pytest.raises(DownloadBlocked):
        run(private, record, raw, status=403)
    assert not list(private.iterdir())


def test_refuses_world_readable_directory(valid):
    private, raw, record = valid
    private.chmod(0o755)
    with pytest.raises(DownloadBlocked, match="group/other"):
        run(private, record, raw)
    assert not list(private.iterdir())


def test_refuses_unmanaged_backup(valid):
    private, raw, record = valid
    record.app_properties["cam_kind"] = "asset"
    with pytest.raises(DownloadBlocked, match="managed"):
        run(private, record, raw)
    assert not list(private.iterdir())


def test_refuses_missing_hash_metadata(valid):
    private, raw, record = valid
    record.app_properties.pop("cam_sha256")
    with pytest.raises(DownloadBlocked, match="checksum"):
        run(private, record, raw)
    assert not list(private.iterdir())


def test_refuses_invalid_backup_ids(valid):
    private, raw, record = valid
    record.remote_file_id = "../../private"
    with pytest.raises(DownloadBlocked, match="Invalid managed backup"):
        run(private, record, raw)
    assert not list(private.iterdir())


def test_refuses_symlink_destination(valid):
    private, raw, record = valid
    target = private.parent / "victim.dump"
    target.write_text("DO NOT MODIFY")
    (private / record.name).symlink_to(target)
    with pytest.raises(DownloadBlocked):
        run(private, record, raw)
    assert target.read_text() == "DO NOT MODIFY"
    assert (private / record.name).is_symlink()
