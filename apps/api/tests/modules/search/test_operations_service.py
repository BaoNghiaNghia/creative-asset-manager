import unittest
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.search.index_types import AliasSwitchResult
from app.modules.search.operations_model import SearchOperationItemModel
from app.modules.search.operations_repository import SearchOperationRepository
from app.modules.search.operations_service import SearchMaintenanceService
from app.modules.search.source_index import SearchSourceIndexResolver


class FakeSearchIndex:
    def __init__(self):
        self.created = []
        self.batches = []
        self.switched = []
        self.fail_next_bulk = False

    async def create_index(self, version):
        name = f"creative-assets-v2-{version}"
        self.created.append(name)
        return name

    async def bulk_upsert(self, documents):
        return await self.bulk_upsert_to_index(documents, "write-alias")

    async def bulk_upsert_to_index(self, documents, target_index):
        self.batches.append((target_index, tuple(documents)))
        if self.fail_next_bulk:
            self.fail_next_bulk = False
            raise RuntimeError("bulk unavailable")
        return len(documents)

    async def switch_aliases(self, target_index):
        self.switched.append(target_index)
        return AliasSwitchResult(
            target_index,
            ("creative-assets-v2-000019",),
            ("creative-assets-v2-000019",),
        )

    async def rollback_aliases(self, previous_index):
        raise NotImplementedError

    async def search(self, body):
        raise NotImplementedError


class SearchMaintenanceServiceTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.operations = SearchOperationRepository(self.session)
        assets = AssetRegistryRepository(self.session)
        metadata = AiMetadataRepository(self.session)
        self.default_profile = metadata.create_profile(
            tenant_id="tenant-a",
            profile_name="default",
            profile_version="1",
            prompt_template="Analyze",
        )
        self.special_profile = metadata.create_profile(
            tenant_id="tenant-a",
            profile_name="special",
            profile_version="1",
            prompt_template="Analyze",
        )
        self.analyses = []
        for index in range(4):
            asset = assets.create_asset(
                tenant_id="tenant-a",
                content_hash=f"{index + 1:064x}",
            )
            profile = self.special_profile if index == 3 else self.default_profile
            analysis = metadata.create_analysis(
                tenant_id="tenant-a",
                asset_id=asset.id,
                metadata_profile_id=profile.id,
                prompt_version="1",
                pipeline_version="1",
            )
            metadata.mark_running(analysis.id)
            metadata.complete_analysis(
                analysis_id=analysis.id,
                metadata={"subject": f"Cat {index}", "heritage": "EST. 2015"},
            )
            self.analyses.append(analysis)
        tenant_b_asset = assets.create_asset(
            tenant_id="tenant-b",
            content_hash="f" * 64,
        )
        tenant_b_profile = metadata.create_profile(
            tenant_id="tenant-b",
            profile_name="default",
            profile_version="1",
            prompt_template="Analyze",
        )
        tenant_b_analysis = metadata.create_analysis(
            tenant_id="tenant-b",
            asset_id=tenant_b_asset.id,
            metadata_profile_id=tenant_b_profile.id,
            prompt_version="1",
            pipeline_version="1",
        )
        metadata.mark_running(tenant_b_analysis.id)
        metadata.complete_analysis(
            analysis_id=tenant_b_analysis.id,
            metadata={"subject": "Must not cross tenant"},
        )
        self.session.commit()

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def create_run(self, operation, **filters):
        run = self.operations.create_run(
            tenant_id="tenant-a",
            operation_type=operation,
            filters={
                "metadata_profile": filters.get("metadata_profile"),
                "current_projection_version": filters.get("current_projection_version"),
                "asset_ids": filters.get("asset_ids", []),
                "only_missing": filters.get("only_missing", False),
                "only_failed": filters.get("only_failed", False),
            },
            target_projection_version="projection-v2",
            page_size=filters.get("page_size", 2),
            dry_run=filters.get("dry_run", False),
        )
        self.session.commit()
        return run

    async def test_rebuild_is_paginated_tenant_profile_and_asset_filtered(self) -> None:
        selected = [self.analyses[0].asset_id, self.analyses[2].asset_id]
        run = self.create_run(
            "rebuild_projections",
            metadata_profile="default",
            asset_ids=selected,
            page_size=1,
        )
        result = await SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(projection_version="projection-v2"),
            projection_enabled=True,
        ).run(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.succeeded_count, 2)
        self.assertEqual(result.scanned_count, 2)
        self.assertIsNotNone(result.cursor_analysis_id)
        self.assertEqual(self.analyses[0].search_projection_version, "projection-v2")
        self.assertEqual(self.analyses[2].search_projection_version, "projection-v2")
        self.assertIsNone(self.analyses[1].search_projection)
        self.assertIsNone(self.analyses[3].search_projection)

    async def test_dry_run_and_cancellation_do_not_mutate_projection_or_index(self) -> None:
        index = FakeSearchIndex()
        dry = self.create_run("rebuild_and_reindex", dry_run=True)
        result = await SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(projection_version="projection-v2"),
            index_provider=index,
            projection_enabled=True,
            index_enabled=False,
        ).run(tenant_id="tenant-a", run_id=dry.id)
        self.assertEqual(result.status, "completed")
        self.assertEqual(result.skipped_count, 4)
        self.assertFalse(index.created)
        self.assertTrue(all(item.search_projection is None for item in self.analyses))

        cancelled = self.create_run("rebuild_projections")
        self.operations.request_cancellation("tenant-a", cancelled.id)
        self.session.commit()
        result = await SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(),
            projection_enabled=True,
        ).run(tenant_id="tenant-a", run_id=cancelled.id)
        self.assertEqual(result.status, "cancelled")
        self.assertEqual(result.scanned_count, 0)

    async def test_reindex_failure_resumes_only_failed_then_switches_alias(self) -> None:
        index = FakeSearchIndex()
        index.fail_next_bulk = True
        run = self.create_run("rebuild_and_reindex", page_size=2)
        service = SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(projection_version="projection-v2"),
            index_provider=index,
            projection_enabled=True,
            index_enabled=True,
        )
        first = await service.run(
            tenant_id="tenant-a",
            run_id=run.id,
            index_version="000020",
        )
        self.assertEqual(first.status, "failed")
        self.assertEqual(first.failed_count, 2)
        self.assertFalse(index.switched)
        self.assertEqual(index.created, ["creative-assets-v2-000020"])

        first.filters_json = {**first.filters_json, "only_failed": True}
        first.status = "pending"
        first.cancellation_requested = False
        self.session.commit()
        resumed = await service.run(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(resumed.status, "completed")
        self.assertEqual(resumed.failed_count, 0)
        self.assertEqual(resumed.succeeded_count, 4)
        self.assertEqual(index.created, ["creative-assets-v2-000020"])
        self.assertEqual(index.switched, ["creative-assets-v2-000020"])
        self.assertEqual(
            resumed.alias_switch_json,
            {
                "target_index": "creative-assets-v2-000020",
                "previous_read_indices": ["creative-assets-v2-000019"],
                "previous_write_indices": ["creative-assets-v2-000019"],
            },
        )
        again = await service.run(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(again.status, "completed")
        self.assertEqual(index.switched, ["creative-assets-v2-000020"])
        self.assertTrue(all(len(batch) <= 2 for _, batch in index.batches))
        targets = {target for target, _ in index.batches}
        self.assertEqual(targets, {"creative-assets-v2-000020"})
        self.assertEqual(
            self.session.query(SearchOperationItemModel)
            .filter(SearchOperationItemModel.run_id == run.id)
            .count(),
            4,
        )

    async def test_reindex_reuses_resolver_and_parent_map_per_source(self) -> None:
        assets = AssetRegistryRepository(self.session)
        source_a = assets.upsert_external_source(
            tenant_id="tenant-a", source_key="source-a", source_type="google-drive"
        )
        source_b = assets.upsert_external_source(
            tenant_id="tenant-a", source_key="source-b", source_type="google-drive"
        )
        for source, root_id in ((source_a, "root-a"), (source_b, "root-b")):
            assets.upsert_source_asset(
                tenant_id="tenant-a",
                external_source_id=source.id,
                external_asset_id=root_id,
                filename=root_id,
                source_metadata={"parents": []},
            )
        for number, analysis in enumerate(self.analyses):
            source = source_a if number < 3 else source_b
            root_id = "root-a" if source is source_a else "root-b"
            source_asset = assets.upsert_source_asset(
                tenant_id="tenant-a",
                external_source_id=source.id,
                external_asset_id=f"file-{number}",
                filename=f"file-{number}.jpg",
                source_metadata={"parents": [root_id]},
            )
            assets.link_source_asset(
                tenant_id="tenant-a",
                asset_id=analysis.asset_id,
                source_asset_id=source_asset.id,
            )
        self.session.commit()

        class CountingResolver(SearchSourceIndexResolver):
            instances = []
            parent_map_loads = []
            preloaded_pages = []
            page_cache_clears = []

            def __init__(self, session):
                super().__init__(session)
                self.__class__.instances.append(self)

            def preload_assets(self, *, tenant_id, asset_ids):
                asset_ids = tuple(asset_ids)
                self.__class__.preloaded_pages.append(asset_ids)
                return super().preload_assets(tenant_id=tenant_id, asset_ids=asset_ids)

            def clear_page_cache(self):
                self.__class__.page_cache_clears.append(
                    (len(self._sources_by_asset), len(self._details_by_source))
                )
                return super().clear_page_cache()

            def _parent_map(self, tenant_id, external_source_id):
                key = (tenant_id, external_source_id)
                if key not in self._parent_maps:
                    self.__class__.parent_map_loads.append(key)
                return super()._parent_map(tenant_id, external_source_id)

        index = FakeSearchIndex()
        run = self.create_run("rebuild_and_reindex", page_size=2)
        with patch(
            "app.modules.search.operations_service.SearchSourceIndexResolver",
            CountingResolver,
        ):
            result = await SearchMaintenanceService(
                self.operations,
                SearchProjectionBuilder(projection_version="projection-v2"),
                index_provider=index,
                projection_enabled=True,
                index_enabled=True,
            ).run(tenant_id="tenant-a", run_id=run.id, index_version="000021")

        self.assertEqual(result.status, "completed")
        self.assertEqual(len(CountingResolver.instances), 1)
        self.assertEqual([len(page) for page in CountingResolver.preloaded_pages], [2, 2])
        self.assertEqual(CountingResolver.page_cache_clears[:2], [(2, 2), (2, 2)])
        self.assertEqual(
            set(CountingResolver.parent_map_loads),
            {("tenant-a", source_a.id), ("tenant-a", source_b.id)},
        )
        self.assertEqual(len(CountingResolver.parent_map_loads), 2)
        documents = [document for _, batch in index.batches for document in batch]
        self.assertEqual(len(documents), 4)
        self.assertEqual({document.source_id for document in documents}, {source_a.id, source_b.id})
        self.assertTrue(all(document.ancestor_ids[0].startswith("file-") for document in documents))
        self.assertTrue(all(len(batch) <= 2 for _, batch in index.batches))
    async def test_current_version_and_only_missing_filters(self) -> None:
        self.analyses[0].search_projection = {"search_text": "old"}
        self.analyses[0].search_projection_version = "projection-v1"
        self.session.commit()
        run = self.create_run(
            "rebuild_projections",
            current_projection_version="projection-v1",
            only_missing=False,
        )
        result = await SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(projection_version="projection-v2"),
            projection_enabled=True,
        ).run(tenant_id="tenant-a", run_id=run.id)
        self.assertEqual(result.succeeded_count, 1)
        self.assertEqual(self.analyses[0].search_projection_version, "projection-v2")

        missing = self.create_run("rebuild_projections", only_missing=True)
        missing_result = await SearchMaintenanceService(
            self.operations,
            SearchProjectionBuilder(projection_version="projection-v2"),
            projection_enabled=True,
        ).run(tenant_id="tenant-a", run_id=missing.id)
        self.assertEqual(missing_result.succeeded_count, 3)

    def test_service_source_has_no_ai_provider_call(self) -> None:
        source = __import__("inspect").getsource(SearchMaintenanceService)
        self.assertNotIn("analyze_single", source)
        self.assertNotIn("AiMetadataProvider", source)


if __name__ == "__main__":
    unittest.main()
