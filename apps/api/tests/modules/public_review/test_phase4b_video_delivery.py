import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.auth_persistence.model import TenantModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.public_review.video_delivery import PublicVideoDeliveryResolver
from app.modules.video_cache.guard import VideoDeliveryCircuitBreaker
from app.modules.video_cache.model import (
    VIDEO_CDN_DELIVERY_SETTING_KEY,
    VideoCacheObjectModel,
    VideoDeliveryRuntimeSettingModel,
)
from app.modules.video_cache.service import video_cache_key

SECRET = "phase-4b-test-only-signing-secret-with-entropy-2026"


def settings(**updates):
    values = {
        "R2_VIDEO_CACHE_ENABLED": True,
        "R2_ACCOUNT_ID": "test-account",
        "R2_BUCKET_NAME": "test-bucket",
        "R2_ACCESS_KEY_ID": "fake-id",
        "R2_SECRET_ACCESS_KEY": "fake-key",
        "R2_VIDEO_MEDIA_BASE_URL": "https://media.example.test",
        "R2_VIDEO_MEDIA_SIGNING_SECRET": SECRET,
        "R2_VIDEO_MEDIA_TICKET_TTL_SECONDS": 600,
        "VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS": "tenant-a",
        "VIDEO_CDN_DELIVERY_GUARD_ENABLED": True,
    }
    values.update(updates)
    return Settings(_env_file=None, **values)


def setup():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
    with factory() as session:
        session.add(TenantModel(id="tenant-a", name="A", slug="a"))
        session.flush()
        session.add(ExternalSourceModel(
            id="external-source",
            tenant_id="tenant-a",
            source_key="source-key",
            source_type="google_drive",
        ))
        session.add(AssetModel(
            id="asset-video",
            tenant_id="tenant-a",
            content_hash="d" * 64,
            mime_type="video/mp4",
            size_bytes=100,
        ))
        session.add(SourceAssetModel(
            id="source-authorized",
            tenant_id="tenant-a",
            external_source_id="external-source",
            external_asset_id="provider-video",
            filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=100,
        ))
        session.flush()
        session.add(AssetSourceLinkModel(
            tenant_id="tenant-a",
            asset_id="asset-video",
            source_asset_id="source-authorized",
        ))
        session.add(VideoDeliveryRuntimeSettingModel(
            setting_key=VIDEO_CDN_DELIVERY_SETTING_KEY,
            enabled=True,
        ))
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a",
            asset_id="asset-video",
            source_asset_id="source-authorized",
            content_hash="d" * 64,
            r2_key=video_cache_key("tenant-a", "d" * 64),
            mime_type="video/mp4",
            status="ready",
            size_bytes=100,
        ))
        session.commit()
    return engine, factory


def resolver(factory, configured=None, *, fill_service_factory=None):
    return PublicVideoDeliveryResolver(
        factory,
        configured or settings(),
        guard=VideoDeliveryCircuitBreaker(clock=lambda: 100.0),
        probe=lambda *_args, **_kwargs: True,
        fill_service_factory=fill_service_factory,
    )


def resolve(delivery, *, p=None, asset=None, source=None):
    return asyncio.run(delivery.resolve(
        principal=p or principal(),
        asset=asset or video_asset(),
        source=source or video_source(),
    ))


def principal():
    now = datetime.now(timezone.utc)
    return SimpleNamespace(
        tenant_id="tenant-a",
        expires_at=now + timedelta(hours=1),
        session_expires_at=now + timedelta(minutes=2),
    )


def video_asset(**updates):
    values = dict(
        id="asset-video",
        tenant_id="tenant-a",
        content_hash="d" * 64,
        mime_type="video/mp4",
    )
    values.update(updates)
    return SimpleNamespace(**values)


def video_source(**updates):
    values = dict(
        id="source-authorized",
        tenant_id="tenant-a",
        filename="clip.mp4",
        mime_type="video/mp4",
    )
    values.update(updates)
    return SimpleNamespace(**values)


def test_ready_authorized_video_gets_short_ticket_capped_to_session():
    engine, factory = setup()
    p = principal()
    ticket = resolve(resolver(factory), p=p)
    assert ticket is not None
    query = parse_qs(urlsplit(ticket.url).query)
    assert set(query) == {"v", "exp", "sig"}
    assert int(query["exp"][0]) <= int(p.session_expires_at.timestamp())
    assert SECRET not in repr(ticket)
    engine.dispose()


