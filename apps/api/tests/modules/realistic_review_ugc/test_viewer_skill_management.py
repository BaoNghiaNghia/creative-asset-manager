"""RRUGC Skill permissions are scoped to the workflow, never tenant admin.

Viewer can operate every RRUGC stage and manage Skills because its seeded
capabilities include read, run and configure. No tenant-wide permissions are
granted as a side effect.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.modules.authorization.seed import VIEWER_PERMISSIONS
from app.modules.realistic_review_ugc import router as rrugc_router


def _principal(*, roles=("viewer",), permissions=(), platform_admin=False):
    return SimpleNamespace(
        effective_roles=frozenset(roles),
        effective_permissions=frozenset(permissions),
        platform_admin=platform_admin,
    )


def test_viewer_seed_has_full_rrugc_capabilities_but_not_tenant_admin():
    assert {"realistic_review_ugc.read", "realistic_review_ugc.run",
            "realistic_review_ugc.configure"} <= VIEWER_PERMISSIONS
    assert "tenant_roles.manage" not in VIEWER_PERMISSIONS
    assert "tenant_members.manage" not in VIEWER_PERMISSIONS
    assert "ai_provider.configure" not in VIEWER_PERMISSIONS


@pytest.mark.parametrize("roles", [("viewer",), ("operator",), ("custom_rrugc",)])
def test_all_rrugc_configure_members_can_manage_skills(roles):
    principal = _principal(roles=roles, permissions={
        "realistic_review_ugc.read", "realistic_review_ugc.run",
        "realistic_review_ugc.configure",
    })
    assert rrugc_router._can_manage_stage2_skills(principal)
    assert rrugc_router._require_stage2_skill_admin(principal) is None


def test_missing_configure_denies_skill_crud_even_with_viewer_or_fake_tenant_admin_role():
    for roles in [("viewer",), ("tenant_admin",), ("custom_rrugc",)]:
        principal = _principal(roles=roles, permissions={"realistic_review_ugc.read", "realistic_review_ugc.run"})
        assert not rrugc_router._can_manage_stage2_skills(principal)
        with pytest.raises(HTTPException) as exc:
            rrugc_router._require_stage2_skill_admin(principal)
        assert exc.value.status_code == 403
        assert exc.value.detail["required_permission"] == "realistic_review_ugc.configure"


def test_platform_admin_retains_manage_right_without_explicit_rrugc_permission():
    principal = _principal(roles=(), permissions=(), platform_admin=True)
    assert rrugc_router._can_manage_stage2_skills(principal)
    rrugc_router._require_stage2_skill_admin(principal)


def test_all_skill_mutation_routes_keep_the_scoped_server_side_guard():
    """New mutations cannot accidentally become read-only-permission writes."""
    import inspect

    mutation_routes = [
        route for route in rrugc_router.router.routes
        if getattr(route, "path", "").startswith("/api/v1/realistic-review-ugc/stage2-skills/")
        and (getattr(route, "methods", set()) & {"POST", "PATCH", "PUT", "DELETE"})
    ]
    assert len(mutation_routes) >= 12
    for route in mutation_routes:
        source = inspect.getsource(route.endpoint)
        assert "_require_stage2_skill_admin(principal)" in source, (route.path, route.methods)
