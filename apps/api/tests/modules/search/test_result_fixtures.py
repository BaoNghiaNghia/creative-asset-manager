import json
import unittest
from pathlib import Path

from app.modules.search.query_parser import ClauseKind, QueryMode, SearchQueryParser


FIXTURE = Path(__file__).parents[2] / "fixtures" / "search_v2" / "results.json"


class RequiredSearchResultFixturesTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.documents = json.loads(FIXTURE.read_text())["documents"]
        cls.parser = SearchQueryParser()

    def ids(self, query: str) -> set[str]:
        parsed = self.parser.parse(query)

        def clause_matches(document, clause) -> bool:
            if clause.kind in {ClauseKind.PHRASE, ClauseKind.QUALIFIED_PHRASE}:
                return clause.value in document["phrases"]
            return clause.value in document["normalized_terms"] or clause.value in document["search_terms"]

        result = set()
        for document in self.documents:
            matches = [clause_matches(document, clause) for clause in parsed.clauses]
            if parsed.mode is QueryMode.STRICT_AND:
                accepted = all(matches)
            else:
                accepted = any(matches)
            if accepted:
                result.add(document["asset_id"])
        return result

    def test_cat_and_mama_find_simple_and_nested_assets(self) -> None:
        expected = {"simple-cat", "nested-cat"}
        self.assertEqual(self.ids("cat"), expected)
        self.assertEqual(self.ids("mama"), expected)

    def test_strict_terms_require_cat_est_and_2015(self) -> None:
        self.assertEqual(self.ids("cat, est, 2015"), {"simple-cat", "nested-cat"})
        self.assertNotIn("dog-est", self.ids("cat, est, 2015"))

    def test_phrase_and_or_semantics(self) -> None:
        self.assertEqual(self.ids('"est 2015"'), {"simple-cat", "nested-cat", "dog-est"})
        self.assertEqual(self.ids("cat OR dog"), {"simple-cat", "nested-cat", "dog-est", "dog-only"})


if __name__ == "__main__":
    unittest.main()
