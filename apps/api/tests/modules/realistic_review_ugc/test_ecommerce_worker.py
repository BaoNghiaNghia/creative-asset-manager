"""Worker integration with isolated SQLAlchemy and a fake Codex image provider."""
import asyncio
from contextlib import contextmanager
from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from PIL import Image
import pytest
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


def test_all_multi_hat_and_ugc_shots_receive_calibrated_front_master():
    from app.modules.realistic_review_ugc.ecommerce_pipeline import (
        FRONT_PREVIEW_STAGES, SCENE_GROUPS, SCENE_STAGE_SLOTS, STOCK_ROLES,
    )
    assert FRONT_PREVIEW_STAGES == frozenset({
        "prod_s2_ec1",
        "prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3",
        "prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3",
    })
    assert set(SCENE_STAGE_SLOTS) == set().union(*SCENE_GROUPS.values())
    assert all(STOCK_ROLES[stage][0] == "stock_front"
               for stage in FRONT_PREVIEW_STAGES)
    assert not {"prod_s2_ec2", "prod_s2_ec3", "prod_s2_ec4"}.intersection(
        FRONT_PREVIEW_STAGES)


@pytest.mark.parametrize("stage", [
    "prod_s2_ec1",
    "prod_s2_mh_1", "prod_s2_mh_2", "prod_s2_mh_3",
    "prod_s2_ugc_1", "prod_s2_ugc_2", "prod_s2_ugc_3",
])
def test_ecommerce_worker_stores_output_and_version_without_real_codex(monkeypatch, stage):
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
        stock_digest = pipeline._stock_refs(SimpleNamespace(), stage)[1]
        session.add(RrugcEcommerceJobModel(
            id=job_id, tenant_id="tenant-1", source_plan_id="source-plan",
            source_revision="rev-1", stage=stage,
            stock_sha256=stock_digest, skill_source="local",
            skill_name="sample-ecommerce-image", skill_bundle_sha256="1"*64,
            created_by_user_id="operator", status="queued", processing_job_id=process_id,
        ))
        session.commit()

    monkeypatch.setattr(pipeline, "verify_stage2_skill_runtime", lambda **kwargs: None)
    monkeypatch.setattr(pipeline, "installed_stage2_skill_sha256", lambda *args, **kwargs: "1"*64)

    if stage in pipeline.SCENE_GROUPS["prod_s2_mh"]:
        pinned = [{"image_id": "scout-a"}]
        monkeypatch.setattr(pipeline, "validate_pinned_multihat_references",
                            lambda _value: pinned)
        async def fake_reference_sheets(_refs):
            return [], 3
        monkeypatch.setattr(pipeline, "build_multihat_reference_sheets", fake_reference_sheets)

    if stage in pipeline.SCENE_GROUPS["prod_s2_ugc"]:
        monkeypatch.setattr(pipeline, "allowed_scout_image_url", lambda _url: True)
        class FakeResponse:
            content = _image()
            headers = {"content-type": "image/png"}
            def raise_for_status(self):
                pass
        class FakeAsyncClient:
            def __init__(self, **kwargs):
                pass
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def get(self, _url):
                return FakeResponse()
        monkeypatch.setattr(pipeline.httpx, "AsyncClient", FakeAsyncClient)

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
            image_data = _image()
            if stage == "prod_s2_ec1":
                import numpy as np
                mask_ref = next(r for r in references if r.role == "slot1_edit_mask")
                with Image.open(BytesIO(mask_ref.image.image_bytes)) as mask_image:
                    selected = np.asarray(mask_image.convert("L")) > 180
                with Image.open(BytesIO(person.image_bytes)) as original:
                    candidate = np.asarray(original.convert("RGB"), dtype=np.int16).copy()
                rng = np.random.default_rng(15)
                fibers = rng.normal(0, 13, selected.shape)
                candidate[selected] += np.rint(fibers[selected])[:, None].astype(np.int16)
                stream = BytesIO()
                Image.fromarray(np.uint8(np.clip(candidate, 0, 255)), "RGB").save(stream, format="PNG")
                image_data = stream.getvalue()
            return SimpleNamespace(image_bytes=image_data, mime_type="image/png")

        def cleanup_attempt(self, attempt_id):
            pass

    monkeypatch.setattr(pipeline, "CodexImageGenRunner", FakeRunner)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all, "advance_after_output", lambda *args, **kwargs: None)
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
            payload={
                "ecommerce_job_id": job_id,
                **({"multihat_references": [{"image_id": "scout-a"}]}
                   if stage in pipeline.SCENE_GROUPS["prod_s2_mh"] else {}),
                **({"scout_reference": {"image_url": "https://example.test/ugc.jpg"}}
                   if stage in pipeline.SCENE_GROUPS["prod_s2_ugc"] else {}),
            }, id=process_id,
        ),
        is_cancelled=False,
        dependencies=SimpleNamespace(session_factory=session_factory,
                                     storage_provider=storage),
        logger=SimpleNamespace(info=lambda *args, **kwargs: None, exception=lambda *args, **kwargs: None),
    )
    result = asyncio.run(EcommerceGenerateHandler(settings)._execute(context))
    with Session(engine) as session:
        row = session.scalar(select(RrugcEcommerceJobModel).where(
            RrugcEcommerceJobModel.id == job_id))
        assert row.status == "completed"
        assert row.output_remote_file_id == "persisted-photo-output"
        assert row.front_preview_remote_file_id == "persisted-photo-output"
        assert row.front_preview_design_sha256
        assert (row.output_width, row.output_height) == (
            (512, 640) if stage == "prod_s2_ec1" else (600, 650)
        )
        versions = list(session.scalars(select(RrugcImageOutputVersionModel)))
        assert len(versions) == 1
        assert versions[0].stage == "ecommerce"
        assert versions[0].remote_file_id == "persisted-photo-output"
    assert storage.opened == ["design-remote"]
    assert len(storage.saved) == 2 and all(item[1] > 0 for item in storage.saved)
    assert storage.saved[0][0].startswith("input_embroidered_front_")
    assert len(calls) == 1
    assert calls[0]["references"][0].role == "design_reference"
    assert calls[0]["references"][1].role == (
        "unaltered_stock_reference" if stage == "prod_s2_ec1"
        else "embroidered_front_preview"
    )
    if stage == "prod_s2_ec1":
        assert calls[0]["references"][2].role == "slot1_edit_mask"
        assert "white of slot1_edit_mask" in calls[0]["prompt"]
        assert calls[0]["person"].mime_type == "image/png"
        assert all(ref.role != "embroidered_front_preview"
                   for ref in calls[0]["references"])
    assert len(calls[0]["references"]) >= 2
    if stage == "prod_s2_ec1":
        assert "FRONT high" in calls[0]["prompt"]
    else:
        assert "calibrated" in calls[0]["prompt"].lower()
    assert "canonical-design-direct-thread-v4" in calls[0]["prompt"]
    assert "PRE-SAVE VISUAL QUALITY CHECK" in calls[0]["prompt"]
