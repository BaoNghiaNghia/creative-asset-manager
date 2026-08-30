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
GOOGLE_TOKEN_URI = "https://oauth2.googleapis.com/token"
GOOGLE_DRIVE_FOLDER_MIME_TYPE = "application/vnd.google-apps.folder"


@dataclass(frozen=True, slots=True)
class ManagedStorageCredential:
    access_token: str | None
    refresh_token: str | None


@dataclass(frozen=True, slots=True)
class ManagedStorageCredentialCheck:
    account_email: str | None
    saved: bool


class ManagedStorageCredentialValidationError(ValueError):
    """A supplied refresh token cannot manage the active storage folder."""


class ManagedStorageCredentialUnavailableError(RuntimeError):
    """Google could not be reached to validate a supplied refresh token."""


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
    return ManagedStorageCredential(access_token=None, refresh_token=None)


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
        "reconnect_required": bool(root_folder_id),
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
    return result


def _persist_validated_connection(
    *,
    tenant_id: str,
    initiating_user_id: str,
    root_folder_id: str,
    account_id: str,
    account_email: str | None,
    access_token: str,
    refresh_token: str,
    expires_at: datetime,
    scopes: Iterable[str],
) -> None:
    with auth_repository() as repository:
        connection = repository.upsert_connection(
            tenant_id=tenant_id,
            provider=MANAGED_STORAGE_OAUTH_PROVIDER,
            provider_account_id=account_id,
            account_email=account_email,
            access_token=access_token,
            refresh_token=refresh_token,
            expires_at=expires_at,
            scopes=list(scopes),
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


async def save_managed_storage_refresh_token_unverified(
    settings: Settings,
    refresh_token: str,
    *,
    tenant_id: str,
    initiating_user_id: str,
) -> ManagedStorageCredentialCheck:
    """Persist a manually supplied token without making a Google API request.

    The worker will refresh this token on its first storage operation. Use the
    explicit test endpoint to validate Drive-folder access before enabling
    cleanup or relying on managed storage.
    """
    token = str(refresh_token or "").strip()
    root_folder_id = str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    if not settings.PERSISTENT_AUTH_ENABLED:
        raise ManagedStorageCredentialValidationError(
            "Persistent authentication is not enabled."
        )
    if not root_folder_id:
        raise ManagedStorageCredentialValidationError(
            "Managed Storage root folder is not configured."
        )
    if len(token) < 16 or len(token) > 4096:
        raise ManagedStorageCredentialValidationError(
            "Refresh token format is invalid."
        )
    _persist_validated_connection(
        tenant_id=tenant_id,
        initiating_user_id=initiating_user_id,
        root_folder_id=root_folder_id,
        account_id="managed-storage-manual",
        account_email=None,
        access_token="pending-refresh",
        refresh_token=token,
        expires_at=datetime.now(timezone.utc),
        scopes=(DRIVE_WRITE_SCOPE,),
    )
    return ManagedStorageCredentialCheck(account_email=None, saved=True)


async def check_managed_storage_refresh_token(
    settings: Settings,
    refresh_token: str,
    *,
    tenant_id: str,
    initiating_user_id: str,
    save: bool,
) -> ManagedStorageCredentialCheck:
    """Validate a manually supplied token and optionally persist it encrypted."""
    token = str(refresh_token or "").strip()
    root_folder_id = str(settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or "").strip()
    client_id = str(settings.GOOGLE_CLIENT_ID or "").strip()
    client_secret = str(settings.GOOGLE_CLIENT_SECRET or "").strip()
    if not settings.PERSISTENT_AUTH_ENABLED:
        raise ManagedStorageCredentialValidationError(
            "Persistent authentication is not enabled."
        )
    if not root_folder_id or not client_id or not client_secret:
        raise ManagedStorageCredentialValidationError(
            "Managed Storage OAuth configuration is incomplete."
        )
    if len(token) < 16 or len(token) > 4096:
        raise ManagedStorageCredentialValidationError(
            "Refresh token format is invalid."
        )
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=8)) as client:
            token_response = await client.post(
                GOOGLE_TOKEN_URI,
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "refresh_token": token,
                    "grant_type": "refresh_token",
                },
            )
            if token_response.status_code in {408, 425, 429, 500, 502, 503, 504}:
                raise ManagedStorageCredentialUnavailableError(
                    "Google token validation is temporarily unavailable."
                )
            if token_response.status_code >= 400:
                raise ManagedStorageCredentialValidationError(
                    "Google rejected the refresh token."
                )
            token_payload = token_response.json()
            access_token = str(token_payload.get("access_token") or "").strip()
            if not access_token:
                raise ManagedStorageCredentialUnavailableError(
                    "Google returned no access token."
                )
            granted_scopes = tuple(
                scope for scope in str(token_payload.get("scope") or "").split()
                if scope
            )
            if granted_scopes and DRIVE_WRITE_SCOPE not in granted_scopes:
                raise ManagedStorageCredentialValidationError(
                    "The refresh token does not include Google Drive write access."
                )
            headers = {"Authorization": f"Bearer {access_token}"}
            about_response = await client.get(
                "https://www.googleapis.com/drive/v3/about",
                params={"fields": "user(displayName,emailAddress,permissionId)"},
                headers=headers,
            )
            folder_response = await client.get(
                f"https://www.googleapis.com/drive/v3/files/{root_folder_id}",
                params={
                    "fields": "id,mimeType,trashed,capabilities(canAddChildren,canDeleteChildren)",
                    "supportsAllDrives": "true",
                },
                headers=headers,
            )
    except (httpx.TimeoutException, httpx.NetworkError) as exc:
        raise ManagedStorageCredentialUnavailableError(
            "Google token validation is temporarily unavailable."
        ) from exc
    for response in (about_response, folder_response):
        if response.status_code in {408, 425, 429, 500, 502, 503, 504}:
            raise ManagedStorageCredentialUnavailableError(
                "Google Drive validation is temporarily unavailable."
            )
        if response.status_code >= 400:
            raise ManagedStorageCredentialValidationError(
                "The refresh token cannot access the active storage folder."
            )
    user = about_response.json().get("user") or {}
    account_id = str(user.get("permissionId") or "").strip()
    account_email = str(user.get("emailAddress") or "").strip() or None
    folder = folder_response.json()
    capabilities = folder.get("capabilities") or {}
    if (
        not account_id
        or folder.get("mimeType") != GOOGLE_DRIVE_FOLDER_MIME_TYPE
        or bool(folder.get("trashed"))
        or not bool(capabilities.get("canAddChildren"))
        or not bool(capabilities.get("canDeleteChildren"))
    ):
        raise ManagedStorageCredentialValidationError(
            "The selected Google account cannot manage files in the active storage folder."
        )
    try:
        lifetime = max(0, int(token_payload.get("expires_in", 3600)))
    except (TypeError, ValueError):
        lifetime = 3600
    scopes = granted_scopes or (DRIVE_WRITE_SCOPE,)
    if save:
        _persist_validated_connection(
            tenant_id=tenant_id,
            initiating_user_id=initiating_user_id,
            root_folder_id=root_folder_id,
            account_id=account_id,
            account_email=account_email,
            access_token=access_token,
            refresh_token=token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=lifetime),
            scopes=scopes,
        )
    return ManagedStorageCredentialCheck(
        account_email=_masked_email(account_email),
        saved=save,
    )


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
