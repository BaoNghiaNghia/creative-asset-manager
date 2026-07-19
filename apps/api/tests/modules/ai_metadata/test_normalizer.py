import unittest

from app.modules.ai_metadata.normalizer import MetadataNormalizer
from app.modules.ai_metadata.traverser import ExtractedMetadataValue


def extracted(value: str, path: str = "label") -> ExtractedMetadataValue:
    return ExtractedMetadataValue(path, value, "string")


class MetadataNormalizerTest(unittest.TestCase):
    def test_required_examples(self) -> None:
        normalizer = MetadataNormalizer()
        cases = {
            "Cat": ("cat", ("cat",), (), ()),
            "MAMA": ("mama", ("mama",), (), ()),
            "EST. 2015": (
                "est 2015",
                ("est", "2015"),
                ("2015",),
                ("est 2015",),
            ),
            "cat-est_2015": (
                "cat est 2015",
                ("cat", "est", "2015"),
                ("2015",),
                ("cat est 2015",),
            ),
            '  "Front   Chest" ': (
                "front chest",
                ("front", "chest"),
                (),
                ("front chest",),
            ),
        }
        for original, expected in cases.items():
            with self.subTest(original=original):
                result = normalizer.normalize(extracted(original))
                self.assertIsNotNone(result)
                self.assertEqual(
                    (
                        result.normalized_value,
                        result.tokens,
                        result.numbers,
                        result.phrases,
                    ),
                    expected,
                )

    def test_unicode_nfkc_casefold_and_whitespace(self) -> None:
        result = MetadataNormalizer().normalize(extracted(" ＣＡＴ\tCafé  "))
        self.assertEqual(result.normalized_value, "cat café")
        self.assertEqual(result.tokens, ("cat", "café"))

    def test_meaningful_short_words_are_not_removed(self) -> None:
        result = MetadataNormalizer().normalize(
            extracted("est mom mama dad", "audience")
        )
        self.assertEqual(result.tokens, ("est", "mom", "mama", "dad"))

    def test_duplicate_terms_are_removed_in_first_seen_order(self) -> None:
        result = MetadataNormalizer().normalize(extracted("cat CAT cat MAMA mama"))
        self.assertEqual(result.tokens, ("cat", "mama"))
        self.assertEqual(result.phrases, ("cat mama",))

    def test_empty_punctuation_only_value_is_ignored(self) -> None:
        self.assertIsNone(MetadataNormalizer().normalize(extracted(" -- ___ ... ")))

    def test_term_and_phrase_limits_are_enforced(self) -> None:
        result = MetadataNormalizer(
            max_terms_per_value=2,
            max_phrase_chars=7,
        ).normalize(extracted("one two three four"))
        self.assertEqual(result.tokens, ("one", "two"))
        self.assertEqual(result.phrases, ("one two",))

        without_phrase = MetadataNormalizer(
            max_terms_per_value=2,
            max_phrase_chars=3,
        ).normalize(extracted("one two"))
        self.assertEqual(without_phrase.phrases, ())

    def test_normalize_all_is_deterministic_and_bounded(self) -> None:
        values = [
            extracted("MAMA", "z"),
            extracted("Cat", "a"),
            extracted("EST. 2015", "b"),
        ]
        normalizer = MetadataNormalizer(max_values=2)
        first = normalizer.normalize_all(values)
        second = normalizer.normalize_all(reversed(values))
        self.assertEqual(first, second)
        self.assertEqual([item.path for item in first], ["a", "b"])

    def test_source_value_is_unchanged(self) -> None:
        source = extracted("  Front   Chest ")
        MetadataNormalizer().normalize(source)
        self.assertEqual(source.original_value, "  Front   Chest ")

    def test_invalid_limits_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MetadataNormalizer(max_terms_per_value=0)


if __name__ == "__main__":
    unittest.main()
