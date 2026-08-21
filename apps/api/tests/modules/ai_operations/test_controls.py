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
from app.modules.ai_metadata.model import MetadataProfileModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
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
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure", "ai_budget.read", "ai_budget.update", "ai_emergency_stop", "ai_jobs.retry", "ai_jobs.cancel"}),
            platform_admin=False, session_id=None, authorization_source="tenant_rbac",
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

    def set_principal(self, *permissions, platform_admin=False):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="platform-user" if platform_admin else "user-a",
            active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset(),
            effective_permissions=frozenset(permissions), platform_admin=platform_admin,
            session_id=None, authorization_source="platform_admin" if platform_admin else "tenant_rbac",
        )

    def test_durable_rbac_role_boundaries_and_audit_actor(self):
        self.set_principal()
        self.assertEqual(self.request("GET", "/api/v1/admin/ai-operations/configuration", {}).status_code, 403)
        self.set_principal("ai_operations.read", "ai_analysis.run", "ai_jobs.retry", "ai_jobs.cancel")
        self.assertEqual(self.request("GET", "/api/v1/admin/ai-operations/configuration", {}).status_code, 200)
        self.assertEqual(self.request("PATCH", "/api/v1/admin/ai-operations/providers/gemini", {"single_enabled": True, "reason": "denied"}).status_code, 403)
        self.set_principal("ai_operations.read", "ai_budget.read", "ai_budget.update")
        budget = self.request("PATCH", "/api/v1/admin/ai-operations/budget", {"daily_limit_micros": 1000, "currency": "USD", "reason": "billing"})
        self.assertEqual(budget.status_code, 200)
        self.assertEqual(self.request("PATCH", "/api/v1/admin/ai-operations/providers/gemini", {"single_enabled": True, "reason": "denied"}).status_code, 403)
        self.set_principal("ai_provider.configure")
        configured = self.request("PATCH", "/api/v1/admin/ai-operations/configuration", {"daily_item_limit": 25, "reason": "rbac actor"})
        self.assertEqual(configured.status_code, 200)
        self.assertEqual(self.request("POST", "/api/v1/admin/ai-operations/providers/gemini/pause", {"reason": "denied"}).status_code, 403)
        self.set_principal("ai_emergency_stop")
        self.assertEqual(self.request("POST", "/api/v1/admin/ai-operations/providers/gemini/pause", {"reason": "incident"}).status_code, 200)
        self.assertEqual(self.request("POST", "/api/v1/admin/ai-operations/providers/gemini/resume", {"reason": "resolved"}).status_code, 200)
        self.set_principal("ai_provider.configure")
        with self.factory() as session:
            audit = session.scalar(select(ProcessingPolicyAuditModel).where(ProcessingPolicyAuditModel.action == "tenant_policy_updated").order_by(ProcessingPolicyAuditModel.created_at.desc()))
            self.assertEqual(audit.actor_id, "user-a")
            self.assertEqual(audit.tenant_id, "tenant-a")


    def test_pause_resume_and_provider_controls_are_audited(self):
        paused = self.request("POST", "/api/v1/admin/ai-operations/controls/pause", {"reason": "maintenance"})
        self.assertEqual(paused.status_code, 200)
        self.assertFalse(paused.json()["policy"]["ai_analysis_enabled"])
        # A dashboard reload reads /configuration. It must expose the persisted
        # AI-specific flag rather than the unrelated general pipeline pause.
        reloaded = self.request("GET", "/api/v1/admin/ai-operations/configuration", {})
        self.assertEqual(reloaded.status_code, 200)
        self.assertFalse(reloaded.json()["tenant"]["ai_enabled"])
        self.assertFalse(reloaded.json()["tenant"]["processing_paused"])
        resumed = self.request("POST", "/api/v1/admin/ai-operations/controls/resume", {"reason": "complete"})
        self.assertTrue(resumed.json()["policy"]["ai_analysis_enabled"])
        provider = self.request("POST", "/api/v1/admin/ai-operations/providers/openai/pause", {"reason": "outage"})
        self.assertEqual(provider.status_code, 200)
        self.assertTrue(provider.json()["policy"]["processing_paused"])
        provider = self.request("POST", "/api/v1/admin/ai-operations/providers/openai/resume", {"reason": "restored"})
        self.assertFalse(provider.json()["policy"]["processing_paused"])
        video = self.request("POST", "/api/v1/admin/ai-operations/controls/video/pause", {"reason": "video maintenance"})
        self.assertEqual(video.status_code, 200)
        self.assertTrue(video.json()["policy"]["processing_paused"])
        reloaded = self.request("GET", "/api/v1/admin/ai-operations/configuration", {})
        self.assertFalse(reloaded.json()["tenant"]["video_enabled"])
        self.assertTrue(reloaded.json()["tenant"]["ai_enabled"])
        video = self.request("POST", "/api/v1/admin/ai-operations/controls/video/resume", {"reason": "video restored"})
        self.assertEqual(video.status_code, 200)
        self.assertFalse(video.json()["policy"]["processing_paused"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingPolicyAuditModel)), 6)

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

    def test_configuration_is_public_tenant_scoped_and_audited(self):
        with self.factory() as session:
            session.add(MetadataProfileModel(
                tenant_id="tenant-a", profile_name="creative", profile_version="1",
                prompt_template="Describe the asset", active=True,
            ))
            session.commit()
        response = self.request("GET", "/api/v1/admin/ai-operations/configuration", {})
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["permissions"]["can_manage_global"])
        self.assertTrue(payload["permissions"]["can_manage_tenant"])
        self.assertEqual({item["id"] for item in payload["providers"]}, {"gemini", "openai"})
        self.assertTrue(all("connection_configured" in item for item in payload["providers"]))
        self.assertIn("creative", payload["metadata_profiles"])
        serialized = str(payload).lower()
        self.assertNotIn("test-key", serialized)
        self.assertNotIn("api_key", serialized)

        updated = self.request("PATCH", "/api/v1/admin/ai-operations/configuration", {
            "default_mode": "single", "default_metadata_profile": "creative",
            "auto_analyze_new_assets": True, "daily_item_limit": 250,
            "retry_count": 4, "timeout_seconds": 90, "reason": "operations policy",
        })
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["policy"]["daily_ai_item_limit"], 250)
        self.assertEqual(updated.json()["audit"]["action"], "ai_configuration_updated")
        with self.factory() as session:
            policy = session.get(TenantProcessingPolicyModel, "tenant-a")
            self.assertEqual(policy.default_metadata_profile, "creative")
            self.assertEqual(policy.ai_timeout_seconds, 90)
            self.assertGreaterEqual(session.scalar(select(func.count()).select_from(ProcessingPolicyAuditModel)), 1)

    def test_configuration_validation_and_platform_permissions(self):
        invalid = self.request("PATCH", "/api/v1/admin/ai-operations/configuration", {
            "daily_item_limit": 0, "reason": "invalid",
        })
        self.assertEqual(invalid.status_code, 422)
        missing_profile = self.request("PATCH", "/api/v1/admin/ai-operations/configuration", {
            "default_metadata_profile": "missing", "reason": "invalid",
        })
        self.assertEqual(missing_profile.status_code, 422)
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="platform-user", active_tenant_id="tenant-a", membership_id="membership-platform",
            external_identity=None, effective_roles=frozenset(), effective_permissions=frozenset(),
            platform_admin=True, session_id=None, authorization_source="platform_admin",
        )
        response = self.request("GET", "/api/v1/admin/ai-operations/configuration", {}, tenant_id="tenant-a")
        self.assertTrue(response.json()["permissions"]["can_manage_global"])
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

    def test_retry_jobs_by_error_code_requeues_only_matching_failed_jobs(self):
        with self.factory() as session:
            grouped = []
            for index in range(3):
                grouped.append(ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id=f"analysis-group-{index}",
                    idempotency_key=f"grouped-{index}", provider_key="gemini", provider_scope="ai",
                    status="failed", attempt_count=5, max_attempts=5,
                    last_error_code="analysis_image_dimensions", payload_json={},
                ))
            unrelated = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-other",
                idempotency_key="other-failure", provider_key="gemini", provider_scope="ai",
                status="failed", attempt_count=5, max_attempts=5,
                last_error_code="analysis_storage_read_failed", payload_json={},
            )
            session.add_all([*grouped, unrelated])
            session.commit()
            grouped_ids = [job.id for job in grouped]
            unrelated_id = unrelated.id
        response = self.request("POST", "/api/v1/admin/ai-operations/jobs/retry-by-error", {
            "error_code": "analysis_image_dimensions", "reason": "image preparation fixed", "limit": 1000,
        })
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["matched"], 3)
        self.assertEqual(payload["retried"], 3)
        self.assertEqual(payload["skipped"], 0)
        with self.factory() as session:
            jobs = [session.get(ProcessingJobModel, job_id) for job_id in grouped_ids]
            self.assertTrue(all(job.status == "retry" for job in jobs))
            self.assertEqual(session.get(ProcessingJobModel, unrelated_id).status, "failed")
        second = self.request("POST", "/api/v1/admin/ai-operations/jobs/retry-by-error", {
            "error_code": "analysis_image_dimensions", "reason": "duplicate request", "limit": 1000,
        })
        self.assertEqual(second.status_code, 200)
        self.assertEqual(second.json()["matched"], 0)

    def test_deferred_job_requires_explicit_force_retry(self):
        with self.factory() as session:
            job = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-deferred",
                idempotency_key="deferred", provider_key="gemini", provider_scope="ai",
                status="pending", attempt_count=1, max_attempts=3,
                next_attempt_at=datetime.now(timezone.utc).replace(microsecond=0).replace(year=2099),
                last_error_code="gemini_quota_deferred", payload_json={},
            )
            session.add(job)
            session.commit()
            job_id = job.id
        blocked = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{job_id}/retry", {"reason": "too early"})
        self.assertEqual(blocked.status_code, 409)
        forced = self.request("POST", f"/api/v1/admin/ai-operations/jobs/{job_id}/retry", {"reason": "operator override", "force": True})
        self.assertEqual(forced.status_code, 200)
        self.assertEqual(forced.json()["outcome"], "force_retry_requested")
        with self.factory() as session:
            job = session.get(ProcessingJobModel, job_id)
            self.assertEqual(job.status, "pending")
            self.assertLessEqual(job.next_attempt_at.replace(tzinfo=timezone.utc) if job.next_attempt_at.tzinfo is None else job.next_attempt_at, datetime.now(timezone.utc))
            audit = session.scalar(select(ProcessingPolicyAuditModel).where(ProcessingPolicyAuditModel.action == "ai_job_force_retry_requested"))
            self.assertIsNotNone(audit)

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
