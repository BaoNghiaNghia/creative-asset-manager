import hashlib
import tempfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, event, inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.assets.model import (
    AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel,
)
from app.modules.auth_persistence.model import TenantModel
from app.modules.video_cache.model import VideoCacheObjectModel
from app.modules.video_cache.repository import VideoCacheRepository


def make_session():
    engine = create_engine("sqlite:///:memory:")
    @event.listens_for(engine, "connect")
    def enable_fk(dbapi_connection, _):
        dbapi_connection.execute("PRAGMA foreign_keys=ON")
    tables = [
        TenantModel.__table__, ExternalSourceModel.__table__,
        AssetModel.__table__, SourceAssetModel.__table__,
        AssetSourceLinkModel.__table__, VideoCacheObjectModel.__table__,
    ]
    Base.metadata.create_all(engine)
    return engine, Session(engine)


def seed(session, tenant_id, hash_value):
    tenant = TenantModel(id=tenant_id, name=tenant_id, slug=tenant_id)
    source = ExternalSourceModel(tenant_id=tenant_id, source_key="drive", source_type="google_drive")
    asset = AssetModel(tenant_id=tenant_id, content_hash=hash_value, mime_type="video/mp4", size_bytes=9)
    session.add_all([tenant, source, asset])
    session.flush()
    source_asset = SourceAssetModel(
        tenant_id=tenant_id, external_source_id=source.id, external_asset_id="remote",
        mime_type="video/mp4", size_bytes=9,
    )
    session.add(source_asset)
    session.flush()
    session.add(AssetSourceLinkModel(
        tenant_id=tenant_id, asset_id=asset.id, source_asset_id=source_asset.id,
    ))
    session.flush()
    return asset, source_asset


def test_tenant_uniqueness_accounting_and_state_transitions():
    engine, session = make_session()
    hash_value = hashlib.sha256(b"same").hexdigest()
    a1, s1 = seed(session, "tenant-a", hash_value)
    a2, s2 = seed(session, "tenant-b", hash_value)
    repo = VideoCacheRepository(session)
    r1 = repo.create_preparing(
        tenant_id="tenant-a", asset_id=a1.id, source_asset_id=s1.id,
        content_hash=hash_value, mime_type="video/mp4", reserved_bytes=9,
    )
    r2 = repo.create_preparing(
        tenant_id="tenant-b", asset_id=a2.id, source_asset_id=s2.id,
        content_hash=hash_value, mime_type="video/mp4", reserved_bytes=11,
    )
    assert repo.total_reserved_bytes() == 20
    assert repo.total_reserved_bytes("tenant-a") == 9
    assert repo.total_ready_bytes() == 0
    assert repo.get_by_id("tenant-b", r1.id) is None
    assert repo.get_by_tenant_and_hash("tenant-b", hash_value).id == r2.id
    assert repo.create_preparing(
        tenant_id="tenant-a", asset_id=a1.id, source_asset_id=s1.id,
        content_hash=hash_value, mime_type="video/mp4", reserved_bytes=9,
    ).id == r1.id
    with pytest.raises(LookupError):
        repo.mark_ready("tenant-b", r1.id, size_bytes=9, etag="etag")
    repo.mark_ready("tenant-a", r1.id, size_bytes=9, etag="etag")
    assert repo.total_ready_bytes() == 9
    assert repo.total_reserved_bytes() == 11
    with session.begin_nested():
        session.add(VideoCacheObjectModel(
            tenant_id="tenant-a", asset_id=a1.id, source_asset_id=s1.id,
            content_hash=hash_value, r2_key="video-cache/tenant-a/other/original",
            mime_type="video/mp4", reserved_bytes=1,
        ))
        with pytest.raises(IntegrityError):
            session.flush()
    session.rollback()
    session.close()
    engine.dispose()


def test_repo_rejects_image_and_unlinked_source():
    engine, session = make_session()
    hash_value = hashlib.sha256(b"video").hexdigest()
    asset, source = seed(session, "tenant-a", hash_value)
    repo = VideoCacheRepository(session)
    with pytest.raises(ValueError):
        repo.create_preparing(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source.id,
            content_hash=hash_value, mime_type="image/jpeg", reserved_bytes=9,
        )
    with pytest.raises(ValueError):
        repo.create_preparing(
            tenant_id="tenant-b", asset_id=asset.id, source_asset_id=source.id,
            content_hash=hash_value, mime_type="video/mp4", reserved_bytes=9,
        )
    asset.mime_type = "image/jpeg"
    session.flush()
    with pytest.raises(ValueError):
        repo.create_preparing(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source.id,
            content_hash=hash_value, mime_type="video/mp4", reserved_bytes=9,
        )
    asset.mime_type = "video/mp4"
    source.mime_type = "image/jpeg"
    session.flush()
    with pytest.raises(ValueError):
        repo.create_preparing(
            tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source.id,
            content_hash=hash_value, mime_type="video/mp4", reserved_bytes=9,
        )
    session.close()
    engine.dispose()


def test_migration_round_trip():
    with tempfile.TemporaryDirectory() as directory:
        url = f"sqlite:///{Path(directory) / 'r2-cache.db'}"
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", url)
        command.upgrade(config, "0081_public_review_rate_limits")
        command.upgrade(config, "0082_r2_video_cache_foundation")
        engine = create_engine(url)
        inspector = inspect(engine)
        assert "video_cache_objects" in inspector.get_table_names()
        columns = {item["name"] for item in inspector.get_columns("video_cache_objects")}
        assert {"tenant_id", "source_asset_id", "reserved_bytes", "last_accessed_at"} <= columns
        engine.dispose()
        command.downgrade(config, "0081_public_review_rate_limits")
        engine = create_engine(url)
        assert "video_cache_objects" not in inspect(engine).get_table_names()
        engine.dispose()
        command.upgrade(config, "0082_r2_video_cache_foundation")
        engine = create_engine(url)
        assert "video_cache_objects" in inspect(engine).get_table_names()
        engine.dispose()
