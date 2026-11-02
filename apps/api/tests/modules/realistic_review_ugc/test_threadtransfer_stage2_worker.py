"""Products Stage 2 local stock worker contract; never dispatch Codex."""
import asyncio
import hashlib
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
from app.modules.realistic_review_ugc import ecommerce_pipeline as pipeline
from app.modules.realistic_review_ugc.model import (
    RrugcSourcePlanModel, RrugcEcommerceJobModel, RrugcImageOutputVersionModel,
)
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT


def png(image):
    buffer=BytesIO()
    image.save(buffer,"PNG")
    return buffer.getvalue()


def assets():
    stock=Image.new("RGB",(900,1000),(229,218,203))
    draw=ImageDraw.Draw(stock)
    draw.line((30,80,700,950),fill=(30,43,76),width=4)
    design=Image.new("RGBA",(700,600),(0,0,0,0))
    d=ImageDraw.Draw(design)
    d.rectangle((70,100,620,235),fill=(13,30,128,255))
    material=Image.new("RGBA",(700,600),(0,0,0,0))
    t=ImageDraw.Draw(material)
    t.rectangle((70,100,620,235),fill=(15,37,139,255))
    for x in range(70,620,3):
        t.line((x,100,x,235),fill=(28,70,175,255),width=1)
    return png(stock),png(design),png(material)


class MemoryStorage:
    def __init__(self,store):
        self.store=dict(store)
        self.puts=[]
    async def open_asset(self, request):
        assert request.remote_file_id in self.store
        data=self.store[request.remote_file_id]
        async def stream():
            yield data
        async def close():
            pass
        return SimpleNamespace(body=stream(),close=close)
    async def store_asset(self,request):
        buf=bytearray()
        async for chunk in request.body:
            buf.extend(chunk)
        key="saved-output"
        self.store[key]=bytes(buf)
        self.puts.append(key)
        return SimpleNamespace(remote_file_id=key)


@pytest.mark.parametrize("stage",["prod_s2_ec2","prod_s2_ec3"])
def test_new_products_angle_worker_stores_local_transfer_without_codex(monkeypatch,stage):
    stock,design,material=assets()
    engine=create_engine("sqlite+pysqlite:///:memory:",poolclass=StaticPool,
                         connect_args={"check_same_thread":False})
    Base.metadata.create_all(engine,tables=[
        ProcessingJobModel.__table__, RrugcSourcePlanModel.__table__,
        RrugcEcommerceJobModel.__table__,RrugcImageOutputVersionModel.__table__,
    ])
    with Session(engine) as db:
        db.add(RrugcSourcePlanModel(
            id="plan",tenant_id="tenant",root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design",source_name="design.png",
            source_relative_path="design.png",source_revision="rev",
            source_mime_type="image/png",created_by_user_id="actor",
        ))
        db.add(RrugcEcommerceJobModel(
            id="job",tenant_id="tenant",source_plan_id="plan",
            source_revision="rev",stage=stage,
            color_key="natural-black",stock_sha256="a"*64,
            skill_source="internal",skill_name=pipeline.SLOT1_DIRECT_NAME,
            skill_version=pipeline.SLOT1_DIRECT_VERSION,
            skill_bundle_sha256=pipeline.SLOT1_DIRECT_SHA256,
            front_reference_remote_file_id="front",
            front_reference_content_type="image/png",
            front_reference_size_bytes=len(stock),
            front_preview_remote_file_id="material",
            front_preview_content_type="image/png",
            front_preview_size_bytes=len(material),
            front_preview_design_sha256=hashlib.sha256(design).hexdigest(),
            created_by_user_id="actor",status="queued",
        ))
        db.commit()
    storage=MemoryStorage({"design":design,"front":stock,"material":material})
    monkeypatch.setattr(pipeline,"_pinned_stock_refs",
        lambda *args: ([("stock_three_quarter" if stage=="prod_s2_ec2" else "stock_side_90",stock)],False))
    def fail(*args,**kwargs):
        raise AssertionError("Codex must never be acquired in ThreadTransfer angle")
    monkeypatch.setattr(pipeline,"acquire_worker_profile",fail)
    monkeypatch.setattr(pipeline,"CodexImageGenRunner",fail)
    from app.modules.realistic_review_ugc import products_run_all
    monkeypatch.setattr(products_run_all,"advance_after_output",lambda *a,**kw:None)
    settings=SimpleNamespace(
        PROCESSING_JOBS_ENABLED=True,IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True,CODEX_IMAGE_GENERATION_ENABLED=True,
    )
    context=SimpleNamespace(
        job=SimpleNamespace(
            job_type=pipeline.JOB_TYPE,entity_id="job",tenant_id="tenant",
            id="processing-id",
            payload={"ecommerce_job_id":"job","thread_material_pipeline":"v2"},
        ),
        dependencies=SimpleNamespace(session_factory=lambda:Session(engine),
                                     storage_provider=storage),
        is_cancelled=False,
        logger=SimpleNamespace(info=lambda *a,**kw:None,exception=lambda *a,**kw:None),
    )
    result=asyncio.run(pipeline.EcommerceGenerateHandler(settings)._execute(context))
    assert result.outcome.value=="completed"
    assert len(storage.puts)==1
    with Session(engine) as db:
        row=db.get(RrugcEcommerceJobModel,"job")
        assert row.status=="completed"
        assert row.output_remote_file_id=="saved-output"
        assert row.front_preview_remote_file_id=="material"
        assert db.query(RrugcImageOutputVersionModel).count()==1
    with Image.open(BytesIO(storage.store["saved-output"])) as output:
        after=np.asarray(output.convert("RGB"))
    original=np.asarray(Image.open(BytesIO(stock)).convert("RGB"))
    assert np.count_nonzero(np.any(after!=original,axis=2))>80
    engine.dispose()
