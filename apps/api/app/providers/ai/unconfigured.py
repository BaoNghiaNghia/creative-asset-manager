from collections.abc import AsyncIterator

from app.domain.providers.contracts import (
    AiBatchResult, AiBatchResultsInput, AiBatchStatus, AiBatchStatusInput,
    AiBatchSubmission, AiBatchSubmissionInput, AiMetadataAnalysisInput,
    AiMetadataAnalysisResult, AiProviderError,
)

class UnconfiguredAiMetadataProvider:
    supports_batch=False

    async def analyze_single(self,input:AiMetadataAnalysisInput)->AiMetadataAnalysisResult:
        raise self._error()

    async def submit_batch(self,input:AiBatchSubmissionInput)->AiBatchSubmission:
        raise self._error()

    async def get_batch_status(self,input:AiBatchStatusInput)->AiBatchStatus:
        raise self._error()

    async def stream_batch_results(self,input:AiBatchResultsInput)->AsyncIterator[AiBatchResult]:
        raise self._error()
        if False:
            yield AiBatchResult(custom_item_id="")

    async def cancel_batch(self,input:AiBatchStatusInput)->bool:
        raise self._error()

    @staticmethod
    def _error()->AiProviderError:
        return AiProviderError(
            "AI metadata provider is not configured",
            code="ai_provider_unconfigured",retryable=False)
