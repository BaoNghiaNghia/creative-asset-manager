"""Drive-only Design asset roundtrip, no VPS file writes."""
import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException

from app.modules.etsy_spy import imagegen_drive as module

TENANT = "creative-assets-prod"
JOB = "8c6729df-1aa1-4a43-8d69-f076e152b489"
ASSET = "a" * 24
FOLDER = "10SHkTJ02Wmztr2yBJoFG2oiNo938djtV"


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
