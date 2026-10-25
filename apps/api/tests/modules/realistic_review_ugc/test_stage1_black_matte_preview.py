"""Old Stage 1 previews are repaired without mutating immutable Drive masters."""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from fastapi.responses import Response, StreamingResponse
from PIL import Image

from app.modules.realistic_review_ugc import router as module


def black_typography_image():
    image = Image.new("RGB", (120, 80), "black")
    for x in range(18, 103):
        for y in range(21, 59):
            image.putpixel((x, y), (35, 75, 175))
    data = BytesIO()
    image.save(data, "PNG")
    return data.getvalue()


class Session:
    def __init__(self, skill="gatorhats-typography-concepts"):
        self.job = SimpleNamespace(id="job-a", skill_name=skill)
        self.closed = False

    def scalar(self, query):
        return self.job

    def close(self):
        self.closed = True


class Storage:
    def __init__(self, original):
        self.original = original
        self.closed = 0
        self.opened = 0

    async def open_asset(self, _arg):
        self.opened += 1

        async def chunks():
            yield self.original[:128]
            yield self.original[128:]

        async def close():
            self.closed += 1

        return SimpleNamespace(
            body=chunks(),
            content_type="image/png",
            close=close,
        )


def get_response(monkeypatch, *, stage="stage1", skill="gatorhats-typography-concepts",
                 thumbnail=False, download=False, storage=None):
    blob = black_typography_image()
    storage = storage or Storage(blob)
    session = Session(skill)
    monkeypatch.setattr(module, "output_versions", lambda *_args, **_kwargs: [
        {"version": 1, "remote_file_id": "remote-id-a", "content_type": "image/png",
         "size_bytes": len(blob), "width": 120, "height": 80, "output_name": "design_01.png"},
    ])
    monkeypatch.setattr(module, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace())

    async def run():
        return await module.get_generation_output_version(
            stage, "job-a", 1,
            thumbnail=thumbnail, download=download, size=256, v=None,
            if_none_match=None, session=session,
            principal=SimpleNamespace(active_tenant_id="tenant-a"),
        )

    return asyncio.run(run()), session, storage, blob


def test_original_download_is_not_overwritten(monkeypatch):
    response, session, storage, original = get_response(monkeypatch, download=True)
    assert isinstance(response, StreamingResponse)
    assert session.closed
    assert storage.closed == 0

    async def drain():
        data = b"".join([chunk async for chunk in response.body_iterator])
        await response.background()
        return data

    assert asyncio.run(drain()) == original
    assert storage.closed == 1


def test_old_full_stage1_preview_repairs_black_and_releases_stream(monkeypatch):
    response, session, storage, original = get_response(monkeypatch)
    assert isinstance(response, Response)
    assert not isinstance(response, StreamingResponse)
    assert session.closed
    assert storage.closed == 1
    assert response.body != original
    assert response.media_type == "image/png"
    assert response.headers["cache-control"] == "private, max-age=300"
    assert response.headers["vary"] == "Cookie"
    with Image.open(BytesIO(response.body)) as img:
        assert img.getpixel((0, 0))[3] == 0
        assert img.getpixel((25, 30)) == (35, 75, 175, 255)


def test_other_stage1_skills_keep_original_pixel_bytes(monkeypatch):
    response, _session, storage, original = get_response(
        monkeypatch, skill="redesign-8869-v3")
    assert isinstance(response, StreamingResponse)
    assert storage.closed == 0

    async def drain():
        data = b"".join([chunk async for chunk in response.body_iterator])
        await response.background()
        return data

    assert asyncio.run(drain()) == original
    assert storage.closed == 1


def test_stage1_thumbnail_marks_matte_repair_but_other_skill_does_not(monkeypatch):
    calls = []

    async def thumbnail(_storage, **kwargs):
        calls.append(kwargs)
        return Response(content=b"thumb", media_type="image/png")

    monkeypatch.setattr(module, "_managed_drive_thumbnail_response", thumbnail)
    first, _, _, _ = get_response(monkeypatch, thumbnail=True)
    assert first.body == b"thumb"
    assert calls[-1]["repair_stage1_black_matte"] is True
    second, _, _, _ = get_response(
        monkeypatch, skill="hanh-redesign-8869-ver-4", thumbnail=True)
    assert second.body == b"thumb"
    assert calls[-1]["repair_stage1_black_matte"] is False


def test_cached_drive_thumbnail_returns_repaired_png(monkeypatch):
    blob = black_typography_image()
    tenant = "tenant-stage1-matte-unique"
    module.thumbnail_cache.invalidate_where(lambda key: key[0] == tenant)

    class DriveStorage(module.GoogleDriveAssetStorage):
        def __init__(self):
            pass

        async def get_access_token(self):
            return "mock-only"

    class Upstream:
        headers = {"content-type": "image/png"}

        async def aiter_raw(self):
            yield blob

    async def fake_open(*_args, **_kwargs):
        return object(), Upstream()

    async def fake_close(*_args, **_kwargs):
        return None

    monkeypatch.setattr(module, "open_thumbnail_stream", fake_open)
    monkeypatch.setattr(module, "close_thumbnail_stream", fake_close)

    async def run():
        return await module._managed_drive_thumbnail_response(
            DriveStorage(), tenant_id=tenant, remote_file_id="remote-a",
            size_pixels=256, cache_control="private, max-age=300",
            cache_version="revision-a", repair_stage1_black_matte=True,
        )

    result = asyncio.run(run())
    assert result is not None
    assert result.media_type == "image/png"
    with Image.open(BytesIO(result.body)) as img:
        assert img.getpixel((0, 0))[3] == 0
        assert img.getpixel((30, 35)) == (35, 75, 175, 255)
