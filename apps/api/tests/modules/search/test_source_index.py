from types import SimpleNamespace
import unittest

from app.modules.search.source_index import SearchSourceIndexResolver


class SearchSourceIndexResolverTest(unittest.TestCase):
    def _resolver(self, parent_map):
        resolver = SearchSourceIndexResolver(session=None)
        resolver._parent_maps[("tenant-a", "source-a")] = parent_map
        return resolver

    def _source(self, external_id, metadata=None, *, tenant_id="tenant-a", source_id="source-a"):
        return SimpleNamespace(
            tenant_id=tenant_id,
            external_source_id=source_id,
            external_asset_id=external_id,
            source_metadata=metadata or {},
            filename="asset.jpg",
        )

    def test_ancestor_ids_include_self_and_every_parent(self):
        resolver = self._resolver({"file-a": ("folder-a",), "folder-a": ("root-a",), "root-a": ()})
        details = resolver.for_source(self._source("file-a", {"parents": ["folder-a"]}))
        self.assertEqual(details.source_id, "source-a")
        self.assertEqual(details.parent_id, "folder-a")
        self.assertEqual(details.ancestor_ids, ("file-a", "folder-a", "root-a"))

    def test_reindex_after_a_move_uses_the_current_parent_chain(self):
        resolver = self._resolver({"file-a": ("new-root",), "new-root": (), "old-root": ()})
        details = resolver.for_source(self._source("file-a", {"parents": ["new-root"]}))
        self.assertEqual(details.ancestor_ids, ("file-a", "new-root"))
        self.assertNotIn("old-root", details.ancestor_ids)

    def test_parent_chains_are_isolated_by_external_source(self):
        resolver = SearchSourceIndexResolver(session=None)
        resolver._parent_maps[("tenant-a", "source-a")] = {
            "file-a": ("root-a",), "root-a": (),
        }
        resolver._parent_maps[("tenant-a", "source-b")] = {
            "file-a": ("root-b",), "root-b": (),
        }
        source_b = SimpleNamespace(
            tenant_id="tenant-a", external_source_id="source-b",
            external_asset_id="file-a", source_metadata={}, filename="asset.jpg",
        )
        self.assertEqual(resolver.for_source(source_b).ancestor_ids, ("file-a", "root-b"))
    def test_cycles_are_bounded_and_cannot_escape_source(self):
        resolver = self._resolver({"file-a": ("folder-a",), "folder-a": ("file-a",)})
        self.assertEqual(resolver.for_source(self._source("file-a")).ancestor_ids, ("file-a", "folder-a"))

    def test_missing_parent_is_retained_without_an_unbounded_lookup(self):
        resolver = self._resolver({"file-a": ("missing-parent",)})
        details = resolver.for_source(self._source("file-a"))
        self.assertEqual(details.ancestor_ids, ("file-a", "missing-parent"))

    def test_parent_maps_are_isolated_by_tenant_and_external_source(self):
        resolver = SearchSourceIndexResolver(session=None)
        resolver._parent_maps[("tenant-a", "source-a")] = {"file-a": ("tenant-a-root",)}
        resolver._parent_maps[("tenant-b", "source-a")] = {"file-a": ("tenant-b-root",)}
        details = resolver.for_source(
            self._source("file-a", tenant_id="tenant-b", source_id="source-a")
        )
        self.assertEqual(details.ancestor_ids, ("file-a", "tenant-b-root"))


if __name__ == "__main__":
    unittest.main()
