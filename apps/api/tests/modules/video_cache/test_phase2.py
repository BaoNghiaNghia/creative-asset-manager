import asyncio
import hashlib
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.auth_persistence.model import TenantModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.video_cache.fill import CacheFillStatus, VideoCacheFillService
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.quota import VideoCacheQuota
from app.modules.video_cache.service import video_cache_key
from app.providers.cloudflare.r2 import R2ProviderError


class FakeR2:
    def __init__(self, fail_delete=False):
        self.deleted = []
        self.fail_delete = fail_delete
    async def delete_object(self, key):
        if self.fail_delete:
            raise R2ProviderError("transient", retryable=True)
        self.deleted.append(key)
    async def abort_multipart_upload(self, key, upload_id):
        pass


def setup(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'cache.sqlite'}", connect_args={"timeout": 20})
    @event.listens_for(engine, "connect")
    def fk(dbapi, _):
        dbapi.execute("PRAGMA foreign_keys=ON")
    Base.metadata.create_all(engine)
    return engine, lambda: Session(engine)


def settings(**extra):
    return Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=True, R2_ACCOUNT_ID="account",
                    R2_BUCKET_NAME="bucket", R2_ACCESS_KEY_ID="dummy", R2_SECRET_ACCESS_KEY="dummy",
                    **extra)


def seed(factory, *, tenant="tenant-a", data=b"video", size=None, mime="video/mp4"):
    digest = hashlib.sha256(data).hexdigest()
    n = len(data) if size is None else size
    with factory() as session:
        if session.get(TenantModel, tenant) is None:
            session.add(TenantModel(id=tenant, name=tenant, slug=tenant))
            session.flush()
        source = ExternalSourceModel(tenant_id=tenant, source_key=digest[:12], source_type="google_drive")
        asset = AssetModel(tenant_id=tenant, content_hash=digest, mime_type=mime, size_bytes=n)
        session.add_all((source, asset))
        session.flush()
        child = SourceAssetModel(tenant_id=tenant, external_source_id=source.id,
                                 external_asset_id=digest[:12], mime_type=mime, size_bytes=n)
        session.add(child)
        session.flush()
        session.add(AssetSourceLinkModel(tenant_id=tenant, asset_id=asset.id, source_asset_id=child.id))
        session.commit()
        return asset.id, child.id, digest


def ensure(factory, config, fake, asset, source, digest, tenant="tenant-a"):
    return asyncio.run(VideoCacheFillService(factory, config, fake).ensure_video_cache_fill(
        tenant_id=tenant, asset_id=asset, source_asset_id=source, content_hash=digest))


def test_eligible_video_enqueues_once_and_is_tenant_scoped(tmp_path):
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    asset, source, digest = seed(factory)
    first = ensure(factory, settings(), fake, asset, source, digest)
    second = ensure(factory, settings(), fake, asset, source, digest)
    assert first.status == CacheFillStatus.ENQUEUED
    assert second.status == CacheFillStatus.ALREADY_PREPARING
    assert first.job_id == second.job_id
    with factory() as session:
        jobs = session.scalars(select(ProcessingJobModel).where(ProcessingJobModel.job_type == "video_cache_fill")).all()
        assert len(jobs) == 1
        assert jobs[0].payload_json == {"tenant_id": "tenant-a", "asset_id": asset,
                                        "source_asset_id": source, "content_hash": digest}
        assert VideoCacheQuota.usage(session).reserved_bytes == len(b"video")
    assert not fake.deleted
    engine.dispose()


@pytest.mark.parametrize("mime,size,expected", [
    ("image/jpeg", 9, CacheFillStatus.BYPASSED_NOT_VIDEO),
    ("audio/mpeg", 9, CacheFillStatus.BYPASSED_NOT_VIDEO),
    ("application/pdf", 9, CacheFillStatus.BYPASSED_NOT_VIDEO),
    ("video/mp4", 0, CacheFillStatus.BYPASSED_NO_SIZE),
    ("video/mp4", 2_000_000_000, CacheFillStatus.BYPASSED_OVERSIZED),
])
def test_ineligible_content_never_enqueues_or_touches_r2(tmp_path, mime, size, expected):
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    asset, source, digest = seed(factory, size=size, mime=mime)
    result = ensure(factory, settings(), fake, asset, source, digest)
    assert result.status == expected
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessingJobModel)) == 0
    assert not fake.deleted
    engine.dispose()


