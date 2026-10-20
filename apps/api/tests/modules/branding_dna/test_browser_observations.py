"""Browser reports remain quarantined, replay-safe and tenant-scoped."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.browser_observation_model import BrandBrowserObservationModel
from app.modules.branding_dna.browser_observation_router import (
    BrowserObservationInput, list_browser_observations,
    read_browser_observation, submit_browser_observation,
)
from app.modules.branding_dna.router import ShopCreate, add_shop


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[
        BrandShopModel.__table__, BrandBrowserObservationModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def principal(tenant="tenant-a",actor="operator"):
    return SimpleNamespace(active_tenant_id=tenant,actor_id=actor)


def make_shop(db, name="PilotCaps", tenant="tenant-a", active=True,access="owned"):
    row=add_shop(ShopCreate(platform="etsy",
        storefront_url=f"https://www.etsy.com/shop/{name}",
        access_mode=access),session=db,principal=principal(tenant))
    if active:
        shop=db.get(BrandShopModel,row["id"])
        shop.status="active"
        db.commit()
    return row


def payload(shop, **changes):
    url_hash=sha256(shop["storefront_url"].encode()).hexdigest()
    data=dict(
        report_key="pilot-20261020-123456789abc",
        status="preview_completed",platform="etsy",
        shop_url_sha256=url_hash,
        observed_at=datetime.now(timezone.utc),
        items=[{
            "listing_key":"etsy:123456","title":"Embroidered Cap",
            "observed_page_sha256":"a"*64,
            "image_url_sha256":["b"*64,"c"*64],
            "image_count":2,"source_kind":"browser_dom_observation",
            "rights_state":"unknown","sales_state":"unknown",
            "coverage_state":"partial","source_verification":"unverified",
        }],
        observed_count=1,coverage_state="partial",
        rights_verified=False,sales_verified=False,
        evidence_committed=False,scan_enabled=False,
    )
    data.update(changes)
    return BrowserObservationInput(**data)


def upload(db,shop,body,tenant="tenant-a"):
    return submit_browser_observation(shop["id"],body,Response(),
                                     session=db,principal=principal(tenant))


def test_ingestion_keeps_data_unverified_and_does_not_spawn_jobs(db):
    shop=make_shop(db)
    result=upload(db,shop,payload(shop))
    assert not result["replayed"]
    assert result["item"]["evidence_state"]=="browser_observation_unverified"
    assert result["item"]["rights_verified"] is False
    assert result["item"]["scan_enabled"] is False
    assert result["item"]["dispatchable"] is False
    view=list_browser_observations(shop["id"],Response(),session=db,
                                   principal=principal(),limit=25,offset=0)
    assert view["count"]==1
    details=read_browser_observation(shop["id"],result["item"]["id"],Response(),
                                     session=db,principal=principal())
    assert details["items"][0]["listing_key"]=="etsy:123456"
    assert details["items"][0]["image_url_sha256"]==["b"*64,"c"*64]
    assert details["source_verification"]=="unverified"
    assert details["media_available"] is False
    assert "storefront_url" not in str(details)


def test_idempotent_replay_and_conflict(db):
    shop=make_shop(db)
    body=payload(shop)
    first=upload(db,shop,body)
    replay=upload(db,shop,body)
    assert replay["replayed"] is True
    assert replay["item"]["id"]==first["item"]["id"]
    new=payload(shop)
    new.items[0].title="Changed Title"
    with pytest.raises(HTTPException) as err:
        upload(db,shop,new)
    assert err.value.status_code==409
    assert db.query(BrandBrowserObservationModel).count()==1


def test_other_tenants_cannot_read_or_import(db):
    shop=make_shop(db)
    uploaded=upload(db,shop,payload(shop))
    with pytest.raises(HTTPException) as err:
        upload(db,shop,payload(shop),tenant="tenant-b")
    assert err.value.status_code==404
    with pytest.raises(HTTPException) as err:
        list_browser_observations(shop["id"],Response(),session=db,
                                  principal=principal("tenant-b"),limit=25,offset=0)
    assert err.value.status_code==404
    with pytest.raises(HTTPException) as err:
        read_browser_observation(shop["id"],uploaded["item"]["id"],Response(),
                                 session=db,principal=principal("tenant-b"))
    assert err.value.status_code==404


def test_draft_unowned_and_shop_url_mismatch_rejected(db):
    shop=make_shop(db,active=False)
    with pytest.raises(HTTPException) as err: upload(db,shop,payload(shop))
    assert err.value.status_code==403
    other=make_shop(db,name="UnreviewedCaps",access="public_observation")
    with pytest.raises(HTTPException) as err: upload(db,other,payload(other))
    assert err.value.status_code==403
    shop=db.get(BrandShopModel,shop["id"])
    shop.status="active"
    db.commit()
    with pytest.raises(HTTPException) as err:
        upload(db,dict(id=shop.id,storefront_url=shop.storefront_url),
               payload(dict(id=shop.id,storefront_url=shop.storefront_url),
                       shop_url_sha256="a"*64))
    assert err.value.status_code==409
    assert db.query(BrandBrowserObservationModel).count()==0


def test_unverified_and_malformed_reports_fail_closed(db):
    shop=make_shop(db)
    base=payload(shop).model_dump()
    variants=[
        {"rights_verified":True},{"sales_verified":True},{"scan_enabled":True},
        {"evidence_committed":True},{"observed_count":2},{"status":"needs_operator"},
        {"observed_at":datetime.now(timezone.utc)+timedelta(days=3)},
        {"observed_at":datetime.now(timezone.utc)-timedelta(days=45)},
        {"items":[{**base["items"][0],"source_verification":"verified"}]},
        {"items":[{**base["items"][0],"image_count":5}]},
        {"items":[{**base["items"][0],"listing_key":"amazon:ABCDEF1234"}]},
        {"items":[{**base["items"][0],"image_url_sha256":["b"*64]*2}]},
        {"items":base["items"]*6,"observed_count":6},
        {"extra_field":"untrusted"},
    ]
    for variant in variants:
        try:
            model=BrowserObservationInput(**{**base,**variant})
        except ValidationError:
            continue
        with pytest.raises(HTTPException):
            upload(db,shop,model)
    assert db.query(BrandBrowserObservationModel).count()==0
