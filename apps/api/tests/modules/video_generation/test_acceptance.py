from __future__ import annotations

from contextlib import contextmanager
from uuid import uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

# This is the canonical missing dependency in isolated metadata tests: assets'
# external_sources table has a tenant-aware FK to oauth_connections.
from app.modules.auth_persistence import model as _auth_models  # noqa: F401
from app.core.config import Settings
from app.core.database import Base, get_db
from app.modules.assets.model import AssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.video_generation.model import (
    VideoGenerationReferenceModel,
    VideoGenerationRunModel,
)
from app.modules.video_generation.repository import (
    VideoGenerationRepository,
    VideoGenerationStateError,
)
from app.modules.video_generation.router import router
from app.modules.video_generation.schema import VideoGenerationRequest
from app.modules.video_generation.service import Error, Service, enabled


def settings(**changes):
    values = dict(
        PROCESSING_JOBS_ENABLED=True,
        MANAGED_ASSET_STORAGE_ENABLED=True,
        VIDEO_GENERATION_ENABLED=True,
        DOLA_RENDER_GATEWAY_ENABLED=True,
        VIDEO_GENERATION_CANARY_TENANT_IDS="tenant-a",
    )
    values.update(changes)
    return Settings(**values)


def request(asset_ids=(), request_id="req-1", **changes):
    values = dict(
        client_request_id=request_id,
        prompt="make a film",
        model="seedance-2.0",
        aspect_ratio="16:9",
        duration_seconds=10,
        reference_asset_ids=list(asset_ids),
    )
    values.update(changes)
    return VideoGenerationRequest(**values)


def principal(tenant="tenant-a", permissions=frozenset({"assets.read", "assets.generate"})):
    return CurrentPrincipal(
        user_id="user-a", active_tenant_id=tenant, membership_id="membership-a",
        external_identity=None, effective_roles=frozenset({"operator"}),
        effective_permissions=permissions, platform_admin=False, session_id="session",
        authorization_source="test",
    )


@pytest.fixture
def database():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    try:
        yield factory
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


@pytest.fixture
def api(database, monkeypatch):
    configured = settings()
    monkeypatch.setattr("app.modules.video_generation.router.get_settings", lambda: configured)
    app = FastAPI()
    app.include_router(router)

    def db():
        with database() as session:
            yield session

    app.dependency_overrides[get_db] = db
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()
    with TestClient(app) as client:
        yield client, app, database


def add_asset(session, tenant="tenant-a", mime="image/png", size=100):
    asset = AssetModel(tenant_id=tenant, content_hash=uuid4().hex + uuid4().hex, mime_type=mime, size_bytes=size)
    session.add(asset)
    session.commit()
    return asset


def count(session, model):
    return session.scalar(select(func.count()).select_from(model))


def test_defaults_and_canary_gate_are_fail_closed():
    default = Settings()
    assert default.VIDEO_GENERATION_ENABLED is False
    assert default.DOLA_RENDER_GATEWAY_ENABLED is False
    assert enabled(default, "tenant-a") is False
    assert enabled(settings(VIDEO_GENERATION_CANARY_TENANT_IDS=""), "tenant-a") is False
    assert enabled(settings(), "tenant-a") is True
    assert enabled(settings(), "tenant-b") is False


def test_request_validation_and_zero_reference_acceptance(database):
    with database() as session:
        service = Service(session, settings())
        run = service.create("tenant-a", "user-a", request())
        assert run.status == "queued"
        assert count(session, ProcessingJobModel) == 1
    with pytest.raises(Exception):
        VideoGenerationRequest(client_request_id="x", prompt="", model="seedance-2.0", aspect_ratio="16:9", duration_seconds=10)
    with pytest.raises(Exception):
        VideoGenerationRequest(client_request_id="x", prompt="x", model="bad", aspect_ratio="16:9", duration_seconds=10)
    with pytest.raises(Exception):
        VideoGenerationRequest(client_request_id="x", prompt="x", model="seedance-2.0", aspect_ratio="bad", duration_seconds=10)
    with pytest.raises(Exception):
        VideoGenerationRequest(client_request_id="x", prompt="x", model="seedance-2.0", aspect_ratio="16:9", duration_seconds=99)
    with pytest.raises(Exception):
        VideoGenerationRequest(client_request_id="x", prompt="x", model="seedance-2.0", aspect_ratio="16:9", duration_seconds=10, reference_asset_ids=list(map(str, range(9))))


