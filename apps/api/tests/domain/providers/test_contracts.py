import unittest

from app.domain.providers.contracts import (
    AiMetadataProvider,
    AssetSourceProvider,
    AssetStorageProvider,
    ExternalAssetCandidate,
)
from app.providers.ai.unconfigured import UnconfiguredAiMetadataProvider
from app.providers.google.source_adapter import GoogleDriveSourceAdapter
from app.providers.storage.unconfigured import UnconfiguredAssetStorageProvider


class ProviderContractTest(unittest.TestCase):
    def test_external_asset_candidate_preserves_source_identity(self) -> None:
        candidate = ExternalAssetCandidate(
            source_type="google_drive",
            source_id="drive-connection-1",
            external_asset_id="provider-file-1",
            filename="asset.png",
        )

        self.assertEqual(candidate.source_id, "drive-connection-1")
        self.assertEqual(candidate.external_asset_id, "provider-file-1")

    def test_adapters_satisfy_runtime_checkable_contracts(self) -> None:
        self.assertIsInstance(GoogleDriveSourceAdapter("token"), AssetSourceProvider)
        self.assertIsInstance(UnconfiguredAssetStorageProvider(), AssetStorageProvider)
        self.assertIsInstance(UnconfiguredAiMetadataProvider(), AiMetadataProvider)
