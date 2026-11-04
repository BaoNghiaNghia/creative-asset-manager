"""Stage 2 two-image deterministic angle workflow: no provider lease or flat PNG."""
import asyncio
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
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.embroidery_scale import (
    front_scale_contract, front_transfer_box, projected_raster_box, saved_master_box,
)
from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcImageOutputVersionModel, RrugcSourcePlanModel,
)
from app.modules.realistic_review_ugc.threadtransfer_stock import composite_reference_on_stock_view


def png(image):
    out = BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def photo():
    stock = Image.new("RGB", (900, 1000), (235, 219, 202))
    draw = ImageDraw.Draw(stock)
    draw.line([(620, 150), (755, 690)], fill=(199, 188, 171), width=4)
    draw.text((715, 675), "GatorHats", fill=(21, 51, 84))
    return png(stock)


def embroidery():
    image = Image.new("RGBA", (740, 500), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    for x0, x1, y0, y1, color in (
        (60, 710, 145, 236, (190, 79, 21, 255)),
        (110, 650, 300, 375, (28, 82, 122, 255)),
    ):
        for left in range(x0, x1, 70):
            right = min(left+50, x1)
            draw.rectangle((left, y0, right, y1), fill=color)
            for x in range(left+1, right, 3):
                draw.line((x, y0, x, y1-1),
                          fill=tuple(min(255, int(v*1.11)) for v in color[:3])+(255,), width=1)
    return png(image)


class Storage:
    def __init__(self, data):
        self.data = dict(data)
        self.saved = []

    async def open_asset(self, request):
        if request.remote_file_id not in self.data:
            raise AssertionError("Flat artwork PNG or front output must not be loaded")
        value = self.data[request.remote_file_id]

        async def stream():
            yield value

        async def close():
            pass

        return SimpleNamespace(body=stream(), close=close)

    async def store_asset(self, request):
        buffer = bytearray()
        async for chunk in request.body:
            buffer.extend(chunk)
        key = "saved-angle-" + str(len(self.saved))
        self.data[key] = bytes(buffer)
        self.saved.append(key)
        return SimpleNamespace(remote_file_id=key)


def test_user_placement_guide_is_master_not_old_38_percent_clamp():
    guide = dict(left=.23, top=.34, width=.54, height=.22,
                 upper_line=.23, lower_line=.65, design_size=100)
    pin = front_scale_contract(guide)
    assert pin["version"] == "front-guide-v3"
    assert pin["safe_box"] == [.23, .34, .77, .56]
    fitted = front_transfer_box(pin, 3.3, (1000, 1200))
    center = ((fitted[0]+fitted[2])/2/1000, (fitted[1]+fitted[3])/2/1200)
    assert center == pytest.approx((.50, .45), abs=.002)
    assert .50 < (fitted[2]-fitted[0])/1000 < .52
    assert fitted[1] >= 1200*.34
    assert fitted[3] <= 1200*.56
    old = {
        "version": "front-locked-v1", "center_x": .50, "center_y": .44,
        "target_width": .38, "max_height": .20, "safe_box": [.145,.245,.855,.62],
    }
    assert (front_transfer_box(old, 3.3, (1000,1200))[2]
            - front_transfer_box(old, 3.3, (1000,1200))[0]) <= 380


def test_wide_front_placement_uses_the_full_three_quarter_crown_safely():
    master = saved_master_box((150, 310, 850, 540), 1000, 1000, "rev", "macro")
    x0, y0, x1, y1 = projected_raster_box(master, "prod_s2_ec2", (1000, 1000))
    assert x0 >= 230 and x1 <= 750
    assert (x1 - x0) > 450  # Front-sized yarn, not a tiny central decal.
    assert abs((x0 + x1)/2 - 490) <= 2
    assert y0 >= 300 and y1 <= 620


def test_fresh_side_front_fallback_keeps_full_readable_yarn_on_real_three_quarter_photo():
    stock, ref = photo(), embroidery()
    master = saved_master_box((230, 380, 720, 560), 900, 1000, "rev", "macro")
    out = composite_reference_on_stock_view(
        stock, ref, stage="prod_s2_ec3", placement=master,
        side_front_fallback=True,
    )
    expected = projected_raster_box(
        master, "prod_s2_ec3", (900, 1000), side_front_fallback=True,
    )
    assert out.target_box[2]-out.target_box[0] > 250
    assert expected[0] <= out.target_box[0] < out.target_box[2] <= expected[2]
    before = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(out.output_png)).convert("RGB"))
    assert np.array_equal(before[:, 720:], after[:, 720:])
    assert np.array_equal(before[:expected[1]], after[:expected[1]])
    assert np.array_equal(before[expected[3]:], after[expected[3]:])


