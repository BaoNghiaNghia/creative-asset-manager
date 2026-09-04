from __future__ import annotations

import logging
import secrets
from dataclasses import dataclass
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import TenantModel, TenantMembershipModel, UserModel
from app.modules.auth_persistence.login import LoginAdmissionError
from app.modules.auth_persistence.identity import ApplicationUserInactiveError
from app.modules.auth_persistence.service import clear_provider_session_cookies, cookie_options
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.authorization.service import TenantAuthorizationService
from app.providers.microsoft.onedrive_registration import register_onedrive_source
from app.providers.microsoft.auth import (
    OAUTH_BINDING_COOKIE,
    SESSION_COOKIE,
    authorization_url,
    consume_state_details,
    create_session,
    exchange_code,
    get_session,
    persist_source_connection,
    remove_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/microsoft", tags=["auth"])
ASSETS_MANAGE = require_permission("assets.manage")

def _require_source_connections(*, onedrive: bool = False) -> None:
    settings = get_settings()
    if not settings.MICROSOFT_SOURCE_CONNECTIONS_ENABLED or (onedrive and not settings.ONEDRIVE_SOURCE_ENABLED):
        raise HTTPException(503, detail={"code": "source_connections_disabled", "message": "Microsoft source connections are disabled."})


@dataclass(frozen=True, slots=True)
class MicrosoftOAuthIntent:
    kind: str
    tenant_id: str | None = None
    user_id: str | None = None
    reconnect_source_id: str | None = None

    @classmethod
    def application_login(cls) -> "MicrosoftOAuthIntent":
        return cls("application_login")

    @classmethod
    def source_connect(
        cls, kind: str, *, tenant_id: str, user_id: str, reconnect_source_id: str | None
    ) -> "MicrosoftOAuthIntent":
        if kind not in {"onedrive_connect", "sharepoint_connect"}:
            raise ValueError("unsupported source OAuth intent")
        return cls(kind, tenant_id, user_id, reconnect_source_id)

    def serialize(self) -> str:
        if self.kind == "application_login":
            return self.kind
        if not self.tenant_id or not self.user_id:
            raise ValueError("source OAuth intent is incomplete")
        return ":".join((self.kind, self.tenant_id, self.reconnect_source_id or "-", self.user_id))

    @classmethod
    def parse(cls, value: str) -> "MicrosoftOAuthIntent":
        if value == "application_login":
            return cls.application_login()
        parts = value.split(":", 3)
        if len(parts) != 4 or parts[0] not in {"onedrive_connect", "sharepoint_connect"}:
            raise ValueError("invalid Microsoft OAuth intent")
        kind, tenant_id, source_id, user_id = parts
        if not tenant_id or not user_id:
            raise ValueError("invalid Microsoft OAuth intent")
        return cls.source_connect(
            kind, tenant_id=tenant_id, user_id=user_id,
            reconnect_source_id=None if source_id == "-" else source_id,
        )


def client_redirect(**params: str) -> RedirectResponse:
    client_url = get_settings().PUBLIC_APP_URL.rstrip("/")
    return RedirectResponse(client_url + ("&" if "?" in client_url else "?") + urlencode(params))


def _oauth_response(intent: MicrosoftOAuthIntent) -> RedirectResponse:
    binding = secrets.token_urlsafe(32)
    response = RedirectResponse(authorization_url(binding, intent.serialize()))
    if intent.kind == "application_login":
        clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/microsoft")
    response.set_cookie(
        OAUTH_BINDING_COOKIE, binding, max_age=600, httponly=True,
        secure=cookie_options()["secure"], samesite="lax", path="/api/auth/microsoft",
    )
    return response


def _validate_reconnect(
    *, principal: CurrentPrincipal, source_id: str | None, expected_type: str
) -> None:
    if not source_id:
        return
    with SessionLocal() as session:
        source = session.scalar(select(ExternalSourceModel).where(
            ExternalSourceModel.id == source_id,
            ExternalSourceModel.tenant_id == principal.active_tenant_id,
        ))
        if source is None:
            raise HTTPException(404, detail={"code": "source_not_found", "message": "Source is unavailable"})
        if source.source_type != expected_type:
            raise HTTPException(422, detail={"code": "source_type_mismatch", "message": "Source type does not match this connection"})
        if source.status == "disconnected":
            raise HTTPException(409, detail={"code": "source_not_reconnectable", "message": "Disconnected source cannot be reconnected"})


def _reauthorize_source(intent: MicrosoftOAuthIntent) -> None:
    assert intent.tenant_id and intent.user_id
    expected_type = "onedrive" if intent.kind == "onedrive_connect" else "sharepoint"
    with SessionLocal() as session:
        user = session.get(UserModel, intent.user_id)
        tenant = session.get(TenantModel, intent.tenant_id)
        membership = session.scalar(select(TenantMembershipModel).where(
            TenantMembershipModel.tenant_id == intent.tenant_id,
            TenantMembershipModel.user_id == intent.user_id,
        ))
        if user is None or user.status != "active":
            raise HTTPException(403, detail={"code": "account_inactive", "message": "Application user is inactive"})
        if tenant is None or tenant.status != "active":
            raise HTTPException(403, detail={"code": "tenant_membership_required", "message": "Tenant is inactive"})
        if membership is None or membership.status != "active":
            raise HTTPException(403, detail={"code": "tenant_membership_required", "message": "Active tenant membership is required"})
        effective = TenantAuthorizationService(session).get_effective_permissions(
            tenant_id=intent.tenant_id, user_id=intent.user_id
        )
        if "assets.manage" not in effective.permissions:
            raise HTTPException(403, detail={"code": "source_permission_required", "message": "Source management permission is required"})
        if intent.reconnect_source_id:
            source = session.scalar(select(ExternalSourceModel).where(
                ExternalSourceModel.id == intent.reconnect_source_id,
                ExternalSourceModel.tenant_id == intent.tenant_id,
            ))
            if source is None:
                raise HTTPException(404, detail={"code": "source_not_found", "message": "Source is unavailable"})
            if source.source_type != expected_type:
                raise HTTPException(422, detail={"code": "source_type_mismatch", "message": "Source type does not match this connection"})
            if source.status == "disconnected":
                raise HTTPException(409, detail={"code": "source_not_reconnectable", "message": "Disconnected source cannot be reconnected"})


@router.get("/login")
async def login():
    return _oauth_response(MicrosoftOAuthIntent.application_login())


@router.get("/connect-onedrive")
async def connect_onedrive(
    external_source_id: str | None = Query(None),
    principal: CurrentPrincipal = Depends(ASSETS_MANAGE),
):
    _require_source_connections(onedrive=True)
    _validate_reconnect(principal=principal, source_id=external_source_id, expected_type="onedrive")
    return _oauth_response(MicrosoftOAuthIntent.source_connect(
        "onedrive_connect", tenant_id=principal.active_tenant_id,
        user_id=principal.user_id, reconnect_source_id=external_source_id,
    ))


@router.get("/connect-sharepoint")
async def connect_sharepoint(
    external_source_id: str | None = Query(None),
    principal: CurrentPrincipal = Depends(ASSETS_MANAGE),
):
    _require_source_connections()
    _validate_reconnect(principal=principal, source_id=external_source_id, expected_type="sharepoint")
    return _oauth_response(MicrosoftOAuthIntent.source_connect(
        "sharepoint_connect", tenant_id=principal.active_tenant_id,
        user_id=principal.user_id, reconnect_source_id=external_source_id,
    ))


async def _complete_application_login(verifier: str, code: str) -> RedirectResponse:
    token = await exchange_code(code, verifier, intent="application_login")
    session_id, _ = await create_session(token)
    response = client_redirect(microsoft="connected")
    clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/microsoft")
    response.set_cookie(SESSION_COOKIE, session_id, **cookie_options())
    return response


async def _complete_source_connect(
    intent: MicrosoftOAuthIntent, verifier: str, code: str
) -> RedirectResponse:
    _reauthorize_source(intent)
    token = await exchange_code(code, verifier, intent=intent.kind)
    connection, _profile = await persist_source_connection(
        token, tenant_id=intent.tenant_id or "", initiating_user_id=intent.user_id or "",
        intent=intent.kind,
    )
    source_kind = "onedrive" if intent.kind == "onedrive_connect" else "sharepoint"
    source_id = None
    if source_kind == "onedrive":
        source = await register_onedrive_source(
            tenant_id=intent.tenant_id or "", connection=connection, profile=_profile,
            access_token=token["access_token"], reconnect_source_id=intent.reconnect_source_id,
        )
        source_id = source.id
    params = {"microsoft": "source_connected", "source": source_kind, "connection": connection.id}
    if source_id:
        params["external_source_id"] = source_id
    return client_redirect(**params)


@router.get("/callback")
async def callback(
    request: Request,
    state: str | None = Query(None),
    code: str | None = Query(None),
    error: str | None = Query(None),
    error_description: str | None = Query(None),
):
    request_id = secrets.token_hex(6)
    if error:
        return client_redirect(auth_provider="microsoft", auth_error="denied", auth_request=request_id,
            auth_message=error_description or error)
    if not state or not code:
        return client_redirect(auth_provider="microsoft", auth_error="incomplete", auth_request=request_id)
    try:
        verifier, raw_intent = consume_state_details(state, request.cookies.get(OAUTH_BINDING_COOKIE))
        intent = MicrosoftOAuthIntent.parse(raw_intent)
        response = (
            await _complete_application_login(verifier, code)
            if intent.kind == "application_login"
            else await _complete_source_connect(intent, verifier, code)
        )
    except LoginAdmissionError as exc:
        return client_redirect(auth_provider="microsoft", auth_error=exc.code, auth_request=request_id)
    except ApplicationUserInactiveError:
        return client_redirect(auth_provider="microsoft", auth_error="account_inactive", auth_request=request_id)
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code_value = detail.get("code") if isinstance(detail, dict) else None
        return client_redirect(auth_provider="microsoft", auth_error=code_value or "oauth_state_invalid", auth_request=request_id)
    except Exception as exc:
        logger.exception("Microsoft OAuth callback failed request_id=%s error_type=%s", request_id, type(exc).__name__)
        return client_redirect(auth_provider="microsoft", auth_error="token_exchange_failed", auth_request=request_id)
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/microsoft")
    return response


@router.get("/session")
async def session(request: Request):
    microsoft_session = get_session(request)
    return {"authenticated": microsoft_session is not None, "user": microsoft_session.user if microsoft_session else None}


@router.post("/logout")
async def logout(request: Request):
    remove_session(request)
    response = JSONResponse({"authenticated": False})
    clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/microsoft")
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/microsoft")
    return response
