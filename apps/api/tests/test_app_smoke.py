import unittest

from unittest.mock import patch
from fastapi.testclient import TestClient

from app.main import app


class AppSmokeTest(unittest.TestCase):
    def test_existing_health_route_still_starts(self) -> None:
        with patch("app.main.init_database"):
            with TestClient(app) as client:
                response = client.get("/health")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})


if __name__ == "__main__":
    unittest.main()
