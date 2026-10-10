"""Stage 1 low-resolution Drive drafts and 48-hour cleanup safeguards."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import httpx
from PIL import Image

from app.modules.realistic_review_ugc.stage1_temp_previews import (
    TEMP_FOLDER_ID, compact_preview, preview_paths, upload_temp_previews,
)
from app.operations.stage1_temp_retention import eligible, purge
from app.providers.google.storage import GoogleDriveAssetStorage


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
