import asyncio
from uuid import uuid4

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel, RrugcKeywordAgentOriginModel
from app.modules.realistic_review_ugc.marketplace_images import validate_listing_image
from app.modules.realistic_review_ugc.stage0_source_images import attach_marketplace_image, Stage0SourceImageError

AMAZON_IMAGE = "https://m.media-amazon.com/images/I/41RjV6zpQZL._AC_SX679_.jpg"
ETSY_IMAGE = "https://i.etsystatic.com/14778351/r/il/6943a7/7591702865/il_1588xN.7591702865_4toy.jpg"


def test_real_amazon_and_etsy_cdn_formats():
    assert validate_listing_image("amazon", AMAZON_IMAGE) == AMAZON_IMAGE
    assert validate_listing_image("etsy", ETSY_IMAGE) == ETSY_IMAGE
    assert validate_listing_image("amazon", ETSY_IMAGE) is None
    assert validate_listing_image("etsy", AMAZON_IMAGE) is None


def test_attach_marketplace_source_requires_correct_tenant_listing_and_real_image():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__,
    ])
    listing = "https://www.etsy.com/listing/123456789"
    with Session(engine) as db:
        a = RrugcKeywordVolumeModel(tenant_id="a", keyword="BE STILL AND KNOW",
            keyword_normalized="be still and know", search_volume=23,
            source_image_url=None, picked=True, provider="pending",
            provider_raw_json={"trademark": {"status": "safe"}})
        db.add(a)
        db.flush()
        db.add(RrugcKeywordAgentOriginModel(id=str(uuid4()), tenant_id="a",
            keyword_normalized=a.keyword_normalized, agent_key="scout:reach",
            agent_name="Agent-Reach", source_platform="etsy",
            source_channel="auto-discover", source_url=listing,
            evidence_hash="a"*64))
        db.commit()

        calls = []
        def handler(request: httpx.Request):
            calls.append(request)
            return httpx.Response(200, headers={"Content-Type": "image/jpeg", "Content-Length": "1024"})
        async def save(**kwargs):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await attach_marketplace_image(db,
                    tenant_id=kwargs.pop("tenant_id", "a"), keyword_id=a.id,
                    listing_url=kwargs.pop("listing_url", listing),
                    image_url=kwargs.pop("image_url", ETSY_IMAGE),
                    actor_id="operator", client=client, **kwargs)
        with pytest.raises(LookupError):
            asyncio.run(save(tenant_id="b"))
        with pytest.raises(Stage0SourceImageError, match="previously recorded"):
            asyncio.run(save(listing_url="https://www.etsy.com/listing/999999999"))
        with pytest.raises(Stage0SourceImageError, match="matching marketplace"):
            asyncio.run(save(image_url=AMAZON_IMAGE))
        assert calls == []

        saved = asyncio.run(save())
        assert saved.source_image_url == ETSY_IMAGE
        assert saved.provider_raw_json["_reach_product_image_status"] == "operator_supplied"
        assert saved.provider_raw_json["_reach_product_image_source"] == listing
        assert saved.search_volume == 23 and saved.picked
        assert saved.provider_raw_json["trademark"]["status"] == "safe"
        assert calls and calls[0].method == "HEAD"
        with pytest.raises(Stage0SourceImageError, match="explicit replace"):
            asyncio.run(save())
        assert asyncio.run(save(replace=True)).source_image_url == ETSY_IMAGE
    engine.dispose()


def test_html_or_redirect_cannot_be_saved_as_marketplace_photo():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__,
    ])
    with Session(engine) as db:
        row = RrugcKeywordVolumeModel(
            tenant_id="a", keyword="9B BONJOUR", keyword_normalized="9b bonjour",
            provider="pending", source_image_url=None)
        db.add(row)
        db.flush()
        db.add(RrugcKeywordAgentOriginModel(id=str(uuid4()), tenant_id="a",
            keyword_normalized=row.keyword_normalized, agent_key="scout:reach",
            agent_name="Agent-Reach", source_platform="amazon",
            source_channel="auto-discover", source_url="https://www.amazon.com/dp/B0CJFX7L76",
            evidence_hash="b"*64))
        db.commit()
        for code, mime in [(302, "image/jpeg"), (200, "text/html")]:
            async def run():
                async with httpx.AsyncClient(transport=httpx.MockTransport(
                    lambda req: httpx.Response(code, headers={"Content-Type": mime})
                )) as client:
                    return await attach_marketplace_image(db, tenant_id="a",
                        keyword_id=row.id, listing_url="https://www.amazon.com/dp/B0CJFX7L76",
                        image_url=AMAZON_IMAGE, actor_id="u", client=client)
            with pytest.raises(Stage0SourceImageError, match="public image"):
                asyncio.run(run())
        assert row.source_image_url is None
    engine.dispose()
