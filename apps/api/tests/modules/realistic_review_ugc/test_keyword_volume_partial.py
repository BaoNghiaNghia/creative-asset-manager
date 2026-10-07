"""Regression tests for partial results returned by the keyword volume provider."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.realistic_review_ugc.keyword_volume import (
    KEYWORD_VOLUME_PENDING_PROVIDER,
    KEYWORD_VOLUME_PROVIDER,
    RrugcKeywordVolumeService,
)
from app.modules.realistic_review_ugc.model import RrugcKeywordVolumeModel


def test_partial_provider_response_keeps_missing_keyword_retryable() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            service = RrugcKeywordVolumeService(session)
            service._fetch_provider = AsyncMock(return_value={
                "data": [
                    {"keyword": "Houston Astros hat", "search_volume": 500},
                    {"keyword": "Blank Result hat"},
                ],
            })
            first = asyncio.run(service.resolve(
                tenant_id="tenant-1",
                keywords=["Houston Astros", "Morgan Wallen", "Blank Result"],
                now=now,
            ))
            by_keyword = {row.keyword: row for row in first.rows}
            assert by_keyword["Houston Astros"].search_volume == 500
            assert by_keyword["Houston Astros"].provider == KEYWORD_VOLUME_PROVIDER
            for name in ("Morgan Wallen", "Blank Result"):
                assert by_keyword[name].provider == KEYWORD_VOLUME_PENDING_PROVIDER

            service._fetch_provider = AsyncMock(return_value={
                "data": [
                    {"keyword": "Morgan Wallen hat", "search_volume": 120},
                    {"keyword": "Blank Result hat", "search_volume": 0},
                ],
            })
            second = asyncio.run(service.resolve(
                tenant_id="tenant-1",
                keywords=["Houston Astros", "Morgan Wallen", "Blank Result"],
                now=now + timedelta(minutes=3),
            ))
            assert second.provider_requested == 2
            assert second.cached == 1
            by_keyword = {row.keyword: row for row in second.rows}
            assert by_keyword["Morgan Wallen"].search_volume == 120
            assert by_keyword["Morgan Wallen"].provider == KEYWORD_VOLUME_PROVIDER
            assert by_keyword["Blank Result"].search_volume == 0
            assert by_keyword["Blank Result"].provider == KEYWORD_VOLUME_PROVIDER
    finally:
        engine.dispose()


def test_forced_partial_refresh_preserves_previous_verified_volume() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    now = datetime(2026, 10, 7, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            service = RrugcKeywordVolumeService(session)
            service._fetch_provider = AsyncMock(return_value={
                "data": [{"keyword": "Houston Astros hat", "search_volume": 300}],
            })
            asyncio.run(service.resolve(
                tenant_id="tenant-1", keywords=["Houston Astros"], now=now,
            ))
            service._fetch_provider = AsyncMock(return_value={"data": []})
            refreshed = asyncio.run(service.resolve(
                tenant_id="tenant-1",
                keywords=["Houston Astros"],
                force=True,
                now=now + timedelta(minutes=1),
            ))
            row = refreshed.rows[0]
            assert row.provider == KEYWORD_VOLUME_PROVIDER
            assert row.search_volume == 300
            assert row.fetched_at.replace(tzinfo=timezone.utc) == now
    finally:
        engine.dispose()
