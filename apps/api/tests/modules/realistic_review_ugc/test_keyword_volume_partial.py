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


def test_aebrowse_per_keyword_trademark_status_and_details_are_persisted() -> None:
    from app.modules.realistic_review_ugc.router import _keyword_volume_response

    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            service = RrugcKeywordVolumeService(session)
            service._fetch_provider = AsyncMock(return_value={
                "success": True,
                "trademark_summary": {"safe": 1, "warning": 1, "danger": 1},
                "data": [
                    {"keyword": "matching couple hoodies hat", "search_volume": 4400,
                     "trademark": {
                         "status": "SAFE", "is_safe": True, "class_025": False,
                         "conflict_count": 0, "category": "Chưa phát hiện vi phạm",
                         "advice": "Từ khóa an toàn", "details": "Không phát hiện nhãn hiệu",
                         "matches": [], "primary_conflict": None,
                     }},
                    {"keyword": "Houston Astros hat", "search_volume": 120,
                     "trademark": {
                         "status": "WARNING", "conflict_count": 1, "class_025": True,
                         "primary_conflict": {"wordmark": "Houston Astros"},
                         "matches": [{"wordmark": "Houston Astros"}],
                         "advice": "Review trademark owner",
                     }},
                    {"keyword": "Morgan Wallen hat", "search_volume": 80,
                     "trademark": {"status": "DANGER", "conflict_count": 3}},
                    {"keyword": "Sunset Vibes hat", "search_volume": 0},
                ],
            })
            result = asyncio.run(service.resolve(
                tenant_id="tenant-1",
                keywords=["matching couple hoodies", "Houston Astros", "Morgan Wallen", "Sunset Vibes"],
                now=now,
            ))
            rows = {row.keyword: row for row in result.rows}
            assert rows["matching couple hoodies"].trademark_status == "safe"
            assert rows["matching couple hoodies"].trademark_match_count == 0
            assert rows["Houston Astros"].trademark_status == "warning"
            assert rows["Morgan Wallen"].trademark_status == "danger"
            assert rows["Sunset Vibes"].trademark_status == "unverified"
            assert rows["Houston Astros"].trademark_checked_at.replace(tzinfo=timezone.utc) == now
            assert rows["Houston Astros"].trademark_source == KEYWORD_VOLUME_PROVIDER
            detailed = _keyword_volume_response(rows["Houston Astros"])
            assert detailed.trademark_class_025 is True
            assert detailed.trademark_primary_conflict == {"wordmark": "Houston Astros"}
            assert detailed.trademark_matches == [{"wordmark": "Houston Astros"}]
            assert detailed.trademark_screened_keyword == "Houston Astros hat"
            safe_detail = _keyword_volume_response(rows["matching couple hoodies"])
            assert safe_detail.trademark_category == "Chưa phát hiện vi phạm"
            assert safe_detail.trademark_advice == "Từ khóa an toàn"

            service._fetch_provider.reset_mock()
            cached = asyncio.run(service.resolve(
                tenant_id="tenant-1", keywords=["matching couple hoodies"], now=now + timedelta(hours=1),
            ))
            assert cached.cached == 1
            service._fetch_provider.assert_not_called()
            # Missing per-keyword trademark cannot be manufactured from the
            # aggregate trademark_summary and must be eligible for refresh.
            retry = asyncio.run(service.resolve(
                tenant_id="tenant-1", keywords=["Sunset Vibes"], force=True, now=now + timedelta(hours=1),
            ))
            assert retry.provider_requested == 1
    finally:
        engine.dispose()


def test_restore_raw_provider_trademark_without_extra_external_request() -> None:
    from app.modules.realistic_review_ugc.keyword_volume import restore_cached_provider_trademark

    row = RrugcKeywordVolumeModel(
        tenant_id="tenant-1", keyword="Houston Astros",
        keyword_normalized="houston astros",
        provider_raw_json={
            "keyword": "Houston Astros hat",
            "trademark": {"status": "WARNING", "conflict_count": 2},
        },
        trademark_status="unverified",
    )
    assert restore_cached_provider_trademark(row) is True
    assert row.trademark_status == "warning"
    assert row.trademark_match_count == 2
    assert restore_cached_provider_trademark(row) is False

    invalid = RrugcKeywordVolumeModel(
        tenant_id="tenant-1", keyword="Another Keyword",
        keyword_normalized="another keyword",
        provider_raw_json={"trademark_summary": {"safe": 1}, "trademark": {"status": "UNKNOWN"}},
        trademark_status="unverified",
    )
    assert restore_cached_provider_trademark(invalid) is False
    assert invalid.trademark_status == "unverified"


def test_trademark_get_supplement_fills_post_volume_without_changing_metrics():
    import httpx

    calls = []
    def respond(request: httpx.Request):
        calls.append(request.method)
        if request.method == "POST":
            return httpx.Response(200, json={
                "success": True, "data": [
                    {"keyword": "Houston Astros hat", "search_volume": 900, "cpc_low": 1.2},
                    {"keyword": "Sunset Vibes hat", "search_volume": 10},
                ],
            })
        return httpx.Response(200, json={
            "success": True, "data": [
                {"keyword": "Houston Astros hat", "search_volume": 100,
                 "trademark": {"status": "WARNING", "conflict_count": 2}},
                {"keyword": "Sunset Vibes hat", "search_volume": 1,
                 "trademark": {"status": "SAFE", "conflict_count": 0}},
            ],
        })

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            service = RrugcKeywordVolumeService(None, http_client=client)
            return await service._fetch_provider(["Houston Astros hat", "Sunset Vibes hat"])

    result = asyncio.run(check())
    assert calls == ["POST", "GET"]
    assert result["data"][0]["search_volume"] == 900
    assert result["data"][0]["cpc_low"] == 1.2
    assert result["data"][0]["trademark"]["status"] == "WARNING"
    assert result["data"][1]["trademark"]["status"] == "SAFE"
