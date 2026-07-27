from types import SimpleNamespace
import unittest

from app.modules.search.index_types import build_search_index_document


class SearchIndexDocumentBuilderTest(unittest.TestCase):
    def _analysis(self):
        return SimpleNamespace(
            asset_id="asset-1",
            tenant_id="tenant-a",
            metadata_json={
                "visible_text": [
                    {"text": "BSN, RN", "normalized": "bsn rn"},
                ],
                "subjects": [{"description": "Nurse badge embroidery"}],
                "product": {"description": "White scrub top"},
                "technical_url": "https://example.test/secret?token=not-searchable",
            },
            search_projection={
                "search_text": "nurse badge",
                "search_terms": ["nurse badge"],
                "normalized_terms": ["nurse", "badge"],
                "phrases": ["nurse badge"],
                "numbers": [],
                "facets": {},
                "path_values": [],
            },
            metadata_profile="creative-assets",
            metadata_profile_version="v1",
            search_projection_version="search-projection-v1",
        )

    def test_short_visible_text_terms_are_preserved_in_normalized_document(self):
        document = build_search_index_document(
            self._analysis(),
            source_id="source-1",
            filename="nurse-badge.jpg",
        )

        self.assertEqual(document.source_id, "source-1")
        self.assertEqual(document.visible_text, ("BSN, RN", "bsn rn"))
        self.assertEqual(document.search_suggest, document.search_text)
        for term in ("bsn", "rn", "nurse", "embroidery"):
            self.assertIn(term, document.search_text.split())
        self.assertNotIn("not-searchable", document.search_text)

    def test_v3_fields_are_emitted_with_v2_safe_defaults(self):
        document = build_search_index_document(self._analysis())
        payload = document.to_document()

        self.assertEqual(payload["source_id"], "")
        self.assertEqual(payload["visible_text"], ["BSN, RN", "bsn rn"])
        self.assertIn("search_suggest", payload)


if __name__ == "__main__":
    unittest.main()