def test_reference_validation_order_and_tenant_isolation(database):
    with database() as session:
        one = add_asset(session)
        two = add_asset(session)
        run = Service(session, settings()).create("tenant-a", "user-a", request([two.id, one.id]))
        refs = list(session.scalars(select(VideoGenerationReferenceModel).where(VideoGenerationReferenceModel.run_id == run.id).order_by(VideoGenerationReferenceModel.position)))
        assert [ref.asset_id for ref in refs] == [two.id, one.id]
        with pytest.raises(Error, match="Reference asset was not found"):
            Service(session, settings()).create("tenant-a", "user-a", request(["missing"], "missing"))
        foreign = add_asset(session, tenant="tenant-b")
        with pytest.raises(Error) as failure:
            Service(session, settings()).create("tenant-a", "user-a", request([foreign.id], "foreign"))
        assert failure.value.code == "reference_asset_not_found"
        bad = add_asset(session, mime="image/gif")
        with pytest.raises(Error) as failure:
            Service(session, settings()).create("tenant-a", "user-a", request([bad.id], "mime"))
        assert failure.value.code == "reference_asset_unsupported"
        huge = add_asset(session, size=15 * 1024 * 1024 + 1)
        with pytest.raises(Error) as failure:
            Service(session, settings()).create("tenant-a", "user-a", request([huge.id], "huge"))
        assert failure.value.code == "reference_asset_too_large"


def test_idempotency_conflicts_and_job_contract(database):
    with database() as session:
        asset = add_asset(session)
        service = Service(session, settings())
        first = service.create("tenant-a", "user-a", request([asset.id], "same"))
        replay = service.create("tenant-a", "user-a", request([asset.id], "same"))
        assert first.id == replay.id
        assert count(session, VideoGenerationRunModel) == 1
        assert count(session, ProcessingJobModel) == 1
        job = session.scalar(select(ProcessingJobModel))
        assert (job.job_type, job.entity_type, job.entity_id, job.idempotency_key) == (
            "video_generate", "video_generation_run", first.id, f"video-generate:{first.id}"
        )
        assert job.payload_json == {"video_generation_run_id": first.id}
        assert (job.provider_key, job.provider_scope) == ("dola", "video_generation")
        for field, value in (("prompt", "changed"), ("model", "seedance-2.5"), ("aspect_ratio", "1:1"), ("duration_seconds", 15), ("reference_asset_ids", [])):
            values = {field: value}
            with pytest.raises(Error) as failure:
                service.create("tenant-a", "user-a", request([asset.id], "same", **values))
            assert failure.value.code == "video_generation_request_conflict"
        assert service.create("tenant-a", "user-a", request([asset.id], "new")).id != first.id


def test_atomic_reference_and_job_failures_roll_back(database, monkeypatch):
    with database() as session:
        asset = add_asset(session)
        service = Service(session, settings())
        original_add = session.add
        def fail_reference(item):
            if isinstance(item, VideoGenerationReferenceModel):
                raise RuntimeError("reference injection")
            return original_add(item)
        monkeypatch.setattr(session, "add", fail_reference)
        with pytest.raises(RuntimeError):
            service.create("tenant-a", "user-a", request([asset.id], "reference-fail"))
        assert count(session, VideoGenerationRunModel) == count(session, VideoGenerationReferenceModel) == count(session, ProcessingJobModel) == 0
    with database() as session:
        asset = add_asset(session)
        monkeypatch.setattr(ProcessingRepository, "create_job", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("job injection")))
        with pytest.raises(RuntimeError):
            Service(session, settings()).create("tenant-a", "user-a", request([asset.id], "job-fail"))
        assert count(session, VideoGenerationRunModel) == count(session, VideoGenerationReferenceModel) == count(session, ProcessingJobModel) == 0


