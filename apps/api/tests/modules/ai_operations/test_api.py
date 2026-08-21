import csv
import io
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.main import app
from app.modules.ai_batch.model import AiBatchJobModel
from app.modules.ai_governance.model import AiBudgetReservationModel, AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.model import (
    AssetModel, AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.pipeline.model import AssetPipelineModel
from app.modules.source_sync.model import SourceSyncRunModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.processing_policy.model import ProcessingPolicyAuditModel


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
            self.asset_id = asset.id
            self.external_source_id = source.id
            source_asset = SourceAssetModel(
                tenant_id="tenant-a", external_source_id=source.id,
                external_asset_id="drive-item",
            )
            session.add(source_asset)
            session.flush()
            self.source_asset_id = source_asset.id
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
                    completed_at=(self.now - timedelta(days=1)) if status in {"completed", "failed"} else None,
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
            session.add(AiBatchJobModel(
                tenant_id="tenant-a", submission_key="batch-cost-not-duplicated",
                provider="openai", model="o-model", metadata_profile_id=profile.id,
                metadata_profile="general", metadata_profile_version="1",
                prompt_version="p-batch", pipeline_version="batch-asset-v1",
                status="completed", item_count=1, completed_count=1,
                estimated_cost_micros=999, actual_cost_micros=999,
                currency="USD", created_at=self.now - timedelta(days=1),
                completed_at=self.now - timedelta(days=1),
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
                    status="failed", attempt_count=2, max_attempts=2, processing_duration_ms=2_000,
                    last_error_code="provider_timeout",
                    last_error_message="https://signed.example/file?credential=secret",
                    payload_json={"signed_url": "https://signed.example/file?token=secret"},
                    created_at=self.now - timedelta(seconds=1),
                    completed_at=self.now - timedelta(seconds=1),
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
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset({"ai_operations.read", "ai_provider.configure", "ai_budget.read", "ai_budget.update", "ai_emergency_stop", "ai_jobs.retry", "ai_jobs.cancel"}),
            platform_admin=False, session_id=None, authorization_source="tenant_rbac",
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

    def test_media_dashboard_separates_image_video_and_indexing(self):
        with self.factory() as session:
            session.add_all([
                ProcessingJobModel(tenant_id="tenant-a", job_type="video_analyze", entity_type="video_analysis_run", entity_id="video-run", idempotency_key="video-analysis", status="processing", payload_json={}),
                ProcessingJobModel(tenant_id="tenant-a", job_type="video_search_index", entity_type="video_analysis_run", entity_id="video-run", idempotency_key="video-index", status="completed", payload_json={}),
            ])
            session.commit()
        probe = AsyncMock(return_value={"live": True, "ready": True, "probe": "available"})
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory), patch(
            "app.modules.ai_operations.media_dashboard._probe_worker", probe,
        ):
            response = self.client.get("/api/v1/admin/ai-operations/media-dashboard")
        self.assertEqual(response.status_code, 200)
        document = response.json()
        self.assertEqual(document["image"]["label"], "Image analysis")
        self.assertEqual(document["video"]["running"], 1)
        self.assertEqual(document["video_indexing"]["completed"], 1)
        self.assertEqual([stage["key"] for stage in document["pipeline"]["video"]], ["video_analyze", "video_search_index"])
        self.assertEqual(len(document["workers"]), 2)
        self.assertNotIn("127.0.0.1", str(document))

    def test_media_dashboard_paginates_video_jobs_and_returns_safe_thumbnail_proxy(self):
        with self.factory() as session:
            for index in range(26):
                session.add(ProcessingJobModel(
                    tenant_id="tenant-a", job_type="video_analyze",
                    entity_type="source_asset", entity_id=self.source_asset_id,
                    idempotency_key=f"video-page-{index}", status="completed",
                    payload_json={}, updated_at=self.now - timedelta(seconds=index),
                ))
            session.commit()
        probe = AsyncMock(return_value={"live": True, "ready": True, "probe": "available"})
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory), patch(
            "app.modules.ai_operations.media_dashboard._probe_worker", probe,
        ):
            response = self.client.get("/api/v1/admin/ai-operations/media-dashboard?video_page=2&video_page_size=25")
        self.assertEqual(response.status_code, 200)
        recent = response.json()["recent_video"]
        self.assertEqual(recent["page"], 2)
        self.assertEqual(recent["page_size"], 25)
        self.assertEqual(recent["total"], 26)
        self.assertEqual(len(recent["items"]), 1)
        self.assertEqual(recent["items"][0]["location"], "Google Drive")
        self.assertEqual(
            recent["items"][0]["thumbnail_url"],
            f"/api/explorer/thumbnail/drive-item?provider=google-drive&external_source_id={self.external_source_id}&fallback=video",
        )

    def test_media_dashboard_resolves_video_location_from_synced_folders(self):
        with self.factory() as session:
            external = session.get(ExternalSourceModel, self.external_source_id)
            external.display_name = "Creative Drive"
            video = session.get(SourceAssetModel, self.source_asset_id)
            video.filename = "clip.mp4"
            video.source_metadata = {"parents": ["campaigns"]}
            session.add_all([
                SourceAssetModel(
                    tenant_id="tenant-a", external_source_id=self.external_source_id,
                    external_asset_id="campaigns", filename="Campaigns",
                    source_metadata={"parents": ["spring"]},
                ),
                SourceAssetModel(
                    tenant_id="tenant-a", external_source_id=self.external_source_id,
                    external_asset_id="spring", filename="Spring",
                    source_metadata={},
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="video_analyze",
                    entity_type="source_asset", entity_id=self.source_asset_id,
                    idempotency_key="video-location", status="completed", payload_json={},
                ),
            ])
            session.commit()
        probe = AsyncMock(return_value={"live": True, "ready": True, "probe": "available"})
        with patch("app.modules.ai_operations.router.SessionLocal", self.factory), patch(
            "app.modules.ai_operations.media_dashboard._probe_worker", probe,
        ):
            response = self.client.get("/api/v1/admin/ai-operations/media-dashboard")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json()["recent_video"]["items"][0]["location"],
            "Creative Drive / Spring / Campaigns",
        )

    def test_metadata_prompt_template_is_exposed_versioned_and_audited(self):
        with patch("app.modules.ai_operations.control_router.SessionLocal", self.factory):
            initial = self.client.get("/api/v1/admin/ai-operations/configuration")
            self.assertEqual(initial.status_code, 200)
            current = initial.json()["metadata_prompt_template"]
            self.assertEqual(current["profile_name"], "general")
            self.assertEqual(current["prompt_template"], "Analyze")
            updated = self.client.patch(
                "/api/v1/admin/ai-operations/configuration/metadata-prompt-template",
                json={
                    "prompt_template": "Describe image {{ asset }} with searchable visual attributes.",
                    "reason": "capture search metadata",
                },
            )
        self.assertEqual(updated.status_code, 200)
        document = updated.json()["metadata_prompt_template"]
        self.assertEqual(document["profile_name"], "general")
        self.assertNotEqual(document["id"], current["id"])
        self.assertEqual(updated.json()["audit"]["action"], "metadata_prompt_template_updated")
        with self.factory() as session:
            profiles = list(session.scalars(select(MetadataProfileModel).where(
                MetadataProfileModel.tenant_id == "tenant-a",
                MetadataProfileModel.profile_name == "general",
            )))
            self.assertEqual(len(profiles), 2)
            self.assertEqual(sum(profile.active for profile in profiles), 1)
            self.assertEqual(next(profile for profile in profiles if profile.active).prompt_template, "Describe image {{ asset }} with searchable visual attributes.")

    def test_metadata_prompt_template_provides_a_draft_and_creates_first_profile(self):
        with self.factory() as session:
            for profile in session.scalars(select(MetadataProfileModel).where(
                MetadataProfileModel.tenant_id == "tenant-a",
            )):
                session.delete(profile)
            session.commit()
        with patch("app.modules.ai_operations.control_router.SessionLocal", self.factory):
            initial = self.client.get("/api/v1/admin/ai-operations/configuration")
            self.assertEqual(initial.status_code, 200)
            draft = initial.json()["metadata_prompt_template"]
            self.assertTrue(draft["is_draft"])
            self.assertIsNone(draft["id"])
            self.assertIn("search-ready visual metadata", draft["prompt_template"])
            created = self.client.patch(
                "/api/v1/admin/ai-operations/configuration/metadata-prompt-template",
                json={"prompt_template": draft["prompt_template"], "reason": "initialize profile"},
            )
        self.assertEqual(created.status_code, 200)
        self.assertEqual(created.json()["metadata_prompt_template"]["profile_name"], "creative-default")
        self.assertFalse(created.json()["metadata_prompt_template"]["is_draft"])
        with self.factory() as session:
            self.assertEqual(session.scalar(select(MetadataProfileModel.profile_name).where(
                MetadataProfileModel.tenant_id == "tenant-a",
                MetadataProfileModel.active.is_(True),
            )), "creative-default")

    def test_pipeline_snapshot_uses_current_pipeline_jobs(self):
        with self.factory() as session:
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == "tenant-a",
            ))
            source_asset = session.scalar(select(SourceAssetModel).where(
                SourceAssetModel.tenant_id == "tenant-a",
            ))
            source_asset.mime_type = "image/jpeg"
            session.add(SourceSyncRunModel(
                tenant_id="tenant-a", external_source_id=source.id, mode="full",
                generation=1, status="completed", pages_count=2, items_seen_count=8,
                jobs_created_count=4, started_at=self.now - timedelta(minutes=5),
                completed_at=self.now - timedelta(minutes=1),
            ))
            session.add(AssetPipelineModel(
                tenant_id="tenant-a", correlation_id="pipeline-a", origin_type="source_asset",
                origin_id=source_asset.id, source_asset_id=source_asset.id, state="indexed",
            ))
            session.add_all([
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="source_asset_download",
                    entity_type="source_asset", entity_id=source_asset.id,
                    idempotency_key="download-old-failed", status="failed", payload_json={},
                    created_at=self.now - timedelta(minutes=4), completed_at=self.now - timedelta(minutes=4),
                    last_error_code="download_stage_unconfigured",
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="source_asset_download",
                    entity_type="source_asset", entity_id=source_asset.id,
                    idempotency_key="download-current", status="processing", payload_json={},
                    claimed_at=self.now - timedelta(minutes=1), created_at=self.now - timedelta(minutes=2),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_store", entity_type="asset_pipeline",
                    entity_id="pipeline-a", idempotency_key="store-waiting", status="pending", payload_json={},
                    next_attempt_at=self.now + timedelta(minutes=5), created_at=self.now - timedelta(minutes=1),
                ),
            ])
            session.commit()
        value = self.get("/api/v1/admin/ai-operations/pipeline").json()
        self.assertEqual(value["overall"]["supported_assets"], 1)
        self.assertEqual(value["latest_source_sync"]["items_seen_count"], 8)
        self.assertEqual(dict((item["key"], item["count"]) for item in value["overall"]["asset_progress"])["search_ready"], 1)
        download = next(item for item in value["stages"] if item["key"] == "source_asset_download")
        self.assertEqual(download["completed_assets"], 1)
        self.assertEqual(download["needs_attention_assets"], 0)
        store = next(item for item in value["stages"] if item["key"] == "asset_store")
        self.assertEqual(store["completed_assets"], 1)
        self.assertEqual(value["active_job"]["job_type"], "source_asset_download")
        self.assertNotIn("payload_json", str(value))

    def test_pipeline_snapshot_deduplicates_retries_skips_and_decommissioned_sources(self):
        with self.factory() as session:
            source = session.scalar(select(ExternalSourceModel).where(ExternalSourceModel.tenant_id == "tenant-a"))
            source_asset = session.scalar(select(SourceAssetModel).where(SourceAssetModel.tenant_id == "tenant-a"))
            source_asset.mime_type = "image/jpeg"
            pipeline = AssetPipelineModel(tenant_id="tenant-a", correlation_id="logical-current", origin_type="source_asset", origin_id=source_asset.id, source_asset_id=source_asset.id, state="downloaded")
            duplicate = ExternalSourceModel(tenant_id="tenant-a", source_key="retired-drive", source_type="google_drive", source_metadata={"decommissioned_at": self.now.isoformat(), "canonical_source_id": source.id})
            session.add_all([pipeline, duplicate]); session.flush()
            retired = SourceAssetModel(tenant_id="tenant-a", external_source_id=duplicate.id, external_asset_id="same-file", mime_type="image/jpeg")
            skipped = SourceAssetModel(tenant_id="tenant-a", external_source_id=source.id, external_asset_id="too-large", mime_type="image/jpeg")
            session.add_all([retired, skipped]); session.flush()
            session.add(AssetPipelineModel(tenant_id="tenant-a", correlation_id="logical-skipped", origin_type="source_asset", origin_id=skipped.id, source_asset_id=skipped.id, state="download_failed", last_error_code="source_content_too_large"))
            session.add_all([
                ProcessingJobModel(tenant_id="tenant-a", job_type="source_asset_download", entity_type="source_asset", entity_id=source_asset.id, idempotency_key="old-failed", status="failed", payload_json={}, last_error_code="download_stage_unconfigured", created_at=self.now - timedelta(minutes=3)),
                ProcessingJobModel(tenant_id="tenant-a", job_type="source_asset_download", entity_type="source_asset", entity_id=source_asset.id, idempotency_key="new-completed", status="completed", payload_json={}, created_at=self.now - timedelta(minutes=1)),
                ProcessingJobModel(tenant_id="tenant-a", job_type="source_asset_download", entity_type="source_asset", entity_id=retired.id, idempotency_key="retired-failed", status="failed", payload_json={}, last_error_code="download_stage_unconfigured"),
            ])
            session.commit()
        value = self.get("/api/v1/admin/ai-operations/pipeline").json()
        self.assertEqual(value["overall"]["eligible_assets"], 2)
        self.assertEqual(value["overall"]["needs_attention_assets"], 0)
        self.assertEqual(value["overall"]["skipped_assets"], 1)
        self.assertEqual(value["diagnostics"]["decommissioned_sources_excluded"], 1)
        download = next(item for item in value["stages"] if item["key"] == "source_asset_download")
        self.assertLessEqual(download["total_logical_assets"], value["overall"]["eligible_assets"])
        self.assertEqual(download["needs_attention_assets"], 0)

    def test_summary_costs_percentiles_and_empty_period(self):
        response = self.get("/api/v1/admin/ai-operations/summary")
        self.assertEqual(response.status_code, 200)
        value = response.json()
        self.assertEqual(
            {key: value[key] for key in (
                "requested", "queued", "running", "completed", "failed",
                "cancelled", "budget_blocked",
            )},
            {"requested": 2, "queued": 0, "running": 0, "completed": 0,
             "failed": 1, "cancelled": 1, "budget_blocked": 1},
        )
        self.assertEqual(value["success_rate"], 0.0)
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
        self.assertEqual(daily[0]["provider_estimated_cost_micros"], {"gemini": 10, "openai": 50})
        providers = self.get("/api/v1/admin/ai-operations/providers").json()["items"]
        self.assertEqual({(item["provider"], item["processing_mode"]) for item in providers}, {
            ("gemini", "single"), ("openai", "batch"),
        })
        gemini = self.get(
            "/api/v1/admin/ai-operations/summary",
            provider="gemini", model="g-model", processing_mode="single",
            metadata_profile="general", source_provider="google-drive",
        ).json()
        self.assertEqual(gemini["requested"], 1)
        failures = self.get("/api/v1/admin/ai-operations/failures").json()["items"]
        self.assertTrue(any(item["error_code"] == "provider_timeout" for item in failures))
        self.assertFalse(any(item["error_code"] == "rate_limited" for item in failures))

    def test_jobs_usage_pagination_retry_status_and_sensitive_exclusion(self):
        jobs = self.get(
            "/api/v1/admin/ai-operations/jobs", page=1, page_size=1,
        ).json()
        self.assertEqual(jobs["total"], 2)
        self.assertEqual(len(jobs["items"]), 1)
        self.assertIn("processing_duration_ms", jobs["items"][0])
        all_jobs = self.get("/api/v1/admin/ai-operations/jobs", page=1, page_size=10).json()
        self.assertIn(2_000, [item["processing_duration_ms"] for item in all_jobs["items"]])
        analysis_job = next(item for item in all_jobs["items"] if item["entity_id"] == self.analysis_ids[1])
        self.assertEqual(analysis_job["asset_id"], self.asset_id)
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

    def test_summary_uses_canonical_current_job_states_and_latest_replacement(self):
        today_start = self.now.replace(hour=0, minute=0, second=0, microsecond=0)
        with self.factory() as session:
            session.add_all([
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="current-processing",
                    idempotency_key="current-processing", provider_key="gemini", provider_scope="ai",
                    status="processing", payload_json={}, created_at=self.now - timedelta(minutes=3),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="current-pending",
                    idempotency_key="current-pending", provider_key="gemini", provider_scope="ai",
                    status="pending", payload_json={}, next_attempt_at=self.now - timedelta(seconds=1),
                    created_at=self.now - timedelta(minutes=2),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="older-pending",
                    idempotency_key="older-pending", provider_key="gemini", provider_scope="ai",
                    status="pending", payload_json={}, next_attempt_at=self.now - timedelta(seconds=1),
                    # Live queue state remains visible even when it predates the dashboard range.
                    created_at=self.now - timedelta(days=10),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="completed-today",
                    idempotency_key="completed-today", provider_key="gemini", provider_scope="ai",
                    status="completed", payload_json={}, created_at=self.now - timedelta(days=1),
                    completed_at=self.now - timedelta(minutes=1),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="replacement",
                    idempotency_key="replacement-failed", provider_key="gemini", provider_scope="ai",
                    status="failed", payload_json={}, created_at=self.now - timedelta(minutes=5),
                    completed_at=self.now - timedelta(minutes=5), last_error_code="provider_timeout",
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="replacement",
                    idempotency_key="replacement-completed", provider_key="gemini", provider_scope="ai",
                    status="completed", payload_json={}, created_at=self.now - timedelta(minutes=4),
                    completed_at=self.now - timedelta(minutes=4),
                ),
            ])
            session.commit()

        current = self.get("/api/v1/admin/ai-operations/summary").json()
        self.assertEqual(current["running"], 1)
        self.assertEqual(current["queued"], 2)
        self.assertEqual(current["completed"], 2)
        # The earlier failure is history, not the current state of replacement.
        self.assertEqual(current["failed"], 1)

        today = self.get(
            "/api/v1/admin/ai-operations/summary",
            **{"from": today_start.isoformat(), "to": self.now.isoformat()},
        ).json()
        self.assertEqual(today["completed"], 2)
        self.assertEqual(today["failed"], 1)
        self.assertEqual(today["queued"], 2)

    def test_legacy_running_and_queued_statuses_remain_dashboard_compatible(self):
        # Older deployments may contain these values even though new workers
        # write processing and pending. SQLite permits this test fixture only.
        with self.factory() as session:
            session.connection().exec_driver_sql("PRAGMA ignore_check_constraints = ON")
            session.add_all([
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="legacy-running",
                    idempotency_key="legacy-running", provider_key="gemini", provider_scope="ai",
                    status="running", payload_json={}, created_at=self.now - timedelta(minutes=2),
                ),
                ProcessingJobModel(
                    tenant_id="tenant-a", job_type="asset_analyze",
                    entity_type="asset_ai_analysis", entity_id="legacy-queued",
                    idempotency_key="legacy-queued", provider_key="gemini", provider_scope="ai",
                    status="queued", payload_json={}, next_attempt_at=self.now - timedelta(seconds=1),
                    created_at=self.now - timedelta(minutes=1),
                ),
            ])
            session.commit()
        summary = self.get("/api/v1/admin/ai-operations/summary").json()
        self.assertEqual(summary["running"], 1)
        self.assertEqual(summary["queued"], 1)

    def test_deferred_jobs_are_waiting_not_failed_and_report_next_retry(self):
        retry_at = self.now + timedelta(minutes=10)
        with self.factory() as session:
            session.add(ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id=self.analysis_ids[3],
                idempotency_key="gemini-quota-deferred", provider_key="gemini", provider_scope="ai",
                status="pending", attempt_count=1, max_attempts=3,
                next_attempt_at=retry_at, last_error_code="gemini_quota_deferred",
                last_error_message="Gemini quota is temporarily unavailable.", payload_json={},
                created_at=self.now - timedelta(minutes=1),
            ))
            session.commit()
        waiting = self.get("/api/v1/admin/ai-operations/jobs", status="waiting").json()
        self.assertEqual(waiting["total"], 1)
        item = waiting["items"][0]
        self.assertTrue(item["is_deferred"])
        self.assertEqual(item["waiting_reason"], "gemini_quota_deferred")
        self.assertEqual(item["status"], "pending")
        summary = self.get("/api/v1/admin/ai-operations/summary").json()
        self.assertEqual(summary["deferred"], 1)
        self.assertEqual(summary["failed"], 1)
        self.assertIsNotNone(summary["next_deferred_retry_at"])

    def test_daily_uses_completed_at_and_costs_are_not_double_counted(self):
        with self.factory() as session:
            analysis = session.get(AssetAiAnalysisModel, self.analysis_ids[0])
            analysis.created_at = self.now - timedelta(days=2)
            analysis.completed_at = self.now - timedelta(days=1)
            session.commit()
        daily = self.get("/api/v1/admin/ai-operations/daily").json()["items"]
        by_date = {item["date"]: item for item in daily}
        created_day = (self.now - timedelta(days=2)).date().isoformat()
        completed_day = (self.now - timedelta(days=1)).date().isoformat()
        self.assertGreaterEqual(by_date[created_day]["requested"], 1)
        self.assertEqual(by_date[created_day]["completed"], 0)
        self.assertGreaterEqual(by_date[completed_day]["completed"], 1)
        summary = self.get("/api/v1/admin/ai-operations/summary").json()
        self.assertEqual(summary["cost"]["estimated_cost_micros"], 60)
        self.assertEqual(summary["cost"]["reconciled_cost_micros"], 15)
        self.assertNotEqual(summary["cost"]["reconciled_cost_micros"], 1014)
        self.assertEqual(summary["budget_blocked"], 1)

    def test_csv_exports_are_bounded_audited_tenant_safe_and_secret_free(self):
        expected_headers = {
            "daily": "date", "usage": "id", "failures": "source", "jobs": "id",
        }
        for export_type, first_header in expected_headers.items():
            response = self.get(
                f"/api/v1/admin/ai-operations/exports/{export_type}.csv",
                row_limit=1,
            )
            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.headers["content-type"].startswith("text/csv"))
            self.assertEqual(response.headers["cache-control"], "private, no-store")
            rows = list(csv.reader(io.StringIO(response.text)))
            self.assertEqual(rows[0][0], first_header)
            self.assertLessEqual(len(rows), 2)
            serialized = response.text.lower()
            self.assertNotIn("signed_url", serialized)
            self.assertNotIn("credential=", serialized)
            self.assertNotIn("token=", serialized)
            self.assertNotIn("provider_request_id", serialized)
        with self.factory() as session:
            audits = session.query(ProcessingPolicyAuditModel).filter_by(
                tenant_id="tenant-a", action="ai_operations_export_requested"
            ).all()
            self.assertEqual(len(audits), 4)
            self.assertEqual({item.new_policy_json["export_type"] for item in audits}, set(expected_headers))
            self.assertNotIn("secret", str([item.new_policy_json for item in audits]).lower())
        self.assertEqual(self.get(
            "/api/v1/admin/ai-operations/exports/usage.csv", row_limit=10001,
        ).status_code, 422)
        self.assertEqual(self.get(
            "/api/v1/admin/ai-operations/exports/unknown.csv",
        ).status_code, 404)
        self.assertEqual(self.get(
            "/api/v1/admin/ai-operations/exports/usage.csv", tenant_id="tenant-b",
        ).status_code, 403)

    def test_provider_model_mode_filters_agree_across_endpoints(self):
        filters = {
            "provider": "openai", "model": "o-model",
            "processing_mode": "batch", "metadata_profile": "general",
            "source_provider": "google_drive",
        }
        summary = self.get("/api/v1/admin/ai-operations/summary", **filters).json()
        daily = self.get("/api/v1/admin/ai-operations/daily", **filters).json()["items"]
        providers = self.get("/api/v1/admin/ai-operations/providers", **filters).json()["items"]
        failures = self.get("/api/v1/admin/ai-operations/failures", **filters).json()["items"]
        jobs = self.get("/api/v1/admin/ai-operations/jobs", **filters).json()
        usage = self.get("/api/v1/admin/ai-operations/usage", **filters).json()
        self.assertEqual(summary["requested"], 1)
        self.assertEqual(sum(item["requested"] for item in daily), 2)
        self.assertEqual({item["provider"] for item in providers}, {"openai"})
        self.assertEqual({item["processing_mode"] for item in providers}, {"batch"})
        self.assertEqual(usage["total"], 2)
        self.assertEqual(jobs["total"], 1)
        self.assertTrue(failures)
        exported = self.get(
            "/api/v1/admin/ai-operations/exports/usage.csv", **filters,
        )
        self.assertEqual(len(list(csv.reader(io.StringIO(exported.text)))), 3)
    def test_date_range_authorization_and_tenant_isolation(self):
        six_months = self.get(
            "/api/v1/admin/ai-operations/summary",
            **{"from": (self.now - timedelta(days=180)).isoformat()},
        )
        self.assertEqual(six_months.status_code, 200)
        all_time = self.get("/api/v1/admin/ai-operations/summary", **{"range": "all"})
        self.assertEqual(all_time.status_code, 200)
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
