"""Etsy Spy security, projection, idempotency and offline replay regression."""
from __future__ import annotations

import json
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo
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
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist, EtsySpyPicked,
    EtsySpyLaterSeed, EtsySpyLaterMatch,
    EtsySpyWinKeyword, EtsySpyParentKeyword, EtsySpySmsStatus,
)
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy.service import create_agent
from app.modules.processing.model import ProcessingJobModel
from app.domain.processing.types import JOB_TYPES


TABLES = (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist, EtsySpyPicked,
    EtsySpyLaterSeed, EtsySpyLaterMatch,
    EtsySpyWinKeyword, EtsySpyParentKeyword, EtsySpySmsStatus, ProcessingJobModel,
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


def batch(batch_id="batch-1", *, title="Rudolph T-Shirt", matched=None,
          log_at=datetime.now(timezone.utc).isoformat()):
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
                "eventId": "event-1", "at": datetime.now(timezone.utc).isoformat(),
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




def test_sms_receipt_projection_picked_only_and_immutable_sent(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    picked_url = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(picked_url, json={"picked": True}).status_code == 200
    route = f"/api/v1/etsy-spy/agents/{agent.id}/sms-status"
    now = datetime.now(timezone.utc).isoformat()
    present = {"listingId": "1234567890", "status": "sent",
               "smsCode": "307", "smsId": "idea-123",
               "sentAt": now, "updatedAt": now}
    assert client.post(route, json={"items": [present]}).status_code == 401
    assert client.post(route, headers=headers, json={"items": [
        {**present, "smsCode": None, "smsId": None},
    ]}).status_code == 422
    assert client.post(route, headers=headers, json={"items": [
        present, present
    ]}).status_code == 422
    response = client.post(route, headers=headers, json={"items": [present]})
    assert response.status_code == 200, response.text
    assert response.json()["updated"] == 1
    rows = client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["items"]
    assert rows and rows[0]["smsPush"]["status"] == "sent"
    assert rows[0]["smsPush"]["code"] == "307"
    # A late local snapshot must never turn a confirmed SMS receipt into pending.
    stale = {"listingId": "1234567890", "status": "pending", "updatedAt": now}
    assert client.post(route, headers=headers, json={"items": [stale]}).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["items"][0]["smsPush"]["status"] == "sent"
    # Re-Pick preserves status by Listing ID, not a particular Picked row.
    assert client.put(picked_url, json={"picked": False}).status_code == 200
    assert client.put(picked_url, json={"picked": True}).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["items"][0]["smsPush"]["status"] == "sent"
    # Cross-tenant agent cannot write a status for this tenant's listing.
    other_agent, other_headers = pair(client, db, tenant="other-tenant")
    assert client.post(f"/api/v1/etsy-spy/agents/{other_agent.id}/sms-status",
                       headers=other_headers, json={"items": [present]}).json()["updated"] == 0
    assert db.query(EtsySpySmsStatus).count() == 1
    assert client.get("/api/v1/etsy-spy/listings?pick_state=eligible").json()["items"] == []


def test_win_keyword_notes_are_tenant_scoped_persistent_and_have_no_side_effects(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    baseline = client.get("/api/v1/etsy-spy/summary").json()
    baseline_listings = client.get("/api/v1/etsy-spy/listings").json()["total"]
    url = "/api/v1/etsy-spy/win-keywords"

    empty = client.get(url)
    assert empty.status_code == 200 and empty.json() == {"keywords": [], "count": 0}
    saved = client.post(url, json={"keywords": [
        "  Popular   Now! ", "POPULAR now!", "Santa Claus / Rudolph",
    ]})
    assert saved.status_code == 200, saved.text
    assert saved.json()["count"] == 2
    assert saved.json()["keywords"] == ["Popular Now!", "Santa Claus / Rudolph"]
    assert db.query(EtsySpyWinKeyword).count() == 2
    assert client.get(url).json()["keywords"] == saved.json()["keywords"]

    # Notes must not change qualification, blacklist, Pick, summary, or exports.
    assert client.get("/api/v1/etsy-spy/summary").json() == baseline
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == baseline_listings
    assert client.get("/api/v1/etsy-spy/blacklist").json()["phrases"] == []
    assert "Rudolph T-Shirt" in client.get("/api/v1/etsy-spy/export.csv").text
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] == 0

    # Reload-safe, idempotent and tenant-isolated; viewers cannot write.
    assert client.post(url, json={"keywords": ["popular now!"]}).json()["count"] == 2
    principal.active_tenant_id = "other-tenant"
    assert client.get(url).json()["count"] == 0
    assert client.post(url, json={"keywords": ["ANOTHER"]}).json()["count"] == 1
    principal.active_tenant_id = "boostgame"
    assert client.get(url).json()["count"] == 2
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.post(url, json={"keywords": ["Unauthorised"]}).status_code == 403
    assert client.request("DELETE", url, json={"keyword": "Popular Now!"}).status_code == 403
    principal.effective_permissions = frozenset({"etsy_spy.read", "etsy_spy.manage"})

    assert client.post(url, json={"keywords": []}).status_code == 422
    assert client.post(url, json={"keywords": ["x"]}).status_code == 422
    assert client.post(url, json={"keywords": [123]}).status_code == 422
    assert client.post(url, json={"keywords": ["Z" * 221]}).status_code == 422
    removed = client.request("DELETE", url, json={"keyword": "POPULAR NOW!"})
    assert removed.status_code == 200 and removed.json() == {
        "keywords": ["Santa Claus / Rudolph"], "count": 1}
    assert client.request("DELETE", url, json={"keyword": "POPULAR NOW!"}).json()["count"] == 1
    assert client.get("/api/v1/etsy-spy/summary").json() == baseline


def test_parent_keywords_saved_priorities_and_agent_sync(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    url = "/api/v1/etsy-spy/parent-keywords"
    remote = f"/api/v1/etsy-spy/agents/{agent.id}/parent-keywords"
    db.add(EtsySpyKeyword(
        tenant_id="boostgame", agent_id=agent.id, source_session_id="seed-session",
        node_id="root-1", parent_id=None, depth=0, priority=50,
        keyword="Santa Claus Rudolph boop shirt", status="done",
    ))
    db.commit()
    original = client.get(url)
    assert original.status_code == 200
    assert original.json()["items"][0]["source"] == "observed"
    assert original.json()["items"][0]["priority"] == 50
    assert client.get(remote, headers=headers).json()["items"] == original.json()["items"]
    assert client.get(remote).status_code == 401

    edited = client.put(url, json={"keyword": "SANTA CLAUS RUDOLPH BOOP SHIRT",
                                   "priority": 90})
    assert edited.status_code == 200
    assert edited.json()["items"][0]["priority"] == 90
    assert edited.json()["items"][0]["source"] == "observed"
    normal = client.put(url, json={
        "keyword": "Santa Claus Rudolph boop shirt", "priority": 0,
    })
    assert normal.status_code == 200
    assert normal.json()["items"][0]["priority"] == 0
    assert client.get(remote, headers=headers).json()["items"][0]["priority"] == 0
    restored = client.put(url, json={
        "keyword": "Santa Claus Rudolph boop shirt", "priority": 90,
    })
    assert restored.status_code == 200
    assert restored.json()["items"][0]["priority"] == 90
    added = client.post(url, json={"keyword": "  Halloween   Ghost Shirt ",
                                  "priority": 75})
    assert added.status_code == 200
    assert added.json()["count"] == 2
    assert added.json()["items"][1]["keyword"] == "Halloween Ghost Shirt"
    assert added.json()["items"][1]["source"] == "manual"
    assert client.post(url, json={"keyword": "halloween ghost shirt",
                                  "priority": 99}).json()["count"] == 2
    assert db.query(EtsySpyParentKeyword).count() == 2
    assert db.query(EtsySpyKeyword).one().priority == 50
    remote_items = client.get(remote, headers=headers)
    assert remote_items.status_code == 200
    assert remote_items.json()["items"][0]["priority"] == 99
    assert remote_items.json()["revision"] != original.json()["revision"]

    for invalid in (-1, 101, 3.5, True, "99"):
        assert client.post(url, json={"keyword": "valid", "priority": invalid}).status_code == 422
    assert client.post(url, json={"keyword": "x", "priority": 50}).status_code == 422
    assert client.post(url, json={"keyword": "a" * 181, "priority": 50}).status_code == 422
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.put(url, json={"keyword": "Halloween", "priority": 1}).status_code == 403
    principal.active_tenant_id = "another-tenant"
    assert client.get(url).json()["count"] == 0
    principal.active_tenant_id = "boostgame"
    assert client.get(url).json()["count"] == 2


def test_picked_agent_feed_is_full_tenant_scoped_versioned_and_revocable(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    url = f"/api/v1/etsy-spy/agents/{agent.id}/picked-listings"
    assert client.get(url).status_code == 401
    empty = client.get(url, headers=headers)
    assert empty.status_code == 200, empty.text
    assert empty.headers["cache-control"] == "no-store"
    baseline = empty.json()
    assert baseline["ok"] and baseline["complete"]
    assert baseline["items"] == [] and baseline["total"] == 0
    assert len(baseline["revision"]) == 64

    assert sync(client, agent, headers, batch()).status_code == 200
    pick_url = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(pick_url, json={"picked": True}).status_code == 200
    picked = client.get(url, headers=headers).json()
    assert picked["complete"] is True and picked["total"] == 1
    assert picked["revision"] != baseline["revision"]
    assert picked["items"][0]["listingId"] == "1234567890"
    assert picked["items"][0]["pickedAt"].endswith("Z")
    assert client.get(url, headers=headers).json() == picked

    # One complete feed includes all picks, sorted by numeric ID, regardless
    # of whether they are still in the active Windows inventory.
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234567891",
                         picked_at=datetime(2026, 11, 9, tzinfo=timezone.utc)))
    db.commit()
    ordered = client.get(url, headers=headers).json()
    assert [x["listingId"] for x in ordered["items"]] == ["1234567890", "1234567891"]
    assert ordered["total"] == 2 and ordered["revision"] != picked["revision"]

    other_agent, other_headers = pair(client, db, tenant="another-tenant")
    other_url = f"/api/v1/etsy-spy/agents/{other_agent.id}/picked-listings"
    other_snapshot = client.get(other_url, headers=other_headers).json()
    assert other_snapshot["total"] == 0 and other_snapshot["revision"] == baseline["revision"]
    assert client.get(other_url, headers=headers).status_code == 401

    # Local malformed historic rows must be rejected instead of publishing
    # an apparently complete snapshot that could clear valid Windows picks.
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="invalid-id",
                         picked_at=datetime(2026, 11, 9, tzinfo=timezone.utc)))
    db.commit()
    assert client.get(url, headers=headers).status_code == 409
    db.query(EtsySpyPicked).filter_by(tenant_id="boostgame", listing_key="invalid-id").delete()
    db.commit()

    assert client.put(pick_url, json={"picked": False}).status_code == 200
    remaining = client.get(url, headers=headers).json()
    assert remaining["total"] == 1 and remaining["items"][0]["listingId"] == "1234567891"

    assert client.post(f"/api/v1/etsy-spy/agents/{agent.id}/revoke").status_code == 200
    assert client.get(url, headers=headers).status_code == 401


def test_admin_pair_secret_only_once_and_reader_cannot_pair(scenario):
    client, db, principal = scenario
    assert client.post("/api/v1/etsy-spy/agents",
        json={"name": "CAM Image Agent / counterfeit"}).status_code == 422
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


def test_summary_picked_source_image_completeness_tracks_qualified_distinct_listings(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch("image-source-1")).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"] == {
        "checked": 0, "complete": 0, "missing": 0,
    }
    pick_url = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(pick_url, json={"picked": True}).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"] == {
        "checked": 1, "complete": 0, "missing": 1,
    }
    partial = batch("image-source-2")
    partial["records"]["listings"][0]["updatedAt"] = "2026-11-06T00:00:05Z"
    partial["records"]["listings"][0]["thumbnailUrl"] = (
        "https://i.etsystatic.com/123/il_170x135.test.jpg"
    )
    assert sync(client, agent, headers, partial).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"]["missing"] == 1

    full = batch("image-source-3")
    full["records"]["listings"][0]["updatedAt"] = "2026-11-06T00:00:10Z"
    full["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.test.jpg"
    )
    assert sync(client, agent, headers, full).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"] == {
        "checked": 1, "complete": 1, "missing": 0,
    }
    assert client.put(pick_url, json={"picked": False}).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"] == {
        "checked": 0, "complete": 0, "missing": 0,
    }
    principal.active_tenant_id = "another-tenant"
    assert client.get("/api/v1/etsy-spy/summary").json()["picked_images"] == {
        "checked": 0, "complete": 0, "missing": 0,
    }


def test_listing_thumbnail_and_full_image_urls_sync_without_binary_or_data_loss(scenario):
    client, db, _principal = scenario
    agent, headers = pair(client, db)
    image = batch("image-batch-1")
    item = image["records"]["listings"][0]
    item["thumbnailUrl"] = "https://i.etsystatic.com/123/il_170x135.primary.jpg"
    item["fullImageUrl"] = "https://i.etsystatic.com/123/il_fullxfull.primary.jpg"
    response = sync(client, agent, headers, image)
    assert response.status_code == 200, response.text
    first = client.get("/api/v1/etsy-spy/listings").json()["items"][0]
    assert first["thumbnailUrl"] == item["thumbnailUrl"]
    assert first["fullImageUrl"] == item["fullImageUrl"]
    assert first["metadata"]["thumbnailUrl"] == item["thumbnailUrl"]

    # A newer Windows checkpoint lacking image fields preserves old URLs.
    missing = batch("image-batch-2")
    missing["records"]["listings"][0]["updatedAt"] = "2026-11-06T00:00:10Z"
    assert sync(client, agent, headers, missing).status_code == 200
    retained = client.get("/api/v1/etsy-spy/listings").json()["items"][0]
    assert retained["thumbnailUrl"] == item["thumbnailUrl"]
    assert retained["fullImageUrl"] == item["fullImageUrl"]

    # Neither untrusted origins nor data/blob URLs may reach the API response.
    invalid = batch("image-batch-3")
    invalid_item = invalid["records"]["listings"][0]
    invalid_item["updatedAt"] = "2026-11-06T00:00:20Z"
    invalid_item["thumbnailUrl"] = "https://evil.example.org/img.jpg"
    invalid_item["fullImageUrl"] = "data:image/svg+xml;base64,PHN2Zz4="
    assert sync(client, agent, headers, invalid).status_code == 200
    still_safe = client.get("/api/v1/etsy-spy/listings").json()["items"][0]
    assert still_safe["thumbnailUrl"] == item["thumbnailUrl"]
    assert still_safe["fullImageUrl"] == item["fullImageUrl"]


