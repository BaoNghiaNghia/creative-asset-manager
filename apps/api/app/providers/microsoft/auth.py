import asyncio
import base64
import hashlib
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request

from app.core.config import get_settings
from app.modules.auth_persistence.repository import PersistentCloudSession
from app.modules.auth_persistence.login import ApplicationLoginService
from app.modules.auth_persistence.service import AUTH_METRICS, auth_repository

SESSION_COOKIE="cam_microsoft_session"
OAUTH_BINDING_COOKIE="cam_oauth_binding"
APPLICATION_LOGIN_SCOPES = ("openid", "profile", "email", "offline_access", "User.Read")
ONEDRIVE_SOURCE_SCOPES = ("openid", "profile", "email", "offline_access", "User.Read", "Files.Read")
SHAREPOINT_SOURCE_SCOPES = ("openid", "profile", "email", "offline_access", "User.Read", "Sites.Read.All", "Files.Read.All")
SCOPES = list(SHAREPOINT_SOURCE_SCOPES)  # legacy compatibility only
OIDC_RESPONSE_SCOPES = frozenset(("openid", "profile", "email", "offline_access"))


def scopes_for_intent(intent: str) -> tuple[str, ...]:
    # Source-connect intents retain tenant, source and actor bindings in the
    # OAuth state (for example, onedrive_connect:<tenant>:...). The
    # provider-specific scope decision, however, depends only on the prefix.
    intent = intent.split(":", 1)[0]
    if intent == "application_login":
        return APPLICATION_LOGIN_SCOPES
    if intent == "onedrive_connect":
        return ONEDRIVE_SOURCE_SCOPES
    if intent == "sharepoint_connect":
        return SHAREPOINT_SOURCE_SCOPES
    raise ValueError("unsupported Microsoft OAuth intent")


def required_resource_scopes_for_intent(intent: str) -> frozenset[str]:
    """Return only Microsoft Graph scopes expected in a token response.

    OpenID Connect and offline_access scopes affect authorization, but Microsoft
    does not consistently echo them in the access-token scope claim.
    """
    return frozenset(scopes_for_intent(intent)) - OIDC_RESPONSE_SCOPES


def authority_for_intent(intent: str) -> str:
    # See scopes_for_intent: state bindings are appended to source intents
    # and must not make an otherwise valid provider intent unrecognised.
    intent = intent.split(":", 1)[0]
    configured = os.getenv("MICROSOFT_TENANT_ID", "organizations").strip() or "organizations"
    if intent == "onedrive_connect":
        return os.getenv("MICROSOFT_ONEDRIVE_AUTHORITY", "common").strip() or "common"
    if intent == "application_login":
        return os.getenv("MICROSOFT_APPLICATION_AUTHORITY", configured).strip() or configured
    if intent == "sharepoint_connect":
        return os.getenv("MICROSOFT_SHAREPOINT_AUTHORITY", configured).strip() or configured
    raise ValueError("unsupported Microsoft OAuth intent")
MicrosoftSession=PersistentCloudSession

def _settings():
    client_id=os.getenv("MICROSOFT_CLIENT_ID"); client_secret=os.getenv("MICROSOFT_CLIENT_SECRET")
    tenant_id=os.getenv("MICROSOFT_TENANT_ID","organizations")
    redirect_uri=os.getenv("MICROSOFT_REDIRECT_URI","http://localhost:8000/api/auth/microsoft/callback")
    if not client_id or not client_secret:
        raise HTTPException(503,"Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET.")
    return client_id,client_secret,tenant_id,redirect_uri

def authorization_url(session_binding=None, redirect_intent="application_login"):
    client_id, _, _, redirect_uri = _settings()
    state = secrets.token_urlsafe(32); verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    with auth_repository() as repository:
        repository.remember_state(provider="microsoft", state=state, code_verifier=verifier,
            ttl_seconds=get_settings().AUTH_STATE_TTL_SECONDS, redirect_intent=redirect_intent,
            session_binding=session_binding)
    params = {"client_id": client_id, "response_type": "code", "redirect_uri": redirect_uri,
        "response_mode": "query", "scope": " ".join(scopes_for_intent(redirect_intent)),
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256",
        "prompt": "select_account"}
    return f"https://login.microsoftonline.com/{authority_for_intent(redirect_intent)}/oauth2/v2.0/authorize?"+urlencode(params)


