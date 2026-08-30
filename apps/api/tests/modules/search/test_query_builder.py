import unittest

from app.modules.search.query_builder import (
    ElasticsearchQueryBuilder,
    SearchQueryConfig,
    decode_search_cursor,
    encode_search_cursor,
)
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

    def test_search_after_uses_deterministic_sort_without_offset(self) -> None:
        cursor = [1.25, "asset-2"]
        body = self.builder.build(
            self.parser.parse("cat"),
            tenant_id="tenant-a",
            size=60,
            search_after=cursor,
        )
        self.assertEqual(body["sort"], [{"_score": "desc"}, {"asset_id": "asc"}])
        self.assertEqual(body["search_after"], cursor)
        self.assertNotIn("from", body)
        with self.assertRaises(ValueError):
            self.builder.build(
                self.parser.parse("cat"),
                tenant_id="tenant-a",
                offset=60,
                search_after=cursor,
            )

    def test_cursor_round_trip_and_rejects_malformed_values(self) -> None:
        cursor = encode_search_cursor([1.25, "asset-2"])
        self.assertEqual(decode_search_cursor(cursor), [1.25, "asset-2"])
        for value in ("not-a-cursor", "e30", "a" * 513):
            with self.assertRaises(ValueError):
                decode_search_cursor(value)

    def test_filter_only_query_uses_bool_filters_without_match_none(self) -> None:
        body = self.builder.build(
            self.parser.parse(""),
            tenant_id="tenant-a",
        )
        self.assertEqual(
            body["query"],
            {"bool": {"filter": [{"term": {"tenant_id": "tenant-a"}}]}},
        )
        self.assertNotIn("match_none", str(body))

    def test_plain_term_uses_tiered_dis_max_and_bounded_fuzzy_fallback(self) -> None:
        short_should = self.build("cat")["query"]["bool"]["must"][0]["bool"]["should"]
        long_should = self.build("kitten")["query"]["bool"]["must"][0]["bool"]["should"]
        self.assertTrue(any("dis_max" in item for item in short_should))
        self.assertFalse(any("fuzziness" in str(item) for item in short_should))
        fuzzy = next(item["multi_match"] for item in long_should if "multi_match" in item and "fuzziness" in item["multi_match"])
        self.assertEqual(fuzzy["prefix_length"], 1)
        self.assertLessEqual(fuzzy["max_expansions"], 24)
        self.assertLess(fuzzy["boost"], self.builder.SEARCH_TEXT_BOOST)
        self.assertLessEqual(len(long_should), 8)

    def test_exact_filename_and_phrase_are_stronger_than_lexical_or_fuzzy(self) -> None:
        term_should = self.build("kitten")["query"]["bool"]["must"][0]["bool"]["should"]
        exact_filename = next(
            item["term"]["filename.normalized"]["boost"]
            for item in term_should
            if "term" in item and "filename.normalized" in item["term"]
        )
        lexical = next(item["dis_max"] for item in term_should if "dis_max" in item)
        lexical_boosts = [
            next(iter(next(iter(query.values())).values()))["boost"]
            for query in lexical["queries"]
        ]
        fuzzy = next(item["multi_match"] for item in term_should if "multi_match" in item)
        self.assertGreater(exact_filename, max(lexical_boosts))
        self.assertGreater(min(lexical_boosts), fuzzy["boost"])

        phrase_should = self.build('"summer design"')["query"]["bool"]["must"][0]["bool"]["should"]
        self.assertTrue(any(
            "term" in item and "phrases" in item["term"]
            for item in phrase_should
        ))

    def test_qualified_analyzed_terms_use_match_not_term(self) -> None:
        for query, field in (
            ("filename:summer_design", "filename"),
            ("folder:summer_design", "folder_path"),
            ("text:summer_design", "search_text"),
        ):
            clause = self.build(query)["query"]["bool"]["must"][0]
            self.assertIn("match", clause)
            self.assertIn(field, clause["match"])
            self.assertNotIn("term", clause)
        phrase = self.build('filename:"summer design"')["query"]["bool"]["must"][0]
        self.assertIn("match_phrase", phrase)

    def test_result_source_is_minimal_and_highlighting_is_not_requested(self) -> None:
        body = self.build("cat")
        self.assertEqual(body["_source"], ["asset_id", "source_id", "filename", "folder_path"])
        self.assertNotIn("highlight", body)


if __name__ == "__main__":
    unittest.main()
