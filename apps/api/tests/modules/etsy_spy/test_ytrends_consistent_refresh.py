"""Source-backed YTrends refresh must never mix metrics of different keywords."""
import asyncio
import json

import httpx

from app.modules.etsy_spy import ytrends_mcp


def test_primary_metric_belongs_to_connected_keyword_not_failed_first_query():
    def handler(request: httpx.Request):
        word = json.loads(request.content)["params"]["arguments"]["keyword"]
        if word == "ota shirt":
            return httpx.Response(503)
        count = 8 if word == "occupational therapy shirt" else 3
        body = {"source": "trends.ytuong.ai",
                "snapshot": {"as_of_date": "2026-11-12", "median_data_age_days": 78},
                "data": {"stats": {"total_listings": count, "total_sellers": 2,
                                    "competition_level": "low"}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "result": {
            "content": [{"type": "text", "text": json.dumps(body)}]}})
    found = asyncio.run(ytrends_mcp.research_etsy_keyword_set(
        "Occupational Therapy Shirt, Gift for OTA", transport=httpx.MockTransport(handler)))
    assert found["query"] == "occupational therapy shirt"
    assert found["total_listings"] == 8
    assert found["keywords"][0]["listings"] is None
    assert found["keywords"][1]["listings"] == 8


def test_bulk_merge_preserves_old_metric_only_for_same_keyword_and_snapshot():
    old = {"source": "trends.ytuong.ai", "marketplace": "etsy",
           "status": "connected", "query": "ota shirt",
           "total_listings": 105, "as_of_date": "2026-11-11",
           "keywords": [
               {"keyword": "ota shirt", "status": "connected", "listings": 105,
                "competition": "medium", "as_of_date": "2026-11-11"},
               {"keyword": "occupational therapy shirt", "status": "connected",
                "listings": 8, "competition": "low", "as_of_date": "2026-11-11"}]}
    fresh = {"source": "trends.ytuong.ai", "marketplace": "etsy",
             "status": "connected", "query": "occupational therapy shirt",
             "total_listings": 10, "total_sellers": 3,
             "as_of_date": "2026-11-12", "keywords": [
                 {"keyword": "ota shirt", "status": "unavailable", "listings": None,
                  "competition": None, "as_of_date": None},
                 {"keyword": "occupational therapy shirt", "status": "connected",
                  "listings": 10, "competition": "low", "as_of_date": "2026-11-12"}]}
    result = ytrends_mcp.merge_keyword_evidence(old, fresh)
    assert [row["listings"] for row in result["keywords"]] == [105, 10]
    assert result["query"] == "ota shirt"
    assert result["total_listings"] == 105
    assert result["total_sellers"] is None
    assert result["status"] == "connected"
    assert result["last_refresh_status"] == "connected"
    all_failed = {**fresh, "status": "unavailable",
                  "keywords": [{**row, "status": "unavailable", "listings": None,
                                "as_of_date": None} for row in fresh["keywords"]]}
    preserved = ytrends_mcp.merge_keyword_evidence(old, all_failed)
    assert preserved["keywords"][0]["listings"] == 105
    assert preserved["last_refresh_status"] == "unavailable"


def test_never_replace_newer_snapshot_with_older_listings():
    old = {"source": "trends.ytuong.ai", "marketplace": "etsy",
           "status": "connected", "query": "christmas sweatshirt",
           "total_listings": 11245, "as_of_date": "2026-11-12",
           "keywords": [{"keyword": "christmas sweatshirt",
                         "status": "connected", "listings": 11245,
                         "competition": "very_high", "as_of_date": "2026-11-12"}]}
    stale = {**old, "total_listings": 10000, "as_of_date": "2026-11-11",
             "keywords": [{**old["keywords"][0], "listings": 10000,
                           "as_of_date": "2026-11-11"}]}
    result = ytrends_mcp.merge_keyword_evidence(old, stale)
    assert result["total_listings"] == 11245
    assert result["as_of_date"] == "2026-11-12"