def consume_state_details(state, session_binding=None):
    try:
        with auth_repository() as repository:
            verifier, intent = repository.consume_state(provider="microsoft", state=state,
                session_binding=session_binding)
            if not verifier: raise LookupError("missing_verifier")
            return verifier, intent
    except LookupError as exc:
        raise HTTPException(400, "Invalid or expired Microsoft OAuth state.") from exc


def consume_state(state, session_binding=None):
    return consume_state_details(state, session_binding)[0]


async def exchange_code(code, code_verifier, *, intent="application_login"):
    client_id, client_secret, _, redirect_uri = _settings()
    authority = authority_for_intent(intent)
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token",
            data={"client_id": client_id, "client_secret": client_secret,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": redirect_uri, "scope": " ".join(scopes_for_intent(intent)),
                "code_verifier": code_verifier},
        )
        response.raise_for_status()
        return response.json()

async def create_session(token):
    granted=set(str(token.get("scope") or "").split())
    if not set(APPLICATION_LOGIN_SCOPES).issubset(granted):
        raise PermissionError("Microsoft identity scopes were not granted.")
    async with httpx.AsyncClient(timeout=15) as client:
        response=await client.get("https://graph.microsoft.com/v1.0/me",params={"$select":"id,displayName,mail,userPrincipalName"},headers={"Authorization":f"Bearer {token['access_token']}"})
        response.raise_for_status(); profile=response.json()
    account_id=str(profile.get("id") or "")
    if not account_id: raise ValueError("Microsoft profile has no account identity")
    user={"id":account_id,"name":profile.get("displayName"),"email":profile.get("mail") or profile.get("userPrincipalName"),"picture":None}
    settings=get_settings()
    with auth_repository() as repository:
        login = ApplicationLoginService(repository.session, settings).resolve(
            provider="microsoft",
            provider_subject=account_id,
            provider_email=user["email"],
            display_name=profile.get("displayName"),
            provider_metadata={
                "user_principal_name": profile.get("userPrincipalName"),
            },
        )
        connection=repository.upsert_connection(tenant_id=account_id,provider="microsoft",provider_account_id=account_id, connection_purpose="application_login",account_email=user["email"],access_token=token["access_token"],refresh_token=token.get("refresh_token"),expires_at=datetime.now(timezone.utc)+timedelta(seconds=int(token.get("expires_in") or 3500)),scopes=list(granted),token_type=token.get("token_type") or "Bearer",provider_metadata={})
        session_id,_=repository.create_session(
            connection=connection, user=user,
            ttl_seconds=settings.AUTH_SESSION_TTL_SECONDS,
            user_id=login.user.id,
            active_tenant_id=login.active_tenant_id,
        )
    AUTH_METRICS.increment("connection_created","microsoft")
    return session_id,get_session_by_id(session_id)

def get_session_by_id(session_id):
    settings = get_settings()
    if not settings.PERSISTENT_AUTH_ENABLED: return None
    with auth_repository() as repository:
        return repository.load_session(provider="microsoft", session_id=session_id, allow_legacy_actor_session=settings.legacy_actor_session_compatibility_enabled)

def get_session(request):
    session_id=request.cookies.get(SESSION_COOKIE)
    return get_session_by_id(session_id) if session_id else None

