from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Protocol, Sequence

from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import Session, aliased

from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import AssetModel
from app.modules.processing.model import ProcessingJobModel


class CoverageSearchIndex(Protocol):
    async def search(self, body: Mapping[str, Any]) -> Mapping[str, Any]: ...


@dataclass(frozen=True, slots=True)
class CoverageAuditItem:
    asset_id: str
    analysis_id: str
    category: str


@dataclass(frozen=True, slots=True)
class CoverageAuditResult:
    items: tuple[CoverageAuditItem, ...]
    scanned: int
    counts: Mapping[str, int]
    duplicate_analysis_skipped: int
    failed: int
    next_created_at: datetime | None
    next_analysis_id: str | None

    def to_document(self) -> dict[str, Any]:
        counts = Counter(self.counts)
        skipped = counts["duplicate_analysis_skipped"] + counts["unsupported_asset_skipped"]
        return {
            "scanned": self.scanned,
            "healthy": counts["healthy"],
            "projection_missing": counts["projection_missing"],
            "projection_stale": counts["projection_stale"],
            "index_job_missing": counts["index_job_missing"],
            "index_job_failed": counts["index_job_failed"],
            "document_missing": counts["database_indexed_document_missing"],
            "skipped": skipped,
            "failed": self.failed,
            "categories": dict(sorted(counts.items())),
            "items": [
                {"asset_id": item.asset_id, "analysis_id": item.analysis_id, "category": item.category}
                for item in self.items
            ],
            "cursor": {
                "after_created_at": self.next_created_at.isoformat() if self.next_created_at else None,
                "after_analysis_id": self.next_analysis_id,
            },
        }


