from __future__ import annotations

import base64
import asyncio
import unittest
from datetime import datetime, timezone
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings
from app.core.database import Base
from app.modules.ai_operations.credentials import CreativeAiCredentialRepository, CreativeCredentialError, CreativeGeminiCredentialResolver, creative_credential_cipher
from app.modules.auth_persistence.model import TenantModel
from app.providers.ai.creative_gemini import RuntimeCreativeGeminiProvider
from app.domain.providers.contracts import (
    AiBatchResult,
    AiBatchResultsInput,
    AiBatchStatus,
    AiBatchStatusInput,
    AiBatchSubmission,
    AiBatchSubmissionInput,
    AiMetadataAnalysisInput,
    AiMetadataAnalysisResult,
    AiProviderError,
)

KEY = base64.urlsafe_b64encode(b"C" * 32).decode().rstrip("=")


class CreativeGeminiCredentialTest(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.sessions = sessionmaker(self.engine, class_=Session, expire_on_commit=False)
        with self.sessions() as session:
            session.add_all((TenantModel(id="tenant-a", name="A", slug="a"), TenantModel(id="tenant-b", name="B", slug="b")))
            session.commit()
        self.settings = Settings(CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY=KEY, GEMINI_API_KEY="env-creative-key-0000")

    def tearDown(self): self.engine.dispose()

    def test_encrypted_override_is_tenant_scoped_and_replaces_env(self):
        with self.sessions() as session:
            repo = CreativeAiCredentialRepository(session, creative_credential_cipher(self.settings))
            metadata = repo.replace("tenant-a", secret="creative-db-key-1234", label="Creative project", updated_by="actor")
            session.commit()
            row = session.get(__import__('app.modules.ai_operations.credential_model', fromlist=['CreativeAiCredentialModel']).CreativeAiCredentialModel, metadata.id)
            self.assertNotIn("creative-db-key-1234", row.encrypted_secret)
            self.assertEqual(row.secret_last4, "1234")
        resolver = CreativeGeminiCredentialResolver(self.sessions, self.settings)
        self.assertEqual(resolver.resolve("tenant-a").secret, "creative-db-key-1234")
        self.assertEqual(resolver.resolve("tenant-b").secret, "env-creative-key-0000")

    def test_missing_encryption_or_corrupt_override_fails_closed(self):
        with self.sessions() as session:
            repo = CreativeAiCredentialRepository(session, creative_credential_cipher(self.settings))
            repo.replace("tenant-a", secret="creative-db-key-1234")
            session.commit()
        with self.assertRaises(CreativeCredentialError) as missing:
            CreativeGeminiCredentialResolver(self.sessions, Settings(GEMINI_API_KEY="env")).resolve("tenant-a")
        self.assertEqual(missing.exception.code, "creative_credential_encryption_unavailable")

    def test_rotation_keeps_the_project_quota_boundary_without_restart(self):
        settings = Settings(
            CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY=KEY,
            GEMINI_API_KEY="env-creative-key-0000",
            GEMINI_MODEL_POOL="model",
            GEMINI_MODEL_LIMITS='{"model":{"rpm":1,"tpm":1,"rpd":1}}',
            GEMINI_PROJECT_DAILY_REQUEST_LIMIT=1,
        )
        captured = []

        class FakeProvider:
            provider_name = "gemini"; supports_single = True; supports_batch = True

            def __init__(self, key, **kwargs):
                self.key = key
                self.default_model = kwargs["model"]
                self.coordinator = kwargs["quota_coordinator"]

        provider = RuntimeCreativeGeminiProvider(
            settings,
            self.sessions,
            provider_factory=lambda key, **kwargs: (
                captured.append((key, kwargs["quota_coordinator"]))
                or FakeProvider(key, **kwargs)
            ),
        )
        resolver = CreativeGeminiCredentialResolver(self.sessions, settings)
        first = resolver.resolve("tenant-a")
        provider._delegate_for("tenant-a", first)
        with self.sessions() as session:
            CreativeAiCredentialRepository(
                session, creative_credential_cipher(settings)
            ).replace("tenant-a", secret="creative-rotated-key-5678")
            session.commit()
        second = resolver.resolve("tenant-a")
        provider._delegate_for("tenant-a", second)

        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(
            [row[0] for row in captured],
            ["env-creative-key-0000", "creative-rotated-key-5678"],
        )
        self.assertEqual(
            captured[0][1].quota_scope, settings.GEMINI_PROJECT_QUOTA_SCOPE
        )
        self.assertEqual(captured[0][1].quota_scope, captured[1][1].quota_scope)

        now = datetime(2040, 1, 1, tzinfo=timezone.utc)
        self.assertIsNone(
            captured[0][1].reserve_request(model="model", rpd=1, now=now)
        )
        denied = captured[1][1].reserve_request(model="model", rpd=1, now=now)
        self.assertIsNotNone(denied)
        self.assertEqual(denied.reason, "project_rpd_exhausted")

    def test_missing_everything_is_explicitly_unavailable(self):
        with self.assertRaises(CreativeCredentialError) as context:
            CreativeGeminiCredentialResolver(self.sessions, Settings(CREATIVE_AI_CREDENTIAL_ENCRYPTION_KEY=KEY)).resolve("tenant-a")
        self.assertEqual(context.exception.code, "creative_gemini_credential_unavailable")

    def test_runtime_request_rotation_and_batch_affinity_use_the_expected_key(self):
        calls = []

        class FakeProvider:
            provider_name = "gemini"
            supports_single = True
            supports_batch = True
            def __init__(self, key, **kwargs):
                self.key = key
                self.default_model = kwargs["model"]
            async def analyze_single(self, input):
                calls.append(("single", self.key))
                return AiMetadataAnalysisResult(metadata={}, provider="gemini")
            async def submit_batch(self, input):
                calls.append(("submit", self.key))
                return AiBatchSubmission(provider_batch_id="batch-1", state="submitted")
            async def get_batch_status(self, input):
                calls.append(("status", self.key))
                return AiBatchStatus(state="running")
            async def stream_batch_results(self, input):
                calls.append(("results", self.key))
                yield AiBatchResult(custom_item_id="item-1")
            async def cancel_batch(self, input):
                calls.append(("cancel", self.key))
                return True

        provider = RuntimeCreativeGeminiProvider(
            self.settings, self.sessions,
            provider_factory=lambda key, **kwargs: FakeProvider(key, **kwargs),
        )

        async def initial():
            await provider.analyze_single(AiMetadataAnalysisInput(
                tenant_id="tenant-a", asset_id="asset-1", prompt="x",
                image_bytes=b"jpeg", image_mime_type="image/jpeg",
                metadata_profile="general", metadata_profile_version="1",
            ))
            return await provider.submit_batch(AiBatchSubmissionInput(
                tenant_id="tenant-a", submission_key="submission-1",
                display_name="test", model="gemini-test", input_path="input.jsonl",
                item_count=1, total_bytes=1,
            ))

        submitted = asyncio.run(initial())
        self.assertEqual(calls, [("single", "env-creative-key-0000"), ("submit", "env-creative-key-0000")])
        with self.sessions() as session:
            CreativeAiCredentialRepository(session, creative_credential_cipher(self.settings)).replace(
                "tenant-a", secret="creative-db-key-c-9999"
            )
            session.commit()

        async def complete_old_and_start_new():
            status = AiBatchStatusInput(
                tenant_id="tenant-a", provider_batch_id="batch-1",
                credential_fingerprint=submitted.credential_fingerprint,
                credential_encrypted_secret=submitted.credential_encrypted_secret,
                credential_key_version=submitted.credential_key_version,
            )
            await provider.get_batch_status(status)
            results = AiBatchResultsInput(
                tenant_id="tenant-a", provider_batch_id="batch-1",
                credential_fingerprint=submitted.credential_fingerprint,
                credential_encrypted_secret=submitted.credential_encrypted_secret,
                credential_key_version=submitted.credential_key_version,
            )
            _ = [result async for result in provider.stream_batch_results(results)]
            await provider.cancel_batch(status)
            await provider.analyze_single(AiMetadataAnalysisInput(
                tenant_id="tenant-a", asset_id="asset-2", prompt="x",
                image_bytes=b"jpeg", image_mime_type="image/jpeg",
                metadata_profile="general", metadata_profile_version="1",
            ))

        asyncio.run(complete_old_and_start_new())
        self.assertEqual(calls, [
            ("single", "env-creative-key-0000"),
            ("submit", "env-creative-key-0000"),
            ("status", "env-creative-key-0000"),
            ("results", "env-creative-key-0000"),
            ("cancel", "env-creative-key-0000"),
            ("single", "creative-db-key-c-9999"),
        ])

    def test_corrupt_historical_batch_credential_fails_closed_without_current_key_fallback(self):
        provider = RuntimeCreativeGeminiProvider(
            self.settings, self.sessions,
            provider_factory=lambda key, **kwargs: object(),
        )
        async def run():
            with self.assertRaises(AiProviderError) as context:
                await provider.get_batch_status(AiBatchStatusInput(
                    tenant_id="tenant-a", provider_batch_id="batch-1",
                    credential_fingerprint="0" * 64,
                    credential_encrypted_secret="not-a-valid-ciphertext",
                    credential_key_version="v1",
                ))
            self.assertEqual(context.exception.code, "creative_gemini_batch_credential_unavailable")
        asyncio.run(run())
