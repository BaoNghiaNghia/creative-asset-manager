from __future__ import annotations

import asyncio
import hashlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.authorization.folder_scope import ViewerFolderScopeModel
from app.modules.explorer.router import upload_file, viewer_folder_options
from app.modules.explorer.schema import AssetNode


class UploadRequest:
    def __init__(self, blocks: tuple[bytes, ...]):
        self.blocks = blocks
        self.headers = {"content-length": str(sum(len(block) for block in blocks))}

    async def stream(self):
        for block in self.blocks:
            yield block


class UploadProvider:
    def __init__(self):
        self.parent = AssetNode(
            id="folder-a",
            name="Folder A",
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
        )

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get_node(self, item_id):
        assert item_id == "folder-a"
        return self.parent

    async def upload_file_stream(self, parent_id, filename, mime_type, content):
        uploaded = bytearray()
        async for block in content:
            uploaded.extend(block)
        assert parent_id == "folder-a"
        assert filename == "clip.mp4"
        assert mime_type == "video/mp4"
        assert bytes(uploaded) == b"video-content"
        return AssetNode(
            id="drive-video-a",
            name=filename,
            kind="video",
            mime_type=mime_type,
            parent_id=parent_id,
            size=len(uploaded),
            media_duration_ms=3200,
        )


def test_explorer_upload_registers_content_asset_link_immediately() -> None:
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

    provider = UploadProvider()
    principal = SimpleNamespace(
        membership_id="membership-a",
        effective_roles=("tenant_admin",),
        actor_id="actor-a",
    )
    settings = Settings(
        PROCESSING_JOBS_ENABLED=False,
        VIDEO_SEARCH_ENABLED=False,
        VIDEO_ANALYSIS_ENABLED=False,
        VIDEO_PROXY_ENABLED=False,
    )

    async def scenario():
        with (
            patch(
                "app.modules.explorer.router._source_context",
                new=AsyncMock(
                    return_value=("token", "account-a", "tenant-a", "source-a")
                ),
            ),
            patch(
                "app.modules.explorer.router.create_source_provider",
                return_value=provider,
            ),
            patch("app.modules.explorer.router.ViewerFolderScopeService"),
            patch("app.modules.explorer.router._require_viewer_folder_scope"),
            patch("app.modules.explorer.router.get_settings", return_value=settings),
        ):
            return await upload_file(
                UploadRequest((b"video-", b"content")),
                parent_id="folder-a",
                filename="clip.mp4",
                mime_type="video/mp4",
                provider="google-drive",
                session=session,
                principal=principal,
                external_source_id="source-a",
            )

    try:
        result = asyncio.run(scenario())
        assert result["id"] == "drive-video-a"

        source = session.scalar(
            select(SourceAssetModel).where(
                SourceAssetModel.external_asset_id == "drive-video-a"
            )
        )
        assert source is not None
        assert source.parent_external_id == "folder-a"
        assert source.source_metadata["video_duration_ms"] == 3200
        expected_hash = hashlib.sha256(b"video-content").hexdigest()
        assert source.provider_checksum == expected_hash
        assert source.hashed_provider_checksum == expected_hash

        link = session.scalar(
            select(AssetSourceLinkModel).where(
                AssetSourceLinkModel.source_asset_id == source.id
            )
        )
        assert link is not None
        asset = session.get(AssetModel, link.asset_id)
        assert asset is not None
        assert asset.content_hash == expected_hash
        assert asset.mime_type == "video/mp4"
    finally:
        session.close()
        engine.dispose()


