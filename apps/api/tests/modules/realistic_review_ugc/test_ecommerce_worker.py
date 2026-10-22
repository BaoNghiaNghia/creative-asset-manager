"""Worker integration with isolated SQLAlchemy and a fake Codex image provider."""
import asyncio
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.ecommerce_pipeline import EcommerceGenerateHandler, JOB_TYPE
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcEcommerceJobModel, RrugcImageOutputVersionModel,
)


def _image(fmt="PNG", size=(600, 650)):
    out = BytesIO()
    Image.new("RGB", size, (255, 245, 226)).save(out, format=fmt)
    return out.getvalue()


class _Stream:
    def __init__(self, body):
        async def data():
            yield body
        self.body = data()

    async def close(self):
        pass


class _Storage:
    def __init__(self):
        self.opened = []
        self.saved = []

    async def open_asset(self, request):
        self.opened.append(request.remote_file_id)
        if request.remote_file_id == "design-remote":
            canvas = Image.new("RGBA", (600, 650), (0, 0, 0, 0))
            canvas.paste((175, 35, 42, 255), (155, 200, 455, 420))
            output = BytesIO()
            canvas.save(output, format="PNG")
            return _Stream(output.getvalue())
        return _Stream(_image())

    async def store_asset(self, request):
        data = bytearray()
        async for chunk in request.body:
            data.extend(chunk)
        self.saved.append((request.filename, len(data)))
        return SimpleNamespace(remote_file_id="persisted-photo-output")


def test_ecommerce_worker_stores_output_and_version_without_real_codex(monkeypatch):
    import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        RrugcImageOutputVersionModel.__table__, ProcessingJobModel.__table__,
    ])
    job_id = str(uuid4())
    process_id = str(uuid4())
    with Session(engine) as session:
        session.add(RrugcSourcePlanModel(
            id="source-plan", tenant_id="tenant-1",
            root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design-remote", source_relative_path="design.png",
            source_name="design.png", source_mime_type="image/png",
            source_revision="rev-1", created_by_user_id="operator",
        ))
        stock_digest = pipeline._stock_refs(SimpleNamespace(), "prod_s2_ec1")[1]
        session.add(RrugcEcommerceJobModel(
            id=job_id, tenant_id="tenant-1", source_plan_id="source-plan",
            source_revision="rev-1", stage="prod_s2_ec1",
            stock_sha256=stock_digest, skill_source="local",
            skill_name="sample-ecommerce-image", skill_bundle_sha256="1"*64,
            created_by_user_id="operator", status="queued", processing_job_id=process_id,
        ))
        session.commit()

    monkeypatch.setattr(pipeline, "verify_stage2_skill_runtime", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256", lambda *args, **kwargs: "1"*64)

    @contextmanager
    def acquire(*args, **kwargs):
        yield "account", "/mock/codex/home"

    monkeypatch.setattr(pipeline, "acquire_worker_profile", acquire)

    calls = []
    class FakeRunner:
        def __init__(self, config):
            self.config = config

        async def generate_from_references(self, *, person, references, prompt, **kwargs):
            calls.append({"references": references, "prompt": prompt, "person": person})
            return SimpleNamespace(image_bytes=_image(), mime_type="image/png")

        def cleanup_attempt(self, attempt_id):
            pass

    monkeypatch.setattr(pipeline, "CodexImageGenRunner", FakeRunner)
    storage = _Storage()
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_HOME="/not-read-by-fake",
        IMAGE_GENERATION_STAGING_ROOT="/tmp", CODEX_IMAGE_TIMEOUT_SECONDS=500,
        CODEX_IMAGE_MODEL=None,
    )
    def session_factory():
        return Session(engine)
    context = SimpleNamespace(
        job=SimpleNamespace(
            job_type=JOB_TYPE, tenant_id="tenant-1", entity_id=job_id,
            payload={"ecommerce_job_id": job_id}, id=process_id,
        ),
        is_cancelled=False,
        dependencies=SimpleNamespace(session_factory=session_factory,
                                     storage_provider=storage),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None),
    )
    result = asyncio.run(EcommerceGenerateHandler(settings)._execute(context))
    with Session(engine) as session:
        row = session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.id == job_id))
        assert row.status == "completed"
        assert row.output_remote_file_id == "persisted-photo-output"
        assert row.front_preview_remote_file_id == "persisted-photo-output"
        assert row.front_preview_design_sha256
        assert (row.output_width, row.output_height) == (600, 650)
        versions = list(session.scalars(select(RrugcImageOutputVersionModel)))
        assert len(versions) == 1
        assert versions[0].stage == "ecommerce"
        assert versions[0].remote_file_id == "persisted-photo-output"
    assert storage.opened == ["design-remote"]
    assert len(storage.saved) == 2 and all(item[1] > 0 for item in storage.saved)
    assert storage.saved[0][0].startswith("input_embroidered_front_")
    assert len(calls) == 1 and len(calls[0]["references"]) == 2
    assert calls[0]["references"][1].role == "embroidered_front_preview"
    assert "FRONT high" in calls[0]["prompt"]
