from __future__ import annotations

from collections import Counter
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import and_, func, select, text

from app.core.database import SessionLocal
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.ai_operations.pipeline import PipelineOperationsRepository, SUPPORTED_IMAGE_MIME_TYPES
from app.modules.assets.model import AssetSourceLinkModel, ExternalSourceModel, SourceAssetModel
from app.modules.pipeline.model import AssetPipelineModel

def main() -> None:
    with SessionLocal() as session:
        if session.bind.dialect.name == "postgresql":
            session.execute(text("SET TRANSACTION READ ONLY"))
        completed_assets = (
            select(AssetAiAnalysisModel.tenant_id, AssetAiAnalysisModel.asset_id)
            .where(AssetAiAnalysisModel.status == "completed")
            .distinct().subquery()
        )
        summary = dict(session.execute(
            select(AssetAiAnalysisModel.status, func.count())
            .group_by(AssetAiAnalysisModel.status)
        ).all())
        distinct_completed = session.scalar(select(func.count()).select_from(completed_assets))
        print("AI_RESULTS_BY_STATUS", summary, "distinct_completed_assets", distinct_completed, flush=True)
        source = SourceAssetModel
        external = ExternalSourceModel
        pipeline = AssetPipelineModel
        link = AssetSourceLinkModel
        tenants = list(session.scalars(select(source.tenant_id).distinct()))
        totals = Counter()
        for tenant_id in tenants:
            active_ids, excluded = PipelineOperationsRepository(session)._active_source_ids(tenant_id)
            print("SOURCES", "active",len(active_ids),"decommissioned",excluded,flush=True)
            stmt = select(
                source.id,
                external.source_type,
                pipeline.state,
                pipeline.last_error_code,
                pipeline.asset_id,
                link.asset_id,
                completed_assets.c.asset_id,
            ).select_from(source).join(external, and_(
                source.tenant_id == external.tenant_id,
                source.external_source_id == external.id,
            )).outerjoin(pipeline, and_(
                pipeline.tenant_id == source.tenant_id,
                pipeline.source_asset_id == source.id,
            )).outerjoin(link, and_(
                link.tenant_id == source.tenant_id,
                link.source_asset_id == source.id,
            )).outerjoin(completed_assets, and_(
                completed_assets.c.tenant_id == source.tenant_id,
                completed_assets.c.asset_id == func.coalesce(link.asset_id, pipeline.asset_id),
            )).where(
                source.tenant_id == tenant_id,
                source.deleted_at.is_(None),
                source.mime_type.in_(SUPPORTED_IMAGE_MIME_TYPES),
                source.external_source_id.in_(active_ids),
            ).execution_options(stream_results=True, yield_per=2000)
            unique_sources = set()
            for sid, provider, state, error, pipeline_asset, linked_asset, completed_asset in session.execute(stmt):
                if sid in unique_sources:
                    totals["duplicate_pipeline_source_rows"] += 1
                    continue
                unique_sources.add(sid)
                totals["sources"] += 1
                totals[f"provider:{provider}"] += 1
                totals[f"state:{state or 'none'}"] += 1
                if linked_asset:
                    totals["linked_assets"] += 1
                if pipeline_asset:
                    totals["pipeline_asset_identified"] += 1
                if linked_asset and pipeline_asset and linked_asset != pipeline_asset:
                    totals["asset_link_mismatch"] += 1
                if completed_asset:
                    totals["sources_with_completed_ai"] += 1
                    if state != "completed":
                        totals[f"completed_ai_pipeline_state:{state or 'none'}"] += 1
                elif state == "completed":
                    totals["completed_pipeline_missing_completed_ai"] += 1
                if state and state.endswith("_failed"):
                    totals[f"error:{error or 'unknown'}"] += 1
            print("TENANT_RECONCILIATION", " ".join(f"{name}={count}" for name,count in totals.most_common()),flush=True)
        session.rollback()

if __name__ == "__main__":
    main()
