from __future__ import annotations

import asyncio
import importlib
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from io import BytesIO
from threading import Event
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import httpx
import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import get_db
from app.domain.processing.handlers import (
    ClaimedJob,
    DeferredJobOutcome,
    JobHandlerContext,
    JobOutcome,
    WorkerDependencies,
)
from app.domain.providers.contracts import (
    AiMetadataAnalysisResult,
    AiProviderError,
    StorageProviderError,
    StoredAsset,
    StoredAssetReadStream,
)
from app.domain.providers.registry import AiProviderRegistry
from app.infrastructure.downloader.secure_image import DownloadedImage, SecureDownloadError
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.assets.model import AssetModel
from app.modules.application_logs.model import ApplicationLogModel, LogApplicationModel
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.analysis import (
    AiAuthenticityConfirmationDocument,
    ReferenceAnalysisDocument,
    ReferenceFilterPolicy,
    assess_ai_risk,
    build_ai_risk_calibration,
    build_reference_preference_model,
    calibrate_ai_risk,
    evaluate_reference,
    reference_preference_adjustment,
    reference_preference_features,
)
from app.modules.realistic_review_ugc.handler import (
    RrugcCandidateAnalyzeJobHandler,
    reference_qualification_resolution,
)
from app.modules.realistic_review_ugc.gemini_safety import deferred_rrugc_ai_retry
from app.modules.realistic_review_ugc.keyword_strategy import (
    build_campaign_search_queries,
    campaign_learning_intent,
    detect_campaign_keyword_intent,
    query_is_suppressed_for_reference_search,
)
from app.modules.realistic_review_ugc.generation import RrugcGenerationFoundation
from app.modules.realistic_review_ugc.generation_handler import RrugcGenerateJobHandler
from app.modules.realistic_review_ugc.supervisor_handler import RrugcSupervisorQaJobHandler
from app.modules.realistic_review_ugc.stage3 import (
    RrugcStage3AnalyzeJobHandler,
    RrugcStage3Service,
    Stage3UgcAnalysisDocument,
    evaluate_stage3,
    normalize_stage3_metadata,
    review_text_for_analysis,
)
from app.modules.realistic_review_ugc.stage2 import (
    DEFAULT_STAGE2_PROMPT,
    RrugcStage2Error,
    RrugcStage2Service,
)
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcVisualFingerprintModel,
    RrugcAiFeedbackModel,
    RrugcGenerationAttemptModel,
    RrugcKeywordVolumeModel,
    RrugcScoutFeedbackModel,
    RrugcStage2JobModel,
    RrugcStage3AnalysisModel,
    RrugcStage2SkillRegistryModel,
    RrugcStage2SkillVersionModel,
    RrugcSupervisorResultModel,
    RrugcReviewTaskModel,
    RrugcExportModel,
    RrugcScoutAgentModel,
    RrugcScoutRunModel,
    RrugcSourcePlanModel,
    RrugcDeliveryDestinationModel,
    RrugcDeliveryPackageModel,
    RrugcDeliveryEventModel,
    RrugcDeliveryItemModel,
    RrugcProductModel,
    RrugcProductVariantModel,
    RrugcProductReferenceModel,
    RrugcReferenceAssetModel,
    RrugcReferenceSetModel,
    RrugcReferenceSetItemModel,
    RrugcReferenceSeedModel,
)
from app.modules.realistic_review_ugc.maintenance import RrugcMaintenanceService
from app.modules.realistic_review_ugc.keyword_volume import (
    KEYWORD_VOLUME_PENDING_PROVIDER,
    KeywordVolumeError,
    RrugcKeywordVolumeService,
    normalize_keyword,
    provider_search_keyword,
)
from app.modules.realistic_review_ugc.quote_scout_analysis import HatQuoteDocument
from app.modules.realistic_review_ugc.product_page_import import ProductPageData
from app.modules.realistic_review_ugc.product_registry import RrugcProductRegistry
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.reference_recommendations import (
    build_reference_review_learning,
    discouraged_reference_sets,
    recommend_reference_assets,
    recommend_reference_set_reuse,
)
from app.modules.realistic_review_ugc.review import RrugcReviewService
from app.modules.realistic_review_ugc.scout_automation import (
    RrugcAutoScoutService,
    adaptive_scroll_batch_budget,
    adaptive_search_queries,
    keyword_health_rows,
    scout_retry_delay_seconds,
)
from app.modules.realistic_review_ugc.export import RrugcExportService
from app.modules.realistic_review_ugc.delivery import RrugcDeliveryService
from app.modules.realistic_review_ugc.delivery_automation import (
    RrugcDeliveryMaintenanceScheduler,
)
from app.modules.realistic_review_ugc.supervisor import (
    MAX_GENERATION_ATTEMPTS,
    RrugcSupervisorService,
    SupervisorAnalysisDocument,
    evaluate_supervisor,
)
from app.modules.realistic_review_ugc.router import router
from app.modules.realistic_review_ugc.schema import CandidateSubmission, ProductCreateRequest
from app.providers.ai.codex_image import CodexSkillManifest
from app.providers.decision.jev import JevCallResult, JevUsage
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
    download_reference_image,
    pinterest_original_image_url,
    pinterest_reference_image_urls,
    reference_manual_good_can_override,
    reference_resolution_usable,
    validate_image_url,
    validate_pin_url,
)


def principal() -> CurrentPrincipal:
    return CurrentPrincipal(
        user_id="user-a",
        active_tenant_id="tenant-a",
        membership_id="membership-a",
        external_identity=None,
        effective_roles=frozenset({"operator"}),
        effective_permissions=frozenset({
            "realistic_review_ugc.read",
            "realistic_review_ugc.run",
        }),
        platform_admin=False,
        session_id="session-a",
        authorization_source="test",
    )


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TenantProcessingPolicyModel.__table__.create(engine)
    ProcessingJobModel.__table__.create(engine)
    RrugcCampaignModel.__table__.create(engine)
    RrugcSourcePlanModel.__table__.create(engine)
    RrugcKeywordVolumeModel.__table__.create(engine)
    RrugcScoutFeedbackModel.__table__.create(engine)
    RrugcScoutAgentModel.__table__.create(engine)
    RrugcScoutRunModel.__table__.create(engine)
    RrugcProductModel.__table__.create(engine)
    RrugcProductVariantModel.__table__.create(engine)
    RrugcProductReferenceModel.__table__.create(engine)
    RrugcCandidateModel.__table__.create(engine)
    RrugcReferenceAssetModel.__table__.create(engine)
    RrugcReferenceSetModel.__table__.create(engine)
    RrugcReferenceSetItemModel.__table__.create(engine)
    RrugcReferenceSeedModel.__table__.create(engine)
    RrugcVisualFingerprintModel.__table__.create(engine)
    RrugcAiFeedbackModel.__table__.create(engine)
    RrugcGenerationAttemptModel.__table__.create(engine)
    RrugcStage2SkillRegistryModel.__table__.create(engine)
    RrugcStage2SkillVersionModel.__table__.create(engine)
    RrugcStage2JobModel.__table__.create(engine)
    RrugcStage3AnalysisModel.__table__.create(engine)
    RrugcSupervisorResultModel.__table__.create(engine)
    RrugcReviewTaskModel.__table__.create(engine)
    AssetModel.__table__.create(engine)
    AssetStorageObjectModel.__table__.create(engine)
    RrugcExportModel.__table__.create(engine)
    RrugcDeliveryDestinationModel.__table__.create(engine)
    RrugcDeliveryPackageModel.__table__.create(engine)
    RrugcDeliveryEventModel.__table__.create(engine)
    RrugcDeliveryItemModel.__table__.create(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


@pytest.fixture
def api(database):
    app = FastAPI()
    app.include_router(router)

    def db():
        with database() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_authenticated_principal] = principal
    with TestClient(app) as client:
        yield client


def test_quote_scout_normalizes_object_quotes_and_requires_two_words():
    document = HatQuoteDocument.model_validate({
        "is_hat": True,
        "quotes": [
            {"text": "slow mornings Club", "confidence": 0.99},
            {"text": "HOUSTON", "confidence": 0.99},
            {"text": "I'm", "confidence": 0.99},
            {"text": "HOUSTON ASTROS", "confidence": 0.99},
            {"text": "Morgan Wallen", "confidence": 0.99},
            {"text": "PLEASE BE PATIENT WITH ME. I'M FROM THE 1900s.", "confidence": 0.98},
        ],
    })

    assert document.quotes == [
        "slow mornings Club",
        "HOUSTON ASTROS",
        "Morgan Wallen",
        "PLEASE BE PATIENT WITH ME. I'M FROM THE 1900s.",
    ]
    assert document.confidence == 0.99


def test_keyword_normalizer_extracts_text_only_and_requires_two_words():
    clean, normalized = normalize_keyword(
        "{'text': 'slow mornings Club', 'confidence': 0.99}"
    )
    assert clean == "slow mornings Club"
    assert normalized == "slow mornings club"

    assert normalize_keyword("HOUSTON ASTROS") == ("HOUSTON ASTROS", "houston astros")
    assert normalize_keyword("Morgan Wallen") == ("Morgan Wallen", "morgan wallen")
    assert provider_search_keyword("IN A RELATIONSHIP") == "IN A RELATIONSHIP hat"
    assert provider_search_keyword("Bad Day To Be A Hotdog hat") == "Bad Day To Be A Hotdog hat"

    for invalid in ("HOUSTON", "I'm", "ASTROS"):
        with pytest.raises(KeywordVolumeError) as exc_info:
            normalize_keyword(invalid)
        assert exc_info.value.code == "rrugc_keyword_too_short"
        assert "at least 2 words" in exc_info.value.message


def test_keyword_volume_service_persists_and_reuses_24h_cache(database):
    calls: list[list[str]] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content.decode())
        keywords = list(payload["keywords"])
        calls.append(keywords)
        data = []
        for keyword in keywords:
            data.append({
                "keyword": keyword,
                "search_volume": 4400 if "hotdog" in keyword.casefold() else 260,
                "competition": "HIGH",
                "trademark": {"status": "SAFE", "conflict_count": 0},
                "competition_index": 100,
                "cpc_low": 0.56,
                "cpc_high": 1.96,
                "three_month_change_pct": -33.1,
                "yoy_change_pct": -45.3,
                "monthly_breakdown": [
                    {"year": 2026, "month": 7, "period": "2026-07", "volume": 5400},
                    {"year": 2026, "month": 8, "period": "2026-08", "volume": 4400},
                ],
            })
        return httpx.Response(
            200,
            request=request,
            json={
                "success": True,
                "account": "Test Ads Manager",
                "customer_id": "1234567890",
                "total_requested": len(keywords),
                "total_with_volume": len(keywords),
                "data": data,
            },
        )

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with database() as session:
                service = RrugcKeywordVolumeService(session, http_client=client)
                now = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
                first = await service.resolve(
                    tenant_id="tenant-a",
                    keywords=[
                        "A",
                        "C",
                        "Bad Day To Be A Hotdog hat",
                        "funny hotdog cap",
                        "BAD DAY TO BE A HOTDOG HAT",
                    ],
                    source_image_url="https://i.pinimg.com/736x/aa/bb/hotdog.jpg",
                    source_pin_url="https://www.pinterest.com/pin/123456789/",
                    now=now,
                )
                assert first.requested == 2
                assert first.provider_requested == 2
                assert first.cached == 0
                assert len(first.rows) == 2
                assert first.rows[0].search_volume == 4400
                assert first.rows[0].competition == "HIGH"
                assert first.rows[0].cpc_low == 0.56
                assert first.rows[0].source_image_url == (
                    "https://i.pinimg.com/736x/aa/bb/hotdog.jpg"
                )
                assert first.rows[0].source_pin_url == (
                    "https://www.pinterest.com/pin/123456789/"
                )
                assert first.rows[0].provider_raw_json[
                    "_observed_volume_history"
                ] == [{"period": "2026-10-05", "volume": 4400}]
                assert calls == [
                    ["Bad Day To Be A Hotdog hat", "funny hotdog cap hat"],
                    ["Bad Day To Be A Hotdog hat", "funny hotdog cap"],
                ]

                cached = await service.resolve(
                    tenant_id="tenant-a",
                    keywords=[
                        "Bad Day To Be A Hotdog hat",
                        "funny hotdog cap",
                    ],
                    now=now + timedelta(hours=1),
                )
                assert cached.provider_requested == 0
                assert cached.cached == 2
                assert len(calls) == 2

                forced = await service.resolve(
                    tenant_id="tenant-a",
                    keywords=["Bad Day To Be A Hotdog hat"],
                    force=True,
                    now=now + timedelta(days=1),
                )
                assert forced.provider_requested == 1
                assert forced.cached == 0
                assert len(calls) == 4

                persisted = list(
                    session.scalars(
                        select(RrugcKeywordVolumeModel).where(
                            RrugcKeywordVolumeModel.tenant_id == "tenant-a"
                        )
                    )
                )
                assert len(persisted) == 2
                hotdog = next(
                    row for row in persisted
                    if row.keyword_normalized == "bad day to be a hotdog hat"
                )
                assert hotdog.provider_raw_json["_observed_volume_history"] == [
                    {"period": "2026-10-05", "volume": 4400},
                    {"period": "2026-10-06", "volume": 4400},
                ]
                assert {row.keyword_normalized for row in persisted} == {
                    "bad day to be a hotdog hat",
                    "funny hotdog cap",
                }
                assert all(
                    row.source_image_url
                    == "https://i.pinimg.com/736x/aa/bb/hotdog.jpg"
                    for row in persisted
                )
                assert all(
                    row.source_pin_url
                    == "https://www.pinterest.com/pin/123456789/"
                    for row in persisted
                )

    asyncio.run(scenario())


def test_keyword_volume_persists_quote_before_provider_failure(database):
    calls: list[list[str]] = []

    async def provider(request: httpx.Request) -> httpx.Response:
        payload = __import__("json").loads(request.content.decode())
        calls.append(list(payload["keywords"]))
        return httpx.Response(503, request=request, json={"success": False})

    async def no_sleep(_seconds: float) -> None:
        return None

    async def scenario() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(provider)) as client:
            with database() as session:
                service = RrugcKeywordVolumeService(
                    session,
                    http_client=client,
                    sleeper=no_sleep,
                )
                with pytest.raises(KeywordVolumeError) as exc_info:
                    await service.resolve(
                        tenant_id="tenant-a",
                        keywords=["Bad Day To Be A Hotdog"],
                        now=datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc),
                    )
                assert exc_info.value.code == "rrugc_keyword_volume_provider_unavailable"

                persisted = session.scalar(
                    select(RrugcKeywordVolumeModel).where(
                        RrugcKeywordVolumeModel.tenant_id == "tenant-a",
                        RrugcKeywordVolumeModel.keyword_normalized
                        == "bad day to be a hotdog",
                    )
                )
                assert persisted is not None
                assert persisted.keyword == "Bad Day To Be A Hotdog"
                assert persisted.search_volume == 0
                assert persisted.provider == KEYWORD_VOLUME_PENDING_PROVIDER
                assert persisted.request_count == 1
                assert calls == [
                    ["Bad Day To Be A Hotdog hat"],
                    ["Bad Day To Be A Hotdog hat"],
                    ["Bad Day To Be A Hotdog hat"],
                ]

    asyncio.run(scenario())


def test_keyword_analysis_suggestions_are_tenant_scoped_ranked_and_literal(api, database):
    now = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    rows = [
        ("tenant-a", "HOUSTON ASTROS", 250, False),
        ("tenant-a", "Houston Astros Trucker Hat", 110, True),
        ("tenant-a", "Houston City Cap", 400, False),
        ("tenant-a", "Vintage Houston Hat", 10000, False),
        ("tenant-a", "100% Houston Hat", 8000, False),
        ("tenant-a", "Holiday_Houston Hat", 5000, False),
        ("tenant-b", "Houston Secret Keyword", 999999, True),
    ]
    with database() as session:
        session.add_all([
            RrugcKeywordVolumeModel(
                tenant_id=tenant,
                keyword=keyword,
                keyword_normalized=keyword.lower(),
                search_volume=volume,
                favorite=favorite,
                fetched_at=now,
                last_requested_at=now,
            )
            for tenant, keyword, volume, favorite in rows
        ])
        session.commit()

    endpoint = "/api/v1/realistic-review-ugc/keyword-analysis/suggestions"
    match = api.get(endpoint, params={"q": " houston  "})
    assert match.status_code == 200
    assert [row["keyword"] for row in match.json()] == [
        "Houston Astros Trucker Hat", "Houston City Cap",
        "HOUSTON ASTROS", "Vintage Houston Hat",
        "100% Houston Hat", "Holiday_Houston Hat",
    ]
    assert all(row["search_volume"] < 999999 for row in match.json())
    assert match.json()[0]["favorite"] is True
    assert api.get(endpoint, params={"q": "hou", "limit": 2}).json() == [
        match.json()[0], match.json()[1],
    ]
    assert [row["keyword"] for row in api.get(endpoint, params={"q": "%"}).json()] == ["100% Houston Hat"]
    assert [row["keyword"] for row in api.get(endpoint, params={"q": "_"}).json()] == ["Holiday_Houston Hat"]
    assert api.get(endpoint, params={"q": "not-existing"}).json() == []
    assert api.get(endpoint, params={"q": ""}).status_code == 422
    assert api.get(endpoint, params={"q": "a" * 101}).status_code == 422
    assert api.get(endpoint, params={"q": "houston", "limit": 99}).status_code == 422


def test_keyword_analysis_api_lists_independent_keyword_rows(api, database):
    now = datetime(2026, 10, 5, 10, 0, tzinfo=timezone.utc)
    with database() as session:
        session.add_all([
            RrugcKeywordVolumeModel(
                tenant_id="tenant-a",
                keyword="matching couple hoodies",
                keyword_normalized="matching couple hoodies",
                search_volume=4400,
                competition="HIGH",
                cpc_low=0.56,
                cpc_high=1.96,
                source_image_url="https://i.pinimg.com/736x/aa/bb/matching.jpg",
                source_pin_url="https://www.pinterest.com/pin/222/",
                picked=True,
                picked_at=now,
                picked_by_user_id="user-a",
                provider="aebrowse_google_ads",
                provider_raw_json={
                    "competition_index": 100,
                    "three_month_change_pct": -33.1,
                    "yoy_change_pct": -45.3,
                    "monthly_breakdown": [
                        {"year": 2026, "month": 7, "period": "2026-07", "volume": 5400},
                        {"year": 2026, "month": 8, "period": "2026-08", "volume": 4400},
                    ],
                },
                fetched_at=now,
                last_requested_at=now,
                created_at=now,
            ),
            RrugcKeywordVolumeModel(
                tenant_id="tenant-a",
                keyword="custom initial hoodie",
                keyword_normalized="custom initial hoodie",
                search_volume=0,
                competition="LOW",
                cpc_low=0.12,
                cpc_high=0.44,
                provider="aebrowse_google_ads",
                provider_raw_json={
                    "three_month_change_pct": 12.4,
                    "yoy_change_pct": 83.1,
                },
                fetched_at=now - timedelta(hours=1),
                last_requested_at=now - timedelta(hours=1),
                created_at=now - timedelta(days=2),
            ),
        ])
        session.commit()

    response = api.get("/api/v1/realistic-review-ugc/keyword-analysis")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2
    assert payload["overview"] == {
        "total_keywords": 2,
        "total_search_volume": 4400,
        "average_search_volume": 2200.0,
        "average_cpc": 0.77,
        "high_competition": 1,
        "zero_volume": 1,
        "short_tail_keywords": 0,
        "mid_tail_keywords": 2,
        "long_tail_keywords": 0,
        "picked_keywords": 1,
        "favorite_keywords": 0,
        "suggested_keywords": 0,
    }
    assert [row["keyword"] for row in payload["items"]] == [
        "matching couple hoodies",
        "custom initial hoodie",
    ]
    assert payload["items"][0]["source_image_url"] == (
        "https://i.pinimg.com/736x/aa/bb/matching.jpg"
    )
    assert payload["items"][0]["source_pin_url"] == (
        "https://www.pinterest.com/pin/222/"
    )
    assert payload["items"][0]["picked"] is True
    assert payload["items"][0]["picked_at"] is not None
    assert payload["items"][0]["competition_index"] == 100
    assert payload["items"][0]["three_month_change_pct"] == -33.1
    assert payload["items"][0]["yoy_change_pct"] == -45.3
    assert payload["items"][0]["trend"] == [
        {"period": "2026-07", "volume": 5400},
        {"period": "2026-08", "volume": 4400},
    ]
    assert payload["items"][1]["picked"] is False
    assert payload["items"][1]["source_image_url"] is None
    assert payload["items"][1]["source_pin_url"] is None

    searched = api.get(
        "/api/v1/realistic-review-ugc/keyword-analysis",
        params={"query": "initial"},
    )
    assert searched.status_code == 200
    assert searched.json()["total"] == 1
    assert searched.json()["items"][0]["keyword"] == "custom initial hoodie"

    used = api.get(
        "/api/v1/realistic-review-ugc/keyword-analysis",
        params={"usage": "used"},
    )
    assert used.status_code == 200
    assert used.json()["total"] == 1
    assert used.json()["items"][0]["keyword"] == "matching couple hoodies"
    assert used.json()["overview"]["picked_keywords"] == 1

    unused = api.get(
        "/api/v1/realistic-review-ugc/keyword-analysis",
        params={"usage": "unused"},
    )
    assert unused.status_code == 200
    assert unused.json()["total"] == 1
    assert unused.json()["items"][0]["keyword"] == "custom initial hoodie"

    picked = api.patch(
        f"/api/v1/realistic-review-ugc/keyword-analysis/{unused.json()['items'][0]['id']}/pick",
        json={"picked": True},
    )
    assert picked.status_code == 200
    assert picked.json()["picked"] is True
    assert picked.json()["picked_at"] is not None

    no_unused = api.get(
        "/api/v1/realistic-review-ugc/keyword-analysis",
        params={"usage": "unused"},
    )
    assert no_unused.status_code == 200
    assert no_unused.json()["total"] == 0
    assert no_unused.json()["overview"]["picked_keywords"] == 2

    expected_orders = {
        ("keyword", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("keyword", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("search_volume", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("search_volume", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("three_month_change", "asc"): ["matching couple hoodies", "custom initial hoodie"],
        ("three_month_change", "desc"): ["custom initial hoodie", "matching couple hoodies"],
        ("yoy_change", "asc"): ["matching couple hoodies", "custom initial hoodie"],
        ("yoy_change", "desc"): ["custom initial hoodie", "matching couple hoodies"],
        ("competition", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("competition", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("cpc", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("cpc", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("high_cpc", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("high_cpc", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("created_at", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("created_at", "desc"): ["matching couple hoodies", "custom initial hoodie"],
        ("fetched_at", "asc"): ["custom initial hoodie", "matching couple hoodies"],
        ("fetched_at", "desc"): ["matching couple hoodies", "custom initial hoodie"],
    }
    for (sort_by, sort_dir), expected in expected_orders.items():
        sorted_response = api.get(
            "/api/v1/realistic-review-ugc/keyword-analysis",
            params={"sort_by": sort_by, "sort_dir": sort_dir},
        )
        assert sorted_response.status_code == 200
        assert [row["keyword"] for row in sorted_response.json()["items"]] == expected


def test_keyword_analysis_tail_filters_and_favorites_are_persisted_and_tenant_scoped(api, database):
    now = datetime(2026, 10, 7, 10, 0, tzinfo=timezone.utc)
    with database() as session:
        for keyword in (
            "funny hat",
            "custom cap design",
            "a nice little hat",
            "a very nice little cap",
        ):
            session.add(RrugcKeywordVolumeModel(
                tenant_id="tenant-a",
                keyword=keyword,
                keyword_normalized=keyword,
                provider="aebrowse_google_ads",
                search_volume=300,
                fetched_at=now,
                last_requested_at=now,
            ))
        session.add(RrugcKeywordVolumeModel(
            tenant_id="tenant-b",
            keyword="other tenant secret keyword",
            keyword_normalized="other tenant secret keyword",
            provider="aebrowse_google_ads",
            search_volume=800,
            fetched_at=now,
            last_requested_at=now,
        ))
        session.commit()

    route = "/api/v1/realistic-review-ugc/keyword-analysis"
    short = api.get(route, params={"tail": "short", "page_size": 1})
    assert short.status_code == 200
    assert short.json()["total"] == 1
    assert short.json()["overview"]["short_tail_keywords"] == 1
    assert short.json()["overview"]["mid_tail_keywords"] == 2
    assert short.json()["overview"]["long_tail_keywords"] == 1
    assert [item["keyword"] for item in short.json()["items"]] == ["funny hat"]

    mid = api.get(route, params={"tail": "mid", "page_size": 1})
    assert mid.status_code == 200
    assert mid.json()["total"] == 2
    assert len(mid.json()["items"]) == 1
    mid_page_2 = api.get(route, params={"tail": "mid", "page": 2, "page_size": 1})
    assert mid_page_2.status_code == 200
    assert len(mid_page_2.json()["items"]) == 1
    assert {mid.json()["items"][0]["keyword"], mid_page_2.json()["items"][0]["keyword"]} == {
        "custom cap design", "a nice little hat"
    }

    long = api.get(route, params={"tail": "long"})
    assert long.status_code == 200
    assert long.json()["total"] == 1
    target = long.json()["items"][0]
    assert target["keyword"] == "a very nice little cap"
    assert target["favorite"] is False
    assert target["picked"] is False

    favorite = api.patch(
        f"{route}/{target['id']}/favorite",
        json={"favorite": True},
    )
    assert favorite.status_code == 200
    assert favorite.json()["favorite"] is True
    assert favorite.json()["favorite_at"] is not None
    assert favorite.json()["picked"] is False

    favorites = api.get(route, params={"favorites_only": True, "tail": "long"})
    assert favorites.status_code == 200
    assert favorites.json()["total"] == 1
    assert favorites.json()["overview"]["favorite_keywords"] == 1
    assert favorites.json()["items"][0]["id"] == target["id"]
    assert api.get(route, params={"favorites_only": True, "tail": "short"}).json()["total"] == 0
    assert api.get(route, params={"favorites_only": True, "usage": "used"}).json()["total"] == 0

    # Scout's future refreshes should not overwrite manual curation state.
    repeated = api.patch(f"{route}/{target['id']}/favorite", json={"favorite": True})
    assert repeated.status_code == 200
    assert repeated.json()["favorite"] is True
    assert api.patch(f"{route}/{target['id']}/favorite", json={"favorite": False}).json()["favorite"] is False
    assert api.get(route, params={"favorites_only": True}).json()["total"] == 0
    assert api.get(route, params={"tail": "invalid"}).status_code == 422
    assert api.patch(f"{route}/missing-id/favorite", json={"favorite": True}).status_code == 404
    with database() as session:
        tenant_b = session.scalar(
            select(RrugcKeywordVolumeModel).where(RrugcKeywordVolumeModel.tenant_id == "tenant-b")
        )
    assert tenant_b is not None
    assert api.patch(f"{route}/{tenant_b.id}/favorite", json={"favorite": True}).status_code == 404
    assert api.get(route, params={"query": "other tenant"}).json()["total"] == 0


def test_keyword_quote_backlog_fair_share_is_tenant_scoped_and_atomic(database):
    from app.modules.realistic_review_ugc.scout_automation import (
        keyword_quote_backlog_gate,
    )

    now = datetime.now(timezone.utc)
    pressure = {"active": True, "pending_jobs": 430, "oldest_wait_seconds": 2000}
    from app.modules.ai_governance.model import AiModelRateLimitStateModel

    with database() as session:
        AiModelRateLimitStateModel.__table__.create(
            bind=session.get_bind(), checkfirst=True
        )
        initial = keyword_quote_backlog_gate(
            session, "tenant-a", pressure=pressure, now=now,
        )
        assert initial["active"] is False
        allowed = keyword_quote_backlog_gate(
            session, "tenant-a", pressure=pressure, now=now, reserve=True,
        )
        session.commit()
        assert allowed["active"] is False
        blocked = keyword_quote_backlog_gate(
            session, "tenant-a", pressure=pressure, now=now,
        )
        assert blocked["active"] is True
        assert 1 <= blocked["retry_seconds"] <= 61
        assert keyword_quote_backlog_gate(
            session, "tenant-b", pressure=pressure, now=now,
        )["active"] is False
        # Clear Review backlog never consumes this protected Keyword lane.
        assert keyword_quote_backlog_gate(
            session, "tenant-a", pressure={"active": False}, now=now,
        )["active"] is False
        assert keyword_quote_backlog_gate(
            session, "tenant-a", pressure=pressure,
            now=now + timedelta(seconds=61),
        )["active"] is False


def test_scout_api_backpressure_pauses_claim_and_preserves_retryable_quote(api, database, monkeypatch):
    import app.modules.realistic_review_ugc.scout_automation as automation
    import app.modules.realistic_review_ugc.router as scout_router

    active_pressure = lambda *args, **kwargs: {
        "active": True, "pending_jobs": 430, "oldest_wait_seconds": 2000,
    }
    monkeypatch.setattr(automation, "scout_analysis_backpressure", active_pressure)
    monkeypatch.setattr(scout_router, "scout_analysis_backpressure", active_pressure)
    # An active Review backlog only throttles the independent quote fair-share
    # lane; it is no longer a blanket 180s block on every Keyword request.
    monkeypatch.setattr(
        scout_router, "keyword_quote_backlog_gate",
        lambda *args, **kwargs: {
            "active": True, "retry_seconds": 55, "reason": "keyword_fair_share_wait"
        },
    )
    with database() as session:
        agent, token = RrugcAutoScoutService(session).create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Backpressure test",
        )
        agent_id = agent.id

    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Backpressure source",
            "query": "cap review",
            "target_count": 10,
            "max_scroll_batches": 2,
            "auto_import": True,
            "auto_scout": True,
            "scan_interval_seconds": 180,
        },
    )
    assert campaign.status_code == 201

    auth = {"Authorization": f"Bearer {token}"}
    claimed = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/claim",
        headers={**auth, "X-Scout-Version": "rrugc-scout-v41"},
    )
    assert claimed.status_code == 200
    assert claimed.json() is None

    deferred = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/quote-analysis/extract",
        headers=auth,
        json={
            "image_url": "https://i.pinimg.com/736x/aa/bb/cap.jpg",
            "pin_url": "https://www.pinterest.com/pin/123/",
        },
    )
    assert deferred.status_code == 503
    assert deferred.headers["retry-after"] == "55"
    assert deferred.json()["detail"]["code"] == "rrugc_keyword_fair_share_wait"


def test_quote_scout_prefers_backup_credential_under_review_pressure(api, database, monkeypatch):
    from types import SimpleNamespace
    import app.modules.realistic_review_ugc.router as scout_router

    class FakeRegistry:
        def require(self, name):
            assert name == "gemini"
            return object()

        async def aclose(self):
            return None

    seen = {}

    async def analyze_mock(**kwargs):
        seen["credentials"] = kwargs["credential_providers"]
        return SimpleNamespace(
            quotes=["HELLO COWBOY"], is_target_cap=True,
            confidence=0.9, provider="gemini", model="gemini-test",
        )

    monkeypatch.setattr(
        scout_router, "scout_analysis_backpressure",
        lambda *args, **kwargs: {
            "active": True, "pending_jobs": 480, "oldest_wait_seconds": 2300
        },
    )
    monkeypatch.setattr(
        scout_router, "keyword_quote_backlog_gate",
        lambda *args, **kwargs: {
            "active": False, "retry_seconds": 0,
            "reason": "keyword_fair_share_allowed",
        },
    )
    monkeypatch.setattr(
        scout_router, "build_ai_provider_registry",
        lambda *args, **kwargs: FakeRegistry(),
    )
    monkeypatch.setattr(scout_router, "analyze_hat_quote", analyze_mock)
    monkeypatch.setattr(
        scout_router.CreativeAiCredentialRepository,
        "list_active_backup_providers",
        lambda self, tenant: ("gemini_backup_1", "gemini_backup_2"),
    )
    with database() as session:
        agent, token = RrugcAutoScoutService(session).create_agent(
            tenant_id="tenant-a", user_id="user-a", name="Backup preference"
        )
        agent_id = agent.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/quote-analysis/extract",
        headers={"Authorization": f"Bearer {token}"},
        json={"image_url": "https://i.pinimg.com/736x/aa/bb/cap.jpg"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["quotes"] == ["HELLO COWBOY"]
    assert seen["credentials"] == (
        "gemini_backup_1", "gemini_backup_2", "gemini",
    )


def test_quote_scout_keyword_summary_is_tenant_scoped_and_requires_agent_token(api, database):
    from app.modules.ai_operations.credential_model import CreativeAiCredentialModel

    now = datetime.now(timezone.utc)
    with database() as session:
        CreativeAiCredentialModel.__table__.create(
            bind=session.get_bind(), checkfirst=True,
        )
        agent, token = RrugcAutoScoutService(session).create_agent(
            tenant_id="tenant-a", user_id="user-a", name="Quote stats"
        )
        for tenant, text, age in [
            ("tenant-a", "hello trucker", 1),
            ("tenant-a", "hello cowboy", 48),
            ("tenant-b", "private outsider", 1),
        ]:
            session.add(RrugcKeywordVolumeModel(
                tenant_id=tenant, keyword=text, keyword_normalized=text,
                search_volume=250, provider="aebrowse_google_ads",
                created_at=now - timedelta(hours=age),
                updated_at=now - timedelta(hours=age),
                fetched_at=now, last_requested_at=now,
            ))
        session.commit()
        agent_id = agent.id
    url = f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/keyword-analysis/summary"
    result = api.get(url, headers={"Authorization": f"Bearer {token}"})
    assert result.status_code == 200
    summary = result.json()
    assert summary["total_keywords"] == 2
    assert summary["added_24h"] == 1
    assert summary["added_7d"] == 2
    assert summary["analysis_pending"] >= 0
    assert summary["analysis_oldest_wait_seconds"] >= 0
    assert isinstance(summary["analysis_backpressure_active"], bool)
    assert isinstance(summary["keyword_fair_share_limited"], bool)
    assert summary["keyword_next_slot_seconds"] >= 0
    assert summary["gemini_backup_keys_configured"] >= 0
    assert summary["last_created_at"] is not None
    assert summary["fetched_at"] is not None
    denied = api.get(url, headers={"Authorization": "Bearer incorrect-token"})
    assert denied.status_code in (401, 403)


def test_quote_scout_agent_can_submit_cached_keyword_batch(api, database):
    now = datetime.now(timezone.utc)
    with database() as session:
        agent, token = RrugcAutoScoutService(session).create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Quote Scout terminal",
        )
        session.add(
            RrugcKeywordVolumeModel(
                tenant_id="tenant-a",
                keyword="matching couple hoodies",
                keyword_normalized="matching couple hoodies",
                search_volume=4400,
                competition="HIGH",
                cpc_low=0.56,
                cpc_high=1.96,
                provider="aebrowse_google_ads",
                provider_raw_json={"keyword": "matching couple hoodies hat"},
                fetched_at=now,
                last_requested_at=now,
            )
        )
        session.commit()
        agent_id = agent.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/keyword-analysis/resolve",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "keywords": ["matching couple hoodies"],
            "source_image_url": "https://i.pinimg.com/1200x/cc/dd/new-source.jpg",
            "source_pin_url": "https://www.pinterest.com/pin/999/",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["requested"] == 1
    assert payload["provider_requested"] == 0
    assert payload["cached"] == 1
    assert payload["items"][0]["search_volume"] == 4400
    assert payload["items"][0]["trend"] == []
    assert payload["items"][0]["source_image_url"] == (
        "https://i.pinimg.com/1200x/cc/dd/new-source.jpg"
    )
    assert payload["items"][0]["source_pin_url"] == (
        "https://www.pinterest.com/pin/999/"
    )

    with database() as session:
        row = session.scalar(
            select(RrugcKeywordVolumeModel).where(
                RrugcKeywordVolumeModel.tenant_id == "tenant-a",
                RrugcKeywordVolumeModel.keyword_normalized
                == "matching couple hoodies",
            )
        )
        assert row is not None
        assert row.source_image_url == (
            "https://i.pinimg.com/1200x/cc/dd/new-source.jpg"
        )
        assert row.source_pin_url == "https://www.pinterest.com/pin/999/"


def test_pinterest_url_allowlist_is_strict():
    assert validate_pin_url("https://www.pinterest.com/pin/123/")
    assert validate_image_url("https://i.pinimg.com/736x/a/b/c.jpg")
    with pytest.raises(RrugcError):
        validate_pin_url("https://www.pinterest.com/search/pins/?q=hat")
    with pytest.raises(RrugcError):
        validate_pin_url("https://evil.example/pin/123/")
    with pytest.raises(RrugcError):
        validate_image_url("http://i.pinimg.com/a.jpg")
    with pytest.raises(RrugcError):
        validate_image_url("https://example.com/a.jpg")




def test_pinterest_original_rendition_is_preferred_when_path_is_known():
    assert (
        pinterest_original_image_url(
            "https://i.pinimg.com/736x/aa/bb/photo.jpg"
        )
        == "https://i.pinimg.com/originals/aa/bb/photo.jpg"
    )
    assert (
        pinterest_original_image_url(
            "https://i.pinimg.com/280x280_RS/aa/bb/photo.jpg"
        )
        == "https://i.pinimg.com/originals/aa/bb/photo.jpg"
    )
    assert (
        pinterest_original_image_url(
            "https://i.pinimg.com/originals/aa/bb/photo.jpg"
        )
        == "https://i.pinimg.com/originals/aa/bb/photo.jpg"
    )


def test_pinterest_reference_candidates_upgrade_thumbnail_before_fallback():
    assert pinterest_reference_image_urls(
        "https://i.pinimg.com/236x/aa/bb/photo.jpg"
    ) == [
        "https://i.pinimg.com/originals/aa/bb/photo.jpg",
        "https://i.pinimg.com/1200x/aa/bb/photo.jpg",
        "https://i.pinimg.com/736x/aa/bb/photo.jpg",
        "https://i.pinimg.com/236x/aa/bb/photo.jpg",
    ]


def test_reference_resolution_has_hard_minimum():
    assert reference_resolution_usable(736, 1104) is True
    assert reference_resolution_usable(800, 700) is True
    assert reference_resolution_usable(564, 846) is False
    assert reference_resolution_usable(1200, 300) is False


def test_reference_download_prefers_original_then_falls_back():
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")

        class RecordingDownloader:
            def __init__(self):
                self.urls: list[str] = []

            @asynccontextmanager
            async def download(self, url: str):
                self.urls.append(url)
                if "/originals/" in url or "/1200x/" in url:
                    raise SecureDownloadError("higher rendition unavailable")
                yield DownloadedImage(
                    path=path,
                    content_hash="b" * 64,
                    size_bytes=path.stat().st_size,
                    width=800,
                    height=700,
                    image_format="JPEG",
                    source_url=url,
                )

        downloader = RecordingDownloader()

        async def scenario():
            async with download_reference_image(
                "https://i.pinimg.com/736x/aa/bb/photo.jpg",
                downloader=downloader,
            ) as image:
                assert image.source_url.endswith("/736x/aa/bb/photo.jpg")

        asyncio.run(scenario())
        assert downloader.urls == [
            "https://i.pinimg.com/originals/aa/bb/photo.jpg",
            "https://i.pinimg.com/1200x/aa/bb/photo.jpg",
            "https://i.pinimg.com/736x/aa/bb/photo.jpg",
        ]


def test_reference_download_upgrades_236_thumbnail_to_736_when_larger_candidates_fail():
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")

        class RecordingDownloader:
            def __init__(self):
                self.urls: list[str] = []

            @asynccontextmanager
            async def download(self, url: str):
                self.urls.append(url)
                if "/originals/" in url or "/1200x/" in url:
                    raise SecureDownloadError("higher rendition unavailable")
                yield DownloadedImage(
                    path=path,
                    content_hash="c" * 64,
                    size_bytes=path.stat().st_size,
                    width=736,
                    height=981,
                    image_format="JPEG",
                    source_url=url,
                )

        downloader = RecordingDownloader()

        async def scenario():
            async with download_reference_image(
                "https://i.pinimg.com/236x/aa/bb/photo.jpg",
                downloader=downloader,
            ) as image:
                assert image.width == 736
                assert image.height == 981
                assert image.source_url.endswith("/736x/aa/bb/photo.jpg")

        asyncio.run(scenario())
        assert downloader.urls == [
            "https://i.pinimg.com/originals/aa/bb/photo.jpg",
            "https://i.pinimg.com/1200x/aa/bb/photo.jpg",
            "https://i.pinimg.com/736x/aa/bb/photo.jpg",
        ]


def test_pinterest_pin_url_is_canonicalized_for_source_identity():
    assert (
        validate_pin_url(
            "https://pinterest.com/pin/123456/?utm_source=test#fragment"
        )
        == "https://www.pinterest.com/pin/123456/"
    )





def test_rrugc_maintenance_repairs_orphan_analysis_stale_import_and_scout(database, monkeypatch):
    now = datetime.now(timezone.utc)
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Maintenance",
            query="candid trucker hat",
            target_count=10,
            max_scroll_batches=2,
            auto_import=True,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        orphan = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="maintenance-orphan",
            pin_url="https://www.pinterest.com/pin/900001/",
            image_url="https://i.pinimg.com/736x/aa/bb/orphan.jpg",
            status="analysis_queued",
            analysis_revision=1,
            updated_at=now - timedelta(hours=2),
        )
        stuck_import = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="maintenance-import",
            pin_url="https://www.pinterest.com/pin/900002/",
            image_url="https://i.pinimg.com/736x/aa/bb/import.jpg",
            status="importing",
            analysis_revision=1,
            import_revision=1,
            updated_at=now - timedelta(hours=2),
        )
        stale_agent = RrugcScoutAgentModel(
            tenant_id="tenant-a",
            name="Maintenance Scout",
            token_hash="hash",
            active=True,
            status="ready",
            client_version="rrugc-scout-v43",
            last_seen_at=now - timedelta(minutes=5),
            created_by_user_id="user-a",
        )
        session.add_all((orphan, stuck_import, stale_agent))
        session.commit()

        settings = Settings(
            RRUGC_IMPORT_STALE_SECONDS=3600,
            RRUGC_MAINTENANCE_BATCH_SIZE=100,
            RRUGC_GEMINI_RETRY_WAKE_BATCH_SIZE=10,
        )
        service = RrugcMaintenanceService(session, settings)
        monkeypatch.setattr(
            service,
            "gemini_capacity_available",
            lambda _tenant_id, *, now: False,
        )
        before = service.health("tenant-a", now=now)
        assert before.orphan_analysis_queued == 1
        assert before.stale_importing == 1
        assert before.scout_offline == 1

        repaired = service.repair_tenant("tenant-a", now=now)
        assert repaired["analysis_requeued"] == 1
        assert repaired["imports_requeued"] == 1
        assert repaired["scouts_offlined"] == 1

        session.refresh(orphan)
        session.refresh(stuck_import)
        session.refresh(stale_agent)
        assert orphan.status == "analysis_queued"
        assert orphan.analysis_revision == 2
        assert orphan.last_error_code == "rrugc_analysis_watchdog_requeued"
        assert stuck_import.status == "import_queued"
        assert stuck_import.import_revision == 2
        assert stale_agent.status == "offline"

        active_jobs = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.entity_id.in_((orphan.id, stuck_import.id)),
                ProcessingJobModel.status == "pending",
            )
        ))
        assert {job.job_type for job in active_jobs} == {
            "rrugc_candidate_analyze",
            "rrugc_candidate_import",
        }

        after = service.health("tenant-a", now=now)
        assert after.orphan_analysis_queued == 0
        assert after.stale_importing == 0

def test_rrugc_maintenance_wakes_deferred_gemini_jobs_when_capacity_returns(database, monkeypatch):
    now = datetime.now(timezone.utc)
    with database() as session:
        job = ProcessingJobModel(
            tenant_id="tenant-a",
            job_type="rrugc_candidate_analyze",
            entity_type="rrugc_candidate",
            entity_id="candidate-deferred",
            idempotency_key="maintenance-gemini-deferred",
            payload_json={"candidate_id": "candidate-deferred"},
            provider_key="gemini",
            provider_scope="ai",
            status="pending",
            priority=0,
            attempt_count=0,
            max_attempts=3,
            next_attempt_at=now + timedelta(hours=8),
            last_error_code="gemini_model_pool_temporarily_unavailable",
        )
        session.add(job)
        session.commit()

        service = RrugcMaintenanceService(
            session,
            Settings(RRUGC_GEMINI_RETRY_WAKE_BATCH_SIZE=10),
        )
        monkeypatch.setattr(
            service,
            "gemini_capacity_available",
            lambda _tenant_id, *, now: True,
        )
        monkeypatch.setattr(
            service,
            "gemini_rrugc_slot_available",
            lambda _tenant_id, *, now: True,
        )

        repaired = service.repair_tenant("tenant-a", now=now)
        assert repaired["gemini_jobs_woken"] == 1

        session.refresh(job)
        next_attempt = job.next_attempt_at
        if next_attempt.tzinfo is None:
            next_attempt = next_attempt.replace(tzinfo=timezone.utc)
        assert next_attempt <= now + timedelta(seconds=1)
        assert service.health("tenant-a", now=now).gemini_deferred == 0


def test_rrugc_maintenance_does_not_wake_deferred_during_model_lane_cooldown(database, monkeypatch):
    from app.modules.ai_governance.model import AiModelRateLimitStateModel
    from app.modules.processing_policy.claim import (
        RRUGC_GEMINI_LANE_PROVIDER, RRUGC_GEMINI_LANE_MODEL,
    )
    now = datetime.now(timezone.utc)
    with database() as session:
        AiModelRateLimitStateModel.__table__.create(
            bind=session.get_bind(), checkfirst=True,
        )
        job = ProcessingJobModel(
            tenant_id="tenant-a",
            job_type="rrugc_candidate_analyze",
            entity_type="rrugc_candidate",
            entity_id="candidate-cooldown",
            idempotency_key="gemini-429-cooldown",
            payload_json={"candidate_id": "candidate-cooldown"},
            provider_key="gemini", provider_scope="ai", status="retry",
            attempt_count=1, max_attempts=3,
            next_attempt_at=now + timedelta(hours=1),
            last_error_code="gemini_model_pool_temporarily_unavailable",
        )
        session.add_all([job, AiModelRateLimitStateModel(
            tenant_id="tenant-a", provider=RRUGC_GEMINI_LANE_PROVIDER,
            model=RRUGC_GEMINI_LANE_MODEL, last_started_at=now,
            next_eligible_at=now + timedelta(minutes=5),
            blocked_until=now + timedelta(minutes=5), updated_at=now,
        )])
        session.commit()
        service = RrugcMaintenanceService(
            session, Settings(RRUGC_GEMINI_RETRY_WAKE_BATCH_SIZE=10),
        )
        monkeypatch.setattr(service, "gemini_capacity_available",
                            lambda tenant_id, *, now: True)
        assert service.repair_tenant("tenant-a", now=now)["gemini_jobs_woken"] == 0
        session.refresh(job)
        retry_at = job.next_attempt_at
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=timezone.utc)
        assert retry_at > now + timedelta(minutes=50)


