import base64
import hashlib
import os
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import HTTPException, Request

SESSION_COOKIE = "cam_microsoft_session"
STATE_TTL_SECONDS = 600
SCOPES = [
    "openid",
    "profile",
    "email",
    "offline_access",
    "User.Read",
    "Sites.Read.All",
    "Files.Read.All",
]


@dataclass
class OAuthTransaction:
    expires_at: float
    code_verifier: str


@dataclass
class MicrosoftSession:
    access_token: str
    refresh_token: str | None
    expires_at: float
    user: dict[str, Any]


_sessions: dict[str, MicrosoftSession] = {}
_pending_states: dict[str, OAuthTransaction] = {}


def _settings() -> tuple[str, str, str, str]:
    client_id = os.getenv("MICROSOFT_CLIENT_ID")
    client_secret = os.getenv("MICROSOFT_CLIENT_SECRET")
    tenant_id = os.getenv("MICROSOFT_TENANT_ID", "organizations")
    redirect_uri = os.getenv(
        "MICROSOFT_REDIRECT_URI",
        "http://localhost:8000/api/auth/microsoft/callback",
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Microsoft OAuth is not configured. Set MICROSOFT_CLIENT_ID and MICROSOFT_CLIENT_SECRET.",
        )
    return client_id, client_secret, tenant_id, redirect_uri


def authorization_url() -> str:
    client_id, _, tenant_id, redirect_uri = _settings()
    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(
        hashlib.sha256(verifier.encode()).digest()
    ).rstrip(b"=").decode()

    now = time.time()
    for key, transaction in list(_pending_states.items()):
        if transaction.expires_at <= now:
            _pending_states.pop(key, None)
    _pending_states[state] = OAuthTransaction(now + STATE_TTL_SECONDS, verifier)

    params = {
        "client_id": client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "prompt": "select_account",
    }
    return (
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize?"
        + urlencode(params)
    )


def consume_state(state: str) -> str:
    transaction = _pending_states.pop(state, None)
    if not transaction or transaction.expires_at <= time.time():
        raise HTTPException(status_code=400, detail="Invalid or expired Microsoft OAuth state.")
    return transaction.code_verifier


async def exchange_code(code: str, code_verifier: str) -> dict:
    client_id, client_secret, tenant_id, redirect_uri = _settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "scope": " ".join(SCOPES),
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()


async def create_session(token: dict) -> tuple[str, MicrosoftSession]:
    granted = set(str(token.get("scope") or "").split())
    if not {"Sites.Read.All", "Files.Read.All"}.issubset(granted):
        raise PermissionError("SharePoint read permissions were not granted.")

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://graph.microsoft.com/v1.0/me",
            params={"$select": "id,displayName,mail,userPrincipalName"},
            headers={"Authorization": f"Bearer {token['access_token']}"},
        )
        response.raise_for_status()
        profile = response.json()

    session_id = secrets.token_urlsafe(32)
    session = MicrosoftSession(
        access_token=token["access_token"],
        refresh_token=token.get("refresh_token"),
        expires_at=time.time() + int(token.get("expires_in") or 3500),
        user={
            "id": profile.get("id"),
            "name": profile.get("displayName"),
            "email": profile.get("mail") or profile.get("userPrincipalName"),
            "picture": None,
        },
    )
    _sessions[session_id] = session
    return session_id, session


def get_session(request: Request) -> MicrosoftSession | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    return _sessions.get(session_id) if session_id else None


async def get_access_token(request: Request) -> str | None:
    session = get_session(request)
    if not session:
        return os.getenv("SHAREPOINT_ACCESS_TOKEN") or None
    if session.expires_at > time.time() + 60:
        return session.access_token
    if not session.refresh_token:
        raise HTTPException(status_code=401, detail="Microsoft session expired. Sign in again.")

    client_id, client_secret, tenant_id, _ = _settings()
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
            data={
                "client_id": client_id,
                "client_secret": client_secret,
                "grant_type": "refresh_token",
                "refresh_token": session.refresh_token,
                "scope": " ".join(SCOPES),
            },
        )
        if not response.is_success:
            raise HTTPException(status_code=401, detail="Microsoft session could not be refreshed.")
        token = response.json()

    session.access_token = token["access_token"]
    session.refresh_token = token.get("refresh_token") or session.refresh_token
    session.expires_at = time.time() + int(token.get("expires_in") or 3500)
    return session.access_token


def remove_session(request: Request) -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        _sessions.pop(session_id, None)
