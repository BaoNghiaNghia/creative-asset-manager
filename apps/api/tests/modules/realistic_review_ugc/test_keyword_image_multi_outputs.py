"""Stage 1 must save exactly six separate final files and keep partial uploads durable."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.domain.providers.contracts import StorageProviderError
from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.image_generation.providers import GeneratedImageFile, GeneratedImageResult
from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler
from app.modules.realistic_review_ugc.model import RrugcKeywordImageJobModel, RrugcImageOutputVersionModel


def test_stage1_persists_six_individual_final_files_and_their_names(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (RrugcKeywordImageJobModel.__table__, RrugcImageOutputVersionModel.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add(RrugcKeywordImageJobModel(
            id="image-job-1", tenant_id="tenant-a", keyword_id="keyword-id-1",
            keyword_text="BEACH BUM", skill_source="local", skill_name="any-skill",
            prompt_text="BEACH BUM", status="running", created_by_user_id="admin",
        ))
        session.commit()
    files = []
    for index in range(6):
        path = tmp_path / f"artwork_{index:03}.png"
        Image.new("RGB", (800, 900), (index, 30, 140)).save(path)
        files.append(GeneratedImageFile(
            path=str(path), filename=f"output/final/design_{index + 1:02}.png",
            mime_type="image/png",
        ))
    generated = GeneratedImageResult(
        provider="codex", model=None, image_bytes=Path(files[0].path).read_bytes(),
        mime_type="image/png", output_files=tuple(files),
    )
    class Storage:
        def __init__(self):
            self.uploads = []
        async def store_asset(self, payload):
            body = b"".join([chunk async for chunk in payload.body])
            assert len(body) == payload.size_bytes
            self.uploads.append(payload.filename)
            return SimpleNamespace(
                remote_file_id=f"remote-{len(self.uploads)}",
                web_url=f"https://cdn.example.test/{len(self.uploads)}",
            )
    storage = Storage()
    context = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", entity_id="image-job-1", id="attempt-1"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine, autoflush=False)),
        logger=logging.getLogger(__name__),
    )
    result = asyncio.run(KeywordImageGenerateHandler()._save_skill_outputs(
        context, storage, generated,
    ))
    assert result.outcome.value == "completed"
    assert len(storage.uploads) == 6
    with Session(engine) as session:
        rows = session.scalars(
            select(RrugcImageOutputVersionModel).order_by(RrugcImageOutputVersionModel.version)
        ).all()
        assert len(rows) == 6
        assert [row.version for row in rows] == list(range(1, 7))
        assert rows[5].output_name == "output/final/design_06.png"
        job = session.get(RrugcKeywordImageJobModel, "image-job-1")
        assert job.status == "completed"
        assert job.output_remote_file_id == "remote-1"
        assert job.output_web_url == "https://cdn.example.test/1"
    engine.dispose()

def test_stage1_retains_checkpointed_images_if_upload_fails_midway(tmp_path, monkeypatch):
    monkeypatch.setattr("app.modules.realistic_review_ugc.keyword_images.STAGE1_DRIVE_RETRY_DELAYS", (0, 0))
    """Six final images, third Drive upload fails: the first two survive."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (RrugcKeywordImageJobModel.__table__, RrugcImageOutputVersionModel.__table__):
        table.create(engine)
    with Session(engine, autoflush=False) as session:
        session.add(RrugcKeywordImageJobModel(
            id="partial-job", tenant_id="tenant-a", keyword_id="kw-partial",
            keyword_text="PARTIAL", skill_source="local", skill_name="sample",
            prompt_text="PARTIAL", status="running", created_by_user_id="actor",
        ))
        session.commit()
    files = []
    for index in range(6):
        path = tmp_path / f"part-{index}.png"
        Image.new("RGB", (20, 30), (20 + index, 50, 80)).save(path)
        files.append(GeneratedImageFile(path=str(path), filename=f"artwork/part-{index}.png", mime_type="image/png"))
    generated = GeneratedImageResult(
        provider="codex", model=None, image_bytes=Path(files[0].path).read_bytes(),
        mime_type="image/png", output_files=tuple(files),
    )

    class FailingStorage:
        requests = 0

        async def store_asset(self, payload):
            self.requests += 1
            if self.requests >= 3:
                raise StorageProviderError("temporary Drive failure", retryable=True, code="managed_storage_network_error")
            return SimpleNamespace(remote_file_id=f"uploaded-{self.requests}", web_url=f"https://example.test/{self.requests}")

    context = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", entity_id="partial-job", id="attempt-1"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine, autoflush=False)),
        logger=logging.getLogger(__name__),
    )
    handler = KeywordImageGenerateHandler()
    with pytest.raises(StorageProviderError, match="temporary Drive failure"):
        asyncio.run(handler._save_skill_outputs(context, FailingStorage(), generated))
    handler._fail(context, "managed_storage_network_error", "temporary Drive failure")
    with Session(engine, autoflush=False) as session:
        rows = session.scalars(select(RrugcImageOutputVersionModel).order_by(RrugcImageOutputVersionModel.version)).all()
        assert [row.remote_file_id for row in rows] == ["uploaded-1", "uploaded-2"]
        assert [row.version for row in rows] == [1, 2]
        assert all(row.processing_job_id == "attempt-1" for row in rows)
        job = session.get(RrugcKeywordImageJobModel, "partial-job")
        assert job.status == "failed"
        assert job.output_remote_file_id == "uploaded-1"
        assert job.last_error_code == "managed_storage_network_error"
    engine.dispose()

