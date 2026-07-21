import unittest

from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app


class AppSmokeTest(unittest.TestCase):
    def test_database_is_disposed_when_another_shutdown_hook_fails(self) -> None:
        with (
            patch("app.main.init_database"),
            patch("app.main.dispose_database") as dispose_database,
            patch("app.main.SHADOW_SEARCH.start"),
            patch(
                "app.main.SHADOW_SEARCH.shutdown",
                new_callable=AsyncMock,
                side_effect=RuntimeError("shutdown failed"),
            ),
        ):
            with self.assertRaisesRegex(RuntimeError, "shutdown failed"):
                with TestClient(app):
                    pass

        dispose_database.assert_called_once_with()

    def test_existing_health_route_still_starts(self) -> None:
        with (
            patch("app.main.init_database"),
            patch("app.main.dispose_database") as dispose_database,
        ):
            with TestClient(app) as client:
                response = client.get("/health")

        dispose_database.assert_called_once_with()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
