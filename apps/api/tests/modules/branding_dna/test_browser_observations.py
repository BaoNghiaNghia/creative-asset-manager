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
from app.modules.branding_dna.browser_observation_review_model import BrandBrowserObservationReviewModel
from app.modules.branding_dna.browser_observation_review_router import (
    BrowserObservationReviewInput, list_browser_observation_reviews,
    submit_browser_observation_review, browser_observation_review_queue,
)
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
        BrandBrowserObservationReviewModel.__table__,
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


def review(db,shop_id,report_id, *, actor="reviewer",tenant="tenant-a",**changes):
    data={
        "decision":"needs_authorized_source", "rights_basis":"unknown",
        "note":"Original marketplace export needed before verified claims",
    }
    data.update(changes)
    return submit_browser_observation_review(
        shop_id,report_id,BrowserObservationReviewInput(**data),Response(),
        session=db,principal=principal(tenant,actor),
    )


def test_independent_review_immutable_history_and_replay(db):
    shop=make_shop(db)
    report=upload(db,shop,payload(shop))["item"]["id"]
    with pytest.raises(HTTPException) as err:
        review(db,shop["id"],report,actor="operator")
    assert err.value.status_code==403
    first=review(db,shop["id"],report)
    assert first["replayed"] is False
    assert first["source_verified"] is False
    assert first["rights_verified"] is False
    assert first["scan_enabled"] is False
    assert first["jobs_created"]==0
    second=review(db,shop["id"],report)
    assert second["id"]==first["id"]
    assert second["replayed"] is True
    third=review(db,shop["id"],report,decision="metadata_consistent",
        rights_basis="consent_reference_recorded",
        evidence_reference="consent-record-001",
        note="Metadata plausible, consent record noted not independently verified")
    assert third["id"]!=first["id"]
    assert third["source_verified"] is False
    rejected=review(db,shop["id"],report,actor="another-reviewer",
        decision="rejected",rights_basis="permission_missing",
        note="Marketplace permission documentation is missing")
    assert rejected["source_verified"] is False
    assert db.query(BrandBrowserObservationReviewModel).count()==3
    rows=list_browser_observation_reviews(
        shop["id"],report,Response(),session=db,
        principal=principal(),limit=50)
    assert rows["review_count_returned"]==3
    assert rows["latest_decision"]=="rejected"
    assert rows["training_approved"] is False
    assert rows["scan_enabled"] is False
    assert {x["decision"] for x in rows["items"]}=={
        "needs_authorized_source","metadata_consistent","rejected"}
    assert db.get(BrandBrowserObservationModel,report).status=="preview_completed"


def test_review_is_tenant_and_shop_scoped_even_if_report_id_known(db):
    shop=make_shop(db)
    other=make_shop(db,name="OtherShop",tenant="tenant-b")
    report=upload(db,shop,payload(shop))["item"]["id"]
    for sid,ten in [(shop["id"],"tenant-b"),(other["id"],"tenant-b")]:
        with pytest.raises(HTTPException) as err:
            review(db,sid,report,tenant=ten)
        assert err.value.status_code==404
        with pytest.raises(HTTPException) as err:
            list_browser_observation_reviews(
                sid,report,Response(),session=db,
                principal=principal(ten),limit=50)
        assert err.value.status_code==404
    assert db.query(BrandBrowserObservationReviewModel).count()==0


def test_browser_review_rejects_forged_rights_and_invalid_evidence_note(db):
    cases=[
        {"rights_basis":"consent_reference_recorded"},
        {"rights_basis":"unknown","evidence_reference":"consent-record-01"},
        {"evidence_reference":"x"},
        {"evidence_reference":"https://host/?secret=abc",
         "rights_basis":"consent_reference_recorded"},
        {"decision":"verified"},
        {"note":"Line one\nLine two"},
        {"note":"short"},
        {"source_verified":True},
    ]
    for data in cases:
        base={
            "decision":"metadata_consistent","rights_basis":"unknown",
            "note":"An independent review of metadata only",
        }
        with pytest.raises(ValidationError):
            BrowserObservationReviewInput(**{**base,**data})

def test_operator_review_inbox_is_tenant_scoped_and_filterable(db):
    tenant_a=make_shop(db,name="AlphaReviewCaps")
    tenant_b=make_shop(db,name="BetaReviewCaps")
    foreign=make_shop(db,name="ForeignReviewCaps",tenant="tenant-b")
    report_a=upload(db,tenant_a,payload(tenant_a))["item"]["id"]
    report_b=upload(db,tenant_b,payload(tenant_b))["item"]["id"]
    upload(db,foreign,payload(foreign),tenant="tenant-b")
    review(db,tenant_b["id"],report_b,decision="metadata_consistent",
           note="Only a human metadata judgement without source verification")
    pending=browser_observation_review_queue(
        Response(),state="pending_independent_review",limit=20,offset=0,
        session=db,principal=principal())
    assert pending["count"]==1
    assert pending["items"][0]["id"]==report_a
    assert pending["items"][0]["review_state"]=="pending_independent_review"
    assert "items_json" not in str(pending)
    assert pending["source_verified"] is False
    assert pending["scan_enabled"] is False
    consistent=browser_observation_review_queue(
        Response(),state="metadata_consistent",limit=20,offset=0,
        session=db,principal=principal())
    assert consistent["count"]==1
    assert consistent["items"][0]["id"]==report_b
    page=browser_observation_review_queue(
        Response(),state=None,limit=1,offset=1,
        session=db,principal=principal())
    assert page["count"]==2
    assert len(page["items"])==1
    own=browser_observation_review_queue(
        Response(),state=None,limit=20,offset=0,
        session=db,principal=principal(tenant="tenant-b"))
    assert own["count"]==1
    assert own["items"][0]["shop_id"]==foreign["id"]
