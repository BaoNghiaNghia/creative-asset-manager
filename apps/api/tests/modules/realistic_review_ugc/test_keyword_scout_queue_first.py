"""Keyword Scout must finish existing work before collecting more Pins."""
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


def test_quote_gate_blocks_even_one_review_job_and_releases_after_drain():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    ProcessingJobModel.__table__.create(engine)
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
            assert pressure["active"] is False  # below old 200-item threshold
            assert pressure["pending_jobs"] == 1
            assert keyword_quote_backlog_gate(
                session, "tenant-a", pressure=pressure, now=now, reserve=True,
            ) == {
                "active": True,
                "retry_seconds": 60,
                "reason": "review_analysis_queue_draining",
            }
            assert keyword_quote_backlog_gate(
                session, "tenant-b", now=now,
            )["active"] is False
            session.query(ProcessingJobModel).update({"status": "completed"})
            session.commit()
            assert keyword_quote_backlog_gate(
                session, "tenant-a", now=now, reserve=True,
            )["active"] is False
    finally:
        engine.dispose()


def test_preflight_pauses_when_review_jobs_pending_even_if_legacy_active_false():
    async def scenario():
        def reply(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={
                "analysis_backpressure": {
                    "active": False, "pending_jobs": 3, "oldest_wait_seconds": 40,
                },
                "keyword_quote_backpressure": {"active": False, "retry_seconds": 0},
            })

        scout = QuoteScoutClient("https://example.test", "agent-test", "token-test")
        await scout.client.aclose()
        scout.client = httpx.AsyncClient(
            base_url="https://example.test", transport=httpx.MockTransport(reply)
        )
        try:
            with pytest.raises(KeywordScoutCapacityPaused) as pause:
                await scout.ensure_analysis_capacity()
            assert pause.value.reason == "review_analysis_queue_draining"
        finally:
            await scout.close()

    asyncio.run(scenario())


def test_preflight_fails_closed_when_queue_status_is_unavailable():
    async def scenario():
        scout = QuoteScoutClient("https://example.test", "agent-test", "token-test")
        await scout.client.aclose()
        scout.client = httpx.AsyncClient(
            base_url="https://example.test",
            transport=httpx.MockTransport(lambda _: httpx.Response(503)),
        )
        try:
            with pytest.raises(KeywordScoutCapacityPaused):
                await scout.ensure_analysis_capacity()
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
