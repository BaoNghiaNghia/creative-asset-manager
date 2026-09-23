from __future__ import annotations

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.assets.video_link_repair import repair_video_asset_links


def test_video_link_repair_is_dry_run_by_default_and_executes_sha256_repairs() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        session.add(
            ExternalSourceModel(
                id="source-a",
                tenant_id="tenant-a",
                source_type="google_drive",
                source_key="drive-a",
                source_metadata={},
            )
        )
        session.add_all(
            [
                SourceAssetModel(
                    id="video-sha",
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    external_asset_id="drive-video-sha",
                    filename="clip.mp4",
                    mime_type="video/mp4",
                    provider_checksum="a" * 64,
                    source_metadata={"parents": ["folder-a"], "is_folder": False},
                ),
                SourceAssetModel(
                    id="video-md5",
                    tenant_id="tenant-a",
                    external_source_id="source-a",
                    external_asset_id="drive-video-md5",
                    filename="legacy.mp4",
                    mime_type="video/mp4",
                    provider_checksum="b" * 32,
                    source_metadata={"parents": ["folder-a"], "is_folder": False},
                ),
            ]
        )
        session.commit()

        dry_run = repair_video_asset_links(
            session,
            tenant_id="tenant-a",
            execute=False,
        )
        assert dry_run.eligible_videos == 2
        assert dry_run.repairable_sha256 == 1
        assert dry_run.linked == 0
        assert dry_run.skipped_without_sha256 == 1
        assert session.scalar(select(AssetSourceLinkModel)) is None

        executed = repair_video_asset_links(
            session,
            tenant_id="tenant-a",
            execute=True,
        )
        assert executed.linked == 1
        link = session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == "video-sha"
            )
        )
        assert link is not None
        assert session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == "video-md5"
            )
        ) is None
    finally:
        session.close()
        engine.dispose()
