from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.schema import CreateShareRequest, UpdateShareRequest
from app.modules.public_review.service import PublicReviewService

router = APIRouter(prefix="/api/v1/public-review/shares", tags=["public-review"])
MANAGE_PUBLIC_REVIEW = require_permission("public_review.manage")


def _share_url(public_id: str, raw_secret: str) -> str:
    return f"{get_settings().PUBLIC_APP_URL.rstrip('/')}/share/{public_id}#key={raw_secret}"


def _document(repository: PublicReviewRepository, share, one_time_url: str | None = None) -> dict:
    result = {"id": share.id, "public_id": share.public_id, "name": share.name, "status": share.status, "allow_comments": share.allow_comments, "allow_download": share.allow_download, "expires_at": share.expires_at, "created_at": share.created_at, "updated_at": share.updated_at, "revoked_at": share.revoked_at, "scopes": [{"external_source_id": row.external_source_id, "folder_external_id": row.folder_external_id, "folder_name": row.folder_name} for row in repository.list_scopes(share.tenant_id, share.id)]}
    if one_time_url:
        result["share_url"] = one_time_url
    return result


def _error(exc: Exception):
    if isinstance(exc, LookupError):
        return HTTPException(404, {"code": "public_share_not_found", "message": "Public share was not found"})
    return HTTPException(422, {"code": "invalid_public_share", "message": str(exc)})


@router.post("", status_code=201)
def create_share(body: CreateShareRequest, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share, secret = PublicReviewService(repository).create_managed_share(tenant_id=principal.active_tenant_id, actor_id=principal.user_id, name=body.name, scopes=[item.model_dump() for item in body.scopes], allow_comments=body.allow_comments, allow_download=body.allow_download, expires_at=body.expires_at)
            result = _document(repository, share, _share_url(share.public_id, secret))
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc


@router.get("")
def list_shares(principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        return {"items": [_document(repository, item) for item in repository.list_shares(principal.active_tenant_id)]}


@router.get("/{share_id}")
def get_share(share_id: str, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        share = repository.get_share(principal.active_tenant_id, share_id)
        if share is None:
            raise _error(LookupError())
        return _document(repository, share)


@router.patch("/{share_id}")
def update_share(share_id: str, body: UpdateShareRequest, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share = PublicReviewService(repository).update_managed_share(tenant_id=principal.active_tenant_id, actor_id=principal.user_id, share_id=share_id, changes=body.model_dump(exclude_unset=True, exclude={"scopes"}), scopes=None if body.scopes is None else [item.model_dump() for item in body.scopes])
            result = _document(repository, share)
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc


@router.delete("/{share_id}")
def revoke_share(share_id: str, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share = PublicReviewService(repository).revoke_managed_share(tenant_id=principal.active_tenant_id, actor_id=principal.user_id, share_id=share_id)
            result = _document(repository, share)
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc


@router.post("/{share_id}/rotate-secret")
def rotate_share_secret(share_id: str, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share, secret = PublicReviewService(repository).rotate_managed_share_secret(tenant_id=principal.active_tenant_id, actor_id=principal.user_id, share_id=share_id)
            result = _document(repository, share, _share_url(share.public_id, secret))
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc
