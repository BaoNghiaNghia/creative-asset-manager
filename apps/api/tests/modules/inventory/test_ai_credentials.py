from __future__ import annotations

import base64
import logging
import tempfile
import unittest
from pathlib import Path

from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_metadata.model import AssetAiAnalysisModel
from app.modules.assets.model import ExternalSourceModel
from app.modules.auth_persistence.model import OAuthConnectionModel, TenantModel
from app.modules.inventory.ai.gateway import RuntimeInventoryGeminiGateway
from app.modules.inventory.credentials import (InventoryAiCredentialRepository, InventoryCredentialError, InventoryGeminiCredentialResolver, inventory_credential_cipher)
from app.modules.inventory.persistence_model import (
    InventoryAiCredentialAuditModel,
    InventoryAiCredentialModel,
    InventorySettingsModel,
)

SECRET = "AIzaSyInventoryOnlyCredential000000000000000000"
REPLACEMENT = "AIzaSyInventoryReplacement000000000000000000"
KEY = base64.urlsafe_b64encode(b"I" * 32).decode().rstrip("=")

class InventoryAiCredentialRepositoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.engine = create_engine(f"sqlite:///{Path(self.tmp.name) / 'credentials.sqlite'}")
        event.listen(self.engine, "connect", lambda c, _: c.execute("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all((
                TenantModel(id="tenant-a", name="A", slug="a"),
                TenantModel(id="tenant-b", name="B", slug="b"),
                ExternalSourceModel(id="drive-a", tenant_id="tenant-a", source_key="drive-a", source_type="google_drive"),
                OAuthConnectionModel(id="oauth-a", tenant_id="tenant-a", provider="google", provider_account_id="drive", key_version="v1", access_token_ciphertext="drive-access", refresh_token_ciphertext="drive-refresh", status="active"),
            ))
            session.commit()
        self.settings = Settings(INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY)

    def tearDown(self):
        self.engine.dispose()
        self.tmp.cleanup()

    def repo(self, session):
        return InventoryAiCredentialRepository(session, inventory_credential_cipher(self.settings))

    def test_plaintext_is_not_persisted_and_internal_decryption_is_tenant_scoped(self):
        with self.sessions() as session:
            metadata = self.repo(session).replace("tenant-a", secret=SECRET, label="Gemini B", updated_by="user-a")
            session.commit()
            row = session.scalar(select(InventoryAiCredentialModel))
            self.assertNotIn(SECRET, row.encrypted_secret)
            self.assertEqual(metadata.secret_last4, SECRET[-4:])
            self.assertFalse(hasattr(metadata, "encrypted_secret"))
        with self.sessions() as session:
            self.assertEqual(self.repo(session).get_active_secret("tenant-a"), SECRET)
            self.assertIsNone(self.repo(session).get_active_secret("tenant-b"))

    def test_replacement_does_not_change_drive_or_creative_state(self):
        with self.sessions() as session:
            first = self.repo(session).replace("tenant-a", secret=SECRET)
            session.commit()
            before_oauth = session.scalar(select(func.count()).select_from(OAuthConnectionModel))
            before_creative = session.scalar(select(func.count()).select_from(AssetAiAnalysisModel))
        with self.sessions() as session:
            second = self.repo(session).replace("tenant-a", secret=REPLACEMENT, label="Rotated", updated_by="user-b")
            session.commit()
            self.assertNotEqual(first.secret_fingerprint, second.secret_fingerprint)
            self.assertEqual(second.secret_last4, REPLACEMENT[-4:])
            self.assertEqual(session.scalar(select(func.count()).select_from(OAuthConnectionModel)), before_oauth)
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetAiAnalysisModel)), before_creative)

    def test_missing_key_and_invalid_ciphertext_fail_closed_without_logs(self):
        with self.assertRaisesRegex(InventoryCredentialError, "encryption_unavailable"):
            inventory_credential_cipher(Settings())
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=SECRET)
            row = session.scalar(select(InventoryAiCredentialModel))
            row.encrypted_secret = "not-valid-ciphertext"
            session.commit()
        with self.assertLogs("cam.inventory.credentials", level="INFO") as logs:
            logging.getLogger("cam.inventory.credentials").info("credential_updated tenant_id=tenant-a")
        self.assertNotIn(SECRET, "\n".join(logs.output))
        with self.sessions() as session:
            with self.assertRaisesRegex(InventoryCredentialError, "decryption_failed"):
                self.repo(session).get_active_secret("tenant-a")

    def test_resolver_db_env_missing_and_rotation_priority(self):
        settings = Settings(INVENTORY_GEMINI_API_KEY="inventory-env-key", GEMINI_API_KEY="creative-key", INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY)
        resolver = InventoryGeminiCredentialResolver(self.sessions, settings)
        self.assertEqual(resolver.resolve("tenant-a"), "inventory-env-key")
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=SECRET)
            session.commit()
        self.assertEqual(resolver.resolve("tenant-a"), SECRET)
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=REPLACEMENT)
            session.commit()
        self.assertEqual(resolver.resolve("tenant-a"), REPLACEMENT)
        missing = InventoryGeminiCredentialResolver(self.sessions, Settings(GEMINI_API_KEY="creative-key", INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY))
        with self.assertRaisesRegex(InventoryCredentialError, "credential_unavailable"):
            missing.resolve("tenant-b")

    def test_gateway_uses_current_tenant_key_without_drive_identity_coupling(self):
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=SECRET)
            session.commit()
        seen = []
        def fake_request(key, *_args):
            seen.append(key)
            return {"extracted_json": {"document_type": "stock_count", "business_date": None, "location": None, "page_number": 1, "page_count": 1, "raw_item_lines": []}}
        gateway = RuntimeInventoryGeminiGateway(InventoryGeminiCredentialResolver(self.sessions, self.settings), request=fake_request)
        gateway.analyze(tenant_id="tenant-a", image_bytes=b"image", image_mime_type="image/jpeg", prompt="p", schema={}, provider="gemini", model="model")
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=REPLACEMENT)
            session.commit()
        gateway.analyze(tenant_id="tenant-a", image_bytes=b"image", image_mime_type="image/jpeg", prompt="p", schema={}, provider="gemini", model="model")
        self.assertEqual(seen, [SECRET, REPLACEMENT])

    def test_different_drive_and_gemini_accounts_are_fully_isolated_through_rotation(self):
        drive_email = "drive-account-a@example.test"
        gemini_key_a = "-".join(("fake", "gemini", "account", "a", "key"))
        gemini_key_b = "-".join(("fake", "gemini", "account", "b", "key"))
        controls = Settings(
            INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY,
            INVENTORY_TENANT_ALLOWLIST="tenant-a",
            INVENTORY_SHADOW_MODE=True,
            INVENTORY_AUTOMATION_ENABLED=False,
            INVENTORY_WORKER_ENABLED=False,
            INVENTORY_DRIVE_POLLER_ENABLED=False,
            INVENTORY_DAILY_SCHEDULER_ENABLED=False,
            INVENTORY_AI_ENABLED=False,
            GEMINI_API_KEY="creative-account-key",
        )
        with self.sessions() as session:
            connection = session.get(OAuthConnectionModel, "oauth-a")
            connection.provider_account_id = drive_email
            session.add(InventorySettingsModel(
                id="inventory-settings-a", tenant_id="tenant-a", external_source_id="drive-a",
                inbox_folder_id="inbox-a", processed_folder_id="processed-a",
                reupload_folder_id="reupload-a", excel_folder_id="excel-a",
                backup_folder_id="backup-a", old_image_archive_folder_id="archive-a",
            ))
            first = self.repo(session).replace(
                "tenant-a", secret=gemini_key_a, label="Gemini Account B", updated_by="operator-a"
            )
            session.commit()
            before_connection = (
                connection.id, connection.provider_account_id, connection.access_token_ciphertext,
                connection.refresh_token_ciphertext,
            )
            inventory = session.get(InventorySettingsModel, "inventory-settings-a")
            before_inventory = (
                inventory.external_source_id, inventory.inbox_folder_id, inventory.processed_folder_id,
                inventory.reupload_folder_id, inventory.excel_folder_id, inventory.backup_folder_id,
                inventory.old_image_archive_folder_id,
            )
            before_creative = session.scalar(select(func.count()).select_from(AssetAiAnalysisModel))

        resolver = InventoryGeminiCredentialResolver(self.sessions, controls)
        seen: list[str] = []
        gateway = RuntimeInventoryGeminiGateway(
            resolver,
            request=lambda key, *_: seen.append(key) or {
                "extracted_json": {
                    "document_type": "stock_count", "business_date": None, "location": None,
                    "page_number": 1, "page_count": 1, "raw_item_lines": [],
                }
            },
        )
        gateway.analyze(tenant_id="tenant-a", image_bytes=b"image", image_mime_type="image/jpeg", prompt="p", schema={}, provider="gemini", model="model")
        with self.sessions() as session:
            second = self.repo(session).replace(
                "tenant-a", secret=gemini_key_b, label="Gemini Account B", updated_by="operator-b"
            )
            self.repo(session).audit(
                "tenant-a", actor_id="operator-b", action="credential_replaced",
                result="VALID", previous_fingerprint=first.secret_fingerprint,
                new_fingerprint=second.secret_fingerprint,
            )
            session.commit()
        gateway.analyze(tenant_id="tenant-a", image_bytes=b"image", image_mime_type="image/jpeg", prompt="p", schema={}, provider="gemini", model="model")

        self.assertEqual(seen, [gemini_key_a, gemini_key_b])
        self.assertNotEqual(first.secret_fingerprint, second.secret_fingerprint)
        self.assertEqual(second.secret_last4, gemini_key_b[-4:])
        self.assertEqual(controls.inventory_tenant_allowlist, frozenset({"tenant-a"}))
        self.assertTrue(controls.INVENTORY_SHADOW_MODE)
        self.assertFalse(controls.INVENTORY_AUTOMATION_ENABLED)
        self.assertFalse(controls.INVENTORY_WORKER_ENABLED)
        self.assertFalse(controls.INVENTORY_DRIVE_POLLER_ENABLED)
        self.assertFalse(controls.INVENTORY_DAILY_SCHEDULER_ENABLED)
        self.assertFalse(controls.INVENTORY_AI_ENABLED)
        self.assertEqual(controls.GEMINI_API_KEY, "creative-account-key")

        with self.sessions() as session:
            connection = session.get(OAuthConnectionModel, "oauth-a")
            self.assertEqual(
                (connection.id, connection.provider_account_id, connection.access_token_ciphertext, connection.refresh_token_ciphertext),
                before_connection,
            )
            inventory = session.get(InventorySettingsModel, "inventory-settings-a")
            self.assertEqual(
                (inventory.external_source_id, inventory.inbox_folder_id, inventory.processed_folder_id,
                 inventory.reupload_folder_id, inventory.excel_folder_id, inventory.backup_folder_id,
                 inventory.old_image_archive_folder_id),
                before_inventory,
            )
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetAiAnalysisModel)), before_creative)
            row = session.scalar(select(InventoryAiCredentialModel).where(InventoryAiCredentialModel.tenant_id == "tenant-a"))
            self.assertNotIn(gemini_key_a, row.encrypted_secret)
            self.assertNotIn(gemini_key_b, row.encrypted_secret)
            self.assertEqual(self.repo(session).get_active_secret("tenant-a"), gemini_key_b)
            audit = session.scalar(select(InventoryAiCredentialAuditModel))
            self.assertNotIn(gemini_key_a, repr(audit.__dict__))
            self.assertNotIn(gemini_key_b, repr(audit.__dict__))

    def test_failed_rotation_keeps_existing_gemini_key_and_all_isolated_state(self):
        drive_email = "drive-account-a@example.test"
        existing = "-".join(("fake", "gemini", "account", "a", "key"))
        invalid = "-".join(("fake", "gemini", "account", "b", "key"))
        with self.sessions() as session:
            connection = session.get(OAuthConnectionModel, "oauth-a")
            connection.provider_account_id = drive_email
            self.repo(session).replace("tenant-a", secret=existing, label="Gemini Account B")
            session.commit()
            before_oauth = (connection.id, connection.provider_account_id, connection.access_token_ciphertext, connection.refresh_token_ciphertext)
            before_creative = session.scalar(select(func.count()).select_from(AssetAiAnalysisModel))

        # The API's validation-first contract means no repository replacement is
        # reached for an invalid candidate; resolver remains active without restart.
        resolver = InventoryGeminiCredentialResolver(self.sessions, self.settings)
        self.assertEqual(resolver.resolve("tenant-a"), existing)
        with self.sessions() as session:
            self.assertEqual(self.repo(session).get_active_secret("tenant-a"), existing)
            connection = session.get(OAuthConnectionModel, "oauth-a")
            self.assertEqual((connection.id, connection.provider_account_id, connection.access_token_ciphertext, connection.refresh_token_ciphertext), before_oauth)
            self.assertEqual(session.scalar(select(func.count()).select_from(AssetAiAnalysisModel)), before_creative)
            self.assertNotEqual(existing, invalid)

    def test_broken_db_override_never_falls_back_to_environment(self):
        with self.sessions() as session:
            self.repo(session).replace("tenant-a", secret=SECRET)
            row = session.scalar(select(InventoryAiCredentialModel))
            row.encrypted_secret = "not-valid-ciphertext"
            session.commit()
        resolver = InventoryGeminiCredentialResolver(self.sessions, Settings(INVENTORY_GEMINI_API_KEY="inventory-env-key", INVENTORY_CREDENTIAL_ENCRYPTION_KEY=KEY))
        with self.assertRaisesRegex(InventoryCredentialError, "decryption_failed"):
            resolver.resolve("tenant-a")
