from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Callable

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.modules.inventory.ai.gateway import DisabledInventoryAiGateway, InventoryAiGateway, InventoryAiGatewayError
from app.modules.inventory.ai.profile import (
    INVENTORY_EXTRACTION_PROFILE, INVENTORY_EXTRACTION_PROFILE_VERSION,
    INVENTORY_EXTRACTION_PROMPT, INVENTORY_EXTRACTION_SCHEMA,
    INVENTORY_PROMPT_VERSION, INVENTORY_SCHEMA_VERSION, validate_extraction,
)
from app.modules.inventory.jobs.errors import InventoryJobFailure
from app.modules.inventory.jobs.model import InventoryJobModel
from app.modules.inventory.jobs.repository import InventoryJobRepository
from app.modules.inventory.model import InventoryAiControlModel
from app.modules.inventory.persistence_model import InventoryAiAnalysisModel, InventoryDocumentModel, InventoryDocumentPageModel, inventory_utcnow
from app.modules.inventory.preparation.storage import InventoryPreparedStorage

INVENTORY_DOCUMENT_ANALYZE_JOB = "inventory_document_analyze"
logger = logging.getLogger("cam.inventory.ai")


class InventoryAnalyzeFailure(InventoryJobFailure):
    pass


def analysis_idempotency_key(*, tenant_id: str, page_id: str, content_sha256: str, profile_version: str, prompt_version: str, schema_version: str, provider: str, model: str) -> str:
    return "inventory-ai:" + ":".join((tenant_id, page_id, content_sha256, profile_version, prompt_version, schema_version, provider, model))


