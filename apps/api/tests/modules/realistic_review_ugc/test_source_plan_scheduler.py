import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.processing.model import ProcessingJobModel
from app.modules.realistic_review_ugc.model import RrugcSourcePlanModel
from app.modules.realistic_review_ugc.workspace import TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID
from app.modules.realistic_review_ugc.source_plan_scheduler import (
    RrugcSourcePlanSyncScheduler, TIKTOKSHOP_ANALYSIS_INFLIGHT_LIMIT,
)
from app.modules.realistic_review_ugc.source_plans import (
    RRUGC_SOURCE_ROOT_FOLDER_ID,
    SourcePlanSyncResult,
)
from app.modules.storage.managed_oauth import MANAGED_STORAGE_OAUTH_PROVIDER


class RrugcSourcePlanSyncSchedulerTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = lambda: Session(self.engine, expire_on_commit=False)
        self.settings = Settings(
            PROCESSING_JOBS_ENABLED=True,
            MANAGED_ASSET_STORAGE_ENABLED=True,
            RRUGC_SOURCE_AUTO_SYNC_ENABLED=True,
            RRUGC_SOURCE_AUTO_SYNC_INTERVAL_SECONDS=120,
            GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="managed-root",
        )
        with self.factory() as session:
            session.add_all(
                [
                    OAuthConnectionModel(
                        id="managed-a",
                        tenant_id="tenant-a",
                        provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                        provider_account_id="drive-a",
                        connection_purpose="managed_storage",
                        key_version="v1",
                        status="active",
                        provider_metadata_json={"root_folder_id": "managed-root"},
                    ),
                    OAuthConnectionModel(
                        id="managed-other-root",
                        tenant_id="tenant-b",
                        provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                        provider_account_id="drive-b",
                        connection_purpose="managed_storage",
                        key_version="v1",
                        status="active",
                        provider_metadata_json={"root_folder_id": "different-root"},
                    ),
                ]
            )
            session.commit()

    def tearDown(self):
        self.engine.dispose()

    def test_tick_scans_matching_managed_storage_tenant(self):
        calls = []

        async def syncer(session, **kwargs):
            calls.append(kwargs)
            return SourcePlanSyncResult(
                root_folder_id=kwargs["root_folder_id"],
                folders_scanned=3,
                images_found=7,
                plans_created=1,
                plans_updated=0,
                plans_missing=0,
                jobs_queued=1,
                unchanged=6,
            )

        scheduler = RrugcSourcePlanSyncScheduler(
            self.factory,
            self.settings,
            syncer=syncer,
        )

        results = scheduler.tick()

        self.assertEqual(len(results), 2)
        self.assertEqual([row["root_folder_id"] for row in calls], [
            RRUGC_SOURCE_ROOT_FOLDER_ID, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
        ])
        self.assertEqual(results[0].images_found, 7)
        self.assertEqual(calls[0]["tenant_id"], "tenant-a")
        self.assertEqual(calls[0]["user_id"], "system:rrugc-source-auto-sync")
        self.assertNotIn("analysis_job_limit", calls[0])
        self.assertEqual(calls[1]["analysis_job_limit"], TIKTOKSHOP_ANALYSIS_INFLIGHT_LIMIT)
        self.assertEqual(scheduler.interval_seconds, 120)

    def test_idle_source_scans_back_off_and_resume_after_change(self):
        scheduler = RrugcSourcePlanSyncScheduler(self.factory, self.settings)
        unchanged = SourcePlanSyncResult(
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            folders_scanned=1, images_found=236, plans_created=0,
            plans_updated=0, plans_missing=0, jobs_queued=0, unchanged=236,
        )
        self.assertEqual(scheduler._next_wait_seconds((unchanged,)), 240)
        self.assertEqual(scheduler._next_wait_seconds((unchanged,)), 480)
        self.assertEqual(scheduler._next_wait_seconds((unchanged,)), 600)
        self.assertEqual(scheduler._next_wait_seconds((unchanged,)), 600)
        changed = SourcePlanSyncResult(
            root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
            folders_scanned=1, images_found=236, plans_created=1,
            plans_updated=0, plans_missing=0, jobs_queued=1, unchanged=235,
        )
        self.assertEqual(scheduler._next_wait_seconds((changed,)), 120)
        self.assertEqual(scheduler._next_wait_seconds(()), 120)

    def test_tiktokshop_quota_counts_only_active_own_source_jobs(self):
        """At cap, do not scan/re-enqueue; release one slot when a job finishes."""
        with self.factory() as session:
            for i in range(TIKTOKSHOP_ANALYSIS_INFLIGHT_LIMIT):
                plan = RrugcSourcePlanModel(
                    id=f"tt-{i}", tenant_id="tenant-a",
                    root_folder_id=TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID,
                    source_file_id=f"drive-tt-{i}",
                    source_relative_path=f"product-{i}.png",
                    source_name=f"product-{i}.png",
                    source_mime_type="image/png", source_revision=f"hash-{i}",
                    created_by_user_id="test",
                )
                session.add(plan)
                session.add(ProcessingJobModel(
                    tenant_id="tenant-a", job_type="rrugc_source_plan_analyze",
                    entity_type="rrugc_source_plan", entity_id=plan.id,
                    idempotency_key=f"tt-{i}", provider_key="gemini",
                    provider_scope="ai", status="retry" if i == 0 else "pending",
                ))
            other = RrugcSourcePlanModel(
                id="review-other", tenant_id="tenant-a",
                root_folder_id=RRUGC_SOURCE_ROOT_FOLDER_ID,
                source_file_id="drive-review",
                source_relative_path="review.png", source_name="review.png",
                source_mime_type="image/png", source_revision="hash-review",
                created_by_user_id="test",
            )
            session.add(other)
            session.add(ProcessingJobModel(
                tenant_id="tenant-a", job_type="rrugc_source_plan_analyze",
                entity_type="rrugc_source_plan", entity_id=other.id,
                idempotency_key="review", status="pending",
            ))
            session.commit()

        calls = []

        async def syncer(session, **kwargs):
            calls.append(kwargs)
            return SourcePlanSyncResult(
                root_folder_id=kwargs["root_folder_id"], folders_scanned=1,
                images_found=0, plans_created=0, plans_updated=0,
                plans_missing=0, jobs_queued=0, unchanged=0,
            )

        scheduler = RrugcSourcePlanSyncScheduler(self.factory, self.settings, syncer=syncer)
        with self.factory() as session:
            self.assertEqual(scheduler._tiktokshop_analysis_budget(session, "tenant-a"), 0)
        self.assertEqual(len(scheduler.tick()), 1)
        self.assertEqual([x["root_folder_id"] for x in calls], [RRUGC_SOURCE_ROOT_FOLDER_ID])
        with self.factory() as session:
            job = session.query(ProcessingJobModel).filter_by(idempotency_key="tt-0").one()
            job.status = "completed"
            session.commit()
            self.assertEqual(scheduler._tiktokshop_analysis_budget(session, "tenant-a"), 1)
        calls.clear()
        self.assertEqual(len(scheduler.tick()), 2)
        self.assertEqual(calls[1]["analysis_job_limit"], 1)

    def test_tiktokshop_drive_outage_does_not_block_reviews(self):
        calls = []

        async def syncer(session, **kwargs):
            calls.append(kwargs["root_folder_id"])
            if kwargs["root_folder_id"] == TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID:
                raise RuntimeError("TikTokShop Drive temporarily unavailable")
            return SourcePlanSyncResult(
                root_folder_id=kwargs["root_folder_id"], folders_scanned=1,
                images_found=0, plans_created=0, plans_updated=0,
                plans_missing=0, jobs_queued=0, unchanged=0,
            )
        scheduler = RrugcSourcePlanSyncScheduler(self.factory, self.settings, syncer=syncer)
        self.assertEqual(len(scheduler.tick()), 1)
        self.assertEqual(calls, [RRUGC_SOURCE_ROOT_FOLDER_ID, TIKTOKSHOP_SOURCE_ROOT_FOLDER_ID])

    def test_disabled_scheduler_does_not_scan(self):
        settings = self.settings.model_copy(
            update={"RRUGC_SOURCE_AUTO_SYNC_ENABLED": False}
        )
        scheduler = RrugcSourcePlanSyncScheduler(self.factory, settings)

        self.assertFalse(scheduler.enabled)
        self.assertEqual(scheduler.tick(), ())


if __name__ == "__main__":
    unittest.main()
