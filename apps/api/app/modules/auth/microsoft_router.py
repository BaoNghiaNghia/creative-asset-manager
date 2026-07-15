import logging
import os
import secrets
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.providers.microsoft.auth import (
    SESSION_COOKIE,
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
    return RedirectResponse(authorization_url())


@router.get("/callback")
async def callback(
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
        verifier = consume_state(state)
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

    response = client_redirect(microsoft="connected")
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=os.getenv("MICROSOFT_OAUTH_COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
    )
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
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
