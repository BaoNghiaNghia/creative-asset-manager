from __future__ import annotations

import os
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.modules.ai_governance.model import AiUsageRecordModel
from app.modules.ai_metadata.model import AssetAiAnalysisModel, MetadataProfileModel
from app.modules.ai_operations.queries import AiOperationsRepository
from app.modules.ai_operations.schema import AiOperationsFilters
from app.modules.assets.model import AssetModel


DATABASE_URL = os.getenv("INTEGRATION_DATABASE_URL") or os.getenv("DATABASE_URL", "")
POSTGRES_AVAILABLE = DATABASE_URL.startswith(("postgresql://", "postgresql+psycopg://"))


@unittest.skipUnless(POSTGRES_AVAILABLE, "PostgreSQL integration database is not configured")
class AiOperationsPostgreSqlTest(unittest.TestCase):
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
