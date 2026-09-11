import logging
from datetime import datetime, timezone
from threading import Event

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.modules.auth_persistence import model as _auth_models  # noqa: F401
from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, DeferredJobOutcome, WorkerDependencies
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_generation.gateway_client import GatewayGeneration
from app.modules.video_generation.handler import VideoGenerateJobHandler
from app.modules.video_generation.model import VideoGenerationRunModel
from app.modules.video_generation.schema import VideoGenerationRequest
from app.modules.video_generation.service import Service


class FakeGateway:
    def __init__(self):
        self.submit_calls = []
        self.get_calls = []
        self.status = "accepted"

    async def submit(self, **kwargs):
        self.submit_calls.append(kwargs)
        return GatewayGeneration("gateway-1", self.status)

    async def get_generation(self, generation_id):
        self.get_calls.append(generation_id)
        return GatewayGeneration(generation_id, self.status)

    async def aclose(self):
        pass


@pytest.fixture
def setup():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        PROCESSING_JOBS_ENABLED=True, MANAGED_ASSET_STORAGE_ENABLED=True,
        VIDEO_GENERATION_ENABLED=True, DOLA_RENDER_GATEWAY_ENABLED=True,
        VIDEO_GENERATION_CANARY_TENANT_IDS="tenant-a", VIDEO_GENERATION_POLL_SECONDS=9,
    )
    with factory() as session:
        run = Service(session, settings).create(
            "tenant-a", "user-a",
            VideoGenerationRequest(
                client_request_id="handler", prompt="private",
                model="seedance-2.0", aspect_ratio="16:9", duration_seconds=10,
            ),
        )
        job = session.query(ProcessingJobModel).one()
        run_id, job_id = run.id, job.id
    gateway = FakeGateway()
    dependencies = WorkerDependencies(
        session_factory=factory, settings=settings,
        resources={"dola_gateway_client_factory": lambda _: gateway},
    )
    context = type("Context", (), {
        "job": ClaimedJob(
            id=job_id, tenant_id="tenant-a", job_type="video_generate",
            entity_type="video_generation_run", entity_id=run_id,
            payload={"video_generation_run_id": run_id}, attempt_count=1, lease_owner="test",
        ),
        "dependencies": dependencies, "shutdown_requested": Event(),
        "cancellation_requested": Event(), "logger": logging.getLogger("test.dg04"),
        "is_cancelled": False,
    })()
    try:
        yield factory, settings, gateway, context, run_id
    finally:
        Base.metadata.drop_all(engine)
        engine.dispose()


def test_submit_polls_and_stops_at_storing_without_content_import(setup):
    factory, settings, gateway, context, run_id = setup
    handler = VideoGenerateJobHandler(settings)
    accepted = handler(context)
    assert isinstance(accepted, DeferredJobOutcome)
    assert accepted.reason_code == "video_generation_provider_running"
    assert len(gateway.submit_calls) == 1
    assert gateway.submit_calls[0]["run_id"] == run_id
    assert gateway.submit_calls[0]["references"] == []

    gateway.status = "running"
    running = handler(context)
    assert isinstance(running, DeferredJobOutcome)
    assert len(gateway.submit_calls) == 1
    assert gateway.get_calls == ["gateway-1"]

    gateway.status = "completed"
    storing = handler(context)
    assert isinstance(storing, DeferredJobOutcome)
    assert storing.reason_code == "video_generation_content_import_pending"
    assert len(gateway.submit_calls) == 1
    with factory() as session:
        run = session.get(VideoGenerationRunModel, run_id)
        assert run.status == "storing"
        assert run.output_asset_id is None


def test_unknown_without_gateway_never_submits(setup):
    factory, settings, gateway, context, run_id = setup
    with factory() as session:
        run = session.get(VideoGenerationRunModel, run_id)
        run.status = "submission_unknown"
        session.commit()
    outcome = VideoGenerateJobHandler(settings)(context)
    assert isinstance(outcome, DeferredJobOutcome)
    assert outcome.reason_code == "video_generation_recovery_required"
    assert not gateway.submit_calls and not gateway.get_calls


def test_invalid_payload_fails_without_gateway_call(setup):
    _, settings, gateway, context, _ = setup
    context.job = ClaimedJob(
        id=context.job.id, tenant_id="tenant-a", job_type="video_generate",
        entity_type="video_generation_run", entity_id=context.job.entity_id,
        payload={"video_generation_run_id": "wrong"}, attempt_count=1, lease_owner="test",
    )
    outcome = VideoGenerateJobHandler(settings)(context)
    assert outcome.outcome.value == "non_retryable_failure"
    assert outcome.error_code == "invalid_video_generation_job"
    assert not gateway.submit_calls
