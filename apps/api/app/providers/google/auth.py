import os
import secrets
import time
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from starlette.concurrency import run_in_threadpool

from app.core.config import get_settings
from app.modules.auth_persistence.repository import PersistentCloudSession
from app.modules.auth_persistence.login import ApplicationLoginService
from app.modules.auth_persistence.service import AUTH_METRICS, auth_repository

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")
DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
# Read/write source management is required for Explorer uploads, moves, and deletes.
DRIVE_WRITE_SCOPE = "https://www.googleapis.com/auth/drive"
IDENTITY_SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
]
DRIVE_SCOPES = [*IDENTITY_SCOPES, DRIVE_WRITE_SCOPE]
SCOPES = DRIVE_SCOPES
SESSION_COOKIE = "cam_google_session"
OAUTH_BINDING_COOKIE = "cam_oauth_binding"

GoogleSession = PersistentCloudSession
_REFRESH_WAIT_SECONDS = 3.0
_REFRESH_POLL_SECONDS = 0.1

def _settings():
    client_id=os.getenv("GOOGLE_CLIENT_ID"); client_secret=os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri=os.getenv("GOOGLE_REDIRECT_URI","http://localhost:8000/api/auth/google/callback")
    if not client_id or not client_secret:
        raise HTTPException(503,"Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.")
    return client_id,client_secret,redirect_uri

def oauth_flow(state=None, *, require_drive_scope: bool = False):
    client_id,client_secret,redirect_uri=_settings()
    scopes = DRIVE_SCOPES if require_drive_scope else IDENTITY_SCOPES
    flow=Flow.from_client_config({"web":{"client_id":client_id,"client_secret":client_secret,"auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token","redirect_uris":[redirect_uri]}},scopes=scopes,state=state)
    flow.redirect_uri=redirect_uri
    return flow

def remember_state(state,code_verifier,session_binding=None,redirect_intent="/"):
    settings=get_settings()
    with auth_repository() as repository:
        repository.remember_state(provider="google",state=state,code_verifier=code_verifier,ttl_seconds=settings.AUTH_STATE_TTL_SECONDS,redirect_intent=redirect_intent,session_binding=session_binding)

def consume_state(state,session_binding=None):
    try:
        with auth_repository() as repository:
            verifier,_=repository.consume_state(provider="google",state=state,session_binding=session_binding)
            return verifier
    except LookupError as exc:
        raise HTTPException(400,"Invalid or expired OAuth state.") from exc

def consume_state_details(state, session_binding=None) -> tuple[str | None, str]:
    try:
        with auth_repository() as repository:
            return repository.consume_state(provider="google", state=state, session_binding=session_binding)
    except LookupError as exc:
        raise HTTPException(400,"Invalid or expired OAuth state.") from exc

def validate_granted_scopes(credentials):
    granted=set(credentials.granted_scopes or credentials.scopes or [])
    if DRIVE_WRITE_SCOPE not in granted:
        raise PermissionError("Google Drive read/write permission was not granted. Reconnect Google Drive and approve access.")

async def create_session(credentials, *, require_drive_scope: bool = True, connection_tenant_id: str | None = None):
    if require_drive_scope:
        validate_granted_scopes(credentials)
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.get("https://openidconnect.googleapis.com/v1/userinfo",headers={"Authorization":f"Bearer {credentials.token}"})
        response.raise_for_status(); profile=response.json()
    account_id=str(profile.get("sub") or "")
    if not account_id: raise ValueError("Google profile has no account identity")
    user={"id":account_id,"name":profile.get("name"),"email":profile.get("email"),"picture":profile.get("picture")}
    expiry=credentials.expiry
    if expiry is None: expiry=datetime.now(timezone.utc)+timedelta(seconds=3500)
    elif expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
    settings=get_settings()
    with auth_repository() as repository:
        login = ApplicationLoginService(repository.session, settings).resolve(
            provider="google",
            provider_subject=account_id,
            provider_email=profile.get("email"),
            display_name=profile.get("name"),
            avatar_url=profile.get("picture"),
            provider_metadata={
                "email_verified": profile.get("email_verified"),
                "hosted_domain": profile.get("hd"),
                "locale": profile.get("locale"),
            },
        )
        connection_tenant_id = connection_tenant_id or account_id
        connection=repository.upsert_connection(
            tenant_id=connection_tenant_id,provider="google",provider_account_id=account_id,
            account_email=profile.get("email"),access_token=credentials.token,
            refresh_token=credentials.refresh_token,expires_at=expiry,
            scopes=list(credentials.granted_scopes or credentials.scopes or (DRIVE_SCOPES if require_drive_scope else IDENTITY_SCOPES)),
            token_type="Bearer",provider_metadata={"picture":profile.get("picture"), "connection_purpose": "drive_source" if require_drive_scope else "application_login"},
        )
        session_id,_=repository.create_session(
            connection=connection, user=user,
            ttl_seconds=settings.AUTH_SESSION_TTL_SECONDS,
            user_id=login.user.id,
            active_tenant_id=login.active_tenant_id,
        )
    AUTH_METRICS.increment("connection_created","google")
    return session_id,get_session_by_id(session_id)


