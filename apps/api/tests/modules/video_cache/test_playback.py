from __future__ import annotations

import logging
from threading import Event
from types import SimpleNamespace

from sqlalchemy import select

from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobOutcome, WorkerDependencies
from app.modules.processing.model import ProcessingJobModel
from app.modules.video_cache.cleanup import VideoCacheCleanup
from app.modules.video_cache.model import VideoCacheObjectModel, utcnow
from app.modules.video_cache.playback import (
    PreparedPlayback,
    VideoPlaybackPreparationService,
    VideoPlaybackPrepareJobHandler,
    schedule_video_playback_prepare,
)
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.service import video_playback_key
from app.providers.cloudflare.r2 import R2NotFound, R2ObjectHead
from tests.modules.video_cache.test_phase2 import FakeR2, ensure, seed, settings, setup


def ready_original(factory, config, *, data=b"video"):
    fake = FakeR2()
    asset_id, source_id, digest = seed(factory, data=data)
    admitted = ensure(factory, config, fake, asset_id, source_id, digest)
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        row.status = "ready"
        row.size_bytes = len(data)
        row.reserved_bytes = 0
        row.cached_at = utcnow()
        row.last_accessed_at = row.cached_at
        session.commit()
        record_id = row.id
    return record_id, asset_id, source_id, digest, admitted


def playback_settings(**extra):
    values = dict(
        PROCESSING_JOBS_ENABLED=True,
        R2_VIDEO_PLAYBACK_DERIVED_ENABLED=True,
    )
    values.update(extra)
    return settings(**values)


def test_scheduler_is_idempotent_and_quota_counts_original_derivative_and_reservation(tmp_path):
    engine, factory = setup(tmp_path)
    config = playback_settings()
    record_id, _, _, digest, _ = ready_original(factory, config)

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert schedule_video_playback_prepare(session, config, row) is True
        session.commit()

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        jobs = session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type == "video_playback_prepare"
        )).all()
        assert len(jobs) == 1
        assert row.playback_status == "preparing"
        assert row.playback_reserved_bytes == len(b"video")
        usage = VideoCacheQuota.usage(session)
        assert usage.ready_bytes == len(b"video")
        assert usage.reserved_bytes == len(b"video")
        assert schedule_video_playback_prepare(session, config, row) is False

        row.playback_status = "ready"
        row.playback_r2_key = video_playback_key("tenant-a", digest)
        row.playback_kind = "faststart"
        row.playback_size_bytes = 3
        row.playback_reserved_bytes = 0
        session.commit()

    with factory() as session:
        usage = VideoCacheQuota.usage(session)
        assert usage.ready_bytes == len(b"video") + 3
        assert usage.reserved_bytes == 0
    engine.dispose()


def test_cleanup_backfills_pending_ready_rows_and_lru_skips_active_derivative(tmp_path):
    engine, factory = setup(tmp_path)
    config = playback_settings(R2_VIDEO_PLAYBACK_BACKFILL_BATCH_SIZE=1)
    record_id, _, _, digest, _ = ready_original(factory, config)
    fake = FakeR2()
    cleanup = VideoCacheCleanup(factory, config, fake)

    assert cleanup._schedule_playback_backfill() == 1
    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert row.playback_status == "preparing"
        assert row.playback_r2_key == video_playback_key("tenant-a", digest)
        jobs = session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type == "video_playback_prepare"
        )).all()
        assert len(jobs) == 1

    quota = VideoCacheQuota(factory, config, fake)
    with quota.transaction() as session:
        assert quota.claim_lru(session) is None
    engine.dispose()


def test_scheduler_skips_oversized_source_without_enqueuing_derivative(tmp_path):
    engine, factory = setup(tmp_path)
    config = playback_settings(R2_VIDEO_PLAYBACK_MAX_SOURCE_BYTES=4)
    record_id, _, _, _, _ = ready_original(factory, config)

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert schedule_video_playback_prepare(session, config, row) is True
        session.commit()

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert row.playback_status == "skipped"
        assert row.playback_error_code == "source_too_large"
        jobs = session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type == "video_playback_prepare"
        )).all()
        assert jobs == []
    engine.dispose()


def test_preparer_selects_faststart_for_web_mp4_and_proxy_for_heavy_or_non_web_sources():
    config = playback_settings()
    service = VideoPlaybackPreparationService(config, SimpleNamespace())

    web = {
        "format": {"format_name": "mov,mp4,m4a,3gp,3g2,mj2", "bit_rate": "8000000"},
        "streams": [
            {"codec_type": "video", "codec_name": "h264", "width": 1920, "height": 1080},
            {"codec_type": "audio", "codec_name": "aac"},
        ],
    }
    assert service._kind(web) == "faststart"
    fast = service._command(SimpleNamespace(__str__=lambda self: "source"), SimpleNamespace(__str__=lambda self: "out"), "faststart")
    assert ("-c", "copy") == fast[fast.index("-c"):fast.index("-c") + 2]
    assert "+faststart" in fast

    heavy = {
        "format": {"format_name": "matroska,webm", "bit_rate": "50000000"},
        "streams": [
            {"codec_type": "video", "codec_name": "prores", "width": 3840, "height": 2160},
            {"codec_type": "audio", "codec_name": "pcm_s16le"},
        ],
    }
    assert service._kind(heavy) == "proxy_1080p"