async def get_access_token(request):
    session_id=request.cookies.get(SESSION_COOKIE); cloud=get_session_by_id(session_id) if session_id else None
    if not cloud:
        settings=get_settings()
        return os.getenv("SHAREPOINT_ACCESS_TOKEN") or None if not settings.PERSISTENT_AUTH_ENABLED and settings.APP_ENV.lower() in {"development","dev","local"} else None
    if cloud.expires_at>time.time()+60: return cloud.access_token
    if not cloud.refresh_token: raise HTTPException(401,"Microsoft session expired. Sign in again.")
    owner="microsoft-refresh-"+secrets.token_urlsafe(16); settings=get_settings()
    with auth_repository() as repository:
        claimed=repository.claim_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,lease_seconds=settings.AUTH_REFRESH_LEASE_SECONDS)
    if not claimed:
        AUTH_METRICS.increment("refresh_lock_contention","microsoft")
        for _ in range(20):
            await asyncio.sleep(.1); latest=get_session_by_id(session_id)
            if latest and latest.expires_at>time.time()+60: return latest.access_token
        raise HTTPException(503,"Microsoft token refresh is already in progress.")
    client_id,client_secret,tenant_id,_=_settings()
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response=await client.post(f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",data={"client_id":client_id,"client_secret":client_secret,"grant_type":"refresh_token","refresh_token":cloud.refresh_token,"scope":" ".join(SCOPES)})
            if not response.is_success:
                payload=response.json() if response.headers.get("content-type","").startswith("application/json") else {}
                code=str(payload.get("error") or "refresh_failed")
                permanent=code in {"invalid_grant","interaction_required","invalid_client"}
                with auth_repository() as repository: repository.fail_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,code=code,retryable=not permanent)
                if permanent: AUTH_METRICS.increment("reconnect_required","microsoft")
                raise HTTPException(401 if permanent else 503,"Microsoft session requires reconnection." if permanent else "Microsoft session could not be refreshed.")
            token=response.json()
        with auth_repository() as repository:
            repository.finish_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,access_token=token["access_token"],refresh_token=token.get("refresh_token"),expires_at=datetime.now(timezone.utc)+timedelta(seconds=int(token.get("expires_in") or 3500)),scopes=str(token.get("scope") or "").split() or None,token_type=token.get("token_type"))
        AUTH_METRICS.increment("connection_refreshed","microsoft")
        return token["access_token"]
    except HTTPException:
        raise
    except Exception as exc:
        with auth_repository() as repository: repository.fail_refresh(tenant_id=cloud.tenant_id,connection_id=cloud.connection_id,owner=owner,code="refresh_failed",retryable=True)
        raise HTTPException(503,"Microsoft session could not be refreshed.") from exc

def remove_session(request):
    session_id=request.cookies.get(SESSION_COOKIE)
    if session_id and get_settings().PERSISTENT_AUTH_ENABLED:
        with auth_repository() as repository: repository.revoke_session(provider="microsoft",session_id=session_id)
        AUTH_METRICS.increment("session_revoked","microsoft")


