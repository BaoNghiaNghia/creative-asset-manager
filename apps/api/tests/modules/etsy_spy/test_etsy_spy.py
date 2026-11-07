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
    EtsySpyWinKeyword, EtsySpyParentKeyword,
)
from app.modules.etsy_spy.router import router
from app.modules.etsy_spy.service import create_agent
from app.modules.processing.model import ProcessingJobModel
from app.domain.processing.types import JOB_TYPES


TABLES = (
    EtsySpyAgent, EtsySpySession, EtsySpyKeyword, EtsySpyListing,
    EtsySpyTrademark, EtsySpyLog, EtsySpyBatch, EtsySpyBlacklist, EtsySpyPicked,
    EtsySpyLaterSeed, EtsySpyLaterMatch,
    EtsySpyWinKeyword, EtsySpyParentKeyword, ProcessingJobModel,
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

    for invalid in (0, -1, 101, 3.5, True, "99"):
        assert client.post(url, json={"keyword": "valid", "priority": invalid}).status_code == 422
    assert client.post(url, json={"keyword": "x", "priority": 50}).status_code == 422
    assert client.post(url, json={"keyword": "a" * 181, "priority": 50}).status_code == 422
    principal.effective_permissions = frozenset({"etsy_spy.read"})
    assert client.put(url, json={"keyword": "Halloween", "priority": 1}).status_code == 403
    principal.active_tenant_id = "another-tenant"
    assert client.get(url).json()["count"] == 0
    principal.active_tenant_id = "boostgame"
    assert client.get(url).json()["count"] == 2


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
    assert client.get(base, params={
        "observed_day": today_key, "pick_state": "picked"}).json()["total"] == 1
    pick_record = db.get(EtsySpyPicked, ("boostgame", "1234567891"))
    assert pick_record is not None
    pick_record.picked_at = today_start
    db.commit()
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
    assert match_title("Plant Shirt", "Goose Shirt") is None
    assert not analyze_title("Comfort Colors Gildan Shirt Gift Tee").terms


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
    assert client.get(endpoint + "/extraction").json()["job_id"] == job.id
    db.refresh(job)
    assert job.cancellation_requested is False
    principal.active_tenant_id = "other-tenant"
    assert client.get(endpoint + "/extraction").json()["status"] == "not_picked"


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
    }
    invalid = {**request, "output_sha256": "0" * 64}
    assert client.post(remote + "/result", json=invalid, headers=auth).status_code == 422
    receipt = client.post(remote + "/result", json=request, headers=auth)
    assert receipt.status_code == 200, receipt.text
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
