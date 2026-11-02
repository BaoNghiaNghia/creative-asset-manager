"""Stage 2 Slots 2-3: one native Codex edit with exactly TWO images.

The real pipeline remains unchanged for historical queued V2 local transfers.
New V3 jobs require the official stock target and saved Slot 1 Embroidery
Reference only. The original flat Stage 1 PNG is never fetched or passed.
"""
import asyncio
from contextlib import contextmanager
from hashlib import sha256
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.generation_handler import RrugcGenerationHandlerError
from app.modules.realistic_review_ugc.embroidery_scale import saved_master_box, projected_angle_box
from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcImageOutputVersionModel, RrugcSourcePlanModel,
)


def png(im):
    b = BytesIO()
    im.save(b, "PNG")
    return b.getvalue()


def samples(stage, *, incorrect_side=False):
    source = Image.new("RGB", (900, 1000), (225, 212, 195))
    mark = ImageDraw.Draw(source)
    mark.text((695, 645), "GatorHats", fill=(20, 44, 82))
    draw_design = Image.new("RGBA", (700, 600), (0, 0, 0, 0))
    ImageDraw.Draw(draw_design).rectangle((60, 160, 620, 270), fill=(187, 59, 12))
    macro = Image.new("RGBA", (720, 620), (0, 0, 0, 0))
    d = ImageDraw.Draw(macro)
    d.rectangle((70, 160, 650, 285), fill=(187, 59, 12))
    for x in range(70, 650, 3):
        d.line((x, 160, x, 284), fill=(215, 86, 30), width=1)
    edited = source.copy()
    edit = ImageDraw.Draw(edited)
    if stage == "prod_s2_ec2":
        x, y, width = 270, 347, 205
    elif incorrect_side:
        x, y, width = 690, 375, 130
    else:
        x, y, width = 250, 380, 28
    edit.rectangle((x, y, x+width, y+80), fill=(187,59,12))
    for xx in range(x, x+width, 3):
        edit.line((xx, y, xx, y+80), fill=(215,86,30), width=1)
    return png(source), png(draw_design), png(macro), png(edited)


class Storage:
    def __init__(self, images):
        self.images = dict(images)
        self.saves = 0

    async def open_asset(self, request):
        content = self.images[request.remote_file_id]
        async def stream():
            yield content
        async def close():
            pass
        return SimpleNamespace(body=stream(), close=close)

    async def store_asset(self, request):
        value = bytearray()
        async for chunk in request.body:
            value.extend(chunk)
        self.saves += 1
        self.images["saved-angle"] = bytes(value)
        return SimpleNamespace(remote_file_id="saved-angle")


def test_slot2_qa_reason_is_reported_not_masked_as_storage_error(monkeypatch):
    class Handler(pipeline.EcommerceGenerateHandler):
        def _failure(self, context, code, message, terminal=False):
            context.events.append((code, message, terminal))

        async def _execute(self, context):
            raise RrugcGenerationHandlerError(
                "ecommerce_angle_direct_qa_failed",
                "No credible front embroidery detected inside the visible front crown.",
                retryable=False,
            )

    context = SimpleNamespace(
        job=SimpleNamespace(entity_id="test-angle", tenant_id="tenant"),
        events=[],
        logger=SimpleNamespace(warning=lambda *args, **kw: None),
    )
    result = Handler()(context)
    assert result.outcome.value == "non_retryable_failure"
    assert "No credible front embroidery" in result.error_message
    assert result.error_code == "ecommerce_angle_direct_qa_failed"
    assert context.events == [
        ("ecommerce_angle_direct_qa_failed", result.error_message, True),
    ]


