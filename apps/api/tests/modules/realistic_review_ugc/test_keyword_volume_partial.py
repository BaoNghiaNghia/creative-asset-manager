"""Regression tests for partial results returned by the keyword volume provider."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

from sqlalchemy import create_engine, select
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
            volume_payload = service._fetch_provider.return_value
            async def by_query(terms, *, tm_only=False):
                if not tm_only:
                    assert all(term.endswith(" hat") for term in terms)
                    return volume_payload
                assert all(not term.endswith(" hat") for term in terms)
                original_tm = [
                    {**item, "keyword": item["keyword"][:-4]}
                    for item in volume_payload["data"]
                ]
                return {"success": True, "data": original_tm}
            service._fetch_provider = AsyncMock(side_effect=by_query)
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
            assert detailed.trademark_screened_keyword == "Houston Astros"
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
            "_trademark_screened_keyword": "Houston Astros",
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


def test_tm_requests_original_keyword_and_metrics_request_hat_keyword():
    import httpx

    import json
    calls = []
    def respond(request: httpx.Request):
        keywords = json.loads(request.content.decode())["keywords"]
        calls.append((request.method, keywords))
        if keywords == ["Houston Astros hat", "Sunset Vibes hat"]:
            return httpx.Response(200, json={
                "success": True, "data": [
                    {"keyword": "Houston Astros hat", "search_volume": 900, "cpc_low": 1.2},
                    {"keyword": "Sunset Vibes hat", "search_volume": 10},
                ],
            })
        assert keywords == ["Houston Astros", "Sunset Vibes"]
        return httpx.Response(200, json={
            "success": True, "data": [
                {"keyword": "Houston Astros", "search_volume": 100,
                 "trademark": {"status": "WARNING", "conflict_count": 2}},
                {"keyword": "Sunset Vibes", "search_volume": 1,
                 "trademark": {"status": "SAFE", "conflict_count": 0}},
            ],
        })

    async def check():
        async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
            service = RrugcKeywordVolumeService(None, http_client=client)
            volume = await service._fetch_provider(["Houston Astros hat", "Sunset Vibes hat"])
            tm = await service._fetch_provider(["Houston Astros", "Sunset Vibes"], tm_only=True)
            return volume, tm

    volume, tm = asyncio.run(check())
    assert calls == [
        ("POST", ["Houston Astros hat", "Sunset Vibes hat"]),
        ("POST", ["Houston Astros", "Sunset Vibes"]),
    ]
    assert volume["data"][0]["search_volume"] == 900
    assert volume["data"][0]["cpc_low"] == 1.2
    assert tm["data"][0]["keyword"] == "Houston Astros"
    assert tm["data"][0]["trademark"]["status"] == "WARNING"
    assert tm["data"][1]["trademark"]["status"] == "SAFE"

def test_legacy_hat_tm_is_refreshed_using_original_keyword_without_refetching_volume():
    import httpx
    from app.modules.realistic_review_ugc.router import _keyword_volume_response

    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    requests = []

    def responder(request: httpx.Request) -> httpx.Response:
        import json
        searched = json.loads(request.content.decode())["keywords"]
        requests.append((request.method, searched))
        assert request.method == "POST", "volume cache must remain untouched"
        assert searched == ["Houston Astros"]
        return httpx.Response(200, json={
            "success": True,
            "data": [{
                "keyword": "Houston Astros",
                "search_volume": 1,
                "cpc_low": 99.99,
                "trademark": {"status": "DANGER", "conflict_count": 3,
                              "class_025": True, "advice": "Conflict"},
            }],
        })

    try:
        with Session(engine) as session:
            legacy = RrugcKeywordVolumeModel(
                tenant_id="tenant-1", keyword="Houston Astros",
                keyword_normalized="houston astros",
                search_volume=2200, cpc_low=0.42, cpc_high=1.25,
                provider=KEYWORD_VOLUME_PROVIDER,
                fetched_at=now - timedelta(minutes=1),
                trademark_status="safe", trademark_source=KEYWORD_VOLUME_PROVIDER,
                provider_raw_json={
                    "keyword": "Houston Astros hat",
                    "search_volume": 2200, "cpc_low": 0.42,
                    "trademark": {"status": "SAFE", "conflict_count": 0},
                },
            )
            session.add(legacy)
            session.commit()
            assert _keyword_volume_response(legacy).trademark_status == "unverified"
            async def exercise():
                async with httpx.AsyncClient(transport=httpx.MockTransport(responder)) as client:
                    service = RrugcKeywordVolumeService(session, http_client=client)
                    result = await service.resolve(
                        tenant_id="tenant-1", keywords=["Houston Astros"], now=now,
                    )
                    row = result.rows[0]
                    assert result.cached == 1 and result.provider_requested == 0
                    assert row.search_volume == 2200
                    assert row.cpc_low == 0.42 and row.cpc_high == 1.25
                    assert row.provider_raw_json["keyword"] == "Houston Astros hat"
                    assert row.trademark_status == "danger"
                    assert row.trademark_match_count == 3
                    assert _keyword_volume_response(row).trademark_screened_keyword == "Houston Astros"
                    await service.resolve(
                        tenant_id="tenant-1", keywords=["Houston Astros"], now=now,
                    )
            asyncio.run(exercise())
            assert requests == [("POST", ["Houston Astros"])]
    finally:
        engine.dispose()


def test_trademark_backfill_rechecks_legacy_markers_without_changing_volume():
    from app.modules.realistic_review_ugc import trademark_backfill
    from app.modules.realistic_review_ugc.keyword_volume import trademark_evidence_is_current

    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    now = datetime(2026, 10, 8, tzinfo=timezone.utc)
    try:
        with Session(engine) as session:
            rows = [
                RrugcKeywordVolumeModel(
                    tenant_id="tenant-1", keyword=name,
                    keyword_normalized=name.casefold(),
                    search_volume=3500 if i == 0 else 250,
                    provider=KEYWORD_VOLUME_PROVIDER,
                    fetched_at=now,
                    trademark_status="safe",
                    trademark_source=KEYWORD_VOLUME_PROVIDER,
                    provider_raw_json={
                        "keyword": name + " hat",
                        "search_volume": 3500 if i == 0 else 250,
                        "trademark": {"status": "SAFE"},
                    },
                )
                for i, name in enumerate(["Houston Astros", "Sunset Vibes"])
            ]
            session.add_all(rows)
            session.commit()
            assert trademark_backfill.count_statuses(session) == {"unverified": 2}
            assert not any(trademark_evidence_is_current(row) for row in rows)

            async def mock_original_tm(terms, *, tm_only=False):
                assert tm_only is True
                assert terms == ["Houston Astros", "Sunset Vibes"]
                return {"data": [
                    {"keyword": "Houston Astros", "trademark": {
                        "status": "DANGER", "conflict_count": 3}},
                    {"keyword": "Sunset Vibes", "trademark": {
                        "status": "SAFE", "conflict_count": 0}},
                ]}

            from unittest.mock import patch
            with patch.object(RrugcKeywordVolumeService, "_fetch_provider", side_effect=mock_original_tm):
                success, failed, pending = asyncio.run(trademark_backfill.fetch_missing(
                    session, batch_size=50, pause_seconds=0,
                ))
            assert (success, failed, pending) == (2, 0, 0)
            session.expire_all()
            stored = {
                row.keyword: row for row in session.scalars(
                    select(RrugcKeywordVolumeModel)
                ).all()
            }
            assert stored["Houston Astros"].trademark_status == "danger"
            assert stored["Houston Astros"].search_volume == 3500
            assert stored["Sunset Vibes"].search_volume == 250
            assert stored["Sunset Vibes"].trademark_status == "safe"
            assert trademark_backfill.count_statuses(session) == {"danger": 1, "safe": 1}
    finally:
        engine.dispose()

def test_trademark_duplicate_provider_rows_count_once_per_keyword():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    RrugcKeywordVolumeModel.__table__.create(engine)
    try:
        with Session(engine) as session:
            session.add(RrugcKeywordVolumeModel(
                tenant_id="tenant-a", keyword="Houston Astros",
                keyword_normalized="houston astros",
                search_volume=999, provider=KEYWORD_VOLUME_PROVIDER,
            ))
            session.commit()
            service = RrugcKeywordVolumeService(session)
            service._fetch_provider = AsyncMock(return_value={
                "data": [
                    {"keyword": "Houston Astros", "trademark": {
                        "status": "DANGER", "conflict_count": 3}},
                    {"keyword": "Houston Astros", "trademark": {
                        "status": "SAFE", "conflict_count": 0}},
                ],
            })
            checked, requested = asyncio.run(service.refresh_trademark(
                tenant_id="tenant-a", keywords=["Houston Astros"],
            ))
            assert (checked, requested) == (1, 1)
            row = session.scalar(select(RrugcKeywordVolumeModel))
            assert row.trademark_status == "danger"
            assert row.search_volume == 999
            service._fetch_provider.assert_awaited_once_with(["Houston Astros"], tm_only=True)
    finally:
        engine.dispose()
