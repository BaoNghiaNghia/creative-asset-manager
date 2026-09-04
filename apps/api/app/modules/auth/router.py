import logging
import secrets
from urllib.parse import urlencode
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import case, func, select
from pydantic import BaseModel, Field, SecretStr
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import get_settings
from app.core.database import SessionLocal
from app.modules.source_sync.login_trigger import enqueue_google_login_sync
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, UserModel

from app.modules.auth_persistence.service import clear_provider_session_cookies, cookie_options
from app.modules.authorization.principal import CurrentPrincipal, require_permission, require_platform_admin
from app.modules.authorization.platform_admin import PlatformAdminService
from app.modules.authorization.service import TenantAuthorizationService
from app.modules.auth_persistence.identity import ApplicationUserInactiveError
from app.modules.auth_persistence.login import LoginAdmissionError
from app.modules.storage.managed_oauth import (
    ManagedStorageCredentialUnavailableError,
    ManagedStorageCredentialValidationError,
    check_managed_storage_refresh_token,
    save_managed_storage_refresh_token_unverified,
    managed_storage_oauth_status,
    persist_managed_storage_connection,
)
from app.providers.google.auth import (
    DRIVE_READONLY_SCOPE,
    DRIVE_WRITE_SCOPE,
    SESSION_COOKIE,
    OAUTH_BINDING_COOKIE,
    consume_state_details,
    create_session,
    get_session,
    oauth_flow,
    resolve_granted_scopes,
    remember_state,
    remove_session,
    persist_drive_connection,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])


class ManagedStorageRefreshTokenRequest(BaseModel):
    refresh_token: SecretStr = Field(min_length=16, max_length=4096)


def client_redirect(**params: str) -> RedirectResponse:
    client_url = get_settings().PUBLIC_APP_URL.rstrip("/")
    separator = "&" if "?" in client_url else "?"
    return RedirectResponse(client_url + separator + urlencode(params))

def managed_storage_redirect(status: str, **params: str) -> RedirectResponse:
    client_url = get_settings().PUBLIC_APP_URL.rstrip("/") + "/ai-operations"
    query = {"tab": "providers", "managed_storage": status, **params}
    return RedirectResponse(client_url + "?" + urlencode(query))


def _authorization_response(flow, *, redirect_intent: str, prompt: str | None = None) -> RedirectResponse:
    options = {"access_type": "offline", "include_granted_scopes": "true"}
    if prompt:
        options["prompt"] = prompt
    authorization_url, state = flow.authorization_url(**options)
    binding = secrets.token_urlsafe(32)
    remember_state(
        state,
        getattr(flow, "code_verifier", None),
        binding,
        redirect_intent=redirect_intent,
    )
    response = RedirectResponse(authorization_url)
    # A Drive connection is performed by an already authenticated application
    # user. Keep that session through its separate OAuth consent flow: its
    # callback intentionally persists a source connection, not a new login.
    if redirect_intent == "application_login":
        clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/google")
    response.set_cookie(
        OAUTH_BINDING_COOKIE, binding, max_age=600, httponly=True,
        secure=cookie_options()["secure"], samesite="lax", path="/api/auth/google",
    )
    return response


@router.get("/login")
async def login(request: Request):
    """Application sign-in: identity scopes only, never Drive access."""
    return _authorization_response(
        oauth_flow(require_drive_scope=False),
        redirect_intent="application_login",
        prompt=None,
    )


@router.get("/connect-drive")
async def connect_drive(
    request: Request,
    source_id: str | None = Query(None),
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
):
    """Privileged workspace setup: requests Google Drive read/write access."""
    if source_id:
        with SessionLocal() as db:
            source = db.scalar(
                select(ExternalSourceModel).where(
                    ExternalSourceModel.id == source_id,
                    ExternalSourceModel.tenant_id == principal.active_tenant_id,
                    ExternalSourceModel.source_type == "google_drive",
                )
            )
        if source is None:
            raise HTTPException(404, "Google Drive source was not found.")
    source_token = source_id or chr(45)
    intent = f"drive_connect:{principal.active_tenant_id}:{source_token}:{principal.user_id}"
    return _authorization_response(
        oauth_flow(require_drive_scope=True),
        redirect_intent=intent,
        prompt="consent select_account",
    )


@router.get("/connect-managed-storage")
async def connect_managed_storage(
    principal: CurrentPrincipal = Depends(require_platform_admin),
):
    """Rotate the global Managed Storage credential through explicit consent."""
    settings = get_settings()
    if not settings.GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID:
        raise HTTPException(503, "Managed Storage root folder is not configured.")
    return _authorization_response(
        oauth_flow(require_drive_scope=True),
        redirect_intent=(
            f"managed_storage_connect:{principal.active_tenant_id}:{principal.user_id}"
        ),
        prompt="consent select_account",
    )


@router.get("/managed-storage/status")
async def managed_storage_status(
    principal: CurrentPrincipal = Depends(require_platform_admin),
):
    return managed_storage_oauth_status(get_settings())


