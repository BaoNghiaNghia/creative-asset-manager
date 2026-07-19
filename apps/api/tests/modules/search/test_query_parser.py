import unittest

from app.modules.search.query_parser import ClauseKind, QueryMode, SearchQueryParser


class SearchQueryParserTest(unittest.TestCase):
    def setUp(self) -> None:
        self.parser = SearchQueryParser()

    def test_single_term(self) -> None:
        parsed = self.parser.parse("Cat")
        self.assertEqual(parsed.mode, QueryMode.SINGLE)
        self.assertEqual(parsed.clauses[0].value, "cat")

    def test_space_separated_terms_are_soft_and(self) -> None:
        parsed = self.parser.parse("cat mama")
        self.assertEqual(parsed.mode, QueryMode.SOFT_AND)
        self.assertEqual([item.value for item in parsed.clauses], ["cat", "mama"])

    def test_comma_separated_terms_are_strict_and(self) -> None:
        parsed = self.parser.parse("cat, est, 2015")
        self.assertEqual(parsed.mode, QueryMode.STRICT_AND)
        self.assertEqual([item.value for item in parsed.clauses], ["cat", "est", "2015"])

    def test_phrase(self) -> None:
        parsed = self.parser.parse('"EST. 2015"')
        self.assertEqual(parsed.mode, QueryMode.SINGLE)
        self.assertEqual(parsed.clauses[0].kind, ClauseKind.PHRASE)
        self.assertEqual(parsed.clauses[0].value, "est 2015")

    def test_explicit_or(self) -> None:
        parsed = self.parser.parse("cat OR dog")
        self.assertEqual(parsed.mode, QueryMode.OR)
        self.assertEqual([item.value for item in parsed.clauses], ["cat", "dog"])

    def test_qualified_term_and_phrase(self) -> None:
        term = self.parser.parse("subject:Cat")
        phrase = self.parser.parse('text:"MAMA"')
        self.assertEqual(term.clauses[0].kind, ClauseKind.QUALIFIED_TERM)
        self.assertEqual((term.clauses[0].field, term.clauses[0].value), ("subject", "cat"))
        self.assertEqual(phrase.clauses[0].kind, ClauseKind.QUALIFIED_PHRASE)
        self.assertEqual((phrase.clauses[0].field, phrase.clauses[0].value), ("text", "mama"))

    def test_malformed_syntax_falls_back_to_plain_terms(self) -> None:
        for query in ('"unterminated', "cat OR", "cat,,dog", ":cat"):
            with self.subTest(query=query):
                parsed = self.parser.parse(query)
                self.assertEqual(parsed.mode, QueryMode.FALLBACK)
                self.assertTrue(parsed.clauses)


if __name__ == "__main__":
    unittest.main()
