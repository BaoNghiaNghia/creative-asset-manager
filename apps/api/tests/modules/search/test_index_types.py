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

    def test_typed_source_and_safe_design_fields_are_indexed(self):
        analysis = self._analysis()
        analysis.search_projection["facets"] = {
            "design_type": ["PetFull", "Other Tags"],
            "unrelated": ["handwriting"],
        }
        analysis.search_projection["path_values"] = [
            {"path": "embroidery.type", "value": "Floral"},
            {"path": "unsafe.path", "value": "Roman"},
        ]
        document = build_search_index_document(
            analysis,
            media_kind="image",
            mime_type="image/jpeg",
            extension="jpg",
            source_provider="google-drive",
            source_created_at="2026-08-01T00:00:00+00:00",
            source_modified_at="2026-08-02T00:00:00+00:00",
            width=1200,
            height=800,
            file_size_bytes=4096,
        )
        payload = document.to_document()
        self.assertEqual(payload["design_type"], ["petfull", "other tags", "floral"])
        self.assertNotIn("handwriting", payload["design_type"])
        self.assertNotIn("roman", payload["design_type"])
        self.assertEqual(payload["media_kind"], "image")
        self.assertEqual(payload["source_provider"], "google-drive")
        self.assertEqual(payload["file_size_bytes"], 4096)
        self.assertTrue(payload["has_visible_text"])
        self.assertTrue(payload["has_ai_metadata"])

    def test_unknown_optional_source_values_are_not_emitted(self):
        payload = build_search_index_document(self._analysis()).to_document()
        for field in (
            "media_kind", "mime_type", "extension", "source_provider",
            "source_created_at", "source_modified_at", "width", "height",
            "duration_ms", "file_size_bytes", "design_type",
        ):
            self.assertNotIn(field, payload)

    def test_v3_document_carries_compact_source_ancestry(self):
        document = build_search_index_document(
            self._analysis(),
            source_id="source-a",
            parent_id="folder-a",
            ancestor_ids=("file-a", "folder-a", "root-a", "folder-a"),
        )
        payload = document.to_document()

        self.assertEqual(payload["source_id"], "source-a")
        self.assertEqual(payload["parent_id"], "folder-a")
        self.assertEqual(payload["ancestor_ids"], ["file-a", "folder-a", "root-a"])


if __name__ == "__main__":
    unittest.main()
