"""Metadata inventory and exact original preflight (no production writes)."""
import asyncio, hashlib, io
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from fastapi import HTTPException, Response

from app.core.database import Base
from app.modules.image_orchestrator import candidate_discovery as c
from app.modules.image_orchestrator.model import ImageBatch, ImagePack
from app.modules.etsy_spy.model import (EtsySpyPicked as Picked,
    EtsySpyListingImage as Gallery, EtsySpyListingImageDeletion as Deleted)
from app.modules.processing.model import ProcessingJobModel as Job

TENANT="image-candidate-fixture"
L1="123456789"
L2="123456790"
ART="a"*64
SRC="b"*64
NOW=datetime.now(timezone.utc)

def master(listing,number,sha=ART,position="front",verified=True,tenant=TENANT,
           status="completed",retry=False):
    a={"id":f"{number:024x}","artwork_kind":"native_master_v2","method":"imagegen",
        "position":position,"position_verified":verified,"output_sha256":sha,
        "source_sha256":SRC,"storage_provider":"google_drive_managed",
        "drive_file_id":"fixture_original_1"}
    payload={"design_assets":[a]}
    if retry: payload["retry"]={"explicit":True}
    return Job(id=f"00000000-0000-0000-0000-{number:012d}",tenant_id=tenant,
        job_type="etsy_design_extract",entity_type="etsy_listing",entity_id=listing,
        status=status,idempotency_key=f"{listing}-{number}",payload_json=payload,
        created_at=NOW+timedelta(seconds=number))

@pytest.fixture
def db():
    engine=create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[Picked.__table__,Gallery.__table__,
        Deleted.__table__,Job.__table__,ImageBatch.__table__,ImagePack.__table__])
    with Session(engine) as db:
        db.add_all([Picked(tenant_id=TENANT,listing_key=L1),
            Picked(tenant_id=TENANT,listing_key=L2),Picked(tenant_id="other",listing_key=L1)])
        db.add_all([master(L1,1),master(L2,2),master(L1,3,tenant="other",sha="f"*64)])
        db.commit()
        yield db
    engine.dispose()

def test_new_candidates_and_tenant_pagination(db):
    first=c.discover(db,TENANT,limit=1)
    assert first["nextAfter"]==L1
    assert first["candidateItems"][0]["artworkSha256"]==ART
    assert first["candidateItems"][0]["missingRoles"]==list(range(1,14))
    assert first["claimAllowed"] is False
    assert first["candidateItems"][0]["originalBytesVerified"] is False
    assert c.discover(db,TENANT,after=L1,limit=1)["nextAfter"] is None
    assert c.discover(db,"other")["candidateItems"][0]["artworkSha256"]=="f"*64

def test_newer_pending_retry_not_stale_fallback(db):
    db.add(master(L1,4,sha="e"*64,status="pending",retry=True));db.commit()
    assert c.inspect_listing(db,TENANT,L1)["reason"]=="LATEST_MASTER_NOT_VERIFIED_FRONT"

def test_unverified_position_denied(db):
    db.add(master(L1,4,verified=False));db.commit()
    assert c.inspect_listing(db,TENANT,L1)["reason"]=="NATIVE_MASTER_DESCRIPTOR_UNVERIFIED"

def test_existing_gallery_never_admitted_and_packs_not_merged(db):
    for n,key in ((1,"sp-"+"a"*40),(2,"sp-"+"b"*40)):
        db.add(Gallery(tenant_id=TENANT,listing_key=L1,pack_id=key,slot=n,
            garment_code="C1717",placement="front",design_sha256=ART,source_sha256=SRC,
            output_sha256="e"*64,thumbnail_sha256="f"*64,
            drive_folder_id="fixture_folder",drive_thumbnail_folder_id="fixture_thumbs",
            drive_file_id="fixture_output",drive_thumbnail_id="fixture_thumbnail"))
    db.commit()
    item=c.inspect_listing(db,TENANT,L1)
    assert item["reason"]=="EXISTING_GALLERY_REQUIRES_RECONCILIATION"
    assert item["legacyCurrentPacks"][0]["storedRoles"]==[1]
    assert item["legacyCurrentPacks"][1]["storedRoles"]==[2]
    assert item["legacyReceiptsAreCentralVerified"] is False

def test_deletion_is_not_recreated(db):
    db.add(Deleted(tenant_id=TENANT,listing_key=L1,pack_id="sp-"+"a"*40,
        slot=1,output_sha256="e"*64,deleted_at=NOW))
    db.commit()
    assert c.inspect_listing(db,TENANT,L1)["reason"]=="DELETION_REQUIRES_RECONCILIATION"

def test_original_exact_bytes_and_no_claim(db):
    out=io.BytesIO()
    Image.new("RGBA",(24,32),(255,255,255,0)).save(out,format="PNG")
    raw=out.getvalue();sha=hashlib.sha256(raw).hexdigest()
    db.add(master(L1,4,sha=sha));db.commit()
    response=Response(raw,media_type="image/png",headers={"X-CAM-Artwork-Variant":"original"})
    with patch("app.modules.muse_bridge.router._read_original_artwork",new=AsyncMock(return_value=response)):
        proof=asyncio.run(c.verify_original(db,TENANT,L1,sha))
        assert proof["originalBytesVerified"] and proof["nativeWidth"]==24
        assert proof["sourceVisualQaVerified"] is False and proof["claimAllowed"] is False
        with pytest.raises(HTTPException): asyncio.run(c.verify_original(db,TENANT,L1,ART))
    bad=Response(raw,media_type="image/png",headers={"X-CAM-Artwork-Variant":"thumbnail"})
    with patch("app.modules.muse_bridge.router._read_original_artwork",new=AsyncMock(return_value=bad)):
        with pytest.raises(HTTPException): asyncio.run(c.verify_original(db,TENANT,L1,sha))
