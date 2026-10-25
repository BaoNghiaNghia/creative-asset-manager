"""Regression guards for Stage 5 public photo and API pool starvation.

These tests use asyncio.run() to work without pytest-asyncio. They hold the
mocked remote I/O open and prove the SQLAlchemy session is already released.
They never access production DB or contact Google Drive.
"""
from __future__ import annotations

import asyncio
import inspect
from threading import get_ident
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse

from app.main import create_app
from app.core.config import Settings
from app.modules.realistic_review_ugc import router as stage_router

IMAGE_ID = str(uuid4())


class CheckedSession:
    def __init__(self, row):
        self.row = row
        self.closed = False
        self.executions = 0
        self.execute_thread = None

    def execute(self, statement):
        assert not self.closed, "authorization must use an open SQL session"
        self.executions += 1
        self.execute_thread = get_ident()
        return SimpleNamespace(first=lambda: self.row)

    def close(self):
        self.closed = True


class DelayedStorage:
    def __init__(self, session: CheckedSession):
        self.session = session
        self.opened = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False
        self.open_arguments = None

    async def open_asset(self, args):
        assert self.session.closed, "SQL session held during remote Managed Storage I/O"
        self.open_arguments = args
        self.opened.set()
        await self.release.wait()

        async def body():
            assert self.session.closed, "SQL session held while streaming"
            yield b"sample-original-image"

        async def close():
            self.closed = True

        return SimpleNamespace(body=body(), close=close, content_type="image/png")


def photo_request(method: str) -> Request:
    return Request({
        "type": "http",
        "method": method,
        "scheme": "http",
        "path": f"/stage5/public-photo/{IMAGE_ID}.png",
        "query_string": b"",
        "headers": [],
    })


def ready_photo():
    return (
        SimpleNamespace(
            id=IMAGE_ID, tenant_id="test-tenant", output_remote_file_id="source-file",
            output_content_type="image/png", output_size_bytes=999,
        ),
        SimpleNamespace(status="ready"),
    )


def test_stage5_public_photo_releases_pool_connection_during_slow_remote_io(monkeypatch):
    session = CheckedSession(ready_photo())
    storage = DelayedStorage(session)
    monkeypatch.setattr(stage_router, "build_managed_storage_provider", lambda _settings: storage)

    async def check():
        coroutine = asyncio.create_task(stage_router.public_stage5_original_photo(
            photo_request("GET"), IMAGE_ID, "png", session=session))
        await asyncio.wait_for(storage.opened.wait(), timeout=3)
        assert session.execute_thread != get_ident(), "DB checkout must not block the event loop"
        assert session.closed, "database must be released before remote open completes"
        assert session.executions == 1
        assert not coroutine.done(), "storage still blocked while connection is free"
        storage.release.set()
        response = await asyncio.wait_for(coroutine, timeout=3)
        assert isinstance(response, StreamingResponse)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "public, max-age=300"
        assert response.headers["x-content-type-options"] == "nosniff"
        assert storage.open_arguments.remote_file_id == "source-file"
        assert storage.open_arguments.tenant_id == "test-tenant"
        assert storage.open_arguments.asset_id == f"rrugc-stage2:{IMAGE_ID}"
        data = b"".join([chunk async for chunk in response.body_iterator])
        assert data == b"sample-original-image"
        assert session.closed
        await response.background()
        assert storage.closed

    asyncio.run(check())


def test_stage5_public_photo_head_returns_without_opening_managed_storage(monkeypatch):
    session = CheckedSession(ready_photo())

    def no_storage(_settings):
        raise AssertionError("HEAD must not open Managed Storage")

    monkeypatch.setattr(stage_router, "build_managed_storage_provider", no_storage)

    async def check():
        response = await stage_router.public_stage5_original_photo(
            photo_request("HEAD"), IMAGE_ID, "png", session=session)
        assert response.status_code == 200
        assert session.closed
        assert session.executions == 1

    asyncio.run(check())


def test_stage5_public_photo_checks_ready_review_before_releasing_media(monkeypatch):
    session = CheckedSession(None)

    def no_storage(_settings):
        raise AssertionError("unauthorized/nonready image may not open Managed Storage")

    monkeypatch.setattr(stage_router, "build_managed_storage_provider", no_storage)

    async def check():
        with pytest.raises(HTTPException) as err:
            await stage_router.public_stage5_original_photo(
                photo_request("GET"), IMAGE_ID, "png", session=session)
        assert err.value.status_code == 404
        assert session.executions == 1
        assert session.closed

    asyncio.run(check())


def test_api_liveness_handlers_do_not_depend_on_the_busy_sync_threadpool():
    app = create_app(Settings())
    for path in ("/live", "/health", "/version"):
        matched = [route for route in app.routes if getattr(route, "path", None) == path]
        assert len(matched) == 1
        assert inspect.iscoroutinefunction(matched[0].endpoint), path
    # Readiness still performs a real database dependency check.
    ready = [route for route in app.routes if getattr(route, "path", None) == "/ready"]
    assert len(ready) == 1