def test_sync_is_idempotent_and_title_only_tm_conflicts(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    first_payload = batch()
    first = sync(client, agent, headers, first_payload)
    assert first.status_code == 200, first.text
    assert first.json()["rows"] == 5
    replay = sync(client, agent, headers, first_payload)
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


def test_observed_day_buckets_start_at_6am_vietnam_and_follow_visible_eligibility(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    today = datetime.now(vn).date()
    today_start = datetime.combine(today, time(6), tzinfo=vn).astimezone(timezone.utc)
    def utc_iso(moment):
        return moment.isoformat().replace("+00:00", "Z")
    observations = [
        ("1234567890", today_start - timedelta(seconds=1)),
        ("1234567891", today_start),
        ("1234567892", today_start + timedelta(days=1, seconds=-1)),
        ("1234567893", today_start + timedelta(days=1)),
    ]
    payload = batch("observed-buckets")
    payload["records"]["listings"] = [
        {**product(title=f"Rudolph shirt {index}"), "listingKey": listing_id,
         "url": f"https://www.etsy.com/listing/{listing_id}/test",
         "observedAt": utc_iso(moment)}
        for index, (listing_id, moment) in enumerate(observations)
    ]
    assert sync(client, agent, headers, payload).status_code == 200
    base = "/api/v1/etsy-spy/listings"
    yesterday = (today - timedelta(days=1)).isoformat()
    today_key = today.isoformat()
    tomorrow = (today + timedelta(days=1)).isoformat()
    assert client.get(base, params={"observed_day": yesterday}).json()["total"] == 1
    today_result = client.get(base, params={"observed_day": today_key}).json()
    assert today_result["total"] == 2
    assert {row["listingKey"] for row in today_result["items"]} == {
        "1234567891", "1234567892"}
    assert client.get(base, params={"observed_day": tomorrow}).json()["total"] == 1
    assert client.get(base, params={"observed_day": "not-a-date"}).status_code == 422
    assert client.get(base, params={"observed_day": "2026-02-30"}).status_code == 422
    csv_text = client.get("/api/v1/etsy-spy/export.csv",
                          params={"observed_day": today_key}).text
    assert "1234567891" in csv_text and "1234567892" in csv_text
    assert "1234567890" not in csv_text and "1234567893" not in csv_text

    buckets = client.get("/api/v1/etsy-spy/listings/observed-days")
    assert buckets.status_code == 200, buckets.text
    body = buckets.json()
    assert body["timezone"] == "Asia/Ho_Chi_Minh" and body["start_hour"] == 6
    assert len(body["items"]) == 10
    assert body["items"][0] == {"day": today_key, "count": 2, "pickedCount": 0}
    assert body["items"][1] == {"day": yesterday, "count": 1, "pickedCount": 0}
    assert len({entry["day"] for entry in body["items"]}) == 10

    # A Pick removes the listing from both the daily card and Eligible listings.
    picked = client.put(base + "/1234567891/pick", json={"picked": True})
    assert picked.status_code == 200, picked.text
    assert client.get(base, params={"observed_day": today_key}).json()["total"] == 1
    # Freeze the Pick action within this 06:00 business day. Running the test
    # between midnight and 06:00 local time otherwise assigns it to yesterday.
    pick_record = db.get(EtsySpyPicked, ("boostgame", "1234567891"))
    assert pick_record is not None
    pick_record.picked_at = today_start
    db.commit()
    assert client.get(base, params={
        "observed_day": today_key, "pick_state": "picked"}).json()["total"] == 1
    updated = client.get(base + "/observed-days").json()["items"]
    assert updated[0] == {"day": today_key, "count": 1, "pickedCount": 1}
    assert updated[1] == {"day": yesterday, "count": 1, "pickedCount": 0}
    # Pick time, not original product observation, determines the Pick bucket.
    pick_record.picked_at = today_start - timedelta(microseconds=1)
    db.commit()
    boundary = client.get(base + "/observed-days").json()["items"]
    assert boundary[0]["pickedCount"] == 0
    assert boundary[1]["pickedCount"] == 1
    pick_record.picked_at = today_start
    db.commit()

    # Blacklist hides eligible observations but never erases the Pick decision.
    assert client.post("/api/v1/etsy-spy/blacklist",
                       json={"phrases": ["Rudolph"]}).status_code == 200
    blacklisted_bucket = client.get(base + "/observed-days").json()["items"][0]
    assert blacklisted_bucket["count"] == 0
    assert blacklisted_bucket["pickedCount"] == 1
    assert client.get(base, params={"observed_day": today_key}).json()["total"] == 0
    assert client.delete("/api/v1/etsy-spy/blacklist/rudolph").status_code == 200
    assert client.get(base + "/observed-days").json()["items"][0]["count"] == 1
    assert client.put(base + "/1234567891/pick", json={"picked": False}).status_code == 200
    assert client.get(base + "/observed-days").json()["items"][0]["pickedCount"] == 0



def test_daily_topic_badges_count_unique_listings_and_filter_by_day(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    today = datetime.now(vn).date()
    start = datetime.combine(today, time(6), tzinfo=vn).astimezone(timezone.utc)
    observed_at = (start + timedelta(hours=1)).isoformat()
    rows = [
        ("1234567890", "Santa's Favorite Sweatshirt", "Santa Shirt"),
        ("1234567891", "Christmas Film Shirt", "White Christmas Movie"),
        ("1234567892", "Family Thanksgiving shirt", "Family"),
        ("1234567893", "Reading Bookworm Tee", "Bookworm"),
        ("1234567894", "Gaming Shirt", "Video Games Shirt"),
        ("1234567895", "Silly Goose Graphic", "Goose"),
        ("1234567896", "Unknown Illustrated Tee", "Abstract design shirt"),
    ]
    payload = batch("day-group-primary")
    payload["records"]["listings"] = [
        {**product(title=title, keyword=keyword), "listingKey": key,
         "url": f"https://www.etsy.com/listing/{key}/test",
         "observedAt": observed_at, "updatedAt": observed_at}
        for key, title, keyword in rows
    ]
    assert sync(client, agent, headers, payload).status_code == 200
    duplicate = batch("day-group-repeat")
    duplicate["records"]["listings"] = [
        {**product(title="Unknown Illustrated Tee", keyword="Christmas T-shirt",
                   session="second-source"), "listingKey": "1234567896",
         "url": "https://www.etsy.com/listing/1234567896/test",
         "observedAt": observed_at, "updatedAt": observed_at},
    ]
    assert sync(client, agent, headers, duplicate).status_code == 200

    day = today.isoformat()
    path = "/api/v1/etsy-spy/listings"
    grouped = client.get(path + "/observed-day-groups", params={"day": day})
    assert grouped.status_code == 200, grouped.text
    data = grouped.json()
    assert data["day"] == day and data["total"] == 6
    counts = {entry["key"]: entry["count"] for entry in data["items"]}
    assert counts == {
        "christmas": 3, "books": 1,
        "gaming": 1, "animals": 1,
    }
    # Seasonal titles are automatically routed to Later, not Eligible.
    later_grouped = client.get(path + "/observed-day-groups", params={
        "day": day, "pick_state": "later"}).json()
    assert later_grouped["total"] == 1
    assert {entry["key"]: entry["count"] for entry in later_grouped["items"]} == {
        "thanksgiving": 1,
    }
    assert sum(counts.values()) == data["total"]
    # /listings can return repeated Search/Shop rows. Daily cards and
    # topic badges deliberately count distinct Etsy Listing IDs.
    unfiltered = client.get(path, params={"observed_day": day}).json()
    assert len({entry["listingKey"] for entry in unfiltered["items"]}) == 6
    christmas = client.get(path, params={
        "observed_day": day, "group": "christmas"}).json()
    assert christmas["total"] >= 3  # One listing may have multiple source rows.
    assert {item["listingKey"] for item in christmas["items"]} == {
        "1234567890", "1234567891", "1234567896"}
    assert "1234567892" not in {item["listingKey"] for item in christmas["items"]}
    csv = client.get("/api/v1/etsy-spy/export.csv", params={
        "observed_day": day, "group": "books"})
    assert csv.status_code == 200 and "1234567893" in csv.text
    assert "1234567890" not in csv.text
    assert client.get(path, params={"group": "christmas"}).status_code == 422
    assert client.get(path, params={"observed_day": day, "group": "fake"}).status_code == 422
    assert client.get(path + "/observed-day-groups", params={
        "day": "not-a-date"}).status_code == 422
    assert client.put(path + "/1234567890/pick",
                      json={"picked": True}).status_code == 200
    after_pick = client.get(path + "/observed-day-groups",
                            params={"day": day}).json()
    assert after_pick["total"] == 5
    assert {g["key"]: g["count"] for g in after_pick["items"]}["christmas"] == 2
    # Future Scout inventory pushes inherit the same deterministic category
    # rules immediately, without any manual categorization/backfill job.
    fresh_batch = batch("day-group-future-sync")
    fresh_batch["records"]["listings"] = [
        {**product(title="Merry Christmas Reindeer Tee",
                   keyword="Christmas reindeer artwork"),
         "listingKey": "1234567897",
         "url": "https://www.etsy.com/listing/1234567897/test",
         "observedAt": observed_at, "updatedAt": observed_at},
        {**product(title="Abstract Contemporary Illustrated Tee",
                   keyword="Minimal design print"),
         "listingKey": "1234567898",
         "url": "https://www.etsy.com/listing/1234567898/test",
         "observedAt": observed_at, "updatedAt": observed_at},
    ]
    assert sync(client, agent, headers, fresh_batch).status_code == 200
    after_sync = client.get(path + "/observed-day-groups",
                            params={"day": day}).json()
    assert after_sync["total"] == 7
    latest_groups = {item["key"]: item["count"] for item in after_sync["items"]}
    assert latest_groups["christmas"] == 3
    assert latest_groups["other"] == 1
    assert sum(latest_groups.values()) == after_sync["total"]
    christmas_latest = client.get(path, params={
        "observed_day": day, "group": "christmas"}).json()
    assert "1234567897" in {item["listingKey"] for item in christmas_latest["items"]}
    other_latest = client.get(path, params={
        "observed_day": day, "group": "other"}).json()
    assert {item["listingKey"] for item in other_latest["items"]} == {"1234567898"}

    # Classification and visibility are tenant-scoped.
    principal.active_tenant_id = "other-tenant"
    assert client.get(path + "/observed-day-groups",
                      params={"day": day}).json()["items"] == []


def test_picked_and_later_date_groups_follow_parent_state_and_action_time(scenario):
    """Parent -> day -> theme -> listing must use one consistent scoped dataset."""
    client, db, principal = scenario
    agent, headers = pair(client, db)
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    today = datetime.now(vn).date()
    start = datetime.combine(today, time(6), tzinfo=vn).astimezone(timezone.utc)
    yesterday = (today - timedelta(days=1)).isoformat()
    today_key = today.isoformat()
    earlier = start - timedelta(hours=1)
    later = start + timedelta(hours=1)
    rows = [
        ("1234600001", "Santa Christmas Favorite", "Santa Gift", earlier),
        ("1234600002", "Bookworm Reading Tee", "Reading Club", later),
        ("1234600003", "Silly Goose Graphic", "Goose Gift", earlier),
        ("1234600004", "Thanksgiving Turkey Day", "Thanksgiving", later),
    ]
    payload = batch("parent-scoped-dates")
    payload["records"]["listings"] = [
        {**product(title=title, keyword=keyword), "listingKey": key,
         "url": f"https://www.etsy.com/listing/{key}/test",
         "observedAt": observed.isoformat(), "updatedAt": observed.isoformat()}
        for key, title, keyword, observed in rows
    ]
    # The same Etsy ID in a second source must not double a date or group.
    payload["records"]["listings"].append({
        **product(title="Santa Christmas Favorite", keyword="Santa Gift (Shop)"),
        "listingKey": "1234600001", "sourceKind": "shop",
        "url": "https://www.etsy.com/listing/1234600001/test",
        "observedAt": earlier.isoformat(), "updatedAt": earlier.isoformat(),
    })
    assert sync(client, agent, headers, payload).status_code == 200
    path = "/api/v1/etsy-spy/listings"
    for key in ("1234600001", "1234600002"):
        assert client.put(path + "/" + key + "/pick", json={"picked": True}).status_code == 200
    db.get(EtsySpyPicked, ("boostgame", "1234600001")).picked_at = later
    db.get(EtsySpyPicked, ("boostgame", "1234600002")).picked_at = earlier
    db.add(EtsySpyLaterMatch(tenant_id="boostgame", listing_key="1234600003",
        score=90, reason="Goose theme", state="matched", updated_at=later))
    db.add(EtsySpyLaterMatch(tenant_id="boostgame", listing_key="1234600004",
        score=90, reason="Thanksgiving", state="matched", updated_at=earlier))
    db.commit()

    picked_days = client.get(path + "/observed-days", params={"pick_state": "picked"})
    later_days = client.get(path + "/observed-days", params={"pick_state": "later"})
    assert picked_days.status_code == 200 and later_days.status_code == 200
    assert picked_days.json()["pick_state"] == "picked"
    assert later_days.json()["pick_state"] == "later"
    assert [day["count"] for day in picked_days.json()["items"][:2]] == [1, 1]
    assert [day["count"] for day in later_days.json()["items"][:2]] == [1, 1]

    for state, day, category, expected in [
        ("picked", today_key, "christmas", {"1234600001"}),
        ("picked", yesterday, "books", {"1234600002"}),
        ("later", today_key, "animals", {"1234600003"}),
        ("later", yesterday, "thanksgiving", {"1234600004"}),
    ]:
        group = client.get(path + "/observed-day-groups", params={
            "day": day, "pick_state": state,
        })
        assert group.status_code == 200, group.text
        result = group.json()
        assert result["pick_state"] == state and result["day"] == day
        assert result["total"] == 1
        assert {item["key"]: item["count"] for item in result["items"]} == {category: 1}
        listing_response = client.get(path, params={
            "pick_state": state, "observed_day": day, "group": category,
        })
        assert listing_response.status_code == 200, listing_response.text
        assert {item["listingKey"] for item in listing_response.json()["items"]} == expected
        # The exact same date and group in another parent cannot leak records.
        other = "later" if state == "picked" else "picked"
        mismatched = client.get(path, params={
            "pick_state": other, "observed_day": day, "group": category,
        }).json()
        assert mismatched["total"] == 0

    # The selected parent is still the authority when a user Picks a Later match.
    assert client.put(path + "/1234600003/pick", json={"picked": True}).status_code == 200
    assert client.get(path + "/observed-day-groups", params={
        "day": today_key, "pick_state": "later",
    }).json()["total"] == 0
    assert client.get(path, params={
        "pick_state": "later", "observed_day": today_key, "group": "animals",
    }).json()["total"] == 0

    for endpoint in (path + "/observed-days", path + "/observed-day-groups"):
        assert client.get(endpoint, params={"pick_state": "unknown",
            **({"day": today_key} if endpoint.endswith("groups") else {})}).status_code == 422
    principal.active_tenant_id = "other-tenant"
    assert client.get(path + "/observed-days", params={
        "pick_state": "picked",
    }).json()["items"][0]["count"] == 0
    assert client.get(path + "/observed-day-groups", params={
        "day": today_key, "pick_state": "later",
    }).json()["total"] == 0


def test_later_is_topic_seed_with_related_current_and_future_listing_ids(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    initial = batch("later-existing")
    initial["records"]["listings"] = [
        {**product(keyword="houseplant collection",
                   title="Oops I Got Another Plant Shirt Gardening Sweatshirt"),
         "listingKey": "1234500001"},
        {**product(keyword="potted plant shirts",
                   title="Botanical Succulent Lover Apparel Cute Tee"),
         "listingKey": "1234500002"},
        {**product(keyword="books gifts",
                   title="Cozy Book Lover Shirt Fall Teacher Gift"),
         "listingKey": "1234500003"},
        {**product(keyword="shirt template",
                   title="Comfort Colors Gildan Funny Graphic Tee Gift"),
         "listingKey": "1234500004"},
    ]
    for item in initial["records"]["listings"]:
        item["url"] = "https://www.etsy.com/listing/" + item["listingKey"]
    assert sync(client, agent, headers, initial).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings"
    assert client.get(endpoint).json()["total"] == 4
    created = client.put(endpoint + "/1234500001/later", json={"later": True})
    assert created.status_code == 200, created.text

    summary = client.get("/api/v1/etsy-spy/summary").json()
    assert summary["later"] == 2 and summary["later_seeds"] == 1
    assert summary["listings"] == 2 and summary["picked"] == 0
    result = client.get(endpoint, params={"pick_state": "later"}).json()
    assert result["total"] == 2
    items = {item["listingKey"]: item for item in result["items"]}
    assert set(items) == {"1234500001", "1234500002"}
    assert items["1234500001"]["laterManual"] is True
    assert items["1234500002"]["laterManual"] is False
    assert "chủ đề" in items["1234500002"]["laterReason"]
    assert client.get(endpoint, params={"pick_state": "eligible"}).json()["total"] == 2

    # One future listing comes back via two different keywords and source
    # observations, but has exactly one Later table row.
    fresh = batch("later-future")
    fresh["records"]["listings"] = [
        {**product(keyword="plant obsession",
                   title="Gardener Houseplant Obsession Sweatshirt"),
         "listingKey": "1234500005"},
        {**product(keyword="botanical gift",
                   title="Gardener Houseplant Obsession Sweatshirt"),
         "listingKey": "1234500005", "sourceKind": "shop"},
        {**product(keyword="general gift",
                   title="Christmas Family Matching Gift Funny Shirt"),
         "listingKey": "1234500006"},
    ]
    for item in fresh["records"]["listings"]:
        item["url"] = "https://www.etsy.com/listing/" + item["listingKey"]
    assert sync(client, agent, headers, fresh).status_code == 200
    future = client.get(endpoint, params={"pick_state": "later"}).json()
    assert future["total"] == 3
    assert len({row["listingKey"] for row in future["items"]}) == 3
    assert "1234500006" not in {row["listingKey"] for row in future["items"]}
    # Picking is a higher-priority decision than Later.
    assert client.put(endpoint + "/1234500005/pick", json={"picked": True}).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 2
    assert client.get(endpoint, params={"pick_state": "picked"}).json()["total"] == 2  # two source rows, one Etsy ID
    # Future syncing does not erase matching metadata or user Pick decisions.
    assert sync(client, agent, headers, batch("later-other")).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 2
    # Automatically added result may be dismissed permanently.
    assert client.put(endpoint + "/1234500002/later", json={"later": False}).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 1
    assert sync(client, agent, headers, batch("later-different")).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 1
    # Removing a manual seed dissolves its derived group.
    assert client.put(endpoint + "/1234500001/later", json={"later": False}).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 0
    assert client.get("/api/v1/etsy-spy/summary").json()["later_seeds"] == 0
    assert client.get(endpoint, params={"pick_state": "picked"}).json()["total"] == 2  # two source rows, one Etsy ID
    principal.active_tenant_id = "another-tenant"
    assert client.get("/api/v1/etsy-spy/summary").json()["later"] == 0
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 0


def test_later_rejects_invalid_scope_and_can_reseed_dismissed_product(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch("later-permissions")).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings"
    assert client.put(endpoint + "/abc/later", json={"later": True}).status_code == 422
    assert client.put(endpoint + "/1234567890/later", json={"later": "yes"}).status_code == 422
    assert client.put(endpoint + "/9999999999/later", json={"later": True}).status_code == 404
    principal.active_tenant_id = "another-tenant"
    assert client.put(endpoint + "/1234567890/later", json={"later": True}).status_code == 404
    principal.active_tenant_id = "boostgame"
    assert client.put(endpoint + "/1234567890/later", json={"later": True}).status_code == 200
    assert client.put(endpoint + "/1234567890/later", json={"later": False}).status_code == 200
    assert client.put(endpoint + "/1234567890/later", json={"later": True}).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 1
    assert client.put(endpoint + "/1234567890/pick", json={"picked": True}).status_code == 200
    assert client.get(endpoint, params={"pick_state": "later"}).json()["total"] == 0
    assert client.put(endpoint + "/1234567890/later", json={"later": True}).status_code == 409


def test_later_topic_matching_ignores_shirt_brand_marketing_noise():
    from app.modules.etsy_spy.later_matching import analyze_title, match_title
    assert match_title("Oops I Got Another Plant Shirt", "Botanical Succulent Lover Tee")
    assert match_title("Silly Goose on the Loose Shirt", "Funny Geese Sweatshirt")
    assert match_title("I Wish I Lacked Critical Thinking Skills",
                       "Anxious Overthinker Shirt")
    assert match_title("Rudolph Nose Boop Sweatshirt", "Reindeer Booping Gift Tee")
    assert match_title("Talarico Hinojosa Victory Shirt", "Talarico Hinojosa Texas Tee")
    assert match_title("Comfort Colors Gildan Funny Shirt", "Graphic Tee Gift") is None
    assert match_title("Christmas Family Gift Shirt", "Christmas Funny Teacher Tee") is None
    assert match_title("Halloween Ghost Shirt", "Christmas Ghost Shirt") is None
    assert match_title("Halloween Ghost Shirt", "Ghost Graphic Shirt") is None
    assert match_title("Halloween Moonlit Ghost Shirt",
                       "Halloween Moonlit Ghost Hoodie") is not None
    assert match_title("Spooky Ghost Sweatshirt", "Ghost Graphic Tee") is None
    assert match_title("Plant Shirt", "Goose Shirt") is None
    # Cat/dog are broad animal categories, not proof of the same artwork.
    assert match_title("Embroidered Ghost Cat Sweatshirt, Halloween",
                       "Black Cat Reading Shirt, Cozy Reader") is None
    assert match_title("Vintage 90s Cartoon Dog Shirt",
                       "Dachshund Christmas Shirt, Through the Snow") is None
    assert match_title("Black Cat Reading Shirt, Moonlit Book",
                       "Cat Reading Book Sweatshirt, Moonlit Book") is not None
    assert match_title("Dog Dachshund Snow Shirt",
                       "Dachshund Snow Dog Sweatshirt") is not None
    assert not analyze_title("Comfort Colors Gildan Shirt Gift Tee").terms


@pytest.mark.parametrize("title,keyword,expected", [
    ("Halloween Shirt", "holiday tee", "Halloween"),
    ("Witchy Graphic Shirt", "fall outfits", None),
    ("Cozy Pumpkin Sweatshirt", "autumn", None),
    ("Spooky Ghost Tee", "trick-or-treat", None),
    ("Haunted House Hoodie", "hocus pocus", None),
    ("Harvest Shirt", "GOBBLE GOBBLE turkey day", None),
    ("Friendsgiving Comfort Tee", "family", "Thanksgiving"),
    ("Thanksgiving Graphic Tee", "gifts", "Thanksgiving"),
    ("Halloweentown Tee", "pumpkinhead", None),
    ("Illustrated Plant Tee", "pumpkinhead holiday", None),
    ("Holiday Pumpkin Tee", "summer", None),
    ("Ghost Christmas Shirt", "halloween gift", None),
    ("Christmas Halloween Mix Sweatshirt", "halloween", None),
    ("Christmas Ghost Shirt", "ghost", None),
    ("Halloween Ghost Tee", "christmas shirt", "Halloween"),
    ("Trick-or-Treat Sweatshirt", "christmas", "Halloween"),
    ("Turkey Day Fall Shirt", "christmas", "Thanksgiving"),
])
def test_auto_later_matches_exact_topic_badge_vocabulary(title, keyword, expected):
    from app.modules.etsy_spy.auto_later_keywords import match_auto_later
    assert match_auto_later("boostgame", title, keyword) == expected
    assert match_auto_later("creative-assets-prod", title, keyword) == expected
    assert match_auto_later("other-tenant", title, keyword) is None


def test_auto_later_uses_listing_title_not_source_keyword_or_broad_motif(scenario):
    """Search/Shop aliases and occasion SEO must not hide unrelated products."""
    from app.modules.etsy_spy.auto_later_keywords import batch_auto_later_labels
    assert batch_auto_later_labels("boostgame", [
        ("x", "Halloween Ghost Hoodie", "halloween shirt"),
        ("x", "Christmas Ghost Hoodie", "halloween shirt"),
        ("y", "Happy Thanksgiving Tee", "fall gift"),
        ("y", "Cute Graphic Tee", "friendsgiving"),
        ("z", "Pumpkin Botanical Shirt", "halloween"),
    ]) == {"y": "Thanksgiving"}
    client, db, _ = scenario
    agent, headers = pair(client, db)
    payload = batch("occasion-scope-contrast")
    payload["records"]["listings"] = [
        {**product(title="Christmas Ghost Tee", keyword="halloween spooky shirt"),
         "listingKey": "1234501401"},
        {**product(title="Cute Ghost Sweatshirt", keyword="halloween"),
         "listingKey": "1234501402"},
        {**product(title="Halloween Trick or Treat Hoodie", keyword="holiday"),
         "listingKey": "1234501403"},
        {**product(title="Halloween Ghost Shirt", keyword="halloween"),
         "listingKey": "1234501404"},
        {**product(title="Christmas Ghost Shirt", keyword="winter"),
         "listingKey": "1234501404",
         "sourceKind": "shop"},
        {**product(title="Happy Thanksgiving Shirt", keyword="fall"),
         "listingKey": "1234501405"},
        {**product(title="Pumpkin Botanical Shirt", keyword="Halloween"),
         "listingKey": "1234501406"},
    ]
    for item in payload["records"]["listings"]:
        item["url"] = "https://www.etsy.com/listing/" + item["listingKey"]
    assert sync(client, agent, headers, payload).status_code == 200
    base = "/api/v1/etsy-spy/listings"
    eligible = client.get(base, params={"pick_state": "eligible"})
    later = client.get(base, params={"pick_state": "later"})
    assert eligible.status_code == 200 and later.status_code == 200
    assert {row["listingKey"] for row in eligible.json()["items"]} == {
        "1234501401", "1234501402", "1234501404", "1234501406",
    }
    assert {row["listingKey"] for row in later.json()["items"]} == {
        "1234501403", "1234501405",
    }
    assert {row["laterAutoKeyword"] for row in later.json()["items"]} == {
        "Halloween", "Thanksgiving",
    }


def test_auto_later_badge_groups_leave_eligible_and_follow_day_after_sync(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    vn = ZoneInfo("Asia/Ho_Chi_Minh")
    day = datetime.now(vn).date()
    at = (datetime.combine(day, time(6), tzinfo=vn)
          .astimezone(timezone.utc) + timedelta(hours=1)).isoformat()
    payload = batch("auto-season-day-badges")
    payload["records"]["listings"] = [
        {**product(title="Funny Turkey Day Shirt", keyword="fall harvest"),
         "listingKey": "1234500901", "observedAt": at, "updatedAt": at},
        {**product(title="Spooky Ghost Tee", keyword="autumn clothes"),
         "listingKey": "1234500902", "observedAt": at, "updatedAt": at},
        {**product(title="Pumpkin Lover Tee", keyword="autumn"),
         "listingKey": "1234500903", "observedAt": at, "updatedAt": at},
        {**product(title="Botanical Illustration Shirt", keyword="plant"),
         "listingKey": "1234500904", "observedAt": at, "updatedAt": at},
    ]
    for listing in payload["records"]["listings"]:
        listing["url"] = "https://www.etsy.com/listing/" + listing["listingKey"]
    assert sync(client, agent, headers, payload).status_code == 200
    base = "/api/v1/etsy-spy/listings"
    day_params = {"day": day.isoformat()}
    eligible = client.get(base + "/observed-day-groups", params=day_params).json()
    assert eligible["total"] == 3
    assert {item["key"]: item["count"] for item in eligible["items"]} == {
        "halloween": 2, "other": 1,
    }
    later = client.get(base + "/observed-day-groups", params={
        **day_params, "pick_state": "later",
    }).json()
    assert later["total"] == 1
    assert {item["key"]: item["count"] for item in later["items"]} == {
        "thanksgiving": 1,
    }
    for group, expected in [("thanksgiving", {"1234500901"})]:
        results = client.get(base, params={
            "pick_state": "later", "observed_day": day.isoformat(), "group": group,
        })
        assert results.status_code == 200
        assert {row["listingKey"] for row in results.json()["items"]} == expected


def test_automatic_season_keyword_later_routes_existing_and_future_without_unpick(scenario):
    """Thanksgiving/Halloween are CAM review routing rules, not Scout blacklist."""
    client, db, principal = scenario
    agent, headers = pair(client, db)
    payload = batch("season-current")
    payload["records"]["listings"] = [
        {**product(title="Witchy Graphic Shirt", keyword="witch illustration"),
         "listingKey": "1234500201"},
        {**product(title="Autumn Harvest Tee", keyword="Turkey Day harvest gifts"),
         "listingKey": "1234500202"},
        {**product(title="Classical Friendsgiving Sweatshirt", keyword="fall gifts"),
         "listingKey": "1234500203"},
        {**product(title="Cozy Pumpkin Tee", keyword="autumn collection"),
         "listingKey": "1234500204"},
        {**product(title="Classic Plant Lover Shirt", keyword="botanical tees"),
         "listingKey": "1234500205"},
        # Same Listing ID with no season in this alias: it must NOT leak to Eligible.
        {**product(title="Illustrated Graphic Tee", keyword="minimal illustration"),
         "listingKey": "1234500201", "sourceKind": "shop"},
    ]
    for row in payload["records"]["listings"]:
        row["url"] = "https://www.etsy.com/listing/" + row["listingKey"]
    assert sync(client, agent, headers, payload).status_code == 200
    url = "/api/v1/etsy-spy/listings"
    summary = client.get("/api/v1/etsy-spy/summary").json()
    assert summary["later"] == 1 and summary["listings"] == 4
    later = client.get(url, params={"pick_state": "later"}).json()
    assert later["total"] == 1
    assert {row["listingKey"] for row in later["items"]} == {"1234500203"}
    assert all(row["later"] and not row["picked"] for row in later["items"])
    assert {row["laterAutoKeyword"] for row in later["items"]} == {"Thanksgiving"}
    assert client.get(url, params={"pick_state": "eligible"}).json()["total"] == 5

    # Existing manual Pick is recoverable but has effective Later visibility;
    # no direct deletion of Picked or extraction/related production records.
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234500203"))
    db.add(EtsySpyPicked(tenant_id="boostgame", listing_key="1234500205"))
    db.commit()
    assert db.get(EtsySpyPicked, ("boostgame", "1234500203")) is not None
    assert client.get(url, params={"pick_state": "picked"}).json()["total"] == 1
    assert client.get(url, params={"pick_state": "later"}).json()["total"] == 1
    feed = client.get(f"/api/v1/etsy-spy/agents/{agent.id}/picked-listings",
                      headers=headers)
    assert feed.status_code == 200
    assert {row["listingId"] for row in feed.json()["items"]} == {"1234500205"}
    assert client.put(url + "/1234500203/pick", json={"picked": True}).status_code == 409
    assert client.put(url + "/1234500203/later", json={"later": False}).status_code == 409
    assert client.put(url + "/1234500203/later", json={"later": True}).json()["auto"]

    # A later Windows sync automatically brings a new qualified topic into Later.
    future = batch("season-future")
    future["records"]["listings"] = [
        {**product(title="Hocus Pocus Spooky Sweatshirt", keyword="autumn harvest"),
         "listingKey": "1234500206"},
        {**product(title="Hocus Pocus Spooky Sweatshirt", keyword="seasonal art"),
         "listingKey": "1234500206", "sourceKind": "shop"},
        {**product(title="Book Reading Shirt", keyword="literature"),
         "listingKey": "1234500207"},
    ]
    for row in future["records"]["listings"]:
        row["url"] = "https://www.etsy.com/listing/" + row["listingKey"]
    assert sync(client, agent, headers, future).status_code == 200
    assert client.get(url, params={"pick_state": "later"}).json()["total"] == 1
    assert client.get(url, params={"pick_state": "eligible"}).json()["total"] == 7
    assert client.get(url, params={"pick_state": "picked"}).json()["total"] == 1

    # No cross-tenant leakage: even the same words stay Eligible elsewhere.
    principal.active_tenant_id = "separate-tenant"
    other_agent, other_headers = pair(client, db, tenant="separate-tenant")
    other_payload = batch("season-other-tenant")
    other_payload["records"]["listings"] = [
        {**product(title="Thanksgiving Turkey Shirt", keyword="Thanksgiving"),
         "listingKey": "1234500208"},
    ]
    assert sync(client, other_agent, other_headers, other_payload).status_code == 200
    assert client.get(url, params={"pick_state": "later"}).json()["total"] == 0
    assert client.get(url, params={"pick_state": "eligible"}).json()["total"] == 1


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
def test_carts_20_plus_or_views_above_five_require_source_badge(scenario):
    client, db, _ = scenario
    agent, headers = pair(client, db)
    payload = batch("batch-badge-views-or-carts")
    variants = [
        # source, Search badge, Shop badge, views, bought24h, carts, qualifies
        ("search", "", "", 19, None, 8, False),
        ("search", "Bestseller", "Bestseller", 5, 30, 100, True),
        ("search", "Bestseller", "Bestseller", 6, None, 0, True),
        ("search", "Popular now", "Popular now", 19, None, 8, True),
        ("search", "", "Bestseller", 19, None, 8, False),
        ("shop", "", "Popular now", 6, None, 8, True),
        ("shop", "Bestseller", "", 19, None, 8, False),
        ("search", "Etsy's Pick", "Etsy's Pick", 19, None, 8, False),
        ("search", "Popular now", "Popular now", 5, 30, 19, False),
        ("search", "Popular now", "Popular now", 5, None, 20, True),
        ("shop", "", "Bestseller", None, None, 20, True),
        ("shop", "", "Popular now", 0, 30, 19, False),
        ("shop", "", "", 0, None, 50, False),
        ("shop", "", "Bestseller", None, 30, 19, False),
        ("search", "Popular now", "Popular now", None, None, 20, True),
        ("search", "Bestseller", "Bestseller", None, 30, 19, False),
    ]
    products = []
    for i, (source, search_badge, shop_badge, views, bought, carts, _) in enumerate(variants):
        row = product(title=f"Winter Snow Shirt Variant {i}")
        row["sourceKind"] = source
        row["listingKey"] = str(1234567910 + i)
        row["url"] = f"https://www.etsy.com/listing/{row['listingKey']}/winter-shirt"
        row["searchBadge"] = search_badge
        row["badge"] = shop_badge
        row["views24h"] = views
        row["bought24h"] = bought
        row["inCarts"] = carts
        products.append(row)
    payload["records"]["listings"] = products
    sent = sync(client, agent, headers, payload)
    assert sent.status_code == 200, sent.text
    overview = client.get("/api/v1/etsy-spy/summary").json()
    assert overview["listings"] == 7
    assert overview["search"] == 5
    assert overview["shop"] == 2
    result = client.get("/api/v1/etsy-spy/listings", params={"page_size": 50}).json()
    assert result["total"] == 7
    actual_keys = {item["listingKey"] for item in result["items"]}
    expected_keys = {products[i]["listingKey"] for i, row in enumerate(variants) if row[-1]}
    assert actual_keys == expected_keys
    assert all(
        (item["views24h"] is not None and item["views24h"] > 5)
        or (item["inCarts"] is not None and item["inCarts"] >= 20)
        for item in result["items"]
    )
    assert client.get("/api/v1/etsy-spy/listings", params={"source": "search"}).json()["total"] == 5
    assert client.get("/api/v1/etsy-spy/listings", params={"source": "shop"}).json()["total"] == 2
    csv_text = client.get("/api/v1/etsy-spy/export.csv").text
    for i, row in enumerate(variants):
        title = products[i]["pageTitle"]
        assert (title in csv_text) is row[-1]

def test_pick_moves_eligible_listing_and_unpick_restores_without_touching_sync(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing_id = "1234567890"
    endpoint = f"/api/v1/etsy-spy/listings/{listing_id}/pick"
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 1
    assert client.get("/api/v1/etsy-spy/summary").json()["picked"] == 0
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 1
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] == 0

    assert client.put(endpoint, json={"picked": True}).json() == {
        "listingKey": listing_id, "picked": True,
    }
    assert client.put(endpoint, json={"picked": True}).status_code == 200
    assert db.query(EtsySpyPicked).filter_by(
        tenant_id="boostgame", listing_key=listing_id).count() == 1
    after = client.get("/api/v1/etsy-spy/summary").json()
    assert after["listings"] == 0
    assert after["picked"] == 1
    assert client.get("/api/v1/etsy-spy/listings").json()["items"] == []
    chosen = client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()
    assert chosen["total"] == 1 and chosen["items"][0]["picked"] is True
    assert chosen["items"][0]["listingKey"] == listing_id
    assert listing_id not in client.get("/api/v1/etsy-spy/export.csv").text
    assert listing_id in client.get(
        "/api/v1/etsy-spy/export.csv?pick_state=picked").text
    assert client.get("/api/v1/etsy-spy/listings?pick_state=invalid").status_code == 422

    # New Windows batch for the same Etsy ID does not reset the user's decision.
    later = batch("batch-after-pick")
    later["records"]["listings"][0]["inCarts"] = 28
    assert sync(client, agent, headers, later).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] == 1
    assert client.get("/api/v1/etsy-spy/summary").json()["listings"] == 0
    # Unauthorized readers may see but never change selections.
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.put(endpoint, json={"picked": False}).status_code == 403
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").status_code == 200
    principal.effective_permissions = frozenset({"etsy_spy.read", "etsy_spy.manage"})
    assert client.put(endpoint, json={"picked": False}).status_code == 200
    assert client.put(endpoint, json={"picked": False}).status_code == 200
    final = client.get("/api/v1/etsy-spy/summary").json()
    assert final["listings"] == 1 and final["picked"] == 0
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] == 0
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 1
    assert client.put(endpoint, json={"picked": "yes"}).status_code == 422
    assert client.put("/api/v1/etsy-spy/listings/not-an-id/pick",
                      json={"picked": True}).status_code == 422
    assert client.put("/api/v1/etsy-spy/listings/9999999999/pick",
                      json={"picked": True}).status_code == 404


def test_pick_is_tenant_scoped_across_sources_and_blacklist(scenario):
    client, db, principal = scenario
    first_agent, first_headers = pair(client, db)
    assert sync(client, first_agent, first_headers, batch()).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(endpoint, json={"picked": True}).status_code == 200

    # An independent Shop observation of the same product must follow the same Pick.
    shop = batch("batch-shop-same-product")
    row = shop["records"]["listings"][0]
    row.update({"sourceKind": "shop", "keyword": "Rudolph sweatshirt",
                "badge": "Popular now", "searchBadge": ""})
    assert sync(client, first_agent, first_headers, shop).status_code == 200
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 0
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] >= 1

    other_agent, other_headers = pair(client, db, tenant="other-tenant")
    assert sync(client, other_agent, other_headers, batch("other-batch")).status_code == 200
    principal.active_tenant_id = "other-tenant"
    assert client.get("/api/v1/etsy-spy/summary").json()["picked"] == 0
    assert client.get("/api/v1/etsy-spy/listings").json()["total"] == 1
    principal.active_tenant_id = "boostgame"
    # Blacklist still overrides both the Eligible and Picked tables.
    assert client.post("/api/v1/etsy-spy/blacklist",
                       json={"phrases": ["rudolph"]}).status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked"] == 0
    assert client.get("/api/v1/etsy-spy/listings?pick_state=picked").json()["total"] == 0
    assert db.get(EtsySpyPicked, ("boostgame", "1234567890")) is not None
    assert client.delete("/api/v1/etsy-spy/blacklist/rudolph").status_code == 200
    assert client.get("/api/v1/etsy-spy/summary").json()["picked"] == 1

def test_picked_enqueues_external_image_agent_only_and_preserves_codex(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, principal = scenario
    agent, headers = pair(client, db)
    payload = batch("pick-extraction")
    payload["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.design.jpg"
    )
    assert sync(client, agent, headers, payload).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings/1234567890"
    first = client.put(endpoint + "/pick", json={"picked": True})
    assert first.status_code == 200, first.text
    data = client.get(endpoint + "/extraction").json()
    assert data["status"] == "pending" and data["job_id"]
    job = db.get(ProcessingJobModel, data["job_id"])
    assert job is not None
    assert job.tenant_id == "boostgame"
    assert job.job_type == "etsy_design_extract"
    assert job.job_type not in JOB_TYPES
    assert job.provider_key == "windows_image_agent"
    assert job.payload_json["engine"] == "auto"
    assert job.payload_json["rights_status"] == "unverified"
    assert job.payload_json["manual_qa_required"] is True
    assert job.payload_json["source"]["url"] == payload["records"]["listings"][0]["fullImageUrl"]
    assert client.get(endpoint + "/extraction").json()["job_id"] == job.id
    again = client.put(endpoint + "/pick", json={"picked": True})
    assert client.get(endpoint + "/extraction").json()["job_id"] == job.id
    assert db.query(ProcessingJobModel).count() == 1
    assert client.put(endpoint + "/pick", json={"picked": False}).status_code == 200
    db.refresh(job)
    assert job.cancellation_requested is True
    assert client.get(endpoint + "/extraction").json()["status"] == "not_picked"
    repick = client.put(endpoint + "/pick", json={"picked": True})
    assert repick.status_code == 200
    fresh = client.get(endpoint + "/extraction").json()["job_id"]
    assert fresh != job.id
    db.refresh(job)
    assert job.cancellation_requested is True
    assert db.get(ProcessingJobModel, fresh).cancellation_requested is False
    assert db.query(ProcessingJobModel).count() == 2
    principal.active_tenant_id = "other-tenant"
    assert client.get(endpoint + "/extraction").json()["status"] == "not_picked"


def test_backfill_previously_picked_is_preview_first_and_idempotent(
    scenario, monkeypatch, tmp_path,
):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    payload = batch("historic-picked-for-extraction")
    payload["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.owned.jpg"
    )
    assert sync(client, agent, headers, payload).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings/1234567890"
    # Previously picked while the Windows extraction queue was disabled.
    assert client.put(endpoint + "/pick", json={"picked": True}).status_code == 200
    assert db.query(ProcessingJobModel).count() == 0
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_MIN_FREE_BYTES", "22000000")
    backfill = "/api/v1/etsy-spy/extractions/backfill"
    preview = client.post(backfill, json={"limit": 1})
    assert preview.status_code == 200, preview.text
    assert preview.json()["dry_run"] is True
    assert preview.json()["ready"] == 1
    assert preview.json()["created"] == 0
    assert db.query(ProcessingJobModel).count() == 0

    applied = client.post(backfill, json={"limit": 1, "dry_run": False})
    assert applied.status_code == 200, applied.text
    assert applied.json()["created"] == 1
    assert applied.json()["rights_status"] == "unverified"
    job = db.query(ProcessingJobModel).one()
    assert job.job_type == "etsy_design_extract"
    assert job.payload_json["manual_qa_required"] is True
    assert job.payload_json["print_ready"] is False
    again = client.post(backfill, json={"limit": 1, "dry_run": False})
    assert again.json()["created"] == 0
    assert again.json()["blocked"] == "pilot_job_pending"
    assert db.query(ProcessingJobModel).count() == 1
    principal.active_tenant_id = "other-tenant"
    assert client.post(backfill, json={"limit": 1}).json()["ready"] == 0


def test_backfill_refuses_disabled_worker_and_invalid_limit(
    scenario, monkeypatch, tmp_path,
):
    client, _db, _principal = scenario
    backfill = "/api/v1/etsy-spy/extractions/backfill"
    monkeypatch.delenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", raising=False)
    assert client.post(backfill, json={"limit": 1}).status_code == 503
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_MIN_FREE_BYTES", "22000000")
    for limit in (0, 201, True, "200"):
        assert client.post(backfill, json={"limit": limit}).status_code == 422


def test_picked_without_verified_source_never_invents_an_image(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, _principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch("pick-no-image")).status_code == 200
    reply = client.put("/api/v1/etsy-spy/listings/1234567890/pick", json={"picked": True})
    assert reply.status_code == 200
    assert client.get("/api/v1/etsy-spy/listings/1234567890/extraction").json()["status"] == "source_missing"
    assert db.query(ProcessingJobModel).count() == 0
    assert db.get(EtsySpyPicked, ("boostgame", "1234567890")) is not None


def test_image_agent_dedicated_token_claim_and_receipt_idempotency(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, principal = scenario
    agent, spy_headers = pair(client, db)
    image = batch("image-agent-pilot")
    image["records"]["listings"][0]["fullImageUrl"] = "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    assert sync(client, agent, spy_headers, image).status_code == 200
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick", json={"picked": True}).status_code == 200
    created = client.post("/api/v1/etsy-spy/image-agents", json={"name": "Windows CPU Pilot"})
    assert created.status_code == 201, created.text
    agent_id, token = created.json()["agent_id"], created.json()["token"]
    assert created.json()["tenant_id"] == "boostgame"
    path = f"/api/v1/etsy-spy/image-agents/{agent_id}"
    auth = {"Authorization": "Bearer " + token}
    monkeypatch.delenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", raising=False)
    assert client.post(path + "/claim", headers=auth).status_code == 503
    assert client.post(path + "/claim", headers=spy_headers).status_code == 401
    assert client.post(f"/api/v1/etsy-spy/agents/{agent_id}/sync",
                       json={}, headers=auth).status_code == 403
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    first = client.post(path + "/claim", headers=auth)
    assert first.status_code == 200, first.text
    claim = first.json()
    assert claim["task"]["tenant_id"] == "boostgame"
    assert claim["task"]["attempt_count"] == 1
    assert claim["task"]["source"]["type"] == "etsy_cdn"
    assert claim["task"]["rights_status"] == "unverified"
    replay = client.post(path + "/claim", headers=auth).json()
    assert replay["task"]["id"] == claim["task"]["id"]
    assert replay["lease_token"] == claim["lease_token"]
    # Windows may have been offline beyond the 30-minute lease. Only the
    # originally paired device may renew the same token for the same task.
    leased = db.get(ProcessingJobModel, claim["task"]["id"])
    leased.lease_expires_at = datetime.now(timezone.utc) - timedelta(hours=1)
    db.commit()
    check = client.post(path + "/check-job", json={
        "job_id": leased.id, "lease_token": claim["lease_token"],
    }, headers=auth)
    assert check.status_code == 200, check.text
    assert check.json()["status"] == "processing"
    assert check.json()["attempt_count"] == 1
    assert client.post(path + "/check-job", json={
        "job_id": leased.id, "lease_token": "bad",
    }, headers=auth).status_code == 409
    db.refresh(leased)
    assert leased.lease_expires_at.replace(tzinfo=timezone.utc) > datetime.now(timezone.utc)
    pending = {
        "job_id": claim["task"]["id"], "lease_token": claim["lease_token"],
        "status": "needs_ai", "engine": "ai_review",
    }
    assert client.post(path + "/result", json={**pending,
                "lease_token": "invalid"}, headers=auth).status_code == 403
    result = client.post(path + "/result", json=pending, headers=auth)
    assert result.status_code == 200, result.text
    assert result.json()["ok"] and result.json()["receipt_id"]
    identical = client.post(path + "/result", json=pending, headers=auth)
    assert identical.status_code == 200
    assert identical.json()["receipt_id"] == result.json()["receipt_id"]
    completed = client.post(path + "/check-job", json={
        "job_id": claim["task"]["id"], "lease_token": claim["lease_token"]
    }, headers=auth)
    assert completed.status_code == 200
    assert completed.json()["status"] == "completed"
    assert completed.json()["receipt_id"] == result.json()["receipt_id"]
    listing = client.get("/api/v1/etsy-spy/listings",
                         params={"pick_state": "picked"}).json()["items"][0]
    assert listing["extraction"]["status"] == "needs_ai"
    assert listing["extraction"]["manual_qa_required"] is True
    assert client.post(path + "/claim", headers=auth).json()["task"] is None
    principal.active_tenant_id = "foreign"
    assert client.get("/api/v1/etsy-spy/image-agents").json()["items"] == []
    assert client.get("/api/v1/etsy-spy/image-agents/assets/" + claim["task"]["id"]).status_code == 404


def test_needs_ai_candidate_is_private_review_only_and_keeps_original_receipt(
    scenario, monkeypatch, tmp_path,
):
    import base64
    import binascii
    import hashlib
    import struct
    import zlib

    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "1")
    client, db, principal = scenario
    scout, scout_auth = pair(client, db)
    payload = batch("review-candidate-pilot")
    payload["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg")
    assert sync(client, scout, scout_auth, payload).status_code == 200
    listing_path = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing_path + "/pick", json={"picked": True}).status_code == 200
    first = client.post("/api/v1/etsy-spy/image-agents",
                        json={"name": "Recovery Candidate Pilot"}).json()
    other = client.post("/api/v1/etsy-spy/image-agents",
                        json={"name": "Unrelated Paired Device"}).json()
    base = "/api/v1/etsy-spy/image-agents/"
    worker = base + first["agent_id"]
    auth = {"Authorization": "Bearer " + first["token"]}
    other_auth = {"Authorization": "Bearer " + other["token"]}
    task = client.post(worker + "/claim", headers=auth).json()
    job_id = task["task"]["id"]
    original = client.post(worker + "/result", headers=auth, json={
        "job_id": job_id, "lease_token": task["lease_token"],
        "status": "needs_ai", "engine": "ai_review",
        "source_sha256": "a" * 64,
        "reason": "textured_or_nonuniform_background",
    })
    assert original.status_code == 200
    original_receipt = original.json()["receipt_id"]
    assert db.get(ProcessingJobModel, job_id).status == "completed"

    def chunk(kind, data):
        data_to_sign = kind + data
        return (struct.pack(">I", len(data)) + data_to_sign
                + struct.pack(">I", binascii.crc32(data_to_sign) & 0xFFFFFFFF))

    header = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    scanline = bytes(1 + 4500 * 4)
    png = (bytes([137, 80, 78, 71, 13, 10, 26, 10])
           + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(scanline * 5400, 6))
           + chunk(b"IEND", b""))
    digest = hashlib.sha256(png).hexdigest()
    request = {
        "job_id": job_id,
        "method": "colour_layer_preview_not_ai",
        "source_sha256": "a" * 64,
        "output_sha256": digest,
        "png_base64": base64.b64encode(png).decode(),
        "qa": {
            "source_width": 2500, "source_height": 2389,
            "native_artwork_width": 941, "native_artwork_height": 1133,
            "upscale_factor": 3.81, "foreground_pixels": 356791,
            "manual_visual_review_required": True,
            "not_an_ai_model": True, "ocr_verified": False,
            "print_ready": False, "rights_verified": False,
        },
    }
    endpoint = worker + "/review-candidate"
    assert client.post(endpoint, json=request, headers=other_auth).status_code in (401, 403, 409)
    assert client.post(endpoint, json={**request,
        "source_sha256": "b" * 64}, headers=auth).status_code == 422
    invalid = {**request, "qa": {**request["qa"], "rights_verified": True}}
    assert client.post(endpoint, json=invalid, headers=auth).status_code == 422
    good = client.post(endpoint, json=request, headers=auth)
    assert good.status_code == 200, good.text
    assert good.json()["candidate_ready"] is True
    assert good.json()["original_result_status"] == "needs_ai"
    assert good.json()["print_ready"] is False
    assert client.post(endpoint, json=request, headers=auth).status_code == 200
    assert client.post(endpoint, json={**request,
        "output_sha256": "f" * 64}, headers=auth).status_code == 409
    row = db.get(ProcessingJobModel, job_id)
    db.refresh(row)
    assert row.status == "completed"
    assert row.payload_json["result"]["status"] == "needs_ai"
    assert row.payload_json["result"]["receipt_id"] == original_receipt
    assert row.payload_json["review_candidate"]["output_sha256"] == digest
    assert row.payload_json["review_candidate"]["qa"]["not_an_ai_model"] is True
    status = client.get(listing_path + "/extraction").json()
    assert status["status"] == "needs_ai"
    assert status["candidate_ready"] is True
    assert status["candidate_upscale_factor"] == 3.81
    picked = client.get("/api/v1/etsy-spy/listings",
                        params={"pick_state": "picked"}).json()["items"][0]
    assert picked["extraction"]["candidate_ready"] is True
    assert picked["extraction"]["status"] == "needs_ai"
    assert len(picked["designs"]) == 1
    assert picked["designs"][0]["source"] == "legacy_candidate"
    assert picked["designs"][0]["position"] == "unknown"
    assert picked["designs"][0]["output_sha256"] == digest
    assert picked["designs"][0]["print_ready"] is False
    assert client.get(picked["designs"][0]["original_url"]).content == png
    assert client.get(picked["designs"][0]["thumbnail_url"]).status_code == 200
    assert client.get(base + "assets/" + job_id).status_code == 404
    candidate = client.get(base + "candidates/" + job_id)
    assert candidate.status_code == 200 and candidate.content == png
    assert candidate.headers["cache-control"] == "private, no-store"
    # Candidate upload is not a fresh claim and does not consume another daily slot.
    assert client.post(worker + "/claim", headers=auth).json()["pause_reason"] == "daily_cap_reached"
    principal.active_tenant_id = "foreign"
    assert client.get(base + "candidates/" + job_id).status_code == 404
    principal.active_tenant_id = "boostgame"
    assert client.put(listing_path + "/pick", json={"picked": False}).status_code == 200
    assert client.get(base + "candidates/" + job_id).status_code == 404


def test_image_agent_valid_png_asset_and_unpick_revocation(scenario, monkeypatch, tmp_path):
    import zlib
    import struct
    import binascii
    import base64 as b64
    import hashlib as hashmod
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, _principal = scenario
    agent, headers = pair(client, db)
    payload = batch("image-agent-png")
    payload["records"]["listings"][0]["fullImageUrl"] = "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    assert sync(client, agent, headers, payload).status_code == 200
    base = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(base + "/pick", json={"picked": True}).status_code == 200
    created = client.post("/api/v1/etsy-spy/image-agents", json={"name": "RGBA Pilot"}).json()
    auth = {"Authorization": "Bearer " + created["token"]}
    remote = "/api/v1/etsy-spy/image-agents/" + created["agent_id"]
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    task = client.post(remote + "/claim", headers=auth).json()
    claim_id = task["task"]["id"]
    def chunk(kind, data):
        payload = kind + data
        return struct.pack(">I", len(data)) + payload + struct.pack(">I", binascii.crc32(payload) & 0xFFFFFFFF)
    # A valid 4500x5400 RGBA image, compressed as zero-filled scanlines.
    header = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    scanline = bytes(1 + 4500 * 4)
    compressed = zlib.compress(scanline * 5400, 6)
    png = (bytes([137, 80, 78, 71, 13, 10, 26, 10])
           + chunk(b"IHDR", header) + chunk(b"IDAT", compressed) + chunk(b"IEND", b""))
    request = {
        "job_id": claim_id, "lease_token": task["lease_token"],
        "status": "needs_review", "engine": "fast_cpu",
        "png_base64": b64.b64encode(png).decode(),
        "output_sha256": hashmod.sha256(png).hexdigest(),
        "reason": "quality_gate_pending",
        "qa": {
            "source_resolution": [1400, 1400],
            "cropped_artwork": [650, 720],
            "foreground_coverage": 0.24,
            "border_noise": 2,
            "upscale_factor": 6.23,
            "review_flags": ["upscaled_preview"],
            "print_ready": False, "ocr_verified": False,
            "rights_verified": False,
        },
    }
    rejected_qa = {**request, "qa": {**request["qa"], "rights_verified": True}}
    assert client.post(remote + "/result", json=rejected_qa, headers=auth).status_code == 422
    assert db.get(ProcessingJobModel, claim_id).status == "processing"
    invalid = {**request, "output_sha256": "0" * 64}
    assert client.post(remote + "/result", json=invalid, headers=auth).status_code == 422
    # When the quota is exhausted, keep the lease and the local PNG intact:
    # the agent can retry the exact same result after an operator adds space.
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES", "22000000")
    occupied = tmp_path / "occupied"
    occupied.mkdir()
    filler = occupied / "reserved.png"
    with filler.open("wb") as file:
        file.truncate(22_000_000)
    assert client.post(remote + "/result", json=request, headers=auth).status_code == 507
    assert db.get(ProcessingJobModel, claim_id).status == "processing"
    filler.unlink()
    # A previous interrupted upload may have left a partial temp file.
    # Replaying the same job should repair that file, not fail forever.
    tenant_dir = tmp_path / hashmod.sha256(b"boostgame").hexdigest()[:24]
    tenant_dir.mkdir()
    pending = tenant_dir / (claim_id + ".pending")
    pending.write_bytes(b"partial-upload")
    receipt = client.post(remote + "/result", json=request, headers=auth)
    assert not pending.exists()
    assert receipt.status_code == 200, receipt.text
    db.refresh(db.get(ProcessingJobModel, claim_id))
    saved_result = db.get(ProcessingJobModel, claim_id).payload_json["result"]
    assert saved_result["reason"] == "quality_gate_pending"
    assert saved_result["qa"]["upscale_factor"] == 6.23
    assert saved_result["qa"]["review_flags"] == ["upscaled_preview"]
    assert saved_result["qa"]["rights_verified"] is False
    status = client.get(base + "/extraction").json()
    assert status["status"] == "needs_review"
    assert status["job_status"] == "completed"
    assert status["review_flags"] == ["upscaled_preview"]
    assert status["upscale_factor"] == 6.23
    assert status["rights_status"] == "unverified"
    assert status["print_ready"] is False
    list_item = client.get("/api/v1/etsy-spy/listings",
                           params={"pick_state": "picked"}).json()["items"][0]
    assert list_item["extraction"]["review_flags"] == ["upscaled_preview"]
    assert list_item["extraction"]["rights_status"] == "unverified"
    image = client.get("/api/v1/etsy-spy/image-agents/assets/" + claim_id)
    assert image.status_code == 200 and image.content == png
    assert image.headers["cache-control"] == "private, no-store"
    assert client.put(base + "/pick", json={"picked": False}).status_code == 200
    assert client.get(base + "/extraction").json()["status"] == "not_picked"
    abandoned = client.post(remote + "/check-job", json={
        "job_id": claim_id, "lease_token": task["lease_token"]
    }, headers=auth)
    assert abandoned.status_code == 200
    assert abandoned.json()["status"] == "cancelled"

def test_extraction_feature_off_never_creates_backlog(scenario, monkeypatch):
    monkeypatch.delenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", raising=False)
    monkeypatch.delenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", raising=False)
    client, db, _principal = scenario
    agent, headers = pair(client, db)
    image = batch("image-agent-disabled")
    image["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    )
    assert sync(client, agent, headers, image).status_code == 200
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200
    assert db.query(ProcessingJobModel).count() == 0
    assert client.get("/api/v1/etsy-spy/listings/1234567890/extraction").json()["status"] == "disabled"


def test_unpick_repick_gets_fresh_generation_and_rejects_old_lease(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, _ = scenario
    scout, scout_auth = pair(client, db)
    sample = batch("repick-new-generation")
    sample["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    )
    assert sync(client, scout, scout_auth, sample).status_code == 200
    listing = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    original = client.get(listing + "/extraction").json()["job_id"]
    assert original
    # Repeated Pick *without* Unpick is idempotent, including SQLite naive date.
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    assert client.get(listing + "/extraction").json()["job_id"] == original
    paired = client.post("/api/v1/etsy-spy/image-agents",
                         json={"name": "Replay Isolation"}).json()
    device_url = "/api/v1/etsy-spy/image-agents/" + paired["agent_id"]
    auth = {"Authorization": "Bearer " + paired["token"]}
    claim = client.post(device_url + "/claim", headers=auth).json()
    assert claim["task"]["id"] == original
    lease = claim["lease_token"]
    assert client.put(listing + "/pick", json={"picked": False}).status_code == 200
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    fresh = client.get(listing + "/extraction").json()
    assert fresh["job_id"] != original
    old = db.get(ProcessingJobModel, original)
    db.refresh(old)
    assert old.cancellation_requested is True
    check = client.post(device_url + "/check-job", headers=auth,
                        json={"job_id": original, "lease_token": lease})
    assert check.status_code == 200
    assert check.json()["status"] == "cancelled"
    assert client.post(device_url + "/result", headers=auth, json={
        "job_id": original, "lease_token": lease,
        "status": "needs_ai", "engine": "ai_review",
    }).status_code == 409
    next_claim = client.post(device_url + "/claim", headers=auth)
    assert next_claim.status_code == 200
    assert next_claim.json()["task"]["id"] == fresh["job_id"]


def test_expired_image_lease_reclaimed_only_with_new_token(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    client, db, _ = scenario
    scout, scout_auth = pair(client, db)
    sample = batch("expired-image-claim")
    sample["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    )
    assert sync(client, scout, scout_auth, sample).status_code == 200
    listing = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    one = client.post("/api/v1/etsy-spy/image-agents",
                      json={"name": "First Device"}).json()
    two = client.post("/api/v1/etsy-spy/image-agents",
                      json={"name": "Recovery Device"}).json()
    endpoint1 = "/api/v1/etsy-spy/image-agents/" + one["agent_id"]
    endpoint2 = "/api/v1/etsy-spy/image-agents/" + two["agent_id"]
    auth1 = {"Authorization": "Bearer " + one["token"]}
    auth2 = {"Authorization": "Bearer " + two["token"]}
    initial = client.post(endpoint1 + "/claim", headers=auth1).json()
    assert initial["task"]
    job = db.get(ProcessingJobModel, initial["task"]["id"])
    assert job.attempt_count == 1
    # Never steal a still-valid lease.
    assert client.post(endpoint2 + "/claim", headers=auth2).json()["task"] is None
    job.lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
    db.commit()
    recovered = client.post(endpoint2 + "/claim", headers=auth2)
    assert recovered.status_code == 200, recovered.text
    payload = recovered.json()
    assert payload["task"]["id"] == job.id
    assert payload["lease_token"] != initial["lease_token"]
    assert payload["task"]["attempt_count"] == 2
    db.refresh(job)
    assert job.attempt_count == 2
    assert job.claimed_by == "etsy-image:" + two["agent_id"]
    assert client.post(endpoint1 + "/check-job", headers=auth1, json={
        "job_id": job.id, "lease_token": initial["lease_token"],
    }).status_code == 409
    assert client.post(endpoint1 + "/result", headers=auth1, json={
        "job_id": job.id, "lease_token": initial["lease_token"],
        "status": "needs_ai", "engine": "ai_review",
    }).status_code == 403
    assert client.post(endpoint2 + "/check-job", headers=auth2, json={
        "job_id": job.id, "lease_token": payload["lease_token"],
    }).json()["status"] == "processing"


def test_image_agent_daily_cap_and_tenant_overview(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "1")
    client, db, principal = scenario
    scout, scout_auth = pair(client, db)
    data = batch("cap-200-day")
    data["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    )
    assert sync(client, scout, scout_auth, data).status_code == 200
    listing = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    paired = client.post("/api/v1/etsy-spy/image-agents",
                         json={"name": "Daily Cap Device"}).json()
    endpoint = "/api/v1/etsy-spy/image-agents/" + paired["agent_id"]
    auth = {"Authorization": "Bearer " + paired["token"]}
    first = client.post(endpoint + "/claim", headers=auth).json()
    assert first["task"]
    result = client.post(endpoint + "/result", headers=auth, json={
        "job_id": first["task"]["id"], "lease_token": first["lease_token"],
        "status": "needs_ai", "engine": "ai_review",
    })
    assert result.status_code == 200
    assert client.put(listing + "/pick", json={"picked": False}).status_code == 200
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    limited = client.post(endpoint + "/claim", headers=auth)
    assert limited.status_code == 200
    assert limited.json()["task"] is None
    assert limited.json()["pause_reason"] == "daily_cap_reached"
    overview = client.get("/api/v1/etsy-spy/image-agents/overview")
    assert overview.status_code == 200
    info = overview.json()
    assert info["daily_cap"] == 1
    assert info["claimed_today"] == 1
    assert info["remaining_today"] == 0
    assert info["queued"] == 1
    assert info["needs_ai_today"] == 1
    principal.active_tenant_id = "other-tenant"
    assert client.get("/api/v1/etsy-spy/image-agents/overview").json()["claimed_today"] == 0


def test_image_agent_storage_guard_and_scout_identity_isolation(scenario, monkeypatch, tmp_path):
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    root = tmp_path / "persistent"
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(root))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_QUOTA_BYTES", "22000000")
    client, db, _ = scenario
    scout, scout_auth = pair(client, db)
    source = batch("storage-one-slot")
    source["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    )
    assert sync(client, scout, scout_auth, source).status_code == 200
    listing = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    paired = client.post("/api/v1/etsy-spy/image-agents",
                         json={"name": "Independent Image Pilot"}).json()
    claim_url = "/api/v1/etsy-spy/image-agents/" + paired["agent_id"] + "/claim"
    headers = {"Authorization": "Bearer " + paired["token"]}
    # Image devices do not appear in Scout agent counts or Scout pairing UI.
    scouts = client.get("/api/v1/etsy-spy/agents").json()["items"]
    assert len(scouts) == 1 and scouts[0]["id"] == scout.id
    summary = client.get("/api/v1/etsy-spy/summary").json()
    assert summary["agent_count"] == 1
    state = client.get("/api/v1/etsy-spy/image-agents/overview").json()
    assert state["storage"]["ready"] is False
    assert state["storage"]["reason"] == "storage_not_provisioned"
    response = client.post(claim_url, headers=headers)
    assert response.status_code == 200
    assert response.json()["pause_reason"] == "storage_not_provisioned"
    assert response.json()["task"] is None
    root.mkdir()
    reserved = root / "reserved"
    reserved.mkdir()
    filler = reserved / "already.png"
    with filler.open("wb") as file:
        file.truncate(22_000_000)
    assert client.post(claim_url, headers=headers).json()["pause_reason"] == "storage_quota_reached"
    filler.unlink()
    ready = client.get("/api/v1/etsy-spy/image-agents/overview").json()
    assert ready["storage"]["ready"] is True
    claim = client.post(claim_url, headers=headers)
    assert claim.status_code == 200 and claim.json()["task"] is not None
    assert claim.json()["task"]["rights_status"] == "unverified"


def test_offline_hashed_image_agent_pairing_and_read_only_probe(scenario, monkeypatch):
    from hashlib import sha256
    from uuid import uuid4
    from app.modules.etsy_spy.image_agent_pairing import register_hashed_image_agent
    client, db, _ = scenario
    agent_id = str(uuid4())
    token = "A" * 64
    digest = sha256(token.encode()).hexdigest()
    assert register_hashed_image_agent(db, agent_id=agent_id, token_sha256=digest) == "paired"
    assert register_hashed_image_agent(db, agent_id=agent_id, token_sha256=digest) == "already_paired"
    assert client.get("/api/v1/etsy-spy/agents").json()["items"] == []
    assert client.get("/api/v1/etsy-spy/image-agents").json()["items"][0]["id"] == agent_id
    route = "/api/v1/etsy-spy/image-agents/" + agent_id + "/probe"
    assert client.get(route).status_code == 401
    probe = client.get(route, headers={"Authorization": "Bearer " + token})
    assert probe.status_code == 200, probe.text
    data = probe.json()
    assert data["paired"] is True and data["tenant_id"] == "boostgame"
    assert data["transport_enabled"] is False
    assert data["lease_protocol"] == "v1"
    assert db.get(EtsySpyAgent, agent_id).last_seen_at is not None
    assert client.post("/api/v1/etsy-spy/agents/" + agent_id + "/heartbeat",
        json={}, headers={"Authorization": "Bearer " + token}).status_code == 403
    from pytest import raises
    with raises(ValueError, match="device_id_collision_or_revoked"):
        register_hashed_image_agent(db, agent_id=agent_id, token_sha256="0" * 64)
    with raises(ValueError, match="unauthorized_tenant"):
        register_hashed_image_agent(
            db, agent_id=str(uuid4()), token_sha256=digest, tenant_id="other"
        )
    with raises(ValueError, match="duplicate_device_credential"):
        register_hashed_image_agent(db, agent_id=str(uuid4()), token_sha256=digest)

def test_image_agent_copy_config_is_bound_to_active_tenant(scenario):
    client, db, principal = scenario
    principal.active_tenant_id = "creative-assets-prod"
    response = client.post("/api/v1/etsy-spy/image-agents",
                           json={"name": "Tenant-scoped config"})
    assert response.status_code == 201, response.text
    data = response.json()
    assert data["tenant_id"] == "creative-assets-prod"
    assert db.get(EtsySpyAgent, data["agent_id"]).tenant_id == "creative-assets-prod"
    principal.active_tenant_id = "boostgame"
    assert all(agent["id"] != data["agent_id"] for agent in
               client.get("/api/v1/etsy-spy/image-agents").json()["items"])


def test_offline_image_agent_production_tenant_isolated_from_legacy_tenant(scenario):
    from hashlib import sha256
    from uuid import uuid4
    from pytest import raises
    from app.modules.etsy_spy.image_agent_pairing import register_hashed_image_agent
    client, db, principal = scenario
    device_id = str(uuid4())
    token = "Z" * 64
    digest = sha256(token.encode()).hexdigest()
    assert register_hashed_image_agent(db, agent_id=device_id,
        token_sha256=digest, tenant_id="creative-assets-prod") == "paired"
    # A bearer credential may probe its own tenant, not read arbitrary CAM records.
    response = client.get("/api/v1/etsy-spy/image-agents/" + device_id + "/probe",
        headers={"Authorization": "Bearer " + token})
    assert response.status_code == 200
    assert response.json()["tenant_id"] == "creative-assets-prod"
    assert all(a["id"] != device_id for a in
               client.get("/api/v1/etsy-spy/image-agents").json()["items"])
    with raises(ValueError, match="device_id_collision_or_revoked"):
        register_hashed_image_agent(db, agent_id=device_id,
            token_sha256=digest, tenant_id="boostgame")
    with raises(ValueError, match="duplicate_device_credential"):
        register_hashed_image_agent(db, agent_id=str(uuid4()),
            token_sha256=digest, tenant_id="boostgame")


def test_bulk_pick_later_selected_listings_idempotent_and_guarded(scenario):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    source = batch("bulk-listings")
    source["records"]["listings"] = [
        {**product(title="Mountain Hiking Sweatshirt"), "listingKey": "1234580001"},
        {**product(title="Botanical Flowers Shirt"), "listingKey": "1234580002"},
        {**product(title="Halloween Witch Shirt"), "listingKey": "1234580003"},
        {**product(title="Gecko Lizards Sweatshirt"), "listingKey": "1234580004"},
    ]
    for item in source["records"]["listings"]:
        item["url"] = "https://www.etsy.com/listing/" + item["listingKey"]
    assert sync(client, agent, headers, source).status_code == 200
    endpoint = "/api/v1/etsy-spy/listings/bulk"

    # Validate the entire list before making a single mutation.
    for body in [
        {"action": "pick", "listingKeys": []},
        {"action": "pick", "listingKeys": ["1234580001", "1234580001"]},
        {"action": "pick", "listingKeys": ["1234580001", "bad-id"]},
        {"action": "later", "listingKeys": ["1234580001"] * 201},
        {"action": "delete", "listingKeys": ["1234580001"]},
    ]:
        assert client.post(endpoint, json=body).status_code == 422
    assert db.query(EtsySpyPicked).count() == 0
    assert db.query(EtsySpyLaterSeed).count() == 0
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.post(endpoint, json={
        "action": "pick", "listingKeys": ["1234580001"],
    }).status_code == 403
    principal.effective_permissions = frozenset({"etsy_spy.read", "etsy_spy.manage"})

    picked = client.post(endpoint, json={
        "action": "pick", "listingKeys": ["1234580001", "1234580003", "1234580004"],
    })
    assert picked.status_code == 200, picked.text
    result = picked.json()
    assert result["applied"] == 2
    assert result["appliedListingKeys"] == ["1234580001", "1234580004"]
    assert result["skipped"] == [{"listingKey": "1234580003",
                                  "reason": "automatic_later"}]
    assert db.query(EtsySpyPicked).count() == 2
    feed = client.get(f"/api/v1/etsy-spy/agents/{agent.id}/picked-listings",
                      headers=headers).json()
    assert feed["complete"] and feed["total"] == 2
    assert {item["listingId"] for item in feed["items"]} == {
        "1234580001", "1234580004"}
    again = client.post(endpoint, json={"action": "pick", "listingKeys": ["1234580001"]})
    assert again.status_code == 200
    assert again.json()["applied"] == 0
    assert again.json()["skipped"][0]["reason"] == "already_picked"
    assert db.query(EtsySpyPicked).count() == 2

    later = client.post(endpoint, json={
        "action": "later", "listingKeys": ["1234580001", "1234580002", "1234580003"],
    })
    assert later.status_code == 200, later.text
    result = later.json()
    assert result["applied"] == 1
    assert result["appliedListingKeys"] == ["1234580002"]
    assert {item["reason"] for item in result["skipped"]} == {
        "must_unpick_first", "automatic_later"}
    assert db.query(EtsySpyLaterSeed).count() == 1
    assert client.post(endpoint, json={
        "action": "later", "listingKeys": ["1234580002"],
    }).json()["skipped"][0]["reason"] == "already_later"
    principal.active_tenant_id = "another-tenant"
    assert client.post(endpoint, json={
        "action": "pick", "listingKeys": ["1234580002"],
    }).json()["skipped"][0]["reason"] == "not_eligible"
    assert client.get("/api/v1/etsy-spy/summary").json()["picked"] == 0


def test_multiple_designs_are_mapped_by_position_to_one_picked_listing(
    scenario, monkeypatch, tmp_path,
):
    import base64
    import binascii
    import hashlib as hashing
    import struct
    import zlib

    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "1")
    client, db, principal = scenario
    scout, scout_auth = pair(client, db)
    incoming = batch("multi-design-sample")
    incoming["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.multi.jpg")
    assert sync(client, scout, scout_auth, incoming).status_code == 200
    listing = "/api/v1/etsy-spy/listings/1234567890"
    assert client.put(listing + "/pick", json={"picked": True}).status_code == 200
    device = client.post("/api/v1/etsy-spy/image-agents",
                         json={"name": "Multi-position QA"}).json()
    other_device = client.post("/api/v1/etsy-spy/image-agents",
                               json={"name": "Other device"}).json()
    base = "/api/v1/etsy-spy/image-agents"
    worker = base + "/" + device["agent_id"]
    auth = {"Authorization": "Bearer " + device["token"]}
    other_auth = {"Authorization": "Bearer " + other_device["token"]}
    claimed = client.post(worker + "/claim", headers=auth).json()
    job_id = claimed["task"]["id"]
    assert client.post(worker + "/result", headers=auth, json={
        "job_id": job_id, "lease_token": claimed["lease_token"],
        "status": "needs_ai", "engine": "ai_review",
        "source_sha256": "a" * 64,
        "reason": "textured_or_nonuniform_background",
    }).status_code == 200

    def chunk(kind, raw):
        signed = kind + raw
        return (struct.pack(">I", len(raw)) + signed +
                struct.pack(">I", binascii.crc32(signed) & 0xFFFFFFFF))

    head = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    pixel_line = bytes(4500 * 4 + 1)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", head)
           + chunk(b"IDAT", zlib.compress(pixel_line * 5400, 6))
           + chunk(b"IEND", b""))
    hash_png = hashing.sha256(png).hexdigest()
    request = {
        "job_id": job_id, "source_sha256": "a" * 64,
        "output_sha256": hash_png,
        "png_base64": base64.b64encode(png).decode(),
        "position": "front", "position_verified": True,
        "variant": 1, "method": "local_layered",
        "print_ready": False, "rights_verified": False, "ocr_verified": False,
    }
    endpoint = worker + "/designs"
    assert client.post(endpoint, headers=other_auth, json=request).status_code in (401, 403, 409)
    assert client.post(endpoint, headers=auth,
        json={**request, "position_verified": False}).status_code == 422
    assert client.post(endpoint, headers=auth,
        json={**request, "rights_verified": True}).status_code == 422
    assert client.post(endpoint, headers=auth,
        json={**request, "source_sha256": "f" * 64}).status_code == 422
    one = client.post(endpoint, headers=auth, json=request)
    assert one.status_code == 200, one.text
    assert one.json()["already_mapped"] is False
    assert client.post(endpoint, headers=auth, json=request).json()["already_mapped"] is True
    two = client.post(endpoint, headers=auth,
                      json={**request, "position": "back"})
    assert two.status_code == 200, two.text
    assert two.json()["design_id"] != one.json()["design_id"]

    mapped = client.get("/api/v1/etsy-spy/listings",
                        params={"pick_state": "picked"}).json()["items"][0]
    designs = mapped["designs"]
    assert [d["position"] for d in designs] == ["front", "back"]
    assert all(d["print_ready"] is False and
               d["rights_status"] == "unverified" for d in designs)
    assert all(d["listing_key"] == "1234567890" and
               d["job_id"] == job_id for d in designs)
    assert mapped["extraction"]["status"] == "needs_ai"
    image = client.get(designs[0]["original_url"])
    assert image.status_code == 200 and image.content == png
    thumb = client.get(designs[0]["thumbnail_url"])
    assert thumb.status_code == 200 and thumb.content.startswith(b"\x89PNG")
    assert thumb.headers["cache-control"] == "private, no-store"
    assert client.get(listing + "/extraction").json()["status"] == "needs_ai"
    db.refresh(db.get(ProcessingJobModel, job_id))
    assert db.get(ProcessingJobModel, job_id).payload_json["result"]["status"] == "needs_ai"
    assert len(db.get(ProcessingJobModel, job_id).payload_json["design_assets"]) == 2
    assert client.post(worker + "/claim", headers=auth).json()["pause_reason"] == "daily_cap_reached"

    principal.active_tenant_id = "other-tenant"
    assert client.get(designs[0]["original_url"]).status_code == 404
    principal.active_tenant_id = "boostgame"
    assert client.put(listing + "/pick", json={"picked": False}).status_code == 200
    assert client.get(designs[0]["original_url"]).status_code == 404


def test_queue_only_backfill_preserves_pilot_claim_cap_and_existing_jobs(
    scenario, monkeypatch, tmp_path,
):
    client, db, principal = scenario
    agent, headers = pair(client, db)
    original = batch("backfill-existing-job")
    original["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.initial.jpg")
    assert sync(client, agent, headers, original).status_code == 200

    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_DAILY_CAP", "1")
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200

    # A second listing was picked earlier, without extraction enabled.
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "0")
    second = batch("backfill-second-source")
    second["records"]["listings"][0].update({
        "listingKey": "1234567888",
        "url": "https://www.etsy.com/listing/1234567888/other",
        "pageTitle": "Other Design Sweatshirt",
        "fullImageUrl": "https://i.etsystatic.com/123/il_fullxfull.other.jpg",
    })
    assert sync(client, agent, headers, second).status_code == 200
    assert client.put("/api/v1/etsy-spy/listings/1234567888/pick",
                      json={"picked": True}).status_code == 200
    assert db.query(ProcessingJobModel).count() == 1
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    backfill = "/api/v1/etsy-spy/extractions/backfill"
    # Existing pilot behavior remains unchanged.
    assert client.post(backfill, json={"limit": 50, "dry_run": False}).json()["blocked"] == "pilot_job_pending"
    assert client.post(backfill, json={"queue_only": "yes"}).status_code == 422
    preview = client.post(backfill, json={"limit": 200, "queue_only": True}).json()
    assert preview["ready"] == 1 and preview["created"] == 0
    applied = client.post(backfill, json={
        "limit": 200, "queue_only": True, "dry_run": False,
    })
    assert applied.status_code == 200, applied.text
    assert applied.json()["created"] == 1
    assert applied.json()["queue_only"] is True
    assert db.query(ProcessingJobModel).count() == 2
    assert client.post(backfill, json={
        "limit": 200, "queue_only": True, "dry_run": False,
    }).json()["created"] == 0
    assert db.query(ProcessingJobModel).count() == 2

def test_orchestrator_v1_opt_in_and_isolated_chatgpt_receipt(scenario, monkeypatch, tmp_path):
    import base64
    import binascii
    import hashlib
    import struct
    import zlib

    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED", raising=False)
    client, db, principal = scenario
    spy, headers = pair(client, db)
    initial = batch("orchestrator-claim")
    initial["records"]["listings"][0]["fullImageUrl"] = "https://i.etsystatic.com/123/il_fullxfull.art.jpg"
    assert sync(client, spy, headers, initial).status_code == 200
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick", json={"picked": True}).status_code == 200
    agent = client.post("/api/v1/etsy-spy/image-agents", json={"name": "Orchestrator"}).json()
    route = "/api/v1/etsy-spy/image-agents/" + agent["agent_id"]
    auth = {"Authorization": "Bearer " + agent["token"]}
    orchestrator = {**auth, "X-CAM-Image-Worker": "orchestrator-v1"}
    assert client.post(route + "/claim", headers=orchestrator).json()["pause_reason"] == "orchestrator_not_enabled"
    monkeypatch.setenv("CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED", "1")
    assert client.post(route + "/claim", headers=auth).json()["pause_reason"] == "orchestrator_device_upgrade_required"
    claim = client.post(route + "/claim", headers=orchestrator).json()
    assert claim["task"]["execution_mode"] == "orchestrator_v1"
    assert client.post(route + "/claim", headers=orchestrator).json()["lease_token"] == claim["lease_token"]
    assert client.post(route + "/claim", headers=auth).json()["pause_reason"] == "existing_mode_task_requires_completion"

    def chunk(name, data):
        blob = name + data
        return struct.pack(">I", len(data)) + blob + struct.pack(">I", binascii.crc32(blob) & 0xffffffff)

    ihdr = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    png = (bytes.fromhex("89504e470d0a1a0a") + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(bytes(1 + 4500*4) * 5400))
           + chunk(b"IEND", b""))
    qa = {"schema": "cam-image-orchestrator-qa-v1", "method": "chatgpt_assisted",
          "width": 4500, "height": 5400, "dpi": 300,
          "alpha_pixel_verified": True, "transparent_fraction": 0.5,
          "border_transparent_fraction": 0.9, "qa_status": "needs_review",
          "review_flags": [], "manual_text_review_required": True,
          "manual_artwork_review_required": True, "print_ready": False,
          "ocr_verified": False, "rights_verified": False}
    payload = {"job_id": claim["task"]["id"], "lease_token": claim["lease_token"],
               "status": "needs_review", "engine": "chatgpt_assisted",
               "png_base64": base64.b64encode(png).decode(),
               "output_sha256": hashlib.sha256(png).hexdigest(),
               "source_sha256": "a" * 64, "qa": qa}
    for changed in (
        {**payload, "qa": {**qa, "rights_verified": True}},
        {**payload, "qa": {**qa, "alpha_pixel_verified": False}},
        {**payload, "qa": {**qa, "method": "fast_cpu"}},
        {**payload, "qa": {**qa, "border_transparent_fraction": 0.0}},
        {**payload, "source_sha256": ""},
    ):
        assert client.post(route + "/result", json=changed, headers=auth).status_code == 422
    assert db.get(ProcessingJobModel, payload["job_id"]).status == "processing"
    accepted = client.post(route + "/result", json=payload, headers=auth)
    assert accepted.status_code == 200, accepted.text
    assert client.post(route + "/result", json=payload, headers=auth).json() == accepted.json()
    stored = db.get(ProcessingJobModel, payload["job_id"]).payload_json["result"]
    assert stored["engine"] == "chatgpt_assisted"
    assert stored["manual_qa_required"] is True
    assert stored["print_ready"] is False
    assert stored["rights_status"] == "unverified"

def test_orchestrator_claim_opt_in_and_legacy_receipt_isolation(scenario, monkeypatch, tmp_path):
    """Legacy workers never accidentally claim a v1 job or spoof ImageGen output."""
    import base64
    import binascii
    import hashlib
    import struct
    import zlib

    monkeypatch.setenv("CAM_ETSY_EXTRACTION_WORKER_ENABLED", "1")
    monkeypatch.setenv("CAM_ETSY_EXTRACTION_STORAGE_ROOT", str(tmp_path))
    monkeypatch.delenv("CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED", raising=False)
    client, db, principal = scenario
    agent, spy_headers = pair(client, db)
    original = batch("orchestrator-worker-contract")
    original["records"]["listings"][0]["fullImageUrl"] = (
        "https://i.etsystatic.com/123/il_fullxfull.art.jpg")
    assert sync(client, agent, spy_headers, original).status_code == 200
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200
    paired = client.post("/api/v1/etsy-spy/image-agents",
                         json={"name": "Orchestrator Pilot"}).json()
    base = "/api/v1/etsy-spy/image-agents/" + paired["agent_id"]
    auth = {"Authorization": "Bearer " + paired["token"]}
    newer = {**auth, "X-CAM-Image-Worker": "orchestrator-v1"}

    not_enabled = client.post(base + "/claim", headers=newer)
    assert not_enabled.status_code == 200
    assert not_enabled.json() == {
        "task": None, "pause_reason": "orchestrator_not_enabled"}
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.status == "processing").count() == 0

    monkeypatch.setenv("CAM_ETSY_IMAGE_ORCHESTRATOR_ENABLED", "1")
    old_client = client.post(base + "/claim", headers=auth)
    assert old_client.status_code == 200
    assert old_client.json()["pause_reason"] == "orchestrator_device_upgrade_required"
    claimed = client.post(base + "/claim", headers=newer).json()
    assert claimed["task"]["execution_mode"] == "orchestrator_v1"
    assert claimed["task"]["engine"] == "auto"
    assert claimed["task"]["rights_status"] == "unverified"
    assert client.post(base + "/claim", headers=newer).json()["lease_token"] == claimed["lease_token"]
    assert client.post(base + "/claim", headers=auth).json()["pause_reason"] == "existing_mode_task_requires_completion"

    def chunk(kind, data):
        block = kind + data
        return struct.pack(">I", len(data)) + block + struct.pack(">I", binascii.crc32(block) & 0xFFFFFFFF)

    header = struct.pack(">IIBBBBB", 4500, 5400, 8, 6, 0, 0, 0)
    scanline = bytes(1 + 4500 * 4)
    png = (bytes([137, 80, 78, 71, 13, 10, 26, 10])
           + chunk(b"IHDR", header)
           + chunk(b"IDAT", zlib.compress(scanline * 5400, 6))
           + chunk(b"IEND", b""))
    qa = {
        "schema": "cam-image-orchestrator-qa-v1",
        "method": "chatgpt_assisted", "width": 4500, "height": 5400,
        "dpi": 300, "alpha_pixel_verified": True,
        "transparent_fraction": 0.5,
        "border_transparent_fraction": 0.9,
        "qa_status": "needs_review", "review_flags": [],
        "manual_artwork_review_required": True,
        "manual_text_review_required": True,
        "print_ready": False, "ocr_verified": False,
        "rights_verified": False,
    }
    request = {
        "job_id": claimed["task"]["id"], "lease_token": claimed["lease_token"],
        "status": "needs_review", "engine": "chatgpt_assisted",
        "png_base64": base64.b64encode(png).decode(),
        "output_sha256": hashlib.sha256(png).hexdigest(),
        "source_sha256": "f" * 64,
        "qa": qa,
    }
    for bad in (
        {**request, "qa": {**qa, "rights_verified": True}},
        {**request, "qa": {**qa, "alpha_pixel_verified": False}},
        {**request, "qa": {**qa, "method": "fast_cpu"}},
        {**request, "qa": {**qa, "border_transparent_fraction": 0.0}},
        {**request, "source_sha256": ""},
    ):
        assert client.post(base + "/result", json=bad, headers=auth).status_code == 422
    assert db.get(ProcessingJobModel, request["job_id"]).status == "processing"
    accepted = client.post(base + "/result", json=request, headers=auth)
    assert accepted.status_code == 200, accepted.text
    assert client.post(base + "/result", json=request, headers=auth).json() == accepted.json()
    stored = db.get(ProcessingJobModel, request["job_id"]).payload_json["result"]
    assert stored["engine"] == "chatgpt_assisted"
    assert stored["qa"]["alpha_pixel_verified"] is True
    assert stored["rights_status"] == "unverified"
    assert stored["print_ready"] is False
    assert stored["manual_qa_required"] is True

def test_tiktok_skill_auto_on_pick_idempotent_status_and_unpick(scenario, monkeypatch):
    """A picked listing queues one draft; no provider executes in the HTTP request."""
    from sqlalchemy import select
    from app.modules.etsy_spy import tiktok_skill
    import importlib
    spy_router = importlib.import_module("app.modules.etsy_spy.router")
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(tiktok_skill, "auto_on_pick_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_auto_on_pick_enabled", lambda: True)
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing = db.scalar(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == "boostgame",
        EtsySpyListing.listing_key == "1234567890"))
    listing.metadata_json = {"fullImageUrl": "https://i.etsystatic.com/12345/il_fullxfull.abc.jpg"}
    db.commit()
    picked = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(picked, json={"picked": True}).status_code == 200
    jobs = db.scalars(select(ProcessingJobModel).where(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE)).all()
    assert len(jobs) == 1 and jobs[0].status == "pending"
    status = client.get("/api/v1/etsy-spy/listings?pick_state=picked")
    assert status.status_code == 200
    assert status.json()["items"][0]["tiktokListing"]["status"] == "pending"
    detail = client.get("/api/v1/etsy-spy/listings/1234567890/tiktok-listing")
    assert detail.status_code == 200 and detail.json()["draft_only"] is True
    assert detail.json()["enabled"] is True
    assert client.post("/api/v1/etsy-spy/listings/1234567890/tiktok-listing/queue").status_code == 200
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 1
    overview = client.get("/api/v1/etsy-spy/tiktok-listings/overview").json()
    assert overview["counts"]["pending"] == 1
    assert overview["keyword_source"] == "ytrends_etsy_context"
    assert overview["version"] == tiktok_skill.PROFILE_VERSION
    assert overview["ytrends_enabled"] is True
    assert client.put(picked, json={"picked": False}).status_code == 200
    db.refresh(jobs[0])
    assert jobs[0].cancellation_requested is True
    assert client.get("/api/v1/etsy-spy/listings/1234567890/tiktok-listing").status_code == 404


def test_tiktok_ytrends_refresh_preserves_existing_draft_without_gemini(scenario, monkeypatch):
    from sqlalchemy import select
    from app.modules.etsy_spy import tiktok_skill
    import importlib
    spy_router = importlib.import_module("app.modules.etsy_spy.router")
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing = db.scalar(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == "boostgame",
        EtsySpyListing.listing_key == "1234567890"))
    listing.metadata_json = {"fullImageUrl": "https://i.etsystatic.com/12345/il_fullxfull.abc.jpg"}
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_auto_on_pick_enabled", lambda: False)
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200
    base = "/api/v1/etsy-spy/listings/1234567890/tiktok-listing"
    assert client.post(base + "/queue").status_code == 200
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE))
    assert job is not None
    job.status = "completed"
    original = {"title": "Existing draft title", "description": "Existing description",
                "highlights": ["One", "Two", "Three"], "search_terms": ["original"],
                "warnings": ["tiktok_keyword_metrics_unverified"],
                "qa_status": "needs_fact_check", "ytrends_status": "unavailable",
                "published": False}
    job.payload_json = {**job.payload_json, "result": original}
    db.commit()
    calls = []
    async def researched(title, **kwargs):
        calls.append(title)
        return {"status": "connected", "source": "trends.ytuong.ai",
                "marketplace": "etsy", "query": "oh holy night",
                "as_of_date": "2026-11-12", "median_data_age_days": 78,
                "total_listings": 36, "total_sellers": 31,
                "competition_level": "medium", "opportunity_score": 54.5,
                "dataset_note": "Indexed Etsy subset; not TikTok Shop search data."}
    monkeypatch.setattr(spy_router, "research_etsy_keyword", researched)
    refreshed = client.post(base + "/ytrends/refresh")
    assert refreshed.status_code == 200, refreshed.text
    result = refreshed.json()["result"]
    assert result["title"] == original["title"]
    assert result["description"] == original["description"]
    assert result["search_terms"] == original["search_terms"]
    assert result["ytrends_status"] == "connected"
    assert result["ytrends_evidence"]["total_listings"] == 36
    assert result["published"] is False
    assert len(calls) == 1
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 1
    second = client.post(base + "/ytrends/refresh")
    assert second.status_code == 200
    assert len([v for v in second.json()["result"]["warnings"]
                if v == "ytrends_etsy_snapshot_not_tiktok_search"]) == 1
    assert client.post("/api/v1/etsy-spy/listings/9999999999/tiktok-listing/ytrends/refresh").status_code == 404


