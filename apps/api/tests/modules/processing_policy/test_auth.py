import unittest
from types import SimpleNamespace
from unittest.mock import patch
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin
from app.modules.processing_policy.router import router

class ProcessingAdminTest(unittest.TestCase):
    def test_tenant_admin_is_isolated(self):
        ProcessingAdmin("tenant-a", "tenant-a", False).authorize_tenant("tenant-a")
        with self.assertRaises(HTTPException) as error:
            ProcessingAdmin("tenant-a", "tenant-a", False).authorize_tenant("tenant-b")
        self.assertEqual(error.exception.status_code, 403)

    def test_unauthorized_policy_update_fails(self):
        app = FastAPI(); app.include_router(router)
        response = TestClient(app).patch("/api/v1/admin/processing-policies/tenant-a", json={"pipeline_enabled": True})
        self.assertEqual(response.status_code, 401)
    def test_membership_tenant_replaces_actor_as_tenant_identity(self):
        cloud_session = SimpleNamespace(
            user_id="application-user",
            active_tenant_id="tenant-a",
            tenant_id="provider-account",
            user={"id": "provider-subject", "role": "tenant_admin"},
        )
        request = SimpleNamespace()
        with (
            patch("app.modules.processing_policy.auth.get_google_session", return_value=cloud_session),
            patch("app.modules.processing_policy.auth.get_microsoft_session", return_value=None),
            patch("app.modules.processing_policy.auth.resolve_processing_tenant", return_value="tenant-a"),
        ):
            admin = require_processing_admin(request)
        self.assertEqual(admin.actor_id, "application-user")
        self.assertEqual(admin.own_tenant_id, "tenant-a")
        self.assertNotEqual(admin.actor_id, admin.own_tenant_id)


    def test_platform_admin_may_manage_other_tenant(self):
        ProcessingAdmin("platform", "platform", True).authorize_tenant("tenant-b")
