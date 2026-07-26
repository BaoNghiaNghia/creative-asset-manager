import unittest

from app.modules.search.query_builder import ElasticsearchQueryBuilder, SearchQueryConfig
from app.modules.search.query_parser import SearchQueryParser


class ElasticsearchQueryBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SearchQueryParser()
        self.builder = ElasticsearchQueryBuilder()

    def build(self, query: str, config: SearchQueryConfig | None = None):
        return self.builder.build(
            self.parser.parse(query), tenant_id="tenant-a", config=config
        )

    def test_every_query_is_tenant_scoped(self) -> None:
        body = self.build("cat")
        self.assertEqual(
            body["query"]["bool"]["filter"], [{"term": {"tenant_id": "tenant-a"}}]
        )

    def test_strict_and_and_or_build_expected_boolean_queries(self) -> None:
        strict = self.build("cat, est, 2015")["query"]["bool"]["must"][0]
        disjunction = self.build("cat OR dog")["query"]["bool"]["must"][0]
        self.assertEqual(len(strict["bool"]["must"]), 3)
        self.assertEqual(disjunction["bool"]["minimum_should_match"], 1)

    def test_soft_and_does_not_require_every_term(self) -> None:
        query = self.build("cat mama")["query"]["bool"]["must"][0]
        self.assertEqual(query["bool"]["minimum_should_match"], "75%")

    def test_number_phrase_and_term_boost_order(self) -> None:
        number_should = self.build("2015")["query"]["bool"]["must"][0]["bool"]["should"]
        phrase_should = self.build('"est 2015"')["query"]["bool"]["must"][0]["bool"]["should"]
        self.assertEqual(number_should[0]["term"]["numbers"]["boost"], 16.0)
        self.assertEqual(next(item["term"]["phrases"]["boost"] for item in phrase_should if "term" in item and "phrases" in item["term"]), 14.0)
        self.assertGreater(14.0, 12.0)
        self.assertGreater(12.0, 8.0)
        self.assertGreater(8.0, 6.0)
        self.assertGreater(6.0, 4.0)
        self.assertGreater(4.0, 2.0)

    def test_qualified_facet_path_and_text(self) -> None:
        config = SearchQueryConfig(
            field_aliases={"text": "search_text"},
            facet_names=frozenset({"subject"}),
            path_aliases={"species": "visual_entities.species"},
        )
        facet = self.build("subject:cat", config)["query"]["bool"]["must"][0]
        path = self.build("species:cat", config)["query"]["bool"]["must"][0]
        text = self.build('text:"mama"', config)["query"]["bool"]["must"][0]
        self.assertIn("facets.subject", facet["term"])
        self.assertEqual(path["nested"]["path"], "path_values")
        self.assertIn("match_phrase", text)

    def test_unknown_qualifier_falls_back_without_dynamic_field_name(self) -> None:
        body = self.build("metadata_json.secret:cat")
        serialized = str(body)
        self.assertNotIn("metadata_json.secret", serialized)
        self.assertIn("search_terms", serialized)

    def test_pagination_is_bounded(self) -> None:
        with self.assertRaises(ValueError):
            self.builder.build(self.parser.parse("cat"), tenant_id="tenant-a", size=1001)


if __name__ == "__main__":
    unittest.main()
