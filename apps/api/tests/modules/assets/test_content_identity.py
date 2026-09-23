from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.content_identity import ensure_source_asset_link, normalize_sha256
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository


def _session() -> tuple[Session, object]:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add(
        ExternalSourceModel(
            id="source-a",
            tenant_id="tenant-a",
            source_type="google_drive",
            source_key="drive-a",
            source_metadata={},
        )
    )
    session.commit()
    return session, engine


def test_normalize_sha256_rejects_non_sha_provider_checksums() -> None:
    assert normalize_sha256("A" * 64) == "a" * 64
    assert normalize_sha256("a" * 40) is None
    assert normalize_sha256("a" * 32) is None
    assert normalize_sha256("not-a-hash") is None


def test_ensure_source_asset_link_creates_and_reuses_content_asset() -> None:
    session, engine = _session()
    try:
        repository = AssetRegistryRepository(session)
        source = repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id="source-a",
            external_asset_id="video-a",
            filename="clip.mp4",
            mime_type="video/mp4",
            size_bytes=123,
            provider_checksum="a" * 64,
            provider_version="v1",
            source_metadata={"parents": ["folder-a"], "is_folder": False},
        )

        asset = ensure_source_asset_link(
            repository,
            source_asset=source,
            content_hash="a" * 64,
        )
        repeated = ensure_source_asset_link(
            repository,
            source_asset=source,
            content_hash="a" * 64,
        )
        session.commit()

        assert repeated.id == asset.id
        assert asset.content_hash == "a" * 64
        assert asset.mime_type == "video/mp4"
        assert asset.size_bytes == 123
        assert source.hashed_provider_checksum == "a" * 64
        assert source.hashed_provider_version == "v1"
        assert session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == source.id
            )
        ).asset_id == asset.id
        assert len(list(session.scalars(select(AssetModel)))) == 1
    finally:
        session.close()
        engine.dispose()


def test_ensure_source_asset_link_relinks_when_content_changes() -> None:
    session, engine = _session()
    try:
        repository = AssetRegistryRepository(session)
        source = repository.upsert_source_asset(
            tenant_id="tenant-a",
            external_source_id="source-a",
            external_asset_id="video-a",
            filename="clip.mp4",
            mime_type="video/mp4",
            provider_checksum="a" * 64,
            source_metadata={"parents": ["folder-a"], "is_folder": False},
        )
        first = ensure_source_asset_link(
            repository,
            source_asset=source,
            content_hash="a" * 64,
        )
        source.provider_checksum = "b" * 64
        second = ensure_source_asset_link(
            repository,
            source_asset=source,
            content_hash="b" * 64,
        )
        session.commit()

        assert first.id != second.id
        link = session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == source.id
            )
        )
        assert link.asset_id == second.id
        assert len(list(session.scalars(select(AssetSourceLinkModel)))) == 1
    finally:
        session.close()
        engine.dispose()
