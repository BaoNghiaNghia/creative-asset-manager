import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_governance.model import AiBudgetReservationModel
from app.modules.ai_governance.service import AiBudgetService
from app.modules.ai_governance.repository import AiGovernanceRepository
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.processing.model import ProcessingJobModel
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


if __name__ == "__main__":
    unittest.main()