def test_state_machine_is_explicit(database):
    with database() as session:
        run = Service(session, settings()).create("tenant-a", "user-a", request(request_id="state"))
        repo = VideoGenerationRepository(session)
        assert repo.transition(run, "preparing").status == "preparing"
        assert repo.transition(run, "submission_unknown").status == "submission_unknown"
        with pytest.raises(VideoGenerationStateError):
            repo.transition(run, "queued")
        assert repo.transition(run, "submitted").status == "submitted"
        assert repo.transition(run, "running").status == "running"
        assert repo.transition(run, "storing").status == "storing"
        assert repo.transition(run, "completed").status == "completed"
        with pytest.raises(VideoGenerationStateError):
            repo.transition(run, "running")


def test_api_auth_tenant_and_safe_responses(api):
    client, app, database = api
    disabled = client.get("/api/v1/video-generations/capabilities")
    assert disabled.status_code == 200 and disabled.json()["enabled"] is True
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(permissions=frozenset({"assets.read"}))
    assert client.post("/api/v1/video-generations", json=request().model_dump()).status_code == 403
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()
    created = client.post("/api/v1/video-generations", json=request(request_id="api").model_dump())
    assert created.status_code == 202
    generation_id = created.json()["id"]
    assert client.get(f"/api/v1/video-generations/{generation_id}").status_code == 200
    app.dependency_overrides[require_authenticated_principal] = lambda: principal("tenant-b")
    assert client.get(f"/api/v1/video-generations/{generation_id}").status_code == 404
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(permissions=frozenset({"assets.generate"}))
    assert client.get("/api/v1/video-generations/capabilities").status_code == 403


def test_deterministic_idempotency_race_and_mismatch_conflict(database, monkeypatch):
    with database() as first_session:
        existing = Service(first_session, settings()).create(
            "tenant-a", "user-a", request(request_id="race")
        )
        existing_id = existing.id

    with database() as second_session:
        service = Service(second_session, settings())
        original = service.runs.get_by_client_request
        calls = 0
        def delayed_visibility(*args):
            nonlocal calls
            calls += 1
            return None if calls <= 2 else original(*args)
        monkeypatch.setattr(service.runs, "get_by_client_request", delayed_visibility)
        replay = service.create("tenant-a", "user-a", request(request_id="race"))
        assert replay.id == existing_id
        assert calls >= 3

    with database() as inspect_session:
        assert count(inspect_session, VideoGenerationRunModel) == 1
        assert count(inspect_session, ProcessingJobModel) == 1

    with database() as mismatch_session:
        service = Service(mismatch_session, settings())
        original = service.runs.get_by_client_request
        calls = 0
        def delayed_visibility(*args):
            nonlocal calls
            calls += 1
            return None if calls <= 2 else original(*args)
        monkeypatch.setattr(service.runs, "get_by_client_request", delayed_visibility)
        with pytest.raises(Error) as failure:
            service.create(
                "tenant-a", "user-a",
                request(request_id="race", prompt="different request"),
            )
        assert failure.value.code == "video_generation_request_conflict"

    with database() as inspect_session:
        assert count(inspect_session, VideoGenerationRunModel) == 1
        assert count(inspect_session, ProcessingJobModel) == 1


def test_disabled_capability_and_create_error_are_safe(api, monkeypatch):
    client, _, _ = api
    disabled = settings(VIDEO_GENERATION_ENABLED=False, DOLA_RENDER_GATEWAY_INTERNAL_KEY="fake-internal-key")
    monkeypatch.setattr("app.modules.video_generation.router.get_settings", lambda: disabled)
    capability = client.get("/api/v1/video-generations/capabilities")
    assert capability.status_code == 200
    assert capability.json()["enabled"] is False
    response = client.post("/api/v1/video-generations", json=request(request_id="disabled").model_dump())
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "video_generation_disabled",
        "message": "Video generation is disabled.",
    }
    assert "fake-internal-key" not in response.text
