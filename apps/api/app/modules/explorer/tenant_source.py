from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.providers.google.auth import get_connection_access_token as google_access_token
from app.providers.microsoft.auth import get_connection_access_token as microsoft_access_token


_PURPOSE_BY_SOURCE_TYPE = {
    "google_drive": ("google", "google_drive_source"),
    "onedrive": ("microsoft", "onedrive_source"),
    "sharepoint": ("microsoft", "sharepoint_source"),
}


@dataclass(frozen=True, slots=True)
class ResolvedSourceAccess:
    external_source_id: str
    source_type: str
    connection_id: str
    provider_account_id: str
    access_token: str


# Compatibility name retained while call sites migrate to resolve().
TenantSourceAccess = ResolvedSourceAccess


class TenantSourceResolver:
    """Resolve the credential currently bound to one tenant source.

    Browser/provider application sessions are deliberately not part of this
    resolution path. The source row is the authority, so a queued job naturally
    follows a later reconnect without rewriting its payload.
    """

    def __init__(self, session: Session):
        self.session = session

    async def resolve(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        require_drive_write_scope: bool = False,
    ) -> ResolvedSourceAccess:
        source = self.session.scalar(
            select(ExternalSourceModel).where(
                ExternalSourceModel.tenant_id == tenant_id,
                ExternalSourceModel.id == external_source_id,
            )
        )
        if source is None:
            raise HTTPException(404, "The selected source is unavailable.")
        if source.status != "active":
            raise HTTPException(409, "The selected source requires reconnection.")
        expected = _PURPOSE_BY_SOURCE_TYPE.get(source.source_type)
        if expected is None:
            raise HTTPException(400, "The selected source type is unsupported.")
        if not source.oauth_connection_id:
            raise HTTPException(409, "The selected source requires reconnection.")

        provider, purpose = expected
        connection = self.session.scalar(
            select(OAuthConnectionModel).where(
                OAuthConnectionModel.id == source.oauth_connection_id,
                OAuthConnectionModel.tenant_id == tenant_id,
                OAuthConnectionModel.provider == provider,
                OAuthConnectionModel.connection_purpose == purpose,
                OAuthConnectionModel.status.in_(("active", "refresh_error")),
            )
        )
        if connection is None:
            raise HTTPException(409, "The selected source credential is unavailable.")

        connection_id = connection.id
        provider_account_id = connection.provider_account_id
        source_type = source.source_type
        self.session.close()

        if provider == "google":
            token = await google_access_token(
                connection_id,
                require_drive_write_scope=require_drive_write_scope,
            )
        else:
            token = await microsoft_access_token(connection_id, purpose=purpose)
        return ResolvedSourceAccess(
            external_source_id=external_source_id,
            source_type=source_type,
            connection_id=connection_id,
            provider_account_id=provider_account_id,
            access_token=token,
        )

    async def google_drive(
        self,
        *,
        tenant_id: str,
        external_source_id: str,
        require_drive_write_scope: bool = False,
    ) -> ResolvedSourceAccess:
        resolved = await self.resolve(
            tenant_id=tenant_id,
            external_source_id=external_source_id,
            require_drive_write_scope=require_drive_write_scope,
        )
        if resolved.source_type != "google_drive":
            raise HTTPException(400, "The selected source is not Google Drive.")
        return resolved
