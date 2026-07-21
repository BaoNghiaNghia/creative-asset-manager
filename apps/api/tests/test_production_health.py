from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Request
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.core.config import Settings
from app.core.health import elasticsearch_is_ready, readiness_report
from app.main import create_app


class ProductionHealthTest(unittest.TestCase):
    def request(self, settings: Settings, path: str):
        api = create_app(settings)
        with (
            patch("app.main.init_database"),
            patch("app.main.dispose_database"),
            patch("app.main.SHADOW_SEARCH.start"),
            patch("app.main.SHADOW_SEARCH.shutdown", new_callable=AsyncMock),
        ):
            with TestClient(api) as client:
                return client.get(path)

    def test_live_and_version_do_not_expose_configuration(self) -> None:
        settings = Settings(APP_VERSION="1.2.3", BUILD_COMMIT="abc123")
        live = self.request(settings, "/live")
        version = self.request(settings, "/version")
        self.assertEqual(live.status_code, 200)
        self.assertEqual(live.json(), {"status": "ok"})
        self.assertEqual(
            version.json(),
            {"version": "1.2.3", "commit": "abc123"},
        )
        body = version.text.lower()
        self.assertNotIn("database", body)
        self.assertNotIn("elasticsearch", body)
        self.assertNotIn("token", body)

    def test_ready_when_postgresql_is_available_and_search_is_disabled(self) -> None:
        settings = Settings()
        with (
            patch("app.core.health.postgresql_is_ready", return_value=True),
            patch("app.core.health.elasticsearch_is_ready") as elasticsearch,
        ):
            response = self.request(settings, "/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.json(),
            {
                "status": "ready",
                "dependencies": {
                    "postgresql": "available",
                    "elasticsearch": "disabled",
                },
            },
        )
        elasticsearch.assert_not_called()

    def test_not_ready_hides_postgresql_failure_details(self) -> None:
        settings = Settings()
        with patch(
            "app.core.health.validate_database_connection",
            side_effect=RuntimeError(
                "postgresql://admin:secret-password@private-db/internal"
            ),
        ):
            result = readiness_report(settings)
        self.assertEqual(result.status_code, 503)
        self.assertEqual(result.payload["status"], "not_ready")
        self.assertEqual(result.payload["dependencies"]["postgresql"], "unavailable")
        self.assertNotIn("secret-password", str(result.payload))

    def test_elasticsearch_failure_is_degraded_only_when_enabled(self) -> None:
        settings = Settings(
            ELASTICSEARCH_V2_ENABLED=True,
            ELASTICSEARCH_URL="https://search.internal.example",
        )
        with (
            patch("app.core.health.postgresql_is_ready", return_value=True),
            patch("app.core.health.elasticsearch_is_ready", return_value=False),
        ):
            response = self.request(settings, "/ready")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "status": "degraded",
                "dependencies": {
                    "postgresql": "available",
                    "elasticsearch": "unavailable",
                },
            },
        )

    def test_all_required_dependencies_available_is_ready(self) -> None:
        settings = Settings(
            ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED=True,
            ELASTICSEARCH_URL="https://search.internal.example",
        )
        with (
            patch("app.core.health.postgresql_is_ready", return_value=True),
            patch("app.core.health.elasticsearch_is_ready", return_value=True),
        ):
            response = self.request(settings, "/ready")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ready")
        self.assertEqual(
            response.json()["dependencies"]["elasticsearch"],
            "available",
        )

    def test_elasticsearch_cluster_health_requires_green_or_yellow(self) -> None:
        settings = Settings(
            ELASTICSEARCH_V2_ENABLED=True,
            ELASTICSEARCH_URL="https://search.internal.example",
        )
        response = MagicMock()
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.__enter__.return_value.get.return_value = response

        response.json.return_value = {"status": "red", "timed_out": False}
        with patch("app.core.health.httpx.Client", return_value=client):
            self.assertFalse(elasticsearch_is_ready(settings))

        response.json.return_value = {"status": "yellow", "timed_out": False}
        with patch("app.core.health.httpx.Client", return_value=client):
            self.assertTrue(elasticsearch_is_ready(settings))

    def test_proxy_headers_are_accepted_only_from_configured_networks(self) -> None:
        settings = Settings(
            PROXY_HEADERS_ENABLED=True,
            PROXY_TRUSTED_IPS="127.0.0.1/32",
        )
        api = create_app(settings)

        @api.get("/scope")
        def scope(request: Request):
            return {
                "scheme": request.url.scheme,
                "client": request.client.host,
            }

        headers = {
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": "203.0.113.8",
        }
        with (
            patch("app.main.init_database"),
            patch("app.main.dispose_database"),
            patch("app.main.SHADOW_SEARCH.start"),
            patch("app.main.SHADOW_SEARCH.shutdown", new_callable=AsyncMock),
        ):
            with TestClient(api, client=("127.0.0.1", 50000)) as client:
                trusted = client.get("/scope", headers=headers)
            with TestClient(api, client=("192.0.2.10", 50000)) as client:
                untrusted = client.get("/scope", headers=headers)

        self.assertEqual(
            trusted.json(),
            {"scheme": "https", "client": "203.0.113.8"},
        )
        self.assertEqual(
            untrusted.json(),
            {"scheme": "http", "client": "192.0.2.10"},
        )

    def test_proxy_and_build_settings_fail_closed(self) -> None:
        for values in (
            {"PROXY_HEADERS_ENABLED": True, "PROXY_TRUSTED_IPS": ""},
            {"PROXY_HEADERS_ENABLED": True, "PROXY_TRUSTED_IPS": "*"},
            {"PROXY_HEADERS_ENABLED": True, "PROXY_TRUSTED_IPS": "0.0.0.0/0"},
            {"PROXY_HEADERS_ENABLED": True, "PROXY_TRUSTED_IPS": "proxy.local"},
            {"APP_VERSION": "bad version"},
            {"BUILD_COMMIT": "commit/with/slash"},
            {"HEALTHCHECK_TIMEOUT_SECONDS": 0},
        ):
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    Settings(**values)


if __name__ == "__main__":
    unittest.main()
