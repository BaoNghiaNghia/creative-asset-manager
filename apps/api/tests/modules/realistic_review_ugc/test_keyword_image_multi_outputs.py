"""Stage 1 must durably save every Skill-produced image, without a count cap."""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.modules.image_generation.providers import GeneratedImageFile, GeneratedImageResult
from app.modules.realistic_review_ugc.keyword_images import KeywordImageGenerateHandler
from app.modules.realistic_review_ugc.model import RrugcKeywordImageJobModel, RrugcImageOutputVersionModel


def test_stage1_persists_all_37_image_files_and_their_names(tmp_path):
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
    for index in range(37):
        path = tmp_path / f"artwork_{index:03}.png"
        Image.new("RGB", (80, 90), (index, 30, 140)).save(path)
        files.append(GeneratedImageFile(
            path=str(path), filename=f"artworks/artwork_{index:03}.png",
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
    assert len(storage.uploads) == 37
    with Session(engine) as session:
        rows = session.scalars(
            select(RrugcImageOutputVersionModel).order_by(RrugcImageOutputVersionModel.version)
        ).all()
        assert len(rows) == 37
        assert [row.version for row in rows] == list(range(1, 38))
        assert rows[36].output_name == "artworks/artwork_036.png"
        job = session.get(RrugcKeywordImageJobModel, "image-job-1")
        assert job.status == "completed"
        assert job.output_remote_file_id == "remote-1"
        assert job.output_web_url == "https://cdn.example.test/1"
    engine.dispose()
