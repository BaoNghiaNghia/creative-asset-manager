import unittest

from app.domain.processing.handlers import WorkerDependencies
from app.domain.providers.registry import (
    AiProviderRegistry,
    AiProviderUnavailableError,
)


class FakeProvider:
    supports_single = True
    supports_batch = False

    def __init__(self, provider_name: str, model: str):
        self.provider_name = provider_name
        self.default_model = model
        self.closed = 0

    def close(self):
        self.closed += 1


class AiProviderRegistryTest(unittest.TestCase):
    def test_registers_and_returns_gemini(self):
        registry = AiProviderRegistry()
        gemini = FakeProvider("gemini", "gemini-test")
        registry.register("gemini", gemini)

        self.assertIs(registry.get("gemini"), gemini)
        self.assertIs(registry.require("gemini"), gemini)
        self.assertTrue(registry.has("gemini"))
        self.assertEqual(
            registry.list_capabilities()[0].provider_name,
            "gemini",
        )

    def test_unknown_provider_fails_predictably(self):
        with self.assertRaises(AiProviderUnavailableError) as raised:
            AiProviderRegistry().require("openai")
        self.assertEqual(raised.exception.code, "ai_provider_unavailable")
        self.assertFalse(raised.exception.retryable)

    def test_two_providers_are_independent_and_duplicates_are_rejected(self):
        registry = AiProviderRegistry()
        gemini = FakeProvider("gemini", "gemini-test")
        openai = FakeProvider("openai", "openai-test")
        registry.register("gemini", gemini)
        registry.register("openai", openai)

        self.assertIs(registry.require("gemini"), gemini)
        self.assertIs(registry.require("openai"), openai)
        self.assertEqual(
            [item.provider_name for item in registry.list_capabilities()],
            ["gemini", "openai"],
        )
        with self.assertRaises(ValueError):
            registry.register("gemini", FakeProvider("gemini", "other"))

    def test_worker_dependency_cleanup_closes_each_provider_once(self):
        registry = AiProviderRegistry()
        gemini = FakeProvider("gemini", "gemini-test")
        openai = FakeProvider("openai", "openai-test")
        registry.register("gemini", gemini)
        registry.register("openai", openai)
        dependencies = WorkerDependencies(
            session_factory=lambda: None,
            ai_provider_registry=registry,
        )

        dependencies.close()
        dependencies.close()

        self.assertEqual(gemini.closed, 1)
        self.assertEqual(openai.closed, 1)

class AiProviderRegistryAsyncTest(unittest.IsolatedAsyncioTestCase):
    async def test_async_cleanup_works_inside_running_event_loop(self):
        class AsyncProvider(FakeProvider):
            async def aclose(self):
                self.closed += 1

        registry = AiProviderRegistry()
        provider = AsyncProvider("openai", "openai-test")
        registry.register("openai", provider)

        await registry.aclose()
        await registry.aclose()

        self.assertEqual(provider.closed, 1)



if __name__ == "__main__":
    unittest.main()
