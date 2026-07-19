import copy
import unittest

from app.modules.ai_metadata.traverser import (
    MetadataTraverser,
    TraversalLimits,
    normalize_logical_path,
)


class MetadataTraverserTest(unittest.TestCase):
    def test_traverses_objects_arrays_and_normalizes_array_paths(self) -> None:
        document = {
            "visual_entities": [
                {"species": "Cat", "confidence": 0.98},
                None,
                {"species": "Dog"},
            ],
            "year": 2015,
        }
        values = MetadataTraverser().traverse(document)
        self.assertEqual(
            [(item.path, item.original_value, item.value_type) for item in values],
            [
                ("visual_entities.confidence", "0.98", "number"),
                ("visual_entities.species", "Cat", "string"),
                ("visual_entities.species", "Dog", "string"),
                ("year", "2015", "number"),
            ],
        )
        self.assertEqual(
            normalize_logical_path("visual_entities[4].species"),
            "visual_entities.species",
        )

    def test_booleans_are_optional_and_nulls_are_ignored(self) -> None:
        document = {"approved": True, "archived": False, "empty": None}
        self.assertEqual(MetadataTraverser().traverse(document), ())
        values = MetadataTraverser(include_booleans=True).traverse(document)
        self.assertEqual(
            [(item.path, item.original_value) for item in values],
            [("approved", "true"), ("archived", "false")],
        )

    def test_global_sensitive_paths_are_excluded(self) -> None:
        document = {
            "accessToken": "secret",
            "asset_url": "not-even-a-url",
            "analysis": {
                "embedding": [0.1, 0.2],
                "coordinates": [10, 20],
                "boundingBoxes": [{"x": 1}],
                "providerRequestId": "request-1",
                "debugPayload": {"trace": "hidden"},
                "featureVector": [0.3, 0.4],
                "gpsCoordinates": [30, 40],
                "detectedBoundingBoxes": [{"y": 2}],
                "geminiProviderRequestId": "request-2",
                "caption": "keep me",
            },
        }
        values = MetadataTraverser(include_booleans=True).traverse(document)
        self.assertEqual(
            [(item.path, item.original_value) for item in values],
            [("analysis.caption", "keep me")],
        )

    def test_sensitive_string_values_are_excluded(self) -> None:
        document = {
            "description": "https://example.com/file?signature=secret",
            "opaque": "A" * 80,
            "session": "eyJhbGciOiJIUzI1NiJ9.abcdefghijk.abcdefghijkl",
            "caption": "A normal creative description",
        }
        values = MetadataTraverser().traverse(document)
        self.assertEqual(
            [(item.path, item.original_value) for item in values],
            [("caption", "A normal creative description")],
        )

    def test_profile_and_global_exclude_paths_remove_subtrees(self) -> None:
        document = {
            "campaign": {"private": {"notes": "hidden"}, "name": "Launch"},
            "internal": {"owner": "hidden"},
        }
        traverser = MetadataTraverser(global_exclude_paths=("internal",))
        values = traverser.traverse(
            document, exclude_paths=("campaign.private[0]",)
        )
        self.assertEqual(
            [(item.path, item.original_value) for item in values],
            [("campaign.name", "Launch")],
        )

    def test_limits_stop_safely(self) -> None:
        array_values = MetadataTraverser(
            limits=TraversalLimits(max_array_items=2)
        ).traverse({"items": [1, 2, 3, 4]})
        self.assertEqual(
            [item.original_value for item in array_values],
            ["1", "2"],
        )

        extracted_values = MetadataTraverser(
            limits=TraversalLimits(max_extracted_values=2)
        ).traverse({"a": 1, "b": 2, "c": 3})
        self.assertEqual(len(extracted_values), 2)

        deep_values = MetadataTraverser(
            limits=TraversalLimits(max_depth=1)
        ).traverse({"level": {"value": "too deep"}, "root": "kept"})
        self.assertEqual(
            [(item.path, item.original_value) for item in deep_values],
            [("root", "kept")],
        )

        node_values = MetadataTraverser(
            limits=TraversalLimits(max_nodes=2)
        ).traverse({"a": 1, "b": 2, "c": 3})
        self.assertLessEqual(len(node_values), 1)

    def test_input_is_not_mutated_and_cycles_stop(self) -> None:
        document = {"items": [{"name": "Cat"}]}
        before = copy.deepcopy(document)
        MetadataTraverser().traverse(document)
        self.assertEqual(document, before)

        cyclic = {"name": "safe"}
        cyclic["cycle"] = cyclic
        values = MetadataTraverser().traverse(cyclic)
        self.assertEqual(
            [(item.path, item.original_value) for item in values],
            [("name", "safe")],
        )

    def test_order_is_deterministic_for_different_mapping_insertion_order(self) -> None:
        first = {"z": "MAMA", "a": {"year": 2015, "subject": "Cat"}}
        second = {"a": {"subject": "Cat", "year": 2015}, "z": "MAMA"}
        self.assertEqual(
            MetadataTraverser().traverse(first),
            MetadataTraverser().traverse(second),
        )

    def test_invalid_limits_and_non_object_root_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TraversalLimits(max_nodes=0)
        with self.assertRaises(TypeError):
            MetadataTraverser().traverse([])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
