"""Drive-only Design asset roundtrip, no VPS file writes."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.modules.etsy_spy import imagegen_drive as module

TENANT = "creative-assets-prod"
JOB = "8c6729df-1aa1-4a43-8d69-f076e152b489"
ASSET = "a" * 24
FOLDER = "1BnPuw93Wif2gJ5HEzWpz70GhrrJsdKvi"


class FakeDrive:
    def __init__(self):
        self.uploads = []
        self.data = {}

    async def store_asset(self, request):
        pieces = [chunk async for chunk in request.body]
        payload = b"".join(pieces)
        assert len(payload) == request.size_bytes
        assert hashlib.sha256(payload).hexdigest() == request.content_hash
        assert request.destination_folder_id == FOLDER
        file_id = "drive" + hashlib.sha256(request.asset_id.encode()).hexdigest()[:32]
        self.data[file_id] = payload
        self.uploads.append(request)
        return SimpleNamespace(remote_file_id=file_id)

    async def open_asset(self, request):
        payload = self.data[request.remote_file_id]

        async def stream():
            yield payload[:7]
            yield payload[7:]

        async def close():
            pass

        return SimpleNamespace(body=stream(), close=close)


def test_design_upload_is_drive_only_and_private_readback(tmp_path):
    provider = FakeDrive()
    png = b"PNG" + b"raster data" * 8
    preview = b"PNG small preview"
    digest = hashlib.sha256(png).hexdigest()

    async def check():
        storage = await module.store_design_to_drive(
            tenant_id=TENANT, job_id=JOB, asset_id=ASSET,
            digest=digest, png=png,
        )
        assert storage["storage_provider"] == "google_drive_managed"
        assert len(provider.uploads) == 2
        assert not list(tmp_path.iterdir()), "VPS disk must remain unused"
        assert storage["storage_size_bytes"] == len(png)
        assert await module.read_design_from_drive(
            tenant_id=TENANT, asset_id=JOB+":"+ASSET+":original",
            remote_id=storage["drive_file_id"], sha256=digest,
            max_bytes=1024,
        ) == png
        assert await module.read_design_from_drive(
            tenant_id=TENANT, asset_id=JOB+":"+ASSET+":thumbnail",
            remote_id=storage["drive_thumbnail_id"],
            sha256=storage["thumbnail_sha256"], max_bytes=1024,
        ) == preview

    with patch.object(module,"provider_and_folder",return_value=(provider,FOLDER)), \
         patch.object(module,"verify_private_drive_destination",new_callable=AsyncMock), \
         patch.object(module,"_thumbnail",return_value=preview):
        asyncio.run(check())


def test_invalid_sha_never_uploads():
    provider = FakeDrive()

    async def check():
        with pytest.raises(HTTPException) as invalid:
            await module.store_design_to_drive(
                tenant_id=TENANT, job_id=JOB, asset_id=ASSET,
                digest="0"*64, png=b"Z"*80,
            )
        assert invalid.value.status_code == 422
    with patch.object(module,"provider_and_folder",return_value=(provider,FOLDER)):
        asyncio.run(check())
    assert not provider.uploads


def test_unconnected_drive_is_explicitly_blocked(monkeypatch):
    monkeypatch.delenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID",raising=False)
    with pytest.raises(HTTPException) as unavailable:
        module.provider_and_folder()
    assert unavailable.value.status_code == 503


def test_expected_owner_rejects_other_managed_oauth_account(monkeypatch):
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_FOLDER_ID", FOLDER)
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", "baonghia.kht@gmail.com")
    class Settings:
        def model_copy(self, *, update):
            assert update["GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID"] == FOLDER
            return SimpleNamespace(**update)
    with patch.object(module, "get_settings", return_value=Settings()), \
         patch.object(module, "managed_storage_oauth_status",
                      return_value={"connected": False, "reconnect_required": True}):
        with pytest.raises(HTTPException) as error:
            module.provider_and_folder()
    assert error.value.status_code == 503
    assert "owner account" in str(error.value.detail)


def test_publicly_writable_folder_is_rejected_before_any_upload(monkeypatch):
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", "baonghia.kht@gmail.com")

    class ResponseClient:
        def __init__(self, payload, account="baonghia.kht@gmail.com"):
            self.payload = payload
            self.account = account
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, **kwargs):
            if url.endswith("/about"):
                return SimpleNamespace(status_code=200,
                                       json=lambda: {"user": {"emailAddress": self.account}})
            return SimpleNamespace(status_code=200, json=lambda: self.payload)

    private_metadata = {
        "id": FOLDER, "mimeType": "application/vnd.google-apps.folder",
        "capabilities": {"canAddChildren": True},
        "owners": [{"emailAddress": "baonghia.kht@gmail.com"}],
        "permissions": [{"type": "user", "role": "owner"}],
    }
    public_metadata = {**private_metadata, "permissions": [
        *private_metadata["permissions"], {"type": "anyone", "role": "writer"},
    ]}
    provider = SimpleNamespace(get_access_token=AsyncMock(return_value="test-token"))

    async def verify():
        with patch.object(module.httpx, "AsyncClient",
                          return_value=ResponseClient(public_metadata)):
            with pytest.raises(HTTPException) as error:
                await module.verify_private_drive_destination(provider, FOLDER)
            assert error.value.status_code == 503
            assert "Restricted" in error.value.detail

        with patch.object(module.httpx, "AsyncClient",
                          return_value=ResponseClient(private_metadata)):
            await module.verify_private_drive_destination(provider, FOLDER)

        with patch.object(module.httpx, "AsyncClient",
                          return_value=ResponseClient(private_metadata,
                                                      account="other-google-account@example.com")):
            with pytest.raises(HTTPException) as wrong:
                await module.verify_private_drive_destination(provider, FOLDER)
            assert wrong.value.status_code == 503
            assert "another Google account" in wrong.value.detail

    asyncio.run(verify())
