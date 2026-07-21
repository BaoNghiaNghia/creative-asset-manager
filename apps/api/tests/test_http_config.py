import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import create_app
from app.modules.auth.microsoft_router import client_redirect as microsoft_redirect
from app.modules.auth.router import client_redirect as google_redirect


class HttpConfigurationTest(unittest.TestCase):
    @staticmethod
    def production_settings(**overrides) -> Settings:
        values = {
            "APP_ENV": "production",
            "PUBLIC_APP_URL": "https://assets.example.com",
            "CORS_ALLOWED_ORIGINS": "https://assets.example.com",
            "TRUSTED_HOSTS": "api.example.com",
            "API_DOCS_ENABLED": False,
            "DATABASE_URL": "postgresql+psycopg://cam:test@db/cam",
        }
        return Settings(**{**values, **overrides})

    def test_production_docs_are_disabled_and_trusted_host_is_enforced(self) -> None:
        api = create_app(self.production_settings())
        with patch("app.main.init_database"):
            with TestClient(api, base_url="https://api.example.com") as client:
                self.assertEqual(client.get("/health").status_code, 200)
                self.assertEqual(client.get("/docs").status_code, 404)
                self.assertEqual(client.get("/redoc").status_code, 404)
                self.assertEqual(client.get("/openapi.json").status_code, 404)
            with TestClient(api, base_url="https://evil.example.com") as client:
                self.assertEqual(client.get("/health").status_code, 400)

    def test_production_cors_is_exact_or_disabled(self) -> None:
        api = create_app(self.production_settings())
        headers = {
            "Origin": "https://assets.example.com",
            "Access-Control-Request-Method": "GET",
        }
        with patch("app.main.init_database"):
            with TestClient(api, base_url="https://api.example.com") as client:
                allowed = client.options("/health", headers=headers)
                self.assertEqual(
                    allowed.headers.get("access-control-allow-origin"),
                    "https://assets.example.com",
                )
                denied = client.options(
                    "/health", headers={**headers, "Origin": "https://evil.example.com"}
                )
                self.assertNotIn("access-control-allow-origin", denied.headers)

        api = create_app(
            self.production_settings(CORS_ALLOWED_ORIGINS="")
        )
        with patch("app.main.init_database"):
            with TestClient(api, base_url="https://api.example.com") as client:
                response = client.get(
                    "/health", headers={"Origin": "https://assets.example.com"}
                )
                self.assertNotIn("access-control-allow-origin", response.headers)

    def test_oauth_redirects_use_public_app_url(self) -> None:
        settings = self.production_settings()
        with patch("app.modules.auth.router.get_settings", return_value=settings):
            response = google_redirect(google="connected")
            self.assertEqual(
                response.headers["location"],
                "https://assets.example.com?google=connected",
            )
        with patch(
            "app.modules.auth.microsoft_router.get_settings", return_value=settings
        ):
            response = microsoft_redirect(microsoft="connected")
            self.assertEqual(
                response.headers["location"],
                "https://assets.example.com?microsoft=connected",
            )

    def test_frontend_runtime_has_no_absolute_localhost_api_urls(self) -> None:
        client_root = Path(__file__).resolve().parents[2] / "client"
        runtime_files = [
            *client_root.joinpath("app").rglob("*.ts"),
            *client_root.joinpath("app").rglob("*.tsx"),
            *client_root.joinpath("features").rglob("*.ts"),
            *client_root.joinpath("features").rglob("*.tsx"),
        ]
        for path in runtime_files:
            source = path.read_bytes()
            with self.subTest(path=path):
                self.assertNotIn(b"http://localhost", source)
                self.assertNotIn(b"http://127.0.0.1", source)
        vite = client_root.joinpath("vite.config.ts").read_bytes()
        self.assertIn(b'"/api"', vite)
        self.assertIn(b'"http://localhost:8000"', vite)


if __name__ == "__main__":
    unittest.main()
