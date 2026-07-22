import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.repository import AiMetadataRepository, MetadataValidationFailure
from app.modules.assets.model import AssetModel


class AiMetadataRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.repository = AiMetadataRepository(self.session)
        self.asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
        self.other_asset = AssetModel(tenant_id="tenant-b", content_hash="a" * 64)
        self.session.add_all([self.asset, self.other_asset])
        self.session.flush()
        self.profile = self.repository.create_profile(
            tenant_id="tenant-a",
            profile_name="creative-general",
            profile_version="1",
            prompt_template="Describe {{ asset }}",
            optional_json_schema={
                "type": "object",
                "properties": {"subjects": {"type": "array"}},
                "required": ["subjects"],
            },
            search_config={"facets": ["subjects"]},
        )

    def tearDown(self) -> None:
        self.session.close()
        self.engine.dispose()

    def _analysis(self, *, force: bool = False):
        return self.repository.create_analysis(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            metadata_profile_id=self.profile.id,
            prompt_version="prompt-1",
            pipeline_version="pipeline-1",
            ai_provider="test",
            ai_model="none",
            force=force,
        )

    def test_profile_stores_dynamic_schema_and_search_config(self) -> None:
        self.assertEqual(self.profile.optional_json_schema["type"], "object")
        self.assertEqual(self.profile.search_config_json, {"facets": ["subjects"]})

    def test_normal_analysis_is_idempotent(self) -> None:
        first = self._analysis()
        second = self._analysis()
        self.assertEqual(first.id, second.id)
        self.assertEqual(
            self.session.scalar(select(func.count()).select_from(AssetAiAnalysisModel)), 1
        )

    def test_database_constraint_is_final_idempotency_guard(self) -> None:
        first = self._analysis()
        duplicate = AssetAiAnalysisModel(
            tenant_id=first.tenant_id,
            asset_id=first.asset_id,
            content_hash=first.content_hash,
            metadata_profile_id=first.metadata_profile_id,
            metadata_profile=first.metadata_profile,
            metadata_profile_version=first.metadata_profile_version,
            prompt_version=first.prompt_version,
            pipeline_version=first.pipeline_version,
            ai_provider=first.ai_provider,
            ai_model=first.ai_model,
            forced=False,
        )
        self.session.add(duplicate)
        with self.assertRaises(IntegrityError):
            self.session.flush()
        self.session.rollback()

    def test_forced_reanalysis_preserves_completed_history(self) -> None:
        original = self._analysis()
        self.repository.mark_running(original.id)
        self.repository.complete_analysis(
            analysis_id=original.id,
            metadata={"subjects": ["cat"], "dynamic": {"palette": ["blue"]}},
            raw_response={"provider": "raw"},
            store_raw_response=True,
            search_projection={"search_terms": ["cat"]},
            search_projection_version="projection-2",
        )
        forced_one = self._analysis(force=True)
        forced_two = self._analysis(force=True)

        self.assertNotEqual(forced_one.id, forced_two.id)
        self.assertEqual(len(self.repository.history("tenant-a", self.asset.id)), 3)
        self.assertEqual(original.status, "completed")
        self.assertEqual(original.metadata_json["dynamic"]["palette"], ["blue"])
        self.assertEqual(original.raw_response_json, {"provider": "raw"})
        self.assertEqual(original.search_projection_version, "projection-2")

    def test_raw_response_can_be_suppressed(self) -> None:
        analysis = self._analysis()
        self.repository.complete_analysis(
            analysis_id=analysis.id,
            metadata={"subjects": []},
            raw_response={"secret": "not-persisted"},
            store_raw_response=False,
        )
        self.assertIsNone(analysis.raw_response_json)

    def test_empty_json_documents_are_preserved(self) -> None:
        profile = self.repository.create_profile(
            tenant_id="tenant-a",
            profile_name="empty-schema",
            profile_version="1",
            prompt_template="Analyze",
            optional_json_schema={},
        )
        analysis = self.repository.create_analysis(
            tenant_id="tenant-a",
            asset_id=self.asset.id,
            metadata_profile_id=profile.id,
            prompt_version="1",
            pipeline_version="1",
        )
        self.repository.complete_analysis(
            analysis_id=analysis.id,
            metadata={},
            raw_response={},
            store_raw_response=True,
            search_projection={},
            search_projection_version="projection-1",
        )
        self.assertEqual(profile.optional_json_schema, {})
        self.assertEqual(analysis.metadata_json, {})
        self.assertEqual(analysis.raw_response_json, {})
        self.assertEqual(analysis.search_projection, {})


    def test_profile_schema_rejects_invalid_metadata(self) -> None:
        analysis = self._analysis()
        with self.assertRaises(MetadataValidationFailure) as raised:
            self.repository.complete_analysis(analysis_id=analysis.id, metadata={"wrong": 1})
        self.assertEqual(raised.exception.errors[0].code, "json_schema")
        self.assertEqual(analysis.status, "pending")

    def test_tenant_scope_is_enforced_for_asset_and_profile(self) -> None:
        with self.assertRaises(LookupError):
            self.repository.create_analysis(
                tenant_id="tenant-a",
                asset_id=self.other_asset.id,
                metadata_profile_id=self.profile.id,
                prompt_version="1",
                pipeline_version="1",
            )


if __name__ == "__main__":
    unittest.main()
