"""Slot 1 ThreadTransfer never starts a second ImageGen call."""
import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace

from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
from app.core.database import Base
from app.modules.image_generation.providers import GeneratedImageResult, PreparedImage, ReferenceImageInput
from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel, RrugcSourcePlanModel
from app.modules.realistic_review_ugc.slot1_embroidery import build_slot1_material_pass


def png(img):
    out = BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def inputs():
    stock = Image.new("RGB", (800, 1000), (232, 218, 205))
    art = Image.new("RGBA", (500, 350), (0, 0, 0, 0))
    ImageDraw.Draw(art).rectangle((45, 80, 450, 150), fill=(7, 44, 130, 255))
    design = png(art)
    ref = Image.new("RGBA", (680, 600), (0, 0, 0, 0))
    d = ImageDraw.Draw(ref)
    d.rectangle((55, 185, 625, 275), fill=(10, 47, 139, 255))
    # Directional yarn fibers, not a flat print.
    for x in range(58, 624, 3):
        d.line((x, 185, x, 274), fill=(28, 74, 171, 255), width=1)
    ref_bytes = png(ref)
    return (
        stock, design, ref_bytes,
        build_slot1_material_pass(png(stock), design, {},
                                   contact_width_px=3, localized_edit=True),
    )


class Storage:
    def __init__(self, reference=None):
        self.data = {"saved-reference": reference} if reference else {}
        self.stores = 0

    async def open_asset(self, request):
        async def body():
            yield self.data[request.remote_file_id]
        async def close():
            pass
        return SimpleNamespace(body=body(), close=close)

    async def store_asset(self, request):
        content = bytearray()
        async for chunk in request.body:
            content.extend(chunk)
        self.stores += 1
        key = "new-reference"
        self.data[key] = bytes(content)
        return SimpleNamespace(remote_file_id=key)


def setup(reference: bytes | None):
    engine = create_engine("sqlite+pysqlite:///:memory:", poolclass=StaticPool,
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
    ])
    stock, design, macro, material = inputs()
    with Session(engine) as session:
        session.add(RrugcSourcePlanModel(
            id="plan", tenant_id="tenant", root_folder_id="root",
            source_file_id="source", source_name="design.png",
            source_relative_path="design.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="operator",
        ))
        session.add(RrugcEcommerceJobModel(
            id="slot1", tenant_id="tenant", source_plan_id="plan",
            source_revision="rev", stage="prod_s2_ec1",
            stock_sha256="a" * 64, created_by_user_id="operator",
            skill_source="internal", skill_name=pipeline.SLOT1_DIRECT_NAME,
            skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256, status="running",
            front_preview_remote_file_id="saved-reference" if reference else None,
            front_preview_content_type="image/png" if reference else None,
            front_preview_size_bytes=len(reference) if reference else None,
            front_preview_design_sha256=hashlib.sha256(design).hexdigest() if reference else None,
        ))
        session.commit()
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant", entity_id="slot1", payload={}),
        dependencies=SimpleNamespace(session_factory=lambda: Session(engine)),
    )
    reference_input = ReferenceImageInput(
        image=PreparedImage(image_bytes=design, mime_type="image/png",
                            width=500, height=350),
        role="design_reference", label="source",
    )
    return engine, ctx, stock, design, macro, material, [reference_input]


def kwargs(ctx, design, material, refs, saved):
    return dict(context=ctx, storage=saved, person=refs[0].image,
                references=refs, prompt="Generate a macro of embroidered text",
                source_revision="rev", design_sha=hashlib.sha256(design).hexdigest(),
                folder_id=None, preview_remote_id="saved-reference" if saved.stores == 0 and saved.data else None,
                preview_mime="image/png", preview_size=len(next(iter(saved.data.values()))) if saved.data else None,
                preview_design_sha=hashlib.sha256(design).hexdigest() if saved.data else None,
                transfer_material=material, transfer_design_size=100)


def test_saved_reference_retry_has_zero_model_calls_and_no_new_storage():
    stock, design, macro, material = inputs()
    engine, ctx, stock, design, macro, material, refs = setup(macro)
    storage = Storage(macro)
    result, remote, size = asyncio.run(
        pipeline.EcommerceGenerateHandler()._checkpointed_slot1_generate(
            runner=None, **kwargs(ctx, design, material, refs, storage),
        )
    )
    assert remote == "saved-reference" and size == len(macro)
    assert result.provider_metadata["native_passes"] == 0
    assert result.provider_metadata["reference_reused"] is True
    assert result.provider_metadata["transfer_engine"] == "threadtransfer-v1"
    assert storage.stores == 0
    with Image.open(BytesIO(result.image_bytes)) as image:
        assert image.size == stock.size
    engine.dispose()


def test_first_reference_generates_once_then_transfers_locally(monkeypatch):
    engine, ctx, stock, design, macro, material, refs = setup(None)
    storage = Storage()
    calls = []

    class FakeRunner:
        def __init__(self, config):
            self.config = config
        async def generate_from_references(self, **params):
            calls.append(self.config.output_contract)
            assert not params["references"]
            return GeneratedImageResult(
                provider="codex", model="test", image_bytes=macro,
                mime_type="image/png",
                provider_metadata={"native_sha256": hashlib.sha256(macro).hexdigest()},
            )
        def cleanup_attempt(self, _attempt_id):
            pass

    monkeypatch.setattr(pipeline, "CodexImageGenRunner", FakeRunner)
    fake = FakeRunner(pipeline.CodexImageRunnerConfig(skill_name=pipeline.SLOT1_DIRECT_NAME))
    result, remote, size = asyncio.run(
        pipeline.EcommerceGenerateHandler()._checkpointed_slot1_generate(
            runner=fake, **kwargs(ctx, design, material, refs, storage),
        )
    )
    assert calls == ["slot1_v2_material_single"]
    assert remote == "new-reference" and size == len(macro)
    assert result.provider_metadata["native_passes"] == 1
    assert result.provider_metadata["reference_reused"] is False
    with Session(engine) as db:
        pinned = db.get(RrugcEcommerceJobModel, "slot1")
        assert pinned.front_preview_remote_file_id == remote
        assert pinned.front_preview_design_sha256 == hashlib.sha256(design).hexdigest()
    engine.dispose()
