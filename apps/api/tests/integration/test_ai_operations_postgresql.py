from __future__ import annotations

import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, delete, func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.modules.ai_governance.model import AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.schema import AiOperationsFilters
from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.storage.model import AssetStorageObjectModel
from app.modules.storage.self_ingestion_repair import (
    ManagedStorageSelfIngestionRepairService,
)


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class AiOperationsPostgreSqlTest(unittest.TestCase):
    def test_self_ingestion_repair_candidate_discovery_is_postgresql_safe(self):
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        marker = uuid4().hex
        tenant_id = f"repair-{marker}"
        now = datetime.now(timezone.utc)
        try:
            with Session(engine, expire_on_commit=False) as session:
                source = ExternalSourceModel(
                    tenant_id=tenant_id,
                    source_key=f"drive-{marker}",
                    source_type="google_drive",
                )
                assets = [
                    AssetModel(
                        tenant_id=tenant_id,
                        content_hash=(f"{marker}{index:x}" + "0" * 64)[:64],
                    )
                    for index in range(4)
                ]
                session.add_all([source, *assets])
                session.flush()

                managed_sources = []
                for index, asset in enumerate(assets[:3]):
                    original = SourceAssetModel(
                        tenant_id=tenant_id,
                        external_source_id=source.id,
                        external_asset_id=f"original-{marker}-{index}",
                        source_metadata={"parents": ["customer-root"]},
                    )
                    managed = SourceAssetModel(
                        tenant_id=tenant_id,
                        external_source_id=source.id,
                        external_asset_id=f"managed-{marker}-{index}",
                        source_metadata={"parents": ["managed-root"]},
                    )
                    session.add_all([original, managed])
                    session.flush()
                    managed_sources.append(managed)
                    session.add_all(
                        [
                            AssetSourceLinkModel(
                                tenant_id=tenant_id,
                                asset_id=asset.id,
                                source_asset_id=original.id,
                            ),
                            AssetSourceLinkModel(
                                tenant_id=tenant_id,
                                asset_id=asset.id,
                                source_asset_id=managed.id,
                            ),
                            AssetStorageObjectModel(
                                tenant_id=tenant_id,
                                asset_id=asset.id,
                                content_hash=asset.content_hash,
                                storage_provider="google_drive_managed",
                                status="stored",
                                remote_file_id=managed.external_asset_id,
                                remote_folder_id="managed-root",
                                stored_at=now - timedelta(hours=3 - index),
                            ),
                        ]
                    )
                session.flush()
                # A second matching link previously multiplied the first storage row
                # in the outer join and forced DISTINCT.
                session.add(
                    AssetSourceLinkModel(
                        tenant_id=tenant_id,
                        asset_id=assets[3].id,
                        source_asset_id=managed_sources[0].id,
                    )
                )
                session.commit()
                before = tuple(
                    int(
                        session.scalar(
                            select(func.count())
                            .select_from(model)
                            .where(model.tenant_id == tenant_id)
                        )
                        or 0
                    )
                    for model in (
                        SourceAssetModel,
                        AssetSourceLinkModel,
                        AssetStorageObjectModel,
                    )
                )

            result = asyncio.run(
                ManagedStorageSelfIngestionRepairService(
                    lambda: Session(engine, expire_on_commit=False),
                    Settings(GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID="managed-root"),
                ).execute(tenant_id=tenant_id, limit=2, dry_run=True)
            )

            self.assertEqual(result.selected, 2)
            self.assertEqual(result.skipped_ambiguous, 1)
            self.assertEqual(result.repairable, 1)
            self.assertEqual(result.repaired_links, 0)
            self.assertEqual(result.removed_source_assets, 0)
            with Session(engine) as session:
                after = tuple(
                    int(
                        session.scalar(
                            select(func.count())
                            .select_from(model)
                            .where(model.tenant_id == tenant_id)
                        )
                        or 0
                    )
                    for model in (
                        SourceAssetModel,
                        AssetSourceLinkModel,
                        AssetStorageObjectModel,
                    )
                )
            self.assertEqual(after, before)
        finally:
            with Session(engine) as session:
                for model in (
                    AssetStorageObjectModel,
                    AssetSourceLinkModel,
                    SourceAssetModel,
                    AssetModel,
                    ExternalSourceModel,
                ):
                    session.execute(delete(model).where(model.tenant_id == tenant_id))
                session.commit()
            engine.dispose()

    def test_percentiles_are_aggregated_by_postgresql(self):
        engine = create_engine(DATABASE_URL, pool_pre_ping=True)
        marker = uuid4().hex
        tenant_id = f"ai-ops-{marker}"
        now = datetime.now(timezone.utc)
        try:
            with Session(engine) as session:
                asset = AssetModel(
                    tenant_id=tenant_id,
                    content_hash=marker.ljust(64, "0")[:64],
                )
                profile = MetadataProfileModel(
                    tenant_id=tenant_id, profile_name="ops", profile_version="1",
                    prompt_template="Analyze", search_config_json={}, active=True,
                )
                session.add_all([asset, profile])
                session.flush()
                for index, latency in enumerate((100, 200, 300)):
                    analysis = AssetAiAnalysisModel(
                        tenant_id=tenant_id, asset_id=asset.id,
                        content_hash=asset.content_hash,
                        metadata_profile_id=profile.id, metadata_profile="ops",
                        metadata_profile_version="1", prompt_version=f"p-{index}",
                        pipeline_version="single-asset-v1", ai_provider="gemini",
                        ai_model="test", status="completed", processing_stage="completed",
                        created_at=now,
                    )
                    session.add(analysis)
                    session.flush()
                    session.add(AiUsageRecordModel(
                        tenant_id=tenant_id, provider_operation_key=f"{marker}-{index}",
                        asset_id=asset.id, analysis_id=analysis.id,
                        provider="gemini", model="test", processing_mode="single",
                        metadata_profile="ops", input_units=1, output_units=1,
                        media_units=1, locally_estimated_cost_micros=1,
                        currency="USD", latency_ms=latency, outcome="completed",
                        retry_count=0, occurred_at=now,
                    ))
                session.commit()
                summary = AiOperationsRepository(session).summary(AiOperationsFilters(
                    tenant_id=tenant_id, from_at=now - timedelta(hours=1),
                    to_at=now + timedelta(hours=1),
                ))
                self.assertEqual(summary["latency"]["average_ms"], 200.0)
                self.assertEqual(summary["latency"]["p50_ms"], 200.0)
                self.assertEqual(summary["latency"]["p95_ms"], 290.0)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
