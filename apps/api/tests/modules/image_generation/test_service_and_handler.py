import asyncio
import logging
from pathlib import Path
from threading import Event

import pytest
from PIL import Image
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobHandlerResult, WorkerDependencies
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.image_generation.handler import ImageGenerateJobHandler
from app.modules.image_generation.model import ImageGenerationRunModel
from app.modules.image_generation.providers import PreparedImage
from app.modules.image_generation.repository import ImageGenerationRepository
from app.modules.image_generation.schema import SquareGenerationRequest
from app.modules.image_generation.service import ImageGenerationService, ImageGenerationServiceError
from app.modules.processing.model import ProcessingJobModel


@pytest.fixture
def database():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        engine.dispose()


def settings(tmp_path: Path, **overrides):
    values = {
        "IMAGE_GENERATION_ENABLED": True,
        "FIREFLY_IMAGE_GENERATION_ENABLED": True,
        "FIREFLY_SERVICES_CLIENT_ID": "id",
        "FIREFLY_SERVICES_CLIENT_SECRET": "secret",
        "GEMINI_IMAGE_GENERATION_ENABLED": True,
        "GEMINI_IMAGE_API_KEY": "image-key",
        "MANAGED_ASSET_STORAGE_ENABLED": True,
        "IMAGE_GENERATION_STAGING_ROOT": str(tmp_path),
    }
    values.update(overrides)
    return Settings(**values)


def seed_source(session: Session, tenant="tenant-a", *, mime="image/png"):
    asset = AssetModel(tenant_id=tenant, content_hash=("a" if tenant == "tenant-a" else "b") * 64, mime_type=mime)
    external = ExternalSourceModel(
        tenant_id=tenant,
        source_key=f"source-{tenant}",
        source_type="google_drive",
        source_metadata={"oauth_connection_id": "connection"},
    )
    session.add_all([asset, external])
    session.flush()
    source = SourceAssetModel(
        tenant_id=tenant,
        external_source_id=external.id,
        external_asset_id=f"file-{tenant}",
        filename="photo.png",
        mime_type=mime,
        size_bytes=100,
    )
    session.add(source)
    session.flush()
    session.add(AssetSourceLinkModel(tenant_id=tenant, asset_id=asset.id, source_asset_id=source.id))
    session.commit()
    return asset, source


def request(asset_id, client_id="00000000-0000-4000-8000-000000000001", provider="adobe_firefly"):
    return SquareGenerationRequest(
        source_asset_id=asset_id,
        provider=provider,
        target_size=1024,
        prompt=None,
        client_request_id=client_id,
    )


def test_service_creates_one_durable_job_and_is_idempotent(database, tmp_path):
    with database() as session:
        asset, _ = seed_source(session)
        service = ImageGenerationService(session, settings(tmp_path))
        first = service.create(tenant_id="tenant-a", user_id="user-a", request=request(asset.id))
        second = service.create(tenant_id="tenant-a", user_id="user-a", request=request(asset.id))
        assert first.created
        assert not second.created
        assert first.run.id == second.run.id
        jobs = session.query(ProcessingJobModel).filter_by(job_type="image_generate").all()
        assert len(jobs) == 1
        assert jobs[0].payload_json == {"image_generation_run_id": first.run.id}


def test_service_hides_cross_tenant_and_rejects_invalid_source(database, tmp_path):
    with database() as session:
        asset, source = seed_source(session, "tenant-a")
        seed_source(session, "tenant-b")
        service = ImageGenerationService(session, settings(tmp_path))
        with pytest.raises(ImageGenerationServiceError) as hidden:
            service.create(
                tenant_id="tenant-b",
                user_id="user-b",
                request=request(asset.id, "00000000-0000-4000-8000-000000000002"),
            )
        assert hidden.value.status_code == 404
        bad = request(asset.id, "00000000-0000-4000-8000-000000000003")
        bad.source_source_asset_id = "00000000-0000-4000-8000-000000000099"
        with pytest.raises(ImageGenerationServiceError) as missing:
            service.create(tenant_id="tenant-a", user_id="user-a", request=bad)
        assert missing.value.code == "source_asset_unavailable"


