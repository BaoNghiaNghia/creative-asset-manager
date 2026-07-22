import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.modules.ai_governance.model import AiBudgetReservationModel, AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.model import (
    AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin


class AiOperationsApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.now = datetime.now(timezone.utc).replace(microsecond=0)
        with self.factory() as session:
            profile = MetadataProfileModel(
                tenant_id="tenant-a", profile_name="general", profile_version="1",
                prompt_template="Analyze", search_config_json={}, active=True,
            )
            asset = AssetModel(tenant_id="tenant-a", content_hash="a" * 64)
            other_asset = AssetModel(tenant_id="tenant-b", content_hash="b" * 64)
            source = ExternalSourceModel(
                tenant_id="tenant-a", source_key="drive", source_type="google_drive",
            )
            session.add_all([profile, asset, other_asset, source])
            session.flush()
            source_asset = SourceAssetModel(
                tenant_id="tenant-a", external_source_id=source.id,
                external_asset_id="drive-item",
            )
            session.add(source_asset)
            session.flush()
            session.add(AssetSourceLinkModel(
                tenant_id="tenant-a", asset_id=asset.id, source_asset_id=source_asset.id,
            ))

            states = [
                ("completed", "completed", "gemini", "g-model", "single-asset-v1"),
                ("failed", "failed", "openai", "o-model", "batch-asset-v1"),
                ("running", "provider_call", "gemini", "g-model", "single-asset-v1"),
                ("pending", "queued", "gemini", "g-model", "single-asset-v1"),
                ("budget_blocked", "budget_blocked", "openai", "o-model", "batch-asset-v1"),
                ("pending", "cancelled", "gemini", "g-model", "single-asset-v1"),
            ]
            analyses = []
            for index, (status, stage, provider, model, pipeline) in enumerate(states):
                analysis = AssetAiAnalysisModel(
                    tenant_id="tenant-a", asset_id=asset.id, content_hash=asset.content_hash,
                    metadata_profile_id=profile.id, metadata_profile="general",
                    metadata_profile_version="1", prompt_version=f"p-{index}",
                    pipeline_version=pipeline, ai_provider=provider, ai_model=model,
                    status=status, processing_stage=stage,
                    last_error_code="provider_timeout" if status == "failed" else None,
                    last_error_message="https://secret.example/file?token=hidden" if status == "failed" else None,
                    created_at=self.now - timedelta(days=1, minutes=index),
                )
                session.add(analysis)
                analyses.append(analysis)
            session.flush()
            self.analysis_ids = [item.id for item in analyses]
            usage_rows = [
                (analyses[0], "completed", 100, 10, 5, 10, 20),
                (analyses[1], "provider_failed", 200, 20, 10, 20, None),
                (analyses[4], "budget_blocked", 300, 30, 15, 30, None),
            ]
            for index, (analysis, outcome, latency, inputs, outputs, estimated, reported) in enumerate(usage_rows):
                session.add(AiUsageRecordModel(
                    tenant_id="tenant-a", provider_operation_key=f"op-{index}",
                    asset_id=asset.id, analysis_id=analysis.id,
                    provider=analysis.ai_provider, model=analysis.ai_model,
                    processing_mode="batch" if analysis.pipeline_version.startswith("batch") else "single",
                    metadata_profile="general", metadata_profile_version="1",
                    input_units=inputs, output_units=outputs, media_units=1,
                    locally_estimated_cost_micros=estimated,
                    provider_reported_cost_micros=reported,
                    currency="USD", latency_ms=latency, outcome=outcome,
                    retry_count=1 if outcome == "provider_failed" else 0,
                    occurred_at=self.now - timedelta(days=1),
                ))
            session.add(AiBudgetReservationModel(
                tenant_id="tenant-a", operation_key="reserve-1",
                analysis_id=analyses[0].id, provider="gemini", model="g-model",
                processing_mode="single", estimated_cost_micros=10,
                actual_cost_micros=15, currency="USD", status="reconciled",
                account_keys_json=[], created_at=self.now - timedelta(days=1),
                updated_at=self.now - timedelta(days=1),
            ))
            jobs = [
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id=analyses[1].id,
                    idempotency_key="failed-job", provider_key="openai", provider_scope="ai",
                    status="failed", attempt_count=2, max_attempts=2,
                    last_error_code="provider_timeout",
                    last_error_message="https://signed.example/file?credential=secret",
                    payload_json={"signed_url": "https://signed.example/file?token=secret"},
                    created_at=self.now - timedelta(hours=2),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id=analyses[0].id,
                    idempotency_key="retry-job", provider_key="gemini", provider_scope="ai",
                    status="retry", attempt_count=1, max_attempts=5,
                    last_error_code="rate_limited", payload_json={},
                    created_at=self.now - timedelta(hours=1),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_index", entity_type="asset",
                    entity_id=asset.id, idempotency_key="not-ai", status="failed",
                    payload_json={}, created_at=self.now - timedelta(hours=1),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-b", job_type="asset_analyze",
                    entity_type="asset", entity_id=other_asset.id,
                    idempotency_key="tenant-b", status="failed", payload_json={},
                    created_at=self.now - timedelta(hours=1),
                ),
            ]
            session.add_all(jobs)
            session.commit()
        self.client = TestClient(app)
        app.dependency_overrides[require_processing_admin] = lambda: ProcessingAdmin(
            actor_id="tenant-a", own_tenant_id="tenant-a", platform_admin=False,
        )

    def tearDown(self):
        app.dependency_overrides.clear()
        self.engine.dispose()

    def get(self, path, **params):
        defaults = {
            "from": (self.now - timedelta(days=2)).isoformat(),
            "to": self.now.isoformat(),
        }
        defaults.update(params)
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory):
            return self.client.get(path, params=defaults)

    def test_summary_costs_percentiles_and_empty_period(self):
        response = self.get("/api/v1/admin/ai-operations/summary")
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(
            {key: value[key] for key in (
                "requested", "queued", "running", "completed", "failed",
                "cancelled", "budget_blocked",
            )},
            {"requested": 6, "queued": 1, "running": 1, "completed": 1,
             "failed": 1, "cancelled": 1, "budget_blocked": 1},
        )
        self.assertEqual(value["success_rate"], 0.5)
        self.assertEqual(value["input_units"], 60)
        self.assertEqual(value["output_units"], 30)
        self.assertEqual(value["cost"]["estimated_cost_micros"], 60)
        self.assertEqual(value["cost"]["provider_reported_cost_micros"], 20)
        self.assertEqual(value["cost"]["reconciled_cost_micros"], 15)
        self.assertEqual(value["latency"], {"average_ms": 200.0, "p50_ms": 200.0, "p95_ms": 300.0})
        empty = self.get(
            "/api/v1/admin/ai-operations/summary",
            **{"from": (self.now - timedelta(days=20)).isoformat(),
               "to": (self.now - timedelta(days=19)).isoformat()},
        ).json()
        self.assertEqual(empty["requested"], 0)
        self.assertEqual(empty["success_rate"], 0)

    def test_daily_provider_failure_and_filters(self):
        daily = self.get("/api/v1/admin/ai-operations/daily").json()["items"]
        self.assertEqual(len(daily), 1)
        self.assertEqual(daily[0]["requested"], 6)
        self.assertEqual(daily[0]["reconciled_cost_micros"], 15)
        providers = self.get("/api/v1/admin/ai-operations/providers").json()["items"]
        self.assertEqual({(item["provider"], item["processing_mode"]) for item in providers}, {
            ("gemini", "single"), ("openai", "batch"),
        })
        gemini = self.get(
            "/api/v1/admin/ai-operations/summary",
            provider="gemini", model="g-model", processing_mode="single",
            metadata_profile="general", source_provider="google-drive",
        ).json()
        self.assertEqual(gemini["requested"], 4)
        failures = self.get("/api/v1/admin/ai-operations/failures").json()["items"]
        self.assertTrue(any(item["error_code"] == "provider_timeout" for item in failures))

    def test_jobs_usage_pagination_retry_status_and_sensitive_exclusion(self):
        jobs = self.get(
            "/api/v1/admin/ai-operations/jobs", page=1, page_size=1,
        ).json()
        self.assertEqual(jobs["total"], 2)
        self.assertEqual(len(jobs["items"]), 1)
        serialized = str(jobs)
        self.assertNotIn("signed_url", serialized)
        self.assertNotIn("credential", serialized)
        retrying = self.get(
            "/api/v1/admin/ai-operations/jobs", status="retry",
        ).json()
        self.assertEqual(retrying["total"], 1)
        usage = self.get(
            "/api/v1/admin/ai-operations/usage", provider="openai",
        ).json()
        self.assertEqual(usage["total"], 2)
        self.assertNotIn("provider_request_id", str(usage))

    def test_date_limits_authorization_and_tenant_isolation(self):
        too_long = self.get(
            "/api/v1/admin/ai-operations/summary",
            **{"from": (self.now - timedelta(days=91)).isoformat()},
        )
        self.assertEqual(too_long.status_code, 422)
        invalid = self.get(
            "/api/v1/admin/ai-operations/summary",
            **{"from": self.now.isoformat(), "to": (self.now - timedelta(days=1)).isoformat()},
        )
        self.assertEqual(invalid.status_code, 422)
        cross = self.get("/api/v1/admin/ai-operations/summary", tenant_id="tenant-b")
        self.assertEqual(cross.status_code, 403)
        app.dependency_overrides.clear()
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory):
            self.assertEqual(
                self.client.get("/api/v1/admin/ai-operations/summary").status_code, 401
            )


if __name__ == "__main__":
    unittest.main()
