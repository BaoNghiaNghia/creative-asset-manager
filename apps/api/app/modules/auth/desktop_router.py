from __future__ import annotations

import secrets
from datetime import datetime
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth.desktop_oauth import (
    claim_handoff, complete_callback, consume_launch_token, desktop_intent,
    require_desktop_oauth, set_browser_binding, start_handoff, validate_binding,
)
from app.modules.auth_persistence.service import cookie_options, clear_provider_session_cookies
from app.modules.authorization.principal import require_authenticated_principal
from app.modules.source_sync.login_trigger import enqueue_google_login_sync
from app.providers.google import auth as google_auth
from app.providers.microsoft import auth as microsoft_auth
from app.providers.microsoft.onedrive_registration import register_onedrive_source

router = APIRouter(prefix="/v1/desktop/oauth", tags=["desktop-oauth"])
SOURCE_INTENTS = {"google_drive_connect": ("google", "google_drive"), "onedrive_connect": ("microsoft", "onedrive")}


class StartRequest(BaseModel):
    provider: str | None = Field(default=None, pattern="^(google|microsoft)$")
    intent: str | None = Field(default=None, max_length=32)
    external_source_id: str | None = Field(default=None, min_length=1, max_length=36)
    desktop_instance_binding: str = Field(min_length=64, max_length=64)


class RedeemRequest(BaseModel):
    ticket: str = Field(min_length=32, max_length=256)
    desktop_instance_nonce: str = Field(min_length=32, max_length=256)


def _launch_url(token: str) -> str:
    return get_settings().PUBLIC_APP_URL.rstrip("/") + "/api/v1/desktop/oauth/launch/" + token


def _source_principal(request: Request):
    principal = require_authenticated_principal(request)
    if "assets.manage" not in principal.effective_permissions:
        raise HTTPException(403, detail={"code": "source_permission_required"})
    return principal


def _validate_reconnect(principal, intent: str, source_id: str | None) -> None:
    if not source_id:
        return
    expected = SOURCE_INTENTS[intent][1]
    with SessionLocal() as db:
        source = db.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.id == source_id,
            ExternalSourceModel.tenant_id == principal.active_tenant_id,
        ))
        if source is None:
            raise HTTPException(404, detail={"code": "source_not_found"})
        if source.source_type != expected:
            raise HTTPException(422, detail={"code": "source_type_mismatch"})
        if source.status == "disconnected":
            raise HTTPException(409, detail={"code": "source_not_reconnectable"})


def _browser_binding_cookie(provider: str) -> tuple[str, str]:
    return (google_auth.OAUTH_BINDING_COOKIE, "/api/auth/google") if provider == "google" else (microsoft_auth.OAUTH_BINDING_COOKIE, "/api/auth/microsoft")


def _authorization_url(handoff, browser_binding: str) -> str:
    if handoff.intent == "google_drive_connect":
        flow = google_auth.oauth_flow(require_drive_scope=True)
        url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true", prompt="consent select_account")
        google_auth.remember_state(state, getattr(flow, "code_verifier", None), browser_binding, redirect_intent=desktop_intent(handoff.id))
        return url
    if handoff.intent == "application_login" and handoff.provider == "google":
        flow = google_auth.oauth_flow(require_drive_scope=False)
        url, state = flow.authorization_url(access_type="offline", include_granted_scopes="true")
        google_auth.remember_state(state, getattr(flow, "code_verifier", None), browser_binding, redirect_intent=desktop_intent(handoff.id))
        return url
    return microsoft_auth.authorization_url(browser_binding, desktop_intent(handoff.id) if handoff.intent == "application_login" else handoff.intent + ":" + handoff.id)


@router.post("/start")
def start(body: StartRequest, request: Request):
    require_desktop_oauth()
    validate_binding(body.desktop_instance_binding)
    intent = body.intent or "application_login"
    if intent == "application_login":
        provider = body.provider
        if provider not in {"google", "microsoft"}:
            raise HTTPException(422, detail={"code": "unsupported_provider"})
        context = {}
    elif intent in SOURCE_INTENTS:
        provider = SOURCE_INTENTS[intent][0]
        principal = _source_principal(request)
        if intent == "onedrive_connect":
            settings = get_settings()
            if not settings.MICROSOFT_SOURCE_CONNECTIONS_ENABLED or not settings.ONEDRIVE_SOURCE_ENABLED:
                raise HTTPException(503, detail={"code": "source_connections_disabled"})
        _validate_reconnect(principal, intent, body.external_source_id)
        context = {"initiating_user_id": principal.user_id, "initiating_tenant_id": principal.active_tenant_id, "reconnect_external_source_id": body.external_source_id}
    else:
        raise HTTPException(422, detail={"code": "unsupported_desktop_oauth_intent"})
    token = start_handoff(provider=provider, desktop_instance_binding=body.desktop_instance_binding, intent=intent, **context)
    return {"launch_url": _launch_url(token)}


