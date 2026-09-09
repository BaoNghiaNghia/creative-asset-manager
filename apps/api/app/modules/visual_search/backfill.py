from __future__ import annotations

import time
from dataclasses import dataclass
from sqlalchemy import select
from app.core.config import Settings
from app.modules.assets.model import AssetModel, AssetSourceLinkModel, SourceAssetModel
from app.modules.pipeline.mime_types import is_supported_image_mime_type
from app.modules.processing.repository import ProcessingRepository
from app.modules.visual_search.lifecycle import VISUAL_EMBEDDING_SCHEMA_VERSION, enqueue_visual_index_sync

@dataclass
class VisualSearchBackfillResult:
    scanned: int = 0
    eligible: int = 0
    enqueued: int = 0
    skipped_existing: int = 0
    skipped_unsupported: int = 0
    skipped_missing_hash: int = 0
    errors: int = 0
    checkpoint_asset_id: str | None = None
    stopped: bool = False

class VisualSearchBackfillService:
    def __init__(self, processing: ProcessingRepository, *, settings: Settings):
        self.processing, self.settings, self.session = processing, settings, processing.session

    def run(self, *, tenant_id: str, schema_version: str, after_asset_id: str | None = None, batch_size: int = 25, max_assets: int = 100, delay_seconds: float = 0.0, dry_run: bool = True, stop_requested=lambda: False) -> VisualSearchBackfillResult:
        if not tenant_id: raise ValueError("tenant_id is required")
        if schema_version != VISUAL_EMBEDDING_SCHEMA_VERSION: raise ValueError("unsupported visual embedding schema version")
        if not 1 <= batch_size <= 100: raise ValueError("batch_size must be between 1 and 100")
        if max_assets < 1 or delay_seconds < 0: raise ValueError("invalid max_assets or delay_seconds")
        if not dry_run and not self.settings.VISUAL_SEARCH_BACKFILL_ENABLED: raise ValueError("visual search backfill is disabled")
        result, checkpoint = VisualSearchBackfillResult(), after_asset_id
        while result.scanned < max_assets and not stop_requested():
            query = select(AssetModel, SourceAssetModel).join(AssetSourceLinkModel, AssetSourceLinkModel.asset_id == AssetModel.id).join(SourceAssetModel, SourceAssetModel.id == AssetSourceLinkModel.source_asset_id).where(AssetModel.tenant_id == tenant_id, AssetSourceLinkModel.tenant_id == tenant_id, SourceAssetModel.tenant_id == tenant_id, SourceAssetModel.deleted_at.is_(None)).order_by(AssetModel.id.asc(), SourceAssetModel.id.asc())
            if checkpoint: query = query.where(AssetModel.id > checkpoint)
            rows = self.session.execute(query.limit(min(batch_size, max_assets-result.scanned))).all()
            if not rows: break
            seen = set()
            for asset, source in rows:
                if asset.id in seen: continue
                seen.add(asset.id); checkpoint = asset.id; result.checkpoint_asset_id = checkpoint; result.scanned += 1
                if not asset.content_hash: result.skipped_missing_hash += 1; continue
                if not is_supported_image_mime_type(source.mime_type): result.skipped_unsupported += 1; continue
                result.eligible += 1
                if dry_run: result.enqueued += 1; continue
                try:
                    created = enqueue_visual_index_sync(self.processing, settings=self.settings, tenant_id=tenant_id, asset_id=asset.id, source_asset_id=source.id, content_sha256=asset.content_hash)
                    result.enqueued += int(created)
                    result.skipped_existing += int(not created)
                except Exception: result.errors += 1
            if delay_seconds and result.scanned < max_assets: time.sleep(delay_seconds)
        result.stopped = bool(stop_requested())
        return result
