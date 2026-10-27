"""Regression: Products 'Download all outputs' returns an authenticated ZIP, not 500."""

import asyncio
from datetime import datetime, timezone
from io import BytesIO
from types import SimpleNamespace
from zipfile import ZipFile

import pytest
from fastapi import HTTPException

from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.realistic_review_ugc import router
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.ecommerce_input_contract import SHOTS


def test_product_skill_outputs_archive_downloads_original_zip(monkeypatch):
    source_plan_id = "60919f90-6818-4267-8829-b7e3bc5c9ee6"
    tenant_id = "test-tenant"
    data = b"real-original-image-bytes"
    job = SimpleNamespace(
        id="ecommerce-job-1",
        stage=SHOTS[0].stage,
        processing_job_id="process-1",
        output_remote_file_id="original-1",
        output_content_type="image/png",
        output_size_bytes=len(data),
        completed_at=datetime(2026, 10, 27, 2, 0, tzinfo=timezone.utc),
        created_at=datetime(2026, 10, 27, 1, 0, tzinfo=timezone.utc),
    )
    plan = SimpleNamespace(
        status="ready", source_revision="revision-1",
        source_file_id="source-1",
    )

    class FakeSession:
        def __init__(self):
            self.queries = []
            self.closed = False
            self.rows = iter(([job], [], []))

        def scalar(self, statement):
            self.queries.append(str(statement.compile(compile_kwargs={"literal_binds": True})))
            return plan

        def scalars(self, statement):
            self.queries.append(str(statement))
            return iter(next(self.rows))

        def close(self):
            self.closed = True

    session = FakeSession()
    reads = []
    closes = []

    class Storage:
        async def open_asset(self, request):
            reads.append(request)
            async def body():
                yield data
            async def close():
                closes.append(True)
            return StoredAssetReadStream(body=body(), close=close, content_type="image/png")

    monkeypatch.setattr(router, "build_managed_storage_provider", lambda settings: Storage())

    async def run():
        response = await router.download_product_skill_outputs_archive(
            source_plan_id=source_plan_id,
            session=session,
            principal=SimpleNamespace(active_tenant_id=tenant_id),
        )
        chunks = [chunk async for chunk in response.body_iterator]
        if response.background:
            await response.background()
        return response, b"".join(chunks)

    response, payload = asyncio.run(run())
    assert response.status_code == 200
    assert response.media_type == "application/zip"
    assert response.headers["cache-control"] == "private, no-store"
    assert "attachment;" in response.headers["content-disposition"]
    assert session.closed
    assert len(session.queries) == 4
    assert PRODUCTS_STAGE1_SELECTED_ROOT in session.queries[0]
    assert tenant_id in session.queries[0]
    assert source_plan_id in session.queries[0]
    assert len(reads) == 1
    assert reads[0].tenant_id == tenant_id
    assert reads[0].remote_file_id == "original-1"
    assert closes == [True]
    with ZipFile(BytesIO(payload), "r") as archive:
        assert archive.testzip() is None
        assert len(archive.namelist()) == 1
        assert archive.read(archive.namelist()[0]) == data


def test_product_skill_outputs_archive_rejects_unavailable_plan(monkeypatch):
    class MissingSession:
        def scalar(self, statement):
            return None

        def scalars(self, statement):
            raise AssertionError("Must not fetch outputs without a source plan")

    monkeypatch.setattr(
        router, "build_managed_storage_provider",
        lambda settings: pytest.fail("Must not create storage for a missing plan"),
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(router.download_product_skill_outputs_archive(
            source_plan_id="missing",
            session=MissingSession(),
            principal=SimpleNamespace(active_tenant_id="test-tenant"),
        ))
    assert exc.value.status_code == 404
