from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import yaml

ROOT = Path(__file__).resolve().parents[2]
API_ROOT = ROOT / "apps" / "api"
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from app.core.config import FEATURE_FLAG_NAMES, Settings
from app.providers.google.auth import oauth_flow
from app.providers.microsoft.auth import _settings as microsoft_settings

NGINX = ROOT / "infrastructure" / "nginx" / "creative-asset-manager.conf"
COMPOSE = ROOT / "infrastructure" / "docker" / "docker-compose.prod.yml"
PRODUCTION_ENV = ROOT / "deploy" / "production.env.example"
API_SERVICE = ROOT / "deploy" / "systemd" / "creative-asset-manager-api.service"
WORKER_SERVICE = ROOT / "deploy" / "systemd" / "creative-asset-manager-worker.service"

ROLLOUT_FLAGS = (
    "UNIFIED_ASSET_INGESTION_ENABLED",
    "CONTENT_DEDUP_ENABLED",
    "INCREMENTAL_SOURCE_SYNC_ENABLED",
    "PROCESSING_JOBS_ENABLED",
    "EXTERNAL_ASSET_DOWNLOADER_ENABLED",
    "MANAGED_ASSET_STORAGE_ENABLED",
    "DYNAMIC_AI_METADATA_ENABLED",
    "AI_SINGLE_ANALYSIS_ENABLED",
    "AI_BATCH_ANALYSIS_ENABLED",
    "AI_AUTO_ANALYZE_ENABLED",
    "SEARCH_PROJECTION_ENABLED",
    "ELASTICSEARCH_V2_ENABLED",
    "SEARCH_QUERY_PARSER_V2_ENABLED",
    "SEARCH_SHADOW_COMPARISON_ENABLED",
    "ELASTICSEARCH_INDEX_LIFECYCLE_ENABLED",
    "EXTERNAL_INGESTION_API_ENABLED",
)


class VpsDeploymentArtifactTest(unittest.TestCase):
    def test_nginx_spa_proxy_and_static_cache_contract(self) -> None:
        config = NGINX.read_text(encoding="utf-8")
        self.assertIn("server 127.0.0.1:8000;", config)
        self.assertIn("root /var/www/creative-asset-manager/current;", config)
        self.assertIn("location /api/", config)
        self.assertIn("proxy_pass http://creative_asset_manager_api;", config)
        self.assertIn("proxy_set_header X-Forwarded-Proto $scheme;", config)
        self.assertIn("try_files $uri $uri/ /index.html;", config)
        self.assertIn("location /assets/", config)
        self.assertIn("expires 1y;", config)
        self.assertIn('Cache-Control "public, max-age=31536000, immutable"', config)
        self.assertIn("location = /build-info.json", config)
        self.assertIn('Cache-Control "no-store, no-cache, must-revalidate"', config)

    def test_compose_contains_docker_backend_without_postgres(self) -> None:
        config = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
        self.assertEqual(
            set(config['services']),
            {'api', 'worker', 'migrate', 'elasticsearch'},
        )
        elasticsearch = config['services']['elasticsearch']
        self.assertEqual(elasticsearch['ports'], ['127.0.0.1:9200:9200'])
        self.assertEqual(config['services']['api']['ports'], ['127.0.0.1:8000:8000'])
        self.assertNotIn('ports', config['services']['worker'])
        self.assertNotIn('postgres', config['services'])

    def test_native_services_use_current_release_and_loopback(self) -> None:
        api = API_SERVICE.read_text(encoding="utf-8")
        worker = WORKER_SERVICE.read_text(encoding="utf-8")
        self.assertIn("WorkingDirectory=/opt/creative-asset-manager/current/apps/api", api)
        self.assertIn("--host 127.0.0.1 --port 8000", api)
        self.assertIn("--no-proxy-headers", api)
        self.assertIn("WorkingDirectory=/opt/creative-asset-manager/current", worker)
        self.assertIn("apps/worker/main.py", worker)
        self.assertIn("KillSignal=SIGTERM", worker)
        self.assertIn("TimeoutStopSec=45s", worker)

    def test_rollout_features_are_disabled_in_defaults_and_template(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            settings = Settings()
        for name in FEATURE_FLAG_NAMES:
            with self.subTest(default=name):
                self.assertFalse(getattr(settings, name))

        values = {}
        for raw_line in PRODUCTION_ENV.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key] = value
        for name in ROLLOUT_FLAGS:
            with self.subTest(template=name):
                self.assertEqual(values.get(name), "false")

    def test_production_oauth_callback_urls_are_generated_from_environment(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GOOGLE_CLIENT_ID": "test-client",
                "GOOGLE_CLIENT_SECRET": "test-secret",
                "GOOGLE_REDIRECT_URI": "https://assets.example.com/api/auth/google/callback",
            },
            clear=False,
        ):
            google = oauth_flow()
        self.assertEqual(
            google.redirect_uri,
            "https://assets.example.com/api/auth/google/callback",
        )

        with patch.dict(
            os.environ,
            {
                "MICROSOFT_CLIENT_ID": "test-client",
                "MICROSOFT_CLIENT_SECRET": "test-secret",
                "MICROSOFT_TENANT_ID": "organizations",
                "MICROSOFT_REDIRECT_URI": "https://assets.example.com/api/auth/microsoft/callback",
            },
            clear=False,
        ):
            _, _, _, redirect_uri = microsoft_settings()
        self.assertEqual(
            redirect_uri,
            "https://assets.example.com/api/auth/microsoft/callback",
        )


if __name__ == "__main__":
    unittest.main()
