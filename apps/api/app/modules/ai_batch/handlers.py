from __future__ import annotations

import asyncio

from app.core.config import Settings, get_settings
from app.domain.processing.handlers import JobHandlerContext, JobHandlerResult
from app.domain.providers.contracts import AiProviderError
from app.modules.ai_batch.service import AiBatchService

class _BaseBatchHandler:
    def __init__(self,settings:Settings|None=None): self.settings=settings

    def service(self,context:JobHandlerContext):
        if context.dependencies.ai_provider is None:
            return None,None,JobHandlerResult.non_retryable(
                "ai_provider_unconfigured","AI metadata provider is not configured.")
        if context.dependencies.storage_provider is None:
            return None,None,JobHandlerResult.non_retryable(
                "storage_provider_unconfigured","Asset storage provider is not configured.")
        session=context.dependencies.session_factory()
        return session,AiBatchService(
            session,self.settings or get_settings(),
            context.dependencies.ai_provider,context.dependencies.storage_provider),None

    @staticmethod
    def failure(exc:Exception):
        if isinstance(exc,AiProviderError):
            return (JobHandlerResult.retryable if exc.retryable else JobHandlerResult.non_retryable)(
                exc.code,str(exc))
        return JobHandlerResult.retryable("ai_batch_error","AI batch operation failed.")

class AiBatchPrepareJobHandler(_BaseBatchHandler):
    def __call__(self,context:JobHandlerContext)->JobHandlerResult:
        opened=self.service(context)
        if opened[2]:return opened[2]
        session,service=opened[:2]
        try:
            ids=context.job.payload.get("analysis_ids")
            if ids is not None and not isinstance(ids,list):
                return JobHandlerResult.non_retryable(
                    "invalid_batch_candidates","analysis_ids must be a list.")
            service.create_batches(tenant_id=context.job.tenant_id,analysis_ids=ids)
            session.commit();return JobHandlerResult.completed()
        except Exception as exc:
            session.rollback();return self.failure(exc)
        finally:session.close()

class AiBatchSubmitJobHandler(_BaseBatchHandler):
    def __call__(self,context:JobHandlerContext)->JobHandlerResult:
        return self._async(context,"submit")

    def _async(self,context,method):
        opened=self.service(context)
        if opened[2]:return opened[2]
        session,service=opened[:2]
        try:
            batch_id=context.job.payload.get("batch_id") or context.job.entity_id
            asyncio.run(getattr(service,method)(
                tenant_id=context.job.tenant_id,batch_id=batch_id))
            session.commit();return JobHandlerResult.completed()
        except AiProviderError as exc:
            session.commit();return self.failure(exc)
        except Exception as exc:
            session.rollback();return self.failure(exc)
        finally:session.close()

class AiBatchPollJobHandler(AiBatchSubmitJobHandler):
    def __call__(self,context):return self._async(context,"poll")

class AiBatchImportJobHandler(AiBatchSubmitJobHandler):
    def __call__(self,context):return self._async(context,"import_results")

class AiBatchRetryItemsJobHandler(_BaseBatchHandler):
    def __call__(self,context):
        opened=self.service(context)
        if opened[2]:return opened[2]
        session,service=opened[:2]
        try:
            batch_id=context.job.payload.get("batch_id") or context.job.entity_id
            service.retry_items(tenant_id=context.job.tenant_id,batch_id=batch_id)
            session.commit();return JobHandlerResult.completed()
        except Exception as exc:
            session.rollback();return self.failure(exc)
        finally:session.close()