@pytest.mark.parametrize("stage", ["prod_s2_ec2", "prod_s2_ec3"])
def test_raster_projection_preserves_factory_stock_outside_thread_pixels(stage):
    stock, ref = photo(), embroidery()
    master = saved_master_box((230, 380, 720, 560), 900, 1000, "rev", "macro")
    outcome = composite_reference_on_stock_view(
        stock, ref, stage=stage, placement=master,
    )
    before = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(outcome.output_png)).convert("RGB"))
    expected = projected_raster_box(master, stage, (900, 1000))
    a,b,c,d = outcome.target_box
    assert expected[0] <= a < c <= expected[2]
    assert expected[1] <= b < d <= expected[3]
    assert not np.array_equal(after[b:d, a:c], before[b:d, a:c])
    assert np.array_equal(after[:, 720:], before[:, 720:])  # factory GatorHats
    assert np.array_equal(after[:expected[1]], before[:expected[1]])
    assert np.array_equal(after[expected[3]:], before[expected[3]:])
    assert outcome.extracted_pixels > 100
    assert len(outcome.thread_layer_sha256) == 64
    if stage == "prod_s2_ec3":
        assert c-a < 70  # true side: only visible rightmost yarn edge
    else:
        assert c-a > 260  # 3/4: actual slogan, not tiny right-edge crop


def test_saved_freehand_quad_places_real_yarn_outside_legacy_crown_box():
    stock, reference = photo(), embroidery()
    master = saved_master_box((230, 380, 720, 560), 900, 1000, "rev", "macro")
    guide = {
        "version": "3q-free-v1", "revision": 7, "color_key": "natural-black",
        "corners": [[.61, .30], [.95, .32], [.95, .60], [.60, .58]],
    }
    outcome = composite_reference_on_stock_view(
        stock, reference, stage="prod_s2_ec2", placement=master, guide=guide,
    )
    before = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(outcome.output_png)).convert("RGB"))
    assert outcome.target_box[0] > 540
    # Freehand quad calculations use NumPy; persisted bbox must be JSON-native
    # Python ints or Psycopg fails after the PNG was already uploaded.
    assert all(type(value) is int for value in outcome.target_box)
    import json
    assert json.loads(json.dumps({"box": outcome.target_box}))["box"] == list(outcome.target_box)
    assert outcome.target_box[2] > .76 * 900  # No obsolete fixed 3/4 box
    assert outcome.extracted_pixels > 100
    assert np.array_equal(after[:, :540], before[:, :540])
    assert np.array_equal(after[630:], before[630:])
    assert np.array_equal(after[665:710, 705:850], before[665:710, 705:850])
    assert np.count_nonzero(after[350:590, 560:855] != before[350:590, 560:855]) > 400


def test_invalid_or_unpinned_freehand_quad_cannot_publish():
    from app.modules.realistic_review_ugc.slot1_embroidery import Slot1EmbroideryQAError
    stock, reference = photo(), embroidery()
    master = saved_master_box((230, 380, 720, 560), 900, 1000, "rev", "macro")
    invalid = {
        "version": "3q-free-v1",
        "corners": [[.9, .3], [.5, .3], [.5, .6], [.9, .6]],
    }
    with pytest.raises(Slot1EmbroideryQAError, match="Invalid saved"):
        composite_reference_on_stock_view(
            stock, reference, stage="prod_s2_ec2", placement=master, guide=invalid,
        )
    with pytest.raises(Slot1EmbroideryQAError, match="pinned Slot 1 master"):
        composite_reference_on_stock_view(
            stock, reference, stage="prod_s2_ec2", guide={
                "version": "3q-free-v1",
                "corners": [[.61, .3], [.95, .32], [.95, .6], [.6, .58]],
            },
        )