def test_auto_scout_claim_submit_complete_and_pin_dedupe(database):
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Auto Pinterest",
            query="candid lifestyle portrait",
            search_queries=["candid lifestyle portrait", "casual woman outdoors"],
            target_count=10,
            max_scroll_batches=3,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Studio Pinterest Scout",
        )

        claim = service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v3",
            machine_label="studio-pc",
        )
        assert claim is not None
        assert claim.campaign.id == campaign.id
        assert claim.run.status == "claimed"
        assert campaign.scan_lease_agent_id == agent.id
        assert campaign.scan_lease_run_id == claim.run.id
        assert campaign.scout_status == "busy"

        result = service.submit_candidates(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            source_query="casual woman outdoors",
            submissions=[
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/12345/?utm_source=a",
                    image_url="https://i.pinimg.com/236x/a/b/c.jpg",
                    alt_text="first rendition",
                ),
                CandidateSubmission(
                    pin_url="https://pinterest.com/pin/12345/",
                    image_url="https://i.pinimg.com/736x/a/b/c.jpg",
                    alt_text="larger rendition",
                ),
            ],
        )
        assert result.created == 1
        assert result.existing == 1
        candidates = RrugcRepository(session).list_candidates(
            "tenant-a",
            campaign.id,
        )
        assert len(candidates) == 1
        assert candidates[0].pin_url == "https://www.pinterest.com/pin/12345/"
        assert candidates[0].ai_signal_json["scout_query"] == "casual woman outdoors"
        session.refresh(claim.run)
        assert claim.run.keyword_stats_json == {
            "_scout_runtime": {
                "client_version": "rrugc-scout-v3",
                "machine_label": "studio-pc",
            },
            "casual woman outdoors": {
                "submitted": 2,
                "created": 1,
                "existing": 1,
            }
        }

        derived_result = service.submit_candidates(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            source_query="person wearing cap cafe candid photo",
            submissions=[
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/67890/",
                    image_url="https://i.pinimg.com/736x/d/e/f.jpg",
                    alt_text="derived context query",
                )
            ],
        )
        assert derived_result.created == 1
        derived = session.scalar(
            select(RrugcCandidateModel).where(
                RrugcCandidateModel.tenant_id == "tenant-a",
                RrugcCandidateModel.campaign_id == campaign.id,
                RrugcCandidateModel.pin_url
                == "https://www.pinterest.com/pin/67890/",
            )
        )
        assert derived is not None
        assert (
            derived.ai_signal_json["scout_query"]
            == "person wearing cap cafe candid photo"
        )

        completed = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            status="completed",
        )
        assert completed.status == "completed"
        session.refresh(campaign)
        assert campaign.scan_lease_run_id is None
        assert campaign.scan_lease_agent_id is None
        assert campaign.scan_next_at is not None
        assert campaign.scan_empty_streak == 0
        assert campaign.scout_status == "ready"

        candidates[0].status = "approved"
        candidates[0].ai_signal_json = {
            **(candidates[0].ai_signal_json or {}),
            "reference_manual_label": "good",
        }
        session.flush()
        health = keyword_health_rows(
            list(campaign.search_queries_json or [campaign.query]),
            [completed],
            RrugcRepository(session).candidate_keyword_outcomes(
                "tenant-a",
                campaign.id,
            ),
        )
        by_query = {row["query"]: row for row in health}
        row = by_query["casual woman outdoors"]
        assert row["found"] == 2
        assert row["new"] == 1
        assert row["duplicate"] == 1
        assert row["approved"] == 1
        assert row["ref_good"] == 1
        assert row["ref_bad"] == 0
        assert row["approved_yield"] == pytest.approx(1.0)
        assert row["reference_yield"] == pytest.approx(1.0)


def test_auto_scout_jev_shadow_records_recommendation_without_changing_query(
    database,
    monkeypatch,
):
    class FakeJevClient:
        def __init__(self):
            self.calls = []

        def evaluate(self, **kwargs):
            self.calls.append(kwargs)
            return JevCallResult(
                ok=True,
                source="provider",
                model="jev-latest",
                answers={
                    "next_query": {
                        "type": "choice",
                        "choice": "q2",
                        "confidence": 0.96,
                        "probabilities": {"q1": 0.04, "q2": 0.96},
                    }
                },
                usage=JevUsage(input_tokens=500, output_tokens=5),
                latency_ms=123,
                estimated_cost_micros=21,
                http_status=200,
            )

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.adaptive_search_queries",
        lambda *args, **kwargs: [
            "baseline realistic hat query",
            "hand holding hat candid photo",
        ],
    )

    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Jev shadow",
            query="baseline realistic hat query",
            search_queries=[
                "baseline realistic hat query",
                "hand holding hat candid photo",
            ],
            target_count=20,
            max_scroll_batches=3,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        jev = FakeJevClient()
        service = RrugcAutoScoutService(
            session,
            jev_client=jev,
            jev_scout_query_enabled=True,
            jev_mode="shadow",
        )
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Jev Shadow Scout",
        )

        claim = service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v3",
            machine_label="shadow-pc",
        )

        assert claim is not None
        assert claim.campaign.id == campaign.id
        assert claim.run.query == "baseline realistic hat query"
        shadow = claim.run.keyword_stats_json["_jev_shadow"]
        assert shadow["status"] == "completed"
        assert shadow["mode"] == "shadow"
        assert shadow["baseline_query"] == "baseline realistic hat query"
        assert shadow["recommended_query"] == "hand holding hat candid photo"
        assert shadow["agreed_with_baseline"] is False
        assert shadow["confidence"] == pytest.approx(0.96)
        assert shadow["input_tokens"] == 500
        assert shadow["estimated_cost_micros"] == 21
        assert len(jev.calls) == 1
        assert jev.calls[0]["max_retries"] == 0
        assert jev.calls[0]["state"]["search"]["baseline_choice"] == "q1"
        assert jev.calls[0]["questions"]["next_query"]["criteria"]["q2"] == (
            "hand holding hat candid photo"
        )
        diagnostics = service.diagnostics(
            agent_id=agent.id,
            raw_token=token,
        )
        campaign_diagnostics = next(
            row
            for row in diagnostics["campaigns"]
            if row["campaign_id"] == campaign.id
        )
        assert campaign_diagnostics["jev_shadow"] == {
            "observations": 1,
            "completed": 1,
            "fallback": 0,
            "invalid_answer": 0,
            "agreed": 0,
            "disagreed": 1,
            "agreement_rate": 0.0,
            "avg_latency_ms": 123.0,
            "input_tokens": 500,
            "estimated_cost_micros": 21,
        }


def test_auto_scout_jev_shadow_exception_fails_open(database, monkeypatch):
    class FailingJevClient:
        def evaluate(self, **kwargs):
            raise RuntimeError("provider bug")

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.adaptive_search_queries",
        lambda *args, **kwargs: [
            "baseline realistic hat query",
            "hand holding hat candid photo",
        ],
    )

    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Jev fail open",
            query="baseline realistic hat query",
            search_queries=[
                "baseline realistic hat query",
                "hand holding hat candid photo",
            ],
            target_count=20,
            max_scroll_batches=3,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        service = RrugcAutoScoutService(
            session,
            jev_client=FailingJevClient(),
            jev_scout_query_enabled=True,
            jev_mode="shadow",
        )
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Jev Fail Open Scout",
        )

        claim = service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v3",
            machine_label="fallback-pc",
        )

        assert claim is not None
        assert claim.campaign.id == campaign.id
        assert claim.run.query == "baseline realistic hat query"
        shadow = claim.run.keyword_stats_json["_jev_shadow"]
        assert shadow["status"] == "fallback"
        assert shadow["baseline_query"] == "baseline realistic hat query"
        assert shadow["fallback_reason"] == "unexpected_error"


def test_tenant_exact_source_dedupe_skips_repeat_ai_but_not_cross_tenant(database):
    with database() as session:
        service = RrugcService(session)
        first_campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Source owner",
            query="candid lifestyle",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        first_rows, first_created, first_existing = service.ingest_candidates(
            campaign=first_campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/exact-tenant-source/",
                image_url="https://i.pinimg.com/736x/exact-source-a.jpg",
                alt_text="candid reference",
            )],
            source_query="first source query",
        )
        assert (first_created, first_existing) == (1, 0)
        first = first_rows[0]
        first.status = "approved"
        first.analyzed_at = datetime.now(timezone.utc)
        session.commit()

        second_campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Second campaign",
            query="another lifestyle query",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        duplicate_rows, duplicate_created, duplicate_existing = service.ingest_candidates(
            campaign=second_campaign,
            submissions=[CandidateSubmission(
                pin_url="https://pinterest.com/pin/exact-tenant-source/?utm_source=repeat",
                image_url="https://i.pinimg.com/236x/exact-source-b.jpg",
                alt_text="same Pinterest pin",
            )],
            source_query="second source query",
        )
        assert (duplicate_created, duplicate_existing) == (0, 1)
        duplicate = duplicate_rows[0]
        assert duplicate.campaign_id == second_campaign.id
        assert duplicate.status == "rejected_duplicate"
        assert duplicate.reject_reason == "EXACT_SOURCE_DUPLICATE"
        assert duplicate.ai_signal_json["duplicate_exact_candidate_id"] == first.id
        assert duplicate.ai_signal_json["duplicate_exact_campaign_id"] == first_campaign.id
        assert duplicate.ai_signal_json["scout_query"] == "second source query"
        assert session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == duplicate.id,
            )
        ) is None

        other_tenant_campaign, _ = service.create_campaign(
            tenant_id="tenant-b",
            user_id="user-b",
            name="Other tenant campaign",
            query="candid lifestyle",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        isolated_rows, isolated_created, isolated_existing = service.ingest_candidates(
            campaign=other_tenant_campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/exact-tenant-source/",
                image_url="https://i.pinimg.com/736x/exact-source-c.jpg",
                alt_text="same pin different tenant",
            )],
            source_query="tenant b query",
        )
        assert (isolated_created, isolated_existing) == (1, 0)
        assert isolated_rows[0].status == "analysis_queued"
        assert session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == isolated_rows[0].id,
            )
        ) is not None


def test_visual_fingerprint_repository_uses_dedicated_rows(database):
    with database() as session:
        service = RrugcService(session)
        campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Fingerprint campaign",
            query="candid lifestyle",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _existing = service.ingest_candidates(
            campaign=campaign,
            submissions=[
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/fingerprint-a/",
                    image_url="https://i.pinimg.com/736x/fingerprint-a.jpg",
                ),
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/fingerprint-b/",
                    image_url="https://i.pinimg.com/736x/fingerprint-b.jpg",
                ),
            ],
            source_query="fingerprint query",
        )
        assert created == 2
        first, second = rows
        first.status = "approved"
        first.analyzed_at = datetime.now(timezone.utc)

        repository = RrugcRepository(session)
        repository.replace_visual_fingerprints(
            first,
            ["aa11", "bb22", "aa11"],
        )
        session.flush()

        assert set(repository.visual_fingerprint_rows(
            "tenant-a",
            second.id,
        )) == {"aa11", "bb22"}
        assert session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == second.id,
            )
        ) is not None

        repository.replace_visual_fingerprints(first, ["cc33"])
        first.diversity_signature = "home|portrait_close|eye_level|standing"
        session.flush()
        assert repository.visual_fingerprint_rows(
            "tenant-a",
            second.id,
        ) == ["cc33"]
        assert repository.campaign_diversity_signature_count(
            "tenant-a",
            campaign.id,
            second.id,
            "home|portrait_close|eye_level|standing",
        ) == 1
        assert len(list(session.scalars(
            select(RrugcVisualFingerprintModel).where(
                RrugcVisualFingerprintModel.tenant_id == "tenant-a",
                RrugcVisualFingerprintModel.candidate_id == first.id,
            )
        ))) == 1

        first.status = "rejected_context"
        session.flush()
        assert repository.visual_fingerprint_rows(
            "tenant-a",
            second.id,
        ) == []
        assert repository.campaign_diversity_signature_count(
            "tenant-a",
            campaign.id,
            second.id,
            "home|portrait_close|eye_level|standing",
        ) == 0


def test_candidate_keyword_outcomes_uses_recent_history_limit(database):
    with database() as session:
        service = RrugcService(session)
        campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Keyword history",
            query="candid lifestyle",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        base = datetime(2026, 1, 1, tzinfo=timezone.utc)
        created_rows = []
        for index, query in enumerate(("old query", "middle query", "new query")):
            rows, _created, _existing = service.ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url=f"https://www.pinterest.com/pin/history-{index}/",
                    image_url=f"https://i.pinimg.com/736x/history-{index}.jpg",
                )],
                source_query=query,
            )
            row = rows[0]
            row.status = "approved"
            row.created_at = base + timedelta(seconds=index)
            created_rows.append(row)
        session.flush()

        outcomes = RrugcRepository(session).candidate_keyword_outcomes(
            "tenant-a",
            campaign.id,
            limit=2,
        )
        assert [row[0] for row in outcomes] == ["new query", "middle query"]


def test_auto_scout_v12_claims_only_ready_source_plan_campaigns(database):
    with database() as session:
        legacy, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Legacy campaign",
            query="generic candid lifestyle",
            search_queries=["generic candid lifestyle"],
            target_count=10,
            max_scroll_batches=3,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        source_campaign, _source_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Source campaign",
            query="grandpa golf course candid",
            search_queries=["grandpa golf course candid"],
            target_count=20,
            max_scroll_batches=3,
            auto_import=True,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        session.add(
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id="root",
                source_file_id="source-file",
                source_parent_folder_id="source-folder",
                source_relative_path="Grandpa/Navy/design.webp",
                source_name="design.webp",
                source_mime_type="image/webp",
                source_revision="a" * 64,
                analysis_revision=1,
                target_count=20,
                status="ready",
                campaign_id=source_campaign.id,
                created_by_user_id="user-a",
            )
        )
        session.commit()

        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Source-only Scout",
        )

        claim = service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v12",
            machine_label="studio-pc",
        )

        assert claim is not None
        assert claim.campaign.id == source_campaign.id
        assert claim.campaign.id != legacy.id


def test_auto_scout_v19_diagnostics_matches_source_plan_claim_eligibility(
    database,
    monkeypatch,
):
    with database() as session:
        campaign, _token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Queued source plan",
            query="embroidered hat candid",
            search_queries=["embroidered hat candid"],
            target_count=20,
            max_scroll_batches=3,
            auto_import=True,
            auto_scout=True,
            scan_interval_seconds=120,
        )
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="root",
            source_file_id="queued-source",
            source_parent_folder_id="source-folder",
            source_relative_path="Queued/design.webp",
            source_name="design.webp",
            source_mime_type="image/webp",
            source_revision="b" * 64,
            analysis_revision=1,
            target_count=20,
            status="queued",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        session.add(plan)
        session.commit()

        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Source plan diagnostics Scout",
        )

        monkeypatch.setattr(
            RrugcRepository,
            "recent_scout_shadow_stats",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                AssertionError("Jev shadow history must not be queried while disabled")
            ),
        )

        diagnostics = service.diagnostics(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v19",
        )
        row = next(
            item
            for item in diagnostics["campaigns"]
            if item["campaign_id"] == campaign.id
        )
        assert row["reason"] == "source_plan_not_ready"
        assert row["source_plan_required"] is True
        assert row["source_plan_ready"] is False
        assert row["source_plan_statuses"] == {"queued": 1}
        assert row["jev_shadow"] is None
        assert diagnostics["claimable"] == 0

        assert service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v19",
            machine_label="studio-pc",
        ) is None

        plan.status = "ready"
        session.commit()

        diagnostics = service.diagnostics(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v19",
        )
        row = next(
            item
            for item in diagnostics["campaigns"]
            if item["campaign_id"] == campaign.id
        )
        assert row["reason"] == "claimable"
        assert row["source_plan_ready"] is True
        assert row["source_plan_statuses"] == {"ready": 1}
        assert diagnostics["claimable"] == 1


def test_stage3_ugc_decision_rejects_low_fidelity_or_missing_product():
    ready = evaluate_stage3(
        Stage3UgcAnalysisDocument(
            people_count=1,
            person_visible=True,
            hat_visible=True,
            product_visible=True,
            embroidery_visible=True,
            mobile_ugc_score=0.82,
            photorealism_score=0.88,
            product_visibility_score=0.91,
            review_fit_score=0.86,
            scene_type="home",
            framing_type="medium",
            evidence=["person wearing the hat", "product is clearly visible"],
            summary="Natural lifestyle frame suitable for a customer review.",
            review_text="I wear this one for errands and weekend plans because the embroidered detail makes it feel personal without being over the top.",
        )
    )
    assert ready.status == "ready"
    assert ready.reject_reasons == []
    assert ready.final_score > 0.8

    fallback_document = Stage3UgcAnalysisDocument(
        people_count=1,
        person_visible=True,
        hat_visible=True,
        product_visible=True,
        embroidery_visible=True,
        mobile_ugc_score=0.82,
        photorealism_score=0.88,
        product_visibility_score=0.91,
        review_fit_score=0.86,
        scene_type="home",
        framing_type="medium",
        evidence=["person wearing the hat"],
        summary="Natural lifestyle frame.",
    )
    varied_reviews: list[str] = []
    used_reviews: set[str] = set()
    for index in range(100):
        copy = review_text_for_analysis(
            fallback_document,
            f"analysis-{index}",
            existing_texts=used_reviews,
        )
        varied_reviews.append(copy)
        used_reviews.add(copy)
    assert len(set(varied_reviews)) == 100
    assert {copy.count(".") for copy in varied_reviews} >= {1, 2, 3}
    assert len({copy.split(maxsplit=1)[0] for copy in varied_reviews}) >= 8
    assert all(not copy.lower().startswith("obsessed with") for copy in varied_reviews)

    normalized = normalize_stage3_metadata(
        {
            "people_count": 2,
            "person_visible": True,
            "hat_visible": True,
            "product_visible": True,
            "embroidery_visible": True,
            "mobile_ugc_score": 8,
            "photorealism_score": 7,
            "product_visibility_score": 9,
            "review_fit_score": 90,
            "evidence": ["visible"] * 8,
            "review_text": "Casual review copy that is long enough for the schema.",
            "unexpected": "ignored",
        }
    )
    normalized_document = Stage3UgcAnalysisDocument.model_validate(normalized)
    assert normalized_document.mobile_ugc_score == 0.8
    assert normalized_document.photorealism_score == 0.7
    assert normalized_document.product_visibility_score == 0.9
    assert normalized_document.review_fit_score == 0.9
    assert normalized_document.summary
    assert len(normalized_document.evidence) == 6
    assert "unexpected" not in normalized

    soft_scores_are_diagnostic = evaluate_stage3(
        Stage3UgcAnalysisDocument(
            people_count=1,
            person_visible=True,
            hat_visible=True,
            product_visible=True,
            embroidery_visible=False,
            mobile_ugc_score=0.35,
            photorealism_score=0.4,
            product_visibility_score=0.45,
            review_fit_score=0.3,
            scene_type="outdoor",
            framing_type="medium",
            evidence=["person visibly wearing the hat"],
            summary="Visible person and product with softer quality scores.",
            review_text="I like the casual look of this hat and would wear it for everyday errands.",
        )
    )
    assert soft_scores_are_diagnostic.status == "rejected"
    assert set(soft_scores_are_diagnostic.reject_reasons) == {
        "photorealism_below_threshold",
        "ugc_authenticity_below_threshold",
        "product_clarity_below_threshold",
        "review_fit_below_threshold",
    }

    rejected = evaluate_stage3(
        Stage3UgcAnalysisDocument(
            people_count=0,
            person_visible=False,
            hat_visible=True,
            product_visible=True,
            embroidery_visible=False,
            mobile_ugc_score=0.35,
            photorealism_score=0.9,
            product_visibility_score=0.8,
            review_fit_score=0.3,
            scene_type="studio",
            framing_type="product_only",
            evidence=["hat shown without a person"],
            summary="Product-only frame.",
            review_text="The design caught my eye right away, but I would want a better lifestyle photo before using this as my main review image.",
        )
    )
    assert rejected.status == "rejected"
    assert rejected.reject_reasons == ["no_visible_person", "ugc_authenticity_below_threshold", "review_fit_below_threshold"]


def test_stage3_analysis_worker_persists_ready_result(database):
    class FakeStage3AnalysisProvider:
        provider_name = "gemini"
        supports_single = True
        supports_batch = False
        default_model = "fake-stage3-vision"

        async def analyze_single(self, input):
            assert input.metadata_profile == "rrugc_stage3_ugc_review"
            assert input.metadata_profile_version == "rrugc-stage3-ugc-v2"
            assert input.image_bytes
            assert input.image_mime_type == "image/jpeg"
            return AiMetadataAnalysisResult(
                metadata={
                    "people_count": 1,
                    "person_visible": True,
                    "hat_visible": True,
                    "product_visible": True,
                    "embroidery_visible": True,
                    "mobile_ugc_score": 0.84,
                    "photorealism_score": 0.9,
                    "product_visibility_score": 0.92,
                    "review_fit_score": 0.88,
                    "scene_type": "home",
                    "framing_type": "medium",
                    "evidence": [
                        "person visibly wearing the hat",
                        "hat and embroidery are clearly visible",
                    ],
                    "summary": "Natural phone-style lifestyle image suitable for a review card.",
                    "review_text": "I grabbed this hat for everyday wear and the embroidered detail gives it just enough personality. It has an easy, casual look I can throw on with anything.",
                },
                provider="gemini",
                model="fake-stage3-vision",
            )

    with database() as session:
        campaign, _token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Stage 3 handler",
            query="ugc handler",
            search_queries=["ugc handler"],
            target_count=1,
            max_scroll_batches=1,
            auto_import=True,
            auto_scout=False,
        )
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="root",
            source_file_id="stage3-handler-source",
            source_parent_folder_id="folder-handler",
            source_relative_path="Product/Black/front.png",
            source_name="front.png",
            source_mime_type="image/png",
            source_revision="e" * 64,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        session.add(plan)
        session.flush()
        stage2 = RrugcStage2JobModel(
            tenant_id="tenant-a",
            source_plan_id=plan.id,
            campaign_id=campaign.id,
            source_revision=plan.source_revision,
            skill_name="test-stage2",
            selected_candidate_ids_json=[],
            selected_reference_snapshot_json=[],
            status="completed",
            idempotency_key="stage3-handler-stage2",
            output_remote_file_id="stage3-handler-output",
            output_remote_folder_id="folder-handler",
            output_content_hash="f" * 64,
            output_width=1024,
            output_height=1024,
            output_content_type="image/png",
            output_size_bytes=2048,
            completed_at=datetime.now(timezone.utc),
            created_by_user_id="user-a",
        )
        session.add(stage2)
        session.flush()
        analysis, created = RrugcStage3Service(session).ensure_analysis_for_job(
            tenant_id="tenant-a",
            stage2_job=stage2,
        )
        assert created is True
        session.commit()
        analysis_id = analysis.id

        processing_job = session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == "tenant-a",
                ProcessingJobModel.job_type == "rrugc_stage3_analyze",
                ProcessingJobModel.entity_id == analysis_id,
            )
        )
        assert processing_job is not None
        claimed = ClaimedJob(
            id=processing_job.id,
            tenant_id=processing_job.tenant_id,
            job_type=processing_job.job_type,
            entity_type=processing_job.entity_type,
            entity_id=processing_job.entity_id,
            payload=processing_job.payload_json,
            attempt_count=processing_job.attempt_count,
            lease_owner="test-worker",
            provider_key="gemini",
        )

    registry = AiProviderRegistry()
    registry.register("gemini", FakeStage3AnalysisProvider())
    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=database,
            storage_provider=FakeStorage(),
            ai_provider_registry=registry,
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("rrugc-stage3-handler-test"), {}),
    )
    outcome = RrugcStage3AnalyzeJobHandler()(context)
    assert outcome.outcome == JobOutcome.COMPLETED

    with database() as session:
        persisted = session.get(RrugcStage3AnalysisModel, analysis_id)
        assert persisted is not None
        assert persisted.status == "ready"
        assert persisted.people_count == 1
        assert persisted.person_visible is True
        assert persisted.hat_visible is True
        assert persisted.product_visible is True
        assert persisted.embroidery_visible is True
        assert persisted.mobile_ugc_score == pytest.approx(0.84)
        assert persisted.photorealism_score == pytest.approx(0.9)
        assert persisted.product_visibility_score == pytest.approx(0.92)
        assert persisted.review_fit_score == pytest.approx(0.88)
        assert persisted.final_score > 0.85
        assert persisted.provider == "gemini"
        assert persisted.model == "fake-stage3-vision"
        assert persisted.reviewer_name is not None
        assert persisted.reviewer_name.endswith(".")
        assert persisted.star_rating in {3, 4, 5}
        assert persisted.review_text is not None
        assert len(persisted.review_text) >= 30
        assert not persisted.review_text.lower().startswith("obsessed with")
        assert persisted.review_generated_at is not None
        assert persisted.completed_at is not None
        assert persisted.reject_reasons_json == []


