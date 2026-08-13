from __future__ import annotations

import base64
import logging
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.authorization.principal import CurrentPrincipal
from app.modules.inventory import router as inventory_router
from app.providers.ai import gemini as gemini_provider
from app.modules.inventory.credentials import (
    InventoryAiCredentialRepository,
    inventory_credential_cipher,
)
from app.modules.inventory.persistence_model import (
    InventoryAiCredentialAuditModel,
    InventoryAiCredentialModel,
)

KEY = base64.urlsafe_b64encode(b"I" * 32).decode().rstrip("=")
OLD_KEY = "AIzaSyInventoryOldKey000000000000000000000"
NEW_KEY = "AIzaSyInventoryNewKey000000000000000000000"


class InventoryCredentialRouterTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine(
            "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
        )
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all((
                TenantModel(id="tenant-a", name="A", slug="a"),
                TenantModel(id="tenant-b", name="B", slug="b"),
                ExternalSourceModel(
                    id="drive-a", tenant_id="tenant-a", source_key="drive-a",
                    source_type="google_drive",
                ),
                OAuthConnectionModel(
                    id="oauth-a", tenant_id="tenant-a", provider="google",
                    provider_account_id="drive-account-a", key_version="v1",
                    access_token_ciphertext="drive-access", refresh_token_ciphertext="drive-refresh",
                    status="active",
                ),
            ))
            session.commit()
        self.settings = Settings(INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY)
        self.app = FastAPI()
        self.app.include_router(inventory_router.router)
        self.client = TestClient(self.app)
        self.read = self.principal({"inventory.read"})
        self.manage = self.principal({"inventory.read", "inventory.credentials.manage"})
        self.no_permissions = self.principal(set())

    def tearDown(self):
        self.engine.dispose()

    @staticmethod
    def principal(permissions, tenant="tenant-a"):
        return CurrentPrincipal(
            "user-a", tenant, "membership-a", None, frozenset(),
            frozenset(permissions), False, "session-a", "test",
        )

    def request(self, principal, method, path, **kwargs):
        self.app.dependency_overrides.clear()
        for route in self.app.routes:
            if getattr(route, "path", "").startswith("/api/inventory"):
                for permission_dependency in route.dependant.dependencies:
                    for authenticated_dependency in permission_dependency.dependencies:
                        self.app.dependency_overrides[authenticated_dependency.call] = (
                            lambda principal=principal: principal
                        )
        with patch("app.modules.inventory.router.SessionLocal", self.sessions), patch(
            "app.modules.inventory.router.get_settings", return_value=self.settings
        ):
            return self.client.request(method, path, **kwargs)

    def repository(self, session):
        return InventoryAiCredentialRepository(session, inventory_credential_cipher(self.settings))

    def store(self, tenant, secret, label="Gemini Account B"):
        with self.sessions() as session:
            self.repository(session).replace(tenant, secret=secret, label=label, updated_by="seed")
            session.commit()

    def test_get_is_masked_and_tenant_scoped_without_exposing_secret(self):
        self.store("tenant-a", OLD_KEY)
        self.store("tenant-b", NEW_KEY, "B only")
        response = self.request(self.read, "GET", "/api/inventory/configuration/ai-credential")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["source"], "configuration")
        self.assertEqual(body["masked_key"], f"••••••••{OLD_KEY[-4:]}")
        self.assertEqual(body["label"], "Gemini Account B")
        self.assertNotIn(OLD_KEY, response.text)
        self.assertNotIn(NEW_KEY, response.text)
        self.assertEqual(
            self.request(self.no_permissions, "GET", "/api/inventory/configuration/ai-credential").status_code,
            403,
        )
        tenant_b = self.principal({"inventory.read"}, tenant="tenant-b")
        self.assertEqual(
            self.request(tenant_b, "GET", "/api/inventory/configuration/ai-credential").json()["label"],
            "B only",
        )

    def test_test_endpoint_requires_manage_permission_and_normalizes_provider_statuses(self):
        payload = {"api_key": NEW_KEY, "label": "Candidate"}
        self.assertEqual(
            self.request(self.read, "POST", "/api/inventory/configuration/ai-credential/test", json=payload).status_code,
            403,
        )
        for expected in ("VALID", "INVALID_KEY", "PERMISSION_DENIED", "RATE_LIMITED", "PROVIDER_UNAVAILABLE"):
            with patch("app.modules.inventory.router.validate_gemini_candidate", return_value=expected):
                response = self.request(self.manage, "POST", "/api/inventory/configuration/ai-credential/test", json=payload)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"provider": "gemini", "status": expected})
            self.assertNotIn(NEW_KEY, response.text)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryAiCredentialModel.id))), 0)

    def test_put_validates_before_atomic_replacement_and_audits_safe_metadata(self):
        self.store("tenant-a", OLD_KEY)
        with patch("app.modules.inventory.router.validate_gemini_candidate", return_value="INVALID_KEY"):
            response = self.request(self.manage, "PUT", "/api/inventory/configuration/ai-credential", json={"api_key": NEW_KEY, "label": "New"})
        self.assertEqual(response.status_code, 422)
        self.assertNotIn(NEW_KEY, response.text)
        with self.sessions() as session:
            self.assertEqual(self.repository(session).get_active_secret("tenant-a"), OLD_KEY)
            self.assertEqual(session.scalar(select(func.count(InventoryAiCredentialAuditModel.id))), 1)
        with patch("app.modules.inventory.router.validate_gemini_candidate", return_value="VALID"), self.assertLogs("cam.inventory.credentials_api", level="INFO") as logs:
            response = self.request(self.manage, "PUT", "/api/inventory/configuration/ai-credential", json={"api_key": NEW_KEY, "label": "Gemini Account B"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["masked_key"], f"••••••••{NEW_KEY[-4:]}")
        self.assertIsNotNone(response.json()["last_tested_at"])
        self.assertNotIn(NEW_KEY, response.text)
        self.assertNotIn(NEW_KEY, "\n".join(logs.output))
        with self.sessions() as session:
            self.assertEqual(self.repository(session).get_active_secret("tenant-a"), NEW_KEY)
            audits = session.scalars(select(InventoryAiCredentialAuditModel).order_by(InventoryAiCredentialAuditModel.created_at)).all()
            self.assertEqual(len(audits), 2)
            self.assertEqual(audits[-1].action, "credential_replaced")
            self.assertEqual(audits[-1].actor_id, "user-a")
            self.assertIsNotNone(audits[-1].previous_fingerprint)
            self.assertIsNotNone(audits[-1].new_fingerprint)

    def test_put_requires_manage_permission_is_repeat_safe_and_isolates_drive_and_creative(self):
        payload = {"api_key": NEW_KEY, "label": "Gemini Account B"}
        with patch("app.modules.inventory.router.validate_gemini_candidate", return_value="VALID"):
            self.assertEqual(self.request(self.read, "PUT", "/api/inventory/configuration/ai-credential", json=payload).status_code, 403)
            with self.sessions() as session:
                before_drive = session.scalar(select(func.count(OAuthConnectionModel.id)))
                before_creative = session.scalar(select(func.count(AssetAiAnalysisModel.id)))
            self.assertEqual(self.request(self.manage, "PUT", "/api/inventory/configuration/ai-credential", json=payload).status_code, 200)
            self.assertEqual(self.request(self.manage, "PUT", "/api/inventory/configuration/ai-credential", json=payload).status_code, 200)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryAiCredentialModel.id))), 1)
            self.assertEqual(session.scalar(select(func.count(OAuthConnectionModel.id))), before_drive)
            self.assertEqual(session.scalar(select(func.count(AssetAiAnalysisModel.id))), before_creative)
            self.assertEqual(session.get(OAuthConnectionModel, "oauth-a").provider_account_id, "drive-account-a")

    def test_put_fails_closed_with_a_structured_error_when_server_encryption_is_unconfigured(self):
        self.settings = Settings(INVENTORY_CREDENTIAL_ENCRYPTION_KEY="")
        with patch("app.modules.inventory.router.validate_gemini_candidate", return_value="VALID"):
            response = self.request(
                self.manage, "PUT", "/api/inventory/configuration/ai-credential",
                json={"api_key": NEW_KEY, "label": "Gemini Account B"},
            )
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"]["code"], "inventory_credential_encryption_unavailable")
        self.assertNotIn(NEW_KEY, response.text)
        with self.sessions() as session:
            self.assertEqual(session.scalar(select(func.count(InventoryAiCredentialModel.id))), 0)

    def test_gemini_http_statuses_are_mapped_without_provider_body(self):
        class Response:
            def __init__(self, status_code):
                self.status_code = status_code

        expected = {
            200: "VALID", 400: "INVALID_KEY", 401: "INVALID_KEY",
            403: "PERMISSION_DENIED", 429: "RATE_LIMITED",
            500: "PROVIDER_UNAVAILABLE",
        }
        for code, status in expected.items():
            with patch("app.providers.ai.gemini.httpx.get", return_value=Response(code)):
                self.assertEqual(gemini_provider.validate_gemini_api_key(NEW_KEY), status)
        with patch(
            "app.providers.ai.gemini.httpx.get",
            side_effect=gemini_provider.httpx.ConnectError("offline"),
        ):
            self.assertEqual(
                gemini_provider.validate_gemini_api_key(NEW_KEY), "PROVIDER_UNAVAILABLE"
            )

    def test_environment_source_never_reveals_the_fallback_secret(self):
        self.settings = Settings(INVENTORY_GEMINI_API_KEY=NEW_KEY)
        response = self.request(self.read, "GET", "/api/inventory/configuration/ai-credential")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["source"], "environment")
        self.assertEqual(response.json()["masked_key"], f"••••••••{NEW_KEY[-4:]}")
        self.assertNotIn(NEW_KEY, response.text)


if __name__ == "__main__":
    unittest.main()
