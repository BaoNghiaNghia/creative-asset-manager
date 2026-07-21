import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.core.environment import load_development_environment


class EnvironmentLoadingTest(unittest.TestCase):
    def test_production_declared_in_process_never_loads_dotenv(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("ARBITRARY_SECRET=from-file\n", encoding="utf-8")
            with patch.dict(os.environ, {"APP_ENV": "production"}, clear=True):
                self.assertFalse(load_development_environment(path))
                self.assertNotIn("ARBITRARY_SECRET", os.environ)

    def test_production_declared_in_file_is_not_loaded(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "APP_ENV=production\nARBITRARY_SECRET=from-file\n", encoding="utf-8"
            )
            with patch.dict(os.environ, {}, clear=True):
                self.assertFalse(load_development_environment(path))
                self.assertEqual(os.environ["APP_ENV"], "production")
                self.assertNotIn("ARBITRARY_SECRET", os.environ)

    def test_fixed_development_file_loads_without_overriding_process_env(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text(
                "APP_ENV=development\nPUBLIC_APP_URL=http://from-file:5173\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"PUBLIC_APP_URL": "http://from-process:5173"},
                clear=True,
            ):
                self.assertTrue(load_development_environment(path))
                self.assertEqual(
                    os.environ["PUBLIC_APP_URL"], "http://from-process:5173"
                )


if __name__ == "__main__":
    unittest.main()
