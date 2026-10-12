"""Stage 1 low-resolution Drive drafts and 48-hour cleanup safeguards."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image

from app.modules.realistic_review_ugc.stage1_temp_previews import (
    TEMP_FOLDER_ID, compact_preview, preview_paths, upload_temp_previews,
)
from app.domain.providers.contracts import StorageProviderError
from app.operations.stage1_temp_retention import access_token_with_retry, eligible, purge
from app.providers.google.storage import GoogleDriveAssetStorage



def test_temp_retention_retries_retryable_token_errors(monkeypatch):
    calls = []
    delays = []

    class FakeStorage:
        async def get_access_token(self):
            calls.append(1)
            if len(calls) < 3:
                raise StorageProviderError("temporary network error", retryable=True)
            return "stub-token"

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", fake_sleep)
    assert asyncio.run(access_token_with_retry(FakeStorage())) == "stub-token"
    assert len(calls) == 3
    assert delays == [1, 2]


def test_temp_retention_does_not_retry_rejected_google_credentials(monkeypatch):
    calls = []
    delays = []

    class FakeStorage:
        async def get_access_token(self):
            calls.append(1)
            raise StorageProviderError("rejected credentials", retryable=False)

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", fake_sleep)
    with pytest.raises(StorageProviderError, match="rejected credentials"):
        asyncio.run(access_token_with_retry(FakeStorage()))
    assert len(calls) == 1
    assert delays == []


def test_temp_retention_exhaustion_fails_closed_without_further_calls(monkeypatch):
    calls = []
    delays = []

    class FakeStorage:
        async def get_access_token(self):
            calls.append(1)
            raise StorageProviderError("connect timeout", retryable=True)

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", fake_sleep)
    with pytest.raises(StorageProviderError, match="connect timeout"):
        asyncio.run(access_token_with_retry(FakeStorage()))
    assert len(calls) == 3
    assert delays == [1, 2]


def test_compacts_large_draft_and_ignores_final_outputs(tmp_path):
    base = tmp_path / "working" / "previews"
    base.mkdir(parents=True)
    Image.new("RGBA", (2900, 1900), (40, 50, 80, 255)).save(base / "sketch.png")
    final = tmp_path / "output" / "final"
    final.mkdir(parents=True)
    Image.new("RGB", (2048, 2048)).save(final / "design_01.png")
    found = preview_paths(tmp_path)
    assert [p.name for p in found] == ["sketch.png"]
    from io import BytesIO
    blob = compact_preview(found[0])
    with Image.open(BytesIO(blob)) as image:
        assert image.format == "WEBP"
        assert max(image.size) <= 640
    assert len(blob) < (base / "sketch.png").stat().st_size


def test_preview_uploader_uses_only_temp_folder_and_ownership_metadata(tmp_path):
    source = tmp_path / "working" / "previews"
    source.mkdir(parents=True)
    Image.new("RGB", (1200, 1600), (55, 75, 85)).save(source / "draft.png")

    class FakeDrive(GoogleDriveAssetStorage):
        def __init__(self):
            super().__init__(storage_access_token="test", root_folder_id="final-root")
            self.uploads = []

        async def store_asset(self, payload):
            data = b"".join([item async for item in payload.body])
            self.uploads.append((payload, data))
            return SimpleNamespace(remote_file_id="temporary-file")

    storage = FakeDrive()
    count = asyncio.run(upload_temp_previews(
        storage, workspace=tmp_path, tenant_id="t1",
        job_id="11111111-1111-1111-1111-111111111111",
        processing_job_id="22222222-2222-2222-2222-222222222222",
    ))
    assert count == 1
    payload, data = storage.uploads[0]
    assert payload.destination_folder_id == TEMP_FOLDER_ID
    assert payload.filename.startswith("stage1_draft_")
    assert payload.asset_id.startswith("rrugc-stage1-draft:")
    assert payload.content_type == "image/webp"
    assert payload.size_bytes == len(data)


def test_preview_uploader_retries_transient_drive_timeout_with_fresh_body(tmp_path, monkeypatch):
    source = tmp_path / "working" / "previews"
    source.mkdir(parents=True)
    Image.new("RGB", (400, 300), (55, 75, 85)).save(source / "draft.png")
    attempts, payloads, delays = [], [], []

    class FlakyDrive(GoogleDriveAssetStorage):
        def __init__(self):
            super().__init__(storage_access_token="test", root_folder_id="final-root")

        async def store_asset(self, payload):
            attempts.append(payload.asset_id)
            payloads.append(b"".join([part async for part in payload.body]))
            if len(attempts) < 3:
                raise StorageProviderError("temporary Google Drive read timeout", retryable=True, code="managed_storage_network_error")
            return SimpleNamespace(remote_file_id="temporary-file")

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.modules.realistic_review_ugc.stage1_temp_previews.asyncio.sleep", fake_sleep)
    count = asyncio.run(upload_temp_previews(
        FlakyDrive(), workspace=tmp_path, tenant_id="t1", job_id="j", processing_job_id="p",
    ))
    assert count == 1
    assert len(attempts) == 3
    assert len(set(attempts)) == 1
    assert payloads[0] == payloads[1] == payloads[2]
    assert delays == [1, 3]


def test_preview_uploader_does_not_retry_nonretryable_drive_error(tmp_path, monkeypatch):
    source = tmp_path / "working" / "previews"
    source.mkdir(parents=True)
    Image.new("RGB", (200, 200)).save(source / "draft.png")
    calls, delays = [], []

    class RejectedDrive(GoogleDriveAssetStorage):
        def __init__(self):
            super().__init__(storage_access_token="test", root_folder_id="final-root")

        async def store_asset(self, payload):
            calls.append(payload.asset_id)
            raise StorageProviderError("credential rejected", retryable=False, code="managed_storage_permission_denied")

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.modules.realistic_review_ugc.stage1_temp_previews.asyncio.sleep", fake_sleep)
    count = asyncio.run(upload_temp_previews(
        RejectedDrive(), workspace=tmp_path, tenant_id="t1", job_id="j", processing_job_id="p",
    ))
    assert count == 0
    assert len(calls) == 1
    assert delays == []


def _record(name, *, now, hours_old, owned=True, folder=TEMP_FOLDER_ID):
    return {
        "id": name,
        "name": f"stage1_draft_{name}.webp",
        "parents": [folder],
        "mimeType": "image/webp",
        "trashed": False,
        "createdTime": (now - timedelta(hours=hours_old)).isoformat(),
        "appProperties": (
            {"cam_tenant_id": "t1", "cam_asset_id":
             "rrugc-stage1-draft:11111111-1111-1111-1111-111111111111:"
             "22222222-2222-2222-2222-222222222222:0"}
            if owned else {"cam_tenant_id": "t1", "cam_asset_id": "rrugc-keyword-image:final"}
        ),
    }


def test_ttl_boundaries_ownership_and_folder():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    assert eligible(_record("old", now=now, hours_old=49), now=now)
    assert eligible(_record("boundary", now=now, hours_old=48), now=now)
    assert not eligible(_record("fresh", now=now, hours_old=47), now=now)
    assert not eligible(_record("unrelated", now=now, hours_old=80, owned=False), now=now)
    assert not eligible(_record("outside", now=now, hours_old=80, folder="final-root"), now=now)


def test_retention_revalidates_and_never_deletes_unrelated_files():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    old = _record("old", now=now, hours_old=49)
    new = _record("new", now=now, hours_old=5)
    unrelated = _record("user", now=now, hours_old=90, owned=False)
    moved = _record("moved", now=now, hours_old=60)
    deleted = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/" + TEMP_FOLDER_ID):
            return httpx.Response(200, json={"id": TEMP_FOLDER_ID, "mimeType": "application/vnd.google-apps.folder", "trashed": False})
        if request.method == "GET" and request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [old, new, unrelated, moved]})
        if request.method == "GET":
            name = request.url.path.rsplit("/", 1)[-1]
            record = next(row for row in (old, new, unrelated, moved) if row["id"] == name).copy()
            if name == "moved":
                record["parents"] = ["different-folder"]
            return httpx.Response(200, json=record)
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        raise AssertionError("unexpected request")

    storage = GoogleDriveAssetStorage(
        storage_access_token="test",
        root_folder_id="final-root",
        transport=httpx.MockTransport(handler),
    )
    dry = asyncio.run(purge(storage, apply=False, now=now))
    assert dry["eligible"] == 2 and dry["deleted"] == 0
    run = asyncio.run(purge(storage, apply=True, now=now))
    assert run["eligible"] == 2 and run["deleted"] == 1 and run["errors"] == 0
    assert deleted == ["old"]
