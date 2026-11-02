"""Pass A must survive native Pass B failure and be reused without new tokens."""
import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

import app.modules.realistic_review_ugc.ecommerce_pipeline as pipeline
from app.core.database import Base
from app.modules.image_generation.providers import PreparedImage, ReferenceImageInput
from app.modules.realistic_review_ugc.model import RrugcEcommerceJobModel, RrugcSourcePlanModel


def png(color, size=(600, 600)):
    out = BytesIO()
    Image.new("RGB", size, color).save(out, format="PNG")
    return out.getvalue()


class MemStorage:
    def __init__(self):
        self.data = {}
        self.stores = 0

    async def store_asset(self, request):
        result = bytearray()
        async for data in request.body:
            result.extend(data)
        self.stores += 1
        remote = "reference-" + str(self.stores)
        self.data[remote] = bytes(result)
        return SimpleNamespace(remote_file_id=remote)

    async def open_asset(self, request):
        async def chunks():
            yield self.data[request.remote_file_id]
        async def close():
            pass
        return SimpleNamespace(body=chunks(), close=close)


def test_slot1_checkpoint_before_inpaint_and_reuse_on_retry_and_force_new(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine, tables=[
        RrugcSourcePlanModel.__table__, RrugcEcommerceJobModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan", tenant_id="tenant", root_folder_id="root",
            source_file_id="source", source_name="Cowboy.png",
            source_relative_path="Cowboy.png", source_revision="rev",
            source_mime_type="image/png", created_by_user_id="operator",
        ))
        db.add(RrugcEcommerceJobModel(
            id="slot1", tenant_id="tenant", source_plan_id="plan",
            source_revision="rev", stage="prod_s2_ec1",
            stock_sha256="a" * 64,
            skill_source="internal", skill_name=pipeline.SLOT1_DIRECT_NAME,
            skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256,
            created_by_user_id="operator", status="running",
        ))
        db.commit()

    def factory():
        return Session(engine)
    ctx = SimpleNamespace(
        job=SimpleNamespace(tenant_id="tenant", entity_id="slot1", payload={}),
        dependencies=SimpleNamespace(session_factory=factory),
    )
    storage = MemStorage()
    design = png((12, 30, 115))
    design_sha = hashlib.sha256(design).hexdigest()
    plain = png((246, 230, 211))
    refs = [
        ReferenceImageInput(
            image=PreparedImage(image_bytes=design, mime_type="image/png",
                                width=600, height=600),
            role="design_reference", label="design",
        ),
        ReferenceImageInput(
            image=PreparedImage(image_bytes=plain, mime_type="image/png",
                                width=600, height=600),
            role="slot1_edit_mask", label="mask",
        ),
        ReferenceImageInput(
            image=PreparedImage(image_bytes=plain, mime_type="image/png",
                                width=600, height=600),
            role="slot1_layout_guide", label="guide",
        ),
    ]
    calls = []
    fail_first_cap = True

    class Runner:
        def __init__(self, config):
            self.config = config

        async def generate_from_references(self, *, person, references, **kwargs):
            nonlocal fail_first_cap
            calls.append((self.config.output_contract, [x.role for x in references]))
            if self.config.output_contract == "slot1_v2_cap_single":
                assert len(references) == 4
                assert references[-1].image.image_bytes in storage.data.values()
                with Session(engine) as db:
                    checkpoint = db.get(RrugcEcommerceJobModel, "slot1")
                    assert checkpoint.front_preview_remote_file_id in storage.data
                    assert checkpoint.front_preview_design_sha256 == design_sha
                if fail_first_cap:
                    fail_first_cap = False
                    raise ValueError("Simulated failed Pass B QA")
                photo = png((45, 87, 136))
            else:
                assert self.config.output_contract == "slot1_v2_material_single"
                assert references == []
                photo = png((24, 65, 112 + storage.stores))
            return SimpleNamespace(
                image_bytes=photo, mime_type="image/png", model="fake",
                provider_request_id=None,
                provider_metadata={"native_sha256": hashlib.sha256(photo).hexdigest()},
            )

        def cleanup_attempt(self, _attempt):
            pass

    monkeypatch.setattr(pipeline, "CodexImageGenRunner", Runner)
    handler = pipeline.EcommerceGenerateHandler()
    runner = Runner(pipeline.CodexImageRunnerConfig(
        output_contract="slot1_reference_inpaint_v2",
        skill_name=pipeline.SLOT1_DIRECT_NAME,
    ))
    kwargs = dict(
        context=ctx, storage=storage, runner=runner, person=refs[1].image,
        references=refs, prompt="Embroidery reference then inpaint",
        source_revision="rev", design_sha=design_sha, folder_id=None,
        preview_remote_id=None, preview_mime=None, preview_size=None,
        preview_design_sha=None,
    )

    with pytest.raises(ValueError, match="failed Pass B"):
        asyncio.run(handler._checkpointed_slot1_generate(**kwargs))
    with Session(engine) as db:
        saved = db.get(RrugcEcommerceJobModel, "slot1")
        checkpoint = saved.front_preview_remote_file_id
        assert checkpoint == "reference-1"
        assert saved.output_remote_file_id is None
    assert calls == [
        ("slot1_v2_material_single", []),
        ("slot1_v2_cap_single", [
            "design_reference", "slot1_edit_mask", "slot1_layout_guide",
            "slot1_thread_material_reference",
        ]),
    ]
    assert storage.stores == 1

    photo, reused_id, _ = asyncio.run(handler._checkpointed_slot1_generate(
        **{**kwargs, "preview_remote_id": checkpoint, "preview_mime": "image/png",
           "preview_size": len(storage.data[checkpoint]), "preview_design_sha": design_sha},
    ))
    assert reused_id == checkpoint
    assert photo.provider_metadata["reference_reused"] is True
    assert photo.provider_metadata["reference_sha256"] == hashlib.sha256(
        storage.data[checkpoint],
    ).hexdigest()
    assert calls[-1][0] == "slot1_v2_cap_single"
    assert [name for name, _ in calls].count("slot1_v2_material_single") == 1
    assert storage.stores == 1

    ctx.job.payload["regenerate_material_reference"] = True
    photo, changed_id, _ = asyncio.run(handler._checkpointed_slot1_generate(
        **{**kwargs, "preview_remote_id": checkpoint, "preview_mime": "image/png",
           "preview_size": len(storage.data[checkpoint]), "preview_design_sha": design_sha},
    ))
    assert changed_id != checkpoint
    assert photo.provider_metadata["reference_reused"] is False
    assert storage.stores == 2
    with Session(engine) as db:
        assert db.get(RrugcEcommerceJobModel, "slot1").front_preview_remote_file_id == changed_id
    engine.dispose()
