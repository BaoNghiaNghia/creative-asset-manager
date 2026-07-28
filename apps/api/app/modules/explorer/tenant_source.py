from __future__ import annotations

from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.assets.model import ExternalSourceModel
from app.providers.google.auth import get_connection_access_token


@dataclass(frozen=True, slots=True)
class TenantSourceAccess:
    external_source_id: str
    provider_account_id: str
    access_token: str


class TenantSourceResolver:
    """Resolve a tenant-managed cloud connection without using a viewer's token."""

    def __init__(self, session: Session):
        self.session = session

    async def google_drive(
        self,
        *,
        tenant_id: str,
        external_source_id: str | None = None,
    ) -> TenantSourceAccess:
        statement = select(ExternalSourceModel).where(
            ExternalSourceModel.tenant_id == tenant_id,
            ExternalSourceModel.source_type == "google_drive",
        )
        if external_source_id:
            statement = statement.where(ExternalSourceModel.id == external_source_id)
        sources = [
            source
            for source in self.session.scalars(
                statement.order_by(ExternalSourceModel.created_at)
            )
            if isinstance((source.source_metadata or {}).get("oauth_connection_id"), str)
            and (source.source_metadata or {}).get("oauth_connection_id")
        ]
        if not sources:
            raise HTTPException(
                status_code=404,
                detail="No Google Drive source is configured for this workspace.",
            )
        if len(sources) > 1 and not external_source_id:
            defaults = [
                source
                for source in sources
                if bool((source.source_metadata or {}).get("is_default"))
            ]
            if len(defaults) == 1:
                sources = defaults
            else:
                raise HTTPException(
                    status_code=409,
                    detail="Multiple Google Drive sources are configured. Select a source.",
                )
        if len(sources) != 1:
            raise HTTPException(
                status_code=404,
                detail="The selected Google Drive source is unavailable.",
            )
        source = sources[0]
        metadata = source.source_metadata or {}
        connection_id = str(metadata["oauth_connection_id"])
        account_id = str(metadata.get("provider_account_id") or connection_id)
        token = await get_connection_access_token(connection_id)
        return TenantSourceAccess(
            external_source_id=source.id,
            provider_account_id=account_id,
            access_token=token,
        )
