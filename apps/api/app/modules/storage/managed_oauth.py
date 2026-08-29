from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

import httpx
from sqlalchemy import select

from app.core.config import Settings
from app.modules.auth_persistence.model import OAuthConnectionModel
from app.modules.auth_persistence.repository import PersistentOAuthConnection
from app.modules.auth_persistence.service import auth_repository
from app.providers.google.auth import (
    DRIVE_WRITE_SCOPE,
    resolve_granted_scopes,
    validate_granted_scopes,
)


logger = logging.getLogger(__name__)
MANAGED_STORAGE_OAUTH_PROVIDER = "google_managed_storage"
GOOGLE_DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True, slots=True)
class ManagedStorageCredential:
    access_token: str | None
    refresh_token: str | None


def _matching_rows(repository, root_folder_id: str):
    rows = repository.session.scalars(
        select(OAuthConnectionModel)
        .where(
            OAuthConnectionModel.provider == MANAGED_STORAGE_OAUTH_PROVIDER,
            OAuthConnectionModel.status.in_(("active", "refresh_error")),
        )
        .order_by(OAuthConnectionModel.updated_at.desc())
    )
    return tuple(
        row for row in rows
        if str((row.provider_metadata_json or {}).get("root_folder_id") or "").strip()
        == root_folder_id
    )


def load_managed_storage_connection(
    settings: Settings,
) -> PersistentOAuthConnection | None:
    root_folder_id = str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    if not root_folder_id or not settings.PERSISTENT_AUTH_ENABLED:
        return None
    try:
        with auth_repository() as repository:
            for row in _matching_rows(repository, root_folder_id):
                connection = repository.load_connection(
                    provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                    connection_id=row.id,
                )
                if connection is not None and connection.refresh_token:
                    return connection
    except Exception as exc:
        logger.warning(
            "managed_storage_oauth_load_failed error_type=%s",
            type(exc).__name__,
        )
    return None


def resolve_managed_storage_credential(settings: Settings) -> ManagedStorageCredential:
    connection = load_managed_storage_connection(settings)
    if connection is not None:
        return ManagedStorageCredential(
            access_token=connection.access_token,
            refresh_token=connection.refresh_token,
        )
    return ManagedStorageCredential(
        access_token=settings.GOOGLE_MANAGED_STORAGE_ACCESS_TOKEN,
        refresh_token=settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN,
    )


def _masked_email(value: str | None) -> str | None:
    email = str(value or "").strip()
    if not email or "@" not in email:
        return None
    local, domain = email.rsplit("@", 1)
    return f"{local[:1]}***@{domain}"


def managed_storage_oauth_status(settings: Settings) -> dict[str, Any]:
    root_folder_id = str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    result: dict[str, Any] = {
        "root_folder_configured": bool(root_folder_id),
        "connected": False,
        "source": "none",
        "account_email": None,
        "updated_at": None,
        "reconnect_required": False,
    }
    if root_folder_id and settings.PERSISTENT_AUTH_ENABLED:
        try:
            with auth_repository() as repository:
                rows = _matching_rows(repository, root_folder_id)
                if rows:
                    row = rows[0]
                    connection = repository.load_connection(
                        provider=MANAGED_STORAGE_OAUTH_PROVIDER,
                        connection_id=row.id,
                    )
                    result.update({
                        "connected": bool(connection and connection.refresh_token),
                        "source": "database",
                        "account_email": _masked_email(row.account_email),
                        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                        "reconnect_required": row.status != "active" or connection is None,
                    })
                    return result
        except Exception as exc:
            logger.warning(
                "managed_storage_oauth_status_failed error_type=%s",
                type(exc).__name__,
            )
            result["reconnect_required"] = True
            return result
    if settings.GOOGLE_MANAGED_STORAGE_REFRESH_TOKEN:
        result.update({
            "source": "environment",
            "reconnect_required": True,
        })
    return result


async def persist_managed_storage_connection(
    credentials,
    *,
    tenant_id: str,
    initiating_user_id: str,
    root_folder_id: str,
    granted_scopes: Iterable[str] | None = None,
) -> dict[str, Any]:
    scopes = tuple(granted_scopes or resolve_granted_scopes(credentials))
    validate_granted_scopes(credentials, require_write=True, scopes=scopes)
    refresh_token = str(credentials.refresh_token or "").strip()
    if not refresh_token:
        raise PermissionError(
            "Google did not return a refresh token. Revoke the previous grant and reconnect."
        )
    async with httpx.AsyncClient(timeout=20) as client:
        headers = {"Authorization": f"Bearer {credentials.token}"}
        profile_response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers=headers,
        )
        profile_response.raise_for_status()
        profile = profile_response.json()
        folder_response = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{root_folder_id}",
            params={
                "fields": "id,mimeType,trashed,capabilities(canAddChildren,canDeleteChildren)",
                "supportsAllDrives": "true",
            },
            headers=headers,
        )
        folder_response.raise_for_status()
        folder = folder_response.json()
    capabilities = folder.get("capabilities") or {}
    if (
        folder.get("mimeType") != GOOGLE_DRIVE_FOLDER_MIME_TYPE
        or bool(folder.get("trashed"))
        or not bool(capabilities.get("canAddChildren"))
        or not bool(capabilities.get("canDeleteChildren"))
    ):
        raise PermissionError(
            "The selected Google account cannot manage files in the active storage folder."
        )
    account_id = str(profile.get("sub") or "").strip()
    if not account_id:
        raise ValueError("Google profile has no account identity")
    expiry = credentials.expiry or datetime.now(timezone.utc) + timedelta(seconds=3500)
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=timezone.utc)
    with auth_repository() as repository:
        connection = repository.upsert_connection(
            tenant_id=tenant_id,
            provider=MANAGED_STORAGE_OAUTH_PROVIDER,
            provider_account_id=account_id,
            account_email=profile.get("email"),
            access_token=credentials.token,
            refresh_token=refresh_token,
            expires_at=expiry,
            scopes=list(scopes or (DRIVE_WRITE_SCOPE,)),
            token_type="Bearer",
            provider_metadata={
                "connection_purpose": "managed_storage",
                "root_folder_id": root_folder_id,
                "connected_by_user_id": initiating_user_id,
            },
        )
        for row in _matching_rows(repository, root_folder_id):
            if row.id != connection.id:
                row.status = "revoked"
                row.revoked_at = datetime.now(timezone.utc)
        repository.session.flush()
    return {
        "connected": True,
        "account_email": _masked_email(profile.get("email")),
    }