def test_tiktok_regenerate_completed_draft_keeps_old_result_and_researches_again(
    scenario, monkeypatch,
):
    from sqlalchemy import select
    from app.modules.etsy_spy import tiktok_skill
    import importlib
    spy_router = importlib.import_module("app.modules.etsy_spy.router")
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing = db.scalar(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == "boostgame",
        EtsySpyListing.listing_key == "1234567890"))
    listing.metadata_json = {
        "fullImageUrl": "https://i.etsystatic.com/12345/il_fullxfull.abc.jpg",
    }
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_auto_on_pick_enabled", lambda: False)
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200
    base = "/api/v1/etsy-spy/listings/1234567890/tiktok-listing"
    assert client.post(base + "/queue").status_code == 200
    original = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE))
    draft = {
        "title": "Original Holy Night Draft", "description": "Existing description",
        "highlights": ["Design", "Gift", "Occasion"], "search_terms": ["holy night"],
        "warnings": ["ytrends_mcp_unavailable"],
        "qa_status": "needs_fact_check", "ytrends_status": "unavailable",
        "published": False,
    }
    original.status = "completed"
    original.payload_json = {**original.payload_json, "result": draft}
    db.commit()
    before = client.get(base).json()
    assert before["status"] == "needs_fact_check"
    assert before["can_regenerate"] is True
    queued = client.post(base + "/regenerate")
    assert queued.status_code == 200, queued.text
    assert queued.json()["status"] == "pending"
    assert queued.json()["job_id"] != original.id
    repeated = client.post(base + "/regenerate")
    assert repeated.status_code == 200
    assert repeated.json()["job_id"] == queued.json()["job_id"]
    detail = client.get(base).json()
    assert detail["status"] == "pending"
    assert detail["previous_job_id"] == original.id
    assert detail["previous_draft"] == draft
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 2
    db.refresh(original)
    assert original.payload_json["result"] == draft
    assert client.post(
        "/api/v1/etsy-spy/listings/9999999999/tiktok-listing/regenerate"
    ).status_code == 404


