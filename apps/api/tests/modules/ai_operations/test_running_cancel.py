import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository


class RunningCancellationTest(unittest.TestCase):
    def test_operator_cancel_finishes_instead_of_retrying(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with factory() as session:
            job = ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-1",
                idempotency_key="running-cancel", status="processing",
                claimed_by="worker", attempt_count=1,
                cancellation_requested=True, payload_json={},
            )
            session.add(job)
            session.flush()
            result = ProcessingRepository(session).release_job(
                job_id=job.id, worker_id="worker",
            )
            self.assertEqual(result.status, "failed")
            self.assertEqual(result.last_error_code, "operation_cancelled")
            self.assertIsNotNone(result.completed_at)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
