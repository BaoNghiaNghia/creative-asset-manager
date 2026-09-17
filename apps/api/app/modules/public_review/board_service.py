from __future__ import annotations

from sqlalchemy.orm import Session

from app.modules.public_review.board_repository import BoardRepository
from app.modules.public_review.board_schema import (
    ANNOTATION_PREVIEW_LIMIT,
    DISPLAY_NAME_LIMIT,
    FILENAME_LIMIT,
    MEDIA_TYPE_LIMIT,
    RESOLVER_LIMIT,
    SHARE_NAME_LIMIT,
    BoardAssetDTO,
    BoardIssueDTO,
    BoardIssueDetailDTO,
    BoardIssueFilters,
    BoardIssueListDTO,
    BoardReplyDTO,
    BoardStatsDTO,
    ResolverDTO,
    ReviewerDTO,
    ShareDTO,
)


class BoardIssueNotFound(LookupError):
    pass


def _bounded(value: object, limit: int) -> str:
    return str(value or "")[:limit]


class BoardService:
    def __init__(self, session: Session):
        self.repository = BoardRepository(session)

    @staticmethod
    def _issue_dto(row: tuple, reply_count: int) -> BoardIssueDTO:
        annotation, guest, share, asset, source = row
        return BoardIssueDTO(
            id=annotation.id,
            status=annotation.status,
            annotation_preview=_bounded(
                annotation.plain_text, ANNOTATION_PREVIEW_LIMIT
            ),
            created_at=annotation.created_at,
            updated_at=annotation.updated_at,
            anchor_x=annotation.anchor_x,
            anchor_y=annotation.anchor_y,
            reviewer=ReviewerDTO(
                display_name=_bounded(guest.display_name, DISPLAY_NAME_LIMIT)
            ),
            share=ShareDTO(
                id=share.id, name=_bounded(share.name, SHARE_NAME_LIMIT)
            ),
            asset=BoardAssetDTO(
                asset_id=asset.id,
                source_asset_id=source.id,
                filename=(
                    _bounded(source.filename, FILENAME_LIMIT)
                    if source.filename is not None
                    else None
                ),
                media_type=(
                    _bounded(source.mime_type or asset.mime_type, MEDIA_TYPE_LIMIT)
                    if source.mime_type or asset.mime_type
                    else None
                ),
            ),
            reply_count=reply_count,
            resolved_at=annotation.resolved_at,
            resolver=(
                ResolverDTO(
                    actor_id=_bounded(annotation.resolved_by, RESOLVER_LIMIT)
                )
                if annotation.resolved_by
                else None
            ),
        )

    def list_issues(
        self, *, tenant_id: str, filters: BoardIssueFilters
    ) -> BoardIssueListDTO:
        rows, total = self.repository.list_issues(
            tenant_id=tenant_id, filters=filters
        )
        reply_counts = self.repository.reply_counts(
            tenant_id=tenant_id, issue_rows=rows
        )
        return BoardIssueListDTO(
            items=[
                self._issue_dto(row, reply_counts.get(str(row[0].id), 0))
                for row in rows
            ],
            page=filters.page,
            page_size=filters.page_size,
            total=total,
        )

    def get_issue(
        self, *, tenant_id: str, annotation_id: str
    ) -> BoardIssueDetailDTO:
        row = self.repository.get_issue(
            tenant_id=tenant_id, annotation_id=annotation_id
        )
        if row is None:
            raise BoardIssueNotFound("review issue unavailable")
        annotation = row[0]
        replies = [
            BoardReplyDTO(
                id=reply.id,
                content_json=reply.content_json,
                plain_text=_bounded(reply.plain_text, 10000),
                created_at=reply.created_at,
                updated_at=reply.updated_at,
                reviewer=ReviewerDTO(
                    display_name=_bounded(guest.display_name, DISPLAY_NAME_LIMIT)
                ),
            )
            for reply, guest in self.repository.list_replies(
                tenant_id=tenant_id, issue=annotation
            )
        ]
        issue = self._issue_dto(row, len(replies))
        return BoardIssueDetailDTO(
            **issue.model_dump(),
            content_json=annotation.content_json,
            plain_text=_bounded(annotation.plain_text, 10000),
            replies=replies,
        )

    def stats(self, *, tenant_id: str) -> BoardStatsDTO:
        stats = self.repository.stats(tenant_id=tenant_id)
        return BoardStatsDTO(
            total_issues=stats.total_issues,
            open_issues=stats.open_issues,
            resolved_issues=stats.resolved_issues,
            resolution_rate=(
                0
                if stats.total_issues == 0
                else round(stats.resolved_issues * 100 / stats.total_issues)
            ),
            assets_with_open_issues=stats.assets_with_open_issues,
            shares_with_open_issues=stats.shares_with_open_issues,
        )

