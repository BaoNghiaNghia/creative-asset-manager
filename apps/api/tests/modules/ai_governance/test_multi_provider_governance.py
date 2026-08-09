import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_governance.metrics import AiMetrics
from app.modules.ai_governance.model import (
    AiBudgetEventModel, AiCostRateModel, AiRuntimeControlModel,
)
from app.modules.ai_governance.repository import (
    AiGovernanceRepository, MissingCostRateError, ProviderGovernanceBlocked,
)
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import (
    TenantProcessingPolicyModel, TenantProviderPolicyModel,
)


class MultiProviderGovernanceTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine)
        self.settings = Settings()

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def _tenant(self):
        self.session.add(TenantProcessingPolicyModel(
            tenant_id="tenant-a", pipeline_enabled=True,
            ai_analysis_enabled=True, total_active_jobs_limit=10,
            ai_active_jobs_limit=10,
        ))
        self.session.flush()

    def test_cost_rates_are_provider_model_mode_and_effective_dated(self):
        repo = AiGovernanceRepository(self.session)
        with self.assertRaises(MissingCostRateError):
            repo.require_cost_rate("openai", "gpt-test", "single")
        self.session.add_all([
            AiCostRateModel(
                provider="openai", model="gpt-test", processing_mode="single",
                effective_at=datetime.now(timezone.utc) - timedelta(days=2),
                input_unit_cost=.001, output_unit_cost=.002, media_unit_cost=.003,
            ),
            AiCostRateModel(
                provider="openai", model="gpt-test", processing_mode="batch",
                effective_at=datetime.now(timezone.utc) - timedelta(days=1),
                input_unit_cost=.0005, output_unit_cost=.001, media_unit_cost=.0015,
            ),
        ])
        self.session.flush()
        self.assertEqual(repo.require_cost_rate("openai", "gpt-test", "single").processing_mode, "single")
        self.assertEqual(repo.require_cost_rate("openai", "gpt-test", "batch").processing_mode, "batch")

    def test_provider_policies_are_independent_and_runtime_stop_is_immediate(self):
        self.session.add_all([
            TenantProviderPolicyModel(
                tenant_id="tenant-a", provider_key="gemini", provider_scope="ai",
                processing_enabled=True, single_enabled=True, batch_enabled=False,
            ),
            TenantProviderPolicyModel(
                tenant_id="tenant-a", provider_key="openai", provider_scope="ai",
                processing_enabled=False, single_enabled=True, batch_enabled=True,
            ),
        ])
        self.session.flush()
        repo = AiGovernanceRepository(self.session)
        repo.assert_provider_allowed("tenant-a", "gemini", "single")
        with self.assertRaises(ProviderGovernanceBlocked):
            repo.assert_provider_allowed("tenant-a", "gemini", "batch")
        with self.assertRaises(ProviderGovernanceBlocked):
            repo.assert_provider_allowed("tenant-a", "openai", "single")
        repo.set_runtime_stop("gemini", True, "platform-admin", "incident")
        with self.assertRaises(ProviderGovernanceBlocked):
            repo.assert_provider_allowed("tenant-a", "gemini", "single")
        repo.set_runtime_stop("gemini", False, "platform-admin", "resolved")
        repo.assert_provider_allowed("tenant-a", "gemini", "single")
        self.assertGreaterEqual(
            len(self.session.scalars(select(AiBudgetEventModel)).all()), 2)

    def test_preclaim_honors_provider_mode_limit_and_runtime_stop(self):
        self._tenant()
        provider = TenantProviderPolicyModel(
            tenant_id="tenant-a", provider_key="openai", provider_scope="ai",
            processing_enabled=True, single_enabled=True, batch_enabled=True,
            active_jobs_limit=10, single_active_jobs_limit=1,
            batch_active_jobs_limit=1,
        )
        self.session.add(provider)
        analyses = [
            AssetAiAnalysisModel(
                id=f"analysis-a{index}",
                tenant_id="tenant-a",
                asset_id=f"asset-a{index}",
                content_hash=str(index) * 64,
                metadata_profile_id="profile",
                metadata_profile="creative-assets",
                metadata_profile_version="v1",
                prompt_version="prompt-v1",
                pipeline_version="pipeline-v1",
                ai_provider="openai",
                ai_model="gpt-test",
            )
            for index in (1, 2)
        ]
        self.session.add_all(analyses)
        self.session.flush()
        jobs = ProcessingRepository(self.session)
        first = jobs.create_job(
            tenant_id="tenant-a", job_type="asset_analyze", entity_type="asset_pipeline",
            entity_id="pipeline-a1", idempotency_key="a1",
            payload={"analysis_id": analyses[0].id}, provider_key="openai",
            provider_scope="ai",
        )
        jobs.create_job(
            tenant_id="tenant-a", job_type="asset_analyze", entity_type="asset_pipeline",
            entity_id="pipeline-a2", idempotency_key="a2",
            payload={"analysis_id": analyses[1].id}, provider_key="openai",
            provider_scope="ai",
        )
        self.session.commit()
        claimed = jobs.claim_next_job(
            worker_id="w1", lease_seconds=60, enforce_tenant_policy=True,
            allowed_job_types=("asset_analyze",),
        )
        self.assertEqual(claimed.id, first.id)
        self.session.commit()
        self.assertIsNone(jobs.claim_next_job(
            worker_id="w2", lease_seconds=60, enforce_tenant_policy=True,
            allowed_job_types=("asset_analyze",),
        ))
        jobs.complete_job(job_id=claimed.id, worker_id="w1")
        self.session.commit()
        self.assertIsNotNone(jobs.claim_next_job(
            worker_id="w2", lease_seconds=60, enforce_tenant_policy=True,
            allowed_job_types=("asset_analyze",),
        ))
        self.session.rollback()
        self.session.add(AiRuntimeControlModel(
            control_key="openai", stopped=True, reason="incident",
            updated_at=datetime.now(timezone.utc)))
        self.session.commit()
        self.assertIsNone(jobs.claim_next_job(
            worker_id="w3", lease_seconds=60, enforce_tenant_policy=True,
            allowed_job_types=("asset_analyze",),
        ))

    def test_reservation_identity_and_provider_budget_are_separate(self):
        self.session.add_all([
            TenantProviderPolicyModel(
                tenant_id="tenant-a", provider_key="openai", provider_scope="ai",
                daily_budget_limit_micros=100, monthly_budget_limit_micros=100,
            ),
            TenantProviderPolicyModel(
                tenant_id="tenant-a", provider_key="gemini", provider_scope="ai",
                daily_budget_limit_micros=1000, monthly_budget_limit_micros=1000,
            ),
        ])
        self.session.flush()
        service = AiBudgetService(AiGovernanceRepository(self.session), self.settings)
        first = service.reserve(
            tenant_id="tenant-a",
            operation_key="openai:gpt-test:single:analysis-1:attempt:1",
            estimated_cost_micros=80, provider="openai", model="gpt-test",
            processing_mode="single", operation_item_id="analysis-1", attempt_number=1,
        )
        second = service.reserve(
            tenant_id="tenant-a",
            operation_key="gemini:gemini-test:single:analysis-1:attempt:1",
            estimated_cost_micros=80, provider="gemini", model="gemini-test",
            processing_mode="single", operation_item_id="analysis-1", attempt_number=1,
        )
        denied = service.reserve(
            tenant_id="tenant-a",
            operation_key="openai:gpt-test:batch:item-2:attempt:1",
            estimated_cost_micros=30, provider="openai", model="gpt-test",
            processing_mode="batch", operation_item_id="item-2", attempt_number=1,
        )
        self.assertTrue(first.allowed)
        self.assertTrue(second.allowed)
        self.assertFalse(denied.allowed)

    def test_override_is_tenant_scoped_and_audited(self):
        asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
        self.session.add(asset); self.session.flush()
        profile = AiMetadataRepository(self.session).create_profile(
            tenant_id="tenant-a", profile_name="p", profile_version="1",
            prompt_template="analyze")
        analysis = AiMetadataRepository(self.session).create_analysis(
            tenant_id="tenant-a", asset_id=asset.id, metadata_profile_id=profile.id,
            prompt_version="1", pipeline_version="1")
        repo = AiGovernanceRepository(self.session)
        repo.grant_budget_override("tenant-a", analysis.id, "admin", "approved exception")
        self.assertTrue(repo.has_budget_override("tenant-a", analysis.id))
        self.assertFalse(repo.has_budget_override("tenant-b", analysis.id))
        event = self.session.scalar(select(AiBudgetEventModel).where(
            AiBudgetEventModel.action == "budget_override"))
        self.assertEqual(event.actor_id, "admin")

    def test_metric_labels_are_bounded(self):
        metrics = AiMetrics()
        metrics.increment("ai_requests", provider="tenant-secret", mode="asset-123",
                          outcome="raw-provider-error")
        row = metrics.snapshot()["counters"][0]
        self.assertEqual((row["provider"], row["mode"], row["outcome"]),
                         ("other", "other", "other"))


if __name__ == "__main__":
    unittest.main()
