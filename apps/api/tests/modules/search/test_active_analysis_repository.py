import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.database import Base
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.search.active_analysis import ActiveAnalysisService
from app.modules.search.operations_repository import SearchOperationRepository


class ActiveAnalysisRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        assets = AssetRegistryRepository(self.session)
        metadata = AiMetadataRepository(self.session)
        self.asset = assets.create_asset(tenant_id="tenant-a", content_hash="a" * 64)
        self.profile = metadata.create_profile(
            tenant_id="tenant-a", profile_name="default", profile_version="1",
            prompt_template="Analyze",
        )
        self.analyses = []
        for prompt in ("older", "newer"):
            analysis = metadata.create_analysis(
                tenant_id="tenant-a", asset_id=self.asset.id,
                metadata_profile_id=self.profile.id, prompt_version=prompt,
                pipeline_version="1", force=True,
            )
            analysis.status = "completed"
            analysis.completed_at = datetime.now(timezone.utc)
            analysis.metadata_json = {"subject": prompt}
            analysis.search_projection = {"search_text": prompt}
            analysis.search_projection_version = "v2"
            self.analyses.append(analysis)
        self.session.flush()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_rebuild_selection_uses_explicit_active_pointer(self):
        ActiveAnalysisService(self.session).activate(
            tenant_id="tenant-a", asset_id=self.asset.id,
            analysis_id=self.analyses[0].id, actor_id="admin",
        )
        repository = SearchOperationRepository(self.session)
        run = repository.create_run(
            tenant_id="tenant-a", operation_type="rebuild_and_reindex",
            filters={}, target_projection_version="v2",
        )

        selected = repository.analysis_page(run, require_active=True)

        self.assertEqual([row.id for row in selected], [self.analyses[0].id])


if __name__ == "__main__":
    unittest.main()
