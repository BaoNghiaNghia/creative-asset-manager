"""Operator dispatch health explains no-job state without enabling scans."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandWorkerModel
from app.modules.branding_dna.scout_session_model import BrandScoutSessionModel
from app.modules.branding_dna.access_declaration_router import read_worker_dispatch_health
from app.modules.realistic_review_ugc.model import RrugcScoutAgentModel


@pytest.fixture
def db():
    engine=create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine,tables=[
        RrugcScoutAgentModel.__table__,
        BrandWorkerModel.__table__, BrandScoutSessionModel.__table__,
    ])
    with Session(engine) as session:
        yield session
    engine.dispose()


def principal(tenant):
    return SimpleNamespace(active_tenant_id=tenant)


def test_dispatch_health_is_tenant_scoped_and_read_only(db):
    now=datetime.now(timezone.utc)
    workers=[
        BrandWorkerModel(tenant_id="a",label="Ready",enabled=True,status="idle",
                         last_heartbeat=now-timedelta(seconds=35), token_hash="a"*64),
        BrandWorkerModel(tenant_id="a",label="Paused",enabled=True,status="paused",
                         last_heartbeat=now-timedelta(seconds=45), token_hash="b"*64),
        BrandWorkerModel(tenant_id="a",label="Stale",enabled=True,status="idle",
                         last_heartbeat=now-timedelta(minutes=9), token_hash="c"*64),
        BrandWorkerModel(tenant_id="a",label="Disabled",enabled=False,status="idle",
                         last_heartbeat=now-timedelta(seconds=5), token_hash=None),
        BrandWorkerModel(tenant_id="b",label="Other Tenant",enabled=True,status="idle",
                         last_heartbeat=now-timedelta(seconds=7), token_hash="d"*64),
    ]
    db.add_all(workers)
    paired=[
        RrugcScoutAgentModel(tenant_id="a",name="Paired A", active=True,
                             status="offline",token_hash="e"*64,
                             created_by_user_id="actor"),
        RrugcScoutAgentModel(tenant_id="a",name="Paired B", active=False,
                             status="offline",token_hash="f"*64,
                             created_by_user_id="actor"),
        RrugcScoutAgentModel(tenant_id="b",name="Paired C", active=True,
                             status="offline",token_hash="g"*64,
                             created_by_user_id="actor"),
    ]
    db.add_all(paired)
    db.flush()
    db.add_all([
        BrandScoutSessionModel(tenant_id="a",agent_id=paired[0].id,
                               version="0.1.0",shop_count=1,last_seen_at=now-timedelta(seconds=45)),
        BrandScoutSessionModel(tenant_id="a",agent_id=paired[1].id,
                               version="0.1.0",shop_count=2,last_seen_at=now-timedelta(seconds=20)),
        BrandScoutSessionModel(tenant_id="b",agent_id=paired[2].id,
                               version="0.1.0",shop_count=3,last_seen_at=now-timedelta(seconds=20)),
    ])
    db.commit()

    response=Response()
    state=read_worker_dispatch_health(response,session=db,principal=principal("a"))
    assert response.headers["Cache-Control"]=="no-store"
    assert state["scan_enabled"] is False
    assert state["dispatch_state"]=="disabled"
    assert state["marketplace_authorization_verified"] is False
    assert state["dedicated_workers"]=={
        "registered":4,"online_idle":1,"online_paused":1,
        "online_unhealthy":0,"offline":1,"disabled":1,
    }
    assert state["paired_scouts"]=={
        "registered":2,"connected_waiting":1,"offline":1,
    }
    assert "token_hash" not in str(state)
    assert "shop_count" not in str(state)
    assert "tenant_id" not in str(state)
    other=read_worker_dispatch_health(Response(),session=db,principal=principal("b"))
    assert other["dedicated_workers"]["registered"]==1
    assert other["paired_scouts"]["connected_waiting"]==1
    assert all(w.status in ("idle","paused") for w in workers)
