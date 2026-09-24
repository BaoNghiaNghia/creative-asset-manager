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
SECONDARY_IMAGE_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-image-worker-2.service"
TERTIARY_IMAGE_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-image-worker-3.service"
QUATERNARY_IMAGE_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-image-worker-4.service"
QUINARY_IMAGE_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-image-worker-5.service"
VIDEO_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-video-worker.service"
VISUAL_ENCODER_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-visual-encoder.service"
VISUAL_WORKER_UNIT = ROOT / "deploy" / "systemd" / "creative-asset-manager-visual-worker.service"
NGINX_CONFIG = ROOT / "infrastructure" / "nginx" / "creative-asset-manager.conf"


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
            "favicon.svg", "favicon.ico", "favicon-32x32.png",
            "apple-touch-icon.png", "app-icon-192.png", "app-icon-512.png",
            "site.webmanifest",
            'CHECKED_OUT_COMMIT="$(git -C "$SOURCE_DIR" rev-parse --verify HEAD^{commit})"',
            'Requested commit does not match the checked-out HEAD.',
            'Committed build-info.json is not an ancestor of the requested commit.',
            'Frontend source changed after build-info.json was generated.',
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
            "/opt/creative-asset-manager", "python3", "-m venv", "--no-cache-dir",
            "alembic", "upgrade head", "wait_for_endpoint",
            "diagnose_endpoint_failure", "Health diagnostic for $label",
            'exec 3>&1 4>&2',
            'tee -a "$LOG_FILE" >&3',
            'tee -a "$LOG_FILE" >&4',
            'Waiting for $label (attempt $attempt/30)',
            '$label is healthy (attempt $attempt/30)',
            "creative-asset-manager-api.service",
            "creative-asset-manager-image-worker.service",
            "creative-asset-manager-image-worker-2.service",
            "creative-asset-manager-image-worker-3.service",
            "creative-asset-manager-image-worker-4.service",
            "creative-asset-manager-image-worker-5.service",
            "creative-asset-manager-video-worker.service",
            "creative-asset-manager-visual-worker.service",
            "Preparing persistent isolated visual encoder runtime",
            "VISUAL_ENCODER_RUNTIME_DIR",
            "requirements.sha256",
            "$VISUAL_ENCODER_RUNTIME_DIR/bin/python",
            "visual encoder $endpoint",
            "Restarting isolated visual encoder",
            "creative-asset-manager-visual-encoder.service",
            "creative-asset-manager-worker.service", "--rollback",
        ):
            self.assertIn(required, source)
        for forbidden in ("docker compose build api", "docker compose up api", "docker compose up worker", "alembic downgrade"):
            self.assertNotIn(forbidden, source)

    def test_default_profile_enables_exactly_three_image_workers(self) -> None:
        source = BACKEND.read_text()
        self.assertIn("Stopping/disabling optional image workers 4 and 5", source)
        enable_start = source.index('"Enabling production native services"')
        enable_end = source.index('"Replacing fixed Inventory V4.1 timers', enable_start)
        enable_section = source[enable_start:enable_end]
        for worker in (
            "creative-asset-manager-image-worker.service",
            "creative-asset-manager-image-worker-2.service",
            "creative-asset-manager-image-worker-3.service",
        ):
            self.assertIn(worker, enable_section)
        self.assertNotIn("creative-asset-manager-image-worker-4.service", enable_section)
        self.assertNotIn("creative-asset-manager-image-worker-5.service", enable_section)

    def test_visual_encoder_reserves_two_cpu_threads_without_process_replication(self) -> None:
        unit = VISUAL_ENCODER_UNIT.read_text()
        self.assertIn("CPUQuota=200%", unit)
        self.assertIn("MemoryMax=2300M", unit)
        self.assertNotIn("--workers", unit)

    def test_alembic_configuration_includes_the_api_module_path(self) -> None:
        config = (ROOT / "apps" / "api" / "alembic.ini").read_text()
        self.assertIn("prepend_sys_path = %(here)s", config)

    def test_nginx_csp_allows_only_the_approved_video_cdn_origin(self) -> None:
        config = NGINX_CONFIG.read_text()
        self.assertIn(
            "media-src 'self' blob: https://cam-r2-original-video.baonghia-kht.workers.dev;",
            config,
        )
        self.assertNotIn("media-src *", config)
        self.assertNotIn("https://*.workers.dev", config)

    def test_production_compose_is_elasticsearch_only(self) -> None:
        config = yaml.safe_load(COMPOSE.read_text())
        self.assertEqual(set(config["services"]), {"elasticsearch"})
        self.assertEqual(config["services"]["elasticsearch"]["ports"], ["127.0.0.1:9200:9200"])

    def test_worker_units_have_exclusive_roles(self) -> None:
        self.assertIn("WORKER_ROLE=image", IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ROLE=image", SECONDARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ID=creativeasset-image-secondary", SECONDARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_HEALTH_PORT=8083", SECONDARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ROLE=image", TERTIARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ID=creativeasset-image-tertiary", TERTIARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_HEALTH_PORT=8084", TERTIARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ID=creativeasset-image-quaternary", QUATERNARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_HEALTH_PORT=8085", QUATERNARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ID=creativeasset-image-quinary", QUINARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_HEALTH_PORT=8086", QUINARY_IMAGE_UNIT.read_text())
        self.assertIn("WORKER_ROLE=video", VIDEO_UNIT.read_text())
        self.assertIn("WORKER_ROLE=visual", VISUAL_WORKER_UNIT.read_text())
        self.assertIn("WORKER_ID=creativeasset-visual-index", VISUAL_WORKER_UNIT.read_text())
        self.assertIn("WORKER_HEALTH_PORT=8087", VISUAL_WORKER_UNIT.read_text())
        self.assertIn("WantedBy=multi-user.target", VISUAL_WORKER_UNIT.read_text())

    def test_scripts_have_valid_shell_syntax(self) -> None:
        for script in (FRONTEND, BACKEND):
            result = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
