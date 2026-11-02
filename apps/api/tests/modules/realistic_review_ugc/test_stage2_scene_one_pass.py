"""New Products scene jobs render once with pinned reference, not twice."""
import asyncio
import hashlib
from io import BytesIO
from types import SimpleNamespace

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import (
    RrugcEcommerceJobModel, RrugcSourcePlanModel, RrugcImageOutputVersionModel,
)


def png(im):
    out=BytesIO()
    im.save(out,format="PNG")
    return out.getvalue()


@pytest.mark.parametrize("stage", ["prod_s2_mh_1", "prod_s2_ugc_1"])
def test_one_scene_call_pinned_material_and_immutable_history(monkeypatch,stage):
    source=Image.new("RGBA",(700,600),(0,0,0,0))
    ImageDraw.Draw(source).rectangle((60,140,630,300),fill=(17,30,125,255))
    design=png(source)
    ref=Image.new("RGBA",(700,600),(0,0,0,0))
    drawing=ImageDraw.Draw(ref)
    drawing.rectangle((60,140,630,300),fill=(20,46,160,255))
    for x in range(62,630,4):
        drawing.line((x,140,x,300),fill=(36,67,183,255),width=1)
    macro=png(ref)
    scene_png=png(Image.new("RGB",(900,1000),(224,213,196)))

    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool,
                         connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine,tables=[
        ProcessingJobModel.__table__,RrugcSourcePlanModel.__table__,
        RrugcEcommerceJobModel.__table__,RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan",tenant_id="tenant",root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design",source_name="design.png",source_relative_path="design.png",
            source_revision="rev",source_mime_type="image/png",
            created_by_user_id="actor",
        ))
        db.add(RrugcEcommerceJobModel(
            id="shot",tenant_id="tenant",source_plan_id="plan",source_revision="rev",
            stage=stage,color_key="natural-black",stock_sha256="a"*64,
            front_preview_remote_file_id="macro",front_preview_content_type="image/png",
            front_preview_design_sha256=hashlib.sha256(design).hexdigest(),
            front_preview_size_bytes=len(macro),
            front_reference_remote_file_id="front",front_reference_content_type="image/png",
            front_reference_size_bytes=len(scene_png),
            skill_source="local",skill_name="scene-skill",skill_version=None,
            skill_bundle_sha256="f"*64,created_by_user_id="actor",status="queued",
        ))
        db.commit()

    monkeypatch.setattr(pipeline,"_pinned_stock_refs",lambda *_:(
        [("stock_front",scene_png),("stock_side_90",scene_png),("stock_back",scene_png)],False))
    monkeypatch.setattr(pipeline,"verify_stage2_skill_runtime",lambda **kwargs:None)
    monkeypatch.setattr(pipeline,"installed_stage2_skill_sha256",lambda *a,**kw:"f"*64)
    if stage.startswith("prod_s2_mh"):
        monkeypatch.setattr(pipeline,"validate_pinned_multihat_references",
                            lambda refs: [{"image_id":"scout-a"}])
        async def sheets(_refs):
            return [],3
        monkeypatch.setattr(pipeline,"build_multihat_reference_sheets",sheets)
    else:
        monkeypatch.setattr(pipeline,"allowed_scout_image_url",lambda url:True)
        class Client:
            def __init__(self,**kwargs):pass
            async def __aenter__(self):return self
            async def __aexit__(self,*args):return False
            async def get(self,url):
                return SimpleNamespace(
                    content=scene_png,headers={"content-type":"image/png"},
                    raise_for_status=lambda:None,
                )
        monkeypatch.setattr(pipeline.httpx,"AsyncClient",Client)
    pool_calls=[]
    from contextlib import contextmanager
    @contextmanager
    def pool(*args,**kwargs):
        pool_calls.append(1)
        yield "account","/fake/home"
    monkeypatch.setattr(pipeline,"acquire_worker_profile",pool)
    calls=[]
    class Runner:
        def __init__(self,config):
            self.config=config
        async def generate_from_references(self,**kwargs):
            calls.append(kwargs)
            assert self.config.output_contract=="single_png"
            assert any(ref.role=="slot1_thread_material_reference" for ref in kwargs["references"])
            assert "SINGLE GENERATION" in kwargs["prompt"]
            return SimpleNamespace(image_bytes=scene_png,mime_type="image/png",
                                   model="test",provider_request_id=None,
                                   provider_metadata={"native_sha256":hashlib.sha256(scene_png).hexdigest()})
        def cleanup_attempt(self,attempt_id):pass
    monkeypatch.setattr(pipeline,"CodexImageGenRunner",Runner)

    class Storage:
        def __init__(self):self.stores=0
        async def open_asset(self,request):
            data={"design":design,"macro":macro,"front":scene_png}[request.remote_file_id]
            async def body(): yield data
            async def close():pass
            return SimpleNamespace(body=body(),close=close)
        async def store_asset(self,request):
            self.stores+=1
            return SimpleNamespace(remote_file_id="saved-scene")
    storage=Storage()
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all,"advance_after_output",lambda *a,**kw:None)
    payload={"ecommerce_job_id":"shot","thread_material_pipeline":"v2"}
    payload.update({"multihat_references":[{"image_id":"scout-a"}]} if stage.startswith("prod_s2_mh")
                   else {"scout_reference":{"image_url":"https://example.test/ugc.jpg"}})
    ctx=SimpleNamespace(job=SimpleNamespace(job_type=pipeline.JOB_TYPE,id="processing",
                        entity_id="shot",tenant_id="tenant",payload=payload),
                        dependencies=SimpleNamespace(session_factory=lambda:Session(engine),
                        storage_provider=storage),is_cancelled=False,
                        logger=SimpleNamespace(info=lambda *a,**kw:None,exception=lambda *a,**kw:None))
    settings=SimpleNamespace(PROCESSING_JOBS_ENABLED=True,IMAGE_GENERATION_ENABLED=True,
         MANAGED_ASSET_STORAGE_ENABLED=True,CODEX_IMAGE_GENERATION_ENABLED=True,
         CODEX_IMAGE_BINARY="codex",CODEX_IMAGE_HOME="/mock/home",
         IMAGE_GENERATION_STAGING_ROOT="/mock/temp",CODEX_IMAGE_TIMEOUT_SECONDS=800,
         CODEX_IMAGE_MODEL=None)
    result=asyncio.run(pipeline.EcommerceGenerateHandler(settings)._execute(ctx))
    assert result.outcome.value=="completed"
    assert len(calls)==len(pool_calls)==storage.stores==1
    with Session(engine) as db:
        version=db.query(RrugcImageOutputVersionModel).first()
        assert version.font_info_json["thread_inpaint_pipeline"]["version"]=="scene-single-pass-v2"
        assert version.font_info_json["thread_inpaint_pipeline"]["scene_photo_preserved_by_edit_gate"] is False
        assert db.get(RrugcEcommerceJobModel,"shot").front_preview_remote_file_id=="macro"
    engine.dispose()
