from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from io import BytesIO
from threading import Event
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest
from PIL import Image
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import get_db
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobOutcome, WorkerDependencies
from app.domain.providers.contracts import (
    AiMetadataAnalysisResult,
    StorageProviderError,
    StoredAsset,
    StoredAssetReadStream,
)
from app.domain.providers.registry import AiProviderRegistry
from app.infrastructure.downloader.secure_image import DownloadedImage
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.processing.model import ProcessingJobModel
from app.modules.image_generation.providers import GeneratedImageResult
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.realistic_review_ugc.analysis import (
    ReferenceAnalysisDocument,
    ReferenceFilterPolicy,
    evaluate_reference,
)
from app.modules.realistic_review_ugc.handler import RrugcCandidateAnalyzeJobHandler
from app.modules.realistic_review_ugc.generation_handler import RrugcGenerateJobHandler
from app.modules.realistic_review_ugc.model import (
    RrugcCampaignModel,
    RrugcCandidateModel,
    RrugcGenerationAttemptModel,
    RrugcProductModel,
    RrugcProductReferenceModel,
)
from app.modules.realistic_review_ugc.product_registry import RrugcProductRegistry
from app.modules.realistic_review_ugc.repository import RrugcRepository
from app.modules.realistic_review_ugc.router import router
from app.modules.realistic_review_ugc.schema import CandidateSubmission, ProductCreateRequest
from app.modules.realistic_review_ugc.service import (
    RrugcError,
    RrugcService,
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
    RrugcProductModel.__table__.create(engine)
    RrugcProductReferenceModel.__table__.create(engine)
    RrugcCandidateModel.__table__.create(engine)
    RrugcGenerationAttemptModel.__table__.create(engine)
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


def reference_document(**overrides) -> ReferenceAnalysisDocument:
    values = {
        "people_count": 1,
        "primary_head_ratio": 0.31,
        "smile_score": 0.82,
        "head_visible": True,
        "existing_headwear": False,
        "head_occlusion": 0.08,
        "mobile_ugc_score": 0.81,
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


@pytest.mark.parametrize(
    ("overrides", "status", "reason"),
    [
        ({"people_count": 0, "primary_head_ratio": None}, "rejected_no_person", "NO_PERSON"),
        ({"primary_head_ratio": 0.12}, "rejected_head_ratio", "HEAD_RATIO_OUT_OF_RANGE"),
        ({"existing_headwear": True}, "rejected_existing_headwear", "EXISTING_HEADWEAR"),
        ({"head_occlusion": 0.55}, "rejected_head_occlusion", "HEAD_OCCLUSION"),
        ({"smile_score": 0.20}, "rejected_expression", "SMILE_SCORE_LOW"),
        ({"quality_score": 0.20}, "rejected_quality", "QUALITY_SCORE_LOW"),
        ({"ai_risk_score": 0.80}, "rejected_ai_risk", "AI_RISK_HIGH"),
    ],
)
def test_reference_policy_rejects_failed_constraints(overrides, status, reason):
    decision = evaluate_reference(reference_document(**overrides), ReferenceFilterPolicy())
    assert decision.status == status
    assert decision.reject_reason == reason


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
    assert task.json()["query"] == "happy woman casual outdoor"

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
    def __init__(self, path: Path):
        self.path = path

    @asynccontextmanager
    async def download(self, _url: str):
        yield DownloadedImage(
            path=self.path,
            content_hash="a" * 64,
            size_bytes=self.path.stat().st_size,
            width=640,
            height=480,
            image_format="JPEG",
            source_url="https://i.pinimg.com/test.jpg",
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
