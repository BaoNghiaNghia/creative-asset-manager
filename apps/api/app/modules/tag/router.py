from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.cloud_account import cloud_account_id
from app.core.database import get_db
from app.modules.tag.schema import AssignTagsRequest, MetadataResponse, Tag
from app.modules.tag.service import TagService, UnknownTagError

router = APIRouter(prefix="/tags", tags=["tags"])


@router.get("", response_model=list[Tag])
def tags(session: Session = Depends(get_db)):
    return TagService(session).list_tags()


@router.post("/assign", response_model=MetadataResponse)
def assign(
    body: AssignTagsRequest,
    request: Request,
    session: Session = Depends(get_db),
):
    try:
        return {
            "items": TagService(session).assign(
                cloud_account_id(request, body.provider),
                body.item_ids,
                body.tag_id,
                body.provider,
            )
        }
    except UnknownTagError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
