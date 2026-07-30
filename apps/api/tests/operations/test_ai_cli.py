import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_governance.model import AiBudgetReservationModel, AiModelRateLimitStateModel
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.operations import ai_cli


class AiCliTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        self.now = datetime.now(timezone.utc)
        with self.factory() as session:
            analysis = AssetAiAnalysisModel(
                tenant_id="tenant-a", asset_id="asset-a", content_hash="a" * 64,
                metadata_profile_id="profile-a", metadata_profile="profile",
                metadata_profile_version="1", prompt_version="1", pipeline_version="1",
                status="failed",
            )
            job = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze", entity_type="analysis",
                entity_id="analysis-a", idempotency_key="job-a", status="failed",
            )
            session.add_all((analysis, job))
            session.flush()
            decision = AiBudgetService(
                AiGovernanceRepository(session), Settings()
            ).reserve(
                tenant_id="tenant-a", operation_key="stale-a", estimated_cost_micros=0,
                analysis_id=analysis.id, job_id=job.id,
            )
            session.commit()
            self.reservation_id = decision.reservation_id
            with self.factory() as check:
                row = check.get(AiBudgetReservationModel, self.reservation_id)
                row.created_at = self.now - timedelta(hours=2)
                check.commit()
            self.job_id = job.id

    def tearDown(self):
        self.engine.dispose()

    def test_stale_repair_dry_run_is_read_only_and_apply_is_idempotent(self):
        with patch.object(ai_cli, "SessionLocal", self.factory), patch.object(
            ai_cli, "_settings_for_cli", return_value=Settings()
        ):
            dry_run = ai_cli.repair_stale_reservations(
                tenant_id="tenant-a", now=self.now
            )
            self.assertTrue(dry_run["dry_run"])
            self.assertEqual(dry_run["repaired"], 0)
            with self.factory() as session:
                self.assertEqual(
                    session.get(AiBudgetReservationModel, self.reservation_id).status,
                    "reserved",
                )
            applied = ai_cli.repair_stale_reservations(
                tenant_id="tenant-a", now=self.now, apply=True
            )
            self.assertEqual(applied["repaired"], 1)
            self.assertEqual(applied["repair_reasons"]["terminal_job"], 1)
            repeat = ai_cli.repair_stale_reservations(
                tenant_id="tenant-a", now=self.now, apply=True
            )
            self.assertEqual(repeat["repaired"], 0)
        with self.factory() as session:
            self.assertEqual(
                session.get(AiBudgetReservationModel, self.reservation_id).status,
                "released",
            )

    def test_active_job_reservation_is_not_touched(self):
        with self.factory() as session:
            session.get(ProcessingJobModel, self.job_id).status = "processing"
            session.commit()
        with patch.object(ai_cli, "SessionLocal", self.factory), patch.object(
            ai_cli, "_settings_for_cli", return_value=Settings()
        ):
            result = ai_cli.repair_stale_reservations(
                tenant_id="tenant-a", now=self.now, apply=True
            )
        self.assertEqual(result["repaired"], 0)
        self.assertEqual(result["repair_reasons"]["active_job_skipped"], 1)

    def test_rate_limit_repair_is_dry_run_tenant_scoped_and_idempotent(self):
        with self.factory() as session:
            session.add(
                TenantProcessingPolicyModel(
                    tenant_id="tenant-a",
                    pipeline_enabled=True,
                    ai_analysis_enabled=True,
                    total_active_jobs=1,
                    ai_active_jobs=1,
                )
            )
            target = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id="analysis-rate-a",
                idempotency_key="rate-a",
                status="pending",
                attempt_count=0,
                next_attempt_at=self.now - timedelta(seconds=1),
                claimed_by="stale-worker",
                claimed_at=self.now - timedelta(minutes=1),
                lease_expires_at=self.now - timedelta(seconds=1),
                last_error_code="ai_model_rate_limited",
                last_error_message="stale local defer",
                concurrency_accounted=True,
            )
            other_tenant = ProcessingJobModel(
                tenant_id="tenant-b",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id="analysis-rate-b",
                idempotency_key="rate-b",
                status="pending",
                attempt_count=0,
                next_attempt_at=self.now - timedelta(seconds=1),
                last_error_code="ai_model_rate_limited",
            )
            session.add_all((target, other_tenant))
            session.commit()
            target_id = target.id
            other_id = other_tenant.id

        with patch.object(ai_cli, "SessionLocal", self.factory):
            dry_run = ai_cli.repair_rate_limit_backlog(
                tenant_id="tenant-a", now=self.now
            )
            self.assertTrue(dry_run["dry_run"])
            self.assertEqual(dry_run["repair_candidates"], 1)
            self.assertEqual(dry_run["due_jobs"], 1)
            self.assertEqual(dry_run["future_jobs"], 0)
            self.assertEqual(dry_run["processing_jobs"], 0)
            self.assertEqual(dry_run["leases_or_accounting_to_release"], 1)
            self.assertEqual(dry_run["error_fields_to_clear"], 1)
            self.assertEqual(dry_run["jobs_would_be_made_normally_claimable"], 1)
            self.assertEqual(dry_run["repaired"], 0)
            applied = ai_cli.repair_rate_limit_backlog(
                tenant_id="tenant-a", now=self.now, apply=True
            )
            self.assertEqual(applied["repaired"], 1)
            repeated = ai_cli.repair_rate_limit_backlog(
                tenant_id="tenant-a", now=self.now, apply=True
            )
            self.assertEqual(repeated["repaired"], 0)

        with self.factory() as session:
            repaired = session.get(ProcessingJobModel, target_id)
            untouched = session.get(ProcessingJobModel, other_id)
            self.assertEqual(repaired.status, "pending")
            self.assertEqual(repaired.attempt_count, 0)
            self.assertEqual(repaired.next_attempt_at.replace(tzinfo=timezone.utc), self.now)
            self.assertIsNone(repaired.claimed_by)
            self.assertIsNone(repaired.claimed_at)
            self.assertIsNone(repaired.lease_expires_at)
            self.assertIsNone(repaired.last_error_code)
            self.assertFalse(repaired.concurrency_accounted)
            policy = session.get(TenantProcessingPolicyModel, "tenant-a")
            self.assertEqual(policy.total_active_jobs, 0)
            self.assertEqual(policy.ai_active_jobs, 0)
            self.assertEqual(untouched.last_error_code, "ai_model_rate_limited")

    def test_rate_limit_repair_skips_currently_processing_job(self):
        with self.factory() as session:
            active = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_ai_analysis",
                entity_id="active-rate",
                idempotency_key="active-rate",
                status="processing",
                attempt_count=1,
                next_attempt_at=self.now,
                claimed_by="worker",
                claimed_at=self.now,
                lease_expires_at=self.now + timedelta(minutes=1),
                last_error_code="ai_model_rate_limited",
            )
            session.add(active)
            session.commit()
            active_id = active.id
        with patch.object(ai_cli, "SessionLocal", self.factory):
            result = ai_cli.repair_rate_limit_backlog(
                tenant_id="tenant-a", now=self.now, apply=True
            )
        self.assertEqual(result["active_jobs_skipped"], 1)
        self.assertEqual(result["repaired"], 0)
        with self.factory() as session:
            self.assertEqual(session.get(ProcessingJobModel, active_id).status, "processing")


    def test_model_gate_regression_repair_is_exact_and_idempotent(self):
        deployed_after = self.now - timedelta(hours=1)
        with self.factory() as session:
            target = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="asset_analyze",
                entity_type="asset_pipeline",
                entity_id="pipeline-regression",
                idempotency_key="model-gate-regression",
                status="retry",
                attempt_count=2,
                next_attempt_at=self.now + timedelta(minutes=5),
                last_error_code="gemini_model_pool_exhausted",
                last_error_message="No Gemini model is currently available.",
                payload_json={"analysis_id": "analysis-regression", "keep": True},
                created_at=self.now,
                updated_at=self.now,
            )
            historical = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_pipeline", entity_id="pipeline-old",
                idempotency_key="model-gate-old", status="retry",
                last_error_code="gemini_model_pool_exhausted",
                last_error_message="No Gemini model is currently available.",
                created_at=self.now - timedelta(days=2),
                updated_at=self.now - timedelta(days=2),
            )
            legitimate = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_pipeline", entity_id="pipeline-legitimate",
                idempotency_key="model-gate-legitimate", status="retry",
                last_error_code="gemini_model_pool_exhausted",
                last_error_message="Different provider failure.",
                created_at=self.now, updated_at=self.now,
            )
            terminal = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_pipeline", entity_id="pipeline-terminal",
                idempotency_key="model-gate-terminal", status="failed",
                last_error_code="gemini_model_pool_exhausted",
                last_error_message="No Gemini model is currently available.",
                created_at=self.now, updated_at=self.now,
            )
            session.add_all((target, historical, legitimate, terminal))
            session.commit()
            target_id = target.id
            untouched_ids = (historical.id, legitimate.id, terminal.id)

        with patch.object(ai_cli, "SessionLocal", self.factory):
            dry_run = ai_cli.repair_model_gate_regression(
                tenant_id="tenant-a", deployed_after=deployed_after, now=self.now
            )
            self.assertEqual(dry_run["matching_jobs"], 1)
            self.assertEqual(dry_run["repaired"], 0)
            applied = ai_cli.repair_model_gate_regression(
                tenant_id="tenant-a", deployed_after=deployed_after,
                now=self.now, apply=True,
            )
            self.assertEqual(applied["repaired"], 1)
            repeated = ai_cli.repair_model_gate_regression(
                tenant_id="tenant-a", deployed_after=deployed_after,
                now=self.now, apply=True,
            )
            self.assertEqual(repeated["repaired"], 0)

        with self.factory() as session:
            repaired = session.get(ProcessingJobModel, target_id)
            self.assertEqual(repaired.status, "pending")
            self.assertEqual(repaired.attempt_count, 2)
            self.assertEqual(
                repaired.payload_json,
                {"analysis_id": "analysis-regression", "keep": True},
            )
            self.assertIsNone(repaired.last_error_code)
            self.assertTrue(
                all(
                    session.get(ProcessingJobModel, job_id).last_error_code
                    == "gemini_model_pool_exhausted"
                    for job_id in untouched_ids
                )
            )

    def test_model_gate_repair_requires_timezone_aware_cutoff(self):
        with self.assertRaisesRegex(ValueError, "include a timezone"):
            ai_cli._parse_datetime("2026-07-30T10:00:00")

    def test_rate_limit_validation_is_read_only_and_reports_eligible_defer(self):
        with self.factory() as session:
            session.add(AiModelRateLimitStateModel(
                tenant_id="tenant-a", provider="gemini", model="gemini-2.5-flash",
                last_started_at=self.now - timedelta(seconds=20),
                next_eligible_at=self.now - timedelta(seconds=1),
            ))
            session.add(ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="deferred-a",
                idempotency_key="deferred-a", status="pending",
                last_error_code="ai_model_rate_limited",
                next_attempt_at=self.now - timedelta(seconds=1),
            ))
            session.commit()
        settings = Settings(
            AI_MODEL_RPM_LIMITS='{"gemini":{"gemini-2.5-flash":4}}',
            AI_JOB_MIN_INTERVAL_SECONDS=10,
        )
        with patch.object(ai_cli, "SessionLocal", self.factory), patch.object(
            ai_cli, "_settings_for_cli", return_value=settings
        ):
            result = ai_cli.validate_rate_limits(tenant_id="tenant-a", now=self.now)
        self.assertEqual(result["local_rate_deferred_jobs"], 1)
        self.assertEqual(result["improperly_eligible_local_deferred_jobs"], 1)
        target = next(
            model for model in result["models"]
            if model["model"] == "gemini-2.5-flash"
        )
        self.assertEqual(target["effective_interval_seconds"], 15)
        with self.factory() as session:
            self.assertEqual(
                session.scalar(select(ProcessingJobModel.status).where(
                    ProcessingJobModel.idempotency_key == "deferred-a"
                )),
                "pending",
            )



if __name__ == "__main__":
    unittest.main()