async def get_connection_access_token(
    connection_id: str,
    *,
    require_drive_write_scope: bool = False,
) -> str:
    settings = get_settings()
    with auth_repository() as repository:
        connection = repository.load_connection(
            provider="google", connection_id=connection_id
        )
    if connection is None:
        raise HTTPException(401, "Google connection is unavailable.")
    if require_drive_write_scope and DRIVE_WRITE_SCOPE not in set(connection.scopes):
        raise HTTPException(
            403,
            "Google Drive write access is required. Reconnect the Drive source and approve read/write access.",
        )
    if connection.expires_at > time.time() + 60:
        return connection.access_token
    if not connection.refresh_token:
        raise HTTPException(401, "Google connection requires reconnection.")

    owner = "google-sync-refresh-" + secrets.token_urlsafe(16)
    with auth_repository() as repository:
        claimed = repository.claim_refresh(
            tenant_id=connection.tenant_id,
            connection_id=connection.connection_id,
            owner=owner,
            lease_seconds=settings.AUTH_REFRESH_LEASE_SECONDS,
        )
    if not claimed:
        # The Drive source is shared by the tenant. Several Explorer media
        # requests can discover an expired token at once; let the requests
        # which lost the lease reuse the winner's refreshed token instead of
        # treating that normal race as a Viewer-specific connection failure.
        deadline = time.monotonic() + _REFRESH_WAIT_SECONDS
        while time.monotonic() < deadline:
            await asyncio.sleep(_REFRESH_POLL_SECONDS)
            with auth_repository() as repository:
                refreshed = repository.load_connection(
                    provider="google", connection_id=connection_id
                )
            if refreshed and refreshed.expires_at > time.time() + 60:
                return refreshed.access_token
        raise HTTPException(503, "Google Drive source is being refreshed. Please retry shortly.")

    client_id, client_secret, _ = _settings()
    credentials = Credentials(
        token=connection.access_token,
        refresh_token=connection.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=list(connection.scopes) or SCOPES,
    )
    try:
        await run_in_threadpool(credentials.refresh, GoogleAuthRequest())
        expiry = credentials.expiry or datetime.now(timezone.utc)
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        with auth_repository() as repository:
            repository.finish_refresh(
                tenant_id=connection.tenant_id,
                connection_id=connection.connection_id,
                owner=owner,
                access_token=credentials.token,
                refresh_token=credentials.refresh_token,
                expires_at=expiry,
                scopes=list(credentials.granted_scopes or credentials.scopes or connection.scopes or SCOPES),
                token_type="Bearer",
            )
        AUTH_METRICS.increment("connection_refreshed", "google")
        return credentials.token
    except Exception as exc:
        permanent = isinstance(exc, RefreshError) and "invalid_grant" in str(exc).lower()
        with auth_repository() as repository:
            repository.fail_refresh(
                tenant_id=connection.tenant_id,
                connection_id=connection.connection_id,
                owner=owner,
                code="invalid_grant" if permanent else "refresh_failed",
                retryable=not permanent,
            )
        raise HTTPException(
            401 if permanent else 503,
            "The workspace Google Drive source requires reconnection by an administrator."
            if permanent else "Google connection could not be refreshed.",
        ) from exc

def get_session_by_id(session_id):
    settings = get_settings()
    if not settings.PERSISTENT_AUTH_ENABLED: return None
    with auth_repository() as repository:
        return repository.load_session(
            provider="google", session_id=session_id,
            allow_legacy_actor_session=settings.legacy_actor_session_compatibility_enabled,
        )

def get_session(request):
    session_id=request.cookies.get(SESSION_COOKIE)
    return get_session_by_id(session_id) if session_id else None

async def get_access_token(request):
    session_id=request.cookies.get(SESSION_COOKIE)
    cloud=get_session_by_id(session_id) if session_id else None
    if not cloud:
        settings=get_settings()
        return os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") or None if not settings.PERSISTENT_AUTH_ENABLED and settings.APP_ENV.lower() in {"development","dev","local"} else None
    if cloud.expires_at>time.time()+60: return cloud.access_token
    if not cloud.refresh_token: raise HTTPException(401,"Google session expired. Sign in again.")
    owner="google-refresh-"+secrets.token_urlsafe(16); settings=get_settings()
    with auth_repository() as repository:
        claimed=repository.claim_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,lease_seconds=settings.AUTH_REFRESH_LEASE_SECONDS)
    if not claimed:
        AUTH_METRICS.increment("refresh_lock_contention","google")
        for _ in range(20):
            await asyncio.sleep(.1)
            latest=get_session_by_id(session_id)
            if latest and latest.expires_at>time.time()+60: return latest.access_token
        raise HTTPException(503,"Google token refresh is already in progress.")
    client_id,client_secret,_=_settings()
    credentials=Credentials(token=cloud.access_token,refresh_token=cloud.refresh_token,token_uri="https://oauth2.googleapis.com/token",client_id=client_id,client_secret=client_secret,scopes=SCOPES)
    try:
        await run_in_threadpool(credentials.refresh,GoogleAuthRequest())
        expiry=credentials.expiry or datetime.now(timezone.utc)
        if expiry.tzinfo is None: expiry=expiry.replace(tzinfo=timezone.utc)
        with auth_repository() as repository:
            repository.finish_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,access_token=credentials.token,refresh_token=credentials.refresh_token,expires_at=expiry,scopes=list(credentials.granted_scopes or credentials.scopes or SCOPES),token_type="Bearer")
        AUTH_METRICS.increment("connection_refreshed","google")
        return credentials.token
    except Exception as exc:
        permanent=isinstance(exc,RefreshError) and "invalid_grant" in str(exc).lower()
        with auth_repository() as repository:
            repository.fail_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,code="invalid_grant" if permanent else "refresh_failed",retryable=not permanent)
        if permanent: AUTH_METRICS.increment("reconnect_required","google")
        raise HTTPException(401 if permanent else 503,"Google session requires reconnection." if permanent else "Google session could not be refreshed.") from exc

def remove_session(request):
    session_id=request.cookies.get(SESSION_COOKIE)
    if session_id and get_settings().PERSISTENT_AUTH_ENABLED:
        with auth_repository() as repository: repository.revoke_session(provider="google",session_id=session_id)
        AUTH_METRICS.increment("session_revoked","google")
