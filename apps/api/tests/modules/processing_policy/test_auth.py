import unittest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from app.modules.processing_policy.auth import ProcessingAdmin
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

    def test_platform_admin_may_manage_other_tenant(self):
        ProcessingAdmin("platform", "platform", True).authorize_tenant("tenant-b")
