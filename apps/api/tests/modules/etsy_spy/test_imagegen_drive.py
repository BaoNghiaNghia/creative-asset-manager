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
         patch.object(module,"_thumbnail",return_value=(preview, (4500, 5400))):
        asyncio.run(check())


def test_native_master_is_lossless_and_preview_is_separate(tmp_path):
    """The saved master must be exactly the original PNG, never upscaled."""
    import io
    from PIL import Image

    provider = FakeDrive()
    image = Image.new("RGBA", (512, 512), (0, 0, 0, 0))
    for x in range(150, 350):
        for y in range(150, 350):
            image.putpixel((x, y), (120, 40, 10, 255))
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    png = buf.getvalue()
    digest = hashlib.sha256(png).hexdigest()

    async def check():
        result = await module.store_design_to_drive(
            tenant_id=TENANT, job_id=JOB, asset_id=ASSET,
            digest=digest, png=png, native_master=True,
        )
        assert len(provider.uploads) == 2
        assert result["artwork_kind"] == "native_master_v2"
        assert result["native_width"] == result["native_height"] == 512
        assert result["storage_size_bytes"] == len(png)
        assert provider.data[result["drive_file_id"]] == png
        assert result["drive_file_id"] != result["drive_thumbnail_id"]
        assert not list(tmp_path.iterdir())
        with Image.open(io.BytesIO(provider.data[result["drive_thumbnail_id"]])) as thumb:
            assert max(thumb.size) == 192

    with patch.object(module, "provider_and_folder", return_value=(provider, FOLDER)), \\
         patch.object(module, "verify_private_drive_destination", new_callable=AsyncMock):
        asyncio.run(check())


def test_native_master_rejects_opaque_or_oversized_inputs():
    import io
    from PIL import Image

    provider = FakeDrive()
    async def reject(png):
        with pytest.raises(HTTPException) as error:
            await module.store_design_to_drive(
                tenant_id=TENANT, job_id=JOB, asset_id=ASSET,
                digest=hashlib.sha256(png).hexdigest(),
                png=png, native_master=True,
            )
        assert error.value.status_code == 422

    opaque = io.BytesIO()
    Image.new("RGBA", (512, 512), (12, 23, 34, 255)).save(opaque, "PNG")
    huge_dimensions = io.BytesIO()
    Image.new("RGBA", (4200, 512), (0, 0, 0, 0)).save(huge_dimensions, "PNG")
    with patch.object(module, "provider_and_folder", return_value=(provider, FOLDER)):
        asyncio.run(reject(opaque.getvalue()))
        asyncio.run(reject(huge_dimensions.getvalue()))
    assert not provider.uploads


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





def test_imagegen_oauth_can_secure_only_public_folder_grants(monkeypatch):
    """Owner-authorized connect removes anyone writer; named users remain."""
    monkeypatch.setenv("CAM_ETSY_IMAGEGEN_DRIVE_OWNER_EMAIL", "baonghia.kht@gmail.com")
    private = [
        {"id": "owner123", "type": "user", "role": "owner"},
        {"id": "named456", "type": "user", "role": "writer"},
    ]
    anyone = {"id": "anyone789", "type": "anyone", "role": "writer"}

    class ResponseClient:
        def __init__(self, *, delete_status=204):
            self.deleted = []
            self.delete_status = delete_status
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def get(self, url, **kwargs):
            if url.endswith("/about"):
                data = {"user": {"emailAddress": "baonghia.kht@gmail.com"}}
            else:
                data = {
                    "id": FOLDER, "mimeType": "application/vnd.google-apps.folder",
                    "owners": [{"emailAddress": "baonghia.kht@gmail.com"}],
                    "capabilities": {"canAddChildren": True},
                    "permissions": private if self.deleted else private + [anyone],
                }
            return SimpleNamespace(status_code=200, json=lambda: data)
        async def delete(self, url, **kwargs):
            if self.delete_status in {200, 204}:
                self.deleted.append(url)
            return SimpleNamespace(status_code=self.delete_status)

    async def check():
        normal = ResponseClient()
        with patch.object(module.httpx, "AsyncClient", return_value=normal):
            with pytest.raises(HTTPException, match="Restricted"):
                await module.verify_imagegen_oauth_token("token", FOLDER)
            assert await module.verify_imagegen_oauth_token(
                "token", FOLDER, allow_public_sharing=True,
            ) is False
        assert normal.deleted == []

        secured = ResponseClient()
        with patch.object(module.httpx, "AsyncClient", return_value=secured):
            await module.verify_imagegen_oauth_token(
                "token", FOLDER, restrict_public_sharing=True,
            )
        assert len(secured.deleted) == 1
        assert secured.deleted[0].endswith("/permissions/anyone789")
        assert "named456" not in secured.deleted[0]

        blocked = ResponseClient(delete_status=403)
        with patch.object(module.httpx, "AsyncClient", return_value=blocked):
            with pytest.raises(HTTPException, match="Could not set"):
                await module.verify_imagegen_oauth_token(
                    "token", FOLDER, restrict_public_sharing=True,
                )
            # Google permission change may fail but this owner OAuth is valid;
            # callback may save it, and upload still requires Restricted.
            assert await module.verify_imagegen_oauth_token(
                "token", FOLDER, restrict_public_sharing=True,
                allow_public_sharing=True,
            ) is False
    asyncio.run(check())


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
