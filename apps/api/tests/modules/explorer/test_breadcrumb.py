import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.explorer.breadcrumb import resolve_breadcrumb
from app.modules.explorer.router import item_location
from app.modules.explorer.schema import AssetNode

def test_nested_breadcrumb_stops_at_root():
    folders = {
        "root": {"name": "Desify - Image & Video Assets", "parent_id": None},
        "etsy": {"name": "Etsy - Pasimax", "parent_id": "root"},
        "listing": {"name": "listing - 4344786926", "parent_id": "etsy"},
    }
    assert resolve_breadcrumb(item_id="file", parent_id="listing", folders=folders, source_root_id="root") == [
        {"id": "root", "name": "Desify - Image & Video Assets"},
        {"id": "etsy", "name": "Etsy - Pasimax"},
        {"id": "listing", "name": "listing - 4344786926"},
    ]

def test_root_file_and_incomplete_or_cycle_are_safe():
    folders = {"root": {"name": "Root", "parent_id": None}}
    assert resolve_breadcrumb(item_id="file", parent_id="root", folders=folders, source_root_id="root") == [{"id": "root", "name": "Root"}]
    assert resolve_breadcrumb(item_id="file", parent_id="missing", folders=folders, source_root_id="root") == []
    cyclic = {"a": {"name": "A", "parent_id": "b"}, "b": {"name": "B", "parent_id": "a"}}
    assert resolve_breadcrumb(item_id="file", parent_id="a", folders=cyclic) == []

def test_viewer_root_prevents_ancestors_above_scope():
    folders = {"root": {"name": "Root", "parent_id": None}, "assigned": {"name": "Assigned", "parent_id": "root"}, "child": {"name": "Child", "parent_id": "assigned"}}
    assert resolve_breadcrumb(item_id="file", parent_id="child", folders=folders, source_root_id="root", permitted_root_ids={"assigned"}) == [{"id": "assigned", "name": "Assigned"}, {"id": "child", "name": "Child"}]


class _LocationProvider:
    def __init__(self):
        self.calls = []
        self.nodes = {
            "file-location-fallback": AssetNode(
                id="file-location-fallback",
                name="asset.jpg",
                kind="image",
                mime_type="image/jpeg",
                parent_id="listing-location-fallback",
            ),
            "listing-location-fallback": AssetNode(
                id="listing-location-fallback",
                name="listing - 4344786926",
                kind="folder",
                mime_type="application/vnd.google-apps.folder",
                parent_id="etsy-location-fallback",
            ),
            "etsy-location-fallback": AssetNode(
                id="etsy-location-fallback",
                name="Etsy - Pasimax",
                kind="folder",
                mime_type="application/vnd.google-apps.folder",
                parent_id="root-location-fallback",
            ),
            "root-location-fallback": AssetNode(
                id="root-location-fallback",
                name="Desify - Image & Video Assets",
                kind="folder",
                mime_type="application/vnd.google-apps.folder",
            ),
        }

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get_node(self, item_id):
        self.calls.append(item_id)
        return self.nodes[item_id]


class AssetLocationProviderFallbackTest(unittest.IsolatedAsyncioTestCase):
    async def test_missing_file_parent_is_loaded_before_folder_traversal(self):
        engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(engine)
        provider = _LocationProvider()
        with Session(engine, expire_on_commit=False) as session:
            external = ExternalSourceModel(
                id="source-location-fallback",
                tenant_id="tenant-location-fallback",
                source_key="drive-location-fallback",
                source_type="google_drive",
                display_name="Desify - Image & Video Assets",
                source_metadata={"root_folder_id": "root-location-fallback"},
            )
            source = SourceAssetModel(
                tenant_id="tenant-location-fallback",
                external_source_id=external.id,
                external_asset_id="file-location-fallback",
                filename="asset.jpg",
                mime_type="image/jpeg",
                source_metadata={},
            )
            session.add_all([external, source])
            session.commit()
            principal = CurrentPrincipal(
                user_id="user-location-fallback",
                active_tenant_id="tenant-location-fallback",
                membership_id="membership-location-fallback",
                external_identity=None,
                effective_roles=frozenset({"operator"}),
                effective_permissions=frozenset({"assets.read"}),
                platform_admin=False,
                session_id="session-location-fallback",
                authorization_source="database",
            )
            with (
                patch(
                    "app.modules.explorer.router._source_context",
                    new=AsyncMock(
                        return_value=(
                            "workspace-source-token",
                            "account-location-fallback",
                            "tenant-location-fallback",
                            "source-location-fallback",
                        )
                    ),
                ),
                patch(
                    "app.modules.explorer.router.create_source_provider",
                    return_value=provider,
                ),
            ):
                response = await item_location(
                    request=SimpleNamespace(),
                    item_id="file-location-fallback",
                    provider="google-drive",
                    external_source_id="source-location-fallback",
                    session=session,
                    principal=principal,
                )

            self.assertEqual(response.status, "available")
            self.assertEqual(
                [node.name for node in response.breadcrumb],
                [
                    "Desify - Image & Video Assets",
                    "Etsy - Pasimax",
                    "listing - 4344786926",
                ],
            )
            self.assertEqual(
                provider.calls,
                [
                    "file-location-fallback",
                    "listing-location-fallback",
                    "etsy-location-fallback",
                    "root-location-fallback",
                ],
            )
            refreshed = session.scalar(
                select(SourceAssetModel).where(
                    SourceAssetModel.external_asset_id == "file-location-fallback"
                )
            )
            self.assertEqual(
                refreshed.source_metadata["parents"],
                ["listing-location-fallback"],
            )
        engine.dispose()
