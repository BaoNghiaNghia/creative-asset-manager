import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.operations.processing_cli import (
    requeue_download_stage_unconfigured,
    requeue_video_quota_deferred,
    repair_downloads,
)


class ProcessingCliTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def add_job(self, *, tenant_id, error_code):
        with self.sessions() as session:
            job = ProcessingJobModel(
                tenant_id=tenant_id,
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id=f"asset-{tenant_id}-{error_code}",
                idempotency_key=f"job-{tenant_id}-{error_code}",
                status="failed",
                attempt_count=5,
                max_attempts=5,
                last_error_code=error_code,
                last_error_message="failure",
                claimed_by="old-worker",
                claimed_at=datetime.now(timezone.utc),
                lease_expires_at=datetime.now(timezone.utc),
                completed_at=datetime.now(timezone.utc),
                concurrency_accounted=True,
            )
            session.add(job)
            session.commit()
            return job.id

    def test_repair_creates_fresh_job_without_mutating_terminal_failure(self):
        failed_id = self.add_job(
            tenant_id="tenant-a",
            error_code="download_stage_unconfigured",
        )
        oversized_id = self.add_job(
            tenant_id="tenant-a",
            error_code="source_content_too_large",
        )
        with self.sessions() as session:
            failed = session.get(ProcessingJobModel, failed_id)
            failed.entity_id = "source-asset-a"
            oversized = session.get(ProcessingJobModel, oversized_id)
            oversized.entity_id = "source-asset-b"
            session.commit()

        dry = repair_downloads(tenant_id="tenant-a", session_factory=self.sessions)
        self.assertEqual(dry["created"], 0)
        self.assertEqual(dry["skipped"], 1)

        applied = repair_downloads(
            tenant_id="tenant-a", apply=True, session_factory=self.sessions,
        )
        self.assertEqual(applied["created"], 1)
        self.assertEqual(applied["skipped"], 1)

        with self.sessions() as session:
            historical = session.get(ProcessingJobModel, failed_id)
            self.assertEqual(historical.status, "failed")
            self.assertEqual(historical.attempt_count, 5)
            fresh = session.scalar(select(ProcessingJobModel).where(
                ProcessingJobModel.idempotency_key
                == f"repair:source_asset_download:{failed_id}:source-asset-a"
            ))
            self.assertIsNotNone(fresh)
            self.assertEqual(fresh.status, "pending")

        repeated = repair_downloads(
            tenant_id="tenant-a", apply=True, session_factory=self.sessions,
        )
        self.assertEqual(repeated["created"], 0)
        self.assertEqual(repeated["matched"], 1)
        self.assertEqual(repeated["duplicate_jobs_skipped"], 0)

    def test_requeue_is_tenant_scoped_in_place_and_idempotent(self):
        target_id = self.add_job(
            tenant_id="tenant-a",
            error_code="download_stage_unconfigured",
        )
        other_error_id = self.add_job(
            tenant_id="tenant-a",
            error_code="other_error",
        )
        other_tenant_id = self.add_job(
            tenant_id="tenant-b",
            error_code="download_stage_unconfigured",
        )

        dry_run = requeue_download_stage_unconfigured(
            tenant_id="tenant-a",
            session_factory=self.sessions,
        )
        self.assertEqual(dry_run["matched"], 1)
        self.assertEqual(dry_run["requeued"], 0)

        applied = requeue_download_stage_unconfigured(
            tenant_id="tenant-a",
            apply=True,
            session_factory=self.sessions,
        )
        self.assertEqual(applied["requeued"], 1)

        with self.sessions() as session:
            target = session.get(ProcessingJobModel, target_id)
            self.assertEqual(target.status, "pending")
            self.assertEqual(target.attempt_count, 0)
            self.assertIsNone(target.claimed_by)
            self.assertIsNone(target.claimed_at)
            self.assertIsNone(target.lease_expires_at)
            self.assertIsNone(target.last_error_code)
            self.assertIsNone(target.last_error_message)
            self.assertIsNone(target.completed_at)
            self.assertFalse(target.concurrency_accounted)
            self.assertEqual(
                session.get(ProcessingJobModel, other_error_id).status,
                "failed",
            )
            self.assertEqual(
                session.get(ProcessingJobModel, other_tenant_id).status,
                "failed",
            )
            self.assertEqual(
                session.scalar(
                    select(func.count()).select_from(ProcessingJobModel)
                ),
                3,
            )

        repeated = requeue_download_stage_unconfigured(
            tenant_id="tenant-a",
            apply=True,
            session_factory=self.sessions,
        )
        self.assertEqual(repeated["matched"], 0)
        self.assertEqual(repeated["requeued"], 0)


    def test_video_quota_reconcile_is_tenant_scoped_and_preserves_attempts(self):
        future = datetime.now(timezone.utc) + timedelta(hours=6)
        with self.sessions() as session:
            target = ProcessingJobModel(
                tenant_id="tenant-a",
                job_type="video_analyze",
                entity_type="source_asset",
                entity_id="video-a",
                idempotency_key="video-quota-a",
                status="pending",
                attempt_count=2,
                max_attempts=5,
                next_attempt_at=future,
                last_error_code="video_gemini_quota_deferred",
                last_error_message="quota deferred",
            )
            other = ProcessingJobModel(
                tenant_id="tenant-b",
                job_type="video_analyze",
                entity_type="source_asset",
                entity_id="video-b",
                idempotency_key="video-quota-b",
                status="pending",
                attempt_count=2,
                max_attempts=5,
                next_attempt_at=future,
                last_error_code="video_gemini_quota_deferred",
            )
            session.add_all((target, other))
            session.commit()
            target_id, other_id = target.id, other.id

        dry = requeue_video_quota_deferred(
            tenant_id="tenant-a", session_factory=self.sessions
        )
        self.assertEqual((dry["matched"], dry["made_eligible_now"]), (1, 0))

        applied = requeue_video_quota_deferred(
            tenant_id="tenant-a", apply=True, session_factory=self.sessions
        )
        self.assertEqual(applied["made_eligible_now"], 1)
        with self.sessions() as session:
            target = session.get(ProcessingJobModel, target_id)
            other = session.get(ProcessingJobModel, other_id)
            self.assertLess(target.next_attempt_at, other.next_attempt_at)
            self.assertEqual(target.attempt_count, 2)
            self.assertEqual(target.last_error_code, "video_gemini_quota_deferred")
            self.assertEqual(other.status, "pending")

    def test_video_quota_reconcile_matches_dashboard_deferred_states_and_codes(self):
        future = datetime.now(timezone.utc) + timedelta(hours=6)
        with self.sessions() as session:
            session.add_all(
                ProcessingJobModel(
                    tenant_id="tenant-a",
                    job_type="video_analyze",
                    entity_type="source_asset",
                    entity_id=f"video-{index}",
                    idempotency_key=f"video-quota-variant-{index}",
                    status=status,
                    next_attempt_at=future,
                    last_error_code=error_code,
                )
                for index, (status, error_code) in enumerate(
                    (
                        ("retry", "video_gemini_quota_deferred"),
                        ("retry", "video_gemini_rate_limited"),
                        ("pending", "ai_model_rate_limited"),
                        ("pending", "rate_limited"),
                    )
                )
            )
            session.add(
                ProcessingJobModel(
                    tenant_id="tenant-a",
                    job_type="video_analyze",
                    entity_type="source_asset",
                    entity_id="not-quota-deferred",
                    idempotency_key="not-quota-deferred",
                    status="pending",
                    next_attempt_at=future,
                    last_error_code="operator_retry_requested",
                )
            )
            session.commit()

        result = requeue_video_quota_deferred(
            tenant_id="tenant-a", session_factory=self.sessions
        )

        self.assertEqual(result["matched"], 4)
        self.assertEqual(result["made_eligible_now"], 0)
        self.assertEqual(
            result["queued_breakdown"]["pending:operator_retry_requested"], 1
        )


if __name__ == "__main__":
    unittest.main()
