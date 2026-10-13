import asyncio
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.model import (
    RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel,
)
from app.modules.realistic_review_ugc.marketplace_images import (
    image_from_search_results, validate_listing_image,
    enrich_missing_marketplace_images,
)


def test_only_source_linked_exact_listing_images_are_accepted():
    target = "https://www.etsy.com/listing/4447356223"
    results = [
        {"url": "https://www.etsy.com/market/funny_hat",
         "images": ["https://i.etsystatic.com/12/r/il/aaaaa/1/il_fullxfull.1.jpg"]},
        {"url": "https://www.etsy.com/listing/4447356223/hat?ref=home",
         "images": [
             "https://i.etsystatic.com/site-assets/images/open-graph/etsylogo_1200x630.png",
             "https://i.etsystatic.com/55/r/il/bbbbb/2/il_fullxfull.2_xxx.jpg",
         ]},
    ]
    assert image_from_search_results("etsy", target, results) == (
        "https://i.etsystatic.com/55/r/il/bbbbb/2/il_fullxfull.2_xxx.jpg"
    )
    assert image_from_search_results("etsy", target, results[:1]) is None
    assert image_from_search_results("amazon", "https://www.amazon.com/dp/B0CJFX7L76", [{
        "url": "https://www.amazon.com/embroidered-trucker-hat/dp/B0CJFX7L76",
        "images": ["https://m.media-amazon.com/images/I/81example._AC_SL1500_.jpg"],
    }]) == "https://m.media-amazon.com/images/I/81example._AC_SL1500_.jpg"
    assert image_from_search_results("etsy", target, [{
        "url": target, "images": ["https://evil.com/hat.jpg"]
    }]) is None
    assert validate_listing_image("etsy", "http://i.etsystatic.com/photo.jpg") is None
    assert validate_listing_image("etsy", "https://i.etsystatic.com/logo.png") is None
    assert validate_listing_image("amazon", "https://m.media-amazon.com/images/I/aa.jpg") is not None


def test_enrichment_keeps_volume_metrics_picks_and_other_tenant_images(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__,
    ])
    with Session(engine) as session:
        for tenant in ("t-a", "t-b"):
            session.add(RrugcKeywordVolumeModel(
                tenant_id=tenant, keyword="I COULD BE MEANER", keyword_normalized="i could be meaner",
                source_image_url=None, source_pin_url=None, search_volume=97, picked=True,
                provider="pending", provider_raw_json={"research_unverified": True},
            ))
            session.add(RrugcKeywordAgentOriginModel(
                id=str(uuid4()), tenant_id=tenant, keyword_normalized="i could be meaner",
                agent_key="scout:reach", agent_name="Agent-Reach",
                source_platform="etsy", source_channel="auto-discover",
                source_url="https://www.etsy.com/listing/4447356223",
                evidence_hash=str(uuid4()).replace("-", ""),
            ))
        session.commit()

        async def fake_fetch(source, url, client):
            assert source == "etsy"
            assert url.endswith("/4447356223")
            return ("https://i.etsystatic.com/51/r/il/abc/1/il_fullxfull.1.jpg", "source_indexed")
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.marketplace_images.fetch_image_for_source",
            fake_fetch,
        )
        report = asyncio.run(enrich_missing_marketplace_images(session, tenant_id="t-a", limit=5))
        session.commit()
        assert report["updated"] == 1
        a = session.query(RrugcKeywordVolumeModel).filter_by(tenant_id="t-a").one()
        b = session.query(RrugcKeywordVolumeModel).filter_by(tenant_id="t-b").one()
        assert a.source_image_url.endswith("il_fullxfull.1.jpg")
        assert a.provider_raw_json["_reach_product_image_status"] == "ready"
        assert a.search_volume == 97 and a.picked is True
        assert b.source_image_url is None
        report_again = asyncio.run(enrich_missing_marketplace_images(session, tenant_id="t-a", limit=5))
        assert report_again["checked"] == 0
    engine.dispose()


def test_keyless_fallback_uses_only_page_linked_images(monkeypatch):
    import httpx
    from app.modules.realistic_review_ugc.marketplace_images import fetch_image_for_source

    async def blocked_source(source, listing_url, client):
        return None
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.marketplace_images._public_listing_image",
        blocked_source,
    )
    target = "https://www.etsy.com/listing/4447356223"
    requests = []
    def handler(request):
        payload = __import__("json").loads(request.content)
        requests.append(payload)
        return httpx.Response(200, json={
            "images": ["https://i.etsystatic.com/10/r/il/other/1/il_fullxfull.1.jpg"],
            "results": [
                {"url": "https://www.etsy.com/listing/9999999999",
                 "images": ["https://i.etsystatic.com/11/r/il/other/2/il_fullxfull.2.jpg"]},
                {"url": target + "/hat",
                 "images": ["https://i.etsystatic.com/12/r/il/correct/3/il_fullxfull.3.jpg"]},
            ],
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_image_for_source("etsy", target, client)
    image, status = asyncio.run(run())
    assert status == "source_indexed"
    assert image.endswith("il_fullxfull.3.jpg")
    assert requests[0]["include_images"] is True
    assert requests[0]["include_domains"] == ["etsy.com"]


def test_search_never_uses_unrelated_top_level_images(monkeypatch):
    import httpx
    from app.modules.realistic_review_ugc.marketplace_images import fetch_image_for_source

    async def blocked_source(source, listing_url, client):
        return None
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.marketplace_images._public_listing_image",
        blocked_source,
    )
    def handler(request):
        return httpx.Response(200, json={
            "images": ["https://i.etsystatic.com/12/r/il/other/3/il_fullxfull.3.jpg"],
            "results": [{"url": "https://www.etsy.com/market/hats", "images": []}],
        })
    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await fetch_image_for_source("etsy", "https://www.etsy.com/listing/4447356223", client)
    assert asyncio.run(run()) == (None, "metadata_unavailable")
