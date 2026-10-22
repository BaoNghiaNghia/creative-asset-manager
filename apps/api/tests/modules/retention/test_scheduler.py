import tempfile
import unittest
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel
from app.modules.retention.model import RetentionCleanupRunModel
from app.modules.retention.scheduler import RetentionCleanupScheduler
from app.modules.retention.service import RECORD_TYPES, RetentionCleanupService


class RetentionSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{self.tmp.name}/scheduler.db")
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            RETENTION_CLEANUP_ENABLED=True,
        )
        with self.sessions() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-enabled", pipeline_enabled=True
            ))
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-disabled", pipeline_enabled=False
            ))
            session.commit()

    def test_restricted_retention_canary_only_targets_old_job_payloads(self):
        settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            RETENTION_CLEANUP_ENABLED=True,
            RETENTION_CLEANUP_RECORD_TYPES="completed_job_payloads",
        )
        scheduler = RetentionCleanupScheduler(self.sessions, settings)
        self.assertEqual(scheduler.schedule_known_tenants(now=datetime.now(timezone.utc)), 1)
        with self.sessions() as session:
            run = session.scalar(select(RetentionCleanupRunModel))
            self.assertEqual(run.record_types_json, ["completed_job_payloads"])
            self.assertFalse(run.dry_run)

    def test_unsupported_or_duplicate_scope_fails_without_scheduling(self):
        for selected in ("", "completed_job_payloads,", "raw_ai_responses,raw_ai_responses", "delete_assets"):
            with self.subTest(selected=selected):
                scheduler = RetentionCleanupScheduler(
                    self.sessions, Settings(
                        PROCESSING_JOBS_ENABLED=True,
                        RETENTION_CLEANUP_ENABLED=True,
                        RETENTION_CLEANUP_RECORD_TYPES=selected,
                    ),
                )
                with self.assertRaises(ValueError):
                    scheduler.schedule_known_tenants(now=datetime.now(timezone.utc))
                with self.sessions() as session:
                    self.assertEqual(session.scalar(select(func.count()).select_from(RetentionCleanupRunModel)), 0)

    def test_failed_queue_insert_does_not_leave_a_blocking_pending_run(self):
        scheduler = RetentionCleanupScheduler(self.sessions, self.settings)
        now = datetime.now(timezone.utc)
        with patch.object(ProcessingRepository, "create_job", side_effect=RuntimeError("private details")):
            with self.assertRaises(RuntimeError):
                scheduler.schedule_tenant("tenant-enabled", cutoff_at=now, next_attempt_at=now)
        with self.sessions() as session:
            run = session.scalar(select(RetentionCleanupRunModel))
            self.assertEqual(run.status, "failed")
            self.assertNotIn("private details", str(run.error_json))
        self.assertTrue(scheduler.schedule_tenant("tenant-enabled", cutoff_at=now, next_attempt_at=now))
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(RetentionCleanupRunModel)), 2)
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 1)

    def test_default_scope_preserves_existing_policy(self):
        scheduler = RetentionCleanupScheduler(self.sessions, self.settings)
        self.assertEqual(scheduler._record_types(), RECORD_TYPES)

    def tearDown(self):
        self.engine.dispose(); self.tmp.cleanup()

    def test_completed_payload_scope_preserves_job_identity_and_recent_jobs(self):
        now = datetime.now(timezone.utc)
        with self.sessions() as session:
            for key, age in (("old", 31), ("recent", 1)):
                job = ProcessingRepository(session).create_job(
                    tenant_id="tenant-enabled", job_type="asset_index",
                    entity_type="asset", entity_id=key,
                    idempotency_key=f"retention-test:{key}",
                    payload={"value": "private"},
                )
                job.status = "completed"
                job.completed_at = now - timedelta(days=age)
            session.commit()
        service = RetentionCleanupService(self.sessions, self.settings)
        run = service.create_run(
            tenant_id="tenant-enabled", record_types=("completed_job_payloads",), now=now,
        )
        completed = service.execute(tenant_id="tenant-enabled", run_id=run.id)
        self.assertEqual(completed.status, "completed")
        with self.sessions() as session:
            repo = ProcessingRepository(session)
            self.assertEqual(repo.get_job_by_key("tenant-enabled", "retention-test:old").payload_json, {})
            self.assertEqual(repo.get_job_by_key("tenant-enabled", "retention-test:recent").payload_json, {"value": "private"})
            self.assertEqual(session.scalar(select(func.count()).select_from(ProcessingJobModel)), 2)

    def test_existing_scheduler_creates_one_tenant_scoped_claimable_job(self):
        scheduler = RetentionCleanupScheduler(self.sessions, self.settings)
        now = datetime.now(timezone.utc)
        self.assertEqual(scheduler.schedule_known_tenants(now=now), 1)
        self.assertEqual(scheduler.schedule_known_tenants(now=now), 0)
        with self.sessions() as session:
            self.assertEqual(
                session.scalar(select(func.count()).select_from(RetentionCleanupRunModel)), 1
            )
            job = session.scalar(select(ProcessingJobModel))
            self.assertEqual(job.tenant_id, "tenant-enabled")
            self.assertEqual(job.payload_json, {"cleanup_run_id": job.entity_id})
            claimed = ProcessingRepository(session).claim_next_job(
                worker_id="cleanup-worker", lease_seconds=30, now=now,
                enforce_tenant_policy=True, allowed_job_types=("retention_cleanup",),
            )
            session.commit()
            self.assertIsNotNone(claimed)
            self.assertEqual(claimed.id, job.id)
