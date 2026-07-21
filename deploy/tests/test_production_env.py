from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HELPER = ROOT / "deploy" / "tools" / "production_env.py"
SCRIPT = ROOT / "deploy" / "bin" / "cam-deploy"
API_ROOT = ROOT / "apps" / "api"

spec = importlib.util.spec_from_file_location("production_env", HELPER)
production_env = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(production_env)


class ProductionEnvironmentToolTest(unittest.TestCase):
    def write_environment(self, directory: Path, database_url: str) -> Path:
        path = directory / "production.env"
        path.write_text(
            "\n".join(
                (
                    "APP_ENV=production",
                    "PUBLIC_APP_URL=https://assets.test.example",
                    "CORS_ALLOWED_ORIGINS=",
                    "TRUSTED_HOSTS=assets.test.example",
                    "API_DOCS_ENABLED=false",
                    "APP_VERSION=1.2.3",
                    "BUILD_COMMIT=abc123",
                    "PROXY_HEADERS_ENABLED=true",
                    "PROXY_TRUSTED_IPS=127.0.0.1/32",
                    "AUTH_COOKIE_SECURE=true",
                    f"DATABASE_URL={database_url}",
                )
            )
            + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)
        return path

    def run_check(self, path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            (
                sys.executable,
                str(HELPER),
                "check",
                "--env-file",
                str(path),
                "--expected-owner-uid",
                str(os.getuid()),
                "--api-root",
                str(API_ROOT),
            ),
            text=True,
            capture_output=True,
            check=False,
        )

    def test_valid_configuration_passes_without_echoing_database_secret(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret = "unit-test-database-secret"
            path = self.write_environment(
                Path(temporary),
                f"postgresql+psycopg://cam:{secret}@127.0.0.1:5432/cam",
            )
            result = self.run_check(path)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("Production configuration is valid", result.stdout)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_invalid_configuration_reports_only_safe_field_information(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            secret = "unit-test-sqlite-secret"
            path = self.write_environment(
                Path(temporary),
                f"sqlite:///{secret}.db",
            )
            result = self.run_check(path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("Production settings are invalid", result.stderr)
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_placeholder_failure_names_key_but_never_value(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            marker = "REPLACE_PRIVATE_DATABASE_VALUE"
            path = self.write_environment(
                Path(temporary),
                f"postgresql+psycopg://cam:{marker}@127.0.0.1:5432/cam",
            )
            result = self.run_check(path)
        self.assertEqual(result.returncode, 2)
        self.assertIn("DATABASE_URL", result.stderr)
        self.assertNotIn(marker, result.stdout + result.stderr)

    def test_environment_values_are_data_not_shell_code(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / "must-not-exist"
            path = directory / "environment"
            path.write_text(
                f"BUILD_COMMIT=$(touch {marker})\n",
                encoding="utf-8",
            )
            values = production_env.parse_environment_file(path)
            self.assertEqual(values["BUILD_COMMIT"], f"$(touch {marker})")
            self.assertFalse(marker.exists())

    def test_deployment_script_has_valid_shell_syntax_and_no_db_downgrade(self) -> None:
        result = subprocess.run(
            ("bash", "-n", str(SCRIPT)),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        script = SCRIPT.read_text(encoding="utf-8").lower()
        self.assertNotIn("alembic downgrade", script)
        self.assertNotIn("set -x", script)
        help_result = subprocess.run(
            (str(SCRIPT), "help"),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(help_result.returncode, 0, help_result.stderr)


if __name__ == "__main__":
    unittest.main()
