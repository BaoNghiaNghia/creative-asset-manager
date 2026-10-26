"""Products Stage 2 previews must preserve selected Stage 1 PNG transparency.

Tests are isolated: no production database, Google Drive or credentials.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from fastapi import Response
from PIL import Image, ImageDraw

from app.modules.realistic_review_ugc import router


def master_png() -> bytes:
    image = Image.new("RGBA", (1024, 512), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rectangle((125, 100, 899, 419), fill=(42, 78, 168, 255))
    draw.rectangle((400, 200, 510, 305), fill=(0, 0, 0, 255))
    stream = BytesIO()
    image.save(stream, "PNG")
    return stream.getvalue()


class Session:
    def __init__(self, mime="image/png", revision="alpha-safe-plan-101", size=0):
        self.row = SimpleNamespace(
            id="selected-design-plan", source_file_id=revision,
            source_mime_type=mime, source_size_bytes=size,
            source_revision=revision,
        )
        self.closed = False

    def scalar(self, _query):
        return self.row

    def close(self):
        self.closed = True


class Storage:
    def __init__(self, data: bytes, session: Session):
        self.data = data
        self.session = session
        self.opens = 0
        self.closes = 0

    async def open_asset(self, asset):
        assert self.session.closed, "DB connection must close before Drive I/O"
        assert asset.asset_id == "selected-design-plan"
        self.opens += 1

        async def body():
            yield self.data

        async def close():
            self.closes += 1

        return SimpleNamespace(body=body(), close=close, content_type="image/png")


def request(session: Session, etag: str | None = None):
    return SimpleNamespace(
        headers={"if-none-match": etag} if etag else {},
        app=SimpleNamespace(state=SimpleNamespace(google_drive_stream_client=None)),
    )


def call(session: Session, *, etag=None, artwork="stage1-source-alpha-v2", size=384):
    return asyncio.run(router.get_source_plan_image(
        source_plan_id="selected-design-plan", request=request(session, etag),
        thumbnail=True, size=size, artwork=artwork, session=session,
        principal=SimpleNamespace(active_tenant_id="embroidery-preview-tenant"),
    ))


def test_selected_design_png_preview_preserves_real_alpha_and_does_not_use_drive_jpeg(monkeypatch):
    image = master_png()
    session = Session(size=len(image))
    storage = Storage(image, session)
    monkeypatch.setattr(router, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(router, "get_settings", lambda: SimpleNamespace())

    async def fail_drive_preview(*_args, **_kwargs):
        raise AssertionError("Drive preview would turn transparent PNG background black")

    monkeypatch.setattr(router, "_managed_drive_thumbnail_response", fail_drive_preview)
    response = call(session)
    assert isinstance(response, Response)
    assert response.status_code == 200 and response.media_type == "image/png"
    assert session.closed and storage.opens == 1 and storage.closes == 1
    assert response.headers["etag"] == '"alpha-safe-plan-101:stage1-source-alpha-v2"'
    assert response.headers["vary"] == "Cookie"
    assert response.headers["cache-control"] == "private, max-age=31536000, immutable"
    with Image.open(BytesIO(response.body)) as preview:
        assert preview.mode == "RGBA" and preview.size == (384, 192)
        assert preview.getpixel((0, 0))[3] == 0
        assert preview.getpixel((100, 80))[3] == 255
        assert preview.getpixel((170, 90)) == (0, 0, 0, 255)


def test_selected_design_alpha_thumbnail_cache_and_conditional_etag(monkeypatch):
    image = master_png()
    sessions = [Session(revision="alpha-safe-cache-102", size=len(image)) for _ in range(3)]
    storage = Storage(image, sessions[0])
    monkeypatch.setattr(router, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(router, "get_settings", lambda: SimpleNamespace())
    first = call(sessions[0])
    storage.session = sessions[1]
    second = call(sessions[1])
    assert first.body == second.body
    assert storage.opens == 1  # Shared in-memory single-flight thumbnail cache.
    third = call(sessions[2], etag=first.headers["etag"])
    assert third.status_code == 304 and third.headers["etag"] == first.headers["etag"]
    assert storage.opens == 1
    assert all(session.closed for session in sessions)


def test_other_sources_keep_original_drive_thumbnail_behavior(monkeypatch):
    session = Session(mime="image/jpeg", revision="alpha-safe-jpeg-103", size=1024)
    storage = Storage(b"opaque", session)
    monkeypatch.setattr(router, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(router, "get_settings", lambda: SimpleNamespace())
    calls = []

    async def drive_preview(_storage, **kwargs):
        calls.append(kwargs)
        return Response(content=b"jpeg-preview", media_type="image/jpeg")

    monkeypatch.setattr(router, "_managed_drive_thumbnail_response", drive_preview)
    response = call(session)
    assert response.body == b"jpeg-preview"
    assert response.headers.get("etag") is None
    assert calls[0]["etag"] == '"alpha-safe-jpeg-103"'
    assert storage.opens == 0
    assert session.closed


def test_legacy_source_thumbnail_stays_unchanged_without_explicit_artwork_flag(monkeypatch):
    session = Session(size=len(master_png()), revision="alpha-safe-legacy-104")
    storage = Storage(master_png(), session)
    monkeypatch.setattr(router, "build_managed_storage_provider", lambda _settings: storage)
    monkeypatch.setattr(router, "get_settings", lambda: SimpleNamespace())

    async def old_preview(_storage, **kwargs):
        return Response(content=b"drive-preview", media_type="image/jpeg")

    monkeypatch.setattr(router, "_managed_drive_thumbnail_response", old_preview)
    response = call(session, artwork=None)
    assert response.body == b"drive-preview"
    assert storage.opens == 0
