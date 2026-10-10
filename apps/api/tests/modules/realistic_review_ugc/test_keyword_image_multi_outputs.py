"""Stage 1 must save exactly six separate final files and keep partial uploads durable."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from PIL import Image

from app.domain.providers.contracts import StorageProviderError
from sqlalchemy import create_engine, select
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

def test_stage1_retains_checkpointed_images_if_upload_fails_midway(tmp_path):
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
            if self.requests == 3:
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
