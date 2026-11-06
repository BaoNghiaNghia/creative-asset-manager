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
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist,
)
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy.service import create_agent


TABLES = (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist,
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

def _inventory(agent, headers, client, listings, timestamp="2026-11-06T02:01:00Z"):
    return client.post(f"/api/v1/etsy-spy/agents/{agent.id}/listing-inventory",
        json={"schema_version": 1, "snapshot_at": timestamp, "listings": listings},
        headers=headers)


def test_listing_inventory_matches_windows_unique_products_search_shop_and_tm(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    fresh = product()
    fresh["sourceKinds"] = ["search", "shop"]
    older = {**product(session="old-session", keyword="Rudolph vintage shirt"),
             "sourceKind": "shop", "badge": "Bestseller", "searchBadge": None,
             "sourceKinds": ["shop"], "observedAt": "2026-11-05T00:00:00Z"}
    payload = batch("historical-two", title="Rudolph T-Shirt")
    payload["records"]["listings"] = [fresh, older]
    # Hundreds of keyword TM checks may be pending; they must NOT appear in
    # the dashboard's pending *product* TM count.
    payload["records"]["trademarks"].append({
        "checkId": "keyword_" + "b" * 64, "kind": "keyword",
        "keyword": "unrelated keyword", "status": "pending",
        "matched": [], "source": "USPTO local bulk",
    })
    assert sync(client, agent, headers, payload).status_code == 200
    before = client.get("/api/v1/etsy-spy/summary").json()
    assert before["listings"] == 1
    assert (before["search"], before["shop"]) == (1, 1)
    assert before["tm_pending"] == 0 and before["tm_checked"] == 1
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 2

    current = [{
        "sessionId": fresh["sessionId"], "keyword": fresh["keyword"],
        "sourceKind": fresh["sourceKind"], "listingKey": fresh["listingKey"],
    }]
    response = _inventory(agent, headers, client, current)
    assert response.status_code == 200, response.text
    assert response.json()["count"] == 1
    assert response.json()["retired"] == 1

    item_page = client.get("/api/v1/etsy-spy/listings").json()
    assert item_page["total"] == 1
    assert len(item_page["items"]) == 1
    assert set(item_page["items"][0]["sourceKinds"]) == {"shop", "search"}
    assert item_page["items"][0]["tm"] == "LIVE"
    assert item_page["items"][0]["tmSource"] == "TMHunt"
    for kind in ("search", "shop"):
        assert client.get("/api/v1/etsy-spy/listings", params={"source": kind}).json()["total"] == 1
    summary = client.get("/api/v1/etsy-spy/summary").json()
    assert (summary["listings"], summary["search"], summary["shop"]) == (1, 1, 1)
    assert summary["tm_pending"] == 0
    assert summary["last_inventory_at"] is not None
    # Historical rows remain in PostgreSQL. No DELETE is ever issued.
    retained = db.query(EtsySpyListing).all()
    assert len(retained) == 2
    assert len([row for row in retained if row.is_current]) == 1


def test_inventory_is_atomic_rejects_missing_duplicates_stale_and_foreign_tenant(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    identity = {
        "sessionId": "session-1", "keyword": "Santa Claus Rudolph boop shirt",
        "sourceKind": "search", "listingKey": "1234567890",
    }
    valid = _inventory(agent, headers, client, [identity])
    assert valid.status_code == 200
    older = _inventory(agent, headers, client, [], "2026-11-06T01:00:00Z")
    assert older.status_code == 200 and older.json()["replayed"] is True
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 1
    missing = _inventory(agent, headers, client, [
        {**identity, "listingKey": "9999999999"},
    ], "2026-11-06T02:03:00Z")
    assert missing.status_code == 409
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 1
    duplicate = _inventory(agent, headers, client, [identity, identity],
                           "2026-11-06T02:04:00Z")
    assert duplicate.status_code == 422
    assert db.get(EtsySpyAgent, agent.id).last_inventory_count == 1
    assert db.query(EtsySpyListing).count() == 1
    invalid = client.post(
        f"/api/v1/etsy-spy/agents/{agent.id}/listing-inventory",
        json={"schema_version": 1, "snapshot_at": "2026-11-06T02:05:00Z",
              "listings": [identity]},
        headers={"Authorization": "Bearer invalid"})
    assert invalid.status_code == 401

    agent2, headers2 = pair(client, db, "another-tenant")
    assert sync(client, agent2, headers2, batch("tenant-b")).status_code == 200
    # An empty snapshot on tenant B may never retire tenant A data.
    empty = _inventory(agent2, headers2, client, [],
                       "2026-11-06T02:06:00Z")
    assert empty.status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 1

def test_blacklist_hides_old_and_future_products_and_restores_on_delete(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    initial = client.get("/api/v1/etsy-spy/summary").json()
    assert initial["listings"] == 1

    # Whole phrase is normalized, not a raw wildcard or unsafe substring.
    added = client.post("/api/v1/etsy-spy/blacklist",
        json={"phrases": ["RUDOLPH!!", "cat"]})
    assert added.status_code == 200, added.text
    assert added.json()["phrases"] == ["cat", "rudolph"]
    assert added.json()["visible_products"] == 0
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 0
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 0
    assert "Rudolph T-Shirt" not in client.get(
        "/api/v1/etsy-spy/export.csv").text

    # Windows can obtain the same tenant-scoped blacklist using only its agent token.
    pulled = client.get(f"/api/v1/etsy-spy/agents/{agent.id}/blacklist",
                        headers=headers)
    assert pulled.status_code == 200
    assert pulled.json()["phrases"] == ["cat", "rudolph"]
    assert client.get(f"/api/v1/etsy-spy/agents/{agent.id}/blacklist",
        headers={"Authorization": "Bearer invalid"}).status_code == 401

    # A later sync cannot reintroduce a blocked listing in CAM.
    later = batch("batch-future")
    later["records"]["listings"][0]["pageTitle"] = "New Rudolph Sweatshirt"
    later["records"]["listings"][0]["listingKey"] = "9876543210"
    later["records"]["listings"][0]["url"] = "https://www.etsy.com/listing/9876543210/new"
    assert sync(client, agent, headers, later).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 0

    # 'cat' cannot remove the unrelated word caterpillar.
    not_a_cat = batch("batch-caterpillar")
    not_a_cat["records"]["listings"][0]["listingKey"] = "4567890123"
    not_a_cat["records"]["listings"][0]["pageTitle"] = "Caterpillar Sweatshirt"
    not_a_cat["records"]["listings"][0]["url"] = "https://www.etsy.com/listing/4567890123/caterpillar"
    assert sync(client, agent, headers, not_a_cat).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 1

    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.post("/api/v1/etsy-spy/blacklist",
        json={"phrases": ["unapproved"]}).status_code == 403
    assert client.delete("/api/v1/etsy-spy/blacklist/rudolph").status_code == 403
    principal.effective_permissions = frozenset({"etsy_spy.read", "etsy_spy.manage"})

    removed = client.delete("/api/v1/etsy-spy/blacklist/rudolph")
    assert removed.status_code == 200
    assert removed.json()["phrases"] == ["cat"]
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 3
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 3
def test_unbadged_listing_with_19_verified_views_is_visible_in_cam(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    payload = batch("batch-unbadged-views", title="Vintage Winter Snow Shirt")
    row = payload["records"]["listings"][0]
    row.update({
        "pageTitle": "Vintage Winter Snow Shirt",
        "badge": "", "searchBadge": "",
        "views24h": 19, "inCarts": 8, "bought24h": None,
    })
    assert sync(client, agent, headers, payload).status_code == 200
    overview = client.get("/api/v1/etsy-spy/summary").json()
    assert overview["listings"] == 1
    assert overview["search"] == 1
    result = client.get("/api/v1/etsy-spy/listings").json()
    assert result["total"] == 1
    item = result["items"][0]
    assert item["views24h"] == 19
    assert not item["badge"] and not item["searchBadge"]
    assert item["inCarts"] == 8
    assert "Vintage Winter Snow Shirt" in client.get(
        "/api/v1/etsy-spy/export.csv").text