class SearchV3CoverageAudit:
    """Read-only tenant-scoped coverage check for the active Search V3 alias."""

    def __init__(self, session: Session, *, projection_version: str, index: CoverageSearchIndex | None = None):
        if not projection_version.strip():
            raise ValueError("projection_version is required")
        self.session = session
        self.projection_version = projection_version
        self.index = index

    async def run(
        self, *, tenant_id: str, page_size: int, limit: int | None = None,
        after_created_at: datetime | None = None, after_analysis_id: str | None = None,
        verify_elasticsearch: bool = False,
    ) -> CoverageAuditResult:
        if not tenant_id:
            raise ValueError("tenant_id is required")
        if not 1 <= page_size <= 500:
            raise ValueError("page_size must be between 1 and 500")
        if limit is not None and limit < 1:
            raise ValueError("limit must be positive")
        if (after_created_at is None) != (after_analysis_id is None):
            raise ValueError("after_created_at and after_analysis_id must be provided together")
        if verify_elasticsearch and self.index is None:
            raise ValueError("Search V3 index is required for Elasticsearch verification")

        selected = self._latest_page(
            tenant_id=tenant_id,
            page_size=min(page_size, limit) if limit is not None else page_size,
            after_created_at=after_created_at,
            after_analysis_id=after_analysis_id,
        )
        duplicates = self._duplicate_count(tenant_id=tenant_id, page=selected)
        index_jobs = self._index_jobs(tenant_id=tenant_id, asset_ids=[analysis.asset_id for analysis in selected])
        document_versions = await self._document_versions(tenant_id, selected) if verify_elasticsearch else {}

        counts: Counter[str] = Counter()
        items: list[CoverageAuditItem] = []
        for analysis in selected:
            category = self._classify(
                analysis,
                index_jobs.get((analysis.asset_id, analysis.id), ()),
                document_versions.get(analysis.asset_id),
                verify_elasticsearch=verify_elasticsearch,
            )
            counts[category] += 1
            items.append(CoverageAuditItem(analysis.asset_id, analysis.id, category))

        counts["duplicate_analysis_skipped"] += duplicates
        cursor_analysis = selected[-1] if selected else None
        return CoverageAuditResult(
            items=tuple(items), scanned=len(selected), counts=counts,
            duplicate_analysis_skipped=duplicates, failed=0,
            next_created_at=cursor_analysis.created_at if cursor_analysis else None,
            next_analysis_id=cursor_analysis.id if cursor_analysis else None,
        )

    def _latest_page(
        self, *, tenant_id: str, page_size: int,
        after_created_at: datetime | None, after_analysis_id: str | None,
    ) -> list[AssetAiAnalysisModel]:
        newer = aliased(AssetAiAnalysisModel)
        is_latest = ~exists(select(1).where(
            newer.tenant_id == AssetAiAnalysisModel.tenant_id,
            newer.asset_id == AssetAiAnalysisModel.asset_id,
            newer.status == "completed",
            or_(
                newer.created_at > AssetAiAnalysisModel.created_at,
                and_(newer.created_at == AssetAiAnalysisModel.created_at, newer.id > AssetAiAnalysisModel.id),
            ),
        ))
        statement = select(AssetAiAnalysisModel).join(
            AssetModel,
            and_(AssetModel.id == AssetAiAnalysisModel.asset_id, AssetModel.tenant_id == AssetAiAnalysisModel.tenant_id),
        ).where(
            AssetAiAnalysisModel.tenant_id == tenant_id,
            AssetAiAnalysisModel.status == "completed",
            is_latest,
        )
        if after_created_at is not None and after_analysis_id is not None:
            statement = statement.where(or_(
                AssetAiAnalysisModel.created_at > after_created_at,
                and_(AssetAiAnalysisModel.created_at == after_created_at, AssetAiAnalysisModel.id > after_analysis_id),
            ))
        return list(self.session.scalars(
            statement.order_by(AssetAiAnalysisModel.created_at, AssetAiAnalysisModel.id).limit(page_size)
        ))

    def _duplicate_count(self, *, tenant_id: str, page: Sequence[AssetAiAnalysisModel]) -> int:
        if not page:
            return 0
        selected_ids = {item.id for item in page}
        selected_asset_ids = {item.asset_id for item in page}
        rows = self.session.scalars(select(AssetAiAnalysisModel).where(
            AssetAiAnalysisModel.tenant_id == tenant_id,
            AssetAiAnalysisModel.status == "completed",
            AssetAiAnalysisModel.asset_id.in_(selected_asset_ids),
            AssetAiAnalysisModel.id.not_in(selected_ids),
        ))
        return sum(1 for _ in rows)

    def _index_jobs(self, *, tenant_id: str, asset_ids: Sequence[str]) -> dict[tuple[str, str], tuple[ProcessingJobModel, ...]]:
        if not asset_ids:
            return {}
        grouped: dict[tuple[str, str], list[ProcessingJobModel]] = defaultdict(list)
        jobs = self.session.scalars(select(ProcessingJobModel).where(
            ProcessingJobModel.tenant_id == tenant_id,
            ProcessingJobModel.job_type == "asset_index",
            ProcessingJobModel.entity_type == "asset",
            ProcessingJobModel.entity_id.in_(tuple(asset_ids)),
        ).order_by(ProcessingJobModel.created_at.desc(), ProcessingJobModel.id.desc()))
        for job in jobs:
            analysis_id = (job.payload_json or {}).get("analysis_id")
            if isinstance(analysis_id, str):
                grouped[(job.entity_id, analysis_id)].append(job)
        return {key: tuple(value) for key, value in grouped.items()}

    async def _document_versions(self, tenant_id: str, analyses: Sequence[AssetAiAnalysisModel]) -> dict[str, str]:
        asset_ids = [analysis.asset_id for analysis in analyses]
        if not asset_ids:
            return {}
        response = await self.index.search({
            "size": len(asset_ids),
            "_source": ["asset_id", "tenant_id", "search_projection_version"],
            "query": {"bool": {"filter": [
                {"term": {"tenant_id": tenant_id}},
                {"ids": {"values": asset_ids}},
            ]}},
        })
        values: dict[str, str] = {}
        for hit in response.get("hits", {}).get("hits", []):
            source = hit.get("_source")
            if not isinstance(source, Mapping):
                continue
            asset_id = source.get("asset_id") or hit.get("_id")
            if isinstance(asset_id, str) and source.get("tenant_id") == tenant_id and isinstance(source.get("search_projection_version"), str):
                values[asset_id] = source["search_projection_version"]
        return values

    def _classify(
        self, analysis: AssetAiAnalysisModel, jobs: Sequence[ProcessingJobModel],
        document_version: str | None, *, verify_elasticsearch: bool,
    ) -> str:
        if not isinstance(analysis.search_projection, Mapping) or not analysis.search_projection_version:
            return "projection_missing"
        if analysis.search_projection_version != self.projection_version:
            return "projection_stale"
        statuses = {job.status for job in jobs}
        if "completed" not in statuses:
            return "index_job_failed" if "failed" in statuses else "index_job_missing"
        if verify_elasticsearch and document_version != self.projection_version:
            return "database_indexed_document_missing"
        return "healthy"