def test_stage3_review_groups_completed_stage2_outputs_by_folder(api, database):
    now = datetime.now(timezone.utc)
    with database() as session:
        campaign, _token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Stage 3 folder grouping",
            query="ugc review groups",
            search_queries=["ugc review groups"],
            target_count=10,
            max_scroll_batches=1,
            auto_import=True,
            auto_scout=False,
        )
        plans = [
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id="root",
                source_file_id="source-stage3-a",
                source_parent_folder_id="folder-a",
                source_relative_path="Product A/Natural-Navy/front.png",
                source_name="front.png",
                source_mime_type="image/png",
                source_revision="a" * 64,
                status="ready",
                campaign_id=campaign.id,
                created_by_user_id="user-a",
            ),
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id="root",
                source_file_id="source-stage3-b",
                source_parent_folder_id="folder-a",
                source_relative_path="Product A/Natural-Navy/back.png",
                source_name="back.png",
                source_mime_type="image/png",
                source_revision="b" * 64,
                status="ready",
                campaign_id=campaign.id,
                created_by_user_id="user-a",
            ),
            RrugcSourcePlanModel(
                tenant_id="tenant-a",
                root_folder_id="root",
                source_file_id="source-stage3-c",
                source_parent_folder_id="folder-b",
                source_relative_path="Product B/Black/front.png",
                source_name="front.png",
                source_mime_type="image/png",
                source_revision="c" * 64,
                status="ready",
                campaign_id=campaign.id,
                created_by_user_id="user-a",
            ),
        ]
        session.add_all(plans)
        session.flush()

        jobs = [
            RrugcStage2JobModel(
                tenant_id="tenant-a",
                source_plan_id=plans[0].id,
                campaign_id=campaign.id,
                source_revision=plans[0].source_revision,
                skill_name="test-stage2",
                selected_candidate_ids_json=[],
                selected_reference_snapshot_json=[],
                status="completed",
                idempotency_key="stage3-a-1",
                output_remote_file_id="out-a-1",
                output_remote_folder_id="folder-a",
                output_width=1024,
                output_height=1024,
                output_content_type="image/png",
                completed_at=now - timedelta(minutes=3),
                created_by_user_id="user-a",
            ),
            RrugcStage2JobModel(
                tenant_id="tenant-a",
                source_plan_id=plans[1].id,
                campaign_id=campaign.id,
                source_revision=plans[1].source_revision,
                skill_name="test-stage2",
                selected_candidate_ids_json=[],
                selected_reference_snapshot_json=[],
                status="completed",
                idempotency_key="stage3-a-2",
                output_remote_file_id="out-a-2",
                output_remote_folder_id="folder-a",
                output_width=1024,
                output_height=1024,
                output_content_type="image/png",
                completed_at=now - timedelta(minutes=2),
                created_by_user_id="user-a",
            ),
            RrugcStage2JobModel(
                tenant_id="tenant-a",
                source_plan_id=plans[2].id,
                campaign_id=campaign.id,
                source_revision=plans[2].source_revision,
                skill_name="test-stage2",
                selected_candidate_ids_json=[],
                selected_reference_snapshot_json=[],
                status="completed",
                idempotency_key="stage3-b-1",
                output_remote_file_id="out-b-1",
                output_remote_folder_id="folder-b",
                output_width=1200,
                output_height=900,
                output_content_type="image/jpeg",
                completed_at=now - timedelta(minutes=1),
                created_by_user_id="user-a",
            ),
            RrugcStage2JobModel(
                tenant_id="tenant-a",
                source_plan_id=plans[0].id,
                campaign_id=campaign.id,
                source_revision=plans[0].source_revision,
                skill_name="test-stage2",
                selected_candidate_ids_json=[],
                selected_reference_snapshot_json=[],
                status="running",
                idempotency_key="stage3-running",
                output_remote_file_id="out-running",
                output_remote_folder_id="folder-a",
                created_by_user_id="user-a",
            ),
        ]
        session.add_all(jobs)
        session.commit()

    response = api.get("/api/v1/realistic-review-ugc/stage3/review-groups")
    assert response.status_code == 200
    payload = response.json()
    assert payload["total_groups"] == 2
    assert payload["total_images"] == 3

    groups = {item["folder_id"]: item for item in payload["items"]}
    assert groups["folder-a"]["folder_name"] == "Natural-Navy"
    assert groups["folder-a"]["folder_path"] == "Product A/Natural-Navy"
    assert groups["folder-a"]["image_count"] == 2
    assert len(groups["folder-a"]["images"]) == 2
    assert all(
        image["preview_url"].startswith(
            "/api/v1/realistic-review-ugc/stage2-jobs/"
        )
        for image in groups["folder-a"]["images"]
    )
    assert all(
        image["original_url"]
        == (
            "/api/v1/realistic-review-ugc/stage2-jobs/"
            + image["stage2_job_id"]
            + "/output"
        )
        for image in groups["folder-a"]["images"]
    )
    assert groups["folder-b"]["folder_name"] == "Black"
    assert groups["folder-b"]["image_count"] == 1
    assert all(
        image["output_remote_file_id"] != "out-running"
        for group in payload["items"]
        for image in group["images"]
    )
    assert payload["pending_images"] == 3
    assert groups["folder-a"]["status"] == "pending"
    assert all(
        image["analysis_status"] == "pending"
        for image in groups["folder-a"]["images"]
    )

    queued = api.post(
        "/api/v1/realistic-review-ugc/stage3/review-groups/analyze",
        json={"folder_id": "folder-a", "force": False},
    )
    assert queued.status_code == 202
    assert queued.json() == {"eligible": 2, "queued": 2, "existing": 0, "remaining": 0, "has_more": False}

    duplicate = api.post(
        "/api/v1/realistic-review-ugc/stage3/review-groups/analyze",
        json={"folder_id": "folder-a", "force": False},
    )
    assert duplicate.status_code == 202
    assert duplicate.json() == {"eligible": 0, "queued": 0, "existing": 0, "remaining": 0, "has_more": False}

    with database() as session:
        analyses = session.scalars(
            select(RrugcStage3AnalysisModel).where(
                RrugcStage3AnalysisModel.tenant_id == "tenant-a"
            )
        ).all()
        processing_jobs = session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.tenant_id == "tenant-a",
                ProcessingJobModel.job_type == "rrugc_stage3_analyze",
            )
        ).all()
        assert len(analyses) == 2
        assert len(processing_jobs) == 2
        assert {row.status for row in analyses} == {"queued"}

    refreshed = api.get("/api/v1/realistic-review-ugc/stage3/review-groups")
    assert refreshed.status_code == 200
    refreshed_payload = refreshed.json()
    refreshed_groups = {
        item["folder_id"]: item for item in refreshed_payload["items"]
    }
    assert refreshed_payload["analyzing_images"] == 2
    assert refreshed_payload["pending_images"] == 1
    assert refreshed_groups["folder-a"]["status"] == "analyzing"
    assert refreshed_groups["folder-a"]["analyzing_count"] == 2


def test_stage5_analyze_all_drains_older_outputs_beyond_initial_500(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a", user_id="user-a", name="Large review batch",
            query="UGC test", target_count=10, max_scroll_batches=1,
            auto_import=False, auto_scout=False,
        )
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a", root_folder_id="root", source_file_id="source-large",
            source_parent_folder_id="folder-large", source_relative_path="Items/design.png",
            source_name="design.png", source_mime_type="image/png",
            source_revision="a" * 64, status="ready",
            campaign_id=campaign.id, created_by_user_id="user-a",
        )
        session.add(plan)
        session.flush()
        session.add_all([
            RrugcStage2JobModel(
                tenant_id="tenant-a", source_plan_id=plan.id,
                campaign_id=campaign.id, source_revision=plan.source_revision,
                skill_name="test", selected_candidate_ids_json=[],
                selected_reference_snapshot_json=[], status="completed",
                idempotency_key=f"large-{i}", output_remote_file_id=f"large-{i}",
                output_remote_folder_id="folder-large",
                output_content_hash=f"{i:064x}", created_by_user_id="user-a",
            )
            for i in range(502)
        ])
        session.commit()

    route = "/api/v1/realistic-review-ugc/stage3/review-groups/analyze"
    first = api.post(route, json={"folder_id": "folder-large"})
    assert first.status_code == 202, first.text
    assert first.json() == {"eligible": 502, "queued": 500, "existing": 0, "remaining": 2, "has_more": True}
    second = api.post(route, json={"folder_id": "folder-large"})
    assert second.status_code == 202, second.text
    assert second.json() == {"eligible": 2, "queued": 2, "existing": 0, "remaining": 0, "has_more": False}
    third = api.post(route, json={"folder_id": "folder-large"})
    assert third.json() == {"eligible": 0, "queued": 0, "existing": 0, "remaining": 0, "has_more": False}
    with database() as session:
        assert session.query(RrugcStage3AnalysisModel).filter(
            RrugcStage3AnalysisModel.tenant_id == "tenant-a"
        ).count() == 502


def test_stage2_job_uses_up_to_three_drive_ready_pinterest_refs(database, monkeypatch):
    codex_home = (Path(__file__).resolve().parents[5] / "deploy" / "codex").resolve()
    settings = Settings(
        CODEX_IMAGE_HOME=str(codex_home),
        CODEX_IMAGE_GENERATION_ENABLED=True,
        IMAGE_GENERATION_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True,
    )
    with database() as session:
        campaign, _token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Stage 2 embroidery",
            query="embroidered hat context",
            search_queries=["embroidered hat context"],
            target_count=50,
            max_scroll_batches=3,
            auto_import=True,
            auto_scout=True,
        )
        plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="root",
            source_file_id="source-stage2",
            source_relative_path="Hats/design.png",
            source_name="design.png",
            source_mime_type="image/png",
            source_size_bytes=1024,
            source_revision="c" * 64,
            analysis_revision=1,
            embroidery_signature="d" * 64,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        alternate_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="root",
            source_file_id="source-stage2-alt",
            source_parent_folder_id="hat-folder-alt",
            source_relative_path="Hats/design-alt.png",
            source_name="design-alt.png",
            source_mime_type="image/png",
            source_size_bytes=2048,
            source_revision="e" * 64,
            analysis_revision=1,
            embroidery_signature="d" * 64,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        session.add_all([plan, alternate_plan])
        session.flush()
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.stage2.random.choice",
            lambda rows: next(
                row for row in rows if row.source_file_id == "source-stage2-alt"
            ),
        )

        candidates = []
        for index in range(11):
            candidate = RrugcCandidateModel(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                source_key=f"stage2-{index}",
                pin_url=f"https://www.pinterest.com/pin/{1000 + index}/",
                image_url=f"https://i.pinimg.com/736x/ref-{index}.jpg",
                status="drive_ready",
                image_format="JPEG",
                remote_file_id=f"drive-ref-{index}",
                content_hash=f"{index + 1:064x}",
                ai_signal_json={"reference_manual_label": "good"},
            )
            session.add(candidate)
            candidates.append(candidate)
        session.commit()

        service = RrugcStage2Service(session, settings)
        selected = [row.id for row in candidates[:3]]
        row, created = service.create_job(
            tenant_id="tenant-a",
            user_id="user-a",
            source_plan_id=plan.id,
            selected_candidate_ids=selected,
        )

        assert created is True
        assert row.skill_name == "gatorhats-8869-image-studio"
        assert row.status == "queued"
        assert row.selected_candidate_ids_json == selected
        assert len(row.selected_reference_snapshot_json) == 3
        assert row.selected_source_snapshot_json == {
            "source_plan_id": alternate_plan.id,
            "remote_file_id": "source-stage2-alt",
            "remote_folder_id": "hat-folder-alt",
            "source_name": "design-alt.png",
            "content_type": "image/png",
            "size_bytes": 2048,
            "source_revision": "e" * 64,
        }
        assert row.prompt_text == DEFAULT_STAGE2_PROMPT
        assert all(
            item["remote_file_id"].startswith("drive-ref-")
            for item in row.selected_reference_snapshot_json
        )
        processing_job = session.get(ProcessingJobModel, row.processing_job_id)
        assert processing_job is not None
        assert processing_job.job_type == "rrugc_stage2_generate"
        assert processing_job.entity_id == row.id
        queued_at = row.queued_at
        next_attempt_at = processing_job.next_attempt_at
        if queued_at.tzinfo is None:
            queued_at = queued_at.replace(tzinfo=timezone.utc)
        if next_attempt_at.tzinfo is None:
            next_attempt_at = next_attempt_at.replace(tzinfo=timezone.utc)
        assert next_attempt_at - queued_at == timedelta(seconds=10)

        next_run, next_created = service.create_job(
            tenant_id="tenant-a",
            user_id="user-a",
            source_plan_id=plan.id,
            selected_candidate_ids=selected,
        )
        assert next_created is True
        assert next_run.id != row.id
        assert next_run.selected_candidate_ids_json == selected
        assert next_run.prompt_text == DEFAULT_STAGE2_PROMPT
        assert next_run.selected_source_snapshot_json["remote_file_id"] == "source-stage2-alt"

        # History belongs to the embroidery group, not only the current
        # representative source-plan ID.
        row.status = "completed"
        row.source_plan_id = alternate_plan.id
        session.commit()
        with pytest.raises(RrugcStage2Error) as completed_exc:
            service.create_job(
                tenant_id="tenant-a",
                user_id="user-a",
                source_plan_id=plan.id,
                selected_candidate_ids=[selected[0]],
            )
        assert completed_exc.value.code == "stage2_reference_already_generated"

        with pytest.raises(RrugcStage2Error) as exc:
            service.create_job(
                tenant_id="tenant-a",
                user_id="user-a",
                source_plan_id=plan.id,
                selected_candidate_ids=[candidate.id for candidate in candidates[:4]],
            )
        assert exc.value.code == "stage2_reference_limit_exceeded"

        cancelled = service.cancel_recent_batch(
            tenant_id="tenant-a",
            user_id="user-a",
            source_plan_id=plan.id,
        )
        assert [item.id for item in cancelled] == [next_run.id]
        assert next_run.status == "cancelled"
        cancelled_processing = session.get(ProcessingJobModel, next_run.processing_job_id)
        assert cancelled_processing is not None
        assert cancelled_processing.status == "failed"
        assert cancelled_processing.last_error_code == "operation_cancelled"

        expired_run, expired_created = service.create_job(
            tenant_id="tenant-a",
            user_id="user-a",
            source_plan_id=plan.id,
            selected_candidate_ids=[candidates[3].id],
        )
        assert expired_created is True
        expired_queued_at = expired_run.queued_at
        if expired_queued_at.tzinfo is None:
            expired_queued_at = expired_queued_at.replace(tzinfo=timezone.utc)
        with pytest.raises(RrugcStage2Error) as expired_exc:
            service.cancel_recent_batch(
                tenant_id="tenant-a",
                user_id="user-a",
                source_plan_id=plan.id,
                now=expired_queued_at + timedelta(seconds=11),
            )
        assert expired_exc.value.code == "stage2_cancel_window_expired"
        session.refresh(expired_run)
        assert expired_run.status == "queued"
        expired_processing = session.get(ProcessingJobModel, expired_run.processing_job_id)
        assert expired_processing is not None
        assert expired_processing.status == "pending"

        # A prior expired queued job must never block cancelling a new batch.
        fresh, fresh_created = service.create_job(
            tenant_id="tenant-a", user_id="user-a",
            source_plan_id=plan.id,
            selected_candidate_ids=[candidates[4].id],
        )
        assert fresh_created
        # Reproduce a queued job from an earlier run, not another one
        # enqueued within the same 10-second grace window.
        expired_run.queued_at = fresh.queued_at - timedelta(seconds=25)
        session.flush()
        cancelled = service.cancel_recent_batch(
            tenant_id="tenant-a", user_id="user-a",
            source_plan_id=plan.id, job_ids=[expired_run.id, fresh.id],
            now=fresh.queued_at + timedelta(seconds=1),
        )
        assert [job.id for job in cancelled] == [fresh.id]
        session.refresh(expired_run)
        assert expired_run.status == "queued"
        assert session.get(ProcessingJobModel, expired_run.processing_job_id).status == "pending"
        with pytest.raises(RrugcStage2Error) as forbidden:
            service.cancel_recent_batch(
                tenant_id="tenant-a", user_id="user-b",
                source_plan_id=plan.id, job_ids=[expired_run.id],
            )
        assert forbidden.value.code == "stage2_cancel_ids_invalid"


def test_auto_scout_claim_reconciles_expired_lease_even_when_campaign_not_claimable(database):
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Expired lease cleanup",
            query="candid cap photo",
            target_count=50,
            max_scroll_batches=2,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=180,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Lease Cleanup Scout",
        )

        first = service.claim(agent_id=agent.id, raw_token=token)
        assert first is not None
        assert campaign.scan_lease_run_id == first.run.id

        campaign.auto_scout = False
        campaign.scan_lease_expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

        assert service.claim(agent_id=agent.id, raw_token=token) is None

        session.refresh(campaign)
        session.refresh(first.run)
        assert first.run.status == "cancelled"
        assert first.run.last_error_code == "scout_lease_expired"
        assert first.run.completed_at is not None
        assert campaign.scan_lease_agent_id is None
        assert campaign.scan_lease_run_id is None
        assert campaign.scan_lease_expires_at is None
        assert campaign.scan_last_error_code == "scout_lease_expired"
        assert campaign.scan_next_at is None


def test_auto_scout_empty_runs_back_off_moderately(database, monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.uniform",
        lambda *_args: 1.0,
    )
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Steady scan cadence",
            query="candid lifestyle photo",
            target_count=100,
            max_scroll_batches=2,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=180,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Steady Scout",
        )

        first = service.claim(agent_id=agent.id, raw_token=token)
        assert first is not None
        first_run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=first.run.id,
            status="completed",
        )
        session.refresh(campaign)
        assert campaign.scan_empty_streak == 1
        assert campaign.scan_failure_streak == 0
        assert campaign.scan_next_at is not None
        assert (
            campaign.scan_next_at - first_run.completed_at
        ).total_seconds() == 270
        assert service.claim(agent_id=agent.id, raw_token=token) is None

        campaign.scan_next_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        second = service.claim(agent_id=agent.id, raw_token=token)
        assert second is not None
        assert second.run.max_scroll_batches == 4
        second_run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=second.run.id,
            status="completed",
        )
        session.refresh(campaign)
        assert campaign.scan_empty_streak == 2
        assert campaign.scan_failure_streak == 0
        assert campaign.scan_next_at is not None
        assert (
            campaign.scan_next_at - second_run.completed_at
        ).total_seconds() == 360


def test_scout_retry_delay_is_error_class_aware(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.uniform",
        lambda *_args: 1.0,
    )
    assert scout_retry_delay_seconds(
        status="completed",
        error_code=None,
        scan_interval_seconds=180,
        empty_streak=0,
        failure_streak=0,
        created_count=4,
    ) == 180
    assert scout_retry_delay_seconds(
        status="completed",
        error_code=None,
        scan_interval_seconds=180,
        empty_streak=4,
        failure_streak=0,
        created_count=0,
    ) == 540
    assert scout_retry_delay_seconds(
        status="needs_login",
        error_code="pinterest_access_gate_timeout",
        scan_interval_seconds=180,
        empty_streak=0,
        failure_streak=1,
        created_count=0,
    ) == 360
    assert scout_retry_delay_seconds(
        status="failed",
        error_code="cam_http_429",
        scan_interval_seconds=180,
        empty_streak=0,
        failure_streak=2,
        created_count=0,
    ) == 720
    assert scout_retry_delay_seconds(
        status="failed",
        error_code="pinterest_scan_failed",
        scan_interval_seconds=180,
        empty_streak=0,
        failure_streak=3,
        created_count=0,
    ) == 720


def test_auto_scout_backfill_rearms_missing_capacity_without_erasing_scout_backoff(database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Backfill campaign",
            query="candid cap photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=180,
        )
        service = RrugcService(session)
        now = datetime.now(timezone.utc)
        future = now + timedelta(minutes=5)

        campaign.scout_status = "ready"
        campaign.scan_next_at = future
        assert service.ensure_scout_backfill(campaign, now=now) is True
        assert campaign.status == "running"
        assert campaign.scan_next_at == now

        campaign.scout_status = "error"
        campaign.scan_next_at = future
        assert service.ensure_scout_backfill(campaign, now=now) is True
        assert campaign.scan_next_at == future


def test_auto_scout_failure_streak_grows_then_resets(database, monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.uniform",
        lambda *_args: 1.0,
    )
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Backoff campaign",
            query="candid cap photo",
            target_count=100,
            max_scroll_batches=2,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=180,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Backoff Scout",
        )

        first = service.claim(agent_id=agent.id, raw_token=token)
        assert first is not None
        first_run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=first.run.id,
            status="failed",
            error_code="cam_http_429",
        )
        session.refresh(campaign)
        assert campaign.scan_failure_streak == 1
        assert campaign.scout_status == "error"
        assert (
            campaign.scan_next_at - first_run.completed_at
        ).total_seconds() == 360

        campaign.scan_next_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        second = service.claim(agent_id=agent.id, raw_token=token)
        assert second is not None
        second_run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=second.run.id,
            status="failed",
            error_code="cam_http_429",
        )
        session.refresh(campaign)
        assert campaign.scan_failure_streak == 2
        assert (
            campaign.scan_next_at - second_run.completed_at
        ).total_seconds() == 720

        campaign.scan_next_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        healthy = service.claim(agent_id=agent.id, raw_token=token)
        assert healthy is not None
        healthy.run.created_count = 1
        session.flush()
        healthy_run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=healthy.run.id,
            status="completed",
        )
        session.refresh(campaign)
        assert campaign.scan_failure_streak == 0
        assert campaign.scan_empty_streak == 0
        assert (
            campaign.scan_next_at - healthy_run.completed_at
        ).total_seconds() == 180


def test_auto_scout_scroll_budget_deepens_after_repeat_runs():
    assert adaptive_scroll_batch_budget(
        6,
        scan_attempt_count=0,
        empty_streak=0,
    ) == 6
    assert adaptive_scroll_batch_budget(
        6,
        scan_attempt_count=3,
        empty_streak=0,
    ) == 9
    assert adaptive_scroll_batch_budget(
        6,
        scan_attempt_count=2,
        empty_streak=2,
    ) == 18
    assert adaptive_scroll_batch_budget(
        20,
        scan_attempt_count=100,
        empty_streak=100,
    ) == 50


def test_auto_scout_quality_pipeline_caps_to_target(database):
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Quality-first capped scout",
            query="candid lifestyle photo",
            target_count=1,
            max_scroll_batches=3,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=60,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Quality Scout",
        )

        claim = service.claim(
            agent_id=agent.id,
            raw_token=token,
            client_version="rrugc-scout-v3",
        )
        assert claim is not None
        assert claim.pipeline_count == 0

        result = service.submit_candidates(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            submissions=[
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/quality-1/",
                    image_url="https://i.pinimg.com/736x/quality/1.jpg",
                    alt_text="candid outdoor photo",
                ),
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/quality-2/",
                    image_url="https://i.pinimg.com/736x/quality/2.jpg",
                    alt_text="casual lifestyle photo",
                ),
            ],
        )
        assert result.created == 1
        assert result.existing == 0
        assert result.progress == 0
        assert result.pipeline_count == 1

        candidates = RrugcRepository(session).list_candidates(
            "tenant-a",
            campaign.id,
        )
        assert len(candidates) == 1

        service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            status="completed",
        )
        campaign.scan_next_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

        assert service.claim(agent_id=agent.id, raw_token=token) is None


def test_auto_scout_supports_multiple_active_agents_and_isolated_reset(database):
    with database() as session:
        service = RrugcAutoScoutService(session)
        agent_a, token_a = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="BaoNghia",
        )
        agent_b, token_b = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="DESKTOP-91TD9B5",
        )

        rows = service.list_agents(tenant_id="tenant-a")
        assert {row.id for row in rows} == {agent_a.id, agent_b.id}
        assert all(row.active for row in rows)

        reset_agent, reset_token = service.reset_agent_pairing(
            tenant_id="tenant-a",
            agent_id=agent_a.id,
        )
        assert reset_agent.id == agent_a.id
        assert reset_token != token_a
        assert reset_agent.status == "offline"
        assert reset_agent.machine_label is None
        assert reset_agent.client_version is None
        assert reset_agent.last_seen_at is None

        with pytest.raises(RrugcError) as stale:
            service.heartbeat(
                agent_id=agent_a.id,
                raw_token=token_a,
                status="ready",
            )
        assert stale.value.status_code == 401

        heartbeat_b = service.heartbeat(
            agent_id=agent_b.id,
            raw_token=token_b,
            status="ready",
            machine_label="DESKTOP-91TD9B5",
        )
        assert heartbeat_b.machine_label == "DESKTOP-91TD9B5"

        heartbeat_a = service.heartbeat(
            agent_id=agent_a.id,
            raw_token=reset_token,
            status="ready",
            machine_label="BaoNghia",
        )
        assert heartbeat_a.machine_label == "BaoNghia"

        archived_b = service.archive_agent(
            tenant_id="tenant-a",
            agent_id=agent_b.id,
        )
        assert archived_b.active is False
        assert archived_b.status == "offline"

        with pytest.raises(RrugcError) as archived:
            service.heartbeat(
                agent_id=agent_b.id,
                raw_token=token_b,
                status="ready",
            )
        assert archived.value.status_code == 401


def test_auto_scout_rejects_wrong_agent_token_and_cross_agent_run(database):
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Protected Auto Scout",
            query="outdoor candid",
            target_count=5,
            max_scroll_batches=2,
            auto_import=False,
            auto_scout=True,
            scan_interval_seconds=300,
        )
        service = RrugcAutoScoutService(session)
        agent_a, token_a = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Agent A",
        )
        agent_b, token_b = service.create_agent(
            tenant_id="tenant-b",
            user_id="user-b",
            name="Agent B",
        )

        with pytest.raises(RrugcError) as wrong_token:
            service.claim(agent_id=agent_a.id, raw_token="wrong-token")
        assert wrong_token.value.status_code == 401

        claim = service.claim(agent_id=agent_a.id, raw_token=token_a)
        assert claim is not None
        assert claim.campaign.id == campaign.id

        with pytest.raises(RrugcError) as cross_agent:
            service.submit_candidates(
                agent_id=agent_b.id,
                raw_token=token_b,
                run_id=claim.run.id,
                submissions=[
                    CandidateSubmission(
                        pin_url="https://www.pinterest.com/pin/555/",
                        image_url="https://i.pinimg.com/736x/5.jpg",
                    )
                ],
            )
        assert cross_agent.value.status_code == 404


def test_auto_scout_needs_login_releases_lease_and_requeues(database):
    with database() as session:
        campaign, _legacy_token = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Login gate",
            query="review portrait",
            target_count=5,
            max_scroll_batches=2,
            auto_import=True,
            auto_scout=True,
            scan_interval_seconds=300,
        )
        service = RrugcAutoScoutService(session)
        agent, token = service.create_agent(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Login Agent",
        )
        claim = service.claim(agent_id=agent.id, raw_token=token)
        assert claim is not None

        run = service.complete(
            agent_id=agent.id,
            raw_token=token,
            run_id=claim.run.id,
            status="needs_login",
            error_code="pinterest_login_required",
        )
        assert run.status == "needs_login"
        session.refresh(campaign)
        session.refresh(agent)
        assert campaign.scout_status == "needs_login"
        assert campaign.scan_lease_run_id is None
        assert campaign.scan_next_at is not None
        assert campaign.scan_last_error_code == "pinterest_login_required"
        assert agent.status == "needs_login"



def test_keyword_scout_logs_use_agent_token_and_expire_after_five_days(api, database):
    engine = database.kw["bind"]
    LogApplicationModel.__table__.create(engine, checkfirst=True)
    ApplicationLogModel.__table__.create(engine, checkfirst=True)

    agent_response = api.post(
        "/api/v1/realistic-review-ugc/scout-agents",
        json={"name": "Keyword Scout Log Agent"},
    )
    assert agent_response.status_code == 201
    agent = agent_response.json()

    occurred_at = datetime(2026, 10, 5, 12, 0, tzinfo=timezone.utc)
    payload = {
        "events": [
            {
                "event_id": "evt-keyword-scout-0001",
                "event_type": "keyword_scout_batch_completed",
                "level": "info",
                "occurred_at": occurred_at.isoformat(),
                "payload": {
                    "scout_type": "keyword",
                    "visible": 31,
                    "fresh": 12,
                },
            }
        ]
    }
    response = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent['id']}/logs",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
        json=payload,
    )
    assert response.status_code == 200
    assert response.json() == {
        "accepted": 1,
        "created": 1,
        "retention_days": 5,
    }

    retry = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent['id']}/logs",
        headers={"Authorization": f"Bearer {agent['agent_token']}"},
        json=payload,
    )
    assert retry.status_code == 200
    assert retry.json()["created"] == 0

    with database() as session:
        application = session.scalar(
            select(LogApplicationModel).where(
                LogApplicationModel.slug == "rrugc-scout",
            )
        )
        assert application is not None
        logs = list(
            session.scalars(
                select(ApplicationLogModel).where(
                    ApplicationLogModel.application_id == application.id,
                )
            )
        )
        assert len(logs) == 1
        row = logs[0]
        assert row.event_type == "keyword_scout_batch_completed"
        assert row.payload_json["agent_id"] == agent["id"]
        assert row.payload_json["scout_type"] == "keyword"
        assert row.expires_at - row.received_at == timedelta(days=5)


def test_auto_scout_claim_prioritizes_campaign_with_scarcer_pipeline(api, database):
    agent_response = api.post(
        "/api/v1/realistic-review-ugc/scout-agents",
        json={"name": "Scarcity Priority Agent"},
    )
    assert agent_response.status_code == 201
    agent = agent_response.json()

    richer_response = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Richer campaign",
            "query": "embroidered cap lifestyle",
            "target_count": 10,
            "max_scroll_batches": 1,
            "auto_import": True,
            "auto_scout": True,
            "scan_interval_seconds": 180,
        },
    )
    assert richer_response.status_code == 201
    richer_id = richer_response.json()["id"]

    sparse_response = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Sparse campaign",
            "query": "embroidered cap candid",
            "target_count": 10,
            "max_scroll_batches": 1,
            "auto_import": True,
            "auto_scout": True,
            "scan_interval_seconds": 180,
        },
    )
    assert sparse_response.status_code == 201
    sparse_id = sparse_response.json()["id"]

    with database.begin() as session:
        richer = session.get(RrugcCampaignModel, richer_id)
        sparse = session.get(RrugcCampaignModel, sparse_id)
        assert richer is not None
        assert sparse is not None
        due = datetime.now(timezone.utc) - timedelta(minutes=5)
        richer.scan_next_at = due - timedelta(minutes=1)
        sparse.scan_next_at = due
        for index in range(3):
            session.add(
                RrugcCandidateModel(
                    tenant_id="tenant-a",
                    campaign_id=richer_id,
                    source_key=f"richer-{index}",
                    pin_url=f"https://www.pinterest.com/pin/richer-{index}/",
                    image_url=f"https://i.pinimg.com/736x/richer-{index}.jpg",
                    status="drive_ready",
                    analysis_revision=1,
                )
            )

    claim = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent['id']}/claim",
        headers={
            "Authorization": "Bearer " + agent["agent_token"],
            "X-Scout-Version": "rrugc-scout-v3",
            "X-Scout-Machine": "scarcity-priority-test",
        },
    )
    assert claim.status_code == 200
    payload = claim.json()
    assert payload["campaign_id"] == sparse_id
    assert payload["progress"] == 0
    assert payload["pipeline_count"] == 0


def test_auto_scout_claim_includes_approved_related_pin_seeds(api, database):
    agent_response = api.post(
        "/api/v1/realistic-review-ugc/scout-agents",
        json={"name": "Related Seed Agent"},
    )
    assert agent_response.status_code == 201
    agent = agent_response.json()

    campaign_response = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Related seed campaign",
            "query": "candid lifestyle",
            "target_count": 10,
            "max_scroll_batches": 1,
            "auto_import": False,
            "auto_scout": True,
            "scan_interval_seconds": 180,
        },
    )
    assert campaign_response.status_code == 201
    campaign_id = campaign_response.json()["id"]

    with database.begin() as session:
        now = datetime.now(timezone.utc)
        used_seed = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="used-related-seed",
            pin_url="https://www.pinterest.com/pin/used-related-seed/",
            image_url="https://i.pinimg.com/736x/used-related-seed.jpg",
            alt_text="used seed",
            status="approved",
            analysis_revision=1,
            final_score=0.72,
            analyzed_at=now - timedelta(days=3),
        )
        manual_seed = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="manual-related-seed",
            pin_url="https://www.pinterest.com/pin/manual-related-seed/",
            image_url="https://i.pinimg.com/736x/manual-related-seed.jpg",
            alt_text="manual seed",
            status="approved",
            analysis_revision=1,
            final_score=0.80,
            analyzed_at=now - timedelta(days=1),
            ai_signal_json={"reference_manual_label": "good"},
        )
        auto_seed = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="approved-related-seed",
            pin_url="https://www.pinterest.com/pin/approved-related-seed/",
            image_url="https://i.pinimg.com/736x/approved-related-seed.jpg",
            alt_text="approved seed",
            status="approved",
            analysis_revision=1,
            final_score=0.99,
            analyzed_at=now,
        )
        rejected_seed = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="rejected-related-seed",
            pin_url="https://www.pinterest.com/pin/rejected-related-seed/",
            image_url="https://i.pinimg.com/736x/rejected-related-seed.jpg",
            alt_text="rejected seed",
            status="approved",
            analysis_revision=1,
            final_score=1.0,
            analyzed_at=now,
            ai_signal_json={"reference_manual_label": "bad"},
        )
        session.add_all([used_seed, manual_seed, auto_seed, rejected_seed])
        session.flush()
        source_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="seed-root",
            source_file_id="seed-source",
            source_relative_path="seed-source.png",
            source_name="seed-source.png",
            source_mime_type="image/png",
            source_revision="a" * 64,
            status="ready",
            campaign_id=campaign_id,
            created_by_user_id="user-a",
        )
        session.add(source_plan)
        session.flush()
        session.add(
            RrugcStage2JobModel(
                tenant_id="tenant-a",
                source_plan_id=source_plan.id,
                campaign_id=campaign_id,
                source_revision=source_plan.source_revision,
                skill_name="test-skill",
                skill_source="local",
                selected_candidate_ids_json=[used_seed.id],
                selected_reference_snapshot_json=[],
                status="completed",
                idempotency_key="used-related-seed-job",
                completed_at=now,
                created_by_user_id="user-a",
            )
        )
        session.flush()
        learned_rows = RrugcRepository(session).reference_feedback_training_rows(
            "tenant-a",
            legacy_campaign_id=campaign_id,
        )
        assert any(
            label == "ref_good"
            and isinstance(signal, dict)
            and signal.get("reference_feedback_kind") == "stage2_used"
            for label, signal in learned_rows
        )

    claim = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent['id']}/claim",
        headers={
            "Authorization": "Bearer " + agent["agent_token"],
            # Generic campaign in this fixture: use a pre-source-plan client
            # version so claim selection does not require a Source Plan row.
            "X-Scout-Version": "rrugc-scout-v3",
            "X-Scout-Machine": "related-seed-test",
        },
    )
    assert claim.status_code == 200
    payload = claim.json()
    assert payload["related_seeds"][:3] == [
        {
            "pin_url": "https://www.pinterest.com/pin/used-related-seed/",
            "image_url": "https://i.pinimg.com/736x/used-related-seed.jpg",
            "alt_text": "used seed",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/manual-related-seed/",
            "image_url": "https://i.pinimg.com/736x/manual-related-seed.jpg",
            "alt_text": "manual seed",
        },
        {
            "pin_url": "https://www.pinterest.com/pin/approved-related-seed/",
            "image_url": "https://i.pinimg.com/736x/approved-related-seed.jpg",
            "alt_text": "approved seed",
        },
    ]
    assert all(
        seed["pin_url"] != "https://www.pinterest.com/pin/rejected-related-seed/"
        for seed in payload["related_seeds"]
    )
    assert {
        "https://www.pinterest.com/pin/used-related-seed/",
        "https://www.pinterest.com/pin/manual-related-seed/",
        "https://www.pinterest.com/pin/approved-related-seed/",
        "https://www.pinterest.com/pin/rejected-related-seed/",
    }.issubset(set(payload["known_pin_urls"]))
    assert isinstance(payload["query_performance"], list)


def test_visual_learning_seed_candidates_prioritize_stage2_and_feedback(database):
    with database() as session:
        service = RrugcService(session)
        campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="visual seed learning",
            query="person wearing cap candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        submissions = [
            CandidateSubmission(
                pin_url=f"https://www.pinterest.com/pin/visual-seed-{index}/",
                image_url=f"https://i.pinimg.com/736x/visual-seed-{index}.jpg",
            )
            for index in range(4)
        ]
        rows, created, _ = service.ingest_candidates(
            campaign=campaign,
            submissions=submissions,
            source_query=campaign.query,
        )
        assert created == 4
        stage2_good, manual_good, stage2_bad, cleared = rows
        now = datetime.now(timezone.utc)
        for index, candidate in enumerate(rows, start=1):
            candidate.status = "drive_ready"
            candidate.analyzed_at = now
            candidate.remote_file_id = f"drive-visual-seed-{index}"
            candidate.remote_folder_id = "drive-folder"
            candidate.content_hash = f"{index:064x}"
            candidate.image_format = "jpeg"
            candidate.size_bytes = 12345 + index
            candidate.width = 1200
            candidate.height = 1200
        session.commit()

        service.mark_candidate_reference_label(
            manual_good,
            label="good",
            note=None,
            user_id="user-a",
        )
        service.mark_candidate_reference_label(
            stage2_bad,
            label="bad",
            note="Do not learn this visual style.",
            user_id="user-a",
        )
        service.mark_candidate_reference_label(
            cleared,
            label="good",
            note=None,
            user_id="user-a",
        )
        service.mark_candidate_reference_label(
            cleared,
            label="clear",
            note=None,
            user_id="user-a",
        )

        source_plan = RrugcSourcePlanModel(
            tenant_id="tenant-a",
            root_folder_id="visual-seed-root",
            source_file_id="visual-seed-source",
            source_relative_path="visual-seed-source.png",
            source_name="visual-seed-source.png",
            source_mime_type="image/png",
            source_revision="b" * 64,
            status="ready",
            campaign_id=campaign.id,
            created_by_user_id="user-a",
        )
        session.add(source_plan)
        session.flush()
        for suffix, candidate in (
            ("good", stage2_good),
            ("bad", stage2_bad),
        ):
            session.add(
                RrugcStage2JobModel(
                    tenant_id="tenant-a",
                    source_plan_id=source_plan.id,
                    campaign_id=campaign.id,
                    source_revision=source_plan.source_revision,
                    skill_name="test-skill",
                    skill_source="local",
                    selected_candidate_ids_json=[candidate.id],
                    selected_reference_snapshot_json=[],
                    status="completed",
                    idempotency_key=f"visual-seed-{suffix}-job",
                    completed_at=now,
                    created_by_user_id="user-a",
                )
            )
        session.commit()

        learning_intent = campaign_learning_intent(
            campaign_id=campaign.id,
            name=campaign.name,
            queries=list(campaign.search_queries_json or [campaign.query]),
            product_snapshot=campaign.product_snapshot_json,
        )
        learned = RrugcRepository(session).visual_learning_seed_candidates(
            "tenant-a",
            campaign.id,
            intent=learning_intent,
            positive_limit=4,
            negative_limit=4,
        )
        positives = [
            (candidate.id, source_kind)
            for label, candidate, source_kind in learned
            if label == "positive"
        ]
        negatives = [
            (candidate.id, source_kind)
            for label, candidate, source_kind in learned
            if label == "negative"
        ]

        assert (stage2_good.id, "stage2_used") in positives
        assert (manual_good.id, "ref_good") in positives
        assert positives.index((stage2_good.id, "stage2_used")) < positives.index(
            (manual_good.id, "ref_good")
        )
        assert all(candidate_id != stage2_bad.id for candidate_id, _ in positives)
        assert (stage2_bad.id, "ref_bad") in negatives
        assert all(candidate_id != cleared.id for candidate_id, _ in positives)
        assert all(candidate_id != cleared.id for candidate_id, _ in negatives)