def test_service_rejects_unavailable_provider(database, tmp_path):
    with database() as session:
        asset, _ = seed_source(session)
        disabled = settings(tmp_path, FIREFLY_SERVICES_CLIENT_SECRET="")
        with pytest.raises(ImageGenerationServiceError) as raised:
            ImageGenerationService(session, disabled).create(
                tenant_id="tenant-a", user_id="user-a", request=request(asset.id)
            )
        assert raised.value.status_code == 503


def make_context(factory, run):
    job = ClaimedJob(
        id="job-1",
        tenant_id=run.tenant_id,
        job_type="image_generate",
        entity_type="image_generation_run",
        entity_id=run.id,
        payload={"image_generation_run_id": run.id},
        attempt_count=1,
        lease_owner="worker",
    )
    return JobHandlerContext(
        job=job,
        dependencies=WorkerDependencies(session_factory=factory),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
    )


def add_run(session, asset, source, *, status="preparing", provider="adobe_firefly", provider_job_id=None):
    run = ImageGenerationRunModel(
        tenant_id=asset.tenant_id,
        source_asset_id=asset.id,
        source_source_asset_id=source.id,
        provider=provider,
        provider_model=None,
        preservation_mode="strict_expand" if provider == "adobe_firefly" else "semantic_expand",
        target_width=1024,
        target_height=1024,
        source_width=1,
        source_height=1,
        normalized_width=1,
        normalized_height=1,
        left=0,
        top=0,
        right=0,
        bottom=0,
        status=status,
        provider_job_id=provider_job_id,
        provider_status_url="https://firefly-api.adobe.io/jobs/1" if provider_job_id else None,
        client_request_id="00000000-0000-4000-8000-000000000004",
        created_by_user_id="user-a",
    )
    session.add(run)
    session.commit()
    return run


def test_square_fast_path_never_calls_provider(database, tmp_path, monkeypatch):
    with database() as session:
        asset, source = seed_source(session)
        run = add_run(session, asset, source)
    handler = ImageGenerateJobHandler(settings(tmp_path))
    async def prepared(context, run_id):
        from io import BytesIO
        output = BytesIO()
        Image.new("RGB", (100, 100), "blue").save(output, "PNG")
        return PreparedImage(output.getvalue(), "image/png", 100, 100)
    async def stored(context, configured, path):
        assert path.is_file()
        return JobHandlerResult.completed()
    monkeypatch.setattr(handler, "_prepare_source", prepared)
    monkeypatch.setattr(handler, "_store", stored)
    monkeypatch.setattr(
        "app.modules.image_generation.handler.AdobeFireflySquareProvider",
        lambda **kwargs: pytest.fail("provider must not be called"),
    )
    result = asyncio.run(handler._execute(make_context(database, run)))
    assert result == JobHandlerResult.completed()
    with database() as session:
        persisted = ImageGenerationRepository(session).get("tenant-a", run.id)
        assert persisted.status == "storing"
        assert persisted.provider_model == "local-square-normalize"


def test_persisted_firefly_job_resumes_without_resubmission(database, tmp_path, monkeypatch):
    with database() as session:
        asset, source = seed_source(session)
        run = add_run(session, asset, source, status="submitted", provider_job_id="provider-job")
    handler = ImageGenerateJobHandler(settings(tmp_path))
    called = []
    async def resumed(context, configured, status_url, path):
        called.append(status_url)
        return JobHandlerResult.completed()
    monkeypatch.setattr(handler, "_resume_firefly", resumed)
    monkeypatch.setattr(
        "app.modules.image_generation.handler.AdobeFireflySquareProvider",
        lambda **kwargs: pytest.fail("provider submission must not be called"),
    )
    result = asyncio.run(handler._execute(make_context(database, run)))
    assert result == JobHandlerResult.completed()
    assert called == ["https://firefly-api.adobe.io/jobs/1"]
