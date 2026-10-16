"""Worker event replay, scoped auth, no-secret schema and retry safety."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from fastapi import HTTPException, Response
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.branding_dna.model import BrandWorkerModel
from app.modules.branding_dna.worker_event_model import BrandWorkerEventModel
from app.modules.branding_dna.worker_event_router import (
    WorkerLifecycleEvent, WorkerEventBatch, ingest_worker_events,
)
from app.modules.branding_dna.worker_router import register_worker, WorkerRegister

UTC_NOW = datetime.now(timezone.utc)


@pytest.fixture
def session():
    db = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(db, tables=[BrandWorkerModel.__table__, BrandWorkerEventModel.__table__])
    with Session(db) as s:
        yield s
    db.dispose()


def registered(session, tenant="tenant-a"):
    class Principal:
        active_tenant_id = tenant
    payload = register_worker(
        WorkerRegister(label="Windows Scanner"), Response(),
        session=session, principal=Principal(),
    )
    return payload["id"], "Bearer " + payload["agent_token"]


def event(kind="started", detail="worker_start", **kwargs):
    return WorkerLifecycleEvent(
        event_id=uuid4(), kind=kind, detail_code=detail,
        occurred_at=UTC_NOW, **kwargs,
    )


def ingest(session, agent_id, bearer, values):
    response = Response()
    result = ingest_worker_events(agent_id=agent_id,
        body=WorkerEventBatch(events=values), authorization=bearer,
        response=response, session=session)
    assert response.headers["cache-control"] == "no-store"
    assert result["scan_enabled"] is False
    return result


def test_worker_ingest_replays_idempotently_and_does_not_create_jobs(session):
    agent_id, token = registered(session)
    first = event()
    result = ingest(session, agent_id, token, [first])
    assert result["accepted_ids"] == [str(first.event_id)]
    assert result["accepted_count"] == 1 and result["replayed_count"] == 0
    repeat = ingest(session, agent_id, token, [first])
    assert repeat["replayed_count"] == 1
    assert session.query(BrandWorkerEventModel).count() == 1
    row = session.scalar(select(BrandWorkerEventModel).where(
        BrandWorkerEventModel.event_id == str(first.event_id)
    ))
    assert row.tenant_id == "tenant-a" and row.agent_id == agent_id
    assert row.kind == "started"


def test_conflicting_replay_is_rejected_without_overwrite(session):
    agent_id, token = registered(session)
    started = event()
    ingest(session, agent_id, token, [started])
    altered = WorkerLifecycleEvent(**{**started.model_dump(),
                                      "kind": "stopped", "detail_code": "graceful_shutdown"})
    with pytest.raises(HTTPException) as exc:
        ingest(session, agent_id, token, [altered])
    assert exc.value.status_code == 409
    assert session.query(BrandWorkerEventModel).count() == 1
    assert session.query(BrandWorkerEventModel).first().kind == "started"


def test_revoked_or_cross_agent_credentials_not_accepted(session):
    agent_a, token_a = registered(session, "tenant-a")
    agent_b, token_b = registered(session, "tenant-b")
    for ident, bearer in ((agent_a, token_b), (agent_b, token_a),
                          (agent_a, None), (agent_a, "Bearer invalid")):
        with pytest.raises(HTTPException) as exc:
            ingest(session, ident, bearer, [event()])
        assert exc.value.status_code == 401
    agent = session.get(BrandWorkerModel, agent_a)
    agent.enabled = False
    session.commit()
    with pytest.raises(HTTPException) as exc:
        ingest(session, agent_a, token_a, [event()])
    assert exc.value.status_code == 401
    assert session.query(BrandWorkerEventModel).count() == 0


def test_rejects_untrusted_fields_and_duplicate_batch():
    item = event()
    for bad in ({"token": "a-secret"}, {"source_url": "https://shop.example"},
                {"shop_id": "external-shop"}, {"payload": {"cookie": "x"}}):
        with pytest.raises(ValidationError):
            WorkerLifecycleEvent(**{**item.model_dump(), **bad})
    with pytest.raises(ValidationError):
        WorkerLifecycleEvent(**{**item.model_dump(), "kind": "stopped",
                                "detail_code": "worker_start"})
    with pytest.raises(ValidationError):
        WorkerLifecycleEvent(**{**item.model_dump(), "occurred_at": UTC_NOW.replace(tzinfo=None)})
    with pytest.raises(ValidationError):
        WorkerEventBatch(events=[item, item])
    with pytest.raises(ValidationError):
        WorkerEventBatch(events=[])
    with pytest.raises(ValidationError):
        WorkerEventBatch(events=[event() for _ in range(51)])


def test_too_old_or_future_event_is_not_written(session):
    ident, bearer = registered(session)
    for at in (UTC_NOW - timedelta(days=31), UTC_NOW + timedelta(hours=1)):
        item = WorkerLifecycleEvent(event_id=uuid4(), kind="started",
                                    detail_code="worker_start", occurred_at=at)
        with pytest.raises(HTTPException) as exc:
            ingest(session, ident, bearer, [item])
        assert exc.value.status_code == 422
    assert session.query(BrandWorkerEventModel).count() == 0


def test_daily_worker_event_quota_rejects_unbounded_ingress(session):
    ident, bearer = registered(session)
    session.add_all([
        BrandWorkerEventModel(
            tenant_id="tenant-a", agent_id=ident, event_id=str(uuid4()),
            kind="started", detail_code="worker_start",
            event_sha256="a" * 64, occurred_at=UTC_NOW,
            received_at=UTC_NOW,
        )
        for _ in range(1000)
    ])
    session.commit()
    with pytest.raises(HTTPException) as exc:
        ingest(session, ident, bearer, [event()])
    assert exc.value.status_code == 429
    assert session.query(BrandWorkerEventModel).count() == 1000


def test_batch_is_atomic_when_replay_conflicts(session):
    ident, bearer = registered(session)
    prior = event()
    ingest(session, ident, bearer, [prior])
    new = event(kind="heartbeat_recovered", detail="recovered")
    conflicting = WorkerLifecycleEvent(**{**prior.model_dump(), "kind": "stopped",
                                          "detail_code": "graceful_shutdown"})
    with pytest.raises(HTTPException) as exc:
        ingest(session, ident, bearer, [new, conflicting])
    assert exc.value.status_code == 409
    assert session.query(BrandWorkerEventModel).count() == 1
