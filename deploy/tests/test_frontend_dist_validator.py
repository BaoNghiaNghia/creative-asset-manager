from __future__ import annotations

import importlib.util
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = ROOT / "deploy" / "tools" / "validate_frontend_dist.py"
SPEC = importlib.util.spec_from_file_location("validate_frontend_dist", MODULE_PATH)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FrontendDistValidatorTest(unittest.TestCase):
    def scan(self, content: str):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "app.js").write_text(content)
            return MODULE.violations(root)

    def test_allows_sensitive_field_names_and_runtime_values(self):
        self.assertEqual(
            self.scan(
                'const label="refresh_token";'
                'const body={refresh_token:formValue,access_token:value,client_secret:secret};'
            ),
            [],
        )

    def test_rejects_literal_refresh_token_without_echoing_value(self):
        value = "1//abcdefghijklmnopqrstuvwxyz123456"
        found = self.scan(f'const body={{refresh_token:"{value}"}};')
        self.assertTrue(found)
        self.assertNotIn(value, repr(found))
        self.assertIn("Google OAuth refresh token", {label for _, label in found})

    def test_rejects_generic_sensitive_literal(self):
        found = self.scan('window.config={client_secret:"abcdefghijklmnop123456"};')
        self.assertEqual(found, [("app.js", "literal OAuth credential")])

    def test_rejects_local_endpoint(self):
        self.assertEqual(
            self.scan('const endpoint="http://localhost:8000/api";'),
            [("app.js", "localhost endpoint")],
        )


if __name__ == "__main__":
    unittest.main()