class InventoryDocumentAnalyzer:
    """Inventory-only structured extraction. It never touches Creative AI state."""

    def __init__(self, session_factory: sessionmaker, *, prepared_storage: InventoryPreparedStorage, gateway: InventoryAiGateway | None = None, enabled: bool = False, estimated_cost_micros: int = 0):
        self.session_factory = session_factory
        self.prepared_storage = prepared_storage
        self.gateway = gateway or DisabledInventoryAiGateway()
        self.enabled = enabled
        self.estimated_cost_micros = max(0, estimated_cost_micros)

    def enqueue(self, *, tenant_id: str, page: InventoryDocumentPageModel, document: InventoryDocumentModel, repository: InventoryJobRepository) -> InventoryJobModel:
        content_hash = page.prepared_content_sha256 or page.content_sha256
        if not content_hash:
            raise InventoryAnalyzeFailure("inventory_ai_content_identity_missing", retryable=False)
        # The control determines provider/model at execution time. The job identity is profile/version scoped.
        key = f"inventory-document-analyze:{page.id}:{content_hash}:{INVENTORY_EXTRACTION_PROFILE_VERSION}:{INVENTORY_PROMPT_VERSION}:{INVENTORY_SCHEMA_VERSION}"
        return repository.create_job(tenant_id=tenant_id, job_type=INVENTORY_DOCUMENT_ANALYZE_JOB, entity_type="inventory_document_page", entity_id=page.id, idempotency_key=key, payload={"page_id": page.id, "document_id": document.id})

    def execute(self, job: InventoryJobModel) -> None:
        page_id = str((job.payload_json or {}).get("page_id") or job.entity_id)
        analysis, page, control = self._begin(job.tenant_id, page_id)
        if analysis.status == "succeeded":
            return
        try:
            image = self.prepared_storage.read_bytes(tenant_id=job.tenant_id, storage_key=page.prepared_storage_key or "")
            result = self.gateway.analyze(image_bytes=image, image_mime_type=page.prepared_mime_type or "image/jpeg", prompt=INVENTORY_EXTRACTION_PROMPT, schema=INVENTORY_EXTRACTION_SCHEMA, provider=control.provider, model=analysis.model)
            extracted = validate_extraction(dict(result.extracted_json))
            self._succeed(job.tenant_id, analysis.id, page.id, result, extracted)
        except InventoryAiGatewayError as exc:
            self._fail(job.tenant_id, analysis.id, page.id, exc.code, exc.retryable)
            raise InventoryAnalyzeFailure(exc.code, retryable=exc.retryable) from exc
        except ValueError as exc:
            code = str(exc) if str(exc).startswith("inventory_ai_") else "inventory_ai_invalid_structured_output"
            self._fail(job.tenant_id, analysis.id, page.id, code, False)
            raise InventoryAnalyzeFailure(code, retryable=False) from exc
        except OSError as exc:
            self._fail(job.tenant_id, analysis.id, page.id, "inventory_ai_prepared_artifact_missing", True)
            raise InventoryAnalyzeFailure("inventory_ai_prepared_artifact_missing", retryable=True) from exc

    def _begin(self, tenant_id: str, page_id: str):
        now = inventory_utcnow()
        with self.session_factory() as session:
            page_query = select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.tenant_id == tenant_id, InventoryDocumentPageModel.id == page_id)
            if session.bind is not None and session.bind.dialect.name == "postgresql":
                page_query = page_query.with_for_update()
            page = session.scalar(page_query)
            if page is None:
                raise InventoryAnalyzeFailure("inventory_ai_page_not_found", retryable=False)
            if page.preparation_status not in {"prepared", "duplicate"} or not page.prepared_storage_key:
                raise InventoryAnalyzeFailure("inventory_ai_page_not_prepared", retryable=False)
            document = session.scalar(select(InventoryDocumentModel).where(InventoryDocumentModel.tenant_id == tenant_id, InventoryDocumentModel.id == page.document_id))
            if document is None:
                raise InventoryAnalyzeFailure("inventory_ai_document_not_found", retryable=False)
            control = session.scalar(select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id).with_for_update() if session.bind is not None and session.bind.dialect.name == "postgresql" else select(InventoryAiControlModel).where(InventoryAiControlModel.tenant_id == tenant_id))
            if not self.enabled or control is None or not control.enabled:
                raise InventoryAnalyzeFailure("inventory_ai_disabled", retryable=False)
            if control.emergency_stop:
                raise InventoryAnalyzeFailure("inventory_ai_emergency_stop", retryable=True)
            models = tuple(str(value) for value in (control.allowed_models_json or ()) if value)
            if not models:
                raise InventoryAnalyzeFailure("inventory_ai_model_not_allowed", retryable=False)
            model = models[0]
            content_hash = page.prepared_content_sha256 or page.content_sha256
            if not content_hash:
                raise InventoryAnalyzeFailure("inventory_ai_content_identity_missing", retryable=False)
            key = analysis_idempotency_key(tenant_id=tenant_id, page_id=page.id, content_sha256=content_hash, profile_version=INVENTORY_EXTRACTION_PROFILE_VERSION, prompt_version=INVENTORY_PROMPT_VERSION, schema_version=INVENTORY_SCHEMA_VERSION, provider=control.provider, model=model)
            analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.idempotency_key == key))
            if analysis is None:
                try:
                    with session.begin_nested():
                        analysis = InventoryAiAnalysisModel(tenant_id=tenant_id, document_id=document.id, page_id=page.id, analysis_version=self._next_version(session, tenant_id, page.id), idempotency_key=key, content_sha256=content_hash, extraction_profile=INVENTORY_EXTRACTION_PROFILE, extraction_profile_version=INVENTORY_EXTRACTION_PROFILE_VERSION, provider=control.provider, model=model, prompt_version=INVENTORY_PROMPT_VERSION, schema_version=INVENTORY_SCHEMA_VERSION, status="queued", validation_status="unvalidated")
                        session.add(analysis); session.flush()
                except IntegrityError:
                    analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.idempotency_key == key))
            if analysis is None:
                raise RuntimeError("inventory_ai_analysis_creation_failed")
            if analysis.status == "succeeded":
                session.expunge(analysis); session.expunge(page); session.expunge(control); return analysis, page, control
            self._reserve(session, tenant_id, control, now, self.estimated_cost_micros)
            analysis.estimated_cost_micros = self.estimated_cost_micros
            analysis.status = "analyzing"; analysis.attempt_count += 1; analysis.started_at = now; analysis.error_code = None; analysis.error_message = None
            page.analysis_status = "processing"; document.status = "analyzing"
            session.commit(); session.expunge(analysis); session.expunge(page); session.expunge(control)
            return analysis, page, control

    @staticmethod
    def _next_version(session: Session, tenant_id: str, page_id: str) -> int:
        return int(session.scalar(select(func.coalesce(func.max(InventoryAiAnalysisModel.analysis_version), 0)).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.page_id == page_id)) or 0) + 1

    def _reserve(self, session: Session, tenant_id: str, control: InventoryAiControlModel, now: datetime, estimated: int) -> None:
        active = int(session.scalar(select(func.count(InventoryAiAnalysisModel.id)).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.status == "analyzing")) or 0)
        if active >= control.max_concurrent:
            raise InventoryAnalyzeFailure("inventory_ai_concurrency_limited", retryable=True)
        last_started_at = control.last_started_at
        # SQLite test storage may lose timezone information; Inventory timestamps are UTC.
        if last_started_at is not None and last_started_at.tzinfo is None:
            last_started_at = last_started_at.replace(tzinfo=timezone.utc)
        if last_started_at and now < last_started_at + timedelta(seconds=control.min_start_interval_seconds):
            raise InventoryAnalyzeFailure("inventory_ai_rate_limited", retryable=True)
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        month_start = day_start.replace(day=1)
        for start, limit, code in ((day_start, control.daily_budget_micros, "inventory_ai_daily_budget_exceeded"), (month_start, control.monthly_budget_micros, "inventory_ai_monthly_budget_exceeded")):
            if limit:
                spent = int(session.scalar(select(func.coalesce(func.sum(InventoryAiAnalysisModel.estimated_cost_micros), 0)).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.created_at >= start, InventoryAiAnalysisModel.status.in_(("analyzing", "succeeded")))) or 0)
                if spent + estimated > limit:
                    raise InventoryAnalyzeFailure(code, retryable=True)
        control.last_started_at = now

    def _succeed(self, tenant_id: str, analysis_id: str, page_id: str, result, extracted: dict) -> None:
        with self.session_factory() as session:
            analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.id == analysis_id))
            page = session.scalar(select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.tenant_id == tenant_id, InventoryDocumentPageModel.id == page_id))
            if analysis is None or page is None: raise RuntimeError("inventory_ai_result_target_missing")
            analysis.status = "succeeded"; analysis.validation_status = "valid"; analysis.raw_result_json = dict(result.raw_response_json); analysis.extracted_json = extracted; analysis.provider_request_id = result.provider_request_id; analysis.usage_json = dict(result.usage_json); analysis.estimated_cost_micros = max(0, int(result.estimated_cost_micros)); analysis.completed_at = inventory_utcnow()
            page.analysis_status = "completed"; session.commit()
        logger.info("inventory_ai_analysis_succeeded tenant_id=%s page_id=%s analysis_id=%s", tenant_id, page_id, analysis_id)

    def _fail(self, tenant_id: str, analysis_id: str, page_id: str, code: str, retryable: bool) -> None:
        with self.session_factory() as session:
            analysis = session.scalar(select(InventoryAiAnalysisModel).where(InventoryAiAnalysisModel.tenant_id == tenant_id, InventoryAiAnalysisModel.id == analysis_id))
            page = session.scalar(select(InventoryDocumentPageModel).where(InventoryDocumentPageModel.tenant_id == tenant_id, InventoryDocumentPageModel.id == page_id))
            if analysis is not None:
                analysis.status = "retryable_failure" if retryable else "terminal_failure"; analysis.error_code = code[:100]; analysis.error_message = code[:1000]; analysis.completed_at = inventory_utcnow() if not retryable else None
            if page is not None: page.analysis_status = "failed"
            session.commit()
        logger.warning("inventory_ai_analysis_failed tenant_id=%s page_id=%s analysis_id=%s error_code=%s retryable=%s", tenant_id, page_id, analysis_id, code, retryable)
