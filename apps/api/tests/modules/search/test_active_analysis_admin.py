import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal


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
        app.dependency_overrides[require_authenticated_principal] = lambda: CurrentPrincipal(
            user_id="user-a", active_tenant_id="tenant-a", membership_id="membership-a",
            external_identity=None, effective_roles=frozenset({"tenant_admin"}),
            effective_permissions=frozenset({"search.index.activate"}),
            platform_admin=False, session_id=None, authorization_source="tenant_rbac",
        )
        response = TestClient(app).post(
            "/api/v1/admin/search/tenants/tenant-b/assets/asset-a/active-analysis/rollback",
            json={"metadata_profile_id": "profile-a"},
        )
        self.assertEqual(response.status_code, 403)


if __name__ == "__main__":
    unittest.main()