def test_tiktok_skill_backfill_dry_run_and_feature_off(scenario, monkeypatch):
    from sqlalchemy import select
    from app.modules.etsy_spy import tiktok_skill
    import importlib
    spy_router = importlib.import_module("app.modules.etsy_spy.router")
    client, db, principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing = db.scalar(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == "boostgame",
        EtsySpyListing.listing_key == "1234567890"))
    listing.metadata_json = {"thumbnailUrl": "https://i.etsystatic.com/12345/il_340x270.abc.jpg"}
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: False)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: False)
    assert client.put("/api/v1/etsy-spy/listings/1234567890/pick",
                      json={"picked": True}).status_code == 200
    assert client.post("/api/v1/etsy-spy/tiktok-listings/backfill",
                       json={"limit": 10, "dry_run": False}).status_code == 503
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 0
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: True)
    preview = client.post("/api/v1/etsy-spy/tiktok-listings/backfill",
                          json={"limit": 5, "dry_run": True})
    assert preview.status_code == 200 and preview.json()["ready"] == 1
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 0
    backfill = client.post("/api/v1/etsy-spy/tiktok-listings/backfill",
                          json={"limit": 5, "dry_run": False})
    assert backfill.status_code == 200 and backfill.json()["created"] == 1
    again = client.post("/api/v1/etsy-spy/tiktok-listings/backfill",
                        json={"limit": 5, "dry_run": False})
    assert again.status_code == 200 and again.json()["created"] == 0
    principal.active_tenant_id = "other-tenant"
    assert client.get("/api/v1/etsy-spy/listings/1234567890/tiktok-listing").status_code == 404

