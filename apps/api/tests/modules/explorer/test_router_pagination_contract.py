import inspect
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException

from app.core.config import Settings
from app.modules.explorer.router import _require_legacy_admin, _require_legacy_search, children


class ExplorerRouterPaginationContractTest(unittest.TestCase):
    def test_children_route_exposes_bounded_page_token_contract(self) -> None:
        parameters = inspect.signature(children).parameters

        page_token = parameters["page_token"].default
        page_size = parameters["page_size"].default

        self.assertIsNone(page_token.default)
        self.assertEqual(page_size.default, 100)
        constraints = {type(item).__name__: item for item in page_size.metadata}
        self.assertEqual(constraints["Ge"].ge, 1)
        self.assertEqual(constraints["Le"].le, 200)


class LegacyExplorerSearchGuardTest(unittest.TestCase):
    def test_disabled_legacy_search_is_gone_for_normal_users(self) -> None:
        principal = SimpleNamespace(platform_admin=False, effective_permissions=frozenset({"assets.read"}))
        with patch("app.modules.explorer.router.get_settings", return_value=Settings()):
            with self.assertRaises(HTTPException) as raised:
                _require_legacy_search(principal)
        self.assertEqual(raised.exception.status_code, 410)

    def test_legacy_diagnostics_require_search_rebuild(self) -> None:
        with self.assertRaises(HTTPException) as raised:
            _require_legacy_admin(SimpleNamespace(platform_admin=False, effective_permissions=frozenset()))
        self.assertEqual(raised.exception.status_code, 403)
        _require_legacy_admin(SimpleNamespace(platform_admin=False, effective_permissions=frozenset({"search.rebuild"})))


if __name__ == "__main__":
    unittest.main()
