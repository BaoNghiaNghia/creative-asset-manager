from __future__ import annotations

import json
import unittest

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.domain.providers.contracts import StoredAssetReadStream
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.processing.model import ProcessingJobModel
from app.modules.storage.model import AssetStorageObjectModel
from app.operations.analysis_storage_diagnostics import (
    diagnose_analysis_storage_read_failures,
)


class _ReadableStorageProvider:
    def __init__(self) -> None:
        self.opened: list[tuple[str, str, str]] = []
        self.close_count = 0

    async def open_asset(self, input):
        self.opened.append((input.tenant_id, input.asset_id, input.remote_file_id))

        async def body():
            yield b"test"

        async def close() -> None:
            self.close_count += 1

        return StoredAssetReadStream(
            body=body(),
            close=close,
            content_type="image/png",
            size_bytes=4,
        )


class AnalysisStorageDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.engine = create_engine(
            "sqlite:///:memory:",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(
            bind=self.engine,
            class_=Session,
            expire_on_commit=False,
        )
        self._seed_failure("tenant-a", "a")
        self._seed_failure("tenant-b", "b")

    def tearDown(self) -> None:
        Base.metadata.drop_all(self.engine)
        self.engine.dispose()

    def _seed_failure(self, tenant_id: str, suffix: str) -> None:
        asset_id = f"asset-{suffix}"
        analysis_id = f"analysis-{suffix}"
        source_id = f"source-{suffix}"
        source_asset_id = f"source-asset-{suffix}"
        profile_id = f"profile-{suffix}"
        with self.factory() as session:
            session.add_all(
                [
                    AssetModel(
                        id=asset_id,
                        tenant_id=tenant_id,
                        content_hash=suffix * 64,
                        mime_type="image/png",
                        size_bytes=4,
                    ),
                    MetadataProfileModel(
                        id=profile_id,
                        tenant_id=tenant_id,
                        profile_name="creative-assets",
                        profile_version="v1",
                        prompt_template="safe test prompt",
                    ),
                    ExternalSourceModel(
                        id=source_id,
                        tenant_id=tenant_id,
                        source_key=f"google-{suffix}",
                        source_type="google-drive",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    AssetAiAnalysisModel(
                        id=analysis_id,
                        tenant_id=tenant_id,
                        asset_id=asset_id,
                        content_hash=suffix * 64,
                        metadata_profile_id=profile_id,
                        metadata_profile="creative-assets",
                        metadata_profile_version="v1",
                        prompt_version="prompt-v1",
                        pipeline_version="pipeline-v1",
                        ai_provider="gemini",
                        status="failed",
                        failure_retryable=True,
                        last_error_code="analysis_storage_read_failed",
                    ),
                    SourceAssetModel(
                        id=source_asset_id,
                        tenant_id=tenant_id,
                        external_source_id=source_id,
                        external_asset_id=f"drive-file-{suffix}",
                        filename=f"asset-{suffix}.png",
                        mime_type="image/png",
                    ),
                    AssetStorageObjectModel(
                        id=f"storage-{suffix}",
                        tenant_id=tenant_id,
                        asset_id=asset_id,
                        content_hash=suffix * 64,
                        storage_provider="google_drive_managed",
                        status="stored",
                        remote_file_id=f"secret-remote-{suffix}",
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    AssetSourceLinkModel(
                        id=f"link-{suffix}",
                        tenant_id=tenant_id,
                        asset_id=asset_id,
                        source_asset_id=source_asset_id,
                    ),
                    ProcessingJobModel(
                        id=f"job-{suffix}",
                        tenant_id=tenant_id,
                        job_type="asset_analyze",
                        entity_type="asset_pipeline",
                        entity_id=f"pipeline-{suffix}",
                        idempotency_key=f"analyze-{suffix}",
                        payload_json={"analysis_id": analysis_id},
                        provider_key="gemini",
                        status="failed",
                        attempt_count=2,
                        max_attempts=5,
                        last_error_code="analysis_storage_read_failed",
                        last_error_message="sanitized failure",
                    ),
                ]
            )
            session.commit()

    def test_is_read_only_tenant_scoped_and_sanitized(self) -> None:
        with self.factory() as session:
            before_jobs = session.scalar(select(func.count(ProcessingJobModel.id)))

        result = diagnose_analysis_storage_read_failures(
            tenant_id="tenant-a",
            session_factory=self.factory,
        )

        self.assertTrue(result["read_only"])
        self.assertEqual(result["count"], 1)
        row = result["jobs"][0]
        self.assertEqual(row["processing_job_id"], "job-a")
        self.assertEqual(row["analysis_id"], "analysis-a")
        self.assertEqual(row["asset_id"], "asset-a")
        self.assertEqual(row["asset_filename"], "asset-a.png")
        self.assertEqual(row["source_asset_availability"], "available")
        self.assertTrue(row["remote_file_id_present"])
        self.assertEqual(row["remote_verification_category"], "unknown")
        serialized = json.dumps(result)
        self.assertNotIn("secret-remote-a", serialized)
        self.assertNotIn("secret-remote-b", serialized)
        self.assertNotIn("_remote_file_id", serialized)

        with self.factory() as session:
            after_jobs = session.scalar(select(func.count(ProcessingJobModel.id)))
            persisted = session.get(ProcessingJobModel, "job-a")
        self.assertEqual(after_jobs, before_jobs)
        self.assertEqual(persisted.status, "failed")
        self.assertEqual(persisted.attempt_count, 2)

    def test_remote_verification_is_scoped_and_closes_stream(self) -> None:
        provider = _ReadableStorageProvider()

        result = diagnose_analysis_storage_read_failures(
            tenant_id="tenant-a",
            session_factory=self.factory,
            verify_remote=True,
            storage_provider=provider,
        )

        self.assertEqual(result["jobs"][0]["remote_verification_category"], "ok")
        self.assertEqual(
            provider.opened,
            [("tenant-a", "asset-a", "secret-remote-a")],
        )
        self.assertEqual(provider.close_count, 1)
        self.assertNotIn("secret-remote-a", json.dumps(result))

    def test_limit_is_bounded(self) -> None:
        for invalid_limit in (0, 21):
            with self.subTest(limit=invalid_limit):
                with self.assertRaisesRegex(ValueError, "between 1 and 20"):
                    diagnose_analysis_storage_read_failures(
                        tenant_id="tenant-a",
                        session_factory=self.factory,
                        limit=invalid_limit,
                    )


if __name__ == "__main__":
    unittest.main()