class FakePlaybackPreparer:
    def __init__(self):
        self.calls = []

    async def prepare_and_upload(self, **kwargs):
        self.calls.append(kwargs)
        key = video_playback_key(kwargs["tenant_id"], kwargs["content_hash"])
        return PreparedPlayback(key, "faststart", 4, "etag-playback")


class PlaybackProvider:
    def __init__(self):
        self.deleted = []

    async def delete_object(self, key):
        self.deleted.append(key)


def test_playback_job_marks_derived_object_ready(tmp_path):
    engine, factory = setup(tmp_path)
    config = playback_settings()
    record_id, _, _, _, _ = ready_original(factory, config)
    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert schedule_video_playback_prepare(session, config, row)
        session.commit()
        job = session.scalar(select(ProcessingJobModel).where(
            ProcessingJobModel.job_type == "video_playback_prepare"
        ))
        claimed = ClaimedJob(
            id=job.id,
            tenant_id=job.tenant_id,
            job_type=job.job_type,
            entity_type=job.entity_type,
            entity_id=job.entity_id,
            payload=job.payload_json,
            attempt_count=1,
            lease_owner="test",
        )

    preparer = FakePlaybackPreparer()
    provider = PlaybackProvider()
    context = JobHandlerContext(
        job=claimed,
        dependencies=WorkerDependencies(
            session_factory=factory,
            settings=config,
            resources={
                "video_cache_r2_provider": provider,
                "video_playback_preparer": preparer,
            },
        ),
        shutdown_requested=Event(),
        cancellation_requested=Event(),
        logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
    )
    result = VideoPlaybackPrepareJobHandler(config)(context)
    assert result.outcome == JobOutcome.COMPLETED
    assert len(preparer.calls) == 1

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert row.playback_status == "ready"
        assert row.playback_kind == "faststart"
        assert row.playback_size_bytes == 4
        assert row.playback_reserved_bytes == 0
        assert row.playback_r2_key.endswith("/playback.mp4")
    assert provider.deleted == []
    engine.dispose()

def test_cleanup_releases_unstarted_derivative_when_feature_is_disabled(tmp_path):
    engine, factory = setup(tmp_path)
    enabled = playback_settings()
    record_id, _, _, _, _ = ready_original(factory, enabled)

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert schedule_video_playback_prepare(session, enabled, row)
        session.commit()
        old_job_id = row.playback_job_id

    disabled = settings(PROCESSING_JOBS_ENABLED=True, R2_VIDEO_PLAYBACK_DERIVED_ENABLED=False)
    cleanup = VideoCacheCleanup(factory, disabled, FakeR2())
    assert cleanup._recover_playback_preparing() == 1

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        old_job = session.get(ProcessingJobModel, old_job_id)
        assert row.status == "ready"
        assert row.playback_status == "pending"
        assert row.playback_reserved_bytes == 0
        assert row.playback_job_id is None
        assert old_job.status == "failed"
        assert old_job.last_error_code == "operation_cancelled"

    # Re-enabling the feature can safely admit a new generation.
    cleanup = VideoCacheCleanup(factory, enabled, FakeR2())
    assert cleanup._schedule_playback_backfill() == 1
    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert row.playback_status == "preparing"
        assert row.playback_generation == 2
    engine.dispose()


class PlaybackReconcileProvider:
    def __init__(self, original_key: str, original_size: int, playback_key: str):
        self.original_key = original_key
        self.original_size = original_size
        self.playback_key = playback_key

    async def head_object(self, key):
        if key == self.original_key:
            return R2ObjectHead(size_bytes=self.original_size, etag="original")
        if key == self.playback_key:
            raise R2NotFound()
        raise AssertionError(key)

    async def delete_object(self, key):
        raise AssertionError("healthy original must not be deleted")


def test_reconcile_missing_derivative_falls_back_to_original_without_deleting_it(tmp_path):
    engine, factory = setup(tmp_path)
    config = playback_settings()
    record_id, _, _, digest, _ = ready_original(factory, config)
    playback_key = video_playback_key("tenant-a", digest)

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        row.playback_r2_key = playback_key
        row.playback_kind = "faststart"
        row.playback_status = "ready"
        row.playback_size_bytes = 4
        session.commit()
        original_key = row.r2_key
        original_size = row.size_bytes

    provider = PlaybackReconcileProvider(original_key, original_size, playback_key)
    result = __import__("asyncio").run(
        VideoCacheCleanup(factory, config, provider).reconcile_ready()
    )
    assert result == {"checked": 1, "missing": 0, "size_mismatch": 0}

    with factory() as session:
        row = session.get(VideoCacheObjectModel, record_id)
        assert row.status == "ready"
        assert row.size_bytes == original_size
        assert row.playback_status == "failed"
        assert row.playback_size_bytes == 0
        assert row.playback_error_code == "r2_playback_missing"
    engine.dispose()
