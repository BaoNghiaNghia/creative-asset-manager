from __future__ import annotations

import asyncio
import inspect
import re
from dataclasses import dataclass

from app.domain.providers.contracts import AiMetadataProvider, AiProviderError

_PROVIDER_NAME = re.compile(r"^[a-z][a-z0-9_]*$")


@dataclass(frozen=True, slots=True)
class AiProviderCapability:
    provider_name: str
    supports_single: bool
    supports_batch: bool
    default_model: str | None


class AiProviderUnavailableError(AiProviderError):
    def __init__(self, provider_name: str):
        super().__init__(
            f"AI provider '{provider_name}' is not configured.",
            code="ai_provider_unavailable",
            retryable=False,
        )
        self.provider_name = provider_name


class AiProviderRegistry:
    """Process-local resolver for provider-neutral AI adapters."""

    def __init__(self) -> None:
        self._providers: dict[str, AiMetadataProvider] = {}
        self._closed = False

    def register(self, provider_name: str, provider: AiMetadataProvider) -> None:
        name = self._validated_name(provider_name)
        identity = self._validated_name(provider.provider_name)
        if identity != name:
            raise ValueError("AI provider registration name does not match adapter identity")
        if name in self._providers:
            raise ValueError(f"AI provider '{name}' is already registered")
        if self._closed:
            raise RuntimeError("AI provider registry is closed")
        self._providers[name] = provider

    def get(self, provider_name: str) -> AiMetadataProvider | None:
        return self._providers.get(self._validated_name(provider_name))

    def require(self, provider_name: str) -> AiMetadataProvider:
        try:
            name = self._validated_name(provider_name)
        except ValueError:
            raise AiProviderUnavailableError(str(provider_name or "unknown")) from None
        provider = self._providers.get(name)
        if provider is None:
            raise AiProviderUnavailableError(name)
        return provider

    def has(self, provider_name: str) -> bool:
        return self.get(provider_name) is not None

    def list_capabilities(self) -> tuple[AiProviderCapability, ...]:
        return tuple(
            AiProviderCapability(
                provider_name=name,
                supports_single=bool(provider.supports_single),
                supports_batch=bool(provider.supports_batch),
                default_model=provider.default_model,
            )
            for name, provider in sorted(self._providers.items())
        )

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        first_error: Exception | None = None
        for provider in reversed(tuple(self._providers.values())):
            closer = getattr(provider, "aclose", None) or getattr(provider, "close", None)
            if closer is None:
                continue
            try:
                result = closer()
                if inspect.isawaitable(result):
                    asyncio.run(result)
            except Exception as exc:
                if first_error is None:
                    first_error = exc
        if first_error is not None:
            raise first_error

    @staticmethod
    def _validated_name(provider_name: str) -> str:
        if not isinstance(provider_name, str) or not _PROVIDER_NAME.fullmatch(provider_name):
            raise ValueError("AI provider names must be stable lowercase identifiers")
        return provider_name
