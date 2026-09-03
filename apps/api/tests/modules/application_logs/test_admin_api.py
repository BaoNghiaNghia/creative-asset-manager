import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.database import Base
from app.modules.application_logs.admin_router import router
from app.modules.auth_persistence.model import TenantModel
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal


class ApplicationLogAdminApiTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        Base.metadata.create_all(self.engine)
        with self.sessions() as session:
            session.add(TenantModel(id="tenant-a", name="Tenant A", slug="tenant-a"))
            session.commit()
        principal = CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="member-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset({"application_logs.manage"}), platform_admin=False,
            session_id="session-a", authorization_source="test",
        )
        app = FastAPI(); app.include_router(router)
        app.dependency_overrides[require_authenticated_principal] = lambda: principal
        self.session_patch = patch("app.modules.application_logs.admin_router.SessionLocal", self.sessions)
        self.session_patch.start()
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close(); self.session_patch.stop(); self.engine.dispose()

    def test_create_list_and_rotate_expose_secret_once(self):
        created = self.client.post("/api/v1/tenants/tenant-a/log-applications", json={
            "slug": "orders", "display_name": "Orders",
            "payload_schema": {"type": "object", "required": ["order_id"]},
        })
        self.assertEqual(created.status_code, 201)
        self.assertTrue(created.json()["api_key"].startswith("camlog_"))
        application_id = created.json()["id"]
        listed = self.client.get("/api/v1/tenants/tenant-a/log-applications")
        self.assertEqual(listed.status_code, 200)
        self.assertNotIn("api_key", listed.json()[0])
        rotated = self.client.post(f"/api/v1/tenants/tenant-a/log-applications/{application_id}/rotate-key")
        self.assertEqual(rotated.status_code, 200)
        self.assertNotEqual(rotated.json()["api_key"], created.json()["api_key"])

    def test_invalid_schema_and_tenant_mismatch_are_rejected(self):
        invalid = self.client.post("/api/v1/tenants/tenant-a/log-applications", json={
            "slug": "orders", "display_name": "Orders",
            "payload_schema": {"type": "not-a-json-schema-type"},
        })
        forbidden = self.client.get("/api/v1/tenants/tenant-b/log-applications")
        self.assertEqual(invalid.status_code, 422)
        self.assertEqual(invalid.json()["detail"]["code"], "invalid_payload_schema")
        self.assertEqual(forbidden.status_code, 403)


if __name__ == "__main__": unittest.main()
