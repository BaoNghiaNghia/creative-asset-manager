import logging
from contextlib import asynccontextmanager
from pathlib import Path
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
from app.modules.video_generation.gateway_client import GatewayGeneration, GatewayContent
from app.domain.providers.contracts import StoredAsset
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


def test_submit_polls_and_stays_storing_when_managed_storage_is_unavailable(setup):
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
    assert storing.outcome.value == "retryable_failure"
    assert storing.error_code == "video_generation_storage_unavailable"
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


def test_worker_context_uses_owned_async_executor(setup):
    _, settings, gateway, context, _ = setup
    class Executor:
        def __init__(self):
            self.calls = 0
        def run(self, awaitable):
            self.calls += 1
            import asyncio
            return asyncio.run(awaitable)
    executor = Executor()
    context.dependencies.resources["async_executor"] = executor
    outcome = VideoGenerateJobHandler(settings)(context)
    assert isinstance(outcome, DeferredJobOutcome)
    assert executor.calls == 1
    assert len(gateway.submit_calls) == 1


class CompletedGateway(FakeGateway):
    def __init__(self, payload=b"0000ftypisom-video-content"):
        super().__init__()
        self.status = "completed"
        self.content_calls = []
        self.payload = payload

    @asynccontextmanager
    async def open_content(self, generation_id, *, maximum_bytes):
        self.content_calls.append(generation_id)
        async def chunks():
            for index in range(0, len(self.payload), 5):
                yield self.payload[index:index + 5]
        yield GatewayContent("video/mp4", len(self.payload), chunks())


class FakeStorage:
    provider_name = "fake-storage"
    def __init__(self):
        self.calls = 0
        self.payloads = []
    async def store_asset(self, input):
        self.calls += 1
        self.payloads.append(b"".join([block async for block in input.body]))
        return StoredAsset(storage_key="fake:file", content_hash=input.content_hash, storage_provider=self.provider_name, remote_file_id="file-1", remote_folder_id="folder-1")


def test_storing_imports_same_gateway_content_dedupes_and_completes(setup, tmp_path):
    factory, settings, _, context, run_id = setup
    gateway = CompletedGateway()
    storage = FakeStorage()
    settings.VIDEO_GENERATION_STAGING_ROOT = str(tmp_path)
    context.dependencies.resources["dola_gateway_client_factory"] = lambda _: gateway
    context.dependencies.storage_provider = storage
    with factory() as session:
        run = session.get(VideoGenerationRunModel, run_id)
        run.status = "storing"
        run.gateway_generation_id = "gateway-1"
        session.commit()
    outcome = VideoGenerateJobHandler(settings)(context)
    assert outcome.outcome.value == "completed"
    assert gateway.get_calls == ["gateway-1"]
    assert gateway.content_calls == ["gateway-1"]
    assert storage.payloads == [gateway.payload]
    with factory() as session:
        run = session.get(VideoGenerationRunModel, run_id)
        assert run.status == "completed"
        assert run.output_asset_id
        assert run.completed_at is not None
    assert not (tmp_path / f"{run_id}.mp4").exists()
    retry = VideoGenerateJobHandler(settings)(context)
    assert retry.outcome.value == "completed"
    assert gateway.content_calls == ["gateway-1"]
    assert storage.calls == 1


def test_storing_without_gateway_id_never_submits(setup):
    factory, settings, gateway, context, run_id = setup
    with factory() as session:
        run = session.get(VideoGenerationRunModel, run_id)
        run.status = "storing"
        session.commit()
    outcome = VideoGenerateJobHandler(settings)(context)
    assert outcome.outcome.value == "non_retryable_failure"
    assert outcome.error_code == "video_generation_recovery_required"
    assert not gateway.submit_calls
