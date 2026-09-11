import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.providers.google.auth import google_profile


class UnauthorizedClient:
    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def get(self, *args, **kwargs):
        request = httpx.Request("GET", "https://openidconnect.googleapis.com/v1/userinfo")
        return httpx.Response(401, request=request, json={"error": "invalid_token"})


def test_google_profile_uses_verified_id_token_when_userinfo_rejects_access_token():
    credentials = SimpleNamespace(token="access-token", id_token="signed-id-token")

    with (
        patch("app.providers.google.auth.httpx.AsyncClient", return_value=UnauthorizedClient()),
        patch(
            "app.providers.google.auth.run_in_threadpool",
            new=AsyncMock(return_value={"sub": "google-subject", "email": "user@example.com"}),
        ) as verify,
    ):
        profile = asyncio.run(google_profile(credentials))

    assert profile == {"sub": "google-subject", "email": "user@example.com"}
    verify.assert_awaited_once()


def test_google_profile_keeps_non_auth_userinfo_failures_visible():
    class UnavailableClient(UnauthorizedClient):
        async def get(self, *args, **kwargs):
            request = httpx.Request("GET", "https://openidconnect.googleapis.com/v1/userinfo")
            return httpx.Response(503, request=request)

    credentials = SimpleNamespace(token="access-token", id_token="signed-id-token")
    with patch("app.providers.google.auth.httpx.AsyncClient", return_value=UnavailableClient()):
        try:
            asyncio.run(google_profile(credentials))
        except httpx.HTTPStatusError as error:
            assert error.response.status_code == 503
        else:
            raise AssertionError("expected the userinfo service failure to propagate")
