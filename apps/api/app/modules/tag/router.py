from fastapi import APIRouter, HTTPException
import httpx
from app.modules.tag.schema import AssignTagsRequest, Tag
from app.modules.tag.service import TagService

router = APIRouter(prefix="/tags", tags=["tags"])

@router.get("", response_model=list[Tag])
async def tags():
    try: return await TagService().list_tags()
    except httpx.HTTPError as exc: raise HTTPException(502, "Unable to load Directus tags") from exc

@router.post("/assign")
async def assign(body: AssignTagsRequest):
    try: return {"assignments": await TagService().assign(body.item_ids, body.tag_id, body.provider)}
    except httpx.HTTPError as exc: raise HTTPException(502, "Unable to assign tag") from exc