def test_auto_scout_agent_api_pairing_claim_and_campaign_controls(api, database):
    created = api.post(
        "/api/v1/realistic-review-ugc/scout-agents",
        json={"name": "Desktop Pinterest"},
    )
    assert created.status_code == 201
    payload = created.json()
    agent_id = payload["id"]
    token = payload["agent_token"]
    assert token
    assert payload["status"] == "offline"

    second = api.post(
        "/api/v1/realistic-review-ugc/scout-agents",
        json={"name": "DESKTOP-91TD9B5"},
    )
    assert second.status_code == 201
    second_payload = second.json()
    assert second_payload["id"] != agent_id

    old_token = token
    repaired = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/reset-pairing",
    )
    assert repaired.status_code == 200
    repaired_payload = repaired.json()
    assert repaired_payload["id"] == agent_id
    assert repaired_payload["agent_token"] != token
    assert repaired_payload["name"] == "Desktop Pinterest"
    token = repaired_payload["agent_token"]

    stale_claim = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/claim",
        headers={"Authorization": "Bearer " + old_token},
    )
    assert stale_claim.status_code == 401

    listed_after_reset = api.get("/api/v1/realistic-review-ugc/scout-agents")
    assert listed_after_reset.status_code == 200
    assert len(listed_after_reset.json()) == 2
    assert {row["id"] for row in listed_after_reset.json()} == {
        agent_id,
        second_payload["id"],
    }

    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "API auto scout",
            "query": "natural lifestyle review",
            "search_queries": [
                "natural lifestyle review",
                "casual woman outdoors",
                "candid smiling portrait",
            ],
            "target_count": 10,
            "max_scroll_batches": 2,
            "auto_import": True,
            "auto_scout": True,
            "scan_interval_seconds": 180,
        },
    )
    assert campaign.status_code == 201
    campaign_payload = campaign.json()
    campaign_id = campaign_payload["id"]
    assert campaign_payload["search_queries"] == [
        "natural lifestyle review",
        "casual woman outdoors",
        "candid smiling portrait",
    ]

    claim = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/claim",
        headers={
            "Authorization": "Bearer " + token,
            "X-Scout-Version": "rrugc-scout-v3",
            "X-Scout-Machine": "desktop-test",
        },
    )
    assert claim.status_code == 200
    work = claim.json()
    assert work["campaign_id"] == campaign_id
    assert sorted(work["search_queries"]) == sorted(campaign_payload["search_queries"])
    assert work["pipeline_count"] == 0
    run_id = work["run"]["id"]

    empty_submitted = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/runs/{run_id}/candidates",
        headers={"Authorization": "Bearer " + token},
        json={
            "items": [],
            "source_query": "casual woman outdoors",
        },
    )
    assert empty_submitted.status_code == 200
    assert empty_submitted.json()["created"] == 0
    assert empty_submitted.json()["existing"] == 0
    assert empty_submitted.json()["pipeline_count"] == 0

    # A v38 desktop can submit a Pin href without an image after a Pinterest
    # detail timeout. Auto-agent ingestion must treat it as a no-op, not abort
    # the entire run with HTTP 422.
    incomplete = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/runs/{run_id}/candidates",
        headers={"Authorization": "Bearer " + token},
        json={
            "items": [{
                "pin_url": "https://www.pinterest.com/pin/9091/",
                "image_url": "",
                "alt_text": "Pinterest pin link only",
            }],
            "source_query": " ".join(["outdoors"] * 100),
        },
    )
    assert incomplete.status_code == 200
    assert incomplete.json()["created"] == 0
    assert incomplete.json()["pipeline_count"] == 0

    submitted = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/runs/{run_id}/candidates",
        headers={"Authorization": "Bearer " + token},
        json={
            "items": [{
                "pin_url": "https://www.pinterest.com/pin/9090/",
                "image_url": "https://i.pinimg.com/736x/9/0/9.jpg",
                "alt_text": "x" * 2501,
            }],
            "source_query": "casual woman outdoors",
        },
    )
    assert submitted.status_code == 200
    assert submitted.json()["created"] == 1
    assert submitted.json()["progress"] == 0
    assert submitted.json()["pipeline_count"] == 1

    denied = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/runs/{run_id}/candidates",
        headers={"Authorization": "Bearer " + token},
        json={"items": [{
            "pin_url": "https://www.pinterest.com/pin/9999/",
            "image_url": "https://untrusted.example/unsafe.jpg",
        }]},
    )
    assert denied.status_code == 400  # URL allowlist still enforced

    with database() as session:
        candidate = RrugcRepository(session).list_candidates(
            "tenant-a",
            campaign_id,
        )[0]
        assert candidate.ai_signal_json["scout_query"] == "casual woman outdoors"
        assert len(candidate.alt_text or "") == 2000

    finished = api.post(
        f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/runs/{run_id}/complete",
        headers={"Authorization": "Bearer " + token},
        json={"status": "completed"},
    )
    assert finished.status_code == 200
    assert finished.json()["status"] == "completed"

    detailed = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}"
    )
    assert detailed.status_code == 200
    health = {
        row["query"]: row
        for row in detailed.json()["keyword_health"]
    }
    assert health["casual woman outdoors"]["found"] == 1
    assert health["casual woman outdoors"]["new"] == 1
    assert health["casual woman outdoors"]["duplicate"] == 0

    edited = api.patch(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}",
        json={
            "name": "API auto scout edited",
            "search_queries": [
                "woman holding coffee candid",
                "outdoor lifestyle portrait",
            ],
            "target_count": 25,
            "max_scroll_batches": 4,
        },
    )
    assert edited.status_code == 200
    assert edited.json()["name"] == "API auto scout edited"
    assert edited.json()["query"] == "woman holding coffee candid"
    assert edited.json()["search_queries"] == [
        "woman holding coffee candid",
        "outdoor lifestyle portrait",
    ]
    assert edited.json()["target_count"] == 25
    assert edited.json()["max_scroll_batches"] == 4

    paused = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/scout-automation",
        json={"auto_scout": False, "scan_interval_seconds": 180},
    )
    assert paused.status_code == 200
    assert paused.json()["auto_scout"] is False
    assert paused.json()["scan_next_at"] is None

    listed = api.get("/api/v1/realistic-review-ugc/scout-agents")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == agent_id


def test_candidate_ai_feedback_api_persists_human_label(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="manual authenticity feedback",
            query="candid lifestyle photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/515151/",
                image_url="https://i.pinimg.com/736x/5/1/5.jpg",
                alt_text="candid person outdoors",
            )],
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "approved"
        candidate.ai_risk_score = 0.42
        candidate.ai_risk_raw_score = 0.38
        candidate.ai_detector_confidence = 0.81
        candidate.ai_signal_json = {"strong_evidence_count": 1}
        candidate.analyzer_version = "test-analyzer"
        candidate.analyzed_at = datetime.now(timezone.utc)
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    marked = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/ai-feedback",
        json={"label": "ai", "note": "Visible geometry inconsistency."},
    )
    assert marked.status_code == 200
    payload = marked.json()
    assert payload["candidate"]["ai_manual_label"] == "ai"
    assert payload["candidate"]["status"] == "rejected_ai_risk"
    assert payload["candidate"]["reject_reason"] == "MANUAL_AI_LABEL"
    assert payload["calibration"]["ai_count"] == 1
    assert payload["calibration"]["active"] is False

    with database() as session:
        rows = list(session.scalars(
            select(RrugcAiFeedbackModel).where(
                RrugcAiFeedbackModel.candidate_id == candidate_id
            )
        ))
        assert len(rows) == 1
        assert rows[0].label == "ai"
        assert rows[0].ai_risk_raw_score == pytest.approx(0.38)
        assert rows[0].created_by_user_id == "user-a"


def test_candidate_reference_feedback_api_tracks_latest_mark_and_clear(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="manual reference feedback",
            query="phone selfie reference",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/616161/",
                image_url="https://i.pinimg.com/736x/6/1/6.jpg",
                alt_text="casual phone portrait",
            )],
            source_query="phone selfie reference",
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "approved"
        candidate.phone_authenticity_score = 0.91
        candidate.mobile_ugc_score = 0.88
        candidate.product_fit_score = 0.84
        candidate.quality_score = 0.82
        candidate.artistic_editorial_risk = 0.08
        candidate.ai_risk_score = 0.04
        candidate.ai_signal_json = {
            "scout_query": "phone selfie reference",
            "diversity": {
                "scene_type": "home",
                "framing_type": "selfie",
                "camera_angle": "eye_level",
                "pose_type": "casual",
            },
        }
        candidate.analyzed_at = datetime.now(timezone.utc)
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    marked_good = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "good"},
    )
    assert marked_good.status_code == 200
    good_payload = marked_good.json()
    assert good_payload["candidate"]["reference_manual_label"] == "good"
    assert good_payload["learning"]["good_count"] == 1
    assert good_payload["learning"]["bad_count"] == 0
    assert good_payload["learning"]["active"] is False

    marked_bad = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "bad", "note": "Too polished for a useful reference."},
    )
    assert marked_bad.status_code == 200
    bad_payload = marked_bad.json()
    assert bad_payload["candidate"]["reference_manual_label"] == "bad"
    assert bad_payload["learning"]["good_count"] == 0
    assert bad_payload["learning"]["bad_count"] == 1

    cleared = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "clear"},
    )
    assert cleared.status_code == 200
    clear_payload = cleared.json()
    assert clear_payload["candidate"]["reference_manual_label"] is None
    assert clear_payload["learning"]["good_count"] == 0
    assert clear_payload["learning"]["bad_count"] == 0

    with database() as session:
        feedback = list(session.scalars(
            select(RrugcAiFeedbackModel)
            .where(RrugcAiFeedbackModel.candidate_id == candidate_id)
            .order_by(RrugcAiFeedbackModel.created_at.asc())
        ))
        assert [row.label for row in feedback] == ["ref_good", "ref_bad", "ref_clear"]
        assert feedback[0].signal_json["reference_preference_trainable"] is True
        assert feedback[0].signal_json["scout_query"] == "phone selfie reference"
        assert feedback[0].signal_json["learning_intent"] == f"campaign:{campaign_id}"
        assert feedback[0].created_by_user_id == "user-a"
        assert RrugcRepository(session).reference_feedback_training_rows("tenant-a") == []


def test_bad_reference_is_excluded_from_usable_target_and_reopens_completed_scout(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="source row replacement feedback",
            query="casual family cookout",
            target_count=1,
            max_scroll_batches=2,
            auto_import=True,
            auto_scout=True,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/717171/",
                image_url="https://i.pinimg.com/736x/7/1/7.jpg",
                alt_text="casual family cookout",
            )],
            source_query="casual family cookout",
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "drive_ready"
        candidate.analyzed_at = datetime.now(timezone.utc)
        candidate.ai_signal_json = {
            "scout_query": "casual family cookout",
        }
        campaign.status = "completed"
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    marked_bad = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "bad", "note": "Not usable for this source row."},
    )
    assert marked_bad.status_code == 200
    assert marked_bad.json()["candidate"]["reference_manual_label"] == "bad"
    assert marked_bad.json()["candidate"]["status"] == "drive_ready"

    with database() as session:
        repository = RrugcRepository(session)
        raw_counts = repository.campaign_counts("tenant-a", campaign_id)
        usable_counts = repository.campaign_usable_counts("tenant-a", campaign_id)
        campaign = repository.get_campaign("tenant-a", campaign_id)
        assert raw_counts["drive_ready"] == 1
        assert usable_counts["drive_ready"] == 0
        assert campaign is not None
        assert campaign.status == "running"
        assert campaign.scan_next_at is not None

    cleared = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "clear"},
    )
    assert cleared.status_code == 200

    with database() as session:
        repository = RrugcRepository(session)
        campaign = repository.get_campaign("tenant-a", campaign_id)
        assert repository.campaign_usable_counts("tenant-a", campaign_id)["drive_ready"] == 1
        assert campaign is not None
        assert campaign.status == "completed"





def test_ai_reference_feedback_trains_negative_and_ai_detector_and_excludes_ref(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="manual ai reference feedback",
            query="realistic candid person",
            target_count=1,
            max_scroll_batches=2,
            auto_import=True,
            auto_scout=True,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/manual-ai-ref/",
                image_url="https://i.pinimg.com/736x/manual-ai-ref.jpg",
                alt_text="synthetic looking portrait",
            )],
            source_query=campaign.query,
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "drive_ready"
        candidate.analyzed_at = datetime.now(timezone.utc)
        candidate.ai_signal_json = {"scout_query": campaign.query}
        campaign.status = "completed"
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    marked_ai = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "ai", "note": "Clearly AI-generated; never use as a real ref."},
    )
    assert marked_ai.status_code == 200
    payload = marked_ai.json()["candidate"]
    assert payload["reference_manual_label"] == "ai"
    assert payload["ai_manual_label"] == "ai"
    assert payload["status"] == "drive_ready"

    with database() as session:
        repository = RrugcRepository(session)
        assert repository.campaign_counts("tenant-a", campaign_id)["drive_ready"] == 1
        assert repository.campaign_usable_counts("tenant-a", campaign_id)["drive_ready"] == 0
        campaign = repository.get_campaign("tenant-a", campaign_id)
        assert campaign is not None
        assert campaign.status == "running"
        feedback = list(session.scalars(
            select(RrugcAiFeedbackModel)
            .where(RrugcAiFeedbackModel.candidate_id == candidate_id)
            .order_by(RrugcAiFeedbackModel.created_at.asc(), RrugcAiFeedbackModel.id.asc())
        ))
        feedback_by_label = {row.label: row for row in feedback}
        assert set(feedback_by_label) == {"ref_bad", "ai"}
        assert feedback_by_label["ref_bad"].signal_json["reference_feedback_kind"] == "ai"
        assert feedback_by_label["ai"].signal_json["feedback_source"] == "reference_review_ai_tag"


def test_reference_feedback_learning_is_profile_scoped(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="profile scoped feedback",
            query="candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/profile-feedback/",
                image_url="https://i.pinimg.com/736x/profile-feedback.jpg",
            )],
            source_query=campaign.query,
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "approved"
        candidate.phone_authenticity_score = 0.92
        candidate.mobile_ugc_score = 0.90
        candidate.product_fit_score = 0.86
        candidate.quality_score = 0.84
        candidate.artistic_editorial_risk = 0.04
        candidate.ai_risk_score = 0.03
        candidate.ai_signal_json = {"scout_query": campaign.query}
        candidate.analyzed_at = datetime.now(timezone.utc)
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    default_good = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "good"},
    )
    assert default_good.status_code == 200
    assert default_good.json()["learning"]["good_count"] == 1
    assert default_good.json()["learning"]["bad_count"] == 0

    detail_bad = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={
            "label": "bad",
            "profile_key": "embroidery-detail",
            "note": "Good person ref, wrong embroidery-detail ref.",
        },
    )
    assert detail_bad.status_code == 200
    assert detail_bad.json()["learning"]["good_count"] == 0
    assert detail_bad.json()["learning"]["bad_count"] == 1

    with database() as session:
        repository = RrugcRepository(session)
        default_rows = repository.reference_feedback_training_rows(
            "tenant-a",
            intent=f"campaign:{campaign_id}",
            legacy_campaign_id=campaign_id,
            profile_key="realistic-person-ugc",
        )
        detail_rows = repository.reference_feedback_training_rows(
            "tenant-a",
            intent=f"campaign:{campaign_id}",
            legacy_campaign_id=campaign_id,
            profile_key="embroidery-detail",
        )
        assert [label for label, _ in default_rows] == ["ref_good"]
        assert [label for label, _ in detail_rows] == ["ref_bad"]
        assert default_rows[0][1]["reference_profile_key"] == "realistic-person-ugc"
        assert detail_rows[0][1]["reference_profile_key"] == "embroidery-detail"


def test_reference_good_approves_immediately_and_requeues_failed_analysis(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="failed ref recovery",
            query="woman wearing cap candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/ref-failed-requeue/",
                image_url="https://i.pinimg.com/736x/ref-failed-requeue.jpg",
            )],
            source_query=campaign.query,
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "analysis_failed"
        candidate.last_error_code = "provider_timeout"
        candidate.analyzed_at = None
        original_revision = candidate.analysis_revision
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "good"},
    )
    assert response.status_code == 200
    payload = response.json()["candidate"]
    assert payload["reference_manual_label"] == "good"
    assert payload["status"] == "approved"

    with database() as session:
        candidate = RrugcRepository(session).get_candidate(
            "tenant-a", campaign_id, candidate_id
        )
        assert candidate is not None
        assert candidate.analysis_revision == original_revision + 1
        assert candidate.status == "approved"
        assert candidate.last_error_code is None
        assert candidate.ai_signal_json["reference_manual_pending_analysis"] is True
        assert candidate.ai_signal_json["reference_manual_approval_override"] is True
        jobs = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == candidate_id,
            )
        ))
        assert any(
            job.payload_json.get("analysis_revision") == candidate.analysis_revision
            for job in jobs
        )


def test_reference_good_requeues_local_prefilter_for_gemini_enrichment(database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="local prefilter override",
            query="woman wearing cap candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/local-prefilter-override/",
                image_url="https://i.pinimg.com/736x/local-prefilter-override.jpg",
            )],
            source_query=campaign.query,
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "rejected_context"
        candidate.reject_reason = "SEED_VISUAL_HIGH_CONFIDENCE_NEGATIVE"
        candidate.analyzed_at = datetime.now(timezone.utc)
        candidate.analyzer_provider = "local_vps"
        candidate.ai_signal_json = {
            "local_prefilter": {
                "gate": "seed_visual_high_confidence_negative",
                "provider_call_skipped": True,
            }
        }
        original_revision = candidate.analysis_revision
        session.commit()

        row = RrugcService(session).mark_candidate_reference_label(
            candidate,
            label="good",
            note="human override",
            user_id="user-a",
        )
        assert row.status == "approved"
        assert row.analysis_revision == original_revision + 1
        assert row.ai_signal_json["reference_manual_pending_analysis"] is True
        jobs = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == candidate.id,
            )
        ))
        assert any(
            job.payload_json.get("analysis_revision") == row.analysis_revision
            for job in jobs
        )


def test_reference_good_stays_approved_when_background_analysis_fails(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="manual ref background failure",
            query="woman wearing cap candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/ref-background-fail/",
                image_url="https://i.pinimg.com/736x/ref-background-fail.jpg",
            )],
            source_query=campaign.query,
        )
        assert created == 1
        candidate = rows[0]
        candidate.status = "analysis_failed"
        candidate.analyzed_at = None
        session.commit()
        campaign_id = campaign.id
        candidate_id = candidate.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-feedback",
        json={"label": "good"},
    )
    assert response.status_code == 200
    assert response.json()["candidate"]["status"] == "approved"

    with database() as session:
        candidate = RrugcRepository(session).get_candidate(
            "tenant-a", campaign_id, candidate_id
        )
        assert candidate is not None
        revision = candidate.analysis_revision
        job = session.scalar(
            select(ProcessingJobModel)
            .where(
                ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                ProcessingJobModel.entity_id == candidate_id,
            )
            .order_by(ProcessingJobModel.created_at.desc())
        )
        assert job is not None
        claimed = ClaimedJob(
            id=job.id,
            tenant_id=job.tenant_id,
            job_type=job.job_type,
            entity_type=job.entity_type,
            entity_id=job.entity_id,
            payload=job.payload_json,
            attempt_count=job.attempt_count,
            lease_owner="test-worker",
            provider_key=job.provider_key,
        )

    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=database,
            storage_provider=FakeStorage(),
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("rrugc-ref-fail-test"), {}),
    )
    RrugcCandidateAnalyzeJobHandler._mark_error(
        context,
        "gemini_model_pool_temporarily_unavailable",
        terminal=False,
    )

    with database() as session:
        candidate = RrugcRepository(session).get_candidate(
            "tenant-a", campaign_id, candidate_id
        )
        assert candidate is not None
        assert candidate.analysis_revision == revision
        assert candidate.status == "approved"
        assert (
            candidate.last_error_code
            == "gemini_model_pool_temporarily_unavailable"
        )
        assert candidate.ai_signal_json["reference_manual_pending_analysis"] is True



def test_gemini_pool_unavailable_is_deferred_even_without_provider_retry_timestamp():
    now = datetime(2026, 10, 3, 5, 30, tzinfo=timezone.utc)
    error = AiProviderError(
        "No Gemini model is currently available.",
        code="gemini_model_pool_temporarily_unavailable",
        retryable=True,
    )

    result = deferred_rrugc_ai_retry(
        error,
        message="Embroidery context analyzer is temporarily unavailable.",
        now=now,
    )

    assert isinstance(result, DeferredJobOutcome)
    assert result.reason_code == "gemini_model_pool_temporarily_unavailable"
    assert result.retry_at == now + timedelta(seconds=60)


def test_generic_retryable_ai_error_without_retry_timestamp_uses_normal_attempt_policy():
    error = AiProviderError(
        "Transient provider error.",
        code="ai_provider_transient_error",
        retryable=True,
    )

    assert (
        deferred_rrugc_ai_retry(
            error,
            message="AI provider is temporarily unavailable.",
        )
        is None
    )


def test_reference_good_is_authoritative_for_soft_and_ai_rejections(api, database):
    with database() as session:
        service = RrugcService(session)
        campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="manual ref approval",
            query="woman wearing cap candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        rows, created, _ = service.ingest_candidates(
            campaign=campaign,
            submissions=[
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/ref-soft-reject/",
                    image_url="https://i.pinimg.com/736x/ref-soft-reject.jpg",
                ),
                CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/ref-ai-reject/",
                    image_url="https://i.pinimg.com/736x/ref-ai-reject.jpg",
                ),
            ],
            source_query=campaign.query,
        )
        assert created == 2
        soft, hard = rows
        now = datetime.now(timezone.utc)

        soft.status = "rejected_context"
        soft.reject_reason = "UGC_SCORE_LOW"
        soft.analyzed_at = now
        soft.phone_authenticity_score = 0.8
        soft.mobile_ugc_score = 0.5
        soft.quality_score = 0.8
        soft.product_fit_score = 0.8
        soft.ai_risk_score = 0.05

        hard.status = "rejected_ai_risk"
        hard.reject_reason = "AI_RISK_CONFIRMED"
        hard.analyzed_at = now
        hard.phone_authenticity_score = 0.8
        hard.mobile_ugc_score = 0.8
        hard.quality_score = 0.8
        hard.product_fit_score = 0.8
        hard.ai_risk_score = 0.95
        session.commit()
        campaign_id = campaign.id
        soft_id = soft.id
        hard_id = hard.id

    soft_response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{soft_id}/reference-feedback",
        json={"label": "good"},
    )
    assert soft_response.status_code == 200
    soft_payload = soft_response.json()["candidate"]
    assert soft_payload["status"] == "approved"
    assert soft_payload["reject_reason"] is None

    hard_response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{hard_id}/reference-feedback",
        json={"label": "good"},
    )
    assert hard_response.status_code == 200
    hard_payload = hard_response.json()["candidate"]
    assert hard_payload["status"] == "approved"
    assert hard_payload["reject_reason"] is None

    with database() as session:
        soft = RrugcRepository(session).get_candidate("tenant-a", campaign_id, soft_id)
        hard = RrugcRepository(session).get_candidate("tenant-a", campaign_id, hard_id)
        assert soft.ai_signal_json["reference_manual_approval_override"] is True
        assert soft.ai_signal_json["reference_manual_auto_status"] == "rejected_context"
        assert hard.ai_signal_json["reference_manual_approval_override"] is True
        assert hard.ai_signal_json["reference_manual_auto_status"] == "rejected_ai_risk"
        assert hard.ai_signal_json["reference_manual_auto_reject_reason"] == "AI_RISK_CONFIRMED"


def test_reference_good_override_policy_treats_human_ref_as_authoritative():
    assert reference_manual_good_can_override(
        "rejected_context",
        manual_ai_label=None,
    ) is True
    assert reference_manual_good_can_override(
        "rejected_quality",
        manual_ai_label="real",
    ) is True
    assert reference_manual_good_can_override(
        "rejected_ai_risk",
        manual_ai_label=None,
    ) is True
    assert reference_manual_good_can_override(
        "rejected_duplicate",
        manual_ai_label=None,
    ) is True
    assert reference_manual_good_can_override(
        "rejected_context",
        manual_ai_label="ai",
    ) is True


def test_reference_good_wins_over_background_duplicate_and_diversity_rejection():
    duplicate = reference_qualification_resolution(
        manual_reference_good=True,
        near_duplicate=True,
        diversity_redundant=False,
        decision_status="approved",
        decision_reject_reason=None,
    )
    assert duplicate == (
        "approved",
        None,
        "rejected_duplicate",
        "visual_near_duplicate",
    )

    diversity = reference_qualification_resolution(
        manual_reference_good=True,
        near_duplicate=False,
        diversity_redundant=True,
        decision_status="approved",
        decision_reject_reason=None,
    )
    assert diversity == (
        "approved",
        None,
        "rejected_context",
        "DIVERSITY_REDUNDANT",
    )


def test_reference_learning_scope_isolates_hat_feedback(database):
    with database() as session:
        service = RrugcService(session)
        hat_campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="People wearing hats",
            query="woman wearing hat candid phone photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        other_campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Tote references",
            query="casual tote lifestyle",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )

        marked = []
        for campaign, suffix in ((hat_campaign, "hat"), (other_campaign, "tote")):
            rows, created, _ = service.ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url=f"https://www.pinterest.com/pin/scope-{suffix}/",
                    image_url=f"https://i.pinimg.com/736x/scope-{suffix}.jpg",
                )],
                source_query=campaign.query,
            )
            assert created == 1
            candidate = rows[0]
            candidate.status = "approved"
            candidate.analyzed_at = datetime.now(timezone.utc)
            candidate.phone_authenticity_score = 0.9
            candidate.mobile_ugc_score = 0.9
            candidate.product_fit_score = 0.8
            candidate.quality_score = 0.8
            candidate.artistic_editorial_risk = 0.1
            candidate.ai_risk_score = 0.05
            service.mark_candidate_reference_label(
                candidate,
                label="good",
                note=None,
                user_id="user-a",
            )
            marked.append(candidate)

        repository = RrugcRepository(session)
        all_rows = repository.reference_feedback_training_rows("tenant-a")
        hat_rows = repository.reference_feedback_training_rows(
            "tenant-a",
            intent="hat_people",
        )
        other_rows = repository.reference_feedback_training_rows(
            "tenant-a",
            intent=f"campaign:{other_campaign.id}",
        )
        assert len(all_rows) == 2
        assert len(hat_rows) == 1
        assert len(other_rows) == 1
        assert hat_rows[0][1]["learning_intent"] == "hat_people"
        assert other_rows[0][1]["learning_intent"] == f"campaign:{other_campaign.id}"


def test_reference_preference_model_learns_human_selection_direction():
    good = reference_preference_features(
        phone_authenticity_score=0.92,
        mobile_ugc_score=0.90,
        product_fit_score=0.84,
        quality_score=0.82,
        artistic_editorial_risk=0.08,
        ai_risk_score=0.04,
    )
    bad = reference_preference_features(
        phone_authenticity_score=0.30,
        mobile_ugc_score=0.28,
        product_fit_score=0.45,
        quality_score=0.65,
        artistic_editorial_risk=0.86,
        ai_risk_score=0.22,
    )
    model = build_reference_preference_model(
        [("ref_good", {"reference_preference_features": good}) for _ in range(3)]
        + [("ref_bad", {"reference_preference_features": bad}) for _ in range(3)]
    )
    assert model.active is True
    assert model.good_count == 3
    assert model.bad_count == 3
    assert reference_preference_adjustment(good, model) > 0
    assert reference_preference_adjustment(bad, model) < 0


def test_hat_keyword_strategy_detects_vietnamese_and_builds_balanced_personas():
    assert detect_campaign_keyword_intent(
        name="Người đội mũ",
        queries=["ảnh người đội mũ tự nhiên"],
    ) == "hat_people"

    queries = build_campaign_search_queries(
        name="Người đội mũ",
        queries=["ảnh người đội mũ tự nhiên"],
    )
    assert len(queries) == 10
    combined = " | ".join(queries)
    assert "man wearing" in combined
    assert "woman wearing" in combined
    assert "couple wearing" in combined
    assert "family wearing" in combined
    assert "friends wearing" in combined
    assert all(not query_is_suppressed_for_reference_search(query) for query in queries)


def test_hat_keyword_strategy_suppresses_editorial_and_respects_explicit_persona():
    queries = build_campaign_search_queries(
        name="Couple đội mũ",
        queries=[
            "couple hats editorial fashion shoot",
            "couple wearing caps candid phone photo",
        ],
    )
    assert queries
    assert len(queries) <= 10
    assert all("couple" in query.casefold() for query in queries)
    assert not any("editorial" in query.casefold() for query in queries)
    assert not any("fashion shoot" in query.casefold() for query in queries)
    assert "couple wearing caps candid phone photo" in queries


def test_create_hat_campaign_auto_expands_search_queries(database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Hat people references",
            query="people wearing hats",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        assert campaign.search_query_anchors_json == ["people wearing hats"]
        assert len(campaign.search_queries_json or []) == 10
        combined = " | ".join(campaign.search_queries_json or [])
        assert "man wearing" in combined
        assert "woman wearing" in combined
        assert "couple wearing" in combined
        assert "family wearing" in combined


def test_update_hat_campaign_preserves_manual_anchors_separately(database):
    with database() as session:
        service = RrugcService(session)
        campaign, _ = service.create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Hat people references",
            query="people wearing hats",
            target_count=10,
            max_scroll_batches=2,
            auto_import=False,
        )
        updated = service.update_campaign(
            campaign,
            search_queries=["family wearing hats candid phone photo"],
        )
        assert updated.search_query_anchors_json == [
            "family wearing hats candid phone photo"
        ]
        assert "family wearing hats candid phone photo" in (
            updated.search_queries_json or []
        )
        assert len(updated.search_queries_json or []) > 1


def test_hat_keyword_pool_keeps_bad_query_as_exploration_reserve():
    baseline = build_campaign_search_queries(
        name="People wearing hats",
        queries=["people wearing hats"],
    )
    bad_query = baseline[0]
    refreshed = build_campaign_search_queries(
        name="People wearing hats",
        queries=baseline,
        outcomes=[(bad_query, "approved", "bad") for _ in range(6)],
    )
    assert len(refreshed) == 10
    assert bad_query in refreshed
    assert refreshed[-1] == bad_query
    assert refreshed != baseline


def test_hat_keyword_pool_keeps_manual_anchor_even_with_bad_feedback():
    anchor_query = "people wearing hats"
    refreshed = build_campaign_search_queries(
        name="People wearing hats",
        queries=[anchor_query],
        protected_queries=[anchor_query],
        outcomes=[(anchor_query, "approved", "bad") for _ in range(8)],
    )
    assert len(refreshed) == 10
    assert anchor_query in refreshed


def test_keyword_lifecycle_suppresses_only_after_enough_reference_evidence():
    query = "woman wearing bucket hat candid phone photo"
    insufficient = keyword_health_rows(
        [query],
        [],
        [(query, "rejected_context", "bad") for _ in range(2)],
    )
    assert insufficient[0]["state"] == "explore"

    enough = keyword_health_rows(
        [query],
        [],
        [(query, "rejected_context", "bad") for _ in range(3)],
    )
    assert enough[0]["state"] == "suppressed"


def test_keyword_lifecycle_never_suppresses_manual_anchor():
    query = "people wearing hats"
    health = keyword_health_rows(
        [query],
        [],
        [(query, "rejected_context", "bad") for _ in range(8)],
        protected_queries=[query],
    )
    assert health[0]["state"] == "protected"
    assert health[0]["protected"] is True


def test_adaptive_keyword_exploration_retries_suppressed_keyword(monkeypatch):
    good = "phone selfie"
    bad = "editorial portrait"
    outcomes = (
        [(good, "approved", "good") for _ in range(6)]
        + [(bad, "rejected_context", "bad") for _ in range(6)]
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.0,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.shuffle",
        lambda rows: None,
    )
    ranked = adaptive_search_queries(
        [good, bad],
        [],
        outcomes,
    )
    assert ranked[0] == bad


def test_adaptive_keyword_ranking_prefers_human_approved_reference_yield(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.9,
    )
    outcomes = (
        [("phone selfie", "approved", "good") for _ in range(6)]
        + [("editorial portrait", "approved", "bad") for _ in range(6)]
    )
    ranked = adaptive_search_queries(
        ["editorial portrait", "phone selfie"],
        [],
        outcomes,
    )
    assert ranked == ["phone selfie"]


def test_adaptive_keyword_ranking_penalizes_repeated_zero_new_scan_failures(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.9,
    )
    bad = "fragile pinterest query"
    good = "stable pinterest query"
    runs = [
        SimpleNamespace(
            status="completed",
            query=bad,
            keyword_stats_json=None,
            submitted_count=10,
            created_count=5,
            existing_count=5,
            last_error_code=None,
        ),
        SimpleNamespace(
            status="completed",
            query=good,
            keyword_stats_json=None,
            submitted_count=10,
            created_count=5,
            existing_count=5,
            last_error_code=None,
        ),
        *[
            SimpleNamespace(
                status="failed",
                query=bad,
                keyword_stats_json={
                    "_scout_runtime": {
                        "client_version": "rrugc-scout-v36",
                        "machine_label": "test-v36",
                    }
                },
                submitted_count=0,
                created_count=0,
                existing_count=0,
                last_error_code="pinterest_scan_failed",
            )
            for _ in range(3)
        ],
    ]

    ranked = adaptive_search_queries([bad, good], runs, [])
    assert ranked[0] == good

    health = {
        row["query"]: row
        for row in keyword_health_rows([bad, good], runs, [])
    }
    assert health[bad]["failed_scans"] == 3
    assert health[bad]["failure_rate"] == pytest.approx(0.75)
    assert health[good]["failed_scans"] == 0
    assert health[good]["failure_rate"] == 0.0


def test_adaptive_keyword_failure_penalty_trusts_v36_more_than_legacy(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.9,
    )
    advantaged = "advantaged discovery query"
    stable = "stable discovery query"

    def ranked_for(version: str) -> list[str]:
        runs = [
            SimpleNamespace(
                status="completed",
                query=advantaged,
                keyword_stats_json=None,
                submitted_count=10,
                created_count=6,
                existing_count=4,
                last_error_code=None,
            ),
            SimpleNamespace(
                status="completed",
                query=stable,
                keyword_stats_json=None,
                submitted_count=10,
                created_count=5,
                existing_count=5,
                last_error_code=None,
            ),
            *[
                SimpleNamespace(
                    status="failed",
                    query=advantaged,
                    keyword_stats_json={
                        "_scout_runtime": {
                            "client_version": version,
                            "machine_label": "test-machine",
                        }
                    },
                    submitted_count=0,
                    created_count=0,
                    existing_count=0,
                    last_error_code="pinterest_scan_failed",
                )
                for _ in range(3)
            ],
        ]
        return adaptive_search_queries([advantaged, stable], runs, [])

    assert ranked_for("rrugc-scout-v34")[0] == advantaged
    assert ranked_for("rrugc-scout-v36")[0] == stable


def test_keyword_health_counts_metadata_only_run_via_query_fallback():
    query = "metadata-only query"
    runs = [
        SimpleNamespace(
            status="completed",
            query=query,
            keyword_stats_json={
                "_scout_runtime": {
                    "client_version": "rrugc-scout-v36",
                    "machine_label": "test-machine",
                }
            },
            submitted_count=0,
            created_count=0,
            existing_count=0,
            last_error_code=None,
        )
    ]
    health = keyword_health_rows([query], runs, [])
    assert health[0]["scans"] == 1
    assert health[0]["found"] == 0
    assert health[0]["new"] == 0


