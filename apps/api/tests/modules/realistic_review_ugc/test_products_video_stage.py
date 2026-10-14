from __future__ import annotations

import hashlib
from io import BytesIO
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.auth_persistence import model as _auth_models  # noqa: F401
from app.core.config import Settings
from app.core.database import Base, get_db
from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.assets.model import AssetModel
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.video_generation.model import VideoGenerationRunModel, VideoGenerationReferenceModel
from app.modules.realistic_review_ugc.colorways import PRODUCTS_STAGE1_SELECTED_ROOT
from app.modules.realistic_review_ugc.model import RrugcColorwayJobModel, RrugcSourcePlanModel
from app.modules.realistic_review_ugc.products_video_stage import router


def settings(**changes):
    values = {
        "PROCESSING_JOBS_ENABLED": True,
        "MANAGED_ASSET_STORAGE_ENABLED": True,
        "VIDEO_GENERATION_ENABLED": True,
        "DOLA_RENDER_GATEWAY_ENABLED": True,
        "VIDEO_GENERATION_CANARY_TENANT_IDS": "tenant-a",
    }
    values.update(changes)
    return Settings(**values)


def principal(tenant="tenant-a"):
    return CurrentPrincipal(
        user_id="user-a", active_tenant_id=tenant, membership_id="membership-a",
        external_identity=None, effective_roles=frozenset({"operator"}),
        effective_permissions=frozenset({"assets.read", "assets.generate"}),
        platform_admin=False, session_id="session", authorization_source="test",
    )


@pytest.fixture
def harness(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    image = BytesIO()
    Image.new("RGB", (256, 256), "navy").save(image, format="PNG")
    content = image.getvalue()
    class Storage:
        provider_name = "google_drive_managed"
        async def open_asset(self, request):
            assert request.remote_file_id == "stage2-remote-1"
            async def body():
                yield content
            async def close():
                pass
            return StoredAssetReadStream(body=body(), close=close, content_type="image/png", size_bytes=len(content))

    monkeypatch.setattr("app.modules.realistic_review_ugc.products_video_stage.get_settings", lambda: settings())
    monkeypatch.setattr("app.modules.realistic_review_ugc.products_video_stage.build_managed_storage_provider", lambda _: Storage())

    app = FastAPI()
    app.include_router(router, prefix="/api/v1/realistic-review-ugc")
    def db():
        with factory() as session:
            yield session
    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()

    with factory() as session:
        plan = RrugcSourcePlanModel(
            id=str(uuid4()), tenant_id="tenant-a", root_folder_id=PRODUCTS_STAGE1_SELECTED_ROOT,
            source_file_id="design-1", source_relative_path="design.png", source_name="design.png",
            source_mime_type="image/png", source_revision="r1", status="ready", created_by_user_id="user-a",
        )
        job = RrugcColorwayJobModel(
            id=str(uuid4()), tenant_id="tenant-a", source_plan_id=plan.id,
            source_revision="r1", color_key="natural-navy", stock_sha256="0" * 64,
            skill_source="local", skill_name="scale-image", status="completed",
            output_remote_file_id="stage2-remote-1", output_content_type="image/png",
            output_size_bytes=len(content), created_by_user_id="user-a",
        )
        session.add_all([plan, job])
        session.commit()
        plan_id, job_id = plan.id, job.id
    try:
        with TestClient(app) as client:
            yield client, factory, plan_id, job_id, content
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def request(job_id, request_id="video-1"):
    return {
        "client_request_id": request_id,
        "colorway_job_ids": [job_id],
        "prompt": "Video of the same original embroidered cap",
        "aspect_ratio": "9:16",
        "duration_seconds": 10,
    }


def test_stage3_video_creates_real_durable_seedance_job_and_history(harness):
    api, factory, plan_id, job_id, content = harness
    created = api.post("/api/v1/realistic-review-ugc/products-stage3/videos", json=request(job_id))
    assert created.status_code == 202, created.text
    data = created.json()
    assert data["model"] == "seedance-2.5"
    assert data["status"] == "queued"
    repeat = api.post("/api/v1/realistic-review-ugc/products-stage3/videos", json=request(job_id))
    assert repeat.status_code == 202
    assert repeat.json()["id"] == data["id"]
    history = api.get("/api/v1/realistic-review-ugc/products-stage3/videos", params={"source_plan_id": plan_id})
    assert history.status_code == 200, history.text
    assert [item["id"] for item in history.json()] == [data["id"]]
    with factory() as session:
        asset = session.scalar(select(AssetModel))
        assert asset.content_hash == hashlib.sha256(content).hexdigest()
        stored = session.scalar(select(AssetStorageObjectModel))
        assert stored.remote_file_id == "stage2-remote-1"
        assert stored.storage_provider == "google_drive_managed"
        assert session.scalar(select(VideoGenerationRunModel)).provider_model == "seedance-2.5"
        assert session.scalar(select(VideoGenerationReferenceModel)).asset_id == asset.id


def test_stage3_video_rejects_unfinished_other_workflow_and_invalid_duration(harness):
    api, factory, plan_id, job_id, _ = harness
    wrong = api.post("/api/v1/realistic-review-ugc/products-stage3/videos", json=request("invalid"))
    assert wrong.status_code == 422
    too_long = api.post("/api/v1/realistic-review-ugc/products-stage3/videos", json={**request(job_id), "duration_seconds": 25})
    assert too_long.status_code == 422
    with factory() as session:
        job = session.get(RrugcColorwayJobModel, job_id)
        job.status = "running"
        session.commit()
    pending = api.post("/api/v1/realistic-review-ugc/products-stage3/videos", json=request(job_id))
    assert pending.status_code == 422
    assert pending.json()["detail"]["code"] == "stage2_photos_not_ready"
