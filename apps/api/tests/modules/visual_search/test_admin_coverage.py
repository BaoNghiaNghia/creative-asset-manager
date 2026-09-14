from app.modules.visual_search.admin_router import router
def test_admin_coverage_routes_are_read_only_get_routes():
 paths={r.path for r in router.routes}
 assert "/api/v1/admin/visual-search/coverage" in paths
 assert "/api/v1/admin/visual-search/coverage/sources" in paths
 assert all("GET" in r.methods for r in router.routes)
def test_admin_coverage_routes_require_ai_operations_permission():
 for route in router.routes:
  assert route.dependant.dependencies
