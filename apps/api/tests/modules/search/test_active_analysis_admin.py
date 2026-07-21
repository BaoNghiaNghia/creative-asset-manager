import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.modules.processing_policy.auth import ProcessingAdmin, require_processing_admin


class ActiveAnalysisAdminAuthorizationTest(unittest.TestCase):
    def tearDown(self):
        app.dependency_overrides.clear()

    def test_unauthenticated_activation_fails(self):
        response = TestClient(app).post(
            "/api/v1/admin/search/tenants/tenant-a/assets/asset-a/active-analysis",
            json={"analysis_id": "analysis-a"},
        )
        self.assertEqual(response.status_code, 401)

    def test_cross_tenant_rollback_fails(self):
        app.dependency_overrides[require_processing_admin] = lambda: ProcessingAdmin(
            actor_id="tenant-a", own_tenant_id="tenant-a", platform_admin=False,
        )
        response = TestClient(app).post(
            "/api/v1/admin/search/tenants/tenant-b/assets/asset-a/active-analysis/rollback",
            json={"metadata_profile_id": "profile-a"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
