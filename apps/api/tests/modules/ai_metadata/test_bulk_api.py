import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.main import app
from app.modules.ai_batch.repository import AiBatchRepository
from app.modules.ai_governance.model import AiCostRateModel
from datetime import datetime, timedelta, timezone
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.assets.model import AssetModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel


class BulkAssetAnalysisApiTest(unittest.TestCase):
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
            assets = [
                AssetModel(
                    tenant_id="tenant-a",
                    content_hash=f"{index:064x}",
                    mime_type="image/png",
                )
                for index in range(1, 4)
            ]
            foreign = AssetModel(
                tenant_id="tenant-b",
                content_hash=f"{99:064x}",
                mime_type="image/png",
            )
            session.add_all([*assets, foreign])
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
            session.add_all([
                AiCostRateModel(provider="gemini", model="gemini-test", processing_mode="any", effective_at=datetime.now(timezone.utc)-timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0),
                AiCostRateModel(provider="openai", model="openai-test", processing_mode="any", effective_at=datetime.now(timezone.utc)-timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0),
                AiCostRateModel(provider="gemini", model="gemini-alt", processing_mode="any", effective_at=datetime.now(timezone.utc)-timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0),
                AiCostRateModel(provider="openai", model="openai-alt", processing_mode="any", effective_at=datetime.now(timezone.utc)-timedelta(days=1), input_unit_cost=0, output_unit_cost=0, media_unit_cost=0),
            ])
            session.commit()
            self.asset_ids = [asset.id for asset in assets]
            self.foreign_asset_id = foreign.id
        self.client = TestClient(app)
        self._set_principal()
        self.settings = Settings(
            UNIFIED_ASSET_INGESTION_ENABLED=True,
            PROCESSING_JOBS_ENABLED=True,
            DYNAMIC_AI_METADATA_ENABLED=True,
            AI_SINGLE_ANALYSIS_ENABLED=True,
            AI_BATCH_ANALYSIS_ENABLED=True,
            AI_BATCH_MINIMUM_AGE_SECONDS=3600,
            AI_ANALYSIS_BULK_MAX_ITEMS=3,
            GEMINI_API_KEY="test-only",
            GEMINI_MODEL="gemini-test",
            GEMINI_ALLOWED_MODELS="gemini-test,gemini-alt",
            OPENAI_AI_ENABLED=True,
            OPENAI_API_KEY="sk-test-only",
            OPENAI_DEFAULT_MODEL="openai-test",
            OPENAI_ALLOWED_MODELS="openai-test,openai-alt",
            OPENAI_BATCH_ENABLED=True,
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def _set_principal(self, platform_admin=False):
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"operator"}),
            effective_permissions=frozenset({"ai_analysis.run", "ai_analysis.force", "ai_operations.read", "ai_jobs.cancel"}),
            platform_admin=platform_admin, session_id=None, authorization_source="tenant_rbac",
        )

    def _call(
        self, method, path, *, body=None, key=None, settings=None, admin=False
    ):
        headers = {"Idempotency-Key": key} if key else {}
        self._set_principal(admin)
        identity = SimpleNamespace(
            user={"id": "tenant-a", "is_admin": admin}
        )
        with (
            patch("app.modules.ai_metadata.bulk_router.SessionLocal", self.factory),
            patch(
                "app.modules.authorization.principal.get_google_session",
                return_value=identity,
            ),
            patch(
                "app.modules.authorization.principal.get_microsoft_session",
                return_value=None,
            ),
            patch(
                "app.modules.processing_policy.auth.get_google_session",
                return_value=identity,
            ),
            patch(
                "app.modules.processing_policy.auth.get_microsoft_session",
                return_value=None,
            ),
            patch(
                "app.modules.ai_metadata.bulk_router.get_settings",
                return_value=settings or self.settings,
            ),
        ):
            return self.client.request(method, path, json=body, headers=headers)

    def _body(self, **changes):
        body = {
            "asset_ids": self.asset_ids[:2],
            "metadata_profile": "general",
            "ai_provider": "openai",
            "processing_mode": "batch",
        }
        body.update(changes)
        return body

    def test_single_and_batch_enqueue_only_requested_job_types(self):
        single = self._call(
            "POST",
            "/api/v1/admin/asset-analyses/bulk",
            key="single-request",
            body=self._body(
                ai_provider="gemini",
                processing_mode="single",
            ),
        )
        self.assertEqual(single.status_code, 202)
        self.assertEqual(single.json()["analysis_count"], 2)
        with self.factory() as session:
            jobs = list(session.scalars(select(ProcessingJobModel)))
            self.assertEqual([job.job_type for job in jobs],
                             ["asset_analyze", "asset_analyze"])
            self.assertTrue(all(job.provider_key == "gemini" for job in jobs))

        batch = self._call(
            "POST",
            "/api/v1/admin/asset-analyses/bulk",
            key="batch-request",
            body=self._body(),
        )
        self.assertEqual(batch.status_code, 202)
        with self.factory() as session:
            jobs = list(session.scalars(
                select(ProcessingJobModel).where(
                    ProcessingJobModel.job_type == "ai_batch_prepare"
                )
            ))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].provider_key, "openai")
            self.assertEqual(
                jobs[0].payload_json["analysis_ids"],
                [item["analysis_id"] for item in batch.json()["items"]],
            )
            openai_analyses = list(session.scalars(
                select(AssetAiAnalysisModel).where(
                    AssetAiAnalysisModel.id.in_(
                        jobs[0].payload_json["analysis_ids"]
                    )
                )
            ))
            self.assertTrue(all(value.ai_provider == "openai"
                                for value in openai_analyses))
            self.assertTrue(all(value.ai_model == "openai-test"
                                for value in openai_analyses))

    def test_partial_acceptance_tenant_isolation_and_idempotency(self):
        body = self._body(asset_ids=[
            self.asset_ids[0],
            "00000000-0000-0000-0000-000000000000",
            self.foreign_asset_id,
        ])
        first = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="partial-request", body=body,
        )
        second = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="partial-request", body=body,
        )
        self.assertEqual(first.status_code, 202)
        self.assertEqual(second.status_code, 202)
        self.assertEqual(first.json()["request_id"], second.json()["request_id"])
        self.assertEqual(
            [item["acceptance_status"] for item in first.json()["items"]],
            ["accepted", "invalid_asset", "unauthorized"],
        )
        conflict = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="partial-request",
            body=self._body(asset_ids=[self.asset_ids[1]]),
        )
        self.assertEqual(conflict.status_code, 409)

    def test_maximum_items_provider_unavailable_and_single_batch_warning(self):
        maximum = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="too-many",
            body=self._body(asset_ids=[
                *self.asset_ids,
                "00000000-0000-0000-0000-000000000004",
            ]),
        )
        self.assertEqual(maximum.status_code, 413)

        unavailable_settings = self.settings.model_copy(update={
            "OPENAI_AI_ENABLED": False,
            "OPENAI_API_KEY": None,
        })
        unavailable = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="unavailable",
            body=self._body(asset_ids=[self.asset_ids[0]]),
            settings=unavailable_settings,
        )
        self.assertEqual(unavailable.status_code, 202)
        self.assertEqual(
            unavailable.json()["items"][0]["acceptance_status"],
            "provider_unavailable",
        )
        self.assertIn("delayed completion", unavailable.json()["warning"])

    def test_provider_and_model_groups_never_mix(self):
        requests = (
            ("gemini-one", "gemini", "gemini-test"),
            ("gemini-two", "gemini", "gemini-alt"),
            ("openai-one", "openai", "openai-test"),
        )
        analysis_ids = []
        for key, provider, model in requests:
            response = self._call(
                "POST", "/api/v1/admin/asset-analyses/bulk",
                key=key,
                body=self._body(
                    asset_ids=[self.asset_ids[0]],
                    ai_provider=provider,
                    ai_model=model,
                ),
            )
            self.assertEqual(response.status_code, 202)
            analysis_ids.append(response.json()["items"][0]["analysis_id"])
        with self.factory() as session:
            groups = AiBatchRepository(session).group_candidates(
                tenant_id="tenant-a",
                analysis_ids=analysis_ids,
                minimum_age_seconds=86_400,
                max_items=100,
            )
            self.assertEqual(len(groups), 3)
            identities = {
                (group[0].ai_provider, group[0].ai_model)
                for group in groups
            }
            self.assertEqual(identities, {
                ("gemini", "gemini-test"),
                ("gemini", "gemini-alt"),
                ("openai", "openai-test"),
            })

    def test_status_reports_batches_and_protects_provider_identity(self):
        created = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="status-batch",
            body=self._body(),
        ).json()
        request_id = created["request_id"]
        analysis_ids = [
            item["analysis_id"] for item in created["items"]
        ]
        with self.factory() as session:
            analyses = list(session.scalars(
                select(AssetAiAnalysisModel).where(
                    AssetAiAnalysisModel.id.in_(analysis_ids)
                )
            ))
            batch = AiBatchRepository(session).create_batch(
                analyses, submission_key="status-batch-provider"
            )
            batch.status = "submitted"
            batch.provider_batch_id = "provider-secret-id"
            session.commit()

        public = self._call(
            "GET",
            f"/api/v1/admin/asset-analyses/requests/{request_id}",
        )
        self.assertEqual(public.status_code, 200)
        self.assertEqual(public.json()["batch_count"], 1)
        self.assertEqual(public.json()["running"], 2)
        self.assertTrue(all(
            item["provider_batch_id"] is None
            for item in public.json()["items"]
        ))

        privileged = self._call(
            "GET",
            (
                f"/api/v1/admin/asset-analyses/requests/{request_id}"
                "?include_provider_batch_id=true"
            ),
            admin=True,
        )
        self.assertEqual(privileged.status_code, 200)
        self.assertTrue(all(
            item["provider_batch_id"] == "provider-secret-id"
            for item in privileged.json()["items"]
        ))

    def test_status_aggregation_and_queued_cancellation(self):
        created = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="cancel-me",
            body=self._body(),
        ).json()
        request_id = created["request_id"]
        status = self._call(
            "GET",
            f"/api/v1/admin/asset-analyses/requests/{request_id}",
        )
        self.assertEqual(status.status_code, 200)
        self.assertEqual(status.json()["queued"], 2)
        self.assertEqual(status.json()["batch_count"], 0)

        cancelled = self._call(
            "POST",
            f"/api/v1/admin/asset-analyses/requests/{request_id}/cancel",
            body={"reason": "Operator stopped this request."},
        )
        self.assertEqual(cancelled.status_code, 200)
        self.assertEqual(cancelled.json()["status"], "cancelled")
        self.assertEqual(cancelled.json()["cancelled"], 2)
        with self.factory() as session:
            jobs = list(session.scalars(select(ProcessingJobModel)))
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0].status, "failed")
            self.assertEqual(jobs[0].last_error_code, "operation_cancelled")

    def test_budget_preflight_and_authentication(self):
        stopped = self.settings.model_copy(update={
            "AI_EMERGENCY_STOP_ENABLED": True,
        })
        response = self._call(
            "POST", "/api/v1/admin/asset-analyses/bulk",
            key="budget-stopped",
            body=self._body(asset_ids=[self.asset_ids[0]]),
            settings=stopped,
        )
        self.assertEqual(response.status_code, 202)
        self.assertEqual(
            response.json()["items"][0]["acceptance_status"],
            "provider_unavailable",
        )

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
            unauthorized = self.client.post(
                "/api/v1/admin/asset-analyses/bulk",
                headers={"Idempotency-Key": "unauthenticated"},
                json=self._body(asset_ids=[self.asset_ids[0]]),
            )
        self.assertEqual(unauthorized.status_code, 401)


if __name__ == "__main__":
    unittest.main()
