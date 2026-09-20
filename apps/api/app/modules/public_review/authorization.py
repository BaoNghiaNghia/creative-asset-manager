from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.authorization.folder_scope import FolderScopeAccess, FolderScopeResolver
from app.modules.public_review.model import (
    PublicShareModel,
    PublicShareScopeModel,
    PublicShareSessionModel,
)
from app.modules.public_review.service import sha256_digest


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PublicShareAccessDenied(Exception):
    """Intentionally non-disclosing authorization failure for public shares."""


@dataclass(frozen=True, slots=True)
class SharePrincipal:
    share_id: str
    public_id: str
    tenant_id: str
    session_id: str
    guest_id: str | None
    allow_comments: bool
    allow_download: bool
    expires_at: datetime | None
    session_expires_at: datetime


class PublicShareScopeService:
    """Server-side authorization boundary for an already-issued public session."""

    def __init__(self, session: Session, *, now=utcnow):
        self.session = session
        self._now = now
        self._resolver = FolderScopeResolver(session)

    def resolve_principal(
        self,
        *,
        raw_session_token: str,
        expected_public_id: str | None = None,
    ) -> SharePrincipal:
        digest = sha256_digest(raw_session_token)
        row = self.session.scalar(
            select(PublicShareSessionModel).where(
                PublicShareSessionModel.session_digest == digest,
            )
        )
        now = self._instant(self._now())
        if row is None or row.revoked_at is not None or self._instant(row.expires_at) <= now:
            raise PublicShareAccessDenied()
        share = self.session.scalar(
            select(PublicShareModel).where(
                PublicShareModel.tenant_id == row.tenant_id,
                PublicShareModel.id == row.share_id,
            )
        )
        if (
            share is None
            or share.status != "active"
            or share.revoked_at is not None
            or (share.expires_at is not None and self._instant(share.expires_at) <= now)
            or (expected_public_id is not None and share.public_id != expected_public_id)
        ):
            raise PublicShareAccessDenied()
        return SharePrincipal(
            share_id=share.id,
            public_id=share.public_id,
            tenant_id=share.tenant_id,
            session_id=row.id,
            guest_id=row.guest_id,
            allow_comments=share.allow_comments,
            allow_download=share.allow_download,
            expires_at=share.expires_at,
            session_expires_at=row.expires_at,
        )

    @staticmethod
    def _instant(value: datetime) -> datetime:
        return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)

    def _require_active_principal(self, principal: SharePrincipal) -> None:
        now = self._instant(self._now())
        session_row = self.session.scalar(
            select(PublicShareSessionModel).where(
                PublicShareSessionModel.tenant_id == principal.tenant_id,
                PublicShareSessionModel.share_id == principal.share_id,
                PublicShareSessionModel.id == principal.session_id,
            )
        )
        share = self.session.scalar(
            select(PublicShareModel).where(
                PublicShareModel.tenant_id == principal.tenant_id,
                PublicShareModel.id == principal.share_id,
            )
        )
        if (
            session_row is None
            or session_row.revoked_at is not None
            or self._instant(session_row.expires_at) <= now
            or share is None
            or share.public_id != principal.public_id
            or share.status != "active"
            or share.revoked_at is not None
            or (share.expires_at is not None and self._instant(share.expires_at) <= now)
        ):
            raise PublicShareAccessDenied()

    def scoped_accesses(self, *, principal: SharePrincipal) -> dict[str, FolderScopeAccess]:
        self._require_active_principal(principal)
        rows = self.session.execute(
            select(PublicShareScopeModel.external_source_id, PublicShareScopeModel.folder_external_id)
            .where(
                PublicShareScopeModel.tenant_id == principal.tenant_id,
                PublicShareScopeModel.share_id == principal.share_id,
            )
        ).all()
        grouped: dict[str, set[str]] = {}
        for source_id, folder_id in rows:
            source = str(source_id or "").strip()
            folder = str(folder_id or "").strip()
            if source and folder:
                grouped.setdefault(source, set()).add(folder)
        return {
            source_id: FolderScopeAccess(True, source_id, frozenset(folder_ids))
            for source_id, folder_ids in grouped.items()
        }

    def allowed_asset_source_pairs(self, *, principal: SharePrincipal) -> set[tuple[str, str]]:
        allowed: set[tuple[str, str]] = set()
        for access in self.scoped_accesses(principal=principal).values():
            allowed.update(
                self._resolver.allowed_asset_source_pairs(
                    tenant_id=principal.tenant_id,
                    access=access,
                )
            )
        return allowed

    def authorize_asset_source_pair(
        self,
        *,
        principal: SharePrincipal,
        asset_id: str,
        source_asset_id: str,
    ) -> None:
        if (str(asset_id), str(source_asset_id)) not in self.allowed_asset_source_pairs(
            principal=principal,
        ):
            raise PublicShareAccessDenied()

    def allows_external_asset(
        self,
        *,
        principal: SharePrincipal,
        external_source_id: str,
        external_asset_id: str,
    ) -> bool:
        access = self.scoped_accesses(principal=principal).get(str(external_source_id))
        if access is None:
            return False
        return self._resolver.allows_external_asset(
            tenant_id=principal.tenant_id,
            access=access,
            external_asset_id=external_asset_id,
        )