async def _check_manual_managed_storage_token(
    body: ManagedStorageRefreshTokenRequest,
    principal: CurrentPrincipal,
    *,
    save: bool,
) -> dict[str, Any]:
    try:
        result = await check_managed_storage_refresh_token(
            get_settings(),
            body.refresh_token.get_secret_value(),
            tenant_id=principal.active_tenant_id,
            initiating_user_id=principal.user_id,
            save=save,
        )
    except ManagedStorageCredentialValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    except ManagedStorageCredentialUnavailableError as exc:
        raise HTTPException(503, str(exc)) from exc
    return {
        "status": "VALID",
        "account_email": result.account_email,
        "folder_access": "READ_WRITE",
        "saved": result.saved,
    }


@router.post("/managed-storage/credential/test")
async def test_managed_storage_refresh_token(
    body: ManagedStorageRefreshTokenRequest,
    principal: CurrentPrincipal = Depends(require_platform_admin),
):
    return await _check_manual_managed_storage_token(body, principal, save=False)


@router.put("/managed-storage/credential")
async def save_managed_storage_refresh_token(
    body: ManagedStorageRefreshTokenRequest,
    principal: CurrentPrincipal = Depends(require_platform_admin),
):
    try:
        result = await save_managed_storage_refresh_token_unverified(
            get_settings(),
            body.refresh_token.get_secret_value(),
            tenant_id=principal.active_tenant_id,
            initiating_user_id=principal.user_id,
        )
    except ManagedStorageCredentialValidationError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "status": "SAVED_UNVERIFIED",
        "account_email": result.account_email,
        "folder_access": "UNVERIFIED",
        "saved": result.saved,
    }


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
        logger.warning(
            "Google OAuth was denied request_id=%s error=%s",
            request_id,
            error,
        )
        return client_redirect(
            auth_error="denied",
            auth_request=request_id,
            auth_message=error_description or error,
        )

    if not state or not code:
        logger.warning("Google OAuth callback is incomplete request_id=%s", request_id)
        return client_redirect(auth_error="incomplete", auth_request=request_id)

    try:
        code_verifier, redirect_intent = consume_state_details(
            state, request.cookies.get(OAUTH_BINDING_COOKIE)
        )
    except HTTPException:
        logger.warning("Google OAuth state is invalid or expired request_id=%s", request_id)
        return client_redirect(auth_error="state", auth_request=request_id)

    drive_connect = redirect_intent.startswith("drive_connect:")
    connection_tenant_id = None
    reconnect_source_id = None
    initiating_user_id = None
    managed_storage_connect = redirect_intent.startswith("managed_storage_connect:")
    if drive_connect:
        parts = redirect_intent.split(":", 3)
        connection_tenant_id = parts[1] if len(parts) > 1 else None
        reconnect_source_id = parts[2] if len(parts) > 2 and parts[2] != "-" else None
        initiating_user_id = parts[3] if len(parts) > 3 else None
        if not connection_tenant_id or not initiating_user_id:
            return client_redirect(auth_error="source_connection_failed", auth_request=request_id)
        with SessionLocal() as db:
            user = db.get(UserModel, initiating_user_id)
            authorization = TenantAuthorizationService(db).get_effective_permissions(tenant_id=connection_tenant_id, user_id=initiating_user_id)
            if user is None or user.status != "active" or not authorization.membership_id or "assets.manage" not in authorization.permissions:
                logger.warning("Google Drive callback authorization failed request_id=%s", request_id)
                return client_redirect(auth_error="source_connection_failed", auth_request=request_id)
            if reconnect_source_id:
                source = db.scalar(select(ExternalSourceModel).where(ExternalSourceModel.id == reconnect_source_id, ExternalSourceModel.tenant_id == connection_tenant_id, ExternalSourceModel.source_type == "google_drive"))
                if source is None:
                    return client_redirect(auth_error="source_connection_failed", auth_request=request_id)
    elif managed_storage_connect:
        parts = redirect_intent.split(":", 2)
        connection_tenant_id = parts[1] if len(parts) > 1 else None
        initiating_user_id = parts[2] if len(parts) > 2 else None
        if not connection_tenant_id or not initiating_user_id:
            return managed_storage_redirect("error", auth_request=request_id)
        with SessionLocal() as db:
            user = db.get(UserModel, initiating_user_id)
            is_platform_admin = PlatformAdminService(db).is_platform_admin(
                initiating_user_id
            )
            if user is None or user.status != "active" or not is_platform_admin:
                logger.warning("Managed Storage callback authorization failed request_id=%s", request_id)
                return managed_storage_redirect("error", auth_request=request_id)
    flow = oauth_flow(state, require_drive_scope=drive_connect or managed_storage_connect)
    if code_verifier:
        flow.code_verifier = code_verifier

    try:
        # State is validated above. Passing the one-time code directly avoids
        # OAuthlib rejecting an otherwise valid HTTP localhost callback while
        # the token request itself still goes to Google's HTTPS endpoint.
        token_response = flow.fetch_token(code=code)
    except Exception as exc:
        logger.exception(
            "Google OAuth token exchange failed request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        return client_redirect(auth_error="token_exchange", auth_request=request_id)

    try:
        granted_scopes = resolve_granted_scopes(flow.credentials, token_response if isinstance(token_response, dict) else None)
        if managed_storage_connect:
            root_folder_id = str(
                get_settings().GOOGLE_MANAGED_STORAGE_ROOT_FOLDER_ID or ""
            ).strip()
            if not root_folder_id:
                raise ValueError("Managed Storage root folder is not configured")
            await persist_managed_storage_connection(
                flow.credentials,
                tenant_id=connection_tenant_id,
                initiating_user_id=initiating_user_id,
                root_folder_id=root_folder_id,
                granted_scopes=granted_scopes,
            )
            cloud_session = None
            session_id = None
        elif drive_connect:
            cloud_session = await persist_drive_connection(flow.credentials, tenant_id=connection_tenant_id, user_id=initiating_user_id, granted_scopes=granted_scopes)
            session_id = None
        else:
            session_id, cloud_session = await create_session(flow.credentials, require_drive_scope=False, granted_scopes=granted_scopes)
    except ApplicationUserInactiveError:
        logger.warning("Google application user is inactive request_id=%s", request_id)
        return client_redirect(auth_error="account_inactive", auth_request=request_id)
    except LoginAdmissionError as exc:
        logger.warning(
            "Google application login denied request_id=%s code=%s",
            request_id,
            exc.code,
        )
        return client_redirect(auth_error=exc.code, auth_request=request_id)
    except PermissionError as exc:
        logger.warning("Google Drive scope was not granted request_id=%s", request_id)
        if managed_storage_connect:
            return managed_storage_redirect(
                "error", auth_request=request_id,
                auth_message=str(exc),
            )
        return client_redirect(auth_error="insufficient_drive_scope", auth_request=request_id)
    except Exception as exc:
        logger.exception(
            "Google OAuth user profile failed request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        if managed_storage_connect:
            return managed_storage_redirect("error", auth_request=request_id)
        return client_redirect(auth_error="profile", auth_request=request_id)


    if drive_connect:
        try:
            if reconnect_source_id:
                enqueue_google_login_sync(cloud_session, external_source_id=reconnect_source_id)
            else:
                enqueue_google_login_sync(cloud_session)
        except Exception as exc:
            logger.exception(
                "Google Drive source connection failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )
            return client_redirect(auth_error="source_connection_failed", auth_request=request_id)

    if managed_storage_connect:
        response = managed_storage_redirect("connected")
    elif drive_connect:
        response = client_redirect(google="source_connected")
    else:
        response = client_redirect(google="signed_in")
    if not drive_connect and not managed_storage_connect:
        remove_session(request)
        clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/google")
        response.set_cookie(SESSION_COOKIE, session_id, **cookie_options())
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/google")
    return response


@router.get("/session")
async def session(request: Request):
    google_session = get_session(request)
    result = {"authenticated": google_session is not None, "user": google_session.user if google_session else None, "drive_connected": False, "drive_usable": False, "external_source_id": None, "connection_status": None, "reconnect_required": False}
    active_tenant_id = getattr(google_session, "active_tenant_id", None) if google_session else None
    if not active_tenant_id:
        return result
    with SessionLocal() as db:
        is_default_text = func.lower(
            func.coalesce(
                ExternalSourceModel.source_metadata.op("->>")("is_default"),
                "false",
            )
        )
        is_default_rank = case(
            (is_default_text.in_(("true", "1", "yes")), 1),
            else_=0,
        )
        source = db.scalar(
            select(ExternalSourceModel)
            .where(
                ExternalSourceModel.tenant_id == active_tenant_id,
                ExternalSourceModel.source_type == "google_drive",
            )
            .order_by(
                is_default_rank.desc(),
                ExternalSourceModel.updated_at.desc(),
                ExternalSourceModel.created_at.asc(),
                ExternalSourceModel.id.asc(),
            )
            .limit(1)
        )
        if source:
            result["external_source_id"] = source.id
            metadata = source.source_metadata if isinstance(source.source_metadata, dict) else {}
            connection_id = metadata.get("oauth_connection_id")
            connection = db.scalar(select(OAuthConnectionModel).where(OAuthConnectionModel.id == connection_id, OAuthConnectionModel.tenant_id == active_tenant_id)) if connection_id else None
            result["drive_connected"] = connection is not None
            result["connection_status"] = connection.status if connection else "missing"
            result["reconnect_required"] = result["connection_status"] != "active"
            result["drive_usable"] = bool(
                connection
                and connection.status == "active"
                and ({DRIVE_READONLY_SCOPE, DRIVE_WRITE_SCOPE} & set(connection.scopes_json or ()))
            )
    return result


@router.post("/logout")
async def logout(request: Request):
    remove_session(request)
    response = JSONResponse({"authenticated": False})
    clear_provider_session_cookies(response, SESSION_COOKIE, "/api/auth/google")
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/google")
    return response