def test_missing_hash_and_disabled_never_enqueue(tmp_path):
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory)
    fake = FakeR2()
    assert ensure(factory, settings(), fake, asset, source, None).status == CacheFillStatus.BYPASSED_NO_HASH
    disabled = Settings(_env_file=None, R2_VIDEO_CACHE_ENABLED=False)
    assert ensure(factory, disabled, fake, asset, source, digest).status == CacheFillStatus.DISABLED
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(ProcessingJobModel)) == 0
    engine.dispose()


def test_bucket_global_accounting_and_remote_delete_failure(tmp_path):
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory)
    fake = FakeR2(fail_delete=True)
    with factory() as session:
        row = VideoCacheObjectModel(tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
                                    content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
                                    mime_type="video/mp4", status="ready", size_bytes=90)
        session.add(row)
        session.commit()
    quota = VideoCacheQuota(factory, settings(), fake)
    assert not asyncio.run(quota.evict_one())
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        assert row.status == "deleting"
        assert quota.usage(session).effective_bytes == 90
    fake.fail_delete = False
    from app.modules.video_cache.cleanup import VideoCacheCleanup
    with factory() as session:
        row = session.scalar(select(VideoCacheObjectModel))
        row.next_attempt_at = None
        session.commit()
    asyncio.run(VideoCacheCleanup(factory, settings(), fake).run_once())
    with factory() as session:
        assert quota.usage(session).effective_bytes == 0
    engine.dispose()


def test_concurrent_reservation_never_exceeds_hard_limit_sqlite_fallback(tmp_path):
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    ids = [seed(factory, data=f"v{i}".encode(), size=400_000_000) for i in range(2)]
    asset, source, digest = seed(factory, data=b"existing", size=8_500_000_000)
    with factory() as session:
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
            mime_type="video/mp4", status="preparing", reserved_bytes=8_500_000_000))
        session.commit()
    barrier = Barrier(2)
    def worker(identity):
        barrier.wait()
        return ensure(factory, settings(), fake, *identity).status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, ids))
    assert sorted(outcomes) == [CacheFillStatus.BYPASSED_QUOTA, CacheFillStatus.ENQUEUED]
    with factory() as session:
        assert VideoCacheQuota.usage(session).effective_bytes == 8_900_000_000
    engine.dispose()

def test_postgres_advisory_lock_prevents_concurrent_overreservation():
    import os
    from uuid import uuid4
    from sqlalchemy import text
    from sqlalchemy.engine import make_url
    url = os.getenv("R2_VIDEO_CACHE_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("isolated local PostgreSQL URL not configured")
    parsed = make_url(url)
    assert parsed.host in {"127.0.0.1", "localhost"}
    assert parsed.database == "cam_r2_phase2_test"
    schema = f"phase2_{uuid4().hex[:12]}"
    admin = create_engine(url, connect_args={"connect_timeout": 10})
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    admin.dispose()
    engine = create_engine(url, connect_args={
        "connect_timeout": 10, "options": f"-csearch_path={schema}",
    })
    Base.metadata.create_all(engine)
    factory = lambda: Session(engine)
    tenant = f"tenant-pg-{uuid4().hex[:8]}"
    fake = FakeR2()
    ids = [seed(factory, tenant=tenant, data=f"pg-{i}".encode(), size=400_000_000) for i in range(2)]
    asset, source, digest = seed(factory, tenant=tenant, data=b"pg-reserved", size=8_500_000_000)
    with factory() as session:
        session.add(VideoCacheObjectModel(
            tenant_id=tenant, asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key(tenant, digest),
            mime_type="video/mp4", status="preparing", reserved_bytes=8_500_000_000))
        session.commit()
    barrier = Barrier(2)
    def worker(identity):
        barrier.wait()
        return ensure(factory, settings(), fake, *identity, tenant=tenant).status
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(worker, ids))
    assert sorted(outcomes) == [CacheFillStatus.BYPASSED_QUOTA, CacheFillStatus.ENQUEUED]
    with factory() as session:
        assert VideoCacheQuota.usage(session).effective_bytes == 8_900_000_000
    engine.dispose()

