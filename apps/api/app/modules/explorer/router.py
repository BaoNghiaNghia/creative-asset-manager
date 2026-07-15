from fastapi import APIRouter, HTTPException, Query, Request
import httpx

from app.modules.explorer.schema import FolderListing
from app.modules.explorer.service import ExplorerService
from app.providers.google.auth import get_access_token

router = APIRouter(prefix="/explorer", tags=["explorer"])


@router.get("/children", response_model=FolderListing)
async def children(request: Request, parent_id: str = Query("root")):
    try:
        token = await get_access_token(request)
        return await ExplorerService().list_folder(parent_id, token)
    except HTTPException:
        raise
    except (httpx.HTTPError, StopIteration) as exc:
        status = 401 if isinstance(exc, httpx.HTTPStatusError) and exc.response.status_code == 401 else 502
        raise HTTPException(status_code=status, detail="Unable to load Google Drive folder") from exc
