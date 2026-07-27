import unittest

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core.database import Base

from app.core.config import Settings
from app.providers.ai.factory import build_ai_provider_registry
from app.providers.ai.gemini import GeminiAiMetadataProvider, GeminiModelLimit


class AiProviderFactoryTest(unittest.TestCase):
    def test_passes_typed_gemini_model_limits_to_the_provider(self) -> None:
        registry = build_ai_provider_registry(
            Settings(
                GEMINI_API_KEY="test-key",
                GEMINI_MODEL_POOL="gemini-2.5-flash-lite",
            )
        )

        provider = registry.require("gemini")

        self.assertIsInstance(provider, GeminiAiMetadataProvider)
        self.assertEqual(
            provider._limits["gemini-2.5-flash-lite"],
            GeminiModelLimit(rpm=8, tpm=200000, rpd=16),
        )

    def test_worker_registry_uses_durable_gemini_project_quota_state(self) -> None:
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        sessions = sessionmaker(engine, class_=Session, expire_on_commit=False)
        try:
            provider = build_ai_provider_registry(
                Settings(
                    GEMINI_API_KEY="test-key",
                    GEMINI_MODEL_POOL="gemini-2.5-flash-lite",
                ),
                session_factory=sessions,
            ).require("gemini")
            self.assertIsNotNone(provider._quota_coordinator)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()