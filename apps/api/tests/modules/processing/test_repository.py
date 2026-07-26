import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.model import OutboxEventModel, ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing.service import ProcessingJobService


NOW = datetime(2026, 7, 18, 8, 0, tzinfo=timezone.utc)


class ProcessingRepositoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        path = Path(self.directory.name) / "jobs.db"
        self.engine = create_engine(
            f"sqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 10},
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(bind=self.engine, expire_on_commit=False)

    def tearDown(self) -> None:
        self.engine.dispose()
        self.directory.cleanup()

    def _enqueue(
        self,
        *,
        key: str = "download:asset-1:v1",
        max_attempts: int = 5,
    ) -> str:
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            job = service.enqueue_job(
                tenant_id="tenant-a",
                job_type="source_asset_download",
                entity_type="source_asset",
                entity_id="asset-1",
                idempotency_key=key,
                payload={"source_asset_id": "asset-1"},
                max_attempts=max_attempts,
                next_attempt_at=NOW,
            )
            return job.id

    def test_duplicate_job_creation_returns_one_job(self) -> None:
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            first = repository.create_job(
                tenant_id="tenant-a",
                job_type="asset_store",
                entity_type="asset",
                entity_id="asset-1",
                idempotency_key="store:asset-1:v1",
            )
            second = repository.create_job(
                tenant_id="tenant-a",
                job_type="asset_store",
                entity_type="asset",
                entity_id="asset-1",
                idempotency_key="store:asset-1:v1",
            )
            session.commit()
            self.assertEqual(first.id, second.id)
            self.assertEqual(repository.count_jobs(), 1)

    def test_two_workers_do_not_claim_the_same_job(self) -> None:
        job_id = self._enqueue()
        barrier = threading.Barrier(2)
        claimed: list[str | None] = []
        errors: list[Exception] = []

        def claim(worker_id: str) -> None:
            try:
                with self.sessions() as session:
                    service = ProcessingJobService(ProcessingRepository(session))
                    barrier.wait(timeout=5)
                    job = service.claim_next(
                        worker_id=worker_id, lease_seconds=60, now=NOW
                    )
                    claimed.append(job.id if job else None)
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=claim, args=(f"worker-{i}",)) for i in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)

        self.assertFalse(errors)
        self.assertEqual(claimed.count(job_id), 1)
        self.assertEqual(claimed.count(None), 1)

    def test_expired_lease_is_recovered_by_another_worker(self) -> None:
        job_id = self._enqueue()
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            first = service.claim_next(worker_id="worker-a", lease_seconds=10, now=NOW)
            self.assertEqual(first.id, job_id)
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            recovered = service.claim_next(
                worker_id="worker-b", lease_seconds=10, now=NOW + timedelta(seconds=11)
            )
            self.assertEqual(recovered.id, job_id)
            self.assertEqual(recovered.claimed_by, "worker-b")
            self.assertEqual(recovered.attempt_count, 2)

    def test_accumulates_active_worker_time_without_queue_delay(self) -> None:
        job_id = self._enqueue(max_attempts=2)
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            first = repository.claim_next_job(worker_id="worker-a", lease_seconds=30, now=NOW + timedelta(hours=2))
            repository.fail_job(
                job_id=first.id, worker_id="worker-a", error_code="temporary",
                error_message="retry", base_backoff_seconds=5, now=NOW + timedelta(hours=2, seconds=2),
            )
            session.commit()
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            second = repository.claim_next_job(worker_id="worker-b", lease_seconds=30, now=NOW + timedelta(hours=2, seconds=7))
            completed = repository.complete_job(
                job_id=second.id, worker_id="worker-b", now=NOW + timedelta(hours=2, seconds=10),
            )
            session.commit()
            self.assertEqual(completed.processing_duration_ms, 5_000)
            self.assertIsNone(completed.claimed_at)

    def test_retry_uses_same_job_and_reaches_terminal_failure(self) -> None:
        job_id = self._enqueue(max_attempts=2)
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            first = service.claim_next(worker_id="worker-a", lease_seconds=30, now=NOW)
            repository = service.repository
            retried = repository.fail_job(
                job_id=first.id,
                worker_id="worker-a",
                error_code="temporary",
                error_message="try later",
                base_backoff_seconds=5,
                now=NOW,
            )
            session.commit()
            self.assertEqual(retried.status, "retry")
            self.assertEqual(retried.next_attempt_at.replace(tzinfo=timezone.utc), NOW + timedelta(seconds=5))

        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            self.assertIsNone(
                service.claim_next(worker_id="worker-b", lease_seconds=30, now=NOW + timedelta(seconds=4))
            )
            second = service.claim_next(
                worker_id="worker-b", lease_seconds=30, now=NOW + timedelta(seconds=5)
            )
            repository = service.repository
            failed = repository.fail_job(
                job_id=second.id,
                worker_id="worker-b",
                error_code="permanent",
                error_message="still broken",
                now=NOW + timedelta(seconds=5),
            )
            session.commit()
            self.assertEqual(failed.status, "failed")
            self.assertIsNotNone(failed.completed_at)
            self.assertEqual(failed.id, job_id)
            self.assertEqual(repository.count_jobs(), 1)

    def test_deferred_job_waits_until_retry_time_then_worker_can_reclaim(self) -> None:
        job_id = self._enqueue()
        retry_at = NOW + timedelta(minutes=5)
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            claimed = repository.claim_next_job(
                worker_id="worker-a", lease_seconds=30, now=NOW
            )
            deferred = repository.defer_job(
                job_id=claimed.id,
                worker_id="worker-a",
                retry_at=retry_at,
                reason_code="quota_deferred",
                reason_message="Quota is temporarily unavailable.",
                now=NOW + timedelta(seconds=1),
            )
            session.commit()
            self.assertEqual(deferred.status, "pending")
            self.assertEqual(deferred.attempt_count, 0)
            self.assertIsNone(deferred.claimed_by)
            self.assertIsNone(deferred.claimed_at)
            self.assertIsNone(deferred.lease_expires_at)

        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            self.assertIsNone(
                service.claim_next(
                    worker_id="worker-b",
                    lease_seconds=30,
                    now=retry_at - timedelta(seconds=1),
                )
            )
            reclaimed = service.claim_next(
                worker_id="worker-b", lease_seconds=30, now=retry_at
            )
            self.assertEqual(reclaimed.id, job_id)
            self.assertEqual(reclaimed.attempt_count, 1)

    def test_completed_and_non_retryable_failure_remain_terminal(self) -> None:
        completed_id = self._enqueue(key="terminal-completed")
        failed_id = self._enqueue(key="terminal-failed")
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            completed = repository.claim_next_job(
                worker_id="worker-a", lease_seconds=30, now=NOW
            )
            repository.complete_job(
                job_id=completed.id, worker_id="worker-a", now=NOW
            )
            failed = repository.claim_next_job(
                worker_id="worker-a", lease_seconds=30, now=NOW
            )
            repository.fail_job_non_retryable(
                job_id=failed.id,
                worker_id="worker-a",
                error_code="invalid",
                error_message="Invalid input.",
                now=NOW,
            )
            session.commit()

        with self.sessions() as session:
            self.assertEqual(session.get(ProcessingJobModel, completed_id).status, "completed")
            self.assertEqual(session.get(ProcessingJobModel, failed_id).status, "failed")

    def test_domain_mutation_and_outbox_share_transaction(self) -> None:
        with self.sessions() as session:
            assets = AssetRegistryRepository(session)
            processing = ProcessingRepository(session)
            source = assets.upsert_external_source(
                tenant_id="tenant-a",
                source_key="drive-primary",
                source_type="google_drive",
            )
            processing.create_outbox_event(
                tenant_id="tenant-a",
                event_type="external_source.created",
                entity_type="external_source",
                entity_id=source.id,
                idempotency_key=f"external-source-created:{source.id}",
                payload={"source_id": source.id},
            )
            session.rollback()

        with self.sessions() as session:
            source_count = session.scalar(select(func.count()).select_from(ExternalSourceModel))
            event_count = session.scalar(select(func.count()).select_from(OutboxEventModel))
            self.assertEqual(source_count, 0)
            self.assertEqual(event_count, 0)

    def test_outbox_creation_and_publish_are_idempotent(self) -> None:
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            first = repository.create_outbox_event(
                tenant_id="tenant-a",
                event_type="asset.ready",
                entity_type="asset",
                entity_id="asset-1",
                idempotency_key="asset-ready:asset-1:v1",
                next_attempt_at=NOW,
            )
            second = repository.create_outbox_event(
                tenant_id="tenant-a",
                event_type="asset.ready",
                entity_type="asset",
                entity_id="asset-1",
                idempotency_key="asset-ready:asset-1:v1",
                next_attempt_at=NOW,
            )
            session.commit()
            self.assertEqual(first.id, second.id)
        with self.sessions() as session:
            repository = ProcessingRepository(session)
            event = repository.claim_next_outbox_event(
                worker_id="publisher-a", lease_seconds=30, now=NOW
            )
            published = repository.publish_outbox_event(
                event_id=event.id, worker_id="publisher-a", now=NOW
            )
            again = repository.publish_outbox_event(
                event_id=event.id, worker_id="publisher-a", now=NOW
            )
            session.commit()
            self.assertEqual(published.id, again.id)
            count = session.scalar(select(func.count()).select_from(OutboxEventModel))
            self.assertEqual(count, 1)

    def test_terminalizes_expired_final_lease(self) -> None:
        job_id = self._enqueue(max_attempts=1)
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            service.claim_next(worker_id="worker-a", lease_seconds=1, now=NOW)
        with self.sessions() as session:
            service = ProcessingJobService(ProcessingRepository(session))
            self.assertIsNone(
                service.claim_next(worker_id="worker-b", lease_seconds=1, now=NOW + timedelta(seconds=2))
            )
            job = session.get(ProcessingJobModel, job_id)
            self.assertEqual(job.status, "failed")
            self.assertEqual(job.last_error_code, "lease_expired")
