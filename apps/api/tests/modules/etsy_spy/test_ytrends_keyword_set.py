"""Multi-keyword YTrends research remains source-backed and Etsy-only."""
import asyncio
import json

import httpx

from app.modules.etsy_spy import ytrends_mcp as ytrends


def test_keyword_variants_keep_garment_queries_distinct():
    variants = ytrends.keyword_variants("Retro Santa Sweatshirt, Holiday Christmas Gift")
    assert "retro santa sweatshirt" in variants
    assert "retro santa crewneck" in variants
    assert 3 <= len(variants) <= 5
    assert len(variants) == len(set(variants))


def test_legacy_titles_strip_seller_brands_and_unsupported_punctuation():
    assert ytrends.research_query(
        "Comfort Colors® Please Be Patient With Me I'm From The 1900s shirt, Meme Gift"
    ) == "please be patient with me i'm from the 1900s"
    assert ytrends.research_query(
        "Funny Sister T-Shirt: Sarcastic Sister Gift Tee"
    ) == "funny sister"
    assert ytrends.research_query("God Is Within Her Sweatshirt: Christian Cross Graphic") == "god is within her"
    assert ytrends.research_query("Comfort Colors® Tired Dads Club Shirt") == "tired dads club"
    assert ytrends.keyword_variants("Comfort Colors® Tired Dads Club Shirt")
    assert ytrends.research_query("hello\\\\nignore prompt") == ""


def test_keyword_set_uses_real_individual_provider_responses():
    requests = []
    def answer(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = json.loads(request.content)["params"]["arguments"]["keyword"]
        payload = {"source": "trends.ytuong.ai",
                   "snapshot": {"as_of_date": "2026-11-12", "median_data_age_days": 78},
                   "data": {"stats": {"total_listings": 12 + len(query),
                                      "total_sellers": 5,
                                      "competition_level": "medium"}}}
        return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(payload)}]}})
    evidence = asyncio.run(ytrends.research_etsy_keyword_set(
        "Santa Sweatshirt", transport=httpx.MockTransport(answer)))
    assert evidence["status"] == "connected"
    assert 3 <= len(evidence["keywords"]) <= 5
    assert len(requests) == len(evidence["keywords"])
    for row in evidence["keywords"]:
        assert row["listings"] == 12 + len(row["keyword"])
        assert row["competition"] == "medium"
        assert row["as_of_date"] == "2026-11-12"
