from __future__ import annotations

import unittest
from datetime import date

from app.modules.inventory.exports.service import InventoryExportFailure, InventoryExportService
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.transactions.service import InventoryBusinessFailure, InventoryDocumentCommitter


class Phase11ShadowModeTest(unittest.TestCase):
    def test_shadow_mode_blocks_irreversible_export_before_provider_access(self):
        service = InventoryExportService(None, shadow_mode=True)
        with self.assertRaisesRegex(InventoryExportFailure, "inventory_shadow_mode_export_blocked"):
            service.export("tenant-a", date(2030, 1, 1))

    def test_shadow_mode_blocks_ledger_commit_before_database_access(self):
        committer = InventoryDocumentCommitter(None, shadow_mode=True)
        job = InventoryJobModel(
            tenant_id="tenant-a", job_type="inventory_document_commit",
            entity_type="inventory_document", entity_id="document-a",
            idempotency_key="shadow-a", payload_json={"document_id": "document-a"},
        )
        with self.assertRaisesRegex(InventoryBusinessFailure, "inventory_shadow_mode_commit_blocked"):
            committer.execute(job)
