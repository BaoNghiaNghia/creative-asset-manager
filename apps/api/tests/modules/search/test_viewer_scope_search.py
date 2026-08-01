from types import SimpleNamespace
from unittest.mock import patch
import unittest

from app.modules.search.router import _viewer_scope_filter


def _matches_scope(document, clause):
    for branch in clause["bool"]["should"]:
        filters = branch["bool"]["filter"]
        source_id = filters[0]["term"]["source_id"]
        allowed_ancestors = set(filters[1]["terms"]["ancestor_ids"])
        if document["source_id"] == source_id and allowed_ancestors.intersection(document["ancestor_ids"]):
            return True
    return False


class ViewerScopeSearchFilterTest(unittest.TestCase):
    def _viewer(self, roles=frozenset({"viewer"}), membership_id="membership-a"):
        return SimpleNamespace(active_tenant_id="tenant-a", membership_id=membership_id, effective_roles=roles)

    @patch("app.modules.search.router.ViewerFolderScopeService")
    def test_v3_scope_is_compact_and_allows_selected_folder_and_descendants_only(self, service_type):
        service_type.return_value.list_membership_scopes.return_value = {"source-a": {"root-a"}, "source-b": {"root-b"}}
        clause, cache_key = _viewer_scope_filter(object(), self._viewer(), generation="v3")
        self.assertEqual(cache_key, (("source-a", ("root-a",)), ("source-b", ("root-b",))))
        self.assertNotIn("asset_id", str(clause))
        self.assertEqual(len(clause["bool"]["should"]), 2)
        self.assertTrue(_matches_scope({"source_id": "source-a", "ancestor_ids": ["root-a"]}, clause))
        self.assertTrue(_matches_scope({"source_id": "source-a", "ancestor_ids": ["child-a", "root-a"]}, clause))
        self.assertFalse(_matches_scope({"source_id": "source-a", "ancestor_ids": ["sibling-a", "other-root"]}, clause))
        self.assertFalse(_matches_scope({"source_id": "source-b", "ancestor_ids": ["root-a"]}, clause))
        self.assertFalse(_matches_scope({"source_id": "source-a", "ancestor_ids": ["ancestor-above-root"]}, clause))

    @patch("app.modules.search.router.ViewerFolderScopeService")
    def test_viewer_without_scope_and_legacy_index_fail_closed(self, service_type):
        service_type.return_value.list_membership_scopes.return_value = {}
        clause, _ = _viewer_scope_filter(object(), self._viewer(), generation="v3")
        self.assertEqual(clause, {"match_none": {}})
        legacy_clause, _ = _viewer_scope_filter(object(), self._viewer(), generation="v2")
        self.assertEqual(legacy_clause, {"match_none": {}})

    @patch("app.modules.search.router.ViewerFolderScopeService")
    def test_viewer_without_durable_membership_fails_closed(self, service_type):
        clause, cache_key = _viewer_scope_filter(
            object(), self._viewer(membership_id=None), generation="v3"
        )
        self.assertEqual(clause, {"match_none": {}})
        self.assertEqual(cache_key, ())
        service_type.assert_not_called()
    def test_non_viewer_roles_are_not_restricted(self):
        for roles in (frozenset({"operator"}), frozenset({"tenant_admin"}), frozenset({"billing_admin"}), frozenset({"viewer", "operator"})):
            clause, cache_key = _viewer_scope_filter(object(), self._viewer(roles), generation="v3")
            self.assertIsNone(clause)
            self.assertIsNone(cache_key)


if __name__ == "__main__":
    unittest.main()
