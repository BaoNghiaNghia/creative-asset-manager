import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.modules.ai_governance.model import AiModelRateLimitStateModel
from app.modules.ai_governance.rate_limit import AiModelRateLimitRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService
from app.modules.processing_policy.model import TenantProcessingPolicyModel


NOW = datetime(2026, 7, 20, tzinfo=timezone.utc)


class RateLimitedClaimTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "rate-limit-claim.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)
        with self.sessions.begin() as session:
            session.add(
                TenantProcessingPolicyModel(
                    tenant_id="tenant",
                    pipeline_enabled=True,
                    download_enabled=True,
                    source_sync_enabled=True,
                    managed_storage_enabled=True,
                    ai_analysis_enabled=True,
                    search_v2_enabled=True,
                    sidecar_enabled=True,
                    total_active_jobs_limit=10,
                    ai_active_jobs_limit=10,
                )
            )

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def _analysis_job(self, key: str, model: str, *, priority: int = 0) -> str:
        with self.sessions.begin() as session:
            analysis = AssetAiAnalysisModel(
                id=f"analysis-{key}",
                tenant_id="tenant",
                asset_id=f"asset-{key}",
                content_hash=(key * 64)[:64],
                metadata_profile_id="profile",
                metadata_profile="creative-assets",
                metadata_profile_version="v1",
                prompt_version="prompt-v1",
                pipeline_version="pipeline-v1",
                ai_provider="gemini",
                ai_model=model,
            )
            session.add(analysis)
            session.flush()
            return ProcessingRepository(session).create_job(
                tenant_id="tenant",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id=analysis.id,
                idempotency_key=f"analyze:{key}",
                payload={"analysis_id": analysis.id},
                priority=priority,
                next_attempt_at=NOW,
                provider_key="gemini",
                provider_scope="ai",
            ).id

    def _claim(self, worker: str, now: datetime = NOW):
        with self.sessions() as session:
            return ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id=worker,
                lease_seconds=60,
                now=now,
                enforce_tenant_policy=True,
                allowed_job_types=("asset_analyze",),
            )

    def _block_model(self, model: str, retry_at: datetime) -> None:
        with self.sessions.begin() as session:
            session.add(
                AiModelRateLimitStateModel(
                    tenant_id="tenant",
                    provider="gemini",
                    model=model,
                    last_started_at=NOW,
                    next_eligible_at=retry_at,
                    blocked_until=None,
                    updated_at=NOW,
                )
            )

    def test_all_delayed_jobs_are_filtered_before_claim(self):
        first = self._analysis_job("a", "model-a")
        second = self._analysis_job("b", "model-a")
        retry_at = NOW + timedelta(seconds=10)
        self._block_model("model-a", retry_at)

        self.assertIsNone(self._claim("worker-1"))
        self.assertIsNone(self._claim("worker-2"))
        with self.sessions() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.id.in_((first, second))
                    )
                )
            )
            self.assertEqual([job.attempt_count for job in jobs], [0, 0])
            self.assertTrue(all(job.claimed_by is None for job in jobs))

    def test_independently_eligible_model_can_be_claimed(self):
        self._analysis_job("blocked", "model-a", priority=10)
        eligible = self._analysis_job("eligible", "model-b")
        self._block_model("model-a", NOW + timedelta(seconds=10))

        claimed = self._claim("worker")
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, eligible)

    def test_accepted_start_prevents_same_model_queue_churn(self):
        first = self._analysis_job("first", "model-a")
        second = self._analysis_job("second", "model-a")
        claimed = self._claim("worker-1")
        self.assertEqual(claimed.id, first)

        with self.sessions.begin() as session:
            decision = AiModelRateLimitRepository(session).reserve_start(
                tenant_id="tenant",
                provider="gemini",
                model="model-a",
                rpm=12,
                minimum_interval_seconds=10,
                now=NOW,
            )
            self.assertTrue(decision.allowed)

        self.assertIsNone(self._claim("worker-2"))
        with self.sessions() as session:
            remaining = session.get(ProcessingJobModel, second)
            self.assertEqual(remaining.attempt_count, 0)
            self.assertIsNone(remaining.claimed_by)


if __name__ == "__main__":
    unittest.main()
