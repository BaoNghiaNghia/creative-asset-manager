"""Bounded VPS marketplace enrichment, safe image host selection and tenant isolation."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import asyncio
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.realistic_review_ugc.model import RrugcKeywordAgentOriginModel, RrugcKeywordVolumeModel
from app.operations import stage0_reach_enrich as enrich


def test_only_known_product_and_image_hosts_are_accepted():
    assert enrich.checked_marketplace_source("amazon", "https://www.amazon.com/dp/B0H4JNV7Z3") == "https://www.amazon.com/dp/B0H4JNV7Z3"
    assert enrich.checked_marketplace_source("etsy", "https://www.etsy.com/listing/4536924550/low-iron") == "https://www.etsy.com/listing/4536924550"
    assert enrich.checked_marketplace_source("amazon", "https://127.0.0.1/dp/B0H4JNV7Z3") is None
    assert enrich.checked_marketplace_source("amazon", "https://www.amazon.com.evil.com/dp/B0H4JNV7Z3") is None
    assert enrich.checked_marketplace_source("etsy", "https://www.etsy.com/shop/other") is None
    assert enrich.select_image_url("etsy", [
        "https://i.etsystatic.com/123/il_fullxfull.123.jpg",
        "https://cdn.evil.com/hat.jpg",
        "http://i.etsystatic.com/h.jpg",
        "https://i.etsystatic.com/123/il_fullxfull.123.jpg",
    ]) == ["https://i.etsystatic.com/123/il_fullxfull.123.jpg"]
    assert enrich.select_image_url("amazon", ["https://m.media-amazon.com/images/I/123.jpg"]) == [
        "https://m.media-amazon.com/images/I/123.jpg"
    ]


def test_retry_cooldowns_distinguish_image_and_metric_operations():
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    assert enrich.due({}, enrich.ATTEMPT_IMAGE_KEY, now, enrich.IMAGE_RETRY_AFTER)
    raw = {enrich.ATTEMPT_IMAGE_KEY: (now - timedelta(hours=1)).isoformat()}
    assert not enrich.due(raw, enrich.ATTEMPT_IMAGE_KEY, now, enrich.IMAGE_RETRY_AFTER)
    assert enrich.due(raw, enrich.ATTEMPT_METRICS_KEY, now, enrich.METRICS_RETRY_AFTER)


def test_enrichment_uses_source_for_matching_tenant_only(monkeypatch):
    asyncio.run(_exercise_enrichment(monkeypatch))


async def _exercise_enrichment(monkeypatch):
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[
        RrugcKeywordVolumeModel.__table__, RrugcKeywordAgentOriginModel.__table__,
    ])
    SessionFactory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(enrich, "SessionLocal", SessionFactory)
    now = datetime.now(timezone.utc)
    with SessionFactory() as session:
        session.add_all([
            RrugcKeywordVolumeModel(id="a", tenant_id="tenant-a", keyword="FEAR NOT",
                                    keyword_normalized="fear not", provider="pending"),
            RrugcKeywordVolumeModel(id="b", tenant_id="tenant-b", keyword="FEAR NOT",
                                    keyword_normalized="fear not", provider="pending"),
            RrugcKeywordAgentOriginModel(
                id="e", tenant_id="tenant-a", keyword_normalized="fear not",
                agent_key="scout:reach", agent_name="Agent-Reach Auto Discover",
                source_platform="amazon", source_channel="auto-discover",
                evidence_hash="a"*64, source_url="https://www.amazon.com/dp/B0H4JNV7Z3", first_seen_at=now,
            ),
        ])
        session.commit()
        images, metrics = enrich.pending_rows(session, image_limit=4, metrics_limit=6, now=now)
        assert images == [("tenant-a", "a", "amazon", "https://www.amazon.com/dp/B0H4JNV7Z3")]
        assert metrics == [("tenant-a", "a", "FEAR NOT")]

    async def page(_client, _url):
        return SimpleNamespace(images=["https://m.media-amazon.com/images/I/hat.jpg",
                                        "https://attacker.test/track.jpg"])
    async def image(_client, _url):
        return bytes.fromhex("ffd8ff") + bytes(3000), "image/jpeg"
    monkeypatch.setattr(enrich, "fetch_product_page", page)
    monkeypatch.setattr(enrich, "fetch_product_image", image)
    async with __import__("httpx").AsyncClient() as client:
        assert await enrich.enrich_image("tenant-a", "a", "amazon", images[0][3],
                                        client=client, now=now)
        assert not await enrich.enrich_image("tenant-a", "a", "amazon", images[0][3],
                                            client=client, now=now)
    with SessionFactory() as session:
        assert session.get(RrugcKeywordVolumeModel, "a").source_image_url == "https://m.media-amazon.com/images/I/hat.jpg"
        assert session.get(RrugcKeywordVolumeModel, "b").source_image_url is None
        assert enrich.claim_attempt(session, tenant="tenant-a", row_id="a",
                                    key=enrich.ATTEMPT_METRICS_KEY, now=now - timedelta(hours=7))
    engine.dispose()
