import logging
import os
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.modules.auth_persistence.service import cookie_options, delete_cookie_options
from app.providers.microsoft.auth import (
    SESSION_COOKIE,
    OAUTH_BINDING_COOKIE,
    authorization_url,
    consume_state,
    create_session,
    exchange_code,
    get_session,
    remove_session,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/auth/microsoft", tags=["auth"])


def client_redirect(**params: str) -> RedirectResponse:
    client_url = os.getenv("CLIENT_URL", "http://localhost:5173")
    separator = "&" if "?" in client_url else "?"
    return RedirectResponse(client_url + separator + urlencode(params))


@router.get("/login")
async def login():
    binding = secrets.token_urlsafe(32)
    response = RedirectResponse(authorization_url(binding))
    response.set_cookie(OAUTH_BINDING_COOKIE, binding, max_age=600, httponly=True, secure=cookie_options()["secure"], samesite="lax", path="/api/auth/microsoft")
    return response


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
        logger.warning("Microsoft OAuth denied request_id=%s error=%s", request_id, error)
        return client_redirect(
            auth_provider="microsoft",
            auth_error="denied",
            auth_request=request_id,
            auth_message=error_description or error,
        )
    if not state or not code:
        return client_redirect(auth_provider="microsoft", auth_error="incomplete", auth_request=request_id)

    try:
        verifier = consume_state(state, request.cookies.get(OAUTH_BINDING_COOKIE))
        token = await exchange_code(code, verifier)
        session_id, _ = await create_session(token)
    except PermissionError:
        logger.warning("Microsoft SharePoint scopes missing request_id=%s", request_id)
        return client_redirect(auth_provider="microsoft", auth_error="scope", auth_request=request_id)
    except Exception as exc:
        logger.exception(
            "Microsoft OAuth callback failed request_id=%s error_type=%s",
            request_id,
            type(exc).__name__,
        )
        return client_redirect(auth_provider="microsoft", auth_error="token_exchange", auth_request=request_id)

    remove_session(request)
    response = client_redirect(microsoft="connected")
    response.set_cookie(SESSION_COOKIE, session_id, **cookie_options())
    response.delete_cookie(OAUTH_BINDING_COOKIE, path="/api/auth/microsoft")
    return response


@router.get("/session")
async def session(request: Request):
    microsoft_session = get_session(request)
    return {
        "authenticated": microsoft_session is not None,
        "user": microsoft_session.user if microsoft_session else None,
    }


@router.post("/logout")
async def logout(request: Request):
    remove_session(request)
    response = JSONResponse({"authenticated": False})
    response.delete_cookie(SESSION_COOKIE, **delete_cookie_options())
    return response