@pytest.mark.parametrize("stage,wrong_side", [
    ("prod_s2_ec2", False),
    ("prod_s2_ec3", False),
    ("prod_s2_ec3", True),
])
def test_stock_edit_invokes_single_native_model_without_mask_and_side_fallback(
        monkeypatch, stage, wrong_side):
    stock, design, macro, native = samples(stage, incorrect_side=wrong_side)
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        ProcessingJobModel.__table__, RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan", tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design", source_name="design.png",
            source_relative_path="design.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="user",
        ))
        db.add(RrugcEcommerceJobModel(
            id="job", tenant_id="tenant", source_plan_id="plan",
            source_revision="rev", stage=stage, color_key="natural-black",
            stock_sha256="a"*64, skill_source="internal",
            skill_name=pipeline.SLOT1_DIRECT_NAME,
            skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256,
            front_reference_remote_file_id="front", front_reference_content_type="image/png",
            front_reference_size_bytes=len(stock), front_preview_remote_file_id="macro",
            front_preview_content_type="image/png", front_preview_size_bytes=len(macro),
            front_preview_design_sha256=sha256(design).hexdigest(),
            created_by_user_id="user", status="queued",
        ))
        db.commit()

    # The source PNG is deliberately NOT in storage. Any attempt to retrieve
    # it for a direct angle edit must fail this integration test.
    storage = Storage({"front": stock, "macro": macro})
    monkeypatch.setattr(pipeline, "_pinned_stock_refs",
                        lambda *args: ([("stock_three_quarter" if stage == "prod_s2_ec2"
                                         else "stock_side_90", stock)], False))
    pool_calls = []
    @contextmanager
    def pool(*args, **kwargs):
        pool_calls.append(kwargs)
        yield "codex-user", "/fake/codex"
    monkeypatch.setattr(pipeline, "acquire_worker_profile", pool)
    provider_calls = []
    class Runner:
        def __init__(self, config):
            self.config = config
            assert config.output_contract == "stage2_angle_direct_single"
            assert config.skill_name == pipeline.SLOT1_DIRECT_NAME
        async def generate_from_references(self, **args):
            provider_calls.append(args)
            assert args["person"].image_bytes
            assert args["person"].width == 900 and args["person"].height == 1000
            assert [r.role for r in args["references"]] == [
                "slot1_thread_material_reference",
            ]
            assert "ONE IMAGE-EDIT invocation only" in args["prompt"]
            assert "No mask image" in args["prompt"]
            assert "PHYSICALLY LOCKED EMBROIDERY SIZE" in args["prompt"]
            assert "slot1_edit_mask" not in args["prompt"]
            assert "REFERENCE C" not in args["prompt"]
            assert all("mask" not in r.role for r in args["references"])
            assert "design_reference" not in [r.role for r in args["references"]]
            return GeneratedImageResult(
                provider="codex", model="test", image_bytes=native,
                mime_type="image/png",
                provider_metadata={"native_sha256": sha256(native).hexdigest(), "native_passes": 1},
            )
        def cleanup_attempt(self, _attempt_id):
            pass
    monkeypatch.setattr(pipeline, "CodexImageGenRunner", Runner)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all, "advance_after_output", lambda *a, **kw: None)
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=True,
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_HOME="/fake/codex",
        IMAGE_GENERATION_STAGING_ROOT="/tmp", CODEX_IMAGE_TIMEOUT_SECONDS=120,
        CODEX_IMAGE_MODEL=None,
    )
    master = saved_master_box(
        (255, 340, 615, 540), 900, 1000, "rev", "macro",
    )
    context = SimpleNamespace(
        job=SimpleNamespace(
            id="processing", job_type=pipeline.JOB_TYPE, entity_id="job", tenant_id="tenant",
            payload={"ecommerce_job_id": "job", "thread_material_pipeline": "v3-direct-edit",
                     "embroidery_placement_v1": master},
        ),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine),
                                     storage_provider=storage),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *a, **kw: None, exception=lambda *a, **kw: None),
    )
    result=asyncio.run(pipeline.EcommerceGenerateHandler(settings)._execute(context))
    assert result.outcome.value == "completed"
    assert len(pool_calls) == len(provider_calls) == storage.saves == 1
    with Session(engine) as db:
        row = db.get(RrugcEcommerceJobModel, "job")
        assert row.front_preview_remote_file_id == "macro"
        assert row.output_remote_file_id == "saved-angle"
        assert row.status == "completed"
        versions = list(db.query(RrugcImageOutputVersionModel).all())
        assert len(versions) == 1
        info=versions[0].font_info_json["thread_inpaint_pipeline"]
        assert info["version"] == "angle-direct-v3-single-edit"
        assert versions[0].font_info_json["embroidery_placement_v1"] == master
        assert info["native_passes"] == 1
        assert info["original_stock_pixels_protected"] is True
        assert info["side_stock_fallback"] is wrong_side
    with Image.open(BytesIO(storage.images["saved-angle"])) as img:
        final = np.asarray(img.convert("RGB"))
    with Image.open(BytesIO(stock)) as img:
        before = np.asarray(img.convert("RGB"))
    assert np.array_equal(final[600:680,690:875], before[600:680,690:875])
    if wrong_side:
        assert np.array_equal(final, before)
    else:
        assert np.count_nonzero(final != before) > 120
    engine.dispose()
