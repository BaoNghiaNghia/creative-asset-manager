import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.modules.auth.router import router as google_router
from app.modules.auth.microsoft_router import router as microsoft_router

class AuthApiExposureTest(unittest.TestCase):
    def test_session_apis_never_return_provider_tokens(self):
        app=FastAPI(); app.include_router(google_router,prefix="/api"); app.include_router(microsoft_router,prefix="/api")
        cloud=SimpleNamespace(user={"id":"tenant-a","email":"a@example.com"},access_token="plain-access-secret",refresh_token="plain-refresh-secret")
        with patch("app.modules.auth.router.get_session",return_value=cloud):
            google=TestClient(app).get("/api/auth/google/session")
        with patch("app.modules.auth.microsoft_router.get_session",return_value=cloud):
            microsoft=TestClient(app).get("/api/auth/microsoft/session")
        for response in (google,microsoft):
            self.assertEqual(response.status_code,200)
            self.assertEqual(set(response.json()),{"authenticated","user"})
            self.assertNotIn("plain-access-secret",response.text)
            self.assertNotIn("plain-refresh-secret",response.text)

    def test_google_callback_returns_stable_signup_denial(self):
        from app.modules.auth_persistence.login import LoginAdmissionError
        flow=SimpleNamespace(fetch_token=lambda **_kwargs: None,credentials=object())
        app=FastAPI(); app.include_router(google_router,prefix="/api")
        with patch("app.modules.auth.router.consume_state",return_value=None), patch("app.modules.auth.router.oauth_flow",return_value=flow), patch("app.modules.auth.router.create_session",new=AsyncMock(side_effect=LoginAdmissionError("self_signup_disabled","denied"))):
            response=TestClient(app).get("/api/auth/google/callback?state=state&code=code",follow_redirects=False)
        self.assertEqual(response.status_code,307)
        self.assertIn("auth_error=self_signup_disabled",response.headers["location"])
        self.assertNotIn("denied",response.headers["location"])

    def test_microsoft_callback_returns_stable_domain_denial(self):
        from app.modules.auth_persistence.login import LoginAdmissionError
        app=FastAPI(); app.include_router(microsoft_router,prefix="/api")
        with patch("app.modules.auth.microsoft_router.consume_state",return_value="verifier"), patch("app.modules.auth.microsoft_router.exchange_code",new=AsyncMock(return_value={"access_token":"secret"})), patch("app.modules.auth.microsoft_router.create_session",new=AsyncMock(side_effect=LoginAdmissionError("email_domain_not_allowed","denied"))):
            response=TestClient(app).get("/api/auth/microsoft/callback?state=state&code=code",follow_redirects=False)
        self.assertEqual(response.status_code,307)
        self.assertIn("auth_provider=microsoft",response.headers["location"])
        self.assertIn("auth_error=email_domain_not_allowed",response.headers["location"])
        self.assertNotIn("secret",response.headers["location"])

    def test_logout_revokes_server_session_without_returning_tokens(self):
        app=FastAPI(); app.include_router(google_router,prefix="/api")
        with patch("app.modules.auth.router.remove_session") as remove:
            client=TestClient(app)
            client.cookies.set("cam_google_session","opaque")
            response=client.post("/api/auth/google/logout")
        self.assertEqual(response.status_code,200)
        remove.assert_called_once()
        self.assertEqual(response.json(),{"authenticated":False})

    def test_google_callback_enqueues_scan_and_redirects_without_running_it(self):
        cloud = SimpleNamespace(
            active_tenant_id="tenant-a",
            connection_id="connection-a",
            user={"id": "google-a", "email": "a@example.com"},
        )
        flow = SimpleNamespace(fetch_token=lambda **_kwargs: None, credentials=object())
        app = FastAPI()
        app.include_router(google_router, prefix="/api")
        with (
            patch("app.modules.auth.router.consume_state", return_value=None),
            patch("app.modules.auth.router.oauth_flow", return_value=flow),
            patch(
                "app.modules.auth.router.create_session",
                new=AsyncMock(return_value=("session-id", cloud)),
            ),
            patch("app.modules.auth.router.enqueue_google_login_sync") as enqueue,
            patch("app.modules.auth.router.remove_session"),
        ):
            response = TestClient(app).get(
                "/api/auth/google/callback?state=state&code=code",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertIn("google=connected", response.headers["location"])
        enqueue.assert_called_once_with(cloud)

    def test_google_oauth_failure_does_not_enqueue_scan(self):
        def fail_exchange(**_kwargs):
            raise RuntimeError("exchange failed")

        flow = SimpleNamespace(fetch_token=fail_exchange, credentials=object())
        app = FastAPI()
        app.include_router(google_router, prefix="/api")
        with (
            patch("app.modules.auth.router.consume_state", return_value=None),
            patch("app.modules.auth.router.oauth_flow", return_value=flow),
            patch("app.modules.auth.router.enqueue_google_login_sync") as enqueue,
        ):
            response = TestClient(app).get(
                "/api/auth/google/callback?state=state&code=code",
                follow_redirects=False,
            )

        self.assertEqual(response.status_code, 307)
        self.assertIn("auth_error=token_exchange", response.headers["location"])
        enqueue.assert_not_called()

if __name__=="__main__": unittest.main()
