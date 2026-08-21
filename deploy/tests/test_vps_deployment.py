from __future__ import annotations

import subprocess
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
SCRIPTS = ROOT / "scripts"
FRONTEND = SCRIPTS / "deploy-cam-frontend.sh"
BACKEND = SCRIPTS / "cam-rebuild-backend.sh"
COMPOSE = ROOT / "infrastructure" / "docker" / "docker-compose.prod.yml"
IMAGE_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-image-worker.service"
VIDEO_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-video-worker.service"


class SimplifiedProductionDeploymentTest(unittest.TestCase):
    def test_only_two_production_deployment_entrypoints_remain(self) -> None:
        for path in (
            FRONTEND, BACKEND,
        ):
            self.assertTrue(path.is_file(), path)
        for name in (
            "build-frontend-release.sh", "deploy-vps.sh", "rollback-vps.sh",
            "validate-production.sh", "production-release-gate.sh",
        ):
            self.assertFalse((SCRIPTS / name).exists(), name)
        self.assertFalse((ROOT / "deploy" / "bin" / "cam-deploy").exists())

    def test_frontend_native_release_contract(self) -> None:
        source = FRONTEND.read_text()
        for required in (
            'SOURCE_DIR="${CAM_SOURCE_DIR:-$CHECKOUT_ROOT}"',
            "set -Eeuo pipefail", 'DIST="$SOURCE_DIR/apps/client/dist"',
            'CONFIG_PYTHON="$APP_ROOT/current/apps/api/.venv/bin/python"',
            "Committed frontend dist is incomplete.", "build-info.json",
            "nginx -t", "systemctl reload nginx", "--rollback",
            'http://127.0.0.1:8000/version" >/dev/null',
        ):
            self.assertIn(required, source)
        for forbidden in ("docker compose", "systemctl restart creative-asset-manager-api", "npm --prefix", 'git -C "$SOURCE_DIR" archive'):
            self.assertNotIn(forbidden, source)

    def test_backend_native_systemd_contract(self) -> None:
        source = BACKEND.read_text()
        for required in (
            'SOURCE_DIR="${CAM_SOURCE_DIR:-$CHECKOUT_ROOT}"',
            "/opt/creative-asset-manager", "python3 -m venv", "--no-cache-dir",
            "alembic", "upgrade head", "wait_for_endpoint",
            "creative-asset-manager-api.service",
            "creative-asset-manager-image-worker.service",
            "creative-asset-manager-video-worker.service",
            "creative-asset-manager-worker.service", "--rollback",
        ):
            self.assertIn(required, source)
        for forbidden in ("docker compose build api", "docker compose up api", "docker compose up worker", "alembic downgrade"):
            self.assertNotIn(forbidden, source)

    def test_alembic_configuration_includes_the_api_module_path(self) -> None:
        config = (ROOT / "apps" / "api" / "alembic.ini").read_text()
        self.assertIn("prepend_sys_path = %(here)s", config)

    def test_production_compose_is_elasticsearch_only(self) -> None:
        config = yaml.safe_load(COMPOSE.read_text())
        self.assertEqual(set(config["services"]), {"elasticsearch"})
        self.assertEqual(config["services"]["elasticsearch"]["ports"], ["127.0.0.1:9200:9200"])

    def test_worker_units_have_exclusive_roles(self) -> None:
        self.assertIn("WORKER_ROLE=image", IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ROLE=video", VIDEO_UNIT.read_text())

    def test_scripts_have_valid_shell_syntax(self) -> None:
        for script in (FRONTEND, BACKEND):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