def test_0083_sqlite_migration_round_trip():
    import tempfile
    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite:///{Path(directory) / 'phase2.sqlite'}"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0082_r2_video_cache_foundation")
        command.upgrade(config, "0083_r2_video_cache_fill")
        engine = create_engine(url)
        assert "fill_job_id" in {x["name"] for x in inspect(engine).get_columns("video_cache_objects")}
        names = {x["name"] for x in inspect(engine).get_foreign_keys("video_cache_objects")}
        assert "fk_video_cache_tenant_asset" not in names
        engine.dispose()
        command.downgrade(config, "0082_r2_video_cache_foundation")
        engine = create_engine(url)
        names = {x["name"] for x in inspect(engine).get_foreign_keys("video_cache_objects")}
        assert "fk_video_cache_tenant_asset" in names
        engine.dispose()
        command.upgrade(config, "0083_r2_video_cache_fill")

def test_pressure_evicts_until_incoming_would_fit_soft_target(tmp_path):
    from datetime import datetime, timedelta, timezone
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    config = settings()

    for i in range(9):
        asset, source, digest = seed(factory, data=f"ready-{i}".encode(), size=980_000_000)
        with factory() as session:
            session.add(VideoCacheObjectModel(
                tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
                content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
                mime_type="video/mp4", status="ready", size_bytes=980_000_000,
                last_accessed_at=datetime.now(timezone.utc) - timedelta(days=10-i)))
            session.commit()
    incoming = seed(factory, data=b"incoming", size=300_000_000)
    result = ensure(factory, config, fake, *incoming)
    assert result.status == CacheFillStatus.ENQUEUED
    assert len(fake.deleted) == 2
    with factory() as session:
        usage = VideoCacheQuota.usage(session)
        assert usage.effective_bytes == 7_160_000_000
        assert usage.effective_bytes <= config.R2_VIDEO_CACHE_SOFT_LIMIT_BYTES
    engine.dispose()


def test_lru_ignores_preparing_and_deleting(tmp_path):
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    ids = [seed(factory, data=f"candidate-{i}".encode()) for i in range(3)]
    with factory() as session:
        for index, (asset, source, digest) in enumerate(ids):
            session.add(VideoCacheObjectModel(
                tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
                content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
                mime_type="video/mp4",
                status=("ready", "preparing", "deleting")[index],
                size_bytes=9 if index != 1 else 0,
                reserved_bytes=9 if index == 1 else 0))
        session.commit()
    quota = VideoCacheQuota(factory, settings(), fake)
    assert asyncio.run(quota.evict_one())
    assert fake.deleted == [video_cache_key("tenant-a", ids[0][2])]
    assert not asyncio.run(quota.evict_one())
    with factory() as session:
        assert quota.usage(session).effective_bytes == 18
    engine.dispose()


def test_touch_access_is_debounced(tmp_path):
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory)
    with factory() as session:
        row = VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
            mime_type="video/mp4", status="ready", size_bytes=9)
        session.add(row)
        session.commit()
        record_id = row.id
    from app.modules.video_cache.repository import VideoCacheRepository
    with factory() as session:
        repo = VideoCacheRepository(session)
        assert repo.touch_access("tenant-a", record_id)
        assert not repo.touch_access("tenant-a", record_id)
        assert not repo.touch_access("wrong-tenant", record_id)
        session.commit()
    engine.dispose()