def test_adaptive_keyword_ranking_does_not_penalize_cam_http_failures(monkeypatch):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.scout_automation.random.random",
        lambda: 0.9,
    )
    first = "query with old submit bug"
    second = "comparison query"
    runs = [
        SimpleNamespace(
            status="completed",
            query=first,
            keyword_stats_json=None,
            submitted_count=10,
            created_count=5,
            existing_count=5,
            last_error_code=None,
        ),
        SimpleNamespace(
            status="completed",
            query=second,
            keyword_stats_json=None,
            submitted_count=10,
            created_count=5,
            existing_count=5,
            last_error_code=None,
        ),
        *[
            SimpleNamespace(
                status="failed",
                query=first,
                keyword_stats_json=None,
                submitted_count=0,
                created_count=0,
                existing_count=0,
                last_error_code="cam_http_422",
            )
            for _ in range(3)
        ],
    ]

    ranked = adaptive_search_queries([first, second], runs, [])
    assert ranked == [first, second]

    health = {
        row["query"]: row
        for row in keyword_health_rows([first, second], runs, [])
    }
    assert health[first]["failed_scans"] == 0
    assert health[first]["failure_rate"] == 0.0


def reference_document(**overrides) -> ReferenceAnalysisDocument:
    values = {
        "people_count": 1,
        "primary_head_ratio": 0.31,
        "smile_score": 0.82,
        "head_visible": True,
        "existing_headwear": False,
        "head_occlusion": 0.08,
        "mobile_ugc_score": 0.81,
        "phone_authenticity_score": 0.78,
        "artistic_editorial_risk": 0.12,
        "quality_score": 0.84,
        "ai_risk_score": 0.08,
        "product_fit_score": 0.91,
        "summary": "Casual visible portrait with a clear head.",
    }
    values.update(overrides)
    return ReferenceAnalysisDocument(**values)


def test_reference_policy_approves_good_hat_reference():
    decision = evaluate_reference(reference_document(), ReferenceFilterPolicy())
    assert decision.status == "approved"
    assert decision.reject_reason is None
    assert decision.final_score > 0.75


def test_variant_matching_prioritizes_correct_existing_hat_but_keeps_mismatch_reviewable():
    mismatch = evaluate_reference(
        reference_document(
            existing_headwear=True,
            matched_variant_id=None,
            color_match_score=0.15,
            product_shape_score=0.80,
        ),
        ReferenceFilterPolicy(),
        variant_matching_required=True,
    )
    assert mismatch.status == "needs_review"
    assert mismatch.reject_reason == "PRODUCT_VARIANT_MISMATCH"

    matching = evaluate_reference(
        reference_document(
            existing_headwear=True,
            matched_variant_id="variant-navy",
            matched_variant_name="Natural/ Navy",
            matched_color="Natural/ Navy",
            color_match_score=0.92,
            product_shape_score=0.88,
        ),
        ReferenceFilterPolicy(),
        variant_matching_required=True,
    )
    assert matching.status == "approved"
    assert matching.final_score > mismatch.final_score

    bare_head = evaluate_reference(
        reference_document(
            existing_headwear=False,
            matched_variant_id=None,
            color_match_score=0.0,
            product_shape_score=0.75,
        ),
        ReferenceFilterPolicy(),
        variant_matching_required=True,
    )
    assert bare_head.status == "approved"


@pytest.mark.parametrize(
    "overrides",
    [
        {
            "primary_head_ratio": 0.65,
            "smile_score": 0.0,
            "existing_headwear": True,
            "head_occlusion": 0.25,
            "mobile_ugc_score": 0.95,
            "quality_score": 0.85,
            "product_fit_score": 0.90,
        },
        {
            "primary_head_ratio": 0.42,
            "smile_score": 0.0,
            "existing_headwear": True,
            "head_occlusion": 0.45,
            "mobile_ugc_score": 0.95,
            "quality_score": 0.82,
            "product_fit_score": 0.75,
        },
        {
            "primary_head_ratio": 0.28,
            "smile_score": 0.0,
            "existing_headwear": True,
            "head_occlusion": 0.40,
            "mobile_ugc_score": 0.75,
            "quality_score": 0.85,
            "product_fit_score": 0.60,
        },
        {
            "primary_head_ratio": 0.20,
            "smile_score": 0.10,
            "existing_headwear": True,
            "head_occlusion": 0.55,
            "mobile_ugc_score": 0.90,
            "quality_score": 0.75,
            "product_fit_score": 0.60,
        },
    ],
)
def test_reference_policy_accepts_cap_friendly_candid_examples(overrides):
    decision = evaluate_reference(
        reference_document(**overrides),
        ReferenceFilterPolicy(),
    )
    assert decision.status == "approved"
    assert decision.reject_reason is None


def test_reference_policy_prefers_smartphone_style_over_editorial_polish():
    phone_photo = reference_document(
        mobile_ugc_score=0.88,
        phone_authenticity_score=0.92,
        artistic_editorial_risk=0.08,
        quality_score=0.72,
        product_fit_score=0.82,
    )
    editorial_photo = reference_document(
        mobile_ugc_score=0.80,
        phone_authenticity_score=0.30,
        artistic_editorial_risk=0.84,
        quality_score=0.95,
        product_fit_score=0.82,
    )

    phone_decision = evaluate_reference(phone_photo, ReferenceFilterPolicy())
    editorial_decision = evaluate_reference(editorial_photo, ReferenceFilterPolicy())

    assert phone_decision.status == "approved"
    assert editorial_decision.status == "rejected_context"
    assert editorial_decision.reject_reason == "PHONE_AUTHENTICITY_LOW"
    assert phone_decision.final_score > editorial_decision.final_score


def test_reference_policy_rejects_strong_editorial_risk_even_when_phone_score_is_moderate():
    decision = evaluate_reference(
        reference_document(
            mobile_ugc_score=0.75,
            phone_authenticity_score=0.55,
            artistic_editorial_risk=0.82,
            quality_score=0.90,
            product_fit_score=0.80,
        ),
        ReferenceFilterPolicy(),
    )
    assert decision.status == "rejected_context"
    assert decision.reject_reason == "ARTISTIC_EDITORIAL_HIGH"


def test_reference_policy_allows_existing_headwear_by_default():
    decision = evaluate_reference(
        reference_document(existing_headwear=True),
        ReferenceFilterPolicy(),
    )
    assert decision.status == "approved"
    assert decision.reject_reason is None


def test_reference_policy_can_still_reject_existing_headwear():
    decision = evaluate_reference(
        reference_document(existing_headwear=True),
        ReferenceFilterPolicy(reject_headwear=True),
    )
    assert decision.status == "rejected_existing_headwear"
    assert decision.reject_reason == "EXISTING_HEADWEAR"


@pytest.mark.parametrize(
    ("overrides", "status", "reason"),
    [
        ({"people_count": 0, "primary_head_ratio": None}, "rejected_no_person", "NO_PERSON"),
        ({"people_count": 7}, "rejected_context", "TOO_MANY_PEOPLE"),
        ({"primary_head_ratio": 0.12}, "rejected_head_ratio", "HEAD_RATIO_OUT_OF_RANGE"),
        ({"head_occlusion": 0.75}, "rejected_head_occlusion", "HEAD_OCCLUSION"),
        ({"quality_score": 0.59}, "rejected_quality", "QUALITY_SCORE_LOW"),
        ({"mobile_ugc_score": 0.54}, "rejected_context", "UGC_SCORE_LOW"),
        ({"ai_risk_score": 0.16}, "rejected_ai_risk", "AI_RISK_HIGH"),
    ],
)
def test_reference_policy_rejects_failed_constraints(overrides, status, reason):
    decision = evaluate_reference(reference_document(**overrides), ReferenceFilterPolicy())
    assert decision.status == status
    assert decision.reject_reason == reason


def test_ai_risk_requires_corroborated_evidence_in_ensemble_mode():
    document = reference_document(
        ai_risk_score=0.92,
        ai_detector_confidence=0.45,
        ai_anatomy_risk=0.10,
        ai_text_symbol_risk=0.05,
        ai_geometry_risk=0.08,
        ai_texture_risk=0.12,
        ai_lighting_reflection_risk=0.08,
        ai_background_consistency_risk=0.06,
    )
    assessment = assess_ai_risk(document)
    decision = evaluate_reference(
        document,
        ReferenceFilterPolicy(),
        effective_ai_risk_score=assessment.calibrated_score,
        ai_evidence_count=assessment.strong_evidence_count,
        ai_detector_confidence=assessment.detector_confidence,
        ai_risk_confirmed=assessment.confirmed,
    )
    assert assessment.strong_evidence_count == 0
    assert decision.status == "approved"


def test_ai_risk_second_pass_can_confirm_synthetic_image():
    document = reference_document(
        ai_risk_score=0.72,
        ai_detector_confidence=0.82,
        ai_anatomy_risk=0.78,
        ai_geometry_risk=0.71,
        ai_texture_risk=0.66,
    )
    confirmation = AiAuthenticityConfirmationDocument(
        camera_photo_probability=0.08,
        synthetic_probability=0.92,
        confidence=0.91,
        anatomy_risk=0.86,
        text_symbol_risk=0.44,
        geometry_risk=0.82,
        texture_risk=0.76,
        lighting_reflection_risk=0.64,
        background_consistency_risk=0.70,
        evidence=["Malformed fingers", "Inconsistent background geometry"],
        summary="Multiple independent synthetic inconsistencies are visible.",
    )
    assessment = assess_ai_risk(document, confirmation=confirmation)
    decision = evaluate_reference(
        document,
        ReferenceFilterPolicy(),
        effective_ai_risk_score=assessment.calibrated_score,
        ai_evidence_count=assessment.strong_evidence_count,
        ai_detector_confidence=assessment.detector_confidence,
        ai_risk_confirmed=assessment.confirmed,
    )
    assert assessment.confirmed is True
    assert assessment.strong_evidence_count >= 1
    assert decision.status == "rejected_ai_risk"
    assert decision.reject_reason == "AI_RISK_CONFIRMED"


def test_ai_risk_calibration_activates_after_human_feedback():
    calibration = build_ai_risk_calibration(
        [("real", value) for value in (0.08, 0.10, 0.12, 0.09, 0.11)]
        + [("ai", value) for value in (0.72, 0.76, 0.80, 0.78, 0.74)]
    )
    calibrated_real, real_applied = calibrate_ai_risk(0.10, calibration)
    calibrated_ai, ai_applied = calibrate_ai_risk(0.76, calibration)
    assert calibration.active is True
    assert real_applied is True
    assert ai_applied is True
    assert calibrated_real <= 0.10
    assert calibrated_ai >= 0.90


def test_manual_ai_label_overrides_authenticity_only():
    document = reference_document(ai_risk_score=0.95)
    real_decision = evaluate_reference(
        document,
        ReferenceFilterPolicy(),
        effective_ai_risk_score=0.95,
        ai_evidence_count=3,
        ai_detector_confidence=0.95,
        ai_risk_confirmed=True,
        manual_ai_label="real",
    )
    ai_decision = evaluate_reference(
        document,
        ReferenceFilterPolicy(),
        effective_ai_risk_score=0.01,
        ai_evidence_count=0,
        ai_detector_confidence=0.10,
        ai_risk_confirmed=False,
        manual_ai_label="ai",
    )
    assert real_decision.status == "approved"
    assert ai_decision.status == "rejected_ai_risk"
    assert ai_decision.reject_reason == "MANUAL_AI_LABEL"



def supervisor_document(**overrides) -> SupervisorAnalysisDocument:
    values = {
        "product_visual_similarity": 0.92,
        "product_color_similarity": 0.94,
        "logo_fidelity": 0.90,
        "placement_score": 0.91,
        "scale_score": 0.90,
        "person_scene_preservation": 0.96,
        "photorealism_score": 0.91,
        "artifact_risk": 0.07,
        "hat_head_width_ratio": 1.05,
        "summary": "Generated hat matches the product references and preserves the visible scene.",
    }
    values.update(overrides)
    return SupervisorAnalysisDocument(**values)


def test_supervisor_policy_passes_high_fidelity_generation():
    decision = evaluate_supervisor(supervisor_document())
    assert decision.status == "pass"
    assert decision.reason is None
    assert decision.correction is None


@pytest.mark.parametrize(
    ("overrides", "reason", "correction_kind"),
    [
        ({"person_scene_preservation": 0.5}, "PERSON_SCENE_CHANGED", "preserve_person_scene"),
        ({"product_visual_similarity": 0.5}, "PRODUCT_VISUAL_MISMATCH", "increase_product_fidelity"),
        ({"hat_head_width_ratio": 1.4}, "HAT_TOO_LARGE", "scale_product"),
        ({"artifact_risk": 0.7}, "ARTIFACT_RISK_HIGH", "remove_generation_artifacts"),
    ],
)
def test_supervisor_policy_returns_structured_correction(
    overrides, reason, correction_kind
):
    decision = evaluate_supervisor(supervisor_document(**overrides))
    assert decision.status == "fail"
    assert decision.reason == reason
    assert decision.correction is not None
    assert decision.correction["kind"] == correction_kind




def test_supervisor_prepare_correction_is_idempotent_and_preserves_frozen_provenance(database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Supervisor correction",
            query="casual review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-QA",
            name="QA cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="7" * 64,
            pin_url="https://www.pinterest.com/pin/7001/",
            image_url="https://i.pinimg.com/qa-source.jpg",
            status="drive_ready",
            remote_file_id="person-qa-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=2,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "revision": 2,
            },
            product_reference_snapshot_json=[{
                "id": "qa-front",
                "view_type": "front",
                "version": 3,
                "remote_file_id": "qa-front-file",
            }],
            candidate_snapshot_json={
                "id": candidate.id,
                "remote_file_id": candidate.remote_file_id,
            },
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            status="completed",
            idempotency_key="qa-source-attempt",
            output_remote_file_id="generated-qa-file",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.flush()
        result = RrugcSupervisorResultModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            generation_attempt_id=attempt.id,
            candidate_id=candidate.id,
            product_id=product.id,
            supervisor_skill_version="supervisor-hat-v1",
            status="fail",
            reason="HAT_TOO_LARGE",
            metrics_json={"hat_head_width_ratio": 1.4},
            expected_json={"max": 1.25},
            correction_json={
                "kind": "scale_product",
                "direction": "down",
                "preserve_brim_angle": True,
            },
        )
        session.add(result)
        session.commit()

        service = RrugcSupervisorService(session)
        correction, created = service.prepare_correction(
            result=result,
            user_id="user-a",
        )
        assert created is True
        assert correction.status == "prepared"
        assert correction.generation_variant == 2
        assert correction.parent_attempt_id == attempt.id
        assert correction.correction_supervisor_result_id == result.id
        assert correction.supervisor_correction_json["direction"] == "down"
        assert correction.product_snapshot_json == attempt.product_snapshot_json
        assert (
            correction.product_reference_snapshot_json
            == attempt.product_reference_snapshot_json
        )
        assert correction.candidate_snapshot_json == attempt.candidate_snapshot_json

        duplicate, created_again = service.prepare_correction(
            result=result,
            user_id="user-a",
        )
        assert created_again is False
        assert duplicate.id == correction.id
        attempts = RrugcRepository(session).list_generation_attempts(
            "tenant-a",
            campaign.id,
            candidate_id=candidate.id,
            limit=100,
        )
        assert len(attempts) == 2
        assert len(attempts) < MAX_GENERATION_ATTEMPTS




def test_supervisor_worker_persists_pass_for_completed_generation(database):
    class FakeSupervisorProvider:
        provider_name = "gemini"
        supports_single = True
        supports_batch = False
        default_model = "fake-supervisor"

        async def analyze_single(self, input):
            assert input.metadata_profile == "rrugc_supervisor"
            assert input.image_bytes
            assert b"" != input.image_bytes
            return AiMetadataAnalysisResult(
                metadata=supervisor_document().model_dump(),
                provider="gemini",
                model="fake-supervisor",
            )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="Supervisor worker",
            query="casual review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-SUP",
            name="Supervisor cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="8" * 64,
            pin_url="https://www.pinterest.com/pin/8001/",
            image_url="https://i.pinimg.com/supervisor-source.jpg",
            status="drive_ready",
            remote_file_id="person-supervisor-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=1,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "revision": 1,
            },
            product_reference_snapshot_json=[{
                "id": "sup-front",
                "view_type": "front",
                "version": 1,
                "content_type": "image/png",
                "remote_file_id": "product-supervisor-front-file",
            }],
            candidate_snapshot_json={
                "id": candidate.id,
                "remote_file_id": candidate.remote_file_id,
            },
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            status="completed",
            idempotency_key="supervisor-worker-attempt",
            output_remote_file_id="generated-supervisor-file",
            output_content_type="image/png",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.commit()
        result, created = RrugcSupervisorService(session).enqueue(attempt=attempt)
        assert created is True
        job = session.get(ProcessingJobModel, result.processing_job_id)
        assert job is not None
        claimed = ClaimedJob(
            id=job.id,
            tenant_id=job.tenant_id,
            job_type=job.job_type,
            entity_type=job.entity_type,
            entity_id=job.entity_id,
            payload=job.payload_json,
            attempt_count=job.attempt_count,
            lease_owner="test-worker",
            provider_key=job.provider_key,
        )
        result_id = result.id

    registry = AiProviderRegistry()
    registry.register("gemini", FakeSupervisorProvider())
    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=database,
            storage_provider=FakeStorage(),
            ai_provider_registry=registry,
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("rrugc-supervisor-test"), {}),
    )
    outcome = RrugcSupervisorQaJobHandler()(context)
    assert outcome.outcome == JobOutcome.COMPLETED

    with database() as session:
        result = session.get(RrugcSupervisorResultModel, result_id)
        assert result is not None
        assert result.status == "pass"
        assert result.reason is None
        assert result.provider == "gemini"
        assert result.provider_model == "fake-supervisor"
        assert result.metrics_json["product_visual_similarity"] == pytest.approx(0.92)
        assert result.completed_at is not None




def _seed_review_case(session, *, supervisor_status: str, key: str):
    campaign, _ = RrugcService(session).create_campaign(
        tenant_id="tenant-a",
        user_id="user-a",
        name=f"Review {key}",
        query="casual review portrait",
        target_count=1,
        max_scroll_batches=1,
        auto_import=False,
    )
    product = RrugcProductModel(
        tenant_id="tenant-a",
        sku=f"CAP-{key.upper()}",
        name=f"Review cap {key}",
        created_by_user_id="user-a",
    )
    session.add(product)
    session.flush()
    candidate = RrugcCandidateModel(
        tenant_id="tenant-a",
        campaign_id=campaign.id,
        source_key=(key * 64)[:64],
        pin_url=f"https://www.pinterest.com/pin/{9000 + ord(key[0])}/",
        image_url=f"https://i.pinimg.com/review-{key}.jpg",
        status="drive_ready",
        remote_file_id=f"person-review-{key}",
    )
    session.add(candidate)
    session.flush()
    attempt = RrugcGenerationAttemptModel(
        tenant_id="tenant-a",
        campaign_id=campaign.id,
        candidate_id=candidate.id,
        product_id=product.id,
        product_revision=1,
        product_snapshot_json={
            "id": product.id,
            "sku": product.sku,
            "name": product.name,
            "revision": 1,
        },
        product_reference_snapshot_json=[{
            "id": f"review-ref-{key}",
            "view_type": "front",
            "version": 1,
            "remote_file_id": f"review-ref-file-{key}",
        }],
        candidate_snapshot_json={
            "id": candidate.id,
            "remote_file_id": candidate.remote_file_id,
        },
        generation_variant=1,
        worker_skill_version="worker-hat-v1",
        status="completed",
        idempotency_key=f"review-attempt-{key}",
        output_content_hash=(key.encode().hex() * 64)[:64],
        output_remote_file_id=f"generated-review-{key}",
        output_remote_folder_id="generated-folder",
        output_web_url=f"https://drive.google.com/file/d/generated-review-{key}/view",
        output_content_type="image/png",
        output_size_bytes=2048,
        created_by_user_id="user-a",
    )
    session.add(attempt)
    session.flush()
    result = RrugcSupervisorResultModel(
        tenant_id="tenant-a",
        campaign_id=campaign.id,
        generation_attempt_id=attempt.id,
        candidate_id=candidate.id,
        product_id=product.id,
        supervisor_skill_version="supervisor-hat-v1",
        status=supervisor_status,
        reason=(
            "HAT_TOO_LARGE"
            if supervisor_status == "needs_human_review"
            else None
        ),
        metrics_json={"product_visual_similarity": 0.91},
        summary="Review handoff test.",
    )
    session.add(result)
    session.commit()
    return campaign, attempt, result


def test_review_handoff_routes_terminal_supervisor_states_idempotently(database):
    with database() as session:
        _campaign, pass_attempt, pass_result = _seed_review_case(
            session, supervisor_status="pass", key="p"
        )
        service = RrugcReviewService(session)
        task, created = service.ensure_from_supervisor(result=pass_result)
        assert created is True
        assert task is not None
        assert task.priority == "standard"
        assert task.queue_reason == "supervisor_pass"
        assert task.status == "pending"
        assert pass_attempt.review_status == "pending"
        assert pass_attempt.export_status == "pending_review"

        duplicate, created_again = service.ensure_from_supervisor(result=pass_result)
        assert created_again is False
        assert duplicate is not None
        assert duplicate.id == task.id

        _campaign2, human_attempt, human_result = _seed_review_case(
            session, supervisor_status="needs_human_review", key="h"
        )
        human_task, human_created = service.ensure_from_supervisor(
            result=human_result
        )
        assert human_created is True
        assert human_task is not None
        assert human_task.priority == "high"
        assert human_task.queue_reason == "supervisor_needs_human_review"
        assert human_attempt.review_status == "pending"

        _campaign3, fail_attempt, fail_result = _seed_review_case(
            session, supervisor_status="fail", key="f"
        )
        skipped, fail_created = service.ensure_from_supervisor(result=fail_result)
        assert skipped is None
        assert fail_created is False
        assert (
            RrugcRepository(session).review_task_for_attempt(
                "tenant-a", fail_attempt.id
            )
            is None
        )


def test_review_transition_updates_generation_provenance_and_rejects_conflict(database):
    with database() as session:
        _campaign, attempt, result = _seed_review_case(
            session, supervisor_status="pass", key="t"
        )
        service = RrugcReviewService(session)
        task, _ = service.ensure_from_supervisor(result=result)
        assert task is not None

        approved, transitioned = service.transition(
            tenant_id="tenant-a",
            task_id=task.id,
            target_status="approved",
            user_id="reviewer-a",
            review_note="Looks good for export.",
        )
        assert transitioned is True
        assert approved.status == "approved"
        assert approved.reviewed_by_user_id == "reviewer-a"
        assert attempt.review_status == "approved"
        assert attempt.export_status == "export_ready"
        assert attempt.review_note == "Looks good for export."
        assert attempt.reviewed_at is not None

        same, transitioned_again = service.transition(
            tenant_id="tenant-a",
            task_id=task.id,
            target_status="approved",
            user_id="reviewer-a",
            review_note="Ignored idempotent retry.",
        )
        assert transitioned_again is False
        assert same.status == "approved"

        with pytest.raises(RrugcError) as exc:
            service.transition(
                tenant_id="tenant-a",
                task_id=task.id,
                target_status="rejected",
                user_id="reviewer-b",
            )
        assert exc.value.code == "review_transition_conflict"


def test_review_reconcile_backfills_terminal_supervisor_results(database):
    with database() as session:
        _seed_review_case(session, supervisor_status="pass", key="r")
        _seed_review_case(session, supervisor_status="needs_human_review", key="n")
        _seed_review_case(session, supervisor_status="fail", key="x")
        result = RrugcReviewService(session).reconcile(
            tenant_id="tenant-a",
            limit=20,
        )
        assert result.scanned == 2
        assert result.created == 2
        rows, total = RrugcRepository(session).list_review_tasks(
            "tenant-a",
            limit=20,
        )
        assert total == 2
        assert {row.priority for row in rows} == {"standard", "high"}


def test_review_task_api_lists_and_approves_with_export_state(api, database):
    with database() as session:
        _campaign, attempt, result = _seed_review_case(
            session, supervisor_status="pass", key="a"
        )
        task, created = RrugcReviewService(session).ensure_from_supervisor(
            result=result
        )
        assert created is True
        assert task is not None
        task_id = task.id
        attempt_id = attempt.id

    listed = api.get(
        "/api/v1/realistic-review-ugc/review-tasks",
        params={"status": "pending", "priority": "standard"},
    )
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == task_id
    assert payload["items"][0]["output_url"].endswith(
        f"/generation-attempts/{attempt_id}/output"
    )

    approved = api.post(
        f"/api/v1/realistic-review-ugc/review-tasks/{task_id}/approve",
        json={"review_note": "Approved in board."},
    )
    assert approved.status_code == 200
    assert approved.json()["transitioned"] is True
    assert approved.json()["task"]["status"] == "approved"
    assert approved.json()["task"]["export_status"] == "export_ready"

    repeated = api.post(
        f"/api/v1/realistic-review-ugc/review-tasks/{task_id}/approve",
        json={"review_note": "same-state retry"},
    )
    assert repeated.status_code == 200
    assert repeated.json()["transitioned"] is False

    conflict = api.post(
        f"/api/v1/realistic-review-ugc/review-tasks/{task_id}/reject",
        json={},
    )
    assert conflict.status_code == 409




def _approve_review_for_export(session, *, key: str):
    campaign, attempt, result = _seed_review_case(
        session,
        supervisor_status="pass",
        key=key,
    )
    review_service = RrugcReviewService(session)
    task, _ = review_service.ensure_from_supervisor(result=result)
    assert task is not None
    review_service.transition(
        tenant_id="tenant-a",
        task_id=task.id,
        target_status="approved",
        user_id="reviewer-a",
        review_note="Approved for final catalog.",
    )
    return campaign, attempt, task


def test_export_registers_catalog_asset_without_reupload_and_is_idempotent(database):
    with database() as session:
        campaign, attempt, task = _approve_review_for_export(session, key="e")
        service = RrugcExportService(session)

        row, created = service.export_attempt(
            tenant_id="tenant-a",
            generation_attempt_id=attempt.id,
            user_id="exporter-a",
        )
        assert created is True
        assert row.review_task_id == task.id
        assert row.remote_file_id == attempt.output_remote_file_id
        assert row.storage_provider == "google_drive_managed"
        assert row.status == "exported"
        assert attempt.export_status == "exported"
        assert attempt.export_record_id == row.id
        assert attempt.catalog_asset_id == row.catalog_asset_id
        assert attempt.exported_by_user_id == "exporter-a"
        assert attempt.exported_at is not None

        asset = session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == "tenant-a",
                AssetModel.id == row.catalog_asset_id,
            )
        )
        assert asset is not None
        assert asset.content_hash == attempt.output_content_hash
        storage = session.scalar(
            select(AssetStorageObjectModel).where(
                AssetStorageObjectModel.tenant_id == "tenant-a",
                AssetStorageObjectModel.asset_id == row.catalog_asset_id,
            )
        )
        assert storage is not None
        assert storage.status == "stored"
        assert storage.storage_class == "durable"
        assert storage.remote_file_id == attempt.output_remote_file_id

        same, created_again = service.export_attempt(
            tenant_id="tenant-a",
            generation_attempt_id=attempt.id,
            user_id="exporter-b",
        )
        assert created_again is False
        assert same.id == row.id
        assert attempt.exported_by_user_id == "exporter-a"

        rows, total = RrugcRepository(session).list_exports(
            "tenant-a",
            campaign_id=campaign.id,
        )
        assert total == 1
        assert rows[0].id == row.id


def test_export_rejects_unapproved_generation(database):
    with database() as session:
        _campaign, attempt, result = _seed_review_case(
            session,
            supervisor_status="pass",
            key="u",
        )
        task, _ = RrugcReviewService(session).ensure_from_supervisor(result=result)
        assert task is not None
        with pytest.raises(RrugcError) as exc:
            RrugcExportService(session).export_attempt(
                tenant_id="tenant-a",
                generation_attempt_id=attempt.id,
                user_id="exporter-a",
            )
        assert exc.value.code == "rrugc_export_approval_required"


def test_campaign_batch_export_and_summary(database):
    with database() as session:
        campaign, attempt_a, _task_a = _approve_review_for_export(session, key="b")
        _campaign_b, attempt_b, _task_b = _approve_review_for_export(session, key="c")
        # Move the second approved attempt into the same campaign to exercise one bounded batch.
        attempt_b.campaign_id = campaign.id
        session.commit()

        service = RrugcExportService(session)
        before = service.summary(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
        )
        assert before["export_ready"] == 2
        assert before["exported"] == 0

        batch = service.export_campaign(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            user_id="exporter-a",
            limit=20,
        )
        assert batch.scanned == 2
        assert batch.exported == 2
        assert batch.reused == 0
        assert {item.generation_attempt_id for item in batch.items} == {
            attempt_a.id,
            attempt_b.id,
        }

        after = service.summary(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
        )
        assert after["export_ready"] == 0
        assert after["exported"] == 2


def test_export_api_single_batch_list_and_summary(api, database):
    with database() as session:
        campaign, attempt, _task = _approve_review_for_export(session, key="z")
        campaign_id = campaign.id
        attempt_id = attempt.id

    single = api.post(
        f"/api/v1/realistic-review-ugc/generation-attempts/{attempt_id}/export",
    )
    assert single.status_code == 200
    payload = single.json()
    assert payload["generation_attempt_id"] == attempt_id
    assert payload["status"] == "exported"
    assert payload["catalog_asset_id"]

    repeated = api.post(
        f"/api/v1/realistic-review-ugc/generation-attempts/{attempt_id}/export",
    )
    assert repeated.status_code == 200
    assert repeated.json()["id"] == payload["id"]

    listed = api.get(
        "/api/v1/realistic-review-ugc/exports",
        params={"campaign_id": campaign_id},
    )
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    summary = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/export-summary",
    )
    assert summary.status_code == 200
    assert summary.json()["exported"] == 1

    batch = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/exports",
    )
    assert batch.status_code == 200
    assert batch.json()["scanned"] == 0
    assert batch.json()["exported"] == 0




class _FakeDeliveryStorage:
    def __init__(
        self,
        *,
        fail_first: bool = False,
        always_fail: bool = False,
    ):
        self.calls: list[dict] = []
        self.fail_first = fail_first
        self.always_fail = always_fail

    async def copy_asset_to_folder(self, **kwargs):
        self.calls.append(dict(kwargs))
        if self.always_fail or (self.fail_first and len(self.calls) == 1):
            raise StorageProviderError(
                "temporary delivery failure",
                code="delivery_temporary",
                retryable=True,
            )
        item_id = kwargs["delivery_item_id"]
        return StoredAsset(
            storage_key=f"google_drive_managed:copy-{item_id}",
            content_hash=kwargs["content_hash"],
            size_bytes=2048,
            storage_provider="google_drive_managed",
            remote_file_id=f"copy-{item_id}",
            remote_folder_id=kwargs["destination_folder_id"],
            web_url=f"https://drive.google.com/file/d/copy-{item_id}/view",
        )




def _delivery_automation_settings(**overrides):
    values = {
        "PROCESSING_JOBS_ENABLED": True,
        "MANAGED_ASSET_STORAGE_ENABLED": True,
        "RRUGC_DELIVERY_AUTOMATION_ENABLED": True,
        "RRUGC_DELIVERY_MAINTENANCE_INTERVAL_SECONDS": 300,
        "RRUGC_DELIVERY_MAINTENANCE_MAX_PACKAGES_PER_RUN": 20,
        "RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS": 3,
        "RRUGC_DELIVERY_AUTO_RETRY_BASE_SECONDS": 60,
        "RRUGC_DELIVERY_AUTO_RETRY_MAX_SECONDS": 600,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _catalog_one_for_delivery(session, *, key: str):
    campaign, attempt, task = _approve_review_for_export(session, key=key)
    export, created = RrugcExportService(session).export_attempt(
        tenant_id="tenant-a",
        generation_attempt_id=attempt.id,
        user_id="exporter-a",
    )
    assert created is True
    return campaign, attempt, task, export


def test_delivery_copies_cataloged_asset_idempotently_and_auto_completes(database):
    with database() as session:
        campaign, _attempt, _task, export = _catalog_one_for_delivery(
            session,
            key="delivery-a",
        )
        fake = _FakeDeliveryStorage()
        service = RrugcDeliveryService(session, storage=fake)
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Paid Social Finals",
            kind="google_drive_folder",
            target_ref="drive-folder-final",
            retention_days=30,
        )
        service.set_campaign_policy(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            auto_complete_on_delivery=True,
            completion_destination_id=destination.id,
        )

        package = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert package.status == "delivered"
        assert package.export_count == 1
        assert package.delivered_count == 1
        assert package.failed_count == 0
        assert package.delivered_at is not None
        assert package.expires_at is not None
        assert len(fake.calls) == 1
        assert fake.calls[0]["source_remote_file_id"] == export.remote_file_id
        assert fake.calls[0]["destination_folder_id"] == "drive-folder-final"

        refreshed_campaign = RrugcRepository(session).get_campaign(
            "tenant-a",
            campaign.id,
        )
        assert refreshed_campaign is not None
        assert refreshed_campaign.completed_at is not None

        repeated = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-b",
            )
        )
        assert repeated.id == package.id
        assert len(fake.calls) == 1

        summary = service.delivery_summary(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
        )
        assert summary["cataloged"] == 1
        assert summary["packages_delivered"] == 1
        assert summary["auto_complete_eligible"] is True


def test_delivery_partial_failure_retries_same_package_without_new_package(database):
    with database() as session:
        campaign, _attempt, _task, _export = _catalog_one_for_delivery(
            session,
            key="delivery-retry",
        )
        fake = _FakeDeliveryStorage(fail_first=True)
        service = RrugcDeliveryService(session, storage=fake)
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Retry Folder",
            kind="google_drive_folder",
            target_ref="drive-folder-retry",
            retention_days=14,
        )

        first = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert first.status == "partial_failed"
        assert first.failed_count == 1
        assert first.delivered_count == 0

        second = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert second.id == first.id
        assert second.status == "delivered"
        assert second.delivered_count == 1
        assert len(fake.calls) == 2

        packages, total = service.list_packages(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
        )
        assert total == 1
        assert packages[0].id == first.id


def test_delivery_lifecycle_marks_expired_without_deleting_catalog_asset(database):
    with database() as session:
        campaign, _attempt, _task, export = _catalog_one_for_delivery(
            session,
            key="delivery-expire",
        )
        fake = _FakeDeliveryStorage()
        service = RrugcDeliveryService(session, storage=fake)
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Short Retention",
            kind="google_drive_folder",
            target_ref="drive-folder-short",
            retention_days=1,
        )
        package = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        package.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        session.commit()

        result = service.reconcile_lifecycle(
            tenant_id="tenant-a",
            now=datetime.now(timezone.utc),
        )
        assert result.scanned == 1
        assert result.expired == 1
        session.refresh(package)
        assert package.status == "expired"
        assert package.expired_at is not None
        asset = session.scalar(
            select(AssetModel).where(
                AssetModel.tenant_id == "tenant-a",
                AssetModel.id == export.catalog_asset_id,
            )
        )
        assert asset is not None




def test_delivery_automation_schedules_due_retry_and_records_events(database):
    settings = _delivery_automation_settings()
    with database() as session:
        campaign, _attempt, _task, _export = _catalog_one_for_delivery(
            session,
            key="delivery-auto-retry",
        )
        fake = _FakeDeliveryStorage(fail_first=True)
        service = RrugcDeliveryService(
            session,
            storage=fake,
            settings=settings,
        )
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Automated Retry Folder",
            kind="google_drive_folder",
            target_ref="drive-folder-auto",
            retention_days=30,
        )
        package = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert package.status == "partial_failed"
        assert package.auto_retry_count == 0
        assert package.next_retry_at is not None

        package.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()
        summary = service.operations_summary(tenant_id="tenant-a")
        assert summary["automation_enabled"] is True
        assert summary["retry_due"] == 1

    scheduler = RrugcDeliveryMaintenanceScheduler(
        database,
        settings,
        logger=logging.getLogger("test.rrugc.delivery"),
    )
    scheduled = scheduler.tick(now=datetime.now(timezone.utc))
    assert len(scheduled) == 1
    assert scheduled[0].created is True
    with database() as session:
        job = session.scalar(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_delivery_maintenance",
                ProcessingJobModel.tenant_id == "tenant-a",
            )
        )
        assert job is not None
        package = session.scalar(
            select(RrugcDeliveryPackageModel).where(
                RrugcDeliveryPackageModel.tenant_id == "tenant-a",
                RrugcDeliveryPackageModel.campaign_id == campaign.id,
            )
        )
        assert package is not None
        retried = asyncio.run(
            RrugcDeliveryService(
                session,
                storage=fake,
                settings=settings,
            ).retry_package(
                tenant_id="tenant-a",
                package_id=package.id,
                automated_retry=True,
            )
        )
        assert retried.status == "delivered"
        assert retried.auto_retry_count == 1
        assert retried.last_retry_at is not None
        assert retried.next_retry_at is None
        events = RrugcDeliveryService(
            session,
            settings=settings,
        ).recent_events(tenant_id="tenant-a", limit=20)
        assert {event.event_type for event in events} >= {
            "package_partial_failed",
            "package_delivered",
        }


