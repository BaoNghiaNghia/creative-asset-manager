import copy
import unittest
from unittest.mock import patch

from app.modules.ai_metadata.validator import MetadataDocumentValidator


class MetadataDocumentValidatorTest(unittest.TestCase):
    def test_requires_object_root_and_valid_json(self) -> None:
        validator = MetadataDocumentValidator()
        self.assertEqual(validator.validate("{").errors[0].code, "invalid_json")
        self.assertEqual(validator.validate("[]").errors[0].code, "root_object")
        self.assertEqual(validator.validate('{"score": NaN}').errors[0].code, "invalid_json")

    def test_byte_limit_accepts_boundary_and_rejects_next_byte(self) -> None:
        raw = '{"name":"cat"}'
        self.assertTrue(MetadataDocumentValidator(max_bytes=len(raw)).validate(raw).valid)
        result = MetadataDocumentValidator(max_bytes=len(raw) - 1).validate(raw)
        self.assertEqual(result.errors[0].code, "max_bytes")
        self.assertEqual(result.errors[0].actual, len(raw))

    def test_depth_and_node_boundaries(self) -> None:
        value = {"a": {"b": 1}}
        self.assertTrue(MetadataDocumentValidator(max_depth=3, max_nodes=5).validate(value).valid)
        self.assertEqual(
            MetadataDocumentValidator(max_depth=2).validate(value).errors[0].code,
            "max_depth",
        )
        self.assertEqual(
            MetadataDocumentValidator(max_nodes=4).validate(value).errors[0].code,
            "max_nodes",
        )

    def test_array_and_string_boundaries(self) -> None:
        self.assertTrue(
            MetadataDocumentValidator(max_array_items=2, max_string_length=3)
            .validate({"arr": ["cat", "dog"]})
            .valid
        )
        self.assertEqual(
            MetadataDocumentValidator(max_array_items=1)
            .validate({"arr": [1, 2]})
            .errors[0]
            .code,
            "max_array_items",
        )
        self.assertEqual(
            MetadataDocumentValidator(max_string_length=2)
            .validate({"key": "ok"})
            .errors[0]
            .code,
            "max_string_length",
        )
        self.assertEqual(
            MetadataDocumentValidator(max_string_length=3)
            .validate({"key": "long"})
            .errors[0]
            .code,
            "max_string_length",
        )

    def test_optional_json_schema_returns_structured_paths(self) -> None:
        schema = {
            "type": "object",
            "properties": {"subjects": {"type": "array", "items": {"type": "string"}}},
            "required": ["subjects"],
        }
        validator = MetadataDocumentValidator()
        self.assertTrue(validator.validate({"subjects": ["cat"]}, json_schema=schema).valid)
        result = validator.validate({"subjects": [3]}, json_schema=schema)
        self.assertEqual(result.errors[0].code, "json_schema")
        self.assertEqual(result.errors[0].path, ("subjects", 0))

    def test_invalid_schema_is_structured_error(self) -> None:
        result = MetadataDocumentValidator().validate(
            {"subject": "cat"}, json_schema={"type": "not-a-json-schema-type"}
        )
        self.assertEqual(result.errors[0].code, "invalid_schema")

    def test_does_not_mutate_or_alias_original_document(self) -> None:
        original = {"subjects": [{"name": "cat"}]}
        before = copy.deepcopy(original)
        result = MetadataDocumentValidator().validate(original)
        self.assertTrue(result.valid)
        result.document["subjects"][0]["name"] = "dog"
        self.assertEqual(original, before)

    def test_malicious_deep_json_is_rejected_without_recursion_escape(self) -> None:
        raw = '{"a":' * 1_500 + "0" + "}" * 1_500
        result = MetadataDocumentValidator(max_depth=10).validate(raw)
        self.assertFalse(result.valid)
        self.assertIn(result.errors[0].code, {"invalid_json", "max_depth"})

    def test_rejects_excessive_depth_before_recursive_copy(self) -> None:
        raw = '{"a":' * 30 + "0" + "}" * 30
        with patch(
            "app.modules.ai_metadata.validator.copy.deepcopy",
            side_effect=AssertionError("unsafe document was copied"),
        ):
            result = MetadataDocumentValidator(max_depth=10).validate(raw)

        self.assertFalse(result.valid)
        self.assertEqual(result.errors[0].code, "max_depth")


if __name__ == "__main__":
    unittest.main()