@pytest.mark.parametrize("stage", ["prod_s2_ec2", "prod_s2_ec3"])
@pytest.mark.parametrize("codex_enabled", [True, False])
def test_angle_worker_publishes_without_codex_or_png_artwork(monkeypatch, stage, codex_enabled):
    stock, ref = photo(), embroidery()
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        ProcessingJobModel.__table__, RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan", tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="artwork-never-loaded", source_name="design.png",
            source_relative_path="design.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="operator",
        ))
        db.add(RrugcEcommerceJobModel(
            id="job", tenant_id="tenant", source_plan_id="plan",
            source_revision="rev", stage=stage, color_key="natural-black",
            stock_sha256="e"*64, skill_source="internal",
            skill_name=pipeline.SLOT1_DIRECT_NAME, skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256,
            front_reference_remote_file_id="front-not-loaded",
            front_preview_remote_file_id="macro", front_preview_content_type="image/png",
            front_preview_size_bytes=len(ref), front_preview_design_sha256="d"*64,
            created_by_user_id="operator", status="queued",
        ))
        db.commit()

    storage = Storage({"macro": ref})
    monkeypatch.setattr(pipeline, "_pinned_stock_refs",
                        lambda *_args: ([("stock_three_quarter" if stage == "prod_s2_ec2"
                                         else "stock_side_90", stock)], False))
    def codex_prohibited(*_args, **_kwargs):
        raise AssertionError("No Codex account/profile should be acquired")
    monkeypatch.setattr(pipeline, "acquire_worker_profile", codex_prohibited)
    monkeypatch.setattr(pipeline, "CodexImageGenRunner", codex_prohibited)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all, "advance_after_output", lambda *_a, **_kw: None)
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True, IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True, CODEX_IMAGE_GENERATION_ENABLED=codex_enabled,
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_HOME="/fake",
        CODEX_IMAGE_TIMEOUT_SECONDS=120, CODEX_IMAGE_MODEL=None,
    )
    master = saved_master_box((230, 380, 720, 560), 900, 1000, "rev", "macro")
    context = SimpleNamespace(
        job=SimpleNamespace(
            id="processing", job_type=pipeline.JOB_TYPE,
            entity_id="job", tenant_id="tenant",
            payload={"ecommerce_job_id": "job", "thread_material_pipeline": "v4-raster-placement",
                     "embroidery_placement_v1": master},
        ),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine),
                                     storage_provider=storage),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *_a, **_kw: None,
                               exception=lambda *_a, **_kw: None),
    )
    result = asyncio.run(pipeline.EcommerceGenerateHandler(settings)._execute(context))
    assert result.outcome.value == "completed"
    assert len(storage.saved) == 1
    saved_photo = storage.data[storage.saved[0]]
    before = np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    after = np.asarray(Image.open(BytesIO(saved_photo)).convert("RGB"))
    assert np.array_equal(before[:, 720:], after[:, 720:])
    assert np.count_nonzero(before != after) > 100
    with Session(engine) as db:
        output = db.query(RrugcImageOutputVersionModel).one()
        info = output.font_info_json
        assert info["embroidery_placement_v1"] == master
        assert info["thread_inpaint_pipeline"]["version"] == "threadtransfer-v4-calibrated-quad"
        assert info["thread_inpaint_pipeline"]["native_passes"] == 0
        row = db.get(RrugcEcommerceJobModel, "job")
        assert row.status == "completed"
        assert row.front_preview_remote_file_id == "macro"
    engine.dispose()


