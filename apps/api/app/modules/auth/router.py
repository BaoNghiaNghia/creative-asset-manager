import logging
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.core.config import get_settings
from app.modules.source_sync.login_trigger import enqueue_google_login_sync

from app.modules.auth_persistence.service import cookie_options, delete_cookie_options
from app.modules.authorization.principal import CurrentPrincipal, require_permission
from app.modules.auth_persistence.identity import ApplicationUserInactiveError
from app.modules.auth_persistence.login import LoginAdmissionError
from app.providers.google.auth import (
    SESSION_COOKIE,
    OAUTH_BINDING_COOKIE,
    consume_state_details,
    create_session,
    get_session,
    oauth_flow,
    remember_state,
    remove_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/google", tags=["auth"])


def client_redirect(**params: str) -> RedirectResponse:
    client_url = get_settings().PUBLIC_APP_URL.rstrip("/")
    separator = "&" if "?" in client_url else "?"
    return RedirectResponse(client_url + separator + urlencode(params))


def _authorization_response(flow, *, redirect_intent: str) -> RedirectResponse:
    authorization_url, state = flow.authorization_url(access_type="offline", prompt="consent")
    binding = secrets.token_urlsafe(32)
    remember_state(
        state,
        getattr(flow, "code_verifier", None),
        binding,
        redirect_intent=redirect_intent,
    )
    response = RedirectResponse(authorization_url)
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
    )


@router.get("/connect-drive")
async def connect_drive(
    request: Request,
    principal: CurrentPrincipal = Depends(require_permission("assets.manage")),
):
    """Privileged workspace setup: requests Google Drive read/write access."""
    return _authorization_response(
        oauth_flow(require_drive_scope=True),
        redirect_intent=f"drive_connect:{principal.active_tenant_id}",
    )


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
    flow = oauth_flow(state, require_drive_scope=drive_connect)
    if code_verifier:
        flow.code_verifier = code_verifier

    try:
        # State is validated above. Passing the one-time code directly avoids
        # OAuthlib rejecting an otherwise valid HTTP localhost callback while
        # the token request itself still goes to Google's HTTPS endpoint.
        flow.fetch_token(code=code)
    except Exception as exc:
        logger.exception(
            "Google OAuth token exchange failed request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        return client_redirect(auth_error="token_exchange", auth_request=request_id)

    try:
        connection_tenant_id = (
            redirect_intent.removeprefix("drive_connect:")
            if drive_connect else None
        )
        session_id, cloud_session = await create_session(
            flow.credentials,
            require_drive_scope=drive_connect,
            connection_tenant_id=connection_tenant_id,
        )
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
    except PermissionError:
        logger.warning("Google Drive scope was not granted request_id=%s", request_id)
        return client_redirect(auth_error="scope", auth_request=request_id)
    except Exception as exc:
        logger.exception(
            "Google OAuth user profile failed request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        return client_redirect(auth_error="profile", auth_request=request_id)


    if drive_connect:
        try:
            enqueue_google_login_sync(cloud_session)
        except Exception as exc:
            logger.exception(
                "Google Drive source sync enqueue failed request_id=%s error_type=%s",
                request_id,
                type(exc).__name__,
            )

    remove_session(request)
    response = client_redirect(
        google="source_connected" if drive_connect else "signed_in"
    )
    response.set_cookie(SESSION_COOKIE, session_id, **cookie_options())
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/google")
    return response


@router.get("/session")
async def session(request: Request):
    google_session = get_session(request)
    return {
        "authenticated": google_session is not None,
        "user": google_session.user if google_session else None,
    }


@router.post("/logout")
async def logout(request: Request):
    remove_session(request)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, **delete_cookie_options())
    return response
