import asyncio
import hashlib
import logging
from contextlib import asynccontextmanager
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobOutcome, WorkerDependencies
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_cache.cleanup import VideoCacheCleanup
from app.modules.video_cache.handler import VideoCacheFillJobHandler
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.quota import VideoCacheQuota
from app.providers.cloudflare.r2 import R2ObjectHead, R2ProviderError
from tests.modules.video_cache.test_phase2 import FakeR2, ensure, seed, settings, setup


class StreamR2(FakeR2):
    def __init__(self, *, fail_upload=False, fail_delete=False):
        super().__init__(fail_delete=fail_delete)
        self.fail_upload = fail_upload
        self.parts = []
        self.blob = None
        self.aborted = []
    async def create_multipart_upload(self, key, mime):
        return "upload-id"
    async def upload_part(self, key, upload_id, number, body):
        if self.fail_upload:
            raise R2ProviderError("transient", retryable=True)
        self.parts.append(body)
        return str(number)
    async def complete_multipart_upload(self, key, upload_id, parts):
        self.blob = b"".join(self.parts)
        return "etag"
    async def head_object(self, key):
        if self.blob is None:
            from app.providers.cloudflare.r2 import R2NotFound
            raise R2NotFound()
        return R2ObjectHead(len(self.blob), "etag")
    async def abort_multipart_upload(self, key, upload_id):
        self.aborted.append(upload_id)
    async def delete_object(self, key):
        await super().delete_object(key)
        self.blob = None


class Resolver:
    def __init__(self, data, *, transient=False):
        self.data = data
        self.transient = transient
        self.calls = []
    @asynccontextmanager
    async def open(self, *, tenant_id, source_asset_id, range_header=None):
        self.calls.append((tenant_id, source_asset_id))
        if self.transient:
            from app.modules.assets.content_resolver import SourceAssetContentTransient
            raise SourceAssetContentTransient("temporary source failure")
        async def body():
            yield self.data[:2]
            yield self.data[2:]
        yield SimpleNamespace(body=body())


def context(factory, result, provider, resolver, config, *, cancelled=False):
    with factory() as session:
        row = session.get(ProcessingJobModel, result.job_id)
        claimed = ClaimedJob(
            id=row.id, tenant_id=row.tenant_id, job_type=row.job_type,
            entity_type=row.entity_type, entity_id=row.entity_id,
            payload=row.payload_json, attempt_count=1, lease_owner="test",
        )
    cancel = Event()
    if cancelled:
        cancel.set()
    return JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=factory, settings=config,
            resources={"video_cache_r2_provider": provider, "video_cache_content_resolver": resolver},
        ),
        shutdown_requested=Event(), cancellation_requested=cancel,
        logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
    )


def test_handler_streams_exact_original_and_marks_ready(tmp_path):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    resolver = Resolver(data)
    outcome = VideoCacheFillJobHandler(config)(context(factory, admitted, r2, resolver, config))
    assert outcome.outcome == JobOutcome.COMPLETED
    assert r2.blob == data and resolver.calls == [("tenant-a", source)]
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "ready" and row.reserved_bytes == 0
        assert row.size_bytes == len(data) and row.last_accessed_at is not None
        assert row.multipart_upload_id is None
    engine.dispose()


@pytest.mark.parametrize("mutate", ["hash", "size", "mime"])
def test_handler_rejects_changed_authoritative_source(tmp_path, mutate):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    with factory() as session:
        row = session.get(AssetModel, asset)
        if mutate == "hash":
            row.content_hash = hashlib.sha256(b"changed").hexdigest()
        elif mutate == "size":
            row.size_bytes += 1
        else:
            row.mime_type = "image/jpeg"
        session.commit()
    outcome = VideoCacheFillJobHandler(config)(
        context(factory, admitted, r2, Resolver(data), config))
    assert outcome.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    assert r2.blob is None
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "failed" and row.reserved_bytes == 0
    engine.dispose()


@pytest.mark.parametrize("failure", ["source", "r2"])
def test_transient_failure_keeps_bounded_reservation_for_durable_retry(tmp_path, failure):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2(fail_upload=failure == "r2")
    admitted = ensure(factory, config, r2, asset, source, digest)
    resolver = Resolver(data, transient=failure == "source")
    outcome = VideoCacheFillJobHandler(config)(context(factory, admitted, r2, resolver, config))
    assert outcome.outcome == JobOutcome.RETRYABLE_FAILURE
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "preparing" and row.reserved_bytes == len(data)
        assert row.multipart_upload_id is None
    engine.dispose()


def test_non_retryable_r2_failure_logs_safe_phase_and_type(tmp_path, caplog):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)

    class FailingInitR2(StreamR2):
        async def create_multipart_upload(self, key, mime):
            raise R2ProviderError("https://secret.example.invalid/should-not-log", retryable=False)

    config = settings()
    provider = FailingInitR2()
    admitted = ensure(factory, config, provider, asset, source, digest)

    with caplog.at_level(logging.ERROR, logger="test"):
        outcome = VideoCacheFillJobHandler(config)(
            context(factory, admitted, provider, Resolver(data), config)
        )

    assert outcome.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    record = next(
        item for item in caplog.records
        if item.getMessage() == "video_cache_fill_exception"
    )
    assert record.phase == "r2_upload_init"
    assert record.exception_type == "R2ProviderError"
    assert record.retryable is False
    assert "secret.example.invalid" not in caplog.text
    engine.dispose()


