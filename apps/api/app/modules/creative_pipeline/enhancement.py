from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Protocol

@dataclass(frozen=True, slots=True)
class EnhancementPolicy:
    version: str
    watermark_removal_enabled: bool
    smart_enhance_enabled: bool
    engine_profile: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(cls, value: Any) -> "EnhancementPolicy | None":
        if value is None:
            return None
        if isinstance(value, cls):
            return value
        if not isinstance(value, dict) or not value.get("version"):
            return None
        return cls(
            version=str(value["version"]),
            watermark_removal_enabled=bool(value.get("watermark_removal_enabled", False)),
            smart_enhance_enabled=bool(value.get("smart_enhance_enabled", False)),
            engine_profile=value.get("engine_profile"),
            settings={k: v for k, v in value.items() if k not in {"version", "watermark_removal_enabled", "smart_enhance_enabled", "engine_profile"}},
        )

@dataclass(frozen=True, slots=True)
class VideoEnhancementInput:
    tenant_id: str
    raw_artifact_id: str
    raw_content_hash: str
    input_path: str
    aspect_ratio: str | None
    policy: EnhancementPolicy
    idempotency_key: str

@dataclass(frozen=True, slots=True)
class VideoEnhancementResult:
    content_type: str
    content: Any
    content_length: int | None = None
    checksum: str | None = None
    engine: str | None = None
    engine_version: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

class VideoEnhancementError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code, self.retryable, self.details = code, retryable, dict(details or {})

class VideoEnhancementProvider(Protocol):
    async def enhance(self, request: VideoEnhancementInput) -> VideoEnhancementResult: ...

class VideoEnhancementProviderRegistry:
    def __init__(self, providers: dict[str, VideoEnhancementProvider] | None = None):
        self._providers = dict(providers or {})
    def get(self, key: str | None = None):
        return self._providers.get(key or "default")
    def register(self, key: str, provider: VideoEnhancementProvider):
        self._providers[key] = provider