def test_viewer_upload_allows_unsynced_descendant_via_provider_parent_chain() -> None:
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
    session.add(
        ViewerFolderScopeModel(
            tenant_id="tenant-a",
            tenant_membership_id="membership-viewer",
            external_source_id="source-a",
            folder_external_id="folder-a",
            folder_name="Allowed root",
        )
    )
    session.commit()

    class ViewerUploadProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def get_node(self, item_id):
            if item_id == "folder-b":
                return AssetNode(
                    id="folder-b",
                    name="Nested folder",
                    kind="folder",
                    mime_type="application/vnd.google-apps.folder",
                    parent_id="folder-a",
                )
            if item_id == "folder-a":
                return AssetNode(
                    id="folder-a",
                    name="Allowed root",
                    kind="folder",
                    mime_type="application/vnd.google-apps.folder",
                )
            raise AssertionError(item_id)

        async def upload_file_stream(self, parent_id, filename, mime_type, content):
            uploaded = bytearray()
            async for block in content:
                uploaded.extend(block)
            assert parent_id == "folder-b"
            return AssetNode(
                id="uploaded-file",
                name=filename,
                kind="image",
                mime_type=mime_type,
                parent_id=parent_id,
                size=len(uploaded),
            )

    provider = ViewerUploadProvider()
    principal = SimpleNamespace(
        membership_id="membership-viewer",
        effective_roles=frozenset({"viewer"}),
        platform_admin=False,
        actor_id="viewer-a",
    )
    settings = Settings(
        PROCESSING_JOBS_ENABLED=False,
        VIDEO_SEARCH_ENABLED=False,
        VIDEO_ANALYSIS_ENABLED=False,
        VIDEO_PROXY_ENABLED=False,
    )

    async def scenario():
        with (
            patch(
                "app.modules.explorer.router._source_context",
                new=AsyncMock(
                    return_value=("token", "account-a", "tenant-a", "source-a")
                ),
            ),
            patch(
                "app.modules.explorer.router.create_source_provider",
                return_value=provider,
            ),
            patch("app.modules.explorer.router.get_settings", return_value=settings),
        ):
            return await upload_file(
                UploadRequest((b"viewer-upload",)),
                parent_id="folder-b",
                filename="photo.jpg",
                mime_type="image/jpeg",
                provider="google-drive",
                session=session,
                principal=principal,
                external_source_id="source-a",
            )

    try:
        result = asyncio.run(scenario())
        assert result == {
            "id": "uploaded-file",
            "name": "photo.jpg",
            "kind": "image",
        }
    finally:
        session.close()
        engine.dispose()


def test_viewer_folder_options_selects_active_tenant_drive_when_source_is_omitted() -> None:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    session.add(
        ExternalSourceModel(
            id="source-disconnected",
            tenant_id="tenant-a",
            source_type="google_drive",
            source_key="drive-old",
            source_metadata={},
            status="disconnected",
        )
    )
    session.add(
        ExternalSourceModel(
            id="source-active",
            tenant_id="tenant-a",
            source_type="google_drive",
            source_key="drive-active",
            source_metadata={},
            status="active",
        )
    )
    session.commit()

    class FolderProvider:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, traceback):
            return None

        async def list_children(self, parent_id, folders_only=False):
            assert parent_id == "root"
            assert folders_only is True
            return [
                AssetNode(
                    id="folder-1",
                    name="Campaigns",
                    kind="folder",
                    mime_type="application/vnd.google-apps.folder",
                )
            ]

    source_context = AsyncMock(
        return_value=("token", "account-a", "tenant-a", "source-active")
    )
    principal = SimpleNamespace(active_tenant_id="tenant-a")

    async def scenario():
        with (
            patch("app.modules.explorer.router._source_context", new=source_context),
            patch(
                "app.modules.explorer.router.create_source_provider",
                return_value=FolderProvider(),
            ),
        ):
            return await viewer_folder_options(
                request=SimpleNamespace(),
                provider="google-drive",
                session=session,
                principal=principal,
                external_source_id=None,
            )

    try:
        result = asyncio.run(scenario())
        assert result == {
            "external_source_id": "source-active",
            "folders": [{"id": "folder-1", "name": "Campaigns"}],
        }
        assert source_context.await_args.args[4] == "source-active"
    finally:
        session.close()
        engine.dispose()