def test_delivery_automation_exhausts_bounded_retry_budget(database):
    settings = _delivery_automation_settings(
        RRUGC_DELIVERY_AUTO_RETRY_MAX_ATTEMPTS=1,
    )
    with database() as session:
        campaign, _attempt, _task, _export = _catalog_one_for_delivery(
            session,
            key="delivery-auto-exhaust",
        )
        fake = _FakeDeliveryStorage(always_fail=True)
        service = RrugcDeliveryService(
            session,
            storage=fake,
            settings=settings,
        )
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Exhaust Folder",
            kind="google_drive_folder",
            target_ref="drive-folder-exhaust",
            retention_days=7,
        )
        package = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert package.next_retry_at is not None
        package.next_retry_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        session.commit()

        package = asyncio.run(
            service.retry_package(
                tenant_id="tenant-a",
                package_id=package.id,
                automated_retry=True,
            )
        )
        assert package.status == "partial_failed"
        assert package.auto_retry_count == 1
        assert package.next_retry_at is None
        summary = service.operations_summary(tenant_id="tenant-a")
        assert summary["retry_exhausted"] == 1
        events = service.recent_events(tenant_id="tenant-a", limit=20)
        assert any(event.event_type == "auto_retry_exhausted" for event in events)


def test_delivery_automation_adopts_legacy_partial_package_without_schedule(database):
    settings = _delivery_automation_settings()
    with database() as session:
        campaign, _attempt, _task, _export = _catalog_one_for_delivery(
            session,
            key="delivery-auto-legacy",
        )
        fake = _FakeDeliveryStorage(always_fail=True)
        disabled_settings = _delivery_automation_settings(
            RRUGC_DELIVERY_AUTOMATION_ENABLED=False,
        )
        service = RrugcDeliveryService(
            session,
            storage=fake,
            settings=disabled_settings,
        )
        destination = service.create_destination(
            tenant_id="tenant-a",
            user_id="operator-a",
            name="Legacy Partial Folder",
            kind="google_drive_folder",
            target_ref="drive-folder-legacy",
            retention_days=7,
        )
        package = asyncio.run(
            service.deliver_campaign(
                tenant_id="tenant-a",
                campaign_id=campaign.id,
                destination_id=destination.id,
                user_id="operator-a",
            )
        )
        assert package.status == "partial_failed"
        assert package.next_retry_at is None
        package.updated_at = datetime.now(timezone.utc) - timedelta(minutes=2)
        session.commit()

        due = RrugcDeliveryService(
            session,
            storage=fake,
            settings=settings,
        ).due_retry_packages(
            tenant_id="tenant-a",
            limit=20,
            now=datetime.now(timezone.utc),
        )
        assert [row.id for row in due] == [package.id]


def test_delivery_destination_policy_and_summary_api(api, database):
    with database() as session:
        campaign, _attempt, _task, _export = _catalog_one_for_delivery(
            session,
            key="delivery-api",
        )
        campaign_id = campaign.id

    created = api.post(
        "/api/v1/realistic-review-ugc/delivery-destinations",
        json={
            "name": "TikTok Finals",
            "kind": "google_drive_folder",
            "target_ref": "drive-folder-tiktok",
            "retention_days": 45,
        },
    )
    assert created.status_code == 201
    destination_id = created.json()["id"]

    listed = api.get("/api/v1/realistic-review-ugc/delivery-destinations")
    assert listed.status_code == 200
    assert listed.json()[0]["id"] == destination_id

    policy = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/lifecycle-policy",
        json={
            "auto_complete_on_delivery": True,
            "completion_destination_id": destination_id,
        },
    )
    assert policy.status_code == 200
    assert policy.json()["auto_complete_on_delivery"] is True
    assert policy.json()["completion_destination_id"] == destination_id

    summary = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/delivery-summary",
    )
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["cataloged"] == 1
    assert payload["packages_total"] == 0
    assert payload["auto_complete_eligible"] is False

    packages = api.get(
        "/api/v1/realistic-review-ugc/delivery-packages",
        params={"campaign_id": campaign_id},
    )
    assert packages.status_code == 200
    assert packages.json()["total"] == 0

    operations = api.get(
        "/api/v1/realistic-review-ugc/delivery-operations/summary",
    )
    assert operations.status_code == 200
    operations_payload = operations.json()
    assert operations_payload["campaigns_total"] == 1
    assert operations_payload["destinations_active"] == 1
    assert operations_payload["packages_total"] == 0
    assert operations_payload["recent_events"] == []


def test_campaign_approved_count_survives_drive_lifecycle(api, database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="qualification count",
            query="candid lifestyle photo",
            target_count=10,
            max_scroll_batches=2,
            auto_import=True,
        )
        rows, created, _ = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[
                CandidateSubmission(
                    pin_url=f"https://www.pinterest.com/pin/approved-count-{index}/",
                    image_url=f"https://i.pinimg.com/736x/approved-count-{index}.jpg",
                    alt_text="candid portrait",
                )
                for index in range(4)
            ],
        )
        assert created == 4
        for candidate, status in zip(
            rows,
            ("import_queued", "drive_ready", "import_failed", "rejected_duplicate"),
            strict=True,
        ):
            candidate.status = status
        session.commit()
        campaign_id = campaign.id

    listed = api.get("/api/v1/realistic-review-ugc/campaigns")
    assert listed.status_code == 200
    payload = next(item for item in listed.json() if item["id"] == campaign_id)
    assert payload["approved"] == 3
    assert payload["drive_ready"] == 1


def test_campaign_delete_archives_and_hides_from_list(api, database):
    created = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Disposable campaign",
            "query": "casual portrait",
            "target_count": 10,
            "max_scroll_batches": 2,
            "auto_import": False,
            "auto_scout": True,
        },
    )
    assert created.status_code == 201
    campaign_id = created.json()["id"]
    scout_token = created.json()["scout_token"]

    deleted = api.delete(
        "/api/v1/realistic-review-ugc/campaigns/" + campaign_id
    )
    assert deleted.status_code == 204
    assert deleted.content == b""

    listed = api.get("/api/v1/realistic-review-ugc/campaigns")
    assert listed.status_code == 200
    assert all(item["id"] != campaign_id for item in listed.json())

    scout_after_delete = api.get(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/task",
        headers={"Authorization": "Bearer " + scout_token},
    )
    assert scout_after_delete.status_code == 410

    with database() as session:
        archived = session.get(RrugcCampaignModel, campaign_id)
        assert archived is not None
        assert archived.status == "archived"


def test_campaign_api_scout_auth_and_idempotent_candidates(api, database):
    created = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Hat references",
            "query": "happy woman casual outdoor",
            "target_count": 25,
            "max_scroll_batches": 4,
            "auto_import": False,
            "min_head_ratio": 0.20,
            "max_head_ratio": 0.45,
        },
    )
    assert created.status_code == 201
    payload = created.json()
    campaign_id = payload["id"]
    token = payload["scout_token"]
    assert token
    assert payload["min_head_ratio"] == pytest.approx(0.20)
    assert payload["max_head_ratio"] == pytest.approx(0.45)
    assert "scout_token" not in api.get(
        "/api/v1/realistic-review-ugc/campaigns"
    ).json()[0]

    task = api.get(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/task",
        headers={"Authorization": "Bearer " + token},
    )
    assert task.status_code == 200
    assert task.json()["query"] in payload["search_queries"]
    assert "woman wearing" in task.json()["query"]
    assert "hat" in task.json()["query"] or "cap" in task.json()["query"]

    denied = api.get(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/task",
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert denied.status_code == 401

    body = {
        "items": [{
            "pin_url": "https://www.pinterest.com/pin/123/",
            "image_url": "https://i.pinimg.com/736x/a/b/c.jpg",
            "alt_text": "casual portrait",
        }]
    }
    first = api.post(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/candidates",
        headers={"Authorization": "Bearer " + token},
        json=body,
    )
    assert first.status_code == 200
    assert first.json()["created"] == 1
    candidate = first.json()["items"][0]
    assert candidate["status"] == "analysis_queued"

    replay = api.post(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/candidates",
        headers={"Authorization": "Bearer " + token},
        json=body,
    )
    assert replay.status_code == 200
    assert replay.json()["created"] == 0
    assert replay.json()["existing"] == 1

    synthetic = api.post(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/candidates",
        headers={"Authorization": "Bearer " + token},
        json={"items": [{
            "pin_url": "https://www.pinterest.com/pin/ai-portrait/",
            "image_url": "https://i.pinimg.com/736x/ai/portrait.jpg",
            "alt_text": "AI generated Midjourney fashion portrait",
        }]},
    )
    assert synthetic.status_code == 200
    assert synthetic.json()["created"] == 0
    assert synthetic.json()["existing"] == 0
    assert synthetic.json()["items"] == []

    with database() as session:
        jobs = list(session.scalars(select(ProcessingJobModel)))
        assert len(jobs) == 1
        assert jobs[0].job_type == "rrugc_candidate_analyze"
        assert jobs[0].entity_id == candidate["id"]
        assert jobs[0].provider_key == "gemini"

    invalid = api.post(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/candidates",
        headers={"Authorization": "Bearer " + token},
        json={"items": [{
            "pin_url": "https://evil.example/pin/9/",
            "image_url": "https://i.pinimg.com/a.jpg",
        }]},
    )
    assert invalid.status_code == 400


def test_import_api_requires_approved_reference_and_queues_job(api, database):
    created = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Hat references",
            "query": "casual portrait",
            "target_count": 1,
            "max_scroll_batches": 1,
        },
    ).json()
    campaign_id = created["id"]
    token = created["scout_token"]
    submitted = api.post(
        "/api/v1/realistic-review-ugc/scout/" + campaign_id + "/candidates",
        headers={"Authorization": "Bearer " + token},
        json={"items": [{
            "pin_url": "https://www.pinterest.com/pin/555/",
            "image_url": "https://i.pinimg.com/a.jpg",
        }]},
    ).json()
    candidate_id = submitted["items"][0]["id"]

    rejected = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/import",
        json={},
    )
    assert rejected.status_code == 409

    with database() as session:
        row = session.get(RrugcCandidateModel, candidate_id)
        assert row is not None
        row.status = "approved"
        row.analyzed_at = row.created_at
        session.commit()

    queued = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/import",
        json={},
    )
    assert queued.status_code == 202
    assert queued.json()["candidate"]["status"] == "import_queued"

    with database() as session:
        jobs = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_candidate_import"
            )
        ))
        assert len(jobs) == 1
        assert jobs[0].provider_key == "google_drive"


class FakeDownloader:
    def __init__(
        self,
        path: Path,
        *,
        width: int = 800,
        height: int = 700,
        source_url: str = "https://i.pinimg.com/test.jpg",
    ):
        self.path = path
        self.width = width
        self.height = height
        self.source_url = source_url
        self.requested_urls: list[str] = []

    @asynccontextmanager
    async def download(self, url: str):
        self.requested_urls.append(url)
        yield DownloadedImage(
            path=self.path,
            content_hash="a" * 64,
            size_bytes=self.path.stat().st_size,
            width=self.width,
            height=self.height,
            image_format="JPEG",
            source_url=self.source_url,
        )


class FakeStorage:
    def __init__(self):
        self.payload = b""
        self.input = None
        self.calls = 0

    async def store_asset(self, input):
        self.calls += 1
        self.input = input
        chunks = []
        async for chunk in input.body:
            chunks.append(chunk)
        self.payload = b"".join(chunks)
        return StoredAsset(
            storage_key="drive/file-1",
            content_hash=input.content_hash,
            size_bytes=len(self.payload),
            storage_provider="google_drive_managed",
            remote_file_id="file-1",
            remote_folder_id="folder-1",
            web_url="https://drive.google.com/file/d/file-1/view",
        )

    async def open_asset(self, input):
        async def body():
            yield self.payload or _png_bytes()

        async def close():
            return None

        return StoredAssetReadStream(
            body=body(),
            close=close,
            content_type=input.content_type or "image/png",
            size_bytes=len(self.payload or _png_bytes()),
        )


def test_import_candidate_uses_existing_storage_contract(database):
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")
        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="test",
                query="test",
                target_count=1,
                max_scroll_batches=1,
                auto_import=False,
            )
            rows, created, existing = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/123/",
                    image_url="https://i.pinimg.com/a.jpg",
                )],
            )
            assert (created, existing) == (1, 0)
            rows[0].status = "approved"
            session.commit()
            storage = FakeStorage()
            imported = asyncio.run(RrugcService(session).import_candidate(
                candidate=rows[0],
                storage=storage,
                downloader=FakeDownloader(path),
            ))
            assert imported.status == "drive_ready"
            assert imported.remote_file_id == "file-1"
            assert imported.remote_folder_id == "folder-1"
            assert storage.payload == b"fake-jpeg-content"
            assert storage.input.filename.startswith("REF_")

            references = RrugcRepository(session).list_reference_assets("tenant-a")
            assert len(references) == 1
            assert references[0].source_type == "pinterest"
            assert references[0].source_candidate_id == imported.id
            assert references[0].source_campaign_id == campaign.id
            assert references[0].content_hash == imported.content_hash
            assert references[0].remote_file_id == imported.remote_file_id
            reference_id = references[0].id

            retried = asyncio.run(RrugcService(session).import_candidate(
                candidate=imported,
                storage=storage,
                downloader=FakeDownloader(path),
            ))
            assert retried.id == imported.id
            references = RrugcRepository(session).list_reference_assets("tenant-a")
            assert len(references) == 1
            assert references[0].id == reference_id


def test_reference_asset_api_promotes_ready_candidate_idempotently(database, api):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="reference library",
            query="pet owner candid",
            target_count=3,
            max_scroll_batches=1,
            auto_import=False,
        )
        campaign.discovery_mode = "product_context"
        campaign.product_context_json = {"themes": ["pet_owner", "outdoor"]}
        rows, created, existing = RrugcService(session).ingest_candidates(
            campaign=campaign,
            submissions=[CandidateSubmission(
                pin_url="https://www.pinterest.com/pin/reference-library-1/",
                image_url="https://i.pinimg.com/736x/ref/library.jpg",
            )],
        )
        assert (created, existing) == (1, 0)
        candidate = rows[0]
        candidate.status = "drive_ready"
        candidate.content_hash = "a" * 64
        candidate.width = 1200
        candidate.height = 1600
        candidate.size_bytes = 345678
        candidate.image_format = "jpeg"
        candidate.people_count = 1
        candidate.quality_score = 0.91
        candidate.ai_signal_json = {
            **(candidate.ai_signal_json or {}),
            "context_match": {"active": True, "score": 0.88},
        }
        candidate.remote_file_id = "managed-ref-1"
        candidate.remote_folder_id = "managed-folder"
        candidate.web_url = "https://drive.google.com/file/d/managed-ref-1/view"
        session.commit()
        candidate_id = candidate.id
        campaign_id = campaign.id

    first = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-asset"
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["created"] is True
    assert first_payload["asset"]["source_type"] == "pinterest"
    assert first_payload["asset"]["source_candidate_id"] == candidate_id
    assert first_payload["asset"]["reference_type"] == "person"
    assert first_payload["asset"]["themes"] == ["pet_owner", "outdoor"]
    assert first_payload["asset"]["context_score"] == 0.88
    reference_id = first_payload["asset"]["id"]

    second = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/reference-asset"
    )
    assert second.status_code == 200
    assert second.json()["created"] is False
    assert second.json()["asset"]["id"] == reference_id

    listed = api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"source_type": "pinterest", "campaign_id": campaign_id},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [reference_id]

    fetched = api.get(
        f"/api/v1/realistic-review-ugc/reference-assets/{reference_id}"
    )
    assert fetched.status_code == 200
    assert fetched.json()["content_hash"] == "a" * 64


def test_import_candidate_rejects_low_resolution_before_storage(database):
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")
        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="low-res import",
                query="test",
                target_count=1,
                max_scroll_batches=1,
                auto_import=False,
            )
            rows, created, existing = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/low-res-import/",
                    image_url="https://i.pinimg.com/736x/aa/bb/low.jpg",
                )],
            )
            assert (created, existing) == (1, 0)
            rows[0].status = "approved"
            session.commit()
            storage = FakeStorage()
            imported = asyncio.run(RrugcService(session).import_candidate(
                candidate=rows[0],
                storage=storage,
                downloader=FakeDownloader(
                    path,
                    width=564,
                    height=846,
                    source_url="https://i.pinimg.com/originals/aa/bb/low.jpg",
                ),
            ))
            assert imported.status == "rejected_quality"
            assert imported.reject_reason == "RESOLUTION_TOO_LOW"
            assert imported.width == 564
            assert imported.height == 846
            assert storage.calls == 0


def test_manual_reference_good_cannot_override_hard_low_resolution(database):
    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="low-res manual ref",
            query="test",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="c" * 64,
            pin_url="https://www.pinterest.com/pin/low-res-manual/",
            image_url="https://i.pinimg.com/originals/aa/bb/low.jpg",
            status="rejected_quality",
            reject_reason="RESOLUTION_TOO_LOW",
            width=564,
            height=846,
            analyzed_at=datetime.now(timezone.utc),
        )
        session.add(candidate)
        session.commit()
        row = RrugcService(session).mark_candidate_reference_label(
            candidate,
            label="good",
            note="composition is useful but file is too small",
            user_id="user-a",
        )
        assert row.status == "rejected_quality"
        assert row.reject_reason == "RESOLUTION_TOO_LOW"
        assert row.ai_signal_json["reference_manual_label"] == "good"
        assert row.ai_signal_json["reference_manual_approval_override"] is False


class FakeAnalysisProvider:
    provider_name = "gemini"
    supports_single = True
    supports_batch = False
    default_model = "fake-vision"

    async def analyze_single(self, input):
        assert input.metadata_profile == "rrugc_reference"
        assert input.image_bytes
        return AiMetadataAnalysisResult(
            metadata=reference_document().model_dump(),
            provider="gemini",
            model="fake-vision",
        )


def test_analysis_worker_persists_metrics_and_queues_auto_import(database, monkeypatch):
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.build_reference_downloader",
            lambda: FakeDownloader(path),
        )

        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="auto",
                query="happy candid portrait",
                target_count=1,
                max_scroll_batches=1,
                auto_import=True,
            )
            rows, created, _ = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/987/",
                    image_url="https://i.pinimg.com/analysis.jpg",
                )],
            )
            assert created == 1
            candidate_id = rows[0].id
            job = session.scalar(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                    ProcessingJobModel.entity_id == candidate_id,
                )
            )
            assert job is not None
            claimed = ClaimedJob(
                id=job.id,
                tenant_id=job.tenant_id,
                job_type=job.job_type,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                payload=job.payload_json,
                attempt_count=job.attempt_count,
                lease_owner="test-worker",
                provider_key=job.provider_key,
            )

        registry = AiProviderRegistry()
        registry.register("gemini", FakeAnalysisProvider())
        context = JobHandlerContext(
            job=claimed,
            dependencies=WorkerDependencies(
                session_factory=database,
                storage_provider=FakeStorage(),
                ai_provider_registry=registry,
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger("rrugc-test"), {}),
        )
        outcome = RrugcCandidateAnalyzeJobHandler()(context)
        assert outcome.outcome == JobOutcome.COMPLETED

        with database() as session:
            candidate = session.get(RrugcCandidateModel, candidate_id)
            assert candidate is not None
            assert candidate.status == "import_queued"
            assert candidate.people_count == 1
            assert candidate.primary_head_ratio == pytest.approx(0.31)
            assert candidate.smile_score == pytest.approx(0.82)
            assert candidate.analyzer_provider == "gemini"
            assert candidate.analyzer_model == "fake-vision"
            assert candidate.analyzed_at is not None
            import_jobs = list(session.scalars(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "rrugc_candidate_import",
                    ProcessingJobModel.entity_id == candidate_id,
                )
            ))
            assert len(import_jobs) == 1


def test_analysis_worker_rejects_visual_duplicate_locally_before_gemini(database, monkeypatch):
    from app.modules.realistic_review_ugc.visual_dedupe import visual_fingerprints

    class CountingAnalysisProvider(FakeAnalysisProvider):
        def __init__(self):
            self.calls = 0

        async def analyze_single(self, input):
            self.calls += 1
            return await super().analyze_single(input)

    with TemporaryDirectory() as temp:
        path = Path(temp) / "duplicate.jpg"
        Image.new("RGB", (1000, 900), (120, 80, 40)).save(
            path,
            format="JPEG",
            quality=90,
        )
        payload = path.read_bytes()
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.build_reference_downloader",
            lambda: FakeDownloader(path, width=1000, height=900),
        )

        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="local-dedupe",
                query="candid person outdoors",
                target_count=2,
                max_scroll_batches=1,
                auto_import=False,
            )
            rows, created, _ = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[
                    CandidateSubmission(
                        pin_url="https://www.pinterest.com/pin/local-dedupe-source/",
                        image_url="https://i.pinimg.com/local-dedupe-source.jpg",
                    ),
                    CandidateSubmission(
                        pin_url="https://www.pinterest.com/pin/local-dedupe-copy/",
                        image_url="https://i.pinimg.com/local-dedupe-copy.jpg",
                    ),
                ],
            )
            assert created == 2
            source, duplicate = rows
            source.status = "approved"
            source.analyzed_at = datetime.now(timezone.utc)
            source.content_hash = "b" * 64
            RrugcRepository(session).replace_visual_fingerprints(
                source,
                visual_fingerprints(payload),
            )
            session.commit()

            job = session.scalar(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                    ProcessingJobModel.entity_id == duplicate.id,
                )
            )
            assert job is not None
            claimed = ClaimedJob(
                id=job.id,
                tenant_id=job.tenant_id,
                job_type=job.job_type,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                payload=job.payload_json,
                attempt_count=job.attempt_count,
                lease_owner="test-worker",
                provider_key=job.provider_key,
            )
            duplicate_id = duplicate.id

        provider = CountingAnalysisProvider()
        registry = AiProviderRegistry()
        registry.register("gemini", provider)
        context = JobHandlerContext(
            job=claimed,
            dependencies=WorkerDependencies(
                session_factory=database,
                storage_provider=FakeStorage(),
                ai_provider_registry=registry,
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(
                logging.getLogger("rrugc-local-dedupe-test"),
                {},
            ),
        )

        outcome = RrugcCandidateAnalyzeJobHandler()(context)

        assert outcome.outcome == JobOutcome.COMPLETED
        assert provider.calls == 0
        with database() as session:
            row = session.get(RrugcCandidateModel, duplicate_id)
            assert row is not None
            assert row.status == "rejected_duplicate"
            assert row.reject_reason == "visual_near_duplicate"
            assert row.analyzer_provider == "local_vps"
            assert row.analyzer_model == "dhash16"
            assert row.analyzed_at is not None
            assert row.ai_signal_json["local_prefilter"] == {
                "gate": "visual_near_duplicate",
                "runtime": "vps",
                "provider_call_skipped": True,
                "fingerprint_count": 1,
            }


def test_high_confidence_negative_seed_gate_is_conservative():
    from app.modules.realistic_review_ugc.seed_similarity import (
        high_confidence_negative_seed_gate,
    )

    strong = high_confidence_negative_seed_gate({
        "active": True,
        "positive_count": 2,
        "negative_count": 3,
        "positive_similarity": 0.50,
        "negative_similarity": 0.82,
    })
    assert strong is not None
    assert strong["mode"] == "negative_over_positive"
    assert strong["margin"] == pytest.approx(0.32)

    assert high_confidence_negative_seed_gate({
        "active": True,
        "positive_count": 2,
        "negative_count": 3,
        "positive_similarity": 0.70,
        "negative_similarity": 0.82,
    }) is None
    assert high_confidence_negative_seed_gate({
        "active": True,
        "positive_count": 0,
        "negative_count": 2,
        "positive_similarity": None,
        "negative_similarity": 0.87,
    }) is None

    negative_only = high_confidence_negative_seed_gate({
        "active": True,
        "positive_count": 0,
        "negative_count": 2,
        "positive_similarity": None,
        "negative_similarity": 0.89,
    })
    assert negative_only is not None
    assert negative_only["mode"] == "negative_only"


def test_analysis_worker_skips_gemini_for_high_confidence_negative_seed(
    database,
    monkeypatch,
):
    class CountingAnalysisProvider(FakeAnalysisProvider):
        def __init__(self):
            self.calls = 0

        async def analyze_single(self, input):
            self.calls += 1
            return await super().analyze_single(input)

    with TemporaryDirectory() as temp:
        path = Path(temp) / "seed-negative.jpg"
        Image.new("RGB", (1000, 900), (90, 120, 70)).save(
            path,
            format="JPEG",
            quality=90,
        )
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.build_reference_downloader",
            lambda: FakeDownloader(path, width=1000, height=900),
        )

        async def strong_negative_seed_signal(**_kwargs):
            return {
                "active": True,
                "profile_key": "realistic-person-ugc",
                "positive_count": 2,
                "negative_count": 3,
                "positive_similarity": 0.50,
                "negative_similarity": 0.82,
                "score": -0.32,
                "adjustment": -0.0192,
            }

        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.compute_seed_visual_signal",
            strong_negative_seed_signal,
        )

        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="local-seed-gate",
                query="candid person outdoors",
                target_count=2,
                max_scroll_batches=1,
                auto_import=False,
            )
            rows, created, _ = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[CandidateSubmission(
                    pin_url="https://www.pinterest.com/pin/local-seed-negative/",
                    image_url="https://i.pinimg.com/local-seed-negative.jpg",
                )],
            )
            assert created == 1
            candidate_id = rows[0].id
            job = session.scalar(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                    ProcessingJobModel.entity_id == candidate_id,
                )
            )
            assert job is not None
            claimed = ClaimedJob(
                id=job.id,
                tenant_id=job.tenant_id,
                job_type=job.job_type,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                payload=job.payload_json,
                attempt_count=job.attempt_count,
                lease_owner="test-worker",
                provider_key=job.provider_key,
            )

        provider = CountingAnalysisProvider()
        registry = AiProviderRegistry()
        registry.register("gemini", provider)
        context = JobHandlerContext(
            job=claimed,
            dependencies=WorkerDependencies(
                session_factory=database,
                storage_provider=FakeStorage(),
                ai_provider_registry=registry,
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(
                logging.getLogger("rrugc-local-seed-gate-test"),
                {},
            ),
        )

        outcome = RrugcCandidateAnalyzeJobHandler()(context)

        assert outcome.outcome == JobOutcome.COMPLETED
        assert provider.calls == 0
        with database() as session:
            candidate = session.get(RrugcCandidateModel, candidate_id)
            assert candidate is not None
            assert candidate.status == "rejected_context"
            assert candidate.reject_reason == "SEED_VISUAL_HIGH_CONFIDENCE_NEGATIVE"
            assert candidate.analyzer_provider == "local_vps"
            assert candidate.analyzer_model == "siglip2-seed-gate-v1"
            assert candidate.ai_signal_json["local_prefilter"]["provider_call_skipped"] is True
            assert candidate.ai_signal_json["local_prefilter"]["margin"] == pytest.approx(0.32)
            assert candidate.ai_signal_json["seed_visual"]["negative_count"] == 3


def test_analysis_worker_applies_scoped_seed_visual_ranking(api, database, monkeypatch):
    with TemporaryDirectory() as temp:
        path = Path(temp) / "sample.jpg"
        path.write_bytes(b"fake-jpeg-content")
        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.build_reference_downloader",
            lambda: FakeDownloader(path),
        )

        with database() as session:
            campaign, _ = RrugcService(session).create_campaign(
                tenant_id="tenant-a",
                user_id="user-a",
                name="seed-ranked",
                query="candid person outdoors",
                target_count=2,
                max_scroll_batches=1,
                auto_import=False,
            )
            rows, created, _ = RrugcService(session).ingest_candidates(
                campaign=campaign,
                submissions=[
                    CandidateSubmission(
                        pin_url="https://www.pinterest.com/pin/seed-ranked/",
                        image_url="https://i.pinimg.com/seed-ranked.jpg",
                    )
                ],
            )
            assert created == 1
            candidate_id = rows[0].id
            reference = RrugcReferenceAssetModel(
                tenant_id="tenant-a",
                source_type="upload",
                source_key="seed-source",
                source_url=None,
                original_filename="seed.png",
                source_campaign_id=None,
                source_candidate_id=None,
                profile_key="realistic-person-ugc",
                reference_type="person",
                status="ready",
                content_hash="c" * 64,
                width=800,
                height=800,
                size_bytes=1234,
                image_format="PNG",
                tags_json=[],
                themes_json=[],
                quality_score=0.9,
                visual_score=None,
                context_score=None,
                usage_count=0,
                remote_file_id="seed-file",
                remote_folder_id="seed-folder",
                web_url=None,
                created_by_user_id="user-a",
            )
            session.add(reference)
            session.flush()
            session.add(
                RrugcReferenceSeedModel(
                    tenant_id="tenant-a",
                    campaign_id=campaign.id,
                    reference_asset_id=reference.id,
                    profile_key="realistic-person-ugc",
                    label="positive",
                    note=None,
                    created_by_user_id="user-a",
                )
            )
            session.commit()
            job = session.scalar(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "rrugc_candidate_analyze",
                    ProcessingJobModel.entity_id == candidate_id,
                )
            )
            assert job is not None
            claimed = ClaimedJob(
                id=job.id,
                tenant_id=job.tenant_id,
                job_type=job.job_type,
                entity_type=job.entity_type,
                entity_id=job.entity_id,
                payload=job.payload_json,
                attempt_count=job.attempt_count,
                lease_owner="test-worker",
                provider_key=job.provider_key,
            )

        async def fake_seed_signal(**kwargs):
            assert len(kwargs["seeds"]) == 1
            assert kwargs["seeds"][0].reference_asset_id == reference.id
            assert kwargs["seeds"][0].label == "positive"
            return {
                "active": True,
                "profile_key": "realistic-person-ugc",
                "positive_count": 1,
                "negative_count": 0,
                "positive_similarity": 0.9,
                "negative_similarity": None,
                "score": 0.9,
                "adjustment": 0.03,
            }

        monkeypatch.setattr(
            "app.modules.realistic_review_ugc.handler.compute_seed_visual_signal",
            fake_seed_signal,
        )
        registry = AiProviderRegistry()
        registry.register("gemini", FakeAnalysisProvider())
        context = JobHandlerContext(
            job=claimed,
            dependencies=WorkerDependencies(
                session_factory=database,
                storage_provider=FakeStorage(),
                ai_provider_registry=registry,
            ),
            shutdown_requested=Event(),
            cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger("rrugc-seed-test"), {}),
        )
        outcome = RrugcCandidateAnalyzeJobHandler()(context)
        assert outcome.outcome == JobOutcome.COMPLETED

        with database() as session:
            candidate = session.get(RrugcCandidateModel, candidate_id)
            assert candidate is not None
            assert candidate.ai_signal_json["seed_visual"]["adjustment"] == 0.03
            assert candidate.ai_signal_json["seed_visual"]["selected_sources"] == {
                "positive": {"reference_library": 1}
            }
            base_score = candidate.final_score
            assert base_score is not None

        response = api.get(
            f"/api/v1/realistic-review-ugc/campaigns/{campaign.id}/candidates"
        )
        assert response.status_code == 200
        payload = response.json()[0]
        assert payload["seed_visual_active"] is True
        assert payload["seed_visual_adjustment"] == pytest.approx(0.03)
        assert payload["seed_visual_positive_similarity"] == pytest.approx(0.9)
        assert payload["ranking_score"] == pytest.approx(min(1.0, base_score + 0.03))


def _png_bytes(width: int = 48, height: int = 32, value: int = 120) -> bytes:
    output = BytesIO()
    Image.new("RGB", (width, height), (value, 80, 40)).save(output, format="PNG")
    return output.getvalue()