def test_runtime_off_missing_cache_and_non_video_use_source_fallback():
    engine, factory = setup()
    with factory() as session:
        row = session.get(VideoDeliveryRuntimeSettingModel, VIDEO_CDN_DELIVERY_SETTING_KEY)
        row.enabled = False
        session.commit()
    delivery = resolver(factory)
    assert resolve(delivery) is None
    with factory() as session:
        assert session.query(ProcessingJobModel).filter_by(
            job_type="video_cache_fill"
        ).count() == 0

    with factory() as session:
        row = session.get(VideoDeliveryRuntimeSettingModel, VIDEO_CDN_DELIVERY_SETTING_KEY)
        row.enabled = True
        cache = session.query(VideoCacheObjectModel).one()
        session.delete(cache)
        session.commit()
    assert resolve(delivery) is None
    with factory() as session:
        job = session.query(ProcessingJobModel).filter_by(
            job_type="video_cache_fill"
        ).one()
        assert job.tenant_id == "tenant-a"
        assert job.payload_json == {
            "tenant_id": "tenant-a",
            "asset_id": "asset-video",
            "source_asset_id": "source-authorized",
            "content_hash": "d" * 64,
        }
    assert resolve(
        delivery,
        asset=video_asset(mime_type="image/jpeg"),
        source=video_source(filename="image.jpg", mime_type="image/jpeg"),
    ) is None
    engine.dispose()


def test_cache_identity_and_tenant_mismatch_fail_closed():
    engine, factory = setup()
    delivery = resolver(factory)
    assert resolve(delivery, asset=video_asset(id="different-asset")) is None
    assert resolve(delivery, source=video_source(id="different-source")) is None
    assert resolve(
        delivery,
        p=SimpleNamespace(
            tenant_id="tenant-b",
            expires_at=None,
            session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    ) is None
    engine.dispose()


def test_rollout_scope_defaults_deny_and_global_override_is_explicit():
    engine, factory = setup()
    deny_all = settings(VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="")
    assert resolve(resolver(factory, deny_all)) is None

    global_settings = settings(
        VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS="",
        VIDEO_CDN_DELIVERY_GLOBAL_ROLLOUT_ENABLED=True,
    )
    assert resolve(resolver(factory, global_settings)) is not None
    engine.dispose()


def test_missing_runtime_row_falls_back_instead_of_breaking_playback():
    engine, factory = setup()
    with factory() as session:
        session.query(VideoDeliveryRuntimeSettingModel).delete()
        session.commit()
    assert resolve(resolver(factory)) is None
    engine.dispose()


def test_authorized_canary_cache_miss_enqueues_exact_pair_and_keeps_fallback():
    engine, factory = setup()
    with factory() as session:
        session.query(VideoCacheObjectModel).delete()
        session.commit()

    calls = []

    class FillService:
        async def ensure_video_cache_fill(self, **kwargs):
            calls.append(kwargs)

    delivery = resolver(factory, fill_service_factory=lambda: FillService())
    assert resolve(delivery) is None
    assert calls == [{
        "tenant_id": "tenant-a",
        "asset_id": "asset-video",
        "source_asset_id": "source-authorized",
        "content_hash": "d" * 64,
    }]
    engine.dispose()


def test_cache_admission_failure_and_disallowed_inputs_never_break_playback():
    engine, factory = setup()
    with factory() as session:
        session.query(VideoCacheObjectModel).delete()
        session.commit()

    calls = []

    class FailingFillService:
        async def ensure_video_cache_fill(self, **kwargs):
            calls.append(kwargs)
            raise RuntimeError("sensitive provider detail")

    delivery = resolver(factory, fill_service_factory=lambda: FailingFillService())
    assert resolve(delivery) is None
    assert len(calls) == 1
    assert resolve(
        delivery,
        asset=video_asset(mime_type="image/jpeg"),
        source=video_source(filename="image.jpg", mime_type="image/jpeg"),
    ) is None
    assert resolve(
        delivery,
        p=SimpleNamespace(
            tenant_id="tenant-b",
            expires_at=None,
            session_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        ),
    ) is None
    assert len(calls) == 1
    deny_scope = resolver(
        factory,
        settings(VIDEO_CDN_DELIVERY_CANARY_TENANT_IDS=""),
        fill_service_factory=lambda: FailingFillService(),
    )
    assert resolve(deny_scope) is None
    assert len(calls) == 1
    engine.dispose()
