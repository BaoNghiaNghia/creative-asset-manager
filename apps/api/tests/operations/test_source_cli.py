import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.assets.model import ExternalSourceModel, SourceAssetModel
from app.modules.processing.model import ProcessingJobModel
from app.operations.source_cli import repair_google_drive_duplicates


class SourceCliTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)

    def tearDown(self):
        self.session.close()
        self.engine.dispose()

    def source(self, key, *, default=False):
        source = ExternalSourceModel(
            tenant_id="tenant-a",
            source_key=key,
            source_type="google_drive",
            source_metadata={
                "provider_account_id": "google-a",
                "oauth_connection_id": key,
                "is_default": default,
            },
        )
        self.session.add(source)
        self.session.flush()
        return source

    def add_asset(self, source, external_id):
        asset = SourceAssetModel(
            tenant_id="tenant-a",
            external_source_id=source.id,
            external_asset_id=external_id,
        )
        self.session.add(asset)
        self.session.flush()
        return asset

    def add_job(self, asset, *, status="pending"):
        job = ProcessingJobModel(
            tenant_id="tenant-a",
            job_type="source_asset_download",
            entity_type="source_asset",
            entity_id=asset.id,
            idempotency_key=f"job:{asset.id}",
            status=status,
        )
        self.session.add(job)
        self.session.commit()
        return job

    def test_dry_run_reports_duplicate_work_without_writes(self):
        canonical = self.source("canonical", default=False)
        duplicate = self.source("duplicate", default=True)
        self.add_asset(canonical, "one")
        self.add_asset(canonical, "one-more")
        duplicate_asset = self.add_asset(duplicate, "two")
        job = self.add_job(duplicate_asset)
        job_id = job.id
        duplicate_asset_id = duplicate_asset.id

        result = repair_google_drive_duplicates(
            tenant_id="tenant-a",
            session_factory=lambda: self.session,
        )

        self.assertTrue(result["dry_run"])
        self.assertEqual(result["duplicate_sources"], 1)
        self.assertEqual(result["duplicate_source_assets"], 1)
        self.assertEqual(result["unstarted_jobs"], 1)
        self.assertEqual(self.session.get(ProcessingJobModel, job_id).status, "pending")
        self.assertIsNone(self.session.get(SourceAssetModel, duplicate_asset_id).deleted_at)

    def test_apply_decommissions_duplicate_and_removes_only_unstarted_work(self):
        canonical = self.source("canonical")
        duplicate = self.source("duplicate", default=True)
        self.add_asset(canonical, "one")
        self.add_asset(canonical, "one-more")
        self.add_asset(canonical, "one-most")
        duplicate_asset = self.add_asset(duplicate, "two")
        pending = self.add_job(duplicate_asset)
        active = self.add_job(self.add_asset(duplicate, "three"), status="processing")

        result = repair_google_drive_duplicates(
            tenant_id="tenant-a",
            apply=True,
            session_factory=lambda: self.session,
        )

        self.assertFalse(result["dry_run"])
        self.assertEqual(result["unstarted_jobs_removed"], 1)
        self.assertIsNone(self.session.get(ProcessingJobModel, pending.id))
        self.assertTrue(self.session.get(ProcessingJobModel, active.id).cancellation_requested)
        self.assertIsNotNone(self.session.get(SourceAssetModel, duplicate_asset.id).deleted_at)
        self.assertTrue(self.session.get(ExternalSourceModel, canonical.id).source_metadata["is_default"])
        duplicate_metadata = self.session.get(ExternalSourceModel, duplicate.id).source_metadata
        self.assertFalse(duplicate_metadata["is_default"])
        self.assertEqual(duplicate_metadata["canonical_source_id"], canonical.id)


if __name__ == "__main__":
    unittest.main()
