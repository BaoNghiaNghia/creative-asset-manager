import logging
import unittest
from datetime import datetime, timezone
from threading import Event

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.processing.handlers import ClaimedJob, JobHandlerContext, JobOutcome, WorkerDependencies
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.pipeline.handlers import SearchProjectionBuildJobHandler
from app.modules.processing.model import ProcessingJobModel
from app.modules.search.active_analysis import ActiveAnalysisService, AnalysisActivationError
from app.modules.search.governance_model import ActiveAnalysisAuditModel


class ActiveAnalysisServiceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.session = self.sessions()
        assets = AssetRegistryRepository(self.session)
        metadata = AiMetadataRepository(self.session)
        self.asset = assets.create_asset(tenant_id="tenant-a", content_hash="a" * 64)
        self.profile = metadata.create_profile(
            tenant_id="tenant-a", profile_name="default", profile_version="1",
            prompt_template="Analyze",
        )
        self.analyses = []
        for prompt in ("one", "two", "three"):
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
        self.session.commit()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def test_activation_is_scoped_valid_and_audited_with_exact_rollback(self):
        service = ActiveAnalysisService(self.session)
        for analysis in self.analyses:
            service.activate(
                tenant_id="tenant-a", asset_id=self.asset.id,
                analysis_id=analysis.id, actor_id="admin",
            )
        original_audits = list(self.session.scalars(
            select(ActiveAnalysisAuditModel).order_by(ActiveAnalysisAuditModel.created_at)
        ))
        original_ids = [row.id for row in original_audits]

        rolled = service.rollback(
            tenant_id="tenant-a", asset_id=self.asset.id,
            metadata_profile_id=self.profile.id, actor_id="admin",
        )

        self.assertEqual(rolled.active.analysis_id, self.analyses[1].id)
        audits = list(self.session.scalars(select(ActiveAnalysisAuditModel)))
        self.assertEqual(len(audits), 4)
        self.assertEqual([row.id for row in audits[:3]], original_ids)
        self.assertEqual(audits[-1].action, "rollback")
        with self.assertRaises(LookupError):
            service.activate(
                tenant_id="tenant-b", asset_id=self.asset.id,
                analysis_id=self.analyses[0].id, actor_id="admin",
            )

    def test_invalid_completed_analysis_cannot_be_activated(self):
        self.analyses[0].validation_errors_json = [{"code": "invalid"}]
        with self.assertRaises(AnalysisActivationError):
            ActiveAnalysisService(self.session).activate(
                tenant_id="tenant-a", asset_id=self.asset.id,
                analysis_id=self.analyses[0].id, actor_id="admin",
            )

    def test_index_job_becomes_eligible_only_after_projection_job_finishes(self):
        service = ActiveAnalysisService(self.session)
        active = service.activate(
            tenant_id="tenant-a", asset_id=self.asset.id,
            analysis_id=self.analyses[0].id, actor_id="admin",
        ).active
        (projection_id,) = service.enqueue_rebuild_and_reindex(
            tenant_id="tenant-a", active=active,
        )
        self.session.commit()
        jobs = list(self.session.scalars(select(ProcessingJobModel)))
        self.assertEqual([job.job_type for job in jobs], ["search_projection_build"])

        projection = self.session.get(ProcessingJobModel, projection_id)
        context = JobHandlerContext(
            job=ClaimedJob(
                id=projection.id, tenant_id=projection.tenant_id,
                job_type=projection.job_type, entity_type=projection.entity_type,
                entity_id=projection.entity_id, payload=projection.payload_json,
                attempt_count=1, lease_owner="test",
            ),
            dependencies=WorkerDependencies(session_factory=self.sessions),
            shutdown_requested=Event(), cancellation_requested=Event(),
            logger=logging.LoggerAdapter(logging.getLogger("test"), {}),
        )
        result = SearchProjectionBuildJobHandler(
            Settings(SEARCH_PROJECTION_ENABLED=True)
        )(context)

        self.assertEqual(result.outcome, JobOutcome.COMPLETED)
        index_jobs = list(self.session.scalars(
            select(ProcessingJobModel).where(ProcessingJobModel.job_type == "asset_index")
        ))
        self.assertEqual(len(index_jobs), 1)
        self.assertEqual(index_jobs[0].payload_json["active_analysis_id"], active.id)


if __name__ == "__main__":
    unittest.main()
