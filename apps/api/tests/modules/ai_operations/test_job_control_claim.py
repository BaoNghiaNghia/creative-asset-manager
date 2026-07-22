import unittest
from datetime import datetime, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base
from app.modules.processing.model import ProcessingJobModel
from app.modules.processing.repository import ProcessingRepository
from app.modules.processing_policy.model import TenantProcessingPolicyModel


class JobControlClaimTest(unittest.TestCase):
    def test_cancel_requested_job_is_not_claimed(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        factory = sessionmaker(engine, class_=Session, expire_on_commit=False)
        with factory() as session:
            session.add(TenantProcessingPolicyModel(
                tenant_id="tenant-a", pipeline_enabled=True,
                ai_analysis_enabled=True,
            ))
            session.add(ProcessingJobModel(
                tenant_id="tenant-a", job_type="asset_analyze",
                entity_type="asset_ai_analysis", entity_id="analysis-1",
                idempotency_key="cancelled", provider_key="gemini",
                provider_scope="ai", cancellation_requested=True,
                status="pending", next_attempt_at=datetime.now(timezone.utc),
            ))
            session.commit()
            claimed = ProcessingRepository(session).claim_next_job(
                worker_id="worker", lease_seconds=30,
                enforce_tenant_policy=True,
                allowed_job_types=("asset_analyze",),
            )
            self.assertIsNone(claimed)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