def test_product_registry_crud_api(api):
    created = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={
            "sku": "hat-001",
            "name": "Forest Cap",
            "product_type": "hat",
            "color": "forest green",
            "material": "cotton twill",
            "crown_profile": "mid",
            "crown_height_mm": 118,
            "brim_style": "curved",
            "brim_length_mm": 72,
            "circumference_mm": 580,
            "logo_position": "front center",
            "fit_notes": "Structured six-panel cap.",
        },
    )
    assert created.status_code == 201
    product = created.json()
    assert product["sku"] == "HAT-001"
    assert product["status"] == "active"
    assert product["revision"] == 1
    assert product["reference_count"] == 0

    duplicate = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "HAT-001", "name": "Duplicate"},
    )
    assert duplicate.status_code == 409

    rows = api.get("/api/v1/realistic-review-ugc/products")
    assert rows.status_code == 200
    assert [row["id"] for row in rows.json()] == [product["id"]]

    updated = api.patch(
        f"/api/v1/realistic-review-ugc/products/{product['id']}",
        json={"color": "dark forest green", "fit_notes": "Updated geometry notes."},
    )
    assert updated.status_code == 200
    assert updated.json()["color"] == "dark forest green"
    assert updated.json()["revision"] == 2

    archived = api.delete(
        f"/api/v1/realistic-review-ugc/products/{product['id']}"
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"
    assert archived.json()["revision"] == 3

    active = api.get("/api/v1/realistic-review-ugc/products")
    assert active.json() == []
    all_rows = api.get(
        "/api/v1/realistic-review-ugc/products?include_archived=true"
    )
    assert len(all_rows.json()) == 1


def test_product_url_import_api_builds_product_from_page_details(api, monkeypatch):
    router_module = importlib.import_module(
        "app.modules.realistic_review_ugc.router"
    )

    async def fake_fetch_product_page(_client, raw_url: str):
        return ProductPageData(
            source_url=raw_url,
            source_host="shop.example.com",
            name="Imported Forest Cap",
            sku="URL-CAP-1",
            brand="North Studio",
            description="Casual cotton baseball cap for outdoor phone-photo UGC.",
            category="Baseball Caps",
            color="Forest Green",
            material="Cotton Twill",
            price_text="29.00",
            currency="USD",
            images=[
                "https://cdn.example.com/front.jpg",
                "https://cdn.example.com/side.jpg",
            ],
            variants=[
                {
                    "source_variant_id": "shopify-forest",
                    "sku": "URL-CAP-1-GREEN",
                    "name": "Forest Green",
                    "color": "Forest Green",
                    "image_urls": ["https://cdn.example.com/front.jpg"],
                    "available": True,
                },
                {
                    "source_variant_id": "shopify-navy",
                    "sku": "URL-CAP-1-NAVY",
                    "name": "Navy",
                    "color": "Navy",
                    "image_urls": ["https://cdn.example.com/navy.jpg"],
                    "available": True,
                },
            ],
        )

    monkeypatch.setattr(
        router_module,
        "fetch_product_page",
        fake_fetch_product_page,
    )

    response = api.post(
        "/api/v1/realistic-review-ugc/products/import-urls",
        json={
            "urls": ["https://shop.example.com/products/forest-cap"],
            "import_primary_image": False,
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["created"] == 1
    assert payload["updated"] == 0
    assert payload["failed"] == 0
    assert payload["items"][0]["images_found"] == 2
    product = payload["items"][0]["product"]
    assert product["sku"] == "URL-CAP-1"
    assert product["name"] == "Imported Forest Cap"
    assert product["product_type"] == "hat"
    assert product["brand"] == "North Studio"
    assert product["source_category"] == "Baseball Caps"
    assert product["source_price_text"] == "29.00"
    assert product["source_currency"] == "USD"
    assert product["source_images"] == [
        "https://cdn.example.com/front.jpg",
        "https://cdn.example.com/side.jpg",
    ]
    assert payload["items"][0]["variants_found"] == 2
    assert payload["items"][0]["variant_references_imported"] == 0
    assert len(product["variants"]) == 2
    assert [variant["color"] for variant in product["variants"]] == [
        "Forest Green",
        "Navy",
    ]
    assert product["variants"][0]["image_urls"] == [
        "https://cdn.example.com/front.jpg"
    ]

    disabled = api.patch(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/variants/{product['variants'][1]['id']}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["enabled"] is False

    product = api.get(
        f"/api/v1/realistic-review-ugc/products/{product['id']}"
    ).json()
    enabled_variant_id = next(
        variant["id"] for variant in product["variants"] if variant["enabled"]
    )

    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Imported product campaign",
            "query": "candid phone photo",
            "target_count": 5,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    ).json()
    bound = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/product",
        json={
            "product_id": product["id"],
            "variant_ids": [enabled_variant_id],
        },
    )
    assert bound.status_code == 200
    bound_payload = bound.json()
    assert bound_payload["product_source_url"] == "https://shop.example.com/products/forest-cap"
    assert bound_payload["product_brand"] == "North Studio"
    assert bound_payload["product_variant_ids"] == [enabled_variant_id]
    assert len(bound_payload["product_variants"]) == 2
    assert {
        variant["color"] for variant in bound_payload["product_variants"]
    } == {"Forest Green", "Navy"}


def test_product_reference_upload_is_versioned_and_reuses_hash(database):
    first_bytes = _png_bytes(value=100)
    second_bytes = _png_bytes(value=180)
    storage = FakeStorage()

    with database() as session:
        product = RrugcProductRegistry(session).create_product(
            tenant_id="tenant-a",
            user_id="user-a",
            request=ProductCreateRequest(
                sku="CAP-100",
                name="Everyday Cap",
                color="navy",
                crown_profile="mid",
                brim_style="curved",
            ),
        )
        registry = RrugcProductRegistry(session)

        front_v1 = asyncio.run(registry.upload_reference(
            tenant_id="tenant-a",
            user_id="user-a",
            product_id=product.id,
            view_type="front",
            original_filename="front.png",
            content=first_bytes,
            storage=storage,
        ))
        assert front_v1.version == 1
        assert front_v1.width == 48
        assert front_v1.height == 32
        assert front_v1.image_format == "PNG"
        assert front_v1.reused_storage is False
        assert storage.calls == 1

        exact_replay = asyncio.run(registry.upload_reference(
            tenant_id="tenant-a",
            user_id="user-a",
            product_id=product.id,
            view_type="front",
            original_filename="front-copy.png",
            content=first_bytes,
            storage=storage,
        ))
        assert exact_replay.id == front_v1.id
        assert storage.calls == 1

        side_same_content = asyncio.run(registry.upload_reference(
            tenant_id="tenant-a",
            user_id="user-a",
            product_id=product.id,
            view_type="side_left",
            original_filename="side.png",
            content=first_bytes,
            storage=storage,
        ))
        assert side_same_content.version == 1
        assert side_same_content.reused_storage is True
        assert side_same_content.remote_file_id == front_v1.remote_file_id
        assert storage.calls == 1

        front_v2 = asyncio.run(registry.upload_reference(
            tenant_id="tenant-a",
            user_id="user-a",
            product_id=product.id,
            view_type="front",
            original_filename="front-v2.png",
            content=second_bytes,
            storage=storage,
        ))
        assert front_v2.version == 2
        assert front_v2.id != front_v1.id
        assert storage.calls == 2

        references = RrugcRepository(session).list_product_references(
            "tenant-a", product.id
        )
        assert [(row.view_type, row.version) for row in references] == [
            ("front", 2),
            ("front", 1),
            ("side_left", 1),
        ]


def test_product_reference_upload_rejects_invalid_image(database):
    with database() as session:
        product = RrugcProductRegistry(session).create_product(
            tenant_id="tenant-a",
            user_id="user-a",
            request=ProductCreateRequest(sku="CAP-BAD", name="Bad Image Guard"),
        )
        with pytest.raises(RrugcError) as captured:
            asyncio.run(RrugcProductRegistry(session).upload_reference(
                tenant_id="tenant-a",
                user_id="user-a",
                product_id=product.id,
                view_type="front",
                original_filename="not-image.png",
                content=b"not an image",
                storage=FakeStorage(),
            ))
        assert captured.value.code == "product_reference_invalid_image"


def test_product_reference_upload_api(api, monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )
    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "CAP-API", "name": "API Cap"},
    ).json()

    response = api.post(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references",
        data={"view_type": "logo_closeup"},
        files={"file": ("logo.png", _png_bytes(), "image/png")},
    )
    assert response.status_code == 201
    reference = response.json()
    assert reference["view_type"] == "logo_closeup"
    assert reference["version"] == 1
    assert reference["remote_file_id"] == "file-1"

    listed = api.get(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references"
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    refreshed_product = api.get(
        f"/api/v1/realistic-review-ugc/products/{product['id']}"
    )
    assert refreshed_product.json()["reference_count"] == 1
    assert refreshed_product.json()["active_views"] == ["logo_closeup"]

    preview = api.get(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references/{reference['id']}/image"
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.content == storage.payload

    archived = api.delete(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references/{reference['id']}"
    )
    assert archived.status_code == 200
    assert archived.json()["status"] == "archived"


def test_reference_asset_upload_api_is_idempotent_and_previewable(api, monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )
    content = _png_bytes(value=211)

    first = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "person"},
        files={"file": ("person-ref.png", content, "image/png")},
    )
    assert first.status_code == 200
    first_payload = first.json()
    assert first_payload["created"] is True
    assert first_payload["asset"]["source_type"] == "upload"
    assert first_payload["asset"]["reference_type"] == "person"
    assert first_payload["asset"]["width"] == 48
    assert first_payload["asset"]["height"] == 32
    assert first_payload["asset"]["image_format"] == "PNG"
    assert first_payload["asset"]["original_filename"] == "person-ref.png"
    assert storage.calls == 1
    reference_id = first_payload["asset"]["id"]

    replay = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "scene"},
        files={"file": ("same-content.png", content, "image/png")},
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["asset"]["id"] == reference_id
    assert storage.calls == 1

    listed = api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"source_type": "upload"},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [reference_id]

    typed = api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"reference_type": "person"},
    )
    assert typed.status_code == 200
    assert [row["id"] for row in typed.json()] == [reference_id]
    assert api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"reference_type": "product"},
    ).json() == []

    profiled = api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"profile_key": "realistic-person-ugc"},
    )
    assert profiled.status_code == 200
    assert [row["id"] for row in profiled.json()] == [reference_id]
    assert api.get(
        "/api/v1/realistic-review-ugc/reference-assets",
        params={"profile_key": "embroidery-detail"},
    ).json() == []

    preview = api.get(
        f"/api/v1/realistic-review-ugc/reference-assets/{reference_id}/image"
    )
    assert preview.status_code == 200
    assert preview.headers["content-type"].startswith("image/png")
    assert preview.content == content


def test_reference_sets_bind_ready_assets_by_generic_role(
    api,
    database,
    monkeypatch,
):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )

    person = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "person"},
        files={
            "file": (
                "person.png",
                _png_bytes(value=101),
                "image/png",
            )
        },
    )
    product = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "product"},
        files={
            "file": (
                "product.png",
                _png_bytes(value=202),
                "image/png",
            )
        },
    )
    assert person.status_code == 200
    assert product.status_code == 200
    person_id = person.json()["asset"]["id"]
    product_id = product.json()["asset"]["id"]

    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Generic role set",
            "query": "candid phone photo",
            "target_count": 2,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    )
    assert campaign.status_code == 201
    campaign_id = campaign.json()["id"]

    created = api.post(
        "/api/v1/realistic-review-ugc/reference-sets",
        json={
            "name": "Generation refs",
            "campaign_id": campaign_id,
            "profile_key": "realistic-person-ugc",
            "description": "Role-based skill inputs",
        },
    )
    assert created.status_code == 201
    reference_set = created.json()
    reference_set_id = reference_set["id"]
    assert reference_set["items"] == []

    person_binding = api.post(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}/items",
        json={
            "reference_asset_id": person_id,
            "role": "Person / Scene",
            "position": 5,
        },
    )
    assert person_binding.status_code == 200
    assert person_binding.json()["created"] is True
    person_item_id = person_binding.json()["item"]["id"]
    assert person_binding.json()["item"]["role"] == "person_scene"

    replay = api.post(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}/items",
        json={
            "reference_asset_id": person_id,
            "role": "person scene",
            "position": 9,
            "note": "Prefer this framing",
        },
    )
    assert replay.status_code == 200
    assert replay.json()["created"] is False
    assert replay.json()["item"]["id"] == person_item_id
    assert replay.json()["item"]["position"] == 9
    assert replay.json()["item"]["note"] == "Prefer this framing"

    product_binding = api.post(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}/items",
        json={
            "reference_asset_id": product_id,
            "role": "product-front",
            "position": 1,
        },
    )
    assert product_binding.status_code == 200
    assert product_binding.json()["item"]["role"] == "product_front"

    fetched = api.get(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}"
    )
    assert fetched.status_code == 200
    assert [
        (item["role"], item["position"])
        for item in fetched.json()["items"]
    ] == [
        ("product_front", 1),
        ("person_scene", 9),
    ]

    global_set = api.post(
        "/api/v1/realistic-review-ugc/reference-sets",
        json={
            "name": "Reusable global refs",
            "profile_key": "realistic-person-ugc",
        },
    )
    assert global_set.status_code == 201
    global_set_id = global_set.json()["id"]

    listed = api.get(
        "/api/v1/realistic-review-ugc/reference-sets",
        params={"campaign_id": campaign_id},
    )
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [reference_set_id]

    listed_with_global = api.get(
        "/api/v1/realistic-review-ugc/reference-sets",
        params={"campaign_id": campaign_id, "include_global": True},
    )
    assert listed_with_global.status_code == 200
    assert {row["id"] for row in listed_with_global.json()} == {
        reference_set_id,
        global_set_id,
    }

    with database() as session:
        foreign_asset = RrugcReferenceAssetModel(
            tenant_id="tenant-b",
            source_type="upload",
            source_key="tenant-b-reference",
            source_url=None,
            original_filename="tenant-b.png",
            source_campaign_id=None,
            source_candidate_id=None,
            profile_key=None,
            reference_type="person",
            status="ready",
            content_hash="e" * 64,
            width=48,
            height=32,
            size_bytes=100,
            image_format="PNG",
            tags_json=[],
            themes_json=[],
            quality_score=None,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="tenant-b-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-b",
        )
        session.add(foreign_asset)
        session.commit()
        foreign_id = foreign_asset.id

    cross_tenant = api.post(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}/items",
        json={
            "reference_asset_id": foreign_id,
            "role": "person",
        },
    )
    assert cross_tenant.status_code == 404

    removed = api.delete(
        (
            f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}"
            f"/items/{person_item_id}"
        )
    )
    assert removed.status_code == 204
    remaining = api.get(
        f"/api/v1/realistic-review-ugc/reference-sets/{reference_set_id}"
    )
    assert [item["role"] for item in remaining.json()["items"]] == [
        "product_front"
    ]


def test_reference_asset_upload_reuses_product_reference_storage(api, monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )
    content = _png_bytes(value=177)
    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "CAP-REF-REUSE", "name": "Reference Reuse Cap"},
    ).json()
    product_reference = api.post(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references",
        data={"view_type": "front"},
        files={"file": ("front.png", content, "image/png")},
    )
    assert product_reference.status_code == 201
    assert storage.calls == 1

    generic_reference = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "product"},
        files={"file": ("same-front.png", content, "image/png")},
    )
    assert generic_reference.status_code == 200
    payload = generic_reference.json()
    assert payload["created"] is True
    assert payload["asset"]["source_type"] == "upload"
    assert payload["asset"]["remote_file_id"] == product_reference.json()["remote_file_id"]
    assert storage.calls == 1



def test_reference_seeds_are_campaign_and_profile_scoped(api, monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )
    uploaded = api.post(
        "/api/v1/realistic-review-ugc/reference-assets/uploads",
        data={"reference_type": "person"},
        files={
            "file": (
                "seed-person.png",
                _png_bytes(value=188),
                "image/png",
            )
        },
    )
    assert uploaded.status_code == 200
    reference_id = uploaded.json()["asset"]["id"]

    def create_campaign(name: str) -> dict:
        response = api.post(
            "/api/v1/realistic-review-ugc/campaigns",
            json={
                "name": name,
                "query": "candid phone photo",
                "target_count": 2,
                "max_scroll_batches": 1,
                "auto_import": False,
            },
        )
        assert response.status_code == 201
        return response.json()

    campaign_a = create_campaign("Seed scope A")
    campaign_b = create_campaign("Seed scope B")

    positive = api.put(
        (
            f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}"
            f"/reference-seeds/{reference_id}"
        ),
        json={"label": "positive"},
    )
    assert positive.status_code == 200
    seed_id = positive.json()["id"]
    assert positive.json()["label"] == "positive"
    assert positive.json()["profile_key"] == "realistic-person-ugc"

    updated = api.put(
        (
            f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}"
            f"/reference-seeds/{reference_id}"
        ),
        json={
            "label": "negative",
            "note": "Wrong style for this campaign",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["id"] == seed_id
    assert updated.json()["label"] == "negative"
    assert updated.json()["note"] == "Wrong style for this campaign"

    alternate_profile = api.put(
        (
            f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}"
            f"/reference-seeds/{reference_id}"
        ),
        json={
            "label": "positive",
            "profile_key": "embroidery-detail",
        },
    )
    assert alternate_profile.status_code == 200
    assert alternate_profile.json()["id"] != seed_id

    other_campaign = api.put(
        (
            f"/api/v1/realistic-review-ugc/campaigns/{campaign_b['id']}"
            f"/reference-seeds/{reference_id}"
        ),
        json={"label": "positive"},
    )
    assert other_campaign.status_code == 200
    assert other_campaign.json()["label"] == "positive"

    default_rows = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}/reference-seeds"
    )
    assert default_rows.status_code == 200
    assert len(default_rows.json()) == 1
    assert default_rows.json()[0]["label"] == "negative"

    detail_rows = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}/reference-seeds",
        params={"profile_key": "embroidery-detail"},
    )
    assert detail_rows.status_code == 200
    assert len(detail_rows.json()) == 1
    assert detail_rows.json()[0]["label"] == "positive"

    cleared = api.delete(
        (
            f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}"
            f"/reference-seeds/{reference_id}"
        )
    )
    assert cleared.status_code == 204
    after_clear = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}/reference-seeds"
    )
    assert after_clear.status_code == 200
    assert after_clear.json() == []

    still_detail = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_a['id']}/reference-seeds",
        params={"profile_key": "embroidery-detail"},
    )
    assert len(still_detail.json()) == 1
    still_other_campaign = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_b['id']}/reference-seeds"
    )
    assert len(still_other_campaign.json()) == 1
    assert still_other_campaign.json()[0]["label"] == "positive"

    asset = api.get(
        f"/api/v1/realistic-review-ugc/reference-assets/{reference_id}"
    )
    assert asset.status_code == 200
    assert asset.json()["status"] == "ready"


def test_skill_reference_preset_creates_role_complete_set_atomically(
    api,
    database,
    monkeypatch,
):
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork", "detail"),
        max_references=3,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._resolve_generation_skill",
        lambda _campaign, _requested: ("worker-hat-v1", manifest),
    )

    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Preset campaign",
            "query": "candid phone photo",
            "target_count": 1,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    ).json()
    with database() as session:
        product = RrugcReferenceAssetModel(
            tenant_id="tenant-a",
            source_type="upload",
            source_key="preset-product-front",
            source_url=None,
            original_filename="front.png",
            source_campaign_id=campaign["id"],
            source_candidate_id=None,
            profile_key=None,
            reference_type="product",
            status="ready",
            content_hash="a" * 64,
            width=1000,
            height=1000,
            size_bytes=1000,
            image_format="PNG",
            tags_json=[],
            themes_json=[],
            quality_score=0.9,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="preset-product-front-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-a",
        )
        artwork = RrugcReferenceAssetModel(
            tenant_id="tenant-a",
            source_type="upload",
            source_key="preset-artwork",
            source_url=None,
            original_filename="artwork.png",
            source_campaign_id=None,
            source_candidate_id=None,
            profile_key=None,
            reference_type="artwork",
            status="ready",
            content_hash="b" * 64,
            width=1000,
            height=1000,
            size_bytes=1000,
            image_format="PNG",
            tags_json=[],
            themes_json=[],
            quality_score=0.8,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="preset-artwork-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-a",
        )
        session.add_all([product, artwork])
        session.commit()
        product_id = product.id
        artwork_id = artwork.id

    created = api.post(
        "/api/v1/realistic-review-ugc/reference-sets/from-skill",
        json={
            "skill_name": "worker-hat-v1",
            "name": "Hat preset",
            "campaign_id": campaign["id"],
            "items": [
                {"role": "product-front", "reference_asset_id": product_id},
                {"role": "artwork", "reference_asset_id": artwork_id},
            ],
        },
    )
    assert created.status_code == 201
    payload = created.json()
    assert payload["campaign_id"] == campaign["id"]
    assert payload["description"] == "Generated from $worker-hat-v1 manifest roles."
    assert [(item["role"], item["position"]) for item in payload["items"]] == [
        ("product_front", 0),
        ("artwork", 1),
    ]

    missing = api.post(
        "/api/v1/realistic-review-ugc/reference-sets/from-skill",
        json={
            "skill_name": "worker-hat-v1",
            "name": "Invalid preset",
            "campaign_id": campaign["id"],
            "items": [
                {"role": "artwork", "reference_asset_id": artwork_id},
            ],
        },
    )
    assert missing.status_code == 409
    assert missing.json()["detail"]["code"] == "reference_set_skill_roles_missing"

    listed = api.get(
        "/api/v1/realistic-review-ugc/reference-sets",
        params={"campaign_id": campaign["id"]},
    ).json()
    assert [row["name"] for row in listed] == ["Hat preset"]


def test_reference_set_recommendation_uses_role_campaign_and_product_context(
    api,
    database,
    monkeypatch,
):
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork", "scene"),
        max_references=3,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._resolve_generation_skill",
        lambda _campaign, _requested: ("worker-hat-v1", manifest),
    )
    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Dog dad hat campaign",
            "query": "dog dad candid phone photo",
            "target_count": 1,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    ).json()

    with database() as session:
        campaign_row = session.get(RrugcCampaignModel, campaign["id"])
        assert campaign_row is not None
        campaign_row.product_snapshot_json = {
            "product_type": "hat",
            "sku": "HAT-CTX",
            "name": "Dog Dad Hat",
        }
        campaign_row.product_context_json = {
            "themes": ["pet", "dog"],
            "preferred_scenes": ["dog park"],
        }

        def add_asset(
            *,
            key: str,
            filename: str,
            reference_type: str,
            content_hash: str,
            quality: float,
            source_campaign_id: str | None = None,
            tags: list[str] | None = None,
            themes: list[str] | None = None,
        ) -> str:
            row = RrugcReferenceAssetModel(
                tenant_id="tenant-a",
                source_type="upload",
                source_key=key,
                source_url=None,
                original_filename=filename,
                source_campaign_id=source_campaign_id,
                source_candidate_id=None,
                profile_key=None,
                reference_type=reference_type,
                status="ready",
                content_hash=content_hash,
                width=1200,
                height=1200,
                size_bytes=1000,
                image_format="PNG",
                tags_json=tags or [],
                themes_json=themes or [],
                quality_score=quality,
                visual_score=None,
                context_score=None,
                usage_count=0,
                remote_file_id=key + "-file",
                remote_folder_id=None,
                web_url=None,
                created_by_user_id="user-a",
            )
            session.add(row)
            session.flush()
            return row.id

        campaign_front_id = add_asset(
            key="ctx-campaign-front",
            filename="hat-front.png",
            reference_type="product",
            content_hash="c" * 64,
            quality=0.55,
            source_campaign_id=campaign["id"],
            tags=["front", "hat"],
        )
        global_product_id = add_asset(
            key="ctx-global-product",
            filename="studio-product.png",
            reference_type="product",
            content_hash="d" * 64,
            quality=1.0,
            tags=["hat"],
        )
        campaign_detail_id = add_asset(
            key="ctx-campaign-front-detail",
            filename="hat-front-detail.png",
            reference_type="detail",
            content_hash="2" * 64,
            quality=1.0,
            source_campaign_id=campaign["id"],
            tags=["front", "hat", "detail"],
        )
        artwork_id = add_asset(
            key="ctx-artwork",
            filename="embroidered-logo.png",
            reference_type="artwork",
            content_hash="e" * 64,
            quality=0.8,
            tags=["logo", "dog"],
        )
        pet_scene_id = add_asset(
            key="ctx-pet-scene",
            filename="dog-park-phone.png",
            reference_type="scene",
            content_hash="f" * 64,
            quality=0.6,
            themes=["pet", "dog"],
        )
        generic_scene_id = add_asset(
            key="ctx-generic-scene",
            filename="generic-room.png",
            reference_type="scene",
            content_hash="1" * 64,
            quality=1.0,
        )
        session.commit()

    response = api.get(
        "/api/v1/realistic-review-ugc/reference-sets/recommendation",
        params={
            "campaign_id": campaign["id"],
            "skill_name": "worker-hat-v1",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["skill_name"] == "worker-hat-v1"
    assert payload["suggested_name"] == "HAT-CTX · Hat product on person"
    assert payload["complete"] is True
    assert payload["missing_required_roles"] == []

    by_role = {item["role"]: item for item in payload["items"]}
    assert by_role["product_front"]["reference_asset"]["id"] == campaign_front_id
    assert by_role["product_front"]["reference_asset"]["id"] != global_product_id
    assert by_role["product_front"]["reference_asset"]["id"] != campaign_detail_id
    assert "Same campaign" in by_role["product_front"]["reasons"]
    assert by_role["artwork"]["reference_asset"]["id"] == artwork_id
    assert by_role["scene"]["reference_asset"]["id"] == pet_scene_id
    assert by_role["scene"]["reference_asset"]["id"] != generic_scene_id
    assert any(
        reason.startswith("Context match:")
        for reason in by_role["scene"]["reasons"]
    )
    recommended_ids = [
        item["reference_asset"]["id"]
        for item in payload["items"]
        if item["reference_asset"] is not None
    ]
    assert len(recommended_ids) == len(set(recommended_ids))
    assert payload["reuse_recommendation"]["reference_set_id"] is None
    assert payload["reuse_recommendation"]["candidate_count"] == 0
    assert payload["discouraged_reference_sets"] == []


def test_reference_recommendation_learning_uses_human_review_without_breaking_type_priority():
    campaign = SimpleNamespace(
        id="campaign-current",
        name="Hat learning",
        product_snapshot_json={"product_type": "hat", "sku": "HAT-LEARN"},
        product_context_json={},
    )
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=(),
        max_references=1,
    )

    def asset(
        asset_id: str,
        *,
        reference_type: str,
        quality: float,
    ):
        return SimpleNamespace(
            id=asset_id,
            status="ready",
            archived_at=None,
            reference_type=reference_type,
            source_type="upload",
            original_filename=asset_id + ".png",
            source_key=asset_id,
            profile_key=None,
            tags_json=[],
            themes_json=[],
            source_campaign_id=None,
            quality_score=quality,
            context_score=None,
            visual_score=None,
            updated_at=None,
        )

    learned_product = asset(
        "learned-product",
        reference_type="product",
        quality=0.40,
    )
    high_quality_product = asset(
        "high-quality-product",
        reference_type="product",
        quality=1.0,
    )
    heavily_approved_detail = asset(
        "approved-detail",
        reference_type="detail",
        quality=1.0,
    )

    def reviewed_attempt(
        *,
        asset_id: str,
        reference_type: str,
        review_status: str,
        product_type: str = "hat",
    ):
        return SimpleNamespace(
            review_status=review_status,
            product_snapshot_json={"product_type": product_type},
            product_reference_snapshot_json=[
                {
                    "reference_asset_id": asset_id,
                    "role": "product_front",
                    "reference_type": reference_type,
                    "source_type": "upload",
                }
            ],
        )

    attempts = [
        *[
            reviewed_attempt(
                asset_id=learned_product.id,
                reference_type="product",
                review_status="approved",
            )
            for _ in range(6)
        ],
        *[
            reviewed_attempt(
                asset_id=high_quality_product.id,
                reference_type="product",
                review_status="rejected",
            )
            for _ in range(6)
        ],
        *[
            reviewed_attempt(
                asset_id=heavily_approved_detail.id,
                reference_type="detail",
                review_status="approved",
            )
            for _ in range(6)
        ],
        *[
            reviewed_attempt(
                asset_id=high_quality_product.id,
                reference_type="product",
                review_status="approved",
                product_type="shirt",
            )
            for _ in range(20)
        ],
    ]
    learning = build_reference_review_learning(
        campaign=campaign,
        attempts=attempts,
    )
    assert learning.review_count == 18

    result = recommend_reference_assets(
        campaign=campaign,
        manifest=manifest,
        assets=[
            learned_product,
            high_quality_product,
            heavily_approved_detail,
        ],
        review_learning=learning,
    )
    assert len(result) == 1
    recommendation = result[0]
    assert recommendation.reference_asset.id == learned_product.id
    assert recommendation.review_approved_count == 6
    assert recommendation.review_rejected_count == 0
    assert recommendation.learning_adjustment > 0
    assert any(
        reason.startswith("Human review:")
        for reason in recommendation.reasons
    )
    assert recommendation.score > 300
    assert (
        recommendation.reference_asset.id
        != heavily_approved_detail.id
    )


def test_reference_set_reuse_uses_version_safe_human_review_history():
    campaign = SimpleNamespace(
        id="campaign-current",
        name="Hat reuse",
        product_snapshot_json={"product_type": "hat", "sku": "HAT-REUSE"},
    )
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork",),
        max_references=2,
    )

    def reference_set(
        reference_set_id: str,
        *,
        campaign_id: str | None = None,
    ):
        return SimpleNamespace(
            id=reference_set_id,
            name=reference_set_id,
            campaign_id=campaign_id,
            status="active",
            archived_at=None,
            updated_at=None,
        )

    def item(role: str, reference_asset_id: str):
        return SimpleNamespace(
            role=role,
            reference_asset_id=reference_asset_id,
        )

    proven_set = reference_set("set-proven")
    mixed_set = reference_set("set-mixed", campaign_id=campaign.id)
    discouraged_set = reference_set("set-discouraged")
    mutated_set = reference_set("set-mutated")
    incompatible_set = reference_set("set-incompatible")
    proven_items = [item("product_front", "asset-proven")]
    mixed_items = [item("product_front", "asset-mixed")]
    discouraged_items = [item("product_front", "asset-discouraged")]
    mutated_items = [item("product_front", "asset-new")]
    incompatible_items = [item("scene", "asset-scene")]

    def reviewed_attempt(
        *,
        reference_set_id: str,
        reference_asset_id: str,
        review_status: str,
        role: str = "product_front",
        product_type: str = "hat",
    ):
        return SimpleNamespace(
            review_status=review_status,
            product_snapshot_json={"product_type": product_type},
            product_reference_snapshot_json=[
                {
                    "reference_set_id": reference_set_id,
                    "reference_asset_id": reference_asset_id,
                    "role": role,
                    "reference_type": "product",
                    "source_type": "upload",
                }
            ],
        )

    attempts = [
        *[
            reviewed_attempt(
                reference_set_id=proven_set.id,
                reference_asset_id="asset-proven",
                review_status="approved",
            )
            for _ in range(5)
        ],
        reviewed_attempt(
            reference_set_id=proven_set.id,
            reference_asset_id="asset-proven",
            review_status="rejected",
        ),
        *[
            reviewed_attempt(
                reference_set_id=mixed_set.id,
                reference_asset_id="asset-mixed",
                review_status="approved",
            )
            for _ in range(3)
        ],
        *[
            reviewed_attempt(
                reference_set_id=mixed_set.id,
                reference_asset_id="asset-mixed",
                review_status="rejected",
            )
            for _ in range(2)
        ],
        reviewed_attempt(
            reference_set_id=discouraged_set.id,
            reference_asset_id="asset-discouraged",
            review_status="approved",
        ),
        *[
            reviewed_attempt(
                reference_set_id=discouraged_set.id,
                reference_asset_id="asset-discouraged",
                review_status="rejected",
            )
            for _ in range(5)
        ],
        *[
            reviewed_attempt(
                reference_set_id=mutated_set.id,
                reference_asset_id="asset-old",
                review_status="rejected",
            )
            for _ in range(10)
        ],
        *[
            reviewed_attempt(
                reference_set_id=incompatible_set.id,
                reference_asset_id="asset-scene",
                review_status="approved",
                role="scene",
            )
            for _ in range(10)
        ],
        *[
            reviewed_attempt(
                reference_set_id=mixed_set.id,
                reference_asset_id="asset-mixed",
                review_status="approved",
                product_type="shirt",
            )
            for _ in range(20)
        ],
    ]
    learning = build_reference_review_learning(
        campaign=campaign,
        attempts=attempts,
    )
    recommendation = recommend_reference_set_reuse(
        campaign=campaign,
        manifest=manifest,
        reference_sets=[
            (proven_set, proven_items),
            (mixed_set, mixed_items),
            (discouraged_set, discouraged_items),
            (mutated_set, mutated_items),
            (incompatible_set, incompatible_items),
        ],
        review_learning=learning,
    )
    assert recommendation.reference_set.id == proven_set.id
    assert recommendation.review_approved_count == 5
    assert recommendation.review_rejected_count == 1
    assert recommendation.candidate_count == 4
    assert recommendation.score is not None
    assert recommendation.score > 0
    assert "Reusable global set" in recommendation.reasons

    discouraged = discouraged_reference_sets(
        campaign=campaign,
        manifest=manifest,
        reference_sets=[
            (proven_set, proven_items),
            (mixed_set, mixed_items),
            (discouraged_set, discouraged_items),
            (mutated_set, mutated_items),
            (incompatible_set, incompatible_items),
        ],
        review_learning=learning,
    )
    assert [row.reference_set.id for row in discouraged] == [discouraged_set.id]
    assert discouraged[0].score < 0
    assert discouraged[0].review_approved_count == 1
    assert discouraged[0].review_rejected_count == 5

    sparse_learning = build_reference_review_learning(
        campaign=campaign,
        attempts=[
            reviewed_attempt(
                reference_set_id=proven_set.id,
                reference_asset_id="asset-proven",
                review_status="approved",
            )
            for _ in range(2)
        ],
    )
    sparse_recommendation = recommend_reference_set_reuse(
        campaign=campaign,
        manifest=manifest,
        reference_sets=[(proven_set, proven_items)],
        review_learning=sparse_learning,
    )
    assert sparse_recommendation.reference_set is None
    assert sparse_recommendation.score is None
    assert sparse_recommendation.candidate_count == 1
    assert discouraged_reference_sets(
        campaign=campaign,
        manifest=manifest,
        reference_sets=[(proven_set, proven_items)],
        review_learning=sparse_learning,
    ) == ()


def test_generation_skill_catalog_exposes_recommended_manifest(api, monkeypatch):
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork", "detail"),
        max_references=32,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._codex_generation_skill_catalog",
        lambda _campaign=None: ([manifest], manifest),
    )

    response = api.get("/api/v1/realistic-review-ugc/generation-skills")
    assert response.status_code == 200
    payload = response.json()
    assert payload["recommended_skill_name"] == "worker-hat-v1"
    assert payload["items"][0]["recommended"] is True
    assert payload["items"][0]["required_reference_roles"] == ["product_front"]


def test_generation_attempt_rejects_reference_set_missing_skill_role(
    api,
    database,
    monkeypatch,
):
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork",),
        max_references=32,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._resolve_generation_skill",
        lambda _campaign, _requested: ("worker-hat-v1", manifest),
    )

    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "CAP-ROLE-GUARD", "name": "Role Guard Cap"},
    ).json()
    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Role guard",
            "query": "candid portrait",
            "target_count": 1,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    ).json()
    campaign_id = campaign["id"]
    assert api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/product",
        json={"product_id": product["id"]},
    ).status_code == 200

    with database() as session:
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="d" * 64,
            pin_url="https://www.pinterest.com/pin/role-guard/",
            image_url="https://i.pinimg.com/role-guard.jpg",
            status="drive_ready",
            remote_file_id="person-role-guard",
        )
        reference_set = RrugcReferenceSetModel(
            tenant_id="tenant-a",
            name="Artwork only",
            campaign_id=campaign_id,
            profile_key=None,
            description=None,
            status="active",
            created_by_user_id="user-a",
        )
        artwork = RrugcReferenceAssetModel(
            tenant_id="tenant-a",
            source_type="upload",
            source_key="role-guard-artwork",
            source_url=None,
            original_filename="artwork.png",
            source_campaign_id=campaign_id,
            source_candidate_id=None,
            profile_key=None,
            reference_type="artwork",
            status="ready",
            content_hash="e" * 64,
            width=1000,
            height=1000,
            size_bytes=1000,
            image_format="PNG",
            tags_json=[],
            themes_json=[],
            quality_score=None,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="role-guard-artwork-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-a",
        )
        session.add_all([candidate, reference_set, artwork])
        session.flush()
        session.add(
            RrugcReferenceSetItemModel(
                tenant_id="tenant-a",
                reference_set_id=reference_set.id,
                reference_asset_id=artwork.id,
                role="artwork",
                position=0,
                note=None,
            )
        )
        session.commit()
        candidate_id = candidate.id
        reference_set_id = reference_set.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/generation-attempts",
        json={"reference_set_id": reference_set_id},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reference_set_skill_roles_missing"
    assert "product_front" in response.json()["detail"]["message"]


