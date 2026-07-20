import unittest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.core.redaction import sanitize_sensitive_urls
from app.domain.providers.contracts import ExternalAssetCandidate, SourceChange, SourceChangePage
from app.modules.assets.model import SourceAssetModel
from app.modules.assets.repository import AssetRegistryRepository
from app.modules.processing.repository import ProcessingRepository
from app.modules.source_sync.model import SourceSyncRunModel
from app.modules.source_sync.repository import SourceSyncRepository
from app.modules.source_sync.service import SourceSyncService


def item(external_id: str) -> ExternalAssetCandidate:
    return ExternalAssetCandidate(
        source_type="google_drive", source_id="source",
        external_asset_id=external_id, filename=f"{external_id}.jpg",
        mime_type="image/jpeg", provider_version="v1",
        source_metadata={"is_folder": False},
    )


class Provider:
    def __init__(self, pages):
        self.pages = list(pages)
        self.cursors = []

    async def list_changes(self, input):
        self.cursors.append(input.cursor)
        value = self.pages.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


class GenerationReconciliationTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite:///:memory:", connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.session = Session(self.engine, expire_on_commit=False)
        self.assets = AssetRegistryRepository(self.session)
        self.source = self.assets.upsert_external_source(
            tenant_id="tenant-a", source_key="drive", source_type="google_drive"
        )
        for external_id in ("keep", "missing"):
            self.assets.upsert_source_asset(
                tenant_id="tenant-a", external_source_id=self.source.id,
                external_asset_id=external_id, filename=external_id,
            )
        self.session.commit()
        self.repository = SourceSyncRepository(self.session)
        self.service = SourceSyncService(
            self.repository, ProcessingRepository(self.session), enabled=True
        )

    def tearDown(self):
        self.session.close(); self.engine.dispose()

    async def test_successful_generation_sweeps_only_after_completion(self):
        result = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=Provider([SourceChangePage(
                (SourceChange("updated", "keep", item("keep")),), "done", False
            )]), reconciliation=True,
        )
        missing = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "missing"
        )
        run = self.session.get(SourceSyncRunModel, result.run_id)
        self.assertEqual(run.status, "completed")
        self.assertEqual(run.items_seen_count, 1)
        self.assertEqual(result.missing_marked, 1)
        self.assertIsNotNone(missing.deleted_at)

    async def test_failed_page_never_sweeps_and_resume_uses_checkpoint(self):
        provider = Provider([
            SourceChangePage((SourceChange("updated", "keep", item("keep")),), "page-2", True),
            TimeoutError("provider timeout"),
        ])
        with self.assertRaises(TimeoutError):
            await self.service.sync_source(
                tenant_id="tenant-a", source_id=self.source.id,
                provider=provider, reconciliation=True,
            )
        missing = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "missing"
        )
        self.assertIsNone(missing.deleted_at)
        failed = self.session.scalar(select(SourceSyncRunModel))
        self.assertEqual(failed.status, "failed")
        resumed_provider = Provider([
            SourceChangePage((SourceChange("updated", "missing", item("missing")),), "done", False)
        ])
        resumed = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=resumed_provider, reconciliation=True,
        )
        self.assertEqual(resumed.run_id, failed.id)
        self.assertEqual(resumed_provider.cursors, ["page-2"])
        self.assertIsNone(missing.deleted_at)

    async def test_lost_lease_before_sweep_never_marks_missing_deleted(self):
        checks = iter((True, False))
        with self.assertRaisesRegex(RuntimeError, "interrupted"):
            await self.service.sync_source(
                tenant_id="tenant-a", source_id=self.source.id,
                provider=Provider([SourceChangePage(
                    (SourceChange("updated", "keep", item("keep")),), "done", False
                )]),
                reconciliation=True,
                continue_check=lambda: next(checks),
            )
        missing = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "missing"
        )
        run = self.session.scalar(select(SourceSyncRunModel))
        self.assertEqual(run.status, "cancelled")
        self.assertIsNone(missing.deleted_at)

    def test_overlapping_full_runs_coalesce_on_one_generation(self):
        first = self.repository.start_or_resume_full_run("tenant-a", self.source.id)
        self.session.commit()
        second = self.repository.start_or_resume_full_run("tenant-a", self.source.id)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.generation, second.generation)

    async def test_incremental_discovery_during_full_run_is_generation_safe(self):
        run = self.repository.start_or_resume_full_run("tenant-a", self.source.id)
        self.session.commit()
        await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=Provider([SourceChangePage(
                (SourceChange("updated", "new", item("new")),), "incremental", False
            )]),
        )
        new = self.repository.get_source_asset_by_external_id(
            "tenant-a", self.source.id, "new"
        )
        self.assertEqual(new.last_seen_generation, run.generation)
        self.repository.complete_full_run(run)
        self.session.commit()
        self.assertIsNone(new.deleted_at)

    def test_source_metadata_signed_url_is_sanitized_without_mutation(self):
        metadata = {
            "preview": "https://cdn.example.test/a.jpg?signature=secret#fragment",
            "nested": ["plain", "https://example.test/path?token=value"],
        }
        cleaned = sanitize_sensitive_urls(metadata)
        self.assertEqual(cleaned["preview"], "https://cdn.example.test/a.jpg")
        self.assertEqual(cleaned["nested"][1], "https://example.test/path")
        self.assertIn("signature=secret", metadata["preview"])

    async def test_large_source_keeps_only_provider_page_in_memory(self):
        page_size = 25
        pages = []
        total = 2500
        for start in range(0, total, page_size):
            changes = tuple(
                SourceChange("updated", f"large-{index}", item(f"large-{index}"))
                for index in range(start, start + page_size)
            )
            pages.append(SourceChangePage(
                changes, f"cursor-{start + page_size}",
                start + page_size < total,
            ))
        result = await self.service.sync_source(
            tenant_id="tenant-a", source_id=self.source.id,
            provider=Provider(pages), reconciliation=True,
            page_size=page_size, max_pages=101,
        )
        self.assertEqual(result.changes, total)
        self.assertFalse(hasattr(self.repository, "list_external_ids"))
        self.assertEqual(
            self.session.scalar(select(SourceSyncRunModel.items_seen_count)), total
        )
