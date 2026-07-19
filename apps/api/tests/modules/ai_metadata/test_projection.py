import copy
import inspect
import json
import unittest
from pathlib import Path

from app.modules.ai_metadata.projection import (
    ProjectionLimits,
    SearchProjectionBuilder,
)

FIXTURES = Path(__file__).parents[2] / "fixtures" / "search_projection"


class SearchProjectionBuilderTest(unittest.TestCase):
    def test_flat_and_deep_schemas_expose_equivalent_search_output(self) -> None:
        simple = json.loads((FIXTURES / "simple_metadata.json").read_text())
        deep = json.loads((FIXTURES / "deep_metadata.json").read_text())
        builder = SearchProjectionBuilder()

        simple_projection = builder.build(simple).projection
        deep_projection = builder.build(deep).projection

        comparable = (
            "search_text",
            "search_terms",
            "normalized_terms",
            "phrases",
            "numbers",
        )
        for field in comparable:
            self.assertEqual(
                getattr(simple_projection, field),
                getattr(deep_projection, field),
                field,
            )
        self.assertEqual(
            simple_projection.normalized_terms,
            ("2015", "cat", "est", "mama"),
        )
        self.assertEqual(simple_projection.phrases, ("est 2015",))
        self.assertEqual(simple_projection.numbers, ("2015",))

    def test_facets_text_paths_and_include_all_are_profile_driven(self) -> None:
        metadata = {
            "visual_entities": [
                {"species": "Cat", "color": "Black"},
                {"species": "Cat"},
            ],
            "copy": {"headline": "MAMA EST. 2015"},
            "ignored": "Should not be searchable",
        }
        result = SearchProjectionBuilder().build(
            metadata,
            {
                "include_all_scalar_values": False,
                "text_paths": ["copy"],
                "facet_paths": {
                    "subject": ["visual_entities[0].species"],
                },
            },
        )
        projection = result.projection
        self.assertEqual(projection.facets, {"subject": ("cat",)})
        self.assertIn("cat", projection.normalized_terms)
        self.assertIn("mama", projection.normalized_terms)
        self.assertNotIn("black", projection.normalized_terms)
        self.assertNotIn("searchable", projection.normalized_terms)
        self.assertEqual(
            [(item.path, item.value) for item in projection.path_values],
            [
                ("copy.headline", "mama est 2015"),
                ("visual_entities.species", "cat"),
            ],
        )

    def test_excluded_paths_and_sensitive_values_never_reach_projection(self) -> None:
        projection = SearchProjectionBuilder().build(
            {
                "caption": "Public Cat",
                "private": {"notes": "MAMA"},
                "image_url": "https://example.com/cat.jpg?signature=secret",
                "embedding": [0.1, 0.2],
            },
            {"exclude_paths": ["private"]},
        ).projection
        self.assertEqual(projection.normalized_terms, ("cat", "public"))
        self.assertEqual(projection.facets, {})
        self.assertEqual(len(projection.path_values), 1)

    def test_booleans_are_only_included_when_configured(self) -> None:
        builder = SearchProjectionBuilder()
        without = builder.build({"approved": True}).projection
        with_boolean = builder.build(
            {"approved": True}, {"include_booleans": True}
        ).projection
        self.assertEqual(without.normalized_terms, ())
        self.assertEqual(with_boolean.normalized_terms, ("true",))

    def test_boost_paths_are_separate_query_configuration(self) -> None:
        result = SearchProjectionBuilder(projection_version="projection-v7").build(
            {"subject": "Cat"},
            {
                "boost_paths": {
                    "subject": 3,
                    "ignored": "invalid",
                    "negative": -1,
                }
            },
        )
        document = result.projection.to_document()
        self.assertEqual(result.projection_version, "projection-v7")
        self.assertEqual(result.query_config, {"boost_paths": {"subject": 3.0}})
        self.assertNotIn("projection_version", document)
        self.assertNotIn("boost_paths", document)
        self.assertEqual(
            set(document),
            {
                "search_text",
                "search_terms",
                "normalized_terms",
                "phrases",
                "numbers",
                "facets",
                "path_values",
            },
        )

    def test_duplicates_and_projection_limits_are_enforced(self) -> None:
        projection = SearchProjectionBuilder(
            limits=ProjectionLimits(
                max_search_text_chars=7,
                max_search_terms=2,
                max_normalized_terms=2,
                max_phrases=1,
                max_numbers=1,
                max_facets=1,
                max_facet_values=1,
                max_path_values=2,
                max_term_chars=20,
                max_path_value_chars=20,
                max_boost_paths=1,
            )
        ).build(
            {
                "a": ["Cat", "Cat"],
                "b": "MAMA",
                "c": "EST. 2015",
            },
            {"facet_paths": ["a", "b"]},
        ).projection
        self.assertEqual(projection.search_terms, ("cat", "est 2015"))
        self.assertEqual(projection.normalized_terms, ("2015", "cat"))
        self.assertEqual(projection.numbers, ("2015",))
        self.assertEqual(projection.search_text, "cat")
        self.assertEqual(projection.facets, {"a": ("cat",)})
        self.assertEqual(len(projection.path_values), 2)

    def test_build_is_deterministic_and_does_not_mutate_metadata(self) -> None:
        metadata = {
            "z": "EST. 2015",
            "a": [{"label": "MAMA"}, {"label": "Cat"}],
        }
        before = copy.deepcopy(metadata)
        builder = SearchProjectionBuilder()
        first = builder.build(metadata).projection.to_document()
        second = builder.build(
            {"a": [{"label": "MAMA"}, {"label": "Cat"}], "z": "EST. 2015"}
        ).projection.to_document()
        self.assertEqual(first, second)
        self.assertEqual(metadata, before)

    def test_projection_module_has_no_ai_provider_dependency(self) -> None:
        source = inspect.getsource(__import__(
            "app.modules.ai_metadata.projection",
            fromlist=["SearchProjectionBuilder"],
        ))
        self.assertNotIn("AiMetadataProvider", source)
        self.assertNotIn("analyze_single", source)

    def test_invalid_version_config_and_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            SearchProjectionBuilder(projection_version=" ")
        with self.assertRaises(ValueError):
            ProjectionLimits(max_search_terms=0)
        with self.assertRaises(TypeError):
            SearchProjectionBuilder().build({}, [])  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
