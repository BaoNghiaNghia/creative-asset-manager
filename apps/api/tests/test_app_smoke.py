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

    def test_inventory_configuration_routes_are_registered_while_automation_is_disabled(self) -> None:
        from app.core.config import Settings
        from app.main import create_app

        api = create_app(Settings(INVENTORY_AUTOMATION_ENABLED=False))
        paths = {getattr(route, "path", "") for route in api.routes}
        self.assertIn("/api/inventory/configuration/ai-credential", paths)

    def test_lifespan_uses_the_settings_attached_to_that_app_instance(self) -> None:
        from app.core.config import Settings
        from app.main import create_app

        settings = Settings(INVENTORY_AUTOMATION_ENABLED=False, APP_VERSION="test-settings")
        with (
            patch("app.main.init_database") as init_database,
            patch("app.main.dispose_database"),
            patch("app.main.SHADOW_SEARCH.start"),
            patch("app.main.SHADOW_SEARCH.shutdown", new_callable=AsyncMock),
        ):
            with TestClient(create_app(settings)):
                pass

        init_database.assert_called_once_with(settings)



if __name__ == "__main__":
    unittest.main()
