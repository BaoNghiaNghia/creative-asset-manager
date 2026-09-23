from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.auth_persistence.encryption import TokenCipher, TokenEncryptionError
from app.modules.public_review.repository import PublicReviewRepository
from app.modules.public_review.schema import CreateShareRequest, UpdateShareRequest
from app.modules.public_review.service import PublicReviewService, sha256_digest

router = APIRouter(prefix="/api/v1/public-review/shares", tags=["public-review"])
MANAGE_PUBLIC_REVIEW = require_permission("public_review.manage")


def _share_url(public_id: str, raw_secret: str) -> str:
    return f"{get_settings().PUBLIC_APP_URL.rstrip('/')}/share/{public_id}#key={raw_secret}"


class CurrentShareLinkUnavailable(RuntimeError):
    pass


def _secret_aad(share) -> str:
    return f"cam-public-review:{share.tenant_id}:{share.id}:secret"


def _share_secret_cipher(*, required: bool = True) -> TokenCipher | None:
    settings = get_settings()
    configured = settings.SENSITIVE_URL_ENCRYPTION_KEYS.strip()
    if not configured:
        if not required:
            return None
        raise HTTPException(
            503,
            {
                "code": "public_share_secret_encryption_unavailable",
                "message": "Share-link encryption is not configured",
            },
        )
    try:
        return TokenCipher.from_config(
            configured,
            settings.SENSITIVE_URL_ACTIVE_KEY_VERSION,
        )
    except ValueError as exc:
        raise HTTPException(
            503,
            {
                "code": "public_share_secret_encryption_unavailable",
                "message": "Share-link encryption is not configured",
            },
        ) from exc


def _persist_current_secret(
    repository: PublicReviewRepository,
    share,
    raw_secret: str,
):
    cipher = _share_secret_cipher(required=False)
    if cipher is None:
        # Creation/rotation must remain usable even when optional current-link
        # recovery has not been configured. The raw bearer secret is still
        # returned exactly once to the authenticated manager and is never
        # persisted in plaintext.
        return repository.update_share(
            share.tenant_id,
            share.id,
            secret_ciphertext=None,
            secret_key_version=None,
        )
    encrypted = cipher.encrypt(
        raw_secret,
        aad=_secret_aad(share),
    )
    if encrypted is None:
        raise RuntimeError("public review secret encryption failed")
    return repository.update_share(
        share.tenant_id,
        share.id,
        secret_ciphertext=encrypted.ciphertext,
        secret_key_version=encrypted.key_version,
    )


def _recover_current_secret(share) -> str:
    if not share.secret_ciphertext or not share.secret_key_version:
        raise CurrentShareLinkUnavailable(
            "Current share link is unavailable; update the share link once."
        )
    cipher = _share_secret_cipher()
    assert cipher is not None
    raw_secret = cipher.decrypt(
        share.secret_ciphertext,
        key_version=share.secret_key_version,
        aad=_secret_aad(share),
    )
    if not raw_secret or sha256_digest(raw_secret) != share.secret_digest:
        raise TokenEncryptionError("public review secret integrity check failed")
    return raw_secret


def _document(repository: PublicReviewRepository, share, one_time_url: str | None = None) -> dict:
    result = {"id": share.id, "public_id": share.public_id, "name": share.name, "status": share.status, "allow_comments": share.allow_comments, "allow_download": share.allow_download, "expires_at": share.expires_at, "created_at": share.created_at, "updated_at": share.updated_at, "revoked_at": share.revoked_at, "scopes": [{"external_source_id": row.external_source_id, "folder_external_id": row.folder_external_id, "folder_name": row.folder_name} for row in repository.list_scopes(share.tenant_id, share.id)]}
    if one_time_url:
        result["share_url"] = one_time_url
    return result


def _error(exc: Exception):
    if isinstance(exc, HTTPException):
        return exc
    if isinstance(exc, CurrentShareLinkUnavailable):
        return HTTPException(
            409,
            {
                "code": "current_share_link_unavailable",
                "message": str(exc),
            },
        )
    if isinstance(exc, TokenEncryptionError):
        return HTTPException(
            503,
            {
                "code": "public_share_secret_unavailable",
                "message": "The current share link cannot be recovered right now",
            },
        )
    if isinstance(exc, LookupError):
        return HTTPException(404, {"code": "public_share_not_found", "message": "Public share was not found"})
    return HTTPException(422, {"code": "invalid_public_share", "message": str(exc)})


@router.post("", status_code=201)
def create_share(body: CreateShareRequest, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share, secret = PublicReviewService(repository).create_managed_share(tenant_id=principal.active_tenant_id, actor_id=principal.user_id, name=body.name, scopes=[item.model_dump() for item in body.scopes], allow_comments=body.allow_comments, allow_download=body.allow_download, expires_at=body.expires_at)
            share = _persist_current_secret(repository, share, secret)
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


@router.get("/{share_id}/current-link")
def current_share_link(share_id: str, principal: CurrentPrincipal = Depends(MANAGE_PUBLIC_REVIEW)):
    with SessionLocal() as session:
        repository = PublicReviewRepository(session)
        try:
            share = repository.get_share(principal.active_tenant_id, share_id)
            if share is None or not share.is_active_at():
                raise LookupError("public share is unavailable")
            secret = _recover_current_secret(share)
            repository.audit_share_event(
                tenant_id=principal.active_tenant_id,
                actor_id=principal.user_id,
                action="public_review_share_link_retrieved",
                share_id=share.id,
            )
            result = {
                "share_url": _share_url(share.public_id, secret),
                "expires_at": share.expires_at,
            }
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc


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
            share = _persist_current_secret(repository, share, secret)
            result = _document(repository, share, _share_url(share.public_id, secret))
            session.commit()
            return result
        except Exception as exc:
            session.rollback()
            raise _error(exc) from exc