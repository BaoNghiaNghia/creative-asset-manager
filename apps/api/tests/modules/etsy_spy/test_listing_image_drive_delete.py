"""Delete only private Drive files with proven tenant/asset/SHA/folder."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from app.modules.etsy_spy import imagegen_drive as drive


def test_delete_one_exact_verified_google_drive_file(monkeypatch):
    class Provider:
        async def get_access_token(self):
            return "managed-test-token"

    monkeypatch.setattr(drive, "provider_and_folder",
        lambda: (Provider(), "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"))
    async def verified(_provider, _folder):
        return None
    monkeypatch.setattr(drive, "verify_private_drive_destination", verified)
    pack = "sp-" + "a" * 40
    events = []
    state = {"tamper": False, "missing": False}
    def handler(request: httpx.Request):
        events.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == "Bearer managed-test-token"
        assert "/files/remoteFile12345" in str(request.url)
        if request.method == "GET":
            if state["missing"]:
                return httpx.Response(404, json={"error": "not found"})
            return httpx.Response(200, json={
                "id": "remoteFile12345", "parents": ["garmentFolderA12345"],
                "trashed": False, "mimeType": "image/png",
                "appProperties": {
                    "cam_tenant_id": "boostgame",
                    "cam_asset_id": pack + ":listing-01:original",
                    "cam_content_hash": ("0" * 64 if state["tamper"]
                                         else "d" * 64),
                },
            })
        assert request.method == "DELETE"
        return httpx.Response(204)
    transport = httpx.MockTransport(handler)
    def delete(**more):
        return asyncio.run(drive.delete_listing_drive_object(
            tenant_id="boostgame", pack_id=pack, slot=1,
            kind="original", remote_file_id="remoteFile12345",
            folder_id="garmentFolderA12345", sha256="d" * 64,
            transport=transport, **more))
    delete()
    assert [kind for kind, _ in events] == ["GET", "DELETE"]
    events.clear()
    state["tamper"] = True
    with pytest.raises(HTTPException) as bad:
        delete()
    assert bad.value.status_code == 409
    assert [kind for kind, _ in events] == ["GET"]
    events.clear()
    state["missing"] = True
    delete()  # Remote object already deleted after a previous partial attempt.
    assert [kind for kind, _ in events] == ["GET"]


def test_drive_delete_denies_outside_parent_or_other_asset(monkeypatch):
    class Provider:
        async def get_access_token(self):
            return "token"

    monkeypatch.setattr(drive, "provider_and_folder",
        lambda: (Provider(), "1JdCzemUBcLhfro5IbOq5jPze7rdDIJMl"))
    async def verified(_provider, _folder):
        pass
    monkeypatch.setattr(drive, "verify_private_drive_destination", verified)
    def handler(request):
        if request.method == "DELETE":
            raise AssertionError("Deletion must never touch unproven assets")
        return httpx.Response(200, json={
            "id": "remoteFile12345", "parents": ["someoneElseFolder12345"],
            "mimeType": "image/png", "trashed": False,
            "appProperties": {
                "cam_tenant_id": "wrong-tenant",
                "cam_asset_id": "sp-" + "a" * 40 + ":listing-01:original",
                "cam_content_hash": "d" * 64,
            }})
    with pytest.raises(HTTPException) as bad:
        asyncio.run(drive.delete_listing_drive_object(
            tenant_id="boostgame",pack_id="sp-" + "a" * 40,slot=1,
            kind="original",remote_file_id="remoteFile12345",
            folder_id="garmentFolderA12345",sha256="d" * 64,
            transport=httpx.MockTransport(handler)))
    assert bad.value.status_code == 409
