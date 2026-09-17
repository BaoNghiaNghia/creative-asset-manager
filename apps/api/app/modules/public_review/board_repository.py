from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import case, false, func, select
from sqlalchemy.orm import Session, aliased

from app.modules.assets.model import (
    AssetModel,
    AssetSourceLinkModel,
    ExternalSourceModel,
    SourceAssetModel,
)
from app.modules.authorization.folder_scope import FolderScopeAccess, FolderScopeResolver
from app.modules.public_review.board_schema import (
    BoardIssueFilters,
    BoardIssueSort,
    BoardIssueStatus,
)
from app.modules.public_review.model import (
    AssetAnnotationModel,
    PublicShareGuestModel,
    PublicShareModel,
    PublicShareScopeModel,
)


@dataclass(frozen=True, slots=True)
class BoardStats:
    total_issues: int
    open_issues: int
    resolved_issues: int
    assets_with_open_issues: int
    shares_with_open_issues: int


class BoardRepository:
    def __init__(self, session: Session):
        self.session = session
        self.folder_resolver = FolderScopeResolver(session)

    @staticmethod
    def _issue_query(tenant_id: str):
        annotation = AssetAnnotationModel
        return (
            select(
                annotation,
                PublicShareGuestModel,
                PublicShareModel,
                AssetModel,
                SourceAssetModel,
            )
            .join(
                PublicShareGuestModel,
                (PublicShareGuestModel.tenant_id == annotation.tenant_id)
                & (PublicShareGuestModel.share_id == annotation.share_id)
                & (PublicShareGuestModel.id == annotation.guest_id),
            )
            .join(
                PublicShareModel,
                (PublicShareModel.tenant_id == annotation.tenant_id)
                & (PublicShareModel.id == annotation.share_id),
            )
            .join(
                AssetSourceLinkModel,
                (AssetSourceLinkModel.tenant_id == annotation.tenant_id)
                & (AssetSourceLinkModel.asset_id == annotation.asset_id)
                & (AssetSourceLinkModel.source_asset_id == annotation.source_asset_id),
            )
            .join(
                AssetModel,
                (AssetModel.tenant_id == annotation.tenant_id)
                & (AssetModel.id == AssetSourceLinkModel.asset_id),
            )
            .join(
                SourceAssetModel,
                (SourceAssetModel.tenant_id == annotation.tenant_id)
                & (SourceAssetModel.id == AssetSourceLinkModel.source_asset_id),
            )
            .where(
                annotation.tenant_id == tenant_id,
                annotation.parent_annotation_id.is_(None),
            )
        )

    def _folder_source_asset_ids(
        self, *, tenant_id: str, filters: BoardIssueFilters
    ) -> set[str]:
        if not filters.folder_external_id:
            return set()
        if filters.external_source_id:
            source_ids = [filters.external_source_id]
        elif filters.share_id:
            source_ids = list(
                self.session.scalars(
                    select(PublicShareScopeModel.external_source_id)
                    .where(
                        PublicShareScopeModel.tenant_id == tenant_id,
                        PublicShareScopeModel.share_id == filters.share_id,
                    )
                    .distinct()
                )
            )
        else:
            source_ids = list(
                self.session.scalars(
                    select(ExternalSourceModel.id).where(
                        ExternalSourceModel.tenant_id == tenant_id
                    )
                )
            )
        allowed: set[str] = set()
        for source_id in source_ids:
            allowed.update(
                self.folder_resolver.allowed_source_asset_ids(
                    tenant_id=tenant_id,
                    access=FolderScopeAccess(
                        restricted=True,
                        source_id=str(source_id),
                        folder_ids=frozenset({filters.folder_external_id}),
                    ),
                )
            )
        return allowed

    def filtered_issue_query(
        self, *, tenant_id: str, filters: BoardIssueFilters
    ):
        annotation = AssetAnnotationModel
        query = self._issue_query(tenant_id)
        if filters.status != BoardIssueStatus.ALL:
            query = query.where(annotation.status == filters.status.value)
        if filters.share_id:
            query = query.where(annotation.share_id == filters.share_id)
        if filters.external_source_id:
            query = query.where(
                SourceAssetModel.external_source_id == filters.external_source_id
            )
        if filters.folder_external_id:
            source_asset_ids = self._folder_source_asset_ids(
                tenant_id=tenant_id, filters=filters
            )
            query = query.where(
                annotation.source_asset_id.in_(source_asset_ids)
                if source_asset_ids
                else false()
            )
        if filters.asset_id:
            query = query.where(annotation.asset_id == filters.asset_id)
        if filters.reviewer:
            query = query.where(
                func.lower(PublicShareGuestModel.display_name).contains(
                    filters.reviewer.strip().casefold(), autoescape=True
                )
            )
        if filters.pinned is True:
            query = query.where(
                annotation.anchor_x.is_not(None), annotation.anchor_y.is_not(None)
            )
        elif filters.pinned is False:
            query = query.where(
                annotation.anchor_x.is_(None), annotation.anchor_y.is_(None)
            )
        if filters.created_from:
            query = query.where(annotation.created_at >= filters.created_from)
        if filters.created_to:
            query = query.where(annotation.created_at <= filters.created_to)
        return query

    @staticmethod
    def _ordered(query, sort: BoardIssueSort):
        annotation = AssetAnnotationModel
        if sort == BoardIssueSort.OLDEST:
            return query.order_by(annotation.created_at.asc(), annotation.id.asc())
        if sort == BoardIssueSort.RECENTLY_UPDATED:
            return query.order_by(annotation.updated_at.desc(), annotation.id.desc())
        if sort == BoardIssueSort.RECENTLY_RESOLVED:
            return query.order_by(
                annotation.resolved_at.desc().nullslast(), annotation.id.desc()
            )
        return query.order_by(annotation.created_at.desc(), annotation.id.desc())

    def list_issues(
        self, *, tenant_id: str, filters: BoardIssueFilters
    ) -> tuple[list[tuple], int]:
        filtered = self.filtered_issue_query(tenant_id=tenant_id, filters=filters)
        total = int(
            self.session.scalar(
                select(func.count()).select_from(
                    filtered.with_only_columns(AssetAnnotationModel.id)
                    .order_by(None)
                    .subquery()
                )
            )
            or 0
        )
        rows = self.session.execute(
            self._ordered(filtered, filters.sort)
            .offset((filters.page - 1) * filters.page_size)
            .limit(filters.page_size)
        ).all()
        return rows, total

    def get_issue(self, *, tenant_id: str, annotation_id: str):
        return self.session.execute(
            self._issue_query(tenant_id).where(
                AssetAnnotationModel.id == annotation_id
            )
        ).first()

    def reply_counts(
        self, *, tenant_id: str, issue_rows: list[tuple]
    ) -> dict[str, int]:
        if not issue_rows:
            return {}
        parent = aliased(AssetAnnotationModel)
        reply = aliased(AssetAnnotationModel)
        issue_ids = [row[0].id for row in issue_rows]
        rows = self.session.execute(
            select(reply.parent_annotation_id, func.count(reply.id))
            .join(
                parent,
                (parent.tenant_id == reply.tenant_id)
                & (parent.share_id == reply.share_id)
                & (parent.asset_id == reply.asset_id)
                & (parent.source_asset_id == reply.source_asset_id)
                & (parent.id == reply.parent_annotation_id),
            )
            .where(
                reply.tenant_id == tenant_id,
                parent.tenant_id == tenant_id,
                parent.parent_annotation_id.is_(None),
                parent.id.in_(issue_ids),
            )
            .group_by(reply.parent_annotation_id)
        ).all()
        return {str(parent_id): int(count) for parent_id, count in rows}

    def list_replies(self, *, tenant_id: str, issue: AssetAnnotationModel):
        reply = aliased(AssetAnnotationModel)
        guest = aliased(PublicShareGuestModel)
        return self.session.execute(
            select(reply, guest)
            .join(
                guest,
                (guest.tenant_id == reply.tenant_id)
                & (guest.share_id == reply.share_id)
                & (guest.id == reply.guest_id),
            )
            .join(
                AssetSourceLinkModel,
                (AssetSourceLinkModel.tenant_id == reply.tenant_id)
                & (AssetSourceLinkModel.asset_id == reply.asset_id)
                & (AssetSourceLinkModel.source_asset_id == reply.source_asset_id),
            )
            .where(
                reply.tenant_id == tenant_id,
                reply.parent_annotation_id == issue.id,
                reply.share_id == issue.share_id,
                reply.asset_id == issue.asset_id,
                reply.source_asset_id == issue.source_asset_id,
            )
            .order_by(reply.created_at.asc(), reply.id.asc())
        ).all()

    def stats(self, *, tenant_id: str) -> BoardStats:
        issues = (
            self._issue_query(tenant_id)
            .with_only_columns(
                AssetAnnotationModel.id,
                AssetAnnotationModel.status,
                AssetAnnotationModel.asset_id,
                AssetAnnotationModel.source_asset_id,
                AssetAnnotationModel.share_id,
            )
            .order_by(None)
            .subquery()
        )
        open_pairs = (
            select(issues.c.asset_id, issues.c.source_asset_id)
            .where(issues.c.status == "open")
            .distinct()
            .subquery()
        )
        open_shares = (
            select(issues.c.share_id)
            .where(issues.c.status == "open")
            .distinct()
            .subquery()
        )
        row = self.session.execute(
            select(
                func.count(issues.c.id),
                func.coalesce(
                    func.sum(case((issues.c.status == "open", 1), else_=0)), 0
                ),
                func.coalesce(
                    func.sum(case((issues.c.status == "resolved", 1), else_=0)), 0
                ),
                select(func.count()).select_from(open_pairs).scalar_subquery(),
                select(func.count()).select_from(open_shares).scalar_subquery(),
            ).select_from(issues)
        ).one()
        return BoardStats(*(int(value or 0) for value in row))

