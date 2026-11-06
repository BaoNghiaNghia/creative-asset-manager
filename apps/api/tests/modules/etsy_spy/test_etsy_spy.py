"""Etsy Spy security, projection, idempotency and offline replay regression."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base, get_db
from app.modules.authorization.principal import require_authenticated_principal, _require_navigation_access
from app.modules.authorization.navigation_scope import (
    NAVIGATION_MODULES, navigation_for_api_path,
)
from app.modules.etsy_spy.model import (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch,
)
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy.service import create_agent


TABLES = (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch,
)


@pytest.fixture
def scenario():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine, tables=[model.__table__ for model in TABLES])
    session = Session(engine)
    principal = SimpleNamespace(
        active_tenant_id="boostgame", effective_permissions=frozenset({"etsy_spy.read", "etsy_spy.manage"}),
        platform_admin=False, navigation_modules=frozenset({"etsy-spy"}),
    )
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: session
    app.dependency_overrides[require_authenticated_principal] = lambda: principal
    with TestClient(app) as client:
        yield client, session, principal
    session.close()
    engine.dispose()


def product(session="session-1", keyword="Santa Claus Rudolph boop shirt",
            title="Rudolph T-Shirt"):
    return {
        "sessionId": session, "keyword": keyword,
        "sourceKind": "search", "listingKey": "1234567890",
        "pageTitle": title, "url": "https://www.etsy.com/listing/1234567890/example",
        "badge": "Bestseller", "searchBadge": "Bestseller",
        "bought24h": None, "views24h": 20, "inCarts": 25,
        "demandScore": 0, "observedAt": "2026-11-06T00:00:00Z",
        "updatedAt": "2026-11-06T00:00:00Z",
        "tmKey": "a" * 64,
    }


def batch(batch_id="batch-1", *, title="Rudolph T-Shirt", matched=None):
    return {
        "schema_version": 1, "batch_id": batch_id, "cursor": "cursor-a",
        "records": {
            "sessions": [{
                "sessionId": "session-1", "revision": 5, "status": "complete",
                "phase": "shop", "keyword": "Santa Claus Rudolph boop shirt",
                "startedAt": "2026-11-06T00:00:00Z",
                "updatedAt": "2026-11-06T00:00:01Z",
            }],
            "keywords": [{
                "sessionId": "session-1", "id": "node-1", "keyword": "Rudolph T-Shirt",
                "depth": 1, "priority": 0.8, "status": "complete",
                "evidence": ["trending"], "matchedCount": 1,
                "updatedAt": "2026-11-06T00:00:01Z",
            }],
            "listings": [product(title=title)],
            "trademarks": [{
                "checkId": "a" * 64, "kind": "product", "keyword": "Santa Claus Rudolph boop shirt",
                "title": title, "status": "complete", "risk": "potential_conflict",
                "liveCount": 2, "deadCount": 0, "unknownCount": 0,
                "matched": matched if matched is not None else [
                    {"markText": "Rudolph", "serialNumber": "12345678", "liveStatus": "LIVE"},
                ], "source": "TMHunt", "sourceAsOf": "2026-07-24",
                "checkedAt": "2026-11-06T00:00:02Z",
            }],
            "logs": [{
                "eventId": "event-1", "at": "2026-11-06T00:00:02Z",
                "source": "extension", "level": "error", "phase": "search",
                "sessionId": "session-1", "keyword": "Rudolph",
                "message": "Request failed", "stack": "=HYPERLINK(1,2)",
            }],
        },
    }


def pair(client, session, tenant="boostgame"):
    agent, secret = create_agent(session, tenant, "Desktop Spy")
    return agent, {"Authorization": f"Bearer {secret}"}


def sync(client, agent, headers, value):
    return client.post(f"/api/v1/etsy-spy/agents/{agent.id}/sync",
                       content=json.dumps(value, separators=(",", ":")),
                       headers={**headers, "Content-Type": "application/json"})


def test_admin_pair_secret_only_once_and_reader_cannot_pair(scenario):
    client, db, principal = scenario
    created = client.post("/api/v1/etsy-spy/agents", json={"name": "Desktop Spy"})
    assert created.status_code == 201, created.text
    token = created.json()["token"]
    assert len(token) >= 64
    listed = client.get("/api/v1/etsy-spy/agents").json()["items"]
    assert len(listed) == 1
    assert token not in json.dumps(listed)
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    denied = client.post("/api/v1/etsy-spy/agents", json={"name": "Unauthorized"})
    assert denied.status_code == 403


def test_sync_is_idempotent_and_title_only_tm_conflicts(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    first = sync(client, agent, headers, batch())
    assert first.status_code == 200, first.text
    assert first.json()["rows"] == 5
    replay = sync(client, agent, headers, batch())
    assert replay.status_code == 200 and replay.json()["replayed"]
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 1
    items = client.get("/api/v1/etsy-spy/listings").json()["items"]
    assert len(items) == 1 and items[0]["tm"] == "LIVE"
    assert items[0]["badge"] == "Bestseller" and items[0]["bought24h"] is None
    assert items[0]["tmMatches"][0]["serialNumber"] == "12345678"
    tamper = batch()
    tamper["cursor"] = "modified_same_batch"
    assert sync(client, agent, headers, tamper).status_code == 409
    # A search keyword match does NOT make an unrelated product title conflicting.
    second = batch("batch-2", matched=[
        {"markText": "Santa Claus", "serialNumber": "99999999", "liveStatus": "LIVE"},
    ])
    assert sync(client, agent, headers, second).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings").json()["items"][0]["tm"] == "no_match"


def test_tenant_isolation_navigation_and_revocation(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db, tenant="another-tenant")
    assert sync(client, agent, headers, batch()).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 0
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 0
    assert client.get("/api/v1/etsy-spy/trademarks").json()["total"] == 0
    assert client.post(f"/api/v1/etsy-spy/agents/{agent.id}/revoke").status_code == 404
    principal.active_tenant_id = "another-tenant"
    assert client.post(f"/api/v1/etsy-spy/agents/{agent.id}/revoke").status_code == 200
    assert sync(client, agent, headers, batch("next")).status_code == 401
    assert navigation_for_api_path("/api/v1/etsy-spy/listings") == "etsy-spy"
    assert NAVIGATION_MODULES["etsy-spy"] == {"etsy_spy.read", "etsy_spy.manage"}
    principal.navigation_modules = frozenset({"realistic-review-ugc"})
    with pytest.raises(HTTPException) as exc:
        _require_navigation_access(principal, "/api/v1/etsy-spy/listings")
    assert exc.value.status_code == 403


def test_offline_data_retention_search_sort_pagination_and_csv(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    agent.last_seen_at = datetime.now(timezone.utc) - timedelta(minutes=20)
    db.commit()
    summary = client.get("/api/v1/etsy-spy/summary").json()
    assert summary["source_status"] == "offline" and summary["listings"] == 1
    response = client.get("/api/v1/etsy-spy/listings", params={
        "q": "Rudolph", "sort": "inCarts", "dir": "desc", "page": 1, "page_size": 50,
    })
    assert response.status_code == 200 and response.json()["total"] == 1
    assert response.json()["source_status"] == "offline"
    csv_response = client.get("/api/v1/etsy-spy/export.csv", params={"q": "Rudolph"})
    assert csv_response.status_code == 200 and "Rudolph T-Shirt" in csv_response.text
    logs_csv = client.get("/api/v1/etsy-spy/logs/export.csv")
    assert "'=HYPERLINK" in logs_csv.text
    assert client.get("/api/v1/etsy-spy/logs/stats").json()["errors"] == 1


def test_bad_credentials_custom_titles_oversize_and_separate_permissions(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, {"Authorization": "Bearer invalid"}, batch()).status_code == 401
    bad_custom = batch()
    bad_custom["records"]["listings"][0]["pageTitle"] = "Custom Personalized Shirt"
    assert sync(client, agent, headers, bad_custom).status_code == 422
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 0
    oversized = b" " * (2*1024*1024+1)
    assert client.post(f"/api/v1/etsy-spy/agents/{agent.id}/sync",
        content=oversized, headers=headers).status_code == 413
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.post(f"/api/v1/etsy-spy/agents/{agent.id}/rotate").status_code == 403


def test_tm_lookup_uses_cached_title_evidence_only(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    response = client.post("/api/v1/etsy-spy/trademarks/lookup", json={
        "queries": ["Rudolph"], "mode": "exactMatch",
    })
    assert response.status_code == 200
    assert response.json()["total"] == 1
    unknown = client.post("/api/v1/etsy-spy/trademarks/lookup", json={
        "queries": ["Unrelated"], "mode": "partialMatch",
    })
    assert unknown.status_code == 200 and unknown.json()["total"] == 0
    assert client.post("/api/v1/etsy-spy/trademarks/lookup", json={
        "queries": ["a"] * 31, "mode": "exactMatch",
    }).status_code == 422