def test_transient_source_failure_logs_source_phase_without_message(tmp_path, caplog):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, provider = settings(), StreamR2()
    admitted = ensure(factory, config, provider, asset, source, digest)

    with caplog.at_level(logging.ERROR, logger="test"):
        outcome = VideoCacheFillJobHandler(config)(
            context(
                factory,
                admitted,
                provider,
                Resolver(data, transient=True),
                config,
            )
        )

    assert outcome.outcome == JobOutcome.RETRYABLE_FAILURE
    record = next(
        item for item in caplog.records
        if item.getMessage() == "video_cache_fill_exception"
    )
    assert record.phase == "source_provider_setup"
    assert record.exception_type == "SourceAssetContentTransient"
    assert record.retryable is True
    assert "temporary source failure" not in caplog.text
    engine.dispose()


def test_cancelled_fill_does_not_leak_reservation(tmp_path):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    outcome = VideoCacheFillJobHandler(config)(
        context(factory, admitted, r2, Resolver(data), config, cancelled=True))
    assert outcome.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "failed" and row.reserved_bytes == 0
    engine.dispose()


def test_cleanup_skips_active_job_and_recovers_abandoned_preparing(tmp_path):
    from datetime import timedelta
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        row.updated_at = utcnow() - timedelta(hours=2)
        session.commit()
    cleanup = VideoCacheCleanup(factory, config, r2)
    assert asyncio.run(cleanup.run_once())["recovered"] == 0
    with factory() as session:
        job = session.get(ProcessingJobModel, admitted.job_id)
        job.status = "failed"
        row = session.scalar(select(VideoCacheObjectModel))
        row.updated_at = utcnow() - timedelta(hours=2)
        session.commit()
    assert asyncio.run(cleanup.run_once())["recovered"] == 1
    with factory() as session:
        assert session.scalar(select(VideoCacheObjectModel)) is None
    engine.dispose()

def test_source_changes_during_stream_never_becomes_ready(tmp_path):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    class MutatingResolver:
        @asynccontextmanager
        async def open(self, *, tenant_id, source_asset_id):
            async def body():
                yield data[:2]
                with factory() as session:
                    row = session.get(AssetModel, asset)
                    row.content_hash = hashlib.sha256(b"new content").hexdigest()
                    session.commit()
                yield data[2:]
            yield SimpleNamespace(body=body())
    outcome = VideoCacheFillJobHandler(config)(
        context(factory, admitted, r2, MutatingResolver(), config))
    assert outcome.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    assert r2.blob is None
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "failed"
    engine.dispose()


def test_uncertain_remote_cleanup_keeps_physical_bytes_counted(tmp_path):
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2(fail_upload=True, fail_delete=True)
    admitted = ensure(factory, config, r2, asset, source, digest)
    outcome = VideoCacheFillJobHandler(config)(
        context(factory, admitted, r2, Resolver(data), config))
    assert outcome.outcome == JobOutcome.NON_RETRYABLE_FAILURE
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "deleting" and row.reserved_bytes == 0
        assert VideoCacheQuota.usage(session).effective_bytes == len(data)
    r2.fail_delete = False
    asyncio.run(VideoCacheCleanup(factory, config, r2).run_once())
    with factory() as session:
        assert VideoCacheQuota.usage(session).effective_bytes == 0
    engine.dispose()

def test_stale_preparing_respects_active_lease_then_recovers_exhausted_lease(tmp_path):
    from datetime import timedelta
    engine, factory = setup(tmp_path)
    data = b"video original bytes"
    asset, source, digest = seed(factory, data=data)
    config, r2 = settings(), StreamR2()
    admitted = ensure(factory, config, r2, asset, source, digest)
    with factory() as session:
        job = session.get(ProcessingJobModel, admitted.job_id)
        job.status = "processing"
        job.attempt_count = job.max_attempts
        job.lease_expires_at = utcnow() + timedelta(minutes=10)
        row = session.scalar(select(VideoCacheObjectModel))
        row.updated_at = utcnow() - timedelta(hours=2)
        session.commit()
    cleanup = VideoCacheCleanup(factory, config, r2)
    assert asyncio.run(cleanup.run_once())["recovered"] == 0
    with factory() as session:
        job = session.get(ProcessingJobModel, admitted.job_id)
        job.lease_expires_at = utcnow() - timedelta(hours=2)
        row = session.scalar(select(VideoCacheObjectModel))
        row.updated_at = utcnow() - timedelta(hours=2)
        session.commit()
    assert asyncio.run(cleanup.run_once())["recovered"] == 1
    with factory() as session:
        assert session.scalar(select(VideoCacheObjectModel)) is None
    engine.dispose()
