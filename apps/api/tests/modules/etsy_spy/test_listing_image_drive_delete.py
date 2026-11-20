"""Drive deletion may target ONLY the verified PNG + its thumbnail."""
import asyncio
import hashlib

import httpx
import pytest
from fastapi import HTTPException

from app.modules.etsy_spy import imagegen_drive as drive
from app.modules.etsy_spy.listing_drive_folders import ROOT_FOLDER_ID


@pytest.fixture
def remote(monkeypatch):
    class Provider:
        async def get_access_token(self):
            return "test-access-token"

    monkeypatch.setattr(drive, "provider_and_folder",
                        lambda: (Provider(), ROOT_FOLDER_ID))

    async def verify_destination(provider, folder):
        assert folder == ROOT_FOLDER_ID
    monkeypatch.setattr(drive, "verify_private_drive_destination",
                        verify_destination)

    pack = "sp-" + "a" * 40
    original_id, thumbnail_id = "storedOriginal12345", "storedPreview123456"
    original_parent, thumbnail_parent = "garmentFolderD12345", "thumbnailFolderD12345"
    original_sha, thumbnail_sha = "f" * 64, "e" * 64
    present = {original_id, thumbnail_id}
    requests = []
    failure = {"thumbnail_once": False, "wrong_parent": False}
    metadata = {
        original_id: {
            "id": original_id, "parents": [original_parent],
            "trashed": False,
            "appProperties": {
                "cam_asset_id": f"{pack}:listing-01:original",
                "cam_tenant_id": "boostgame", "cam_content_hash": original_sha,
            },
        },
        thumbnail_id: {
            "id": thumbnail_id, "parents": [thumbnail_parent],
            "trashed": False,
            "appProperties": {
                "cam_asset_id": f"{pack}:listing-01:thumbnail",
                "cam_tenant_id": "boostgame", "cam_content_hash": thumbnail_sha,
            },
        },
    }

    def handler(request):
        key = str(request.url).rsplit("/", 1)[-1].split("?", 1)[0]
        assert key in metadata
        requests.append((request.method, key))
        if request.method == "GET":
            if key not in present:
                return httpx.Response(404)
            data = dict(metadata[key])
            if failure["wrong_parent"] and key == thumbnail_id:
                data["parents"] = ["foreignFolder12345"]
            return httpx.Response(200, json=data)
        if request.method == "DELETE":
            if key not in present:
                return httpx.Response(404)
            if failure["thumbnail_once"] and key == thumbnail_id:
                failure["thumbnail_once"] = False
                return httpx.Response(503)
            present.remove(key)
            return httpx.Response(204)
        raise AssertionError("Unexpected method")

    transport = httpx.MockTransport(handler)
    original_client = httpx.AsyncClient
    monkeypatch.setattr(drive.httpx, "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=transport, **kwargs,
        ))
    details = dict(
        tenant_id="boostgame", pack_id=pack, slot=1,
        drive_file_id=original_id, drive_thumbnail_id=thumbnail_id,
        drive_folder_id=original_parent,
        drive_thumbnail_folder_id=thumbnail_parent,
        output_sha256=original_sha, thumbnail_sha256=thumbnail_sha,
    )
    return details, present, requests, failure


def test_drive_delete_verifies_both_exact_files_before_any_delete(remote):
    details, present, calls, failure = remote
    failure["wrong_parent"] = True
    with pytest.raises(HTTPException) as exc:
        asyncio.run(drive.delete_listing_output_files_from_drive(**details))
    assert exc.value.status_code == 409
    assert present == {details["drive_file_id"], details["drive_thumbnail_id"]}
    assert not any(method == "DELETE" for method, key in calls)
    failure["wrong_parent"] = False
    response = asyncio.run(drive.delete_listing_output_files_from_drive(**details))
    assert response == {"original_deleted": True, "thumbnail_deleted": True}
    assert present == set()
    assert [k for method, k in calls if method == "DELETE"] == [
        details["drive_file_id"], details["drive_thumbnail_id"]]


def test_drive_delete_partial_failure_recovers_without_touching_unrelated_files(remote):
    details, present, calls, failure = remote
    failure["thumbnail_once"] = True
    with pytest.raises(HTTPException) as exc:
        asyncio.run(drive.delete_listing_output_files_from_drive(**details))
    assert exc.value.status_code == 503
    assert present == {details["drive_thumbnail_id"]}
    # The original is already gone. The DB row still holds both verified IDs.
    result = asyncio.run(drive.delete_listing_output_files_from_drive(**details))
    assert result["thumbnail_deleted"] is True
    assert present == set()
    assert sum(method == "DELETE" and key == details["drive_file_id"]
               for method, key in calls) == 1


def test_drive_delete_invalid_identity_fails_before_oauth(remote):
    details, present, calls, failure = remote
    with pytest.raises(HTTPException) as exc:
        asyncio.run(drive.delete_listing_output_files_from_drive(
            **{**details, "drive_file_id": details["drive_thumbnail_id"]}))
    assert exc.value.status_code == 422
    assert calls == []
    assert len(present) == 2
