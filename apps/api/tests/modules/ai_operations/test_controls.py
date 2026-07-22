import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.domain.providers.registry import AiProviderRegistry
from app.main import app
from app.modules.ai_governance.model import AiCostRateModel, TenantAiBudgetPolicyModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
from app.modules.processing_policy.model import (
    ProcessingPolicyAuditModel, TenantProcessingPolicyModel, TenantProviderPolicyModel,
)


class _Provider:
    def __init__(self, name, model, *, batch=True):
        self.provider_name = name
        self.default_model = model
        self.supports_single = True
        self.supports_batch = batch


class AiOperationsControlsTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.settings = Settings(
            GEMINI_API_KEY="test-key",
            GEMINI_MODEL="g-model",
            GEMINI_ALLOWED_MODELS="g-model",
            OPENAI_AI_ENABLED=True,
            OPENAI_API_KEY="test-key",
            OPENAI_DEFAULT_MODEL="o-model",
            OPENAI_ALLOWED_MODELS="o-model",
        )
        with self.factory() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-a", pipeline_enabled=True,
                ai_analysis_enabled=True,
            ))
            self.failed = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-1",
                idempotency_key="failed", provider_key="gemini", provider_scope="ai",
                status="failed", attempt_count=5, max_attempts=5,
                last_error_code="provider_timeout", payload_json={"token": "never-return"},
            )
            self.queued = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-2",
                idempotency_key="queued", provider_key="openai", provider_scope="ai",
                status="pending", payload_json={"api_key": "never-return"},
            )
            self.running = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-3",
                idempotency_key="running", provider_key="gemini", provider_scope="ai",
                status="processing", claimed_by="worker", attempt_count=1,
                payload_json={},
            )
            session.add_all([self.failed, self.queued, self.running])
            session.commit()
            self.ids = (self.failed.id, self.queued.id, self.running.id)
        app.dependency_overrides[require_processing_admin] = lambda: ProcessingAdmin(
            actor_id="tenant-a", own_tenant_id="tenant-a", platform_admin=False,
        )
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _registry(self):
        registry = AiProviderRegistry()
        registry.register("gemini", _Provider("gemini", "g-model"))
        registry.register("openai", _Provider("openai", "o-model"))
        return registry

    def request(self, method, path, body, **params):
        with (
            patch("app.modules.ai_operations.control_router.SessionLocal", self.factory),
            patch("app.modules.ai_operations.control_router.get_settings", return_value=self.settings),
            patch("app.modules.ai_operations.control_router.build_ai_provider_registry", side_effect=lambda _settings: self._registry()),
        ):
            return self.client.request(method, path, json=body, params=params)

    def add_rate(self, provider, model, mode):
        with self.factory() as session:
            session.add(AiCostRateModel(
                provider=provider, model=model, processing_mode=mode,
                effective_at=datetime.now(timezone.utc),
                input_unit_cost=0.001, output_unit_cost=0.001,
                media_unit_cost=0.001, currency="USD",
            ))
            session.commit()

    def test_pause_resume_and_provider_controls_are_audited(self):
        paused = self.request("POST", "/api/v1/admin/ai-operations/controls/pause", {"reason": "maintenance"})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()["policy"]["ai_analysis_enabled"])
        resumed = self.request("POST", "/api/v1/admin/ai-operations/controls/resume", {"reason": "complete"})
        self.assertTrue(resumed.json()["policy"]["ai_analysis_enabled"])
        provider = self.request("POST", "/api/v1/admin/ai-operations/providers/openai/pause", {"reason": "outage"})
        self.assertEqual(provider.status_code, 200)
        self.assertTrue(provider.json()["policy"]["processing_paused"])
        provider = self.request("POST", "/api/v1/admin/ai-operations/providers/openai/resume", {"reason": "restored"})
        self.assertFalse(provider.json()["policy"]["processing_paused"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingPolicyAuditModel)), 4)

    def test_defaults_validate_model_and_cost_rates(self):
        invalid = self.request("PATCH", "/api/v1/admin/ai-operations/controls/defaults", {
            "provider": "gemini", "model": "arbitrary", "reason": "test",
        })
        self.assertEqual(invalid.status_code, 422)
        missing = self.request("PATCH", "/api/v1/admin/ai-operations/controls/defaults", {
            "provider": "gemini", "model": "g-model", "reason": "test",
        })
        self.assertEqual(missing.status_code, 409)
        self.assertEqual(missing.json()["detail"]["code"], "missing_cost_rate")
        self.add_rate("gemini", "g-model", "single")
        self.add_rate("gemini", "g-model", "batch")
        valid = self.request("PATCH", "/api/v1/admin/ai-operations/controls/defaults", {
            "provider": "gemini", "model": "g-model", "reason": "approved",
        })
        self.assertEqual(valid.status_code, 200)
        self.assertEqual(valid.json()["policy"]["default_ai_provider"], "gemini")

    def test_modes_concurrency_and_budget(self):
        self.add_rate("openai", "o-model", "single")
        updated = self.request("PATCH", "/api/v1/admin/ai-operations/providers/openai", {
            "single_enabled": True, "batch_enabled": False,
            "active_jobs_limit": 3, "single_active_jobs_limit": 2,
            "tenant_ai_active_jobs_limit": 4, "reason": "capacity",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["policy"]["active_jobs_limit"], 3)
        invalid = self.request("PATCH", "/api/v1/admin/ai-operations/providers/openai", {
            "active_jobs_limit": 0, "reason": "invalid",
        })
        self.assertEqual(invalid.status_code, 422)
        budget = self.request("PATCH", "/api/v1/admin/ai-operations/budget", {
            "daily_limit_micros": 1000, "monthly_limit_micros": 10000,
            "currency": "USD", "reason": "budget",
        })
        self.assertEqual(budget.status_code, 200)
        self.assertEqual(budget.json()["budget"]["daily_limit_micros"], 1000)
        with self.factory() as session:
            self.assertEqual(session.get(TenantProcessingPolicyModel, "tenant-a").ai_active_jobs_limit, 4)
            self.assertEqual(session.get(TenantAiBudgetPolicyModel, "tenant-a").monthly_limit_micros, 10000)

    def test_retry_is_idempotent_and_cancel_distinguishes_states(self):
        failed_id, queued_id, running_id = self.ids
        first = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{failed_id}/retry", {"reason": "transient"})
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.json()["outcome"], "retry_requested")
        second = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{failed_id}/retry", {"reason": "duplicate"})
        self.assertEqual(second.json()["outcome"], "already_requested")
        queued = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{queued_id}/cancel", {"reason": "not needed"})
        self.assertEqual(queued.json()["outcome"], "queued_cancelled")
        running = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{running_id}/cancel", {"reason": "stop"})
        self.assertEqual(running.json()["outcome"], "running_cancel_requested")
        serialized = str((first.json(), queued.json(), running.json()))
        self.assertNotIn("never-return", serialized)
        self.assertNotIn("api_key", serialized)

    def test_authorization_and_tenant_isolation(self):
        cross = self.request(
            "POST", "/api/v1/admin/ai-operations/controls/pause",
            {"reason": "no"}, tenant_id="tenant-b",
        )
        self.assertEqual(cross.status_code, 403)
        app.dependency_overrides.clear()
        response = self.request(
            "POST", "/api/v1/admin/ai-operations/controls/pause", {"reason": "no"}
        )
        self.assertEqual(response.status_code, 401)


if __name__ == "__main__":
    unittest.main()
