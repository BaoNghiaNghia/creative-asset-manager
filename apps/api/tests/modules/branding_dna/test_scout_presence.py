from datetime import timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandShopModel
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.scout_session_router import (
    ScoutStandbyHeartbeat, standby_heartbeat, list_paired_presence, online,
)
from app.modules.branding_dna.router import ShopCreate, add_shop
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel
from app.modules.realistic_review_ugc.service import token_digest
from app.modules.branding_dna.model import now_utc

TOKEN = "test-paired-token-long-enough-for-local-tests"


@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        BrandShopModel.__table__, RrugcScoutAgentModel.__table__,
        BrandScoutSessionModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def principal(tenant):
    return SimpleNamespace(active_tenant_id=tenant, actor_id="test-admin")


def enroll(db, tenant="tenant-a"):
    scout = RrugcScoutAgentModel(
        tenant_id=tenant, name="Scout", active=True, status="offline",
        token_hash=token_digest(TOKEN), created_by_user_id="test-admin",
    )
    db.add(scout)
    db.commit()
    return scout


def body():
    return ScoutStandbyHeartbeat(
        version="0.2.5", state="connected_waiting",
        scan_enabled=False, active_scan_jobs=0,
    )


def test_standby_presence_is_isolated_and_tenant_scoped(db):
    a = enroll(db)
    b = enroll(db, "tenant-b")
    shop = add_shop(ShopCreate(
        platform="amazon", storefront_url="https://www.amazon.com/stores/OwnHats",
    ), session=db, principal=principal("tenant-a"))
    response = Response()
    ack = standby_heartbeat(a.id, body(), response,
        authorization="Bearer " + TOKEN, session=db)
    assert ack["accepted"] is True
    assert ack["shop_count"] == 1
    assert ack["scan_enabled"] is False
    assert ack["active_scan_jobs"] == 0
    assert response.headers["Cache-Control"] == "no-store"

    record = db.get(BrandScoutSessionModel, a.id)
    assert record.tenant_id == "tenant-a"
    assert record.shop_count == 1
    assert db.get(RrugcScoutAgentModel, a.id).status == "offline", (
        "Branding heartbeat must not turn on the Pinterest Scout")
    visible = list_paired_presence(Response(), db, principal("tenant-a"))
    assert len(visible["items"]) == 1
    assert visible["items"][0]["online"] is True
    assert visible["items"][0]["state"] == "connected_waiting"
    assert list_paired_presence(Response(), db, principal("tenant-b"))["items"] == []

    repeat = standby_heartbeat(a.id, body(), Response(),
        authorization="Bearer " + TOKEN, session=db)
    assert repeat["accepted"] is True
    assert db.query(BrandScoutSessionModel).count() == 1

    a.active = False
    db.commit()
    offline = list_paired_presence(Response(), db, principal("tenant-a"))
    assert offline["items"][0]["online"] is False
    with pytest.raises(HTTPException) as failure:
        standby_heartbeat(a.id, body(), Response(),
            authorization="Bearer " + TOKEN, session=db)
    assert failure.value.status_code == 401

    with pytest.raises(HTTPException) as wrong_agent:
        standby_heartbeat(b.id, body(), Response(),
            authorization="Bearer wrong", session=db)
    assert wrong_agent.value.status_code == 401


def test_presence_expires_and_never_auto_scans(db):
    a = enroll(db)
    standby_heartbeat(a.id, body(), Response(), authorization="Bearer " + TOKEN, session=db)
    now = now_utc()
    assert not online(now - timedelta(seconds=190), now)
    assert not online(now + timedelta(seconds=20), now)
    record = db.get(BrandScoutSessionModel, a.id)
    record.last_seen_at = now - timedelta(minutes=4)
    db.commit()
    state = list_paired_presence(Response(), db, principal("tenant-a"))
    assert state["items"][0]["online"] is False
    assert state["dispatch_state"] == "disabled"
    assert state["scan_enabled"] is False


@pytest.mark.parametrize("bad", [
    {"version": "0.2.5", "state": "scanning", "scan_enabled": False, "active_scan_jobs": 0},
    {"version": "0.2.5", "state": "connected_waiting", "scan_enabled": True, "active_scan_jobs": 0},
    {"version": "0.2.5", "state": "connected_waiting", "scan_enabled": False, "active_scan_jobs": 1},
    {"version": "0.2.5", "state": "connected_waiting", "scan_enabled": False,
     "active_scan_jobs": 0, "arbitrary_url": "https://example.com"},
])
def test_payload_rejects_scan_claims_and_extra_fields(bad):
    with pytest.raises(ValidationError):
        ScoutStandbyHeartbeat.model_validate(bad)
