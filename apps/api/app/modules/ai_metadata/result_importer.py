from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.domain.providers.contracts import AiMetadataAnalysisResult
from app.modules.ai_metadata.projection import SearchProjectionBuilder
from app.modules.ai_metadata.repository import AiMetadataRepository
from app.modules.ai_metadata.validator import MetadataDocumentValidator
from app.modules.processing.repository import ProcessingRepository

@dataclass(frozen=True,slots=True)
class ResultImportOutcome:
    status: Literal["completed","invalid_metadata"]
    validation_errors: tuple[dict,...]=()

class AiAnalysisResultImporter:
    """The single interpretation path shared by interactive and batch AI results."""
    def __init__(self,session:Session,settings:Settings,*,validator=None,projection_builder=None):
        self.session=session;self.settings=settings
        self.validator=validator or MetadataDocumentValidator()
        self.projection_builder=projection_builder or SearchProjectionBuilder()

    def import_result(self,*,tenant_id:str,analysis_id:str,
                      result:AiMetadataAnalysisResult,
                      enqueue_index:bool=True)->ResultImportOutcome:
        repository=AiMetadataRepository(self.session,self.validator)
        analysis=repository.get_analysis(analysis_id)
        if analysis.tenant_id!=tenant_id: raise LookupError(analysis_id)
        if analysis.status=="completed": return ResultImportOutcome("completed")
        profile=repository.get_profile(analysis.metadata_profile_id)
        validation=self.validator.validate(
            result.metadata,json_schema=profile.optional_json_schema)
        if not validation.valid:
            errors=tuple({
                "code":item.code,"message":item.message,"path":list(item.path),
                "limit":item.limit,"actual":item.actual,
            } for item in validation.errors)
            retryable=analysis.attempt_count<self.settings.AI_ANALYSIS_MAX_VALIDATION_ATTEMPTS
            repository.fail_analysis(
                analysis_id,error_code="metadata_validation_failed",
                error_message="AI metadata failed safety or profile validation.",
                retryable=retryable,validation_errors=list(errors),terminal=not retryable)
            return ResultImportOutcome("invalid_metadata",errors)
        metadata=validation.document or {}
        projection_result=self.projection_builder.build(
            metadata,profile.search_config_json)
        projection=projection_result.projection.to_document()
        encoded=json.dumps(projection,ensure_ascii=False,sort_keys=True,
                           separators=(",",":")).encode("utf-8")
        checksum=hashlib.sha256(encoded).hexdigest()
        repository.set_stage(analysis_id,"projection_ready")
        completed=repository.complete_analysis(
            analysis_id=analysis_id,metadata=metadata,
            raw_response=result.raw_response,
            store_raw_response=self.settings.AI_STORE_RAW_RESPONSE_ENABLED,
            search_projection=projection,
            search_projection_version=projection_result.projection_version,
            projection_checksum=checksum,
            provider_request_id=result.provider_request_id,
            usage=result.usage,provider_metadata=result.provider_metadata,
            ai_model=result.model)
        if enqueue_index:
            # The projection/index stages are durable and ordered.  Do not let a
            # completed analysis jump directly to indexing.
            ProcessingRepository(self.session).create_job(
                tenant_id=tenant_id,
                job_type="search_projection_build",
                entity_type="asset",
                entity_id=completed.asset_id,
                idempotency_key=(
                    f"direct-projection:{completed.id}:"
                    f"{projection_result.projection_version}:{checksum}"
                ),
                provider_key="elasticsearch",
                provider_scope="search",
                payload={
                    "asset_id": completed.asset_id,
                    "analysis_id": completed.id,
                    "direct_analysis": True,
                    "projection_version": projection_result.projection_version,
                },
            )
        return ResultImportOutcome("completed")
