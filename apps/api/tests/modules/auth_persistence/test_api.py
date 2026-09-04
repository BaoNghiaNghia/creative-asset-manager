import unittest
from urllib.parse import urlencode
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
            self.assertTrue({"authenticated", "user"}.issubset(response.json()))
            self.assertNotIn("plain-access-secret",response.text)
            self.assertNotIn("plain-refresh-secret",response.text)

    def test_google_callback_returns_stable_signup_denial(self):
        from app.modules.auth_persistence.login import LoginAdmissionError
        flow=SimpleNamespace(fetch_token=lambda **_kwargs: None,credentials=object())
        app=FastAPI(); app.include_router(google_router,prefix="/api")
        with patch("app.modules.auth.router.consume_state_details",return_value=(None, "application_login")), patch("app.modules.auth.router.oauth_flow",return_value=flow), patch("app.modules.auth.router.create_session",new=AsyncMock(side_effect=LoginAdmissionError("self_signup_disabled","denied"))):
            response=TestClient(app).get("/api/auth/google/callback?state=state&code=code",follow_redirects=False)
        self.assertEqual(response.status_code,307)
        self.assertIn("auth_error=self_signup_disabled",response.headers["location"])
        self.assertNotIn("denied",response.headers["location"])

    def test_microsoft_callback_returns_stable_domain_denial(self):
        from app.modules.auth_persistence.login import LoginAdmissionError
        app=FastAPI(); app.include_router(microsoft_router,prefix="/api")
        with patch("app.modules.auth.microsoft_router.consume_state_details",return_value=("verifier", "application_login")), patch("app.modules.auth.microsoft_router.exchange_code",new=AsyncMock(return_value={"access_token":"secret"})), patch("app.modules.auth.microsoft_router.create_session",new=AsyncMock(side_effect=LoginAdmissionError("email_domain_not_allowed","denied"))):
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
            patch("app.modules.auth.router.consume_state_details", return_value=(None, "application_login")),
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
        self.assertIn("google=signed_in", response.headers["location"])
        enqueue.assert_not_called()

    def test_google_drive_connect_enqueues_scan_only_for_drive_intent(self):
        cloud = SimpleNamespace(active_tenant_id="tenant-a", connection_id="connection-a", user={"id": "google-a"})
        flow = SimpleNamespace(fetch_token=lambda **_kwargs: None, credentials=object())
        app = FastAPI(); app.include_router(google_router, prefix="/api")
        fake_user = SimpleNamespace(id="user-a", status="active")
        fake_authz = SimpleNamespace(membership_id="membership-a", permissions=frozenset({"assets.manage"}))
        class FakeDb:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def get(self, _model, _key): return fake_user
            def scalar(self, _statement): return None
        with (
            patch("app.modules.auth.router.consume_state_details", return_value=(None, "drive_connect:tenant-a:-:user-a")),
            patch("app.modules.auth.router.oauth_flow", return_value=flow),
            patch("app.modules.auth.router.SessionLocal", return_value=FakeDb()),
            patch("app.modules.auth.router.TenantAuthorizationService.get_effective_permissions", return_value=fake_authz),
            patch("app.modules.auth.router.resolve_granted_scopes", return_value=("https://www.googleapis.com/auth/drive",)),
            patch("app.modules.auth.router.persist_drive_connection", new=AsyncMock(return_value=cloud)) as persist,
            patch("app.modules.auth.router.enqueue_google_login_sync") as enqueue,
        ):
            response = TestClient(app).get("/api/auth/google/callback?state=state&code=code", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("google=source_connected", response.headers["location"])
        self.assertEqual(persist.await_args.kwargs["tenant_id"], "tenant-a")
        self.assertEqual(persist.await_args.kwargs["user_id"], "user-a")
        enqueue.assert_called_once_with(cloud)

    def test_google_drive_callback_ignores_unrelated_application_cookie(self):
        flow = SimpleNamespace(fetch_token=lambda **_kwargs: None, credentials=object())
        cloud = SimpleNamespace(active_tenant_id="tenant-a", connection_id="connection-a", user={"id": "google-a"})
        app = FastAPI(); app.include_router(google_router, prefix="/api")
        class FakeDb:
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def get(self, _model, key): return SimpleNamespace(id=key, status="active")
            def scalar(self, _statement): return None
        authz = SimpleNamespace(membership_id="member-a", permissions=frozenset({"assets.manage"}))
        with (
            patch("app.modules.auth.router.consume_state_details", return_value=(None, "drive_connect:tenant-a:-:user-a")),
            patch("app.modules.auth.router.oauth_flow", return_value=flow),
            patch("app.modules.auth.router.SessionLocal", return_value=FakeDb()),
            patch("app.modules.auth.router.TenantAuthorizationService.get_effective_permissions", return_value=authz),
            patch("app.modules.auth.router.resolve_granted_scopes", return_value=("https://www.googleapis.com/auth/drive",)),
            patch("app.modules.auth.router.persist_drive_connection", new=AsyncMock(return_value=cloud)),
            patch("app.modules.auth.router.enqueue_google_login_sync"),
        ):
            client = TestClient(app)
            client.cookies.set("cam_google_session", "tenant-b-session")
            response = client.get("/api/auth/google/callback?state=state&code=code", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("google=source_connected", response.headers["location"])

    def test_drive_authorization_forces_account_chooser(self):
        from urllib.parse import parse_qs, urlparse
        from app.modules.auth.router import _authorization_response
        flow = SimpleNamespace(
            code_verifier=None,
            authorization_url=lambda **options: (
                "https://accounts.google.com/o/oauth2/auth?" + urlencode(options),
                "oauth-state",
            ),
        )
        with (
            patch("app.modules.auth.router.remember_state"),
            patch("app.modules.auth.router.clear_provider_session_cookies") as clear_session,
        ):
            response = _authorization_response(
                flow,
                redirect_intent="drive_connect:tenant-a:-:user-a",
                prompt="consent select_account",
            )
        clear_session.assert_not_called()
        params = parse_qs(urlparse(response.headers["location"]).query)
        self.assertEqual(params["prompt"], ["consent select_account"])
        self.assertEqual(params["access_type"], ["offline"])
        self.assertEqual(params["include_granted_scopes"], ["true"])
        self.assertNotIn("login_hint", params)

    def test_google_oauth_failure_does_not_enqueue_scan(self):
        def fail_exchange(**_kwargs):
            raise RuntimeError("exchange failed")

        flow = SimpleNamespace(fetch_token=fail_exchange, credentials=object())
        app = FastAPI()
        app.include_router(google_router, prefix="/api")
        with (
            patch("app.modules.auth.router.consume_state_details", return_value=(None, "application_login")),
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


    def test_microsoft_source_callback_preserves_google_application_cookie(self):
        app = FastAPI(); app.include_router(microsoft_router, prefix="/api")
        connection = SimpleNamespace(id="connection-onedrive")
        with (
            patch("app.modules.auth.microsoft_router.consume_state_details", return_value=("verifier", "onedrive_connect:tenant-a:-:user-a")),
            patch("app.modules.auth.microsoft_router._reauthorize_source") as reauthorize,
            patch("app.modules.auth.microsoft_router.exchange_code", new=AsyncMock(return_value={"access_token": "secret"})),
            patch("app.modules.auth.microsoft_router.persist_source_connection", new=AsyncMock(return_value=(connection, {}))) as persist,
            patch("app.modules.auth.microsoft_router.register_onedrive_source", new=AsyncMock(return_value=SimpleNamespace(id="source-onedrive"))),
            patch("app.modules.auth.microsoft_router.create_session", new=AsyncMock()) as create_session,
        ):
            client = TestClient(app)
            client.cookies.set("cam_google_session", "google-application-session")
            response = client.get("/api/auth/microsoft/callback?state=state&code=code", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("microsoft=source_connected", response.headers["location"])
        self.assertIn("source=onedrive", response.headers["location"])
        self.assertNotIn("cam_microsoft_session", response.headers.get("set-cookie", ""))
        self.assertNotIn("cam_google_session=;", response.headers.get("set-cookie", ""))
        create_session.assert_not_called()
        reauthorize.assert_called_once()
        self.assertEqual(persist.await_args.kwargs["tenant_id"], "tenant-a")

    def test_microsoft_sharepoint_source_callback_never_sets_application_session(self):
        app = FastAPI(); app.include_router(microsoft_router, prefix="/api")
        connection = SimpleNamespace(id="connection-sharepoint")
        with (
            patch("app.modules.auth.microsoft_router.consume_state_details", return_value=("verifier", "sharepoint_connect:tenant-a:-:user-a")),
            patch("app.modules.auth.microsoft_router._reauthorize_source"),
            patch("app.modules.auth.microsoft_router.exchange_code", new=AsyncMock(return_value={"access_token": "secret"})),
            patch("app.modules.auth.microsoft_router.persist_source_connection", new=AsyncMock(return_value=(connection, {}))),
            patch("app.modules.auth.microsoft_router.create_session", new=AsyncMock()) as create_session,
        ):
            response = TestClient(app).get("/api/auth/microsoft/callback?state=state&code=code", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("source=sharepoint", response.headers["location"])
        self.assertNotIn("cam_microsoft_session", response.headers.get("set-cookie", ""))
        create_session.assert_not_called()

    def test_microsoft_source_token_exchange_failure_preserves_application_cookies(self):
        app = FastAPI(); app.include_router(microsoft_router, prefix="/api")
        with (
            patch("app.modules.auth.microsoft_router.consume_state_details", return_value=("verifier", "onedrive_connect:tenant-a:-:user-a")),
            patch("app.modules.auth.microsoft_router._reauthorize_source"),
            patch("app.modules.auth.microsoft_router.exchange_code", new=AsyncMock(side_effect=RuntimeError("failed"))),
        ):
            response = TestClient(app).get("/api/auth/microsoft/callback?state=state&code=code", follow_redirects=False)
        self.assertEqual(response.status_code, 307)
        self.assertIn("auth_error=token_exchange_failed", response.headers["location"])
        self.assertNotIn("cam_microsoft_session", response.headers.get("set-cookie", ""))

    def test_microsoft_intent_scopes_and_authority_are_separated(self):
        from app.providers.microsoft.auth import (
            APPLICATION_LOGIN_SCOPES, ONEDRIVE_SOURCE_SCOPES,
            SHAREPOINT_SOURCE_SCOPES, authority_for_intent, scopes_for_intent,
        )
        self.assertEqual(scopes_for_intent("application_login"), APPLICATION_LOGIN_SCOPES)
        self.assertEqual(scopes_for_intent("onedrive_connect"), ONEDRIVE_SOURCE_SCOPES)
        self.assertEqual(scopes_for_intent("sharepoint_connect"), SHAREPOINT_SOURCE_SCOPES)
        self.assertNotIn("Sites.Read.All", APPLICATION_LOGIN_SCOPES)
        self.assertNotIn("Sites.Read.All", ONEDRIVE_SOURCE_SCOPES)
        self.assertEqual(authority_for_intent("onedrive_connect"), "common")

if __name__=="__main__": unittest.main()