@router.get("/launch/{launch_token}")
def launch(launch_token: str):
    handoff = consume_launch_token(launch_token)
    browser_binding = secrets.token_urlsafe(32)
    set_browser_binding(handoff_id=handoff.id, browser_binding=browser_binding)
    url = _authorization_url(handoff, browser_binding)
    cookie_name, path = _browser_binding_cookie(handoff.provider)
    response = RedirectResponse(url)
    response.set_cookie(cookie_name, browser_binding, max_age=300, httponly=True, secure=cookie_options()["secure"], samesite="lax", path=path)
    return response


async def _persist_source(handoff, payload: dict, request: Request) -> dict:
    principal = _source_principal(request)
    if principal.user_id != handoff.initiating_user_id or principal.active_tenant_id != handoff.initiating_tenant_id:
        raise HTTPException(403, detail={"code": "desktop_oauth_initiator_mismatch"})
    if handoff.intent == "google_drive_connect":
        credentials = google_auth.Credentials(token=payload["access_token"], refresh_token=payload.get("refresh_token"), token_uri="https://oauth2.googleapis.com/token", client_id=get_settings().GOOGLE_CLIENT_ID, client_secret=get_settings().GOOGLE_CLIENT_SECRET, scopes=payload.get("scopes") or google_auth.DRIVE_SCOPES)
        if payload.get("expiry"): credentials.expiry = datetime.fromisoformat(payload["expiry"])
        cloud = await google_auth.persist_drive_connection(credentials, tenant_id=principal.active_tenant_id, user_id=principal.user_id, granted_scopes=payload.get("scopes"))
        result = enqueue_google_login_sync(cloud, external_source_id=handoff.reconnect_external_source_id)
        if result is None: raise HTTPException(409, detail={"code": "source_registration_failed"})
        return {"success": True, "external_source_id": result.external_source_id, "source_type": "google_drive", "status": "active"}
    if handoff.intent == "onedrive_connect":
        connection, profile = await microsoft_auth.persist_source_connection(payload, tenant_id=principal.active_tenant_id, initiating_user_id=principal.user_id, intent="onedrive_connect")
        source = await register_onedrive_source(tenant_id=principal.active_tenant_id, connection=connection, profile=profile, access_token=payload["access_token"], reconnect_source_id=handoff.reconnect_external_source_id)
        return {"success": True, "external_source_id": source.id, "source_type": "onedrive", "status": source.status}
    raise HTTPException(400, detail={"code": "invalid_desktop_source_intent"})


@router.post("/redeem")
async def redeem(body: RedeemRequest, request: Request):
    handoff, payload = claim_handoff(ticket=body.ticket, desktop_instance_nonce=body.desktop_instance_nonce)
    if handoff.intent in SOURCE_INTENTS:
        return JSONResponse(await _persist_source(handoff, payload, request))
    if handoff.provider == "google":
        credentials = google_auth.Credentials(token=payload["access_token"], refresh_token=payload.get("refresh_token"), token_uri="https://oauth2.googleapis.com/token", client_id=get_settings().GOOGLE_CLIENT_ID, client_secret=get_settings().GOOGLE_CLIENT_SECRET, scopes=payload.get("scopes") or google_auth.IDENTITY_SCOPES)
        if payload.get("expiry"): credentials.expiry = datetime.fromisoformat(payload["expiry"])
        session_id, _ = await google_auth.create_session(credentials, require_drive_scope=False, granted_scopes=payload.get("scopes"))
        cookie_name, oauth_path = google_auth.SESSION_COOKIE, "/api/auth/google"
    else:
        session_id, _ = await microsoft_auth.create_session(payload)
        cookie_name, oauth_path = microsoft_auth.SESSION_COOKIE, "/api/auth/microsoft"
    response = JSONResponse({"authenticated": True})
    clear_provider_session_cookies(response, cookie_name, oauth_path)
    response.set_cookie(cookie_name, session_id, **cookie_options())
    return response


def complete_provider_callback(*, provider: str, handoff_id: str, browser_binding: str, payload: dict) -> RedirectResponse:
    ticket = complete_callback(handoff_id=handoff_id, provider=provider, browser_binding=browser_binding, pending_payload=payload)
    return RedirectResponse("cam://oauth-complete?ticket=" + quote(ticket, safe=""))
