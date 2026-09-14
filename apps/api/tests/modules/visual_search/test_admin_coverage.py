from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.modules.authorization.principal import CurrentPrincipal, require_authenticated_principal
from app.modules.visual_search import admin_router
from app.modules.visual_search.admin_router import router


def principal(*, tenant_id="tenant-a", permissions=frozenset({"ai_operations.read"})):
    return CurrentPrincipal(
        user_id="user-a", active_tenant_id=tenant_id, membership_id="membership-a",
        external_identity=None, effective_roles=frozenset({"tenant_admin"}),
        effective_permissions=permissions, platform_admin=False, session_id="session",
        authorization_source="tenant_rbac",
    )


class CoverageService:
    def __init__(self, index_state="available"):
        self.index_state = index_state
        self.tenants = []

    def collect(self, tenant_id):
        self.tenants.append(tenant_id)
        unavailable = self.index_state == "unavailable"
        return SimpleNamespace(
            index_state=self.index_state,
            totals={
                "discovered_images": 2, "imported_images": 1, "visual_eligible": 1,
                "visual_indexed_current": None if unavailable else 1,
                "visual_index_missing": None if unavailable else 0,
                "visual_index_stale": None if unavailable else 0,
                "unsupported_images": 1, "visual_jobs_pending": 0,
                "visual_jobs_processing": 0, "visual_jobs_failed": 0,
            },
            ratios={
                "import_coverage": 0.5,
                "eligible_visual_coverage": None if unavailable else 1.0,
                "whole_resource_searchable": None if unavailable else 0.5,
            },
            sources=[{
                "source_id": "source-a", "display_name": "Drive A", "source_type": "google_drive",
                "discovered_images": 2, "imported_images": 1, "visual_eligible": 1,
                "visual_indexed_current": None if unavailable else 1,
                "visual_index_missing": None if unavailable else 0,
                "visual_index_stale": None if unavailable else 0, "unsupported_images": 1,
                "ratios": {"import_coverage": 0.5, "eligible_visual_coverage": None if unavailable else 1.0, "whole_resource_searchable": None if unavailable else 0.5},
            }],
        )


@pytest.fixture
def coverage_client(monkeypatch):
    service = CoverageService()
    monkeypatch.setattr(admin_router, "service", lambda: service)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()
    with TestClient(app) as client:
        yield client, service
    app.dependency_overrides.clear()


def test_admin_coverage_routes_are_read_only_get_routes():
    paths = {route.path for route in router.routes}
    assert "/api/v1/admin/visual-search/coverage" in paths
    assert "/api/v1/admin/visual-search/coverage/sources" in paths
    assert "/api/v1/admin/visual-search/coverage/dashboard" in paths
    assert all("GET" in route.methods for route in router.routes if route.path in {"/api/v1/admin/visual-search/coverage", "/api/v1/admin/visual-search/coverage/sources", "/api/v1/admin/visual-search/coverage/dashboard"})


def test_authorized_admin_gets_tenant_coverage_and_cannot_override_tenant(coverage_client):
    client, service = coverage_client
    response = client.get("/api/v1/admin/visual-search/coverage?tenant_id=tenant-b")
    assert response.status_code == 200
    body = response.json()
    assert body["index_state"] == "available"
    assert body["totals"]["visual_indexed_current"] == 1
    assert body["ratios"]["whole_resource_searchable"] == 0.5
    assert "generated_at" in body
    assert service.tenants == ["tenant-a"]
    assert "tenant-b" not in response.text


def test_authorized_admin_gets_deterministic_safe_source_coverage(coverage_client):
    client, service = coverage_client
    response = client.get("/api/v1/admin/visual-search/coverage/sources")
    assert response.status_code == 200
    assert response.json() == {"index_state": "available", "sources": service.collect("tenant-a").sources}
    assert "token" not in response.text.lower()
    assert "credential" not in response.text.lower()


def test_permission_missing_is_rejected_and_service_is_not_called(monkeypatch):
    service = CoverageService()
    monkeypatch.setattr(admin_router, "service", lambda: service)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal(permissions=frozenset({"assets.read"}))
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/visual-search/coverage")
        assert response.status_code == 403
        assert response.json()["detail"]["code"] == "permission_required"
        assert service.tenants == []
    finally:
        app.dependency_overrides.clear()


def test_es_unavailable_serializes_es_metrics_and_ratios_as_null(monkeypatch):
    service = CoverageService("unavailable")
    monkeypatch.setattr(admin_router, "service", lambda: service)
    app.dependency_overrides[require_authenticated_principal] = lambda: principal()
    try:
        with TestClient(app) as client:
            response = client.get("/api/v1/admin/visual-search/coverage")
        assert response.status_code == 200
        body = response.json()
        assert body["totals"]["discovered_images"] == 2
        assert body["totals"]["visual_indexed_current"] is None
        assert body["totals"]["visual_index_missing"] is None
        assert body["ratios"]["eligible_visual_coverage"] is None
        assert body["ratios"]["whole_resource_searchable"] is None
    finally:
        app.dependency_overrides.clear()


def test_dashboard_returns_coverage_and_sources_from_one_collection(monkeypatch):
    service = CoverageService()
    monkeypatch.setattr(admin_router, "service", lambda: service)
    body = admin_router.coverage_dashboard(principal())
    assert body["totals"]["discovered_images"] == 2
    assert body["sources"][0]["display_name"] == "Drive A"
    assert service.tenants == ["tenant-a"]
