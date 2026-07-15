import os

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, RedirectResponse

from app.providers.google.auth import (
    SESSION_COOKIE,
    consume_state,
    create_session,
    get_session,
    oauth_flow,
    remember_state,
    remove_session,
)

router = APIRouter(prefix="/auth/google", tags=["auth"])


@router.get("/login")
async def login():
    flow = oauth_flow()
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    remember_state(state)
    return RedirectResponse(authorization_url)


@router.get("/callback")
async def callback(
    state: str = Query(...),
    code: str | None = Query(None),
    error: str | None = Query(None),
):
    if error:
        raise HTTPException(status_code=400, detail=f"Google authorization failed: {error}")
    if not code:
        raise HTTPException(status_code=400, detail="Google did not return an authorization code.")

    consume_state(state)
    flow = oauth_flow(state)
    try:
        flow.fetch_token(code=code)
        session_id, _ = await create_session(flow.credentials)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Could not complete Google authorization.") from exc

    client_url = os.getenv("CLIENT_URL", "http://localhost:5173")
    response = RedirectResponse(client_url)
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=30 * 24 * 60 * 60,
        httponly=True,
        secure=os.getenv("GOOGLE_OAUTH_COOKIE_SECURE", "false").lower() == "true",
        samesite="lax",
        path="/",
    )
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
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response
