from __future__ import annotations

import os
import json
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
CI = ROOT / ".github" / "workflows" / "ci.yml"
GITIGNORE = ROOT / ".gitignore"
GITATTRIBUTES = ROOT / ".gitattributes"
CLIENT_ROOT = ROOT / "apps" / "client"
BUILD_INFO = DIST / "build-info.json"


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
        (dist / "build-info.json").write_text(
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
        forbidden = (
            "fetch('http://localhost:8000/api')",
            "fetch('http://127.0.0.1:8000/api')",
            "DATABASE_URL=postgresql://cam:password@database/assets",
            "GEMINI_API_KEY=AIza12345678901234567890",
            "OPENAI_API_KEY=sk-1234567890abcdefghijkl",
            "GOOGLE_CLIENT_SECRET=google-secret",
            "MICROSOFT_CLIENT_SECRET=microsoft-secret",
            "-----BEGIN RSA PRIVATE KEY-----",
            "OAUTH_TOKEN=ya29.abcdefghijklmnopqrstuvwxyz",
        )
        for value in forbidden:
            with self.subTest(kind=value.split("=", 1)[0]):
                with tempfile.TemporaryDirectory() as temporary:
                    dist = self.make_dist(Path(temporary), value)
                    result = self.run_scan(dist)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(
                    "Forbidden local or secret-like value",
                    result.stderr,
                )

    def test_source_maps_are_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = self.make_dist(Path(temporary))
            (dist / "assets" / "index.js.map").write_text("{}", encoding="utf-8")
            result = self.run_scan(dist)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source maps are disabled", result.stderr)

    def test_safe_dist_passes_scan(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            dist = self.make_dist(Path(temporary), "fetch('/api/assets')")
            result = self.run_scan(dist)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_release_script_pipeline_and_push_are_explicit(self) -> None:
        script = BUILD.read_text(encoding="utf-8")
        expected = (
            "npm --prefix apps/client ci",
            "npm --prefix apps/client test",
            "npm --prefix apps/client run typecheck",
            "npm --prefix apps/client run build",
            'scan_frontend_dist "$DIST"',
        )
        positions = [script.index(value) for value in expected]
        self.assertEqual(positions, sorted(positions))
        self.assertIn("--push requires --commit", script)
        self.assertIn('if [[ "$PUSH" == true ]]', script)
        self.assertIn('[[ "$(id -un)" == "baonghia" ]]', script)

    def test_only_client_dist_is_unignored_and_maps_stay_ignored(self) -> None:
        rules = GITIGNORE.read_text(encoding="utf-8")
        self.assertIn("**/dist/", rules)
        self.assertIn("!apps/client/dist/", rules)
        self.assertIn("!apps/client/dist/**", rules)
        self.assertIn("apps/client/dist/**/*.map", rules)

        other_dist = subprocess.run(
            ("git", "check-ignore", "--no-index", "packages/example/dist/file.js"),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        client_dist = subprocess.run(
            ("git", "check-ignore", "--no-index", "apps/client/dist/index.html"),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        source_map = subprocess.run(
            ("git", "check-ignore", "--no-index", "apps/client/dist/assets/app.js.map"),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(other_dist.returncode, 0)
        self.assertNotEqual(client_dist.returncode, 0)
        self.assertEqual(source_map.returncode, 0)

    def test_ci_rebuilds_and_compares_committed_dist(self) -> None:
        workflow = CI.read_text(encoding="utf-8")
        self.assertIn("Production build matches committed dist", workflow)
        self.assertIn("dist/build-info.json", workflow)
        self.assertIn("BUILD_COMMIT", workflow)
        self.assertIn("BUILD_UTC_TIMESTAMP", workflow)
        self.assertIn("git diff --exit-code -- dist", workflow)
        self.assertIn("git status --porcelain --untracked-files=all -- dist", workflow)

    def test_build_info_contains_only_safe_bounded_fields(self) -> None:
        data = json.loads(BUILD_INFO.read_text(encoding="utf-8"))
        self.assertEqual(
            set(data),
            {"build_commit", "build_utc_timestamp", "frontend_version"},
        )
        for value in data.values():
            self.assertIsInstance(value, str)
            self.assertLessEqual(len(value), 128)

    def test_production_bundle_uses_relative_api_urls(self) -> None:
        bundle = "\n".join(
            path.read_text(encoding="utf-8")
            for path in (DIST / "assets").glob("*.js")
        )
        self.assertIn("/api/", bundle)
        self.assertNotIn("http://localhost", bundle)
        self.assertNotIn("http://127.0.0.1", bundle)

    def test_deploy_defaults_and_safe_ordering(self) -> None:
        script = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('REPO_ROOT="/home/desify/creative-asset-manager"', script)
        self.assertIn('FRONTEND_ROOT="/var/www/creative-asset-manager"', script)
        self.assertIn("git merge --ff-only", script)
        self.assertNotIn("git reset --hard", script)
        self.assertLess(script.index('validate_database_connection'), script.index('--profile migration run --rm migrate'))
        self.assertLess(script.index('--profile migration run --rm migrate'), script.index('up -d --no-build api worker'))
        self.assertLess(script.index("wait_for_api_release"), script.index('current.new.$$'))
        self.assertIn("sudo mv -Tf", script)

    def test_compose_and_images_match_target_architecture(self) -> None:
        config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        self.assertEqual(set(config["services"]), {"api", "worker", "migrate", "elasticsearch"})
        self.assertNotIn("postgres", config["services"])
        self.assertIn("host.docker.internal:host-gateway", config["x-backend-common"]["extra_hosts"])
        self.assertEqual(config["services"]["api"]["ports"], ["127.0.0.1:8000:8000"])
        self.assertNotIn("ports", config["services"]["worker"])
        backend = (COMPOSE.parent / "backend.Dockerfile").read_text(encoding="utf-8").lower()
        self.assertIn("from python:3.12.8-slim-bookworm", backend)
        self.assertNotIn("copy .", backend)
        self.assertIn("database/migrations", backend)
        common = config["x-backend-common"]
        self.assertEqual(common["user"], "10001:10001")
        self.assertTrue(common["read_only"])
        self.assertEqual(common["stop_signal"], "SIGTERM")
        self.assertIn("ALL", common["cap_drop"])
        self.assertEqual(
            common["environment"]["ELASTICSEARCH_URL"],
            "http://elasticsearch:9200",
        )
        self.assertNotIn("node", backend)
        self.assertIn("user 10001:10001", backend)
        self.assertNotIn("apps/client", backend)
        self.assertEqual(config["services"]["api"]["image"], config["services"]["worker"]["image"])
        self.assertEqual(config["services"]["api"]["image"], config["services"]["migrate"]["image"])
        self.assertNotIn("build", config["services"]["worker"])
        self.assertNotIn("build", config["services"]["migrate"])
        self.assertEqual(
            config["services"]["migrate"]["command"],
            ["python", "-m", "alembic", "upgrade", "head"],
        )

    def test_nginx_proxy_spa_and_cache_policy(self) -> None:
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("proxy_pass http://creative_asset_manager_api;", config)
        self.assertIn("try_files $uri $uri/ /index.html;", config)
        self.assertIn("location = /build-info.json", config)
        self.assertIn("max-age=31536000, immutable", config)
        self.assertIn("no-store, no-cache, must-revalidate", config)

    def test_committed_frontend_has_marker_and_no_forbidden_values(self) -> None:
        self.assertTrue((DIST / "index.html").is_file())
        self.assertTrue((DIST / "build-info.json").is_file())
        result = self.run_scan(DIST)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
