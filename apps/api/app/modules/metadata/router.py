from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.core.cloud_account import cloud_account_id, cloud_tenant_id
from app.core.database import get_db
from app.modules.metadata.asset_service import AssetMetadataService
from app.modules.metadata.schema import MetadataQueryRequest, SetRatingRequest
from app.modules.tag.schema import MetadataResponse

router = APIRouter(prefix="/metadata", tags=["metadata"])


@router.post("/query", response_model=MetadataResponse)
def query_metadata(
    body: MetadataQueryRequest,
    request: Request,
    session: Session = Depends(get_db),
):
    return {
        "items": AssetMetadataService(session).list(
            cloud_account_id(request, body.provider),
            body.provider,
            body.item_ids,
            processing_tenant_id=cloud_tenant_id(request, body.provider),
            external_source_id=body.external_source_id,
        )
    }


@router.put("/rating", response_model=MetadataResponse)
def set_rating(
    body: SetRatingRequest,
    request: Request,
    session: Session = Depends(get_db),
):
    return {
        "items": AssetMetadataService(session).set_rating(
            cloud_account_id(request, body.provider),
            body.provider,
            body.item_ids,
            body.rating,
        )
    }
