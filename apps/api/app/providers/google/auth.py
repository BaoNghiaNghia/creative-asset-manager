import os
import secrets
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, Request
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from starlette.concurrency import run_in_threadpool

# Google can return canonical scope aliases in the token response. OAuthlib
# may otherwise reject a valid grant before we can validate it explicitly.
os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")

DRIVE_READONLY_SCOPE = "https://www.googleapis.com/auth/drive.readonly"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/userinfo.profile",
    DRIVE_READONLY_SCOPE,
]
SESSION_COOKIE = "cam_google_session"
STATE_TTL_SECONDS = 600


@dataclass
class OAuthTransaction:
    expires_at: float
    code_verifier: str | None


@dataclass
class GoogleSession:
    access_token: str
    refresh_token: str | None
    expires_at: float
    user: dict[str, Any]


_sessions: dict[str, GoogleSession] = {}
_pending_states: dict[str, OAuthTransaction] = {}


def _settings() -> tuple[str, str, str]:
    client_id = os.getenv("GOOGLE_CLIENT_ID")
    client_secret = os.getenv("GOOGLE_CLIENT_SECRET")
    redirect_uri = os.getenv(
        "GOOGLE_REDIRECT_URI",
        "http://localhost:8000/api/auth/google/callback",
    )
    if not client_id or not client_secret:
        raise HTTPException(
            status_code=503,
            detail="Google OAuth is not configured. Set GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET.",
        )
    return client_id, client_secret, redirect_uri


def oauth_flow(state: str | None = None) -> Flow:
    client_id, client_secret, redirect_uri = _settings()
    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [redirect_uri],
            }
        },
        scopes=SCOPES,
        state=state,
    )
    flow.redirect_uri = redirect_uri
    return flow


def remember_state(state: str, code_verifier: str | None) -> None:
    now = time.time()
    for key, transaction in list(_pending_states.items()):
        if transaction.expires_at <= now:
            _pending_states.pop(key, None)
    _pending_states[state] = OAuthTransaction(
        expires_at=now + STATE_TTL_SECONDS,
        code_verifier=code_verifier,
    )


def consume_state(state: str) -> str | None:
    transaction = _pending_states.pop(state, None)
    if not transaction or transaction.expires_at <= time.time():
        raise HTTPException(status_code=400, detail="Invalid or expired OAuth state.")
    return transaction.code_verifier


def validate_granted_scopes(credentials: Credentials) -> None:
    granted = set(credentials.granted_scopes or credentials.scopes or [])
    if DRIVE_READONLY_SCOPE not in granted:
        raise PermissionError("The required Google Drive read-only scope was not granted.")


async def create_session(credentials: Credentials) -> tuple[str, GoogleSession]:
    validate_granted_scopes(credentials)
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.get(
            "https://openidconnect.googleapis.com/v1/userinfo",
            headers={"Authorization": f"Bearer {credentials.token}"},
        )
        response.raise_for_status()
        user = response.json()

    session_id = secrets.token_urlsafe(32)
    expires_at = credentials.expiry.timestamp() if credentials.expiry else time.time() + 3500
    session = GoogleSession(
        access_token=credentials.token,
        refresh_token=credentials.refresh_token,
        expires_at=expires_at,
        user={
            "id": user.get("sub"),
            "name": user.get("name"),
            "email": user.get("email"),
            "picture": user.get("picture"),
        },
    )
    _sessions[session_id] = session
    return session_id, session


def get_session(request: Request) -> GoogleSession | None:
    session_id = request.cookies.get(SESSION_COOKIE)
    return _sessions.get(session_id) if session_id else None


async def get_access_token(request: Request) -> str | None:
    session = get_session(request)
    if not session:
        return os.getenv("GOOGLE_DRIVE_ACCESS_TOKEN") or None

    if session.expires_at > time.time() + 60:
        return session.access_token

    if not session.refresh_token:
        raise HTTPException(status_code=401, detail="Google session expired. Sign in again.")

    client_id, client_secret, _ = _settings()
    credentials = Credentials(
        token=session.access_token,
        refresh_token=session.refresh_token,
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    try:
        await run_in_threadpool(credentials.refresh, GoogleAuthRequest())
    except Exception as exc:
        raise HTTPException(status_code=401, detail="Google session could not be refreshed.") from exc

    session.access_token = credentials.token
    session.expires_at = (
        credentials.expiry.replace(tzinfo=timezone.utc).timestamp()
        if credentials.expiry and credentials.expiry.tzinfo is None
        else credentials.expiry.timestamp() if credentials.expiry
        else time.time() + 3500
    )
    return session.access_token


def remove_session(request: Request) -> None:
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id:
        _sessions.pop(session_id, None)
