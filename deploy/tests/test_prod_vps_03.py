from __future__ import annotations

import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NGINX = ROOT / "infrastructure" / "nginx" / "creative-asset-manager.conf"
DEPLOY = ROOT / "scripts" / "deploy-vps.sh"
ROLLBACK = ROOT / "scripts" / "rollback-vps.sh"
VALIDATE = ROOT / "scripts" / "validate-production.sh"
COMMON = ROOT / "scripts" / "lib" / "deployment-common.sh"
RUNBOOK = ROOT / "docs" / "operations" / "VPS_PRODUCTION.md"


class ProdVps03Test(unittest.TestCase):
    def test_nginx_static_spa_proxy_and_security_contract(self) -> None:
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("root /var/www/creative-asset-manager/current;", config)
        self.assertIn("location /api/", config)
        self.assertIn("proxy_pass http://creative_asset_manager_api;", config)
        self.assertIn("server 127.0.0.1:8000;", config)
        self.assertIn("try_files $uri $uri/ /index.html;", config)
        self.assertIn("/ai-operations", config)
        self.assertIn("/settings/access", config)
        self.assertIn("location /assets/", config)
        self.assertIn("max-age=31536000, immutable", config)
        self.assertIn("location = /index.html", config)
        self.assertIn("location = /build-info.json", config)
        self.assertGreaterEqual(
            config.count('no-store, no-cache, must-revalidate'),
            2,
        )
        self.assertIn("location ~ (^|/)[.]", config)
        self.assertIn("Strict-Transport-Security", config)
        self.assertIn("Content-Security-Policy", config)
        self.assertIn("Permissions-Policy", config)
        self.assertNotIn("$proxy_add_x_forwarded_for", config)
        self.assertGreaterEqual(
            config.count("proxy_set_header X-Forwarded-For $remote_addr;"),
            4,
        )
        self.assertGreaterEqual(
            config.count("proxy_set_header X-Forwarded-Proto https;"),
            4,
        )

    def test_deploy_is_fail_closed_ordered_and_version_matched(self) -> None:
        script = DEPLOY.read_text(encoding="utf-8")
        self.assertIn('REPO_ROOT="/home/desify/creative-asset-manager"', script)
        self.assertIn('FRONTEND_ROOT="/var/www/creative-asset-manager"', script)
        self.assertIn('require_deployment_user "desify" "$ALLOW_USER"', script)
        self.assertIn("--allow-user", script)
        self.assertIn("git merge --ff-only", script)
        self.assertNotIn("git reset --hard", script)
        self.assertIn('validate_production_env_file "$ENV_FILE"', script)
        self.assertIn('"${COMPOSE[@]}" build api', script)
        self.assertIn("validate_database_connection", script)
        self.assertIn("--profile migration run --rm migrate", script)
        self.assertIn("up -d elasticsearch", script)
        self.assertIn("up -d --no-build api worker", script)
        self.assertIn('wait_for_api_release "http://127.0.0.1:8000"', script)
        self.assertIn('version_matches_commit "$PUBLIC_URL/version"', script)
        self.assertIn("/settings/access", script)
        self.assertIn("sudo mv -Tf", script)
        self.assertIn("KEEP_RELEASES=5", script)
        self.assertLess(
            script.index("validate_database_connection"),
            script.index("--profile migration run --rm migrate"),
        )
        self.assertLess(
            script.index("--profile migration run --rm migrate"),
            script.index('up -d --no-build api worker'),
        )
        self.assertLess(
            script.index("wait_for_api_release"),
            script.index('current.new.$$'),
        )

    def test_rollback_matches_backend_frontend_without_database_downgrade(self) -> None:
        script = ROLLBACK.read_text(encoding="utf-8")
        self.assertIn('require_deployment_user "desify" "$ALLOW_USER"', script)
        self.assertIn('"${COMPOSE[@]}" build api', script)
        self.assertIn('wait_for_api_release "http://127.0.0.1:8000"', script)
        self.assertIn('version_matches_commit "$PUBLIC_URL/version"', script)
        self.assertIn("current.rollback.$$", script)
        self.assertIn("sudo mv -Tf", script)
        self.assertIn("never downgraded automatically", script)
        self.assertNotIn("alembic downgrade", script)
        self.assertNotIn("docker compose down", script)

    def test_shared_validation_is_secret_safe_and_reusable(self) -> None:
        common = COMMON.read_text(encoding="utf-8")
        validator = VALIDATE.read_text(encoding="utf-8")
        self.assertIn("validate_production_env_file()", common)
        self.assertIn("DATABASE_URL must use native PostgreSQL", common)
        self.assertIn("API documentation must be disabled", common)
        self.assertNotIn("set -x", validator)
        self.assertIn("--config-only", validator)
        self.assertIn("--preflight", validator)
        self.assertIn("wait_for_api_release", validator)

    def test_runbook_documents_target_and_rollback_boundary(self) -> None:
        text = RUNBOOK.read_text(encoding="utf-8")
        self.assertIn("/home/desify/creative-asset-manager", text)
        self.assertIn("/var/www/creative-asset-manager", text)
        self.assertIn("sudo -u desify ./scripts/deploy-vps.sh", text)
        self.assertIn("sudo -u desify ./scripts/rollback-vps.sh", text)
        self.assertIn("never runs `alembic downgrade`", text)
        self.assertIn("matching `/version.commit`", text)


if __name__ == "__main__":
    unittest.main()