async def get_connection_access_token(connection_id: str, *, purpose: str) -> str:
    """Return a token for a tenant-bound Microsoft source credential.

    This intentionally accepts a connection ID from the authoritative source
    resolver, never from a browser session. Refresh is scoped to this exact
    connection, therefore OneDrive and SharePoint credentials for the same
    Microsoft account cannot overwrite each other.
    """
    settings = get_settings()
    with auth_repository() as repository:
        from sqlalchemy import select
        from app.modules.auth_persistence.model import OAuthConnectionModel
        row = repository.session.scalar(
            select(OAuthConnectionModel).where(
                OAuthConnectionModel.id == connection_id,
                OAuthConnectionModel.provider == "microsoft",
                OAuthConnectionModel.connection_purpose == purpose,
                OAuthConnectionModel.status.in_(("active", "refresh_error")),
            )
        )
        cloud = repository.load_connection(provider="microsoft", connection_id=connection_id)
    if row is None or cloud is None:
        raise HTTPException(401, "Microsoft source connection is unavailable.")
    if cloud.expires_at > time.time() + 60:
        return cloud.access_token
    if not cloud.refresh_token:
        raise HTTPException(401, "Microsoft source connection requires reconnection.")

    owner = "microsoft-source-refresh-" + secrets.token_urlsafe(16)
    with auth_repository() as repository:
        claimed = repository.claim_refresh(
            tenant_id=cloud.tenant_id,
            connection_id=connection_id,
            owner=owner,
            lease_seconds=settings.AUTH_REFRESH_LEASE_SECONDS,
        )
    if not claimed:
        AUTH_METRICS.increment("refresh_lock_contention", "microsoft")
        for _ in range(20):
            await asyncio.sleep(.1)
            with auth_repository() as repository:
                latest = repository.load_connection(
                    provider="microsoft", connection_id=connection_id
                )
            if latest and latest.expires_at > time.time() + 60:
                return latest.access_token
        raise HTTPException(503, "Microsoft source token refresh is in progress.")

    client_id, client_secret, authority, _ = _settings()
    scopes = list(cloud.scopes) or list(SCOPES)
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            response = await client.post(
                f"https://login.microsoftonline.com/{authority}/oauth2/v2.0/token",
                data={
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "grant_type": "refresh_token",
                    "refresh_token": cloud.refresh_token,
                    "scope": " ".join(scopes),
                },
            )
        if not response.is_success:
            payload = response.json() if response.headers.get(
                "content-type", ""
            ).startswith("application/json") else {}
            code = str(payload.get("error") or "refresh_failed")
            permanent = code in {"invalid_grant", "interaction_required", "invalid_client"}
            with auth_repository() as repository:
                repository.fail_refresh(
                    tenant_id=cloud.tenant_id, connection_id=connection_id,
                    owner=owner, code=code, retryable=not permanent,
                )
            if permanent:
                AUTH_METRICS.increment("reconnect_required", "microsoft")
            raise HTTPException(
                401 if permanent else 503,
                "Microsoft source requires reconnection." if permanent
                else "Microsoft source could not be refreshed.",
            )
        token = response.json()
        with auth_repository() as repository:
            repository.finish_refresh(
                tenant_id=cloud.tenant_id, connection_id=connection_id, owner=owner,
                access_token=token["access_token"],
                refresh_token=token.get("refresh_token"),
                expires_at=datetime.now(timezone.utc) + timedelta(
                    seconds=int(token.get("expires_in") or 3500)
                ),
                scopes=str(token.get("scope") or "").split() or None,
                token_type=token.get("token_type"),
            )
        AUTH_METRICS.increment("connection_refreshed", "microsoft")
        return token["access_token"]
    except HTTPException:
        raise
    except Exception as exc:
        with auth_repository() as repository:
            repository.fail_refresh(
                tenant_id=cloud.tenant_id, connection_id=connection_id,
                owner=owner, code="refresh_failed", retryable=True,
            )
        raise HTTPException(503, "Microsoft source could not be refreshed.") from exc


async def persist_source_connection(
    token: dict,
    *,
    tenant_id: str,
    initiating_user_id: str,
    intent: str,
):
    """Persist a source credential without resolving or rotating CAM identity."""
    if intent not in {"onedrive_connect", "sharepoint_connect"}:
        raise ValueError("unsupported Microsoft source-connect intent")
    granted = set(str(token.get("scope") or "").split())
    required = required_resource_scopes_for_intent(intent)
    if not required.issubset(granted):
        raise PermissionError("Microsoft source scopes were not granted.")
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        response.raise_for_status()
        profile = response.json()
    account_id = str(profile.get("id") or "")
    if not account_id:
        raise ValueError("Microsoft profile has no account identity")
    purpose = "onedrive_source" if intent == "onedrive_connect" else "sharepoint_source"
    expiry = datetime.now(timezone.utc) + timedelta(seconds=int(token.get("expires_in") or 3500))
    with auth_repository() as repository:
        # The initiating CAM identity must already be valid; this check is
        # deliberately not an identity-link/login operation.
        from app.modules.auth_persistence.model import UserModel
        user = repository.session.get(UserModel, initiating_user_id)
        if user is None or user.status != "active":
            raise PermissionError("CAM application user is inactive")
        connection = repository.upsert_connection(
            tenant_id=tenant_id, provider="microsoft", provider_account_id=account_id,
            connection_purpose=purpose,
            account_email=profile.get("mail") or profile.get("userPrincipalName"),
            access_token=token["access_token"], refresh_token=token.get("refresh_token"),
            expires_at=expiry, scopes=sorted(granted), token_type=token.get("token_type") or "Bearer",
            provider_metadata={"user_principal_name": profile.get("userPrincipalName")},
        )
        repository.audit(
            "source_connection_reconnected", tenant_id=tenant_id,
            actor_id=initiating_user_id, provider="microsoft", connection_id=connection.id,
            detail={"connection_purpose": purpose},
        )
        repository.session.commit()
    AUTH_METRICS.increment("connection_reconnected", "microsoft")
    return connection, profile
