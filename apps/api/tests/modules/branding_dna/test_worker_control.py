from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
import pytest
from fastapi import HTTPException, Response
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandWorkerModel
from app.modules.branding_dna.worker_router import (
    WorkerRegister, WorkerHeartbeat, register_worker,
    disable_worker, worker_heartbeat, worker_lease,
    authenticate_agent, token_digest, worker_online,
)

@pytest.fixture
def db():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[BrandWorkerModel.__table__])
    with Session(engine) as s:
        yield s
    engine.dispose()

def who(tenant="tenant-a"):
    return SimpleNamespace(active_tenant_id=tenant)

def test_worker_enrollment_stores_only_hash_and_is_tenant_scoped(db):
    response = Response()
    result = register_worker(WorkerRegister(label="Windows Scanner One"),
                             response, session=db, principal=who())
    assert response.headers["cache-control"] == "no-store"
    assert result["enabled"] and len(result["agent_token"]) >= 42
    agent = db.get(BrandWorkerModel, result["id"])
    assert agent.token_hash == token_digest(result["agent_token"])
    assert result["agent_token"] not in repr(agent)
    with pytest.raises(HTTPException) as ex:
        disable_worker(result["id"], session=db, principal=who("tenant-b"))
    assert ex.value.status_code == 404

def test_worker_heartbeat_requires_bearer_and_never_dispatches_job(db):
    created = register_worker(WorkerRegister(label="Windows Scanner One"),
                              Response(), session=db, principal=who())
    ident, token = created["id"], created["agent_token"]
    for invalid in (None, "Bearer invalid", "Bearer " + "z" * 60):
        with pytest.raises(HTTPException) as err:
            worker_heartbeat(ident, WorkerHeartbeat(), authorization=invalid, session=db)
        assert err.value.status_code == 401
    response = worker_heartbeat(ident, WorkerHeartbeat(status="idle",agent_version="v0.1.0"),
                                authorization="Bearer " + token, session=db)
    assert response["status"] == "accepted" and response["scan_enabled"] is False
    assert worker_online(db.get(BrandWorkerModel, ident))
    lease = worker_lease(ident, authorization="Bearer " + token, session=db)
    assert lease["job"] is None and lease["scan_enabled"] is False
    assert db.get(BrandWorkerModel, ident).agent_version == "v0.1.0"

def test_disable_revokes_token_and_marks_offline(db):
    created = register_worker(WorkerRegister(label="Windows Scanner One"),
                              Response(), session=db, principal=who())
    agent_id, bearer = created["id"], "Bearer " + created["agent_token"]
    worker_heartbeat(agent_id, WorkerHeartbeat(), authorization=bearer, session=db)
    assert disable_worker(agent_id, session=db, principal=who())["enabled"] is False
    assert db.get(BrandWorkerModel, agent_id).token_hash is None
    assert not worker_online(db.get(BrandWorkerModel, agent_id))
    with pytest.raises(HTTPException) as err:
        worker_heartbeat(agent_id, WorkerHeartbeat(), authorization=bearer, session=db)
    assert err.value.status_code == 401

def test_expired_heartbeat_is_not_online(db):
    agent = BrandWorkerModel(tenant_id="tenant-a", label="Old PC", enabled=True,
                             status="idle", token_hash="f" * 64,
                             last_heartbeat=datetime.now(timezone.utc)-timedelta(minutes=6))
    db.add(agent)
    db.commit()
    assert not worker_online(agent)
    agent.last_heartbeat = datetime.now(timezone.utc) + timedelta(minutes=10)
    db.commit()
    assert not worker_online(agent)