def test_stage1_transient_drive_500_retries_stream_without_duplicate_versions(tmp_path, monkeypatch):
    monkeypatch.setattr("app.modules.realistic_review_ugc.keyword_images.STAGE1_DRIVE_RETRY_DELAYS", (0, 0))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (RrugcKeywordImageJobModel.__table__, RrugcImageOutputVersionModel.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add(RrugcKeywordImageJobModel(
            id="transient-job", tenant_id="tenant-a", keyword_id="kw", keyword_text="GOD & COUNTRY MUSIC",
            skill_source="local", skill_name="gatorhats-stage1-six-designs",
            prompt_text="GOD & COUNTRY MUSIC", status="running", created_by_user_id="admin",
        ))
        session.commit()
    files = []
    for number in range(1, 7):
        path = tmp_path / f"design_{number:02}.png"
        Image.new("RGB", (800, 900), (number * 15, 40, 100)).save(path)
        files.append(GeneratedImageFile(path=str(path), filename=f"output/final/{path.name}", mime_type="image/png"))
    generated = GeneratedImageResult(provider="codex", model=None, image_bytes=b"",
                                     mime_type="image/png", output_files=tuple(files))

    class FlakyStorage:
        def __init__(self):
            self.requests = {}
        async def store_asset(self, input):
            data = b"".join([chunk async for chunk in input.body])
            assert len(data) == input.size_bytes
            times = self.requests.get(input.asset_id, 0) + 1
            self.requests[input.asset_id] = times
            if input.asset_id.endswith(":2") and times == 1:
                raise StorageProviderError(
                    "Google Drive storage request failed with HTTP 500.",
                    code="managed_storage_temporarily_unavailable", retryable=True,
                )
            return SimpleNamespace(remote_file_id="remote-" + input.asset_id, web_url="https://example.test/asset")

    storage = FlakyStorage()
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", entity_id="transient-job", id="original-run"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine, autoflush=False)),
        logger=logging.getLogger(__name__),
    )
    result = asyncio.run(KeywordImageGenerateHandler()._save_skill_outputs(ctx, storage, generated))
    assert result.outcome.value == "completed"
    assert storage.requests["rrugc-keyword-image:transient-job:original-run:2"] == 2
    with Session(engine) as session:
        rows = session.scalars(select(RrugcImageOutputVersionModel).order_by(
            RrugcImageOutputVersionModel.version,
        )).all()
        assert len(rows) == 6
        assert {row.processing_job_id for row in rows} == {"original-run"}
        assert session.get(RrugcKeywordImageJobModel, "transient-job").status == "completed"
    engine.dispose()


def test_stage1_resume_drive_upload_skips_saved_outputs_and_reuses_generation_id(tmp_path, monkeypatch):
    monkeypatch.setattr("app.modules.realistic_review_ugc.keyword_images.STAGE1_DRIVE_RETRY_DELAYS", (0,))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    for table in (RrugcKeywordImageJobModel.__table__, RrugcImageOutputVersionModel.__table__):
        table.create(engine)
    with Session(engine) as session:
        session.add(RrugcKeywordImageJobModel(
            id="resume-job", tenant_id="tenant-a", keyword_id="kw", keyword_text="QUOTE",
            skill_source="local", skill_name="gatorhats-stage1-six-designs",
            prompt_text="QUOTE", status="running", created_by_user_id="admin",
        ))
        session.commit()
    final_files = []
    for index in range(6):
        path = tmp_path / f"design_{index + 1:02}.png"
        Image.new("RGB", (900, 900), (index * 16, 35, 55)).save(path)
        final_files.append(GeneratedImageFile(
            path=str(path), filename=f"output/final/{path.name}", mime_type="image/png",
        ))
    generated = GeneratedImageResult(provider="codex", model=None, image_bytes=b"",
                                     mime_type="image/png", output_files=tuple(final_files))
    class PartialStorage:
        def __init__(self, fail):
            self.fail = fail
            self.calls = []
        async def store_asset(self, input):
            self.calls.append(input.asset_id)
            if self.fail and input.asset_id.endswith(":2"):
                raise StorageProviderError("Drive HTTP 500", code="managed_storage_temporarily_unavailable", retryable=True)
            return SimpleNamespace(remote_file_id="remote-" + input.asset_id, web_url="https://example.test/image")

    old_ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", entity_id="resume-job", id="first-run"),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine, autoflush=False)),
        logger=logging.getLogger(__name__),
    )
    with pytest.raises(StorageProviderError):
        asyncio.run(KeywordImageGenerateHandler()._save_skill_outputs(old_ctx, PartialStorage(True), generated))
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(RrugcImageOutputVersionModel)) == 2
    new_ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant-a", entity_id="resume-job", id="upload-only-run"),
        dependencies=old_ctx.dependencies,
        logger=old_ctx.logger,
    )
    storage = PartialStorage(False)
    result = asyncio.run(KeywordImageGenerateHandler()._save_skill_outputs(
        new_ctx, storage, generated, generation_processing_id="first-run",
    ))
    assert result.outcome.value == "completed"
    assert len(storage.calls) == 4
    assert all(":first-run:" in asset_id for asset_id in storage.calls)
    with Session(engine) as session:
        rows = session.scalars(select(RrugcImageOutputVersionModel).order_by(
            RrugcImageOutputVersionModel.version,
        )).all()
        assert [row.version for row in rows] == list(range(1, 7))
        assert {row.processing_job_id for row in rows} == {"first-run"}
    engine.dispose()
