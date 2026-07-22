import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_governance.model import AiBudgetEventModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import (
    TenantProcessingPolicyModel,
    TenantProviderPolicyModel,
)


class AssetAnalysisAdminApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            self.engine, class_=Session, expire_on_commit=False
        )
        with self.factory() as session:
            asset = AssetModel(tenant_id="tenant-a", content_hash="b" * 64)
            session.add(asset)
            session.flush()
            AiMetadataRepository(session).create_profile(
                tenant_id="tenant-a",
                profile_name="general",
                profile_version="1",
                prompt_template="Analyze",
            )
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-a",
                pipeline_enabled=True,
                ai_analysis_enabled=True,
            ))
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-b",
                pipeline_enabled=True,
                ai_analysis_enabled=True,
            ))
            session.commit()
            self.asset_id = asset.id
        self.client = TestClient(app)
        self._set_principal("tenant-a")
        self.settings = Settings(
            UNIFIED_ASSET_INGESTION_ENABLED=True,
            PROCESSING_JOBS_ENABLED=True,
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            AI_BATCH_ANALYSIS_ENABLED=True,
            GEMINI_API_KEY="test-only",
            GEMINI_MODEL="gemini-test",
            GEMINI_ALLOWED_MODELS="gemini-test,gemini-alt",
            OPENAI_AI_ENABLED=True,
            OPENAI_API_KEY="sk-openai-secret",
            OPENAI_DEFAULT_MODEL="openai-test",
            OPENAI_ALLOWED_MODELS="openai-test,openai-alt",
            OPENAI_BATCH_ENABLED=True,
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _set_principal(self, tenant_id: str):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id=tenant_id, membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"ai_analysis.run", "ai_analysis.force", "ai_operations.read"}),
            platform_admin=False, session_id=None, authorization_source="tenant_rbac",
        )


    def test_authenticated_enqueue_is_async_and_idempotent(self):
        body = {
            "asset_id": self.asset_id,
            "metadata_profile": "general",
            "source_provider": "google-drive",
        }
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=SimpleNamespace(user={"id": "tenant-a"}),
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=self.settings,
            ),
        ):
            first = self.client.post("/api/v1/admin/asset-analyses", json=body)
            second = self.client.post("/api/v1/admin/asset-analyses", json=body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["analysis_id"], second.json()["analysis_id"])
        self.assertEqual(first.json()["job_id"], second.json()["job_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 1
            )

    def test_force_creates_new_history_and_job(self):
        body = {
            "asset_id": self.asset_id,
            "metadata_profile": "general",
            "force": True,
        }
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=SimpleNamespace(user={"id": "tenant-a"}),
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=self.settings,
            ),
        ):
            first = self.client.post("/api/v1/admin/asset-analyses", json=body)
            second = self.client.post("/api/v1/admin/asset-analyses", json=body)
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertNotEqual(first.json()["analysis_id"], second.json()["analysis_id"])
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2
            )
            events = list(session.scalars(select(AiBudgetEventModel).where(AiBudgetEventModel.action == "forced_analysis")))
            self.assertEqual(len(events), 2)
            self.assertTrue(all(event.actor_id == "user-a" and event.tenant_id == "tenant-a" for event in events))

    def test_unauthenticated_and_disabled_requests_are_rejected(self):
        app.dependency_overrides.clear()
        body = {"asset_id": self.asset_id, "metadata_profile": "general"}
        with (
            patch("app.modules.ai_metadata.router.get_settings", return_value=self.settings),
            patch("app.modules.authorization.principal.get_google_session", return_value=None),
        ):
            self.assertEqual(
                self.client.post("/api/v1/admin/asset-analyses", json=body).status_code,
                401,
            )
        self._set_principal("tenant-a")
        with (
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=Settings(),
            ),
            patch("app.modules.authorization.principal.get_google_session",
                  return_value=SimpleNamespace(user={"id": "tenant-a"})),
        ):
            self.assertEqual(
                self.client.post("/api/v1/admin/asset-analyses", json=body).status_code,
                503,
            )
    def _request(self, body, *, settings=None, tenant_id="tenant-a"):
        self._set_principal(tenant_id)
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=SimpleNamespace(user={"id": tenant_id}),
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=settings or self.settings,
            ),
        ):
            return self.client.post(
                "/api/v1/admin/asset-analyses", json=body
            )

    def _capabilities(self, *, settings=None, tenant_id="tenant-a"):
        self._set_principal(tenant_id)
        with (
            patch("app.modules.ai_metadata.router.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=SimpleNamespace(user={"id": tenant_id}),
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
            patch(
                "app.modules.ai_metadata.router.get_settings",
                return_value=settings or self.settings,
            ),
        ):
            return self.client.get("/api/v1/admin/ai/capabilities")

    def _body(self, **changes):
        body = {
            "asset_id": self.asset_id,
            "metadata_profile": "general",
        }
        body.update(changes)
        return body

    def test_provider_model_and_processing_mode_selection(self):
        cases = (
            ("gemini", "single", None, "gemini-test"),
            ("gemini", "batch", "gemini-alt", "gemini-alt"),
            ("openai", "single", None, "openai-test"),
            ("openai", "batch", "openai-alt", "openai-alt"),
        )
        responses = []
        for provider, mode, model, expected_model in cases:
            body = self._body(
                ai_provider=provider,
                processing_mode=mode,
            )
            if model is not None:
                body["ai_model"] = model
            response = self._request(body)
            self.assertEqual(response.status_code, 202)
            self.assertEqual(response.json()["provider"], provider)
            self.assertEqual(response.json()["model"], expected_model)
            self.assertEqual(response.json()["processing_mode"], mode)
            responses.append(response.json())
        self.assertEqual(
            len({value["analysis_id"] for value in responses}), 4
        )
        with self.factory() as session:
            jobs = list(session.scalars(select(ProcessingJobModel)))
            self.assertEqual(
                {job.job_type for job in jobs},
                {"asset_analyze", "ai_batch_prepare"},
            )
            self.assertEqual(
                {job.provider_key for job in jobs},
                {"gemini", "openai"},
            )

    def test_compatibility_defaults_and_cross_provider_idempotency(self):
        compatible = self._request(self._body())
        self.assertEqual(compatible.status_code, 202)
        self.assertEqual(compatible.json()["provider"], "gemini")
        self.assertEqual(compatible.json()["processing_mode"], "single")
        self.assertEqual(compatible.json()["model"], "gemini-test")

        requests = (
            self._body(ai_provider="gemini", processing_mode="single"),
            self._body(ai_provider="openai", processing_mode="single"),
            self._body(ai_provider="openai", processing_mode="batch"),
        )
        first = [self._request(body).json() for body in requests]
        second = [self._request(body).json() for body in requests]
        self.assertEqual(
            [item["analysis_id"] for item in first],
            [item["analysis_id"] for item in second],
        )
        self.assertEqual(len({item["analysis_id"] for item in first}), 3)

    def test_invalid_model_and_unavailable_provider_are_structured(self):
        invalid = self._request(self._body(
            ai_provider="openai",
            ai_model="browser-controlled-model",
        ))
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(
            invalid.json()["detail"]["code"], "ai_model_not_allowed"
        )

        unavailable_settings = self.settings.model_copy(update={
            "OPENAI_AI_ENABLED": False,
            "OPENAI_API_KEY": None,
        })
        unavailable = self._request(
            self._body(ai_provider="openai"),
            settings=unavailable_settings,
        )
        self.assertEqual(unavailable.status_code, 503)
        self.assertEqual(
            unavailable.json()["detail"]["code"],
            "ai_provider_unavailable",
        )

    def test_openai_batch_requires_provider_batch_feature(self):
        settings = self.settings.model_copy(update={
            "OPENAI_BATCH_ENABLED": False,
        })
        response = self._request(
            self._body(
                ai_provider="openai",
                processing_mode="batch",
            ),
            settings=settings,
        )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["detail"]["code"],
            "ai_provider_unavailable",
        )

    def test_tenant_provider_policy_and_cross_tenant_isolation(self):
        with self.factory() as session:
            session.add(TenantProviderPolicyModel(
                tenant_id="tenant-a",
                provider_key="openai",
                provider_scope="ai",
                processing_enabled=False,
            ))
            session.commit()
        disabled = self._request(self._body(ai_provider="openai"))
        enabled = self._request(self._body(ai_provider="gemini"))
        self.assertEqual(disabled.status_code, 503)
        self.assertEqual(enabled.status_code, 202)

        other_tenant = self._request(
            self._body(ai_provider="gemini"),
            tenant_id="tenant-b",
        )
        self.assertEqual(other_tenant.status_code, 404)

    def test_capabilities_response_is_public_and_tenant_scoped(self):
        response = self._capabilities()
        self.assertEqual(response.status_code, 200)
        providers = {
            value["id"]: value
            for value in response.json()["providers"]
        }
        self.assertEqual(
            providers["gemini"]["supported_modes"],
            ["single", "batch"],
        )
        self.assertEqual(
            providers["openai"]["supported_modes"],
            ["single", "batch"],
        )
        self.assertNotIn("test-only", response.text)
        self.assertNotIn("sk-openai-secret", response.text)
        self.assertNotIn("OPENAI_API_KEY", response.text)

        with self.factory() as session:
            session.add(TenantProviderPolicyModel(
                tenant_id="tenant-a",
                provider_key="openai",
                provider_scope="ai",
                processing_enabled=False,
            ))
            session.commit()
        restricted = self._capabilities().json()
        openai = next(
            value
            for value in restricted["providers"]
            if value["id"] == "openai"
        )
        self.assertFalse(openai["enabled"])
        self.assertEqual(openai["supported_modes"], [])

    def test_capabilities_requires_authentication(self):
        app.dependency_overrides.clear()
        with (
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=None,
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
        ):
            response = self.client.get("/api/v1/admin/ai/capabilities")
        self.assertEqual(response.status_code, 401)
