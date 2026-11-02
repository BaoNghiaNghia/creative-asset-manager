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
    assert pin["version"] == "front-guide-v2"
    assert pin["safe_box"] == [.23, .34, .77, .56]
    fitted = front_transfer_box(pin, 3.3, (1000, 1200))
    center = ((fitted[0]+fitted[2])/2/1000, (fitted[1]+fitted[3])/2/1200)
    assert center == pytest.approx((.50, .45), abs=.002)
    assert .46 < (fitted[2]-fitted[0])/1000 < .50
    assert fitted[1] >= 1200*.34
    assert fitted[3] <= 1200*.56
    old = {
        "version": "front-locked-v1", "center_x": .50, "center_y": .44,
        "target_width": .38, "max_height": .20, "safe_box": [.145,.245,.855,.62],
    }
    assert (front_transfer_box(old, 3.3, (1000,1200))[2]
            - front_transfer_box(old, 3.3, (1000,1200))[0]) <= 380


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
        assert info["thread_inpaint_pipeline"]["version"] == "threadtransfer-v3-deterministic-quad"
        assert info["thread_inpaint_pipeline"]["native_passes"] == 0
        row = db.get(RrugcEcommerceJobModel, "job")
        assert row.status == "completed"
        assert row.front_preview_remote_file_id == "macro"
    engine.dispose()
