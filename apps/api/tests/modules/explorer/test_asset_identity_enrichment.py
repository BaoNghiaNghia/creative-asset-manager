import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.assets.status_service import AssetProcessingStatusService
from app.modules.explorer.schema import AssetNode, SearchRequest
from app.modules.explorer.service import ExplorerService


DRIVE_ITEM_ID = "1LsLLaM9t20b_iBzwcf_tBBib3ckt85jY"
TENANT_ID = "tenant-drive"


class FakeExplorerProvider:
    def __init__(self, parent: AssetNode, children: list[AssetNode]):
        self.parent = parent
        self.children = children

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return None

    async def get_node(self, _item_id: str) -> AssetNode:
        return self.parent

    async def list_children(
        self, _parent_id: str, *, folders_only: bool = False
    ) -> list[AssetNode]:
        if folders_only:
            return [item for item in self.children if item.kind == "folder"]
        return self.children


class ExplorerAssetIdentityEnrichmentTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    async def test_list_folder_enriches_only_linked_file_in_tenant(self) -> None:
        registry = AssetRegistryRepository(self.session)
        source = registry.upsert_external_source(
            tenant_id=TENANT_ID,
            source_key="connected-google-account",
            source_type="google_drive",
        )
        source_asset = registry.upsert_source_asset(
            tenant_id=TENANT_ID,
            external_source_id=source.id,
            external_asset_id=DRIVE_ITEM_ID,
            filename="linked.jpg",
            mime_type="image/jpeg",
        )
        asset = registry.create_asset(
            tenant_id=TENANT_ID,
            content_hash="1" * 64,
            mime_type="image/jpeg",
        )
        registry.link_source_asset(
            tenant_id=TENANT_ID,
            asset_id=asset.id,
            source_asset_id=source_asset.id,
        )
        source_only_asset = registry.upsert_source_asset(
            tenant_id=TENANT_ID,
            external_source_id=source.id,
            external_asset_id="source-only",
            filename="source-only.jpg",
            mime_type="image/jpeg",
        )
        deleted = registry.upsert_source_asset(
            tenant_id=TENANT_ID,
            external_source_id=source.id,
            external_asset_id="deleted",
            filename="deleted.jpg",
            mime_type="image/jpeg",
        )
        registry.mark_source_asset_deleted(
            tenant_id=TENANT_ID,
            source_asset_id=deleted.id,
        )

        other_source = registry.upsert_external_source(
            tenant_id="other-tenant",
            source_key="other-google-account",
            source_type="google_drive",
        )
        registry.upsert_source_asset(
            tenant_id="other-tenant",
            external_source_id=other_source.id,
            external_asset_id=DRIVE_ITEM_ID,
        )
        self.session.commit()

        parent = AssetNode(
            id="root",
            name="My Drive",
            kind="folder",
            mime_type="application/vnd.google-apps.folder",
        )
        children = [
            AssetNode(
                id=DRIVE_ITEM_ID,
                name="linked.jpg",
                kind="image",
                mime_type="image/jpeg",
                parent_id="root",
            ),
            AssetNode(
                id="folder",
                name="Folder",
                kind="folder",
                mime_type="application/vnd.google-apps.folder",
                parent_id="root",
            ),
            AssetNode(id="source-only", name="source-only.jpg", kind="image", mime_type="image/jpeg"),
            AssetNode(id="deleted", name="deleted.jpg", kind="image", mime_type="image/jpeg"),
            AssetNode(id="missing", name="missing.jpg", kind="image", mime_type="image/jpeg"),
        ]
        provider = FakeExplorerProvider(parent, children)
        service = ExplorerService(
            lambda _provider, _token: provider,
            AssetProcessingStatusService(self.session),
        )
        statements: list[str] = []
        event.listen(
            self.engine,
            "before_cursor_execute",
            lambda _conn, _cursor, statement, _parameters, _context, _many: statements.append(statement),
        )

        with patch("app.modules.explorer.service.schedule_metadata_index") as schedule:
            schedule.side_effect = lambda coroutine: coroutine.close()
            listing = await service.list_folder(
                "root",
                "token",
                "connected-google-account",
                "google-drive",
                TENANT_ID,
            )

        linked = listing.children[0]
        self.assertEqual(linked.id, DRIVE_ITEM_ID)
        self.assertEqual(linked.source_asset_id, source_asset.id)
        self.assertEqual(linked.internal_asset_id, asset.id)
        self.assertEqual(linked.external_source_id, source.id)
        source_only = listing.children[2]
        self.assertEqual(source_only.source_asset_id, source_only_asset.id)
        self.assertIsNone(source_only.internal_asset_id)
        self.assertEqual(source_only.external_source_id, source.id)
        for child in (
            listing.children[1],
            listing.children[3],
            listing.children[4],
        ):
            self.assertIsNone(child.source_asset_id)
            self.assertIsNone(child.internal_asset_id)
            self.assertIsNone(child.external_source_id)
        identity_queries = [
            statement for statement in statements
            if "FROM source_assets" in statement and "external_asset_id IN" in statement
        ]
        self.assertEqual(len(identity_queries), 1)

    async def test_search_subtree_enriches_linked_results_for_active_tenant(self) -> None:
        registry = AssetRegistryRepository(self.session)
        source = registry.upsert_external_source(
            tenant_id=TENANT_ID,
            source_key="search-google-account",
            source_type="google_drive",
        )
        source_asset = registry.upsert_source_asset(
            tenant_id=TENANT_ID,
            external_source_id=source.id,
            external_asset_id="hero",
            filename="hero-banner.jpg",
            mime_type="image/jpeg",
        )
        asset = registry.create_asset(
            tenant_id=TENANT_ID,
            content_hash="2" * 64,
            mime_type="image/jpeg",
        )
        registry.link_source_asset(
            tenant_id=TENANT_ID,
            asset_id=asset.id,
            source_asset_id=source_asset.id,
        )
        self.session.commit()

        service = ExplorerService(
            lambda _provider, _token: None,
            AssetProcessingStatusService(self.session),
        )
        result = await service.search_subtree(
            SearchRequest(query="hero", root_id="root", provider="google-drive"),
            None,
            "search-account",
            tenant_id=TENANT_ID,
        )

        self.assertEqual(len(result.items), 1)
        matched = result.items[0]
        self.assertEqual(matched.id, "hero")
        self.assertEqual(matched.source_asset_id, source_asset.id)
        self.assertEqual(matched.external_source_id, source.id)
        self.assertEqual(matched.internal_asset_id, asset.id)
    async def test_search_subtree_finds_completed_ai_projection(self) -> None:
        registry = AssetRegistryRepository(self.session)
        source = registry.upsert_external_source(
            tenant_id=TENANT_ID,
            source_key="projection-google-account",
            source_type="google_drive",
            source_metadata={"provider_account_id": "projection-account"},
        )
        source_asset = registry.upsert_source_asset(
            tenant_id=TENANT_ID,
            external_source_id=source.id,
            external_asset_id="bandana",
            filename="bandana.jpg",
            mime_type="image/jpeg",
            source_metadata={"parents": ["root"]},
        )
        asset = registry.create_asset(
            tenant_id=TENANT_ID,
            content_hash="3" * 64,
            mime_type="image/jpeg",
        )
        registry.link_source_asset(
            tenant_id=TENANT_ID, asset_id=asset.id, source_asset_id=source_asset.id,
        )
        profile = MetadataProfileModel(
            tenant_id=TENANT_ID, profile_name="creative-assets", profile_version="v1",
            prompt_template="Analyze", search_config_json={}, active=True,
        )
        self.session.add(profile)
        self.session.flush()
        self.session.add(AssetAiAnalysisModel(
            tenant_id=TENANT_ID, asset_id=asset.id, content_hash=asset.content_hash,
            metadata_profile_id=profile.id, metadata_profile="creative-assets",
            metadata_profile_version="v1", prompt_version="v1", pipeline_version="v1",
            status="completed", metadata_json={}, search_projection={
                "search_text": "white fabric dog bandana reading Please don't pet me I'm workin here",
                "normalized_terms": ["please", "don", "t", "pet", "me"],
                "phrases": ["please don t pet me"],
            }, search_projection_version="search-projection-v1",
            completed_at=datetime.now(timezone.utc),
        ))
        self.session.commit()

        service = ExplorerService(
            lambda _provider, _token: None,
            AssetProcessingStatusService(self.session),
        )
        result = await service.search_subtree(
            SearchRequest(
                query="Please don't pet me", root_id="root", provider="google-drive",
            ),
            None, "projection-account", tenant_id=TENANT_ID,
        )

        self.assertEqual([item.id for item in result.items], ["bandana"])
        self.assertEqual(result.items[0].internal_asset_id, asset.id)
        self.assertGreaterEqual(result.indexed_count, 1)
