from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
COMMON = ROOT / "scripts" / "lib" / "deployment-common.sh"
BUILD = ROOT / "scripts" / "build-frontend-release.sh"
DEPLOY = ROOT / "scripts" / "deploy-vps.sh"
ROLLBACK = ROOT / "scripts" / "rollback-vps.sh"
VALIDATE = ROOT / "scripts" / "validate-production.sh"
COMPOSE = ROOT / "infrastructure" / "docker" / "docker-compose.prod.yml"
NGINX = ROOT / "infrastructure" / "nginx" / "creative-asset-manager.conf"
DIST = ROOT / "apps" / "client" / "dist"


class CommittedFrontendDeploymentTest(unittest.TestCase):
    def run_scan(self, dist: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ("bash", "-c", 'source "$1"; scan_frontend_dist "$2"', "test", str(COMMON), str(dist)),
            text=True,
            capture_output=True,
            check=False,
        )

    def make_dist(self, root: Path, javascript: str = "console.log('safe')") -> Path:
        dist = root / "dist"
        (dist / "assets").mkdir(parents=True)
        (dist / "index.html").write_text("<div id='root'></div>", encoding="utf-8")
        (dist / "build-meta.json").write_text(
            '{"build_commit":"abc1234","build_utc_timestamp":"2026-01-01T00:00:00Z","frontend_version":"0.1.0"}',
            encoding="utf-8",
        )
        (dist / "assets" / "index-abc.js").write_text(javascript, encoding="utf-8")
        return dist

    def test_shell_scripts_have_strict_syntax(self) -> None:
        for script in (COMMON, BUILD, DEPLOY, ROLLBACK, VALIDATE):
            with self.subTest(script=script.name):
                result = subprocess.run(("bash", "-n", str(script)), capture_output=True, text=True)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_build_script_refuses_root(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            fake_bin = Path(temporary)
            fake_id = fake_bin / "id"
            fake_id.write_text(
                "#!/bin/sh\n[ \"$1\" = -u ] && { echo 0; exit 0; }\nexec /usr/bin/id \"$@\"\n",
                encoding="utf-8",
            )
            fake_id.chmod(0o755)
            env = os.environ.copy()
            env["PATH"] = f"{fake_bin}:{env['PATH']}"
            result = subprocess.run((str(BUILD),), env=env, text=True, capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("must not run as root", result.stderr)

    def test_missing_dist_and_marker_fail_closed(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            result = self.run_scan(Path(temporary) / "missing")
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("missing index.html", result.stderr)

            dist = Path(temporary) / "partial"
            (dist / "assets").mkdir(parents=True)
            (dist / "index.html").write_text("ok", encoding="utf-8")
            result = self.run_scan(dist)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn("build marker is missing", result.stderr)

    def test_secret_scan_rejects_local_and_secret_like_values(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = self.make_dist(Path(temporary), "fetch('http://localhost:8000/api')")
            result = self.run_scan(dist)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Forbidden local or secret-like value", result.stderr)

    def test_safe_dist_passes_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = self.make_dist(Path(temporary), "fetch('/api/assets')")
            result = self.run_scan(dist)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_deploy_defaults_and_safe_ordering(self) -> None:
        script = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('REPO_ROOT="/home/desify/creative-asset-manager"', script)
        self.assertIn('FRONTEND_ROOT="/var/www/creative-asset-manager"', script)
        self.assertIn("git merge --ff-only", script)
        self.assertNotIn("git reset --hard", script)
        self.assertLess(script.index('validate_database_connection'), script.index('--profile migration run --rm migration'))
        self.assertLess(script.index('--profile migration run --rm migration'), script.index('up -d --no-build api worker'))
        self.assertLess(script.index("API readiness failed"), script.index('current.new.$$'))
        self.assertIn("sudo mv -Tf", script)

    def test_compose_and_images_match_target_architecture(self) -> None:
        config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        self.assertEqual(set(config["services"]), {"api", "worker", "migration", "elasticsearch"})
        self.assertNotIn("postgres", config["services"])
        self.assertIn("host.docker.internal:host-gateway", config["x-backend-common"]["extra_hosts"])
        self.assertEqual(config["services"]["api"]["ports"], ["127.0.0.1:8000:8000"])
        self.assertNotIn("ports", config["services"]["worker"])
        for name in ("api.Dockerfile", "worker.Dockerfile"):
            dockerfile = (COMPOSE.parent / name).read_text(encoding="utf-8").lower()
            self.assertNotIn("node", dockerfile)
            self.assertIn("user 10001:10001", dockerfile)
            self.assertNotIn("apps/client", dockerfile)

    def test_nginx_proxy_spa_and_cache_policy(self) -> None:
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("proxy_pass http://creative_asset_manager_api;", config)
        self.assertIn("try_files $uri $uri/ /index.html;", config)
        self.assertIn("location = /build-meta.json", config)
        self.assertIn("max-age=31536000, immutable", config)
        self.assertIn("no-store, no-cache, must-revalidate", config)

    def test_committed_frontend_has_marker_and_no_forbidden_values(self) -> None:
        self.assertTrue((DIST / "index.html").is_file())
        self.assertTrue((DIST / "build-meta.json").is_file())
        result = self.run_scan(DIST)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
