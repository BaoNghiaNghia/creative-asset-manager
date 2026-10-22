"""Legacy-only Stage 1 preview deletion; uploading previews is disabled."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.domain.providers.contracts import StorageProviderError
from app.modules.realistic_review_ugc import stage1_temp_previews
from app.modules.realistic_review_ugc.stage1_temp_previews import TEMP_FOLDER_ID
from app.operations.stage1_temp_retention import access_token_with_retry, eligible, purge
from app.providers.google.storage import GoogleDriveAssetStorage


def test_preview_module_exposes_no_writer():
    assert not hasattr(stage1_temp_previews, "upload_temp_previews")
    assert not hasattr(stage1_temp_previews, "compact_preview")
    assert not hasattr(stage1_temp_previews, "preview_paths")


def test_temp_retention_retries_transient_token_errors(monkeypatch):
    calls, delays = [], []

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


@pytest.mark.parametrize("retryable,expected_calls", [(False, 1), (True, 3)])
def test_retention_token_errors_never_mutate_drive(monkeypatch, retryable, expected_calls):
    calls, delays = [], []

    class BadToken:
        async def get_access_token(self):
            calls.append(1)
            raise StorageProviderError("Google credentials unavailable", retryable=retryable)

    async def fake_sleep(seconds):
        delays.append(seconds)

    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", fake_sleep)
    with pytest.raises(StorageProviderError):
        asyncio.run(access_token_with_retry(BadToken()))
    assert len(calls) == expected_calls
    assert delays == ([1, 2] if retryable else [])


def test_temp_retention_retries_drive_reads_but_never_retries_writes(monkeypatch):
    attempts, sleeps = [], []
    async def no_wait(delay):
        sleeps.append(delay)
    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", no_wait)

    def handler(request):
        assert request.method == "GET"
        if request.url.path.endswith("/" + TEMP_FOLDER_ID):
            attempts.append(1)
            if len(attempts) < 3:
                raise httpx.ConnectTimeout("transient", request=request)
            return httpx.Response(200, json={
                "id": TEMP_FOLDER_ID,
                "mimeType": "application/vnd.google-apps.folder", "trashed": False,
            })
        return httpx.Response(200, json={"files": []})

    storage = GoogleDriveAssetStorage(
        storage_access_token="test", root_folder_id="final-root",
        transport=httpx.MockTransport(handler),
    )
    result = asyncio.run(purge(storage, apply=True, purge_all_owned=True))
    assert result["deleted"] == result["errors"] == 0
    assert len(attempts) == 3
    assert sleeps == [1, 2]


def test_temp_retention_exhausted_drive_reads_fail_closed(monkeypatch):
    async def no_wait(_):
        pass
    monkeypatch.setattr("app.operations.stage1_temp_retention.asyncio.sleep", no_wait)
    attempts = []
    def handler(request):
        assert request.method == "GET"
        attempts.append(1)
        raise httpx.ConnectTimeout("temporary unavailable", request=request)
    storage = GoogleDriveAssetStorage(
        storage_access_token="test", root_folder_id="final-root",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(httpx.ConnectTimeout):
        asyncio.run(purge(storage, apply=True, purge_all_owned=True))
    assert len(attempts) == 3


def record(name, *, now, age_hours, owned=True, folder=TEMP_FOLDER_ID):
    return {
        "id": name,
        "name": f"stage1_draft_{name}.webp",
        "parents": [folder],
        "mimeType": "image/webp",
        "trashed": False,
        "createdTime": (now - timedelta(hours=age_hours)).isoformat(),
        "appProperties": (
            {"cam_tenant_id": "t1", "cam_asset_id":
             "rrugc-stage1-draft:11111111-1111-1111-1111-111111111111:"
             "22222222-2222-2222-2222-222222222222:0"}
            if owned else {"cam_tenant_id": "t1", "cam_asset_id": "rrugc-keyword-image:final"}
        ),
    }


def test_age_override_still_requires_exact_ownership_and_folder():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    assert eligible(record("old", now=now, age_hours=49), now=now)
    assert eligible(record("boundary", now=now, age_hours=48), now=now)
    assert not eligible(record("new", now=now, age_hours=1), now=now)
    assert eligible(record("new", now=now, age_hours=1), now=now, purge_all_owned=True)
    for row in (
        record("other", now=now, age_hours=90, owned=False),
        record("elsewhere", now=now, age_hours=90, folder="final-root"),
        dict(record("wrongtype", now=now, age_hours=90), mimeType="image/png"),
        dict(record("invalidname", now=now, age_hours=90), name="design_01.png"),
    ):
        assert not eligible(row, now=now, purge_all_owned=True)


def test_immediate_legacy_purge_rechecks_ownership_and_preserves_unrelated():
    now = datetime(2026, 10, 10, 12, tzinfo=timezone.utc)
    old = record("old", now=now, age_hours=49)
    recent = record("recent", now=now, age_hours=1)
    unrelated = record("unrelated", now=now, age_hours=90, owned=False)
    moved = record("moved", now=now, age_hours=60)
    deleted = []

    def handler(request):
        if request.method == "GET" and request.url.path.endswith("/" + TEMP_FOLDER_ID):
            return httpx.Response(200, json={
                "id": TEMP_FOLDER_ID, "mimeType": "application/vnd.google-apps.folder",
                "trashed": False,
            })
        if request.method == "GET" and request.url.path.endswith("/files"):
            return httpx.Response(200, json={"files": [old, recent, unrelated, moved]})
        if request.method == "GET":
            name = request.url.path.rsplit("/", 1)[-1]
            value = next(item for item in (old, recent, unrelated, moved)
                         if item["id"] == name).copy()
            if name == "moved":
                value["parents"] = ["outside-temp"]
            return httpx.Response(200, json=value)
        if request.method == "DELETE":
            deleted.append(request.url.path.rsplit("/", 1)[-1])
            return httpx.Response(204)
        raise AssertionError("unexpected request")

    storage = GoogleDriveAssetStorage(
        storage_access_token="test", root_folder_id="final-root",
        transport=httpx.MockTransport(handler),
    )
    dry = asyncio.run(purge(storage, now=now, purge_all_owned=True))
    assert dry["eligible"] == 3 and dry["deleted"] == 0
    cleanup = asyncio.run(purge(storage, now=now, apply=True, purge_all_owned=True))
    assert cleanup["eligible"] == 3 and cleanup["deleted"] == 2
    assert cleanup["errors"] == 0 and cleanup["skipped"] >= 1
    assert sorted(deleted) == ["old", "recent"]
    deleted.clear()
    legacy = asyncio.run(purge(storage, now=now, apply=True))
    assert legacy["eligible"] == 2 and deleted == ["old"]
