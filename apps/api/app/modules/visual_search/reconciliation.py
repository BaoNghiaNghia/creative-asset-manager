from __future__ import annotations

import asyncio
from dataclasses import dataclass
from collections import defaultdict

from app.modules.visual_search.coverage_repository import VisualCoverageResourceReader
from app.modules.visual_search.model_spec import VISUAL_SEARCH_BASELINE_DESCRIPTOR
from app.modules.visual_search.lifecycle import enqueue_visual_index_sync


@dataclass(frozen=True)
class VisualReconciliationResult:
    scanned: int
    current: int
    missing: int
    stale: int
    enqueued: int
    existing: int


def _current(document, resource) -> bool:
    descriptor=VISUAL_SEARCH_BASELINE_DESCRIPTOR
    return document.get("asset_id")==resource.asset_id and document.get("content_sha256")==resource.content_hash and not document.get("is_deleted") and not document.get("is_hidden") and all(document.get(key)==getattr(descriptor,key) for key in ("embedding_schema_version","encoder_name","encoder_revision","preprocess_version","similarity"))


class VisualSearchReconciliationService:
    """Bounded, tenant-scoped producer for current visual projection work."""
    def __init__(self, session, processing, index, *, settings):
        self.session,self.processing,self.index,self.settings=session,processing,index,settings

    def reconcile(self, *, tenant_id: str, max_assets: int=100) -> VisualReconciliationResult:
        if not 1 <= max_assets <= 1000: raise ValueError("max_assets must be between 1 and 1000")
        resources=VisualCoverageResourceReader(self.session).resources(tenant_id)
        documents=asyncio.run(self.index.scan_projection_metadata(tenant_id))
        by_asset=defaultdict(list)
        for document in documents:
            if document.get("tenant_id")==tenant_id: by_asset[document.get("asset_id")].append(document)
        assets={}
        for resource in resources:
            if resource.eligible and resource.asset_id and resource.asset_id not in assets: assets[resource.asset_id]=resource
        current=missing=stale=enqueued=existing=0
        for asset_id in sorted(assets)[:max_assets]:
            resource=assets[asset_id]; candidates=by_asset[asset_id]
            if any(_current(document,resource) for document in candidates): current+=1; continue
            if candidates: stale+=1
            else: missing+=1
            created=enqueue_visual_index_sync(self.processing, settings=self.settings, tenant_id=tenant_id, asset_id=resource.asset_id, source_asset_id=resource.source_asset_id, content_sha256=resource.content_hash)
            enqueued+=int(created); existing+=int(not created)
        return VisualReconciliationResult(len(assets),current,missing,stale,enqueued,existing)
