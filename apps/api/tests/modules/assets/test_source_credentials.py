import unittest

from app.modules.assets.source_credentials import source_credential_contract


class SourceCredentialContractTest(unittest.TestCase):
    def test_supported_contracts(self):
        expected = {
            "google_drive": ("google", "google_drive_source", "google-drive"),
            "sharepoint": ("microsoft", "sharepoint_source", "sharepoint"),
            "onedrive": ("microsoft", "onedrive_source", "onedrive"),
        }
        for source_type, values in expected.items():
            with self.subTest(source_type=source_type):
                contract = source_credential_contract(source_type)
                self.assertEqual(
                    (contract.provider, contract.connection_purpose, contract.adapter_key),
                    values,
                )

    def test_unknown_source_type_is_bounded(self):
        with self.assertRaises(ValueError):
            source_credential_contract("unknown")


if __name__ == "__main__":
    unittest.main()