def test_reconciliation_repairs_known_missing_object_but_never_unknown_key(tmp_path):
    from app.providers.cloudflare.r2 import R2NotFound
    from app.modules.video_cache.cleanup import VideoCacheCleanup
    engine, factory = setup(tmp_path)
    first = seed(factory, data=b"known")
    second = seed(factory, data=b"unknown-key")
    with factory() as session:
        for identity, key in (
            (first, video_cache_key("tenant-a", first[2])),
            (second, "outside-video-cache/unknown"),
        ):
            asset, source, digest = identity
            session.add(VideoCacheObjectModel(
                tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
                content_hash=digest, r2_key=key,
                mime_type="video/mp4", status="ready", size_bytes=9))
        session.commit()
    class MissingR2(FakeR2):
        async def head_object(self, key):
            raise R2NotFound()
    fake = MissingR2()
    result = asyncio.run(VideoCacheCleanup(factory, settings(), fake).reconcile_ready())
    assert result == {"checked": 1, "missing": 1, "size_mismatch": 0}
    assert fake.deleted == [video_cache_key("tenant-a", first[2])]
    with factory() as session:
        assert session.scalar(select(func.count()).select_from(VideoCacheObjectModel)) == 1
    engine.dispose()


def test_reconciliation_does_not_delete_a_newer_ready_generation(tmp_path):
    from datetime import datetime, timedelta, timezone
    from app.modules.video_cache.cleanup import VideoCacheCleanup
    from app.providers.cloudflare.r2 import R2NotFound
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory, data=b"generation-race")
    old_time = datetime.now(timezone.utc) - timedelta(hours=1)
    with factory() as session:
        row = VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
            mime_type="video/mp4", status="ready", size_bytes=15, cached_at=old_time,
        )
        session.add(row)
        session.commit()
        record_id = row.id

    class StaleHead(FakeR2):
        async def head_object(self, key):
            with factory() as session:
                row = session.get(VideoCacheObjectModel, record_id)
                row.cached_at = datetime.now(timezone.utc)
                session.commit()
            raise R2NotFound()

    fake = StaleHead()
    asyncio.run(VideoCacheCleanup(factory, settings(), fake).reconcile_ready())
    with factory() as session:
        assert session.get(VideoCacheObjectModel, record_id).status == "ready"
    assert fake.deleted == []
    engine.dispose()


def test_cleanup_runner_does_not_log_raw_provider_error(monkeypatch, caplog):
    import logging
    from app.modules.video_cache import cleanup as cleanup_module
    runner = cleanup_module.VideoCacheCleanupRunner(lambda: None, settings(),
        logger=logging.getLogger("test.video_cache.cleanup"))

    class FailingCleanup:
        def __init__(self, *args):
            pass

        async def run_once(self):
            runner._stop.set()
            raise ValueError("do-not-log-this-secret")

    monkeypatch.setattr(cleanup_module, "VideoCacheCleanup", FailingCleanup)
    with caplog.at_level(logging.ERROR, logger="test.video_cache.cleanup"):
        runner._loop()
    assert len(caplog.records) == 1
    assert caplog.records[0].error_code == "ValueError"
    assert caplog.records[0].exc_info is None
    assert "do-not-log-this-secret" not in caplog.text


def test_operator_status_contains_counts_not_credentials_or_keys(tmp_path):
    from app.operations.video_cache_cli import video_cache_status
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory)
    with factory() as session:
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
            mime_type="video/mp4", status="preparing", reserved_bytes=9))
        session.commit()
    with factory() as session:
        value = video_cache_status(session, settings())
    assert value["preparing_objects"] == 1
    assert value["reserved_bytes"] == value["effective_bytes"] == 9
    assert "secret" not in str(value).lower()
    assert digest not in str(value)
    engine.dispose()

def test_real_smoke_requires_opt_in_and_uses_only_dedicated_prefix():
    from io import BytesIO
    from app.operations.video_cache_smoke import smoke
    from app.providers.cloudflare.r2 import R2NotFound, R2ObjectHead
    class SmokeR2:
        def __init__(self):
            self.objects = {}
            self.keys = []
        async def _call(self, method, **kwargs):
            key = kwargs["Key"]
            self.keys.append(key)
            assert key.startswith("video-cache-smoke-test/")
            if method == "put_object":
                self.objects[key] = kwargs["Body"]
                return {}
            if method == "get_object":
                return {"Body": BytesIO(self.objects[key])}
            raise AssertionError(method)
        async def head_object(self, key):
            self.keys.append(key)
            if key not in self.objects:
                raise R2NotFound()
            return R2ObjectHead(len(self.objects[key]), None)
        async def delete_object(self, key):
            self.keys.append(key)
            self.objects.pop(key, None)
    fake = SmokeR2()
    with pytest.raises(ValueError):
        asyncio.run(smoke(settings(), fake))
    assert not fake.keys
    enabled = settings(R2_VIDEO_CACHE_REAL_SMOKE=True)
    assert asyncio.run(smoke(enabled, fake))
    assert not fake.objects
    assert fake.keys and all(key.startswith("video-cache-smoke-test/") for key in fake.keys)

