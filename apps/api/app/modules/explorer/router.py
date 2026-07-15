from fastapi import APIRouter, HTTPException, Query
import httpx
from app.modules.explorer.schema import FolderListing
from app.modules.explorer.service import ExplorerService

router = APIRouter(prefix="/explorer", tags=["explorer"])

@router.get("/children", response_model=FolderListing)
async def children(parent_id: str = Query("root")):
    try:
        return await ExplorerService().list_folder(parent_id)
    except (httpx.HTTPError, StopIteration) as exc:
        raise HTTPException(status_code=502, detail="Unable to load Google Drive folder") from exc
