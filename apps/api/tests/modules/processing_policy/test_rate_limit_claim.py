import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_governance.model import AiModelRateLimitStateModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService
from app.modules.processing_policy.claim import AI_MODEL_SLOT_PAYLOAD_KEY
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
        self.settings = Settings()
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
                    total_active_jobs_limit=20,
                    ai_active_jobs_limit=20,
                )
            )

    def tearDown(self):
        self.engine.dispose()
        self.directory.cleanup()

    def _analysis_job(self, key: str, *, priority: int = 0) -> str:
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
                ai_model=self.settings.gemini_model_pool[0],
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

    def _storage_job(self) -> str:
        with self.sessions.begin() as session:
            return ProcessingRepository(session).create_job(
                tenant_id="tenant",
                job_type="asset_store",
                entity_type="asset_pipeline",
                entity_id="pipeline",
                idempotency_key="store:pipeline",
                payload={"pipeline_id": "pipeline"},
                next_attempt_at=NOW,
                provider_key="google_drive_managed",
                provider_scope="storage",
            ).id

    def _claim(
        self,
        worker: str,
        now: datetime = NOW,
        allowed_job_types: tuple[str, ...] = ("asset_analyze",),
    ):
        with self.sessions() as session:
            return ProcessingJobService(ProcessingRepository(session)).claim_next(
                worker_id=worker,
                lease_seconds=60,
                now=now,
                enforce_tenant_policy=True,
                allowed_job_types=allowed_job_types,
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

    def _block_all_models(self) -> None:
        for model in self.settings.gemini_model_pool:
            self._block_model(model, NOW + timedelta(seconds=30))

    def test_all_delayed_jobs_are_left_untouched_before_claim(self):
        job_ids = [self._analysis_job(str(index)) for index in range(100)]
        self._block_all_models()

        for index in range(10):
            self.assertIsNone(self._claim(f"worker-{index}"))
        with self.sessions() as session:
            jobs = list(
                session.scalars(
                    select(ProcessingJobModel).where(ProcessingJobModel.id.in_(job_ids))
                )
            )
            self.assertEqual(sum(job.attempt_count for job in jobs), 0)
            self.assertTrue(all(job.status == "pending" for job in jobs))
            self.assertTrue(all(job.claimed_by is None for job in jobs))
            self.assertTrue(all(job.last_error_code is None for job in jobs))

    def test_claim_reserves_one_job_per_available_model_slot(self):
        job_ids = [self._analysis_job(str(index)) for index in range(100)]

        claimed = [
            self._claim(f"worker-{index}")
            for index in range(len(self.settings.gemini_model_pool))
        ]
        self.assertTrue(all(job is not None for job in claimed))
        reserved_models = {
            job.payload_json[AI_MODEL_SLOT_PAYLOAD_KEY]["model"] for job in claimed
        }
        self.assertEqual(reserved_models, set(self.settings.gemini_model_pool))
        self.assertIsNone(self._claim("worker-extra"))

        claimed_ids = {job.id for job in claimed}
        with self.sessions() as session:
            untouched = list(
                session.scalars(
                    select(ProcessingJobModel).where(
                        ProcessingJobModel.id.in_(set(job_ids) - claimed_ids)
                    )
                )
            )
            self.assertEqual(len(untouched), 100 - len(claimed_ids))
            self.assertTrue(all(job.attempt_count == 0 for job in untouched))
            self.assertTrue(all(job.claimed_by is None for job in untouched))

    def test_blocked_primary_uses_next_pool_model(self):
        job_id = self._analysis_job("fallback")
        primary, fallback = self.settings.gemini_model_pool[:2]
        self._block_model(primary, NOW + timedelta(seconds=30))

        claimed = self._claim("worker")
        self.assertEqual(claimed.id, job_id)
        marker = claimed.payload_json[AI_MODEL_SLOT_PAYLOAD_KEY]
        self.assertEqual(marker["model"], fallback)
        with self.sessions() as session:
            primary_state = session.scalar(
                select(AiModelRateLimitStateModel).where(
                    AiModelRateLimitStateModel.tenant_id == "tenant",
                    AiModelRateLimitStateModel.model == primary,
                )
            )
            self.assertEqual(
                primary_state.next_eligible_at.replace(tzinfo=timezone.utc),
                NOW + timedelta(seconds=30),
            )

    def test_unrelated_job_is_claimed_when_ai_pool_is_blocked(self):
        self._analysis_job("blocked", priority=10)
        storage_job_id = self._storage_job()
        self._block_all_models()

        claimed = self._claim(
            "worker", allowed_job_types=("asset_analyze", "asset_store")
        )
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed.id, storage_job_id)


if __name__ == "__main__":
    unittest.main()
