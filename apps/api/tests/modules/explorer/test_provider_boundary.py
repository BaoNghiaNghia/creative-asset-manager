import ast
import inspect
import unittest
from unittest.mock import patch

from app.modules.authorization.folder_scope import ViewerFolderAccess
from app.modules.explorer.schema import AssetNode
from app.modules.explorer.service import ExplorerService


class FakeExplorerProvider:
    def __init__(self, parent: AssetNode, children: list[AssetNode]):
        self.parent = parent
        self.children = children

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get_node(self, item_id: str) -> AssetNode:
        self.assert_item_id = item_id
        return self.parent

    async def list_children(
        self, parent_id: str, *, folders_only: bool = False
    ) -> list[AssetNode]:
        if folders_only:
            return [item for item in self.children if item.kind == "folder"]
        return self.children


class ExplorerProviderBoundaryTest(unittest.IsolatedAsyncioTestCase):
    def test_explorer_service_has_no_concrete_cloud_provider_import(self) -> None:
        tree = ast.parse(inspect.getsource(inspect.getmodule(ExplorerService)))
        imports = {
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module
        }

        self.assertNotIn("app.providers.google.drive", imports)
        self.assertNotIn("app.providers.microsoft.sharepoint", imports)

    async def test_existing_folder_listing_shape_is_preserved_through_injection(self) -> None:
        parent = AssetNode(
            id="root",
            name="My Drive",
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
            has_children=True,
        )
        child = AssetNode(
            id="file-1",
            name="asset.png",
            kind="image",
            mime_type="image/png",
            parent_id="root",
        )
        provider = FakeExplorerProvider(parent, [child])
        service = ExplorerService(lambda _provider, _token: provider)

        with patch(
            "app.modules.explorer.service.schedule_metadata_index"
        ) as schedule_index:
            schedule_index.side_effect = lambda coroutine: coroutine.close()
            listing = await service.list_folder(
                "root", "token", "account-1", "google-drive"
            )

        self.assertEqual(listing.parent, parent)
        self.assertEqual(listing.children, [child])
        schedule_index.assert_called_once()

    async def test_verified_descendant_listing_keeps_all_children_in_viewer_scope(self) -> None:
        parent = AssetNode(
            id="nested-folder",
            name="Nested",
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
            parent_id="selected-folder",
            has_children=True,
        )
        children = [
            AssetNode(
                id="nested-file",
                name="visible.png",
                kind="image",
                mime_type="image/png",
                parent_id="nested-folder",
            ),
            AssetNode(
                id="deeper-folder",
                name="Deeper",
                kind="folder",
                mime_type="application/vnd.google-apps.folder",
                parent_id="nested-folder",
            ),
        ]
        provider = FakeExplorerProvider(parent, children)
        service = ExplorerService(
            lambda _provider, _token: provider,
            viewer_access=ViewerFolderAccess(True, "source-1", frozenset({"selected-folder"})),
        )

        with patch("app.modules.explorer.service.schedule_metadata_index") as schedule_index:
            schedule_index.side_effect = lambda coroutine: coroutine.close()
            listing = await service.list_folder(
                "nested-folder",
                "token",
                "account-1",
                "google-drive",
                viewer_parent_authorized=True,
            )

        self.assertEqual(listing.children, children)
        schedule_index.assert_called_once()
