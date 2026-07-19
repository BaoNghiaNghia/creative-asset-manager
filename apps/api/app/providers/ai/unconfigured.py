from app.domain.providers.contracts import (
    AiMetadataAnalysisInput,
    AiMetadataAnalysisResult,
)


class UnconfiguredAiMetadataProvider:
    async def analyze_single(
        self, input: AiMetadataAnalysisInput
    ) -> AiMetadataAnalysisResult:
        raise RuntimeError("AI metadata provider is not configured")
