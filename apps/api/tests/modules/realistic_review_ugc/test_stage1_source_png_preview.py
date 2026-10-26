"""Stage 1 previews must use original Drive PNG data without black-matte repair.

The tests mock storage and use no production database, auth, or Drive traffic.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from fastapi.responses import Response, StreamingResponse
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc import router as module


def source_png():
    im = Image.new("RGBA", (2048, 1024), (0, 0, 0, 0))
    draw = ImageDraw.Draw(im)
    draw.rectangle((320, 180, 1730, 850), fill=(38, 82, 177, 255))
    draw.rectangle((540, 400, 690, 560), fill=(0, 0, 0, 255))
    f = BytesIO()
    im.save(f, format="PNG")
    return f.getvalue()


class Session:
    def __init__(self, skill="gatorhats-typography-concepts"):
        self.job = SimpleNamespace(id="stage1-job", skill_name=skill)
        self.closed = False

    def scalar(self, _statement):
        assert not self.closed
        return self.job

    def close(self):
        self.closed = True


class Storage:
    def __init__(self, original):
        self.original = original
        self.opened = 0
        self.closed = 0

    async def open_asset(self, _args):
        self.opened += 1
        async def content():
            yield self.original[:200]
            yield self.original[200:]
        async def close():
            self.closed += 1
        return SimpleNamespace(body=content(), close=close, content_type="image/png")


def run_request(monkeypatch, *, thumbnail=False, download=False, stage="stage1",
                storage=None, remote_file_id="source-only-test-unique", artwork=None, source_bytes=None):
    source = source_bytes if source_bytes is not None else source_png()
    storage = storage or Storage(source)
    session = Session()
    monkeypatch.setattr(module, "output_versions", lambda *_a, **_kw: [{
        "version": 7, "remote_file_id": remote_file_id,
        "content_type": "image/png", "size_bytes": len(source),
        "width": 2048, "height": 1024, "output_name": "design_01.png",
    }])
    monkeypatch.setattr(module, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace())

    async def request():
        return await module.get_generation_output_version(
            stage, "stage1-job", 7, thumbnail=thumbnail, download=download,
            size=400, v=None, artwork=artwork, if_none_match=None, session=session,
            principal=SimpleNamespace(active_tenant_id="stage1-source-tenant"),
        )
    return asyncio.run(request()), session, storage, source


def drain_stream(response):
    async def drain():
        blob = b"".join([part async for part in response.body_iterator])
        await response.background()
        return blob
    return asyncio.run(drain())


def test_stage1_thumbnail_is_derived_from_original_and_not_drive_thumbnail(monkeypatch):
    def forbid_drive_thumbnail(*_a, **_kw):
        raise AssertionError("Stage 1 must not request a cropped Drive thumbnail")
    monkeypatch.setattr(module, "_managed_drive_thumbnail_response", forbid_drive_thumbnail)
    response, session, storage, original = run_request(
        monkeypatch, thumbnail=True, remote_file_id="alpha-source-101",
    )
    assert isinstance(response, Response)
    assert not isinstance(response, StreamingResponse)
    assert session.closed and storage.opened == 1 and storage.closed == 1
    assert response.media_type == "image/png"
    assert response.headers["vary"] == "Cookie"
    assert response.body != original
    with Image.open(BytesIO(response.body)) as image:
        assert image.size == (400, 200)
        assert image.mode == "RGBA"
        assert image.getpixel((0, 0))[3] == 0
        assert image.getpixel((100, 65)) == (38, 82, 177, 255)
        assert image.getpixel((115, 94)) == (0, 0, 0, 255)


def test_full_stage1_image_and_download_are_the_identical_original_bytes(monkeypatch):
    for download in (False, True):
        response, session, storage, original = run_request(monkeypatch, download=download)
        assert isinstance(response, StreamingResponse)
        assert session.closed
        assert storage.opened == 1 and storage.closed == 0
        assert drain_stream(response) == original
        assert storage.closed == 1
        assert response.media_type == "image/png"


def test_thumbnail_cache_reuses_original_and_separates_revisions(monkeypatch):
    storage = Storage(source_png())
    for _ in range(2):
        response, _session, _storage, _original = run_request(
            monkeypatch, thumbnail=True, storage=storage,
            remote_file_id="alpha-source-cache-202",
        )
        assert response.media_type == "image/png"
    assert storage.opened == 1 and storage.closed == 1


def test_fabric_stage1_full_and_thumb_remain_rgb_but_cap_preview_is_rgba(monkeypatch):
    from app.modules.realistic_review_ugc.stage1_fabric import fabric_output_bytes
    art = Image.new("RGBA", (480, 240), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((40, 30, 420, 185), fill=(40, 82, 170, 255))
    source = fabric_output_bytes(art)
    remote = "fabric-routing-740"

    original, _, _, _ = run_request(
        monkeypatch, source_bytes=source, remote_file_id=remote,
    )
    assert drain_stream(original) == source

    thumbnail, _, storage, _ = run_request(
        monkeypatch, thumbnail=True, storage=Storage(source),
        source_bytes=source, remote_file_id=remote,
    )
    assert thumbnail.media_type == "image/png"
    with Image.open(BytesIO(thumbnail.body)) as rgb:
        assert rgb.mode == "RGB"
        assert rgb.size == (400, 200)

    preview, _, storage, _ = run_request(
        monkeypatch, thumbnail=True, artwork="stage1-thread-geometry-v1",
        storage=Storage(source), source_bytes=source, remote_file_id=remote,
    )
    assert preview.media_type == "image/png"
    with Image.open(BytesIO(preview.body)) as rgba:
        assert rgba.mode == "RGBA"
        assert rgba.getpixel((0, 0))[3] == 0
        assert rgba.size == (400, 200)


def test_non_stage1_still_uses_previous_thumbnail_pipeline(monkeypatch):
    seen = []
    async def existing_thumbnail(_storage, **kwargs):
        seen.append(kwargs)
        return Response(content=b"other-stage", media_type="image/jpeg")
    monkeypatch.setattr(module, "_managed_drive_thumbnail_response", existing_thumbnail)
    result, _session, _storage, _source = run_request(monkeypatch, thumbnail=True, stage="stage4")
    assert result.body == b"other-stage"
    assert len(seen) == 1
    assert seen[0]["size_pixels"] == 400


def test_invalid_png_falls_back_to_unchanged_original_without_matte_repair(monkeypatch):
    original = b"invalid-PNG-source"
    storage = Storage(original)
    session = Session()
    monkeypatch.setattr(module, "output_versions", lambda *_a, **_kw: [{
        "version": 7, "remote_file_id": "broken-source-303",
        "content_type": "image/png", "size_bytes": len(original),
        "width": 2048, "height": 1024, "output_name": "design_01.png",
    }])
    monkeypatch.setattr(module, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(module, "get_settings", lambda: SimpleNamespace())

    async def request():
        return await module.get_generation_output_version(
            "stage1", "stage1-job", 7, thumbnail=True, size=400,
            download=False, v=None, if_none_match=None, session=session,
            principal=SimpleNamespace(active_tenant_id="stage1-source-tenant"),
        )
    response = asyncio.run(request())
    assert isinstance(response, StreamingResponse)
    assert drain_stream(response) == original
    assert session.closed
    assert storage.closed == 2  # Failed thumb fetch and fallback source stream.
