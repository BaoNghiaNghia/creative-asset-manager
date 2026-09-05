from __future__ import annotations

import base64
import hashlib
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.modules.auth.desktop_oauth import (
    claim_handoff,
    complete_callback,
    consume_launch_token,
    desktop_intent,
    require_desktop_oauth,
    set_browser_binding,
    start_handoff,
    validate_binding,
)
from app.modules.auth_persistence.service import cookie_options, clear_provider_session_cookies
from app.providers.google import auth as google_auth
from app.providers.microsoft import auth as microsoft_auth

router = APIRouter(prefix="/v1/desktop/oauth", tags=["desktop-oauth"])


class StartRequest(BaseModel):
    provider: str = Field(pattern="^(google|microsoft)$")
    desktop_instance_binding: str = Field(min_length=64, max_length=64)


class RedeemRequest(BaseModel):
    ticket: str = Field(min_length=32, max_length=256)
    desktop_instance_nonce: str = Field(min_length=32, max_length=256)


def _launch_url(token: str) -> str:
    return get_settings().PUBLIC_APP_URL.rstrip("/") + "/api/v1/desktop/oauth/launch/" + token


def _browser_binding_cookie(provider: str) -> tuple[str, str]:
    if provider == "google":
        return google_auth.OAUTH_BINDING_COOKIE, "/api/auth/google"
    return microsoft_auth.OAUTH_BINDING_COOKIE, "/api/auth/microsoft"


def _authorization_url(provider: str, browser_binding: str, intent: str) -> str:
    if provider == "google":
        flow = google_auth.oauth_flow(require_drive_scope=False)
        url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true")
        google_auth.remember_state(
            state,
            getattr(flow, "code_verifier", None),
            browser_binding,
            redirect_intent=intent,
        )
        return url
    return microsoft_auth.authorization_url(browser_binding, intent)


@router.post("/start")
def start(body: StartRequest):
    require_desktop_oauth()
    validate_binding(body.desktop_instance_binding)
    token = start_handoff(
        provider=body.provider,
        desktop_instance_binding=body.desktop_instance_binding,
    )
    return {"launch_url": _launch_url(token)}


@router.get("/launch/{launch_token}")
def launch(launch_token: str):
    handoff = consume_launch_token(launch_token)
    browser_binding = secrets.token_urlsafe(32)
    set_browser_binding(handoff_id=handoff.id, browser_binding=browser_binding)
    url = _authorization_url(
        handoff.provider,
        browser_binding,
        desktop_intent(handoff.id),
    )
    cookie_name, path = _browser_binding_cookie(handoff.provider)
    response = RedirectResponse(url)
    response.set_cookie(
        cookie_name,
        browser_binding,
        max_age=300,
        httponly=True,
        secure=cookie_options()["secure"],
        samesite="lax",
        path=path,
    )
    return response


@router.post("/redeem")
async def redeem(body: RedeemRequest):
    provider, payload = claim_handoff(
        ticket=body.ticket,
        desktop_instance_nonce=body.desktop_instance_nonce,
    )
    if provider == "google":
        expiry = payload.get("expiry")
        credentials = google_auth.Credentials(
            token=payload["access_token"],
            refresh_token=payload.get("refresh_token"),
            token_uri="https://oauth2.googleapis.com/token",
            client_id=get_settings().GOOGLE_CLIENT_ID,
            client_secret=get_settings().GOOGLE_CLIENT_SECRET,
            scopes=payload.get("scopes") or google_auth.IDENTITY_SCOPES,
        )
        if expiry:
            credentials.expiry = datetime.fromisoformat(expiry)
        session_id, _ = await google_auth.create_session(
            credentials,
            require_drive_scope=False,
            granted_scopes=payload.get("scopes"),
        )
        cookie_name, oauth_path = google_auth.SESSION_COOKIE, "/api/auth/google"
    elif provider == "microsoft":
        session_id, _ = await microsoft_auth.create_session(payload)
        cookie_name, oauth_path = microsoft_auth.SESSION_COOKIE, "/api/auth/microsoft"
    else:
        raise HTTPException(400, detail={"code": "invalid_desktop_provider"})
    response = JSONResponse({"authenticated": True})
    clear_provider_session_cookies(response, cookie_name, oauth_path)
    response.set_cookie(cookie_name, session_id, **cookie_options())
    return response


def complete_provider_callback(
    *,
    provider: str,
    handoff_id: str,
    browser_binding: str,
    payload: dict,
) -> RedirectResponse:
    ticket = complete_callback(
        handoff_id=handoff_id,
        provider=provider,
        browser_binding=browser_binding,
        pending_payload=payload,
    )
    return RedirectResponse("cam://oauth-complete?ticket=" + quote(ticket, safe=""))