def test_generation_attempt_snapshots_generic_reference_set_roles(
    api,
    database,
    monkeypatch,
):
    manifest = CodexSkillManifest(
        schema_version=1,
        skill_name="worker-hat-v1",
        display_name="Hat product on person",
        description="test",
        workflows=("rrugc_generate",),
        product_types=("hat",),
        required_reference_roles=("product_front",),
        optional_reference_roles=("artwork",),
        max_references=32,
    )
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._resolve_generation_skill",
        lambda _campaign, _requested: ("worker-hat-v1", manifest),
    )

    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "CAP-REFSET", "name": "Reference Set Cap"},
    ).json()
    campaign = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Reference set generation",
            "query": "candid portrait",
            "target_count": 1,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    ).json()
    campaign_id = campaign["id"]
    bound = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/product",
        json={"product_id": product["id"]},
    )
    assert bound.status_code == 200
    assert bound.json()["generation_ready"] is False

    with database() as session:
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="a" * 64,
            pin_url="https://www.pinterest.com/pin/refset-generation/",
            image_url="https://i.pinimg.com/refset-generation.jpg",
            status="drive_ready",
            remote_file_id="person-refset-file",
        )
        session.add(candidate)
        reference_set = RrugcReferenceSetModel(
            tenant_id="tenant-a",
            name="Role refs",
            campaign_id=campaign_id,
            profile_key="realistic-person-ugc",
            description=None,
            status="active",
            created_by_user_id="user-a",
        )
        session.add(reference_set)
        session.flush()
        product_asset = RrugcReferenceAssetModel(
            tenant_id="tenant-a",
            source_type="upload",
            source_key="refset-product",
            source_url=None,
            original_filename="product-front.png",
            source_campaign_id=campaign_id,
            source_candidate_id=None,
            profile_key=None,
            reference_type="product",
            status="ready",
            content_hash="b" * 64,
            width=1200,
            height=1200,
            size_bytes=1000,
            image_format="PNG",
            tags_json=[],
            themes_json=[],
            quality_score=None,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="refset-product-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-a",
        )
        artwork_asset = RrugcReferenceAssetModel(
            tenant_id="tenant-a",
            source_type="pinterest",
            source_key="refset-artwork",
            source_url="https://www.pinterest.com/pin/refset-artwork/",
            original_filename=None,
            source_campaign_id=campaign_id,
            source_candidate_id=None,
            profile_key=None,
            reference_type="artwork",
            status="ready",
            content_hash="c" * 64,
            width=900,
            height=900,
            size_bytes=900,
            image_format="JPEG",
            tags_json=[],
            themes_json=[],
            quality_score=None,
            visual_score=None,
            context_score=None,
            usage_count=0,
            remote_file_id="refset-artwork-file",
            remote_folder_id=None,
            web_url=None,
            created_by_user_id="user-a",
        )
        session.add_all([product_asset, artwork_asset])
        session.flush()
        session.add_all([
            RrugcReferenceSetItemModel(
                tenant_id="tenant-a",
                reference_set_id=reference_set.id,
                reference_asset_id=product_asset.id,
                role="product_front",
                position=0,
                note=None,
            ),
            RrugcReferenceSetItemModel(
                tenant_id="tenant-a",
                reference_set_id=reference_set.id,
                reference_asset_id=artwork_asset.id,
                role="artwork",
                position=1,
                note="Preserve this artwork",
            ),
        ])
        session.commit()
        candidate_id = candidate.id
        reference_set_id = reference_set.id

    prepared = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/generation-attempts",
        json={"reference_set_id": reference_set_id},
    )
    assert prepared.status_code == 201
    payload = prepared.json()
    assert payload["created"] is True
    attempt = payload["attempt"]
    assert attempt["reference_set_id"] == reference_set_id
    assert attempt["reference_roles"] == ["product_front", "artwork"]
    assert attempt["worker_skill_version"] == "worker-hat-v1"
    assert attempt["reference_count"] == 2
    assert attempt["reference_views"] == []

    replay = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/generation-attempts",
        json={
            "reference_set_id": reference_set_id,
            "worker_skill_version": "worker-hat-v1",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["created"] is False
    assert replay.json()["attempt"]["id"] == attempt["id"]

    with database() as session:
        persisted = session.get(RrugcGenerationAttemptModel, attempt["id"])
        assert persisted is not None
        snapshots = list(persisted.product_reference_snapshot_json)
        assert [row["role"] for row in snapshots] == ["product_front", "artwork"]
        assert [row["content_type"] for row in snapshots] == [
            "image/png",
            "image/jpeg",
        ]
        assert {row["reference_set_id"] for row in snapshots} == {
            reference_set_id
        }


def test_campaign_product_binding_and_generation_attempt_provenance(api, database, monkeypatch):
    storage = FakeStorage()
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router.build_managed_storage_provider",
        lambda _settings: storage,
    )

    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={
            "sku": "CAP-GEN",
            "name": "Generation Cap",
            "color": "navy",
            "crown_profile": "mid",
            "brim_style": "curved",
        },
    ).json()
    front = api.post(
        f"/api/v1/realistic-review-ugc/products/{product['id']}/references",
        data={"view_type": "front"},
        files={"file": ("front.png", _png_bytes(value=130), "image/png")},
    )
    assert front.status_code == 201

    campaign_created = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Generation foundation",
            "query": "happy candid portrait",
            "target_count": 2,
            "max_scroll_batches": 1,
            "auto_import": False,
        },
    )
    assert campaign_created.status_code == 201
    campaign = campaign_created.json()
    token = campaign["scout_token"]

    bound = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/product",
        json={"product_id": product["id"]},
    )
    assert bound.status_code == 200
    binding = bound.json()
    assert binding["product_id"] == product["id"]
    assert binding["product_sku"] == "CAP-GEN"
    assert binding["product_revision"] == 1
    assert binding["product_reference_views"] == ["front"]
    assert binding["generation_ready"] is True
    assert binding["product_binding_stale"] is False

    submitted = api.post(
        f"/api/v1/realistic-review-ugc/scout/{campaign['id']}/candidates",
        headers={"Authorization": "Bearer " + token},
        json={"items": [{
            "pin_url": "https://www.pinterest.com/pin/444/",
            "image_url": "https://i.pinimg.com/generation-source.jpg",
        }]},
    )
    assert submitted.status_code == 200
    candidate_id = submitted.json()["items"][0]["id"]

    not_durable = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/candidates/{candidate_id}/generation-attempts",
        json={"generation_variant": 1},
    )
    assert not_durable.status_code == 409

    with database() as session:
        candidate = session.get(RrugcCandidateModel, candidate_id)
        assert candidate is not None
        candidate.status = "drive_ready"
        candidate.remote_file_id = "person-ref-file"
        session.commit()

    prepared = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/candidates/{candidate_id}/generation-attempts",
        json={
            "generation_variant": 1,
            "worker_skill_version": "worker-hat-001-v1",
        },
    )
    assert prepared.status_code == 201
    payload = prepared.json()
    assert payload["created"] is True
    attempt = payload["attempt"]
    assert attempt["status"] == "prepared"
    assert attempt["product_sku"] == "CAP-GEN"
    assert attempt["product_revision"] == 1
    assert attempt["reference_views"] == ["front"]
    assert attempt["worker_skill_version"] == "worker-hat-001-v1"

    replay = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/candidates/{candidate_id}/generation-attempts",
        json={
            "generation_variant": 1,
            "worker_skill_version": "worker-hat-001-v1",
        },
    )
    assert replay.status_code == 201
    assert replay.json()["created"] is False
    assert replay.json()["attempt"]["id"] == attempt["id"]

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.router._rrugc_generation_capability",
        lambda *_args: SimpleNamespace(available=True, reason=None),
    )
    queued = api.post(
        f"/api/v1/realistic-review-ugc/generation-attempts/{attempt['id']}/execute",
        json={},
    )
    assert queued.status_code == 202
    assert queued.json()["status"] == "queued"
    assert queued.json()["provider"] == "gemini"
    assert queued.json()["provider_model"] == "gemini-3.1-flash-image"
    assert queued.json()["processing_job_id"]

    replay_queue = api.post(
        f"/api/v1/realistic-review-ugc/generation-attempts/{attempt['id']}/execute",
        json={},
    )
    assert replay_queue.status_code == 202
    assert replay_queue.json()["processing_job_id"] == queued.json()["processing_job_id"]
    with database() as session:
        jobs = list(session.scalars(
            select(ProcessingJobModel).where(
                ProcessingJobModel.job_type == "rrugc_generate",
                ProcessingJobModel.entity_id == attempt["id"],
            )
        ))
        assert len(jobs) == 1
        assert jobs[0].provider_key == "gemini"
        assert jobs[0].payload_json["generation_attempt_id"] == attempt["id"]

    updated = api.patch(
        f"/api/v1/realistic-review-ugc/products/{product['id']}",
        json={"color": "forest green"},
    )
    assert updated.status_code == 200
    assert updated.json()["revision"] == 2

    stale = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}"
    )
    assert stale.status_code == 200
    assert stale.json()["product_revision"] == 1
    assert stale.json()["product_binding_stale"] is True
    assert stale.json()["generation_ready"] is False

    stale_prepare = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/candidates/{candidate_id}/generation-attempts",
        json={
            "generation_variant": 2,
            "worker_skill_version": "worker-hat-001-v1",
        },
    )
    assert stale_prepare.status_code == 409
    assert stale_prepare.json()["detail"]["code"] == "campaign_product_binding_stale"

    refreshed = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/product",
        json={"product_id": product["id"]},
    )
    assert refreshed.status_code == 200
    assert refreshed.json()["product_revision"] == 2
    assert refreshed.json()["product_binding_stale"] is False

    second = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/candidates/{candidate_id}/generation-attempts",
        json={
            "generation_variant": 1,
            "worker_skill_version": "worker-hat-001-v1",
        },
    )
    assert second.status_code == 201
    assert second.json()["created"] is True
    assert second.json()["attempt"]["id"] != attempt["id"]
    assert second.json()["attempt"]["product_revision"] == 2

    attempts = api.get(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign['id']}/generation-attempts"
    )
    assert attempts.status_code == 200
    rows = attempts.json()
    assert len(rows) == 2
    assert {row["product_revision"] for row in rows} == {1, 2}

    with database() as session:
        persisted = list(session.scalars(select(RrugcGenerationAttemptModel)))
        assert len(persisted) == 2
        first = next(row for row in persisted if row.id == attempt["id"])
        assert first.product_snapshot_json["color"] == "navy"
        assert first.product_revision == 1


def test_generation_attempt_requires_bound_product_front_reference(api, database):
    product = api.post(
        "/api/v1/realistic-review-ugc/products",
        json={"sku": "CAP-NOREF", "name": "No front ref"},
    ).json()
    campaign_created = api.post(
        "/api/v1/realistic-review-ugc/campaigns",
        json={
            "name": "Needs reference",
            "query": "portrait",
            "target_count": 1,
            "max_scroll_batches": 1,
        },
    ).json()
    campaign_id = campaign_created["id"]
    bound = api.put(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/product",
        json={"product_id": product["id"]},
    )
    assert bound.status_code == 200
    assert bound.json()["generation_ready"] is False

    with database() as session:
        row = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign_id,
            source_key="f" * 64,
            pin_url="https://www.pinterest.com/pin/991/",
            image_url="https://i.pinimg.com/no-ref-source.jpg",
            status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(row)
        session.commit()
        candidate_id = row.id

    response = api.post(
        f"/api/v1/realistic-review-ugc/campaigns/{campaign_id}/candidates/{candidate_id}/generation-attempts",
        json={},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "product_reference_incomplete"


def test_rrugc_generation_enqueue_uses_codex_provider_when_configured(
    database,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.generation.get_settings",
        lambda: SimpleNamespace(
            RRUGC_IMAGE_GENERATION_PROVIDER="codex",
            CODEX_IMAGE_MODEL="",
        ),
    )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="codex enqueue",
            query="review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-CODEX-QUEUE",
            name="Codex queue cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="8" * 64,
            pin_url="https://www.pinterest.com/pin/6088/",
            image_url="https://i.pinimg.com/codex-queue.jpg",
            status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=1,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "revision": 1,
            },
            product_reference_snapshot_json=[{
                "id": "front",
                "view_type": "front",
                "remote_file_id": "front-file",
            }],
            candidate_snapshot_json={},
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            prompt_text="",
            status="prepared",
            idempotency_key="codex-enqueue-attempt",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.commit()

        queued, created = RrugcGenerationFoundation(
            session
        ).enqueue_attempt(
            attempt=attempt,
            actor_id="user-a",
        )
        assert created is True
        assert queued.provider == "codex"
        assert queued.provider_model == "account-default"
        assert queued.status == "queued"
        job = session.get(ProcessingJobModel, queued.processing_job_id)
        assert job is not None
        assert job.provider_key == "codex"
        assert job.provider_scope == "image_generation"


def test_rrugc_generation_worker_completes_and_persists_drive_output(database, monkeypatch):
    output_bytes = _png_bytes(width=96, height=64, value=170)

    class FakeReferenceProvider:
        def __init__(self, *, api_key):
            assert api_key == "test-image-key"

        async def generate_from_references(self, *, person, references, prompt):
            assert person.image_bytes
            assert [item.label for item in references] == ["front"]
            assert "Create one photorealistic product-on-person edit" in prompt
            return GeneratedImageResult(
                provider="gemini",
                model="gemini-3.1-flash-image",
                image_bytes=output_bytes,
                mime_type="image/png",
                provider_request_id="provider-request-1",
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.generation_handler.GeminiReferenceImageProvider",
        FakeReferenceProvider,
    )
    monkeypatch.setattr(
        RrugcGenerateJobHandler,
        "_gemini_image_key",
        lambda self, context, settings: "test-image-key",
    )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="worker generation",
            query="review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-WORKER",
            name="Worker cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="1" * 64,
            pin_url="https://www.pinterest.com/pin/6001/",
            image_url="https://i.pinimg.com/worker-source.jpg",
            status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=1,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "product_type": "hat",
                "revision": 1,
            },
            product_reference_snapshot_json=[{
                "id": "ref-front",
                "view_type": "front",
                "version": 1,
                "content_type": "image/png",
                "remote_file_id": "product-front-file",
            }],
            candidate_snapshot_json={
                "id": candidate.id,
                "remote_file_id": "person-file",
            },
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            provider="gemini",
            provider_model="gemini-3.1-flash-image",
            prompt_text=(
                "Create one photorealistic product-on-person edit and preserve the source person."
            ),
            status="queued",
            idempotency_key="worker-attempt-key",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.commit()
        attempt_id = attempt.id

    claimed = ClaimedJob(
        id="job-generate-1",
        tenant_id="tenant-a",
        job_type="rrugc_generate",
        entity_type="rrugc_generation_attempt",
        entity_id=attempt_id,
        payload={"generation_attempt_id": attempt_id},
        attempt_count=1,
        lease_owner="test-worker",
        provider_key="gemini",
    )
    storage = FakeStorage()
    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=database,
            storage_provider=storage,
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("rrugc-generation-test"), {}),
    )
    settings = SimpleNamespace(
        IMAGE_GENERATION_ENABLED=True,
        GEMINI_IMAGE_GENERATION_ENABLED=True,
        GEMINI_IMAGE_API_KEY="test-image-key",
    )
    outcome = RrugcGenerateJobHandler(settings)(context)
    assert outcome.outcome == JobOutcome.COMPLETED
    assert storage.payload == output_bytes
    assert storage.input.asset_id == f"rrugc-generation:{attempt_id}"

    with database() as session:
        persisted = session.get(RrugcGenerationAttemptModel, attempt_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.provider == "gemini"
        assert persisted.provider_model == "gemini-3.1-flash-image"
        assert persisted.provider_request_id == "provider-request-1"
        assert persisted.output_content_type == "image/png"
        assert persisted.output_width == 96
        assert persisted.output_height == 64
        assert persisted.output_remote_file_id == "file-1"
        assert persisted.output_web_url == "https://drive.google.com/file/d/file-1/view"
        assert persisted.completed_at is not None



def test_rrugc_generation_worker_codex_completes_with_existing_output_lifecycle(
    database,
    monkeypatch,
    tmp_path,
):
    output_bytes = _png_bytes(width=104, height=72, value=190)
    captured: dict[str, object] = {}

    class FakeCodexRunner:
        def __init__(self, config):
            captured["config"] = config

        async def generate_from_references(
            self,
            *,
            attempt_id,
            person,
            references,
            prompt,
        ):
            captured["attempt_id"] = attempt_id
            captured["labels"] = [item.label for item in references]
            captured["roles"] = [item.role for item in references]
            captured["prompt"] = prompt
            return GeneratedImageResult(
                provider="codex",
                model="account-default",
                image_bytes=output_bytes,
                mime_type="image/png",
                provider_request_id="codex-thread-1",
            )

        def cleanup_attempt(self, attempt_id):
            captured["cleanup_attempt_id"] = attempt_id

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.generation_handler.CodexImageGenRunner",
        FakeCodexRunner,
    )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="codex worker generation",
            query="review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-CODEX",
            name="Codex cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="9" * 64,
            pin_url="https://www.pinterest.com/pin/6099/",
            image_url="https://i.pinimg.com/codex-source.jpg",
            status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=1,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "product_type": "hat",
                "revision": 1,
            },
            product_reference_snapshot_json=[{
                "reference_set_id": "reference-set-1",
                "reference_set_item_id": "reference-set-item-1",
                "reference_asset_id": "reference-asset-1",
                "role": "product_front",
                "reference_type": "product",
                "content_type": "image/png",
                "remote_file_id": "product-front-file",
            }],
            candidate_snapshot_json={
                "id": candidate.id,
                "remote_file_id": "person-file",
            },
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            prompt_text="Preserve the person and apply the referenced cap.",
            status="queued",
            idempotency_key="codex-worker-attempt-key",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.commit()
        attempt_id = attempt.id

    claimed = ClaimedJob(
        id="job-generate-codex-1",
        tenant_id="tenant-a",
        job_type="rrugc_generate",
        entity_type="rrugc_generation_attempt",
        entity_id=attempt_id,
        payload={"generation_attempt_id": attempt_id},
        attempt_count=1,
        lease_owner="test-worker",
        provider_key="codex",
    )
    storage = FakeStorage()
    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=database,
            storage_provider=storage,
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(
            logging.getLogger("rrugc-generation-codex-test"),
            {},
        ),
    )
    settings = SimpleNamespace(
        IMAGE_GENERATION_ENABLED=True,
        GEMINI_IMAGE_GENERATION_ENABLED=False,
        CODEX_IMAGE_GENERATION_ENABLED=True,
        RRUGC_IMAGE_GENERATION_PROVIDER="codex",
        CODEX_IMAGE_BINARY="codex",
        CODEX_IMAGE_HOME=str(tmp_path / "codex-home"),
        CODEX_IMAGE_SKILL="worker-hat-v1",
        CODEX_IMAGE_MODEL="",
        CODEX_IMAGE_TIMEOUT_SECONDS=30,
        IMAGE_GENERATION_STAGING_ROOT=str(tmp_path / "staging"),
    )

    outcome = RrugcGenerateJobHandler(settings)(context)
    assert outcome.outcome == JobOutcome.COMPLETED
    assert storage.payload == output_bytes
    assert storage.input.asset_id == f"rrugc-generation:{attempt_id}"
    assert captured["attempt_id"] == attempt_id
    assert captured["labels"] == ["product"]
    assert captured["roles"] == ["product_front"]
    assert captured["cleanup_attempt_id"] == attempt_id
    assert captured["config"].skill_name == "worker-hat-v1"

    with database() as session:
        persisted = session.get(RrugcGenerationAttemptModel, attempt_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.provider == "codex"
        assert persisted.provider_model == "account-default"
        assert persisted.provider_request_id == "codex-thread-1"
        assert persisted.output_content_type == "image/png"
        assert persisted.output_width == 104
        assert persisted.output_height == 72
        assert persisted.output_remote_file_id == "file-1"
        assert persisted.completed_at is not None


def test_rrugc_generation_worker_retryable_provider_error_returns_attempt_to_queue(database, monkeypatch):
    from app.providers.ai.gemini_image import GeminiImageProviderError

    class FailingReferenceProvider:
        def __init__(self, *, api_key):
            pass

        async def generate_from_references(self, *, person, references, prompt):
            raise GeminiImageProviderError(
                "gemini_image_provider_unavailable",
                "temporary provider failure",
                retryable=True,
            )

        async def aclose(self):
            return None

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.generation_handler.GeminiReferenceImageProvider",
        FailingReferenceProvider,
    )
    monkeypatch.setattr(
        RrugcGenerateJobHandler,
        "_gemini_image_key",
        lambda self, context, settings: "test-image-key",
    )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a", user_id="user-a", name="retry generation",
            query="review portrait", target_count=1, max_scroll_batches=1, auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a", sku="CAP-RETRY", name="Retry cap",
            created_by_user_id="user-a",
        )
        session.add(product); session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a", campaign_id=campaign.id, source_key="2" * 64,
            pin_url="https://www.pinterest.com/pin/6002/",
            image_url="https://i.pinimg.com/retry-source.jpg", status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(candidate); session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a", campaign_id=campaign.id, candidate_id=candidate.id,
            product_id=product.id, product_revision=1,
            product_snapshot_json={"id": product.id, "sku": product.sku, "name": product.name, "revision": 1},
            product_reference_snapshot_json=[{"id": "front", "view_type": "front", "remote_file_id": "front-file"}],
            candidate_snapshot_json={"id": candidate.id, "remote_file_id": "person-file"},
            generation_variant=1, worker_skill_version="worker-hat-v1",
            prompt_text="preserve person", status="queued", idempotency_key="retry-attempt-key",
            created_by_user_id="user-a",
        )
        session.add(attempt); session.commit(); attempt_id=attempt.id

    context = JobHandlerContext(
        job=ClaimedJob(
            id="job-generate-retry", tenant_id="tenant-a", job_type="rrugc_generate",
            entity_type="rrugc_generation_attempt", entity_id=attempt_id,
            payload={"generation_attempt_id": attempt_id}, attempt_count=1,
            lease_owner="test-worker", provider_key="gemini",
        ),
        dependencies=WorkerDependencies(session_factory=database, storage_provider=FakeStorage()),
        shutdown_requested=Event(), cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("rrugc-generation-retry-test"), {}),
    )
    settings = SimpleNamespace(
        IMAGE_GENERATION_ENABLED=True, GEMINI_IMAGE_GENERATION_ENABLED=True,
        GEMINI_IMAGE_API_KEY="test-image-key",
    )
    outcome = RrugcGenerateJobHandler(settings)(context)
    assert outcome.outcome == JobOutcome.RETRYABLE_FAILURE
    assert outcome.error_code == "gemini_image_provider_unavailable"
    with database() as session:
        persisted = session.get(RrugcGenerationAttemptModel, attempt_id)
        assert persisted.status == "queued"
        assert persisted.last_error_code == "gemini_image_provider_unavailable"

def test_rrugc_generation_storage_retry_reuses_staged_provider_result(database, monkeypatch):
    output_bytes = _png_bytes(width=88, height=58, value=180)
    calls = {"provider": 0}

    class CountingReferenceProvider:
        def __init__(self, *, api_key):
            assert api_key == "test-image-key"

        async def generate_from_references(self, *, person, references, prompt):
            calls["provider"] += 1
            return GeneratedImageResult(
                provider="gemini",
                model="gemini-3.1-flash-image",
                image_bytes=output_bytes,
                mime_type="image/png",
                provider_request_id="staged-request-1",
            )

        async def aclose(self):
            return None

    class FlakyStorage(FakeStorage):
        def __init__(self):
            super().__init__()
            self.fail_next_store = True

        async def store_asset(self, input):
            if self.fail_next_store:
                self.fail_next_store = False
                raise StorageProviderError(
                    "temporary managed storage failure",
                    retryable=True,
                    code="managed_storage_network_error",
                )
            return await super().store_asset(input)

    monkeypatch.setattr(
        "app.modules.realistic_review_ugc.generation_handler.GeminiReferenceImageProvider",
        CountingReferenceProvider,
    )
    monkeypatch.setattr(
        RrugcGenerateJobHandler,
        "_gemini_image_key",
        lambda self, context, settings: "test-image-key",
    )

    with database() as session:
        campaign, _ = RrugcService(session).create_campaign(
            tenant_id="tenant-a",
            user_id="user-a",
            name="staged retry generation",
            query="review portrait",
            target_count=1,
            max_scroll_batches=1,
            auto_import=False,
        )
        product = RrugcProductModel(
            tenant_id="tenant-a",
            sku="CAP-STAGE",
            name="Stage cap",
            created_by_user_id="user-a",
        )
        session.add(product)
        session.flush()
        candidate = RrugcCandidateModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            source_key="3" * 64,
            pin_url="https://www.pinterest.com/pin/6003/",
            image_url="https://i.pinimg.com/stage-source.jpg",
            status="drive_ready",
            remote_file_id="person-file",
        )
        session.add(candidate)
        session.flush()
        attempt = RrugcGenerationAttemptModel(
            tenant_id="tenant-a",
            campaign_id=campaign.id,
            candidate_id=candidate.id,
            product_id=product.id,
            product_revision=1,
            product_snapshot_json={
                "id": product.id,
                "sku": product.sku,
                "name": product.name,
                "revision": 1,
            },
            product_reference_snapshot_json=[{
                "id": "front",
                "view_type": "front",
                "content_type": "image/png",
                "remote_file_id": "front-file",
            }],
            candidate_snapshot_json={
                "id": candidate.id,
                "remote_file_id": "person-file",
            },
            generation_variant=1,
            worker_skill_version="worker-hat-v1",
            provider="gemini",
            provider_model="gemini-3.1-flash-image",
            prompt_text="preserve person",
            status="queued",
            idempotency_key="stage-retry-attempt-key",
            created_by_user_id="user-a",
        )
        session.add(attempt)
        session.commit()
        attempt_id = attempt.id

    storage = FlakyStorage()
    with TemporaryDirectory() as temp:
        settings = SimpleNamespace(
            IMAGE_GENERATION_ENABLED=True,
            GEMINI_IMAGE_GENERATION_ENABLED=True,
            GEMINI_IMAGE_API_KEY="test-image-key",
            IMAGE_GENERATION_STAGING_ROOT=temp,
        )

        def context():
            return JobHandlerContext(
                job=ClaimedJob(
                    id="job-generate-stage-retry",
                    tenant_id="tenant-a",
                    job_type="rrugc_generate",
                    entity_type="rrugc_generation_attempt",
                    entity_id=attempt_id,
                    payload={"generation_attempt_id": attempt_id},
                    attempt_count=1,
                    lease_owner="test-worker",
                    provider_key="gemini",
                ),
                dependencies=WorkerDependencies(
                    session_factory=database,
                    storage_provider=storage,
                ),
                shutdown_requested=Event(),
                cancellation_requested=Event(),
                logger=logging.LoggerAdapter(
                    logging.getLogger("rrugc-generation-stage-retry-test"), {}
                ),
            )

        first = RrugcGenerateJobHandler(settings)(context())
        assert first.outcome == JobOutcome.RETRYABLE_FAILURE
        assert first.error_code == "managed_storage_network_error"
        assert calls["provider"] == 1
        staged = Path(temp) / "rrugc" / f"{attempt_id}.result"
        assert staged.is_file()

        second = RrugcGenerateJobHandler(settings)(context())
        assert second.outcome == JobOutcome.COMPLETED
        assert calls["provider"] == 1
        assert storage.payload == output_bytes
        assert not staged.exists()

    with database() as session:
        persisted = session.get(RrugcGenerationAttemptModel, attempt_id)
        assert persisted is not None
        assert persisted.status == "completed"
        assert persisted.provider_request_id == "staged-request-1"
        assert persisted.output_remote_file_id == "file-1"


def test_reference_borderline_phone_photo_requires_review():
    decision = evaluate_reference(
        reference_document(
            phone_authenticity_score=0.50,
            mobile_ugc_score=0.65,
            quality_score=0.70,
            product_fit_score=0.65,
            ai_risk_score=0.10,
            artistic_editorial_risk=0.30,
        ),
        ReferenceFilterPolicy(),
    )
    assert decision.status == "needs_review"
    assert decision.reject_reason == "AUTO_APPROVE_UNCERTAIN"


def test_reference_high_confidence_phone_photo_auto_approves():
    decision = evaluate_reference(
        reference_document(
            phone_authenticity_score=0.85,
            mobile_ugc_score=0.85,
            quality_score=0.80,
            product_fit_score=0.80,
            ai_risk_score=0.05,
            artistic_editorial_risk=0.10,
        ),
        ReferenceFilterPolicy(),
    )
    assert decision.status == "approved"
    assert decision.reject_reason is None


def test_manual_real_label_bypasses_uncertain_auto_approve_gate():
    decision = evaluate_reference(
        reference_document(
            phone_authenticity_score=0.50,
            mobile_ugc_score=0.65,
            quality_score=0.70,
            product_fit_score=0.65,
            ai_risk_score=0.10,
            artistic_editorial_risk=0.30,
        ),
        ReferenceFilterPolicy(),
        manual_ai_label="real",
    )
    assert decision.status == "approved"


def test_visual_fingerprint_matches_resized_and_cropped_reposts():
    from app.modules.realistic_review_ugc.visual_dedupe import (
        is_visual_near_duplicate,
        visual_fingerprints,
    )

    base = Image.new("RGB", (240, 320), "white")
    for x in range(20, 220):
        for y in range(30, 290):
            if (int(x / 24) + int(y / 31)) % 3 == 0:
                base.putpixel((x, y), (30, 80, 160))
    original = BytesIO()
    base.save(original, format="JPEG", quality=92)
    repost = BytesIO()
    base.crop((10, 14, 230, 306)).resize((440, 584)).save(repost, format="JPEG", quality=76)

    assert is_visual_near_duplicate(
        visual_fingerprints(original.getvalue()),
        visual_fingerprints(repost.getvalue()),
    )


def test_visual_fingerprint_rejects_invalid_bytes_without_crashing():
    from app.modules.realistic_review_ugc.visual_dedupe import visual_fingerprints

    assert visual_fingerprints(b"not-an-image") == []


def test_product_context_score_soft_ranks_and_holds_clear_mismatch():
    matching = evaluate_reference(
        reference_document(
            context_match_score=0.92,
            context_match_evidence=["dog visible beside subject in park"],
        ),
        ReferenceFilterPolicy(),
        context_matching_required=True,
    )
    mismatch = evaluate_reference(
        reference_document(
            context_match_score=0.12,
            context_match_evidence=["formal indoor event with no pet cues"],
        ),
        ReferenceFilterPolicy(),
        context_matching_required=True,
    )
    inactive = evaluate_reference(
        reference_document(context_match_score=0.12),
        ReferenceFilterPolicy(),
        context_matching_required=False,
    )

    assert matching.status == "approved"
    assert mismatch.status == "needs_review"
    assert mismatch.reject_reason == "PRODUCT_CONTEXT_LOW"
    assert matching.final_score > mismatch.final_score
    assert inactive.status == "approved"


def test_candidate_reanalysis_preserves_manual_context_feedback():
    candidate = SimpleNamespace(
        ai_signal_json={
            "scout_query": "dog owner park",
            "context_manual_label": "good",
            "context_manual_note": "Keep this lifestyle context",
        }
    )
    assessment = SimpleNamespace(
        calibrated_score=0.05,
        raw_score=0.05,
        detector_confidence=0.90,
        confirmed=False,
        signal_json={},
    )
    document = reference_document(
        context_match_score=0.88,
        context_match_evidence=["dog visible beside subject"],
    )

    RrugcCandidateAnalyzeJobHandler._apply_document(
        candidate,
        document,
        assessment,
        "approved",
        None,
        0.90,
        "gemini",
        "model",
        "hash",
        1200,
        1200,
        123456,
        "JPEG",
        ["fingerprint"],
        None,
        True,
        "visual-binding-1",
    )

    assert candidate.ai_signal_json["context_manual_label"] == "good"
    assert candidate.ai_signal_json["context_manual_note"] == "Keep this lifestyle context"
    assert candidate.ai_signal_json["context_match"]["active"] is True
    assert candidate.ai_signal_json["context_match"]["score"] == 0.88
    assert candidate.ai_signal_json["context_match"]["binding_fingerprint"] == "visual-binding-1"
    assert candidate.ai_signal_json["context_match"]["evidence"] == [
        "dog visible beside subject"
    ]


def test_context_reanalysis_backfill_is_bounded_and_skips_current_or_bad_refs():
    candidates = [
        SimpleNamespace(
            id="needs-new-context",
            status="approved",
            ai_signal_json={
                "context_match": {"binding_fingerprint": "old-binding"},
            },
        ),
        SimpleNamespace(
            id="already-current",
            status="needs_review",
            ai_signal_json={
                "context_match": {"binding_fingerprint": "new-binding"},
            },
        ),
        SimpleNamespace(
            id="manual-bad",
            status="rejected_context",
            ai_signal_json={
                "reference_manual_label": "bad",
                "context_match": {"binding_fingerprint": "old-binding"},
            },
        ),
        SimpleNamespace(
            id="context-review",
            status="rejected_context",
            ai_signal_json=None,
        ),
        SimpleNamespace(
            id="quality-fail",
            status="rejected_quality",
            ai_signal_json=None,
        ),
    ]
    commits = []
    queued = []
    service = RrugcService.__new__(RrugcService)
    service.session = SimpleNamespace(commit=lambda: commits.append(True))
    service.repository = SimpleNamespace(
        context_reanalysis_candidates=lambda *_args, **_kwargs: candidates,
    )
    service.enqueue_analysis = lambda candidate, *, increment_revision=False: queued.append(
        (candidate.id, increment_revision)
    )
    campaign = SimpleNamespace(
        tenant_id="tenant-a",
        id="campaign-a",
        discovery_mode="product_context",
    )

    count = service.enqueue_context_reanalysis(
        campaign,
        binding_fingerprint="new-binding",
        limit=24,
    )

    assert count == 2
    assert queued == [
        ("needs-new-context", True),
        ("context-review", True),
    ]
    assert commits == [True]


def test_refresh_campaign_discovery_learns_consistent_context_feedback():
    service = RrugcService.__new__(RrugcService)
    service.repository = SimpleNamespace(
        candidate_keyword_outcomes=lambda *_args, **_kwargs: [
            (
                "dog owner park candid phone photo",
                "approved",
                None,
                "good",
            ),
            (
                "dog owner park candid phone photo",
                "needs_review",
                None,
                "good",
            ),
            (
                "studio fashion portrait",
                "approved",
                None,
                "wrong",
            ),
            (
                "studio fashion portrait",
                "rejected_context",
                None,
                "wrong",
            ),
        ],
    )
    campaign = SimpleNamespace(
        tenant_id="tenant-a",
        id="campaign-a",
        name="Pet portrait",
        query="pet lifestyle",
        search_query_anchors_json=["pet lifestyle"],
        search_queries_json=["pet lifestyle"],
        discovery_mode="product_context",
        product_snapshot_json={"name": "Custom dog portrait cap"},
        product_reference_snapshot_json=[],
        product_context_json={"auto_context": True},
        reject_headwear=False,
    )

    refreshed = service.refresh_campaign_discovery(
        campaign,
        commit=False,
    )

    learning = refreshed.product_context_json["feedback_learning"]
    assert learning["active"] is True
    assert learning["promoted_queries"] == [
        "dog owner park candid phone photo"
    ]
    assert learning["suppressed_queries"] == [
        "studio fashion portrait"
    ]
    assert "dog owner park candid phone photo" in refreshed.search_queries_json
    assert "studio fashion portrait" not in refreshed.search_queries_json

def test_scout_operations_summary_reports_live_job_counts_without_leaking_keys(api, database, monkeypatch):
    from app.modules.ai_operations.credential_model import CreativeAiCredentialModel
    from app.modules.realistic_review_ugc.model import (
        RrugcScoutQueryModel, RrugcScoutMetricCycleModel,
    )
    from app.modules.realistic_review_ugc.maintenance import RrugcMaintenanceService

    now = datetime.now(timezone.utc)
    with database() as session:
        CreativeAiCredentialModel.__table__.create(
            bind=session.get_bind(), checkfirst=True,
        )
        RrugcScoutQueryModel.__table__.create(
            bind=session.get_bind(), checkfirst=True,
        )
        RrugcScoutMetricCycleModel.__table__.create(
            bind=session.get_bind(), checkfirst=True,
        )
        agent, token = RrugcAutoScoutService(session).create_agent(
            tenant_id="tenant-a", user_id="user-a", name="Scout Manager jobs"
        )
        session.add_all([
            RrugcScoutQueryModel(
                tenant_id="tenant-a", query="Funny hat quotes",
                query_normalized="funny hat quotes", lane="product",
                claimed_by_agent_id=agent.id, lease_token="owned",
                lease_expires_at=now + timedelta(minutes=10),
            ),
            RrugcScoutQueryModel(
                tenant_id="tenant-b", query="Private tenant only",
                query_normalized="private tenant only", lane="product",
                completed_cycles=999,
            ),
        ])
        session.commit()
        agent_id = agent.id
    monkeypatch.setattr(RrugcMaintenanceService, "gemini_available_credential_count",
                        lambda self, tenant_id, now: 1)
    url = f"/api/v1/realistic-review-ugc/scout-agents/{agent_id}/operations-summary"
    response = api.get(url, headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["keyword"]["active_searches"] == 1
    assert body["keyword"]["total_queries"] == 1
    assert body["keyword"]["completed_cycles_total"] == 0
    assert body["review"]["stage1_pending"] == 0
    assert body["gemini"]["capacity_available"] is True
    assert body["gemini"]["strategy"] == "capacity_aware_failover"
    assert "token" not in response.text.lower()
    assert api.get(url).status_code in (401, 403)
    assert api.get(url, headers={"Authorization": "Bearer invalid"}).status_code in (401, 403)

def test_stage0_suggested_card_counts_unique_keyword_and_pin_feedback_and_filters(api, database):
    now = datetime.now(timezone.utc)
    with database() as session:
        for tenant, keyword, pin in [
            ("tenant-a", "funny cowboy", "https://www.pinterest.com/pin/111/"),
            ("tenant-a", "funny western", "https://www.pinterest.com/pin/111/"),
            ("tenant-a", "classic saying", "https://www.pinterest.com/pin/222/"),
            ("tenant-a", "hidden blocked", "https://www.pinterest.com/pin/333/"),
            ("tenant-b", "secret other tenant", "https://www.pinterest.com/pin/444/"),
        ]:
            session.add(RrugcKeywordVolumeModel(
                tenant_id=tenant, keyword=keyword, keyword_normalized=keyword,
                source_pin_url=pin, provider="aebrowse_google_ads", search_volume=123,
                fetched_at=now, last_requested_at=now,
            ))
        session.add_all([
            # Two scopes for one keyword must still count one row.
            RrugcScoutFeedbackModel(
                tenant_id="tenant-a", target_type="keyword", target_key="funny cowboy",
                keyword="funny cowboy", display_value="funny cowboy", status="suggested",
                updated_by_user_id="owner",
            ),
            RrugcScoutFeedbackModel(
                tenant_id="tenant-a", target_type="pin",
                target_key="https://www.pinterest.com/pin/111/",
                keyword="funny cowboy", display_value="pin 111", status="suggested",
                updated_by_user_id="owner",
            ),
            RrugcScoutFeedbackModel(
                tenant_id="tenant-a", target_type="keyword", target_key="hidden blocked",
                keyword="hidden blocked", display_value="hidden blocked", status="blocked",
                updated_at=now - timedelta(minutes=2), updated_by_user_id="owner",
            ),
            RrugcScoutFeedbackModel(
                tenant_id="tenant-b", target_type="keyword", target_key="secret other tenant",
                keyword="secret other tenant", display_value="other tenant", status="suggested",
                updated_by_user_id="other",
            ),
        ])
        session.commit()
    route = "/api/v1/realistic-review-ugc/keyword-analysis"
    all_rows = api.get(route).json()
    assert all_rows["overview"]["total_keywords"] == 3
    assert all_rows["overview"]["suggested_keywords"] == 2
    first = api.get(route, params={"suggested_only": True, "page_size": 1}).json()
    second = api.get(route, params={"suggested_only": True, "page_size": 1, "page": 2}).json()
    assert first["total"] == second["total"] == 2
    assert first["overview"]["suggested_keywords"] == 2
    assert {first["items"][0]["keyword"], second["items"][0]["keyword"]} == {
        "funny cowboy", "funny western",
    }
    filtered = api.get(route, params={"suggested_only": True, "query": "western"}).json()
    assert filtered["total"] == 1
    assert filtered["overview"]["suggested_keywords"] == 1
    assert filtered["items"][0]["keyword"] == "funny western"
    # Clearing a suggested keyword immediately updates its card and filter.
    with database() as session:
        feedback = session.scalar(select(RrugcScoutFeedbackModel).where(
            RrugcScoutFeedbackModel.tenant_id == "tenant-a",
            RrugcScoutFeedbackModel.target_type == "pin",
        ))
        feedback.status = "neutral"
        session.commit()
    only_keyword = api.get(route, params={"suggested_only": True}).json()
    assert only_keyword["total"] == only_keyword["overview"]["suggested_keywords"] == 1
    assert only_keyword["items"][0]["keyword"] == "funny cowboy"