def test_tiktok_manual_pilot_does_not_auto_enqueue_and_can_retry(scenario, monkeypatch):
    from sqlalchemy import select
    from app.modules.etsy_spy import tiktok_skill
    import importlib
    spy_router = importlib.import_module("app.modules.etsy_spy.router")
    client, db, _principal = scenario
    agent, headers = pair(client, db)
    assert sync(client, agent, headers, batch()).status_code == 200
    listing = db.scalar(select(EtsySpyListing).where(
        EtsySpyListing.tenant_id == "boostgame",
        EtsySpyListing.listing_key == "1234567890"))
    listing.metadata_json = {"fullImageUrl": "https://i.etsystatic.com/12345/il_fullxfull.abc.jpg"}
    db.commit()
    monkeypatch.setattr(tiktok_skill, "is_enabled", lambda: True)
    monkeypatch.setattr(tiktok_skill, "auto_on_pick_enabled", lambda: False)
    monkeypatch.setattr(spy_router, "tiktok_skill_enabled", lambda: True)
    monkeypatch.setattr(spy_router, "tiktok_auto_on_pick_enabled", lambda: False)
    picked_url = "/api/v1/etsy-spy/listings/1234567890/pick"
    assert client.put(picked_url, json={"picked": True}).status_code == 200
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 0
    overview = client.get("/api/v1/etsy-spy/tiktok-listings/overview").json()
    assert overview["enabled"] is True and overview["auto_on_pick"] is False
    queue = "/api/v1/etsy-spy/listings/1234567890/tiktok-listing/queue"
    assert client.post(queue).status_code == 200
    assert client.post(queue).status_code == 200
    job = db.scalar(select(ProcessingJobModel).where(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE))
    assert job is not None
    assert db.query(ProcessingJobModel).filter(
        ProcessingJobModel.job_type == tiktok_skill.JOB_TYPE).count() == 1
    job.status = "failed"
    job.attempt_count = job.max_attempts
    job.last_error_code = "tiktok_listing_invalid_output"
    db.commit()
    retry = "/api/v1/etsy-spy/listings/1234567890/tiktok-listing/retry"
    response = client.post(retry)
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "pending"
    assert db.get(ProcessingJobModel, job.id).payload_json["manual_retry_count"] == 1
    assert client.post("/api/v1/etsy-spy/tiktok-listings/backfill",
                       json={"limit": 6, "dry_run": False}).status_code == 422