@pytest.mark.parametrize("override", [
    {"R2_VIDEO_CACHE_PREPARING_STALE_SECONDS": 299},
    {"R2_VIDEO_CACHE_CLEANUP_INTERVAL_SECONDS": 59},
    {"R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE": 0},
    {"R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN": 0},
    {"R2_VIDEO_CACHE_CLEANUP_BATCH_SIZE": 101,
     "R2_VIDEO_CACHE_CLEANUP_MAX_ITEMS_PER_RUN": 100},
])
def test_phase2_cleanup_config_bounds(override):
    with pytest.raises(ValueError):
        settings(**override)

def test_remote_404_frees_accounting_idempotently(tmp_path):
    from app.providers.cloudflare.r2 import R2NotFound
    engine, factory = setup(tmp_path)
    asset, source, digest = seed(factory)
    with factory() as session:
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
            content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
            mime_type="video/mp4", status="ready", size_bytes=50))
        session.commit()
    class MissingR2(FakeR2):
        async def delete_object(self, key):
            raise R2NotFound()
    quota = VideoCacheQuota(factory, settings(), MissingR2())
    assert asyncio.run(quota.evict_one())
    with factory() as session:
        assert quota.usage(session).effective_bytes == 0
    engine.dispose()


def test_same_hash_two_tenants_have_separate_fill_rows_and_keys(tmp_path):
    engine, factory = setup(tmp_path)
    a1, s1, digest = seed(factory, tenant="tenant-a", data=b"same-video")
    a2, s2, _ = seed(factory, tenant="tenant-b", data=b"same-video")
    fake = FakeR2()
    r1 = ensure(factory, settings(), fake, a1, s1, digest, tenant="tenant-a")
    r2 = ensure(factory, settings(), fake, a2, s2, digest, tenant="tenant-b")
    assert r1.status == r2.status == CacheFillStatus.ENQUEUED
    assert r1.record_id != r2.record_id and r1.job_id != r2.job_id
    with factory() as session:
        keys = set(session.scalars(select(VideoCacheObjectModel.r2_key)).all())
    assert keys == {
        video_cache_key("tenant-a", digest),
        video_cache_key("tenant-b", digest),
    }
    engine.dispose()

def test_unreclaimable_pressure_bypasses_without_destroying_ready_cache(tmp_path):
    engine, factory = setup(tmp_path)
    fake = FakeR2()
    held = seed(factory, data=b"held", size=8_500_000_000)
    ready = seed(factory, data=b"ready-small", size=100_000_000)
    incoming = seed(factory, data=b"new-large", size=500_000_000)
    with factory() as session:
        for identity, status, size in (
            (held, "preparing", 8_500_000_000),
            (ready, "ready", 100_000_000),
        ):
            asset, source, digest = identity
            session.add(VideoCacheObjectModel(
                tenant_id="tenant-a", asset_id=asset, source_asset_id=source,
                content_hash=digest, r2_key=video_cache_key("tenant-a", digest),
                mime_type="video/mp4", status=status,
                size_bytes=size if status == "ready" else 0,
                reserved_bytes=size if status == "preparing" else 0))
        session.commit()
    assert ensure(factory, settings(), fake, *incoming).status == CacheFillStatus.BYPASSED_QUOTA
    assert fake.deleted == []
    with factory() as session:
        assert VideoCacheQuota.usage(session).effective_bytes == 8_600_000_000
    engine.dispose()
