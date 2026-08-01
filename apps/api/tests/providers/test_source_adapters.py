import unittest

from app.domain.providers.contracts import (
    AssetSourceProvider,
    GetSourceAssetInput,
    SourceChangePage,
    ListSourceChangesInput,
)
from app.modules.explorer.schema import AssetNode
from app.providers.google.source_adapter import GoogleDriveSourceAdapter
from app.providers.microsoft.source_adapter import SharePointSourceAdapter
from app.providers.source_factory import create_source_provider


class FakeCloudClient:
    def __init__(self, _access_token: str, node: AssetNode):
        self.node = node
        self.entered = False
        self.children_calls: list[tuple[str, bool]] = []

    async def __aenter__(self):
        self.entered = True
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.entered = False

    async def get(self, item_id: str) -> AssetNode:
        if item_id != self.node.id:
            raise KeyError(item_id)
        return self.node

    async def children(
        self, parent_id: str, folders_only: bool = False
    ) -> list[AssetNode]:
        self.children_calls.append((parent_id, folders_only))
        return [self.node]


class SourceAdapterTest(unittest.IsolatedAsyncioTestCase):
    async def test_google_adapter_maps_existing_node_to_candidate(self) -> None:
        node = AssetNode(
            id="google-file-1",
            name="creative.png",
            kind="image",
            mime_type="image/png",
            size=123,
        )
        fake = FakeCloudClient("token", node)
        adapter = GoogleDriveSourceAdapter("token", client_factory=lambda _: fake)

        async with adapter:
            candidate = await adapter.get_asset(
                GetSourceAssetInput("google-source-1", node.id)
            )
            children = await adapter.list_children("root", folders_only=True)

        self.assertEqual(candidate.source_type, "google_drive")
        self.assertEqual(candidate.source_id, "google-source-1")
        self.assertEqual(candidate.external_asset_id, node.id)
        self.assertEqual(candidate.filename, node.name)
        self.assertEqual(candidate.size_bytes, 123)
        self.assertEqual(children, [node])
        self.assertEqual(fake.children_calls, [("root", True)])

    async def test_sharepoint_adapter_maps_existing_node_to_candidate(self) -> None:
        node = AssetNode(
            provider="sharepoint",
            id="sp:item:opaque",
            name="creative.mp4",
            kind="video",
            mime_type="video/mp4",
            size=456,
        )
        fake = FakeCloudClient("token", node)
        adapter = SharePointSourceAdapter("token", client_factory=lambda _: fake)

        async with adapter:
            candidate = await adapter.get_asset(
                GetSourceAssetInput("sharepoint-source-1", node.id)
            )

        self.assertEqual(candidate.source_type, "sharepoint")
        self.assertEqual(candidate.external_asset_id, node.id)
        self.assertEqual(candidate.mime_type, "video/mp4")

    async def test_legacy_adapter_page_compatibility_keeps_existing_browse_working(
        self,
    ) -> None:
        node = AssetNode(
            provider="sharepoint",
            id="sp:item:opaque",
            name="creative.mp4",
            kind="video",
            mime_type="video/mp4",
            size=456,
        )
        fake = FakeCloudClient("token", node)
        adapter = SharePointSourceAdapter("token", client_factory=lambda _: fake)

        async with adapter:
            page, next_page_token = await adapter.list_children_page(
                "root", page_size=100
            )

        self.assertEqual(page, [node])
        self.assertIsNone(next_page_token)
        self.assertEqual(fake.children_calls, [("root", False)])

    async def test_incremental_changes_use_provider_lister(self) -> None:
        async def lister(_token, input):
            self.assertEqual(input.cursor, "cursor-1")
            return SourceChangePage((), "cursor-2")

        adapter = GoogleDriveSourceAdapter("token", changes_lister=lister)
        page = await adapter.list_changes(
            ListSourceChangesInput("source-1", cursor="cursor-1")
        )
        self.assertEqual(page.next_cursor, "cursor-2")

    def test_factory_resolves_current_provider_names(self) -> None:
        google = create_source_provider("google-drive", "token")
        sharepoint = create_source_provider("sharepoint", "token")

        self.assertIsInstance(google, GoogleDriveSourceAdapter)
        self.assertIsInstance(sharepoint, SharePointSourceAdapter)
        self.assertIsInstance(google, AssetSourceProvider)

    def test_factory_rejects_unknown_provider(self) -> None:
        with self.assertRaisesRegex(ValueError, "Unsupported source provider"):
            create_source_provider("unknown", "token")