@pytest.mark.parametrize("codex_enabled", [False, True])
def test_slot2_worker_uses_original_png_frozen_uv_with_no_front_reference(monkeypatch, codex_enabled):
    """Exercise real job persistence, provider bypass, metadata and masked stock."""
    from app.modules.realistic_review_ugc.slot2_fixed_uv import (
        PIPELINE_VERSION, load_profile,
    )
    artwork = embroidery()
    stock = png(Image.new("RGB", (1024, 1024), (233, 227, 212)))
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
        ProcessingJobModel.__table__, RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="uv-plan", tenant_id="tenant", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="original-design-png", source_name="original.png",
            source_relative_path="original.png", source_revision="rev",
            source_mime_type="image/png", source_size_bytes=len(artwork),
            created_by_user_id="operator",
        ))
        db.add(RrugcEcommerceJobModel(
            id="uv-job", tenant_id="tenant", source_plan_id="uv-plan",
            source_revision="rev", stage="prod_s2_ec2", color_key="natural-black",
            stock_sha256="f"*64, skill_source="internal",
            skill_name=pipeline.SLOT1_DIRECT_NAME,
            skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256,
            created_by_user_id="operator", status="queued",
        ))
        db.commit()
    storage = Storage({"original-design-png": artwork})
    monkeypatch.setattr(
        pipeline, "_pinned_stock_refs",
        lambda *_args: ([("stock_three_quarter", stock)], False),
    )
    def no_codex(*_args, **_kwargs):
        raise AssertionError("Fixed UV must not acquire Codex or perform an image edit.")
    monkeypatch.setattr(pipeline, "CodexImageGenRunner", no_codex)
    monkeypatch.setattr(pipeline, "acquire_worker_profile", no_codex)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all, "advance_after_output", lambda *_a, **_kw: None)
    context = SimpleNamespace(
        job=SimpleNamespace(
            id="uv-processing", job_type=pipeline.JOB_TYPE,
            entity_id="uv-job", tenant_id="tenant",
            payload={
                "ecommerce_job_id": "uv-job", "thread_material_pipeline": PIPELINE_VERSION,
                "uv_profile_id": "cream-navy-user-axis-v1",
                "uv_profile_sha256": load_profile()[1],
            },
        ),
        dependencies=SimpleNamespace(
            session_factory=lambda: Session(engine), storage_provider=storage,
        ),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *_a, **_kw: None,
                               exception=lambda *_a, **_kw: None),
    )
    settings = SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True,
        IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True,
        CODEX_IMAGE_GENERATION_ENABLED=codex_enabled,
        CODEX_IMAGE_BINARY="codex", CODEX_IMAGE_HOME="/fake",
        CODEX_IMAGE_TIMEOUT_SECONDS=120, CODEX_IMAGE_MODEL=None,
    )
    result = asyncio.run(pipeline.EcommerceGenerateHandler(settings)._execute(context))
    assert result.outcome.value == "completed"
    assert len(storage.saved) == 1
    saved = storage.data[storage.saved[0]]
    image = Image.open(BytesIO(saved))
    assert image.format == "PNG" and image.size == (1024, 1024)
    output = np.asarray(image.convert("RGB"))
    before = np.asarray(Image.open(BytesIO(stock)))
    assert np.array_equal(output[0:150], before[0:150])
    assert np.array_equal(output[900:], before[900:])
    assert int(np.count_nonzero(output != before)) > 100
    with Session(engine) as db:
        version = db.query(RrugcImageOutputVersionModel).one()
        metadata = version.font_info_json["thread_inpaint_pipeline"]
        assert metadata["version"] == PIPELINE_VERSION
        assert metadata["material_source"] == "original-stage1-transparent-design"
        assert metadata["material_remote_file_id"] is None
        assert metadata["profile_sha256"] == load_profile()[1]
        assert metadata["uv_center"] == [.5, .5]
        assert metadata["visible_pixels"] > 100
        assert metadata["native_passes"] == 0
        assert metadata["auto_rotate"] is False
        assert metadata["auto_bend"] is False
        assert len(metadata["shared_atlas_sha256"]) == 64
        row = db.get(RrugcEcommerceJobModel, "uv-job")
        assert row.status == "completed"
        assert row.front_preview_remote_file_id is None
    engine.dispose()
