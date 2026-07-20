import unittest
from types import SimpleNamespace
from unittest.mock import patch

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

if __name__=="__main__": unittest.main()
