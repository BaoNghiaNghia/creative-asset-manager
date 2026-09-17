from fastapi import APIRouter, Depends, HTTPException

from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.public_review.board_schema import (
    BoardIssueDetailDTO,
    BoardIssueFilters,
    BoardIssueListDTO,
    BoardStatsDTO,
    parse_board_issue_filters,
)
from app.modules.public_review.board_service import BoardIssueNotFound, BoardService


router = APIRouter(prefix="/api/v1/public-review/board", tags=["review-board"])
READ_PUBLIC_REVIEW = require_permission("public_review.read")


@router.get("/issues", response_model=BoardIssueListDTO)
def list_issues(
    filters: BoardIssueFilters = Depends(parse_board_issue_filters),
    principal: CurrentPrincipal = Depends(READ_PUBLIC_REVIEW),
) -> BoardIssueListDTO:
    with SessionLocal() as database:
        return BoardService(database).list_issues(
            tenant_id=principal.active_tenant_id, filters=filters
        )


@router.get("/issues/{annotation_id}", response_model=BoardIssueDetailDTO)
def get_issue(
    annotation_id: str,
    principal: CurrentPrincipal = Depends(READ_PUBLIC_REVIEW),
) -> BoardIssueDetailDTO:
    with SessionLocal() as database:
        try:
            return BoardService(database).get_issue(
                tenant_id=principal.active_tenant_id,
                annotation_id=annotation_id,
            )
        except BoardIssueNotFound as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "review_issue_not_found",
                    "message": "Review issue is unavailable",
                },
            ) from exc


@router.get("/stats", response_model=BoardStatsDTO)
def get_stats(
    principal: CurrentPrincipal = Depends(READ_PUBLIC_REVIEW),
) -> BoardStatsDTO:
    with SessionLocal() as database:
        return BoardService(database).stats(
            tenant_id=principal.active_tenant_id
        )

