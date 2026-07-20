from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataAnalysisResult,
    AiProviderError,
)


class UnconfiguredAiMetadataProvider:
    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        raise AiProviderError(
            "AI metadata provider is not configured",
            code="ai_provider_unconfigured",
            retryable=False,
        )
