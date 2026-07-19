import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.projection_service import SearchProjectionService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel


class SearchProjectionServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.repository = AiMetadataRepository(self.session)
        asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
        self.session.add(asset)
        self.session.flush()
        profile = self.repository.create_profile(
            tenant_id="tenant-a",
            profile_name="searchable",
            profile_version="1",
            prompt_template="Analyze",
            search_config={
                "include_all_scalar_values": False,
                "text_paths": ["subject", "heritage"],
                "facet_paths": ["subject"],
            },
        )
        self.analysis = self.repository.create_analysis(
            tenant_id="tenant-a",
            asset_id=asset.id,
            metadata_profile_id=profile.id,
            prompt_version="1",
            pipeline_version="1",
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def test_disabled_service_does_not_build_or_store(self) -> None:
        service = SearchProjectionService(
            self.repository,
            SearchProjectionBuilder(),
            enabled=False,
        )
        with self.assertRaisesRegex(RuntimeError, "disabled"):
            service.rebuild(self.analysis.id)
        self.assertIsNone(self.analysis.search_projection)
        self.assertIsNone(self.analysis.search_projection_version)

    def test_rebuild_stores_projection_and_version_separately(self) -> None:
        self.repository.complete_analysis(
            analysis_id=self.analysis.id,
            metadata={
                "subject": "Cat",
                "audience": "MAMA",
                "heritage": "EST. 2015",
            },
        )
        service = SearchProjectionService(
            self.repository,
            SearchProjectionBuilder(projection_version="projection-v3"),
            enabled=True,
        )
        first = service.rebuild(self.analysis.id)
        persisted = dict(self.analysis.search_projection)
        second = service.rebuild(self.analysis.id)

        self.assertEqual(first, second)
        self.assertEqual(self.analysis.search_projection, persisted)
        self.assertEqual(self.analysis.search_projection_version, "projection-v3")
        self.assertNotIn("projection_version", self.analysis.search_projection)
        self.assertEqual(
            self.analysis.search_projection["normalized_terms"],
            ["2015", "cat", "est"],
        )
        self.assertEqual(
            self.analysis.search_projection["facets"],
            {"subject": ["cat"]},
        )
        self.assertNotIn("mama", self.analysis.search_projection["normalized_terms"])

    def test_analysis_without_metadata_cannot_be_rebuilt(self) -> None:
        service = SearchProjectionService(
            self.repository,
            SearchProjectionBuilder(),
            enabled=True,
        )
        with self.assertRaisesRegex(ValueError, "no validated metadata"):
            service.rebuild(self.analysis.id)


if __name__ == "__main__":
    unittest.main()
