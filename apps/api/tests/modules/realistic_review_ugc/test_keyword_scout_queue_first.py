"""Bounded Keyword fair-share with Review priority and durable retries."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

# Standalone Windows scout source is a sibling of the API project.
sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "rrugc_scout"))
from quote_keyword_volume import (  # noqa: E402
    KeywordScoutCapacityPaused,
    KeywordScoutHistory,
    QuoteScoutClient,
    ensure_pending_quote_volumes_drained,
)

from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.scout_automation import (
    keyword_quote_backlog_gate,
    scout_analysis_backpressure,
)


def test_quote_gate_preserves_review_and_admits_one_keyword_per_8_seconds():
    from app.modules.ai_governance.model import AiModelRateLimitStateModel

    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProcessingJobModel.__table__.create(engine)
    AiModelRateLimitStateModel.__table__.create(engine)
    now = datetime(2026, 10, 8, 16, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            session.add(ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="rrugc_candidate_analyze",
                entity_type="rrugc_candidate",
                entity_id="candidate-1",
                idempotency_key="review-1",
                status="retry",
                priority=0,
                created_at=now - timedelta(minutes=1),
                next_attempt_at=now,
                payload_json={},
                provider_key="gemini",
            ))
            session.commit()
            pressure = scout_analysis_backpressure(session, "tenant-a", now=now)
            assert pressure["active"] is False  # below hard Review threshold
            assert pressure["pending_jobs"] == 1
            assert not keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure, now=now,
            )["active"]
            assert not keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure, now=now, reserve=True,
            )["active"]
            session.commit()
            blocked = keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure, now=now,
            )
            assert blocked["active"] is True
            assert blocked["reason"] == "keyword_fair_share_wait"
            assert 1 <= blocked["retry_seconds"] <= 9
            assert keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure, now=now, reserve=True,
            )["active"] is True
            assert not keyword_quote_backlog_gate(
                session, "tenant-b", pressure=pressure, now=now,
            )["active"]
            assert not keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure,
                now=now + timedelta(seconds=9),
            )["active"]
            session.query(ProcessingJobModel).update({"status": "completed"})
            session.commit()
            assert not keyword_quote_backlog_gate(
                session, "tenant-a", now=now, reserve=True,
            )["active"]
    finally:
        engine.dispose()


def test_preflight_does_not_block_search_when_fair_share_is_waiting():
    async def scenario():
        def reply(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "analysis_backpressure": {
                    "active": False, "pending_jobs": 3, "oldest_wait_seconds": 40,
                },
                "keyword_quote_backpressure": {
                    "active": True, "retry_seconds": 42,
                    "reason": "keyword_fair_share_wait",
                },
            })

        scout = QuoteScoutClient("https://example.test", "agent-test", "token-test")
        await scout.client.aclose()
        scout.client = httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(reply)
        )
        try:
            assert await scout.ensure_analysis_capacity() is True
        finally:
            await scout.close()

    asyncio.run(scenario())


def test_preflight_degrades_to_protected_quote_endpoint_on_diagnostic_outage():
    async def scenario():
        scout = QuoteScoutClient("https://example.test", "agent-test", "token-test")
        await scout.client.aclose()
        scout.client = httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        )
        try:
            assert await scout.ensure_analysis_capacity() is False
        finally:
            await scout.close()

    asyncio.run(scenario())


def test_unresolved_volume_queue_pauses_before_more_images(tmp_path):
    history = KeywordScoutHistory(tmp_path / "keyword-scout-history.json")
    history.remember_pending_quotes(["Sunset Vibes"])
    fake = SimpleNamespace(resolve_volume=AsyncMock(
        return_value={"items": [{"keyword": "Sunset Vibes", "provider": "pending"}]}
    ))

    with pytest.raises(KeywordScoutCapacityPaused) as pause:
        asyncio.run(ensure_pending_quote_volumes_drained(fake, history, set()))
    assert pause.value.reason == "pending_keyword_volumes"
    assert history.pending_quotes == ["Sunset Vibes"]

    fake.resolve_volume.return_value = {
        "items": [{"keyword": "Sunset Vibes", "provider": "aebrowse", "search_volume": 0}]
    }
    asyncio.run(ensure_pending_quote_volumes_drained(fake, history, set()))
    assert history.pending_quotes == []
    assert "sunset vibes" in history.seen_quotes


def test_blocked_pending_quote_does_not_hold_queue(tmp_path):
    history = KeywordScoutHistory(tmp_path / "keyword-scout-history.json")
    history.remember_pending_quotes(["Brand Blocked"])
    fake = SimpleNamespace(resolve_volume=AsyncMock())
    asyncio.run(ensure_pending_quote_volumes_drained(
        fake, history, {"brand blocked"},
    ))
    fake.resolve_volume.assert_not_called()